@echo off
rem Abre Bubble. La primera vez prepara todo solo: Python (si falta), el entorno y lo que Bubble necesita.
rem Lo demas (modelos de voz, voces, Claude Code) lo instala Bubble al abrirse, en la ventana "Preparar Bubble".
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\python.exe" -c "import bubble" >nul 2>nul && goto abrir
)
title Bubble - primera vez
echo.
echo   Preparando Bubble por primera vez (unos minutos, se descarga)...
echo.
rem Python 3.12 o mas nuevo, de 64 bits (el de 32 bits no puede usar la parte de voz).
set "CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 12) and sys.maxsize > 2**32 else 1)"
set "PY="
for %%V in (3.12 3.13 3.14) do (
    if not defined PY (
        py -%%V -c "%CHECK%" >nul 2>nul && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "%CHECK%" >nul 2>nul && set "PY=python"
)
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
if not defined PY (
    echo   Falta Python 3.12 de 64 bits ^(gratis^).
    choice /c SN /m "   Lo instalo ahora"
    if errorlevel 2 goto sin_python
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
)
if not defined PY goto sin_python
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || goto error
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -e ".[voz]" || goto error
:abrir
start "" ".venv\Scripts\pythonw.exe" -m bubble
exit /b 0
:sin_python
echo   Instala Python 3.12 de 64 bits desde python.org y volve a abrir Bubble.
start "" https://www.python.org/downloads/
pause
exit /b 1
:error
echo.
echo   No se pudo preparar Bubble. Revisa tu conexion a internet y volve a abrirlo.
echo   Si sigue fallando, escribinos desde Bubble (Ajustes, Soporte) o a juanmartindeza14@gmail.com.
pause
exit /b 1
