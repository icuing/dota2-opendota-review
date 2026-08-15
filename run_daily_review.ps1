$ErrorActionPreference = "Continue"
$logDir = Join-Path $PSScriptRoot "daily_logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir "windows-latest.log"
$scriptPath = Join-Path $PSScriptRoot "dota2_review.py"

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Daily review started." | Out-File -FilePath $logPath -Encoding utf8

if (Get-Command py.exe -ErrorAction SilentlyContinue) {
    & py.exe -3 $scriptPath --daily --day-offset 1 --parse-timeout 60 *>> $logPath
    $exitCode = $LASTEXITCODE
}
elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    & python.exe $scriptPath --daily --day-offset 1 --parse-timeout 60 *>> $logPath
    $exitCode = $LASTEXITCODE
}
else {
    "Python 3 was not found. Reinstall Python and select Add Python to PATH." | Out-File -FilePath $logPath -Append -Encoding utf8
    $exitCode = 9009
}

"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Daily review finished with exit code $exitCode." | Out-File -FilePath $logPath -Append -Encoding utf8
exit $exitCode
