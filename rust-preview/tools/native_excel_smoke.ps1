param(
    [Parameter(Mandatory = $true)][string]$Manifest,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [ValidateSet(0, 1)][int]$CorruptLoad = 0,
    [switch]$SkipLayout
)
$ErrorActionPreference = 'Stop'
function Get-InputHash([string]$Path) {
    $stream = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $sha.Dispose(); $stream.Dispose() }
}
function Copy-InputFile([string]$Path, [string]$Destination) {
    $source = [System.IO.File]::Open($Path, 'Open', 'Read', 'ReadWrite')
    $destinationStream = [System.IO.File]::Open($Destination, 'Create', 'Write', 'None')
    try { $source.CopyTo($destinationStream) }
    finally { $destinationStream.Dispose(); $source.Dispose() }
}
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ExcelSmokeWindow {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
}
'@
$root = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $root | Out-Null
$inputs = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$before = @{}
$results = [System.Collections.Generic.List[object]]::new()
foreach ($item in $inputs) { $before[$item.path] = Get-InputHash $item.path }
$priorPids = @(Get-Process EXCEL -ErrorAction SilentlyContinue | ForEach-Object Id)
$excel = $null
$owned = $false
try {
    $excel = New-Object -ComObject Excel.Application
    [uint32]$excelPid = 0
    [void][ExcelSmokeWindow]::GetWindowThreadProcessId([IntPtr]$excel.Hwnd, [ref]$excelPid)
    if (!$excelPid -or $priorPids -contains [int]$excelPid) {
        throw 'Refusing to use an existing Excel process.'
    }
    $owned = $true
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.EnableEvents = $false
    $excel.AskToUpdateLinks = $false
    $excel.AutomationSecurity = 3
    Write-Output "Owned isolated Excel PID $excelPid; prior PIDs: $($priorPids -join ',')"
    foreach ($item in $inputs) {
        $book = $null
        if ($item.name -match '[\\/:]') { throw 'Copy names must be file names without a directory.' }
        $copy = Join-Path $root ($item.name + '.xlsx')
        if ([string]::Equals([System.IO.Path]::GetFullPath($item.path), $copy, [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing to overwrite an input file.' }
        Copy-InputFile $item.path $copy
        $result = [ordered]@{ name = $item.name; input = $item.path; copy = $copy; input_sha256 = $before[$item.path]; corrupt_load = $CorruptLoad; excel_pid = $excelPid; opened = $false; normal_open_no_repair = $false; repair_logs = @(); sheets = @() }
        try {
            Write-Output "Opening disposable $($item.name)"
            $priorRepairLogs = @{}
            Get-ChildItem -LiteralPath $env:TEMP -Filter 'error*.xml' -File | ForEach-Object { $priorRepairLogs[$_.FullName] = $_.LastWriteTimeUtc.Ticks }
            if ($CorruptLoad -eq 0) { $book = $excel.Workbooks.Open($copy, 0, $true) }
            else { $book = $excel.Workbooks.Open($copy, 0, $true, 5, '', '', $true, 2, '', $false, $false, 0, $false, $true, 1) }
            $result.opened = $true
            $result.excel_version = $excel.Version
            $result.repair_logs = @(Get-ChildItem -LiteralPath $env:TEMP -Filter 'error*.xml' -File | Where-Object {
                $priorRepairLogs[$_.FullName] -ne $_.LastWriteTimeUtc.Ticks -and
                (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8).Contains($copy)
            } | ForEach-Object Name)
            $result.normal_open_no_repair = $CorruptLoad -eq 0 -and !$result.repair_logs.Count
            foreach ($sheet in $book.Worksheets) {
                if ($SkipLayout) { $result.sheets += [ordered]@{ name = $sheet.Name }; continue }
                $sheet.Activate()
                $window = $excel.ActiveWindow
                $widths = @()
                for ($col = 1; $col -le 64; $col++) { $widths += [double]$sheet.Columns.Item($col).ColumnWidth }
                $heights = @()
                for ($row = 1; $row -le 40; $row++) { $heights += [double]$sheet.Rows.Item($row).RowHeight }
                $result.sheets += [ordered]@{
                    name = $sheet.Name; used_range = $sheet.UsedRange.Address();
                    freeze_panes = [bool]$window.FreezePanes; split_row = [double]$window.SplitRow; split_column = [double]$window.SplitColumn;
                    column_widths = $widths; row_heights = $heights;
                    a1_number_format = $sheet.Range('A1').NumberFormat; a4_number_format = $sheet.Range('A4').NumberFormat;
                    a4_value = $sheet.Range('A4').Value2; d4_number_format = $sheet.Range('D4').NumberFormat
                }
            }
            if ($CorruptLoad -eq 1) {
                $repaired = Join-Path $root ($item.name + '.repaired.xlsx')
                $book.SaveCopyAs($repaired)
                $result.repaired_copy = $repaired
            }
            Write-Output "Opened $($item.name), $($result.sheets.Count) sheets"
        }
        catch { $result.error = $_.Exception.Message; Write-Output "Failed $($item.name): $($result.error)" }
        finally {
            if ($null -ne $book) { $book.Close($false); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($book) }
        }
        $results.Add($result)
        $results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $root 'native-results.json') -Encoding UTF8
    }
}
finally {
    if ($owned) { $excel.Quit() }
    if ($null -ne $excel) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel) }
    $proof = foreach ($item in $inputs) {
        $after = Get-InputHash $item.path
        [ordered]@{ path = $item.path; before = $before[$item.path]; after = $after; unchanged = $before[$item.path] -eq $after }
    }
    $proof | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $root 'source-hash-proof.json') -Encoding UTF8
    if (@($proof | Where-Object { !$_.unchanged }).Count) { throw 'Input source changed during smoke check.' }
}
if (@($results | Where-Object { !$_.opened }).Count) { exit 1 }
