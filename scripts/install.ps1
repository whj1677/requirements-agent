$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 虚拟环境创建失败' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败' }
& '.\.venv\Scripts\python.exe' -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw '浏览器安装失败' }
Push-Location web
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
} finally { Pop-Location }
Write-Host '本项目依赖与前端构建完成。运行 scripts\start.ps1 启动。'
