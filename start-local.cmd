@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install.ps1"
  if errorlevel 1 goto end
)
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start.ps1" -NoAuth -OpenBrowser
:end
pause
