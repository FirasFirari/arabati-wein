$procs = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*carhunt*" -or $_.CommandLine -like "*run_fb_service*" -or
    $_.CommandLine -like "*run_watchdog*" -or $_.CommandLine -like "*run_dashboard*" -or
    $_.CommandLine -like "*run_monitor*" -or $_.CommandLine -like "*run_intake*"
}
foreach ($p in $procs) {
    $chain = @()
    $cur = $p
    for ($i = 0; $i -lt 6 -and $cur; $i++) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($cur.ParentProcessId)" -ErrorAction SilentlyContinue
        if (-not $parent) { break }
        $chain += $parent.Name
        $cur = $parent
    }
    $cmd = ($p.CommandLine -replace '.*\\','') -replace '"',''
    $short = $cmd.Substring(0, [Math]::Min(46, $cmd.Length))
    "{0,-48} <- {1}" -f $short, ($chain -join " <- ")
}
