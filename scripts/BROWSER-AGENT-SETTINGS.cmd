@echo off
setlocal

set "AGENT_ROOT=%LOCALAPPDATA%\IntranetBrowserAgent"
set "CURRENT_FILE=%AGENT_ROOT%\maintenance\current-version.txt"

if not exist "%CURRENT_FILE%" (
  echo Browser Agent settings are not installed. Run INSTALL-WINDOWS-PILOT.cmd first.
  pause
  exit /b 1
)

set /p SETTINGS_VERSION=<"%CURRENT_FILE%"
if not defined SETTINGS_VERSION (
  echo Browser Agent settings version is missing.
  pause
  exit /b 1
)

set "SETTINGS_SCRIPT=%AGENT_ROOT%\maintenance\%SETTINGS_VERSION%\scripts\BROWSER-AGENT-SETTINGS.ps1"
if not exist "%SETTINGS_SCRIPT%" (
  echo Browser Agent settings are incomplete: %SETTINGS_SCRIPT%
  pause
  exit /b 1
)

powershell.exe -NoProfile -File "%SETTINGS_SCRIPT%" %*
set "SETTINGS_EXIT=%ERRORLEVEL%"

if not "%SETTINGS_EXIT%"=="0" (
  echo.
  echo Browser Agent settings stopped with exit code %SETTINGS_EXIT%.
)

pause
exit /b %SETTINGS_EXIT%
