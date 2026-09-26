param(
    [Parameter(Mandatory = $true)][string]$DocumentPath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$source = (Resolve-Path -LiteralPath $DocumentPath -ErrorAction Stop).Path
if ([IO.Path]::GetExtension($source).ToLowerInvariant() -ne '.docx') { throw 'DOCX_REQUIRED' }
$destination = [IO.Path]::GetFullPath($OutputDirectory)
if ([IO.Directory]::Exists($destination) -and @(Get-ChildItem -LiteralPath $destination -Force).Count -gt 0) {
    throw 'OUTPUT_DIRECTORY_NOT_EMPTY'
}
[IO.Directory]::CreateDirectory($destination) | Out-Null

$before = @(Get-Process -Name WINWORD -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$word = $null
$document = $null
$ownedId = 0
$ownedTicks = 0
$proven = $false
try {
    $word = New-Object -ComObject Word.Application
    $created = @(Get-Process -Name WINWORD -ErrorAction SilentlyContinue | Where-Object { $before -notcontains $_.Id })
    if ($created.Count -ne 1) {
        throw 'OWN_WORD_INSTANCE_NOT_PROVEN'
    }
    $ownedId = [int]$created[0].Id
    $ownedTicks = $created[0].StartTime.ToUniversalTime().Ticks
    if ($word.Documents.Count -ne 0) { throw 'WORD_INSTANCE_NOT_EMPTY' }
    $proven = $true
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $word.Options.UpdateLinksAtOpen = $false
    $document = $word.Documents.Open($source, $false, $true)
    if (-not $document.ReadOnly) { throw 'DOCUMENT_NOT_READ_ONLY' }
    $document.Repaginate()
    $window = $document.ActiveWindow
    $window.View.Type = 3
    $pages = $window.Panes.Item(1).Pages
    $count = $pages.Count
    if ($count -lt 1) { throw 'WORD_NO_PAGES' }
    for ($number = 1; $number -le $count; $number++) {
        [byte[]]$bytes = $pages.Item($number).EnhMetaFileBits
        if ($bytes.Length -eq 0) { throw "WORD_EMPTY_PAGE_$number" }
        $stream = [IO.MemoryStream]::new($bytes, $false)
        try {
            $metafile = [Drawing.Imaging.Metafile]::new($stream)
            try {
                $bitmap = [Drawing.Bitmap]::new(1700, 2200)
                $graphics = [Drawing.Graphics]::FromImage($bitmap)
                try {
                    $graphics.Clear([Drawing.Color]::White)
                    $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                    $graphics.DrawImage($metafile, [Drawing.Rectangle]::new(0, 0, 1700, 2200))
                    $bitmap.Save((Join-Path $destination ('page-{0:D3}.png' -f $number)), [Drawing.Imaging.ImageFormat]::Png)
                } finally { $graphics.Dispose(); $bitmap.Dispose() }
            } finally { $metafile.Dispose() }
        } finally { $stream.Dispose() }
    }
    Write-Output ('page_count=' + $count)
    Write-Output ('output_directory=' + $destination)
    Write-Output ('owned_word_pid=' + $ownedId)
} finally {
    if ($document) {
        try { $document.Close(0) } finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($document) }
    }
    if ($word) {
        try {
            $current = Get-Process -Id $ownedId -ErrorAction SilentlyContinue
            if ($proven -and $current -and $current.ProcessName -eq 'WINWORD' -and
                $current.StartTime.ToUniversalTime().Ticks -eq $ownedTicks -and $word.Documents.Count -eq 0) {
                $word.Quit()
            }
        } finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word) }
    }
}
