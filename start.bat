@echo off
REM ====================================================================
REM One-click launcher: UAC -> ensure .venv -> ensure deps INSIDE .venv -> run GUI
REM
REM Always check/install with .venv\Scripts\python.exe -m pip, never with a
REM bare python/pip: otherwise packages go into the global site-packages and
REM .venv still misses them (script then cannot start).
REM Messages are ASCII on purpose: a UTF-8 .bat gets mis-parsed by cmd when
REM the console codepage is GBK (Chinese bytes can even swallow nearby chars).
REM ====================================================================

REM ---- 0) request admin rights (mouse simulation needs elevation) ----
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~f0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    exit /B
)
if exist "%temp%\getadmin.vbs" ( del "%temp%\getadmin.vbs" )
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "REQ=%~dp0requirement.txt"

REM ---- 1) create .venv when missing (only this step uses the system python) ----
if not exist "%VENV_PY%" (
    echo [env] .venv not found, creating it ...
    python -m venv "%~dp0.venv" >nul 2>&1
    if not exist "%VENV_PY%" (
        py -3.11 -m venv "%~dp0.venv" >nul 2>&1
    )
    if not exist "%VENV_PY%" (
        echo [error] failed to create .venv.
        echo         Install Python 3.11 first and tick "Add python.exe to PATH".
        pause
        exit /B 1
    )
    echo [env] .venv created: %~dp0.venv
)

REM ---- 2) check dependencies with the VENV interpreter (find_spec, no import) ----
"%VENV_PY%" -c "import importlib.util as u, sys; mods=['cv2','numpy','PyQt6','dxcam','pydirectinput','win32api','PIL','paddle','paddleocr']; miss=[m for m in mods if u.find_spec(m) is None]; print('[env] missing: '+', '.join(miss)) if miss else print('[env] deps ok'); sys.exit(1 if miss else 0)"
if errorlevel 1 (
    echo [env] installing dependencies into .venv, please wait ...
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r "%REQ%"
    if errorlevel 1 (
        echo [error] dependency install failed, please check your network.
        echo         Retry with a mirror:
        echo         "%~dp0.venv\Scripts\python.exe" -m pip install -r requirement.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        pause
        exit /B 1
    )
)

REM ---- 3) launch the GUI ----
"%VENV_PY%" main_gui.py
pause
