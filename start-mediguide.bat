@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python virtual environment not found.
  echo Create it with: py -m venv .venv
  pause
  exit /b 1
)

if not exist ".venv\Scripts\uvicorn.exe" (
  echo Installing backend dependencies...
  call ".venv\Scripts\python.exe" -m pip install -r python_backend\requirements.txt
)

rem Load .env (lines of NAME=VALUE, # comments ignored)
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do set "%%A=%%B"
)

set "PYTHONPATH=%~dp0"

rem Bring the database schema up to date before serving. Safe to run every time:
rem Alembic applies only the migrations that have not run yet.
echo Applying database migrations...
call ".venv\Scripts\python.exe" -m alembic upgrade head || (
  echo Database migration failed. The application was not started.
  pause
  exit /b 1
)

rem Free the port if a previous run is still holding it.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8011" ^| findstr "LISTENING"') do taskkill /PID %%P /F >nul 2>&1

start "MediGuide" /D "%~dp0" cmd /k ".venv\Scripts\python.exe -m uvicorn python_backend.app.main:app --host 127.0.0.1 --port 8011"
timeout /t 4 /nobreak >nul
start "" "http://localhost:8011/ui"
endlocal
