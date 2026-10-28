@echo off
REM ===========================================================================
REM  Scan Watcher installer. Double-click this file. No typing needed.
REM  Safe to run again at any time (it replaces the old install cleanly).
REM
REM  What it does:
REM   1. checks the files (was the zip extracted? is watcher_config.json here?)
REM   2. finds Python 3.10+ (installs Python 3.12 with winget if missing)
REM   3. stops any running watcher, installs requirements.txt with pip
REM   4. copies the program to %LOCALAPPDATA%\ScanWatcher, creates the scan folder
REM   5. removes any old auto-start at log-on (uninstall_task.bat) and makes the
REM      "Start Scan Watcher" / "Stop Scan Watcher" shortcuts on the desktop and
REM      in the Start menu (shortcuts.bat). Since 2.5.0 the watcher is started by
REM      hand; IT can still turn auto-start on with install_task.bat.
REM   6. starts the watcher now, once (minimized window)
REM ===========================================================================
setlocal EnableExtensions DisableDelayedExpansion
title Scan Watcher - Installer
color 1F
cd /d "%~dp0"
set "HERE=%~dp0"
set "DEST=%LOCALAPPDATA%\ScanWatcher"
set "SW_DEST=%DEST%"
set "TMPF=%TEMP%\scanwatcher_folder_%RANDOM%.txt"
set "SCANDIR=C:\Scans"

cls
echo.
echo  ==============================================================
echo.
echo       SCAN WATCHER  -  INSTALLER
echo.
echo  ==============================================================
echo.
echo   Hello! This sets up the Scan Watcher on this computer.
echo   It takes about 2 to 5 minutes.
echo   You do not need to type anything. Just wait.
echo.

REM ------------------------------------------------------------------ Step 1
call :step 1 "Checking the files"
set "CHK=%HERE:.zip\=%"
if /i not "%CHK%"=="%HERE%" goto not_extracted
set "CHK=%HERE:\AppData\Local\Temp\=%"
if /i not "%CHK%"=="%HERE%" goto not_extracted
for %%F in (folder_watcher.py requirements.txt setup_helper.py start_watcher.bat find_python.bat watcher_process.bat install_task.bat uninstall_task.bat STOP.bat shortcuts.bat) do if not exist "%HERE%%%F" goto not_extracted
if not exist "%HERE%watcher_config.json" goto no_config
echo   [OK] All files are here.

REM ------------------------------------------------------------------ Step 2
call :step 2 "Looking for the helper program (Python)"
call "%HERE%find_python.bat"
if defined PY goto have_python

echo   The helper program is not on this computer yet.
echo   Installing it now. This can take a few minutes. Please wait...
echo.
echo   If Windows asks "Do you want to allow this app to make changes
echo   to your device?", click "Yes".
echo.
where winget >nul 2>&1
if errorlevel 1 goto no_python
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
call "%HERE%find_python.bat"
if not defined PY goto no_python

:have_python
echo   [OK] Helper program found.

REM ------------------------------------------------------------------ Step 3
call :step 3 "Installing the helper parts (needs internet)"
echo   Stopping any old Scan Watcher that is still running...
call "%HERE%watcher_process.bat" stop
set /a TRIES=0

:pip_again
set /a TRIES+=1
echo   Downloading and installing. This can take a few minutes. Please wait...
%PY% -m pip --version >nul 2>&1
if errorlevel 1 %PY% -m ensurepip --upgrade >nul 2>&1
%PY% -m pip install --disable-pip-version-check --no-warn-script-location -q -r "%HERE%requirements.txt"
if errorlevel 1 goto pip_error
%PY% -c "import requests, PIL" >nul 2>&1
if errorlevel 1 goto pip_error
echo   [OK] Helper parts are installed.
goto pip_done

:pip_error
color 6F
echo.
echo   [!] The helper parts could not be downloaded.
echo       Please check that this computer is connected to the internet.
if %TRIES% GEQ 3 goto pip_failed
echo.
echo       When the internet works, press any key to try again.
pause >nul
color 1F
goto pip_again

:pip_done

