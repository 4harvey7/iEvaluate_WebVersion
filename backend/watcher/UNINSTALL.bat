@echo off
REM ===========================================================================
REM  Removes the Scan Watcher from this computer. Double-click this file.
REM  Stops the watcher (STOP.bat: also tells the website), removes the Start /
REM  Stop shortcuts and any auto-start. Scans and the scan folder are NOT
REM  touched. Program files stay in %LOCALAPPDATA%\ScanWatcher
REM  (delete that folder by hand if you want them gone).
REM ===========================================================================
setlocal EnableExtensions DisableDelayedExpansion
title Scan Watcher - Uninstall
color 1F
cd /d "%~dp0"
set "HERE=%~dp0"
set "DEST=%LOCALAPPDATA%\ScanWatcher"
set "TOOLS=%DEST%"
if not exist "%TOOLS%\watcher_process.bat" set "TOOLS=%HERE:~0,-1%"
REM An older install in DEST may not have these yet: then use the ones here.
set "SW_STOPPER=%TOOLS%\STOP.bat"
if not exist "%SW_STOPPER%" set "SW_STOPPER=%HERE%STOP.bat"
set "SW_SHORTCUTS=%TOOLS%\shortcuts.bat"
if not exist "%SW_SHORTCUTS%" set "SW_SHORTCUTS=%HERE%shortcuts.bat"

cls
echo.
echo  ==============================================================
echo.
echo       SCAN WATCHER  -  UNINSTALL
echo.
echo  ==============================================================
echo.
echo   This stops the Scan Watcher on this computer.
echo   Your scans and the scan folder are NOT deleted.
echo.

if not exist "%SW_STOPPER%" goto missing_files
if not exist "%SW_SHORTCUTS%" goto missing_files
if not exist "%TOOLS%\uninstall_task.bat" goto missing_files

echo  --------------------------------------------------------------
echo   Step 1 of 3:  Stopping the Scan Watcher
echo  --------------------------------------------------------------
call "%SW_STOPPER%" /quiet
echo.

echo  --------------------------------------------------------------
echo   Step 2 of 3:  Removing the Start / Stop shortcuts
echo  --------------------------------------------------------------
call "%SW_SHORTCUTS%" remove /quiet
if errorlevel 1 echo   [!] Some shortcuts are left. Delete "Start Scan Watcher" and "Stop Scan Watcher" from the desktop by hand.
if not errorlevel 1 echo   [OK] Shortcuts removed.
echo.

echo  --------------------------------------------------------------
echo   Step 3 of 3:  Removing any auto-start
echo  --------------------------------------------------------------
call "%TOOLS%\uninstall_task.bat" /quiet
if errorlevel 1 goto autostart_left
echo   [OK] It will not start by itself.

color 2F
echo.
echo  ==============================================================
echo.
echo       DONE   [OK]
echo.
echo       The Scan Watcher is removed.
echo       Your scans were NOT touched.
echo.
echo  ==============================================================
echo.
echo   To use it again later, double-click INSTALL.
echo.
echo   Press any key to close this window.
pause >nul
exit /b 0

:autostart_left
color 4F
echo.
echo   [!] The auto-start could not be removed completely.
echo       Ask your IT contact to delete "iEvaluate Scan Watcher"
echo       in Task Scheduler. Your scans were NOT touched.
echo.
echo   Press any key to close this window.
pause >nul
exit /b 1

:missing_files
color 4F
echo   [!] Some files are missing next to UNINSTALL.
echo       Extract the whole ScanWatcher download first, then try again.
echo.
echo   Press any key to close this window.
pause >nul
exit /b 1
