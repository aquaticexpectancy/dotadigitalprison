@echo off
setlocal EnableDelayedExpansion
echo Stopping Dota Digital Prison processes...

call :kill_port 5000 "MCP server"
call :kill_port 3000 "GSI watcher"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":4040" ^| findstr "LISTENING"') do (
  echo   ngrok agent PID %%P
  taskkill /PID %%P /F >nul 2>&1
)

taskkill /IM ngrok.exe /F >nul 2>&1

echo Done.
exit /b 0

:kill_port
set "PORT=%~1"
set "LABEL=%~2"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr "127.0.0.1:!PORT!" ^| findstr "LISTENING"') do (
  echo   !LABEL! on :!PORT! PID %%P
  taskkill /PID %%P /F >nul 2>&1
)
exit /b 0
