param([switch]$Force)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pidPath = Join-Path $projectRoot 'data\server.pid'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Host '没有本项目 PID 记录'; exit 0 }
$serverPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$serverPid"
if ($serverProcess) {
    $expectedScript = Join-Path $PSScriptRoot 'serve.py'
    if (-not $serverProcess.CommandLine.Contains($expectedScript)) { throw 'PID 对应进程不是本项目服务，拒绝停止' }
    if (-not $Force) {
        $dbPath = Join-Path $projectRoot 'data\requirements.sqlite3'
        $projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
        $pythonCommand = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { 'python' }
        & $pythonCommand (Join-Path $PSScriptRoot 'check_active_tasks.py') $dbPath
        if ($LASTEXITCODE -ne 0) { throw '活动任务存在或检查失败，拒绝正常停服；确需中断请显式使用 -Force' }
    }
    # Windows venv python.exe starts a child interpreter. Verify its identity too.
    $children=Get-CimInstance Win32_Process -Filter "ParentProcessId=$serverPid"
    foreach($child in $children) {
        if ($child.Name -eq 'conhost.exe') { continue }
        if (-not $child.CommandLine.Contains($expectedScript)) { throw '发现非本项目子进程，拒绝批量停止' }
        Stop-Process -Id $child.ProcessId
    }
    Stop-Process -Id $serverPid
}
Remove-Item -LiteralPath $pidPath
Write-Host '本项目服务已停止。'
