@echo off
REM ============================================================
REM  PROMETHEE Agent OBSERVATEUR - MODE QUARANTAINE (lecture seule)
REM  Rearme le 30/05/2026 apres 46j d'extinction.
REM  Outillage rabote : ni git, ni Edit, ni powershell-agent, ni cp.
REM  Cycle toutes les 4h via le Planificateur de taches Windows.
REM ============================================================

setlocal EnableDelayedExpansion
set PYTHONIOENCODING=utf-8
set CLAUDECODE=
set PROJECT=C:\MesProjets\PROMETHEE_V11_restructuration2026
set WORKDIR=C:\Users\redla\projetclaude
set CLAUDE=C:\Users\redla\.local\bin\claude.exe

if not exist "%PROJECT%\logs\autonomous_reports" mkdir "%PROJECT%\logs\autonomous_reports"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm"') do set STAMP=%%i
set LOGFILE=%PROJECT%\logs\autonomous_reports\%STAMP%.log
set LOCKFILE=%PROJECT%\logs\auto_monitor.lock

if exist "%LOCKFILE%" (
    for /f %%a in ('powershell -NoProfile -Command "if (Test-Path '%LOCKFILE%') { $age = (Get-Date) - (Get-Item '%LOCKFILE%').LastWriteTime; if ($age.TotalHours -gt 2) { 'stale' } else { 'active' } } else { 'absent' }"') do set LOCKSTATE=%%a
    if "!LOCKSTATE!"=="active" (
        echo [%date% %time%] Cycle precedent encore en cours, abandon >> "%LOGFILE%"
        goto :end
    )
    echo [%date% %time%] Verrou orphelin detecte, nettoyage >> "%LOGFILE%"
    del "%LOCKFILE%" 2>nul
)
echo %date% %time% > "%LOCKFILE%"
echo [%date% %time%] Demarrage agent OBSERVATEUR (quarantaine) >> "%LOGFILE%"

cd /d "%WORKDIR%"
REM --model FIGE explicitement (14/06) : ne plus dependre du defaut de session du
REM CLI. Fable 5 a ete retire par Anthropic -> les cycles 00h/04h/08h du 14/06 ont
REM echoue ("Fable 5 is currently unavailable"). Opus 4.8 = modele stable de l'observateur.
"%CLAUDE%" -p "Lis le fichier PROMETHEE_V11_restructuration2026\auto_session_protocol.md et execute le protocole complet. Tu es en MODE QUARANTAINE LECTURE SEULE : aucune correction, aucun git, aucun redemarrage." ^
  --model claude-opus-4-8 ^
  --add-dir "C:\MesProjets\PROMETHEE_V11_restructuration2026" ^
  --allowedTools "Bash(PYTHONIOENCODING=utf-8 python:*)" "Bash(python:*)" "Bash(cd:*)" "Bash(tail:*)" "Read" "Write" "Glob" "Grep" ^
  >> "%LOGFILE%" 2>&1

echo [%date% %time%] Cycle termine >> "%LOGFILE%"
del "%LOCKFILE%" 2>nul
:end
endlocal
