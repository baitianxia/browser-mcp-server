#requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet("core", "diagnostic")]
    [string]$Profile = "core",

    [string]$OutputDir = (Join-Path $PSScriptRoot "..\dist"),
    [string]$NodeDistribution = "",
    [string]$NodeBin = "",
    [string]$PnpmBin = "",
    [string]$PythonBin = "",
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:PythonExe = ""
$script:PythonPrefix = @()

function Resolve-Tool {
    param([string]$ExplicitPath, [string]$CommandName)
    if ($ExplicitPath) {
        if (-not (Test-Path -LiteralPath $ExplicitPath -PathType Leaf)) {
            throw "Tool not found: $ExplicitPath"
        }
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }
    $Command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if (-not $Command) {
        throw "Tool not found: $CommandName"
    }
    return $Command.Source
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    $Output = @()
    $ExitCode = 1
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 surfaces redirected native stderr as an
        # ErrorRecord. Native exit status remains the source of truth.
        $ErrorActionPreference = "Continue"
        $Output = & $Executable @Arguments 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
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

function Get-CheckedOutput {
    param([string]$Executable, [string[]]$Arguments)
    $Output = @()
    $ExitCode = 1
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $Output = & $Executable @Arguments 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    $Text = ((@($Output) | ForEach-Object { [string]$_ }) -join [Environment]::NewLine).Trim()
    if ($ExitCode -ne 0) {
        throw "$Executable failed with exit code ${ExitCode}: $Text"
    }
    return $Text
}

function Invoke-PythonChecked {
    param([string[]]$Arguments)
    $HadDontWriteBytecode = Test-Path Env:PYTHONDONTWRITEBYTECODE
    $PreviousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
    try {
        $env:PYTHONDONTWRITEBYTECODE = "1"
        Invoke-Checked $script:PythonExe ($script:PythonPrefix + $Arguments)
    } finally {
        if ($HadDontWriteBytecode) {
            $env:PYTHONDONTWRITEBYTECODE = $PreviousDontWriteBytecode
        } else {
            Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
        }
    }
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "Use build-offline-bundle.ps1 only on Windows"
}
if (-not $NodeDistribution) {
    throw "Windows one-click bundles require -NodeDistribution"
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$NodeBin = Resolve-Tool $NodeBin "node.exe"
$PnpmBin = Resolve-Tool $PnpmBin "pnpm.cmd"
if (-not $PythonBin) {
    $PythonCommand = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($PythonCommand) {
        $PythonBin = $PythonCommand.Source
    } else {
        $PythonBin = Resolve-Tool "" "python.exe"
    }
} else {
    $PythonBin = Resolve-Tool $PythonBin "python.exe"
}
$script:PythonExe = $PythonBin
if ([IO.Path]::GetFileName($PythonBin) -ieq "py.exe") {
    $script:PythonPrefix = @("-3")
}

$NodeVersion = Get-CheckedOutput $NodeBin @("--version")
if ([version]$NodeVersion.TrimStart("v") -lt [version]"20.19.0") {
    throw "Node.js 20.19+ is required; found $NodeVersion"
}
$PnpmVersion = Get-CheckedOutput $PnpmBin @("--version")
if ($PnpmVersion -ne "11.19.0") {
    throw "pnpm 11.19.0 is required; found $PnpmVersion"
}
Invoke-PythonChecked @("-c", "import sys; raise SystemExit(sys.version_info < (3, 10))")

$Architecture = $env:PROCESSOR_ARCHITECTURE.ToUpperInvariant()
switch ($Architecture) {
    "AMD64" { $TargetMachine = "x64" }
    default { throw "Windows V1 supports x64/AMD64 only; found $Architecture" }
}

$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$ArtifactName = "browser-agent-runtime-1.0.14-$Profile-windows-$TargetMachine"
$Archive = Join-Path $OutputDir "$ArtifactName.tar.gz"
$ArchiveSidecar = "$Archive.sha256"
if ((Test-Path -LiteralPath $Archive) -or (Test-Path -LiteralPath $ArchiveSidecar)) {
    if (-not $Force) {
        throw "Refusing to overwrite $Archive; pass -Force"
    }
}

$BuildTemp = Join-Path $OutputDir (".browser-agent-build." + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $BuildTemp | Out-Null
try {
    $Stage = Join-Path $BuildTemp $ArtifactName
    New-Item -ItemType Directory -Path (Join-Path $Stage "bin") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "runtime\package.json") -Destination $Stage
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "runtime\pnpm-lock.yaml") -Destination $Stage
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "runtime\pnpm-workspace.yaml") -Destination $Stage
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "runtime\bin\playwright-mcp.cmd") -Destination (Join-Path $Stage "bin")
    if ($Profile -eq "diagnostic") {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot "runtime\bin\chrome-devtools-mcp.cmd") -Destination (Join-Path $Stage "bin")
    }

    $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
    $env:PNPM_DISABLE_SELF_UPDATE_CHECK = "1"
    $env:NO_UPDATE_NOTIFIER = "1"
    $InstallArguments = @("--dir", $Stage, "install", "--prod", "--frozen-lockfile", "--ignore-scripts")
    Invoke-Checked $PnpmBin $InstallArguments

    Invoke-PythonChecked @(
        (Join-Path $PSScriptRoot "prepare_runtime_tree.py"),
        "--node-modules", (Join-Path $Stage "node_modules"),
        "--profile", $Profile
    )

    $RuntimeNode = $NodeBin
    $BundledNodeArguments = @()
    if ($NodeDistribution) {
        Invoke-PythonChecked @(
            (Join-Path $PSScriptRoot "validate_node_distribution.py"),
            $NodeDistribution,
            "--expected-version", $NodeVersion,
            "--approval-file", (Join-Path $ProjectRoot "config\windows-node-sources.json")
        )
        Copy-Item -LiteralPath $NodeDistribution -Destination (Join-Path $Stage "node") -Recurse
        Invoke-PythonChecked @(
            (Join-Path $PSScriptRoot "validate_node_distribution.py"),
            (Join-Path $Stage "node"),
            "--expected-version", $NodeVersion,
            "--approval-file", (Join-Path $ProjectRoot "config\windows-node-sources.json")
        )
        $RuntimeNode = Join-Path $Stage "node\node.exe"
        $BundledNodeArguments = @("--bundled-node")
    }

    $env:BROWSER_AGENT_NODE = $RuntimeNode
    Invoke-Checked $RuntimeNode @(
        (Join-Path $Stage "node_modules\@playwright\mcp\cli.js"),
        "--help"
    )
    Invoke-Checked (Join-Path $Stage "bin\playwright-mcp.cmd") @("--help")
    if ($Profile -eq "diagnostic") {
        Invoke-Checked (Join-Path $Stage "bin\chrome-devtools-mcp.cmd") @("--help")
    } elseif (Test-Path -LiteralPath (Join-Path $Stage "node_modules\chrome-devtools-mcp\package.json")) {
        throw "Core profile unexpectedly contains chrome-devtools-mcp"
    }

    Invoke-PythonChecked @(
        (Join-Path $PSScriptRoot "check_runtime_portability.py"),
        "--root", (Join-Path $Stage "node_modules"),
        "--target-os", "windows",
        "--target-arch", $TargetMachine
    )
    Invoke-PythonChecked @(
        (Join-Path $PSScriptRoot "generate_sbom.py"),
        "--node-modules", (Join-Path $Stage "node_modules"),
        "--output", (Join-Path $Stage "SBOM.cdx.json")
    )

    $MetadataArguments = @(
        (Join-Path $PSScriptRoot "write_build_metadata.py"),
        "--output", (Join-Path $Stage "BUILD-METADATA.json"),
        "--profile", $Profile,
        "--node-version", $NodeVersion,
        "--pnpm-version", $PnpmVersion,
        "--target-system", "windows",
        "--target-machine", $TargetMachine
    ) + $BundledNodeArguments
    Invoke-PythonChecked $MetadataArguments

    Invoke-PythonChecked @(
        (Join-Path $PSScriptRoot "write_integrity.py"),
        "--root", $Stage,
        "--reject-links"
    )
    $StagedArchive = Join-Path $BuildTemp "$ArtifactName.tar.gz"
    Invoke-PythonChecked @(
        (Join-Path $PSScriptRoot "create_bundle_archive.py"),
        "--root", $Stage,
        "--output", $StagedArchive,
        "--reject-links"
    )
    Invoke-PythonChecked @((Join-Path $PSScriptRoot "write_archive_hash.py"), $StagedArchive)
    Invoke-PythonChecked @((Join-Path $PSScriptRoot "verify-bundle.py"), $StagedArchive)

    if ($Force) {
        Remove-Item -LiteralPath $Archive, $ArchiveSidecar -Force -ErrorAction SilentlyContinue
    }
    Move-Item -LiteralPath $StagedArchive -Destination $Archive
    Move-Item -LiteralPath "$StagedArchive.sha256" -Destination $ArchiveSidecar
    Write-Output "Built $Archive"
    Write-Output "Checksum $ArchiveSidecar"
} finally {
    Remove-Item -LiteralPath $BuildTemp -Recurse -Force -ErrorAction SilentlyContinue
}
