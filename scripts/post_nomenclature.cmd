@echo off
REM Запуск из cmd.exe — .ps1 иначе не выполняется. Корень проекта — родитель папки scripts.
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0post_nomenclature.ps1" %*
exit /b %ERRORLEVEL%
