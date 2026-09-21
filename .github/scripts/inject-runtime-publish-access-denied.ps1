#requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$StagingRoot,
    [Parameter(Mandatory = $true)][string]$ReadyFile,
    [Parameter(Mandatory = $true)][string]$ReleaseFile,
    [Parameter(Mandatory = $true)][string]$MarkerPath,
    [Parameter(Mandatory = $true)][string]$InstallLogRoot,
    [ValidateRange(10, 300)][int]$RetryWaitSeconds = 180,
    [ValidateRange(10, 600)][int]$WaitSeconds = 300
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DeniedPath = $null
$DeniedParentPath = $null
$OriginalRuntimeAccessSddl = $null
$OriginalParentAccessSddl = $null
$RuntimeAccessDenied = $false
$ParentAccessDenied = $false
$AccessDeniedArmed = $false
$RetryObserved = $false
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Get-ContainedReadyPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )
    if (-not [IO.Path]::IsPathRooted($Path)) {
        throw "The installer published a non-absolute runtime gate path."
    }
    $ResolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd("\")
    $ResolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd("\") + "\"
    if (-not $ResolvedPath.StartsWith(
        $ResolvedRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "The runtime gate path escaped the isolated staging root."
    }
    return $ResolvedPath
}

try {
    $ReadyDeadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
    while (-not (Test-Path -LiteralPath $ReadyFile -PathType Leaf) -and
        [DateTime]::UtcNow -lt $ReadyDeadline) {
        Start-Sleep -Milliseconds 25
    }
    if (-not (Test-Path -LiteralPath $ReadyFile -PathType Leaf)) {
        throw "The installer did not publish its runtime gate before the timeout."
    }

    $ReadyText = [IO.File]::ReadAllText($ReadyFile, $Utf8NoBom).Trim()
    if (-not $ReadyText) {
        throw "The installer published an empty runtime gate path."
    }
    $DeniedPath = Get-ContainedReadyPath -Path $ReadyText -Root $StagingRoot
    if (-not (Test-Path -LiteralPath $DeniedPath -PathType Container)) {
        throw "The staged runtime gate path does not exist: $DeniedPath"
    }
    $DeniedParentPath = Split-Path -Parent $DeniedPath

    # The installer is paused immediately before Directory.Move, so ACL
    # inspection and arming no longer race extraction or publication.
    $RuntimeAcl = Get-Acl -LiteralPath $DeniedPath
    $ParentAcl = Get-Acl -LiteralPath $DeniedParentPath
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

    # Windows permits a rename when either Delete on the child or DeleteChild
    # on its parent is granted. Deny both paths before releasing the installer.
    $null = $ParentAcl.AddAccessRule($DenyDeleteChildRule)
    Set-Acl -LiteralPath $DeniedParentPath -AclObject $ParentAcl
    $ParentAccessDenied = $true
    $null = $RuntimeAcl.AddAccessRule($DenyDeleteRule)
    Set-Acl -LiteralPath $DeniedPath -AclObject $RuntimeAcl
    $RuntimeAccessDenied = $true
    $AccessDeniedArmed = $true

    [IO.File]::WriteAllText(
        $MarkerPath,
        "$DeniedPath`n$DeniedParentPath",
        $Utf8NoBom
    )
    [IO.File]::WriteAllText($ReleaseFile, "allow`n", $Utf8NoBom)
    $FaultArmedAtUtc = [DateTime]::UtcNow
    Write-Host ("CI RUNTIME PUBLISH ACCESS DENIED ARMED {0}: {1}" -f `
        (Get-Date -Format "o"), $DeniedPath)

    $RetryDeadline = $FaultArmedAtUtc.AddSeconds($RetryWaitSeconds)
    while ([DateTime]::UtcNow -lt $RetryDeadline) {
        $InstallLogs = @(Get-ChildItem -LiteralPath $InstallLogRoot `
            -Filter "INSTALL-*.log" -File -ErrorAction SilentlyContinue)
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
