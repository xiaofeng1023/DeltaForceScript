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
REM "net session" fails with errorlevel 5/2 for non-admin; it is available on every Windows.
REM The old cacls.exe probe + getadmin.vbs relaunch silently closed the window on machines
REM where Windows Script Host is restricted, which looked like a flash-close with no message.
net session >nul 2>&1
if errorlevel 1 (
    echo [env] administrator rights are required, relaunching with a UAC prompt ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs } catch { exit 1 }" >> "%~dp0start.log" 2>&1
    if errorlevel 1 (
        echo [error] failed to relaunch as administrator, details in start.log
        echo         Try: right-click start.bat and choose "Run as administrator".
        pause
    )
    exit /B
)
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "REQ=%~dp0requirement.txt"
set "LOG=%~dp0install.log"

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
    "%VENV_PY%" -m pip install -r "%REQ%" --log "%LOG%"
    if errorlevel 1 (
        REM PyPI or the network may be unstable in some regions: retry once via the tsinghua mirror
        echo [env] install failed, retrying with the tsinghua mirror ...
        "%VENV_PY%" -m pip install -r "%REQ%" --log "%LOG%" -i https://pypi.tuna.tsinghua.edu.cn/simple
        if errorlevel 1 (
            echo [error] dependency install failed. Full pip log: install.log
            echo         Send install.log if you need help.
            echo         Also make sure Python is 3.11; paddle wheels are version specific.
            echo         Manual retry:
            echo            "%~dp0.venv\Scripts\python.exe" -m pip install -r requirement.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
            pause
            exit /B 1
        )
    )
)

REM ---- 2.5) the GPU build is the default and it is NOT on PyPI: install it from the Paddle index ----
REM paddlepaddle-gpu 3.x only exists on the Paddle official index, so it cannot be put in
REM requirement.txt (pip -r would fail with "No matching distribution found").
REM The cu129 index tops out at 3.4.0 -- there is no 3.4.1, do not bump the version blindly.
"%VENV_PY%" -c "import importlib.metadata as m, sys; v=[d.version for d in m.distributions() if d.metadata.get('Name','').lower()=='paddlepaddle-gpu']; sys.exit(0 if v and v[0].startswith('3.4.0') else 1)"
if errorlevel 1 (
    echo [env] installing paddlepaddle-gpu 3.4.0 from the Paddle cu129 index, this is a large download ...
    "%VENV_PY%" -m pip uninstall -y paddlepaddle >nul 2>&1
    REM --extra-index-url (not -i): the GPU wheel comes from Paddle, its deps still come from PyPI
    "%VENV_PY%" -m pip install paddlepaddle-gpu==3.4.0 --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cu129/ --log "%LOG%"
    if errorlevel 1 (
        echo [error] paddlepaddle-gpu install failed. Full pip log: install.log
        echo         Needs an NVIDIA GPU with a matching CUDA driver, and cu129 must match it.
        echo         Other CUDA builds: cu130 also has 3.4.0; cu118 / cu126 top out at 3.3.1,
        echo         so use paddlepaddle-gpu==3.3.1 there instead of 3.4.0.
        echo         No NVIDIA GPU? Fall back to the CPU build:
        echo            "%~dp0.venv\Scripts\python.exe" -m pip install paddlepaddle==3.4.0 --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/
        pause
        exit /B 1
    )
)

REM ---- 3) launch the GUI ----
"%VENV_PY%" main_gui.py
pause
