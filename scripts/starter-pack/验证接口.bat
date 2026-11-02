@echo off
rem ai-life API self-check wrapper (ASCII only on purpose)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify-api.ps1"
echo.
pause
