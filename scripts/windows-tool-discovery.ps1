#requires -Version 5.1

function Resolve-NpmClaudeInvocation {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$CommandPath)

    if (-not (Test-Path -LiteralPath $CommandPath -PathType Leaf) -or
        [IO.Path]::GetExtension($CommandPath) -ine ".cmd") {
        return $null
    }

    try {
        $ResolvedCommand = (Resolve-Path -LiteralPath $CommandPath -ErrorAction Stop).Path
        $CommandRoot = Split-Path -Parent $ResolvedCommand
        $PackageRootCandidate = Join-Path $CommandRoot `
            "node_modules\@anthropic-ai\claude-code"
        if (-not (Test-Path -LiteralPath $PackageRootCandidate -PathType Container)) {
            return $null
        }
        $PackageRoot = (Resolve-Path -LiteralPath $PackageRootCandidate `
            -ErrorAction Stop).Path
        $PackageJsonPath = Join-Path $PackageRoot "package.json"
        if (-not (Test-Path -LiteralPath $PackageJsonPath -PathType Leaf)) {
            return $null
        }
        $Package = Get-Content -LiteralPath $PackageJsonPath -Raw `
            -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ([string]$Package.name -ne "@anthropic-ai/claude-code") {
            return $null
        }

        $BinPath = ""
        if ($Package.bin -is [string]) {
            $BinPath = [string]$Package.bin
        } elseif ($null -ne $Package.bin) {
            $ClaudeBin = $Package.bin.PSObject.Properties["claude"]
            if ($null -ne $ClaudeBin) {
                $BinPath = [string]$ClaudeBin.Value
            }
        }
        if (-not $BinPath -or [IO.Path]::IsPathRooted($BinPath)) {
            return $null
        }
        $PackageRootFull = [IO.Path]::GetFullPath($PackageRoot).TrimEnd("\")
        $PackageBinPath = [IO.Path]::GetFullPath((Join-Path $PackageRootFull $BinPath))
        $PackagePrefix = $PackageRootFull + "\"
        if (-not $PackageBinPath.StartsWith(
            $PackagePrefix,
            [StringComparison]::OrdinalIgnoreCase
        ) -or -not (Test-Path -LiteralPath $PackageBinPath -PathType Leaf)) {
            return $null
        }

        # Recent npm Claude Code packages publish a Windows-native bin target
        # (for example, bin\claude.exe) instead of a JavaScript CLI.  Do not
        # pass that PE file to Node: Node's ESM loader then reports
        # ERR_UNKNOWN_FILE_EXTENSION for .exe.  Native package bins are still
        # represented as an npm invocation so all callers keep the same
        # command/prefix contract, but the executable is the package bin and
        # the prefix is empty.
        $BinExtension = [IO.Path]::GetExtension($PackageBinPath).ToLowerInvariant()
        if ($BinExtension -eq ".exe") {
            return [pscustomobject]@{
                CommandPath = $ResolvedCommand
                Executable = $PackageBinPath
                Prefix = [string[]]@()
                Kind = "npm-native"
            }
        }
        if ($BinExtension -notin @(".js", ".cjs", ".mjs")) {
            return $null
        }

        $NodeCandidates = @((Join-Path $CommandRoot "node.exe"))
        $NodeCommand = Get-Command "node.exe" -CommandType Application `
            -ErrorAction SilentlyContinue
        if ($NodeCommand) {
            $NodeResolved = if ($NodeCommand.Source) {
                $NodeCommand.Source
            } else {
                $NodeCommand.Path
            }
            if ($NodeResolved) {
                $NodeCandidates += $NodeResolved
            }
        }
        foreach ($NodeCandidate in $NodeCandidates) {
            if (-not $NodeCandidate -or
                -not (Test-Path -LiteralPath $NodeCandidate -PathType Leaf)) {
                continue
            }
            $ResolvedNode = (Resolve-Path -LiteralPath $NodeCandidate `
                -ErrorAction Stop).Path
            if ([IO.Path]::GetExtension($ResolvedNode) -ine ".exe") {
                continue
            }
            return [pscustomobject]@{
                CommandPath = $ResolvedCommand
                Executable = $ResolvedNode
                Prefix = [string[]]@($PackageBinPath)
                Kind = "npm-js"
            }
        }
    } catch {
        return $null
    }
    return $null
}

function Resolve-ClaudeCodeInvocation {
    [CmdletBinding()]
    param([string]$ExplicitPath = "")

    $Candidates = @()
    if ($ExplicitPath) {
        # An explicit release-runner path is authoritative. Do not silently
        # replace a bad explicit path with a different installation.
        $Candidates = @($ExplicitPath)
    } else {
        foreach ($CommandName in @("claude.exe", "claude.cmd")) {
            $ClaudeCommand = Get-Command $CommandName `
                -CommandType Application -ErrorAction SilentlyContinue
            if ($ClaudeCommand) {
                $ResolvedCommand = if ($ClaudeCommand.Source) {
                    $ClaudeCommand.Source
                } else {
                    $ClaudeCommand.Path
                }
                if ($ResolvedCommand) {
                    $Candidates += $ResolvedCommand
                }
            }
        }

        # The native Claude Code installer uses this per-user location. A
        # newly installed executable can exist here before Explorer or an
        # already-open terminal has refreshed its inherited PATH.
        if ($env:USERPROFILE) {
            $Candidates += (Join-Path $env:USERPROFILE ".local\bin\claude.exe")
        }
    }

    foreach ($Candidate in $Candidates) {
        if (-not $Candidate -or
            -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            continue
        }
        try {
            $ResolvedCandidate = (Resolve-Path -LiteralPath $Candidate `
                -ErrorAction Stop).Path
        } catch {
            continue
        }
        $Extension = [IO.Path]::GetExtension($ResolvedCandidate)
        if ($Extension -ieq ".exe") {
            return [pscustomobject]@{
                CommandPath = $ResolvedCandidate
                Executable = $ResolvedCandidate
                Prefix = [string[]]@()
                Kind = "native"
            }
        }
        if ($Extension -ieq ".cmd") {
            $NpmInvocation = Resolve-NpmClaudeInvocation `
                -CommandPath $ResolvedCandidate
            if ($null -ne $NpmInvocation) {
                return $NpmInvocation
            }
        }
    }
    return $null
}

