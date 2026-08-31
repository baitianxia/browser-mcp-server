#requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$StagingRoot,
    [Parameter(Mandatory = $true)][string]$MarkerPath,
    [Parameter(Mandatory = $true)][string]$InstallLogRoot,
    [ValidateRange(10, 300)][int]$RetryWaitSeconds = 180,
    [ValidateRange(10, 600)][int]$WaitSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
$DeniedPath = $null
$OriginalAccessSddl = $null
$AccessDeniedArmed = $false
$RetryObserved = $false
try {
    while ([DateTime]::UtcNow -lt $Deadline) {
        $Candidates = @(Get-ChildItem -LiteralPath $StagingRoot `
            -Directory -Filter "r-*" -ErrorAction SilentlyContinue | ForEach-Object {
                Get-ChildItem -LiteralPath $_.FullName -Directory `
                    -Filter "browser-agent-runtime-*-windows-x64" `
                    -ErrorAction SilentlyContinue
            })
        foreach ($Candidate in $Candidates) {
            $NodePath = Join-Path $Candidate.FullName "node\node.exe"
            # Runtime archives are emitted in lexical order, making this root
            # file the final archive entry. Wait until extraction is complete
            # before changing only the directory's Delete permission.
            $ExtractionCompletionMarker = Join-Path $Candidate.FullName `
                "pnpm-workspace.yaml"
            if (-not (Test-Path -LiteralPath $NodePath -PathType Leaf) -or
                -not (Test-Path -LiteralPath $ExtractionCompletionMarker `
                    -PathType Leaf)) {
                continue
            }

            $CurrentAcl = Get-Acl -LiteralPath $Candidate.FullName
            $OriginalAccessSddl = $CurrentAcl.GetSecurityDescriptorSddlForm(
                [Security.AccessControl.AccessControlSections]::Access
            )
            $CurrentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
            $DenyDeleteRule = New-Object `
                -TypeName Security.AccessControl.FileSystemAccessRule `
                -ArgumentList @(
                    $CurrentSid,
                    [Security.AccessControl.FileSystemRights]::Delete,
                    [Security.AccessControl.AccessControlType]::Deny
                )
            $null = $CurrentAcl.AddAccessRule($DenyDeleteRule)
            Set-Acl -LiteralPath $Candidate.FullName -AclObject $CurrentAcl
            $DeniedPath = $Candidate.FullName
            $AccessDeniedArmed = $true
            break
        }
        if ($AccessDeniedArmed) {
            break
        }
        Start-Sleep -Milliseconds 25
    }
    if (-not $AccessDeniedArmed -or -not $DeniedPath -or
        -not $OriginalAccessSddl) {
        throw "The CI fault injector could not deny Delete on a staged runtime directory."
    }
    [IO.File]::WriteAllText(
        $MarkerPath,
        $DeniedPath,
        (New-Object System.Text.UTF8Encoding($false))
    )
    $FaultArmedAtUtc = [DateTime]::UtcNow
    Write-Host ("CI RUNTIME PUBLISH ACCESS DENIED ARMED {0}: {1}" -f `
        (Get-Date -Format "o"), $DeniedPath)

    $RetryDeadline = $FaultArmedAtUtc.AddSeconds($RetryWaitSeconds)
    while ([DateTime]::UtcNow -lt $RetryDeadline) {
        $InstallLogs = @(Get-ChildItem -LiteralPath $InstallLogRoot `
            -Filter "INSTALL-WINDOWS-PILOT-*.log" -File `
            -ErrorAction SilentlyContinue | Where-Object {
                $_.LastWriteTimeUtc -ge $FaultArmedAtUtc
            })
        foreach ($InstallLog in $InstallLogs) {
            if (Select-String -LiteralPath $InstallLog.FullName `
                -SimpleMatch "RUNTIME PUBLISH RETRY" -Quiet `
                -ErrorAction SilentlyContinue) {
                $RetryObserved = $true
                break
            }
        }
        if ($RetryObserved) {
            break
        }
        if (-not (Test-Path -LiteralPath $DeniedPath -PathType Container)) {
            throw "The staged runtime moved without the expected access denial."
        }
        Start-Sleep -Milliseconds 25
    }
    if (-not $RetryObserved) {
        throw "The installer did not attempt runtime publish while Delete was denied."
    }
    Write-Host ("CI RUNTIME PUBLISH RETRY OBSERVED {0}: {1}" -f `
        (Get-Date -Format "o"), $DeniedPath)
} finally {
    if ($AccessDeniedArmed -and $DeniedPath -and $OriginalAccessSddl -and
        (Test-Path -LiteralPath $DeniedPath -PathType Container)) {
        $RestoreAcl = Get-Acl -LiteralPath $DeniedPath
        $RestoreAcl.SetSecurityDescriptorSddlForm(
            $OriginalAccessSddl,
            [Security.AccessControl.AccessControlSections]::Access
        )
        Set-Acl -LiteralPath $DeniedPath -AclObject $RestoreAcl
        Write-Host ("CI RUNTIME PUBLISH ACCESS DENIED RESTORED {0}: {1}" -f `
            (Get-Date -Format "o"), $DeniedPath)
    }
}
