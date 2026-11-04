@echo off
title Starting Comam AI...
echo ====================================================
echo             Starting Comam AI System...
echo ====================================================
echo.

rem Start backend on port 8000
echo [1/2] Starting Python FastAPI Backend...
start "Comam Backend (Port 8000)" cmd /k "cd /d "%~dp0backend" && .venv\Scripts\uvicorn app.main:app --port 8000 --reload"

rem Start frontend on port 5173
echo [2/2] Starting React Vite Frontend...
start "Comam Frontend (Port 5173)" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo.
echo Waiting 3 seconds for servers to initialize...
timeout /t 3 /nobreak >nul

echo Opening browser at http://localhost:5173 ...
start http://localhost:5173

echo.
echo ====================================================
echo Comam is running! You can now speak in Bengali.
echo To stop, close the two terminal windows.
echo ====================================================
pause
