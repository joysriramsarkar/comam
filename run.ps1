# Start Comam Backend and Frontend in separate windows
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "         Starting Comam AI...             " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$root = $PSScriptRoot

# 1. Start Backend
Write-Host "[1/2] Starting Backend on http://localhost:8000 ..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .venv\Scripts\uvicorn app.main:app --port 8000 --reload"

# 2. Start Frontend
Write-Host "[2/2] Starting Frontend on http://localhost:5173 ..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; npm run dev"

Start-Sleep -Seconds 2

# 3. Open Browser
Start-Process "http://localhost:5173"

Write-Host "`nAll set! Browser opened at http://localhost:5173" -ForegroundColor Yellow
