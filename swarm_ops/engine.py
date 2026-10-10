"""Deterministic disaster-operations demonstrator; kinematics, not flight physics.

Allocations are feasible local bids reconciled by this simulation harness. This
is deliberately NOT a hardware swarm, decentralized consensus, or medical AI.
Ground dispatch, observational evidence and actual service completion are
separate from drone arrival and supply drops.
"""
from __future__ import annotations

import copy
import hashlib
import heapq
import json
import math
import random
import uuid
from pathlib import Path


ANCHOR = [18.6526632, 73.7624378]
SCENARIOS = {
    "rooftop_flood": ("Rooftop flood rescue", "Flood water strands families; rescue boats and reconnaissance respond together."),
    "medical_supply": ("Urgent medical supply", "An injured resident needs first-aid supplies and an ambulance."),
    "blocked_bridge": ("Blocked bridge evacuation", "A damaged crossing blocks evacuation; engineers and rescue teams need an aerial assessment."),
    "dock_fire": ("Dock-launched fire response", "A building fire is reported. Drones wait in docking stations across the district; only the 2-3 best-placed aircraft launch while fire, ambulance and rescue units respond."),
}
# Most aircraft that may be airborne on a dock-launched dispatch at once; the rest stay docked.
DOCK_DISPATCH_CAP = 3
# Communication pulses are kept this many simulated seconds in snapshots.
SIGNAL_WINDOW = 6.0
# A docked fleet does not relaunch for an already-assessed incident sooner than this.
RELAUNCH_COOLDOWN = 20.0
# Supplies a ground unit carries (illustrative); used to name who holds a missing item.
GROUND_SUPPLIES = {"ambulance": ("first_aid", "medical"), "rescue": ("food", "first_aid"), "fire_engine": ("first_aid",)}
SUPPLY_ITEMS = ("first_aid", "medical", "food")
ACTIVITIES = ("docked", "launching", "transit", "scanning", "assessing", "delivering", "returning", "charging", "holding", "failed")
SENSOR_HALF_ANGLE = math.radians(30)
SEARCH_SPACING = 100  # metres per search-map cell
MAX_OPEN_INCIDENTS = 12  # operator-added incidents are rejected beyond this many open incidents
# Event kind -> status the swarm agency reports back to its orchestrator.
ORCHESTRATOR_STATUS = {"request_accepted": "accepted", "request_declined": "declined", "drone_launch": "launched",
    "drone_dispatch": "launched", "drone_on_scene": "on_scene", "briefing": "briefing", "resource_request": "resource_request",
    "resource_request_fulfilled": "resource_assigned", "supply": "supply_delivered", "ground_arrival": "ground_arrived",
    "drone_recall": "recalled", "drone_retask": "retasked", "handoff": "handed_over", "resolved": "completed"}
REQUEST_KINDS = ("sos", "assess", "supply", "overwatch")
TRANSPORT_KINDS = ("ambulance", "rescue")
BEHAVIORS = ("search", "confirm", "assess", "relay", "return", "charge", "idle", "hold", "failed")
PERSON_STATUSES = ("waiting", "supplied", "assessed", "being_rescued", "evacuated", "hospitalised")
CATALOG_PATH = Path(__file__).resolve().parents[1] / "assets" / "operations" / "agency-catalog.json"
# Minimal stand-in used only if the catalog file is missing or unreadable.
_FALLBACK_CATALOG = {"resources": [
    {"source_id": "PC-FIRE-01", "kind": "fire_engine", "name": "Fire tender 01 (Pradhikaran stn, Nigdi)", "operator": "PCMC Fire Brigade", "capacity": 6, "local_xy": [982.3, 728.3], "on_map": False},
    {"source_id": "PC-AMB-06", "kind": "ambulance", "name": "Ambulance 06 (Global Hospital, Ravet)", "operator": "Hospital fleet", "capacity": 3, "local_xy": [-557.3, -685.9], "on_map": False},
    {"source_id": "PC-TEAM-02", "kind": "rescue_team", "name": "SDRF team 02 (Nigdi)", "operator": "SDRF Maharashtra", "capacity": 20, "local_xy": [978.1, 722.8], "on_map": False}],
    "lifelines": [{"source_id": "lf-pc-h5", "kind": "hospital", "name": "Global Hospital, Ravet", "local_xy": [-557.3, -685.9], "on_map": False}]}
# DisruptionOps catalog kind -> simulator resource kind.
CATALOG_KINDS = {"fire_engine": "fire_engine", "ambulance": "ambulance", "rescue_team": "rescue", "boat": "rescue", "jcb": "engineering"}
GROUND_SPEED = 9.0
_ROAD_CACHE = {}


def _agency_catalog():
    """PCMC/PCCOE units and lifelines migrated read-only from DisruptionOps (029_pcmc_pccoe_zone)."""
    try:
        return json.loads(CATALOG_PATH.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return _FALLBACK_CATALOG


def _station_label(name):
    return name[name.find("(")+1:name.rfind(")")] if "(" in name and name.endswith(")") else name


def _clock(seconds):
    seconds = max(0, int(round(seconds)))
    return f"{seconds//60}m{seconds%60:02d}s" if seconds >= 60 else f"{seconds}s"


def _distance(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(min(len(a), len(b)))))


