@echo off
REM ===========================================================================
REM  "Is it working?" Double-click this file. It changes nothing.
REM  Shows: is the watcher running (it is started by hand with the "Start Scan
REM  Watcher" shortcut, so "turned off" is normal), does the scan folder exist,
REM  and the last messages from watcher.log in plain words. An auto-start that
REM  IT turned on (install_task.bat) is shown as a note.
REM ===========================================================================
setlocal EnableExtensions DisableDelayedExpansion
title Scan Watcher - Is it working?
color 1F
set "HERE=%~dp0"
set "APPDIR=%LOCALAPPDATA%\ScanWatcher"
if not exist "%APPDIR%\folder_watcher.py" set "APPDIR=%HERE:~0,-1%"
set "SW_LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\iEvaluate Scan Watcher.lnk"

cls
echo.
echo  ==============================================================
echo.
echo       SCAN WATCHER  -  IS IT WORKING?
echo.
echo  ==============================================================
echo   Please wait a few seconds...

set "RUNNING=0"
if not exist "%APPDIR%\watcher_process.bat" goto not_installed
call "%APPDIR%\watcher_process.bat" check
if not errorlevel 1 set "RUNNING=1"

set "AUTOSTART=0"
if exist "%SW_LNK%" set "AUTOSTART=1"
schtasks /query /tn "iEvaluate Scan Watcher" >nul 2>&1
if not errorlevel 1 set "AUTOSTART=1"

call "%APPDIR%\find_python.bat"
if not defined PY goto no_python
if not exist "%APPDIR%\setup_helper.py" goto no_python

%PY% "%APPDIR%\setup_helper.py" report --dir "%APPDIR%" --running %RUNNING% --autostart %AUTOSTART%
if errorlevel 2 goto turned_off
if errorlevel 1 goto problem

color 2F
echo  ==============================================================
echo    Looks good. The light on the website should be green.
echo    Test: scan one page. It should show up on the website.
echo  ==============================================================
goto end

:turned_off
color 1F
echo  ==============================================================
echo    Nothing is broken. The Scan Watcher is just turned off.
echo    Before scanning, double-click "Start Scan Watcher" on the
echo    desktop. Then the light on the website turns green.
echo  ==============================================================
goto end

:problem
color 6F
echo  ==============================================================
echo    Something needs fixing - see the [!!] lines above.
echo    Still stuck? Double-click INSTALL again.
echo  ==============================================================
goto end

:no_python
color 4F
echo.
if "%RUNNING%"=="1" echo   [OK]  The Scan Watcher is running.
if "%RUNNING%"=="0" echo   [i]   The Scan Watcher is turned off. Double-click Start Scan Watcher.
echo   [!!]  The helper program is missing. Double-click INSTALL to fix this.
echo.
goto end

:not_installed
color 4F
echo.
echo   [!!]  The Scan Watcher is not installed on this computer.
echo         Double-click INSTALL to install it.
echo.
goto end

:end
echo.
echo   Press any key to close this window.
pause >nul
exit /b 0
