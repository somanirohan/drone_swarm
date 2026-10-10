# One command: stops old runs, starts the Jev-driven swarm + public tunnel, prints the link to share.
param([int]$Port = 8765, [switch]$NoJev)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)  # repository root (scripts/legacy/..)
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and ($_.CommandLine -like '*run-web.ps1*' -or $_.Name -in 'python.exe','cloudflared.exe') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$cf = Join-Path $root '.tools\cloudflared.exe'
if (-not (Test-Path $cf)) { throw 'cloudflared missing: re-download it to .tools (see legacy/pybullet/docs/WEB-GUIDE.md).' }
$log = Join-Path $root '.cache\tunnel.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
Remove-Item $log -ErrorAction SilentlyContinue
$tunnel = Start-Process $cf -ArgumentList 'tunnel', '--url', "http://localhost:$Port", '--protocol', 'http2', '--no-autoupdate', '--logfile', $log -PassThru -WindowStyle Hidden
$url = $null
for ($i = 0; $i -lt 40 -and -not $url; $i++) {
    Start-Sleep -Milliseconds 750
    if (Test-Path $log) { $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1; if ($m) { $url = $m.Matches[0].Value } }
}
if (-not $url) { throw "Tunnel did not start; see $log" }
Set-Content (Join-Path $root '.cache\public-url.txt') $url
$guide = Join-Path $root 'legacy\pybullet\docs\FRIEND-GUIDE.md'
if (Test-Path $guide) {
    $text = [IO.File]::ReadAllText($guide)
    [IO.File]::WriteAllText($guide, [regex]::Replace($text, 'https://[a-z0-9-]+\.trycloudflare\.com', $url))
}
Write-Host "`n=====================================================" -ForegroundColor Green
Write-Host " SEND THIS LINK TO YOUR FRIEND:  $url" -ForegroundColor Green
Write-Host " Your own view:                  http://localhost:$Port" -ForegroundColor Green
Write-Host "=====================================================`n" -ForegroundColor Green
try {
    if ($NoJev) { & (Join-Path $PSScriptRoot 'run-web.ps1') -Port $Port } else { & (Join-Path $PSScriptRoot 'run-web.ps1') -Port $Port -AI jev }
} finally {
    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force }
}
