@echo off
REM ============================================================
REM update_copy.bat — Synchronise le source vers la copie de test
REM Usage : double-clic ou "update_copy.bat" depuis le dossier projet
REM ============================================================

set SRC=C:\MesProjets\PROMETHEE_V11_restructuration2026
set DST=C:\Users\redla\projetclaude\PROMETHEE_V11_restructuration2026

echo === Synchronisation %SRC% → %DST% ===

robocopy "%SRC%" "%DST%" /MIR /XD .git memory logs __pycache__ .pytest_cache chroma_db /XF *.pyc .env evolution_catalog.json /NFL /NDL /NP

if %ERRORLEVEL% LEQ 3 (
    echo.
    echo === Copie OK. Lancement des tests... ===
    cd /d "%DST%"
    set PYTHONIOENCODING=utf-8
    python -m pytest tests/ --tb=short -q
) else (
    echo.
    echo === ERREUR robocopy (code %ERRORLEVEL%) ===
)

pause
