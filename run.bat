@echo off
REM PM Assistant launcher.
REM Opens two windows: backend (uvicorn) and frontend (vite).
REM
REM Override port if 8000 is stuck:  run.bat 8001
REM Default backend port: 8000

setlocal
set "BACKEND_PORT=%~1"
if "%BACKEND_PORT%"=="" set "BACKEND_PORT=8000"

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"

echo [run.bat] backend port: %BACKEND_PORT%
echo [run.bat] backend dir : %BACKEND_DIR%
echo [run.bat] frontend dir: %FRONTEND_DIR%
echo.

start "PM backend :%BACKEND_PORT%" cmd /k "cd /d ""%BACKEND_DIR%"" && uv run uvicorn main:app --reload --port %BACKEND_PORT%"

REM Pass BACKEND_PORT into vite via env so the proxy targets the right port.
start "PM frontend :5173" cmd /k "cd /d ""%FRONTEND_DIR%"" && set BACKEND_PORT=%BACKEND_PORT% && pnpm dev"

echo Backend on http://localhost:%BACKEND_PORT%
echo Frontend on http://localhost:5173
endlocal
