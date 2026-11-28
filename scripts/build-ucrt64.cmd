@echo off
rem ---------------------------------------------------------------------------
rem  Запускает scripts\build-ucrt64.sh в окружении MSYS2 UCRT64.
rem  Аргументы не передаются; для тонкой настройки открой «MSYS2 UCRT64»
rem  и запусти скрипт там с нужными переменными (DEPS, TMUF, FFMPEG, INSTALL...).
rem ---------------------------------------------------------------------------
setlocal

set "MSYS2_ROOT=C:\msys64"
if not exist "%MSYS2_ROOT%\usr\bin\bash.exe" (
  echo [oshibka] MSYS2 ne naiden v %MSYS2_ROOT%.
  echo Ustanovi MSYS2 s https://www.msys2.org/ ili poprav MSYS2_ROOT v etom fayle.
  pause
  exit /b 1
)

set "MSYSTEM=UCRT64"
set "CHERE_INVOKING=1"
set "FT_ROOT=%~dp0.."

"%MSYS2_ROOT%\usr\bin\bash.exe" -lc "cd \"$(cygpath -u \"$FT_ROOT\")\" && ./scripts/build-ucrt64.sh"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo [i] Sborka ne udalas. Otkroy terminal "MSYS2 UCRT64" i zapusti vruchnuyu:
  echo       cd "%FT_ROOT%"
  echo       ./scripts/build-ucrt64.sh
  echo   Tak log budet vidno polnostyu - prishli ego, razberyom.
  pause
)
exit /b %RC%
