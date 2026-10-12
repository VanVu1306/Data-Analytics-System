#!/usr/bin/env bash
# scripts/setup_ollama.sh
# Setup Ollama cho NL2Viz (Linux/Mac)

set -e

echo "================================================"
echo " NL2Viz — Ollama Setup (Linux/Mac)             "
echo "================================================"

# 1. Check/install Ollama
echo ""
echo "[1/4] Checking Ollama installation..."
if ! command -v ollama &> /dev/null; then
    echo "📥 Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi
echo "✅ Ollama installed"

# 2. Pull llama3.2
echo ""
echo "[2/4] Pulling llama3.2 model (~2GB)..."
ollama pull llama3.2
echo "✅ Model llama3.2 ready"

# 3. List models
echo ""
echo "[3/4] Local models:"
ollama list

# 4. Start server in background
echo ""
echo "[4/4] Starting Ollama server in background..."
nohup ollama serve > /tmp/ollama.log 2>&1 &
sleep 2
echo "✅ Server started (logs: /tmp/ollama.log)"

echo ""
echo "================================================"
echo " ✅ SETUP DONE!                                 "
echo "================================================"
echo ""
echo "Next steps:"
echo "  1. Verify: curl http://localhost:11434/api/tags"
echo "  2. Set API key: export DEEPSEEK_API_KEY='sk-...'  (hoặc ghi vào file .env)"
echo "  3. Create DB: python scripts/create_demo_db.py"
echo "  4. Run backend:  uvicorn backend.main:app --host 127.0.0.1 --port 8000"
echo "  5. Run frontend: streamlit run frontend/app.py"
