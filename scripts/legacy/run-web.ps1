param([int]$Port = 8765, [switch]$FailureDemo, [ValidateSet('openai','ollama','jev')][string]$AI, [string]$Model)
# Hosts the swarm on this laptop: simulation + WebSocket viewer on one port.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)  # repository root (scripts/legacy/..)
$env:PATH = "$root\.conda-drones;$root\.conda-drones\Library\bin;$root\.conda-drones\Scripts;$env:PATH"
$env:PYTHONPATH = Join-Path $root 'gym-pybullet-drones'
$env:TEMP = $env:TMP = Join-Path $root '.cache\tmp'
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$python = Join-Path $root '.conda-drones\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Environment missing: .conda-drones not found.' }
Write-Host "Local viewer: http://localhost:$Port"
$taskArguments = @('-u', (Join-Path $root 'legacy\pybullet\disaster_demo.py'), '--web', '--port', $Port, '--duration', 86400)
if ($FailureDemo) { $taskArguments += '--failure-demo' }
if ($AI) { $taskArguments += @('--ai', $AI) }
if ($Model) { $taskArguments += @('--ai-model', $Model) }
# The scenario restarts after each completed mission; browsers reconnect automatically.
while ($true) { & $python @taskArguments; if ($LASTEXITCODE -ne 0) { Write-Warning "Simulation exited with code $LASTEXITCODE; restarting in 3s"; Start-Sleep 3 } }
