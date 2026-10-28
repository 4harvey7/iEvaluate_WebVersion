@echo off
REM ===========================================================================
REM  Stops the Scan Watcher. The "Stop Scan Watcher" shortcut runs this.
REM  Stops every running Scan Watcher on this computer (and its window), then
REM  tells the website, so its light turns red right away. Scans and the scan
REM  folder are NOT touched. Start it again with "Start Scan Watcher".
REM    STOP.bat          stop, show a short message, close by itself
REM    STOP.bat /quiet   same messages, no banner and no wait (UNINSTALL.bat)
REM  Exit code 0 = stopped, 1 = still running (or files missing).
REM ===========================================================================
setlocal EnableExtensions DisableDelayedExpansion
set "HERE=%~dp0"
set "DEST=%LOCALAPPDATA%\ScanWatcher"
set "TOOLS=%HERE:~0,-1%"
if not exist "%TOOLS%\watcher_process.bat" set "TOOLS=%DEST%"
set "QUIET=0"
if /i "%~1"=="/quiet" set "QUIET=1"

if "%QUIET%"=="1" goto do_stop
title Scan Watcher - Stop
color 1F
cls
echo.
echo  ==============================================================
echo.
echo       SCAN WATCHER  -  STOP
echo.
echo  ==============================================================
echo.
echo   Stopping the Scan Watcher. Please wait a few seconds...
echo.

:do_stop
if not exist "%TOOLS%\watcher_process.bat" goto missing_files
call "%TOOLS%\watcher_process.bat" stop
call "%TOOLS%\watcher_process.bat" check
if not errorlevel 1 goto still_running
echo   [OK] Scan Watcher stopped.
call :tell_website
if "%QUIET%"=="1" exit /b 0

color 2F
echo.
echo   Scan Watcher stopped. Scans saved now wait in the scan folder.
echo   Before scanning again, double-click "Start Scan Watcher".
echo.
echo   This window closes by itself.
ping -n 6 127.0.0.1 >nul
exit /b 0

:still_running
echo   [!] It is still running. Restart the computer to stop it.
if "%QUIET%"=="1" exit /b 1
color 4F
echo.
echo   Press any key to close this window.
pause >nul
exit /b 1

:missing_files
echo   [!] Some Scan Watcher files are missing. Double-click INSTALL again.
if "%QUIET%"=="1" exit /b 1
color 4F
echo.
echo   Press any key to close this window.
pause >nul
exit /b 1

:tell_website
REM Watcher 2.4.4+ server: tell the website now, so its light turns red right
REM away instead of after ~40 s (the watcher was killed, so it could not say so
REM itself). Best effort (a few seconds at most); without internet, or with an
REM older server, the light still turns red by itself.
set "SW_CFG=%DEST%\watcher_config.json"
if not exist "%SW_CFG%" set "SW_CFG=%HERE%watcher_config.json"
set "SW_PYFILE=%HERE%folder_watcher.py"
if not exist "%SW_PYFILE%" set "SW_PYFILE=%TOOLS%\folder_watcher.py"
if not exist "%SW_CFG%" goto :eof
if not exist "%SW_PYFILE%" goto :eof
set "PY="
if exist "%TOOLS%\find_python.bat" (call "%TOOLS%\find_python.bat") else if exist "%HERE%find_python.bat" call "%HERE%find_python.bat"
if not defined PY goto :eof
%PY% "%SW_PYFILE%" --offline --config "%SW_CFG%" >nul 2>&1
if errorlevel 1 goto :eof
echo   [OK] The website now shows this laptop as offline.
goto :eof
