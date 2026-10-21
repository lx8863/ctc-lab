@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Python environment not found: .venv\Scripts\pythonw.exe
  pause
  exit /b 1
)
start "CTC Lab" ".venv\Scripts\pythonw.exe" "desktop_app.py"
