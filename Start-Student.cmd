@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Follow README setup instructions first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m agent.client
if errorlevel 1 pause
