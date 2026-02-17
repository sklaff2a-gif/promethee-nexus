@echo off
REM ============================================================
REM  PROMETHEE Auto-Monitor — Cycle autonome toutes les 4 heures
REM  Lancé par le Planificateur de tâches Windows
REM ============================================================

setlocal
set PYTHONIOENCODING=utf-8
set PROJECT=C:\MesProjets\PROMETHEE_V11_restructuration2026

REM Créer le dossier de rapports s'il n'existe pas
if not exist "%PROJECT%\logs\autonomous_reports" mkdir "%PROJECT%\logs\autonomous_reports"

REM Horodatage pour le log
for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set DATESTAMP=%%c-%%b-%%a
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set TIMESTAMP=%%a-%%b
set LOGFILE=%PROJECT%\logs\autonomous_reports\%DATESTAMP%_%TIMESTAMP%.log

echo [%date% %time%] Démarrage cycle autonome PROMETHEE >> "%LOGFILE%"

REM Lancer Claude Code en mode headless avec le prompt du fichier auto_monitor.md
cd /d "%PROJECT%"
claude -p "Lis le fichier auto_monitor.md et exécute le protocole décrit. Sois concis." ^
  --allowedTools "Bash(PYTHONIOENCODING=utf-8 python:*)" "Bash(python:*)" "Bash(git:*)" "Bash(cp:*)" "Bash(mkdir:*)" "Bash(ls:*)" "Read" "Edit" "Glob" "Grep" ^
  >> "%LOGFILE%" 2>&1

echo [%date% %time%] Cycle terminé >> "%LOGFILE%"
endlocal
