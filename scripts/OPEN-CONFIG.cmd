@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SERVER_ROOT=%USERPROFILE%\browser-mcp-server"
set "SETTINGS=%SERVER_ROOT%\config\settings.json"
if not exist "%SETTINGS%" set "SETTINGS=%~dp0config\settings.example.json"
if not exist "%SETTINGS%" (
  echo browser-mcp-server settings file was not found.
  pause
  exit /b 1
)
start "browser-mcp-server settings" notepad.exe "%SETTINGS%"
exit /b 0
