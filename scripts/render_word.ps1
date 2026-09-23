param([Parameter(Mandatory=$true)][string]$DocumentPath,[Parameter(Mandatory=$true)][string]$OutputDirectory)
# Native Word page rendering fallback when LibreOffice is unavailable.
# Uses documented Page.EnhMetaFileBits; does not change security configuration.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$inputFile=(Resolve-Path -LiteralPath $DocumentPath).Path
$outputPath=[IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($outputPath) | Out-Null
$word=New-Object -ComObject Word.Application
$word.Visible=$false
$word.DisplayAlerts=0
try {
    $document=$word.Documents.Open($inputFile,$false,$true)
    try {
        $document.Repaginate()
        $window=$document.ActiveWindow
        $window.View.Type=3
        $pages=$window.Panes.Item(1).Pages
        for($index=1;$index -le $pages.Count;$index++) {
            [byte[]]$pageBytes=$pages.Item($index).EnhMetaFileBits
            $stream=[IO.MemoryStream]::new($pageBytes,$false)
            try {
                $metafile=[Drawing.Imaging.Metafile]::new($stream)
                try {
                    $bitmap=[Drawing.Bitmap]::new(1020,1320)
                    $graphics=[Drawing.Graphics]::FromImage($bitmap)
                    try {
                        $graphics.Clear([Drawing.Color]::White)
                        $graphics.DrawImage($metafile,[Drawing.Rectangle]::new(0,0,1020,1320))
                        $bitmap.Save((Join-Path $outputPath "page-$index.png"),[Drawing.Imaging.ImageFormat]::Png)
                    } finally { $graphics.Dispose(); $bitmap.Dispose() }
                } finally { $metafile.Dispose() }
            } finally { $stream.Dispose() }
        }
        Write-Host "Word rendered $($pages.Count) pages to $outputPath"
    } finally { $document.Close(0) }
} finally { $word.Quit(); [Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null }
