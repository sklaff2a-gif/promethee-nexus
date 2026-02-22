@echo off
REM ============================================================
REM run_tests.bat — Lance les tests sur la copie
REM Usage : run_tests.bat           (tous les tests)
REM         run_tests.bat council    (un seul fichier)
REM         run_tests.bat -k "test_consensus"  (par nom)
REM ============================================================

set DST=C:\Users\redla\projetclaude\PROMETHEE_V11_restructuration2026
set PYTHONIOENCODING=utf-8

cd /d "%DST%"

if "%~1"=="" (
    python -m pytest tests/ --tb=short -q
) else if "%~1"=="-k" (
    python -m pytest tests/ --tb=short -q -k "%~2"
) else (
    python -m pytest tests/test_%~1.py --tb=short -v
)

pause
