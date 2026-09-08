@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "SERVER_ROOT=%USERPROFILE%\browser-mcp-server"
set "CURRENT_FILE=%SERVER_ROOT%\maintenance\current-version.txt"
if exist "%CURRENT_FILE%" (
  set /p SETTINGS_VERSION=<"%CURRENT_FILE%"
  if defined SETTINGS_VERSION (
    set "SETTINGS_SCRIPT=!SERVER_ROOT!\maintenance\!SETTINGS_VERSION!\scripts\BROWSER-AGENT-SETTINGS.ps1"
    if exist "!SETTINGS_SCRIPT!" (
      powershell.exe -NoProfile -File "!SETTINGS_SCRIPT!" %*
      exit /b !ERRORLEVEL!
    )
  )
)

echo browser-mcp-server is not installed for this Windows user.
echo Run INSTALL.cmd once, then use CONFIGURE.cmd to change settings.
pause
exit /b 1
