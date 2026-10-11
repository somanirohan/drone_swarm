<#
.SYNOPSIS
  Start the swarm and verify a temporary Cloudflare URL for DisruptionOps.
.DESCRIPTION
  Reuses the private integration token and verifies HTTP and real WS permissions.
  Saves secrets under git-ignored .cache/share, never in the console or clipboard.
  Ctrl+C stops only processes started here; reused servers are left running.
.EXAMPLE
  .\scripts\launch-for-disruptionops.ps1 -Fleet 20
  .\scripts\launch-for-disruptionops.ps1 -World pcmc-region -Coordination decentralized
  .\scripts\launch-for-disruptionops.ps1 -Port 8879 -NoTunnel -VerifyOnly
#>
[CmdletBinding()]
param(
    [ValidateRange(1,65535)][int]$Port = 8787,
    [ValidateRange(1,100)][int]$Fleet = 20,
    [int]$Seed = 42,
    [ValidateSet('pune-district','pcmc-region')][string]$World = 'pune-district',
    [ValidateSet('central','decentralized','orchestrated')][string]$Coordination = 'central',
    [ValidateSet('external','internal')][string]$Orchestrator = 'external',
    [switch]$NoTunnel,
    [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$share = Join-Path $root '.cache\share'
$runDir = Join-Path $share ('launch-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID)
$owned = @()
$server = $null
$previousToken = $env:OPS_INTEGRATION_TOKEN
$ready = $false

function Find-Python {
    foreach ($relative in @('.conda-drones\python.exe','.venv-ops\Scripts\python.exe','.venv-swarm\Scripts\python.exe')) {
        $candidate = Join-Path $root $relative
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw 'Python not found. Run scripts\setup-ops.ps1 first.'
}

function Get-Health([string]$Base) {
    try {
        $result = Invoke-RestMethod -Uri "$Base/health" -TimeoutSec 5
        if ($result.ok -and $result.protocol -eq 'disruptionops.swarm.v2' -and $result.simulation_only) { return $result }
    } catch { }
    return $null
}

function Verify-Connection([string]$Base, [switch]$Public) {
    $verifyArgs = @((Join-Path $PSScriptRoot 'verify-swarm-connection.py'), '--base-url', $Base,
                    '--token-file', $tokenFile, '--orchestrator', $Orchestrator)
    if ($Public) { $verifyArgs += '--require-public' }
    $output = & $python @verifyArgs
    if ($LASTEXITCODE -ne 0) { throw "WebSocket verification failed for $Base. Check the integration token and server version." }
    return ($output | ConvertFrom-Json)
}

try {
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    $python = Find-Python
    & $python -c 'import sys,websockets; assert sys.version_info >= (3,12)'
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12+ and requirements\requirements-ops.txt (scripts\setup-ops.ps1).' }
    $cloudflared = $null
    if (-not $NoTunnel) {
        $cloudflared = Join-Path $root '.tools\cloudflared.exe'
        if (-not (Test-Path -LiteralPath $cloudflared)) {
            $command = Get-Command cloudflared -ErrorAction SilentlyContinue
            if (-not $command) { throw 'cloudflared not found: install it or place cloudflared.exe under .tools\.' }
            $cloudflared = $command.Source
        }
    }
    $tokenFile = Join-Path $share 'integration-token.txt'
    if (-not (Test-Path -LiteralPath $tokenFile)) {
        $bytes = New-Object byte[] 32
        $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
        $generated = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
        Set-Content -LiteralPath $tokenFile -Value $generated -Encoding ascii
    }
    $token = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
    if ($token.Length -lt 24 -or $token -match '[\r\n]') { throw 'The private integration token must be at least 24 characters on one line.' }
    $env:OPS_INTEGRATION_TOKEN = $token
    $local = "http://127.0.0.1:$Port"
    if (Get-Health $local) {
        Write-Host "Verifying existing swarm on port $Port; this launcher will not stop it."
    } else {
        Write-Host "Starting swarm: fleet $Fleet, $World, $Coordination."
        $server = Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden -PassThru `
            -ArgumentList @('-u','-m','swarm_ops.server','--host','127.0.0.1','--port',$Port,
                            '--seed',$Seed,'--fleet',$Fleet,'--world',$World,'--coordination',$Coordination) `
            -RedirectStandardOutput (Join-Path $runDir 'server.stdout.log') `
            -RedirectStandardError (Join-Path $runDir 'server.stderr.log')
        $owned += $server
        $deadline = (Get-Date).AddSeconds(60)
        while (-not (Get-Health $local)) {
            $server.Refresh()
            if ($server.HasExited) { throw "Server exited. Check $runDir\server.stderr.log" }
            if ((Get-Date) -ge $deadline) { throw "Server startup timed out. Check $runDir\server.stderr.log" }
            Start-Sleep -Milliseconds 500
        }
    }
    $localCheck = Verify-Connection $local
    if ($server -and ($localCheck.fleet -ne $Fleet -or $localCheck.coordination -ne $Coordination)) {
        throw 'Fresh server snapshot did not match the requested fleet/coordination.'
    }
    $session = Invoke-RestMethod -Uri "http://localhost:$Port/ops/session" -TimeoutSec 5
    if (-not $session.can_control -or -not $session.control_token) { throw 'Direct loopback operator session unavailable.' }
    $base = $local
    $tunnel = $null
    if (-not $NoTunnel) {
        $tunnelLog = Join-Path $runDir 'tunnel.stderr.log'
        $tunnel = Start-Process -FilePath $cloudflared -WorkingDirectory $root -WindowStyle Hidden -PassThru `
            -ArgumentList @('tunnel','--no-autoupdate','--protocol','http2','--url',$local) `
            -RedirectStandardOutput (Join-Path $runDir 'tunnel.stdout.log') -RedirectStandardError $tunnelLog
        $owned += $tunnel
        $base = $null
        $deadline = (Get-Date).AddSeconds(90)
        while (-not $base) {
            $tunnel.Refresh()
            if ($tunnel.HasExited) { throw "Cloudflare stopped. Check $tunnelLog" }
            if ((Get-Date) -ge $deadline) { throw "Cloudflare URL discovery timed out. Check $tunnelLog" }
            if (Test-Path -LiteralPath $tunnelLog) {
                $match = Select-String -LiteralPath $tunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
                if ($match) { $base = $match.Matches[0].Value }
            }
            if (-not $base) { Start-Sleep -Milliseconds 500 }
        }
        $deadline = (Get-Date).AddSeconds(90)
        while (-not (Get-Health $base)) {
            $tunnel.Refresh()
            if ($tunnel.HasExited) { throw "Cloudflare stopped. Check $tunnelLog" }
            if ((Get-Date) -ge $deadline) { throw 'Public health never became reachable; no ready files published.' }
            Start-Sleep -Seconds 2
        }
        $publicCheck = Verify-Connection $base -Public
        if ($publicCheck.run_id -ne $localCheck.run_id) { throw 'Public URL reached a different exercise; retry the launcher.' }
    }
    $ws = ($base -replace '^https:', 'wss:' -replace '^http:', 'ws:') + '/ops/ws'
    $envText = "SWARM_AGENCY=1`nSWARM_WS_URL=$ws`nSWARM_INTEGRATION_TOKEN=$token`n"
    Set-Content -LiteralPath (Join-Path $share 'disruptionops.env') -Value $envText -Encoding ascii
    Set-Content -LiteralPath (Join-Path $share 'friend-operator-link.txt') -Value "$base/#control=$($session.control_token)" -Encoding ascii
    $connections = [ordered]@{
        verified_at = (Get-Date).ToUniversalTime().ToString('o'); viewer = $base; websocket = $ws
        health = "$base/health"; protocol = $localCheck.protocol; run_id = $localCheck.run_id
        fleet = $localCheck.fleet; coordination = $localCheck.coordination; orchestrator = $Orchestrator
        launcher_pid = $PID; server_pid = $(if ($server) { $server.Id } else { $null })
        tunnel_pid = $(if ($tunnel) { $tunnel.Id } else { $null }); verify_only = [bool]$VerifyOnly
        log_directory = $runDir
    }
    $connections | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $share 'connection.json') -Encoding utf8
    $ready = $true
    Write-Host "Verified viewer: $base"
    Write-Host "Verified WebSocket: $ws"
    Write-Host "Actual fleet: $($localCheck.fleet); coordination: $($localCheck.coordination); ground orchestrator: $Orchestrator"
    Write-Host "Private DisruptionOps settings: $share\disruptionops.env"
    Write-Host "Private browser operator link: $share\friend-operator-link.txt"
    Write-Host 'Copy the env settings into DisruptionOps privately. Guide: docs/INTEGRATION-KIT.md'
    if ($VerifyOnly) { Write-Host 'Verification complete; owned processes will now stop.'; return }
    Write-Host 'Keep this terminal open. Ctrl+C stops only the processes this launcher started.'
    while ($true) {
        Start-Sleep -Seconds 2
        foreach ($process in $owned) {
            $process.Refresh()
            if ($process.HasExited) { throw "A launcher-owned process stopped; check $runDir" }
        }
    }
} finally {
    foreach ($process in $owned) {
        $process.Refresh()
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    }
    $env:OPS_INTEGRATION_TOKEN = $previousToken
    if ($ready) {
        $connections['stopped_at'] = (Get-Date).ToUniversalTime().ToString('o')
        $connections | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $share 'connection.json') -Encoding utf8
    }
}
