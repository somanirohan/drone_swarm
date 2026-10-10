"""Command center against a real server on localhost (port 0) over the real WebSocket API."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from swarm_ops.command_center import CommandCenter, RealModeAdapter, random_scenario
from swarm_ops.engine import OperationsSimulation
from swarm_ops.server import OperationsServer

TOKEN = 'cc-test-integration-token'


class CommandCenterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        with mock.patch.dict(os.environ, {'OPS_INTEGRATION_TOKEN': TOKEN}):
            self.engine = OperationsSimulation(seed=11, fleet_size=20)
            self.server = OperationsServer(self.engine, control_token='cc-test-operator', port=0,
                                           journal_path=Path(self.folder.name) / 'events.sqlite3', frame_hz=20)
        self.engine.speed = 16
        await self.server.start()
        self.url = f'ws://127.0.0.1:{self.server.port}/ops/ws'

    async def asyncTearDown(self):
        await self.server.stop()
        self.folder.cleanup()

    def center(self, tasks, mode='orchestrator', timeout=90):
        lines = []
        center = CommandCenter(self.url, TOKEN, tasks, mode=mode, timeline_path=Path(self.folder.name) / 'timeline.json',
                               timeout_s=timeout, scenario='test', log=lines.append, tick_s=.25)
        center.lines = lines
        return center

    async def test_orchestrator_runs_tasks_answers_requests_and_completes(self):
        tasks = random_scenario(seed=5, count=3)
        self.assertGreaterEqual(len(tasks), 2)
        center = self.center(tasks)
        summary = await asyncio.wait_for(center.run(), 120)
        totals = summary['totals']
        self.assertEqual(totals['tasks'], len(tasks))
        self.assertGreaterEqual(totals['accepted'], 1)
        self.assertGreaterEqual(totals['resource_requests'], 1)
        self.assertGreaterEqual(totals['units_committed'], 1)
        self.assertGreaterEqual(totals['completed'], 1, '\n'.join(center.lines[-25:]))
        self.assertEqual(totals['errors'], 0)
        accepted = [t for t in summary['tasks'].values() if t['accepted']]
        statuses = {h['status'] for t in accepted for h in t['history']}
        self.assertTrue({'accepted', 'launched', 'resource_request', 'ground_arrived'} <= statuses, statuses)
        # every swarm resource request was answered by a committed unit
        self.assertTrue(all(rr['units'] for rr in summary['resource_requests'].values()))
        timeline = json.loads((Path(self.folder.name) / 'timeline.json').read_text(encoding='utf8'))
        self.assertEqual(timeline['totals'], totals)

    async def test_request_id_is_idempotent(self):
        task = random_scenario(seed=5, count=1)[0]
        center = self.center([task])
        center._ready = asyncio.Event()
        from websockets.asyncio.client import connect
        async with connect(self.url + f'?token={TOKEN}', max_size=None) as ws:
            center.ws = ws
            reader = asyncio.create_task(center._reader())
            try:
                await asyncio.wait_for(center._ready.wait(), 5)
                center.run_id = center.state['run_id']
                first = await center.send('agency_request', {'request_id': 'cc-idem-1', 'kind': 'sos', 'position': task['position'], 'category': task['category'], 'people': 2, 'needs': task['needs']}, command_id='cmd-a')
                again = await center.send('agency_request', {'request_id': 'cc-idem-1', 'kind': 'sos', 'position': task['position'], 'category': task['category'], 'people': 2, 'needs': task['needs']}, command_id='cmd-b')
                self.assertTrue(first['ok'] and again['ok'])
                self.assertTrue(again['result'].get('duplicate'))
                self.assertEqual(first['result']['incident_id'], again['result']['incident_id'])
                incidents = [i for i in self.engine.incidents if i.get('request_id') == 'cc-idem-1']
                self.assertEqual(len(incidents), 1)
            finally:
                reader.cancel()

    async def test_standalone_mode_commits_nothing_and_local_mock_answers(self):
        tasks = random_scenario(seed=5, count=1)
        center = self.center(tasks, mode='standalone', timeout=90)
        summary = await asyncio.wait_for(center.run(), 120)
        self.assertEqual(summary['totals']['units_committed'], 0)
        self.assertFalse(self.engine.external_ground)
        self.assertGreaterEqual(summary['totals']['accepted'], 1)

    async def test_messages_endpoint_is_paged_json(self):
        import urllib.request
        def get(path):
            with urllib.request.urlopen(f'http://127.0.0.1:{self.server.port}{path}', timeout=5) as r:
                return json.loads(r.read())
        body = await asyncio.to_thread(get, '/ops/messages?limit=5')
        self.assertIn('messages', body)
        self.assertLessEqual(len(body['messages']), 5)
        self.assertEqual(body['run_id'], self.engine.run_id)

    async def test_no_token_cannot_issue_requests(self):
        center = CommandCenter(self.url, 'wrong-token', random_scenario(seed=5, count=1), timeout_s=10, log=lambda s: None, tick_s=.2)
        summary = await asyncio.wait_for(center.run(), 30)
        self.assertEqual(summary['totals']['accepted'], 0)


class RealAdapterTests(unittest.TestCase):
    def test_dry_run_maps_without_sending(self):
        adapter = RealModeAdapter('http://localhost:9', 'x')
        task = {'kind': 'sos', 'position': [10, 20, 0], 'category': 'flood', 'people': 3, 'needs': ['rescue']}
        method, path, body = adapter.report_for_task(task, 'run-1')
        self.assertEqual((method, path), ('POST', '/api/v1/reports'))
        self.assertEqual(body['category'], 'person_stranded')
        self.assertEqual(len(body['location']), 2)
        result = adapter.call(method, path, body)
        self.assertTrue(result['dry_run'])


class SituationAndCatalogTests(unittest.TestCase):
    def test_pawana_tasks_carry_disruptionops_situation_within_message_limit(self):
        from swarm_ops.command_center import load_scenario
        tasks = load_scenario('pccoe_pawana_night')
        if not any(t.get('situation', {}).get('source', '').endswith('pccoe_pawana_night') for t in tasks):
            self.skipTest('pccoe_pawana_night scenario not registered in this checkout')
        for task in tasks:
            situation = task['situation']
            self.assertTrue(situation['report_text'])
            self.assertIn('disruptionops_category', situation)
            self.assertLessEqual(len(situation['nearby_lifelines']), 3)
            self.assertLess(len(json.dumps({'payload': task})), 8000)  # server accepts 16 KiB per message
        elderly = [t for t in tasks if 'elderly' in t['situation'].get('vulnerable', [])]
        self.assertTrue(elderly, 'the elderly couple report must be flagged from its text')

    def test_nearest_matching_catalog_unit_is_committed(self):
        from swarm_ops.command_center import load_catalog
        catalog = load_catalog()
        if not catalog.get('resources'):
            self.skipTest('no agency catalog')
        center = CommandCenter('ws://x', 't', [], mode='orchestrator', log=lambda s: None)
        center.depots = []
        fire = [r for r in catalog['resources'] if r['kind'] == 'fire_engine' and r.get('local_xy')]
        target = fire[0]['local_xy']
        unit = center._commit_unit({'needed_kind': 'fire_engine', 'request_id': 'X-RR1', 'position': [*target, 0]},
                                   {'id': 'I001', 'position': [*target, 0]})
        self.assertTrue(unit['from_catalog'])
        self.assertEqual(unit['id'], fire[0]['source_id'])  # a station at the incident is the nearest
        second = center._commit_unit({'needed_kind': 'fire_engine', 'request_id': 'X-RR2', 'position': [*target, 0]},
                                     {'id': 'I001', 'position': [*target, 0]})
        self.assertNotEqual(second['id'], unit['id'])  # a committed unit is not committed twice


if __name__ == '__main__':
    unittest.main()