# Windows packages carry no node.exe.  The installer, settings tool, and
# publisher gate all select the target's existing Node.js through this one
# resolver so they accept and reject exactly the same candidates.  Keep this
# file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.
function Get-NodeRuntimeProbe {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Executable)

    # NODE_OPTIONS/NODE_PATH could preload code into the probe.  The MCP
    # itself runs with both cleared, so probe under the same conditions.
    $Saved = @{}
    foreach ($Name in @("NODE_OPTIONS", "NODE_PATH")) {
        $Saved[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
        [Environment]::SetEnvironmentVariable($Name, $null, "Process")
    }
    $PreviousPreference = $ErrorActionPreference
    $Output = @()
    $ExitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        $Output = & $Executable -p "process.version+'/'+process.arch" 2>&1
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
        foreach ($Name in @($Saved.Keys)) {
            [Environment]::SetEnvironmentVariable($Name, $Saved[$Name], "Process")
        }
    }
    $Text = ((@($Output) | ForEach-Object { [string]$_ }) -join "`n").Trim()
    if ($ExitCode -ne 0) {
        throw "exit code ${ExitCode}: $Text"
    }
    $Match = [regex]::Match($Text, '^v(\d+)\.(\d+)\.(\d+)/([A-Za-z0-9_]+)$')
    if (-not $Match.Success) {
        throw "unexpected version output: $Text"
    }
    return [pscustomobject]@{
        Executable = $Executable
        Text = ("v{0}.{1}.{2}" -f $Match.Groups[1].Value,
            $Match.Groups[2].Value, $Match.Groups[3].Value)
        Version = [version]("{0}.{1}.{2}" -f $Match.Groups[1].Value,
            $Match.Groups[2].Value, $Match.Groups[3].Value)
        Arch = $Match.Groups[4].Value
    }
}

