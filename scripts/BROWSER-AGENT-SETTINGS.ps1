#requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet("interactive", "extension", "dedicated")]
    [string]$BrowserMode = "interactive",
    [ValidateSet("ask", "session", "user")]
    [string]$ExtensionAuthorization = "ask",
    [ValidateSet("ask", "headed", "headless")]
    [string]$DisplayMode = "ask",
    [ValidateSet("ask", "compact", "full")]
    [string]$SnapshotStrategy = "ask",
    [ValidateSet("ask", "robust", "standard")]
    [string]$CompatibilityMode = "ask",
    [string]$ExtensionTokenFile = "",
    [string]$LogPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$MaintenanceVersionRoot = Split-Path -Parent $PSScriptRoot
$MaintenanceRoot = Split-Path -Parent $MaintenanceVersionRoot
$AgentRoot = Split-Path -Parent $MaintenanceRoot
$Configurator = Join-Path $PSScriptRoot "configure_windows_pilot.py"
$Registrar = Join-Path $PSScriptRoot "register_claude_user_mcp.py"
$McpSmoke = Join-Path $PSScriptRoot "smoke_playwright_mcp.py"
$Discovery = Join-Path $PSScriptRoot "windows-tool-discovery.ps1"
$BrowserAgent = Join-Path $MaintenanceVersionRoot "tools\browser_agent.py"
$ConfigRoot = Join-Path $AgentRoot "config\pilot"
$InstalledManifest = Join-Path $ConfigRoot "deployment.windows-pilot.json"
$InstallLockPath = Join-Path $AgentRoot ".install.lock"
$InstallLockStream = $null
$StageRoot = $null
$BackupRoot = $null
$ConfigBackupPath = $null
$ConfigBackedUp = $false
$ConfigPublished = $false
$ConfigCommitted = $false
$script:PythonExe = ""
$script:PythonPrefix = @()
$script:LogPath = $LogPath
$WasInteractive = $BrowserMode -eq "interactive"

function Write-SettingsLog {
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
        $Line = "{0} {1}{2}" -f `
            (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"), $Message, `
            [Environment]::NewLine
        [IO.File]::AppendAllText($script:LogPath, $Line, $Encoding)
    } catch {
        # Diagnostics must not hide the original configuration failure.
    }
}

function Invoke-Python {
    param([string[]]$Arguments)
    $Prefix = $script:PythonPrefix
    $PreviousErrorActionPreference = $ErrorActionPreference
    $HadDontWriteBytecode = Test-Path Env:PYTHONDONTWRITEBYTECODE
    $PreviousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    $Output = @()
    $ExitCode = 1
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
            Write-SettingsLog "PYTHON: $Line"
        }
    }
    if ($ExitCode -ne 0) {
        throw "Python helper failed with exit code $ExitCode"
    }
}

function Invoke-ExistingCommand {
    param([string]$Executable, [string[]]$Arguments)
    $PreviousErrorActionPreference = $ErrorActionPreference
    $Output = @()
    $ExitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        $Output = & $Executable @Arguments 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    foreach ($Line in @($Output)) {
        if ($null -ne $Line) {
            Write-Host $Line
            Write-SettingsLog "NATIVE: $Line"
        }
    }
    if ($ExitCode -ne 0) {
        throw "$Executable failed with exit code $ExitCode"
    }
}

function Test-RetryableDirectoryMoveError {
    param(
        [Parameter(Mandatory = $true)]
        [Management.Automation.ErrorRecord]$ErrorRecord
    )
    if ($ErrorRecord.CategoryInfo.Category -eq `
        [Management.Automation.ErrorCategory]::PermissionDenied) {
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
        [ValidateRange(2, 100)][int]$MaximumAttempts = 25
    )
    $DelayMilliseconds = 250
    for ($Attempt = 1; $Attempt -le $MaximumAttempts; $Attempt++) {
        try {
            [IO.Directory]::Move($Source, $Destination)
            if ((Test-Path -LiteralPath $Source) -or
                -not (Test-Path -LiteralPath $Destination -PathType Container)) {
                throw "$OperationLabel returned an inconsistent directory state."
            }
            if ($Attempt -gt 1) {
                Write-Host "$OperationLabel 占用已释放，继续保存。" `
                    -ForegroundColor Green
            }
            return
        } catch {
            $MoveError = $_
            $SourcePresent = Test-Path -LiteralPath $Source -PathType Container
            $DestinationPresent = Test-Path -LiteralPath $Destination -PathType Container
            if (-not $SourcePresent -and $DestinationPresent) {
                return
            }
            if (-not $SourcePresent -or $DestinationPresent) {
                throw "$OperationLabel 状态不明确，未继续重试。"
            }
            if (-not (Test-RetryableDirectoryMoveError $MoveError) -or
                $Attempt -ge $MaximumAttempts) {
                throw
            }
            if ($Attempt -eq 1) {
                Write-Host "$OperationLabel 正被占用；将自动等待并继续。" `
                    -ForegroundColor Yellow
            }
            Write-SettingsLog (
                "DIRECTORY MOVE RETRY {0}/{1}: {2}" -f `
                    $Attempt, $MaximumAttempts, $MoveError.Exception.Message
            )
            Start-Sleep -Milliseconds $DelayMilliseconds
            $DelayMilliseconds = [Math]::Min($DelayMilliseconds * 2, 5000)
        }
    }
}

