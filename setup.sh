#!/usr/bin/env bash
set -euo pipefail

echo ""
echo "============================================================"
echo "  Codebase Analyzer — Setup"
echo "============================================================"

# Check Python
python3 --version >/dev/null 2>&1 || { echo "ERROR: Python 3.10+ required"; exit 1; }
echo "✓ Python $(python3 --version 2>&1)"

# Virtual environment
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "✓ Created .venv/"
fi
source .venv/bin/activate 2>/dev/null || true
echo "✓ Activated .venv/"

# Install
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✓ Installed dependencies"

# Verify parsers
python3 -c "
import tree_sitter_java, tree_sitter_python, tree_sitter_go
import tree_sitter_javascript, tree_sitter_typescript
print('✓ All 5 language parsers verified')
"

# Run tests
echo ""
echo "Running tests..."
PYTHONPATH=. python3 -m pytest tests/ -v --tb=short

echo ""
echo "============================================================"
echo "  Done! To run:"
echo ""
echo "    cp .env.example .env"
echo "    nano .env                          # set CODEBASE_PATH"
echo "    python run_analysis.py"
echo ""
echo "  Config: config/default.yaml (static) + .env (per-environment)"
echo "============================================================"
echo ""
