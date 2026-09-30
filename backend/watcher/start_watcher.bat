@echo off
REM Starts the iEvaluate scan watcher and restarts it if it ever stops.
REM INSTALL.bat makes this start by itself (minimized) every time you log on.
REM Closing this window stops the watcher until the next log-on.
setlocal
cd /d "%~dp0"
title iEvaluate Scan Watcher
set "SW_SCRIPT=%~dp0folder_watcher.py"

REM Never run two copies of the same watcher.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$me = $env:SW_SCRIPT.ToLower(); $p = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(py|pyw|python|pythonw)[0-9.]*\.exe$' -and $_.CommandLine -and $_.CommandLine.ToLower().Contains($me) }); if ($p.Count -gt 0) { exit 7 }" >nul 2>&1
if "%ERRORLEVEL%"=="7" goto already_running

echo The Scan Watcher is running. Keep this window open - you can minimize it.
echo.

:loop
call "%~dp0find_python.bat"
if not defined PY goto no_python
%PY% "%SW_SCRIPT%" %*
echo.
echo The watcher stopped (code %ERRORLEVEL%). Restarting in 10 seconds... Close this window to stop it.
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
