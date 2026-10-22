"""Summarise a recorded run: KPIs, incident timelines, decision stats, utilisation (PNG when matplotlib exists).

    python tools/analyze_run.py [RUN_DIR_OR_ID] [--out DIR] [--runs .cache/runs]

Without an argument the newest run is used. Writes summary.json, summary.md (+ *.png) into --out
(default: <run dir>/analysis) and prints the markdown.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from swarm_ops.telemetry.recorder import list_run_dirs, read_dataset  # noqa: E402
from swarm_ops.telemetry.schemas import DATASETS  # noqa: E402


def load(run: Path) -> dict:
    return {name: read_dataset(run, name, 0, 10 ** 9) for name in DATASETS}


def mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def summarise(run: Path) -> dict:
    manifest = json.loads((run / 'manifest.json').read_text(encoding='utf8'))
    data = load(run)
    timelines = defaultdict(list)
    for r in data['incidents']:
        timelines[r['incident_id']].append({'stage': r['stage'], 'occurred_t': r.get('occurred_t'), 'observed_t': r['sim_t'],
                                            'since_created_s': r.get('time_since_created_s')})
    incidents = {}
    first_counts = Counter()
    for iid, stages in timelines.items():
        by = {s['stage']: s for s in stages}
        if 'first_on_scene' in by:
            first = next((r.get('first_on_scene') for r in data['incidents'] if r['incident_id'] == iid and r['stage'] == 'first_on_scene'), None)
            first_counts[first or 'unknown'] += 1
        incidents[iid] = {'stages': stages, 'resolved': 'resolved' in by,
                          'time_to_resolve_s': (by['resolved'].get('since_created_s') if 'resolved' in by else None),
                          'time_to_drone_on_scene_s': by.get('drone_on_scene', {}).get('since_created_s'),
                          'time_to_briefing_s': by.get('briefing', {}).get('since_created_s')}
    decisions = data['decisions']
    dec_kinds = Counter(d.get('kind') or 'unknown' for d in decisions)
    fallbacks = sum(1 for d in decisions if d.get('fallback'))
    latencies = sorted(d['latency_ms'] for d in decisions if isinstance(d.get('latency_ms'), (int, float)))
    kpis = data['kpis']
    final = kpis[-1] if kpis else {}
    drone_states = Counter(r.get('activity') for r in data['drones'])
    total_samples = sum(drone_states.values()) or 1
    summary = {
        'run_id': manifest.get('run_id'), 'started_at': manifest.get('started_at'), 'status': manifest.get('status'),
        'scenario': (manifest.get('scenario') or {}).get('id'), 'seed': manifest.get('seed'), 'git': manifest.get('git'),
        'sim_duration_s': manifest.get('last_sim_t'), 'record_counts': manifest.get('counts'), 'data_quality': manifest.get('quality'),
        'dropped_records': manifest.get('dropped_records'),
        'kpis_final': final,
        'incidents': {'total': len(incidents), 'resolved': sum(i['resolved'] for i in incidents.values()),
                      'mean_time_to_drone_on_scene_s': mean(i['time_to_drone_on_scene_s'] for i in incidents.values()),
                      'mean_time_to_briefing_s': mean(i['time_to_briefing_s'] for i in incidents.values()),
                      'mean_time_to_resolve_s': mean(i['time_to_resolve_s'] for i in incidents.values()),
                      'first_on_scene': dict(first_counts)},
        'decisions': {'total': len(decisions), 'by_kind': dict(dec_kinds), 'fallbacks': fallbacks,
                      'fallback_rate': round(fallbacks / len(decisions), 4) if decisions else None,
                      'latency_ms_p50': latencies[len(latencies) // 2] if latencies else None,
                      'latency_ms_p95': latencies[int(len(latencies) * .95)] if latencies else None},
        'messages': {'total': len(data['messages']), 'by_kind': dict(Counter(m.get('kind') or 'unknown' for m in data['messages']))},
        'drone_activity_share': {k: round(v / total_samples, 3) for k, v in drone_states.most_common()},
        'event_kinds': dict(Counter(e['kind'] for e in data['events']).most_common(20)),
        'timelines': incidents,
    }
    summary['_series'] = {'kpis': kpis}
    return summary


def charts(summary: dict, out: Path) -> list[str]:
    """Render in a child process: a broken numpy/BLAS install can hard-crash matplotlib; the report must survive."""
    import subprocess
    series = out / '_kpis.json'
    series.write_text(json.dumps(summary['_series']['kpis']), encoding='utf8')
    try:
        done = subprocess.run([sys.executable, str(Path(__file__)), '--charts-child', str(series), str(out)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []
    series.unlink(missing_ok=True)
    return [line for line in done.stdout.split() if line.endswith('.png')] if done.returncode == 0 else []


def render_charts(kpis: list, out: Path) -> list[str]:
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    if len(kpis) < 2:
        return []
    t = [k['sim_t'] / 60 for k in kpis]
    files = []
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.6))
    ax[0].plot(t, [k['drone_utilisation'] * 100 for k in kpis], label='drone utilisation %')
    ax[0].plot(t, [(k.get('ground_utilisation') or 0) * 100 for k in kpis], label='ground utilisation %')
    ax[0].plot(t, [k.get('coverage_pct') or 0 for k in kpis], label='coverage %')
    ax[0].set_xlabel('sim minutes'); ax[0].legend(); ax[0].set_title('Utilisation and coverage')
    ax[1].plot(t, [k['open_incidents'] for k in kpis], label='open')
    ax[1].plot(t, [k['resolved_incidents'] for k in kpis], label='resolved')
    ax[1].plot(t, [k['airborne'] for k in kpis], label='airborne')
    ax[1].set_xlabel('sim minutes'); ax[1].legend(); ax[1].set_title('Incidents and airborne drones')
    fig.tight_layout()
    path = out / 'kpis.png'
    fig.savefig(path, dpi=110)
    plt.close(fig)
    files.append(path.name)
    return files


def markdown(s: dict, pngs: list[str]) -> str:
    i, d, k = s['incidents'], s['decisions'], s['kpis_final']
    lines = [f"# Run {s['run_id']}", '', f"- scenario `{s['scenario']}`, seed {s['seed']}, sim {s['sim_duration_s']} s, status {s['status']}",
             f"- git {(s.get('git') or {}).get('commit')} (dirty: {(s.get('git') or {}).get('dirty')})",
             f"- records: {s['record_counts']}", f"- data quality: {s['data_quality']}, dropped {s['dropped_records']}", '',
             '## Incidents', f"- {i['resolved']}/{i['total']} resolved; mean to drone on scene {i['mean_time_to_drone_on_scene_s']} s, "
             f"briefing {i['mean_time_to_briefing_s']} s, resolve {i['mean_time_to_resolve_s']} s",
             f"- first on scene: {i['first_on_scene']} (ground-first vs drone-first)", '',
             '## Decisions', f"- {d['total']} total, fallback rate {d['fallback_rate']}, latency p50/p95 {d['latency_ms_p50']}/{d['latency_ms_p95']} ms",
             f"- by kind: {d['by_kind']}", '', '## Final KPIs', '```json', json.dumps(k, indent=1), '```', '',
             f"## Drone activity share\n{s['drone_activity_share']}", '', '## Incident timelines']
    for iid, inc in s['timelines'].items():
        lines.append(f"- **{iid}**: " + ' -> '.join(f"{st['stage']}@{st['occurred_t'] if st['occurred_t'] is not None else st['observed_t']}" for st in inc['stages']))
    lines += [''] + [f'![{p}]({p})' for p in pngs]
    return '\n'.join(lines) + '\n'


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--charts-child':
        print(' '.join(render_charts(json.loads(Path(sys.argv[2]).read_text(encoding='utf8')), Path(sys.argv[3]))))
        return
    parser = argparse.ArgumentParser()
    parser.add_argument('run', nargs='?')
    parser.add_argument('--runs', default=str(ROOT / '.cache' / 'runs'))
    parser.add_argument('--out')
    args = parser.parse_args()
    runs = list_run_dirs(args.runs)
    run = Path(args.run) if args.run and Path(args.run).is_dir() else next((r for r in reversed(runs) if args.run and r.name.endswith(args.run)), None) if args.run else (runs[-1] if runs else None)
    if run is None:
        sys.exit('no run found')
    out = Path(args.out) if args.out else run / 'analysis'
    out.mkdir(parents=True, exist_ok=True)
    summary = summarise(run)
    pngs = charts(summary, out)
    summary.pop('_series')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf8')
    text = markdown(summary, pngs)
    (out / 'summary.md').write_text(text, encoding='utf8')
    print(text)


if __name__ == '__main__':
    main()
