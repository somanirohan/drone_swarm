$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)  # repository root (scripts/legacy/..)
$runtime = Join-Path $root '.venv-swarm'
python -m venv $runtime
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 and make python available in PATH.' }
& (Join-Path $runtime 'Scripts\python.exe') -m pip install -r (Join-Path $root 'requirements\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Ready: powershell -ExecutionPolicy Bypass -File .\scripts\legacy\run-disaster.ps1'
