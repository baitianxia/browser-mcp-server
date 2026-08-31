#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$PythonExecutable = "",
    [string]$ClaudeExecutable = "",
    [Parameter(Mandatory = $true)]
    [string]$TransferPath = "",
    [string]$LogPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ToolDiscovery = Join-Path $PSScriptRoot "windows-tool-discovery.ps1"
if (-not (Test-Path -LiteralPath $ToolDiscovery -PathType Leaf)) {
    throw "Windows tool discovery helper is missing: $ToolDiscovery"
}
. $ToolDiscovery

function Invoke-ReleaseGate {
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Windows release gate must run on Windows."
}
if ($PSVersionTable.PSEdition -ne "Desktop" -or
    $PSVersionTable.PSVersion -lt [version]"5.1") {
    throw "Windows PowerShell 5.1 Desktop is required for the release gate."
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPrefix = @()
if (-not $PythonExecutable) {
    $Py = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($Py) {
        $PythonExecutable = $Py.Source
        $PythonPrefix = @("-3")
    } else {
        $Python = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if (-not $Python) {
            throw "Python 3.10+ is required."
        }
        $PythonExecutable = $Python.Source
    }
}

$ClaudeInvocation = Resolve-ClaudeCodeInvocation `
    -ExplicitPath $ClaudeExecutable
if ($null -eq $ClaudeInvocation) {
    throw "An existing usable Claude Code command is required. Supported forms are native claude.exe and an npm-generated claude.cmd; the release gate does not install or repair Claude Code."
}

function Invoke-PythonChecked {
    param([string[]]$Arguments)
    $PreviousPreference = $ErrorActionPreference
    $HadDontWriteBytecode = Test-Path Env:PYTHONDONTWRITEBYTECODE
    $PreviousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    $Output = @()
    $ExitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        $env:PYTHONDONTWRITEBYTECODE = "1"
        $Output = & $PythonExecutable @PythonPrefix @Arguments 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        if ($HadDontWriteBytecode) {
            $env:PYTHONDONTWRITEBYTECODE = $PreviousDontWriteBytecode
        } else {
            Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
        }
        $ErrorActionPreference = $PreviousPreference
    }
    foreach ($Line in @($Output)) {
        if ($null -ne $Line) {
            Write-Host $Line
        }
    }
    if ($ExitCode -ne 0) {
        throw "Python release check failed with exit code $ExitCode"
    }
}

function Invoke-NativeChecked {
    param([string]$Executable, [string[]]$Arguments)
    $PreviousPreference = $ErrorActionPreference
    $Output = @()
    $ExitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        $Output = & $Executable @Arguments 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    foreach ($Line in @($Output)) {
        if ($null -ne $Line) {
            Write-Host $Line
        }
    }
    if ($ExitCode -ne 0) {
        throw "$Executable failed with exit code $ExitCode"
    }
}

function Get-NativeOutput {
    param([string]$Executable, [string[]]$Arguments)
    $PreviousPreference = $ErrorActionPreference
    $Output = @()
    $ExitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        $Output = & $Executable @Arguments 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    $Text = ((@($Output) | ForEach-Object { [string]$_ }) -join [Environment]::NewLine).Trim()
    if ($ExitCode -ne 0) {
        throw "$Executable failed with exit code ${ExitCode}: $Text"
    }
    return $Text
}

$PowerShellScripts = @(
    Get-ChildItem -LiteralPath $PSScriptRoot | Where-Object {
        -not $_.PSIsContainer -and $_.Extension -ieq ".ps1"
    }
)
foreach ($PowerShellScript in $PowerShellScripts) {
    $Tokens = $null
    $ParseErrors = $null
    [Management.Automation.Language.Parser]::ParseFile(
        $PowerShellScript.FullName,
        [ref]$Tokens,
        [ref]$ParseErrors
    ) | Out-Null
    if (@($ParseErrors).Count -ne 0) {
        $ParseDetails = (@($ParseErrors) | ForEach-Object { $_.Message }) -join "; "
        throw "$($PowerShellScript.Name) has PowerShell parse errors: $ParseDetails"
    }
}

Push-Location $ProjectRoot
try {
    $ResolvedTransferPath = (Resolve-Path -LiteralPath $TransferPath).Path
    if (-not (Test-Path -LiteralPath $ResolvedTransferPath -PathType Container)) {
        throw "TransferPath must be the extracted transfer directory, not an archive."
    }
    $PackagedProjectRoot = (Resolve-Path -LiteralPath (
        Join-Path $ResolvedTransferPath "toolkit"
    )).Path
    if (-not [string]::Equals(
        [IO.Path]::GetFullPath($ProjectRoot).TrimEnd("\"),
        [IO.Path]::GetFullPath($PackagedProjectRoot).TrimEnd("\"),
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Release gate must run from the same extracted transfer directory it verifies."
    }
    Invoke-PythonChecked @("-c", "import sys; raise SystemExit(sys.version_info < (3, 10))")
    # Verify before executing the packaged test suite, then verify again after it.
    # The second pass proves that the gate itself did not mutate the transfer kit.
    Invoke-PythonChecked @(
        (Join-Path $PSScriptRoot "verify-bundle.py"),
        $ResolvedTransferPath
    )

    Invoke-PythonChecked @("-m", "unittest", "discover", "-s", "tests", "-v")
    Invoke-PythonChecked @(
        (Join-Path $PSScriptRoot "register_claude_user_mcp.py"),
        "self-test"
    )
    $ClaudeVersionArguments = @($ClaudeInvocation.Prefix) + @("--version")
    $ClaudeVersion = Get-NativeOutput `
        ([string]$ClaudeInvocation.Executable) $ClaudeVersionArguments
    Write-Host ("CLAUDE CODE: {0} ({1})" -f `
        $ClaudeVersion, $ClaudeInvocation.Kind)

    $KitMetadataPath = Join-Path $ResolvedTransferPath "KIT-METADATA.json"
    $KitMetadata = Get-Content -LiteralPath $KitMetadataPath -Raw | ConvertFrom-Json
    $RuntimeArchiveName = [string]$KitMetadata.runtime.archive
    if ($RuntimeArchiveName -notmatch '^browser-agent-runtime-[a-zA-Z0-9._-]+\.tar\.gz$') {
        throw "Runtime archive name is invalid: $RuntimeArchiveName"
    }
    $RuntimeArchive = Join-Path (Join-Path $ResolvedTransferPath "runtime") `
        $RuntimeArchiveName
    Invoke-PythonChecked @(
        (Join-Path $PSScriptRoot "verify-bundle.py"),
        $RuntimeArchive
    )

    $ProbeRoot = Join-Path ([IO.Path]::GetTempPath()) `
        ("intranet-browser-agent-release-probe-" + [guid]::NewGuid().ToString("N"))
    $ProbeClaudeConfig = Join-Path $ProbeRoot "claude-config"
    $ProbeRuntimeExtraction = Join-Path $ProbeRoot "runtime"
    $ProbePlaywrightConfig = Join-Path $ProbeRoot "playwright.config.json"
    $ProbeUserConfig = Join-Path $ProbeClaudeConfig ".claude.json"
    $ProbeBackup = Join-Path $ProbeRoot "claude-user-config.bak"
    $HadClaudeConfigDir = Test-Path Env:CLAUDE_CONFIG_DIR
    $PreviousClaudeConfigDir = $env:CLAUDE_CONFIG_DIR
    try {
        New-Item -ItemType Directory -Path $ProbeClaudeConfig -Force | Out-Null
        New-Item -ItemType Directory -Path $ProbeRuntimeExtraction -Force | Out-Null
        $TarCommand = Get-Command "tar.exe" -ErrorAction SilentlyContinue
        if (-not $TarCommand) {
            throw "Windows tar.exe is required for the release probe."
        }
        Invoke-NativeChecked $TarCommand.Source @(
            "-xzf", $RuntimeArchive, "-C", $ProbeRuntimeExtraction
        )
        $ProbeRuntimeName = $RuntimeArchiveName -replace '\.tar\.gz$', ''
        $ProbeRuntimeRoot = Join-Path $ProbeRuntimeExtraction $ProbeRuntimeName
        Invoke-PythonChecked @(
            (Join-Path $PSScriptRoot "verify-bundle.py"),
            $ProbeRuntimeRoot
        )
        $ExpectedNodeVersion = [string]$KitMetadata.runtime.buildMetadata.tools.node
        $ProbeNodeRoot = Join-Path $ProbeRuntimeRoot "node"
        $ProbeNode = Join-Path $ProbeNodeRoot "node.exe"
        $ProbePlaywrightCli = Join-Path $ProbeRuntimeRoot `
            "node_modules\@playwright\mcp\cli.js"
        Invoke-PythonChecked @(
            (Join-Path $PSScriptRoot "validate_node_distribution.py"),
            $ProbeNodeRoot,
            "--expected-version", $ExpectedNodeVersion,
            "--approval-file", (Join-Path $ProjectRoot "config\windows-node-sources.json")
        )
        $ActualNodeVersion = Get-NativeOutput $ProbeNode @("--version")
        if ($ActualNodeVersion -ne $ExpectedNodeVersion) {
            throw "Packaged Node.js reports $ActualNodeVersion; expected $ExpectedNodeVersion"
        }
        [IO.File]::WriteAllText($ProbePlaywrightConfig, "{}`n")
        Invoke-PythonChecked @(
            (Join-Path $PSScriptRoot "smoke_playwright_mcp.py"),
            "--node-executable", $ProbeNode,
            "--playwright-cli", $ProbePlaywrightCli,
            "--playwright-config", $ProbePlaywrightConfig
        )
        $env:CLAUDE_CONFIG_DIR = $ProbeClaudeConfig
        $RegistrationArguments = @(
            (Join-Path $PSScriptRoot "register_claude_user_mcp.py"),
            "register",
            "--claude-executable", [string]$ClaudeInvocation.Executable
        )
        foreach ($ClaudePrefixArgument in @($ClaudeInvocation.Prefix)) {
            $RegistrationArguments += @(
                "--claude-prefix", [string]$ClaudePrefixArgument
            )
        }
        $RegistrationArguments += @(
            "--server-name", "intranet-browser-agent-release-probe",
            "--node-executable", $ProbeNode,
            "--playwright-cli", $ProbePlaywrightCli,
            "--playwright-config", $ProbePlaywrightConfig,
            "--user-config", $ProbeUserConfig,
            "--backup", $ProbeBackup
        )
        Invoke-PythonChecked $RegistrationArguments
        if (-not (Test-Path -LiteralPath $ProbeUserConfig -PathType Leaf)) {
            throw "Claude CLI probe did not create isolated user configuration."
        }
    } finally {
        if ($HadClaudeConfigDir) {
            $env:CLAUDE_CONFIG_DIR = $PreviousClaudeConfigDir
        } else {
            Remove-Item Env:CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $ProbeRoot) {
            Remove-Item -LiteralPath $ProbeRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Invoke-PythonChecked @(
        (Join-Path $PSScriptRoot "verify-bundle.py"),
        $ResolvedTransferPath
    )
} finally {
    Pop-Location
}

Write-Host "WINDOWS POWERSHELL 5.1 RELEASE GATE PASSED" -ForegroundColor Green
}

$TranscriptStarted = $false
try {
    if ($LogPath) {
        $LogDirectory = Split-Path -Parent $LogPath
        if ($LogDirectory) {
            New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
        }
        Start-Transcript -LiteralPath $LogPath -Force | Out-Null
        $TranscriptStarted = $true
    }
    Invoke-ReleaseGate
} finally {
    if ($TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
}
