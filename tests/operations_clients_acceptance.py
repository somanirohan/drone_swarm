"""Validate copyable clients against an isolated real localhost server.

Run from the repo: python tests/operations_clients_acceptance.py
Requires Node 22+ and requirements-ops.txt. Does not mutate the live demo.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from examples.operations_ws_client import consume, local_token
from swarm_ops.server import OperationsServer
from swarm_ops.engine import OperationsSimulation


async def verify():
    checks = 0
    with tempfile.TemporaryDirectory() as directory:
        engine = OperationsSimulation(seed=42, fleet_size=5)
        server = OperationsServer(engine, control_token='example-test-only', port=0,
                                  journal_path=Path(directory)/'events.sqlite3', frame_hz=20)
        await server.start()
        base = f'http://127.0.0.1:{server.port}'
        try:
            read = await consume(base, frames=3)
            assert read['frames'] >= 3 and len(read['state']['drones']) == 5
            assert read['state']['world'] is not None and read['ack'] is None
            checks += 2
            token = await asyncio.to_thread(local_token, base)
            assert token == server.control_token
            checks += 1
            try:
                await consume(base, action='pause', frames=1)
                raise AssertionError('Unauthenticated command unexpectedly succeeded')
            except ValueError as error:
                assert 'Commands require' in str(error)
                checks += 1
            try:
                await consume(base, token='wrong-test-only', action='pause', frames=1)
                raise AssertionError('Wrong token unexpectedly authorized')
            except ValueError as error:
                assert 'authorization required' in str(error).lower()
                checks += 1
            paused = await consume(base, token, 'pause', frames=3)
            assert paused['ack']['ok'] and paused['state']['paused']
            checks += 1
            resumed = await consume(base, token, 'resume', frames=3)
            assert resumed['ack']['ok'] and not resumed['state']['paused']
            checks += 1
            previous = resumed['state']['run_id']
            switched = await consume(base, token, 'scenario', {'id':'medical_supply','seed':91}, frames=1)
            assert switched['state']['run_id'] != previous
            assert switched['state']['scenario']['id'] == 'medical_supply'
            assert switched['state']['world'] is not None
            checks += 1

            child_env = {**os.environ, 'OPS_EXAMPLE_TEST_TOKEN': token}
            process = await asyncio.create_subprocess_exec(
                'node', str(ROOT/'tests'/'operations-client-example.mjs'), base,
                cwd=ROOT, env=child_env, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            async def collect():
                result = None
                while True:
                    line = await process.stdout.readline()
                    if not line: break
                    value = line.decode().strip()
                    if value == 'REQUEST_CONNECTION_DROP':
                        for socket in list(server.clients):
                            await socket.close(code=1012, reason='Example test: reconnect')
                    elif value.startswith('{'):
                        result = json.loads(value)
                await process.wait()
                errors = (await process.stderr.read()).decode()
                assert process.returncode == 0, errors
                assert result and result['passed'], 'No JavaScript completion result'
                return result
            try:
                js = await asyncio.wait_for(collect(), timeout=25)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            print(json.dumps({'passed':True,'python_checks':checks,
                              'javascript_checks':js['checks'],
                              'total_checks':checks+js['checks'],
                              'live_demo_mutated':False}, indent=2))
        finally:
            await server.stop()


if __name__ == '__main__':
    asyncio.run(verify())
