# ============================================================
#  PROMETHEE — Installation de la tâche planifiée Windows
#  Exécuter en tant qu'Administrateur :
#    powershell -ExecutionPolicy Bypass -File install_scheduler.ps1
# ============================================================

$TaskName = "PROMETHEE_AutoMonitor"
$BatPath = "C:\MesProjets\PROMETHEE_V11_restructuration2026\auto_monitor.bat"

# Vérifier que le script existe
if (-not (Test-Path $BatPath)) {
    Write-Host "ERREUR: $BatPath introuvable" -ForegroundColor Red
    exit 1
}

# Supprimer la tâche si elle existe déjà
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tâche existante supprimée." -ForegroundColor Yellow
}

# Créer l'action
$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory "C:\MesProjets\PROMETHEE_V11_restructuration2026"

# Trigger : toutes les 4 heures, indéfiniment
$Trigger = New-ScheduledTaskTrigger -Once -At "00:00" -RepetitionInterval (New-TimeSpan -Hours 4)

# Paramètres : exécuter même si l'utilisateur n'est pas connecté ? Non, on garde l'utilisateur courant
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# Enregistrer la tâche (s'exécute sous l'utilisateur courant)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "PROMETHEE: Cycle de monitoring autonome Claude Code (toutes les 4h)"

Write-Host ""
Write-Host "Tâche '$TaskName' créée avec succès !" -ForegroundColor Green
Write-Host "  - Fréquence : toutes les 4 heures"
Write-Host "  - Script    : $BatPath"
Write-Host "  - Rapports  : logs\autonomous_reports\"
Write-Host ""
Write-Host "Pour vérifier  : Get-ScheduledTask -TaskName $TaskName"
Write-Host "Pour désactiver : Disable-ScheduledTask -TaskName $TaskName"
Write-Host "Pour supprimer  : Unregister-ScheduledTask -TaskName $TaskName"
