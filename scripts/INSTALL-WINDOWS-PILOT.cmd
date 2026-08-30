@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
for %%I in ("%~dp0.") do set "TRANSFER_ROOT=%%~fI"

if not exist "%~dp0INSTALL-WINDOWS-PILOT.ps1" (
  echo INSTALL-WINDOWS-PILOT.ps1 was not found beside this launcher.
  pause
  exit /b 2
)
if not exist "%~dp0toolkit\scripts\verify-windows-release.ps1" (
  echo The automated Windows release gate was not found in this transfer package.
  pause
  exit /b 2
)

echo Running automated Windows checks. No installation changes have started...
set "GATE_LOG=%TEMP%\IntranetBrowserAgent\WINDOWS-RELEASE-GATE-%RANDOM%-%RANDOM%.log"
powershell.exe -NoProfile -File "%~dp0toolkit\scripts\verify-windows-release.ps1" -TransferPath "%TRANSFER_ROOT%" -LogPath "%GATE_LOG%"
set "GATE_EXIT=%ERRORLEVEL%"
if not "%GATE_EXIT%"=="0" goto gate_failed

echo.
echo Automated Windows checks passed. Continuing in the same window...

set "INSTALL_LOG=%TEMP%\IntranetBrowserAgent\INSTALL-WINDOWS-PILOT-%RANDOM%-%RANDOM%.log"
powershell.exe -NoProfile -File "%~dp0INSTALL-WINDOWS-PILOT.ps1" -LogPath "%INSTALL_LOG%"
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
if exist "%GATE_LOG%" echo Gate log: %GATE_LOG%
if exist "%INSTALL_LOG%" echo Install log: %INSTALL_LOG%
pause
exit /b %INSTALL_EXIT%

:gate_failed
echo.
echo Automated Windows checks stopped with exit code %GATE_EXIT%.
echo Installation was not started and the real Claude user configuration was not changed.
echo No npm, pnpm, or npx repair command should be run on the intranet host.
if exist "%GATE_LOG%" (
  echo.
  echo Detailed gate error log:
  powershell.exe -NoProfile -Command "Get-Content -LiteralPath $env:GATE_LOG -Tail 120"
  echo Gate log: %GATE_LOG%
)
pause
exit /b %GATE_EXIT%
