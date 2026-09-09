@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

if not exist "%~dp0payload\scripts\UNINSTALL.ps1" (
  echo browser-mcp-server package is incomplete: uninstall helper was not found.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0payload\scripts\UNINSTALL.ps1"
set "UNINSTALL_EXIT=%ERRORLEVEL%"
if not "%UNINSTALL_EXIT%"=="0" pause
exit /b %UNINSTALL_EXIT%
