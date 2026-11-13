param(
    [Parameter(Mandatory = $true)][string]$DocumentPath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$RasterPython,
    [double]$TimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$officePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $officePython)) { throw 'PROJECT_PYTHON_REQUIRED' }
if (-not $RasterPython) { $RasterPython = $officePython }
$source = (Resolve-Path -LiteralPath $DocumentPath).Path
$destination = [IO.Path]::GetFullPath($OutputDirectory)
& $officePython -u (Join-Path $PSScriptRoot 'render_office_pages.py') --document $source --output $destination --raster-python $RasterPython --timeout $TimeoutSeconds
if ($LASTEXITCODE -ne 0) { throw ('DOCX_RENDER_FAILED: exit ' + $LASTEXITCODE) }
