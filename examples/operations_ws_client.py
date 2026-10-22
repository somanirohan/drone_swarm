"""Read-only by default; explicit operator commands for development exercises.

python examples/operations_ws_client.py --frames 3
python examples/operations_ws_client.py --local-session --action pause
python examples/operations_ws_client.py --local-session --action random_incident --payload '{"category":"medical"}' --watch 30
"""
import argparse
import asyncio
import json
import os
import time
import uuid
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode

from websockets.asyncio.client import connect

ACTIONS = ('scenario', 'random_scenario', 'reset', 'pause', 'resume', 'speed', 'fleet',
           'fault', 'release_resource', 'ack_handoff', 'detect', 'random_incident')


def describe(state, printed):
    """Print only what is new: ground dispatches, signals and orchestrator-facing status events.
    `printed` is a set of already printed keys, so reconnects or repeated frames stay quiet."""
    by_id = {r['id']: r for r in state.get('resources', [])}
    for e in state.get('events', []):
        key = ('e', e['id'])
        if key in printed:continue
        printed.add(key)
        if e['kind'] == 'ground_dispatch':
            rid = e.get('resource_id') or (e['message'].split(' ')[0])
            r = by_id.get(rid, {})
            print(f"DISPATCH t={e['time']}s {rid} kind={r.get('kind')} agency={r.get('agency')} -> {e.get('incident_id')} "
                  f"state={r.get('state')} eta={r.get('eta')} route_points={len(r.get('route') or [])}")
        elif e.get('status'):
            print(f"STATUS   t={e['time']}s {e['status']} incident={e.get('incident_id')} request={e.get('request_id')} data={e.get('data')}")
    for sig in state.get('signals', []):
        key = ('s', sig['id'])
        if key in printed:continue
        printed.add(key)
        if sig['kind'] in ('sos', 'dispatch', 'resource_request', 'supply_request', 'recall', 'arrival'):
            print(f"SIGNAL   {sig['kind']} {sig['from_type']}:{sig['from_id']} -> {sig['to_ids']} incident={sig.get('incident_id')} {sig.get('text','')}")
    agency = state.get('metrics', {}).get('agency')
    return agency


async def consume(base_url, token='', action=None, payload=None, frames=3, watch=0.0):
    base=urlsplit(base_url)
    path=base.path.rstrip('/')+'/ops/ws'
    uri=urlunsplit(('wss' if base.scheme=='https' else 'ws',base.netloc,path,
                   urlencode({'token':token}) if token else '', ''))
    run=None;sequence=-1;world=None;state=None;seen=0;ack=None;identifier=None;first=True;printed=set();agency=None;deadline=None
    async with connect(uri, proxy=None, max_size=4*1024*1024, open_timeout=10,
                       ping_interval=20, ping_timeout=30) as socket:
        async with asyncio.timeout(15+watch):
            while (seen<max(1,frames) or action and ack is None or
                   ack and ack.get('result',{}).get('run_id') not in (None,state['run_id']) or
                   watch and time.monotonic()<(deadline or 0)):
                packet=json.loads(await socket.recv())
                if packet.get('type')=='ack':
                    if packet.get('id')==identifier:
                        ack=packet
                        if not ack['ok']:raise ValueError(str(ack.get('error','Command rejected')))
                    continue
                if packet.get('type')!='snapshot' or packet.get('protocol')!='disruptionops.swarm.v2':continue
                next_state=packet['state']
                if next_state.get('schema_version')!=2:raise ValueError('Unsupported snapshot schema')
                if next_state['run_id']!=run:run=next_state['run_id'];sequence=-1;world=None
                if packet['seq']<=sequence:continue
                sequence=packet['seq'];world=next_state.get('world',world);state={**next_state,'world':world};seen+=1
                if deadline is None:deadline=time.monotonic()+watch
                agency=describe(state,printed)
                if first and action:
                    if not token:raise ValueError('Commands require --local-session or an explicit token environment value')
                    identifier=str(uuid.uuid4())
                    await socket.send(json.dumps({'type':'command','id':identifier,'expected_run_id':run,
                                                  'action':action,'payload':payload or {}}))
                first=False
    return {'state':state,'ack':ack,'frames':seen,'agency':agency}


def local_token(base_url):
    parsed=urlsplit(base_url)
    if parsed.hostname not in ('localhost','127.0.0.1','::1'):
        raise ValueError('--local-session is only for direct loopback development')
    with urllib.request.urlopen(base_url.rstrip('/')+'/ops/session',timeout=5) as response:
        session=json.load(response)
    if not session.get('control_token'):raise ValueError('This request did not receive a local operator session')
    return session['control_token']


async def main(args):
    token=os.environ.get(args.token_env,'')
    if args.local_session:token=await asyncio.to_thread(local_token,args.base_url)
    raw=Path(args.payload_file).read_text(encoding='utf-8-sig') if args.payload_file else args.payload
    payload=json.loads(raw)
    if not isinstance(payload,dict):raise ValueError('Payload must be a JSON object')
    result=await consume(args.base_url,token,args.action,payload,args.frames,args.watch)
    state=result['state']
    print(json.dumps({'run_id':state['run_id'],'scenario':state['scenario']['id'],
                      'fleet':len(state['drones']),'paused':state['paused'],
                      'open_incidents':state['metrics']['open_incidents'],
                      'cached_world':state['world'] is not None,'agency_metrics':result['agency'],'frames':result['frames'],
                      'command_ack':result['ack']},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://localhost:8787')
    parser.add_argument('--frames',type=int,default=3)
    parser.add_argument('--action',choices=ACTIONS)
    parser.add_argument('--payload',default='{}',help='JSON object')
    parser.add_argument('--payload-file',help='UTF-8 JSON object file; avoids shell quoting problems')
    parser.add_argument('--token-env',default='OPS_FRIEND_TOKEN')
    parser.add_argument('--watch',type=float,default=0.0,help='keep reading this many seconds, printing dispatches, signals and status events')
    parser.add_argument('--local-session',action='store_true')
    args=parser.parse_args()
    asyncio.run(main(args))
