@echo off
REM ============================================================
REM   CiteThreads backend dev launcher (wrapper)
REM
REM   Started by start-dev.bat. The parent sets CT_BACKEND_PORT
REM   in the environment before launching this script.
REM ============================================================

setlocal

cd /d "%~dp0\..\backend"

if not defined CT_BACKEND_PORT set "CT_BACKEND_PORT=8000"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] backend venv not found: %CD%\.venv\Scripts\python.exe
    echo Run scripts\init.ps1 first.
    pause
    exit /b 1
)

set "PYTHONUNBUFFERED=1"
echo Starting uvicorn on port %CT_BACKEND_PORT% ...
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port %CT_BACKEND_PORT% --reload
pause
