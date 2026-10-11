param(
    [int]$Port = 8861,
    [string]$Scenario = 'pccoe_pawana_night',
    [ValidateSet('orchestrator', 'standalone', 'orchestrated')][string]$Mode = 'orchestrator',
    [int]$Seed = 42,
    [int]$Fleet = 20,
    [int]$Timeout = 600,
    [switch]$NoServer,        # connect to an already running server instead of starting one
    [switch]$RealDryRun       # print the DisruptionOps REST calls the tasks map to; sends nothing
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot  # repository root (scripts/..)
$python = Join-Path $root '.conda-drones\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python).Source }
if (-not $env:OPS_INTEGRATION_TOKEN) { $env:OPS_INTEGRATION_TOKEN = [guid]::NewGuid().ToString('N') }  # server and command center share it
$server = $null
Push-Location $root
try {
    if ($RealDryRun) { & $python -m swarm_ops.command_center --scenario $Scenario --real-dry-run; return }
    if (-not $NoServer) {
        $log = Join-Path $root ".cache\command-center\server-$Port.log"
        New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
        $server = Start-Process -FilePath $python -ArgumentList @('-u', '-m', 'swarm_ops.server', '--port', $Port, '--seed', $Seed, '--fleet', $Fleet) `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
        for ($i = 0; $i -lt 60; $i++) {
            try { Invoke-RestMethod "http://127.0.0.1:$Port/health" | Out-Null; break } catch { Start-Sleep -Milliseconds 500 }
        }
    }
    Write-Host "Viewer: http://localhost:$Port  (simulation only; the integration token stays in this process)"
    & $python -u -m swarm_ops.command_center --url "ws://127.0.0.1:$Port/ops/ws" --scenario $Scenario --mode $Mode --timeout $Timeout
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    Pop-Location
}
