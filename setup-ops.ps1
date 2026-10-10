param([switch]$WithGeodata, [switch]$Recreate)
# Creates an isolated environment for the operations simulator and installs its dependencies.
$ErrorActionPreference = 'Stop'
$env = Join-Path $PSScriptRoot '.venv-ops'
if ($Recreate -and (Test-Path -LiteralPath $env)) { Remove-Item -Recurse -Force -LiteralPath $env }
if (-not (Test-Path -LiteralPath $env)) {
    python -m venv $env
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12+ and make `python` available in PATH.' }
}
$py = Join-Path $env 'Scripts\python.exe'
& $py -m pip install --upgrade pip
& $py -m pip install -r (Join-Path $PSScriptRoot 'requirements-ops.txt')
if ($WithGeodata) { & $py -m pip install -r (Join-Path $PSScriptRoot 'requirements-geodata.txt') }
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Ready. Local view : .\run-operations.ps1 -Fleet 50'
Write-Host 'Share with friend : .\share-with-friend.ps1'
