# Build a single-file exe (portable Python inside, heavy deps installed on first run)
# Usage: powershell -ExecutionPolicy Bypass -File build_exe.ps1
# NOTE: keep this file ASCII-only. Windows PowerShell 5 reads .ps1 as ANSI and
#       mangles UTF-8 comments/strings, which breaks parsing.
param([switch]$NoInstall)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv not found: create .venv and install deps first" }

# 1) payload check: portable python + pip bootstrap
$zip = "packaging\python-3.11.9-embed-amd64.zip"
if (-not (Test-Path $zip)) {
    Write-Host "[build] downloading portable Python 3.11.9 ..."
    New-Item -ItemType Directory -Force -Path packaging | Out-Null
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" -OutFile $zip -UseBasicParsing
}
if (-not (Test-Path "packaging\bootstrap\pip")) {
    Write-Host "[build] preparing pip bootstrap ..."
    & $py -m pip install --target "packaging\bootstrap" --no-warn-script-location --upgrade pip setuptools wheel -i https://pypi.tuna.tsinghua.edu.cn/simple
}

# 2) collect the source files shipped inside the exe (never .venv / runtime)
$app = "packaging\app"
if (Test-Path $app) { Remove-Item $app -Recurse -Force }
New-Item -ItemType Directory -Force -Path $app | Out-Null
$files = @("main_gui.py", "page_flow.py", "gui_monitor.py", "window_capture.py",
           "region_selector.py", "requirement.txt", "regions_2k.json")
foreach ($f in $files) {
    if (Test-Path $f) { Copy-Item $f $app }
}
Write-Host ("[build] source copy: {0} files" -f (Get-ChildItem $app).Count)

# 3) install pyinstaller into .venv only
if (-not $NoInstall) {
    & $py -m pip install --upgrade pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
}

# 4) build the single-file exe
& $py -m PyInstaller --noconfirm --clean --onefile --console --name DeltaForceScript `
    --add-data "packaging\python-3.11.9-embed-amd64.zip;payload" `
    --add-data "packaging\bootstrap;payload\bootstrap" `
    --add-data "packaging\app;payload\app" `
    launcher.py

if (Test-Path "dist\DeltaForceScript.exe") {
    $mb = [math]::Round((Get-Item "dist\DeltaForceScript.exe").Length / 1MB, 1)
    Write-Host ("[build] done: dist\DeltaForceScript.exe ({0} MB)" -f $mb)
} else {
    throw "build failed, see the PyInstaller output above"
}
