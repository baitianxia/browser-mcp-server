@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

if not exist "%~dp0payload\INSTALL-WINDOWS-PILOT.cmd" (
  echo browser-mcp-server package is incomplete: payload\INSTALL-WINDOWS-PILOT.cmd was not found.
  pause
  exit /b 2
)

call "%~dp0payload\INSTALL-WINDOWS-PILOT.cmd" %*
set "INSTALL_EXIT=%ERRORLEVEL%"
if "%INSTALL_EXIT%"=="0" (
  echo.
  echo browser-mcp-server installation or upgrade completed.
) else (
  echo.
  echo browser-mcp-server installation or upgrade stopped with exit code %INSTALL_EXIT%.
)
exit /b %INSTALL_EXIT%
