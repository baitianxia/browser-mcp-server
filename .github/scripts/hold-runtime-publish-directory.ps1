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

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class IntranetBrowserAgentDirectoryLock {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern SafeFileHandle CreateFile(
        string name,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);
}
"@

$Deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
$LockedPath = $null
$Handle = $null
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
            # file the final archive entry. Waiting for it avoids consuming the
            # hold interval while tar.exe is still extracting node_modules.
            $ExtractionCompletionMarker = Join-Path $Candidate.FullName `
                "pnpm-workspace.yaml"
            if (-not (Test-Path -LiteralPath $NodePath -PathType Leaf) -or
                -not (Test-Path -LiteralPath $ExtractionCompletionMarker `
                    -PathType Leaf)) {
                continue
            }
            # FILE_SHARE_READ | FILE_SHARE_WRITE deliberately omits
            # FILE_SHARE_DELETE. This reproduces an EDR-style directory hold
            # without changing ACLs or package contents.
            $Handle = [IntranetBrowserAgentDirectoryLock]::CreateFile(
                $Candidate.FullName,
                0,
                3,
                [IntPtr]::Zero,
                3,
                0x02000000,
                [IntPtr]::Zero
            )
            if (-not $Handle.IsInvalid) {
                $LockedPath = $Candidate.FullName
                break
            }
            $Handle.Dispose()
            $Handle = $null
        }
        if ($LockedPath) {
            break
        }
        Start-Sleep -Milliseconds 25
    }
    if (-not $LockedPath -or -not $Handle -or $Handle.IsInvalid) {
        throw "The CI fault injector could not lock a staged runtime directory."
    }
    [IO.File]::WriteAllText(
        $MarkerPath,
        $LockedPath,
        (New-Object System.Text.UTF8Encoding($false))
    )
    $LockAcquiredAtUtc = [DateTime]::UtcNow
    Write-Host ("CI RUNTIME PUBLISH DIRECTORY LOCKED {0}: {1}" -f `
        (Get-Date -Format "o"), $LockedPath)
    $RetryDeadline = $LockAcquiredAtUtc.AddSeconds($RetryWaitSeconds)
    while ([DateTime]::UtcNow -lt $RetryDeadline) {
        $InstallLogs = @(Get-ChildItem -LiteralPath $InstallLogRoot `
            -Filter "INSTALL-WINDOWS-PILOT-*.log" -File `
            -ErrorAction SilentlyContinue | Where-Object {
                $_.LastWriteTimeUtc -ge $LockAcquiredAtUtc
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
        if (-not (Test-Path -LiteralPath $LockedPath -PathType Container)) {
            throw "The staged runtime moved without the expected sharing violation."
        }
        Start-Sleep -Milliseconds 25
    }
    if (-not $RetryObserved) {
        throw "The installer did not attempt runtime publish while the directory handle was held."
    }
    Write-Host ("CI RUNTIME PUBLISH RETRY OBSERVED {0}: {1}" -f `
        (Get-Date -Format "o"), $LockedPath)
} finally {
    if ($Handle) {
        $Handle.Dispose()
    }
}

Write-Host ("CI RUNTIME PUBLISH DIRECTORY RELEASED {0}: {1}" -f `
    (Get-Date -Format "o"), $LockedPath)
