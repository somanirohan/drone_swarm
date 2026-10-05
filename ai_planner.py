"""Asynchronous LLM mission tools; never supplies motor commands or hidden truth."""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SCHEMA = {'type':'object','properties':{
    'frontier':{'type':['integer','null']},
    'bid_tasks':{'type':'array','items':{'type':'integer'}},
    'return_home':{'type':'boolean'},
    'reason':{'type':'string'}},
    'required':['frontier','bid_tasks','return_home','reason'], 'additionalProperties':False}
INSTRUCTIONS = '''You are one autonomous rescue drone. Use only your local observation and received peer messages.
Choose an unvisited, unclaimed frontier to search; bid for known open rescue tasks when you have supply and battery.
Prioritize rescuing people, spread search with peers, replan after discoveries and failures.
Return home when unable to help. Your tool chooses mission goals; flight safety and auctions enforce constraints.
Give a short operational reason, not a chain of thought. Never invent tasks or cells.'''


class AIPlanner:
    def __init__(self, provider='openai', model=None, transport=None, max_requests=120):
        self.provider=provider
        self.model=model or os.environ.get('SWARM_AI_MODEL')
        if not self.model:
            raise ValueError('Set SWARM_AI_MODEL to a model available in your AI backend.')
        self.key=os.environ.get('OPENAI_API_KEY')
        if provider=='openai' and not self.key and transport is None:
            raise ValueError('AI mode requires OPENAI_API_KEY in this PowerShell session. Never paste it into chat.')
        self.transport=transport or self.request
        self.pool=ThreadPoolExecutor(max_workers=4)
        self.pending={}
        self.policies={}
        self.last={}
        self.requests=0
        self.accepted=0
        self.errors=0
        self.max_requests=max_requests
        self.events=[]

    def request(self, observation):
        if self.provider=='openai':
            payload={'model':self.model,'store':False,'instructions':INSTRUCTIONS,
                'input':json.dumps(observation),
                'tools':[{'type':'function','name':'set_mission_goal',
                          'description':'Choose search frontier, rescue bids and return goal.',
                          'parameters':SCHEMA,'strict':True}],
                'tool_choice':{'type':'function','name':'set_mission_goal'},
                'parallel_tool_calls':False}
            url='https://api.openai.com/v1/responses'
            headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'}
        else:
            payload={'model':self.model,'stream':False,'format':SCHEMA,
                     'messages':[{'role':'system','content':INSTRUCTIONS},
                                 {'role':'user','content':json.dumps(observation)}]}
            url='http://127.0.0.1:11434/api/chat'
            headers={'Content-Type':'application/json'}
        req=urllib.request.Request(url,json.dumps(payload).encode(),headers=headers)
        with urllib.request.urlopen(req,timeout=25) as response:
            result=json.load(response)
        if self.provider=='openai':
            calls=[item for item in result.get('output',[]) if item.get('type')=='function_call' and item.get('name')=='set_mission_goal']
            if len(calls)!=1:
                raise ValueError('AI did not return one mission tool call')
            return json.loads(calls[0]['arguments'])
        return json.loads(result['message']['content'])

    @staticmethod
    def validate(action, agent, now):
        if not isinstance(action,dict) or set(action)!=set(SCHEMA['required']):
            raise ValueError('Invalid mission tool fields')
        cell=action['frontier']
        if cell is not None and (type(cell)!=int or not 0<=cell<len(agent.cells)):
            raise ValueError('Invalid frontier')
        bids=action['bid_tasks']
        if not isinstance(bids,list) or any(type(k)!=int or k not in agent.tasks for k in bids):
            raise ValueError('Unknown rescue task')
        if type(action['return_home'])!=bool or not isinstance(action['reason'],str):
            raise ValueError('Invalid goal types')
        claimed={c for drone,(c,stamp) in agent.claims.items() if drone!=agent.id and now-stamp<3}
        safe=dict(action)
        if cell in agent.covered or cell in claimed:
            safe['frontier']=None
        safe['bid_tasks']=[k for k in bids if agent.tasks[k]['status']=='open' and agent.available(agent.id,now)]
        safe['reason']=safe['reason'][:300]
        return safe

    def policy(self, agent, pos, now):
        wall=time.monotonic()
        pending=self.pending.get(agent.id)
        if pending and pending.done():
            del self.pending[agent.id]
            try:
                policy=self.validate(pending.result(),agent,now)
                self.policies[agent.id]=(wall,policy)
                self.accepted+=1
                event={'time':round(now,2),'event':'ai_tool','drone':agent.id+1,**policy}
            except Exception as exc:
                self.errors+=1
                # Never log HTTP bodies, credentials or full exception strings.
                event={'time':round(now,2),'event':'ai_error','drone':agent.id+1,'type':type(exc).__name__}
            self.events.append(event)
            print('AI: '+json.dumps(event),flush=True)
        stamp,policy=self.policies.get(agent.id,(0,{}))
        if wall-stamp>60:
            policy={}
        needs_plan=(agent.frontier is None or agent.frontier in agent.covered or
                    any(k not in policy.get('bid_tasks',[]) for k,t in agent.tasks.items() if t['status']=='open'))
        interval=5 if needs_plan else 20
        if (agent.id not in self.pending and wall-self.last.get(agent.id,-100)>=interval
                and agent.active and agent.state not in ('RETURN','COMPLETE') and self.requests<self.max_requests):
            observation={'drone':agent.id+1,'position':pos.tolist(),'state':agent.state,
                'battery':agent.battery,'supply':agent.supply,'previous_goal':policy,
                'cells':[{'id':i,'xy':c.tolist()} for i,c in enumerate(agent.cells) if i not in agent.covered],
                'claims':{str(d):c for d,(c,t) in agent.claims.items() if now-t<3},
                'tasks':{str(k):{'point':t['point'].tolist(),'status':t['status']} for k,t in agent.tasks.items()},
                'owners':{str(k):o[0]+1 for k,o in agent.owners.items()},
                'peers':{str(d+1):data for d,(stamp,data) in agent.peers.items() if now-stamp<3}}
            self.pending[agent.id]=self.pool.submit(self.transport,observation)
            self.last[agent.id]=wall
            self.requests+=1
        return self.validate(policy,agent,now) if policy else {}

    def close(self):
        self.pool.shutdown(wait=False,cancel_futures=True)

    def metrics(self):
        return {'ai_provider':self.provider,'ai_model':self.model,'ai_requests':self.requests,
                'ai_accepted':self.accepted,'ai_errors':self.errors,
                'ai_status':'ACTIVE' if self.accepted else 'WAITING / HOLD',
                'ai_request_limit':self.max_requests}
