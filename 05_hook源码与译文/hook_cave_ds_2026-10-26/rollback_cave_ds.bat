@echo off
setlocal enableextensions
rem ============================================================
rem  ObsCure hanhua - rollback from CAVE test DLL to STABLE BASELINE
rem  Restores clearflash9_arm (0c17dfc0) version.dll
rem  Baseline copy lives in this same folder (ASCII name).
rem ============================================================
set "GAME=D:\SteamLibrary\steamapps\common\Obscure"
set "SRC=%~dp0"
set "BAK=%GAME%\_obscure_backup\pre_rollback_ds"

if not exist "%SRC%version_clearflash9_baseline.dll" echo [FAIL] baseline DLL missing & goto :fail

echo [1/4] verifying baseline DLL md5
call :chk "%SRC%version_clearflash9_baseline.dll" 0c17dfc07b666255b04652dbf0cbe541 baseline
if errorlevel 1 goto :fail

echo [2/4] backing up current version.dll
if not exist "%BAK%" mkdir "%BAK%"
copy /Y "%GAME%\version.dll" "%BAK%\version.dll" >nul 2>nul
echo       done

echo [3/4] restoring baseline version.dll
copy /Y "%SRC%version_clearflash9_baseline.dll" "%GAME%\version.dll" >nul
if errorlevel 1 echo [FAIL] copy failed & goto :fail

echo [4/4] verifying restored md5
call :chk "%GAME%\version.dll" 0c17dfc07b666255b04652dbf0cbe541 baseline
if errorlevel 1 goto :fail

echo.
echo [OK] back to STABLE BASELINE V1.0 (clearflash9_arm).
exit /b 0

:chk
set "TMPMD5="
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile %~1 MD5') do if not defined TMPMD5 set "TMPMD5=%%H"
if /I "%TMPMD5%"=="%~2" exit /b 0
echo   [FAIL] md5 mismatch: %~3
exit /b 1

:fail
echo.
echo [ABORTED] rollback not completed.
pause
exit /b 1
