$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$bash = Get-Command bash -ErrorAction SilentlyContinue
if (-not $bash) {
    throw "bash.exe が見つかりません。Git for WindowsをインストールしてPATHへ追加してください。"
}

Push-Location $root
try {
    & $bash.Source "scripts/run_ci_local_quiet.sh"
    $status = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $status
