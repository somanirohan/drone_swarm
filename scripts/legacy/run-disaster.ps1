param([switch]$Headless, [switch]$Overview, [switch]$FailureDemo, [switch]$Scripted, [int]$Duration = 3600, [ValidateSet('openai','ollama','jev')][string]$AI, [string]$Model, [int]$AIMaxRequests = 0)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)  # repository root (scripts/legacy/..)
$env:PATH = "$root\.conda-drones;$root\.conda-drones\Library\bin;$root\.conda-drones\Scripts;$env:PATH"
$env:PYTHONPATH = Join-Path $root 'gym-pybullet-drones'
$swarmPython = Join-Path $root '.venv-swarm\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $swarmPython)) { $swarmPython = Join-Path $root '.conda-drones\python.exe' }
if (-not (Test-Path -LiteralPath $swarmPython)) { throw 'Run powershell -ExecutionPolicy Bypass -File .\scripts\legacy\setup.ps1 first.' }
$taskArguments = @('-u', (Join-Path $root 'legacy\pybullet\disaster_demo.py'), '--duration', $Duration)
if ($Headless) { $taskArguments += '--headless' }
if ($Overview) { $taskArguments += '--overview' }
if ($FailureDemo) { $taskArguments += '--failure-demo' }
if ($Scripted) { $taskArguments += '--scripted' }
if ($AI) { $taskArguments += @('--ai', $AI); if ($AIMaxRequests -gt 0) { $taskArguments += @('--ai-max-requests', $AIMaxRequests) } }
if ($Model) { $taskArguments += @('--ai-model', $Model) }
& $swarmPython @taskArguments
if ($LASTEXITCODE -ne 0) { throw "Simulation failed with exit code $LASTEXITCODE" }
