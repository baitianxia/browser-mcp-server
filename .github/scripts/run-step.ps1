# Run a GitHub Actions PowerShell step and report its failure as annotations.
#
# Usage in a workflow step:
#   shell: powershell -NoLogo -NoProfile -File .github\scripts\run-step.ps1 {0}
#
# Actions logs of this public repository need a signed-in GitHub account, but
# check-run annotations do not.  The step script is dot-sourced like the
# default `powershell -command ". '{0}'"` shell; every step that uses this
# wrapper sets $ErrorActionPreference = "Stop" itself.  When it
# throws, this wrapper prints the error, its position, the newest installer and
# settings log tails, and the tails of any *Stdout/*Stderr files the step
# defined, then exits 1.  Non-ASCII text is written as \uXXXX so it survives
# the runner's console code page.  Keep this file ASCII-only.
param([Parameter(Mandatory = $true)][string]$StepScript)

function ConvertTo-AnnotationText {
    param([string]$Text, [int]$MaxChars = 6000)
    if ($null -eq $Text) {
        return ""
    }
    if ($Text.Length -gt $MaxChars) {
        $Text = "...`n" + $Text.Substring($Text.Length - $MaxChars)
    }
    $Builder = New-Object System.Text.StringBuilder
    foreach ($Character in $Text.ToCharArray()) {
        $Code = [int]$Character
        if ($Code -eq 10) {
            [void]$Builder.Append("%0A")
        } elseif ($Code -eq 13) {
            [void]$Builder.Append("%0D")
        } elseif ($Code -eq 37) {
            [void]$Builder.Append("%25")
        } elseif ($Code -gt 126 -or ($Code -lt 32 -and $Code -ne 9)) {
            [void]$Builder.Append(("\u{0:x4}" -f $Code))
        } else {
            [void]$Builder.Append($Character)
        }
    }
    return $Builder.ToString()
}

function Write-FailureAnnotation {
    param([string]$Title, [string]$Message)
    $SafeTitle = (ConvertTo-AnnotationText $Title 200).Replace(":", "%3A").Replace(",", "%2C")
    Write-Host ("::error title={0}::{1}" -f $SafeTitle, (ConvertTo-AnnotationText $Message))
}

function Get-TailText {
    param([string]$Path, [int]$Lines = 80)
    try {
        return ((Get-Content -LiteralPath $Path -Tail $Lines -Encoding UTF8 `
            -ErrorAction Stop) -join "`n")
    } catch {
        return "(could not read: $($_.Exception.Message))"
    }
}

try {
    . $StepScript
} catch {
    $Failure = $_
    try {
        Write-FailureAnnotation "Step failed" (
            $Failure.Exception.Message + "`n" +
            $Failure.InvocationInfo.PositionMessage + "`n" +
            $Failure.ScriptStackTrace
        )
        $LogRoot = Join-Path $env:TEMP "browser-mcp-server"
        foreach ($Pattern in @("INSTALL-*.log", "CONFIGURE-*.log")) {
            $Latest = Get-ChildItem -LiteralPath $LogRoot -Filter $Pattern `
                -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if ($Latest) {
                Write-FailureAnnotation "Tail of $($Latest.Name)" (Get-TailText $Latest.FullName)
            }
        }
        $Reported = @{}
        foreach ($Variable in @(Get-Variable -Name "*Stdout", "*Stderr" `
            -ErrorAction SilentlyContinue)) {
            $Path = [string]$Variable.Value
            if (-not $Path -or $Reported.ContainsKey($Path.ToLowerInvariant())) {
                continue
            }
            $Reported[$Path.ToLowerInvariant()] = $true
            try {
                $IsReportable = (Test-Path -LiteralPath $Path -PathType Leaf) -and
                    (Get-Item -LiteralPath $Path).Length -gt 0
            } catch {
                $IsReportable = $false
            }
            if ($IsReportable) {
                Write-FailureAnnotation "Tail of `$$($Variable.Name)" (Get-TailText $Path 40)
            }
        }
    } catch {
        Write-Host "Failure annotation reporting failed: $($_.Exception.Message)"
    }
    Write-Host ($Failure | Out-String)
    exit 1
}
