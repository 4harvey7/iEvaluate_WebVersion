@echo off
REM Starts the iEvaluate scan watcher and restarts it if it ever stops.
REM It is started by hand: double-click "Start Scan Watcher" (desktop or Start
REM menu) before scanning. Stop it with "Stop Scan Watcher" or close this window.
REM (It does not start at log-on unless IT turned that on with install_task.bat.)
REM If another copy is already running (folder_watcher.py exit code 7 = its
REM watcher.lock is taken), this window says so and closes instead of retrying.
setlocal
cd /d "%~dp0"
title iEvaluate Scan Watcher
set "SW_SCRIPT=%~dp0folder_watcher.py"

REM Never run two copies of the same watcher.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$me = $env:SW_SCRIPT.ToLower(); $p = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(py|pyw|python|pythonw)[0-9.]*\.exe$' -and $_.CommandLine -and $_.CommandLine.ToLower().Contains($me) }); if ($p.Count -gt 0) { exit 7 }" >nul 2>&1
if "%ERRORLEVEL%"=="7" goto already_running

echo Starting the Scan Watcher. Keep this window open while scanning - you can minimize it.
echo To stop it, double-click "Stop Scan Watcher" or close this window.
echo.

:loop
call "%~dp0find_python.bat"
if not defined PY goto no_python
%PY% "%SW_SCRIPT%" %*
set "SW_CODE=%ERRORLEVEL%"
if "%SW_CODE%"=="7" goto already_running
echo.
echo The watcher stopped (code %SW_CODE%). Restarting in 10 seconds... Close this window to stop it.
ping -n 11 127.0.0.1 >nul
goto loop

:no_python
echo The helper program Python 3 was not found. Double-click INSTALL to fix this.
echo Trying again in 60 seconds...
ping -n 61 127.0.0.1 >nul
goto loop

:already_running
echo The Scan Watcher is already running. This window will close by itself.
ping -n 6 127.0.0.1 >nul
exit /b 0
