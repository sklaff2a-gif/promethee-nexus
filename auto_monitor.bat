@echo off
REM ============================================================
REM  PROMETHEE Auto-Monitor — Cycle autonome toutes les 4 heures
REM  Lancé par le Planificateur de tâches Windows
REM ============================================================

setlocal
set PYTHONIOENCODING=utf-8
set CLAUDECODE=
set PROJECT=C:\MesProjets\PROMETHEE_V11_restructuration2026

REM Créer le dossier de rapports s'il n'existe pas
if not exist "%PROJECT%\logs\autonomous_reports" mkdir "%PROJECT%\logs\autonomous_reports"

REM Horodatage locale-indépendant via PowerShell
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm"') do set STAMP=%%i
set LOGFILE=%PROJECT%\logs\autonomous_reports\%STAMP%.log

echo [%date% %time%] Démarrage cycle autonome PROMETHEE >> "%LOGFILE%"

REM Lancer Claude Code en mode headless avec le prompt du fichier auto_monitor.md
cd /d "%PROJECT%"
claude -p "Lis le fichier auto_monitor.md et exécute le protocole décrit. Sois concis." ^
  --allowedTools "Bash(PYTHONIOENCODING=utf-8 python:*)" "Bash(python:*)" "Bash(git:*)" "Bash(cp:*)" "Bash(mkdir:*)" "Bash(ls:*)" "Read" "Edit" "Glob" "Grep" ^
  >> "%LOGFILE%" 2>&1

echo [%date% %time%] Cycle terminé >> "%LOGFILE%"
endlocal
