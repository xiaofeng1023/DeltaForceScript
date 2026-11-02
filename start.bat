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

REM ---- 1.5) paddle wheels are python-version specific: warn when .venv python is not 3.10-3.12 ----
"%VENV_PY%" -c "import sys; v=sys.version_info; print('[env] python ' + sys.version.split()[0]); sys.exit(0 if 310 <= v[0]*100+v[1] <= 312 else 1)"
if errorlevel 1 (
    echo [warn] this project is built and tested on Python 3.11.
    echo        paddle wheels are python-version specific; if the install below fails,
    echo        delete .venv and recreate it with Python 3.11.
)

REM ---- 2) check dependencies with the VENV interpreter (find_spec, no import) ----
"%VENV_PY%" -c "import importlib.util as u, sys; mods=['cv2','numpy','PyQt6','dxcam','pydirectinput','win32api','PIL','paddle','paddleocr']; miss=[m for m in mods if u.find_spec(m) is None]; print('[env] missing: '+', '.join(miss)) if miss else print('[env] deps ok'); sys.exit(1 if miss else 0)"
if errorlevel 1 (
    echo [env] installing dependencies into .venv, please wait ...
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r "%REQ%"
    if errorlevel 1 (
        echo [error] dependency install failed.
        echo         1) Python must be 3.11; paddle wheels are version specific.
        echo         2) Slow network, retry with a mirror:
        echo            "%~dp0.venv\Scripts\python.exe" -m pip install -r requirement.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        pause
        exit /B 1
    )
)

REM ---- 2.5) the GPU build is the default and it is NOT on PyPI: install it from the Paddle index ----
REM paddlepaddle-gpu 3.x only exists on the Paddle official index, so it cannot be put in
REM requirement.txt (pip -r would fail with "No matching distribution found").
"%VENV_PY%" -c "import importlib.metadata as m, sys; v=[d.version for d in m.distributions() if d.metadata.get('Name','').lower()=='paddlepaddle-gpu']; sys.exit(0 if v and v[0].startswith('3.4.1') else 1)"
if errorlevel 1 (
    echo [env] installing paddlepaddle-gpu 3.4.1 from the Paddle cu129 index, this is a large download ...
    "%VENV_PY%" -m pip uninstall -y paddlepaddle >nul 2>&1
    "%VENV_PY%" -m pip install paddlepaddle-gpu==3.4.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu129/
    if errorlevel 1 (
        echo [error] paddlepaddle-gpu install failed.
        echo         Needs an NVIDIA GPU with a matching CUDA driver, and cu129 must match it.
        echo         Other CUDA builds: replace cu129 with cu118 / cu126 in the command above.
        echo         No NVIDIA GPU? Fall back to the CPU build:
        echo            "%~dp0.venv\Scripts\python.exe" -m pip install paddlepaddle==3.2.1
        pause
        exit /B 1
    )
)

REM ---- 3) launch the GUI ----
"%VENV_PY%" main_gui.py
pause
