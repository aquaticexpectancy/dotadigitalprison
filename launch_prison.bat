@echo off
setlocal EnableDelayedExpansion
set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"

set "NGROK_DOMAIN="
if exist "%PROJECT%\.ngrok_domain" (
  for /f "usebackq tokens=* delims=" %%D in ("%PROJECT%\.ngrok_domain") do set "NGROK_DOMAIN=%%D"
)

if not defined NGROK_DOMAIN (
  echo ERROR: Create .ngrok_domain with your ngrok subdomain, one line, e.g.:
  echo   triage-buffer-buffing.ngrok-free.dev
  pause
  exit /b 1
)

echo ngrok domain: !NGROK_DOMAIN!
set "NGROK_CMD=ngrok http 5000 --url=!NGROK_DOMAIN!"

start "Dota Prison - Watcher" cmd /k "cd /d %PROJECT% && python watcher.py"
timeout /t 2 /nobreak >nul
start "Dota Prison - MCP Server" cmd /k "cd /d %PROJECT% && python mcp_server.py"
timeout /t 2 /nobreak >nul
start "Dota Prison - ngrok" cmd /k "!NGROK_CMD!"
