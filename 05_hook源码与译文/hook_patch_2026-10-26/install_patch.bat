@echo off
setlocal enableextensions
rem ============================================================
rem  ObsCure hanhua - install clearflash9_arm (TEST)
rem  Adds black-until-text on FIRST menu entry (startup) and
rem  lengthens the black window to ~20 frames (~333ms).
rem  Backs up current version.dll; rollback = rollback_clearflash9.bat
rem ============================================================
set GAME=D:\SteamLibrary\steamapps\common\Obscure
set SRC=%~dp0
set BAK=%GAME%\_obscure_backup\pre_clearflash9

echo [1/3] verify source md5
call :chk "%SRC%version_c93e_clearflash9_arm.dll" 0c17dfc07b666255b04652dbf0cbe541
if errorlevel 1 goto :fail
echo       source OK

echo [2/3] backup current version.dll
if not exist "%BAK%" mkdir "%BAK%"
copy /Y "%GAME%\version.dll" "%BAK%\version.dll" >nul
if errorlevel 1 (
  echo [FAIL] backup failed
  goto :fail
)
echo       backup OK, saved to %BAK%

echo [3/3] install + verify
copy /Y "%SRC%version_c93e_clearflash9_arm.dll" "%GAME%\version.dll" >nul
if errorlevel 1 (
  echo [FAIL] copy failed
  goto :fail
)
call :chk "%GAME%\version.dll" 0c17dfc07b666255b04652dbf0cbe541
if errorlevel 1 goto :fail

echo.
echo [OK] clearflash9_arm installed.
echo      Expect: black screen on FIRST main-menu entry (until text shows),
echo      and ~330ms black on screen switches (was ~50ms).
echo      Rollback: rollback_clearflash9.bat
exit /b 0

:chk
set "TMPMD5="
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile "%~1" MD5') do if not defined TMPMD5 set "TMPMD5=%%H"
if /I "%TMPMD5%"=="%~2" exit /b 0
echo [FAIL] md5 mismatch: %~1
echo        got %TMPMD5%
exit /b 1

:fail
echo.
echo [ABORTED] nothing installed.
exit /b 1
