$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
function Run-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker 命令失败：$($args[0])" }
}
$dest = Join-Path $PSScriptRoot ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $dest | Out-Null
Run-Docker compose stop gateway web worker
try {
    Run-Docker compose exec -T db pg_dump -U postgres -Fc -f /tmp/labwork-backup.dump labwork_prod
    $dbId = (& docker compose ps -q db).Trim()
    Run-Docker cp "${dbId}:/tmp/labwork-backup.dump" (Join-Path $dest 'database.dump')
    $compose = (& docker compose config --format json | ConvertFrom-Json)
    $runtimeVolume = $compose.volumes.runtime.name
    $backupContainer = 'labwork-backup-' + [guid]::NewGuid().ToString('N')
    try {
        Run-Docker create --name $backupContainer -v "${runtimeVolume}:/runtime:ro" python:3.12-slim python -c 'import shutil; shutil.make_archive("/tmp/runtime", "zip", "/runtime")'
        Run-Docker start -a $backupContainer
        Run-Docker cp "${backupContainer}:/tmp/runtime.zip" (Join-Path $dest 'runtime.zip')
    } finally {
        Run-Docker rm $backupContainer
    }
    Copy-Item -LiteralPath '.env' -Destination $dest
    Write-Host "一致性备份完成：$dest（含业务数据与凭据，请限制访问）"
} finally {
    Run-Docker compose start web worker gateway
}
