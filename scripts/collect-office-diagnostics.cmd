@echo off
setlocal
set "agentExe=%LOCALAPPDATA%\Programs\RequirementsAgent\RequirementsAgent.exe"
if not "%~1"=="" set "agentExe=%~1"
if not exist "%agentExe%" (
  echo Requirements Agent was not found. Drag its installed EXE onto this script.
  pause
  exit /b 2
)
start "" /wait "%agentExe%" --office-diagnostics --output "%~dp0Office-registration.json"
if errorlevel 1 (
  echo Diagnostic failed. Requires Requirements Agent 1.2.12 or newer.
  pause
  exit /b 1
)
echo Diagnostic saved beside this script: Office-registration.json
echo No documents opened. No registry or security settings changed.
pause
