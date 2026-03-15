@echo off
REM ============================================================
REM  PROMETHEE Agent Correcteur Autonome — Cycle toutes les 4h
REM  Lance par le Planificateur de taches Windows
REM ============================================================

setlocal EnableDelayedExpansion
set PYTHONIOENCODING=utf-8
set CLAUDECODE=
set PROJECT=C:\MesProjets\PROMETHEE_V11_restructuration2026
set WORKDIR=C:\Users\redla\projetclaude

REM Creer le dossier de rapports s'il n'existe pas
if not exist "%PROJECT%\logs\autonomous_reports" mkdir "%PROJECT%\logs\autonomous_reports"

REM Horodatage locale-independant via PowerShell
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm"') do set STAMP=%%i
set LOGFILE=%PROJECT%\logs\autonomous_reports\%STAMP%.log

REM --- Verrou anti-chevauchement ---
set LOCKFILE=%PROJECT%\logs\auto_monitor.lock

REM Verifier si un cycle precedent tourne encore
if exist "%LOCKFILE%" (
    for /f %%a in ('powershell -NoProfile -Command "if (Test-Path '%LOCKFILE%') { $age = (Get-Date) - (Get-Item '%LOCKFILE%').LastWriteTime; if ($age.TotalHours -gt 2) { 'stale' } else { 'active' } } else { 'absent' }"') do set LOCKSTATE=%%a
    if "!LOCKSTATE!"=="active" (
        echo [%date% %time%] Cycle precedent encore en cours, abandon >> "%LOGFILE%"
        goto :end
    )
    echo [%date% %time%] Verrou orphelin detecte, nettoyage >> "%LOGFILE%"
    del "%LOCKFILE%" 2>nul
)

REM Poser le verrou
echo %date% %time% > "%LOCKFILE%"

echo [%date% %time%] Demarrage agent correcteur PROMETHEE >> "%LOGFILE%"

REM Lancer Claude Code — lit le protocole depuis un fichier
cd /d "%WORKDIR%"
claude -p "Lis le fichier PROMETHEE_V11_restructuration2026\auto_session_protocol.md et execute le protocole complet." ^
  --allowedTools "Bash(PYTHONIOENCODING=utf-8 python:*)" "Bash(python:*)" "Bash(git:*)" "Bash(cp:*)" "Bash(mkdir:*)" "Bash(ls:*)" "Bash(tail:*)" "Bash(sleep:*)" "Bash(powershell.exe:*)" "Bash(powershell:*)" "Bash(nvidia-smi:*)" "Read" "Write" "Edit" "Glob" "Grep" ^
  >> "%LOGFILE%" 2>&1

echo [%date% %time%] Cycle termine >> "%LOGFILE%"

REM Liberer le verrou
del "%LOCKFILE%" 2>nul

:end
endlocal
