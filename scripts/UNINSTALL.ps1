#requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "browser-mcp-server uninstall is supported only on Windows."
}
if (-not $env:USERPROFILE -or $env:USERPROFILE -notmatch '^[A-Za-z]:[\\/]') {
    throw "USERPROFILE must be a local absolute Windows path."
}

$ServerRoot = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE "browser-mcp-server"))
$ClaudeConfigDirectory = if ($env:CLAUDE_CONFIG_DIR) {
    [string]$env:CLAUDE_CONFIG_DIR
} else {
    $env:USERPROFILE
}
if ($ClaudeConfigDirectory -notmatch '^[A-Za-z]:[\\/]') {
    throw "CLAUDE_CONFIG_DIR must be a local absolute Windows path."
}
$ClaudeConfigDirectory = [IO.Path]::GetFullPath($ClaudeConfigDirectory)
$BackupRoot = Join-Path $env:USERPROFILE (
    "browser-mcp-server-backups\\uninstall-{0}-{1}" -f
    (Get-Date -Format "yyyyMMdd-HHmmss-fff"),
    [guid]::NewGuid().ToString("N").Substring(0, 8)
)

$ToolDiscoveryCandidates = @(
    (Join-Path $PSScriptRoot "..\toolkit\scripts\windows-tool-discovery.ps1"),
    (Join-Path $PSScriptRoot "windows-tool-discovery.ps1")
)
$ToolDiscovery = $ToolDiscoveryCandidates |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
if (-not $ToolDiscovery) {
    throw "browser-mcp-server tool discovery helper is missing."
}
. $ToolDiscovery

if ($env:CLAUDE_CONFIG_DIR -and -not (Test-Path -LiteralPath $env:CLAUDE_CONFIG_DIR -PathType Container)) {
    throw "CLAUDE_CONFIG_DIR is not a directory."
}

$LockPath = Join-Path $ServerRoot ".install.lock"
$LockStream = $null
if (Test-Path -LiteralPath $ServerRoot -PathType Leaf) {
    $ServerItem = Get-Item -LiteralPath $ServerRoot -Force
    if (($ServerItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to uninstall a reparse-point server root: $ServerRoot"
    }
    throw "Refusing to uninstall because the server root is a regular file: $ServerRoot"
}
if (Test-Path -LiteralPath $ServerRoot -PathType Container) {
    $ServerItem = Get-Item -LiteralPath $ServerRoot -Force
    if (($ServerItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to uninstall a reparse-point server root: $ServerRoot"
    }
    if (Test-Path -LiteralPath $LockPath -PathType Leaf) {
        $LockItem = Get-Item -LiteralPath $LockPath -Force
        if (($LockItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to open a reparse-point install lock: $LockPath"
        }
    }
    try {
        $LockStream = [IO.File]::Open(
            $LockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            # Keep read/write exclusivity while allowing the containing
            # server directory to be moved to its recoverable backup.
            # A second locker requesting ReadWrite/None still conflicts with
            # this handle because Read and Write are not shared.
            [IO.FileShare]::Delete
        )
    } catch {
        throw "Another browser-mcp-server operation is using $ServerRoot."
    }
}

try {
    $ClaudeInvocation = Resolve-ClaudeCodeInvocation
    if (-not $ClaudeInvocation) {
        throw "An existing usable Claude Code command is required; the active installation was left unchanged."
    }
    try {
        $RemoveArguments = @($ClaudeInvocation.Prefix) + @(
            "mcp", "remove", "browser-mcp", "--scope", "user"
        )
        & $ClaudeInvocation.Executable @RemoveArguments 2>$null
        if ($LASTEXITCODE -notin @(0, 1)) {
            throw "Claude Code could not remove browser-mcp (exit $LASTEXITCODE)."
        }
    } catch {
        throw "Could not remove browser-mcp from Claude Code automatically; the active installation was left unchanged: $($_.Exception.Message)"
    }
    if (Test-Path -LiteralPath $ServerRoot) {
        New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
        $BackupPath = Join-Path $BackupRoot "browser-mcp-server"
        [IO.Directory]::Move($ServerRoot, $BackupPath)
        if ((Test-Path -LiteralPath $ServerRoot) -or
            -not (Test-Path -LiteralPath $BackupPath -PathType Container)) {
            throw "Uninstall move did not leave a recoverable backup."
        }
        Write-Host "browser-mcp-server was removed from the active path."
        Write-Host "Recoverable backup: $BackupRoot"
    } else {
        Write-Host "browser-mcp-server is already absent from $ServerRoot."
    }

    if ($LockStream) {
        $LockStream.Dispose()
        $LockStream = $null
        if (Test-Path -LiteralPath $LockPath -PathType Leaf) {
            Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
        }
    }
} finally {
    if ($LockStream) {
        try { $LockStream.Dispose() } catch { }
    }
}
