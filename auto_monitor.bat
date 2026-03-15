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

REM Lancer Claude Code en mode headless — protocole inline
cd /d "%WORKDIR%"
claude -p "Tu es l'agent correcteur autonome de Promethee. Execute ce protocole complet en mode CLAUDE (sans intervention humaine). Sois concis et methodique. PHASE A - ETAT DES LIEUX : 1) Lance analyze_run.py sur les logs du jour (cd C:\MesProjets\PROMETHEE_V11_restructuration2026 && PYTHONIOENCODING=utf-8 python analyze_run.py logs/ --date today). Si vide essaie 'log run copie/'. Lis le rapport. 2) Verifie les processus Promethee (powershell Get-CimInstance Win32_Process filtrer python.exe guardian/start_nexus/main). 3) Verifie le GPU (nvidia-smi). 4) Lis cardiac_state.json, desire_state.json, prefrontal_state.json, daily_budget.json dans C:\MesProjets\PROMETHEE_V11_restructuration2026\memory\. 5) Synthetise un bilan. PHASE B - CORRECTIONS : Si probleme CRITIQUE detecte : inspecte le module (grep imports, bus events, tests), propose 3 approches, choisis la plus simple, applique (max 3 fichiers, 30 lignes). Si probleme MODERE avec correction claire et moins de 15 lignes, corrige. Sinon note dans le rapport sans corriger. PHASE C - AMELIORATION : Si une amelioration HAUT impact est identifiee et faisable en moins de 30 lignes et 1 fichier, implemente-la. Max 1 par session. PHASE D - DEPLOIEMENT : Si des corrections ont ete faites : lance les tests (PYTHONIOENCODING=utf-8 python -m pytest tests/ -x --tb=short -q). Si tests PASSENT : git add -A, git commit -m 'fix(auto): description', git push origin master. Sync vers copie (powershell Copy-Item). Kill Promethee (JAMAIS Stop-Process python* — cibler uniquement guardian/start_nexus/main via Get-CimInstance, exclure claude/auto_monitor/pytest/telegram), sleep 5, restart guardian.py via Start-Process. Si tests ECHOUENT : git checkout -- . et noter dans rapport. PHASE E - RAPPORT : Ecris un rapport dans logs/autonomous_reports/ avec : duree run, routines, qualite, diagnostic, corrections, tests, redemarrage, recommandations. REGLES ABSOLUES : Fichiers proteges JAMAIS modifies (main.py, config.py, guardian.py, start_nexus.py, .env). Max 5 fichiers et 100 lignes par session. Rollback si tests echouent. Ne pas toucher veto prefrontal, dream consolidation, dopamine dip." ^
  --allowedTools "Bash(PYTHONIOENCODING=utf-8 python:*)" "Bash(python:*)" "Bash(git:*)" "Bash(cp:*)" "Bash(mkdir:*)" "Bash(ls:*)" "Bash(tail:*)" "Bash(sleep:*)" "Bash(powershell.exe:*)" "Bash(powershell:*)" "Bash(nvidia-smi:*)" "Read" "Write" "Edit" "Glob" "Grep" ^
  >> "%LOGFILE%" 2>&1

echo [%date% %time%] Cycle termine >> "%LOGFILE%"

REM Liberer le verrou
del "%LOCKFILE%" 2>nul

:end
endlocal