REM ------------------------------------------------------------------ Step 4
call :step 4 "Copying the Scan Watcher to this computer"
%PY% "%HERE%setup_helper.py" config --config "%HERE%watcher_config.json" >nul 2>&1
if errorlevel 3 if not errorlevel 4 goto bad_config
if /i "%HERE%"=="%DEST%\" goto copied
if not exist "%DEST%\" mkdir "%DEST%" >nul 2>&1
if not exist "%DEST%\" goto copy_failed
for %%F in (folder_watcher.py requirements.txt setup_helper.py start_watcher.bat find_python.bat watcher_process.bat install_task.bat uninstall_task.bat STOP.bat shortcuts.bat INSTALL.bat UNINSTALL.bat CHECK.bat "HOW TO INSTALL.txt" "TECHNICAL NOTES.txt") do if exist "%HERE%%%~F" copy /y "%HERE%%%~F" "%DEST%\" >nul 2>&1
copy /y "%HERE%watcher_config.json" "%DEST%\watcher_config.json" >nul 2>&1
fc /b "%HERE%watcher_config.json" "%DEST%\watcher_config.json" >nul 2>&1
if errorlevel 1 goto copy_failed
fc /b "%HERE%folder_watcher.py" "%DEST%\folder_watcher.py" >nul 2>&1
if errorlevel 1 goto copy_failed
REM Files from a download are marked "from the internet"; unmark our copies so
REM Windows does not ask about them at every start-up.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath $env:SW_DEST -File | Unblock-File" >nul 2>&1

:copied
echo   [OK] Program copied to "%DEST%"
if exist "%TMPF%" del /q "%TMPF%" >nul 2>&1
%PY% "%DEST%\setup_helper.py" config --config "%DEST%\watcher_config.json" --out "%TMPF%" 2>nul
set "CFGRESULT=%ERRORLEVEL%"
if exist "%TMPF%" set /p SCANDIR=<"%TMPF%"
if exist "%TMPF%" del /q "%TMPF%" >nul 2>&1
if "%CFGRESULT%"=="3" goto bad_config
if "%CFGRESULT%"=="0" echo   [OK] Scan folder is ready: "%SCANDIR%"
if "%CFGRESULT%"=="4" echo   [!] Could not make the scan folder "%SCANDIR%". The Scan Watcher keeps trying by itself.
if "%CFGRESULT%"=="5" echo   [!] The scan folder "%SCANDIR%" is not allowed. Change it on the website to C:\Scans.

REM ------------------------------------------------------------------ Step 5
call :step 5 "Making the Start / Stop shortcuts"
REM The watcher no longer starts at log-on: remove an auto-start left by an
REM older install (Start-up shortcut or scheduled task).
call "%DEST%\uninstall_task.bat" /quiet
if errorlevel 1 echo   [!] An old auto-start could not be removed. Ask your IT contact for help.
set "SHORTCUTS_OK=1"
call "%DEST%\shortcuts.bat" create /quiet
if errorlevel 1 set "SHORTCUTS_OK=0"
if "%SHORTCUTS_OK%"=="1" echo   [OK] "Start Scan Watcher" and "Stop Scan Watcher" are on the desktop.
if "%SHORTCUTS_OK%"=="0" echo   [!] The shortcuts could not be made. To start the Scan Watcher, open
if "%SHORTCUTS_OK%"=="0" echo       "%DEST%" and double-click start_watcher.bat.

REM ------------------------------------------------------------------ Step 6
call :step 6 "Starting the Scan Watcher"
call "%DEST%\watcher_process.bat" stop
start "iEvaluate Scan Watcher" /min /d "%DEST%" "%DEST%\start_watcher.bat"
echo   Checking the connection to the website...
set "ONLINE_OK=1"
%PY% "%DEST%\setup_helper.py" online --config "%DEST%\watcher_config.json" >nul 2>&1
if errorlevel 1 set "ONLINE_OK=0"
set /a WAITS=0

:wait_running
call "%DEST%\watcher_process.bat" check
if not errorlevel 1 goto is_running
set /a WAITS+=1
if %WAITS% GEQ 10 goto not_running
ping -n 3 127.0.0.1 >nul
goto wait_running

