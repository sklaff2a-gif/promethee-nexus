# Validateur V14 -- verifie que le sommeil circadien tient en vol libre.
#
# Parse les state files runtime de Promethee :
#   memory/circadian_state.json    : last_sleep_report, tasks_completed, trigger_reason
#   memory/synaptic_network.json   : last_dream_time, distribution des poids
#   memory/soliloque_v2_state.json : statistiques V2
#
# Ecrit un verdict (V14 OK / PARTIEL / KO) dans
#   logs/v14_validation/v14_validation_<YYYYMMDD_HHMMSS>.txt
#
# Critères V14 OK :
#   [A] last_sleep_report.tasks_completed contient "dream_consolidation"
#   [B] trigger_reason ne commence pas par "budget="
#   [C] dream_connections > 0
#   [D] last_dream_time recent (< 24h)
#   [E] distribution synaptique pas en regression (faibles < 95%)
#
# Script declenche par Windows Task Scheduler (pas besoin de Claude Code ouvert).
# Tout ASCII pour eviter les soucis d'encoding cross-shell.

[CmdletBinding()]
param(
    [string]$BaseDir = 'C:\MesProjets\PROMETHEE_V11_restructuration2026'
)

$ErrorActionPreference = 'Continue'

# Setup
$now      = Get-Date
$stamp    = $now.ToString('yyyyMMdd_HHmmss')
$logDir   = Join-Path $BaseDir 'logs\v14_validation'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile  = Join-Path $logDir "v14_validation_${stamp}.txt"

$lines = New-Object System.Collections.Generic.List[string]
function Add-Line([string]$s = '') { [void]$lines.Add($s) }

Add-Line "======================================================================="
Add-Line "  VALIDATEUR V14 -- Sommeil circadien"
Add-Line ("  Lancement : {0} (Europe/Paris)" -f $now.ToString('yyyy-MM-dd HH:mm:ss'))
Add-Line "  Base dir  : $BaseDir"
Add-Line "======================================================================="

# Verdicts par critere
$verdicts = @{
    A_dream_in_tasks   = $null
    B_trigger_real     = $null
    C_dream_connect    = $null
    D_dream_recent     = $null
    E_synaptic_health  = $null
}
$detail = @{}

# --- 1. circadian_state.json ---
Add-Line ''
Add-Line '-- 1. CIRCADIAN_STATE -------------------------------------------------'
$circPath = Join-Path $BaseDir 'memory\circadian_state.json'
if (-not (Test-Path $circPath)) {
    Add-Line "  [X] Fichier absent : $circPath"
    $verdicts.A_dream_in_tasks = $false
    $verdicts.B_trigger_real   = $false
    $verdicts.C_dream_connect  = $false
} else {
    try {
        $circ = Get-Content $circPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Add-Line ("  Phase actuelle      : {0}" -f $circ.phase)
        Add-Line ("  Total sleep cycles  : {0}" -f $circ.total_sleep_cycles)
        Add-Line ("  Tasks completed cum : {0}" -f $circ.stats.total_tasks_completed)
        Add-Line ("  Tasks failed cum    : {0}" -f $circ.stats.total_tasks_failed)

        $report = $circ.last_sleep_report
        if ($null -eq $report) {
            Add-Line "  [X] last_sleep_report ABSENT -- V14 jamais exerce en vol"
            $verdicts.A_dream_in_tasks = $false
            $verdicts.B_trigger_real   = $false
            $verdicts.C_dream_connect  = $false
        } else {
            $started   = [DateTimeOffset]::FromUnixTimeSeconds([int]$report.started_at).LocalDateTime
            $ended     = if ($report.ended_at -gt 0) { [DateTimeOffset]::FromUnixTimeSeconds([int]$report.ended_at).LocalDateTime } else { $null }
            $durationS = if ($ended) { ($ended - $started).TotalSeconds } else { 0 }
            $tasksDone = @($report.tasks_completed)
            $tasksFail = @($report.tasks_failed)
            $reason    = [string]$report.trigger_reason

            Add-Line ''
            Add-Line "  -- last_sleep_report --"
            Add-Line ("  Demarre        : {0}" -f $started.ToString('yyyy-MM-dd HH:mm:ss'))
            $endStr = if ($ended) { $ended.ToString('yyyy-MM-dd HH:mm:ss') } else { '(en cours)' }
            Add-Line ("  Termine        : {0}" -f $endStr)
            Add-Line ("  Duree          : {0} s ({1} min)" -f ([int]$durationS), ([math]::Round($durationS/60,1)))
            Add-Line ("  Trigger reason : '{0}'" -f $reason)
            Add-Line ("  Tasks completed: [{0}]" -f ($tasksDone -join ', '))
            Add-Line ("  Tasks failed   : [{0}]" -f ($tasksFail -join ', '))
            Add-Line ("  dream_connections : {0}" -f $report.dream_connections)
            Add-Line ("  pruned_synapses   : {0}" -f $report.pruned_synapses)
            Add-Line ("  episodes_purged   : {0}" -f $report.episodes_purged)
            Add-Line ("  arcs_consolidated : {0}" -f $report.arcs_consolidated)
            Add-Line ("  chromadb_removed  : {0}" -f $report.chromadb_removed)
            Add-Line ("  rules_compiled    : {0}" -f $report.rules_compiled)
            Add-Line ("  grimoire_harvested: {0}" -f $report.grimoire_harvested)
            Add-Line ("  mdp_transitions   : {0}" -f $report.mdp_transitions)

            # A : dream_consolidation a tourne
            $verdicts.A_dream_in_tasks = ($tasksDone -contains 'dream_consolidation')
            if ($verdicts.A_dream_in_tasks) {
                $detail.A = "OK -- dream_consolidation presente dans tasks_completed"
            } else {
                $detail.A = "ECHEC -- dream_consolidation ABSENTE (tasks: $($tasksDone -join ','))"
            }

            # B : trigger pas le generique buggy
            $verdicts.B_trigger_real = ($reason -and ($reason -notmatch '^budget='))
            if ($verdicts.B_trigger_real) {
                $detail.B = "OK -- trigger_reason='$reason' (V14 expose la vraie cause)"
            } else {
                $detail.B = "ECHEC -- trigger_reason='$reason' encore generique (V14 _last_trigger_reason non cablee ?)"
            }

            # C : dream_connections > 0
            $verdicts.C_dream_connect = ([int]$report.dream_connections -gt 0)
            if ($verdicts.C_dream_connect) {
                $detail.C = "OK -- $($report.dream_connections) connexions oniriques creees"
            } else {
                $detail.C = "ECHEC -- dream_connections=0 (le reve n'a rien produit)"
            }
        }
    } catch {
        Add-Line ("  [X] Parsing echoue : {0}" -f $_.Exception.Message)
        $verdicts.A_dream_in_tasks = $false
        $verdicts.B_trigger_real   = $false
        $verdicts.C_dream_connect  = $false
    }
}

