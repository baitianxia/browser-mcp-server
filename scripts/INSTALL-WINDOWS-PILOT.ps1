#requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet("auto", "chrome", "msedge")]
    [string]$BrowserChannel = "auto",
    [string]$LogPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:PythonExe = ""
$script:PythonPrefix = @()
$script:LogPath = $LogPath
$StageRoot = $null
$RuntimeExtractionRoot = $null
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

function Test-BrowserInstalled {
    param([ValidateSet("chrome", "msedge")][string]$Channel)
    $Executable = if ($Channel -eq "chrome") { "chrome.exe" } else { "msedge.exe" }
    if (Get-Command $Executable -ErrorAction SilentlyContinue) {
        return $true
    }
    $RelativePath = if ($Channel -eq "chrome") {
        "Google\Chrome\Application\chrome.exe"
    } else {
        "Microsoft\Edge\Application\msedge.exe"
    }
    foreach ($Base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if ($Base -and (Test-Path -LiteralPath (Join-Path $Base $RelativePath) -PathType Leaf)) {
            return $true
        }
    }
    return $false
}

function Get-ClaudeExecutable {
    $Command = Get-Command "claude.exe" -ErrorAction SilentlyContinue
    if ($Command) {
        $ResolvedCommand = if ($Command.Source) {
            $Command.Source
        } else {
            $Command.Path
        }
        if ($ResolvedCommand -and
            [IO.Path]::GetExtension($ResolvedCommand) -ieq ".exe") {
            return $ResolvedCommand
        }
    }
    $NativeCandidate = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
    if (Test-Path -LiteralPath $NativeCandidate -PathType Leaf) {
        return $NativeCandidate
    }
    return ""
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $Encoding)
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
    $ClaudeExecutable = Get-ClaudeExecutable
    if (-not $ClaudeExecutable) {
        throw "未找到原生 Claude Code claude.exe；旧式 claude.cmd 不适用于此一键安装包。"
    }
    Invoke-External $ClaudeExecutable @("--version")

    $ToolkitRoot = Join-Path $PSScriptRoot "toolkit"
    $Verifier = Join-Path $ToolkitRoot "scripts\verify-bundle.py"
    $NodeDistributionVerifier = Join-Path $ToolkitRoot "scripts\validate_node_distribution.py"
    $NodeSourceApprovals = Join-Path $ToolkitRoot "config\windows-node-sources.json"
    $Configurator = Join-Path $ToolkitRoot "scripts\configure_windows_pilot.py"
    $McpRegistrar = Join-Path $ToolkitRoot "scripts\register_claude_user_mcp.py"
    $McpSmoke = Join-Path $ToolkitRoot "scripts\smoke_playwright_mcp.py"
    $BrowserAgent = Join-Path $ToolkitRoot "tools\browser_agent.py"
    $KitMetadataPath = Join-Path $PSScriptRoot "KIT-METADATA.json"
    foreach ($RequiredPath in @(
        $Verifier,
        $NodeDistributionVerifier,
        $NodeSourceApprovals,
        $Configurator,
        $McpRegistrar,
        $McpSmoke,
        $BrowserAgent,
        $KitMetadataPath
    )) {
        if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
            throw "迁移包不完整，缺少：$RequiredPath"
        }
    }
    Invoke-Python @($Verifier, $PSScriptRoot)
    Invoke-Python @($McpRegistrar, "self-test")

    $KitMetadata = Get-Content -LiteralPath $KitMetadataPath -Raw | ConvertFrom-Json
    if ($KitMetadata.runtime.buildMetadata.target.system -ne "windows" -or
        $KitMetadata.runtime.buildMetadata.target.machine -ne "x64") {
        throw "迁移包目标不是 Windows x64。"
    }
    $RuntimeArchiveName = [string]$KitMetadata.runtime.archive
    if ($RuntimeArchiveName -notmatch '^browser-agent-runtime-[a-zA-Z0-9._-]+\.tar\.gz$') {
        throw "运行包文件名不合法：$RuntimeArchiveName"
    }
    $RuntimeArchive = Join-Path (Join-Path $PSScriptRoot "runtime") $RuntimeArchiveName
    Invoke-Python @($Verifier, $RuntimeArchive)
    $BundledNode = $KitMetadata.runtime.buildMetadata.bundledNode -eq $true
    $ExpectedNodeVersion = [string]$KitMetadata.runtime.buildMetadata.tools.node
    if (-not $BundledNode) {
        throw "Windows 一键试点包必须携带已校验的 Node.js；当前运行包未携带。"
    }
    if ($ExpectedNodeVersion -notmatch '^v\d+\.\d+\.\d+$') {
        throw "运行包记录的 Node.js 版本不合法：$ExpectedNodeVersion"
    }
    if ($KitMetadata.runtime.buildMetadata.crossBuilt -eq $true) {
        Write-Host "注意：这是交叉构建的试点候选包，不能作为生产制品。" -ForegroundColor Yellow
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
    $ProfileDirectory = Join-Path $AgentRoot "profiles\pilot"
    Invoke-Python @(
        $Configurator,
        "assert-user-paths",
        "--local-app-data", $LocalAppDataRoot,
        "--path", $AgentRoot,
        "--path", $ReleaseRoot,
        "--path", $RuntimeRoot,
        "--path", $ConfigRoot,
        "--path", $OutputDirectory,
        "--path", $ProfileDirectory,
        "--path", (Join-Path $AgentRoot "staging"),
        "--path", (Join-Path $AgentRoot "backups"),
        "--path", (Join-Path $AgentRoot ".install.lock")
    )

    Write-Host ""
    Write-Host "即将部署：" -ForegroundColor Cyan
    Write-Host "  安装范围：当前用户的全部 Claude Code 项目"
    Write-Host "  安装目录：$AgentRoot"
    Write-Host "  浏览器：$BrowserChannel"
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
        Move-Item -LiteralPath $StagedRuntimeRoot -Destination $RuntimeRoot
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
        "--profile-directory", $ProfileDirectory,
        "--profile-owner", $ProfileOwner,
        "--node-executable", $NodeExe,
        "--browser-channel", $BrowserChannel
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
        -Path (Split-Path -Parent $ConfigRoot), $OutputDirectory, $ProfileDirectory `
        -Force | Out-Null
    $ConfigChangeStarted = $true
    if ($ConfigWasPresent) {
        Move-Item -LiteralPath $ConfigRoot -Destination $ConfigBackupPath
        $ConfigBackupComplete = $true
    }
    Move-Item -LiteralPath $StageDeploy -Destination $ConfigRoot
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
        "--playwright-config", $PlaywrightConfigPath
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
    $UserConfigChangeStarted = $true
    Invoke-Python @(
        $McpRegistrar,
        "register",
        "--claude-executable", $ClaudeExecutable,
        "--server-name", $McpServerName,
        "--node-executable", $NodeExe,
        "--playwright-cli", $PlaywrightCliPath,
        "--playwright-config", $PlaywrightConfigPath,
        "--user-config", $ClaudeUserConfigPath,
        "--backup", $ClaudeUserConfigBackup
    )
    $UserConfigCommitted = $true
    $ConfigCommitted = $true

    Write-Step 6 "完成"
    $SummaryPath = Join-Path $AgentRoot "INSTALLATION.txt"
    $Summary = @"
Windows Browser Agent pilot is installed.

Runtime: $RuntimeRoot
Manifest: $InstalledManifest
Claude MCP scope: user (all projects for the current Windows user)
Backup: $BackupRoot

Next: restart Claude Code in any project, run /mcp to confirm intranet-browser-agent,
let a human finish SSO/MFA, and perform a read-only page-title test first.
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
                        Remove-Item -LiteralPath $ConfigRoot -Recurse -Force
                    }
                    if (-not $ConfigBackupPath -or
                        -not (Test-Path -LiteralPath $ConfigBackupPath -PathType Container)) {
                        throw "配置回滚备份不存在。"
                    }
                    Move-Item -LiteralPath $ConfigBackupPath -Destination $ConfigRoot
                    Write-InstallLog "ROLLBACK: restored previous pilot configuration directory"
                } elseif (-not (Test-Path -LiteralPath $ConfigRoot -PathType Container)) {
                    throw "旧配置未完成备份，原目录也不存在；不执行删除。"
                } else {
                    Write-InstallLog "ROLLBACK: original pilot configuration remained in place"
                }
            } else {
                if (Test-Path -LiteralPath $ConfigRoot) {
                    Remove-Item -LiteralPath $ConfigRoot -Recurse -Force
                }
                Write-InstallLog "ROLLBACK: removed newly installed pilot configuration directory"
            }
        } catch {
            Write-InstallLog "CONFIG ROLLBACK FAILED: $($_.Exception.Message)"
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
