"""Exercise anonymous permissions through the deployed HTTP/WebSocket entrypoint."""
import asyncio
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path

from websockets.asyncio.client import connect

from swarm_ops.cli import DeployedServer
from swarm_ops.config import Settings
from swarm_ops.engine import OperationsSimulation


class PublicDemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_deployed_anonymous_access_is_explicit(self):
        for enabled in (False, True):
            with self.subTest(public_demo=enabled), tempfile.TemporaryDirectory() as folder:
                settings = Settings(public_demo=enabled, control_token='private-control',
                                    integration_token='private-integration')
                engine = OperationsSimulation(seed=42, fleet_size=5)
                server = DeployedServer(engine, settings, {}, port=0, frame_hz=1,
                                        control_token=settings.control_token,
                                        journal_path=Path(folder) / 'events.sqlite3')
                await server.start()
                try:
                    def session_request():
                        request = urllib.request.Request(
                            f'http://127.0.0.1:{server.port}/ops/session',
                            headers={'X-Forwarded-For': '203.0.113.1'})
                        with urllib.request.urlopen(request, timeout=4) as response:
                            return json.load(response)

                    session = await asyncio.to_thread(session_request)
                    self.assertEqual(session['can_control'], enabled)
                    self.assertNotIn('control_token', session)
                    async with connect(f'ws://127.0.0.1:{server.port}/ops/ws') as ws:
                        async with asyncio.timeout(6):
                            await ws.recv()
                            for action, payload in [('pause', {}), ('control', {'orchestrator': 'external'})]:
                                await ws.send(json.dumps({'type': 'command', 'id': action,
                                                          'action': action, 'payload': payload}))
                                while True:
                                    ack = json.loads(await ws.recv())
                                    if ack.get('type') == 'ack' and ack.get('id') == action:
                                        break
                                self.assertEqual(ack['ok'], enabled, ack)
                            self.assertEqual(engine.paused, enabled)
                finally:
                    await server.stop()

    def test_configuration_requires_opt_in(self):
        self.assertFalse(Settings.from_env({}).public_demo)
        for value in ('true', '1', 'yes', ' TRUE '):
            self.assertTrue(Settings.from_env({'OPS_PUBLIC_DEMO': value}).public_demo)
        self.assertFalse(Settings.from_env({'OPS_PUBLIC_DEMO': 'false'}).public_demo)
        self.assertTrue(Settings(public_demo=True).public_summary()['public_demo'])
