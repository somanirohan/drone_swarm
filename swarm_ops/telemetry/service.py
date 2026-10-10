"""Telemetry service: non-blocking run recording, audit/connection logs and the /ops/runs read API.

``observe(state, engine)`` is called from the server tick with the snapshot it already built. It only
does O(1) checks and a queue put; derivation, validation and disk I/O run on one background thread.
"""
from __future__ import annotations

import logging
import os
import platform
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from .derive import Deriver
from .logging_setup import LogContext, log_event, register_secret, scrub, utc_iso
from .recorder import (RunRecorder, apply_retention, find_run_dir, list_run_dirs, read_dataset, to_csv)
from .schemas import DATASETS

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger('swarm.telemetry')
audit_log = logging.getLogger('swarm.audit')
http_log = logging.getLogger('swarm.http')
CONFIG_ENV = ('LOG_LEVEL', 'LOG_FORMAT', 'FLEET', 'WORLD', 'SEED', 'SCENARIO', 'SWARM_AI_PROVIDER', 'SWARM_AI_MODEL', 'ALLOWED_ORIGINS', 'HOST', 'PORT')
SECRET_ENV = ('OPS_CONTROL_TOKEN', 'OPS_INTEGRATION_TOKEN', 'ANTHROPIC_API_KEY', 'DISRUPTIONOPS_TOKEN')


