@echo off
setlocal
chcp 65001 > nul
title Полный запуск Яндекс.Музыки

set "SCRIPT_DIR=%~dp0"
set "PS1_FILE=%SCRIPT_DIR%start.ps1"

echo ============================================================
echo   Полный запуск Яндекс.Музыки
echo ============================================================
echo.

where pwsh > nul 2>&1
if "%ERRORLEVEL%"=="0" (
    pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1_FILE%"
) else (
    powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PS1_FILE%"
)
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ============================================================
if "%EXIT_CODE%"=="0" (
    echo Скрипт завершён.
) else (
    echo Скрипт завершён с кодом ошибки %EXIT_CODE%.
)
echo Нажмите любую клавишу для выхода.
pause > nul
exit /b %EXIT_CODE%
