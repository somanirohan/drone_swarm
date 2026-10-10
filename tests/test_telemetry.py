import asyncio
import gzip
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from swarm_ops.engine import OperationsSimulation
from swarm_ops.server import OperationsServer
from swarm_ops.telemetry import (DATASETS, RunRecorder, Telemetry, read_dataset, register_secret, scrub_text, setup_logging, validate)
from swarm_ops.telemetry.recorder import apply_retention, list_run_dirs

TOKEN = 'ctl-SECRET-token-value-123456'


def drive(tel, engine, seconds=120, dt=0.1, add=True):
    if add:
        engine.command('random_incident', {})
    for _ in range(int(seconds / dt)):
        engine.step(dt)
        tel.observe(engine.snapshot(include_world=False), engine)
    tel.sync(10)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_valid_datasets_under_load(self):
        tel = Telemetry(self.base, enabled=True)
        engine = OperationsSimulation(seed=3, fleet_size=10, scenario='rooftop_flood')
        drive(tel, engine, 180)
        tel.close()
        run = list_run_dirs(self.base)[0]
        manifest = json.loads((run / 'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'complete')
        self.assertEqual(manifest['run_id'], engine.run_id)
        for name in ('events', 'drones', 'incidents', 'kpis', 'ground'):
            records = read_dataset(run, name, 0, 100000)
            self.assertTrue(records, name)
            last_seq, last_t = 0, -1
            for r in records:
                self.assertIsNone(validate(name, r))
                self.assertEqual(r['seq'], last_seq + 1)
                self.assertGreaterEqual(r['sim_t'], last_t)
                self.assertEqual(r['run_id'], engine.run_id)
                last_seq, last_t = r['seq'], r['sim_t']
        self.assertEqual(manifest['quality']['rejected'], 0)
        ids = [r['id'] for r in read_dataset(run, 'events', 0, 100000)]
        self.assertEqual(len(ids), len(set(ids)))
        stages = {r['stage'] for r in read_dataset(run, 'incidents', 0, 100000)}
        self.assertIn('created', stages)

    def test_validation_and_dedupe(self):
        rec = RunRecorder(self.base, 'run-test-1', {})
        self.assertTrue(rec.write('events', {'id': 'a', 'kind': 'x'}, 1.0))
        self.assertFalse(rec.write('events', {'id': 'a', 'kind': 'x'}, 1.0))
        self.assertFalse(rec.write('events', {'id': 'b'}, 1.0))  # missing kind
        self.assertFalse(rec.write('events', {'id': 'c', 'kind': 5}, 1.0))
        rec.write('events', {'id': 'd', 'kind': 'x'}, 0.5)  # regression is clamped
        rec.close()
        out = read_dataset(rec.dir, 'events')
        self.assertEqual([r['sim_t'] for r in out], [1.0, 1.0])
        self.assertEqual(rec.stats['duplicates'], 1)
        self.assertEqual(rec.stats['rejected'], 2)
        self.assertEqual(rec.stats['sim_t_regressions'], 1)

    def test_rotation_cap_and_retention(self):
        rec = RunRecorder(self.base, 'run-test-2', {}, rotate_bytes=2000, run_cap_bytes=6000)
        for i in range(600):
            rec.write('drones', {'drone_id': 'D01', 'x': 1.0, 'y': 2.0, 'alt': 3.0}, float(i))
        rec.close()
        self.assertGreater(rec.stats['rotations'], 3)
        self.assertGreater(rec.stats['parts_deleted'], 0)
        self.assertTrue(any(p.suffix == '.gz' for p in rec.dir.iterdir()))
        total = sum(p.stat().st_size for p in rec.dir.iterdir())
        self.assertLess(total, 12000)
        tail = read_dataset(rec.dir, 'drones', 0, 10000)
        self.assertEqual(tail[-1]['seq'], 600)
        for i in range(3):
            RunRecorder(self.base, f'run-test-x{i}', {}, started_at=time.time() + 10 * (i + 1)).close()
        removed = apply_retention(self.base, keep_runs=2)
        self.assertEqual(len(list_run_dirs(self.base)), 2)
        self.assertEqual(len(removed), 2)

    def test_overhead_budget(self):
        tel = Telemetry(self.base, enabled=True)
        engine = OperationsSimulation(seed=5, fleet_size=20, scenario='rooftop_flood')
        drive(tel, engine, 60)
        stats = tel.stats()
        tel.close()
        self.assertLess(stats['overhead']['mean_ms'], 1.0)
        self.assertEqual(stats['dropped'], 0)


class SecretTests(unittest.TestCase):
    def test_no_secrets_in_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup_logging(tmp, level='DEBUG', console=False)
            register_secret(TOKEN)
            log = logging.getLogger('swarm.test')
            log.info('token is %s and Bearer abcdef123456', TOKEN, extra={'event': 't', 'fields': {'url': f'/ops/ws?token={TOKEN}', 'authorization': 'x', 'note': TOKEN}})
            Telemetry.audit('pause', 'operator', {'token': TOKEN, 'n': 1}, True)
            for handler in logging.getLogger('swarm').handlers:
                handler.flush()
            text = ''.join(p.read_text() for p in Path(tmp).glob('*.jsonl'))
            self.assertIn('"event":"t"', text)
            self.assertIn('"event":"command"', text)
            self.assertNotIn(TOKEN, text)
            self.assertNotIn('abcdef123456', text)
            for handler in list(logging.getLogger('swarm').handlers):
                handler.close()
                logging.getLogger('swarm').removeHandler(handler)
        self.assertNotIn(TOKEN, scrub_text(f'x {TOKEN}'))


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_endpoints_require_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = OperationsSimulation(seed=3, fleet_size=6, scenario='rooftop_flood')
            server = OperationsServer(engine, control_token=TOKEN, journal_path=Path(tmp) / 'ev.sqlite3', port=0)
            await server.start()
            try:
                base = f'http://127.0.0.1:{server.port}'
                await asyncio.sleep(1.5)

                def get(path, token=None):
                    req = Request(base + path, headers={'Authorization': f'Bearer {token}'} if token else {})
                    try:
                        with urlopen(req, timeout=5) as r:
                            return r.status, r.read()
                    except HTTPError as exc:
                        return exc.code, exc.read()
                loop = asyncio.get_running_loop()
                status, _ = await loop.run_in_executor(None, get, '/ops/runs')
                self.assertEqual(status, 403)
                status, _ = await loop.run_in_executor(None, get, f'/ops/runs/{engine.run_id}/events?token={TOKEN}x')
                self.assertEqual(status, 403)
                status, body = await loop.run_in_executor(None, get, '/ops/runs', TOKEN)
                self.assertEqual(status, 200)
                runs = json.loads(body)['runs']
                self.assertEqual(runs[0]['run_id'], engine.run_id)
                status, body = await loop.run_in_executor(None, get, f'/ops/runs/{engine.run_id}/manifest', TOKEN)
                self.assertEqual(json.loads(body)['run_id'], engine.run_id)
                status, body = await loop.run_in_executor(None, get, f'/ops/runs/{engine.run_id}/drones?limit=5&format=csv', TOKEN)
                self.assertEqual(status, 200)
                self.assertIn(b'drone_id', body)
                status, _ = await loop.run_in_executor(None, get, f'/ops/runs/{engine.run_id}/nope', TOKEN)
                self.assertEqual(status, 404)
            finally:
                await server.stop()


if __name__ == '__main__':
    unittest.main()