:is_running
echo   [OK] The Scan Watcher is running.
if "%ONLINE_OK%"=="0" echo   [!] This computer cannot reach the website right now. Check the internet.
if "%ONLINE_OK%"=="0" echo       The Scan Watcher keeps trying by itself.

REM ------------------------------------------------------------------ Done
color 2F
echo.
echo  ==============================================================
echo.
echo       ALL DONE   [OK]
echo.
echo       You can close this window.
echo.
echo  ==============================================================
echo.
echo   Set your scanner to save files in:   %SCANDIR%
echo.
echo   Then look at the website. The light should be green.
echo.
echo   NEXT TIME: before scanning, double-click "Start Scan Watcher"
echo   on the desktop. To stop it, double-click "Stop Scan Watcher"
echo   or close its window.
echo.
echo   To check this laptop later, double-click CHECK.
echo.
echo   Press any key to close this window.
pause >nul
exit /b 0


REM ======================================================== subroutines
:step
echo.
echo  --------------------------------------------------------------
echo   Step %~1 of 6:  %~2
echo  --------------------------------------------------------------
goto :eof


REM ======================================================== problems
:not_extracted
color 4F
echo.
echo  ==============================================================
echo   STOP - the files are still inside the zip.
echo  ==============================================================
echo.
echo   1. Close this window.
echo   2. Open your Downloads folder.
echo   3. Right-click the ScanWatcher file.
echo   4. Click "Extract All", then click "Extract".
echo   5. In the new folder, open "ScanWatcher" and double-click INSTALL.
echo.
goto end_error

:no_config
color 4F
echo.
echo  ==============================================================
echo   STOP - the settings file is missing.
echo  ==============================================================
echo.
echo   This folder has no "watcher_config.json" file.
echo.
echo   1. On the website, open "Scanner setup".
echo   2. Choose your scanner station.
echo   3. Click "Download installer".
echo   4. Extract the new download and double-click INSTALL in it.
echo.
goto end_error

:bad_config
color 4F
echo.
echo  ==============================================================
echo   STOP - the settings file is damaged.
echo  ==============================================================
echo.
echo   1. On the website, open "Scanner setup".
echo   2. Choose your scanner station.
echo   3. Click "Download installer".
echo   4. Extract the new download and double-click INSTALL in it.
echo.
goto end_error

:no_python
color 4F
echo.
echo  ==============================================================
echo   ONE MORE THING - please install the helper program by hand.
echo  ==============================================================
echo.
echo   1. Your internet browser opens this page now:
echo          https://www.python.org/downloads/
echo   2. Click the yellow "Download Python" button.
echo   3. Open the file you downloaded.
echo   4. At the bottom of the new window, tick the box
echo          "Add python.exe to PATH"
echo   5. Click "Install Now". If Windows asks for permission, click "Yes".
echo   6. When it says "Setup was successful", click "Close".
echo   7. Double-click INSTALL again.
echo.
start "" "https://www.python.org/downloads/" >nul 2>&1
goto end_error

:pip_failed
color 4F
echo.
echo  ==============================================================
echo   STOP - no internet, or the download is blocked.
echo  ==============================================================
echo.
echo   1. Make sure this computer is connected to the internet.
echo   2. Double-click INSTALL again.
echo   If it still does not work, ask your IT contact for help.
echo.
goto end_error

:copy_failed
color 4F
echo.
echo  ==============================================================
echo   STOP - the files could not be copied.
echo  ==============================================================
echo.
echo   1. Restart the computer.
echo   2. Double-click INSTALL again.
echo   If it still does not work, ask your IT contact for help.
echo   They need this folder: "%DEST%"
echo.
goto end_error

:not_running
color 4F
echo.
echo  ==============================================================
echo   STOP - the Scan Watcher did not start.
echo  ==============================================================
echo.
echo   1. Restart the computer.
echo   2. Double-click INSTALL again.
echo   If it still does not work, double-click CHECK and show the
echo   message to your IT contact.
echo.
goto end_error

:end_error
echo   Press any key to close this window.
pause >nul
exit /b 1