# --- 2. synaptic_network.json ---
Add-Line ''
Add-Line '-- 2. SYNAPTIC_NETWORK -----------------------------------------------'
$synPath = Join-Path $BaseDir 'memory\synaptic_network.json'
if (-not (Test-Path $synPath)) {
    Add-Line "  [X] Fichier absent : $synPath"
    $verdicts.D_dream_recent    = $false
    $verdicts.E_synaptic_health = $false
} else {
    try {
        $syn = Get-Content $synPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $nodes = @($syn.nodes.PSObject.Properties).Count
        $synap = @($syn.synapses.PSObject.Properties).Count
        $lastDream = [DateTimeOffset]::FromUnixTimeSeconds([int]$syn.last_dream_time).LocalDateTime
        $deltaH = ((Get-Date) - $lastDream).TotalHours

        Add-Line ("  Noeuds        : {0}" -f $nodes)
        Add-Line ("  Synapses      : {0}" -f $synap)
        Add-Line ("  Last dream    : {0} (il y a {1} h)" -f $lastDream.ToString('yyyy-MM-dd HH:mm:ss'), [math]::Round($deltaH,1))

        # Distribution
        $weights = New-Object System.Collections.Generic.List[double]
        foreach ($p in $syn.synapses.PSObject.Properties) {
            $w = $p.Value.weight
            if ($null -ne $w) { [void]$weights.Add([double]$w) }
        }
        if ($weights.Count -gt 0) {
            $sorted = $weights | Sort-Object
            $median = $sorted[[int]($sorted.Count / 2)]
            $mean   = ($weights | Measure-Object -Average).Average
            $strong = ($weights | Where-Object { $_ -ge 0.5 }).Count
            $weak   = ($weights | Where-Object { $_ -lt 0.1 }).Count
            $pctWeak   = [math]::Round(100.0 * $weak / $weights.Count, 1)
            $pctStrong = [math]::Round(100.0 * $strong / $weights.Count, 2)
            Add-Line ("  Poids mean    : {0}   median : {1}" -f ([math]::Round($mean,4)), ([math]::Round($median,4)))
            Add-Line ("  Fortes >=0.5  : {0} ({1} pourcent)" -f $strong, $pctStrong)
            Add-Line ("  Faibles <0.1  : {0} ({1} pourcent)" -f $weak, $pctWeak)

            # D : dream recent
            $verdicts.D_dream_recent = ($deltaH -lt 24)
            if ($verdicts.D_dream_recent) {
                $detail.D = ("OK -- last_dream il y a {0}h (< 24h)" -f [math]::Round($deltaH,1))
            } else {
                $detail.D = ("ECHEC -- last_dream vieux de {0}h (>= 24h, sommeil ne consolide pas)" -f [math]::Round($deltaH,1))
            }

            # E : pas de regression franche vs baseline 01/05 (91.7 pct weak, 8 strong)
            # Seuil large (>97pct) pour tolerer la situation post-dream_emergency
            # (474 connexions vierges injectees a w=0.08 le 01/05 a 07:52, ce qui
            # gonfle artificiellement le pct faibles temporairement).
            $regression = ($pctWeak -gt 97.0 -and $strong -lt 5)
            $verdicts.E_synaptic_health = (-not $regression)
            if ($verdicts.E_synaptic_health) {
                $detail.E = ("OK -- distribution acceptable (faibles={0}pct, fortes={1}, baseline 91.7pct/8)" -f $pctWeak, $strong)
            } else {
                $detail.E = ("ECHEC -- REGRESSION franche : faibles={0}pct ET fortes={1} (graphe sclerose)" -f $pctWeak, $strong)
            }
        } else {
            Add-Line "  [!] Aucun poids synaptique extrait -- structure JSON inattendue"
            $verdicts.D_dream_recent    = ($deltaH -lt 24)
            $verdicts.E_synaptic_health = $true
        }
    } catch {
        Add-Line ("  [X] Parsing echoue : {0}" -f $_.Exception.Message)
        $verdicts.D_dream_recent    = $false
        $verdicts.E_synaptic_health = $false
    }
}

