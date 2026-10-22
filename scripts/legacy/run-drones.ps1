param(
    [ValidateSet('pid', 'pid_velocity', 'downwash', 'mrac', 'learn', 'play')]
    [string]$Example = 'pid',
    [switch]$Headless,
    [switch]$Multiagent,
    [string]$ModelPath = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)  # repository root (scripts/legacy/..)
$env:PATH = "$root\.conda-drones;$root\.conda-drones\Library\bin;$root\.conda-drones\Scripts;$env:PATH"
$env:MPLCONFIGDIR = Join-Path $root '.mpl-cache'
$env:PYTHONPATH = Join-Path $root 'gym-pybullet-drones'
$taskArgs = @('-m', "gym_pybullet_drones.examples.$Example", '--gui', $(if ($Headless) {'false'} else {'true'}))
if ($Example -notin @('play', 'learn')) { $taskArgs += @('--plot', 'false') }
if ($Example -in @('learn', 'play')) { $taskArgs += @('--multiagent', $(if ($Multiagent) {'true'} else {'false'})) }
if ($ModelPath) { $taskArgs += @('--model_path', $ModelPath) }
if ($Example -eq 'mrac') { $taskArgs = @('-c', "from gym_pybullet_drones.examples.mrac import run; run(gui=$($(if ($Headless) {'False'} else {'True'})), plot=False)") }
if ($Example -in @('learn', 'play')) { $taskArgs = @('-c', 'import torch, runpy, sys; module = sys.argv.pop(1); sys.argv[0] = module; runpy.run_module(module, run_name="__main__")') + $taskArgs[1..($taskArgs.Length-1)] }
Push-Location (Join-Path $root 'gym-pybullet-drones')
try {
    & (Join-Path $root '.conda-drones\python.exe') @taskArgs
    if ($LASTEXITCODE -ne 0) { throw "Example failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }




