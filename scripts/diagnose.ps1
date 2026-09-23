$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
& '.\.venv\Scripts\python.exe' -c "import sys,sqlite3; from app.contracts import profile; from app.core import DATA; from app.main import app; print('Python',sys.version); print('SQLite',sqlite3.sqlite_version); print('PRD',profile('prd')['id']); print('MRD',profile('mrd')['id']); print('Data',DATA); print('Project env path',app.state.provider.environment.path); print('Project env exists',app.state.provider.environment.path.is_file()); print('Model key status', {slot:app.state.provider.key_status(app.state.store.setting(slot) or __import__('app.provider',fromlist=['DEFAULT']).DEFAULT) for slot in ('model','vision')})"
if ($LASTEXITCODE -ne 0) { throw '依赖或参考文件诊断失败' }
if (Test-Path -LiteralPath 'data\requirements.sqlite3') {
    & '.\.venv\Scripts\python.exe' -c "import sqlite3; c=sqlite3.connect('data/requirements.sqlite3'); print('DB integrity',c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()"
}
Write-Host '前端构建存在：' (Test-Path -LiteralPath 'web\dist\index.html')
