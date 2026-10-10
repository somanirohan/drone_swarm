param([switch]$Headless, [switch]$Overview, [switch]$FailureDemo, [switch]$Scripted, [int]$Duration = 3600, [ValidateSet('openai','ollama','jev')][string]$AI, [string]$Model, [int]$AIMaxRequests = 0)
$ErrorActionPreference = 'Stop'
$env:PATH = "$PSScriptRoot\.conda-drones;$PSScriptRoot\.conda-drones\Library\bin;$PSScriptRoot\.conda-drones\Scripts;$env:PATH"
$env:PYTHONPATH = Join-Path $PSScriptRoot 'gym-pybullet-drones'
$swarmPython = Join-Path $PSScriptRoot '.venv-swarm\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $swarmPython)) { $swarmPython = Join-Path $PSScriptRoot '.conda-drones\python.exe' }
if (-not (Test-Path -LiteralPath $swarmPython)) { throw 'Run powershell -ExecutionPolicy Bypass -File .\setup.ps1 first.' }
$taskArguments = @('-u', (Join-Path $PSScriptRoot 'disaster_demo.py'), '--duration', $Duration)
if ($Headless) { $taskArguments += '--headless' }
if ($Overview) { $taskArguments += '--overview' }
if ($FailureDemo) { $taskArguments += '--failure-demo' }
if ($Scripted) { $taskArguments += '--scripted' }
if ($AI) { $taskArguments += @('--ai', $AI); if ($AIMaxRequests -gt 0) { $taskArguments += @('--ai-max-requests', $AIMaxRequests) } }
if ($Model) { $taskArguments += @('--ai-model', $Model) }
& $swarmPython @taskArguments
if ($LASTEXITCODE -ne 0) { throw "Simulation failed with exit code $LASTEXITCODE" }
