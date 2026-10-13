param(
    [ValidateSet('Check', 'Write')][string]$Mode,
    [string]$RequestFile,
    [string]$OldConfig = '',
    [string]$RegisteredRoot = '',
    [string]$TargetFile = ''
)
$ErrorActionPreference = 'Stop'
$temporary = ''
function FullPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}
function PasswordValid([string]$Password) {
    # Rust counts Unicode scalar values rather than UTF-16 code units.
    $length = [regex]::Matches($Password, '[^\uDC00-\uDFFF]').Count
    return $length -ge 12 -and $length -le 256
}
function LanValid([string]$Value) {
    if (!$Value) { return $true }
    $parts = $Value.Split('.')
    if ($parts.Count -ne 4) { return $false }
    foreach ($part in $parts) {
        if ($part -notmatch '^(0|[1-9][0-9]{0,2})$' -or [int]$part -gt 255) { return $false }
    }
    return $parts[0] -eq '10' -or ($parts[0] -eq '172' -and [int]$parts[1] -ge 16 -and [int]$parts[1] -le 31) -or ($parts[0] -eq '192' -and $parts[1] -eq '168')
}
function PreviousPassword([string]$DataDirectory) {
    if (!$OldConfig -or !$RegisteredRoot) { return '' }
    try {
        $root = (FullPath $RegisteredRoot) + '\'
        $path = FullPath $OldConfig
        if (!$path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { return '' }
        $old = [IO.File]::ReadAllText($path) | ConvertFrom-Json
        if ($old -isnot [pscustomobject] -or $old.data_dir -isnot [string] -or $old.access_password -isnot [string]) { return '' }
        if (@($old.PSObject.Properties.Name | Where-Object { $_ -notin @('port', 'data_dir', 'lan_ip', 'access_password') }).Count) { return '' }
        if (($old.port -isnot [int] -and $old.port -isnot [long]) -or $old.port -lt 1024 -or $old.port -gt 65535) { return '' }
        if ($old.PSObject.Properties.Name -contains 'lan_ip') {
            if ($old.lan_ip -isnot [string] -or !(LanValid $old.lan_ip)) { return '' }
        }
        $oldData = $old.data_dir
        if (![IO.Path]::IsPathRooted($oldData)) { $oldData = [IO.Path]::Combine([IO.Path]::GetDirectoryName($path), $oldData) }
        if (!(FullPath $oldData).Equals((FullPath $DataDirectory), [StringComparison]::OrdinalIgnoreCase)) { return '' }
        if (!(PasswordValid $old.access_password)) { return '' }
        return $old.access_password
    } catch { return '' }
}
try {
    $request = [IO.File]::ReadAllText($RequestFile) | ConvertFrom-Json
    if ($request -isnot [pscustomobject] -or $request.data_dir -isnot [string] -or $request.lan_ip -isnot [string] -or $request.access_password -isnot [string]) { exit 20 }
    if (($request.port -isnot [int] -and $request.port -isnot [long]) -or $request.port -lt 1024 -or $request.port -gt 65535 -or !(LanValid $request.lan_ip)) { exit 20 }
    $password = $request.access_password
    if (!$password) { $password = PreviousPassword $request.data_dir }
    if (($request.lan_ip -or $password) -and !(PasswordValid $password)) { exit 10 }
    if ($Mode -eq 'Check') { exit 0 }
    $config = [ordered]@{ port = $request.port; data_dir = $request.data_dir; lan_ip = $request.lan_ip; access_password = $password }
    $target = [IO.Path]::GetFullPath($TargetFile)
    $temporary = $target + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::WriteAllText($temporary, ($config | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    # Same-directory replacement preserves a previous config if writing fails.
    if ([IO.File]::Exists($target)) { [IO.File]::Replace($temporary, $target, [Management.Automation.Language.NullString]::Value) }
    else { [IO.File]::Move($temporary, $target) }
    exit 0
} catch {
    # JSON exceptions can contain the password: report only a numeric status.
    exit 20
} finally {
    if ($temporary -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
}
