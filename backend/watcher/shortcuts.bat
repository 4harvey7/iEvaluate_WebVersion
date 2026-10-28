@echo off
REM Makes (or removes) the "Start Scan Watcher" and "Stop Scan Watcher" shortcuts
REM on the desktop and in the Start menu (folder "iEvaluate Scan Watcher").
REM INSTALL.bat / UNINSTALL.bat call this - you normally do not need it.
REM   shortcuts.bat create [/quiet]   make the shortcuts (old ones are replaced)
REM   shortcuts.bat remove [/quiet]   delete them and the Start-menu folder
REM The shortcuts open start_watcher.bat / STOP.bat next to this file, in a normal
REM window. The desktop is asked from Windows, so a OneDrive desktop works too.
REM No administrator rights needed. Exit code 0 = OK, 1 = failed.
setlocal EnableExtensions DisableDelayedExpansion
set "SW_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\iEvaluate Scan Watcher"
set "SW_START=%~dp0start_watcher.bat"
set "SW_STOP=%~dp0STOP.bat"
for %%I in ("%~dp0.") do set "SW_WORKDIR=%%~fI"
set "QUIET=0"
if /i "%~2"=="/quiet" set "QUIET=1"
if /i "%~1"=="create" goto create
if /i "%~1"=="remove" goto remove
echo Usage: shortcuts.bat create^|remove [/quiet]
exit /b 1

:create
if not exist "%SW_START%" goto create_failed
if not exist "%SW_STOP%" goto create_failed
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok = $true; $sh = New-Object -ComObject WScript.Shell; $desk = [Environment]::GetFolderPath('Desktop'); $dirs = @($env:SW_MENU); if ($desk) { $dirs += $desk } else { $ok = $false }; foreach ($d in $dirs) { try { if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }; foreach ($l in @(@('Start Scan Watcher', $env:SW_START, 'Start the iEvaluate Scan Watcher before scanning'), @('Stop Scan Watcher', $env:SW_STOP, 'Stop the iEvaluate Scan Watcher'))) { $p = Join-Path $d ($l[0] + '.lnk'); $s = $sh.CreateShortcut($p); $s.TargetPath = $l[1]; $s.WorkingDirectory = $env:SW_WORKDIR; $s.WindowStyle = 1; $s.Description = $l[2]; $s.Save(); if (-not (Test-Path -LiteralPath $p)) { $ok = $false } } } catch { $ok = $false } }; if ($ok) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 goto create_failed
if "%QUIET%"=="1" exit /b 0
echo "Start Scan Watcher" and "Stop Scan Watcher" are on the desktop and in the Start menu.
pause
exit /b 0

:create_failed
if "%QUIET%"=="1" exit /b 1
echo Could not make all the shortcuts. You can still start the watcher by
echo double-clicking start_watcher.bat in "%SW_WORKDIR%".
pause
exit /b 1

:remove
powershell -NoProfile -ExecutionPolicy Bypass -Command "$left = @(); $desk = [Environment]::GetFolderPath('Desktop'); if ($desk) { foreach ($n in @('Start Scan Watcher.lnk', 'Stop Scan Watcher.lnk')) { $p = Join-Path $desk $n; Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue; if (Test-Path -LiteralPath $p) { $left += $p } } }; if (Test-Path -LiteralPath $env:SW_MENU) { Remove-Item -LiteralPath $env:SW_MENU -Recurse -Force -ErrorAction SilentlyContinue }; if (Test-Path -LiteralPath $env:SW_MENU) { $left += $env:SW_MENU }; if ($left.Count -gt 0) { exit 1 } else { exit 0 }" >nul 2>&1
if errorlevel 1 goto remove_failed
if "%QUIET%"=="1" exit /b 0
echo The Start / Stop shortcuts are removed.
pause
exit /b 0

:remove_failed
if "%QUIET%"=="1" exit /b 1
echo Some shortcuts could not be removed. Delete "Start Scan Watcher" and
echo "Stop Scan Watcher" from the desktop by hand.
pause
exit /b 1
