@echo off
setlocal

set "AGENT_ROOT=%USERPROFILE%\browser-mcp-server"
set "CURRENT_FILE=%AGENT_ROOT%\maintenance\current-version.txt"

if not exist "%CURRENT_FILE%" (
  echo browser-mcp-server settings are not installed. Run INSTALL.cmd first.
  pause
  exit /b 1
)

set /p SETTINGS_VERSION=<"%CURRENT_FILE%"
if not defined SETTINGS_VERSION (
  echo browser-mcp-server settings version is missing.
  pause
  exit /b 1
)

set "SETTINGS_SCRIPT=%AGENT_ROOT%\maintenance\%SETTINGS_VERSION%\scripts\BROWSER-AGENT-SETTINGS.ps1"
if not exist "%SETTINGS_SCRIPT%" (
  echo browser-mcp-server settings are incomplete: %SETTINGS_SCRIPT%
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SETTINGS_SCRIPT%" %*
set "SETTINGS_EXIT=%ERRORLEVEL%"

if not "%SETTINGS_EXIT%"=="0" (
  echo.
  echo browser-mcp-server settings stopped with exit code %SETTINGS_EXIT%.
)

pause
exit /b %SETTINGS_EXIT%