function Read-Choice {
    param(
        [string]$Prompt,
        [string[]]$Allowed,
        [string]$Default
    )
    while ($true) {
        $Value = (Read-Host "$Prompt [$Default]").Trim()
        if (-not $Value) {
            return $Default
        }
        if ($Allowed -contains $Value) {
            return $Value
        }
        Write-Host "请输入：$($Allowed -join ' / ')" -ForegroundColor Yellow
    }
}

function Get-ClaudeUserConfigPath {
    if ($env:CLAUDE_CONFIG_DIR) {
        $Directory = [string]$env:CLAUDE_CONFIG_DIR
        if (-not [IO.Path]::IsPathRooted($Directory) -or
            $Directory -notmatch '^[A-Za-z]:[\\/]') {
            throw "CLAUDE_CONFIG_DIR 必须是本机盘符绝对路径。"
        }
        $Directory = [IO.Path]::GetFullPath($Directory)
        if ($Directory -notmatch '^[A-Za-z]:\\') {
            throw "CLAUDE_CONFIG_DIR 不能使用 UNC 路径。"
        }
        return Join-Path $Directory ".claude.json"
    }
    return Join-Path $env:USERPROFILE ".claude.json"
}

function Get-ExistingExtensionToken {
    param([string]$UserConfigPath)
    try {
        if (-not (Test-Path -LiteralPath $UserConfigPath -PathType Leaf)) {
            return ""
        }
        $Payload = Get-Content -LiteralPath $UserConfigPath -Raw | ConvertFrom-Json
        $ServersProperty = $Payload.PSObject.Properties["mcpServers"]
        if ($null -eq $ServersProperty -or $null -eq $ServersProperty.Value) {
            return ""
        }
        $EntryProperty = $ServersProperty.Value.PSObject.Properties[
            "intranet-browser-agent"
        ]
        if ($null -eq $EntryProperty -or $null -eq $EntryProperty.Value) {
            return ""
        }
        $EnvironmentProperty = $EntryProperty.Value.PSObject.Properties["env"]
        if ($null -eq $EnvironmentProperty -or
            $null -eq $EnvironmentProperty.Value) {
            return ""
        }
        $TokenProperty = $EnvironmentProperty.Value.PSObject.Properties[
            "PLAYWRIGHT_MCP_EXTENSION_TOKEN"
        ]
        if ($null -eq $TokenProperty) {
            return ""
        }
        $Token = [string]$TokenProperty.Value
        if ($Token -match '^[A-Za-z0-9_-]{43}$') {
            return $Token
        }
    } catch {
        Write-SettingsLog "WARNING: existing extension token could not be read"
    }
    return ""
}

function Read-ExtensionTokenSecurely {
    Write-Host ""
    Write-Host "需要一次性保存 Playwright Extension 页面显示的令牌。" `
        -ForegroundColor Yellow
    $StatusUrl = "chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/status.html"
    try {
        Start-Process -FilePath $BrowserExecutable -ArgumentList @($StatusUrl) |
            Out-Null
        Write-Host "已打开 Playwright Extension 状态页。"
    } catch {
        Write-SettingsLog (
            "WARNING: could not open extension status page: {0}" -f `
                $_.Exception.Message
        )
        Write-Host "请在 Claude Code 调用一次浏览器工具以打开扩展连接页。"
    }
    Write-Host "页面会显示："
    Write-Host "PLAYWRIGHT_MCP_EXTENSION_TOKEN=..."
    Write-Host "复制整行或等号后的内容并粘贴到下面。输入内容不会回显。"
    $SecureValue = Read-Host "扩展令牌" -AsSecureString
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
}

