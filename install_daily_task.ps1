$ErrorActionPreference = "Stop"
$taskName = "Dota2 Daily Review"
$defaultTime = "06:15"

Write-Host "This installs a Windows daily task for Dota 2 reviews."
Write-Host "It processes the previous calendar day so late-night matches are included."
$runTimeText = Read-Host "Daily run time in HH:mm format [$defaultTime]"
if ([string]::IsNullOrWhiteSpace($runTimeText)) {
    $runTimeText = $defaultTime
}

$parsedTime = [datetime]::MinValue
$valid = [datetime]::TryParseExact(
    $runTimeText,
    "HH:mm",
    [System.Globalization.CultureInfo]::InvariantCulture,
    [System.Globalization.DateTimeStyles]::None,
    [ref]$parsedTime
)
if (-not $valid) {
    throw "Invalid time. Use HH:mm, for example 06:15 or 23:30."
}

$runner = Join-Path $PSScriptRoot "run_daily_review.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument
$trigger = New-ScheduledTaskTrigger -Daily -At $parsedTime
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $userId `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Select best and worst Dota 2 matches from the previous day and prepare ChatGPT review data." `
    -Force | Out-Null

Write-Host "Installed task: $taskName"
Write-Host "Daily time: $runTimeText"
Write-Host "The task will run when possible after a missed start time."
