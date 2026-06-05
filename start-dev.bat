@echo off
setlocal

REM ============================================================
REM   CiteThreads - Dev Launcher
REM
REM   Double-click this file or run from terminal:
REM       start-dev.bat
REM
REM   Opens two new windows:
REM     1. Backend (FastAPI)  on http://localhost:8000
REM     2. Frontend (Vite)    on http://localhost:5173
REM
REM   First-time setup: run  scripts\init.ps1 install  in PowerShell
REM ============================================================

echo.
echo   ==========================================
echo     CiteThreads - Dev Launcher
echo   ==========================================
echo.

REM -- Detect project root ------------------------------------
set "CT_ROOT=%~dp0"
set "CT_SCRIPTS=%CT_ROOT%scripts"

REM -- Sanity checks ------------------------------------------
if not exist "%CT_ROOT%backend\.venv\Scripts\python.exe" (
    echo [WARN]  Backend venv not found.
    echo         Run the following in PowerShell first:
    echo           .\scripts\init.ps1 install
    echo.
    choice /c yn /m "Continue anyway? (y/n)"
    if errorlevel 2 exit /b 1
)

if not exist "%CT_ROOT%frontend\node_modules" (
    echo [WARN]  frontend\node_modules not found.
    echo         Run the following in the frontend directory:
    echo           npm install
    echo.
    choice /c yn /m "Continue anyway? (y/n)"
    if errorlevel 2 exit /b 1
)

REM -- Optional port overrides --------------------------------
if not defined CT_BACKEND_PORT  set "CT_BACKEND_PORT=8000"
if not defined CT_FRONTEND_PORT set "CT_FRONTEND_PORT=5173"

echo Backend  : http://localhost:%CT_BACKEND_PORT%  (API docs: /docs)
echo Frontend : http://localhost:%CT_FRONTEND_PORT%
echo.
echo Press Ctrl+C in each window to stop that server.
echo.

REM -- Launch -------------------------------------------------
start "CT-Backend (%CT_BACKEND_PORT%)"  "%CT_SCRIPTS%\run-backend.bat"
start "CT-Frontend (%CT_FRONTEND_PORT%)" "%CT_SCRIPTS%\run-frontend.bat"

echo Both servers launched. Check the new windows.
echo.

endlocal
