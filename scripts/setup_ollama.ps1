# scripts/setup_ollama.ps1
# Setup Ollama cho NL2Viz (Windows PowerShell)

Write-Host "================================================" -ForegroundColor Cyan
Write-Host " NL2Viz — Ollama Setup (Windows)               " -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

# 1. Check Ollama installed
Write-Host "`n[1/4] Checking Ollama installation..." -ForegroundColor Yellow
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host "❌ Ollama chưa cài. Tải từ: https://ollama.com/download" -ForegroundColor Red
    Write-Host "   Sau khi cài xong, chạy lại script này." -ForegroundColor Red
    exit 1
}
Write-Host "✅ Ollama installed" -ForegroundColor Green

# 2. Pull llama3.2 model
Write-Host "`n[2/4] Pulling llama3.2 model (~2GB)..." -ForegroundColor Yellow
ollama pull llama3.2
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Lỗi khi pull model" -ForegroundColor Red
    exit 1
}
Write-Host "✅ Model llama3.2 ready" -ForegroundColor Green

# 3. List models
Write-Host "`n[3/4] Local models:" -ForegroundColor Yellow
ollama list

# 4. Start server (in new window)
Write-Host "`n[4/4] Starting Ollama server in new window..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "ollama serve"

Write-Host "`n================================================" -ForegroundColor Cyan
Write-Host " ✅ SETUP DONE!                                 " -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "`nNext steps:"
Write-Host "  1. Verify server: curl http://localhost:11434/api/tags"
Write-Host "  2. Set DEEPSEEK_API_KEY: `$env:DEEPSEEK_API_KEY = 'sk-...'  (hoặc ghi vào file .env)"
Write-Host "  3. Create DB: python scripts/create_demo_db.py"
Write-Host "  4. Run backend:  uvicorn backend.main:app --host 127.0.0.1 --port 8000"
Write-Host "  5. Run frontend: streamlit run frontend/app.py"
