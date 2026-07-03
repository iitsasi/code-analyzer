@echo off
echo.
echo ============================================================
echo   Codebase Analyzer - Setup
echo ============================================================

python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python 3.10+ required. Install from https://python.org
    pause
    exit /b 1
)

if not exist ".venv" (
    python -m venv .venv
    echo Created .venv\
)
call .venv\Scripts\activate.bat >nul 2>&1
echo Activated .venv\

python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
echo Installed dependencies

python -c "import tree_sitter_java, tree_sitter_python, tree_sitter_go, tree_sitter_javascript, tree_sitter_typescript; print('All 5 language parsers verified')"

echo.
echo Running tests...
set PYTHONPATH=.
python -m pytest tests\ -v --tb=short

echo.
echo ============================================================
echo   Done! To run:
echo.
echo     copy .env.example .env
echo     notepad .env                       REM set CODEBASE_PATH
echo     python run_analysis.py
echo.
echo   Config: config/default.yaml (static) + .env (per-environment)
echo ============================================================
pause
