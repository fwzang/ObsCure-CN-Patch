@echo off
setlocal enableextensions
rem ============================================================
rem  ObsCure hanhua - restore STABLE BASELINE V1.0
rem  clearflash8 hook + V1 shadow / V4b 20px ASCII atlas
rem  Verify package md5 -> backup current -> copy -> verify again
rem ============================================================
set GAME=D:\SteamLibrary\steamapps\common\Obscure
set SRC=%~dp0
set BAK=%GAME%\_obscure_backup\pre_baseline_restore

echo [1/4] verify package md5
call :chk "%SRC%version.dll" 028ec9a88266ba1526aeeabe930c16e6 version.dll
if errorlevel 1 goto :fail
call :chk "%SRC%dinput8.dll" b8c51891352e3e7bfc2c30f2c903b46c dinput8.dll
if errorlevel 1 goto :fail
call :chk "%SRC%big_atlas_c.raw" 99a1244494b42d9cb9572e92335720ff big_atlas_c.raw
if errorlevel 1 goto :fail
call :chk "%SRC%table_c.bin" 6a498e9d8478b48ff9b3dc0e29f5c183 table_c.bin
if errorlevel 1 goto :fail
echo       package OK

echo [2/4] backup current game files
if not exist "%BAK%" mkdir "%BAK%"
for %%F in (version.dll dinput8.dll big_atlas_c.raw table_c.bin) do (
  if exist "%GAME%\%%F" (
    copy /Y "%GAME%\%%F" "%BAK%\%%F" >nul
    if errorlevel 1 (
      echo [FAIL] backup %%F
      goto :fail
    )
  )
)
echo       backup OK

echo [3/4] install baseline files
for %%F in (version.dll dinput8.dll big_atlas_c.raw table_c.bin) do (
  copy /Y "%SRC%%%F" "%GAME%\%%F" >nul
  if errorlevel 1 (
    echo [FAIL] copy %%F
    goto :fail
  )
)

echo [4/4] verify installed md5
call :chk "%GAME%\version.dll" 028ec9a88266ba1526aeeabe930c16e6 version.dll
if errorlevel 1 goto :fail
call :chk "%GAME%\dinput8.dll" b8c51891352e3e7bfc2c30f2c903b46c dinput8.dll
if errorlevel 1 goto :fail
call :chk "%GAME%\big_atlas_c.raw" 99a1244494b42d9cb9572e92335720ff big_atlas_c.raw
if errorlevel 1 goto :fail
call :chk "%GAME%\table_c.bin" 6a498e9d8478b48ff9b3dc0e29f5c183 table_c.bin
if errorlevel 1 goto :fail

echo.
echo [OK] STABLE BASELINE V1.0 restored.
echo      Screen-switch black-until-text mode is ACTIVE. Restart the game.
exit /b 0

:chk
set "TMPMD5="
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile "%~1" MD5') do if not defined TMPMD5 set "TMPMD5=%%H"
if /I "%TMPMD5%"=="%~2" exit /b 0
echo [FAIL] md5 mismatch: %~3
echo        got %TMPMD5%
exit /b 1

:fail
echo.
echo [ABORTED] baseline NOT fully applied.
exit /b 1
