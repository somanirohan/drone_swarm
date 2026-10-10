"""Deterministic disaster-operations demonstrator; kinematics, not flight physics.

Allocations are feasible local bids reconciled by this simulation harness. This
is deliberately NOT a hardware swarm, decentralized consensus, or medical AI.
Ground dispatch, observational evidence and actual service completion are
separate from drone arrival and supply drops.
"""
from __future__ import annotations

import copy
import heapq
import math
import random
import uuid


ANCHOR = [18.6526632, 73.7624378]
SCENARIOS = {
    "rooftop_flood": ("Rooftop flood rescue", "Flood water strands families; rescue boats and reconnaissance respond together."),
    "medical_supply": ("Urgent medical supply", "An injured resident needs first-aid supplies and an ambulance."),
    "blocked_bridge": ("Blocked bridge evacuation", "A damaged crossing blocks evacuation; engineers and rescue teams need an aerial assessment."),
}


def _distance(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(min(len(a), len(b)))))


def _project_segment(position, a, b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    fraction=max(0,min(1,((position[0]-a[0])*dx+(position[1]-a[1])*dy)/max(1e-12,dx*dx+dy*dy)))
    return [a[0]+fraction*dx,a[1]+fraction*dy],fraction


def _inside(p, polygon):
    result = False
    j = len(polygon) - 1
    for i, q in enumerate(polygon):
        r = polygon[j]
        if (q[1] > p[1]) != (r[1] > p[1]) and p[0] < (r[0] - q[0]) * (p[1] - q[1]) / (r[1] - q[1]) + q[0]:
            result = not result
        j = i
    return result


def _default_world():
    roads = []
    for i, v in enumerate(range(-500, 501, 100)):
        roads.append({"id": f"east-{i}", "points": [[-500, v], [500, v]], "blocked": False})
        roads.append({"id": f"north-{i}", "points": [[v, -500], [v, 500]], "blocked": False})
    buildings = []
    for i, (x, y) in enumerate([(-260, 160), (230, 250), (280, -220), (-150, -160)]):
        buildings.append({"id": f"B{i}", "footprint": [[x-22,y-22],[x+22,y-22],[x+22,y+22],[x-22,y+22]], "height": 18+i*4, "kind": "residential"})
    return {"bounds": [-600,-600,600,600], "roads": roads, "buildings": buildings,
            "river": [[-600,330],[-200,320],[200,340],[600,320]], "river_width": 70,
            "bases": [{"id":"base","position":[-440,-440,0],"kind":"operations"}],
            "shelters": [{"id":"shelter","position":[-350,-400,0],"kind":"shelter"}], "no_fly": []}


class OperationsSimulation:
    """JSON snapshots and commands for a local, single-authority demo server."""

    def __init__(self, world=None, seed=42, fleet_size=20):
        self.world = copy.deepcopy(world or _default_world())
        self.fleet_size = max(1, min(100, int(fleet_size)))
        self.seed = int(seed)
        self._initialize("rooftop_flood")

    def _initialize(self, scenario):
        self.rng = random.Random(self.seed)
        self.run_id = str(uuid.uuid4())
        self.revision = 0
        self.t = 0.0
        self.paused = False
        self.speed = 1.0
        self.events = []
        self.incidents = []
        self._event_seq = 0
        self._faults = 0
        self.external_ground = False
        self._last_allocate = -10
        self.flood={"enabled":True,"width":float(self.world.get("river_width",45))+90,"level":2.1,"synthetic":True,"model":"river corridor; prescribed depth, not hydrodynamics"}
        self.road_conditions=[]
        self._coverage = {(x,y):None for x in range(-400,401,200) for y in range(-400,401,200)}
        self._base = list(self.world.get("bases", [{"position": [-440,-440,0]}])[0]["position"])
        self._base = (self._base + [0,0,0])[:3]
        self.scenario = {"id": scenario, "name": SCENARIOS[scenario][0], "description": SCENARIOS[scenario][1], "seed": self.seed}
        self.drones = []
        for i in range(self.fleet_size):
            self.drones.append({"id": f"D{i+1:02}", "position": [self._base[0]+(i%5)*5,self._base[1]+(i//5)*5,0],
                "heading": 0.0, "state": "ready", "battery": 0.82+self.rng.random()*.16,
                "energy_wh": 0.0, "task_id": None, "payload": ["first_aid", "food", "medical"][i%3],
                "active": True, "route": [], "link": True, "capacity_wh": 110.0, "cruise": 65.0+(i%4)*6,
                "delivered": False, "payload_stock":1,"payload_capacity":1,"last_telemetry": 0.0, "coverage_task": None, "lane": (i%5)*8.0})
            self.drones[-1]["energy_wh"] = self.drones[-1]["battery"]*110
        self.resources = []
        for i, kind in enumerate(["ambulance","ambulance","rescue","rescue","engineering"]):
            self.resources.append({"id": f"R{i+1:02}", "kind": kind, "state": "available" if i%2==0 else "busy",
                "position": [self._base[0]+i*12,self._base[1],0], "incident_id": None, "route": [], "eta": None,
                "agency": {"ambulance":"EMS","rescue":"District rescue","engineering":"Public works"}[kind],
                "capacity": 6 if kind=="rescue" else 2, "free_at": 12.0+i*3 if i%2 else None,
                "arrived_at": None, "acknowledged": False,"transport":"boat" if i==2 else "road","load":0})
            if i==2:
                staging=self._river_projection(self._base)
                if staging:self.resources[-1]["position"]=[*staging[1],self.flood["level"]]
        self._build_roads()
        self._create_scenario(scenario)
        self._event("scenario", f"{self.scenario['name']} started; {self.fleet_size} drones ready.")
        self._allocate()

    def _event(self, kind, message, incident_id=None):
        self._event_seq += 1
        entry = {"id": f"{self.run_id}:{self._event_seq}", "run_id":self.run_id,"time": round(self.t,2), "kind":kind, "message":message}
        if incident_id:
            entry["incident_id"] = incident_id
        self.events.append(entry)
        self.events = self.events[-180:]

    def _incident(self, position, category, title, severity, people, needs, required, source="scenario"):
        # Detection deduplication is spatial + category, with severity escalation.
        for incident in self.incidents:
            if incident["category"] == category and _distance(incident["position"],position)<35 and incident["status"]!="resolved":
                incident["severity"] = max(severity, incident["severity"])
                incident["people"] = max(people,incident["people"])
                self._event("deduplicated", "Repeated observation merged into existing incident.", incident["id"])
                return incident
        incident = {"id":f"I{len(self.incidents)+1:03}","position":list(position),"category":category,"title":title,
                    "severity":severity,"people":people,"needs":list(needs),"status":"reported","drone_id":None,
                    "resource_ids":[],"briefing":None,"handed_over":False,"required_kinds":list(required),
                    "completed_kinds":[],"supplied":[],"created_at":self.t,"uncertain_delivery":False,
                    "source":source,"wait_reason":None,"observations":0,"last_observed":None}
        incident["evacuated_people"]=0
        self.incidents.append(incident)
        self._event("incident",title,incident["id"])
        return incident

    def _create_scenario(self, scenario):
        buildings = self.world.get("buildings",[])
        candidate = None
        candidates=[]
        bounds=self.world.get("bounds",[-600,-600,600,600])
        for building in buildings:
            footprint = building.get("footprint",[])
            if footprint:
                # Ignore a duplicated closing vertex when locating the roof.
                points=footprint[:-1] if footprint[0]==footprint[-1] else footprint
                center=[sum(p[0] for p in points)/len(points),sum(p[1] for p in points)/len(points),float(building.get("height",20))]
                projection=self._river_projection(center)
                if projection and bounds[0]+30<center[0]<bounds[2]-30 and bounds[1]+30<center[1]<bounds[3]-30:
                    candidates.append((projection[0],building,center))
        if candidates:
            near=sorted(candidates,key=lambda b:b[0])
            pool=[entry for entry in near if entry[0]<self.flood["width"]/2-10]
            if not pool:
                pool=near[:max(1,min(4,len(near)))];self.flood["width"]=max(self.flood["width"],2*(pool[-1][0]+15))
            _,building,candidate=self.rng.choice(pool)
            self.scenario["roof_building_id"]=building["id"]
            self.scenario["roof_height_source"]=building.get("height_source","scenario estimate")
        else:
            projection=self._river_projection([0,100,0])
            candidate=[*(projection[1] if projection else [0,100]),18]
        self._apply_flood_roads()
        # Dry road-adjacent points are selected using the same seed; do not
        # fabricate ambulance access inside the flooded corridor.
        dry_nodes=[list(p)+[0] for p in self._road_graph if not self._submerged(p) and _distance(p,self._base[:2])>100 and bounds[0]+20<p[0]<bounds[2]-20 and bounds[1]+20<p[1]<bounds[3]-20]
        sampled=list(dry_nodes);self.rng.shuffle(sampled)
        reachable=[p for p in sampled[:24] if self._ground_path(self._base,p) is not None]
        dry=self.rng.choice(reachable or dry_nodes) if dry_nodes else [self._base[0]+40,self._base[1],0]
        if scenario=="rooftop_flood":
            self._incident(candidate,"flood","Families stranded on a rooftop",5,self.rng.randint(4,9),["food","rescue"],["rescue"])
            self._incident(dry,"medical","Injured person at evacuation point",4,1,["first_aid","medical"],["ambulance"])
        elif scenario=="medical_supply":
            self._incident(dry,"medical","Urgent first-aid delivery and evacuation",5,self.rng.randint(1,3),["first_aid","medical"],["ambulance"])
            self._incident(candidate,"flood","Residents need food and safe transport",3,self.rng.randint(3,7),["food","rescue"],["rescue"])
        else:
            # Scenario road closure is dynamic: do not permanently mutate static world.
            bridge_point=self._safe_crossings[0] if self._safe_crossings else [100,0]
            accessible=min(reachable or dry_nodes or [dry],key=lambda p:_distance(p,bridge_point))
            self._incident(accessible,"blocked_route","Crossing unsafe; evacuation route assessment",5,6,["assessment","rescue"],["engineering","rescue"])
            self._incident(dry,"medical","Patient isolated behind blocked crossing",4,1,["first_aid"],["ambulance"])
            if self._road_edges:
                eligible_edges=[edge for edge in self._road_edges if tuple(sorted(edge)) not in self._blocked_edges]
                blocked = min(eligible_edges or self._road_edges,key=lambda e:_distance([(e[0][0]+e[1][0])/2,(e[0][1]+e[1][1])/2],bridge_point))
                self._blocked_edges.add(tuple(sorted(blocked)))
                self.road_conditions.append({"points":[list(blocked[0]),list(blocked[1])],"blocked":True,"reason":"bridge_damage"})
                self._event("route_blocked","Damaged crossing removed from ground-route graph; dispatch must detour.")

    def _river_projection(self,position):
        river=self.world.get("river",[])
        projections=[]
        for index,(a,b) in enumerate(zip(river,river[1:])):
            point,fraction=_project_segment(position,a,b)
            projections.append((_distance(position[:2],point),point,index,fraction))
        return min(projections,key=lambda p:p[0]) if projections else None

    def _submerged(self,position):
        projection=self._river_projection(position)
        return bool(self.flood["enabled"] and projection and projection[0]<self.flood["width"]/2)

    def _elevated_crossing(self,position):
        # Two explicitly synthetic elevated crossing corridors keep a dry
        # network possible without treating every submerged road as passable.
        river=self.world.get("river",[])
        if len(river)<2:return False
        vertical=abs(river[-1][1]-river[0][1])>abs(river[-1][0]-river[0][0])
        axis=1 if vertical else 0
        return any(abs(position[axis]-point[axis])<32 and _distance(position[:2],point)<self.flood["width"]/2+70 for point in self._safe_crossings)

    def _apply_flood_roads(self):
        river=self.world.get("river",[])
        self._safe_crossings=[]
        if len(river)>1:
            self._safe_crossings=[river[max(0,len(river)//3)],river[min(len(river)-1,2*len(river)//3)]]
        self.flood["safe_crossings"]=copy.deepcopy(self._safe_crossings)
        for a,b in self._road_edges:
            count=max(2,int(_distance(a,b)/12))
            submerged=any(self._submerged(p) and not self._elevated_crossing(p) for p in ([a[0]+(b[0]-a[0])*i/count,a[1]+(b[1]-a[1])*i/count] for i in range(count+1)))
            if submerged:
                self._blocked_edges.add(tuple(sorted((a,b))))
                self.road_conditions.append({"points":[list(a),list(b)],"blocked":True,"reason":"flood"})
        self._event("flood_routes",f"{len(self.road_conditions)} road segments closed by synthetic flood; designated elevated crossings retained.")

    def _water_path(self,start,end):
        origin=self._river_projection(start);target=self._river_projection(end)
        if not origin or not target or origin[0]>self.flood["width"]/2+1 or target[0]>self.flood["width"]/2+1:return None
        river=self.world["river"];first,last=origin[2]+origin[3],target[2]+target[3]
        points=[origin[1]]
        if first<=last:
            points += [river[i] for i in range(math.floor(first)+1,math.floor(last)+1)]
        else:
            points += [river[i] for i in range(math.ceil(first)-1,math.ceil(last)-1,-1)]
        points += [target[1],end[:2]]
        return [[p[0],p[1],self.flood["level"]] for p in points]

    def _resource_path(self,resource,incident):
        if resource.get("transport")=="boat":
            if incident["category"]!="flood":return None
            return self._water_path(resource["position"],incident["position"])
        # Rooftop boat access is deliberately different from road access.
        if self._submerged(incident["position"]) and not self._elevated_crossing(incident["position"]):return None
        return self._ground_path(resource["position"],incident["position"])

    def _build_roads(self):
        # Subdivide road polylines and split intersections, enabling true graph paths.
        segments=[]
        for road in self.world.get("roads",[]):
            points=road.get("points",[])
            if road.get("blocked"):
                continue
            for a,b in zip(points,points[1:]):
                segments.append((tuple(a[:2]),tuple(b[:2])))
        splits=[{a,b} for a,b in segments]
        for i,(a,b) in enumerate(segments):
            for j,(c,d) in enumerate(segments[i+1:],i+1):
                rx,ry=b[0]-a[0],b[1]-a[1]
                sx,sy=d[0]-c[0],d[1]-c[1]
                cross=rx*sy-ry*sx
                if abs(cross)<1e-8:
                    continue
                t=((c[0]-a[0])*sy-(c[1]-a[1])*sx)/cross
                u=((c[0]-a[0])*ry-(c[1]-a[1])*rx)/cross
                if -1e-8<=t<=1+1e-8 and -1e-8<=u<=1+1e-8:
                    p=(round(a[0]+t*rx,3),round(a[1]+t*ry,3))
                    splits[i].add(p);splits[j].add(p)
        self._road_graph={}
        self._road_edges=[]
        self._blocked_edges=set()
        for (a,b),points in zip(segments,splits):
            points=sorted(points,key=lambda p:_distance(a,p))
            for x,y in zip(points,points[1:]):
                if x==y:
                    continue
                self._road_graph.setdefault(x,{})[y]=_distance(x,y)
                self._road_graph.setdefault(y,{})[x]=_distance(x,y)
                self._road_edges.append((x,y))

    def _ground_path(self,start,end):
        if not self._road_graph:
            return None
        if any(self._submerged(p) and not self._elevated_crossing(p) for p in (start,end)):return None
        # Connect to the road at its closest point, not its closest distant vertex.
        graph={a:dict(edges) for a,edges in self._road_graph.items()}
        def connector(position,label):
            best=None
            for a,b in self._road_edges:
                if tuple(sorted((a,b))) in self._blocked_edges:
                    continue
                dx,dy=b[0]-a[0],b[1]-a[1]
                ratio=max(0,min(1,((position[0]-a[0])*dx+(position[1]-a[1])*dy)/(dx*dx+dy*dy)))
                p=(a[0]+ratio*dx,a[1]+ratio*dy)
                cost=_distance(position[:2],p)
                if cost>150 or (best is not None and cost>=best[0]):continue
                checks=max(2,int(cost/12))
                if any(self._submerged(q) and not self._elevated_crossing(q) for q in ([position[0]+(p[0]-position[0])*k/checks,position[1]+(p[1]-position[1])*k/checks] for k in range(checks+1))):continue
                if best is None or cost<best[0]:
                    best=(cost,a,b,p)
            if best is None or best[0]>150:
                return None
            _,a,b,p=best
            key=(float(position[0]),float(position[1]),label)
            # One unique key per connector prevents accidentally joining islands.
            graph[key]={a:_distance(position[:2],p)+_distance(p,a),b:_distance(position[:2],p)+_distance(p,b)}
            graph[a][key]=graph[key][a];graph[b][key]=graph[key][b]
            return key,p
        first=connector(start,"start");last=connector(end,"end")
        if first is None or last is None:
            return None
        source,destination=first[0],last[0]
        queue=[(0,0,source)];costs={source:0};previous={};serial=0
        while queue:
            cost,_,node=heapq.heappop(queue)
            if cost>costs[node]:
                continue
            if node==destination:
                path=[node]
                while node in previous:
                    node=previous[node];path.append(node)
                path.reverse()
                route=[list(first[1])+[0]]+[list(p[:2])+[0] for p in path[1:-1]]+[list(last[1])+[0],[end[0],end[1],0]]
                return route
            for neighbor,length in graph[node].items():
                if len(node)==2 and len(neighbor)==2 and tuple(sorted((node,neighbor))) in self._blocked_edges:
                    continue
                value=cost+length
                if value<costs.get(neighbor,float("inf")):
                    costs[neighbor]=value;previous[neighbor]=node;serial+=1
                    heapq.heappush(queue,(value,serial,neighbor))
        return None

    def _drone_path(self, drone, target):
        altitude=max(drone["cruise"],float(target[2])+12,max((float(b.get("height",0))+12 for b in self.world.get("buildings",[])),default=0))+drone.get("lane",0)
        # Horizontal grid A* routes around restricted zones; roof surveys remain
        # above the highest obstacle. Vertical motion is simulated kinematically.
        bounds=self.world.get("bounds",[-600,-600,600,600]);spacing=35
        if not bounds[0]<=target[0]<=bounds[2] or not bounds[1]<=target[1]<=bounds[3]:return None
        nx=max(1,int((bounds[2]-bounds[0])/spacing));ny=max(1,int((bounds[3]-bounds[1])/spacing))
        polygons=[]
        for zone in self.world.get("no_fly",self.world.get("no_fly_zones",[])):
            if isinstance(zone,dict):
                polygon=zone.get("polygon",zone.get("footprint",[]))
                if not polygon and "center" in zone:
                    center=zone["center"];radius=float(zone.get("radius",30))+8
                    polygon=[[center[0]+radius*math.cos(i*math.tau/24),center[1]+radius*math.sin(i*math.tau/24)] for i in range(24)]
                polygons.append(polygon)
            else:polygons.append(zone)
        def restricted(a,b):
            count=max(2,int(_distance(a[:2],b[:2])/8)+1)
            return any(_inside([a[0]+(b[0]-a[0])*i/count,a[1]+(b[1]-a[1])*i/count],p) for p in polygons if len(p)>2 for i in range(count+1))
        if restricted(target,target) or restricted(drone["position"],drone["position"]):
            return None
        def cell(p):return (max(0,min(nx,round((p[0]-bounds[0])/spacing))),max(0,min(ny,round((p[1]-bounds[1])/spacing))))
        def point(c):return [bounds[0]+c[0]*spacing,bounds[1]+c[1]*spacing,altitude]
        source,dest=cell(drone["position"]),cell(target)
        queue=[(0,source)];costs={source:0};previous={}
        while queue:
            _,node=heapq.heappop(queue)
            if node==dest:
                path=[node]
                while node in previous:
                    node=previous[node];path.append(node)
                path.reverse()
                coords=[point(p) for p in path]
                if restricted(drone["position"],coords[0]) or restricted(coords[-1],target):
                    return None
                return [[*drone["position"][:2],altitude]]+coords+[[target[0],target[1],altitude]]
            for dx,dy in [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]:
                neighbor=(node[0]+dx,node[1]+dy)
                if not (0<=neighbor[0]<=nx and 0<=neighbor[1]<=ny) or restricted(point(node),point(neighbor)):
                    continue
                value=costs[node]+math.hypot(dx,dy)*spacing
                if value<costs.get(neighbor,float("inf")):
                    costs[neighbor]=value;previous[neighbor]=node
                    heapq.heappush(queue,(value+_distance(point(neighbor),point(dest)),neighbor))
        return None

    @staticmethod
    def _route_distance(position,route):
        return sum(_distance(a,b) for a,b in zip([position]+route,route))

    def _return(self,drone,reason):
        incident=self._find_incident(drone["task_id"])
        if incident and incident["drone_id"]==drone["id"]:
            incident["drone_id"]=None
        drone["task_id"]=None;drone["coverage_task"]=None
        path=self._drone_path(drone,self._base)
        if path is None:
            drone["state"]="holding";drone["route"]=[]
            self._event("safety_hold",f"{drone['id']} has no safe recovery path; holding for assistance.")
        else:
            drone["state"]="returning";drone["route"]=path+[[*self._base[:2],0]]
            self._event("drone_return",f"{drone['id']} returning: {reason}.",incident["id"] if incident else None)

    def _find_incident(self,identifier):
        return next((i for i in self.incidents if i["id"]==identifier),None)

    def _allocate(self):
        for incident in sorted(self.incidents,key=lambda i:(-i["severity"],i["created_at"],i["id"])):
            if incident["status"]=="resolved":continue
            incident["wait_reason"]=None
            for kind in ([] if self.external_ground else incident["required_kinds"]):
                if kind in incident["completed_kinds"] or any(r["kind"]==kind and r["incident_id"]==incident["id"] for r in self.resources):continue
                offers=[]
                for resource in self.resources:
                    if resource["state"]!="available" or resource["kind"]!=kind:continue
                    route=self._resource_path(resource,incident)
                    if route is not None:offers.append((self._route_distance(resource["position"],route),resource["id"],resource,route))
                if offers:
                    length,_,resource,route=min(offers,key=lambda offer:offer[:2])
                    resource.update(state="enroute",incident_id=incident["id"],route=route,eta=length/(7 if resource.get("transport")=="boat" else 9),acknowledged=False,arrived_at=None)
                    if resource["id"] not in incident["resource_ids"]:incident["resource_ids"].append(resource["id"])
                    self._event("ground_dispatch",f"{resource['id']} ({kind}) dispatched concurrently via a valid {resource.get('transport','road')} route.",incident["id"])
                else:incident["wait_reason"]=f"No available {kind} with a traversable route"
            if incident["handed_over"] or incident["drone_id"] or incident["uncertain_delivery"]:continue
            offers=[]
            for drone in self.drones:
                if not drone["active"] or not drone["link"] or drone["state"] not in ("ready","patrolling"):continue
                route=self._drone_path(drone,incident["position"])
                recovery=self._drone_path({**drone,"position":incident["position"]},self._base)
                if route is None or recovery is None:continue
                travel=self._route_distance(drone["position"],route)
                required=(travel+self._route_distance(incident["position"],recovery))*0.018+7+22
                if drone["energy_wh"]<required:continue
                supplies=[need for need in incident["needs"] if need in ("first_aid","medical","food") and need not in incident["supplied"]]
                compatible=drone["payload"] in supplies and drone["payload_stock"]>0
                # Capability is a feasibility preference before travel cost,
                # never an arbitrary timebonus that a mismatched drone beats.
                offers.append((0 if compatible or not supplies else 1,travel/18,drone["id"],drone,route))
            if offers:
                capability,_,_,drone,route=min(offers,key=lambda offer:offer[:3])
                drone.update(state="enroute",task_id=incident["id"],route=route,delivered=False,coverage_task=None)
                incident["drone_id"]=drone["id"];incident["status"]="responding"
                self._event("drone_dispatch",f"{drone['id']} wins feasible local bid; ground response continues independently.",incident["id"])
                if capability:self._event("supply_unavailable",f"{drone['id']} provides reconnaissance only; compatible aerial supplies unavailable.",incident["id"])
            elif not incident["drone_id"]:incident["wait_reason"]=incident["wait_reason"] or "No energy-feasible drone route"
        # Eligible idle drones claim unique observation patches, not ID sectors.
        used={d["coverage_task"] for d in self.drones if d["coverage_task"] is not None}
        for drone in self.drones:
            if drone["state"]!="ready" or not drone["link"] or not drone["active"] or drone["battery"]<.4:continue
            cells=[cell for cell in self._coverage if cell not in used]
            if not cells:continue
            cells.sort(key=lambda c:(self._coverage[c] if self._coverage[c] is not None else -1e9,_distance(drone["position"][:2],c)))
            for target in cells:
                path=self._drone_path(drone,[*target,0])
                if path is not None:
                    drone.update(state="patrolling",route=path,coverage_task=target)
                    used.add(target)
                    break

    @staticmethod
    def _move(entity,dt,velocity):
        budget=velocity*dt
        while entity["route"] and budget>0:
            target=entity["route"][0];distance=_distance(entity["position"],target)
            if distance<1e-6:
                entity["route"].pop(0);continue
            entity["heading"]=math.atan2(target[1]-entity["position"][1],target[0]-entity["position"][0])
            if distance<=budget:
                entity["position"]=list(target);entity["route"].pop(0);budget-=distance
            else:
                entity["position"]=[a+(b-a)*budget/distance for a,b in zip(entity["position"],target)];budget=0

    def _brief(self,incident,drone=None):
        incident["observations"]+=1;incident["last_observed"]=self.t
        previous=incident["briefing"] or {}
        briefing={"observed_at":round(self.t,2),"source":drone["id"] if drone else "ground crew",
            "people":incident["people"],"hazards":["flood water"] if incident["category"]=="flood" else ["restricted access"],
            "needs":list(incident["needs"]),"supplied":list(incident["supplied"]),
            "summary":f"{incident['people']} people; needs: {', '.join(incident['needs'])}. Ground assessment required.",
            "confidence":0.82 if drone else 0.95}
        # Revision means briefing content changed, not merely that a heartbeat
        # refreshed observation time. Otherwise valid delayed ACKs starve.
        changed=any(briefing[key]!=previous.get(key) for key in ("people","hazards","needs","supplied","source","summary"))
        briefing["revision"]=previous.get("revision",0)+int(changed)
        incident["briefing"]=briefing

    def _ack(self,incident,resource):
        if resource["incident_id"]!=incident["id"] or resource["state"] not in ("awaiting_handoff","on_scene"):
            return False
        if incident["briefing"] is None:self._brief(incident)
        resource["acknowledged"]=True;resource["state"]="on_scene"
        if resource.get("transport")=="boat" or resource["kind"] in ("ambulance","rescue"):
            resource["load"]=min(resource["capacity"],max(0,incident["people"]-incident["evacuated_people"]))
            resource["service_end"]=self.t+8+resource["load"]*2
        else:resource["service_end"]=self.t+(18+incident["people"]*2)*max(1,math.ceil(incident["people"]/max(1,resource["capacity"])))
        first=not incident["handed_over"]
        incident["handed_over"]=True;incident["status"]="ground_response"
        self._event("handoff",f"{resource['id']} arrived and acknowledged the situation briefing; physical response continues.",incident["id"])
        if first and incident["drone_id"]:
            drone=next(d for d in self.drones if d["id"]==incident["drone_id"])
            if drone["link"]:self._return(drone,"ground crew confirmed briefing and arrival")
            else:self._event("handoff_pending",f"{drone['id']} unreachable; recall remains unconfirmed.",incident["id"])
        return True

    def step(self,dt):
        if not math.isfinite(float(dt)) or dt<0:raise ValueError("dt must be finite and non-negative")
        if self.paused:return
        remaining=min(float(dt),10)*self.speed
        # Substeps avoid skipping arrival, acknowledgments and energy thresholds.
        while remaining>1e-9:
            delta=min(.25,remaining);remaining-=delta;self.t+=delta
            for drone in self.drones:
                if not drone["active"]:continue
                flying=drone["state"] not in ("ready","charging")
                if flying:
                    drone["energy_wh"]=max(0,drone["energy_wh"]-delta*.075)
                    drone["battery"]=drone["energy_wh"]/drone["capacity_wh"]
                if drone["link"]:drone["last_telemetry"]=self.t
                if drone["battery"]<.23 and drone["state"] not in ("returning","holding","charging","ready","lost_link"):
                    self._return(drone,"energy reserve reached")
                if drone["battery"]<=0:
                    drone.update(active=False,state="emergency_landed",route=[])
                    self._event("emergency_landing",f"{drone['id']} exhausted energy; simulation treats recovery as failed.")
                    continue
                if drone["state"]=="lost_link":continue
                # Demonstration-only separation heuristic: layered routes and
                # deterministic right of way; not a flight safety guarantee.
                yield_to_peer=False
                if drone["route"] and drone["position"][2]>8:
                    target=drone["route"][0];distance=_distance(drone["position"],target)
                    predicted=[a+(b-a)*min(1,18*2/max(.001,distance)) for a,b in zip(drone["position"],target)]
                    for other in self.drones:
                        if other["id"]>=drone["id"] or not other["active"] or other["position"][2]<8:continue
                        if _distance(predicted,other["position"])<9:
                            yield_to_peer=True;break
                drone["yielding"]=yield_to_peer
                if not yield_to_peer:self._move(drone,delta,18)
                incident=self._find_incident(drone["task_id"])
                if drone["state"]=="enroute" and not drone["route"] and incident:
                    drone["state"]="observing";incident["status"]="observing";self._brief(incident,drone)
                    if drone["payload_stock"]>0 and drone["payload"] in incident["needs"] and drone["payload"] not in incident["supplied"]:
                        incident["supplied"].append(drone["payload"]);drone["delivered"]=True;drone["payload_stock"]-=1;drone["delivered_at"]=self.t
                        self._event("supply",f"{drone['id']} simulated {drone['payload']} delivery; incident remains open.",incident["id"])
                    self._brief(incident,drone)
                    self._event("briefing",f"{drone['id']} shares current situation and outstanding needs with dispatched agencies.",incident["id"])
                elif drone["state"]=="observing" and incident:
                    if self.t-(incident["last_observed"] or 0)>4:self._brief(incident,drone)
                    if drone["payload_stock"]==0 and self.t-drone.get("delivered_at",self.t)>8:self._return(drone,"supplies exhausted; returning to reload while incident and agency response remain open")
                elif drone["state"]=="returning" and not drone["route"]:
                    drone["state"]="charging"
                    if drone["payload_stock"]==0:
                        drone["payload_stock"]=drone["payload_capacity"]
                        self._event("reload",f"{drone['id']} reloaded {drone['payload']} at the operations base.")
                elif drone["state"]=="charging":
                    drone["energy_wh"]=min(drone["capacity_wh"],drone["energy_wh"]+delta*.7)
                    drone["battery"]=drone["energy_wh"]/drone["capacity_wh"]
                    if drone["battery"]>.85:drone["state"]="ready"
                elif drone["state"]=="patrolling" and not drone["route"]:
                    if drone["coverage_task"] is not None:self._coverage[tuple(drone["coverage_task"])]=self.t
                    drone["state"]="ready";drone["coverage_task"]=None
            for resource in self.resources:
                if self.external_ground:continue
                if resource["state"]=="busy" and resource["free_at"] is not None and self.t>=resource["free_at"]:
                    resource.update(state="available",free_at=None)
                    self._event("resource_free",f"{resource['id']} is now free; pending incidents will be reconsidered.")
                if resource["state"] in ("enroute","evacuating"):
                    velocity=7 if resource.get("transport")=="boat" else 9
                    self._move(resource,delta,velocity);resource["eta"]=self._route_distance(resource["position"],resource["route"])/velocity
                    if not resource["route"]:
                        if resource["state"]=="evacuating":
                            incident=self._find_incident(resource["incident_id"])
                            if incident:
                                incident["evacuated_people"]+=resource["load"];resource["load"]=0
                                self._event("evacuation",f"{incident['evacuated_people']}/{incident['people']} people reached {resource.get('transfer_destination','a safe transfer point')}.",incident["id"])
                                if incident["evacuated_people"]<incident["people"]:
                                    route=self._resource_path(resource,incident)
                                    resource.update(state="enroute" if route is not None else "holding",route=route or [],arrived_at=None)
                                    continue
                                self._complete_service(resource,incident)
                        else:
                            resource.update(state="awaiting_handoff",arrived_at=self.t,eta=0)
                            self._event("ground_arrival",f"{resource['id']} reached the incident; awaiting confirmed briefing.",resource["incident_id"])
                incident=self._find_incident(resource["incident_id"])
                if resource["state"]=="awaiting_handoff" and incident and self.t-resource["arrived_at"]>=4:
                    self._ack(incident,resource)
                if resource["state"]=="on_scene" and self.t>=resource.get("service_end",float("inf")) and incident:
                    if resource.get("transport")=="boat" or resource["kind"] in ("ambulance","rescue"):
                        path,destination=self._transfer_path(resource)
                        if path is None:
                            resource["state"]="holding";self._event("evacuation_hold","Responder has no safe transfer path; incident stays open.",incident["id"])
                        else:
                            velocity=7 if resource.get("transport")=="boat" else 9
                            resource.update(state="evacuating",route=path,eta=self._route_distance(resource["position"],path)/velocity,transfer_destination=destination)
                            incident["status"]="evacuating"
                    else:self._complete_service(resource,incident)
            if self.t-self._last_allocate>=1:
                self._allocate();self._last_allocate=self.t
        self.revision+=1

    def _complete_service(self,resource,incident):
        if resource["kind"] not in incident["completed_kinds"]:incident["completed_kinds"].append(resource["kind"])
        if all(k in incident["completed_kinds"] for k in incident["required_kinds"]):
            incident["status"]="resolved";incident["resolved_at"]=self.t
            self._event("resolved","Required agency actions completed; incident resolved.",incident["id"])
        resource.update(state="available",incident_id=None,eta=None,acknowledged=False)

    def _transfer_path(self,resource):
        if resource["kind"]=="ambulance":
            destinations=[b for b in self.world.get("bases",[]) if b.get("kind")=="hospital"] or [{"id":"operations medical transfer","position":self._base}]
        else:destinations=self.world.get("shelters",[]) or [{"id":"operations transfer","position":self._base}]
        offers=[]
        for destination in destinations:
            if resource.get("transport")=="boat":
                projection=self._river_projection(destination["position"])
                path=self._water_path(resource["position"],[*projection[1],self.flood["level"]]) if projection else None
                label=f"{destination.get('id','shelter')} shore-transfer point"
            else:
                path=self._ground_path(resource["position"],destination["position"])
                label=destination.get("id","shelter")
            if path is not None:offers.append((self._route_distance(resource["position"],path),label,path))
        if not offers:return None,None
        _,label,path=min(offers,key=lambda offer:offer[:2])
        return path,label

    def command(self,action,payload=None):
        payload=payload or {}
        try:
            if action in ("scenario","random_scenario","reset"):
                scenario=payload.get("id",self.scenario["id"])
                if action=="random_scenario":
                    choices=[s for s in SCENARIOS if s!=self.scenario["id"]]
                    scenario=self.rng.choice(choices);self.seed=self.rng.randint(1,1000000)
                    next_seed=self.seed
                else:next_seed=int(payload.get("seed",self.seed))
                if scenario not in SCENARIOS:raise ValueError("Unknown scenario")
                self.seed=next_seed
                self._initialize(scenario)
            elif action in ("pause","resume"):
                self.paused=action=="pause"
            elif action=="speed":
                value=float(payload.get("value",1))
                if not math.isfinite(value) or not .25<=value<=16:raise ValueError("Speed must be between 0.25 and 16")
                self.speed=value
            elif action=="fault":
                drone=next((d for d in self.drones if d["id"]==payload.get("drone_id")),None)
                if drone is None:raise ValueError("Unknown drone")
                if not drone["active"]:raise ValueError("Drone is already unavailable")
                kind=payload.get("kind");incident=self._find_incident(drone["task_id"])
                if kind=="crash":
                    drone.update(active=False,state="crashed",route=[])
                    if incident and incident["drone_id"]==drone["id"]:
                        incident["drone_id"]=None;incident["uncertain_delivery"]=False
                    drone["task_id"]=None
                elif kind=="battery":
                    drone["battery"]=.21;drone["energy_wh"]=23.1;self._return(drone,"injected low-energy fault")
                elif kind=="link":
                    drone.update(link=False,state="lost_link",route=[])
                    if incident:incident["uncertain_delivery"]=True
                else:raise ValueError("Fault must be crash, battery or link")
                self._faults+=1;self._event("fault",f"{drone['id']}: {kind}; mission state preserved.",incident["id"] if incident else None)
                self._allocate()
            elif action=="release_resource":
                resource=next((r for r in self.resources if r["id"]==payload.get("resource_id")),None)
                if resource is None:raise ValueError("Unknown resource")
                if resource["incident_id"] is not None:raise ValueError("Cannot release a committed responder before service completion")
                resource.update(state="available",free_at=None);self._allocate()
            elif action=="ack_handoff":
                incident=self._find_incident(payload.get("incident_id"))
                resource=next((r for r in self.resources if r["id"]==payload.get("resource_id")),None)
                if incident is None or resource is None or not self._ack(incident,resource):raise ValueError("Responder must be physically on scene before handoff")
            elif action=="detect":
                position=[float(v) for v in payload.get("position",[0,0,0])]
                if len(position)!=3 or not all(math.isfinite(v) for v in position):raise ValueError("Detection position must be three finite coordinates")
                category=payload.get("category","medical")
                if category not in ("medical","flood","blocked_route"):raise ValueError("Unknown incident category")
                required={"medical":["ambulance"],"flood":["rescue"],"blocked_route":["engineering","rescue"]}[category]
                self._incident(position,category,str(payload.get("title","New observation")),max(1,min(5,int(payload.get("severity",3)))),max(1,int(payload.get("people",1))),payload.get("needs",["first_aid"]),required,"operator")
                self._allocate()
            elif action=="external_resource_update":
                if payload.get("source")!="disruptionops":raise ValueError("External updates require DisruptionOps source")
                if payload.get("run_id") not in (None,self.run_id):raise ValueError("External update belongs to a different run")
                if not self.external_ground:self.resources=[]
                self.external_ground=True
                mapping={entry.get("external_id"):entry.get("local_id") for entry in payload.get("incidents",[]) if entry.get("external_id")}
                for entry in payload.get("incidents",[]):
                    incident=self._find_incident(entry.get("local_id"))
                    if incident and (entry.get("status") in ("resolved","closed") or entry.get("resolved") is True):
                        incident["status"]="resolved";incident["resolved_at"]=self.t
                for entry in payload.get("resources",[]):
                    identifier=entry.get("id")
                    if not identifier:continue
                    resource=next((r for r in self.resources if r["id"]==identifier),None)
                    if resource is None:
                        resource={"id":identifier,"kind":entry.get("kind","rescue"),"state":"unavailable","position":list(self._base),"incident_id":None,"route":[],"eta":None,"agency":"DisruptionOps","capacity":1,"arrived_at":None,"acknowledged":False}
                        self.resources.append(resource)
                    for key in ("kind","state","position","eta","agency","assignment_id","capacity"):
                        if key in entry:resource[key]=copy.deepcopy(entry[key])
                    resource["position"]=(resource["position"]+[0])[:3]
                    external_incident=entry.get("incident_id")
                    resource["incident_id"]=mapping.get(external_incident,external_incident)
                    resource["route"]=[]
                    incident=self._find_incident(resource["incident_id"])
                    if incident and identifier not in incident["resource_ids"]:incident["resource_ids"].append(identifier)
                for handoff in payload.get("handoffs",[]):
                    identifier=mapping.get(handoff.get("incident_id"),handoff.get("incident_id"))
                    incident=self._find_incident(identifier)
                    resource=next((r for r in self.resources if r["id"]==handoff.get("resource_id")),None)
                    if incident and resource and handoff.get("accepted") is True and resource["state"] in ("on_site","on_scene","awaiting_handoff"):
                        current=(incident["briefing"] or {}).get("revision")
                        if current is not None and handoff.get("briefing_revision")!=current:
                            self._event("stale_handoff","Ground acknowledgment references an outdated briefing; recall deferred.",incident["id"])
                            continue
                        resource["state"]="awaiting_handoff";self._ack(incident,resource)
                self._event("external_update","Authoritative DisruptionOps ground state applied; local ground simulation disabled.")
            else:raise ValueError("Unknown command")
            self.revision+=1
            return {"ok":True,"action":action,"run_id":self.run_id,"revision":self.revision}
        except (ValueError,TypeError,OverflowError) as error:
            return {"ok":False,"action":action,"error":str(error)}

    def snapshot(self,include_world=True):
        active=[i for i in self.incidents if i["status"]!="resolved"]
        return copy.deepcopy({"schema_version":2,"run_id":self.run_id,"revision":self.revision,"t":round(self.t,3),
            "paused":self.paused,"speed":self.speed,"scenario":self.scenario,"anchor":ANCHOR,"flood":self.flood,"road_conditions":self.road_conditions,
            **({"world":self.world} if include_world else {}),
            "drones":self.drones,"resources":self.resources,"incidents":self.incidents,"events":self.events,
            "coverage":[{"position":[*cell,0],"last_observed":observed} for cell,observed in self._coverage.items()],
            "metrics":{"fleet":len(self.drones),"active_drones":sum(d["active"] for d in self.drones),
                "open_incidents":len(active),"resolved_incidents":len(self.incidents)-len(active),
                "available_resources":sum(r["state"]=="available" for r in self.resources),
                "handoffs":sum(i["handed_over"] for i in self.incidents),"supply_drops":sum(len(i["supplied"]) for i in self.incidents),
                "faults":self._faults,"covered_patches":sum(t is not None for t in self._coverage.values()),"total_patches":len(self._coverage),"simulation_only":True,"external_ground":self.external_ground,"allocation_mode":"deterministic feasible bids (central simulation harness)"}})
