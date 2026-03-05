@echo off
chcp 65001 >nul
title PROMETHEE - Nexus Launcher
cd /d "C:\MesProjets\PROMETHEE_V11_restructuration2026"

echo ============================================
echo   PROMETHEE - Lancement automatique
echo ============================================
echo.

:: Verifier Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERREUR: Python non trouve dans le PATH
    pause
    exit /b 1
)

:: Verifier Ollama
tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if %errorlevel% neq 0 (
    echo [!] Ollama non detecte - tentative de lancement...
    start "" "C:\Users\redla\AppData\Local\Programs\Ollama\ollama app.exe" 2>nul
    timeout /t 5 /nobreak >nul
)

set PYTHONIOENCODING=utf-8
echo [OK] Demarrage de start_nexus.py...
echo.
python start_nexus.py
