@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PYTHON=.venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
)

start "Language Training Agent API" cmd /k ""%PYTHON%" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload"
start "Language Training Agent UI" cmd /k "cd /d ""%~dp0frontend"" && npm run dev -- --host 127.0.0.1 --port 5173"

set "attempts=0"
:wait_for_services
curl --silent --fail http://127.0.0.1:5173/ >nul 2>&1
if errorlevel 1 goto wait_retry
curl --silent --fail http://127.0.0.1:8000/api/health >nul 2>&1
if errorlevel 1 goto wait_retry
goto services_ready

:wait_retry
set /a attempts+=1
if %attempts% GEQ 30 goto services_ready
timeout /t 1 /nobreak >nul
goto wait_for_services

:services_ready
start "" http://127.0.0.1:5173