function Resolve-SystemNodeRuntime {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$MinimumVersion,
        [string]$ExplicitPath = "",
        [string[]]$ExcludedRoots = @()
    )

    $Minimum = [version]$MinimumVersion
    $Candidates = @()
    if ($ExplicitPath) {
        # An explicit path (release gate, or the path recorded at install
        # time) is authoritative; never substitute another installation.
        $Candidates = @($ExplicitPath)
    } else {
        foreach ($NodeCommand in @(Get-Command "node.exe" `
            -CommandType Application -ErrorAction SilentlyContinue)) {
            $Resolved = if ($NodeCommand.Source) {
                $NodeCommand.Source
            } else {
                $NodeCommand.Path
            }
            if ($Resolved) {
                $Candidates += $Resolved
            }
        }
        # Common per-machine locations that an already-open Explorer or
        # terminal may not have on PATH yet (official MSI and nvm-windows).
        if ($env:NVM_SYMLINK) {
            $Candidates += (Join-Path $env:NVM_SYMLINK "node.exe")
        }
        if ($env:ProgramFiles) {
            $Candidates += (Join-Path $env:ProgramFiles "nodejs\node.exe")
        }
    }

    $NormalizedExclusions = @()
    foreach ($Root in @($ExcludedRoots)) {
        if ($Root) {
            $NormalizedExclusions += ([IO.Path]::GetFullPath($Root).TrimEnd("\") + "\")
        }
    }

    $Rejected = @()
    $Seen = @{}
    foreach ($Candidate in $Candidates) {
        if (-not $Candidate) {
            continue
        }
        try {
            $FullPath = [IO.Path]::GetFullPath($Candidate)
        } catch {
            $Rejected += "${Candidate}: invalid path"
            continue
        }
        $Key = $FullPath.ToLowerInvariant()
        if ($Seen.ContainsKey($Key)) {
            continue
        }
        $Seen[$Key] = $true
        if ($FullPath -notmatch '^[A-Za-z]:\\') {
            $Rejected += "${FullPath}: not a local drive path"
            continue
        }
        if ([IO.Path]::GetFileName($FullPath) -ine "node.exe") {
            $Rejected += "${FullPath}: not node.exe"
            continue
        }
        if ($FullPath -match '\\WindowsApps\\') {
            $Rejected += "${FullPath}: WindowsApps alias is not a Node.js installation"
            continue
        }
        $Excluded = $false
        foreach ($Prefix in $NormalizedExclusions) {
            if ($FullPath.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) {
                $Excluded = $true
            }
        }
        if ($Excluded) {
            $Rejected += "${FullPath}: inside the product directory (packaged copies are not used)"
            continue
        }
        if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
            $Rejected += "${FullPath}: file not found"
            continue
        }
        try {
            $Probe = Get-NodeRuntimeProbe -Executable $FullPath
        } catch {
            $Rejected += "${FullPath}: $($_.Exception.Message)"
            continue
        }
        if ($Probe.Arch -ne "x64") {
            $Rejected += "${FullPath}: $($Probe.Text) is $($Probe.Arch), x64 is required"
            continue
        }
        if ($Probe.Version -lt $Minimum) {
            $Rejected += "${FullPath}: $($Probe.Text) is older than $MinimumVersion"
            continue
        }
        return [pscustomobject]@{
            Executable = $FullPath
            Text = $Probe.Text
            Version = $Probe.Version
            Arch = $Probe.Arch
            Rejected = [string[]]$Rejected
        }
    }
    return [pscustomobject]@{
        Executable = ""
        Text = ""
        Version = $null
        Arch = ""
        Rejected = [string[]]$Rejected
    }
}
