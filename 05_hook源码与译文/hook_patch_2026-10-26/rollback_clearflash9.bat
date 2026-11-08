@echo off
setlocal enableextensions
rem ============================================================
rem  ObsCure hanhua - rollback clearflash9_arm
rem  Restores clearflash8 (stable baseline) from this folder:
rem    version_clearflash8_baseline.dll  md5 028ec9a8...
rem ============================================================
set GAME=D:\SteamLibrary\steamapps\common\Obscure
set SRC=%~dp0version_clearflash8_baseline.dll

echo [1/2] verify clean source
if not exist "%SRC%" (
  echo [FAIL] baseline dll missing in this folder
  echo        alternative: run restore_baseline.bat in the STABLE BASELINE folder
  exit /b 1
)
call :chk "%SRC%" 028ec9a88266ba1526aeeabe930c16e6
if errorlevel 1 exit /b 1
echo       source OK

echo [2/2] restore game version.dll + verify
copy /Y "%SRC%" "%GAME%\version.dll" >nul
if errorlevel 1 (
  echo [FAIL] copy failed - close the game and retry
  exit /b 1
)
call :chk "%GAME%\version.dll" 028ec9a88266ba1526aeeabe930c16e6
if errorlevel 1 exit /b 1
echo.
echo [OK] rolled back to clearflash8 (stable baseline). Restart the game.
exit /b 0

:chk
set "TMPMD5="
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile "%~1" MD5') do if not defined TMPMD5 set "TMPMD5=%%H"
if /I "%TMPMD5%"=="%~2" exit /b 0
echo [FAIL] md5 mismatch: %~1
echo        got %TMPMD5%
exit /b 1
