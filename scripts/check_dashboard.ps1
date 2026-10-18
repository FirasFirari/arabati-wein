Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*dashboard*" } | ForEach-Object {
    $par = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue
    $who = if ($par) { $par.Name } else { "none (detached)" }
    "pid $($_.ProcessId)  parent=$who"
}