def _git_commit():
    try:
        out = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        return {'commit': out.stdout.strip() or None, 'dirty': bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        return {'commit': None, 'dirty': None}


class Telemetry:
    def __init__(self, base_dir=None, *, enabled=None, drone_period=1.0, rotate_bytes=8_000_000, run_cap_bytes=250_000_000,
                 keep_runs=30, max_total_bytes=2_000_000_000, queue_max=5000, secrets=()):
        self.enabled = (os.environ.get('OPS_TELEMETRY', '1') != '0') if enabled is None else enabled
        self.base_dir = Path(base_dir) if base_dir else ROOT / '.cache' / 'runs'
        self.drone_period, self.rotate_bytes, self.run_cap_bytes = drone_period, rotate_bytes, run_cap_bytes
        self.keep_runs, self.max_total_bytes = keep_runs, max_total_bytes
        for secret in secrets:
            register_secret(secret)
        self.rec: RunRecorder | None = None
        self.deriver: Deriver | None = None
        self.run_id = None
        self._next_t = 0.0
        self._last_event = self._last_sig = None
        self._last_msg = self._last_dec = None
        self._msg_n = self._dec_n = 0
        self.dropped = 0
        self.overhead = {'n': 0, 'total_s': 0.0, 'max_s': 0.0, 'worker_s': 0.0}
        self._q: queue.Queue = queue.Queue(maxsize=queue_max)
        self._git = None
        self._thread = None
        if self.enabled:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self._thread = threading.Thread(target=self._worker, name='telemetry-writer', daemon=True)
            self._thread.start()

    # -- hot path (called from the sim tick) -------------------------------------------------------
    def observe(self, state: dict, engine=None) -> None:
        if not self.enabled:
            return
        started = time.perf_counter()
        sim_t = state.get('t') or 0.0
        LogContext.run_id, LogContext.sim_t = state.get('run_id'), sim_t
        new_run = state.get('run_id') != self.run_id
        if new_run:
            self.run_id = state.get('run_id')
            self._last_msg = self._last_dec = None
            self._msg_n = self._dec_n = 0
        events, signals = state.get('events') or (), state.get('signals') or ()
        last_event = events[-1].get('id') if events else None
        last_sig = signals[-1].get('id') if signals else None
        msgs = self._new_items(engine, state, 'messages', '_last_msg')
        decs = self._new_items(engine, state, 'decisions', '_last_dec')
        if new_run or msgs or decs or sim_t >= self._next_t or last_event != self._last_event or last_sig != self._last_sig:
            self._next_t = sim_t + self.drone_period
            self._last_event, self._last_sig = last_event, last_sig
            item = ('state', state, time.time(), msgs, decs, self._manifest_info(state, engine) if new_run else None)
            try:
                self._q.put_nowait(item)
            except queue.Full:
                self.dropped += 1
        elapsed = time.perf_counter() - started
        ov = self.overhead
        ov['n'] += 1
        ov['total_s'] += elapsed
        ov['max_s'] = max(ov['max_s'], elapsed)

    def _new_items(self, engine, state, name, marker):
        items = getattr(engine, name, None) if engine is not None else None
        if items is None:
            items = state.get(name)
        if not items or not isinstance(items, (list, tuple)):
            return []
        last = getattr(self, marker)
        tail = items[-1].get('id') if isinstance(items[-1], dict) else None
        if tail is not None and tail == last:
            return []
        if tail is None:  # no ids: use a length watermark
            seen = self._msg_n if name == 'messages' else self._dec_n
            fresh = list(items[seen:]) if len(items) >= seen else list(items)
            if name == 'messages':
                self._msg_n = len(items)
            else:
                self._dec_n = len(items)
            return [dict(i) if isinstance(i, dict) else i for i in fresh]
        ids = [i.get('id') if isinstance(i, dict) else None for i in items]
        fresh = items[ids.index(last) + 1:] if last in ids else items
        setattr(self, marker, tail)
        return [dict(i) if isinstance(i, dict) else i for i in fresh]

    def _manifest_info(self, state, engine):
        env = os.environ
        metrics = state.get('metrics') or {}
        cap = getattr(engine, 'dispatch_cap', None)
        return {
            'scenario': state.get('scenario'), 'world_id': getattr(engine, 'world_id', None) or (state.get('scenario') or {}).get('world_id'),
            'seed': getattr(engine, 'seed', None), 'fleet': {'size': metrics.get('fleet') or len(state.get('drones') or ()),
                                                             'ground_units': len(state.get('resources') or ())},
            'dispatch_caps': {'total': metrics.get('dispatch_cap'), 'per_incident': metrics.get('dispatch_cap_per_incident'), 'configured': cap},
            'config': {**{k: env[k] for k in CONFIG_ENV if k in env}, **{f'{k}_set': bool(env.get(k)) for k in SECRET_ENV}},
        }

    # -- worker ------------------------------------------------------------------------------------
    def _worker(self):
        while True:
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                if self.rec:
                    self.rec.flush()
                    self.rec.maybe_manifest()
                continue
            kind = item[0]
            try:
                if kind == 'state':
                    self._handle_state(item)
                elif kind == 'flush':
                    if self.rec:
                        self.rec.flush()
                        self.rec.write_manifest()
                    item[1].set()
                elif kind == 'close':
                    self._end_run()
                    item[1].set()
                    return
            except Exception:  # never let telemetry kill itself
                log.exception('telemetry worker error', extra={'event': 'telemetry.error'})
                if kind in ('flush', 'close'):
                    item[1].set()

    def _handle_state(self, item):
        _, state, wall_ts, msgs, decs, info = item
        started = time.perf_counter()
        if self.rec is None or self.rec.run_id != state.get('run_id'):
            self._end_run()
            if self._git is None:
                self._git = _git_commit()
            manifest = {**(info or {}), 'git': self._git, 'versions': self._versions(), 'telemetry': {
                'drone_period_s': self.drone_period, 'rotate_bytes': self.rotate_bytes, 'run_cap_bytes': self.run_cap_bytes}}
            apply_retention(self.base_dir, self.keep_runs, self.max_total_bytes, protect=None)
            self.rec = RunRecorder(self.base_dir, state['run_id'], manifest, rotate_bytes=self.rotate_bytes, run_cap_bytes=self.run_cap_bytes)
            self.deriver = Deriver(drone_period=self.drone_period)
            log_event(log, 'run.start', run_id=state['run_id'], dir=self.rec.dir.name)
        self.deriver.process(self.rec, state, wall_ts, msgs, decs)
        self.rec.maybe_manifest()
        self.overhead['worker_s'] += time.perf_counter() - started

    def _end_run(self):
        if self.rec is None:
            return
        self.deriver.finish(self.rec, time.time())
        self.rec.close({'overhead': self.stats()['overhead'], 'dropped_records': self.dropped})
        log_event(log, 'run.end', run_id=self.rec.run_id, counts=self.rec.counts)
        self.rec = self.deriver = None

    @staticmethod
    def _versions():
        out = {'python': sys.version.split()[0], 'platform': platform.platform()}
        for name in ('websockets', 'numpy', 'matplotlib'):
            try:
                out[name] = __import__(name).__version__
            except Exception:
                pass
        return out

    # -- control -----------------------------------------------------------------------------------
    def sync(self, timeout=5.0):
        """Wait until everything queued so far is written and flushed (tests, export)."""
        if not self.enabled:
            return
        done = threading.Event()
        self._q.put(('flush', done))
        done.wait(timeout)

    def close(self, timeout=5.0):
        if not self.enabled or not self._thread:
            return
        done = threading.Event()
        self._q.put(('close', done))
        done.wait(timeout)
        self.enabled = False

    def stats(self):
        ov = self.overhead
        n = max(1, ov['n'])
        return {'enabled': self.enabled, 'run_id': self.run_id, 'queue': self._q.qsize(), 'dropped': self.dropped,
                'overhead': {'calls': ov['n'], 'mean_ms': round(ov['total_s'] / n * 1000, 4), 'max_ms': round(ov['max_s'] * 1000, 4),
                             'worker_total_s': round(ov['worker_s'], 3)},
                'counts': dict(self.rec.counts) if self.rec else {}, 'quality': dict(self.rec.stats) if self.rec else {}}

    # -- logging helpers ---------------------------------------------------------------------------
    @staticmethod
    def audit(action, role, payload, ok, error=None, latency_ms=0.0, command_id=None, incident_id=None):
        summary = {k: (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:60]) for k, v in list((payload or {}).items())[:8]} if isinstance(payload, dict) else {}
        log_event(audit_log, 'command', f'{action} {"ok" if ok else "rejected"}', logging.INFO if ok else logging.WARNING,
                  action=action, role=role, command_id=command_id, payload=summary, ok=bool(ok), error=error,
                  latency_ms=round(latency_ms, 3), incident_id=incident_id)

    @staticmethod
    def connection(event, role, remote=None, **fields):
        host = remote[0] if isinstance(remote, (tuple, list)) and remote else None
        log_event(http_log, event, role=role, remote=host, **fields)

    # -- read API ----------------------------------------------------------------------------------
    def handle(self, path: str, query: dict):
        """Return (status, body_bytes_or_obj, content_type). Caller has already authorised the role."""
        parts = [p for p in path.split('/') if p][2:]  # drop 'ops','runs'
        if not self.base_dir.exists():
            return 200, {'runs': []}, 'json'
        if not parts:
            runs = []
            for d in reversed(list_run_dirs(self.base_dir)):
                try:
                    import json
                    m = json.loads((d / 'manifest.json').read_text(encoding='utf8'))
                except (OSError, ValueError):
                    m = {}
                runs.append({'run_id': d.name.split('_', 1)[1], 'dir': d.name, 'started_at': m.get('started_at'), 'status': m.get('status'),
                             'scenario': (m.get('scenario') or {}).get('id'), 'counts': m.get('counts'), 'last_sim_t': m.get('last_sim_t'),
                             'current': d.name.endswith('_' + str(self.run_id))})
            return 200, {'runs': runs, 'telemetry': self.stats()}, 'json'
        run_dir = find_run_dir(self.base_dir, parts[0])
        if run_dir is None:
            return 404, {'error': 'run not found'}, 'json'
        if len(parts) == 1 or parts[1] == 'manifest':
            import json
            self.sync(1.0) if parts[0] == self.run_id else None
            return 200, json.loads((run_dir / 'manifest.json').read_text(encoding='utf8')), 'json'
        dataset = parts[1]
        if dataset not in DATASETS:
            return 404, {'error': 'unknown dataset', 'datasets': sorted(DATASETS)}, 'json'
        try:
            after = max(0, int(query.get('after', ['0'])[0]))
            limit = min(5000, max(1, int(query.get('limit', ['500'])[0])))
        except ValueError:
            return 400, {'error': 'after and limit must be integers'}, 'json'
        if parts[0] == self.run_id:
            self.sync(1.0)
        records = read_dataset(run_dir, dataset, after, limit)
        next_after = records[-1]['seq'] if records else after
        fmt = query.get('format', ['json'])[0]
        if fmt == 'csv':
            return 200, to_csv(records).encode(), 'text/csv'
        if fmt == 'ndjson':
            import json
            return 200, ('\n'.join(json.dumps(r, separators=(',', ':')) for r in records) + '\n').encode(), 'application/x-ndjson'
        return 200, {'run_id': parts[0], 'dataset': dataset, 'schema_version': DATASETS[dataset]['version'], 'records': records, 'next_after': next_after}, 'json'
