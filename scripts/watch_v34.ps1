$log = "C:\MesProjets\PROMETHEE_V11_restructuration2026\logs\promethee_2026-04-27.log"
$out = "C:\MesProjets\PROMETHEE_V11_restructuration2026\logs\v34_watch_$(Get-Date -Format 'HHmm').log"
$pattern = 'V34 MOTIVATIONAL|LOOP_BREAKER|cap atteint|AUTONOMY \[FORCED\]|FATAL|Traceback|mark_satisfied|✨ AUTONOMY|blacklist|Routine FORCED'
$last = (Get-Content $log -ErrorAction SilentlyContinue).Count
"[WATCH] start last=$last out=$out" | Out-File $out -Encoding UTF8
while ($true) {
    Start-Sleep -Seconds 30
    $lines = Get-Content $log -ErrorAction SilentlyContinue
    if ($lines.Count -gt $last) {
        $new = $lines[$last..($lines.Count - 1)]
        $matches = $new | Where-Object { $_ -match $pattern }
        if ($matches) {
            $matches | Out-File $out -Encoding UTF8 -Append
        }
        $last = $lines.Count
    }
}
