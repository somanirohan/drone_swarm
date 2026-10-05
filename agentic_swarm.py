"""Independent rule-based drone agents with delayed messages and task auctions.

No agent receives the simulator's survivor list. The runtime supplies local sensor
events. The coordinator below transports messages and applies a flight safety
filter; task and exploration decisions are made inside each DroneAgent.
"""
from dataclasses import dataclass, field
import heapq
import numpy as np


class MessageBus:
    def __init__(self, latency=.15):
        self.latency=latency
        self.queue=[]
        self.sequence=0
        self.sent=0

    def broadcast(self, sender, kind, data, now):
        self.sequence+=1
        self.sent+=1
        heapq.heappush(self.queue,(now+self.latency,self.sequence,sender,kind,data))

    def deliver(self, agents, now):
        while self.queue and self.queue[0][0]<=now:
            _,_,sender,kind,data=heapq.heappop(self.queue)
            for agent in agents:
                if agent.active and agent.id!=sender:
                    agent.receive(sender,kind,data,now)


@dataclass
class DroneAgent:
    id: int
    home: np.ndarray
    cruise: float
    cells: np.ndarray
    bus: MessageBus
    total_tasks: int=3
    active: bool=True
    battery: float=1.
    supply: bool=True
    state: str='TAKEOFF'
    tasks: dict=field(default_factory=dict)
    peers: dict=field(default_factory=dict)
    bids: dict=field(default_factory=dict)
    owners: dict=field(default_factory=dict)
    covered: set=field(default_factory=set)
    claims: dict=field(default_factory=dict)
    task: int|None=None
    frontier: int|None=None
    last_tick: float=-1.
    last_heartbeat: float=-1.
    descended: bool=False
    landing: bool=False
    detour: np.ndarray|None=None
    detour_until: float=0.
    replans: int=0
    decisions: int=0
    planner: object=None
    ai_return: bool=False

    def emit(self, kind, data, now):
        self.receive(self.id,kind,data,now)
        self.bus.broadcast(self.id,kind,data,now)

    def receive(self, sender, kind, data, now):
        if kind=='heartbeat':
            self.peers[sender]=(now,data)
        elif kind=='discovery':
            key,point=data
            self.tasks.setdefault(key,{'point':np.array(point),'seen':now,'status':'open'})
        elif kind=='bid':
            key,cost=data
            self.bids.setdefault(key,{})[sender]=(cost,now)
        elif kind=='claim':
            key,cost=data
            if key in self.tasks and self.tasks[key]['status']=='open':
                old=self.owners.get(key)
                if old is None or (cost,sender)<(old[2],old[0]):
                    self.owners[key]=(sender,now,cost)
        elif kind=='released':
            if data in self.tasks:
                self.tasks[data]['status']='released'
        elif kind=='delivered':
            if data in self.tasks:
                self.tasks[data]['status']='done'
            self.owners.pop(data,None)
        elif kind=='offline':
            self.peers[sender]=(now,{'active':False,'state':'UNAVAILABLE','battery':0})
            for key,owner in list(self.owners.items()):
                if owner[0]==sender:
                    del self.owners[key]
                    self.replans+=1
        elif kind=='coverage':
            self.covered.add(data)
        elif kind=='frontier':
            self.claims[sender]=(data,now)

    def observe_survivor(self, key, point, now):
        if self.active and key not in self.tasks:
            self.emit('discovery',(key,np.array(point).tolist()),now)

    def available(self, drone, now):
        if drone==self.id:
            return self.active and self.supply and self.battery>.22
        seen,data=self.peers.get(drone,(-100,{}))
        return now-seen<3 and data.get('active',False) and data.get('supply',False) and data.get('battery',0)>.22

    def decide(self, pos, vel, now):
        self.decisions+=1
        policy=self.planner.policy(self,pos,now) if self.planner else None
        if policy and policy.get('return_home'):
            self.ai_return=True
        if now-self.last_heartbeat>=.5 and self.active:
            self.emit('heartbeat',{'active':True,'state':self.state,'battery':self.battery,'supply':self.supply},now)
            self.last_heartbeat=now
        for key,owner in list(self.owners.items()):
            if not self.available(owner[0],now) and self.tasks[key]['status']=='open':
                del self.owners[key]
                self.replans+=1
        # Each agent publishes its own bid; there is no central nearest-drone selector.
        for key,task in sorted(self.tasks.items()):
            if task['status']=='open' and self.available(self.id,now):
                if policy is not None and key not in policy.get('bid_tasks',[]):
                    continue
                cost=float(np.linalg.norm(pos[:2]-task['point'][:2])+20*(1-self.battery))
                self.emit('bid',(key,cost),now)
        occupied={owner[0] for key,owner in self.owners.items() if self.tasks[key]['status']=='open'}
        for key,task in sorted(self.tasks.items()):
            if task['status']!='open' or key in self.owners or now-task['seen']<2:
                continue
            bidders=[(cost,drone) for drone,(cost,stamp) in self.bids.get(key,{}).items()
                     if now-stamp<2 and self.available(drone,now) and drone not in occupied]
            if bidders:
                cost,winner=min(bidders)
                if winner==self.id:
                    self.emit('claim',(key,cost),now)
                    occupied.add(self.id)
        own=[key for key,owner in self.owners.items() if owner[0]==self.id and self.tasks[key]['status']=='open']
        new_task=min(own) if own else None
        if new_task!=self.task:
            self.task=new_task
            self.descended=False
            self.replans+=1
        if not self.active or self.battery<.2 or self.ai_return:
            self.state='RETURN'
        elif len([task for task in self.tasks.values() if task['status']=='done'])>=self.total_tasks:
            self.state='RETURN'
        elif self.state=='TAKEOFF' and pos[2]<self.cruise-.2:
            return
        elif self.task is not None:
            self.state='DELIVER'
        elif not self.supply:
            self.state='RELAY'
        else:
            self.state='EXPLORE'
        if self.state=='EXPLORE':
            if self.frontier is not None and np.linalg.norm(pos[:2]-self.cells[self.frontier])<.6:
                self.emit('coverage',self.frontier,now)
                self.frontier=None
            claimed={cell for drone,(cell,stamp) in self.claims.items() if drone!=self.id and now-stamp<3}
            conflict=any(drone<self.id and cell==self.frontier and now-stamp<3
                         for drone,(cell,stamp) in self.claims.items())
            if self.frontier in self.covered or conflict:
                self.frontier=None
            if self.frontier is None:
                options=[cell for cell in range(len(self.cells)) if cell not in self.covered and cell not in claimed]
                if policy is not None:
                    proposed=policy.get('frontier')
                    if proposed in options:
                        self.frontier=proposed
                        self.replans+=1
                elif options:
                    # A soft sector preference spreads the team without fixed routes.
                    preferred=(-11.25,0,11.25)[self.id//4]
                    self.frontier=min(options,key=lambda cell:np.linalg.norm(pos[:2]-self.cells[cell])+.6*abs(self.cells[cell,0]-preferred))
                    self.replans+=1
            if self.frontier is not None:
                self.emit('frontier',self.frontier,now)

    def goal(self, pos, vel, now):
        if now-self.last_tick>=.5:
            self.decide(pos,vel,now)
            self.last_tick=now
        if self.state in ('RETURN','COMPLETE'):
            target=np.array([*self.home[:2],self.cruise])
            if np.linalg.norm(pos[:2]-self.home[:2])<.12:
                self.landing=True
            if self.landing:
                target[2]=.14
                if pos[2]<.22:
                    self.state='COMPLETE'
        elif self.state=='DELIVER' and self.task is not None:
            target=np.array([*self.tasks[self.task]['point'][:2],self.cruise])
            if np.linalg.norm(pos[:2]-target[:2])<.12 and np.linalg.norm(vel)<.2:
                self.descended=True
            if self.descended:
                target[2]=.85
        elif self.state=='EXPLORE' and self.frontier is not None:
            target=np.array([*self.cells[self.frontier],self.cruise])
        elif self.state=='RELAY':
            target=np.array([self.home[0],self.home[1]+3,self.cruise])
        else:
            target=np.array([*self.home[:2],self.cruise])
        if self.detour is not None and now<self.detour_until:
            target=self.detour.copy()
        elif target[2]>=self.cruise and pos[2]<self.cruise-.5:
            target[:2]=pos[:2]
        return target


class AgenticSwarm:
    def __init__(self, initial, cruise_height, failure_demo=False, planner=None):
        self.bus=MessageBus()
        cells=np.array([[x,y] for x in [-13,-9,-5,-1,3,7,11,13] for y in [-8,-4,0,4,8]])
        self.agents=[DroneAgent(i,home.copy(),cruise_height+.35*5*(i//4),cells,self.bus) for i,home in enumerate(initial)]
        self.planner=planner
        for agent in self.agents:
            agent.planner=planner
        self.initial=initial
        self.time=0.
        self.found={}
        self.delivered=set()
        self.released=set()
        self.assignments={}
        self.phase='TAKEOFF'
        self.failure_demo=failure_demo
        self.failed=None
        self.events=[]

    def discover(self, key, point, observer):
        self.agents[observer].observe_survivor(key,point,self.time)
        self.found.setdefault(key,np.array(point).copy())
        self.events.append({'time':round(self.time,2),'event':'discovery','drone':observer+1,'site':key+1})

    def release(self, key, drone):
        self.released.add(key)
        agent=self.agents[drone]
        agent.supply=False
        agent.emit('released',key,self.time)
        self.events.append({'time':round(self.time,2),'event':'release','drone':drone+1,'site':key+1})

    def confirm_delivery(self, key):
        self.delivered.add(key)
        drone=self.assignments.get(key,0)
        self.agents[drone].emit('delivered',key,self.time)
        self.events.append({'time':round(self.time,2),'event':'landed','site':key+1})

    def obstacle(self, drone, pos, direction):
        agent=self.agents[drone]
        if self.time<agent.detour_until:
            return
        side=np.array([-direction[1],direction[0],0.])
        if np.linalg.norm(side)<.01:
            side=np.array([1.,0,0])
        agent.detour=pos+side/np.linalg.norm(side)*1.2+[0,0,.8]
        agent.detour_until=self.time+3
        agent.replans+=1
        self.events.append({'time':round(self.time,2),'event':'obstacle_detour','drone':drone+1})

    def update(self, positions, velocities):
        self.time+=1/48
        self.bus.deliver(self.agents,self.time)
        targets=[]
        for agent,pos,vel in zip(self.agents,positions,velocities):
            # Illustrative battery consumption, not an electrical battery model.
            agent.battery=max(0.,agent.battery-(.00035+.0002*np.linalg.norm(vel))/48)
            targets.append(agent.goal(pos,vel,self.time))
        new_assignments={}
        for agent in self.agents:
            if agent.active and agent.state=='DELIVER' and agent.task not in self.released:
                new_assignments[agent.task]=agent.id
        for key,drone in new_assignments.items():
            if self.assignments.get(key)!=drone:
                self.events.append({'time':round(self.time,2),'event':'assignment','drone':drone+1,'site':key+1})
        self.assignments.update(new_assignments)
        # Demonstrate an agent losing mission availability, while its PID performs
        # a controlled recovery; this does not pretend to simulate motor failure.
        if self.failure_demo and self.failed is None and new_assignments:
            key,drone=next(iter(new_assignments.items()))
            agent=self.agents[drone]
            agent.active=False
            agent.state='RETURN'
            agent.task=None
            agent.emit('offline',None,self.time)
            self.failed=drone
            self.assignments.pop(key,None)
            targets[drone]=agent.goal(positions[drone],velocities[drone],self.time)
            self.events.append({'time':round(self.time,2),'event':'unavailable','drone':drone+1,'site':key+1})
        for key,drone in list(self.assignments.items()):
            if key not in self.released and (not self.agents[drone].active or self.agents[drone].task!=key):
                del self.assignments[key]
        states=[agent.state for agent in self.agents]
        self.phase='COMPLETE' if all(state=='COMPLETE' for state in states) else 'RETURN' if len(self.delivered)==3 else 'AGENTIC SEARCH / RESCUE'
        if self.planner:
            self.phase+=' | AI '+('ACTIVE' if self.planner.accepted else 'WAITING / HOLD')
        desired=.8*(np.array(targets)-positions)
        for i in range(len(desired)):
            speed=np.linalg.norm(desired[i])
            if speed>.65:
                desired[i]*=.65/speed
        # Shared deterministic safety filter; high-level decisions remain local.
        for _ in range(6):
            for i in range(len(positions)):
                for j in range(i):
                    delta=positions[i]-positions[j]
                    distance=np.linalg.norm(delta)
                    if .001<distance<.8:
                        normal=delta/distance
                        approach=np.dot(desired[i]-desired[j],normal)
                        closing=max(0.,-np.dot(velocities[i]-velocities[j],normal))
                        bound=-1.5*(distance-.4)+.5*closing
                        if approach<bound:
                            correction=.5*(bound-approach)*normal
                            desired[i]+=correction
                            desired[j]-=correction
        for i,agent in enumerate(self.agents):
            if agent.state in ('RETURN','COMPLETE') and positions[i,2]<.5:
                desired[i,2]=max(desired[i,2],-.08)
        return desired

    def metrics(self):
        result={'agent_states':{str(a.id+1):a.state for a in self.agents},
                'covered_cells':len(set().union(*(a.covered for a in self.agents))),
                'messages':self.bus.sent,'replans':sum(a.replans for a in self.agents),
                'failed_agent':None if self.failed is None else self.failed+1,
                'battery':{str(a.id+1):round(a.battery,3) for a in self.agents}}
        if self.planner:
            result.update(self.planner.metrics())
        return result
