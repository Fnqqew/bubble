@echo off
rem Desinstala Bubble: elegis que se borra (configuracion, modelos, accesos directos, micro virtual, la carpeta).
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m bubble --desinstalar
) else (
    echo   Bubble no esta instalado en esta carpeta: podes borrarla directamente.
    pause
)
