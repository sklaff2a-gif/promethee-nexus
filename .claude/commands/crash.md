---
description: Investigation crash GPU — analyse automatique apres un freeze/ecran noir/BSOD
argument-hint:
---

# Investigation Crash GPU

Le PC a probablement crashe (ecran noir, ventilateurs a fond, hard reboot).
Lance une investigation complete et automatisee.

## 0. Lire le dossier d'investigation existant

```bash
cat "C:\Users\redla\.claude\projects\C--Users-redla-projetclaude\memory\gpu_crash_history.md"
```

Ce dossier contient l'historique, les hypotheses, les corrections deja appliquees, et les actions restantes.
**Reprendre la ou on en etait, pas repartir de zero.**

## 1. Chronologie des crashs recents (Event Viewer)

Executer ce script PowerShell pour collecter les evenements :

```bash
cat > /tmp/crash_events.ps1 << 'PSEOF'
Write-Host "=== CRASHS RECENTS (Kernel-Power 41) ==="
Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008} -MaxEvents 15 | Format-Table TimeCreated, Id, Message -Wrap

Write-Host "`n=== ERREURS NVLDDMKM ==="
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='nvlddmkm'} -MaxEvents 10 | Format-Table TimeCreated, Id, LevelDisplayName -AutoSize

Write-Host "`n=== BSOD DETAILS ==="
Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001; ProviderName='Microsoft-Windows-WER-SystemErrorReporting'} -MaxEvents 5 | Format-List TimeCreated, Message

Write-Host "`n=== MINIDUMPS ==="
Start-Process powershell -ArgumentList '-Command', 'Get-ChildItem C:\WINDOWS\Minidump\ -Filter *.dmp | Sort LastWriteTime -Desc | Select Name, LastWriteTime, Length -First 5 | Out-File C:\Users\redla\projetclaude\minidumps.txt' -Verb RunAs -Wait -ErrorAction SilentlyContinue
if (Test-Path C:\Users\redla\projetclaude\minidumps.txt) { Get-Content C:\Users\redla\projetclaude\minidumps.txt }
PSEOF
powershell.exe -ExecutionPolicy Bypass -File /tmp/crash_events.ps1
```

Comparer avec les crashs deja documentes dans le dossier. Identifier les NOUVEAUX crashs.

## 2. Etat GPU actuel

```bash
nvidia-smi --query-gpu=driver_version,temperature.gpu,power.draw,power.limit,utilization.gpu,utilization.memory,memory.used,memory.total,clocks.current.graphics,clocks.current.memory,fan.speed,pstate --format=csv
```

Verifier :
- Power limit = 250W (si 300W, le power cap est perdu → re-appliquer)
- Temperature raisonnable (< 50°C au repos)
- VRAM pas saturee

## 3. Log du moniteur GPU (boite noire)

```bash
cat "C:\Users\redla\projetclaude\gpu_monitor.log"
```

Si le fichier existe, il contient l'etat GPU seconde par seconde AVANT le crash.
Chercher :
- Les dernieres lignes avant la fin du log = moment du crash
- Les alertes (lignes avec `***`)
- Les spikes VRAM, temperature, puissance
- Les erreurs NVLDDMKM
- Les processus GPU inhabituels

Si le fichier n'existe pas ou est vide, le moniteur n'etait pas lance.

## 4. Processus GPU et overlays

```bash
cat > /tmp/gpu_procs.ps1 << 'PSEOF'
Write-Host "=== PROCESSUS NVIDIA ==="
Get-Process | Where-Object { $_.ProcessName -like '*nvidia*' -or $_.ProcessName -like '*NVIDIA*' } | Select-Object ProcessName, Id | Format-Table

Write-Host "`n=== OLLAMA ==="
Get-Process ollama* -ErrorAction SilentlyContinue | Select-Object ProcessName, Id, StartTime

Write-Host "`n=== PROMETHEE ==="
Get-Process python* -ErrorAction SilentlyContinue | Select-Object ProcessName, Id, StartTime
PSEOF
powershell.exe -ExecutionPolicy Bypass -File /tmp/gpu_procs.ps1
```

Compter les NVIDIA Overlay — plus de 2 = anormal.

## 5. Logs Promethee (si pertinent)

```bash
powershell.exe -Command "Get-Content 'C:\MesProjets\PROMETHEE_V11_restructuration2026\logs\promethee_$(Get-Date -Format yyyy-MM-dd).log' -Tail 50"
```

Seulement si Promethee tournait au moment du crash.

## 6. Synthese et mise a jour du dossier

Apres l'analyse :

1. **Comparer** les nouveaux crashs avec les hypotheses existantes dans le dossier
2. **Mettre a jour** `gpu_crash_history.md` avec :
   - Les nouveaux crashs dans la chronologie
   - Les hypotheses confirmees/infirmees
   - Les nouvelles observations
3. **Relancer le moniteur GPU** s'il n'est plus actif :

```bash
cat > /tmp/relaunch_monitor.ps1 << 'PSEOF'
$running = Get-Process powershell -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -eq '' }
if (-not $running) {
    Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File C:\Users\redla\projetclaude\gpu_monitor.ps1 -IntervalSeconds 10' -WindowStyle Minimized
    Write-Host "GPU Monitor relance"
} else {
    Write-Host "GPU Monitor deja actif"
}
PSEOF
powershell.exe -ExecutionPolicy Bypass -File /tmp/relaunch_monitor.ps1
```

4. **Recommander** les prochaines actions (pas les executer sans accord)
