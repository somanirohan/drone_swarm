<#
.SYNOPSIS
  Start the Pune operations simulator and create a public link a friend can open.

.DESCRIPTION
  1. Starts the simulator (python -m swarm_ops.server) on this machine (or reuses one already running on -Port).
  2. Starts a Cloudflare quick tunnel to it.
  3. Waits for the https://*.trycloudflare.com address, checks it works, then prints and
     copies a ready-to-send message (viewer link + WebSocket address).
  Friends get a READ-ONLY session: the server withholds the operator token from tunnelled
  requests. Everything keeps running on your hardware; closing this window stops the link.
  The address changes every time you run this.

.EXAMPLE
  .\scripts\share-with-friend.ps1
  .\scripts\share-with-friend.ps1 -Fleet 100 -Seed 7
  .\scripts\share-with-friend.ps1 -NoTunnel      # local test only, no public link
#>
param([int]$Port = 8787, [int]$Seed = 42, [int]$Fleet = 50, [switch]$NoTunnel)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot  # repository root (scripts/..)
$runtime = Join-Path $root '.cache\operations'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

function Find-Python {
    foreach ($candidate in @('.conda-drones\python.exe', '.venv-ops\Scripts\python.exe', '.venv-swarm\Scripts\python.exe')) {
        $path = Join-Path $root $candidate
        if (Test-Path -LiteralPath $path) { return $path }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw 'No Python found. Run .\scripts\setup-ops.ps1 first.'
}

function Test-Health($base) {
    try { return (Invoke-RestMethod -Uri "$base/health" -TimeoutSec 4).ok -eq $true } catch { return $false }
}

$local = "http://127.0.0.1:$Port"
$started = @()
try {
    # --- 1. simulator ---
    if (Test-Health $local) {
        Write-Host "Simulator already running on port $Port; reusing it." -ForegroundColor Yellow
    } else {
        $python = Find-Python
        & $python -c 'import websockets' 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Missing dependency. Run .\scripts\setup-ops.ps1 (installs requirements\requirements-ops.txt).' }
        $world = Join-Path $root 'assets\operations\pune-world.json'
        if (-not (Test-Path -LiteralPath $world)) { & $python (Join-Path $root 'tools\prepare_operations_map.py') }
        Write-Host "Starting simulator ($Fleet drones, seed $Seed)..."
        $server = Start-Process -FilePath $python -WorkingDirectory $root -WindowStyle Hidden -PassThru `
            -ArgumentList @('-u', '-m', 'swarm_ops.server', '--port', $Port, '--seed', $Seed, '--fleet', $Fleet) `
            -RedirectStandardOutput (Join-Path $runtime 'server.stdout.log') -RedirectStandardError (Join-Path $runtime 'server.stderr.log')
        $started += $server
        $deadline = (Get-Date).AddSeconds(40)
        while (-not (Test-Health $local)) {
            if ($server.HasExited) { throw "Simulator exited early. See $runtime\server.stderr.log" }
            if ((Get-Date) -gt $deadline) { throw "Simulator did not become healthy. See $runtime\server.stderr.log" }
            Start-Sleep -Milliseconds 500
        }
    }
    Write-Host "Local operator view (full control, this PC only): http://localhost:$Port" -ForegroundColor Green
    if ($NoTunnel) { Write-Host 'NoTunnel set: no public link created. Press Ctrl+C to stop.'; while ($true) { Start-Sleep 5 } }

    # --- 2. tunnel ---
    $cloudflared = Join-Path $root '.tools\cloudflared.exe'
    if (-not (Test-Path -LiteralPath $cloudflared)) {
        $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
        if (-not $cmd) { throw 'cloudflared not found. Put cloudflared.exe in .tools\ or install it (winget install Cloudflare.cloudflared).' }
        $cloudflared = $cmd.Source
    }
    $tunnelLog = Join-Path $runtime 'tunnel.log'
    Remove-Item -LiteralPath $tunnelLog -ErrorAction SilentlyContinue
    Write-Host 'Opening Cloudflare tunnel...'
    $tunnel = Start-Process -FilePath $cloudflared -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -ArgumentList @('tunnel', '--no-autoupdate', '--protocol', 'http2', '--url', $local) `
        -RedirectStandardError $tunnelLog -RedirectStandardOutput (Join-Path $runtime 'tunnel.stdout.log')
    $started += $tunnel

    # --- 3. discover the public address ---
    $public = $null
    $deadline = (Get-Date).AddSeconds(60)
    while (-not $public) {
        if ($tunnel.HasExited) { throw "cloudflared exited. See $tunnelLog" }
        if ((Get-Date) -gt $deadline) { throw "Timed out waiting for a trycloudflare.com address. See $tunnelLog" }
        if (Test-Path -LiteralPath $tunnelLog) {
            $match = Select-String -LiteralPath $tunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
            if ($match) { $public = $match.Matches[0].Value }
        }
        Start-Sleep -Milliseconds 500
    }
    # The hostname can take a few seconds to resolve worldwide.
    $reachable = $false
    for ($i = 0; $i -lt 20 -and -not $reachable; $i++) { $reachable = Test-Health $public; if (-not $reachable) { Start-Sleep 2 } }

    $ws = ($public -replace '^https://', 'wss://') + '/ops/ws'
    $message = @"
Drone swarm live simulation (read-only, runs on my laptop)

Open in a browser:   $public
WebSocket feed:      $ws
Health check:        $public/health
State snapshot:      $public/ops/state

Python test:   python examples/operations_ws_client.py --base-url $public --frames 3
Docs:          docs/implementation/FRIEND-WEBSOCKET-HANDOFF.md

Works only while my laptop is on and this session is running. The link changes each run.
"@
    $message | Set-Content -LiteralPath (Join-Path $runtime 'share-message.txt') -Encoding utf8
    try { Set-Clipboard -Value $message } catch { }

    Write-Host ''
    Write-Host '=============== SEND THIS TO YOUR FRIEND ===============' -ForegroundColor Cyan
    Write-Host $message
    Write-Host '=========================================================' -ForegroundColor Cyan
    if ($reachable) { Write-Host 'Public link verified. Message copied to clipboard.' -ForegroundColor Green }
    else { Write-Host 'Link created but not yet reachable from this PC; wait a minute and retry it in a browser. Message copied to clipboard.' -ForegroundColor Yellow }
    Write-Host 'Leave this window open. Press Ctrl+C to stop sharing.'
    while ($true) {
        Start-Sleep -Seconds 5
        if ($tunnel.HasExited) { throw "Tunnel stopped. See $tunnelLog" }
    }
} finally {
    foreach ($p in $started) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
    Write-Host 'Stopped.'
}
