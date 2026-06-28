@echo off
setlocal
set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"

REM Optional: set NGROK_DOMAIN=your-subdomain.ngrok-free.dev for a fixed tunnel URL.
if defined NGROK_DOMAIN (
  set "NGROK_CMD=ngrok http 5000 --url=%NGROK_DOMAIN%"
) else (
  set "NGROK_CMD=ngrok http 5000"
)

start "Dota Prison - Watcher" cmd /k "cd /d %PROJECT% && python watcher.py"
start "Dota Prison - ngrok" cmd /k "%NGROK_CMD%"
start "Dota Prison - MCP Server" cmd /k "cd /d %PROJECT% && python mcp_server.py"
