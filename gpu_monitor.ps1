# GPU Monitor  - Boite noire pour diagnostiquer les crashs RTX 5070 Ti
# Usage : powershell -ExecutionPolicy Bypass -File gpu_monitor.ps1
# Les logs sont ecrits dans gpu_monitor.log (meme dossier)
# Chaque ligne est flushee immediatement pour survivre a un crash

param(
    [int]$IntervalSeconds = 10,
    [string]$LogFile = (Join-Path $PSScriptRoot "gpu_monitor.log")
)

function Write-Log {
    param([string]$Line)
    $Line | Out-File -FilePath $LogFile -Append -Encoding utf8
    # Pas de buffering  - chaque ligne est ecrite immediatement
}

function Get-GpuMetrics {
    try {
        $raw = & nvidia-smi --query-gpu=timestamp,temperature.gpu,power.draw,power.limit,utilization.gpu,utilization.memory,memory.used,memory.total,clocks.current.graphics,clocks.current.memory,fan.speed,pstate --format=csv,noheader,nounits 2>&1
        if ($LASTEXITCODE -eq 0) {
            $fields = $raw -split ',\s*'
            return @{
                Timestamp   = $fields[0]
                TempC       = [int]$fields[1]
                PowerW      = [float]$fields[2]
                PowerCapW   = [float]$fields[3]
                GpuUtil     = [int]$fields[4]
                MemUtil     = [int]$fields[5]
                VramUsedMB  = [int]$fields[6]
                VramTotalMB = [int]$fields[7]
                ClockGfxMHz = [int]$fields[8]
                ClockMemMHz = [int]$fields[9]
                FanPct      = $fields[10].Trim()
                PState      = $fields[11].Trim()
            }
        }
    } catch {}
    return $null
}

function Get-GpuProcesses {
    try {
        $raw = & nvidia-smi --query-compute-apps=pid,used_memory,name --format=csv,noheader,nounits 2>&1
        if ($raw -and $raw -notmatch "no running") {
            return $raw
        }
    } catch {}
    return $null
}

function Get-NvDriverErrors {
    # Erreurs nvlddmkm depuis le dernier check
    param([datetime]$Since)
    try {
        $events = Get-WinEvent -FilterHashtable @{
            LogName      = 'System'
            ProviderName = 'nvlddmkm'
            StartTime    = $Since
        } -ErrorAction SilentlyContinue
        return $events
    } catch {}
    return @()
}

function Get-VramPercent {
    param($Metrics)
    if ($Metrics -and $Metrics.VramTotalMB -gt 0) {
        return [math]::Round(($Metrics.VramUsedMB / $Metrics.VramTotalMB) * 100, 1)
    }
    return 0
}

# --- Demarrage ---
$startTime = Get-Date
Write-Log "============================================"
Write-Log "GPU MONITOR DEMARRE  - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Log "Intervalle : ${IntervalSeconds}s | Log : $LogFile"
Write-Log "============================================"

# Etat initial
$metrics = Get-GpuMetrics
if ($metrics) {
    Write-Log "GPU : NVIDIA GeForce RTX 5070 Ti"
    Write-Log "Driver : $(& nvidia-smi --query-gpu=driver_version --format=csv,noheader)"
    Write-Log "Power cap : $($metrics.PowerCapW)W"
    Write-Log "VRAM : $($metrics.VramUsedMB)/$($metrics.VramTotalMB) MB"
    Write-Log "--------------------------------------------"
} else {
    Write-Log "ERREUR : nvidia-smi inaccessible !"
}

$lastDriverCheck = $startTime
$prevVramUsed = 0
$prevGpuUtil = 0
$spikeCount = 0
$baseline = $null

# --- Boucle principale ---
while ($true) {
    $now = Get-Date
    $ts = $now.ToString("HH:mm:ss")
    $metrics = Get-GpuMetrics

    if (-not $metrics) {
        Write-Log "[$ts] ERREUR nvidia-smi - GPU inaccessible ou driver crash imminent"
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }

    $vramPct = Get-VramPercent $metrics
    $line = "[$ts] ${vramPct}% VRAM ($($metrics.VramUsedMB)MB) | GPU $($metrics.GpuUtil)% | $($metrics.TempC)C | $($metrics.PowerW)W/$($metrics.PowerCapW)W | Clk $($metrics.ClockGfxMHz)/$($metrics.ClockMemMHz)MHz | Fan $($metrics.FanPct)% | $($metrics.PState)"

    # Detection d'anomalies
    $alerts = @()

    # Temperature elevee
    if ($metrics.TempC -ge 75) {
        $alerts += "ALERTE TEMP $($metrics.TempC)C >= 75C"
    }

    # VRAM en hausse rapide (>500MB en un intervalle)
    if ($prevVramUsed -gt 0) {
        $vramDelta = $metrics.VramUsedMB - $prevVramUsed
        if ($vramDelta -gt 500) {
            $alerts += "VRAM SPIKE +${vramDelta}MB"
        }
    }

    # VRAM critique
    if ($vramPct -ge 80) {
        $alerts += "VRAM CRITIQUE ${vramPct}%"
    }

    # Power draw proche du cap
    if ($metrics.PowerCapW -gt 0 -and $metrics.PowerW -ge ($metrics.PowerCapW * 0.95)) {
        $alerts += "POWER NEAR CAP $($metrics.PowerW)W/$($metrics.PowerCapW)W"
    }

    # GPU utilisation spike (0 → >80 en un intervalle)
    if ($prevGpuUtil -lt 20 -and $metrics.GpuUtil -gt 80) {
        $alerts += "GPU SPIKE $prevGpuUtil% -> $($metrics.GpuUtil)%"
    }

    # Power cap perdu (revenu a 300W)
    if ($metrics.PowerCapW -gt 260) {
        $alerts += "POWER CAP PERDU ($($metrics.PowerCapW)W au lieu de 250W)"
    }

    # Ecriture
    if ($alerts.Count -gt 0) {
        $line += " *** " + ($alerts -join " | ")
    }
    Write-Log $line

    # Processus GPU (toutes les 30s ou si alerte)
    if ($alerts.Count -gt 0 -or ($now.Second % 30 -lt $IntervalSeconds)) {
        $procs = Get-GpuProcesses
        if ($procs) {
            foreach ($p in $procs) {
                Write-Log "  [PROC] $($p.Trim())"
            }
        }
    }

    # Erreurs driver nvlddmkm (check toutes les 30s)
    if (($now - $lastDriverCheck).TotalSeconds -ge 30) {
        $errors = Get-NvDriverErrors -Since $lastDriverCheck
        foreach ($evt in $errors) {
            Write-Log "  [NVLDDMKM] Event $($evt.Id) a $($evt.TimeCreated)  - $($evt.LevelDisplayName)"
        }
        $lastDriverCheck = $now
    }

    # Memoriser pour comparaison
    $prevVramUsed = $metrics.VramUsedMB
    $prevGpuUtil = $metrics.GpuUtil

    Start-Sleep -Seconds $IntervalSeconds
}
