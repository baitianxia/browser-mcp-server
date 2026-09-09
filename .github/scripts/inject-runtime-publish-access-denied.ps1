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
$DeniedParentPath = $null
$OriginalRuntimeAccessSddl = $null
$OriginalParentAccessSddl = $null
$RuntimeAccessDenied = $false
$ParentAccessDenied = $false
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
            $DeniedPath = $Candidate.FullName
            $DeniedParentPath = Split-Path -Parent $DeniedPath
            # The installer may publish the staged directory between the
            # enumeration above and ACL inspection. Treat that one expected
            # race as a stale candidate and keep looking; do not leave a
            # partially armed parent ACL behind.
            try {
                $RuntimeAcl = Get-Acl -LiteralPath $DeniedPath
                $ParentAcl = Get-Acl -LiteralPath $DeniedParentPath
            } catch [System.Management.Automation.ItemNotFoundException] {
                $DeniedPath = $null
                $DeniedParentPath = $null
                continue
            }
            $OriginalRuntimeAccessSddl = $RuntimeAcl.GetSecurityDescriptorSddlForm(
                [Security.AccessControl.AccessControlSections]::Access
            )
            $OriginalParentAccessSddl = $ParentAcl.GetSecurityDescriptorSddlForm(
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
            $DenyDeleteChildRule = New-Object `
                -TypeName Security.AccessControl.FileSystemAccessRule `
                -ArgumentList @(
                    $CurrentSid,
                    [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles,
                    [Security.AccessControl.AccessControlType]::Deny
                )

            # Windows permits a rename when either Delete on the child or
            # DeleteChild on its parent is granted. Deny both paths before
            # extraction completes so the installer cannot race the injector.
            $null = $ParentAcl.AddAccessRule($DenyDeleteChildRule)
            Set-Acl -LiteralPath $DeniedParentPath -AclObject $ParentAcl
            $ParentAccessDenied = $true
            $null = $RuntimeAcl.AddAccessRule($DenyDeleteRule)
            Set-Acl -LiteralPath $DeniedPath -AclObject $RuntimeAcl
            $RuntimeAccessDenied = $true
            break
        }
        if ($RuntimeAccessDenied -and $ParentAccessDenied) {
            break
        }
        Start-Sleep -Milliseconds 25
    }
    if (-not $RuntimeAccessDenied -or -not $ParentAccessDenied -or
        -not $DeniedPath -or -not $DeniedParentPath -or
        -not $OriginalRuntimeAccessSddl -or -not $OriginalParentAccessSddl) {
        throw "The CI fault injector could not deny runtime Delete and parent DeleteChild."
    }

    # Runtime archives are emitted in lexical order, making this root file the
    # final archive entry. The ACLs are already armed, but the marker must only
    # announce a fully extracted runtime that is ready for publish.
    $NodePath = Join-Path $DeniedPath "node\node.exe"
    $ExtractionCompletionMarker = Join-Path $DeniedPath "pnpm-workspace.yaml"
    while ([DateTime]::UtcNow -lt $Deadline -and
        ((-not (Test-Path -LiteralPath $NodePath -PathType Leaf)) -or
        (-not (Test-Path -LiteralPath $ExtractionCompletionMarker -PathType Leaf)))) {
        if (-not (Test-Path -LiteralPath $DeniedPath -PathType Container)) {
            throw "The staged runtime moved before the access denial was fully armed."
        }
        Start-Sleep -Milliseconds 25
    }
    if (-not (Test-Path -LiteralPath $NodePath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ExtractionCompletionMarker -PathType Leaf)) {
        throw "The staged runtime did not finish extraction while access denial was armed."
    }
    $AccessDeniedArmed = $true
    [IO.File]::WriteAllText(
        $MarkerPath,
        "$DeniedPath`n$DeniedParentPath",
        (New-Object System.Text.UTF8Encoding($false))
    )
    $FaultArmedAtUtc = [DateTime]::UtcNow
    Write-Host ("CI RUNTIME PUBLISH ACCESS DENIED ARMED {0}: {1}" -f `
        (Get-Date -Format "o"), $DeniedPath)

    $RetryDeadline = $FaultArmedAtUtc.AddSeconds($RetryWaitSeconds)
    while ([DateTime]::UtcNow -lt $RetryDeadline) {
        $InstallLogs = @(Get-ChildItem -LiteralPath $InstallLogRoot `
            -Filter "INSTALL-*.log" -File `
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
    $RuntimeAclRestored = -not $RuntimeAccessDenied
    $ParentAclRestored = -not $ParentAccessDenied
    if ($RuntimeAccessDenied -and $DeniedPath -and
        $OriginalRuntimeAccessSddl -and
        (Test-Path -LiteralPath $DeniedPath -PathType Container)) {
        $RestoreRuntimeAcl = Get-Acl -LiteralPath $DeniedPath
        $RestoreRuntimeAcl.SetSecurityDescriptorSddlForm(
            $OriginalRuntimeAccessSddl,
            [Security.AccessControl.AccessControlSections]::Access
        )
        Set-Acl -LiteralPath $DeniedPath -AclObject $RestoreRuntimeAcl
        $RuntimeAclRestored = $true
    }
    if ($ParentAccessDenied -and $DeniedParentPath -and
        $OriginalParentAccessSddl -and
        (Test-Path -LiteralPath $DeniedParentPath -PathType Container)) {
        $RestoreParentAcl = Get-Acl -LiteralPath $DeniedParentPath
        $RestoreParentAcl.SetSecurityDescriptorSddlForm(
            $OriginalParentAccessSddl,
            [Security.AccessControl.AccessControlSections]::Access
        )
        Set-Acl -LiteralPath $DeniedParentPath -AclObject $RestoreParentAcl
        $ParentAclRestored = $true
    }
    if ($AccessDeniedArmed -and $RuntimeAclRestored -and $ParentAclRestored) {
        Write-Host ("CI RUNTIME PUBLISH ACCESS DENIED RESTORED {0}: {1}" -f `
            (Get-Date -Format "o"), $DeniedPath)
    }
}
