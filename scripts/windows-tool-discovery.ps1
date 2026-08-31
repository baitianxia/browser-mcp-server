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
        $CliPath = [IO.Path]::GetFullPath((Join-Path $PackageRootFull $BinPath))
        $PackagePrefix = $PackageRootFull + "\"
        if (-not $CliPath.StartsWith(
            $PackagePrefix,
            [StringComparison]::OrdinalIgnoreCase
        ) -or -not (Test-Path -LiteralPath $CliPath -PathType Leaf)) {
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
                Prefix = [string[]]@($CliPath)
                Kind = "npm"
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
