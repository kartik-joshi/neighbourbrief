#!/bin/zsh
# One-command setup on a Mac (Python packages, Ollama models, stylesheet).
#   zsh scripts/setup.sh
set -e
cd "$(dirname "$0")/.."
echo "== NeighbourBrief TO setup in $(pwd)"

if ! command -v python3 >/dev/null || ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "!! Python 3.11+ is needed. Install Python 3.12 from https://www.python.org/downloads/ and run this again."; exit 1
fi
[ -d .venv ] || python3 -m venv .venv
echo "== Installing Python packages (1-2 minutes)…"
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "!! No .env found. Created one from .env.example: add your own keys to $(pwd)/.env"
fi

RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
echo "== This Mac has ${RAM_GB} GB of memory."
if command -v ollama >/dev/null; then
  if ollama list 2>/dev/null | grep -q "gemma3:12b"; then
    echo "== AI model gemma3:12b is already here."
  elif [ "$RAM_GB" -ge 16 ]; then
    echo "== Downloading the AI model gemma3:12b (8 GB; do this on good Wi-Fi)…"
    ollama pull gemma3:12b || echo "!! Model download failed: drafts will use the quick template writer."
  else
    echo "!! Only ${RAM_GB} GB of memory: skipping the 12B model. Drafts will use the quick template writer;"
    echo "   translation still uses Azure when online."
  fi
  if ollama list 2>/dev/null | grep -q "bge-m3"; then
    echo "== Meaning-score model bge-m3 is already here."
  else
    echo "== Downloading bge-m3 (1.2 GB), which checks each translated line means the same as the English…"
    ollama pull bge-m3 || echo "!! bge-m3 download failed: the meaning score is skipped (the other translation checks still run)."
  fi
else
  echo "!! Ollama isn't installed: drafts will use the quick template writer."
  echo "   For AI drafts, install Ollama from https://ollama.com and run this script again."
fi

echo
echo "== Done. Start the app with:"
echo "   cd \"$(pwd)\" && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo "   then open http://127.0.0.1:8000/status"
