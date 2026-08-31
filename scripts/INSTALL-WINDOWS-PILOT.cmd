@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

if not exist "%~dp0INSTALL-WINDOWS-PILOT.ps1" (
  echo INSTALL-WINDOWS-PILOT.ps1 was not found beside this launcher.
  pause
  exit /b 2
)
set "INSTALL_LOG=%TEMP%\IntranetBrowserAgent\INSTALL-WINDOWS-PILOT-%RANDOM%-%RANDOM%.log"
powershell.exe -NoProfile -File "%~dp0INSTALL-WINDOWS-PILOT.ps1" -LogPath "%INSTALL_LOG%" %*
set "INSTALL_EXIT=%ERRORLEVEL%"

if "%INSTALL_EXIT%"=="0" (
  echo.
  echo Windows pilot setup completed.
) else (
  echo.
  echo Windows pilot setup stopped with exit code %INSTALL_EXIT%.
  echo No npm, pnpm, or npx repair command should be run on the intranet host.
  if exist "%INSTALL_LOG%" (
    echo.
    echo Detailed error log:
    powershell.exe -NoProfile -Command "Get-Content -LiteralPath $env:INSTALL_LOG -Tail 80"
  )
)
if exist "%INSTALL_LOG%" echo Install log: %INSTALL_LOG%
pause
exit /b %INSTALL_EXIT%
