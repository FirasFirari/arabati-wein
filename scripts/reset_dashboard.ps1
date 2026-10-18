# Kill every dashboard-related process, then relaunch only via Task Scheduler
# so the survivor is detached from any terminal session.
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*carhunt.dashboard*" -or $_.CommandLine -like "*run_dashboard*"
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 4
schtasks /Run /TN "CarHunt Dashboard" | Out-Null
Start-Sleep -Seconds 12

"--- surviving dashboard processes ---"
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*carhunt.dashboard*" } | ForEach-Object {
    $par = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue
    $who = if ($par) { $par.Name } else { "none (detached)" }
    "pid $($_.ProcessId)  parent=$who"
}
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8770/api" -UseBasicParsing -TimeoutSec 5
    "dashboard responding: HTTP $($r.StatusCode)"
} catch {
    "dashboard NOT responding"
}
