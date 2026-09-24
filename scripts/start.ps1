param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '请先运行 scripts\install.ps1' }
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'web\dist\index.html'))) { throw '请先构建前端' }
$dataPath = Join-Path $projectRoot 'data'
[IO.Directory]::CreateDirectory($dataPath) | Out-Null
$pidPath = Join-Path $dataPath 'server.pid'
if (Test-Path -LiteralPath $pidPath) {
    $priorPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    if (Get-Process -Id $priorPid -ErrorAction SilentlyContinue) { throw "端口 $Port 可能已被 PID $priorPid 占用（PID 文件指向正在运行的进程，可能是本项目已在运行的服务）；请用 scripts\diagnose.ps1 检查，或确认后手动停止该进程。本脚本不会自动结束任何进程。" }
}
$tokenBytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($tokenBytes) } finally { $rng.Dispose() }
$accessToken = [Convert]::ToBase64String($tokenBytes)
$env:RA_ACCESS_TOKEN = $accessToken
$env:RA_PORT = [string]$Port
$env:PYTHONUTF8 = '1'
try {
    $emptyInput=Join-Path $dataPath 'server.stdin'
    [IO.File]::WriteAllText($emptyInput,'')
    $process = Start-Process -FilePath $pythonPath -ArgumentList @('-u', ('"' + (Join-Path $PSScriptRoot 'serve.py') + '"')) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardInput $emptyInput -RedirectStandardOutput (Join-Path $dataPath 'server.stdout.log') -RedirectStandardError (Join-Path $dataPath 'server.stderr.log')
} finally { Remove-Item Env:RA_ACCESS_TOKEN; Remove-Item Env:RA_PORT }
[IO.File]::WriteAllText($pidPath, [string]$process.Id)
Start-Sleep -Seconds 2
if ($process.HasExited) { throw '服务启动失败，请查看 data\server.stderr.log' }
Write-Host "入口：http://127.0.0.1:$Port"
Write-Host "本机访问令牌：$accessToken"
Write-Host '请复制访问令牌登录。模型 API Key 可从项目根目录 .env 自动读取；在模型设置查看来源与接收端。修改 .env 后重启。'
