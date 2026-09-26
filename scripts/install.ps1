$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$databasePath = Join-Path $projectRoot 'data\requirements.sqlite3'
$mutex = New-Object Threading.Mutex($false, 'Local\RequirementsAgent-Workbench-8765')
$locked = $false
try {
try { $locked = $mutex.WaitOne(30000) } catch [Threading.AbandonedMutexException] { $locked = $true }
if (-not $locked) { throw '另一个启动或安装操作正在进行，安装未修改运行文件。' }

function Assert-WorkbenchIdle {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue)
    if ($listeners.Count) { throw '8765 仍有监听服务；请完成备份并正常停止工作台后再安装，未替换任何运行文件。' }
    $pidPath = Join-Path $projectRoot 'data\server.pid'
    if (Test-Path -LiteralPath $pidPath) {
        $recordedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
        if (Get-Process -Id $recordedPid -ErrorAction SilentlyContinue) {
            throw '本项目 PID 记录仍指向运行进程；请核实后正常停服。'
        }
    }
    $checkerPython = if (Test-Path -LiteralPath $pythonPath) { $pythonPath } else { 'python' }
    & $checkerPython (Join-Path $PSScriptRoot 'check_active_tasks.py') $databasePath
    if ($LASTEXITCODE -ne 0) { throw '活动任务存在或无法核实任务状态；安装已拒绝。' }
}

Assert-WorkbenchIdle
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 虚拟环境创建失败' }
}
& $pythonPath -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败' }
& $pythonPath -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw '浏览器安装失败' }

$buildRoot = Join-Path $projectRoot ('evidence\install-build-' + [Guid]::NewGuid().ToString('N'))
$verifiedDist = Join-Path $buildRoot 'dist'
[IO.Directory]::CreateDirectory($buildRoot) | Out-Null
Push-Location (Join-Path $projectRoot 'web')
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
    npm.cmd run build -- --outDir $verifiedDist
    if ($LASTEXITCODE -ne 0) { throw '前端隔离构建失败' }
} finally { Pop-Location }
if (-not (Test-Path -LiteralPath (Join-Path $verifiedDist 'index.html')) -or
    -not (Test-Path -LiteralPath (Join-Path $verifiedDist 'assets'))) {
    throw '隔离构建缺少入口或资源，未替换 web/dist'
}

# Recheck immediately before touching the served build. No automatic stop or task cancellation.
Assert-WorkbenchIdle
$currentDist = Join-Path $projectRoot 'web\dist'
$previousDist = Join-Path $buildRoot 'previous-dist'
$movedOld = $false
try {
    if (Test-Path -LiteralPath $currentDist) {
        Move-Item -LiteralPath $currentDist -Destination $previousDist
        $movedOld = $true
    }
    Move-Item -LiteralPath $verifiedDist -Destination $currentDist
} catch {
    if ($movedOld -and -not (Test-Path -LiteralPath $currentDist)) {
        Move-Item -LiteralPath $previousDist -Destination $currentDist
    }
    throw
}
Write-Host '依赖与隔离前端构建完成；服务未运行时已更新 web/dist。'
if ($movedOld) { Write-Host "旧前端已保留：$previousDist" }
Write-Host '运行 scripts\start.ps1 启动唯一工作台。'
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
