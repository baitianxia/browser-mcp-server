#requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet("auto", "chrome", "msedge")]
    [string]$BrowserChannel = "auto",
    [ValidateRange(0, 86400)]
    [int]$ManualExtensionWaitSeconds = 0,
    [string]$LogPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ToolDiscovery = Join-Path $PSScriptRoot `
    "toolkit\scripts\windows-tool-discovery.ps1"
if (-not (Test-Path -LiteralPath $ToolDiscovery -PathType Leaf)) {
    # Keep the source script directly testable before transfer-kit assembly.
    $ToolDiscovery = Join-Path $PSScriptRoot "windows-tool-discovery.ps1"
}
if (-not (Test-Path -LiteralPath $ToolDiscovery -PathType Leaf)) {
    throw "迁移包不完整，缺少 Windows 工具探测脚本：$ToolDiscovery"
}
. $ToolDiscovery

$script:PythonExe = ""
$script:PythonPrefix = @()
$script:LogPath = $LogPath
$StageRoot = $null
$RuntimeExtractionRoot = $null
$ExtensionStagingRoot = $null
$BackupRoot = $null
$ConfigBackupPath = $null
$ConfigWasPresent = $false
$ConfigChangeStarted = $false
$ConfigBackupComplete = $false
$ConfigPublished = $false
$ConfigCommitted = $false
$ClaudeUserConfigPath = $null
$ClaudeUserConfigBackup = $null
$UserConfigWasPresent = $false
$UserConfigChangeStarted = $false
$UserConfigCommitted = $false
$InstallLockPath = $null
$InstallLockStream = $null
$ExtensionPolicyPath = $null
$ExtensionPolicyValueName = $null
$ExtensionPolicyPreviousValue = $null
$ExtensionPolicyPreviousValueKind = $null
$ExtensionPolicyValueWasPresent = $false
$ExtensionPolicyKeyWasPresent = $false
$ExtensionPolicyKeyCreated = $false
$ExtensionPolicyCreatedPaths = @()
$ExtensionPolicyChangeStarted = $false
$ExtensionPolicyCommitted = $false
$ExtensionInstallMethod = ""

function Write-InstallLog {
    param([string]$Message)
    if (-not $script:LogPath) {
        return
    }
    try {
        $Parent = Split-Path -Parent $script:LogPath
        if ($Parent) {
            New-Item -ItemType Directory -Path $Parent -Force | Out-Null
        }
        $Encoding = New-Object System.Text.UTF8Encoding($true)
        $Line = "{0} {1}{2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"), $Message, [Environment]::NewLine
        [IO.File]::AppendAllText($script:LogPath, $Line, $Encoding)
    } catch {
        # Diagnostics must never hide the original installer failure.
    }
}

function Write-Step {
    param([int]$Number, [string]$Message)
    Write-Host ""
    Write-Host "[$Number/6] $Message" -ForegroundColor Cyan
    Write-InstallLog "STEP $Number/6: $Message"
}

function Invoke-External {
    param([string]$Executable, [string[]]$Arguments)
    $Output = @()
    $ExitCode = 1
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 turns redirected native stderr into ErrorRecord
        # objects. Capture it for the log, but decide success from the exit code.
        $ErrorActionPreference = "Continue"
        $Output = & $Executable @Arguments 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    foreach ($Line in @($Output)) {
        if ($null -ne $Line) {
            Write-Host $Line
            Write-InstallLog "NATIVE: $Line"
        }
    }
    if ($ExitCode -ne 0) {
        throw "$Executable failed with exit code $ExitCode"
    }
}

function Invoke-Python {
    param([string[]]$Arguments)
    $Prefix = $script:PythonPrefix
    $Output = @()
    $ExitCode = 1
    $PreviousErrorActionPreference = $ErrorActionPreference
    $HadDontWriteBytecode = Test-Path Env:PYTHONDONTWRITEBYTECODE
    $PreviousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    try {
        $ErrorActionPreference = "Continue"
        $env:PYTHONDONTWRITEBYTECODE = "1"
        $Output = & $script:PythonExe @Prefix @Arguments 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        if ($HadDontWriteBytecode) {
            $env:PYTHONDONTWRITEBYTECODE = $PreviousDontWriteBytecode
        } else {
            Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
        }
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    foreach ($Line in @($Output)) {
        if ($null -ne $Line) {
            Write-Host $Line
            Write-InstallLog "PYTHON: $Line"
        }
    }
    if ($ExitCode -ne 0) {
        throw "Python helper failed with exit code $ExitCode"
    }
}

function Test-RetryableDirectoryMoveError {
    param(
        [Parameter(Mandatory = $true)]
        [Management.Automation.ErrorRecord]$ErrorRecord
    )
    if ($ErrorRecord.CategoryInfo.Category -eq
        [Management.Automation.ErrorCategory]::PermissionDenied) {
        return $true
    }
    $ErrorId = [string]$ErrorRecord.FullyQualifiedErrorId
    if ($ErrorId -match '(?i)(UnauthorizedAccess|MoveDirectoryItemIOError|MoveFileInfoItemUnauthorizedAccessError)') {
        return $true
    }
    $Exception = $ErrorRecord.Exception
    while ($null -ne $Exception) {
        if ($Exception -is [UnauthorizedAccessException] -or
            $Exception -is [IO.IOException]) {
            return $true
        }
        $Exception = $Exception.InnerException
    }
    return $false
}

function Move-DirectoryAtomicallyWithRetry {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$OperationLabel,
        [Parameter(Mandatory = $true)][string]$LogPrefix,
        [ValidateRange(2, 100)][int]$MaximumAttempts = 25
    )
    $DelayMilliseconds = 250
    for ($Attempt = 1; $Attempt -le $MaximumAttempts; $Attempt++) {
        try {
            # PowerShell's FileSystem provider may create the destination
            # directory before a denied move finishes, leaving both paths
            # present and making a safe retry impossible. Directory.Move on
            # Windows PowerShell 5.1 delegates to the native same-volume
            # MoveFile operation, so a denied rename leaves the destination
            # absent and the retry state remains unambiguous.
            [IO.Directory]::Move($Source, $Destination)
            if (Test-Path -LiteralPath $Source) {
                throw ("{0}返回成功，但源目录仍然存在：{1}" -f `
                    $OperationLabel, $Source)
            }
            if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
                throw ("{0}返回成功，但目标目录不存在：{1}" -f `
                    $OperationLabel, $Destination)
            }
            if ($Attempt -gt 1) {
                Write-Host ("{0}占用已释放，安装继续。" -f $OperationLabel) `
                    -ForegroundColor Green
                Write-InstallLog ("{0} RECOVERED: attempts={1}; destination={2}" -f `
                    $LogPrefix, $Attempt, $Destination)
            } else {
                Write-InstallLog "$LogPrefix COMPLETED: $Destination"
            }
            return
        } catch {
            $MoveError = $_
            $SourcePresent = Test-Path -LiteralPath $Source -PathType Container
            $DestinationPresent = Test-Path -LiteralPath $Destination -PathType Container
            if (-not $SourcePresent -and $DestinationPresent) {
                Write-InstallLog ("{0} RECOVERED: move completed while reporting an error; destination={1}" -f `
                    $LogPrefix, $Destination)
                return
            }
            if (-not $SourcePresent -or $DestinationPresent) {
                throw ("{0}状态不明确，未继续重试。sourcePresent={1}; destinationPresent={2}; error={3}" -f `
                    $OperationLabel, $SourcePresent, $DestinationPresent, `
                    $MoveError.Exception.Message)
            }
            if (-not (Test-RetryableDirectoryMoveError $MoveError)) {
                throw
            }
            if ($Attempt -ge $MaximumAttempts) {
                throw ("{0}被安全软件或其他进程持续占用，安装器已在同一进程中自动重试 {1} 次但仍无法完成。未覆盖目标目录。原始错误：{2}" -f `
                    $OperationLabel, $MaximumAttempts, $MoveError.Exception.Message)
            }
            if ($Attempt -eq 1) {
                Write-Host ("{0}正被安全软件或其他进程占用；安装器将自动等待并继续，无需重新运行。" -f `
                    $OperationLabel) -ForegroundColor Yellow
            } elseif (($Attempt % 5) -eq 0) {
                Write-Host ("仍在等待{0}释放（已重试 {1} 次）..." -f `
                    $OperationLabel, $Attempt) -ForegroundColor Yellow
            }
            Write-InstallLog ("{0} RETRY {1}/{2}: {3}" -f `
                $LogPrefix, $Attempt, $MaximumAttempts, `
                $MoveError.Exception.Message)
            Start-Sleep -Milliseconds $DelayMilliseconds
            $DelayMilliseconds = [Math]::Min($DelayMilliseconds * 2, 5000)
        }
    }
}

function Publish-StagedRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Move-DirectoryAtomicallyWithRetry `
        -Source $Source `
        -Destination $Destination `
        -OperationLabel "暂存运行时" `
        -LogPrefix "RUNTIME PUBLISH"
}

function Publish-FileAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "待发布文件不存在：$Source"
    }
    $Parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    $Temporary = Join-Path $Parent (
        ".{0}.tmp-{1}" -f ([IO.Path]::GetFileName($Destination)), `
            [guid]::NewGuid().ToString("N")
    )
    try {
        [IO.File]::Copy($Source, $Temporary, $false)
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            [IO.File]::Replace($Temporary, $Destination, $null, $true)
        } elseif (Test-Path -LiteralPath $Destination) {
            throw "发布目标已存在但不是普通文件：$Destination"
        } else {
            [IO.File]::Move($Temporary, $Destination)
        }
    } finally {
        if (Test-Path -LiteralPath $Temporary -PathType Leaf) {
            Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Install-BrowserAgentSettingsTool {
    param(
        [Parameter(Mandatory = $true)][string]$ToolkitRoot,
        [Parameter(Mandatory = $true)][string]$AgentRoot,
        [Parameter(Mandatory = $true)][string]$StagingRoot,
        [Parameter(Mandatory = $true)][string]$ToolkitVersion
    )
    if ($ToolkitVersion -notmatch '^\d+\.\d+\.\d+$') {
        throw "设置工具版本不合法：$ToolkitVersion"
    }
    $Files = @(
        "config\windows-mcp-environment.json",
        "scripts\BROWSER-AGENT-SETTINGS.ps1",
        "scripts\configure_windows_pilot.py",
        "scripts\register_claude_user_mcp.py",
        "scripts\smoke_playwright_mcp.py",
        "scripts\windows-tool-discovery.ps1",
        "templates\CLAUDE.browser.md",
        "tools\browser_agent.py"
    )
    $LauncherSource = Join-Path $ToolkitRoot `
        "scripts\BROWSER-AGENT-SETTINGS.cmd"
    foreach ($RelativePath in @($Files) + @(
        "scripts\BROWSER-AGENT-SETTINGS.cmd"
    )) {
        $SourcePath = Join-Path $ToolkitRoot $RelativePath
        if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
            throw "迁移包缺少设置工具文件：$SourcePath"
        }
    }

    $MaintenanceRoot = Join-Path $AgentRoot "maintenance"
    $VersionRoot = Join-Path $MaintenanceRoot $ToolkitVersion
    $StagedVersionRoot = Join-Path $StagingRoot (
        "settings-tool-" + [guid]::NewGuid().ToString("N")
    )
    New-Item -ItemType Directory -Path $StagedVersionRoot -Force | Out-Null
    foreach ($RelativePath in $Files) {
        $SourcePath = Join-Path $ToolkitRoot $RelativePath
        $DestinationPath = Join-Path $StagedVersionRoot $RelativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $DestinationPath) `
            -Force | Out-Null
        Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath
    }

    $ExistingIsValid = Test-Path -LiteralPath $VersionRoot -PathType Container
    if ($ExistingIsValid) {
        foreach ($RelativePath in $Files) {
            $ExistingPath = Join-Path $VersionRoot $RelativePath
            $SourcePath = Join-Path $ToolkitRoot $RelativePath
            if (-not (Test-Path -LiteralPath $ExistingPath -PathType Leaf) -or
                (Get-FileHash -LiteralPath $ExistingPath -Algorithm SHA256).Hash -ne
                (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash) {
                $ExistingIsValid = $false
                break
            }
        }
    }
    if (Test-Path -LiteralPath $VersionRoot) {
        if ($ExistingIsValid) {
            Remove-Item -LiteralPath $StagedVersionRoot -Recurse -Force
        } else {
            if (-not (Test-Path -LiteralPath $VersionRoot -PathType Container)) {
                throw "设置工具版本路径已存在但不是目录：$VersionRoot"
            }
            $QuarantineRoot = Join-Path $AgentRoot (
                "backups\settings-tool-{0}-{1}" -f `
                    (Get-Date -Format "yyyyMMdd-HHmmss-fff"), `
                    [guid]::NewGuid().ToString("N").Substring(0, 8)
            )
            New-Item -ItemType Directory -Path (Split-Path -Parent $QuarantineRoot) `
                -Force | Out-Null
            $ExistingToolQuarantined = $false
            try {
                Move-DirectoryAtomicallyWithRetry `
                    -Source $VersionRoot `
                    -Destination $QuarantineRoot `
                    -OperationLabel "旧设置工具隔离" `
                    -LogPrefix "SETTINGS TOOL QUARANTINE"
                $ExistingToolQuarantined = $true
                Move-DirectoryAtomicallyWithRetry `
                    -Source $StagedVersionRoot `
                    -Destination $VersionRoot `
                    -OperationLabel "设置工具发布" `
                    -LogPrefix "SETTINGS TOOL PUBLISH"
            } catch {
                $PublishError = $_
                if ($ExistingToolQuarantined -and
                    -not (Test-Path -LiteralPath $VersionRoot) -and
                    (Test-Path -LiteralPath $QuarantineRoot `
                        -PathType Container)) {
                    try {
                        Move-DirectoryAtomicallyWithRetry `
                            -Source $QuarantineRoot `
                            -Destination $VersionRoot `
                            -OperationLabel "旧设置工具恢复" `
                            -LogPrefix "SETTINGS TOOL RESTORE"
                    } catch {
                        throw ("{0}; 旧设置工具恢复失败：{1}" -f `
                            $PublishError.Exception.Message, `
                            $_.Exception.Message)
                    }
                }
                throw $PublishError
            }
        }
    } else {
        New-Item -ItemType Directory -Path $MaintenanceRoot -Force | Out-Null
        Move-DirectoryAtomicallyWithRetry `
            -Source $StagedVersionRoot `
            -Destination $VersionRoot `
            -OperationLabel "设置工具发布" `
            -LogPrefix "SETTINGS TOOL PUBLISH"
    }

    $LauncherDestination = Join-Path $AgentRoot `
        "BROWSER-AGENT-SETTINGS.cmd"
    Publish-FileAtomically -Source $LauncherSource -Destination $LauncherDestination
    $VersionMarkerSource = Join-Path $StagingRoot (
        "current-version-" + [guid]::NewGuid().ToString("N") + ".txt"
    )
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText(
        $VersionMarkerSource,
        $ToolkitVersion + [Environment]::NewLine,
        $Utf8NoBom
    )
    Publish-FileAtomically `
        -Source $VersionMarkerSource `
        -Destination (Join-Path $MaintenanceRoot "current-version.txt")
    Remove-Item -LiteralPath $VersionMarkerSource -Force -ErrorAction SilentlyContinue
    Write-InstallLog "SETTINGS TOOL INSTALLED: $LauncherDestination"
}

function Get-NodeInfo {
    param([string]$Executable)
    $Output = @()
    $ExitCode = 1
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $Output = & $Executable --version 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    $Text = ((@($Output) | ForEach-Object { [string]$_ }) -join [Environment]::NewLine).Trim()
    if ($ExitCode -ne 0) {
        throw "$Executable --version failed with exit code ${ExitCode}: $Text"
    }
    $Match = [regex]::Match($Text, '^v?(\d+)\.(\d+)\.(\d+)$')
    if (-not $Match.Success) {
        throw "无法读取 Node.js 版本：$Text"
    }
    return [pscustomobject]@{
        Executable = $Executable
        Text = $Text
        Version = [version]("{0}.{1}.{2}" -f `
            $Match.Groups[1].Value, $Match.Groups[2].Value, $Match.Groups[3].Value)
    }
}