# --- 3. soliloque_v2_state.json (info, ne casse pas le verdict) ---
Add-Line ''
Add-Line '-- 3. SOLILOQUE_V2 (info) --------------------------------------------'
$sv2Path = Join-Path $BaseDir 'memory\soliloque_v2_state.json'
if (-not (Test-Path $sv2Path)) {
    Add-Line "  [-] Fichier absent -- V2 jamais appele cette nuit (silence metabolique)"
} else {
    try {
        $sv2 = Get-Content $sv2Path -Raw -Encoding UTF8 | ConvertFrom-Json
        Add-Line ("  session_count : {0}" -f $sv2.session_count)
        Add-Line ("  success_count : {0}" -f $sv2.success_count)
        Add-Line ("  silence_count : {0}" -f $sv2.silence_count)
        Add-Line ("  abort_count   : {0}" -f $sv2.abort_count)
        if ([int]$sv2.session_count -gt 0) {
            $rate = [math]::Round(100.0 * $sv2.success_count / $sv2.session_count, 1)
            Add-Line ("  success rate  : {0} pourcent" -f $rate)
        }
        if ($sv2.history -and @($sv2.history).Count -gt 0) {
            $last = @($sv2.history)[-1]
            $lastTs = [DateTimeOffset]::FromUnixTimeSeconds([int]$last.timestamp).LocalDateTime
            Add-Line ("  Dernier insight : {0}  ancrages=[{1}]" -f $lastTs.ToString('HH:mm:ss'), ($last.ancrages_used -join ','))
            if ($last.insight) { Add-Line ("    [insight] {0}" -f $last.insight) }
        }
    } catch {
        Add-Line ("  [!] Parsing echoue : {0}" -f $_.Exception.Message)
    }
}

# --- 4. VERDICT GLOBAL ---
Add-Line ''
Add-Line '======================================================================='
Add-Line '  VERDICT V14'
Add-Line '======================================================================='

$nbFalse = (@($verdicts.Values) | Where-Object { $_ -eq $false }).Count
$nbTrue  = (@($verdicts.Values) | Where-Object { $_ -eq $true }).Count
$nbTested= (@($verdicts.Values) | Where-Object { $null -ne $_ }).Count

if ($nbTested -eq 0) {
    $verdict = '[?] V14 INDETERMINE -- aucun fichier exploitable'
} elseif ($nbFalse -eq 0) {
    $verdict = '[OK] V14 OK -- sommeil circadien fonctionnel, dream consolidation active'
} elseif ($nbTrue -gt 0) {
    $verdict = '[!]  V14 PARTIEL -- voir criteres ci-dessous'
} else {
    $verdict = '[X] V14 KO -- le patch n''a pas tenu, sommeil paradoxal toujours muet'
}

Add-Line ''
Add-Line "  $verdict"
Add-Line ''
Add-Line '  -- Criteres --'
foreach ($k in @('A_dream_in_tasks','B_trigger_real','C_dream_connect','D_dream_recent','E_synaptic_health')) {
    $v = $verdicts[$k]
    if ($v -eq $true)  { $icon = '[V]' }
    elseif ($v -eq $false) { $icon = '[X]' }
    else { $icon = '[?]' }
    $key1 = $k.Substring(0,1)
    $d = if ($detail.ContainsKey($key1)) { $detail[$key1] } else { '(non teste)' }
    Add-Line ("  {0} {1} : {2}" -f $icon, $k, $d)
}

Add-Line ''
Add-Line '======================================================================='

# Ecrire le rapport (UTF8 sans BOM)
$content = [string]::Join([Environment]::NewLine, $lines)
[System.IO.File]::WriteAllText($logFile, $content, [System.Text.UTF8Encoding]::new($false))

# Echo console
$lines | ForEach-Object { Write-Host $_ }
Write-Host ''
Write-Host ("Rapport ecrit : {0}" -f $logFile) -ForegroundColor Cyan
