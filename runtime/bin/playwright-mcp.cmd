@echo off
setlocal EnableExtensions DisableDelayedExpansion

for %%I in ("%~dp0..") do set "RUNTIME_ROOT=%%~fI"
set "NODE_EXECUTABLE="

if exist "%RUNTIME_ROOT%\node\node.exe" set "NODE_EXECUTABLE=%RUNTIME_ROOT%\node\node.exe"
if not defined NODE_EXECUTABLE if defined BROWSER_AGENT_NODE if exist "%BROWSER_AGENT_NODE%" set "NODE_EXECUTABLE=%BROWSER_AGENT_NODE%"
if not defined NODE_EXECUTABLE for %%I in (node.exe) do set "NODE_EXECUTABLE=%%~$PATH:I"

if not defined NODE_EXECUTABLE (
  echo Node.js was not found; install Node.js 20.19+ or set BROWSER_AGENT_NODE 1>&2
  exit /b 127
)

"%NODE_EXECUTABLE%" "%RUNTIME_ROOT%\node_modules\@playwright\mcp\cli.js" %*
exit /b %ERRORLEVEL%
