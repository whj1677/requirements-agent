param([ValidateSet(8765)][int]$Port = 8765, [switch]$NoAuth, [switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$scriptPath = Join-Path $PSScriptRoot 'serve.py'
$url = 'http://127.0.0.1:8765/'
# Serialize double-clicks across check/start/PID recording.
$mutex = New-Object Threading.Mutex($false, 'Local\RequirementsAgent-Workbench-8765')
$locked = $false
try {
    try { $locked = $mutex.WaitOne(30000) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw '另一个启动操作正在进行，请稍后重试。' }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    if ($listeners.Count) {
        foreach ($listener in $listeners) {
            $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
            if (-not $owner -or -not $owner.CommandLine.Contains(('"' + $scriptPath + '"'))) {
                throw '8765 被其他进程或另一份工程占用。请先核对并停止旧入口；本脚本不会另开工作台或结束未知进程。'
            }
        }
        Write-Host "工作台已在运行，复用入口：$url"
        Write-Host '现有登录方式与数据不变；修改启动参数需先停止服务。'
        if ($OpenBrowser) { Start-Process $url }
        return
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先运行 scripts\install.ps1' }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'web\dist\index.html'))) { throw '请先构建前端' }
    $dataPath = Join-Path $projectRoot 'data'
    [IO.Directory]::CreateDirectory($dataPath) | Out-Null
    $pidPath = Join-Path $dataPath 'server.pid'
    if (Test-Path -LiteralPath $pidPath) {
        $priorPid = [int](Get-Content -LiteralPath $pidPath -Raw)
        if (Get-Process -Id $priorPid -ErrorAction SilentlyContinue) { throw 'PID 记录仍指向运行中的进程，请先运行 scripts\stop.ps1。' }
    }
    $savedEnvironment = @{}
    foreach ($name in @('RA_ACCESS_TOKEN','RA_PORT','RA_DATA_DIR','PYTHONUTF8')) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    try {
        if ($NoAuth) {
            $env:RA_ACCESS_TOKEN = 'off'
        } else {
            $tokenBytes = New-Object byte[] 32
            $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($tokenBytes) } finally { $rng.Dispose() }
            $accessToken = [Convert]::ToBase64String($tokenBytes)
            $env:RA_ACCESS_TOKEN = $accessToken
        }
        $env:RA_PORT = '8765'
        $env:RA_DATA_DIR = $dataPath
        $env:PYTHONUTF8 = '1'
        $emptyInput = Join-Path $dataPath 'server.stdin'
        [IO.File]::WriteAllText($emptyInput, '')
        $process = Start-Process -FilePath $pythonPath -ArgumentList @('-u', ('"' + $scriptPath + '"')) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardInput $emptyInput -RedirectStandardOutput (Join-Path $dataPath 'server.stdout.log') -RedirectStandardError (Join-Path $dataPath 'server.stderr.log')
        [IO.File]::WriteAllText($pidPath, [string]$process.Id)
        $ready = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            $process.Refresh()
            if ($process.HasExited) { throw '服务启动失败，请查看 data\server.stderr.log' }
            try {
                $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 1
                if ($response.StatusCode -eq 200) { $ready = $true; break }
            } catch { }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw '服务尚未就绪，请运行 scripts\diagnose.ps1；不会另开实例。' }
    } finally {
        foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
    }
    Write-Host "唯一工作台入口：$url"
    if ($NoAuth) { Write-Host '本机无令牌模式，仅监听 127.0.0.1。' }
    else { Write-Host "本机访问令牌：$accessToken" }
    if ($OpenBrowser) { Start-Process $url }
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