function Normalize-ExtensionToken {
    param([string]$Value)
    $Token = $Value.Trim()
    $Prefix = "PLAYWRIGHT_MCP_EXTENSION_TOKEN="
    if ($Token.StartsWith($Prefix, [StringComparison]::Ordinal)) {
        $Token = $Token.Substring($Prefix.Length).Trim()
    }
    if ($Token -notmatch '^[A-Za-z0-9_-]{43}$') {
        throw "扩展令牌格式不正确。"
    }
    return $Token
}

try {
    if (-not $env:LOCALAPPDATA -or -not $env:USERPROFILE) {
        throw "当前 Windows 用户缺少 LOCALAPPDATA 或 USERPROFILE。"
    }
    if (-not $script:LogPath) {
        $LogDirectory = Join-Path ([IO.Path]::GetTempPath()) `
            "IntranetBrowserAgent"
        $script:LogPath = Join-Path $LogDirectory (
            "BROWSER-AGENT-SETTINGS-{0}-{1}.log" -f `
                $PID, (Get-Random -Minimum 1000 -Maximum 9999)
        )
    }
    Write-SettingsLog "Browser Agent settings started"

    foreach ($RequiredPath in @(
        $Configurator, $Registrar, $McpSmoke, $Discovery, $BrowserAgent,
        $InstalledManifest
    )) {
        if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
            throw "设置工具不完整或尚未安装：$RequiredPath"
        }
    }
    . $Discovery

    $PyCommand = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($PyCommand) {
        $script:PythonExe = $PyCommand.Source
        $script:PythonPrefix = @("-3")
    } else {
        $PythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if (-not $PythonCommand) {
            throw "未找到 Python 3.10+。"
        }
        $script:PythonExe = $PythonCommand.Source
    }
    Invoke-Python @("-c", "import sys; raise SystemExit(sys.version_info < (3, 10))")

    $ClaudeInvocation = Resolve-ClaudeCodeInvocation
    if ($null -eq $ClaudeInvocation) {
        throw "未找到当前用户已有的 Claude Code；设置工具不会安装或修复它。"
    }
    Invoke-ExistingCommand ([string]$ClaudeInvocation.Executable) `
        (@($ClaudeInvocation.Prefix) + @("--version"))

    $Manifest = Get-Content -LiteralPath $InstalledManifest -Raw | ConvertFrom-Json
    if ([string]$Manifest.environment -ne "pilot" -or
        [string]$Manifest.mcpScope -ne "user" -or
        [string]$Manifest.target.os -ne "windows" -or
        [string]$Manifest.target.arch -ne "x64") {
        throw "当前配置不是受支持的 Windows user-scope pilot。"
    }
    $RuntimeRoot = [string]$Manifest.installRoot
    $NodeExe = [string]$Manifest.nodeExecutable
    $BrowserChannel = [string]$Manifest.browser.channel
    $BrowserExecutable = [string]$Manifest.browser.executablePath
    $PlaywrightCli = Join-Path $RuntimeRoot `
        "bin\intranet-browser-agent-mcp.js"
    foreach ($RequiredRuntimePath in @($NodeExe, $BrowserExecutable, $PlaywrightCli)) {
        if (-not (Test-Path -LiteralPath $RequiredRuntimePath -PathType Leaf)) {
            throw "已安装运行环境不完整：$RequiredRuntimePath"
        }
    }

    try {
        $InstallLockStream = [IO.File]::Open(
            $InstallLockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    } catch {
        throw "另一个安装或设置进程正在运行，请等待其完成。"
    }

    $CurrentMode = if ([string]$Manifest.mode -eq "extension") {
        "extension"
    } else {
        "dedicated"
    }
    $CurrentAuthorization = if (
        $CurrentMode -eq "extension" -and
        $Manifest.browser.manualConnectionApproval -eq $false
    ) {
        "user"
    } else {
        "session"
    }
    $CurrentDisplay = if (
        $CurrentMode -eq "dedicated" -and $Manifest.browser.headless -eq $true
    ) {
        "headless"
    } else {
        "headed"
    }
    $CurrentSnapshotStrategy = "compact"
    $CurrentCompatibilityMode = "robust"
    $InteractionProperty = $Manifest.PSObject.Properties["interaction"]
    if ($null -ne $InteractionProperty -and $null -ne $InteractionProperty.Value) {
        $SnapshotProperty = `
            $InteractionProperty.Value.PSObject.Properties["snapshotStrategy"]
        $CompatibilityProperty = `
            $InteractionProperty.Value.PSObject.Properties["compatibilityMode"]
        $SnapshotValue = if ($null -ne $SnapshotProperty) {
            [string]$SnapshotProperty.Value
        } else { "" }
        $CompatibilityValue = if ($null -ne $CompatibilityProperty) {
            [string]$CompatibilityProperty.Value
        } else { "" }
        if (@("compact", "full") -contains $SnapshotValue) {
            $CurrentSnapshotStrategy = $SnapshotValue
        }
        if (@("robust", "standard") -contains $CompatibilityValue) {
            $CurrentCompatibilityMode = $CompatibilityValue
        }
    }

    if ($BrowserMode -eq "interactive") {
        Write-Host ""
        Write-Host "Browser Agent 设置" -ForegroundColor Cyan
        Write-Host "  1. 使用现有浏览器和登录态"
        Write-Host "  2. 使用独立浏览器 Profile（不共享原 Chrome/Edge 登录态）"
        $DefaultModeChoice = if ($CurrentMode -eq "extension") { "1" } else { "2" }
        $ModeChoice = Read-Choice "请选择浏览器模式" @("1", "2") `
            $DefaultModeChoice
        $BrowserMode = if ($ModeChoice -eq "1") { "extension" } else { "dedicated" }
    }

    if ($BrowserMode -eq "extension") {
        if ($DisplayMode -eq "headless") {
            throw "Extension 连接的是正在显示的浏览器，不能使用无头模式。"
        }
        $DisplayMode = "headed"
        if ($ExtensionAuthorization -eq "ask") {
            Write-Host ""
            Write-Host "  1. 记住当前 Windows 用户（只需首次保存一次令牌）"
            Write-Host "  2. 每次连接都由用户确认"
            Write-Host "注意：选 1 会允许该用户后续连接绕过扩展批准页。" `
                -ForegroundColor Yellow
            $DefaultAuthChoice = "1"
            $AuthChoice = Read-Choice "请选择扩展授权方式" @("1", "2") `
                $DefaultAuthChoice
            $ExtensionAuthorization = if ($AuthChoice -eq "1") { "user" } else { "session" }
        }
    } else {
        if ($ExtensionAuthorization -eq "user") {
            throw "独立 Profile 不使用 Extension，不能配置 Extension 用户授权。"
        }
        $ExtensionAuthorization = "session"
        if ($DisplayMode -eq "ask") {
            Write-Host ""
            Write-Host "  1. 有头模式（可见，首次登录推荐）"
            Write-Host "  2. 无头模式（后台运行）"
            $DefaultDisplayChoice = if ($CurrentDisplay -eq "headless") { "2" } else { "1" }
            $DisplayChoice = Read-Choice "请选择显示方式" @("1", "2") `
                $DefaultDisplayChoice
            $DisplayMode = if ($DisplayChoice -eq "2") { "headless" } else { "headed" }
        }
    }
    if ($DisplayMode -eq "ask" -or $ExtensionAuthorization -eq "ask") {
        throw "非交互调用必须提供完整设置。"
    }

    if ($WasInteractive) {
        Write-Host ""
        Write-Host "  1. 精简快照（推荐，内联返回，减少上下文）"
        Write-Host "  2. 完整快照（诊断用，可能生成较大文件）"
        $DefaultSnapshotChoice = if ($CurrentSnapshotStrategy -eq "full") { "2" } else { "1" }
        $SnapshotChoice = Read-Choice "请选择快照方式" @("1", "2") `
            $DefaultSnapshotChoice
        $SnapshotStrategy = if ($SnapshotChoice -eq "2") { "full" } else { "compact" }

        Write-Host ""
        Write-Host "  1. 动态页面兼容（推荐，支持稳定等待和同目标回退）"
        Write-Host "  2. 标准上游行为（关闭自动兼容回退）"
        $DefaultCompatibilityChoice = if ($CurrentCompatibilityMode -eq "standard") { "2" } else { "1" }
        $CompatibilityChoice = Read-Choice "请选择页面兼容方式" @("1", "2") `
            $DefaultCompatibilityChoice
        $CompatibilityMode = if ($CompatibilityChoice -eq "2") { "standard" } else { "robust" }
    }
    if ($SnapshotStrategy -eq "ask") {
        $SnapshotStrategy = $CurrentSnapshotStrategy
    }
    if ($CompatibilityMode -eq "ask") {
        $CompatibilityMode = $CurrentCompatibilityMode
    }

    $ClaudeUserConfigPath = Get-ClaudeUserConfigPath
    $ExtensionToken = ""
    if ($BrowserMode -eq "extension" -and
        $ExtensionAuthorization -eq "user") {
        if ($ExtensionTokenFile) {
            if (-not (Test-Path -LiteralPath $ExtensionTokenFile -PathType Leaf)) {
                throw "扩展令牌文件不存在：$ExtensionTokenFile"
            }
            $ExtensionToken = Get-Content -LiteralPath $ExtensionTokenFile -Raw
        } else {
            $ExtensionToken = Get-ExistingExtensionToken $ClaudeUserConfigPath
            if (-not $ExtensionToken) {
                $ExtensionToken = Read-ExtensionTokenSecurely
            }
        }
        $ExtensionToken = Normalize-ExtensionToken $ExtensionToken
    }

    $DedicatedProfile = Join-Path $AgentRoot "browser-profile\pilot"
    $StagingRoot = Join-Path $AgentRoot "staging"
    Invoke-Python @(
        $Configurator,
        "assert-user-paths",
        "--local-app-data", ([IO.Path]::GetFullPath($env:LOCALAPPDATA)),
        "--path", $AgentRoot,
        "--path", $ConfigRoot,
        "--path", $DedicatedProfile,
        "--path", $StagingRoot,
        "--path", (Join-Path $AgentRoot "backups")
    )

    New-Item -ItemType Directory -Path $StagingRoot -Force | Out-Null
    $StageRoot = Join-Path $StagingRoot `
        ("settings-" + [guid]::NewGuid().ToString("N"))
    $StageManifest = Join-Path $StageRoot "deployment.windows-pilot.json"
    $StageRendered = Join-Path $StageRoot "rendered"
    $StageDeploy = Join-Path $StageRoot "deploy"
    New-Item -ItemType Directory -Path $StageRoot, $StageDeploy -Force | Out-Null

    Invoke-Python @(
        $Configurator,
        "reconfigure",
        "--manifest", $InstalledManifest,
        "--manifest-out", $StageManifest,
        "--render-out", $StageRendered,
        "--browser-mode", $BrowserMode,
        "--headless", $(if ($DisplayMode -eq "headless") { "true" } else { "false" }),
        "--extension-authorization", $ExtensionAuthorization,
        "--user-data-dir", $DedicatedProfile,
        "--snapshot-strategy", $SnapshotStrategy,
        "--compatibility-mode", $CompatibilityMode
    )
    Copy-Item -LiteralPath $StageManifest `
        -Destination (Join-Path $StageDeploy "deployment.windows-pilot.json")
    foreach ($Name in @(
        "playwright.config.json", "interaction.config.json", ".mcp.json", "deployment.lock.json",
        "CLAUDE.browser.md", "DEPLOYMENT.txt"
    )) {
        Copy-Item -LiteralPath (Join-Path $StageRendered $Name) `
            -Destination (Join-Path $StageDeploy $Name)
    }
    Invoke-Python @(
        $BrowserAgent,
        "preflight",
        "--manifest", $StageManifest,
        "--runtime-root", $RuntimeRoot,
        "--config-root", $StageDeploy
    )
    Invoke-Python @(
        $McpSmoke,
        "--node-executable", $NodeExe,
        "--playwright-cli", $PlaywrightCli,
        "--playwright-config", (Join-Path $StageDeploy "playwright.config.json"),
        "--browser-channel", $BrowserChannel,
        "--browser-executable", $BrowserExecutable
    )

    $Timestamp = (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + `
        [guid]::NewGuid().ToString("N").Substring(0, 8)
    $BackupRoot = Join-Path $AgentRoot "backups\settings-$Timestamp"
    $ConfigBackupPath = Join-Path $BackupRoot "config-pilot"
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath $ConfigRoot -PathType Container)) {
        throw "已安装配置目录不存在：$ConfigRoot"
    }
    Move-DirectoryAtomicallyWithRetry `
        -Source $ConfigRoot `
        -Destination $ConfigBackupPath `
        -OperationLabel "旧设置备份"
    $ConfigBackedUp = $true
    Move-DirectoryAtomicallyWithRetry `
        -Source $StageDeploy `
        -Destination $ConfigRoot `
        -OperationLabel "新设置发布"
    $ConfigPublished = $true

    Invoke-Python @(
        $BrowserAgent,
        "preflight",
        "--manifest", (Join-Path $ConfigRoot "deployment.windows-pilot.json"),
        "--runtime-root", $RuntimeRoot,
        "--config-root", $ConfigRoot
    )

    $RegistrarArguments = @(
        $Registrar,
        "register",
        "--claude-executable", [string]$ClaudeInvocation.Executable
    )
    foreach ($PrefixArgument in @($ClaudeInvocation.Prefix)) {
        $RegistrarArguments += @("--claude-prefix", [string]$PrefixArgument)
    }
    $RegistrarArguments += @(
        "--server-name", "intranet-browser-agent",
        "--node-executable", $NodeExe,
        "--playwright-cli", $PlaywrightCli,
        "--playwright-config", (Join-Path $ConfigRoot "playwright.config.json"),
        "--browser-channel", $BrowserChannel,
        "--browser-executable", $BrowserExecutable,
        "--user-config", $ClaudeUserConfigPath,
        "--backup", (Join-Path $BackupRoot "claude-user-config.json.bak")
    )
    if ($ExtensionToken) {
        $TokenInputEnvironmentName = `
            "INTRANET_BROWSER_AGENT_EXTENSION_TOKEN_INPUT"
        $PreviousTokenInput = [Environment]::GetEnvironmentVariable(
            $TokenInputEnvironmentName,
            [EnvironmentVariableTarget]::Process
        )
        try {
            [Environment]::SetEnvironmentVariable(
                $TokenInputEnvironmentName,
                $ExtensionToken,
                [EnvironmentVariableTarget]::Process
            )
            $RegistrarArguments += "--extension-token-environment"
            Invoke-Python $RegistrarArguments
        } finally {
            [Environment]::SetEnvironmentVariable(
                $TokenInputEnvironmentName,
                $PreviousTokenInput,
                [EnvironmentVariableTarget]::Process
            )
        }
    } else {
        Invoke-Python $RegistrarArguments
    }
    $ConfigCommitted = $true

    $Summary = if ($BrowserMode -eq "extension") {
        $AuthorizationLabel = if ($ExtensionAuthorization -eq "user") {
            "记住当前 Windows 用户"
        } else {
            "每次连接确认"
        }
        "现有 $BrowserChannel 登录态；有头；$AuthorizationLabel"
    } else {
        $DisplayLabel = if ($DisplayMode -eq "headless") { "无头" } else { "有头" }
        "独立 $BrowserChannel Profile；$DisplayLabel；不共享原浏览器登录态"
    }
    $SnapshotLabel = if ($SnapshotStrategy -eq "compact") { "精简快照" } else { "完整快照" }
    $CompatibilityLabel = if ($CompatibilityMode -eq "robust") { "动态页面兼容" } else { "标准上游行为" }
    $Summary = "$Summary；$SnapshotLabel；$CompatibilityLabel"
    Write-SettingsLog "SUCCESS: $Summary"
    Write-Host ""
    Write-Host "设置已保存：$Summary" -ForegroundColor Green
    Write-Host "请重启 Claude Code 后使用。"
    Write-Host "备份：$BackupRoot"
} catch {
    Write-SettingsLog "FAILED: $($_.Exception.Message)"
    if ($ConfigBackedUp -and -not $ConfigCommitted -and $ConfigBackupPath) {
        try {
            if (Test-Path -LiteralPath $ConfigRoot -PathType Container) {
                $FailedConfig = Join-Path $BackupRoot "failed-config-pilot"
                Move-DirectoryAtomicallyWithRetry `
                    -Source $ConfigRoot `
                    -Destination $FailedConfig `
                    -OperationLabel "失败设置隔离"
            }
            if (Test-Path -LiteralPath $ConfigBackupPath -PathType Container) {
                Move-DirectoryAtomicallyWithRetry `
                    -Source $ConfigBackupPath `
                    -Destination $ConfigRoot `
                    -OperationLabel "旧设置恢复"
            }
        } catch {
            Write-SettingsLog "CONFIG ROLLBACK FAILED: $($_.Exception.Message)"
        }
    }
    Write-Host ""
    Write-Host "设置未生效：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "日志：$($script:LogPath)"
    exit 1
} finally {
    if ($StageRoot -and (Test-Path -LiteralPath $StageRoot)) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
    if ($InstallLockStream) {
        try {
            $InstallLockStream.Dispose()
        } catch {
            Write-SettingsLog "WARNING: could not release settings lock"
        }
    }
    if ($InstallLockPath -and
        (Test-Path -LiteralPath $InstallLockPath -PathType Leaf)) {
        Remove-Item -LiteralPath $InstallLockPath -Force `
            -ErrorAction SilentlyContinue
    }
}
