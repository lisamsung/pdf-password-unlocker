$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
    python build_assets.py
    if ($LASTEXITCODE -ne 0) { throw '图标生成失败' }
    python collect_licenses.py
    if ($LASTEXITCODE -ne 0) { throw '许可证收集失败' }
    python -m PyInstaller --noconfirm --clean --onefile --windowed --name PDF密码解除器 --icon assets/app.ico --add-data 'assets;assets' --add-data 'licenses;licenses' --collect-data tkinterdnd2 --collect-data pikepdf --exclude-module numpy --exclude-module matplotlib --exclude-module pytest app.py
    if ($LASTEXITCODE -ne 0) { throw '打包失败' }
    Write-Host '完成：dist\PDF密码解除器.exe'
} finally {
    Pop-Location
}
