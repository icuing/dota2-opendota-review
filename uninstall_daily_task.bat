@echo off
setlocal EnableExtensions
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Unregister-ScheduledTask -TaskName 'Dota2 Review Coach Daily' -Confirm:$false -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName 'Dota2 Daily Review' -Confirm:$false -ErrorAction SilentlyContinue"
echo Daily review task removed.
echo.
pause

