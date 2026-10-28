@echo off
REM Helper (do not double-click). Used by INSTALL.bat, STOP.bat, CHECK.bat.
REM   watcher_process.bat stop    stop every running Scan Watcher on this computer
REM                               (from ANY folder, including old installs)
REM   watcher_process.bat check   exit code 0 = a Scan Watcher is running, 1 = not running
setlocal
if /i "%~1"=="check" goto check

REM Old installs ran from a scheduled task; ending it stops its window too.
schtasks /end /tn "iEvaluate Scan Watcher" >nul 2>&1
REM First the restart loops (cmd running start_watcher.bat), then the watcher itself.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$all = @(Get-CimInstance Win32_Process); $loops = @($all | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*start_watcher.bat*' }); $py = @($all | Where-Object { $_.Name -match '^(py|pyw|python|pythonw)[0-9.]*\.exe$' -and $_.CommandLine -like '*folder_watcher.py*' }); foreach ($p in ($loops + $py)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
REM Give Windows a moment to release the files.
ping -n 3 127.0.0.1 >nul
exit /b 0

:check
powershell -NoProfile -ExecutionPolicy Bypass -Command "$py = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(py|pyw|python|pythonw)[0-9.]*\.exe$' -and $_.CommandLine -like '*folder_watcher.py*' }); if ($py.Count -gt 0) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 exit /b 1
exit /b 0
