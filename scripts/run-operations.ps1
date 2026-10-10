param([int]$Port = 8787, [int]$Seed = 42, [int]$Fleet = 20, [switch]$Tunnel)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot  # repository root (scripts/..)
$python = Join-Path $root '.conda-drones\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python).Source }
$world = Join-Path $root 'assets\operations\pune-world.json'
if (-not (Test-Path -LiteralPath $world)) { & $python (Join-Path $root 'tools\prepare_operations_map.py') }
$tunnelProcess = $null
Push-Location $root  # python -m swarm_ops.server needs the repository root on sys.path
try {
    if ($Tunnel) {
        $cloudflared = Join-Path $root '.tools\cloudflared.exe'
        if (-not (Test-Path -LiteralPath $cloudflared)) { $cloudflared = (Get-Command cloudflared).Source }
        $runtime = Join-Path $root '.cache\operations'
        New-Item -ItemType Directory -Force -Path $runtime | Out-Null
        $tunnelProcess = Start-Process -FilePath $cloudflared -ArgumentList @('tunnel','--no-autoupdate','--url',"http://127.0.0.1:$Port") -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $runtime 'tunnel.log') -RedirectStandardOutput (Join-Path $runtime 'tunnel.stdout.log')
        Write-Host "Public URL will appear in $runtime\tunnel.log. Public viewers are read-only."
    }
    & $python -u -m swarm_ops.server --port $Port --seed $Seed --fleet $Fleet
    if ($LASTEXITCODE -ne 0) { throw "Operations server exited with code $LASTEXITCODE" }
} finally {
    if ($tunnelProcess -and -not $tunnelProcess.HasExited) { Stop-Process -Id $tunnelProcess.Id }
    Pop-Location
}