def travel_eta(drone, route, speed=18.0):
    return sum(_distance(a, b) for a, b in zip([drone["position"]] + route, route)) / speed


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

    def __init__(self, world=None, seed=42, fleet_size=20, scenario="rooftop_flood"):
        self.world = copy.deepcopy(world or _default_world())
        self.fleet_size = max(1, min(100, int(fleet_size)))
        self.seed = int(seed)
        if scenario not in SCENARIOS:raise ValueError("Unknown scenario")
        self._initialize(scenario)

    def _initialize(self, scenario):
        self.rng = random.Random(self.seed)
        self.run_id = str(uuid.uuid4())
        self.revision = 0
        self.t = 0.0
        self.paused = False
        self.speed = 1.0
        self.events = []
        self.incidents = []
        self.signals = []
        self._signal_seq = 0
        self._random_incidents = 0
        self._sim_requests = 0
        self._agency_requests = {}
        self._agency_stats = {"requests_received": 0, "accepted": 0, "declined": 0}
        self._lifelines = None
        self._station_nodes = None
        self._event_seq = 0
        self._faults = 0
        self.external_ground = False
        self._last_allocate = -10
        self.flood={"enabled":True,"width":float(self.world.get("river_width",45))+90,"level":2.1,"synthetic":True,"model":"river corridor; prescribed depth, not hydrodynamics"}
        self.road_conditions=[]
        self._setup_search()
        self._base = list(self.world.get("bases", [{"position": [-440,-440,0]}])[0]["position"])
        self._base = (self._base + [0,0,0])[:3]
        self.scenario = {"id": scenario, "name": SCENARIOS[scenario][0], "description": SCENARIOS[scenario][1], "seed": self.seed}
        self.docks = []
        self.drones = []
        for i in range(self.fleet_size):
            self.drones.append({"id": f"D{i+1:02}", "position": [self._base[0]+(i%5)*5,self._base[1]+(i//5)*5,0],
                "heading": 0.0, "state": "ready", "battery": 0.82+self.rng.random()*.16,
                "energy_wh": 0.0, "task_id": None, "payload": ["first_aid", "food", "medical"][i%3],
                "active": True, "route": [], "link": True, "capacity_wh": 110.0, "cruise": 65.0+(i%4)*6,
                "delivered": False, "payload_stock":1,"payload_capacity":1,"last_telemetry": 0.0, "coverage_task": None, "lane": (i%5)*8.0,
                "zone":[],"search_target":None,"speed_mps":0.0})
            self.drones[-1]["energy_wh"] = self.drones[-1]["battery"]*110
        self.resources = []
        for i, kind in enumerate([] if scenario=="dock_fire" else ["ambulance","ambulance","rescue","rescue","engineering"]):
            self.resources.append({"id": f"R{i+1:02}", "kind": kind, "state": "available" if i%2==0 else "busy",
                "position": [self._base[0]+i*12,self._base[1],0], "incident_id": None, "route": [], "eta": None,
                "agency": {"ambulance":"EMS","rescue":"District rescue","engineering":"Public works"}[kind],
                "capacity": 6 if kind=="rescue" else 2, "free_at": 12.0+i*3 if i%2 else None,
                "arrived_at": None, "acknowledged": False,"transport":"boat" if i==2 else "road","load":0})
            if i==2:
                staging=self._river_projection(self._base)
                if staging:self.resources[-1]["position"]=[*staging[1],self.flood["level"]]
        self._build_roads()
        for resource in self.resources:
            # A staging slot that falls inside a mapped footprint moves to the nearest road point.
            if resource.get("transport")=="road" and self._building_at(resource["position"][:2]):
                link=self._road_connector(resource["position"])
                if link:resource["position"]=[round(link[3][0],2),round(link[3][1],2),0]
        self._create_scenario(scenario)
        if self.docks:self._event("scenario", f"{self.scenario['name']} started; {self.fleet_size} drones docked at {len(self.docks)} stations, launch cap {DOCK_DISPATCH_CAP}.")
        else:self._event("scenario", f"{self.scenario['name']} started; {self.fleet_size} drones ready.")
        self._allocate()

    def _building_index(self):
        """World-static list of (id, centroid, footprint, height) for buildings, built once."""
        index=self.__dict__.get("_buildings_cache")
        if index is None:
            index=[]
            for building in self.world.get("buildings",[]):
                fp=building.get("footprint",[])
                if len(fp)<3:continue
                pts=fp[:-1] if fp[0]==fp[-1] else fp
                c=(sum(q[0] for q in pts)/len(pts),sum(q[1] for q in pts)/len(pts))
                index.append((str(building.get("id")),c,pts,float(building.get("height",15))))
            self._buildings_cache=index
        return index

    def _building_at(self,xy,tolerance=0.0):
        """Building whose footprint contains xy (or nearest centroid within tolerance)."""
        best=None
        for entry in self._building_index():
            _,c,pts,_=entry
            d=_distance(c,xy)
            if d>150:continue
            if _inside(xy,pts):return entry
            if tolerance and d<=tolerance and (best is None or d<_distance(best[1],xy)):best=entry
        return best

    def _in_no_fly(self,p):
        for zone in self.world.get("no_fly",self.world.get("no_fly_zones",[])):
            if isinstance(zone,dict) and "center" in zone:
                if _distance(p[:2],zone["center"])<float(zone.get("radius",30))+30:return True
            else:
                polygon=zone.get("polygon",zone.get("footprint",[])) if isinstance(zone,dict) else zone
                if len(polygon)>2 and _inside(p[:2],polygon):return True
        return False

    def _spread(self,nodes,count):
        """Deterministic farthest-point sampling so docks/stations cover the district."""
        chosen=[self.rng.choice(nodes)]
        while len(chosen)<min(count,len(nodes)):
            chosen.append(max(nodes,key=lambda q:(min(_distance(q,c) for c in chosen),q)))
        return chosen

    def _setup_docks(self):
        """Docks, docked aircraft and catalog-named ground units; runs after flood closures are applied."""
        b=self.world.get("bounds",[-600,-600,600,600])
        reach=self._reachable_road_nodes(self._base)
        nodes=sorted(q for q in self._road_graph if b[0]+80<q[0]<b[2]-80 and b[1]+80<q[1]<b[3]-80 and not self._submerged(q) and not self._in_no_fly(q))
        if not nodes:nodes=[tuple(self._base[:2])]
        count=min(len(nodes),max(3,min(8,math.ceil(self.fleet_size/8))))
        for n,q in enumerate(self._spread(nodes,count)):
            self.docks.append({"id":f"DOCK-{n+1}","name":f"Drone dock {n+1}","position":[round(q[0],1),round(q[1],1),0],"drone_ids":[]})
        for i,drone in enumerate(self.drones):
            dock=self.docks[i%len(self.docks)];slot=len(dock["drone_ids"]);dock["drone_ids"].append(drone["id"])
            # Each aircraft owns a landing pad at its dock and returns to that exact pad.
            pad=[round(dock["position"][0]+(slot%4)*4-6,1),round(dock["position"][1]+(slot//4)*4-6,1),0]
            drone.update(dock_id=dock["id"],home=pad,state="ready",position=list(pad),lane=(slot%4)*8.0)
        self._station_nodes=sorted(q for q in reach if b[0]+20<q[0]<b[2]-20 and b[1]+20<q[1]<b[3]-20 and not self._submerged(q))
        self._load_catalog_resources(("fire_engine","ambulance","rescue_team","boat","jcb"))

    def _ensure_ground_kinds(self,required):
        """Mutual aid: if the scenario has no unit of a required kind, bring in the real PCMC units of that kind."""
        if self.external_ground:return
        missing=[k for k in required if not any(r["kind"]==k for r in self.resources)]
        catalog_kinds=tuple(ck for ck,k in CATALOG_KINDS.items() if k in missing and ck!="boat")
        if not catalog_kinds:return
        before=len(self.resources)
        existing={r["id"] for r in self.resources}
        self._load_catalog_resources(catalog_kinds,lifelines=False)
        self.resources=[r for i,r in enumerate(self.resources) if i<before or r["id"] not in existing]
        added=[r["id"] for r in self.resources[before:]]
        if added:self._event("mutual_aid",f"No local {', '.join(missing)} unit; PCMC mutual-aid units on call: {', '.join(added)}.")

    def _road_entry(self,xy):
        """Nearest dry, base-connected road node to a real position (map-edge entry for off-map units)."""
        nodes=getattr(self,"_station_nodes",None) or sorted(self._reachable_road_nodes(self._base))
        if not nodes:return None
        for node in sorted(nodes,key=lambda q:(_distance(q,xy),q))[:8]:
            if self._ground_path(self._base,node) is not None:return node
        return None

    def _load_catalog_resources(self,catalog_kinds,lifelines=True):
        """Real DisruptionOps units (ids PC-*). Off-map units wait at the nearest map-edge road
        node and pay a one-off approach delay = real remaining distance / ground speed."""
        catalog=_agency_catalog()
        if lifelines:self._lifelines=[dict(l) for l in catalog.get("lifelines",[]) if l.get("local_xy")]
        for entry in catalog.get("resources",[]):
            if entry.get("kind") not in catalog_kinds or entry.get("kind") not in CATALOG_KINDS or not entry.get("local_xy"):continue
            real=[float(entry["local_xy"][0]),float(entry["local_xy"][1])]
            if entry["kind"]=="boat":
                # Boats launch on the river at the in-map point nearest their real ghat.
                b=self.world.get("bounds",[-600,-600,600,600])
                projection=self._river_projection([max(b[0]+30,min(b[2]-30,real[0])),max(b[1]+30,min(b[3]-30,real[1])),0])
                if projection is None:continue
                node=(round(projection[1][0],2),round(projection[1][1],2));z=self.flood["level"]
            else:
                node=self._road_entry(real);z=0
            if node is None:continue
            approach=0.0 if entry.get("on_map") and _distance(node,real)<40 else _distance(node,real)
            self.resources.append({"id":entry["source_id"],"kind":CATALOG_KINDS[entry["kind"]],"catalog_kind":entry["kind"],
                "name":entry.get("name",entry["source_id"]),"agency":entry.get("operator",entry.get("agency","")),"agency_id":entry.get("agency_id"),
                "station":_station_label(entry.get("name","")),"state":"available","position":[node[0],node[1],z],
                "real_position":[round(real[0],1),round(real[1],1),0],"off_map":approach>0,"approach_m":round(approach,1),"approach_until":None,
                "incident_id":None,"route":[],"eta":None,"capacity":int(entry.get("capacity",2)),"free_at":None,
                "arrived_at":None,"acknowledged":False,"transport":"boat" if entry["kind"]=="boat" else "road","load":0,"source_catalog_id":entry["source_id"]})

    def _create_fire_scenario(self,dry_nodes):
        b=self.world.get("bounds",[-600,-600,600,600]);pool=[]
        reach=self._reachable_road_nodes(self._base)
        for building in self.world.get("buildings",[]):
            fp=building.get("footprint",[])
            if not fp:continue
            pts=fp[:-1] if fp[0]==fp[-1] else fp
            c=[sum(q[0] for q in pts)/len(pts),sum(q[1] for q in pts)/len(pts),float(building.get("height",15))]
            if not (b[0]+60<c[0]<b[2]-60 and b[1]+60<c[1]<b[3]-60) or self._submerged(c) or self._in_no_fly(c):continue
            if self.docks and min(_distance(c[:2],d["position"][:2]) for d in self.docks)<90:continue
            pool.append((str(building.get("id")),c))
        pool.sort();self.rng.shuffle(pool)
        sites=[]
        for _,c in pool[:200]:
            # Cheap reachability prefilter (road connector inside the base's dry component), then a real route.
            link=self._road_connector(c)
            if link is None or link[1] not in reach or link[0]>60:continue
            if sites and not 45<_distance(c[:2],sites[0][:2])<240:continue
            if any(_distance(c[:2],o[:2])<45 for o in sites):continue
            if self._ground_path(self._base,c) is None:continue
            sites.append(c)
            if len(sites)==3:break
        fallback=[p for p in dry_nodes if tuple(p[:2]) in reach] or dry_nodes
        while len(sites)<3 and fallback:sites.append(self.rng.choice(fallback))
        count=self.rng.choice([2,3]);self.scenario["incident_count"]=count;self.scenario["dispatch_cap"]=DOCK_DISPATCH_CAP
        # Some seeds have a unit already patrolling near the casualty, so it
        # reaches the scene before any aircraft (ground-first ordering).
        # The casualty has been brought out to the street beside the building (never inside a footprint).
        link=self._road_connector(sites[1])
        sites[1]=[round(link[3][0],2),round(link[3][1],2),0] if link else [sites[1][0],sites[1][1],0]
        ground_first=self.rng.random()<.5;self.scenario["ground_unit_nearby"]=ground_first
        if ground_first:
            ambulances=sorted((r for r in self.resources if r["kind"]=="ambulance" and r["state"]=="available"),key=lambda r:(r.get("approach_m",0),r["id"]))
            near=sorted((q for q in (self._station_nodes or []) if 25<_distance(q,sites[1][:2])<90),key=lambda q:(_distance(q,sites[1][:2]),q))
            near=[q for q in near[:6] if self._ground_path(q,sites[1]) is not None]
            if ambulances and near:
                ambulances[0].update(position=[near[0][0],near[0][1],0],approach_m=0.0,off_map=False,patrol=True)
                self._event("ground_patrol",f"{ambulances[0]['id']} is already on patrol inside the district (scenario variation).")
            else:self.scenario["ground_unit_nearby"]=False
        self._incident(sites[0],"fire","Building fire; occupants on upper floors",5,self.rng.randint(3,8),["assessment"],["fire_engine"],source="sos")
        self._incident(sites[1],"medical","Smoke-inhalation casualty needs first aid",4,self.rng.randint(1,2),["first_aid","medical"],["ambulance"],source="sos")
        if count==3:self._incident(sites[2],"trapped","Residents sheltering in adjacent building",3,self.rng.randint(4,9),["food"],["rescue"],source="sos")

    def _launched(self):
        return sum(1 for d in self.drones if d.get("dock_id") and d["state"] in ("enroute","observing","returning","holding"))

    def _event(self, kind, message, incident_id=None, data=None):
        self._event_seq += 1
        entry = {"id": f"{self.run_id}:{self._event_seq}", "run_id":self.run_id,"time": round(self.t,2), "kind":kind, "message":message}
        if incident_id:
            entry["incident_id"] = incident_id
        if kind in ORCHESTRATOR_STATUS:
            # Machine-readable status for the orchestrator (DisruptionOps) on /ops/events and the WS.
            entry["status"] = ORCHESTRATOR_STATUS[kind]
            incident = self._find_incident(incident_id) if incident_id else None
            if incident and incident.get("request_id"): entry["request_id"] = incident["request_id"]
        if data:
            entry["data"] = data
        self.events.append(entry)
        self.events = self.events[-180:]

    def _signal(self, kind, from_type, from_id, to_ids, position, incident_id, text):
        """Short-lived communication pulse for the viewer (kept for SIGNAL_WINDOW sim-seconds)."""
        self._signal_seq += 1
        self.signals.append({"id":self._signal_seq,"t":round(self.t,2),"kind":kind,"from_type":from_type,"from_id":from_id,
            "to_ids":list(to_ids),"position":[round(float(v),2) for v in (list(position)+[0,0,0])[:3]],
            "incident_id":incident_id,"text":text})
        self._prune_signals()

    def _prune_signals(self):
        self.signals=[s for s in self.signals if self.t-s["t"]<=SIGNAL_WINDOW][-200:]

    def _supply_holder(self, need, near, exclude=()):
        """Nearest stocked aircraft or ground unit that can provide a need (drone first on ties)."""
        offers=[]
        for drone in self.drones:
            if drone["id"] in exclude or not drone["active"] or not drone["link"] or drone["payload"]!=need or drone["payload_stock"]<=0:continue
            if drone["state"] not in ("ready","charging","patrolling","enroute") or (drone["state"]=="enroute" and drone["task_id"]):continue
            offers.append((_distance(drone["position"][:2],near[:2]),0,drone["id"],"drone"))
        for resource in self.resources:
            if resource["id"] in exclude or need not in GROUND_SUPPLIES.get(resource["kind"],()):continue
            offers.append((_distance(resource["position"][:2],near[:2]),1,resource["id"],"resource"))
        if not offers:return None
        distance,_,identifier,holder_type=min(offers)
        return {"id":identifier,"type":holder_type,"distance":distance}

    def _incident(self, position, category, title, severity, people, needs, required, source="scenario", building_id=None):
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
                    "source":source,"wait_reason":None,"observations":0,"last_observed":None,"supply_requests":[]}
        incident["evacuated_people"]=0;incident["hospitalised_people"]=0
        if building_id is None and category in ("fire","trapped","flood") and len(position)>2 and float(position[2])>0:
            found=self._building_at(position[:2],tolerance=5)
            building_id=found[0] if found else None
        incident["building_id"]=building_id
        incident.update(request_id=None,requested_by=None,first_on_scene=None,drone_on_scene_at=None,briefed_at=None,resource_requests=[])
        self.incidents.append(incident)
        self._raise_search_prior(position)
        self._event("incident",title,incident["id"])
        origin={"operator":"Operator-raised SOS","operator-random":"Operator-added SOS","sos":"SOS call"}.get(source,"Detection")
        self._signal("sos","incident",incident["id"],["control"],position,incident["id"],f"{origin}: {title} ({people} people, severity {severity}).")
        # The swarm (an agency) asks the orchestrator for the ground agencies the report implies.
        for kind in required:
            self._raise_resource_request(incident,kind,1,f"initial report: {category}, {people} people","report")
        return incident

    def _raise_resource_request(self,incident,kind,qty,rationale,stage):
        self._resource_request_seq=getattr(self,"_resource_request_seq",0)+1
        request={"request_id":f"{incident['id']}-RR{len(incident['resource_requests'])+1}","incident_id":incident["id"],"needed_kind":kind,"qty":qty,
                 "urgency":"critical" if incident["severity"]>=5 else "high" if incident["severity"]>=4 else "normal",
                 "position":[round(float(v),2) for v in incident["position"]],"rationale":rationale,"stage":stage,
                 "briefing_revision":(incident["briefing"] or {}).get("revision"),"raised_at":round(self.t,2),"status":"open","fulfilled_by":[]}
        incident["resource_requests"].append(request)
        self._event("resource_request",f"Swarm requests {qty}x {kind} for {incident['id']} ({rationale}).",incident["id"],data={k:request[k] for k in ("request_id","needed_kind","qty","urgency","position","rationale","briefing_revision")})
        self._signal("resource_request","incident",incident["id"],["orchestrator"],incident["position"],incident["id"],f"Request {qty}x {kind}: {rationale}.")
        return request

    def _fulfil_request(self,incident,kind,unit_id):
        request=next((r for r in incident.get("resource_requests",[]) if r["needed_kind"]==kind and r["status"]=="open"),None)
        if request is None:return
        request["fulfilled_by"].append(unit_id)
        if len(request["fulfilled_by"])>=request["qty"]:
            request["status"]="fulfilled";request["fulfilled_at"]=round(self.t,2)
        self._event("resource_request_fulfilled",f"{unit_id} assigned to {request['request_id']} ({kind}).",incident["id"],data={"request_id":request["request_id"],"unit_id":unit_id})

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
        if scenario=="dock_fire":self._setup_docks()
        # Dry road-adjacent points are selected using the same seed; do not
        # fabricate ambulance access inside the flooded corridor.
        dry_nodes=[list(p)+[0] for p in self._road_graph if not self._submerged(p) and _distance(p,self._base[:2])>100 and bounds[0]+20<p[0]<bounds[2]-20 and bounds[1]+20<p[1]<bounds[3]-20]
        sampled=list(dry_nodes);self.rng.shuffle(sampled)
        reachable=[p for p in sampled[:24] if self._ground_path(self._base,p) is not None]
        dry=self.rng.choice(reachable or dry_nodes) if dry_nodes else [self._base[0]+40,self._base[1],0]
        if scenario=="rooftop_flood":
            self._incident(candidate,"flood","Families stranded on a rooftop",5,self.rng.randint(4,9),["food","rescue"],["rescue"])
            self._incident(dry,"medical","Injured person at evacuation point",4,1,["first_aid","medical"],["ambulance"])
        elif scenario=="dock_fire":
            self._create_fire_scenario(dry_nodes)
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
        self._ground_cache={};self._connector_cache={}
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
        # The split graph depends only on the (immutable) world; build it once
        # and reset only the per-run closures.
        cached=self.__dict__.get("_road_base")
        if cached is None:
            # Shared across instances for identical road geometry (graphs are read-only).
            key=hashlib.sha1(json.dumps(self.world.get("roads",[]),sort_keys=True).encode()).hexdigest()
            cached=_ROAD_CACHE.get(key)
            if cached is None:
                self._build_roads_uncached()
                cached=_ROAD_CACHE[key]=(self._road_graph,self._road_edges)
                while len(_ROAD_CACHE)>8:_ROAD_CACHE.pop(next(iter(_ROAD_CACHE)))
            self._road_base=cached
        self._road_graph,self._road_edges=cached
        self._blocked_edges=set();self._ground_cache={};self._connector_cache={}

    def _build_roads_uncached(self):
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
        self._ground_cache={};self._connector_cache={}
        for (a,b),points in zip(segments,splits):
            points=sorted(points,key=lambda p:_distance(a,p))
            for x,y in zip(points,points[1:]):
                if x==y:
                    continue
                self._road_graph.setdefault(x,{})[y]=_distance(x,y)
                self._road_graph.setdefault(y,{})[x]=_distance(x,y)
                self._road_edges.append((x,y))

    def _road_connector(self,position):
        """Closest dry, unblocked road point within 150 m (cached per position and closure set)."""
        cache=self.__dict__.setdefault("_connector_cache",{})
        cache_key=(float(position[0]),float(position[1]),len(self._blocked_edges))
        if cache_key in cache:return cache[cache_key]
        best=None;px,py=float(position[0]),float(position[1])
        for a,b in self._road_edges:
            dx,dy=b[0]-a[0],b[1]-a[1]
            ratio=max(0,min(1,((px-a[0])*dx+(py-a[1])*dy)/(dx*dx+dy*dy)))
            p=(a[0]+ratio*dx,a[1]+ratio*dy)
            cost=math.sqrt((px-p[0])**2+(py-p[1])**2)
            if cost>150 or (best is not None and cost>=best[0]):continue
            if tuple(sorted((a,b))) in self._blocked_edges:continue
            checks=max(2,int(cost/12))
            if any(self._submerged(q) and not self._elevated_crossing(q) for q in ([px+(p[0]-px)*k/checks,py+(p[1]-py)*k/checks] for k in range(checks+1))):continue
            best=(cost,a,b,p)
        if len(cache)>4000:cache.clear()
        cache[cache_key]=best
        return best

    def _reachable_road_nodes(self,start):
        """Every road-graph node reachable from start over open (dry, unblocked) edges."""
        best=self._road_connector(start)
        if best is None or (self._submerged(start) and not self._elevated_crossing(start)):return set()
        seen=set();stack=[best[1],best[2]]
        while stack:
            node=stack.pop()
            if node in seen:continue
            seen.add(node)
            for neighbor in self._road_graph.get(node,{}):
                if neighbor not in seen and (node,neighbor) not in self._blocked_edges and (neighbor,node) not in self._blocked_edges:
                    stack.append(neighbor)
        return seen

    def _ground_path(self,start,end):
        if not self._road_graph:
            return None
        # Routes are pure functions of endpoints and the current closure set;
        # cache them (callers mutate routes, so hand out copies).
        cache=self.__dict__.setdefault("_ground_cache",{})
        cache_key=(float(start[0]),float(start[1]),float(end[0]),float(end[1]),len(self._blocked_edges))
        if cache_key in cache:
            cached=cache[cache_key]
            return None if cached is None else [list(p) for p in cached]
        route=self._ground_path_uncached(start,end)
        if len(cache)>4000:cache.clear()
        cache[cache_key]=None if route is None else [list(p) for p in route]
        return route

    def _ground_path_uncached(self,start,end):
        if any(self._submerged(p) and not self._elevated_crossing(p) for p in (start,end)):return None
        # Connect to the road at its closest point, not its closest distant vertex.
        # Connector edges are an overlay instead of a full graph copy.
        extra={}
        def connector(position,label):
            best=self._road_connector(position)
            if best is None:
                return None
            _,a,b,p=best
            key=(float(position[0]),float(position[1]),label)
            # One unique key per connector prevents accidentally joining islands.
            extra[key]={a:_distance(position[:2],p)+_distance(p,a),b:_distance(position[:2],p)+_distance(p,b)}
            extra.setdefault(a,{})[key]=extra[key][a];extra.setdefault(b,{})[key]=extra[key][b]
            return key,p
        first=connector(start,"start");last=connector(end,"end")
        if first is None or last is None:
            return None
        source,destination=first[0],last[0]
        graph=self._road_graph;blocked=self._blocked_edges
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
            neighbors=graph.get(node,{})
            if node in extra:neighbors={**neighbors,**extra[node]}
            for neighbor,length in neighbors.items():
                if len(node)==2 and len(neighbor)==2 and ((node,neighbor) in blocked or (neighbor,node) in blocked):
                    continue
                value=cost+length
                if value<costs.get(neighbor,float("inf")):
                    costs[neighbor]=value;previous[neighbor]=node;serial+=1
                    heapq.heappush(queue,(value,serial,neighbor))
        return None

    def _flight_grid(self):
        """World-static flight grid data (no-fly polygons, obstacle ceiling), built once per world."""
        grid=self.__dict__.get("_flight_grid_cache")
        if grid is not None:return grid
        bounds=self.world.get("bounds",[-600,-600,600,600]);spacing=35
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
        polygons=[p for p in polygons if len(p)>2]
        ceiling=max((float(b.get("height",0))+12 for b in self.world.get("buildings",[])),default=0)
        grid={"bounds":bounds,"spacing":spacing,"nx":nx,"ny":ny,"polygons":polygons,"ceiling":ceiling,"edges":{},"paths":{}}
        self._flight_grid_cache=grid
        return grid

    def _restricted(self,a,b):
        polygons=self._flight_grid()["polygons"]
        if not polygons:return False
        count=max(2,int(_distance(a[:2],b[:2])/8)+1)
        return any(_inside([a[0]+(b[0]-a[0])*i/count,a[1]+(b[1]-a[1])*i/count],p) for p in polygons for i in range(count+1))

    def _grid_cells(self,source,dest):
        """Cached 8-connected A* over the static flight grid (pure function of the two cells)."""
        grid=self._flight_grid()
        if (source,dest) in grid["paths"]:return grid["paths"][(source,dest)]
        bounds,spacing,nx,ny,edges=grid["bounds"],grid["spacing"],grid["nx"],grid["ny"],grid["edges"]
        def point(c):return [bounds[0]+c[0]*spacing,bounds[1]+c[1]*spacing]
        def blocked(a,b):
            key=(a,b) if a<b else (b,a)
            if key not in edges:edges[key]=self._restricted(point(a),point(b))
            return edges[key]
        dp=point(dest)
        queue=[(0,source)];costs={source:0};previous={};result=None
        while queue:
            _,node=heapq.heappop(queue)
            if node==dest:
                path=[node]
                while node in previous:
                    node=previous[node];path.append(node)
                path.reverse();result=path
                break
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                neighbor=(node[0]+dx,node[1]+dy)
                if not (0<=neighbor[0]<=nx and 0<=neighbor[1]<=ny) or blocked(node,neighbor):
                    continue
                value=costs[node]+math.hypot(dx,dy)*spacing
                if value<costs.get(neighbor,float("inf")):
                    costs[neighbor]=value;previous[neighbor]=node
                    np_=point(neighbor)
                    heapq.heappush(queue,(value+_distance([*np_,0],[*dp,0]),neighbor))
        if len(grid["paths"])>20000:grid["paths"].clear()
        grid["paths"][(source,dest)]=result
        return result

    def _drone_path(self, drone, target):
        grid=self._flight_grid()
        altitude=max(drone["cruise"],float(target[2])+12,grid["ceiling"])+drone.get("lane",0)
        # Horizontal grid A* routes around restricted zones; roof surveys remain
        # above the highest obstacle. Vertical motion is simulated kinematically.
        bounds,spacing,nx,ny=grid["bounds"],grid["spacing"],grid["nx"],grid["ny"]
        if not bounds[0]<=target[0]<=bounds[2] or not bounds[1]<=target[1]<=bounds[3]:return None
        restricted=self._restricted
        if restricted(target,target) or restricted(drone["position"],drone["position"]):
            return None
        def cell(p):return (max(0,min(nx,round((p[0]-bounds[0])/spacing))),max(0,min(ny,round((p[1]-bounds[1])/spacing))))
        def point(c):return [bounds[0]+c[0]*spacing,bounds[1]+c[1]*spacing,altitude]
        path=self._grid_cells(cell(drone["position"]),cell(target))
        if path is None:return None
        coords=[point(p) for p in path]
        if restricted(drone["position"],coords[0]) or restricted(coords[-1],target):
            return None
        return [[*drone["position"][:2],altitude]]+coords+[[target[0],target[1],altitude]]

    @staticmethod
    def _route_distance(position,route):
        return sum(_distance(a,b) for a,b in zip([position]+route,route))

    def _return(self,drone,reason,kind="drone_return"):
        incident=self._find_incident(drone["task_id"])
        if incident and incident["drone_id"]==drone["id"]:
            incident["drone_id"]=None;incident["drone_released_at"]=self.t
        drone["task_id"]=None;drone["coverage_task"]=None;drone["retasked"]=None;drone["search_target"]=None
        home=drone.get("home",self._base)
        path=self._drone_path(drone,home)
        if path is None:
            drone["state"]="holding";drone["route"]=[]
            self._event("safety_hold",f"{drone['id']} has no safe recovery path; holding for assistance.")
        else:
            drone["state"]="returning";drone["route"]=path+[[*home[:2],0]]
            where=f" to {drone['dock_id']}" if drone.get("dock_id") else ""
            self._event(kind,f"{drone['id']} returning{where}: {reason}.",incident["id"] if incident else None)

    def _find_incident(self,identifier):
        return next((i for i in self.incidents if i["id"]==identifier),None)

    def _allocate(self):
        for incident in sorted(self.incidents,key=lambda i:(-i["severity"],i["created_at"],i["id"])):
            if incident["status"]=="resolved":continue
            incident["wait_reason"]=None
            for kind in ([] if self.external_ground else incident["required_kinds"]):
                if kind in incident["completed_kinds"]:continue
                assigned=next((r for r in self.resources if r["kind"]==kind and r["incident_id"]==incident["id"]),None)
                # The local units act as a mock orchestrator answering the swarm's resource requests;
                # a follow-up request (e.g. more people than one unit carries) adds another unit.
                extra=any(q["needed_kind"]==kind and q["status"]=="open" and q["stage"]!="report" for q in incident.get("resource_requests",[]))
                if assigned is not None and not extra:
                    self._maybe_reassign_nearer(incident,assigned)
                    continue
                offers=[]
                for resource in self.resources:
                    if resource["state"]!="available" or resource["kind"]!=kind:continue
                    route=self._resource_path(resource,incident)
                    # Off-map units add their real remaining distance to the district edge.
                    if route is not None:offers.append((self._route_distance(resource["position"],route)+resource.get("approach_m",0),resource["id"],resource,route))
                if offers:
                    length,_,resource,route=min(offers,key=lambda offer:offer[:2])
                    resource.update(state="enroute",incident_id=incident["id"],route=route,eta=length/(7 if resource.get("transport")=="boat" else 9),acknowledged=False,arrived_at=None)
                    if resource.get("approach_m",0)>0:
                        approach_s=resource["approach_m"]/(7 if resource.get("transport")=="boat" else GROUND_SPEED);resource["approach_until"]=self.t+approach_s
                        self._event("ground_departure",f"{resource['id']} left {resource.get('station') or 'its station'}, ETA to district {_clock(approach_s)} ({resource['approach_m']/1000:.1f} km).",incident["id"])
                    if resource["id"] not in incident["resource_ids"]:incident["resource_ids"].append(resource["id"])
                    # Explain why this unit, and that aerial work is not waited on.
                    if self.t>0 and incident["drone_id"]:context=f"; {incident['drone_id']} keeps working the scene meanwhile"
                    elif self.t>0 and incident["briefing"]:context="; briefing already on file"
                    else:context=""
                    freed=" (recently freed)" if resource.get("freed_at") is not None else ""
                    self._event("ground_dispatch",f"{resource['id']} ({kind}){freed} dispatched concurrently via a valid {resource.get('transport','road')} route, ETA {resource['eta']:.0f} s as nearest available {kind}{context}.",incident["id"])
                    self._signal("dispatch","resource",resource["id"],["control"],resource["position"],incident["id"],f"{resource.get('name',resource['id'])} responding to {incident['id']}, ETA {resource['eta']:.0f} s.")
                    resource.pop("freed_at",None)
                    self._fulfil_request(incident,kind,resource["id"])
                elif assigned is None:incident["wait_reason"]=f"No available {kind} with a traversable route"
            if incident["handed_over"] or incident["drone_id"] or incident["uncertain_delivery"]:continue
            if self.docks and self._launched()>=DOCK_DISPATCH_CAP:
                incident["wait_reason"]=f"Launch cap of {DOCK_DISPATCH_CAP} reached; next aircraft launches when one is back at its dock"
                continue
            offers=[]
            candidates=self.drones
            supply_only=False
            # Once assessed, another aircraft is sent only to bring a missing
            # supply that a stocked aircraft actually carries, and not sooner
            # than the cooldown (prevents launch/return churn).
            outstanding=[n for n in incident["needs"] if n in SUPPLY_ITEMS and n not in incident["supplied"]]
            if incident["observations"]>0:
                if not outstanding:continue
                if self.t-incident.get("drone_released_at",-1e9)<RELAUNCH_COOLDOWN:
                    incident["wait_reason"]=incident["wait_reason"] or "Assessed; supply relaunch cooling down"
                    continue
                supply_only=True
                candidates=[d for d in self.drones if d["payload"] in outstanding and d["payload_stock"]>0]
            if self.docks:
                eligible=[d for d in self.drones if d["active"] and d["link"] and d["state"] in ("ready","patrolling")
                          and (not supply_only or (d["payload"] in outstanding and d["payload_stock"]>0))]
                # Prefilter by straight-line distance; only a few paths are planned.
                candidates=sorted(eligible,key=lambda d:(_distance(d["position"],incident["position"]),d["id"]))[:6]
                if outstanding and not supply_only:
                    stocked=[d for d in eligible if d["payload"] in outstanding and d["payload_stock"]>0 and d not in candidates]
                    candidates+=sorted(stocked,key=lambda d:(_distance(d["position"],incident["position"]),d["id"]))[:3]
            for drone in candidates:
                if not drone["active"] or not drone["link"] or drone["state"] not in ("ready","patrolling"):continue
                route=self._drone_path(drone,incident["position"])
                recovery=self._drone_path({**drone,"position":incident["position"]},drone.get("home",self._base))
                if route is None or recovery is None:continue
                travel=self._route_distance(drone["position"],route)
                required=(travel+self._route_distance(incident["position"],recovery))*0.018+7+22
                if drone["energy_wh"]<required:continue
                supplies=[need for need in incident["needs"] if need in ("first_aid","medical","food") and need not in incident["supplied"]]
                compatible=drone["payload"] in supplies and drone["payload_stock"]>0
                # Capability is a feasibility preference before travel cost,
                # never an arbitrary timebonus that a mismatched drone beats.
                # Auction bid: ETA seconds plus an energy term (share of the
                # aircraft's remaining energy this task would consume).
                bid=travel/18+20*required/max(1e-6,drone["energy_wh"])
                offers.append((0 if compatible or not supplies else 1,round(bid,6),drone["id"],drone,route))
            if offers:
                capability,bid,_,drone,route=min(offers,key=lambda offer:offer[:3])
                was_searching=drone["state"]=="patrolling"
                drone.update(state="enroute",task_id=incident["id"],route=route,delivered=False,coverage_task=None,search_target=None)
                if was_searching:self._event("search_to_confirm",f"{drone['id']} leaves its search sweep (zone kept) to confirm {incident['id']}; winning bid {bid:.1f} of {len(offers)}.",incident["id"])
                incident["drone_id"]=drone["id"];incident["status"]="responding"
                purpose=f"bringing {drone['payload']}" if supply_only else "first assessment"
                if drone.get("dock_id"):self._event("drone_launch",f"{drone['id']} launched from {drone['dock_id']} for {purpose} ({self._launched()}/{DOCK_DISPATCH_CAP} airborne); ground response continues independently.",incident["id"])
                else:self._event("drone_dispatch",f"{drone['id']} wins feasible local bid; ground response continues independently.",incident["id"])
                self._signal("dispatch","drone",drone["id"],["control"],drone["position"],incident["id"],
                             f"{drone['id']} {'launching from '+drone['dock_id'] if drone.get('dock_id') else 'dispatched'} to {incident['id']} ({purpose}), ETA {travel_eta(drone,route):.0f} s.")
                if capability:self._event("supply_unavailable",f"{drone['id']} provides reconnaissance only; compatible aerial supplies unavailable.",incident["id"])
            elif not incident["drone_id"]:incident["wait_reason"]=incident["wait_reason"] or "No energy-feasible drone route"
        # Idle aircraft search their own zone (docked fleets wait for a launch).
        if self.docks:return
        self._update_zones()
        for drone in self.drones:
            if drone["state"]!="ready" or not drone["link"] or not drone["active"] or drone["battery"]<.4:continue
            self._next_search(drone)

    def _maybe_reassign_nearer(self,incident,assigned):
        """A unit that frees up much nearer than the en-route one takes over; the farther unit stands down."""
        if assigned["state"]!="enroute" or assigned.get("arrived_at") is not None or assigned.get("load",0) or incident["evacuated_people"]:return
        remaining=assigned.get("eta") or 0
        best=None
        for resource in self.resources:
            if resource["state"]!="available" or resource["kind"]!=assigned["kind"]:continue
            velocity=7 if resource.get("transport")=="boat" else GROUND_SPEED
            if (resource.get("approach_m",0))/velocity>=.6*remaining:continue
            route=self._resource_path(resource,incident)
            if route is None:continue
            eta=(self._route_distance(resource["position"],route)+resource.get("approach_m",0))/velocity
            if eta<.6*remaining and remaining-eta>20 and (best is None or (eta,resource["id"])<best[:2]):best=(eta,resource["id"],resource,route)
        if best is None:return
        eta,_,resource,route=best
        # Stand the farther unit down where it is; keep any unfinished off-map approach distance.
        if assigned.get("approach_until") is not None:
            assigned["approach_m"]=round(max(0.0,assigned["approach_until"]-self.t)*GROUND_SPEED,1)
        assigned.update(state="available",incident_id=None,route=[],eta=None,approach_until=None)
        if assigned["id"] in incident["resource_ids"]:incident["resource_ids"].remove(assigned["id"])
        resource.update(state="enroute",incident_id=incident["id"],route=route,eta=eta,acknowledged=False,arrived_at=None)
        if resource.get("approach_m",0)>0:resource["approach_until"]=self.t+resource["approach_m"]/(7 if resource.get("transport")=="boat" else GROUND_SPEED)
        incident["resource_ids"].append(resource["id"])
        for request in incident.get("resource_requests",[]):
            if assigned["id"] in request["fulfilled_by"]:request["fulfilled_by"][request["fulfilled_by"].index(assigned["id"])]=resource["id"]
        self._event("ground_reassign",f"{resource['id']} is free and nearer (ETA {eta:.0f} s vs {remaining:.0f} s): dispatched concurrently with aerial work; {assigned['id']} stood down.",incident["id"])
        self._signal("dispatch","resource",resource["id"],["control",assigned["id"]],resource["position"],incident["id"],f"{resource.get('name',resource['id'])} takes over {incident['id']}, ETA {eta:.0f} s; {assigned['id']} released.")
        resource.pop("freed_at",None)

    INCIDENT_TEMPLATES={
        "medical":("Casualty needs first aid and transport",4,(1,2),["first_aid","medical"],["ambulance"]),
        "flood":("Residents stranded on a flooded rooftop",5,(3,8),["food","rescue"],["rescue"]),
        "fire":("Building fire; occupants on upper floors",5,(3,8),["assessment","first_aid"],["fire_engine"]),
        "trapped":("Residents trapped in a damaged building",4,(2,6),["food","first_aid"],["rescue"]),
        "blocked_route":("Road blocked; access route assessment",3,(2,5),["assessment"],["engineering","rescue"])}

    def _open_incidents(self):
        return sum(i["status"]!="resolved" for i in self.incidents)

    def _sim_request_id(self):
        self._sim_requests=getattr(self,"_sim_requests",0)+1
        return f"sim-{self.run_id[:8]}-{self._sim_requests}"

    @staticmethod
    def _validated_needs(needs):
        if not isinstance(needs,list) or not 1<=len(needs)<=4 or any(n not in (*SUPPLY_ITEMS,"assessment","rescue") for n in needs):
            raise ValueError("needs must list known items: first_aid, medical, food, assessment, rescue")
        return list(dict.fromkeys(needs))

    @staticmethod
    def _validated_numbers(payload):
        severity=payload.get("severity",3);people=payload.get("people",1)
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (severity,people)):raise ValueError("severity/priority and people must be numbers")
        return max(1,min(5,int(severity))),max(1,min(500,int(people)))

    def _agency_request(self,request,source,building_id=None,snap=True):
        """The swarm is an agency: it decides itself whether to accept a request, then runs its own operation."""
        stats=self._agency_stats;stats["requests_received"]+=1
        category,kind,position=request["category"],request["kind"],list(request["position"])
        b=self.world.get("bounds",[-600,-600,600,600])
        def answer(accepted,reason,incident=None,merged=False,eta=None,drones=(),plan=()):
            stats["accepted" if accepted else "declined"]+=1
            result={"request_id":request["request_id"],"accepted":accepted,"reason":reason,"incident_id":incident["id"] if incident else None,
                    "merged":merged,"eta_s":None if eta is None else round(eta,1),"assigned_drones":list(drones),"plan":list(plan),
                    "position":list(incident["position"]) if incident else position,"source":source}
            self._agency_requests[request["request_id"]]=result
            self._event("request_accepted" if accepted else "request_declined",
                        f"Swarm {'accepted' if accepted else 'declined'} {kind} request {request['request_id']} from {request['requested_by']}: {reason}.",
                        incident["id"] if incident else None,data={k:result[k] for k in ("request_id","accepted","reason","eta_s","assigned_drones","plan")})
            return result
        if not (b[0]<=position[0]<=b[2] and b[1]<=position[1]<=b[3]):return answer(False,"outside the swarm's operating area")
        if snap:position,building_id=self._snap_detection(position,category)
        if self._in_no_fly(position):return answer(False,"inside a no-fly zone; aerial response not permitted")
        if self._open_incidents()>=MAX_OPEN_INCIDENTS:return answer(False,f"swarm at capacity ({MAX_OPEN_INCIDENTS} open incidents)")
        capped=bool(self.docks) and self._launched()>=DOCK_DISPATCH_CAP
        if capped and kind!="sos":return answer(False,f"launch cap of {DOCK_DISPATCH_CAP} airborne aircraft reached; retry later")
        feasible=None
        for drone in sorted((d for d in self.drones if d["active"] and d["link"]),key=lambda d:(_distance(d["position"],position),d["id"]))[:6]:
            home=drone.get("home",self._base)
            out=self._drone_path({**drone,"position":list(home)},position);back=self._drone_path({**drone,"position":position},home)
            if out is None or back is None:continue
            if drone["capacity_wh"]*.9>=(self._route_distance(home,out)+self._route_distance(position,back))*0.018+29:feasible=drone;break
        if feasible is None:return answer(False,"no energy-feasible aircraft route to that position")
        title,severity,_,_,required=self.INCIDENT_TEMPLATES[category]
        self._ensure_ground_kinds(required)
        before=len(self.incidents)
        incident=self._incident(position,category,str(request.get("title") or title)[:120],request["priority"],request["people"],request["needs"],required,source,building_id=building_id)
        merged=len(self.incidents)==before
        if not merged:incident.update(request_id=request["request_id"],requested_by=request["requested_by"],request_kind=kind)
        self._allocate()
        drone=next((d for d in self.drones if d["id"]==incident["drone_id"]),None)
        eta=travel_eta(drone,drone["route"]) if drone and drone["route"] else None
        plan=[]
        if drone:plan.append(f"launch {drone['id']}"+(f" from {drone['dock_id']}" if drone.get("dock_id") else "")+" to assess and brief")
        elif capped:plan.append(f"queued: next aircraft launches when one of {DOCK_DISPATCH_CAP} airborne is back at its dock")
        else:plan.append("queued: waiting for an energy-feasible aircraft")
        own=[n for n in incident["needs"] if n in SUPPLY_ITEMS]
        if own:plan.append("drop "+", ".join(own)+" if a stocked aircraft is available")
        plan+=[f"request {q['qty']}x {q['needed_kind']} from orchestrator" for q in incident["resource_requests"]]
        plan.append("hand the scene over to the first ground crew, then return to dock" if self.docks else "hand the scene over to the first ground crew, then resume search")
        reason="merged with an existing open incident" if merged else ("accepted; aircraft assigned" if drone else "accepted; aircraft queued")
        return answer(True,reason,incident,merged,eta,[drone["id"]] if drone else [],plan)

    def _agency_metrics(self):
        def median(values):
            values=sorted(values)
            if not values:return None
            mid=len(values)//2
            return round(values[mid] if len(values)%2 else (values[mid-1]+values[mid])/2,1)
        requests=[q for i in self.incidents for q in i.get("resource_requests",[])]
        return {**self._agency_stats,
            "median_time_to_first_drone_s":median([i["drone_on_scene_at"]-i["created_at"] for i in self.incidents if i.get("drone_on_scene_at") is not None]),
            "median_time_to_briefing_s":median([i["briefed_at"]-i["created_at"] for i in self.incidents if i.get("briefed_at") is not None]),
            "resource_requests_raised":len(requests),"resource_requests_fulfilled":sum(q["status"]=="fulfilled" for q in requests),
            "supplies_delivered":sum(len(i["supplied"]) for i in self.incidents),"handoffs":sum(i["handed_over"] for i in self.incidents),
            "drone_first":sum(i.get("first_on_scene")=="drone" for i in self.incidents),"ground_first":sum(i.get("first_on_scene")=="ground" for i in self.incidents)}

    def _random_site(self,category,r):
        """Valid random location for an operator-added incident (r is a dedicated, reproducible RNG)."""
        b=self.world.get("bounds",[-600,-600,600,600])
        def usable(xy):return b[0]+60<xy[0]<b[2]-60 and b[1]+60<xy[1]<b[3]-60 and not self._in_no_fly(xy) and min((_distance(xy,i["position"][:2]) for i in self.incidents if i["status"]!="resolved"),default=1e9)>45
        if category in ("fire","trapped","flood"):
            reach=self._reachable_road_nodes(self._base)
            pool=sorted(self._building_index())
            r.shuffle(pool)
            for building_id,c,pts,height in pool[:400]:
                if not usable(c):continue
                wet=self._submerged(c)
                if category=="flood":
                    # Boat case: a roof inside the flood corridor.
                    if not wet:continue
                else:
                    link=self._road_connector(c)
                    if wet or link is None or link[1] not in reach or link[0]>60:continue
                return [round(c[0],2),round(c[1],2),height],building_id
            if category!="flood":return None,None
            projection=self._river_projection([0,0,0])
            return ([round(projection[1][0],2),round(projection[1][1],2),self.flood["level"]],None) if projection else (None,None)
        nodes=sorted(q for q in self._reachable_road_nodes(self._base) if usable(q) and not self._submerged(q))
        r.shuffle(nodes)
        for q in nodes[:30]:
            if self._ground_path(self._base,q) is not None:return [q[0],q[1],0],None
        return None,None

    def _snap_detection(self,position,category):
        """Snap an operator map click: inside a footprint -> that roof; ground categories -> nearest open road point."""
        b=self.world.get("bounds",[-600,-600,600,600])
        if not (b[0]<=position[0]<=b[2] and b[1]<=position[1]<=b[3]):return position,None
        building=self._building_at(position[:2])
        if building and category in ("fire","trapped","flood"):
            return [round(building[1][0],2),round(building[1][1],2),building[3]],building[0]
        if category in ("medical","blocked_route"):
            if building:  # a casualty is reported at the street side of the building
                position=[building[1][0],building[1][1],0]
            link=self._road_connector(position)
            if link is not None and link[0]<=80:return [round(link[3][0],2),round(link[3][1],2),0.0],None
        return [position[0],position[1],0.0 if len(position)<3 else position[2]],None

    # ---- goal-directed search: probability map, zones, lawnmower sweeps ----
    def _setup_search(self):
        cache=self.__dict__.get("_search_prior_cache")
        if cache is None:
            b=self.world.get("bounds",[-600,-600,600,600])
            xs=list(range(math.ceil((b[0]+50)/SEARCH_SPACING)*SEARCH_SPACING,int(b[2]-49),SEARCH_SPACING))
            ys=list(range(math.ceil((b[1]+50)/SEARCH_SPACING)*SEARCH_SPACING,int(b[3]-49),SEARCH_SPACING))
            density={}
            for building in self.world.get("buildings",[]):
                fp=building.get("footprint",[])
                if not fp:continue
                cx=sum(q[0] for q in fp)/len(fp);cy=sum(q[1] for q in fp)/len(fp)
                key=(round(cx/SEARCH_SPACING)*SEARCH_SPACING,round(cy/SEARCH_SPACING)*SEARCH_SPACING)
                density[key]=density.get(key,0)+1
            peak=max(density.values(),default=1) or 1
            water_band=float(self.world.get("river_width",45))/2+120
            cache={}
            for ix,x in enumerate(xs):
                for iy,y in enumerate(ys):
                    projection=self._river_projection([x,y,0])
                    water=1.0 if projection and projection[0]<water_band else 0.0
                    prior=round(min(1.0,.15+.35*water+.3*density.get((x,y),0)/peak),3)
                    cache[f"C{ix:02d}{iy:02d}"]=((x,y),prior)
            self._search_prior_cache=cache
        self.search_cells={cid:{"id":cid,"center":center,"prior":prior,"p":prior,"last":None,"blocked":False} for cid,(center,prior) in cache.items()}
        self._cell_by_center={c["center"]:cid for cid,c in self.search_cells.items()}
        self._zone_key=None

    def _raise_search_prior(self,position,amount=.3,radius=150):
        for cell in self.search_cells.values():
            if _distance(cell["center"],position[:2])<=radius:
                cell["prior"]=round(min(1.0,cell["prior"]+amount),3);cell["p"]=min(1.0,cell["p"]+amount)

    def _observe_cells(self,drone,delta):
        z=drone["position"][2]
        if z<=3:return
        r=z*math.tan(SENSOR_HALF_ANGLE)
        x,y=drone["position"][0],drone["position"][1]
        reach=r+30;span=int(reach//SEARCH_SPACING)+1
        gx,gy=round(x/SEARCH_SPACING),round(y/SEARCH_SPACING)
        for ix in range(gx-span,gx+span+1):
            for iy in range(gy-span,gy+span+1):
                cid=self._cell_by_center.get((ix*SEARCH_SPACING,iy*SEARCH_SPACING))
                if cid is None:continue
                cell=self.search_cells[cid];cx,cy=cell["center"]
                if (cx-x)**2+(cy-y)**2<=reach*reach and (cell["last"] is None or self.t-cell["last"]>=15):
                    cell["p"]*=.35;cell["last"]=self.t

    def _update_zones(self):
        """Voronoi partition of search cells among usable aircraft; recomputed only when membership changes."""
        searchers=[d for d in self.drones if d["active"] and d["link"]]
        key=tuple(d["id"] for d in searchers)
        if key==self._zone_key:return
        self._zone_key=key
        cells=[c for c in self.search_cells.values() if not c["blocked"]]
        for drone in self.drones:drone["zone"]=[]
        if not cells or not searchers:return
        k=min(len(searchers),len(cells))
        start=min(cells,key=lambda c:(_distance(c["center"],self._base[:2]),c["id"]))
        # Farthest-point sampling with an incrementally maintained nearest-seed distance (O(k*n)).
        seeds=[start];nearest={c["id"]:_distance(c["center"],start["center"]) for c in cells}
        while len(seeds)<k:
            pick=max(cells,key=lambda c:(nearest[c["id"]],c["id"]))
            seeds.append(pick)
            for c in cells:nearest[c["id"]]=min(nearest[c["id"]],_distance(c["center"],pick["center"]))
        free=list(searchers);owner={}
        for seed in seeds:
            drone=min(free,key=lambda d:(_distance(d.get("home",d["position"])[:2],seed["center"]),d["id"]))
            free.remove(drone);owner[seed["id"]]=drone
        for cell in cells:
            seed=min(seeds,key=lambda s:(_distance(s["center"],cell["center"]),s["id"]))
            owner[seed["id"]]["zone"].append(cell["id"])
        for drone in searchers:drone["zone"].sort()
        if self.t>0:self._event("zones",f"Search zones recomputed for {k} aircraft ({len(cells)} cells); {len(searchers)-k} in reserve.")

    def _next_search(self,drone):
        """Fly to the most likely unsearched cell of the drone's own zone, then sweep it."""
        zone=[self.search_cells[c] for c in drone.get("zone",[]) if not self.search_cells[c]["blocked"]]
        if not zone:return False
        ranked=sorted(zone,key=lambda c:(-round(c["p"],3),_distance(c["center"],drone["position"][:2]),c["id"]))
        for cell in ranked[:4]:
            cx,cy=cell["center"]
            path=self._drone_path(drone,[cx,cy,0])
            if path is None:
                cell["blocked"]=True;self._zone_key=None
                continue
            altitude=path[-1][2];h=SEARCH_SPACING*.3
            # Two-lane boustrophedon (lane spacing 0.6 cell ~ 2x sensor radius at cruise).
            legs=[[cx-h,cy-h,altitude],[cx+h,cy-h,altitude],[cx+h,cy+h,altitude],[cx-h,cy+h,altitude]]
            legs=[p for p in legs if not self._restricted(p,p)]
            drone.update(state="patrolling",route=path+legs,coverage_task=(cx,cy),search_target=cell["id"])
            return True
        return False

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
            "people":incident["people"],"hazards":{"flood":["flood water"],"fire":["fire","smoke"],"trapped":["smoke","restricted access"]}.get(incident["category"],["restricted access"]),
            "needs":list(incident["needs"]),"supplied":list(incident["supplied"]),
            "summary":f"{incident['people']} people; needs: {', '.join(incident['needs'])}. Ground assessment required.",
            "confidence":0.82 if drone else 0.95}
        # Revision means briefing content changed, not merely that a heartbeat
        # refreshed observation time. Otherwise valid delayed ACKs starve.
        changed=any(briefing[key]!=previous.get(key) for key in ("people","hazards","needs","supplied","source","summary"))
        briefing["revision"]=previous.get("revision",0)+int(changed)
        incident["briefing"]=briefing
        if incident.get("briefed_at") is None:incident["briefed_at"]=round(self.t,2)

    def _drone_arrival_brief(self,drone,incident,first_look=True):
        """Drone-first: brief control and every dispatched agency, then name who holds missing supplies."""
        pos=drone["position"]
        if incident.get("drone_on_scene_at") is None:incident["drone_on_scene_at"]=round(self.t,2)
        if incident.get("first_on_scene") is None:incident["first_on_scene"]="drone"
        self._event("drone_on_scene",f"{drone['id']} on scene at {incident['id']}.",incident["id"],data={"drone_id":drone["id"],"first_on_scene":incident["first_on_scene"]})
        self._signal("arrival","drone",drone["id"],["control"],pos,incident["id"],f"{drone['id']} on station over {incident['id']} ({incident['category']}).")
        if first_look:
            # Understand -> resources: compare what the scene needs with what is on the way.
            for kind in incident["required_kinds"]:
                if kind not in TRANSPORT_KINDS:continue
                units=[r for r in self.resources if r["kind"]==kind and r.get("incident_id")==incident["id"]]
                capacity=sum(r.get("capacity",0) for r in units)
                if units and incident["people"]>capacity and not any(q["needed_kind"]==kind and q["stage"]=="assessment" for q in incident["resource_requests"]):
                    self._raise_resource_request(incident,kind,1,f"briefing r{incident['briefing']['revision']}: {incident['people']} people exceed {capacity} seats on the way","assessment")
        recipients=[r for r in self.resources if r["id"] in incident["resource_ids"] and r["incident_id"]==incident["id"]]
        for resource in recipients:
            self._signal("briefing","drone",drone["id"],[resource["id"]],pos,incident["id"],
                         f"Brief to {resource.get('name',resource['id'])}: {incident['people']} people, hazards {', '.join(incident['briefing']['hazards'])}.")
        names=", ".join(r["id"] for r in recipients) or "no agency dispatched yet"
        self._event("briefing",f"{drone['id']} shares current situation and outstanding needs with dispatched agencies ({names}).",incident["id"],
                    data={"revision":incident["briefing"]["revision"],"source":drone["id"],"people":incident["people"],"hazards":incident["briefing"]["hazards"],"needs":incident["needs"],"recipients":[r["id"] for r in recipients]})
        outstanding=[n for n in incident["needs"] if n in SUPPLY_ITEMS and n not in incident["supplied"]]
        for need in outstanding:
            holder=self._supply_holder(need,incident["position"],exclude=(drone["id"],))
            if holder is None:
                text=f"{incident['id']} still needs {need}; no stocked aircraft or ground unit holds it."
                self._signal("supply_request","drone",drone["id"],["control"],pos,incident["id"],text)
            else:
                text=f"{incident['id']} needs {need}: nearest holder {holder['id']} ({holder['type']}, {holder['distance']:.0f} m)."
                self._signal("supply_request","drone",drone["id"],[holder["id"],"control"],pos,incident["id"],text)
            incident.setdefault("supply_requests",[])
            if need not in [r["need"] for r in incident["supply_requests"]]:
                incident["supply_requests"].append({"need":need,"holder_id":holder["id"] if holder else None,"holder_type":holder["type"] if holder else None,"at":round(self.t,2)})
            self._event("supply_request",text,incident["id"])

    def _ack(self,incident,resource):
        if resource["incident_id"]!=incident["id"] or resource["state"] not in ("awaiting_handoff","on_scene"):
            return False
        if incident["briefing"] is None:
            self._brief(incident)
            others=[d["id"] for d in self.drones if d["task_id"]==incident["id"]]
            self._signal("briefing","resource",resource["id"],[*others,"control"],resource["position"],incident["id"],
                         f"Ground-crew briefing from {resource.get('name',resource['id'])}: {incident['people']} people, needs {', '.join(incident['needs'])}.")
        resource["acknowledged"]=True;resource["state"]="on_scene"
        if resource.get("transport")=="boat" or resource["kind"] in ("ambulance","rescue"):
            # Do not double-count people already aboard another unit at this incident.
            aboard=sum(r.get("load",0) for r in self.resources if r is not resource and r.get("incident_id")==incident["id"])
            resource["load"]=min(resource["capacity"],max(0,incident["people"]-incident["evacuated_people"]-aboard))
            resource["service_end"]=self.t+8+resource["load"]*2
        else:resource["service_end"]=self.t+(18+incident["people"]*2)*max(1,math.ceil(incident["people"]/max(1,resource["capacity"])))
        first=not incident["handed_over"]
        incident["handed_over"]=True;incident["status"]="ground_response"
        source=incident["briefing"]["source"]
        self._event("handoff",f"{resource['id']} arrived and acknowledged the situation briefing ({'own ground-crew assessment' if source=='ground crew' else 'from '+source}); physical response continues.",incident["id"])
        drone=next((d for d in self.drones if d["id"]==incident["drone_id"]),None)
        self._signal("ack","resource",resource["id"],[x for x in [drone["id"] if drone else None,"control"] if x],resource["position"],incident["id"],
                     f"{resource.get('name',resource['id'])} on scene; briefing r{incident['briefing'].get('revision',0)} acknowledged.")
        if first and drone:
            if not drone["link"]:
                self._event("handoff_pending",f"{drone['id']} unreachable; recall remains unconfirmed.",incident["id"])
            elif drone["state"]=="enroute":
                # Ground-first: the crew is on scene before this aircraft arrived.
                why="reached the scene first and produced the briefing" if source=="ground crew" else "is on scene and accepted the briefing"
                outstanding=[n for n in incident["needs"] if n in SUPPLY_ITEMS and n not in incident["supplied"]]
                if drone["payload"] in outstanding and drone["payload_stock"]>0:
                    drone["retasked"]="supply"
                    self._event("drone_retask",f"{drone['id']} retasked: {resource['id']} {why}; aircraft continues only to drop {drone['payload']}, then returns.",incident["id"])
                else:
                    self._signal("recall","resource",resource["id"],[drone["id"]],resource["position"],incident["id"],
                                 f"Ground crew on scene first; {drone['id']} recalled to {drone.get('dock_id') or 'base'}.")
                    self._return(drone,f"recalled - {resource['id']} {why}","drone_recall")
            else:
                self._signal("recall","resource",resource["id"],[drone["id"]],resource["position"],incident["id"],f"Handoff accepted; {drone['id']} released.")
                self._return(drone,"ground crew confirmed briefing and arrival")
        return True

    def step(self,dt):
        if not math.isfinite(float(dt)) or dt<0:raise ValueError("dt must be finite and non-negative")
        if self.paused:return
        remaining=min(float(dt),10)*self.speed
        # Substeps avoid skipping arrival, acknowledgments and energy thresholds.
        while remaining>1e-9:
            delta=min(.25,remaining);remaining-=delta;self.t+=delta
            # Unobserved likelihood slowly relaxes back toward the prior.
            relax=min(1.0,delta/600)
            for cell in self.search_cells.values():cell["p"]+=(cell["prior"]-cell["p"])*relax
            # Candidates for local separation (airborne height re-checked live below).
            airborne=[d for d in self.drones if d["active"] and (d["position"][2]>=1 or d["route"])]
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
                    px,py,pz=predicted
                    for other in airborne:
                        op=other["position"]
                        if op[2]<8 or other["id"]>=drone["id"]:continue
                        dx=px-op[0]
                        if dx>=9 or dx<=-9:continue
                        dy=py-op[1]
                        if dy>=9 or dy<=-9:continue
                        dz=pz-op[2]
                        if dx*dx+dy*dy+dz*dz<81:
                            yield_to_peer=True;break
                drone["yielding"]=yield_to_peer
                before=list(drone["position"])
                if not yield_to_peer:self._move(drone,delta,18)
                drone["speed_mps"]=round(_distance(before,drone["position"])/delta,2)
                self._observe_cells(drone,delta)
                incident=self._find_incident(drone["task_id"])
                if drone["state"]=="enroute" and not drone["route"] and incident and drone.get("retasked")=="supply":
                    # Ground crew already briefed: drop the supply only, then go home.
                    if drone["payload_stock"]>0 and drone["payload"] in incident["needs"] and drone["payload"] not in incident["supplied"]:
                        incident["supplied"].append(drone["payload"]);drone["delivered"]=True;drone["payload_stock"]-=1;drone["delivered_at"]=self.t
                        self._event("supply",f"{drone['id']} simulated {drone['payload']} delivery to the ground crew; incident remains open.",incident["id"])
                        self._signal("ack","drone",drone["id"],[incident["id"],*[r for r in incident["resource_ids"]]],drone["position"],incident["id"],f"{drone['id']} dropped {drone['payload']} at {incident['id']}.")
                    self._return(drone,"supply drop complete; ground crew holds the scene")
                elif drone["state"]=="enroute" and not drone["route"] and incident:
                    drone["state"]="observing";incident["status"]="observing" if not incident["handed_over"] else incident["status"];self._brief(incident,drone)
                    first_look=incident["observations"]<=1
                    if drone["payload_stock"]>0 and drone["payload"] in incident["needs"] and drone["payload"] not in incident["supplied"]:
                        incident["supplied"].append(drone["payload"]);drone["delivered"]=True;drone["payload_stock"]-=1;drone["delivered_at"]=self.t
                        self._event("supply",f"{drone['id']} simulated {drone['payload']} delivery; incident remains open.",incident["id"])
                        self._signal("ack","drone",drone["id"],[incident["id"]],drone["position"],incident["id"],f"{drone['id']} dropped {drone['payload']} at {incident['id']}.")
                    self._brief(incident,drone)
                    self._drone_arrival_brief(drone,incident,first_look)
                elif drone["state"]=="observing" and incident:
                    if self.t-(incident["last_observed"] or 0)>4:self._brief(incident,drone)
                    if drone["payload_stock"]==0 and self.t-drone.get("delivered_at",self.t)>8:self._return(drone,"supplies exhausted; returning to reload while incident and agency response remain open")
                elif drone["state"]=="returning" and not drone["route"]:
                    drone["state"]="charging"
                    if drone["payload_stock"]==0:
                        drone["payload_stock"]=drone["payload_capacity"]
                        self._event("reload",f"{drone['id']} reloaded {drone['payload']} at {drone['dock_id'] if drone.get('dock_id') else 'the operations base'}.")
                elif drone["state"]=="charging":
                    drone["energy_wh"]=min(drone["capacity_wh"],drone["energy_wh"]+delta*.7)
                    drone["battery"]=drone["energy_wh"]/drone["capacity_wh"]
                    if drone["battery"]>.85:drone["state"]="ready"
                elif drone["state"]=="patrolling" and not drone["route"]:
                    # Sweep finished: go straight to the next most likely cell
                    # in the zone (no hovering in 'ready'), else fly home.
                    drone.update(coverage_task=None,search_target=None)
                    if not (drone["battery"]>=.4 and self._next_search(drone)):
                        self._return(drone,"search zone swept")
            for resource in self.resources:
                if self.external_ground:continue
                if resource["state"]=="busy" and resource["free_at"] is not None and self.t>=resource["free_at"]:
                    resource.update(state="available",free_at=None,freed_at=self.t)
                    self._event("resource_free",f"{resource['id']} is now free; pending incidents will be reconsidered.")
                if resource["state"] in ("enroute","evacuating"):
                    velocity=7 if resource.get("transport")=="boat" else 9
                    before=list(resource["position"])
                    waiting=resource.get("approach_until") is not None and self.t<resource["approach_until"]
                    if resource.get("approach_until") is not None and not waiting:
                        # Off-map approach complete: the unit now drives inside the district.
                        resource.update(approach_until=None,approach_m=0.0,off_map=False)
                        self._event("ground_entered",f"{resource['id']} entered the district.",resource["incident_id"])
                    if resource.get("offmap_until") is not None:waiting=waiting or self.t<resource["offmap_until"]
                    if not waiting:self._move(resource,delta,velocity)
                    resource["speed_mps"]=round(_distance(before,resource["position"])/delta,2)
                    resource["eta"]=self._route_distance(resource["position"],resource["route"])/velocity
                    if waiting and resource.get("approach_until") is not None:resource["eta"]+=resource["approach_until"]-self.t
                    if waiting and resource.get("offmap_until") is not None:resource["eta"]=resource["offmap_until"]-self.t
                    if not resource["route"] and not waiting:
                        if resource["state"]=="evacuating" and resource.get("offmap_m",0)>0 and resource.get("offmap_until") is None:
                            # Leave the district for the real (off-map) hospital or shelter.
                            run=resource["offmap_m"]/velocity;resource.update(offmap_until=self.t+run,off_map=True)
                            self._event("offmap_transfer",f"{resource['id']} left the district for {resource.get('transfer_destination','transfer')}, ~{_clock(run)} away.",resource["incident_id"])
                            continue
                        if resource["state"]=="evacuating":
                            incident=self._find_incident(resource["incident_id"])
                            if resource.get("offmap_until") is not None:
                                # The unit is now at the real destination, outside the map.
                                resource.update(offmap_until=None,approach_m=resource.get("offmap_m",0.0),offmap_m=0.0)
                            if incident:
                                incident["evacuated_people"]+=resource["load"]
                                if resource["kind"]=="ambulance":incident["hospitalised_people"]=incident.get("hospitalised_people",0)+resource["load"]
                                resource["load"]=0
                                self._event("evacuation",f"{incident['evacuated_people']}/{incident['people']} people reached {resource.get('transfer_destination','a safe transfer point')}.",incident["id"])
                                if incident["evacuated_people"]<incident["people"]:
                                    route=self._resource_path(resource,incident)
                                    if route is not None and resource.get("approach_m",0)>0:resource["approach_until"]=self.t+resource["approach_m"]/velocity
                                    resource.update(state="enroute" if route is not None else "holding",route=route or [],arrived_at=None)
                                    continue
                                self._complete_service(resource,incident)
                        else:
                            resource.update(state="awaiting_handoff",arrived_at=self.t,eta=0)
                            arrived=self._find_incident(resource["incident_id"])
                            aircraft=[d["id"] for d in self.drones if arrived and d["task_id"]==arrived["id"]]
                            first_on_scene=bool(arrived) and arrived["observations"]==0 and not arrived["handed_over"]
                            note=" first on scene; ground crew will produce the briefing" if first_on_scene else "; awaiting confirmed briefing"
                            if arrived and arrived.get("first_on_scene") is None:arrived["first_on_scene"]="ground"
                            self._event("ground_arrival",f"{resource['id']} reached the incident{note}.",resource["incident_id"],
                                        data={"unit_id":resource["id"],"first":arrived.get("first_on_scene") if arrived else None})
                            self._signal("arrival","resource",resource["id"],[*aircraft,"control"],resource["position"],resource["incident_id"],
                                         f"{resource.get('name',resource['id'])} on scene at {resource['incident_id']}.")
                incident=self._find_incident(resource["incident_id"])
                if resource["state"]=="awaiting_handoff" and incident and self.t-resource["arrived_at"]>=4:
                    self._ack(incident,resource)
                if resource["state"]=="on_scene" and self.t>=resource.get("service_end",float("inf")) and incident:
                    transport=resource.get("transport")=="boat" or resource["kind"] in ("ambulance","rescue")
                    if transport and resource["load"]==0 and incident["evacuated_people"]<incident["people"]:
                        # Everyone left is already aboard another unit: this unit is released, not "completed".
                        resource.update(state="available",incident_id=None,eta=None,acknowledged=False)
                        self._event("ground_release",f"{resource['id']} released: remaining people are aboard another unit.",incident["id"])
                    elif transport and resource["load"]==0:self._complete_service(resource,incident)
                    elif transport:
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
        self._prune_signals()
        self.revision+=1

    def _complete_service(self,resource,incident):
        if resource["kind"]=="fire_engine" and incident["category"]=="fire" and incident["evacuated_people"]<incident["people"]:
            # Fire crews bring occupants down to the street during their (timed) service.
            moved=incident["people"]-incident["evacuated_people"];incident["evacuated_people"]=incident["people"]
            self._event("evacuation",f"{resource['id']} brought {moved} occupants down to safety; {incident['evacuated_people']}/{incident['people']} safe.",incident["id"])
        if resource["kind"] not in incident["completed_kinds"]:incident["completed_kinds"].append(resource["kind"])
        if all(k in incident["completed_kinds"] for k in incident["required_kinds"]):
            incident["status"]="resolved";incident["resolved_at"]=self.t
            self._event("resolved","Required agency actions completed; incident resolved.",incident["id"])
        resource.update(state="available",incident_id=None,eta=None,acknowledged=False)

    def _transfer_path(self,resource):
        resource["offmap_m"]=0.0
        lifelines=getattr(self,"_lifelines",None)
        if lifelines and resource.get("transport")!="boat":
            # Catalog lifelines: drive to an on-map destination, else to the
            # map-edge road nearest the real hospital/shelter plus an off-map run.
            kinds=("hospital",) if resource["kind"]=="ambulance" else ("shelter","relief_centre")
            places=[l for l in lifelines if l.get("kind") in kinds]
            if places:
                me=resource["position"]
                def real_cost(place):
                    xy=place["local_xy"]
                    if place.get("on_map"):return _distance(me[:2],xy),xy,0.0
                    entry=self._road_entry(xy)
                    return (_distance(me[:2],entry)+_distance(entry,xy),entry,_distance(entry,xy)) if entry else (float("inf"),None,0.0)
                for place in sorted(places,key=lambda p:(real_cost(p)[0],p.get("source_id",""))):
                    _,target,offmap=real_cost(place)
                    if target is None:continue
                    path=self._ground_path(me,[target[0],target[1],0])
                    if path is not None:
                        resource["offmap_m"]=round(offmap,1)
                        return path,place.get("name",place.get("source_id","transfer point"))
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
        created={}
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
            elif action=="fleet":
                value=payload.get("value")
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value!=int(value):raise ValueError("Fleet size must be an integer")
                if not 1<=int(value)<=100:raise ValueError("Fleet size must be between 1 and 100")
                self.fleet_size=int(value)
                self._initialize(self.scenario["id"])
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
                raw=payload.get("position",[0,0,0])
                if not isinstance(raw,list) or len(raw) not in (2,3) or any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in raw):raise ValueError("Detection position must be two or three finite coordinates")
                position=([float(v) for v in raw]+[0.0])[:3]
                if not all(math.isfinite(v) and abs(v)<1e6 for v in position):raise ValueError("Detection position must be two or three finite coordinates")
                category=payload.get("category","medical")
                if category not in self.INCIDENT_TEMPLATES:raise ValueError("Unknown incident category")
                needs=self._validated_needs(payload.get("needs",["first_aid"]))
                severity,people=self._validated_numbers(payload)
                # UI detections are SOS requests through the same agency path as the orchestrator.
                created=self._agency_request({"request_id":self._sim_request_id(),"kind":"sos","position":position,"category":category,
                    "people":people,"needs":needs,"priority":severity,"requested_by":"operator-ui","title":payload.get("title")},"orchestrator-sim")
                if not created["accepted"]:raise ValueError(created["reason"])
            elif action=="random_incident":
                category=payload.get("category")
                if category is not None and category not in self.INCIDENT_TEMPLATES:raise ValueError("category must be one of medical, flood, fire, trapped, blocked_route")
                if self._open_incidents()>=MAX_OPEN_INCIDENTS:raise ValueError(f"Too many open incidents (limit {MAX_OPEN_INCIDENTS})")
                # Reproducible: depends only on (seed, counter, category), not on wall time or the sim RNG stream.
                self._random_incidents+=1
                r=random.Random(f"{self.seed}:random_incident:{self._random_incidents}")
                category=category or r.choice(sorted(self.INCIDENT_TEMPLATES))
                position,building_id=self._random_site(category,r)
                if position is None:raise ValueError(f"No valid location for a {category} incident on this map")
                title,severity,(low,high),needs,required=self.INCIDENT_TEMPLATES[category]
                created=self._agency_request({"request_id":self._sim_request_id(),"kind":"sos","position":position,"category":category,
                    "people":r.randint(low,high),"needs":needs,"priority":severity,"requested_by":"random-issue harness","title":title},
                    "orchestrator-sim",building_id=building_id,snap=False)
                if not created["accepted"]:raise ValueError(created["reason"])
                created["category"]=category
            elif action=="agency_request":
                request_id=payload.get("request_id")
                if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError("request_id (1-128 characters) is required")
                if request_id in self._agency_requests:
                    created={**self._agency_requests[request_id],"duplicate":True}
                else:
                    if payload.get("sim_run_id") not in (None,self.run_id):raise ValueError("Request belongs to a different simulation run")
                    kind=payload.get("kind","sos")
                    if kind not in REQUEST_KINDS:raise ValueError("kind must be one of sos, assess, supply, overwatch")
                    raw=payload.get("position")
                    if not isinstance(raw,list) or len(raw) not in (2,3) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in raw):raise ValueError("position must be two or three finite coordinates")
                    category=payload.get("category","medical")
                    if category not in self.INCIDENT_TEMPLATES:raise ValueError("Unknown incident category")
                    default_needs={"assess":["assessment"],"overwatch":["assessment"]}.get(kind,self.INCIDENT_TEMPLATES[category][3])
                    needs=self._validated_needs(payload.get("needs",default_needs))
                    severity,people=self._validated_numbers({"severity":payload.get("priority",3),"people":payload.get("people",1)})
                    created=self._agency_request({"request_id":request_id,"kind":kind,"position":([float(v) for v in raw]+[0.0])[:3],"category":category,
                        "people":people,"needs":needs,"priority":severity,"requested_by":str(payload.get("requested_by","orchestrator"))[:80]},"orchestrator")
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
                    if incident and identifier not in incident["resource_ids"]:
                        incident["resource_ids"].append(identifier)
                        self._fulfil_request(incident,resource["kind"],identifier)
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
            return {"ok":True,"action":action,"run_id":self.run_id,"revision":self.revision,**created}
        except (ValueError,TypeError,OverflowError) as error:
            return {"ok":False,"action":action,"error":str(error)}

    def _person_position(self,incident,index):
        """Deterministic (seed, incident, index) placement that does not touch self.rng:
        on the roof inside the building footprint, at a boat pickup site, or on the ground beside the road."""
        cache=self.__dict__.setdefault("_person_positions",{})
        key=(self.seed,incident["id"],index,incident.get("building_id"))
        if key in cache:return cache[key]
        r=random.Random(f"{self.seed}:{incident['id']}:{index}")
        p=incident["position"]
        building=next((b for b in self._building_index() if b[0]==incident.get("building_id")),None) if incident.get("building_id") else None
        if building:
            _,c,pts,height=building
            xs=[q[0] for q in pts];ys=[q[1] for q in pts];spot=None
            for _ in range(40):
                q=[r.uniform(min(xs),max(xs)),r.uniform(min(ys),max(ys))]
                # Pull samples toward the centroid so people stand inside the roof edge.
                q=[c[0]+(q[0]-c[0])*.75,c[1]+(q[1]-c[1])*.75]
                if _inside(q,pts):spot=q;break
            spot=spot or list(c)
            result=([round(spot[0],2),round(spot[1],2),round(height,2)],"roof",building[0])
        elif incident["category"]=="flood" and self._submerged(p):
            angle=r.random()*math.tau;radius=2+r.random()*5
            result=([round(p[0]+radius*math.cos(angle),2),round(p[1]+radius*math.sin(angle),2),round(self.flood["level"],2)],"boat_site",None)
        else:
            # Beside the road: offset sideways from the nearest open road segment.
            link=self._road_connector(p)
            base=link[3] if link else (p[0],p[1])
            angle=r.random()*math.tau;radius=3+r.random()*4
            result=([round(base[0]+radius*math.cos(angle),2),round(base[1]+radius*math.sin(angle),2),0.0],"ground",None)
        cache[key]=result
        return result

    def _people(self):
        """One record per person in need. Order of fulfilment: hospitalised, evacuated, being rescued, then
        supplied/assessed/waiting for everyone else."""
        people=[]
        for incident in self.incidents:
            loaded=sum(r.get("load",0) for r in self.resources if r.get("incident_id")==incident["id"] and r["state"] in ("on_scene","evacuating"))
            on_scene=any(r.get("incident_id")==incident["id"] and r["state"] in ("awaiting_handoff","on_scene","evacuating") for r in self.resources)
            hospitalised=min(incident.get("hospitalised_people",0),incident["people"]);evacuated=min(incident["evacuated_people"],incident["people"])
            needs=[n for n in incident["needs"] if n!="assessment"] or [incident["category"]]
            for index in range(incident["people"]):
                if index<hospitalised:status="hospitalised"
                elif index<evacuated or incident["status"]=="resolved":status="evacuated"
                elif index<evacuated+loaded or on_scene and incident["handed_over"]:status="being_rescued"
                elif incident["supplied"]:status="supplied"
                elif incident["observations"]>0 or incident["briefing"]:status="assessed"
                else:status="waiting"
                position,on,building_id=self._person_position(incident,index)
                people.append({"id":f"{incident['id']}-P{index+1:02d}","incident_id":incident["id"],"position":list(position),
                    "on":on,"building_id":building_id,"status":status,"need":needs[index%len(needs)]})
        return people

    def _activity(self,drone):
        state=drone["state"];z=drone["position"][2]
        if not drone["active"] or state in ("crashed","emergency_landed"):return "failed"
        if state in ("holding","lost_link"):return "holding"
        if state=="charging":return "charging"
        if state=="ready":return "docked" if z<1 else "holding"
        if state=="returning":return "returning"
        if state=="observing":return "assessing"
        route=drone["route"]
        if route and z<route[0][2]-1 and _distance(drone["position"][:2],route[0][:2])<1:return "launching"
        if state=="patrolling":return "scanning"
        if state=="enroute":
            incident=self._find_incident(drone["task_id"])
            if drone.get("retasked")=="supply":return "delivering"
            if incident and incident["observations"]>0 and drone["payload_stock"]>0 and drone["payload"] in incident["needs"] and drone["payload"] not in incident["supplied"]:return "delivering"
            return "transit"
        return "holding"

    def _drone_view(self,drone):
        view=dict(drone)
        z=float(drone["position"][2])
        view["activity"]=self._activity(drone)
        view["sensor_radius_m"]=round(z*math.tan(SENSOR_HALF_ANGLE),1) if z>3 and drone["active"] else 0.0
        moving=drone["active"] and drone["state"] not in ("ready","charging","lost_link")
        view["speed_mps"]=float(drone.get("speed_mps",0.0)) if moving else 0.0
        view["behavior"]=self._behavior(drone)
        view["zone"]=list(drone.get("zone",[]))
        if drone["state"]=="patrolling" and drone.get("search_target"):
            view["target"]={"type":"cell","id":drone["search_target"],"position":[*self.search_cells[drone["search_target"]]["center"],0]}
        elif drone["task_id"]:
            incident=self._find_incident(drone["task_id"])
            view["target"]={"type":"incident","id":drone["task_id"],"position":list(incident["position"]) if incident else None}
        elif drone["state"]=="returning":
            view["target"]={"type":"dock" if drone.get("dock_id") else "base","id":drone.get("dock_id") or "base","position":list(drone.get("home",self._base))}
        else:view["target"]=None
        return view

    def _behavior(self,drone):
        """Behaviour-tree node: search -> confirm -> assess -> relay -> return (plus charge/idle/hold/failed)."""
        state=drone["state"]
        if not drone["active"] or state in ("crashed","emergency_landed"):return "failed"
        if state in ("holding","lost_link"):return "hold"
        if state=="patrolling":return "search"
        if state=="enroute":return "relay" if drone.get("retasked") else "confirm"
        if state=="observing":
            crew=any(r.get("incident_id")==drone["task_id"] and r["state"] in ("awaiting_handoff","on_scene") for r in self.resources)
            return "relay" if crew else "assess"
        if state=="returning":return "return"
        if state=="charging":return "charge"
        return "idle"

    def snapshot(self,include_world=True):
        active=[i for i in self.incidents if i["status"]!="resolved"]
        return copy.deepcopy({"schema_version":2,"run_id":self.run_id,"revision":self.revision,"t":round(self.t,3),
            "paused":self.paused,"speed":self.speed,"scenario":self.scenario,"anchor":ANCHOR,"flood":self.flood,"road_conditions":self.road_conditions,
            **({"world":self.world} if include_world else {}),
            "drones":[self._drone_view(d) for d in self.drones],"resources":self.resources,"incidents":self.incidents,"events":self.events,
            "signals":[s for s in self.signals if self.t-s["t"]<=SIGNAL_WINDOW],
            "docks":[{**dock,"docked":sum(1 for d in self.drones if d.get("dock_id")==dock["id"] and d["state"] in ("ready","charging")),
                      "launched":sum(1 for d in self.drones if d.get("dock_id")==dock["id"] and d["state"] in ("enroute","observing","returning","holding"))} for dock in self.docks],
            "coverage":[{"position":[*c["center"],0],"last_observed":c["last"]} for c in self.search_cells.values()],
            "search_map":[{"id":c["id"],"center":[*c["center"],0],"size_m":SEARCH_SPACING,"probability":round(c["p"],3),"prior":c["prior"],
                           "last_observed":None if c["last"] is None else round(c["last"],2),
                           "status":"blocked" if c["blocked"] else "cleared" if c["p"]<.12 else "searched" if c["last"] is not None else "unsearched"} for c in self.search_cells.values()],
            "people":self._people(),
            "metrics":{"fleet":len(self.drones),"active_drones":sum(d["active"] for d in self.drones),
                "open_incidents":len(active),"resolved_incidents":len(self.incidents)-len(active),
                "available_resources":sum(r["state"]=="available" for r in self.resources),
                "handoffs":sum(i["handed_over"] for i in self.incidents),"supply_drops":sum(len(i["supplied"]) for i in self.incidents),
                "faults":self._faults,"launched":self._launched() if self.docks else None,"dispatch_cap":DOCK_DISPATCH_CAP if self.docks else None,"covered_patches":sum(c["last"] is not None for c in self.search_cells.values()),"total_patches":len(self.search_cells),"simulation_only":True,"external_ground":self.external_ground,"agency":self._agency_metrics(),"allocation_mode":"deterministic feasible bids (central simulation harness)"}})
