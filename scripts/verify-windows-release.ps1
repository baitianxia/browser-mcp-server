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

# Publisher-side gate for the exact public Windows ZIP.  It accepts either the
# ZIP itself or its extracted single top-level directory and never runs package
# code before the outer checksum and release metadata have passed.
$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ToolDiscovery = Join-Path $PSScriptRoot "windows-tool-discovery.ps1"
if (-not (Test-Path -LiteralPath $ToolDiscovery -PathType Leaf)) {
    throw "Windows tool discovery helper is missing: $ToolDiscovery"
}
. $ToolDiscovery

$script:PythonExecutable = ""
$script:PythonPrefix = @()

# A caller-supplied interpreter is part of the release environment contract.
# Resolve it once and use it verbatim; silently falling back to ``py.exe`` or a
# different PATH entry would make the recorded gate non-reproducible.
if ($PythonExecutable) {
    $PythonCommand = Get-Command $PythonExecutable -CommandType Application `
        -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        $script:PythonExecutable = if ($PythonCommand.Source) {
            $PythonCommand.Source
        } else {
            $PythonCommand.Path
        }
    } elseif (Test-Path -LiteralPath $PythonExecutable -PathType Leaf) {
        $script:PythonExecutable = (Resolve-Path -LiteralPath $PythonExecutable `
            -ErrorAction Stop).Path
    } else {
        throw "The requested Python executable was not found: $PythonExecutable"
    }
}

function Invoke-PythonChecked {
    param([string[]]$Arguments)
    $PreviousPreference = $ErrorActionPreference
    $HadDontWriteBytecode = Test-Path Env:PYTHONDONTWRITEBYTECODE
    $PreviousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    $Output = @()
    $ExitCode = 1
    $Prefix = $script:PythonPrefix
    try {
        $ErrorActionPreference = "Continue"
        $env:PYTHONDONTWRITEBYTECODE = "1"
        $Output = & $script:PythonExecutable @Prefix @Arguments 2>&1
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
        if ($null -ne $Line) { Write-Host $Line }
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
        if ($null -ne $Line) { Write-Host $Line }
    }
    if ($ExitCode -ne 0) { throw "$Executable failed with exit code $ExitCode" }
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
    if ($ExitCode -ne 0) { throw "$Executable failed with exit code ${ExitCode}: $Text" }
    return $Text
}

function Parse-PowerShellScripts {
    param([Parameter(Mandatory = $true)][string]$Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "PowerShell source root is missing: $Root"
    }
    $Failures = @()
    foreach ($Script in @(Get-ChildItem -LiteralPath $Root -Filter "*.ps1" -File -Recurse)) {
        $Tokens = $null
        $ParseErrors = $null
        [Management.Automation.Language.Parser]::ParseFile(
            $Script.FullName, [ref]$Tokens, [ref]$ParseErrors
        ) | Out-Null
        foreach ($ParseError in @($ParseErrors)) {
            $Failures += "$($Script.FullName): $($ParseError.Message)"
        }
    }
    if ($Failures.Count -ne 0) { throw ($Failures -join [Environment]::NewLine) }
}

