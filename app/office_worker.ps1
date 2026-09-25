param([string]$InputPath, [string]$OutputPath, [string]$OwnerPath, [string]$CleanupOwner)
# Open only a test-owned document in a new instance; never change Trust Center settings.
$ErrorActionPreference = 'Stop'
if ($CleanupOwner) {
    $owner = Get-Content -LiteralPath $CleanupOwner -Raw -Encoding UTF8 | ConvertFrom-Json
    $process = Get-Process -Id $owner.pid -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq $owner.name -and
        $process.StartTime.ToUniversalTime().Ticks.ToString() -eq $owner.started -and
        $owner.name -in @('WINWORD','EXCEL','POWERPNT')) { Stop-Process -Id $process.Id }
    exit
}
$rows = [Collections.Generic.List[object]]::new()
$limits = [Collections.Generic.List[string]]::new()
$charCount = 0
$app = $null; $document = $null; $owned = $false; $priorLinks = $null
$result = @{status='office_required'; reason='当前本机 Office 无法提取内容；请确认登录、激活和文件读取权限。'; rows=@(); code='OFFICE_OPEN_FAILED'}
function Add-Text([string]$location, [string]$value) {
    $value = $value.Trim([char]13,[char]7,[char]10,[char]0,' ')
    if (-not $value) { return }
    if ($script:rows.Count -ge 20000 -or $script:charCount + $value.Length -gt 2000000) { throw 'OFFICE_CONTENT_LIMIT' }
    $script:charCount += $value.Length
    $script:rows.Add(@($location,$value))
}
function Read-SlideShapes($shapes, [string]$where) {
    for ($i=1; $i -le $shapes.Count; $i++) {
        $shape=$shapes.Item($i); $loc="$where / 对象 $i"
        if ($shape.Type -eq 6) { Read-SlideShapes $shape.GroupItems $loc }
        elseif ($shape.HasTable -eq -1) {
            $table=$shape.Table
            for ($r=1;$r -le $table.Rows.Count;$r++) {
                for ($c=1;$c -le $table.Columns.Count;$c++) { Add-Text "$loc / 表格 R${r}C${c}" $table.Cell($r,$c).Shape.TextFrame.TextRange.Text }
            }
        } elseif ($shape.HasTextFrame -eq -1 -and $shape.TextFrame.HasText -eq -1) {
            Add-Text $loc $shape.TextFrame.TextRange.Text
        } else { $script:limits.Add("$loc 的图片、图表或其他非文本对象未提取") }
    }
}
try {
    $extension=[IO.Path]::GetExtension($InputPath).ToLowerInvariant()
    $kind=switch ($extension) { {$_ -in '.doc','.docx'} {'Word'} {$_ -in '.xls','.xlsx'} {'Excel'} {$_ -in '.ppt','.pptx'} {'PowerPoint'} default {throw 'UNSUPPORTED'} }
    $processName=@{Word='WINWORD';Excel='EXCEL';PowerPoint='POWERPNT'}[$kind]
    $before=@(Get-Process -Name $processName -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
    # PowerPoint can reuse the user's singleton. Do not touch it, including its security settings.
    if ($kind -eq 'PowerPoint' -and $before.Count) { $result.reason='PowerPoint 正在使用中；请保存并自行关闭 PowerPoint 后重试本机读取。'; throw 'OFFICE_BUSY' }
    $app=New-Object -ComObject "$kind.Application"
    $newProcesses=@(Get-Process -Name $processName -ErrorAction SilentlyContinue | Where-Object { $before -notcontains $_.Id })
    if ($newProcesses.Count -ne 1) { throw 'OFFICE_BUSY' }
    $appPid=$newProcesses[0].Id
    $openCount=switch ($kind) { 'Word' {$app.Documents.Count} 'Excel' {$app.Workbooks.Count} 'PowerPoint' {$app.Presentations.Count} }
    if ($openCount -ne 0) { throw 'OFFICE_BUSY' }
    $owned=$true
    $process=Get-Process -Id $appPid
    @{pid=$appPid; name=$process.ProcessName; started=$process.StartTime.ToUniversalTime().Ticks.ToString()} | ConvertTo-Json | Set-Content -LiteralPath $OwnerPath -Encoding UTF8
    $app.AutomationSecurity=3
    switch ($kind) {
        'Word' {
            $app.Visible=$false; $app.DisplayAlerts=0
            $priorLinks=$app.Options.UpdateLinksAtOpen
            $app.Options.UpdateLinksAtOpen=$false
            $document=$app.Documents.Open($InputPath,$false,$true,$false,'','',$false,'','',0,0,$false,$false)
            if (-not $document.ReadOnly) { throw 'OFFICE_NOT_READONLY' }
            $n=0
            foreach ($paragraph in $document.Paragraphs) {
                $n++
                if (-not $paragraph.Range.Information(12)) { Add-Text "正文段落 $n" $paragraph.Range.Text }
            }
            $t=0
            foreach ($table in $document.Tables) {
                $t++; $cellIndex=0
                foreach ($cell in $table.Range.Cells) {
                    $cellIndex++
                    Add-Text "表格 $t / 单元格 $cellIndex / 行 $($cell.RowIndex) 列 $($cell.ColumnIndex)" $cell.Range.Text
                }
            }
            $limits.Add("正文和表格已提取；$($document.InlineShapes.Count) 个内嵌对象和 $($document.Shapes.Count) 个浮动对象未作视觉识别；页眉页脚、批注和修订未单独提取")
            $document.Close(0); $document=$null
        }
        'Excel' {
            $app.Visible=$false; $app.DisplayAlerts=$false; $app.EnableEvents=$false
            $app.AskToUpdateLinks=$false
            # UpdateLinks=0, ReadOnly=true. Do not call RefreshAll/UpdateLink/Calculate.
            $document=$app.Workbooks.Open($InputPath,0,$true,[Type]::Missing,'','',$true,[Type]::Missing,[Type]::Missing,$false,$false,[Type]::Missing,$false)
            if (-not $document.ReadOnly) { throw 'OFFICE_NOT_READONLY' }
            foreach ($sheet in $document.Worksheets) {
                $range=$sheet.UsedRange
                if ([double]$range.CountLarge -gt 100000) { $limits.Add("工作表「$($sheet.Name)」区域过大，未提取"); continue }
                foreach ($cell in $range.Cells) {
                    $location="工作表「$($sheet.Name)」 / $($cell.Address($false,$false))"
                    if ($cell.HasFormula) { Add-Text $location ("公式（未计算、未更新外链）："+[string]$cell.Formula+"；当前显示："+[string]$cell.Text) }
                    else { Add-Text $location ([string]$cell.Text) }
                }
            }
            $limits.Add('单元格显示文字及公式已提取；公式显示结果未独立核验，图片、图表、批注和其他对象未提取')
            $document.Close($false); $document=$null
        }
        'PowerPoint' {
            $app.DisplayAlerts=1
            $document=$app.Presentations.Open($InputPath,-1,0,0)
            if ($document.ReadOnly -ne -1) { throw 'OFFICE_NOT_READONLY' }
            foreach ($slide in $document.Slides) { Read-SlideShapes $slide.Shapes "幻灯片 $($slide.SlideIndex)" }
            $limits.Add('幻灯片文本和表格已提取；图片、图表、动画、备注及其他对象未完整提取；未执行链接更新')
            $document.Close(); $document=$null
        }
    }
    if ($rows.Count) {
        $result=@{status='partial'; reason=($limits | Select-Object -Unique) -join '；'; rows=@($rows.ToArray()); code='OFFICE_EXTRACTED'; office=$kind; method='local-office'; text_extraction='extracted'; structure_extraction='partial'; visual_review='not_run'; manual_open='not_verified'}
    } else { $result.reason='本机 Office 已打开文件，但没有取得可用正文或表格；图片等对象尚未识别。'; $result.code='OFFICE_EMPTY' }
} catch {
    $code=$_.Exception.HResult
    if ($code -eq -2147024891 -or $_.Exception.Message -match '(?i)access.*denied|permission|权限|拒绝访问') {
        $result.status='permission_denied'; $result.code='OFFICE_PERMISSION_DENIED'; $result.reason='本机 Office 或企业策略拒绝提取；请由有权限的人员检查。原件保留。'
    } elseif ($_.Exception.Message -match 'OFFICE_BUSY') { $result.code='OFFICE_BUSY' }
    elseif ($_.Exception.Message -match 'OFFICE_CONTENT_LIMIT') { $result.code='OFFICE_CONTENT_LIMIT'; $result.reason='内容超过本机提取上限，未标记为已读取；请拆分材料。' }
    elseif ($code -eq -2147221164) { $result.code='OFFICE_UNAVAILABLE'; $result.reason='未安装或未注册对应的 Microsoft Office 应用，当前读取方式无法解析。' }
    # Do not export COM exception text, document content, user identity or credentials.
    $result.hresult=$code
    $result.operation_line=$_.InvocationInfo.ScriptLineNumber
} finally {
    if ($owned) {
        try {
            if ($document) {
                if ($kind -eq 'Word') { $document.Close(0) }
                elseif ($kind -eq 'Excel') { $document.Close($false) }
                else { $document.Close() }
            }
        } catch {}
        if ($kind -eq 'Word' -and $null -ne $priorLinks) { try { $app.Options.UpdateLinksAtOpen=$priorLinks } catch {} }
        try { $app.Quit() } catch {}
    }
    if ($app) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($app) }
    $result | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath $OutputPath -Encoding UTF8
}
