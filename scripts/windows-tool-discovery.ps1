#requires -Version 5.1

function Resolve-NativeClaudeExecutable {
    [CmdletBinding()]
    param([string]$ExplicitPath = "")

    $Candidates = @()
    if ($ExplicitPath) {
        # An explicitly supplied release-runner path is authoritative. Do not
        # silently replace a bad explicit path with a different installation.
        $Candidates = @($ExplicitPath)
    } else {
        $ClaudeCommand = Get-Command "claude.exe" `
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

        # The native Claude Code installer uses this per-user location. A
        # newly installed executable can exist here before Explorer or an
        # already-open terminal has refreshed its inherited PATH.
        if ($env:USERPROFILE) {
            $Candidates += (Join-Path $env:USERPROFILE ".local\bin\claude.exe")
        }
    }

    foreach ($Candidate in $Candidates) {
        if (-not $Candidate -or
            [IO.Path]::GetExtension([string]$Candidate) -ine ".exe" -or
            -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            continue
        }
        try {
            return (Resolve-Path -LiteralPath $Candidate -ErrorAction Stop).Path
        } catch {
            continue
        }
    }
    return ""
}
