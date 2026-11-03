# -*- coding: utf-8 -*-
"""DeltaForceScript 启动器（打包成 exe 的入口）

exe 里带的是「环境」，不是「依赖」：
  - 便携版 Python 3.11（官方 embeddable 包）
  - pip / setuptools / wheel（首次装依赖时要用）
  - 项目源码副本（首次运行解压到 exe 所在目录）
业务依赖（PyQt6 / dxcam / paddleocr / paddlepaddle-gpu ...）体积很大，不打进 exe，
第一次运行时装进 <exe目录>\\runtime\\python\\Lib\\site-packages，之后每次启动直接复用。

用法:
  DeltaForceScript.exe             启动界面（首次运行会自动装依赖）
  DeltaForceScript.exe --check     只做环境自检，不安装、不启动
  DeltaForceScript.exe --reinstall 忽略已装状态，强制重装依赖
"""

import ctypes
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

APP_NAME = "DeltaForceScript"
PY_ZIP = "python-3.11.9-embed-amd64.zip"
PY_DIR_NAME = "python"
PTH_NAME = "python311._pth"

# 需要随 exe 一起带、首次解压到 exe 目录的文件
SOURCE_FILES = [
    "main_gui.py", "page_flow.py", "gui_monitor.py", "window_capture.py",
    "region_selector.py", "requirement.txt", "regions_2k.json", "运行步骤.txt",
]

# 依赖探测：只用 find_spec，不真的 import（import paddle 要好几秒）
CHECK_CODE = (
    "import importlib.util as u, sys;"
    "mods=['cv2','numpy','PyQt6','dxcam','pydirectinput','win32api','PIL','paddle','paddleocr'];"
    "miss=[m for m in mods if u.find_spec(m) is None];"
    "print('missing: '+', '.join(miss)) if miss else print('deps ok');"
    "sys.exit(1 if miss else 0)"
)

GPU_CHECK_CODE = (
    "import importlib.metadata as m, sys;"
    "v=[d.version for d in m.distributions() "
    "if d.metadata.get('Name','').lower()=='paddlepaddle-gpu'];"
    "print('paddlepaddle-gpu '+(v[0] if v else 'absent'));"
    "sys.exit(0 if v and v[0].startswith('3.4') else 1)"
)

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
GPU_INDEX = "https://www.paddlepaddle.org.cn/packages/stable/cu129/"
CPU_INDEX = "https://www.paddlepaddle.org.cn/packages/stable/cpu/"


def setup_console():
    """让控制台按 UTF-8 显示中文

    PyInstaller 打包后的进程默认按系统代码页（中文 Windows 是 GBK）输出，
    而源码里的中文是 UTF-8，两个编码错开就会满屏乱码。这里统一改成 UTF-8：
    既把控制台代码页切到 65001，也让 sys.stdout 按 UTF-8 写。
    """
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def child_env():
    """子进程（pip / 界面）同样按 UTF-8 输出，避免日志乱码"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def is_frozen():
    return getattr(sys, "frozen", False)


def app_dir():
    """exe 所在目录（开发模式下就是项目目录）"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def payload_dir():
    """打包进 exe 的物料目录"""
    if is_frozen():
        return Path(sys._MEIPASS) / "payload"
    return Path(__file__).resolve().parent / "packaging"


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin():
    """用 UAC 重新拉起自己（模拟鼠标需要管理员权限）"""
    params = " ".join(f'"{a}"' if " " in a else a for a in sys.argv[1:])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, str(app_dir()), 1)
        return rc > 32
    except Exception:
        return False


def copy_sources():
    """把 exe 里的源码副本解压到 exe 目录（已存在就不覆盖，避免冲掉用户改动）"""
    src = payload_dir() / "app"
    if not src.exists():
        return
    for name in SOURCE_FILES:
        target = app_dir() / name
        if not target.exists() and (src / name).exists():
            shutil.copy2(src / name, target)


def ensure_runtime():
    """首次运行解压便携 Python 与 pip，返回 python.exe 路径"""
    root = app_dir() / "runtime"
    py = root / PY_DIR_NAME / "python.exe"
    if py.exists():
        return py

    print("[env] 首次运行，正在解压内置的 Python 环境（只需一次）...")
    zip_path = payload_dir() / PY_ZIP
    if not zip_path.exists():
        raise SystemExit(f"[error] 缺少 {PY_ZIP}，exe 打包不完整")
    py_dir = root / PY_DIR_NAME
    py_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(py_dir)

    site = py_dir / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    # embeddable 版默认把 site-packages 关掉，这里改成显式放开
    (py_dir / PTH_NAME).write_text(
        "python311.zip\n.\nLib\\site-packages\nimport site\n", encoding="ascii")

    boot = payload_dir() / "bootstrap"
    if boot.exists():
        shutil.copytree(boot, site, dirs_exist_ok=True)
    print(f"[env] Python 环境就绪: {py}")
    return py


