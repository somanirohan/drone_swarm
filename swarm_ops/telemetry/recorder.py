"""Append-only run datasets on disk: one directory per run, JSONL, gzip rotation, size cap, retention.

``RunRecorder`` is a synchronous sink used from the single telemetry worker thread. Records are
validated against ``schemas.DATASETS``, de-duplicated by id, stamped with the common envelope and
written as one JSON object per line. Readers (HTTP, analysis) only ever read closed lines.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
import shutil
import time
from pathlib import Path

from .logging_setup import utc_iso
from .schemas import DATASETS, validate

RUN_DIR_RE = re.compile(r'^(\d{8}T\d{6}Z)_([A-Za-z0-9-]{6,64})$')
PART_RE = re.compile(r'^(?P<name>[a-z]+)\.(?P<first>\d{9})\.jsonl\.gz$')
LIVE_RE = re.compile(r'^(?P<name>[a-z]+)\.jsonl$')
MAX_SEEN_IDS = 200_000


def run_dir_name(started_at: float, run_id: str) -> str:
    return f'{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started_at))}_{run_id}'


def _json(value) -> str:
    return json.dumps(value, separators=(',', ':'), default=str, allow_nan=False)


class RunRecorder:
    def __init__(self, base_dir: Path | str, run_id: str, manifest: dict, *, rotate_bytes: int = 8_000_000,
                 run_cap_bytes: int = 250_000_000, started_at: float | None = None):
        self.run_id = run_id
        self.rotate_bytes, self.run_cap_bytes = int(rotate_bytes), int(run_cap_bytes)
        self.started_at = started_at or time.time()
        self.dir = Path(base_dir) / run_dir_name(self.started_at, run_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest = {**manifest, 'run_id': run_id, 'started_at': utc_iso(self.started_at), 'status': 'recording',
                         'schemas': {name: spec['version'] for name, spec in DATASETS.items()}}
        self._files: dict[str, io.TextIOWrapper] = {}
        self._bytes: dict[str, int] = {}
        self._seq = {name: 0 for name in DATASETS}
        self._first = {name: 1 for name in DATASETS}
        self._last_t = {name: 0.0 for name in DATASETS}
        self._seen: dict[str, set] = {name: set() for name in DATASETS}
        self.counts = {name: 0 for name in DATASETS}
        self.stats = {'rejected': 0, 'duplicates': 0, 'sim_t_regressions': 0, 'rotations': 0, 'parts_deleted': 0, 'rejects_logged': 0}
        self._closed_bytes = 0
        self.last_sim_t = 0.0
        self._last_manifest = 0.0
        self.write_manifest()

    # -- writing ---------------------------------------------------------------------------------
    def write(self, dataset: str, fields: dict, sim_t: float, wall_ts: float | None = None) -> bool:
        spec = DATASETS[dataset]
        wall_ts = time.time() if wall_ts is None else wall_ts
        idf = spec['id_field']
        if idf and fields.get(idf) is not None:
            key = str(fields[idf])
            if key in self._seen[dataset]:
                self.stats['duplicates'] += 1
                return False
        sim_t = float(sim_t)
        if sim_t < self._last_t[dataset]:  # clamp: sim_t is non-decreasing inside a dataset
            self.stats['sim_t_regressions'] += 1
            sim_t = self._last_t[dataset]
        record = {'v': spec['version'], 'seq': self._seq[dataset] + 1, 'run_id': self.run_id, 'sim_t': round(sim_t, 3),
                  'wall_time': utc_iso(wall_ts), 'wall_ts': round(wall_ts, 3), **fields}
        error = validate(dataset, record)
        if error is None:
            try:
                line = _json(record)
            except (TypeError, ValueError) as exc:
                error = f'not serialisable: {exc}'
        if error is not None:
            self._reject(dataset, fields, error)
            return False
        handle = self._handle(dataset)
        data = line + '\n'
        handle.write(data)
        self._bytes[dataset] += len(data)
        self._seq[dataset] += 1
        self._last_t[dataset] = sim_t
        self.last_sim_t = max(self.last_sim_t, sim_t)
        self.counts[dataset] += 1
        if idf and fields.get(idf) is not None:
            seen = self._seen[dataset]
            if len(seen) >= MAX_SEEN_IDS:
                seen.clear()
            seen.add(str(fields[idf]))
        if self._bytes[dataset] >= self.rotate_bytes:
            self._rotate(dataset)
        return True

    def _handle(self, dataset):
        handle = self._files.get(dataset)
        if handle is None:
            path = self.dir / f'{dataset}.jsonl'
            handle = open(path, 'a', encoding='utf8', newline='\n')
            self._files[dataset] = handle
            self._bytes[dataset] = path.stat().st_size
        return handle

    def _reject(self, dataset, fields, error):
        self.stats['rejected'] += 1
        if self.stats['rejects_logged'] < 500:
            self.stats['rejects_logged'] += 1
            with open(self.dir / '_rejects.jsonl', 'a', encoding='utf8') as handle:
                handle.write(_json({'wall_time': utc_iso(), 'dataset': dataset, 'error': error,
                                    'record': str(fields)[:400]}) + '\n')

    def _rotate(self, dataset):
        handle = self._files.pop(dataset, None)
        if handle is None:
            return
        handle.close()
        live = self.dir / f'{dataset}.jsonl'
        part = self.dir / f'{dataset}.{self._first[dataset]:09d}.jsonl.gz'
        with open(live, 'rb') as src, gzip.open(part, 'wb', compresslevel=5) as dst:
            shutil.copyfileobj(src, dst)
        live.unlink()
        self._first[dataset] = self._seq[dataset] + 1
        self._bytes[dataset] = 0
        self.stats['rotations'] += 1
        self._enforce_cap()

    def _enforce_cap(self):
        parts = []
        total = 0
        for path in self.dir.iterdir():
            size = path.stat().st_size
            total += size
            match = PART_RE.match(path.name)
            if match:
                parts.append((0 if match['name'] == 'drones' else 1, int(match['first']), path, size))
        parts.sort()  # high-volume drone series is trimmed first, oldest part first
        while total > self.run_cap_bytes and parts:
            _, _, path, size = parts.pop(0)
            path.unlink(missing_ok=True)
            total -= size
            self.stats['parts_deleted'] += 1
            self.manifest['truncated'] = True

    # -- housekeeping ----------------------------------------------------------------------------
    def flush(self):
        for handle in self._files.values():
            handle.flush()

    def write_manifest(self, extra: dict | None = None, final: bool = False):
        self._last_manifest = time.monotonic()
        if extra:
            self.manifest.update(extra)
        self.manifest.update(counts=dict(self.counts), quality=dict(self.stats), last_sim_t=round(self.last_sim_t, 3),
                             updated_at=utc_iso())
        if final:
            self.manifest.update(status='complete', ended_at=utc_iso())
        tmp = self.dir / 'manifest.json.tmp'
        tmp.write_text(json.dumps(self.manifest, indent=2, default=str), encoding='utf8')
        os.replace(tmp, self.dir / 'manifest.json')

    def maybe_manifest(self, every_s=30.0):
        if time.monotonic() - self._last_manifest >= every_s:
            self.write_manifest()

    def close(self, extra: dict | None = None):
        self.flush()
        for handle in self._files.values():
            handle.close()
        self._files.clear()
        self.write_manifest(extra, final=True)


# -- retention ----------------------------------------------------------------------------------
def dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def list_run_dirs(base_dir: Path | str) -> list[Path]:
    base = Path(base_dir)
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir() and RUN_DIR_RE.match(p.name)), key=lambda p: p.name)


def apply_retention(base_dir: Path | str, keep_runs: int = 30, max_total_bytes: int = 2_000_000_000, protect: Path | None = None) -> list[str]:
    """Delete the oldest run directories beyond ``keep_runs`` or ``max_total_bytes``; never the protected one."""
    removed = []
    runs = [p for p in list_run_dirs(base_dir) if p != protect]
    sizes = {p: dir_size(p) for p in runs}
    total = sum(sizes.values()) + (dir_size(protect) if protect and protect.exists() else 0)
    while runs and (len(runs) + (1 if protect else 0) > keep_runs or total > max_total_bytes):
        victim = runs.pop(0)
        total -= sizes[victim]
        shutil.rmtree(victim, ignore_errors=True)
        removed.append(victim.name)
    return removed


# -- reading ------------------------------------------------------------------------------------
def find_run_dir(base_dir: Path | str, run_id: str) -> Path | None:
    if not re.fullmatch(r'[A-Za-z0-9-]{6,64}', run_id or ''):
        return None
    matches = [p for p in list_run_dirs(base_dir) if p.name.endswith('_' + run_id)]
    return matches[-1] if matches else None


def _iter_lines(path: Path):
    opener = gzip.open if path.suffix == '.gz' else open
    try:
        with opener(path, 'rt', encoding='utf8') as handle:
            yield from handle
    except (OSError, EOFError):
        return


def read_dataset(run_dir: Path, dataset: str, after: int = 0, limit: int = 500) -> list[dict]:
    """Records with ``seq > after`` in order, at most ``limit``. Missing (rotated-away) data is skipped."""
    files = []
    for path in run_dir.iterdir():
        match = PART_RE.match(path.name)
        if match and match['name'] == dataset:
            files.append((int(match['first']), path))
    files.sort()
    live = run_dir / f'{dataset}.jsonl'
    out = []
    for index, (first, path) in enumerate(files):
        next_first = files[index + 1][0] if index + 1 < len(files) else None
        if next_first is not None and next_first <= after + 1:
            continue
        _collect(path, after, limit, out)
        if len(out) >= limit:
            return out
    if live.exists():
        _collect(live, after, limit, out)
    return out


def _collect(path, after, limit, out):
    for line in _iter_lines(path):
        try:
            record = json.loads(line)
        except ValueError:
            continue  # partially written trailing line
        if record.get('seq', 0) > after:
            out.append(record)
            if len(out) >= limit:
                return


def to_csv(records: list[dict]) -> str:
    """Flatten records to CSV (nested values become JSON strings; columns = union, envelope first)."""
    columns = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    head = [c for c in ('v', 'seq', 'run_id', 'sim_t', 'wall_time', 'wall_ts') if c in columns]
    columns = head + [c for c in columns if c not in head]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(columns)
    for record in records:
        writer.writerow([_json(v) if isinstance(v, (dict, list)) else ('' if v is None else v) for v in (record.get(c) for c in columns)])
    return buffer.getvalue()
