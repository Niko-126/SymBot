@echo off
rem ai-life test build launcher (ASCII only on purpose)
cd /d "%~dp0"
if not exist "%~dp0ai-life\ai-life.exe" (
  echo [ERROR] ai-life\ai-life.exe not found. Unzip the whole package first.
  pause
  exit /b 1
)
start "" "%~dp0ai-life\ai-life.exe"
exit /b 0
