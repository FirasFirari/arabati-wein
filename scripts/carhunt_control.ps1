# Pause / resume / status for every Car Hunt process on this computer.
#
#   powershell -ExecutionPolicy Bypass -File scripts\carhunt_control.ps1 status
#   powershell -ExecutionPolicy Bypass -File scripts\carhunt_control.ps1 pause
#   powershell -ExecutionPolicy Bypass -File scripts\carhunt_control.ps1 pause -Light
#   powershell -ExecutionPolicy Bypass -File scripts\carhunt_control.ps1 resume
#
# Or double-click the "CarHunt Pause" / "CarHunt Resume" / "CarHunt Status"
# shortcuts on the desktop.
#
# PAUSE really frees the RAM: it stops the processes, it does not just suspend
# them (a suspended process keeps its memory). To stop them coming straight
# back it also (1) DISABLES the CarHunt scheduled tasks, so neither the
# watchdog's restarts nor the next logon nor the hourly/daily jobs can relaunch
# anything, and (2) writes data\PAUSED, which every scheduled script checks.
# A pause therefore survives a reboot until you resume.
#
# -Light stops only the heavy part: the Facebook collector and the Chromium it
# drives (~700 MB), plus any scoring run. The dashboard, Telegram monitor,
# intake bot and hourly sweep keep going.
#
# Stopping mid-run is safe: the scorer's verdicts are per post, and reconcile
# (claimed vs stored vehicles) has matched exactly after every kill so far.
param(
    [Parameter(Position = 0)][ValidateSet("status", "pause", "resume")]
    [string]$Action = "status",
    [switch]$Light,
    [switch]$Hold      # keep the window open a few seconds (for the shortcuts)
)

$Root = Split-Path -Parent $PSScriptRoot
$Flag = Join-Path $Root "data\PAUSED"
$LightFlag = Join-Path $Root "data\PAUSED_LIGHT"

# The always-on services started at logon, and the periodic jobs.
$Services = "CarHunt Watchdog", "CarHunt Dashboard", "CarHunt Facebook Service",
            "CarHunt Intake Bot", "CarHunt Channel Monitor"
$Periodic = "CarHunt Raw Sweep", "CarHunt Daily Free Lane", "CarHunt Clarity Snapshot", "CarHunt Photo OCR"
$Heavy = "CarHunt Facebook Service", "CarHunt Daily Free Lane", "CarHunt Photo OCR"

# Matched on command line, deliberately narrow: the terminal Claude Code runs
# in also mentions "Car Hunt" and must never be killed.
$PyScripts = "openai_backlog|raw_sweep|daily_free_lane|kashf_verdicts|" +
             "ingest_local_verdicts|fetch_watches|clarity_export|gvision_sweep|local_ocr_sweep|gemini_ocr_sweep"

function Get-CarHuntProcs([switch]$HeavyOnly) {
    Get-CimInstance Win32_Process | Where-Object {
        $c = $_.CommandLine
        if (-not $c) { return $false }
        if ($HeavyOnly) {
            ($_.Name -eq "cmd.exe" -and $c -match "Car Hunt\\scripts\\run_fb_service") -or
            ($_.Name -match "^pythonw?\.exe$" -and $c -match "carhunt\.fb_service|$PyScripts") -or
            ($_.Name -eq "chrome.exe" -and $c -match "Car Hunt\\data")
        } else {
            ($_.Name -eq "cmd.exe" -and $c -match "Car Hunt\\scripts\\run_") -or
            ($_.Name -eq "wscript.exe" -and $c -match "Car Hunt\\scripts\\") -or
            ($_.Name -match "^pythonw?\.exe$" -and $c -match "-m carhunt\.|$PyScripts") -or
            ($_.Name -eq "chrome.exe" -and $c -match "Car Hunt\\data")
        }
    }
}

function Show-Status {
    $mode = if (Test-Path $Flag) { "PAUSED (everything)" }
            elseif (Test-Path $LightFlag) { "PAUSED (light: collector + scoring only)" }
            else { "RUNNING" }
    Write-Host "Car Hunt is $mode" -ForegroundColor $(if ($mode -eq "RUNNING") { "Green" } else { "Yellow" })
    Write-Host ""
    Get-ScheduledTask -TaskName "CarHunt*" -ErrorAction SilentlyContinue |
        Sort-Object TaskName | ForEach-Object {
            "  {0,-28} {1}" -f $_.TaskName, $_.State }
    $procs = @(Get-CarHuntProcs)
    $mb = ($procs | Measure-Object WorkingSetSize -Sum).Sum / 1MB
    Write-Host ""
    Write-Host ("  {0} Car Hunt processes using {1:N0} MB of RAM" -f $procs.Count, $mb)
    $procs | Where-Object { $_.Name -match "python" } | ForEach-Object {
        $what = if ($_.CommandLine -match "carhunt\.(\w+)") { $Matches[1] }
                elseif ($_.CommandLine -match "scripts[\\/](\w+)\.py") { $Matches[1] } else { "python" }
        "    {0,-22} {1,6:N0} MB" -f $what, ($_.WorkingSetSize / 1MB)
    }
    $chrome = ($procs | Where-Object { $_.Name -eq "chrome.exe" } | Measure-Object WorkingSetSize -Sum).Sum / 1MB
    if ($chrome) { "    {0,-22} {1,6:N0} MB" -f "collector browser", $chrome }
}

function Stop-Procs($procs) {
    # loops first (they would respawn python in 30s), then python, then chrome
    foreach ($kind in "cmd.exe", "wscript.exe", "python", "chrome.exe") {
        $procs | Where-Object { $_.Name -match $kind } | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
}

switch ($Action) {
    "status" { Show-Status }

    "pause" {
        $before = (@(Get-CarHuntProcs) | Measure-Object WorkingSetSize -Sum).Sum / 1MB
        if ($Light) {
            New-Item -ItemType File -Force $LightFlag | Out-Null
            foreach ($t in $Heavy) {
                Disable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue | Out-Null
                Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
            Stop-Procs (Get-CarHuntProcs -HeavyOnly)
        } else {
            New-Item -ItemType File -Force $Flag | Out-Null
            foreach ($t in $Services + $Periodic) {
                Disable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue | Out-Null
                Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
            Stop-Procs (Get-CarHuntProcs)
        }
        Start-Sleep -Seconds 3
        $after = (@(Get-CarHuntProcs) | Measure-Object WorkingSetSize -Sum).Sum / 1MB
        Write-Host ("Paused. Freed about {0:N0} MB." -f ($before - $after)) -ForegroundColor Yellow
        Write-Host "Nothing will restart (not even after a reboot) until you run Resume."
        Write-Host ""
        Show-Status
    }

    "resume" {
        Remove-Item $Flag, $LightFlag -Force -ErrorAction SilentlyContinue
        # clear leftovers first so resume never starts a second copy
        Stop-Procs (Get-CarHuntProcs)
        Start-Sleep -Seconds 2
        foreach ($t in $Services + $Periodic) {
            Enable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue | Out-Null }
        foreach ($t in $Services) {
            Start-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
        Write-Host "Resumed. Services are starting (the collector browser takes ~30s)." -ForegroundColor Green
        Write-Host ""
        Start-Sleep -Seconds 10
        Show-Status
    }
}

if ($Hold) { Start-Sleep -Seconds 12 }


