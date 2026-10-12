$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
& '.\.venv\Scripts\python.exe' -c "import sys,sqlite3,json; from app.contracts import profile; from app.core import ROOT; from app.provider import Provider,DEFAULT; data=ROOT/'data'; provider=Provider(); dbpath=data/'requirements.sqlite3'; db=sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) if dbpath.exists() else None; settings=dict(db.execute('SELECT id,payload FROM settings')) if db else {}; print('Python',sys.version); print('SQLite',sqlite3.sqlite_version); print('PRD',profile('prd')['id']); print('MRD',profile('mrd')['id']); print('Data',data); print('Project env exists',provider.environment.path.is_file()); print('Model key status',{slot:provider.key_status(json.loads(settings[slot]) if slot in settings else DEFAULT) for slot in ('model','vision')}); db.close() if db else None"
if ($LASTEXITCODE -ne 0) { throw '依赖或参考文件诊断失败' }
if (Test-Path -LiteralPath 'data\requirements.sqlite3') {
    & '.\.venv\Scripts\python.exe' -c "import sqlite3; c=sqlite3.connect('data/requirements.sqlite3'); print('DB integrity',c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()"
}
Write-Host '前端构建存在：' (Test-Path -LiteralPath 'web\dist\index.html')
