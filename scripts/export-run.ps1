<# Export a recorded run to CSV (one file per dataset). Usage: scripts\export-run.ps1 [-Run <id or dir>] [-Out <dir>] #>
param([string]$Run = '', [string]$Out = '')
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root '.conda-drones\python.exe'
$code = @'
import sys, pathlib
sys.path.insert(0, sys.argv[1])
from swarm_ops.telemetry.recorder import list_run_dirs, read_dataset, to_csv
from swarm_ops.telemetry.schemas import DATASETS
runs = list_run_dirs(pathlib.Path(sys.argv[1]) / '.cache' / 'runs')
sel = sys.argv[2]
run = pathlib.Path(sel) if sel and pathlib.Path(sel).is_dir() else next((r for r in reversed(runs) if not sel or r.name.endswith(sel)), None)
if run is None: sys.exit('no run found')
out = pathlib.Path(sys.argv[3]) if sys.argv[3] else run / 'csv'
out.mkdir(parents=True, exist_ok=True)
for name in DATASETS:
    records = read_dataset(run, name, 0, 10**9)
    if records: (out / f'{name}.csv').write_text(to_csv(records), encoding='utf8')
print('exported to', out)
'@
& $py -c $code $root $Run $Out
