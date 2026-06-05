@echo off
REM ============================================================
REM   CiteThreads frontend dev launcher (wrapper)
REM
REM   Started by start-dev.bat. The parent sets CT_FRONTEND_PORT
REM   in the environment before launching this script.
REM
REM   See the comment in run-backend.bat for why this lives in
REM   a separate file rather than inline in start-dev.bat.
REM ============================================================

setlocal

cd /d "%~dp0\..\frontend"

if not defined CT_FRONTEND_PORT set "CT_FRONTEND_PORT=5173"

if not exist "node_modules" (
    echo [WARN] node_modules missing. Run 'npm install' in frontend\ first.
    pause
    exit /b 1
)

echo Starting vite on port %CT_FRONTEND_PORT% ...
call npm run dev
pause