function Resolve-PublicPackageRoot {
    param(
        [Parameter(Mandatory = $true)][string]$InputPath,
        [Parameter(Mandatory = $true)][string]$Verifier,
        [Parameter(Mandatory = $true)][string]$TemporaryRoot
    )
    $Resolved = (Resolve-Path -LiteralPath $InputPath -ErrorAction Stop).Path
    if (Test-Path -LiteralPath $Resolved -PathType Leaf) {
        if ([IO.Path]::GetExtension($Resolved) -ine ".zip") {
            throw "TransferPath must be the public Windows ZIP or its extracted root: $Resolved"
        }
        $Sidecar = "$Resolved.sha256"
        if (-not (Test-Path -LiteralPath $Sidecar -PathType Leaf)) {
            throw "Public Windows ZIP is missing its adjacent checksum sidecar: $Sidecar"
        }
        $SidecarItem = Get-Item -LiteralPath $Sidecar -Force -ErrorAction Stop
        if (($SidecarItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Public Windows ZIP checksum sidecar must be a regular file: $Sidecar"
        }
        Invoke-PythonChecked @($Verifier, $Resolved)
        $ExtractRoot = Join-Path $TemporaryRoot "public-package"
        New-Item -ItemType Directory -Path $ExtractRoot -Force | Out-Null
        Expand-Archive -LiteralPath $Resolved -DestinationPath $ExtractRoot -Force
        $Roots = @(Get-ChildItem -LiteralPath $ExtractRoot -Directory)
        if ($Roots.Count -ne 1) {
            throw "Public ZIP must extract to exactly one top-level directory."
        }
        $Resolved = $Roots[0].FullName
    }
    if (-not (Test-Path -LiteralPath $Resolved -PathType Container)) {
        throw "TransferPath must be the public Windows ZIP or its extracted root."
    }
    Invoke-PythonChecked @($Verifier, $Resolved)
    return $Resolved
}

function Invoke-ReleaseGate {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "Windows release gate must run on Windows."
    }
    if ($PSVersionTable.PSEdition -ne "Desktop" -or
        $PSVersionTable.PSVersion -lt [version]"5.1") {
        throw "Windows PowerShell 5.1 Desktop is required for the release gate."
    }
    if (-not $script:PythonExecutable) {
        $Py = Get-Command "py.exe" -ErrorAction SilentlyContinue
        if ($Py) {
            $script:PythonExecutable = $Py.Source
            $script:PythonPrefix = @("-3")
        } else {
            $Python = Get-Command "python.exe" -ErrorAction SilentlyContinue
            if (-not $Python) { throw "Python 3.10+ is required." }
            $script:PythonExecutable = $Python.Source
        }
    }
    $ClaudeInvocation = Resolve-ClaudeCodeInvocation -ExplicitPath $ClaudeExecutable
    if ($null -eq $ClaudeInvocation) {
        throw "An existing usable Claude Code command is required; the release gate never installs or repairs Claude Code."
    }

    $TemporaryRoot = Join-Path ([IO.Path]::GetTempPath()) (
        "browser-mcp-server-release-gate-" + [guid]::NewGuid().ToString("N")
    )
    New-Item -ItemType Directory -Path $TemporaryRoot -Force | Out-Null
    try {
        $Verifier = Join-Path $SourceRoot "scripts\verify-bundle.py"
        $MetadataVerifier = Join-Path $SourceRoot "scripts\validate_windows_release_metadata.py"
        $ResolvedPackageRoot = Resolve-PublicPackageRoot `
            -InputPath $TransferPath `
            -Verifier $Verifier `
            -TemporaryRoot $TemporaryRoot
        $PayloadRoot = Join-Path $ResolvedPackageRoot "payload"
        $ToolkitRoot = Join-Path $PayloadRoot "toolkit"
        $KitMetadataPath = Join-Path $PayloadRoot "KIT-METADATA.json"
        $ReleaseManifestPath = Join-Path $ResolvedPackageRoot "release-manifest.json"
        foreach ($Required in @($PayloadRoot, $ToolkitRoot)) {
            if (-not (Test-Path -LiteralPath $Required -PathType Container)) {
                throw "Public package is missing required directory: $Required"
            }
        }
        foreach ($Required in @($KitMetadataPath, $ReleaseManifestPath)) {
            if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
                throw "Public package is missing required file: $Required"
            }
        }
        Invoke-PythonChecked @($MetadataVerifier, $ReleaseManifestPath)
        Invoke-PythonChecked @($MetadataVerifier, $KitMetadataPath)
        $ReleaseManifest = Get-Content -LiteralPath $ReleaseManifestPath -Raw | ConvertFrom-Json
        $KitMetadata = Get-Content -LiteralPath $KitMetadataPath -Raw | ConvertFrom-Json
        if ([string]$ReleaseManifest.product -ne "browser-mcp-server" -or
            [string]$ReleaseManifest.mcpServerName -ne "browser-mcp" -or
            [string]$KitMetadata.product -ne "browser-mcp-server" -or
            [string]$KitMetadata.mcpServerName -ne "browser-mcp") {
            throw "Public and payload metadata do not use the canonical browser-mcp-server identity."
        }

        # Parse both reviewed source and packaged maintenance scripts before
        # starting any runtime or browser process.
        Parse-PowerShellScripts (Join-Path $SourceRoot "scripts")
        Parse-PowerShellScripts $PayloadRoot
        Push-Location $SourceRoot
        try {
            Invoke-PythonChecked @("-c", "import sys; raise SystemExit(sys.version_info < (3, 10))")
            Invoke-PythonChecked @("-m", "unittest", "discover", "-s", "tests", "-v")
            Invoke-PythonChecked @(
                (Join-Path $SourceRoot "scripts\register_claude_user_mcp.py"),
                "self-test"
            )
        } finally {
            Pop-Location
        }

        $RuntimeArchiveName = [string]$KitMetadata.runtime.archive
        $RuntimeArchive = Join-Path (Join-Path $PayloadRoot "runtime") $RuntimeArchiveName
        Invoke-PythonChecked @($Verifier, $RuntimeArchive)
        $RuntimeExtraction = Join-Path $TemporaryRoot "runtime"
        New-Item -ItemType Directory -Path $RuntimeExtraction -Force | Out-Null
        $Tar = Get-Command "tar.exe" -ErrorAction SilentlyContinue
        if (-not $Tar) { throw "Windows tar.exe is required for runtime verification." }
        Invoke-NativeChecked $Tar.Source @("-xzf", $RuntimeArchive, "-C", $RuntimeExtraction)
        $RuntimeName = $RuntimeArchiveName -replace '\.tar\.gz$', ''
        $RuntimeRoot = Join-Path $RuntimeExtraction $RuntimeName
        Invoke-PythonChecked @($Verifier, $RuntimeRoot)
        $BuildMetadata = $KitMetadata.runtime.buildMetadata
        $NodeRoot = Join-Path $RuntimeRoot "node"
        $NodeExe = Join-Path $NodeRoot "node.exe"
        $NodeApprovals = Join-Path $SourceRoot "config\windows-node-sources.json"
        Invoke-PythonChecked @(
            (Join-Path $SourceRoot "scripts\validate_node_distribution.py"),
            $NodeRoot,
            "--expected-version", [string]$BuildMetadata.tools.node,
            "--approval-file", $NodeApprovals
        )
        $ActualNode = Get-NativeOutput $NodeExe @("--version")
        if ($ActualNode -ne [string]$BuildMetadata.tools.node) {
            throw "Packaged Node.js reports $ActualNode; expected $($BuildMetadata.tools.node)"
        }
        $Wrapper = Join-Path $RuntimeRoot "bin\intranet-browser-agent-mcp.js"
        $ProbeConfig = Join-Path $TemporaryRoot "playwright.config.json"
        [IO.File]::WriteAllText(
            $ProbeConfig, "{}`n", (New-Object System.Text.UTF8Encoding($false))
        )
        # The compatibility wrapper requires its interaction sidecar whenever
        # --config is supplied. Keep this probe configuration valid and
        # deterministic instead of letting the first runtime handshake fail
        # before Claude Code is exercised.
        $ProbeInteractionConfig = Join-Path $TemporaryRoot "interaction.config.json"
        $ProbeInteraction = @{
            schemaVersion = 1
            snapshotStrategy = "full"
            compatibilityMode = "standard"
            defaultSnapshotDepth = 6
            settleMs = 1500
        } | ConvertTo-Json -Compress
        [IO.File]::WriteAllText(
            $ProbeInteractionConfig,
            "$ProbeInteraction`n",
            (New-Object System.Text.UTF8Encoding($false))
        )
        Invoke-PythonChecked @(
            (Join-Path $SourceRoot "scripts\smoke_playwright_mcp.py"),
            "--node-executable", $NodeExe,
            "--playwright-cli", $Wrapper,
            "--playwright-config", $ProbeConfig,
            "--expected-server-name", "browser-mcp"
        )

        # Use an isolated Claude user config for a real remove/add/get probe.
        $ProbeClaudeConfig = Join-Path $TemporaryRoot "claude-config"
        New-Item -ItemType Directory -Path $ProbeClaudeConfig -Force | Out-Null
        $ProbeUserConfig = Join-Path $ProbeClaudeConfig ".claude.json"
        $ProbeBackup = Join-Path $TemporaryRoot "claude-user-config.bak"
        $HadClaudeConfigDir = Test-Path Env:CLAUDE_CONFIG_DIR
        $PreviousClaudeConfigDir = $env:CLAUDE_CONFIG_DIR
        try {
            $env:CLAUDE_CONFIG_DIR = $ProbeClaudeConfig
            $Registrar = Join-Path $SourceRoot "scripts\register_claude_user_mcp.py"
            $Registration = @(
                $Registrar, "register",
                "--claude-executable", [string]$ClaudeInvocation.Executable
            )
            foreach ($Prefix in @($ClaudeInvocation.Prefix)) {
                $Registration += @("--claude-prefix", [string]$Prefix)
            }
            $Registration += @(
                "--server-name", "browser-mcp",
                "--node-executable", $NodeExe,
                "--playwright-cli", $Wrapper,
                "--playwright-config", $ProbeConfig,
                "--user-config", $ProbeUserConfig,
                "--backup", $ProbeBackup
            )
            Invoke-PythonChecked $Registration
            if (-not (Test-Path -LiteralPath $ProbeUserConfig -PathType Leaf)) {
                throw "Claude Code did not create the isolated user configuration."
            }
        } finally {
            if ($HadClaudeConfigDir) {
                $env:CLAUDE_CONFIG_DIR = $PreviousClaudeConfigDir
            } else {
                Remove-Item Env:CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue
            }
        }

        # A final pass proves the gate and its tests did not mutate the package.
        Invoke-PythonChecked @($Verifier, $ResolvedPackageRoot)
    } finally {
        if (Test-Path -LiteralPath $TemporaryRoot) {
            Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "WINDOWS POWERSHELL 5.1 RELEASE GATE PASSED" -ForegroundColor Green
}

$TranscriptStarted = $false
try {
    if ($LogPath) {
        $Directory = Split-Path -Parent $LogPath
        if ($Directory) { New-Item -ItemType Directory -Path $Directory -Force | Out-Null }
        Start-Transcript -LiteralPath $LogPath -Force | Out-Null
        $TranscriptStarted = $true
    }
    Invoke-ReleaseGate
} finally {
    if ($TranscriptStarted) { Stop-Transcript | Out-Null }
}