function Get-BrowserExecutable {
    param([ValidateSet("chrome", "msedge")][string]$Channel)
    $Executable = if ($Channel -eq "chrome") { "chrome.exe" } else { "msedge.exe" }
    $Command = Get-Command $Executable -ErrorAction SilentlyContinue
    if ($Command) {
        $Resolved = if ($Command.Source) { $Command.Source } else { $Command.Path }
        if ($Resolved -and (Test-Path -LiteralPath $Resolved -PathType Leaf)) {
            return $Resolved
        }
    }
    $RelativePath = if ($Channel -eq "chrome") {
        "Google\Chrome\Application\chrome.exe"
    } else {
        "Microsoft\Edge\Application\msedge.exe"
    }
    foreach ($Base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if ($Base -and (Test-Path -LiteralPath (Join-Path $Base $RelativePath) -PathType Leaf)) {
            return (Join-Path $Base $RelativePath)
        }
    }
    return ""
}

function Test-BrowserInstalled {
    param([ValidateSet("chrome", "msedge")][string]$Channel)
    return [bool](Get-BrowserExecutable $Channel)
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $Encoding)
}

function Test-PlaywrightExtension {
    param(
        [string]$Checker,
        [string]$LocalAppData,
        [ValidateSet("chrome", "msedge")][string]$Channel,
        [string]$ExpectedVersion,
        [string]$ApprovedUnpackedPath
    )
    $Prefix = $script:PythonPrefix
    $Output = @()
    $ExitCode = 1
    $PreviousErrorActionPreference = $ErrorActionPreference
    $HadDontWriteBytecode = Test-Path Env:PYTHONDONTWRITEBYTECODE
    $PreviousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    try {
        $ErrorActionPreference = "Continue"
        $env:PYTHONDONTWRITEBYTECODE = "1"
        $Output = & $script:PythonExe @Prefix $Checker "check" `
            "--local-app-data" $LocalAppData `
            "--browser-channel" $Channel `
            "--expected-version" $ExpectedVersion `
            "--approved-unpacked-path" $ApprovedUnpackedPath `
            "--quiet" 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        if ($HadDontWriteBytecode) {
            $env:PYTHONDONTWRITEBYTECODE = $PreviousDontWriteBytecode
        } else {
            Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
        }
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($ExitCode -eq 0) {
        return $true
    }
    if ($ExitCode -eq 3) {
        return $false
    }
    foreach ($Line in @($Output)) {
        if ($null -ne $Line) {
            Write-Host $Line
            Write-InstallLog "EXTENSION CHECK: $Line"
        }
    }
    throw "Playwright Extension 检测失败，退出码 $ExitCode。"
}

function Ensure-CurrentUserRegistryKey {
    param([string]$SubKey)
    $CurrentRegistryPath = "HKCU:\"
    foreach ($RegistryPathPart in @($SubKey -split '\\')) {
        if (-not $RegistryPathPart) {
            continue
        }
        $CurrentRegistryPath = Join-Path $CurrentRegistryPath $RegistryPathPart
        if (-not (Test-Path -LiteralPath $CurrentRegistryPath `
            -PathType Container)) {
            New-Item -Path $CurrentRegistryPath -Force -ErrorAction Stop |
                Out-Null
            $script:ExtensionPolicyCreatedPaths += $CurrentRegistryPath
        }
        if (-not (Test-Path -LiteralPath $CurrentRegistryPath `
            -PathType Container)) {
            throw "浏览器扩展策略路径分段创建后不可见：$CurrentRegistryPath"
        }
    }
    return $CurrentRegistryPath
}

function Restore-ExtensionPolicyChange {
    if ((-not $script:ExtensionPolicyChangeStarted -and
            -not $script:ExtensionPolicyKeyCreated -and
            $script:ExtensionPolicyCreatedPaths.Count -eq 0) -or
        $script:ExtensionPolicyCommitted -or
        -not $script:ExtensionPolicyPath) {
        return
    }
    if ($script:ExtensionPolicyChangeStarted -and
        $script:ExtensionPolicyValueName) {
        if ($script:ExtensionPolicyValueWasPresent) {
            New-ItemProperty -LiteralPath $script:ExtensionPolicyPath `
                -Name $script:ExtensionPolicyValueName `
                -Value $script:ExtensionPolicyPreviousValue `
                -PropertyType $script:ExtensionPolicyPreviousValueKind `
                -Force | Out-Null
            $RestoredPolicyKey = Get-Item `
                -LiteralPath $script:ExtensionPolicyPath
            $RestoredPolicyValue = $RestoredPolicyKey.GetValue(
                $script:ExtensionPolicyValueName,
                $null,
                [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
            )
            $RestoredPolicyValueKind = `
                $RestoredPolicyKey.GetValueKind($script:ExtensionPolicyValueName)
            if ($RestoredPolicyValueKind -ne `
                    $script:ExtensionPolicyPreviousValueKind -or
                [string]$RestoredPolicyValue -cne `
                    [string]$script:ExtensionPolicyPreviousValue) {
                throw "浏览器扩展策略原值恢复后核对不一致。"
            }
            Write-InstallLog "ROLLBACK: restored previous browser extension policy"
        } elseif (Test-Path -LiteralPath $script:ExtensionPolicyPath) {
            Remove-ItemProperty -LiteralPath $script:ExtensionPolicyPath `
                -Name $script:ExtensionPolicyValueName -Force -ErrorAction Stop
            $RestoredPolicyKey = Get-Item `
                -LiteralPath $script:ExtensionPolicyPath
            if (@($RestoredPolicyKey.GetValueNames()) -contains `
                $script:ExtensionPolicyValueName) {
                throw "浏览器扩展策略新增值删除后仍然存在。"
            }
            Write-InstallLog "ROLLBACK: removed newly added browser extension policy"
        }
    }
    for ($CreatedPolicyIndex =
            $script:ExtensionPolicyCreatedPaths.Count - 1;
        $CreatedPolicyIndex -ge 0;
        $CreatedPolicyIndex -= 1) {
        $CreatedPolicyPath =
            $script:ExtensionPolicyCreatedPaths[$CreatedPolicyIndex]
        try {
            if (-not (Test-Path -LiteralPath $CreatedPolicyPath `
                -PathType Container)) {
                continue
            }
            $RollbackPolicyKey = Get-Item -LiteralPath $CreatedPolicyPath
            if (@($RollbackPolicyKey.GetValueNames()).Count -eq 0 -and
                $RollbackPolicyKey.SubKeyCount -eq 0) {
                Remove-Item -LiteralPath $CreatedPolicyPath -Force
                Write-InstallLog (
                    "ROLLBACK: removed empty browser extension policy key: {0}" -f `
                        $CreatedPolicyPath
                )
            } else {
                Write-InstallLog (
                    "ROLLBACK: retained non-empty browser extension policy key: {0}" -f `
                        $CreatedPolicyPath
                )
            }
        } catch {
            # Creating an empty key is optional preparation. If inherited policy
            # ACLs allow key creation but deny cleanup, the empty key has no
            # browser effect and must not prevent the supported manual path.
            Write-InstallLog (
                "WARNING: could not remove empty browser extension policy key: {0}" -f `
                    $_.Exception.Message
            )
        }
    }
    $script:ExtensionPolicyChangeStarted = $false
    $script:ExtensionPolicyKeyCreated = $false
    $script:ExtensionPolicyCreatedPaths = @()
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "此安装向导只能在 Windows 上运行。"
    }
    $NativeArchitecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $NativeArchitecture) {
        $NativeArchitecture = $env:PROCESSOR_ARCHITECTURE
    }
    if (-not [Environment]::Is64BitOperatingSystem -or $NativeArchitecture -ne "AMD64") {
        throw "Windows 试点只支持 x64/AMD64 系统；当前为 $NativeArchitecture。"
    }

    if (-not $LogPath) {
        $LogPath = Join-Path $env:TEMP "IntranetBrowserAgent\INSTALL-WINDOWS-PILOT.log"
        $script:LogPath = $LogPath
    }
    try {
        $LogParent = Split-Path -Parent $LogPath
        New-Item -ItemType Directory -Path $LogParent -Force | Out-Null
        $LogEncoding = New-Object System.Text.UTF8Encoding($true)
        [IO.File]::WriteAllText(
            $LogPath,
            ("Windows pilot installer started {0}{1}" -f (Get-Date -Format "o"), [Environment]::NewLine),
            $LogEncoding
        )
    } catch {
        Write-Host "警告：无法创建安装日志 $LogPath" -ForegroundColor Yellow
    }

    Write-Step 1 "检查环境并验证迁移包"
    $PyCommand = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($PyCommand) {
        $script:PythonExe = $PyCommand.Source
        $script:PythonPrefix = @("-3")
    } else {
        $PythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if (-not $PythonCommand) {
            throw "未找到 Python 3.10+；请先安装 Python。"
        }
        $script:PythonExe = $PythonCommand.Source
    }
    Invoke-Python @("-c", "import sys; raise SystemExit(sys.version_info < (3, 10))")
    if (-not $env:USERPROFILE -or -not $env:LOCALAPPDATA) {
        throw "当前 Windows 用户缺少 USERPROFILE 或 LOCALAPPDATA。"
    }
    if (([string]$env:USERPROFILE) -notmatch '^[A-Za-z]:[\\/]' -or
        ([string]$env:LOCALAPPDATA) -notmatch '^[A-Za-z]:[\\/]') {
        throw "USERPROFILE 和 LOCALAPPDATA 必须是本机盘符绝对路径。"
    }
    $ClaudeInvocation = Resolve-ClaudeCodeInvocation
    if ($null -eq $ClaudeInvocation) {
        throw "未找到可用的 Claude Code。安装器支持现有原生 claude.exe 或 npm 生成的 claude.cmd，但不会安装、升级或修复 Claude Code；请确认当前用户可直接运行 claude 后重试。"
    }
    $ClaudeVersionArguments = @($ClaudeInvocation.Prefix) + @("--version")
    Invoke-External ([string]$ClaudeInvocation.Executable) $ClaudeVersionArguments
    Write-InstallLog ("CLAUDE: kind={0}; command={1}" -f `
        $ClaudeInvocation.Kind, $ClaudeInvocation.CommandPath)

    $ToolkitRoot = Join-Path $PSScriptRoot "toolkit"
    $Verifier = Join-Path $ToolkitRoot "scripts\verify-bundle.py"
    $ReleaseMetadataVerifier = Join-Path $ToolkitRoot `
        "scripts\validate_windows_release_metadata.py"
    $NodeDistributionVerifier = Join-Path $ToolkitRoot "scripts\validate_node_distribution.py"
    $NodeSourceApprovals = Join-Path $ToolkitRoot "config\windows-node-sources.json"
    $Configurator = Join-Path $ToolkitRoot "scripts\configure_windows_pilot.py"
    $ExtensionChecker = Join-Path $ToolkitRoot "scripts\check_playwright_extension.py"
    $ExtensionVerifier = Join-Path $ToolkitRoot "scripts\validate_playwright_extension.py"
    $ExtensionApproval = Join-Path $ToolkitRoot "config\playwright-extension-source.json"
    $McpRegistrar = Join-Path $ToolkitRoot "scripts\register_claude_user_mcp.py"
    $McpSmoke = Join-Path $ToolkitRoot "scripts\smoke_playwright_mcp.py"
    $BrowserAgent = Join-Path $ToolkitRoot "tools\browser_agent.py"
    $SettingsLauncher = Join-Path $ToolkitRoot `
        "scripts\BROWSER-AGENT-SETTINGS.cmd"
    $SettingsScript = Join-Path $ToolkitRoot `
        "scripts\BROWSER-AGENT-SETTINGS.ps1"
    $KitMetadataPath = Join-Path $PSScriptRoot "KIT-METADATA.json"
    foreach ($RequiredPath in @(
        $Verifier,
        $ReleaseMetadataVerifier,
        $NodeDistributionVerifier,
        $NodeSourceApprovals,
        $Configurator,
        $ExtensionChecker,
        $ExtensionVerifier,
        $ExtensionApproval,
        $McpRegistrar,
        $McpSmoke,
        $BrowserAgent,
        $SettingsLauncher,
        $SettingsScript,
        $KitMetadataPath
    )) {
        if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
            throw "迁移包不完整，缺少：$RequiredPath"
        }
    }
    Invoke-Python @($Verifier, $PSScriptRoot)
    Invoke-Python @($ReleaseMetadataVerifier, $KitMetadataPath)

    $KitMetadata = Get-Content -LiteralPath $KitMetadataPath -Raw | ConvertFrom-Json
    $RuntimeMetadata = $KitMetadata.runtime
    $BuildMetadata = $RuntimeMetadata.buildMetadata
    $ToolkitVersion = [string]$KitMetadata.toolkitVersion
    if ($ToolkitVersion -ne [string]$BuildMetadata.runtimeVersion) {
        throw "迁移包设置工具版本与运行时版本不一致。"
    }
    $BrowserExtensionProperty = $KitMetadata.PSObject.Properties["browserExtension"]
    if ($null -eq $BrowserExtensionProperty -or
        $null -eq $BrowserExtensionProperty.Value) {
        throw "迁移包缺少离线 Playwright Extension 元数据。"
    }
    $BrowserExtensionMetadata = $BrowserExtensionProperty.Value
    foreach ($MetadataField in @(
        "extensionId", "version", "path", "unpackedPath", "installation"
    )) {
        if ($null -eq $BrowserExtensionMetadata.PSObject.Properties[$MetadataField]) {
            throw "迁移包的 Playwright Extension 元数据缺少字段：$MetadataField"
        }
    }
    $ExtensionId = [string]$BrowserExtensionMetadata.extensionId
    $ExtensionVersion = [string]$BrowserExtensionMetadata.version
    $ExtensionRelativePath = [string]$BrowserExtensionMetadata.path
    $ExtensionUnpackedRelativePath = [string]$BrowserExtensionMetadata.unpackedPath
    $ExtensionInstallation = [string]$BrowserExtensionMetadata.installation
    if ($ExtensionId -ne "mmlmfjhmonkocbjadbfplnigmagldckm" -or
        $ExtensionVersion -notmatch '^\d+\.\d+\.\d+$' -or
        $ExtensionRelativePath -notmatch '^browser-extension/playwright-extension-[0-9.]+\.crx$' -or
        $ExtensionUnpackedRelativePath -ne "browser-extension/unpacked" -or
        $ExtensionInstallation -ne "offline-user-policy-with-manual-unpacked-fallback") {
        throw "迁移包的 Playwright Extension 元数据不合法。"
    }
    $ExtensionSourcePath = Join-Path $PSScriptRoot ($ExtensionRelativePath -replace '/', '\')
    $ExtensionUnpackedSourcePath = Join-Path $PSScriptRoot `
        ($ExtensionUnpackedRelativePath -replace '/', '\')
    if (-not (Test-Path -LiteralPath $ExtensionSourcePath -PathType Leaf)) {
        throw "迁移包缺少离线 Playwright Extension：$ExtensionSourcePath"
    }
    if (-not (Test-Path -LiteralPath $ExtensionUnpackedSourcePath -PathType Container)) {
        throw "迁移包缺少已解压 Playwright Extension：$ExtensionUnpackedSourcePath"
    }
    Invoke-Python @(
        $ExtensionVerifier,
        $ExtensionSourcePath,
        "--approval-file", $ExtensionApproval,
        "--unpacked-directory", $ExtensionUnpackedSourcePath
    )
    $RuntimeArchiveName = [string]$RuntimeMetadata.archive
    if ($RuntimeArchiveName -notmatch '^browser-agent-runtime-[a-zA-Z0-9._-]+\.tar\.gz$') {
        throw "运行包文件名不合法：$RuntimeArchiveName"
    }
    $RuntimeArchive = Join-Path (Join-Path $PSScriptRoot "runtime") $RuntimeArchiveName
    Invoke-Python @($Verifier, $RuntimeArchive)
    $BundledNode = $BuildMetadata.bundledNode -eq $true
    $ExpectedNodeVersion = [string]$BuildMetadata.tools.node
    if (-not $BundledNode) {
        throw "Windows 一键试点包必须携带已校验的 Node.js；当前运行包未携带。"
    }
    if ($ExpectedNodeVersion -notmatch '^v\d+\.\d+\.\d+$') {
        throw "运行包记录的 Node.js 版本不合法：$ExpectedNodeVersion"
    }
    $NodeExe = ""
    Write-Host "系统 Node.js 不参与安装；将使用迁移包内的 $ExpectedNodeVersion。"

    Write-Step 2 "自动识别浏览器并准备当前用户目录"
    if ($BrowserChannel -eq "auto") {
        if (Test-BrowserInstalled "chrome") {
            $BrowserChannel = "chrome"
        } elseif (Test-BrowserInstalled "msedge") {
            $BrowserChannel = "msedge"
        } else {
            throw "未找到 Chrome 或 Edge。"
        }
        Write-Host "已自动选择浏览器：$BrowserChannel"
    } elseif (-not (Test-BrowserInstalled $BrowserChannel)) {
        throw "未找到指定浏览器：$BrowserChannel"
    }
    $BrowserExecutable = Get-BrowserExecutable $BrowserChannel
    if (-not $BrowserExecutable) {
        throw "无法解析浏览器可执行文件：$BrowserChannel"
    }
    $ProfileOwner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $LocalAppDataRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd("\")
    $AgentRoot = [IO.Path]::GetFullPath((Join-Path $LocalAppDataRoot "IntranetBrowserAgent"))
    if (-not $AgentRoot.StartsWith(
        $LocalAppDataRoot + "\",
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "用户安装目录必须位于 LOCALAPPDATA 下。"
    }
    $ReleaseRoot = Join-Path $AgentRoot "releases"
    $RuntimeName = $RuntimeArchiveName -replace '\.tar\.gz$', ''
    $RuntimeRoot = Join-Path $ReleaseRoot $RuntimeName
    $ConfigRoot = Join-Path $AgentRoot "config\pilot"
    $OutputDirectory = Join-Path $AgentRoot "output\pilot"
    $ExtensionInstallRoot = Join-Path $AgentRoot "browser-extension\$ExtensionVersion"
    $InstalledExtensionCrx = Join-Path $ExtensionInstallRoot ([IO.Path]::GetFileName($ExtensionSourcePath))
    $InstalledExtensionUnpacked = Join-Path $ExtensionInstallRoot "unpacked"
    $PlaywrightExtensionAlreadyInstalled = Test-PlaywrightExtension `
        $ExtensionChecker $LocalAppDataRoot $BrowserChannel $ExtensionVersion `
        $InstalledExtensionUnpacked
    Invoke-Python @(
        $Configurator,
        "assert-user-paths",
        "--local-app-data", $LocalAppDataRoot,
        "--path", $AgentRoot,
        "--path", $ReleaseRoot,
        "--path", $RuntimeRoot,
        "--path", $ConfigRoot,
        "--path", $OutputDirectory,
        "--path", $ExtensionInstallRoot,
        "--path", $InstalledExtensionUnpacked,
        "--path", (Join-Path $AgentRoot "staging"),
        "--path", (Join-Path $AgentRoot "backups"),
        "--path", (Join-Path $AgentRoot "maintenance"),
        "--path", (Join-Path $AgentRoot "BROWSER-AGENT-SETTINGS.cmd"),
        "--path", (Join-Path $AgentRoot ".install.lock")
    )

    Write-Host ""
    Write-Host "即将部署：" -ForegroundColor Cyan
    Write-Host "  安装范围：当前用户的全部 Claude Code 项目"
    Write-Host "  安装目录：$AgentRoot"
    Write-Host "  浏览器：$BrowserChannel"
    Write-Host "  浏览器登录态：连接当前 Profile 中已登录的标签页"
    Write-Host "  Playwright Extension：$ExtensionVersion（离线包内安装）"
    Write-Host "  运行账号：$ProfileOwner"

    Write-Step 3 "安装并验证固定运行时"
    New-Item -ItemType Directory -Path $AgentRoot -Force | Out-Null
    $InstallLockPath = Join-Path $AgentRoot ".install.lock"
    try {
        $InstallLockStream = [IO.File]::Open(
            $InstallLockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch {
        throw "另一个安装或升级进程正在使用当前用户的 Browser Agent 目录。"
    }
    New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
    $StagingRoot = Join-Path $AgentRoot "staging"
    New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null

    if (Test-Path -LiteralPath $RuntimeRoot) {
        Write-Host "运行版本已存在，验证后复用：$RuntimeRoot"
        Invoke-Python @($Verifier, $RuntimeRoot)
    } else {
        $TarCommand = Get-Command "tar.exe" -ErrorAction SilentlyContinue
        if (-not $TarCommand) {
            throw "未找到 Windows tar.exe。"
        }
        $RuntimeExtractionRoot = Join-Path $StagingRoot `
            ("r-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
        New-Item -ItemType Directory -Path $RuntimeExtractionRoot | Out-Null
        Invoke-External $TarCommand.Source @(
            "-xzf", $RuntimeArchive, "-C", $RuntimeExtractionRoot
        )
        $StagedRuntimeRoot = Join-Path $RuntimeExtractionRoot $RuntimeName
        Invoke-Python @($Verifier, $StagedRuntimeRoot)
        $StagedBundledNodeRoot = Join-Path $StagedRuntimeRoot "node"
        Invoke-Python @(
            $NodeDistributionVerifier,
            $StagedBundledNodeRoot,
            "--expected-version", $ExpectedNodeVersion,
            "--approval-file", $NodeSourceApprovals
        )
        $StagedBundledNodeInfo = Get-NodeInfo (
            Join-Path $StagedBundledNodeRoot "node.exe"
        )
        if ($StagedBundledNodeInfo.Version -lt [version]"20.19.0" -or
            $StagedBundledNodeInfo.Text -ne $ExpectedNodeVersion) {
            throw "暂存 Node.js 版本不符合运行包记录：$($StagedBundledNodeInfo.Text) / $ExpectedNodeVersion"
        }
        if (Test-Path -LiteralPath $RuntimeRoot) {
            throw "运行版本目录在安装期间被其他进程创建：$RuntimeRoot"
        }
        Publish-StagedRuntime -Source $StagedRuntimeRoot -Destination $RuntimeRoot
        Remove-Item -LiteralPath $RuntimeExtractionRoot -Recurse -Force
        $RuntimeExtractionRoot = $null
        Invoke-Python @($Verifier, $RuntimeRoot)
    }

    $BundledNodeRoot = Join-Path $RuntimeRoot "node"
    Invoke-Python @(
        $NodeDistributionVerifier,
        $BundledNodeRoot,
        "--expected-version", $ExpectedNodeVersion,
        "--approval-file", $NodeSourceApprovals
    )
    $BundledNodeExe = Join-Path $BundledNodeRoot "node.exe"
    $BundledNodeInfo = Get-NodeInfo $BundledNodeExe
    if ($BundledNodeInfo.Version -lt [version]"20.19.0" -or
        $BundledNodeInfo.Text -ne $ExpectedNodeVersion) {
        throw "包内 Node.js 版本不符合运行包记录：$($BundledNodeInfo.Text) / $ExpectedNodeVersion"
    }
    $NodeExe = $BundledNodeExe
    Write-Host "已启用包内 Node.js $($BundledNodeInfo.Text)；未修改系统 Node.js。" -ForegroundColor Green
    Write-InstallLog "Bundled Node.js selected: $BundledNodeExe ($($BundledNodeInfo.Text))"

    if ($PlaywrightExtensionAlreadyInstalled) {
        Invoke-Python @(
            $ExtensionChecker,
            "check",
            "--local-app-data", $LocalAppDataRoot,
            "--browser-channel", $BrowserChannel,
            "--expected-version", $ExtensionVersion,
            "--approved-unpacked-path", $InstalledExtensionUnpacked
        )
        $ExtensionInstallMethod = "existing"
        Write-Host "当前浏览器已安装批准的 Playwright Extension，直接复用。" -ForegroundColor Green
    } else {
        Write-Host "当前浏览器未安装 Playwright Extension；开始从迁移包离线安装。" -ForegroundColor Cyan
        $PreparedExtensionReusable = $false
        if (Test-Path -LiteralPath $ExtensionInstallRoot) {
            if (-not (Test-Path -LiteralPath $ExtensionInstallRoot -PathType Container)) {
                throw "离线扩展安装路径已存在但不是目录：$ExtensionInstallRoot"
            }
            try {
                if (-not (Test-Path -LiteralPath $InstalledExtensionCrx -PathType Leaf)) {
                    throw "离线扩展版本目录不完整：$ExtensionInstallRoot"
                }
                if (-not (Test-Path -LiteralPath $InstalledExtensionUnpacked -PathType Container)) {
                    throw "离线扩展版本目录缺少已解压内容：$ExtensionInstallRoot"
                }
                Invoke-Python @(
                    $ExtensionVerifier,
                    $InstalledExtensionCrx,
                    "--approval-file", $ExtensionApproval,
                    "--unpacked-directory", $InstalledExtensionUnpacked
                )
                $PreparedExtensionReusable = $true
                Write-InstallLog "EXTENSION PREPARED DIRECTORY REUSED: $ExtensionInstallRoot"
            } catch {
                $InvalidExtensionReason = $_.Exception.Message
                $InvalidExtensionBackupRoot = Join-Path $AgentRoot "backups"
                $InvalidExtensionBackup = Join-Path $InvalidExtensionBackupRoot `
                    ("invalid-browser-extension-{0}-{1}" -f `
                        (Get-Date -Format "yyyyMMdd-HHmmss-fff"), `
                        [guid]::NewGuid().ToString("N").Substring(0, 8))
                New-Item -ItemType Directory -Path $InvalidExtensionBackupRoot `
                    -Force | Out-Null
                Write-Host "发现上次遗留的不完整扩展目录，正在保留副本并自动重建。" `
                    -ForegroundColor Yellow
                Move-DirectoryAtomicallyWithRetry `
                    -Source $ExtensionInstallRoot `
                    -Destination $InvalidExtensionBackup `
                    -OperationLabel "不完整扩展目录隔离" `
                    -LogPrefix "EXTENSION QUARANTINE"
                Write-InstallLog (
                    "EXTENSION INVALID DIRECTORY QUARANTINED: source={0}; backup={1}; reason={2}" -f `
                        $ExtensionInstallRoot, $InvalidExtensionBackup, `
                        $InvalidExtensionReason
                )
            }
        }
        if (-not $PreparedExtensionReusable) {
            $ExtensionStagingRoot = Join-Path $StagingRoot `
                ("extension-" + [guid]::NewGuid().ToString("N"))
            New-Item -ItemType Directory -Path $ExtensionStagingRoot | Out-Null
            $StagedExtensionCrx = Join-Path $ExtensionStagingRoot `
                ([IO.Path]::GetFileName($ExtensionSourcePath))
            $StagedExtensionUnpacked = Join-Path $ExtensionStagingRoot "unpacked"
            Copy-Item -LiteralPath $ExtensionSourcePath -Destination $StagedExtensionCrx
            Copy-Item -LiteralPath $ExtensionUnpackedSourcePath `
                -Destination $StagedExtensionUnpacked -Recurse
            Invoke-Python @(
                $ExtensionVerifier,
                $StagedExtensionCrx,
                "--approval-file", $ExtensionApproval,
                "--unpacked-directory", $StagedExtensionUnpacked
            )
            New-Item -ItemType Directory -Path (Split-Path -Parent $ExtensionInstallRoot) `
                -Force | Out-Null
            Move-DirectoryAtomicallyWithRetry `
                -Source $ExtensionStagingRoot `
                -Destination $ExtensionInstallRoot `
                -OperationLabel "离线扩展目录" `
                -LogPrefix "EXTENSION PUBLISH"
        }
        Invoke-Python @(
            $ExtensionVerifier,
            $InstalledExtensionCrx,
            "--approval-file", $ExtensionApproval,
            "--unpacked-directory", $InstalledExtensionUnpacked
        )

        $ExtensionPolicySubKey = if ($BrowserChannel -eq "chrome") {
            "Software\Policies\Google\Chrome\ExtensionInstallForcelist"
        } else {
            "Software\Policies\Microsoft\Edge\ExtensionInstallForcelist"
        }
        $ExtensionPolicyPath = "HKCU:\$ExtensionPolicySubKey"
        $PolicyAttemptAvailable = $false
        try {
            $CrxUri = ([Uri]$InstalledExtensionCrx).AbsoluteUri
            $UpdateManifestPath = Join-Path $ExtensionInstallRoot "updates.xml"
            $EscapedCrxUri = [Security.SecurityElement]::Escape($CrxUri)
            $UpdateManifest = @"
<?xml version="1.0" encoding="UTF-8"?>
<gupdate xmlns="http://www.google.com/update2/response" protocol="2.0">
  <app appid="$ExtensionId">
    <updatecheck codebase="$EscapedCrxUri" version="$ExtensionVersion" />
  </app>
</gupdate>
"@
            Write-Utf8NoBom $UpdateManifestPath $UpdateManifest
            $UpdateManifestUri = ([Uri]$UpdateManifestPath).AbsoluteUri

            $ExtensionPolicyKeyWasPresent = Test-Path `
                -LiteralPath $ExtensionPolicyPath
            if (-not $ExtensionPolicyKeyWasPresent) {
                $CreatedExtensionPolicyPath =
                    Ensure-CurrentUserRegistryKey $ExtensionPolicySubKey
                if ($CreatedExtensionPolicyPath -cne $ExtensionPolicyPath) {
                    throw "浏览器扩展策略键创建结果不一致。"
                }
                $ExtensionPolicyKeyCreated =
                    $ExtensionPolicyCreatedPaths -contains $ExtensionPolicyPath
            }
            $PolicyKey = Get-Item -LiteralPath $ExtensionPolicyPath
            $PolicyValueNames = @($PolicyKey.GetValueNames())
            foreach ($PolicyValueName in $PolicyValueNames) {
                $PolicyValue = [string]$PolicyKey.GetValue(
                    $PolicyValueName,
                    "",
                    [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
                )
                if ($PolicyValue -match `
                    ("^" + [regex]::Escape($ExtensionId) + ";")) {
                    $ExtensionPolicyValueName = $PolicyValueName
                    break
                }
            }
            if (-not $ExtensionPolicyValueName) {
                $PolicyIndex = 1
                while ($PolicyValueNames -contains [string]$PolicyIndex) {
                    $PolicyIndex += 1
                }
                $ExtensionPolicyValueName = [string]$PolicyIndex
            }
            $ExtensionPolicyValueWasPresent = `
                $PolicyValueNames -contains $ExtensionPolicyValueName
            if ($ExtensionPolicyValueWasPresent) {
                $ExtensionPolicyPreviousValue = $PolicyKey.GetValue(
                    $ExtensionPolicyValueName,
                    $null,
                    [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
                )
                $ExtensionPolicyPreviousValueKind = `
                    $PolicyKey.GetValueKind($ExtensionPolicyValueName)
            }
            $RequestedPolicyValue = "$ExtensionId;$UpdateManifestUri"
            New-ItemProperty -LiteralPath $ExtensionPolicyPath `
                -Name $ExtensionPolicyValueName `
                -Value $RequestedPolicyValue `
                -PropertyType String -Force | Out-Null
            # Registry SetValue is atomic. Only mark the policy as changed after
            # the provider confirms the write, so an access-denied write does not
            # trigger a second forbidden write during optional-policy fallback.
            $ExtensionPolicyChangeStarted = $true
            $WrittenPolicyKey = Get-Item -LiteralPath $ExtensionPolicyPath
            $WrittenPolicyValue = [string]$WrittenPolicyKey.GetValue(
                $ExtensionPolicyValueName,
                ""
            )
            $WrittenPolicyValueKind = `
                $WrittenPolicyKey.GetValueKind($ExtensionPolicyValueName)
            if ($WrittenPolicyValue -ne $RequestedPolicyValue -or
                $WrittenPolicyValueKind -ne [Microsoft.Win32.RegistryValueKind]::String) {
                throw "浏览器扩展策略写入后核对不一致。"
            }
            Write-InstallLog (
                "Offline extension policy configured: {0}\{1}" -f `
                    $ExtensionPolicyPath, $ExtensionPolicyValueName
            )

            Start-Process -FilePath $BrowserExecutable `
                -ArgumentList @("about:blank")
            $PolicyAttemptAvailable = $true
            Write-Host "已尝试当前用户离线策略安装，正在等待浏览器确认……"
        } catch {
            $PolicyAttemptError = $_
            if ($ExtensionPolicyChangeStarted -or
                $ExtensionPolicyKeyCreated -or
                $ExtensionPolicyCreatedPaths.Count -gt 0) {
                try {
                    Restore-ExtensionPolicyChange
                } catch {
                    throw (
                        "自动浏览器策略不可用，且无法安全恢复本次策略变更。" +
                        "原始错误：$($PolicyAttemptError.Exception.Message)；" +
                        "恢复错误：$($_.Exception.Message)"
                    )
                }
            }
            Write-InstallLog (
                "OFFLINE EXTENSION POLICY UNAVAILABLE: {0}" -f `
                    $PolicyAttemptError.Exception.Message
            )
            Write-Host (
                "浏览器自动策略安装不可用（可能受权限或企业策略限制）；" +
                "将直接进入手动加载，不会中止或要求重新运行。"
            ) -ForegroundColor Yellow
        }

        if ($PolicyAttemptAvailable) {
            $PolicyInstallDeadline = [DateTime]::UtcNow.AddSeconds(30)
            while (-not (Test-PlaywrightExtension `
                $ExtensionChecker $LocalAppDataRoot $BrowserChannel $ExtensionVersion `
                $InstalledExtensionUnpacked)) {
                if ([DateTime]::UtcNow -ge $PolicyInstallDeadline) {
                    break
                }
                Start-Sleep -Seconds 2
            }
        }
        if (-not (Test-PlaywrightExtension `
            $ExtensionChecker $LocalAppDataRoot $BrowserChannel $ExtensionVersion `
            $InstalledExtensionUnpacked)) {
            if ($ExtensionPolicyChangeStarted -or
                $ExtensionPolicyKeyCreated -or
                $ExtensionPolicyCreatedPaths.Count -gt 0) {
                Restore-ExtensionPolicyChange
            }
            $ExtensionsPage = if ($BrowserChannel -eq "chrome") {
                "chrome://extensions"
            } else {
                "edge://extensions"
            }
            try {
                Start-Process -FilePath $BrowserExecutable `
                    -ArgumentList @($ExtensionsPage)
            } catch {
                Write-InstallLog (
                    "WARNING: could not open browser extension page: {0}" -f `
                        $_.Exception.Message
                )
                Write-Host (
                    "未能自动打开扩展页；请在浏览器地址栏手动打开 " +
                    $ExtensionsPage
                ) -ForegroundColor Yellow
            }
            $ClipCommand = Get-Command "clip.exe" -ErrorAction SilentlyContinue
            if ($ClipCommand) {
                try {
                    $InstalledExtensionUnpacked | & $ClipCommand.Source
                    if ($LASTEXITCODE -eq 0) {
                        Write-Host "扩展目录已复制到剪贴板。" -ForegroundColor Green
                    } else {
                        Write-InstallLog (
                            "WARNING: clip.exe returned exit code {0}" -f `
                                $LASTEXITCODE
                        )
                    }
                } catch {
                    Write-InstallLog (
                        "WARNING: could not copy extension path to clipboard: {0}" -f `
                            $_.Exception.Message
                    )
                }
            } else {
                Write-InstallLog "WARNING: clip.exe is unavailable"
            }
            Write-Host ""
            Write-Host "请在扩展页完成以下 3 步（若未自动打开，请打开 $ExtensionsPage）：" `
                -ForegroundColor Yellow
            Write-Host "  1. 打开右上角“开发者模式”。"
            Write-Host "  2. 点击“加载已解压的扩展程序”。"
            Write-Host "  3. 在文件夹选择框粘贴或选择下面这个目录："
            Write-Host ""
            Write-Host "     $InstalledExtensionUnpacked" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "安装器正在等待；检测到扩展后会自动继续，无需重新运行安装器。" `
                -ForegroundColor Yellow
            Write-InstallLog (
                "MANUAL EXTENSION LOAD REQUIRED: {0} from {1}" -f `
                    $ExtensionsPage, $InstalledExtensionUnpacked
            )
            $ManualExtensionDeadline = if ($ManualExtensionWaitSeconds -gt 0) {
                [DateTime]::UtcNow.AddSeconds($ManualExtensionWaitSeconds)
            } else {
                [DateTime]::MaxValue
            }
            $NextWaitingMessage = [DateTime]::UtcNow.AddSeconds(30)
            while (-not (Test-PlaywrightExtension `
                $ExtensionChecker $LocalAppDataRoot $BrowserChannel $ExtensionVersion `
                $InstalledExtensionUnpacked)) {
                if ([DateTime]::UtcNow -ge $ManualExtensionDeadline) {
                    throw (
                        "未检测到手动加载的 Playwright Extension；等待目录为：" +
                        $InstalledExtensionUnpacked
                    )
                }
                if ([DateTime]::UtcNow -ge $NextWaitingMessage) {
                    Write-Host "仍在等待浏览器加载扩展；完成后会自动继续……"
                    $NextWaitingMessage = [DateTime]::UtcNow.AddSeconds(30)
                }
                Start-Sleep -Seconds 2
            }
            Write-InstallLog "MANUAL EXTENSION LOAD DETECTED"
            $ExtensionInstallMethod = "manual-unpacked"
        } else {
            $ExtensionInstallMethod = "offline-user-policy"
        }
        Invoke-Python @(
            $ExtensionChecker,
            "check",
            "--local-app-data", $LocalAppDataRoot,
            "--browser-channel", $BrowserChannel,
            "--expected-version", $ExtensionVersion,
            "--approved-unpacked-path", $InstalledExtensionUnpacked
        )
        Write-Host "Playwright Extension 已从迁移包离线安装并验证。" -ForegroundColor Green
    }
    Write-InstallLog "EXTENSION INSTALL METHOD: $ExtensionInstallMethod"

    Write-Step 4 "自动生成配置并备份旧版本"
    $StageRoot = Join-Path $StagingRoot `
        ("pilot-" + [guid]::NewGuid().ToString("N"))
    $StageManifest = Join-Path $StageRoot "deployment.windows-pilot.json"
    $StageRendered = Join-Path $StageRoot "rendered"
    $StageDeploy = Join-Path $StageRoot "deploy"
    New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null

    $GenerateArguments = @(
        $Configurator,
        "generate",
        "--template", (Join-Path $ToolkitRoot "config\deployment.windows-pilot.json.template"),
        "--manifest-out", $StageManifest,
        "--render-out", $StageRendered,
        "--runtime-root", $RuntimeRoot,
        "--config-root", $ConfigRoot,
        "--output-directory", $OutputDirectory,
        "--profile-owner", $ProfileOwner,
        "--node-executable", $NodeExe,
        "--browser-channel", $BrowserChannel,
        "--browser-executable", $BrowserExecutable
    )
    Invoke-Python $GenerateArguments
    New-Item -ItemType Directory -Path $StageDeploy -Force | Out-Null
    Copy-Item -LiteralPath $StageManifest `
        -Destination (Join-Path $StageDeploy "deployment.windows-pilot.json")
    foreach ($Name in @(
        "playwright.config.json",
        ".mcp.json",
        "deployment.lock.json",
        "CLAUDE.browser.md",
        "DEPLOYMENT.txt"
    )) {
        Copy-Item -LiteralPath (Join-Path $StageRendered $Name) `
            -Destination (Join-Path $StageDeploy $Name)
    }
    Write-Host "先对暂存配置执行预检；失败时不会改写 Claude Code 用户配置。"
    Invoke-Python @(
        $BrowserAgent,
        "preflight",
        "--manifest", $StageManifest,
        "--runtime-root", $RuntimeRoot,
        "--config-root", $StageDeploy
    )

    $Timestamp = (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    $BackupRoot = Join-Path $AgentRoot "backups\$Timestamp"
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    if (Test-Path -LiteralPath $ConfigRoot) {
        if (-not (Test-Path -LiteralPath $ConfigRoot -PathType Container)) {
            throw "配置路径已存在但不是目录：$ConfigRoot"
        }
        $ConfigWasPresent = $true
        $ConfigBackupPath = Join-Path $BackupRoot "config-pilot"
    }
    New-Item -ItemType Directory `
        -Path (Split-Path -Parent $ConfigRoot), $OutputDirectory `
        -Force | Out-Null
    $ConfigChangeStarted = $true
    if ($ConfigWasPresent) {
        Move-DirectoryAtomicallyWithRetry `
            -Source $ConfigRoot `
            -Destination $ConfigBackupPath `
            -OperationLabel "旧配置目录备份" `
            -LogPrefix "CONFIG BACKUP"
        $ConfigBackupComplete = $true
    }
    Move-DirectoryAtomicallyWithRetry `
        -Source $StageDeploy `
        -Destination $ConfigRoot `
        -OperationLabel "新配置目录发布" `
        -LogPrefix "CONFIG PUBLISH"
    $ConfigPublished = $true

    Write-Step 5 "执行目标机预检并注册用户级 MCP"
    $InstalledManifest = Join-Path $ConfigRoot "deployment.windows-pilot.json"
    Invoke-Python @(
        $BrowserAgent,
        "preflight",
        "--manifest", $InstalledManifest,
        "--runtime-root", $RuntimeRoot,
        "--config-root", $ConfigRoot
    )

    $PlaywrightCliPath = Join-Path $RuntimeRoot "node_modules\@playwright\mcp\cli.js"
    $PlaywrightConfigPath = Join-Path $ConfigRoot "playwright.config.json"
    Invoke-Python @(
        $McpSmoke,
        "--node-executable", $NodeExe,
        "--playwright-cli", $PlaywrightCliPath,
        "--playwright-config", $PlaywrightConfigPath,
        "--browser-channel", $BrowserChannel,
        "--browser-executable", $BrowserExecutable
    )

    if ($env:CLAUDE_CONFIG_DIR) {
        $ClaudeConfigDirectory = [string]$env:CLAUDE_CONFIG_DIR
        if (-not [IO.Path]::IsPathRooted($ClaudeConfigDirectory) -or
            $ClaudeConfigDirectory -notmatch '^[A-Za-z]:[\\/]') {
            throw "CLAUDE_CONFIG_DIR 必须是本机盘符绝对路径，不能使用相对路径或 ~。"
        }
        $ClaudeConfigDirectory = [IO.Path]::GetFullPath($ClaudeConfigDirectory)
        if ($ClaudeConfigDirectory -notmatch '^[A-Za-z]:\\') {
            throw "CLAUDE_CONFIG_DIR 必须是本机盘符绝对路径，不能使用 UNC 路径。"
        }
        $ClaudeUserConfigPath = Join-Path $ClaudeConfigDirectory ".claude.json"
    } else {
        $ClaudeUserConfigPath = Join-Path $env:USERPROFILE ".claude.json"
    }
    $UserConfigWasPresent = Test-Path -LiteralPath $ClaudeUserConfigPath -PathType Leaf
    if ($UserConfigWasPresent) {
        $ClaudeUserConfigBackup = Join-Path $BackupRoot "claude-user-config.json.bak"
    } else {
        $ClaudeUserConfigBackup = Join-Path $BackupRoot "claude-user-config.absent"
    }

    $McpServerName = "intranet-browser-agent"
    $RegistrarArguments = @(
        $McpRegistrar,
        "register",
        "--claude-executable", [string]$ClaudeInvocation.Executable
    )
    foreach ($ClaudePrefixArgument in @($ClaudeInvocation.Prefix)) {
        $RegistrarArguments += @(
            "--claude-prefix", [string]$ClaudePrefixArgument
        )
    }
    $RegistrarArguments += @(
        "--server-name", $McpServerName,
        "--node-executable", $NodeExe,
        "--playwright-cli", $PlaywrightCliPath,
        "--playwright-config", $PlaywrightConfigPath,
        "--browser-channel", $BrowserChannel,
        "--browser-executable", $BrowserExecutable,
        "--user-config", $ClaudeUserConfigPath,
        "--backup", $ClaudeUserConfigBackup
    )
    $UserConfigChangeStarted = $true
    Invoke-Python $RegistrarArguments
    Install-BrowserAgentSettingsTool `
        -ToolkitRoot $ToolkitRoot `
        -AgentRoot $AgentRoot `
        -StagingRoot $StagingRoot `
        -ToolkitVersion $ToolkitVersion
    $UserConfigCommitted = $true
    $ConfigCommitted = $true
    $ExtensionPolicyCommitted = $true

    Write-Step 6 "完成"
    $SummaryPath = Join-Path $AgentRoot "INSTALLATION.txt"
    $Summary = @"
Windows Browser Agent pilot is installed.

Runtime: $RuntimeRoot
Manifest: $InstalledManifest
Claude MCP scope: user (all projects for the current Windows user)
Browser mode: existing $BrowserChannel tabs through Playwright Extension $ExtensionVersion
Extension install method: $ExtensionInstallMethod
Backup: $BackupRoot
Settings: $(Join-Path $AgentRoot "BROWSER-AGENT-SETTINGS.cmd")

Next: restart Claude Code in any project, run /mcp to confirm intranet-browser-agent,
approve the browser tab connection, and perform a read-only page-title test first.
"@
    $SummaryWritten = $false
    try {
        Write-Utf8NoBom $SummaryPath $Summary
        $SummaryWritten = $true
    } catch {
        Write-InstallLog "WARNING: could not write installation summary: $($_.Exception.Message)"
        Write-Host "警告：安装已完成，但无法写入安装摘要。" -ForegroundColor Yellow
    }
    Write-InstallLog "SUCCESS: installation and preflight completed"
    Write-Host "安装和 preflight 已完成。" -ForegroundColor Green
    Write-Host "下一步：重启 Claude Code，在任意项目中输入 /mcp 查看 intranet-browser-agent。"
    Write-Host "首次调用浏览器工具时，在 Playwright Extension 页面选择允许控制的现有标签页。"
    Write-Host "后续切换授权、无头或独立 Profile：$AgentRoot\BROWSER-AGENT-SETTINGS.cmd"
    if ($SummaryWritten) {
        Write-Host "安装摘要：$SummaryPath"
    }
} catch {
    Write-InstallLog "FAILED: $($_.Exception.Message)"
    if ($_.ScriptStackTrace) {
        Write-InstallLog "STACK: $($_.ScriptStackTrace)"
    }
    Write-Host ""
    Write-Host "安装已安全停止：$($_.Exception.Message)" -ForegroundColor Red
    if ($script:LogPath) {
        Write-Host "详细日志：$($script:LogPath)"
    }
    if ($UserConfigChangeStarted -and -not $UserConfigCommitted -and $ClaudeUserConfigPath) {
        try {
            if ($UserConfigWasPresent -and $ClaudeUserConfigBackup -and
                (Test-Path -LiteralPath $ClaudeUserConfigBackup -PathType Leaf)) {
                Copy-Item -LiteralPath $ClaudeUserConfigBackup `
                    -Destination $ClaudeUserConfigPath -Force
                Write-InstallLog "ROLLBACK: restored Claude Code user configuration"
            } elseif (-not $UserConfigWasPresent -and
                (Test-Path -LiteralPath $ClaudeUserConfigPath -PathType Leaf)) {
                Remove-Item -LiteralPath $ClaudeUserConfigPath -Force
                Write-InstallLog "ROLLBACK: removed newly created Claude Code user configuration"
            }
        } catch {
            Write-InstallLog "ROLLBACK FAILED: $($_.Exception.Message)"
        }
    }
    if ($ConfigChangeStarted -and -not $ConfigCommitted -and $ConfigRoot) {
        try {
            if ($ConfigWasPresent) {
                if ($ConfigBackupComplete) {
                    if (-not $ConfigPublished -and
                        (Test-Path -LiteralPath $ConfigRoot)) {
                        throw "配置发布状态不明确；已保留当前目录和备份，未执行破坏性回滚。"
                    }
                    if ($ConfigPublished -and (Test-Path -LiteralPath $ConfigRoot)) {
                        $FailedConfigPath = Join-Path $BackupRoot `
                            "failed-config-pilot"
                        Move-DirectoryAtomicallyWithRetry `
                            -Source $ConfigRoot `
                            -Destination $FailedConfigPath `
                            -OperationLabel "失败配置目录隔离" `
                            -LogPrefix "CONFIG ROLLBACK QUARANTINE"
                        Write-InstallLog (
                            "ROLLBACK: quarantined failed pilot configuration: {0}" -f `
                                $FailedConfigPath
                        )
                    }
                    if (-not $ConfigBackupPath -or
                        -not (Test-Path -LiteralPath $ConfigBackupPath -PathType Container)) {
                        throw "配置回滚备份不存在。"
                    }
                    Move-DirectoryAtomicallyWithRetry `
                        -Source $ConfigBackupPath `
                        -Destination $ConfigRoot `
                        -OperationLabel "旧配置目录恢复" `
                        -LogPrefix "CONFIG ROLLBACK RESTORE"
                    Write-InstallLog "ROLLBACK: restored previous pilot configuration directory"
                } elseif (-not (Test-Path -LiteralPath $ConfigRoot -PathType Container)) {
                    throw "旧配置未完成备份，原目录也不存在；不执行删除。"
                } else {
                    Write-InstallLog "ROLLBACK: original pilot configuration remained in place"
                }
            } else {
                if ($ConfigPublished -and
                    (Test-Path -LiteralPath $ConfigRoot -PathType Container)) {
                    $FailedConfigPath = Join-Path $BackupRoot `
                        "failed-config-pilot"
                    Move-DirectoryAtomicallyWithRetry `
                        -Source $ConfigRoot `
                        -Destination $FailedConfigPath `
                        -OperationLabel "失败配置目录隔离" `
                        -LogPrefix "CONFIG ROLLBACK QUARANTINE"
                    Write-InstallLog (
                        "ROLLBACK: quarantined newly installed pilot configuration: {0}" -f `
                            $FailedConfigPath
                    )
                } elseif (-not $ConfigPublished -and
                    (Test-Path -LiteralPath $ConfigRoot)) {
                    throw "配置发布状态不明确；检测到非本次安装确认发布的目录，已原样保留。"
                }
            }
        } catch {
            Write-InstallLog "CONFIG ROLLBACK FAILED: $($_.Exception.Message)"
        }
    }
    if (($ExtensionPolicyChangeStarted -or
            $ExtensionPolicyKeyCreated -or
            $ExtensionPolicyCreatedPaths.Count -gt 0) -and
        -not $ExtensionPolicyCommitted) {
        try {
            Restore-ExtensionPolicyChange
        } catch {
            Write-InstallLog "EXTENSION POLICY ROLLBACK FAILED: $($_.Exception.Message)"
        }
    }
    if ($BackupRoot) {
        Write-Host "旧配置备份：$BackupRoot"
    }
    exit 1
} finally {
    if ($RuntimeExtractionRoot -and (Test-Path -LiteralPath $RuntimeExtractionRoot)) {
        Remove-Item -LiteralPath $RuntimeExtractionRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($ExtensionStagingRoot -and
        (Test-Path -LiteralPath $ExtensionStagingRoot)) {
        Remove-Item -LiteralPath $ExtensionStagingRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
    if ($StageRoot -and (Test-Path -LiteralPath $StageRoot)) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($InstallLockStream) {
        try {
            $InstallLockStream.Dispose()
        } catch {
            Write-InstallLog "WARNING: could not release install lock: $($_.Exception.Message)"
        }
    }
    if ($InstallLockPath -and (Test-Path -LiteralPath $InstallLockPath -PathType Leaf)) {
        Remove-Item -LiteralPath $InstallLockPath -Force -ErrorAction SilentlyContinue
    }
}
