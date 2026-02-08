@echo off
chcp 65001 >nul
title PROMETHEE - Nexus V11

echo ============================================
echo        PROMETHEE - Systeme Multi-Agents
echo ============================================
echo.
echo  [1] Production   (Guardian + crash-recovery)
echo  [2] Developpement (auto-restart sur exit 65)
echo  [3] Serveur seul  (FastAPI sur 127.0.0.1:8000)
echo  [4] Quitter
echo.
set /p choix="Choix : "

if "%choix%"=="1" goto production
if "%choix%"=="2" goto dev
if "%choix%"=="3" goto serveur
if "%choix%"=="4" exit

echo Choix invalide.
pause
exit

:production
echo.
echo Lancement en mode Production (Guardian)...
cd /d "%~dp0"
python guardian.py
pause
exit

:dev
echo.
echo Lancement en mode Developpement (Nexus)...
cd /d "%~dp0"
python start_nexus.py
pause
exit

:serveur
echo.
echo Lancement du serveur FastAPI...
cd /d "%~dp0"
python main.py
pause
exit
