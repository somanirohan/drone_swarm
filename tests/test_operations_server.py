"""Transport/security/state-contract tests using real localhost WebSockets."""
import asyncio
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from swarm_ops.server import EventJournal, OperationsServer, PROTOCOL
from swarm_ops.engine import OperationsSimulation


class JournalTests(unittest.TestCase):
    def test_restart_dedup_and_ordered_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'events.sqlite3'
            first = EventJournal(path)
            first.append('run-a', [{'id':'one','message':'detected'},{'id':'two','message':'dispatched'}])
            first.append('run-a', [{'id':'one','message':'duplicate'}])
            first.close()
            second = EventJournal(path)
            second.append('run-b', [{'id':'one','message':'new run'}])
            rows = second.replay()
            self.assertEqual([r['message'] for r in rows], ['detected','dispatched','new run'])
            self.assertEqual(second.replay(rows[0]['cursor'])[0]['message'], 'dispatched')
            second.close()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = OperationsSimulation(seed=23, fleet_size=20)
        self.server = OperationsServer(self.engine, control_token='test-only-operator', port=0,
                                       journal_path=Path(self.folder.name) / 'events.sqlite3', frame_hz=20)
        await self.server.start()
        self.base = f'http://127.0.0.1:{self.server.port}'
        self.ws_url = f'ws://127.0.0.1:{self.server.port}/ops/ws'

    async def asyncTearDown(self):
        await self.server.stop()
        self.folder.cleanup()

    async def receive(self, ws, kind, command_id=None):
        async with asyncio.timeout(4):
            while True:
                message = json.loads(await ws.recv())
                if message['type']==kind and (command_id is None or message.get('id')==command_id):
                    return message

    def envelope(self, identifier, action='pause', payload=None, **extra):
        return json.dumps({'type':'command','id':identifier,'action':action,'payload':payload or {},**extra})

    async def http(self, path, headers=None):
        def read():
            request = urllib.request.Request(self.base+path, headers=headers or {})
            try:
                with urllib.request.urlopen(request, timeout=4) as response:
                    return response.status, response.headers, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.headers, error.read()
        return await asyncio.to_thread(read)

    async def test_initial_world_then_bounded_frame(self):
        async with connect(self.ws_url) as ws:
            first = await self.receive(ws, 'snapshot')
            self.assertEqual(first['protocol'], PROTOCOL)
            self.assertEqual(len(first['state']['drones']),20)
            self.assertIn('world',first['state'])
            next_frame = await self.receive(ws,'snapshot')
            self.assertGreater(next_frame['seq'],first['seq'])
            self.assertNotIn('world',next_frame['state'])

    async def test_viewer_cannot_mutate(self):
        async with connect(self.ws_url) as ws:
            await self.receive(ws,'snapshot')
            await ws.send(self.envelope('readonly'))
            ack=await self.receive(ws,'ack','readonly')
            self.assertFalse(ack['ok'])
            self.assertFalse(self.engine.paused)

    async def test_wrong_token_cannot_mutate(self):
        async with connect(self.ws_url+'?token=wrong') as ws:
            await ws.send(self.envelope('bad-token'))
            self.assertFalse((await self.receive(ws,'ack','bad-token'))['ok'])

    async def test_pause_resume_round_trip(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            for action in ('pause','resume'):
                await ws.send(self.envelope(action,action))
                self.assertTrue((await self.receive(ws,'ack',action))['ok'])
                self.assertEqual(self.engine.paused,action=='pause')

    async def test_duplicate_reset_is_idempotent_across_reconnect(self):
        command=self.envelope('reset-once','reset')
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            await ws.send(command)
            ack=await self.receive(ws,'ack','reset-once')
            self.assertTrue(ack['ok'])
            run=self.engine.run_id
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            await ws.send(command)
            self.assertEqual(await self.receive(ws,'ack','reset-once'),ack)
            self.assertEqual(self.engine.run_id,run)

    async def test_command_id_payload_collision_rejected(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            await ws.send(self.envelope('same-id','pause'))
            self.assertTrue((await self.receive(ws,'ack','same-id'))['ok'])
            await ws.send(self.envelope('same-id','resume'))
            self.assertFalse((await self.receive(ws,'ack','same-id'))['ok'])
            self.assertTrue(self.engine.paused)

    async def test_stale_run_command_rejected(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            await ws.send(self.envelope('stale','fault',{'drone_id':'D01','kind':'crash'},expected_run_id='old-run'))
            self.assertFalse((await self.receive(ws,'ack','stale'))['ok'])
            self.assertTrue(self.engine.drones[0]['active'])

    async def test_reset_frame_includes_world(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            old=await self.receive(ws,'snapshot')
            await ws.send(self.envelope('scenario','scenario',{'id':'medical_supply','seed':123}))
            self.assertTrue((await self.receive(ws,'ack','scenario'))['ok'])
            frame=await self.receive(ws,'snapshot')
            self.assertNotEqual(frame['state']['run_id'],old['state']['run_id'])
            self.assertIn('world',frame['state'])
            self.assertEqual(frame['state']['scenario']['id'],'medical_supply')

    async def test_malformed_commands_and_unknown_action_rejected(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            for raw in ('{bad', '[]', json.dumps({'type':'command','id':'one','payload':{}}),self.envelope('unknown','launch_physical_drone')):
                await ws.send(raw)
                self.assertFalse((await self.receive(ws,'ack'))['ok'])
            self.assertFalse(self.engine.paused)

    async def test_invalid_speed_rejected_without_state_change(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            for value in (-1, 999, 'NaN'):
                await ws.send(self.envelope(str(value),'speed',{'value':value}))
                self.assertFalse((await self.receive(ws,'ack',str(value)))['ok'])
            self.assertEqual(self.engine.speed,1)

    async def test_premature_handoff_rejected(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            incident=self.engine.incidents[0]
            resource=next(r for r in self.engine.resources if r['incident_id']==incident['id'])
            await ws.send(self.envelope('early','ack_handoff',{'incident_id':incident['id'],'resource_id':resource['id']}))
            self.assertFalse((await self.receive(ws,'ack','early'))['ok'])
            self.assertFalse(incident['handed_over'])

    async def test_two_viewers_receive_same_run_and_monotonic_sequence(self):
        async with connect(self.ws_url) as a, connect(self.ws_url) as b:
            first_a,first_b=await asyncio.gather(self.receive(a,'snapshot'),self.receive(b,'snapshot'))
            self.assertEqual(first_a['state']['run_id'],first_b['state']['run_id'])
            frames=await asyncio.gather(self.receive(a,'snapshot'),self.receive(b,'snapshot'))
            self.assertTrue(all(f['state']['run_id']==self.engine.run_id for f in frames))
            self.assertGreater(frames[0]['seq'],first_a['seq'])

    async def test_command_rate_limit_disconnects(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            for index in range(12):
                await ws.send(self.envelope(f'flood-{index}'))
            with self.assertRaises(ConnectionClosed) as closed:
                while True:
                    await ws.recv()
            self.assertEqual(closed.exception.rcvd.code,1008)

    async def test_oversize_message_disconnects(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            await ws.send('x'*20000)
            with self.assertRaises(ConnectionClosed) as closed:
                while True:
                    await ws.recv()
            self.assertEqual(closed.exception.rcvd.code,1009)

    async def test_http_session_does_not_leak_token_to_forwarded_requests(self):
        status,headers,body=await self.http('/ops/session')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['control_token'],'test-only-operator')
        status,headers,body=await self.http('/ops/session',{'CF-Connecting-IP':'203.0.113.8'})
        self.assertNotIn('control_token',json.loads(body))
        self.assertIn('no-store',headers['Cache-Control'])

    async def test_http_path_traversal_rejected(self):
        status,_,_=await self.http('/web/%2e%2e/.env')
        self.assertEqual(status,404)
        for path in ('/web/ops/%2e%2e/%2e%2e/.env','/web/legacy/..%2f..%2f.env','/assets/%2e%2e/.env'):
            status,_,_=await self.http(path)
            self.assertEqual(status,404,path)

    async def test_viewer_urls_new_and_old(self):
        # New layout (web/ops, web/legacy) plus the old /web/operations.* links friends already have.
        for path,marker in (('/',b'operations.js'),('/web/ops/operations.html',b'operations.js'),
                            ('/web/operations.html',b'operations.js'),('/legacy',b'importmap'),
                            ('/web/legacy/index.html',b'importmap'),('/web/index.html',b'importmap')):
            status,_,body=await self.http(path)
            self.assertEqual(status,200,path)
            self.assertIn(marker,body,path)
        for path in ('/web/ops/operations.js','/web/operations.js','/web/ops/operations.css','/web/operations.css',
                     '/web/vendor/three.module.js'):
            status,_,_=await self.http(path)
            self.assertEqual(status,200,path)

    async def test_cross_origin_ws_rejected(self):
        with self.assertRaises(InvalidStatus) as rejected:
            async with connect(self.ws_url,origin='https://untrusted.example'):
                self.fail('Cross-origin handshake unexpectedly succeeded')
        self.assertEqual(rejected.exception.response.status_code,403)

    async def test_nonfinite_and_overflow_commands_rejected_without_disconnect(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            for raw in ('{"type":"command","id":"nan","action":"speed","payload":{"value":NaN}}',
                        '{"type":"command","id":"overflow","action":"scenario","payload":{"seed":1e999}}'):
                await ws.send(raw)
                self.assertFalse((await self.receive(ws,'ack'))['ok'])
            await ws.send(self.envelope('still-alive','pause'))
            self.assertTrue((await self.receive(ws,'ack','still-alive'))['ok'])

    async def test_unicode_token_is_read_only(self):
        async with connect(self.ws_url+'?token=%E2%98%83') as ws:
            await ws.send(self.envelope('unicode-token'))
            self.assertFalse((await self.receive(ws,'ack','unicode-token'))['ok'])

    async def test_slow_client_retains_world_until_reset_frame_delivered(self):
        marker = object()
        queue = asyncio.Queue(maxsize=2)
        self.server.clients[marker] = {'queue':queue,'run_id':self.engine.run_id}
        try:
            self.engine.command('reset')
            self.engine.command('pause')
            await asyncio.sleep(.22)
            self.assertEqual(queue.qsize(),2)
            while not queue.empty():
                raw, delivered_run = queue.get_nowait()
                packet = json.loads(raw)
                self.assertEqual(delivered_run,self.engine.run_id)
                self.assertIn('world',packet['state'])
        finally:
            self.server.clients.pop(marker,None)

    async def test_fleet_detect_and_random_incident_commands(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            await ws.send(self.envelope('fleet-5','fleet',{'value':5}))
            self.assertTrue((await self.receive(ws,'ack','fleet-5'))['ok'])
            self.assertEqual(len(self.engine.drones),5)
            await ws.send(self.envelope('fleet-bad','fleet',{'value':101}))
            self.assertFalse((await self.receive(ws,'ack','fleet-bad'))['ok'])
            await ws.send(self.envelope('sos','detect',{'position':[-150,-160],'category':'trapped','people':2}))
            ack=await self.receive(ws,'ack','sos')
            self.assertTrue(ack['ok']);self.assertTrue(ack['incident_id'])
            await ws.send(self.envelope('rnd','random_incident',{'category':'medical'}))
            ack=await self.receive(ws,'ack','rnd')
            self.assertTrue(ack['ok']);self.assertEqual(ack['result']['category'],'medical')
            frame=await self.receive(ws,'snapshot')
            self.assertIn('signals',frame['state']);self.assertIn('people',frame['state']);self.assertIn('search_map',frame['state'])

    async def test_agency_request_requires_integration_token(self):
        async with connect(self.ws_url+'?token=test-only-operator') as ws:
            await ws.send(self.envelope('orch','agency_request',{'request_id':'R-1','kind':'sos','position':[-100,-200]}))
            self.assertFalse((await self.receive(ws,'ack','orch'))['ok'])
        self.server.integration_token='integration-test-token'
        async with connect(self.ws_url+'?token=integration-test-token') as ws:
            await ws.send(self.envelope('orch','agency_request',{'request_id':'R-1','kind':'sos','position':[-100,-200],'category':'medical'}))
            ack=await self.receive(ws,'ack','orch')
            self.assertTrue(ack['ok']);self.assertTrue(ack['result']['accepted']);self.assertTrue(ack['incident_id'])

    async def test_event_http_replay_is_ordered(self):
        await asyncio.sleep(.12)
        status,_,body=await self.http('/ops/events?limit=2')
        data=json.loads(body)
        self.assertEqual(status,200)
        self.assertEqual(len(data['events']),2)
        self.assertLess(data['events'][0]['cursor'],data['events'][1]['cursor'])
        _,_,next_body=await self.http(f"/ops/events?after={data['next_cursor']}&limit=2")
        self.assertTrue(all(e['cursor']>data['next_cursor'] for e in json.loads(next_body)['events']))


if __name__=='__main__':
    unittest.main()