class Tee:
    """同时写控制台和 install.log，方便用户回传日志"""

    def __init__(self, path):
        self.file = open(path, "a", encoding="utf-8")

    def write(self, line):
        print(line, end="" if line.endswith("\n") else "\n")
        self.file.write(line if line.endswith("\n") else line + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


def run_py(py, args, tee=None):
    proc = subprocess.Popen([str(py)] + args,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            env=child_env())
    lines = []
    for line in proc.stdout:
        if tee:
            tee.write(line.rstrip("\n"))
        else:
            print(line, end="")
        lines.append(line)
    proc.wait()
    return proc.returncode, "".join(lines)


def pip_install(py, args, tee):
    print(f"\n[env] pip install {' '.join(args)}")
    return run_py(py, ["-m", "pip", "install"] + args, tee)[0]


def install_deps(py, tee):
    """第一次运行时装依赖：先 PyPI 依赖，再 Paddle 官方源的 GPU 版"""
    req = app_dir() / "requirement.txt"
    run_py(py, ["-m", "pip", "install", "--upgrade", "pip"], tee)

    rc = pip_install(py, ["-r", str(req), "--log", str(app_dir() / "install.log")], tee)
    if rc != 0:
        print("[env] 安装失败，改用清华镜像重试 ...")
        rc = pip_install(py, ["-r", str(req),
                              "--log", str(app_dir() / "install.log"),
                              "-i", MIRROR], tee)
    if rc != 0:
        return False

    # paddleocr 会顺带装 CPU 版 paddlepaddle，装 GPU 版前先卸掉，否则会加载到 CPU 版
    rc, _ = run_py(py, ["-m", "pip", "uninstall", "-y", "paddlepaddle"], tee)
    print("[env] 正在安装 paddlepaddle-gpu 3.4.0（体积很大，请耐心等待）...")
    rc = pip_install(py, ["paddlepaddle-gpu==3.4.0",
                          "--extra-index-url", GPU_INDEX,
                          "--log", str(app_dir() / "install.log")], tee)
    if rc != 0:
        print("[error] GPU 版安装失败（需要 NVIDIA 显卡与匹配的 CUDA 驱动）")
        print("[env] 回退 CPU 版：速度稍慢，但不需要显卡")
        rc = pip_install(py, ["paddlepaddle==3.4.0",
                              "--extra-index-url", CPU_INDEX,
                              "--log", str(app_dir() / "install.log")], tee)
        if rc != 0:
            return False
    return True


def main():
    setup_console()
    args = set(sys.argv[1:])
    check_only = "--check" in args
    force = "--reinstall" in args

    print(f"== {APP_NAME} 启动器 ==")
    print(f"[env] 程序目录: {app_dir()}")

    # 模拟鼠标需要管理员权限；开发模式（非 exe）不强制，方便调试
    if is_frozen() and not check_only and not is_admin():
        print("[env] 需要管理员权限，正在请求 UAC ...")
        if relaunch_as_admin():
            return 0
        print("[error] 提权失败，请右键 exe 选择「以管理员身份运行」")
        return 1

    copy_sources()
    py = ensure_runtime()

    rc, _ = run_py(py, ["-c", "import sys; print('[env] python ' + sys.version.split()[0])"])
    rc, out = run_py(py, ["-c", CHECK_CODE])
    missing = rc != 0

    if check_only:
        rc2, out2 = run_py(py, ["-c", GPU_CHECK_CODE])
        print(f"[check] 依赖状态: {'缺少' if missing else '完整'}")
        print(f"[check] GPU 版状态: {out2.strip()}")
        print("[check] 自检结束（未安装任何依赖）")
        return 0

    if missing or force:
        print("[env] 依赖不完整，开始安装（第一次运行会下载约 1GB，请保持网络连接）")
        tee = Tee(app_dir() / "install.log")
        ok = install_deps(py, tee)
        tee.close()
        if not ok:
            print("[error] 依赖安装失败，完整日志见 install.log，请把该文件发给我们")
            input("按回车键退出 ...")
            return 1
        print("[env] 依赖安装完成")
    else:
        print("[env] 依赖已就绪，直接启动")

    print("[env] 启动界面 ...")
    proc = subprocess.Popen([str(py), "main_gui.py"], cwd=str(app_dir()),
                            env=child_env())
    rc = proc.wait()
    if rc != 0:
        print(f"[error] 界面异常退出（退出码 {rc}）")
    input("按回车键关闭窗口 ...")
    return rc


if __name__ == "__main__":
    sys.exit(main())
