"""Bounded HTTP/WS preflight; reads secrets from a file and never prints them."""
import argparse
import asyncio
import json
import sys
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect

PROTOCOL = 'disruptionops.swarm.v2'


def session(base):
    with urllib.request.urlopen(base + '/ops/session', timeout=10) as response:
        return json.load(response)


async def verify(base, token, orchestrator=None, require_public=False):
    parsed = urlsplit(base.rstrip('/'))
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('Expected an HTTP(S) base URL')
    uri = urlunsplit(('wss' if parsed.scheme == 'https' else 'ws', parsed.netloc,
                     parsed.path.rstrip('/') + '/ops/ws', '', ''))

    async def receive(socket, kind, identifier=None):
        while True:
            packet = json.loads(await socket.recv())
            if packet.get('type') == kind and (identifier is None or packet.get('id') == identifier):
                return packet

    async with asyncio.timeout(25):
        if require_public:
            public_session = await asyncio.to_thread(session, base.rstrip('/'))
            if public_session.get('can_control') or public_session.get('control_token'):
                raise ValueError('Public session leaked operator authorization')
        async with connect(uri, proxy=None, max_size=8*1024*1024, open_timeout=10) as viewer:
            packet = await receive(viewer, 'snapshot')
            if packet.get('protocol') != PROTOCOL or packet['state'].get('schema_version') != 2:
                raise ValueError('Unsupported swarm protocol/schema')
            identifier = str(uuid.uuid4())
            await viewer.send(json.dumps({'type':'command', 'id':identifier, 'action':'pause', 'payload':{}}))
            ack = await receive(viewer, 'ack', identifier)
            if ack.get('ok') or ack.get('error') != 'Operator authorization required':
                raise ValueError('Read-only connection unexpectedly permitted controls')
        async with connect(uri+'?'+urlencode({'token':token}), proxy=None,
                           max_size=8*1024*1024, open_timeout=10) as integration:
            packet = await receive(integration, 'snapshot')
            state = packet['state']
            identifier = str(uuid.uuid4())
            # Invalid task checks authorization without dispatching or modifying an incident.
            await integration.send(json.dumps({'type':'command','id':identifier,
                'expected_run_id':state['run_id'],'action':'request_situation',
                'payload':{'incident_id':'__connection_preflight_nonexistent__'}}))
            ack = await receive(integration, 'ack', identifier)
            if ack.get('ok') or ack.get('error') != 'Unknown incident':
                raise ValueError('Integration authentication/version check failed')
            if orchestrator:
                identifier = str(uuid.uuid4())
                await integration.send(json.dumps({'type':'command','id':identifier,
                    'expected_run_id':state['run_id'],'action':'control',
                    'payload':{'orchestrator':orchestrator}}))
                ack = await receive(integration, 'ack', identifier)
                if not ack.get('ok') or ack.get('result',{}).get('orchestrator') != orchestrator:
                    raise ValueError('Orchestrator control did not acknowledge the requested mode')
            coordination = state.get('coordination', 'central')
            if isinstance(coordination, dict): coordination = coordination.get('mode','central')
            return {'verified':True,'protocol':PROTOCOL,'run_id':state['run_id'],
                    'fleet':len(state['drones']),'coordination':coordination,
                    'integration_authenticated':True,'viewer_read_only':True,
                    'public_session_read_only':True if require_public else None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--orchestrator', choices=('external','internal'))
    parser.add_argument('--require-public', action='store_true')
    args = parser.parse_args()
    try:
        token = Path(args.token_file).read_text(encoding='utf-8-sig').strip()
        print(json.dumps(asyncio.run(verify(args.base_url, token, args.orchestrator, args.require_public))))
    except Exception as error:
        print(f'Preflight failed ({type(error).__name__}); check server, network and private token.', file=sys.stderr)
        raise SystemExit(1)
