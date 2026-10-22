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
        self.assertTrue({'accepted', 'launched', 'dispatch_ordered', 'ground_arrived'} <= statuses, statuses)
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


# ----------------------------------------------------------------------------- orchestrated-mode matrix (docs/TEST-AUDIT.md gaps G1-G21)
class OrchestratedMatrix(unittest.IsolatedAsyncioTestCase):
    """A DisruptionOps-controlled swarm over the real WebSocket API (integration token), server on port 0, engine at 16x."""

    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        with mock.patch.dict(os.environ, {'OPS_INTEGRATION_TOKEN': TOKEN}):
            self.engine = OperationsSimulation(seed=11, fleet_size=20)
            self.server = OperationsServer(self.engine, control_token='matrix-operator', port=0,
                                           journal_path=Path(self.folder.name) / 'events.sqlite3', frame_hz=20)
        self.engine.speed = 16
        await self.server.start()
        self.url = f'ws://127.0.0.1:{self.server.port}/ops/ws'
        import random
        self.rng = random.Random(5)
        self.n = 0

    async def asyncTearDown(self):
        await self.server.stop()
        self.folder.cleanup()

    # -- helpers
    async def client(self, token=TOKEN):
        center = CommandCenter(self.url, token, [], log=lambda s: None, tick_s=.2)
        await center.open()
        self.addAsyncCleanup(center.close)
        return center

    def site(self, category='medical', soft=False):
        position, _ = self.engine._random_site(category, self.rng)
        if position is None and not soft:
            self.fail(f'no valid site for {category}')
        return position

    def rid(self):
        self.n += 1
        return f'matrix-{self.n}'

    def incident(self, ack):
        return next(i for i in self.engine.incidents if i['id'] == ack['result']['incident_id'])

    def drone(self, drone_id):
        return next(d for d in self.engine.drones if d['id'] == drone_id)

    def ext(self, unit_id, kind, site, dx=300):
        """An external (DisruptionOps-owned) unit placed dx metres from the incident; the swarm creates it on assignment."""
        return {'id': unit_id, 'kind': kind, 'agency': 'DisruptionOps', 'position': [site[0] + dx, site[1], 0]}

    def res(self, unit_id):
        return next(r for r in self.engine.resources if r['id'] == unit_id)

    async def until(self, predicate, timeout=45, what='condition'):
        end = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < end:
            if predicate():
                return
            await asyncio.sleep(.1)
        self.fail(f'timed out waiting for {what}')

    async def orchestrate(self, center, category='medical', drones=None, resources=None, site=None, **extra):
        assignments = {'drones': drones if drones is not None else [], 'resources': resources or []}
        ack = await center.orchestrate(self.rid(), site or self.site(category), category, assignments, external_id=f'DO-{self.n}', people=3, **extra)
        self.assertTrue(ack['ok'], ack)
        return ack

    # -- full scripted run in orchestrated mode through CommandCenter.run()
    async def test_scripted_orchestrated_run_completes(self):
        tasks = random_scenario(seed=5, count=2)
        center = CommandCenter(self.url, TOKEN, tasks, mode='orchestrated', timeline_path=Path(self.folder.name) / 'o.json', timeout_s=90,
                               scenario='orch', log=lambda s: None, tick_s=.25)
        summary = await asyncio.wait_for(center.run(), 120)
        self.assertEqual(summary['totals']['accepted'], len(tasks))
        self.assertTrue(all(t['reply_kind'] in ('accept_full', 'accept_partial', 'delayed') for t in summary['tasks'].values()))
        self.assertEqual(summary['totals']['resource_requests'], 0, 'orchestrated mode must not generate swarm resource requests')
        self.assertGreaterEqual(summary['totals']['completed'], 1)

    # -- G1
    async def test_g01_orchestrated_incident_with_drone_and_ambulance(self):
        center = await self.client()
        site = self.site()
        ack = await self.orchestrate(center, drones=[{'count': 1, 'role': 'assess'}], resources=[self.ext('EXT-AMB-1', 'ambulance', site)], site=site)
        ambulance = self.res('EXT-AMB-1')
        self.assertEqual(ack['result']['reply_kind'], 'accept_full')
        self.assertEqual(len(ack['result']['units']), 1)
        incident = self.incident(ack)
        self.assertEqual(incident['control_mode'], 'orchestrated')
        self.assertEqual(incident['external_id'], f'DO-{self.n}')
        await self.until(lambda: ambulance['incident_id'] == incident['id'] and ambulance['state'] in ('enroute', 'awaiting_handoff', 'on_scene'), what='ambulance dispatched')
        await self.until(lambda: self.drone(ack['result']['units'][0])['task_id'] == incident['id'], what='drone tasked')
        await self.until(lambda: incident['handed_over'], 90, 'ground arrival and handoff')

    # -- G2
    async def test_g02_ambulance_only_swarm_does_not_self_launch(self):
        center = await self.client()
        site = self.site()
        ack = await self.orchestrate(center, resources=[self.ext('EXT-AMB-2', 'ambulance', site)], site=site)
        incident = self.incident(ack)
        await asyncio.sleep(2.5)  # ~40 simulated seconds at 16x
        self.assertEqual(ack['result']['units'], [])
        self.assertEqual(incident['assigned_drones'], [])
        self.assertFalse([d for d in self.engine.drones if d['task_id'] == incident['id']], 'a drone self-launched')
        self.assertEqual(incident['resource_requests'], [], 'the swarm raised resource requests on an orchestrated incident')

    # -- G3
    async def test_g03_assignment_added_later(self):
        center = await self.client()
        ack = await self.orchestrate(center, drones=[{'count': 1, 'role': 'assess'}])
        incident = self.incident(ack)
        late = await center.assign(incident['id'], resources=[self.ext('EXT-AMB-3', 'ambulance', incident['position'])])
        self.assertTrue(late['ok'], late)
        ambulance = self.res('EXT-AMB-3')
        await self.until(lambda: ambulance['incident_id'] == incident['id'] and ambulance['state'] != 'available', what='late ambulance dispatched')
        self.assertIn(ambulance['id'], incident['assigned_units'])

    # -- G4
    async def test_g04_recall_drone(self):
        center = await self.client()
        ack = await self.orchestrate(center, drones=[{'count': 1, 'role': 'assess'}])
        incident, drone_id = self.incident(ack), ack['result']['units'][0]
        await self.until(lambda: self.drone(drone_id)['task_id'] == incident['id'], what='drone tasked')
        recall = await center.recall_drone(drone_id, 'DO re-prioritised')
        self.assertTrue(recall['ok'], recall)
        await self.until(lambda: self.drone(drone_id)['task_id'] != incident['id'], what='drone released')
        self.assertNotIn(drone_id, incident['assigned_drones'])
        self.assertEqual(self.drone(drone_id)['state'], 'returning')

    # -- G5
    async def test_g05_cancel_and_close(self):
        center = await self.client()
        for action, outcome in (('cancel', 'cancelled'), ('close', 'completed')):
            ack = await self.orchestrate(center, drones=[{'count': 1, 'role': 'assess'}])
            incident, drone_id = self.incident(ack), ack['result']['units'][0]
            await self.until(lambda: self.drone(drone_id)['task_id'] == incident['id'], what='drone tasked')
            done = await (center.cancel_incident if action == 'cancel' else center.close_incident)(incident['id'], 'DO says so')
            self.assertTrue(done['ok'], done)
            self.assertEqual(incident['status'], 'resolved')
            self.assertEqual(incident['outcome'], outcome)
            self.assertNotEqual(self.drone(drone_id)['task_id'], incident['id'])

    # -- G6
    async def test_g06_update_people_and_severity(self):
        center = await self.client()
        incident = self.incident(await self.orchestrate(center, drones=[{'count': 1, 'role': 'assess'}]))
        update = await center.update_incident(incident['id'], people=9, severity=5, situation={'report_text': 'worse than reported'})
        self.assertTrue(update['ok'], update)
        self.assertEqual((incident['people'], incident['severity']), (9, 5))
        self.assertEqual(incident['situation_report']['report_text'], 'worse than reported')

    # -- G7
    async def test_g07_autonomous_and_orchestrated_concurrently(self):
        center = await self.client()
        orchestrated = self.incident(await self.orchestrate(center, drones=[{'count': 1, 'role': 'assess'}]))
        auto = await center.send('agency_request', {'request_id': self.rid(), 'kind': 'sos', 'position': self.site('flood'), 'category': 'flood', 'people': 3, 'needs': ['rescue']})
        self.assertTrue(auto['ok'] and auto['result']['accepted'], auto)
        autonomous = self.incident(auto)
        self.assertNotEqual(autonomous.get('control_mode'), 'orchestrated')
        self.assertEqual(orchestrated['control_mode'], 'orchestrated')
        await asyncio.sleep(3)
        mine = {d['id'] for d in self.engine.drones if d['task_id'] == orchestrated['id']}
        self.assertEqual(mine, set(orchestrated['assigned_drones']), 'swarm added drones to an orchestrated incident')
        self.assertEqual(orchestrated['resource_requests'], [])
        self.assertTrue(autonomous['resource_requests'] or autonomous['required_kinds'], 'autonomous incident should still ask for ground help')

    # -- G8
    async def test_g08_ground_first_and_drone_first(self):
        center = await self.client()
        near = self.site('medical')
        ack = await center.orchestrate(self.rid(), near, 'medical', {'drones': [{'count': 1, 'role': 'assess'}], 'resources': [
            {'id': 'EXT-AMB-NEAR', 'kind': 'ambulance', 'agency': 'DO', 'position': [near[0] + 15, near[1], 0], 'eta_s': 2}]}, people=2)
        incident = self.incident(ack)
        await self.until(lambda: incident.get('first_on_scene') is not None, 90, 'first on scene (ground case)')
        self.assertEqual(incident['first_on_scene'], 'ground')
        far = self.site('flood')
        b = self.engine.world.get('bounds', [-600, -600, 600, 600])
        corner = [b[0] + 5 if far[0] > 0 else b[2] - 5, b[1] + 5 if far[1] > 0 else b[3] - 5, 0]
        ack = await center.orchestrate(self.rid(), far, 'flood', {'drones': [{'count': 1, 'role': 'assess'}], 'resources': [
            {'id': 'EXT-RESCUE-FAR', 'kind': 'rescue', 'agency': 'DO', 'position': corner}]}, people=2)
        incident = self.incident(ack)
        await self.until(lambda: incident.get('first_on_scene') is not None, 120, 'first on scene (drone case)')
        self.assertEqual(incident['first_on_scene'], 'drone')

    # -- G9
    async def test_g09_unit_freed_and_nearer_unit_reassigned(self):
        center = await self.client()
        site = self.site('medical')
        ack = await center.orchestrate(self.rid(), site, 'medical', {'drones': [], 'resources': [
            {'id': 'EXT-AMB-FAR', 'kind': 'ambulance', 'agency': 'DO', 'position': [site[0] + 400, site[1], 0]}]}, people=2)
        incident = self.incident(ack)
        far = next(r for r in self.engine.resources if r['id'] == 'EXT-AMB-FAR')
        await self.until(lambda: far['incident_id'] == incident['id'], what='far unit dispatched')
        freed = await center.unassign(incident['id'], resource_ids=['EXT-AMB-FAR'])
        self.assertTrue(freed['ok'], freed)
        self.assertEqual(far['state'], 'available')
        near = await center.assign(incident['id'], resources=[{'id': 'EXT-AMB-NEAR', 'kind': 'ambulance', 'agency': 'DO', 'position': [site[0] + 30, site[1], 0]}])
        self.assertTrue(near['ok'], near)
        nearer = next(r for r in self.engine.resources if r['id'] == 'EXT-AMB-NEAR')
        await self.until(lambda: nearer['incident_id'] == incident['id'], what='nearer unit dispatched')
        self.assertEqual(incident['assigned_units'], ['EXT-AMB-NEAR'])

    # -- G10
    async def test_g10_duplicate_request_id(self):
        center = await self.client()
        payload = {'request_id': 'matrix-dup', 'position': self.site(), 'category': 'medical', 'people': 2, 'assignments': {'drones': [{'count': 1, 'role': 'assess'}]}}
        first = await center.send('orchestrate_incident', payload, command_id='dup-a')
        again = await center.send('orchestrate_incident', payload, command_id='dup-b')
        self.assertTrue(first['ok'] and again['ok'])
        self.assertTrue(again['result'].get('duplicate'))
        self.assertEqual(first['result']['incident_id'], again['result']['incident_id'])
        self.assertEqual(len([i for i in self.engine.incidents if i.get('request_id') == 'matrix-dup']), 1)

    # -- G11
    async def test_g11_outside_bounds_decline_jurisdiction(self):
        center = await self.client()
        ack = await center.orchestrate(self.rid(), [99999, 99999, 0], 'medical', {'drones': [{'count': 1, 'role': 'assess'}]})
        self.assertTrue(ack['ok'])
        self.assertFalse(ack['result']['accepted'])
        self.assertEqual(ack['result']['reply_kind'], 'decline_jurisdiction')
        agency = await center.send('agency_request', {'request_id': self.rid(), 'kind': 'sos', 'position': [99999, 99999], 'category': 'medical'})
        self.assertEqual(agency['result']['reply_kind'], 'decline_jurisdiction')

    # -- G12
    async def test_g12_missing_position_need_info(self):
        center = await self.client()
        ack = await center.send('orchestrate_incident', {'request_id': self.rid(), 'category': 'medical', 'assignments': {}})
        self.assertTrue(ack['ok'])
        self.assertFalse(ack['result']['accepted'])
        self.assertEqual(ack['result']['reply_kind'], 'need_info')
        self.assertFalse([i for i in self.engine.incidents if i.get('control_mode') == 'orchestrated'])

    # -- G13
    async def test_g13_capacity_reached_decline_capacity(self):
        from swarm_ops.engine import MAX_OPEN_INCIDENTS
        center = await self.client()
        center.min_gap_s = .12
        for category in ['medical', 'flood', 'fire', 'trapped', 'blocked_route'] * 8:
            if self.engine._open_incidents() >= MAX_OPEN_INCIDENTS:
                break
            site = self.site(category, soft=True)
            if site is not None:
                await center.orchestrate(self.rid(), site, category, {})
        self.assertGreaterEqual(self.engine._open_incidents(), MAX_OPEN_INCIDENTS)
        ack = await center.orchestrate(self.rid(), self.site('medical'), 'medical', {})
        self.assertFalse(ack['result']['accepted'])
        self.assertEqual(ack['result']['reply_kind'], 'decline_capacity')

    # -- G14
    async def test_g14_partial_accept(self):
        # Launch caps exist on docked fleets: run this case on the dock_fire scenario (cap 3 airborne).
        engine = OperationsSimulation(seed=11, fleet_size=20, scenario='dock_fire')
        with mock.patch.dict(os.environ, {'OPS_INTEGRATION_TOKEN': TOKEN}):
            server = OperationsServer(engine, control_token='x', port=0, journal_path=Path(self.folder.name) / 'dock.sqlite3', frame_hz=20)
        await server.start()
        self.addAsyncCleanup(server.stop)
        center = CommandCenter(f'ws://127.0.0.1:{server.port}/ops/ws', TOKEN, [], log=lambda s: None)
        await center.open()
        self.addAsyncCleanup(center.close)
        position, _ = engine._random_site('medical', self.rng)
        ack = await center.orchestrate(self.rid(), position, 'medical', {'drones': [{'count': 6, 'role': 'overwatch'}]})
        self.assertTrue(ack['result']['accepted'], ack)
        launched = len(ack['result']['units'])
        self.assertEqual(ack['result']['reply_kind'], 'accept_partial', ack['result'])
        self.assertTrue(0 < launched < 6)

    # -- G15
    async def test_g15_autonomy_paused_and_resumed(self):
        center = await self.client()
        paused = await center.control('paused', 'DO emergency stop')
        self.assertTrue(paused['ok'], paused)
        self.assertEqual(self.engine.control['autonomy'], 'paused')
        blocked = await center.orchestrate(self.rid(), self.site(), 'medical', {'drones': [{'count': 1, 'role': 'assess'}]})
        self.assertEqual(blocked['result']['reply_kind'], 'decline_capacity')
        agency = await center.send('agency_request', {'request_id': self.rid(), 'kind': 'sos', 'position': self.site(), 'category': 'medical'})
        self.assertFalse(agency['result']['accepted'])
        resumed = await center.control('running')
        self.assertTrue(resumed['ok'])
        ok = await center.orchestrate(self.rid(), self.site('flood'), 'flood', {'drones': [{'count': 1, 'role': 'assess'}]})
        self.assertTrue(ok['result']['accepted'], ok)

    # -- G16
    async def test_g16_faults_during_orchestrated_mission(self):
        center = await self.client()
        for fault in ('crash', 'link', 'battery'):
            ack = await self.orchestrate(center, drones=[{'count': 1, 'role': 'assess'}])
            incident, drone_id = self.incident(ack), ack['result']['units'][0]
            await self.until(lambda: self.drone(drone_id)['task_id'] == incident['id'], what='drone tasked')
            result = self.engine.command('fault', {'drone_id': drone_id, 'kind': fault})
            self.assertTrue(result['ok'], result)
            await asyncio.sleep(1.5)
            with self.subTest(fault=fault):
                self.assertNotEqual(incident['status'], 'resolved', 'incident lost on a drone fault')
                others = [d['id'] for d in self.engine.drones if d['task_id'] == incident['id'] and d['id'] != drone_id]
                self.assertEqual(others, [], 'swarm self-assigned a replacement to an orchestrated incident')
            await center.cancel_incident(incident['id'], 'matrix cleanup')

    # -- G17
    async def test_g17_reconnect_mid_mission(self):
        first = await self.client()
        site = self.site()
        ack = await self.orchestrate(first, drones=[{'count': 1, 'role': 'assess'}], resources=[self.ext('EXT-AMB-17', 'ambulance', site)], site=site)
        incident_id = ack['result']['incident_id']
        await first.close()
        second = await self.client()
        seen = next(i for i in second.state['incidents'] if i['id'] == incident_id)
        self.assertEqual(seen['control_mode'], 'orchestrated')
        self.assertTrue(seen['assigned_drones'])
        replay = await second.send('orchestrate_incident', {'request_id': f'matrix-{self.n}', 'position': seen['position'], 'category': seen['category'], 'assignments': {}})
        self.assertTrue(replay['result'].get('duplicate'))
        def get():
            import urllib.request
            with urllib.request.urlopen(f'http://127.0.0.1:{self.server.port}/ops/events?after=0&limit=200', timeout=5) as r:
                return json.loads(r.read())
        await asyncio.sleep(.5)
        events = (await asyncio.to_thread(get))['events']
        self.assertTrue(any(e.get('incident_id') == incident_id for e in events), 'journal replay lacks the incident events')

    # -- G18
    async def test_g18_view_only_client_cannot_command(self):
        viewer = await self.client(token='')
        ack = await viewer.orchestrate(self.rid(), self.site(), 'medical', {})
        self.assertFalse(ack['ok'])
        self.assertFalse([i for i in self.engine.incidents if i.get('control_mode') == 'orchestrated'])
        for action, payload in (('control', {'autonomy': 'paused'}), ('cancel_incident', {'incident_id': 'I001'})):
            self.assertFalse((await viewer.send(action, payload))['ok'])
        self.assertNotEqual(self.engine.control['autonomy'], 'paused')

    # -- G19
    async def test_g19_integration_vs_operator_permissions(self):
        operator = await self.client(token='matrix-operator')
        for action, payload in (('orchestrate_incident', {'request_id': self.rid(), 'position': self.site(), 'category': 'medical'}), ('control', {'autonomy': 'paused'}),
                                ('agency_request', {'request_id': self.rid(), 'kind': 'sos', 'position': self.site()}), ('recall_drone', {'drone_id': 'D01'})):
            ack = await operator.send(action, payload)
            self.assertFalse(ack['ok'], f'operator token must not run integration command {action}')
        self.assertTrue((await operator.send('pause', {}))['ok'])
        integration = await self.client()
        self.assertFalse((await integration.send('reset', {}))['ok'], 'integration token must not run operator commands')

    # -- G20
    async def test_g20_malformed_payloads(self):
        center = await self.client()
        site = self.site()
        bad = [('orchestrate_incident', {'request_id': self.rid(), 'position': ['x', 'y'], 'category': 'medical'}),
               ('orchestrate_incident', {'request_id': '', 'position': site, 'category': 'medical'}),
               ('assign', {'incident_id': 'NOPE', 'drones': []}), ('unassign', {}), ('recall_drone', {'drone_id': 'NOPE'}),
               ('update_incident', {'incident_id': 'NOPE', 'people': 3}), ('cancel_incident', {'incident_id': 'NOPE'}),
               ('control', {'autonomy': 'maybe'}), ('control', {})]
        for action, payload in bad:
            with self.subTest(action=action, payload=payload):
                ack = await center.send(action, payload)
                self.assertFalse(ack['ok'], ack)
        incident = self.incident(await self.orchestrate(center, drones=[]))
        for fields in ({'people': 'many'}, {'severity': {}}):
            with self.subTest(update=fields):
                ack = await center.update_incident(incident['id'], **fields)
                self.assertFalse(ack['ok'], ack)
        self.assertTrue((await center.update_incident(incident['id'], people=2))['ok'], 'connection must survive bad payloads')

    # -- G21
    async def test_g21_rate_limit_applies_to_integration_session(self):
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed
        async with connect(self.url + f'?token={TOKEN}', max_size=None) as ws:
            await ws.recv()
            for i in range(14):
                await ws.send(json.dumps({'type': 'command', 'id': f'burst-{i}', 'action': 'control', 'payload': {'autonomy': 'running'}}))
            with self.assertRaises(ConnectionClosed):
                async with asyncio.timeout(5):
                    while True:
                        await ws.recv()


if __name__ == '__main__':
    unittest.main()
