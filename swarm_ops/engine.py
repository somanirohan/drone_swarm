"""Deterministic disaster-operations demonstrator; kinematics, not flight physics.

Allocations are feasible local bids reconciled by this simulation harness. This
is deliberately NOT a hardware swarm, decentralized consensus, or medical AI.
Ground dispatch, observational evidence and actual service completion are
separate from drone arrival and supply drops.

Plug-in hook (scenarios and worlds)
-----------------------------------
Another module can add a scenario without touching engine internals::

    from swarm_ops.engine import register_scenario, register_world

    register_world("pcmc", lambda: json.loads(Path(...).read_text()))   # dict or callable -> world dict
    def build(sim):                       # called on every (re)initialisation of that scenario
        sim.add_dock([120, -300])                                    # drones are spread over docks
        roof, building_id = sim.place_on_roof([400, 220])            # roof centre + height of a building
        sim.add_incident(roof, "fire", people=6, building_id=building_id)
        sim.add_resource("fire_engine", sim.nearest_road_point([900, 800]), name="Fire tender 01")
    register_scenario("pcmc_fire", "PCMC fire", "...", build, world_id="pcmc")

Selecting the scenario (``command("scenario", {"id": "pcmc_fire"})``) loads its world
(``world_id``; built-in scenarios run on the world the simulation was constructed with,
whose id is ``world["id"]`` or "pune-district"), applies flood closures, then calls
``builder(sim)``. Builders must only use the public helpers: ``sim.rng`` (seeded),
``sim.bounds``, ``add_incident``, ``add_dock``, ``add_resource``, ``place_on_roof``,
``nearest_road_point``, ``road_path``, ``buildings()``. When a builder adds docks, the
fleet starts docked (launch cap applies). Without docks the fleet starts at the first
``bases`` entry and searches. A world is the same dict shape as assets/operations/
pune-world.json (``bounds`` [minx,miny,maxx,maxy] in metres around ``anchor``; roads,
buildings, optional river/no_fly/bases/shelters). Worlds up to ~14 km x 14 km are
supported: the flight planner, road graph and search map scale their resolution.
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

from swarm_ops.agents import DecisionLog, DroneAgent, MessageBus, SwarmCoordinator
from swarm_ops.decentral import COORDINATION_MODES, DEFAULTS as COORDINATION_DEFAULTS, DecentralizedLayer
from swarm_ops.nodes import NodeRuntime, node_for_kind
from swarm_ops.planning import FlightPlanner
from swarm_ops.traffic import Traffic


ORCH_COMMANDS = {'orchestrate_incident','assign','unassign','update_incident','cancel_incident','close_incident'}
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
# Incident hover point is this far from the target, on the approach side (no overflight, no circling).
STANDOFF_M = 20.0
# A docked fleet does not relaunch for an already-assessed incident sooner than this.
RELAUNCH_COOLDOWN = 20.0
# Supplies a ground unit carries (illustrative); used to name who holds a missing item.
GROUND_SUPPLIES = {"ambulance": ("first_aid", "medical"), "rescue": ("food", "first_aid"), "fire_engine": ("first_aid",),
                   "boat": ("food", "first_aid"), "supply_truck": ("food", "first_aid"), "bus": ("food",)}
SUPPLY_ITEMS = ("first_aid", "medical", "food")
ACTIVITIES = ("docked", "launching", "transit", "scanning", "assessing", "delivering", "returning", "charging", "holding", "failed")
SENSOR_HALF_ANGLE = math.radians(30)
SEARCH_SPACING = 100  # metres per search-map cell
MAX_OPEN_INCIDENTS = 12  # operator-added incidents are rejected beyond this many open incidents
# Event kind -> status the swarm agency reports back to its orchestrator.
ORCHESTRATOR_STATUS = {"request_accepted": "accepted", "request_declined": "declined", "drone_launch": "launched",
    "drone_dispatch": "launched", "drone_on_scene": "on_scene", "briefing": "briefing", "dispatch_order": "dispatch_ordered",
    "resource_request_fulfilled": "resource_assigned", "supply": "supply_delivered", "ground_arrival": "ground_arrived",
    "drone_recall": "recalled", "drone_retask": "retasked", "handoff": "handed_over", "resolved": "completed"}
REQUEST_KINDS = ("sos", "assess", "supply", "overwatch")
# Ground events reported by the agency nodes themselves (unit_status / handoff), not by the swarm.
AGENCY_REPORTED = ("resource_request_fulfilled", "ground_arrival", "handoff")
TRANSPORT_KINDS = ("ambulance", "rescue", "boat", "bus")
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
CATALOG_KINDS = {"fire_engine": "fire_engine", "ambulance": "ambulance", "rescue_team": "rescue", "boat": "rescue", "jcb": "engineering",
                 "pump": "pump", "water_tanker": "water_tanker", "supply_truck": "supply_truck", "bus": "bus"}
GROUND_SPEED = 9.0
# Simulator resource kinds (DisruptionOps capability names; rescue_team -> rescue, jcb -> engineering).
# transport: road|boat; model: hint for the 3D viewer; carries_people: loads and transfers people.
RESOURCE_KINDS = {
    "fire_engine": {"transport": "road", "agency": "Fire brigade", "capacity": 6, "model": "fire_engine", "carries_people": False},
    "ambulance": {"transport": "road", "agency": "EMS", "capacity": 2, "model": "ambulance", "carries_people": True},
    "rescue": {"transport": "road", "agency": "District rescue", "capacity": 6, "model": "rescue_van", "carries_people": True},
    "boat": {"transport": "boat", "agency": "SDRF boat unit", "capacity": 6, "model": "boat", "carries_people": True},
    "engineering": {"transport": "road", "agency": "Public works", "capacity": 2, "model": "jcb", "carries_people": False},
    "pump": {"transport": "road", "agency": "PCMC water works", "capacity": 1, "model": "pump_truck", "carries_people": False},
    "water_tanker": {"transport": "road", "agency": "PCMC water supply", "capacity": 1, "model": "water_tanker", "carries_people": False},
    "supply_truck": {"transport": "road", "agency": "District relief", "capacity": 1, "model": "supply_truck", "carries_people": False},
    "bus": {"transport": "road", "agency": "PMPML", "capacity": 40, "model": "bus", "carries_people": True},
    # Dispatched only when a plan asks for it (taxonomy capability traffic_control); no template requires it.
    "police": {"transport": "road", "agency": "Police", "capacity": 4, "model": "police", "carries_people": False},
}
RESOURCE_ALIASES = {"rescue_team": "rescue", "jcb": "engineering"}
# A required kind may also be served by these kinds (exact kind preferred).
KIND_SUBSTITUTES = {"rescue": ("boat",), "boat": ("rescue",), "supply_truck": ("water_tanker",), "water_tanker": ("supply_truck",)}
CATEGORY_ALIASES = {}
HAZARDS = {"flood": ["flood water"], "fire": ["fire", "smoke"], "trapped": ["smoke", "restricted access"],
           "person_stranded": ["flood water"], "flooded_road": ["standing water"], "waterlogging": ["standing water"],
           "fallen_tree": ["blocked road", "debris"], "structural_damage": ["collapse", "debris"], "air_quality": ["smoke", "gas"],
           "evacuation": ["crowding"], "shelter_full": ["crowding"], "supply_shortage": ["shortage"]}
_ROAD_CACHE = {}
DEFAULT_WORLD_ID = "pune-district"
# id -> {"id","name","description","builder","world_id"}; built-ins have builder None (engine-internal).
SCENARIO_REGISTRY = {sid: {"id": sid, "name": n, "description": d, "builder": None, "world_id": None} for sid, (n, d) in SCENARIOS.items()}
# world id -> dict or zero-argument callable returning a world dict (loaded lazily, cached).
WORLD_REGISTRY = {}
_WORLD_CACHE = {}


def register_scenario(id, name, description, builder, world_id=DEFAULT_WORLD_ID):
    """Register (or replace) a scenario. builder(sim) creates incidents/docks/resources via the public helpers."""
    if not isinstance(id, str) or not id or len(id) > 64:
        raise ValueError("scenario id must be a non-empty string (<= 64 chars)")
    if builder is not None and not callable(builder):
        raise ValueError("builder must be callable")
    SCENARIO_REGISTRY[id] = {"id": id, "name": str(name), "description": str(description), "builder": builder, "world_id": world_id}
    SCENARIOS[id] = (str(name), str(description))
    return SCENARIO_REGISTRY[id]


def register_world(world_id, world):
    """Register a world dict, or a zero-argument callable that returns one (called once, lazily)."""
    WORLD_REGISTRY[str(world_id)] = world
    _WORLD_CACHE.pop(str(world_id), None)


def _resolve_world(world_id):
    if world_id not in _WORLD_CACHE:
        source = WORLD_REGISTRY.get(world_id)
        if source is None:
            raise ValueError(f"Unknown world '{world_id}'")
        world = source() if callable(source) else source
        if not isinstance(world, dict) or len(world.get("bounds", [])) != 4:
            raise ValueError(f"World '{world_id}' must be a dict with 4-value bounds")
        _WORLD_CACHE[world_id] = world
    return _WORLD_CACHE[world_id]


PUNE_WORLD_PATH = Path(__file__).resolve().parents[1] / "assets" / "operations" / "pune-world.json"
WORLD_REGISTRY[DEFAULT_WORLD_ID] = lambda: json.loads(PUNE_WORLD_PATH.read_text(encoding="utf8"))


def scenario_catalog():
    """[{id, name, description, world_id}] for every registered scenario (for UIs/servers)."""
    return [{k: v for k, v in spec.items() if k != "builder"} for spec in SCENARIO_REGISTRY.values()]


def _serves(resource, kind):
    """True when a resource can fill a required kind (exact kind or an accepted substitute)."""
    own = resource.get("kind")
    return own == kind or own in KIND_SUBSTITUTES.get(kind, ())


def _carries_people(resource):
    return resource.get("transport") == "boat" or RESOURCE_KINDS.get(resource.get("kind"), {}).get("carries_people", False)


_REFERENCE = {}


def reference_data():
    """Taxonomy + unit catalog + lifelines via swarm_ops.disruptionops_client.load_reference_data
    (live DisruptionOps API when configured, else the SQL-derived assets). Loaded once per process."""
    if not _REFERENCE:
        try:
            from swarm_ops.disruptionops_client import load_reference_data
            value = load_reference_data()
        except Exception as error:  # never block the simulation on reference data
            value = {"mode": "builtin", "taxonomy": {}, "catalog": {}, "errors": [f"{type(error).__name__}: {error}"[:160]]}
        _REFERENCE.update(value)
    return _REFERENCE


def _agency_catalog():
    """PCMC/PCCOE units and lifelines migrated read-only from DisruptionOps (029_pcmc_pccoe_zone)."""
    catalog = reference_data().get("catalog")
    if isinstance(catalog, dict) and catalog.get("resources"):
        return catalog
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

    # Per-world caches dropped when a different world is loaded.
    _WORLD_CACHES = ("_buildings_cache", "_road_base", "_search_prior_cache", "_person_positions",
                     "_connector_cache", "_ground_cache", "_planner", "_edge_index", "_building_grid")

    def __init__(self, world=None, seed=42, fleet_size=20, scenario="rooftop_flood", coordination="central"):
        if coordination not in COORDINATION_MODES:raise ValueError("coordination must be central, decentralized or orchestrated")
        # Swarm coordination mode (command set_coordination): kept across scenario resets.
        self._coordination_config = {**COORDINATION_DEFAULTS, "mode": coordination}
        self._home_world = copy.deepcopy(world or _default_world())
        self._home_world_id = str(self._home_world.get("id") or (DEFAULT_WORLD_ID if world is not None else "default-grid"))
        self.world = self._home_world
        self.world_id = self._home_world_id
        self.fleet_size = max(1, min(100, int(fleet_size)))
        self.seed = int(seed)
        if scenario not in SCENARIO_REGISTRY:raise ValueError("Unknown scenario")
        self._initialize(scenario)

    def _load_world(self, world_id):
        """Switch to another world (registered id, or the constructor's world) and drop per-world caches."""
        if world_id == self.world_id:return
        world = self._home_world if world_id == self._home_world_id else copy.deepcopy(_resolve_world(world_id))
        for name in self._WORLD_CACHES:self.__dict__.pop(name, None)
        self.world, self.world_id = world, world_id

    @property
    def bounds(self):
        return list(self.world.get("bounds", [-600, -600, 600, 600]))

    def _initialize(self, scenario):
        spec = SCENARIO_REGISTRY.get(scenario)
        if spec is None:raise ValueError("Unknown scenario")
        self._load_world(spec["world_id"] or self._home_world_id)
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
        clock=lambda:self.t
        self.bus = MessageBus(clock)
        self.decisions = DecisionLog(clock)
        self.external_ground = False
        self._last_allocate = -10
        self.flood={"enabled":True,"width":float(self.world.get("river_width",45))+90,"level":2.1,"synthetic":True,"model":"river corridor; prescribed depth, not hydrodynamics"}
        self.road_conditions=[]
        self._setup_search()
        self._base = list(self.world.get("bases", [{"position": [-440,-440,0]}])[0]["position"])
        self._base = (self._base + [0,0,0])[:3]
        mode = reference_data().get("mode", "assets")
        self.scenario = {"id": scenario, "name": spec["name"], "description": spec["description"], "seed": self.seed, "world_id": self.world_id,
                         "data_source": "live" if mode == "live" else "assets", "data_mode": mode}
        # Command-centre autonomy switch (command "control"): paused = no new launches/requests.
        self.control = {"autonomy": "running", "reason": None, "since": 0.0, "by": None}
        self.docks = []
        self.dispatch_cap = None  # builders may set {"per_incident": n, "total": m}
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
        for i, kind in enumerate([] if scenario=="dock_fire" or spec["builder"] else ["ambulance","ambulance","rescue","rescue","engineering"]):
            self.resources.append({"id": f"R{i+1:02}", "kind": kind, "state": "available" if i%2==0 else "busy",
                "position": [self._base[0]+i*12,self._base[1],0], "incident_id": None, "route": [], "eta": None,
                "agency": {"ambulance":"EMS","rescue":"District rescue","engineering":"Public works"}[kind],
                "capacity": 6 if kind=="rescue" else 2, "free_at": 12.0+i*3 if i%2 else None,
                "arrived_at": None, "acknowledged": False,"transport":"boat" if i==2 else "road","load":0})
            if i==2:
                staging=self._river_projection(self._base)
                if staging:self.resources[-1]["position"]=[*staging[1],self.flood["level"]]
        self.agents = {d["id"]: DroneAgent(d, self.bus, self.decisions) for d in self.drones}
        self.coordinator = SwarmCoordinator(self, self.bus, self.decisions)
        # central = coordinator auction (default); decentralized = drones self-organise (CBBA over a simulated
        # mesh radio); orchestrated = DisruptionOps assigns drones/roles. Per incident: incident["coordination_mode"].
        self.coordination = {"mode": self._coordination_config["mode"]}
        self.decentral = DecentralizedLayer(self, self._coordination_config)
        self._booting = True
        self.bus.listeners.append(self._on_bus_message)
        if getattr(self, "nodes", None) is None:
            self.nodes = NodeRuntime(self)
        else:
            self.nodes.reset()
        self._build_roads()
        for resource in self.resources:
            # A staging slot that falls inside a mapped footprint moves to the nearest road point.
            if resource.get("transport")=="road" and self._building_at(resource["position"][:2]):
                link=self._road_connector(resource["position"])
                if link:resource["position"]=[round(link[3][0],2),round(link[3][1],2),0]
        self._create_scenario(scenario)
        if self.docks:self._event("scenario", f"{self.scenario['name']} started; {self.fleet_size} drones docked at {len(self.docks)} stations, launch cap {self._cap_total()}.")
        else:self._event("scenario", f"{self.scenario['name']} started; {self.fleet_size} drones ready.")
        self.traffic = Traffic(self, count=self.traffic_vehicles, seed=self.seed)
        self._booting = False
        self.nodes.pump()   # the agency nodes register their units
        for incident in self.incidents:
            self._announce(incident, "scenario start")
        self.nodes.pump()
        self._allocate()
        self.nodes.pump()

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
        self._assign_drones_to_docks()
        self._station_nodes=sorted(q for q in reach if b[0]+20<q[0]<b[2]-20 and b[1]+20<q[1]<b[3]-20 and not self._submerged(q))
        self._load_catalog_resources(("fire_engine","ambulance","rescue_team","boat","jcb"))

    def _assign_drones_to_docks(self):
        """Round-robin every aircraft onto a dock; each owns a landing pad there and returns to that exact pad."""
        for dock in self.docks:dock["drone_ids"]=[]
        for i,drone in enumerate(self.drones):
            dock=self.docks[i%len(self.docks)];slot=len(dock["drone_ids"]);dock["drone_ids"].append(drone["id"])
            pad=[round(dock["position"][0]+(slot%4)*4-6,1),round(dock["position"][1]+(slot//4)*4-6,1),0]
            drone.update(dock_id=dock["id"],home=pad,state="ready",position=list(pad),lane=(slot%4)*8.0)

    # ---- public helpers for plug-in scenario builders (see module docstring) ----
    def buildings(self):
        """[(id, centroid(x,y), footprint points, height_m)] for every building of the current world."""
        return list(self._building_index())

    def nearest_road_point(self,xy,max_distance=150.0):
        """Closest point on an open (dry, unblocked) road within max_distance, as [x,y,0], else None."""
        link=self._road_connector([float(xy[0]),float(xy[1]),0],max(150.0,float(max_distance)))
        if link is None or link[0]>max_distance:return None
        return [round(link[3][0],2),round(link[3][1],2),0.0]

    def road_path(self,start,end):
        """Open-road route between two points (list of [x,y,0]) or None."""
        return self._ground_path(start,end)

    def place_on_roof(self,where):
        """Roof point of a building, by building id or by a position inside/near it: ([cx,cy,height], building_id)."""
        if isinstance(where,str):
            entry=next((b for b in self._building_index() if b[0]==where),None)
        else:
            entry=self._building_at(list(where)[:2],tolerance=60)
        if entry is None:raise ValueError(f"No building at {where!r}")
        return [round(entry[1][0],2),round(entry[1][1],2),entry[3]],entry[0]

    def add_incident(self,position,category,title=None,severity=None,people=None,needs=None,required=None,source="scenario",building_id=None):
        """Create (or merge into) an incident. Unspecified fields come from INCIDENT_TEMPLATES[category]."""
        category=self._category(category)
        t_title,t_severity,(low,high),t_needs,t_required=self.INCIDENT_TEMPLATES[category]
        people=self.rng.randint(low,high) if people is None else int(people)
        position=[float(v) for v in (list(position)+[0.0])[:3]]
        return self._incident(position,category,title or t_title,int(severity or t_severity),people,list(needs or t_needs),
                              list(required or t_required),source=source,building_id=building_id)

    def add_dock(self,position,name=None):
        """Add a drone dock; the fleet is spread over all docks after the builder returns."""
        n=len(self.docks)+1
        dock={"id":f"DOCK-{n}","name":name or f"Drone dock {n}","position":[round(float(position[0]),1),round(float(position[1]),1),0],"drone_ids":[]}
        self.docks.append(dock)
        return dock

    def add_resource(self,kind,position,name=None,id=None,agency=None,capacity=None,transport=None,state="available",**extra):
        """Add a ground (or boat) responder of any RESOURCE_KINDS kind at position."""
        kind=RESOURCE_ALIASES.get(kind,kind)
        if not isinstance(kind,str) or not kind:raise ValueError("resource kind is required")
        if position is None:raise ValueError("resource position is required")
        # Unknown kinds (e.g. police) are kept and shown, but only dispatched when a plan requires that kind.
        spec=RESOURCE_KINDS.get(kind,{"transport":"road","agency":kind.replace("_"," ").title(),"capacity":1,"model":kind,"carries_people":False})
        transport=transport or spec["transport"]
        identifier=id or f"R{len(self.resources)+1:02}"
        resource={"id":identifier,"kind":kind,"name":name or identifier,"agency":agency or spec["agency"],"state":state,
            "position":[float(v) for v in (list(position)+[0.0])[:3]],"incident_id":None,"route":[],"eta":None,
            "capacity":int(capacity if capacity is not None else spec["capacity"]),"free_at":None,"arrived_at":None,
            "acknowledged":False,"transport":transport,"load":0,"model":spec["model"],"approach_m":0.0,"approach_until":None,"off_map":False}
        if transport=="boat":resource["position"][2]=self.flood["level"]
        resource.update(extra)
        self.resources.append(resource)
        return resource

    def _ensure_ground_kinds(self,required):
        """Mutual aid: if the scenario has no unit of a required kind, bring in the real PCMC units of that kind."""
        if self.external_ground:return
        missing=[k for k in required if not any(_serves(r,k) for r in self.resources)]
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

    def _cap_total(self):
        """Most aircraft airborne at once on a docked fleet (scenario builders may set sim.dispatch_cap)."""
        cap=getattr(self,"dispatch_cap",None)
        return int(cap.get("total",DOCK_DISPATCH_CAP)) if isinstance(cap,dict) else DOCK_DISPATCH_CAP

    def _cap_per_incident(self):
        cap=getattr(self,"dispatch_cap",None)
        return int(cap.get("per_incident",DOCK_DISPATCH_CAP)) if isinstance(cap,dict) else DOCK_DISPATCH_CAP

    STANDOFF_M=STANDOFF_M
    traffic_vehicles=60  # civilian vehicles (<= 80); set on the instance/class before (re)initialising

    def _paused(self):
        return getattr(self,"control",{}).get("autonomy")=="paused"

    @property
    def RESOURCE_KIND_NAMES(self):
        return tuple(RESOURCE_KINDS)

    def _known_kinds(self):
        """Unit kinds the swarm can obtain: on the map now, or via PCMC mutual aid (catalog)."""
        kinds={r["kind"] for r in self.resources}
        if not self.external_ground:kinds|=set(CATALOG_KINDS.values())
        return kinds

    # ---- situation -> plan (SwarmCoordinator.plan_response) ----
    def _situation(self,incident):
        """Evidence-rich picture of one incident for the response planner (also in the snapshot)."""
        p=incident["position"];t=self.t
        evidence=incident.get("evidence") or []
        hazards=list(dict.fromkeys(list((incident["briefing"] or {}).get("hazards") or HAZARDS.get(incident["category"],[]))+list(incident.get("hazards_reported") or [])))
        building=next((b for b in self._building_index() if b[0]==incident.get("building_id")),None) if incident.get("building_id") else None
        if building:
            pts=building[2];area=abs(sum(pts[i][0]*pts[(i+1)%len(pts)][1]-pts[(i+1)%len(pts)][0]*pts[i][1] for i in range(len(pts))))/2
        else:area=400.0
        counts=[e.get("people") for e in evidence if isinstance(e.get("people"),(int,float))]
        if incident.get("trend"):trend=incident["trend"]
        elif incident["evacuated_people"]>0:trend="improving"
        elif len(counts)>=2 and counts[-1]>counts[0]:trend="worsening"
        elif counts:trend="stable"
        else:trend="unknown"
        lifelines=[]
        for item in (self.world.get("lifelines") or self._lifelines or [])+[{"kind":b.get("kind"),"source_id":b.get("id"),"local_xy":b["position"][:2],"name":b.get("id")} for b in self.world.get("bases",[])+self.world.get("shelters",[]) if b.get("kind") in ("hospital","shelter")]:
            xy=item.get("local_xy") or (item.get("position") or [None,None])[:2]
            if not xy or xy[0] is None:continue
            lifelines.append({"id":item.get("source_id") or item.get("id"),"kind":item.get("kind"),"name":item.get("name"),"distance_m":round(_distance(xy,p[:2]),1),"capacity":item.get("capacity")})
        lifelines=sorted(lifelines,key=lambda l:(l["distance_m"],str(l["id"])))[:5]
        drones=sorted(((round(_distance(d["position"],p)/18,1),d["id"],d) for d in self.drones if d["active"] and d["link"] and d["state"] in ("ready","patrolling")),key=lambda o:o[:2])
        report=dict(incident.get("report") or {})
        report.update({k:v for k,v in {"people":incident.get("reported_people"),"callers":incident.get("callers"),"vulnerable":incident.get("vulnerable"),
                       "injuries":incident.get("injuries"),"hazards":incident.get("hazards_reported")}.items() if v not in (None,[])})
        report.setdefault("people",incident["people"])
        vulnerable=incident.get("vulnerable") or []
        injuries=incident.get("injuries") or []
        return {"incident_id":incident["id"],"category":incident.get("disruptionops_category") or incident["category"],"sim_category":incident["category"],
            "t_s":round(t,2),"onset_s":round(incident["created_at"],2),"text":incident.get("report_text") or incident["title"],
            "position":[round(float(v),2) for v in p],"severity":incident["severity"],"status":incident["status"],
            "people":incident["people"],"vulnerable":len(vulnerable) if isinstance(vulnerable,list) else vulnerable,"vulnerable_hints":vulnerable if isinstance(vulnerable,list) else [],
            "injured":len(injuries) if isinstance(injuries,list) else injuries,"injury_hints":injuries if isinstance(injuries,list) else [],
            "crowd_density_per_100m2":round(incident["people"]/max(1.0,area)*100,2),"hazards":hazards,"trend":trend,
            "report":report,"report_confidence":report.get("confidence",0.6),
            "briefing":copy.deepcopy(incident["briefing"]),"briefing_revision":(incident["briefing"] or {}).get("revision"),
            "observations":[e for e in evidence if e.get("source")!="report"][-10:],
            "needs":list(incident["needs"]),"supplied":list(incident["supplied"]),"evacuated":incident["evacuated_people"],
            "lifelines":lifelines,
            "drones_available":[{"id":i,"eta_s":e,"payload":d["payload"],"stock":d["payload_stock"],"battery":round(d["battery"],3)} for e,i,d in drones[:4]],
            "drones_assigned":[d["id"] for d in self.drones if d["task_id"]==incident["id"]]}

    def _observe_evidence(self,incident,drone):
        """A drone observation (with sensor confidence) is new evidence: record it and report the situation."""
        briefing=incident["briefing"] or {}
        incident.setdefault("evidence",[]).append({"source":"drone","drone_id":drone["id"],"t_s":round(self.t,2),"confidence":briefing.get("confidence"),
            "people":incident["people"],"hazards":list(briefing.get("hazards") or []),"briefing_revision":briefing.get("revision")})
        incident["evidence"]=incident["evidence"][-20:]
        self.nodes.swarm.notify(incident["id"],f"briefing r{briefing.get('revision')} from {drone['id']}")

    def _announce(self,incident,reason,intake=True):
        """A new incident reaches the system: the intake desk tells the command centre (what the report said, the standard
        response for that category), and the swarm node makes its first-look situation report."""
        if incident.get("required_hint_only"):incident["plan_owned"]=True
        if intake and incident.get("control_mode")!="orchestrated":
            self.bus.post("intake","command_center","report",f"Incident {incident['id']} reported: {incident['title']} ({incident['people']} people, severity {incident['severity']}).",
                          incident["id"],incident.get("request_id"),{"intake":True,"category":incident.get("disruptionops_category") or incident["category"],"sim_category":incident["category"],
                          "required_hint":list(incident.get("required_hint") or []),"plan_owned":bool(incident.get("plan_owned")),"people":incident["people"],
                          "severity":incident["severity"],"position":[round(float(v),2) for v in incident["position"]],"source":incident.get("source"),
                          "controlled_by":"disruptionops" if incident.get("control_mode")=="orchestrated" else "autonomous"})
        self.nodes.swarm.notify(incident["id"],"first_look" if intake else reason,True)

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
        if kind in ORCHESTRATOR_STATUS and kind not in ("request_accepted","request_declined","dispatch_order") and hasattr(self,"bus") and not (kind in AGENCY_REPORTED and not self.external_ground):
            # Status back to the command centre (the orchestrator) over the bus.
            self.bus.post("swarm","command_center","ack" if kind=="resource_request_fulfilled" else "handoff" if kind=="handoff" else "status",
                          message,incident_id,entry.get("request_id"),{"status":entry["status"],**({"data":data} if data else {})})

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

    def _incident(self, position, category, title, severity, people, needs, required, source="scenario", building_id=None, defer_requests=False):
        # Detection deduplication is spatial + category, with severity escalation.
        for incident in self.incidents:
            if incident["category"] == category and _distance(incident["position"],position)<35 and incident["status"]!="resolved":
                incident["severity"] = max(severity, incident["severity"])
                incident["people"] = max(people,incident["people"])
                self._event("deduplicated", "Repeated observation merged into existing incident.", incident["id"])
                return incident
        incident = {"id":f"I{len(self.incidents)+1:03}","position":list(position),"category":category,"title":title,
                    "severity":severity,"people":people,"needs":list(needs),"status":"reported","drone_id":None,
                    "resource_ids":[],"briefing":None,"handed_over":False,"required_kinds":[],
                    "completed_kinds":[],"supplied":[],"created_at":self.t,"uncertain_delivery":False,
                    "source":source,"wait_reason":None,"control_mode":"autonomous","assigned_by":None,"external_id":None,"assigned_drones":[],"assigned_units":[],"observations":0,"last_observed":None,"supply_requests":[]}
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
        incident.update(required_hint=list(required),evidence=[],plan=None,plan_owned=False,situation=None)
        # The swarm never asks for ground units. The intake desk tells the command centre what was reported and the
        # swarm node reports the situation; the orchestrator (or DisruptionOps) decides (see _announce).
        if not defer_requests and not getattr(self,"_booting",False):
            self._announce(incident,"new incident")
        return incident

    def _on_bus_message(self,message):
        """World-side record of the orders on the bus (read-only observer; the nodes act only through messages)."""
        kind,body=message["kind"],message.get("body") or {}
        if kind=="dispatch" and not body.get("executed"):
            incident=self._find_incident(message.get("incident_id"))
            if incident is None or any(r["request_id"]==body.get("dispatch_id") for r in incident["resource_requests"]):return
            order={"request_id":body["dispatch_id"],"incident_id":incident["id"],"needed_kind":body["kind"],"qty":int(body.get("qty") or 1),
                   "urgency":body.get("urgency"),"position":[round(float(v),2) for v in (body.get("position") or incident["position"])],
                   "rationale":body.get("rationale",""),"stage":body.get("stage","plan"),"briefing_revision":body.get("briefing_revision"),
                   "raised_at":round(self.t,2),"status":"open","fulfilled_by":[],"ordered_by":message["from"],"ordered_to":message["to"]}
            incident["resource_requests"].append(order)
            if order["needed_kind"] not in incident["required_kinds"]:incident["required_kinds"].append(order["needed_kind"])
            self._ensure_ground_kinds([order["needed_kind"]])
            self._event("dispatch_order",f"{message['from']} dispatches {order['qty']}x {order['needed_kind']} to {incident['id']} via {message['to']} ({order['rationale']}).",incident["id"],
                        data={k:order[k] for k in ("request_id","needed_kind","qty","urgency","position","rationale","briefing_revision")})
            self._signal("dispatch","incident",incident["id"],[message["to"]],incident["position"],incident["id"],f"Dispatch {order['qty']}x {order['needed_kind']}: {order['rationale']}.")
        elif kind=="cancel" and body.get("dispatch_id"):
            incident=self._find_incident(message.get("incident_id"))
            for order in (incident or {}).get("resource_requests",[]):
                if order["request_id"]==body["dispatch_id"] and order["status"]=="open":
                    order["qty"]=max(len(order["fulfilled_by"]),order["qty"]-int(body.get("qty") or 0))
                    if order["qty"]<=len(order["fulfilled_by"]):
                        order["status"]="cancelled" if not order["fulfilled_by"] else "fulfilled";order["cancelled_at"]=round(self.t,2)
                    if order["needed_kind"] in incident["required_kinds"] and order["status"]=="cancelled" and order["needed_kind"] not in incident["completed_kinds"] \
                       and not any(r["needed_kind"]==order["needed_kind"] and r["status"]!="cancelled" for r in incident["resource_requests"]) \
                       and not any(r.get("incident_id")==incident["id"] and _serves(r,order["needed_kind"]) for r in self.resources):
                        incident["required_kinds"].remove(order["needed_kind"])

    def _fulfil_request(self,incident,kind,unit_id,request_id=None):
        request=next((r for r in incident.get("resource_requests",[]) if r["status"]=="open" and ((r["request_id"]==request_id) if request_id else r["needed_kind"]==kind)),None)
        if request is None:return
        request["fulfilled_by"].append(unit_id)
        if len(request["fulfilled_by"])>=request["qty"]:
            request["status"]="fulfilled";request["fulfilled_at"]=round(self.t,2)
        self._event("resource_request_fulfilled",f"{unit_id} assigned to {request['request_id']} ({kind}).",incident["id"],data={"request_id":request["request_id"],"unit_id":unit_id})

    def _create_scenario(self, scenario):
        builder=SCENARIO_REGISTRY[scenario]["builder"]
        if builder is not None:
            # Plug-in scenario: flood closures first (roads depend on them), then the builder.
            self._apply_flood_roads()
            self._station_nodes=None
            builder(self)
            if self.docks:self._assign_drones_to_docks()
            return
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

    def _flood_zones(self):
        return [z for z in self.world.get("flood_zones",[]) if isinstance(z,dict) and "center" in z]

    def _submerged(self,position):
        if not self.flood["enabled"]:return False
        zones=self._flood_zones()
        if zones:
            # Localised flooding (region worlds): only inside the named flood zones.
            return any((position[0]-z["center"][0])**2+(position[1]-z["center"][1])**2<float(z.get("radius",300))**2 for z in zones)
        projection=self._river_projection(position)
        return bool(projection and projection[0]<self.flood["width"]/2)

    def _elevated_crossing(self,position):
        if self.world.get("river_crossings"):
            # Real bridges: the deck stays usable over flood water.
            return any(_distance(position[:2],point)<60 for point in self._safe_crossings)
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
        crossings=[c.get("position") for c in self.world.get("river_crossings",[]) if isinstance(c,dict) and c.get("position")]
        if crossings:
            self._safe_crossings=[list(p[:2]) for p in crossings]
        elif len(river)>1:
            self._safe_crossings=[river[max(0,len(river)//3)],river[min(len(river)-1,2*len(river)//3)]]
        self.flood["safe_crossings"]=copy.deepcopy(self._safe_crossings)
        zones=self._flood_zones()
        if zones:
            self.flood["zones"]=copy.deepcopy(zones);self.flood["model"]="localised flood zones; prescribed depth, not hydrodynamics"
            # Only edges near a zone can be submerged.
            candidates={}
            for z in zones:
                for edge in self._edges_near(z["center"][0],z["center"][1],float(z.get("radius",300))):candidates[edge]=None
            edges=list(candidates)
        else:
            edges=self._road_edges if len(river)>1 else ()
        for a,b in edges:
            count=max(2,int(_distance(a,b)/12))
            submerged=any(self._submerged(p) and not self._elevated_crossing(p) for p in ([a[0]+(b[0]-a[0])*i/count,a[1]+(b[1]-a[1])*i/count] for i in range(count+1)))
            if submerged:
                self._blocked_edges.add(tuple(sorted((a,b))))
                self.road_conditions.append({"points":[list(a),list(b)],"blocked":True,"reason":"flood"})
        self._ground_cache={};self._connector_cache={}
        self._event("flood_routes",f"{len(self.road_conditions)} road segments closed by synthetic flood; designated elevated crossings retained.")

    def _water_path(self,start,end):
        origin=self._river_projection(start);target=self._river_projection(end)
        if not origin or not target:return None
        corridor=self.flood["width"]/2+1
        # A flood zone that reaches the river is navigable from the river to the incident.
        if origin[0]>corridor and not self._submerged(start):return None
        if target[0]>corridor and not self._submerged(end):return None
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
            if incident["category"]!="flood" and not self._submerged(incident["position"]):return None
            return self._water_path(resource["position"],incident["position"])
        # Rooftop boat access is deliberately different from road access.
        if self._submerged(incident["position"]) and not self._elevated_crossing(incident["position"]):
            # Localised flood zones (region worlds): a road unit meets the boats at the nearest dry
            # road point at the water's edge (shore handover). District worlds keep road units out.
            if not self._flood_zones():return None
            edge=self._flood_edge(incident["position"])
            return None if edge is None else self._ground_path(resource["position"],edge)
        return self._ground_path(resource["position"],incident["position"])

    def _flood_edge(self,position):
        """Nearest dry, base-connected road node within 900 m of a submerged position (cached)."""
        cache=self.__dict__.setdefault("_flood_edge_cache",{})
        key=(round(float(position[0]),1),round(float(position[1]),1),len(self._blocked_edges))
        if key not in cache:
            best=None
            for radius in (150.0,400.0,900.0):
                nodes=sorted({q for edge in self._edges_near(position[0],position[1],radius) for q in edge
                              if not self._submerged(q) and (edge not in self._blocked_edges and (edge[1],edge[0]) not in self._blocked_edges)},
                             key=lambda q:(_distance(q,position[:2]),q))
                best=next(([q[0],q[1],0.0] for q in nodes[:12] if self._ground_path(self._base,[q[0],q[1],0.0]) is not None),None)
                if best:break
            cache[key]=best
        return cache[key]

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
        if (self.world.get("road_network") or {}).get("prebuilt"):
            # Prebuilt network (OSM shared nodes): polyline end points are the junctions and
            # consecutive points are edges. No intersection splitting, so flyovers stay unjoined.
            self._road_graph={};self._road_edges=[];seen=set()
            for road in self.world.get("roads",[]):
                if road.get("blocked"):continue
                points=[tuple(float(v) for v in p[:2]) for p in road.get("points",[])]
                for x,y in zip(points,points[1:]):
                    key=(x,y) if x<y else (y,x)
                    if x==y or key in seen:continue
                    seen.add(key);length=_distance(x,y)
                    self._road_graph.setdefault(x,{})[y]=length;self._road_graph.setdefault(y,{})[x]=length
                    self._road_edges.append((x,y))
            self._blocked_edges=set();self._ground_cache={};self._connector_cache={}
            return
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

    EDGE_CELL=100.0

    def _edge_grid(self):
        """Spatial hash of road edges (100 m cells), built once per world."""
        index=self.__dict__.get("_edge_index")
        if index is None:
            index={};s=self.EDGE_CELL
            for number,edge in enumerate(self._road_edges):
                (ax,ay),(bx,by)=edge
                for ix in range(int(math.floor(min(ax,bx)/s)),int(math.floor(max(ax,bx)/s))+1):
                    for iy in range(int(math.floor(min(ay,by)/s)),int(math.floor(max(ay,by)/s))+1):
                        index.setdefault((ix,iy),[]).append(number)
            self._edge_index=index
        return index

    def _edges_near(self,x,y,radius):
        """Road edges whose bounding cells come within radius of (x,y), in road-edge-list order."""
        index=self._edge_grid();s=self.EDGE_CELL;found=set()
        for ix in range(int(math.floor((x-radius)/s)),int(math.floor((x+radius)/s))+1):
            for iy in range(int(math.floor((y-radius)/s)),int(math.floor((y+radius)/s))+1):
                found.update(index.get((ix,iy),()))
        edges=self._road_edges
        return [edges[n] for n in sorted(found)]

    def _road_connector(self,position,max_distance=150.0):
        """Closest dry, unblocked road point within max_distance (cached per position and closure set)."""
        cache=self.__dict__.setdefault("_connector_cache",{})
        cache_key=(float(position[0]),float(position[1]),len(self._blocked_edges),float(max_distance))
        if cache_key in cache:return cache[cache_key]
        best=None;px,py=float(position[0]),float(position[1])
        # Only edges near the point (spatial hash), visited in edge-list order so ties resolve as before.
        for a,b in self._edges_near(px,py,max_distance):
            dx,dy=b[0]-a[0],b[1]-a[1]
            ratio=max(0,min(1,((px-a[0])*dx+(py-a[1])*dy)/(dx*dx+dy*dy)))
            p=(a[0]+ratio*dx,a[1]+ratio*dy)
            cost=math.sqrt((px-p[0])**2+(py-p[1])**2)
            if cost>max_distance or (best is not None and cost>=best[0]):continue
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
        # A* (straight-line heuristic is admissible: every edge is at least its chord length).
        ex,ey=float(end[0]),float(end[1]);hypot=math.hypot
        queue=[(hypot(source[0]-ex,source[1]-ey),0,0,source)];costs={source:0};previous={};serial=0
        while queue:
            _,_,cost,node=heapq.heappop(queue)
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
                    heapq.heappush(queue,(value+hypot(neighbor[0]-ex,neighbor[1]-ey),serial,value,neighbor))
        return None

    def _flight_planner(self):
        """World-static any-angle flight planner (swarm_ops.planning), built once per world."""
        planner=self.__dict__.get("_planner")
        if planner is None:planner=self._planner=FlightPlanner(self.world)
        return planner

    def _restricted(self,a,b):
        """True if a point (a==b) is inside, or the segment a-b crosses, a no-fly polygon."""
        planner=self._flight_planner()
        if not planner.polygons:return False
        return planner.restricted(a[:2]) if list(a[:2])==list(b[:2]) else planner.blocked(a[:2],b[:2])

    def _drone_path(self,drone,target,standoff=0.0,observe=False):
        """3-D route from the drone's current position (line of sight, else visibility graph round
        no-fly zones), altitude from obstacles along this path; incident targets end at a stand-off
        hover point facing the approach. None when no permitted route exists."""
        waypoints,info=self._flight_planner().route(drone["position"],target,drone["cruise"],drone.get("lane",0.0),standoff,observe)
        return waypoints

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

    def _coordination_mode(self,incident):
        """Effective mode for an incident: orchestrated (DisruptionOps assigns) > per-incident choice > global."""
        if incident is not None and incident.get("control_mode")=="orchestrated":return "orchestrated"
        return (incident or {}).get("coordination_mode") or self.coordination["mode"]

    def _set_coordination(self,payload):
        mode=payload.get("mode")
        if mode not in COORDINATION_MODES:raise ValueError("mode must be central, decentralized or orchestrated")
        params={}
        for key,low,high in (("radio_range_m",50,20000),("packet_loss",0,0.95),("latency_s",0,5),("max_bundle",1,4),("peer_timeout_s",2,120)):
            value=payload.get(key)
            if value is None:continue
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:raise ValueError(f"{key} must be between {low} and {high}")
            params[key]=int(value) if key=="max_bundle" else float(value)
        self._coordination_config.update(params)
        self.decentral.configure(**params)
        by=str(payload.get("by") or "command_center")[:40]
        if payload.get("incident_id") or payload.get("external_id"):
            incident=self._orch_incident(payload)
            if mode=="orchestrated":
                incident["control_mode"]="orchestrated";incident["assigned_by"]=incident.get("assigned_by") or by
            elif incident.get("control_mode")=="orchestrated":incident.update(control_mode="autonomous",pending_assign=None)
            incident["coordination_mode"]=mode
            scope=incident["id"];iid=incident["id"]
        else:
            self.coordination["mode"]=mode;self._coordination_config["mode"]=mode
            scope="global";iid=None
        what={"decentralized":"drones self-organise by consensus","orchestrated":"DisruptionOps assigns drones and roles"}.get(mode,"swarm coordinator auction")
        self.decisions.record("swarm","set_coordination",f"{scope}: {mode} ({what})",incident_id=iid)
        self.bus.post("command_center","swarm","task",f"set_coordination {scope} -> {mode}",iid,None,{"coordination":mode,**params})
        self._event("coordination",f"Coordination for {scope} set to {mode}.",iid)
        self._allocate()
        return {"coordination":{"mode":self.coordination["mode"],"scope":scope,"incident_mode":mode if iid else None,
                                **{k:self.decentral.config[k] for k in ("radio_range_m","packet_loss","latency_s","max_bundle","peer_timeout_s")}}}

    def _allocate(self):
        for incident in sorted(self.incidents,key=lambda i:(-i["severity"],i["created_at"],i["id"])):
            if incident["status"]=="resolved":continue
            incident["wait_reason"]=None
            if incident.get("control_mode")=="orchestrated":
                self._apply_assignments(incident);continue
            mode=self._coordination_mode(incident)
            if mode=="decentralized":
                # Independent: the command link gives the swarm the mission; the drones derive and allocate the
                # aerial sub-tasks among themselves (swarm_ops.decentral / cbba). No central award here.
                if incident["id"] in self.decentral.missions or incident["handed_over"]:continue
                if self._paused():
                    incident["wait_reason"]=f"Swarm autonomy paused by the command centre ({self.control['reason'] or 'no reason given'})";continue
                self.decentral.issue_mission(incident);continue
            if mode=="orchestrated":
                if not incident["drone_id"]:incident["wait_reason"]="Dependent mode: waiting for DisruptionOps to assign aircraft"
                continue
            if incident["handed_over"] or incident["drone_id"] or incident["uncertain_delivery"]:continue
            if self._paused():
                incident["wait_reason"]=incident["wait_reason"] or f"Swarm autonomy paused by the command centre ({self.control['reason'] or 'no reason given'})"
                continue
            if self.docks and self._launched()>=self._cap_total():
                incident["wait_reason"]=f"Launch cap of {self._cap_total()} reached; next aircraft launches when one is back at its dock"
                continue
            if sum(1 for d in self.drones if d["task_id"]==incident["id"] and d["active"])>=self._cap_per_incident():
                incident["wait_reason"]=f"Per-incident cap of {self._cap_per_incident()} aircraft reached"
                continue
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
            # Each candidate aircraft evaluates the task itself (route, recovery, energy) and bids;
            # the coordinator awards the lowest feasible bid (payload match first) and traces it.
            awarded=self.coordinator.award(incident,candidates,self.agents,supply_only)
            if awarded:
                capability,bid,agent,offer,count=awarded
                drone,route=agent.drone,offer["route"]
                purpose=f"bringing {drone['payload']}" if supply_only else "first assessment"
                was_searching=agent.accept(self,incident,offer,purpose)
                if was_searching:self._event("search_to_confirm",f"{drone['id']} leaves its search sweep (zone kept) to confirm {incident['id']}; winning bid {bid:.1f} of {count}.",incident["id"])
                incident["drone_id"]=drone["id"];incident["status"]="responding"
                if drone.get("dock_id"):self._event("drone_launch",f"{drone['id']} launched from {drone['dock_id']} for {purpose} ({self._launched()}/{self._cap_total()} airborne); ground response continues independently.",incident["id"])
                else:self._event("drone_dispatch",f"{drone['id']} wins feasible local bid; ground response continues independently.",incident["id"])
                self._signal("dispatch","drone",drone["id"],["control"],drone["position"],incident["id"],
                             f"{drone['id']} {'launching from '+drone['dock_id'] if drone.get('dock_id') else 'dispatched'} to {incident['id']} ({purpose}), ETA {travel_eta(drone,route):.0f} s.")
                if capability:self._event("supply_unavailable",f"{drone['id']} provides reconnaissance only; compatible aerial supplies unavailable.",incident["id"])
            elif not incident["drone_id"]:incident["wait_reason"]=incident["wait_reason"] or "No energy-feasible drone route"
        # Idle aircraft search their own zone (docked fleets wait for a launch; nothing new while paused).
        if self.docks or self._paused():return
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
        "fire":("Building fire; occupants on upper floors",5,(3,8),["assessment","first_aid"],["fire_engine","ambulance"]),
        "trapped":("Residents trapped in a damaged building",4,(2,6),["food","first_aid"],["rescue"]),
        "blocked_route":("Road blocked; access route assessment",3,(2,5),["assessment"],["engineering","rescue"]),
        # DisruptionOps categories (incident_category_needs capabilities).
        "person_stranded":("People stranded by flood water",5,(2,6),["food","rescue"],["boat"]),
        "flooded_road":("Road under water; dewatering needed",3,(1,4),["assessment"],["pump"]),
        "waterlogging":("Waterlogged street; dewatering needed",2,(2,6),["assessment"],["pump"]),
        "fallen_tree":("Tree down across the road",2,(1,3),["assessment"],["engineering"]),
        "structural_damage":("Structure collapsed; someone may be trapped",4,(1,4),["assessment","first_aid"],["engineering","rescue"]),
        "evacuation":("Residents need evacuation",4,(10,40),["food"],["bus"]),
        "supply_shortage":("Relief point short of supplies",3,(30,150),["food"],["supply_truck"]),
        "shelter_full":("Shelter full; families need transport to another",3,(15,40),["food"],["bus"]),
        "air_quality":("Smoke and gas; people affected",3,(2,6),["first_aid","medical"],["ambulance"])}
    # Where an operator click / random site goes: a roof (building) or an open road point.
    ROOF_CATEGORIES=("fire","trapped","flood","person_stranded","structural_damage")
    # random_incident without a category draws from these (keeps seeded draws stable).
    CLASSIC_CATEGORIES=("blocked_route","fire","flood","medical","trapped")

    def _category(self,category):
        """Native template first; otherwise a registered alias (e.g. a plug-in's DisruptionOps mapping)."""
        if category in self.INCIDENT_TEMPLATES:return category
        alias=CATEGORY_ALIASES.get(category)
        if alias in self.INCIDENT_TEMPLATES:return alias
        raise ValueError(f"Unknown incident category '{category}'; known: {', '.join(sorted(self.INCIDENT_TEMPLATES))}")

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

    # ---- orchestrated mode: DisruptionOps decides, the swarm executes and reports ----
    def _orch_say(self,incident,text,kind="status",event=None):
        self.bus.post("swarm","command_center",kind,text,incident["id"],incident.get("request_id"),{"external_id":incident.get("external_id")})
        self._event(event or "orchestrated_status",text,incident["id"])

    def _orch_incident(self,payload):
        key=payload.get("incident_id");ext=payload.get("external_id")
        incident=next((i for i in self.incidents if (key and i["id"]==key) or (ext and i.get("external_id")==ext)),None)
        if incident is None:raise ValueError("Unknown incident")
        return incident

    def _queue_assignments(self,incident,assignments):
        assignments=assignments if isinstance(assignments,dict) else {}
        pend=incident.get("pending_assign") or {"drones":[],"resources":[]}
        incident["pending_assign"]=pend
        for spec in assignments.get("drones") or []:
            if isinstance(spec,dict):
                count=1 if spec.get("id") else max(1,min(6,int(spec.get("count",1))))
                pend["drones"].append({"id":spec.get("id"),"count":count,"role":str(spec.get("role","assess"))[:20],"payload":spec.get("payload")})
        for spec in assignments.get("resources") or []:
            if isinstance(spec,dict) and spec.get("id"):pend["resources"].append(dict(spec))
        incident["assigned_by"]=incident.get("assigned_by") or "DisruptionOps"

    def _apply_assignments(self,incident):
        pend=incident.get("pending_assign")
        if not pend or incident["status"]=="resolved" or self._paused():return
        # ground units: catalogue unit -> its own station/route; unknown id -> external unit at the given position
        keep=[]
        for spec in pend["resources"]:
            resource=next((r for r in self.resources if r["id"]==spec["id"]),None)
            if resource is None:
                resource=self.add_resource(str(spec.get("kind") or "rescue"),(self.nearest_road_point(list(spec.get("position") or incident["position"])[:2]) or list(spec.get("position") or incident["position"])[:2]),id=str(spec["id"])[:40],agency=spec.get("agency"),name=spec.get("name"))
                resource["external"]=True
            if resource.get("incident_id")==incident["id"]:continue
            if resource["state"]!="available":
                keep.append(spec);continue
            route=self._resource_path(resource,incident)
            if route is None and resource.get("external"):route=[[*incident["position"][:2],0.0]]  # external unit: straight line
            if route is None:
                keep.append(spec);continue
            eta=spec.get("eta_s") if isinstance(spec.get("eta_s"),(int,float)) else self._route_distance(resource["position"],route)/9
            resource.update(state="enroute",incident_id=incident["id"],route=route,eta=eta,acknowledged=False,arrived_at=None)
            if resource["id"] not in incident["resource_ids"]:incident["resource_ids"].append(resource["id"])
            if resource["id"] not in incident["assigned_units"]:incident["assigned_units"].append(resource["id"])
            self.bus.post("disruptionops",node_for_kind(resource["kind"]),"dispatch",f"DisruptionOps assigns {resource['id']} ({resource['kind']}) to {incident['id']}.",incident["id"],None,
                          {"executed":True,"unit_id":resource["id"],"kind":resource["kind"],"eta_s":round(eta,1)})
            self._orch_say(incident,f"{resource['id']} ({resource['kind']}) dispatched as assigned by DisruptionOps, ETA {eta:.0f} s.",event="ground_dispatch")
        pend["resources"]=keep
        # drones: specific ids, or nearest feasible docked drones for a count
        left=[]
        for spec in pend["drones"]:
            if spec.get("id"):pool=[d for d in self.drones if d["id"]==spec["id"]]
            else:pool=sorted((d for d in self.drones if d["active"] and d["link"]),key=lambda d:(_distance(d["position"],incident["position"]),d["id"]))
            need=spec["count"];launched=0
            for drone in pool:
                if launched>=need:break
                if drone["state"] not in ("ready","patrolling") or drone["task_id"] or not drone["active"] or not drone["link"]:continue
                if self.docks and self._launched()>=self._cap_total():break
                agent=self.agents[drone["id"]]
                offer=agent.bid(self,incident,self._drone_path)
                if not offer.get("ok"):continue
                agent.accept(self,incident,offer,f"assigned by DisruptionOps ({spec['role']})")
                incident["drone_id"]=incident["drone_id"] or drone["id"];incident["status"]="responding"
                if drone["id"] not in incident["assigned_drones"]:incident["assigned_drones"].append(drone["id"])
                self._orch_say(incident,f"{drone['id']} launched as assigned by DisruptionOps ({spec['role']}), ETA {travel_eta(drone,offer['route']):.0f} s.",event="drone_launch")
                launched+=1
            if launched<need:left.append({**spec,"count":need-launched})
        pend["drones"]=left
        if left or pend["resources"]:incident["wait_reason"]="Assigned units not yet dispatchable (cap, energy or not available)"

    def _orch_release(self,incident,reason,drones=None,units=None):
        for did in list(incident["assigned_drones"] if drones is None else drones):
            drone=next((d for d in self.drones if d["id"]==did),None)
            if drone and drone["task_id"]==incident["id"]:self._return(drone,reason)
            if did in incident["assigned_drones"]:incident["assigned_drones"].remove(did)
        if incident["drone_id"] and incident["drone_id"] not in incident["assigned_drones"]:incident["drone_id"]=None
        for rid in list(incident["assigned_units"] if units is None else units):
            resource=next((r for r in self.resources if r["id"]==rid),None)
            if resource and resource.get("incident_id")==incident["id"]:
                resource.update(state="available",incident_id=None,route=[],eta=None,approach_until=None,arrived_at=None)
            for lst in (incident["assigned_units"],incident["resource_ids"]):
                if rid in lst:lst.remove(rid)

    def _orchestrate_incident(self,payload):
        request_id=payload.get("request_id")
        if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError("request_id (1-128 characters) is required")
        if request_id in self._agency_requests:return {**self._agency_requests[request_id],"duplicate":True}
        def reply(accepted,reason,reply_kind,incident=None,units=()):
            r={"request_id":request_id,"accepted":accepted,"reason":reason,"reply_kind":reply_kind,"incident_id":incident["id"] if incident else None,
               "units":list(units),"assigned_drones":list(units),"external_id":payload.get("external_id"),"control_mode":"orchestrated"}
            if reply_kind!="need_info":self._agency_requests[request_id]=r
            self.bus.post("swarm","command_center","accept" if accepted else "decline",f"Orchestrated {request_id}: {reason}.",incident["id"] if incident else None,request_id,{"reply_kind":reply_kind})
            return r
        raw=payload.get("position");raw_category=payload.get("category")
        missing=[k for k,v in (("position",raw),("category",raw_category)) if v in (None,"")]
        if missing:return reply(False,"need "+" and ".join(missing),"need_info")
        if not isinstance(raw,list) or len(raw)<2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in raw):raise ValueError("position must be two or three finite coordinates")
        position=[float(raw[0]),float(raw[1]),0.0];b=self.world.get("bounds",[-600,-600,600,600])
        if not (b[0]<=position[0]<=b[2] and b[1]<=position[1]<=b[3]):return reply(False,"outside the swarm's operating area","decline_jurisdiction")
        if self._paused():return reply(False,"swarm autonomy paused by the command centre","decline_capacity")
        if self._open_incidents()>=MAX_OPEN_INCIDENTS:return reply(False,"swarm at capacity","decline_capacity")
        category=self._category(str(raw_category)[:40])
        title,_,_,needs,_=self.INCIDENT_TEMPLATES[category]
        severity,people=self._validated_numbers({"severity":payload.get("priority",payload.get("severity",3)),"people":payload.get("people",1)})
        incident=self._incident(position,category,str(payload.get("title") or title)[:120],severity,people,needs,[],"orchestrator",defer_requests=True)
        incident.update(request_id=request_id,control_mode="orchestrated",plan_owned=False,required_kinds=[],external_id=payload.get("external_id"),
                        assigned_by=str(payload.get("requested_by") or "DisruptionOps")[:60],
                        situation_report=payload.get("situation") if isinstance(payload.get("situation"),dict) else None)
        assignments=payload.get("assignments") if isinstance(payload.get("assignments"),dict) else {}
        planned=sum(1 if s.get("id") else max(1,min(6,int(s.get("count",1)))) for s in assignments.get("drones") or [] if isinstance(s,dict))
        self._queue_assignments(incident,assignments)
        self._announce(incident,"first look")
        self._orch_say(incident,f"Accepted orchestrated incident {incident['id']} ({category}); the swarm executes DisruptionOps' assignments only.",kind="accept",event="request_accepted")
        self._apply_assignments(incident)
        units=list(incident["assigned_drones"])
        kind="accept_partial" if units and len(units)<planned else "delayed" if planned and not units else "accept_full"
        return reply(True,"accepted; executing assignments",kind,incident,units)

    def _orchestrator_command(self,action,payload):
        if action=="orchestrate_incident":return self._orchestrate_incident(payload)
        incident=self._orch_incident(payload)
        if incident.get("control_mode")!="orchestrated" and action in ("assign","unassign"):
            incident["control_mode"]="orchestrated";incident["assigned_by"]=incident.get("assigned_by") or "DisruptionOps"
        if action=="assign":
            self._queue_assignments(incident,payload);self._apply_assignments(incident)
        elif action=="unassign":
            self._orch_release(incident,"unassigned by DisruptionOps",payload.get("drone_ids") or [],payload.get("resource_ids") or [])
            self._orch_say(incident,"Assignments removed by DisruptionOps.")
        elif action=="update_incident":
            if payload.get("people") is not None:incident["people"]=max(0,min(500,int(payload["people"])))
            if payload.get("severity") is not None:incident["severity"]=max(1,min(5,int(payload["severity"])))
            if isinstance(payload.get("situation"),dict):incident["situation_report"]=payload["situation"]
            self._orch_say(incident,f"Incident updated: {incident['people']} people, severity {incident['severity']}.")
        elif action in ("cancel_incident","close_incident"):
            done=action=="close_incident";reason=str(payload.get("reason") or ("closed" if done else "cancelled"))[:120]
            self._orch_release(incident,reason)
            incident.update(status="resolved",resolved_at=self.t,pending_assign=None,closed_by="DisruptionOps",outcome="completed" if done else "cancelled")
            self._orch_say(incident,f"Incident {'completed' if done else 'cancelled'} by DisruptionOps: {reason}.",event="incident_closed")
        return {"incident_id":incident["id"],"external_id":incident.get("external_id"),"assigned_drones":list(incident["assigned_drones"]),"units":list(incident["assigned_units"]),"status":incident["status"]}

    def _agency_request(self,request,source,building_id=None,snap=True):
        """The swarm is an agency: it decides itself whether to accept a request, then runs its own operation."""
        stats=self._agency_stats;stats["requests_received"]+=1
        category,kind,position=request["category"],request["kind"],list(request["position"])
        self.coordinator.request_received(request,source)
        checks=[]
        b=self.world.get("bounds",[-600,-600,600,600])
        def answer(accepted,reason,incident=None,merged=False,eta=None,drones=(),plan=()):
            stats["accepted" if accepted else "declined"]+=1
            result={"request_id":request["request_id"],"accepted":accepted,"reason":reason,"incident_id":incident["id"] if incident else None,
                    "merged":merged,"eta_s":None if eta is None else round(eta,1),"assigned_drones":list(drones),"plan":list(plan),
                    "position":list(incident["position"]) if incident else position,"source":source}
            result["units"]=list(result["assigned_drones"])
            result["reply_kind"]=("accept_full" if drones or merged else "delayed") if accepted else ("decline_jurisdiction" if "outside" in reason else "decline_capacity")
            self._agency_requests[request["request_id"]]=result
            self.coordinator.request_decided(request,result,checks)
            self._event("request_accepted" if accepted else "request_declined",
                        f"Swarm {'accepted' if accepted else 'declined'} {kind} request {request['request_id']} from {request['requested_by']}: {reason}.",
                        incident["id"] if incident else None,data={k:result[k] for k in ("request_id","accepted","reason","eta_s","assigned_drones","plan")})
            return result
        inside=b[0]<=position[0]<=b[2] and b[1]<=position[1]<=b[3];checks.append(("inside_area",inside))
        if not inside:return answer(False,"outside the swarm's operating area")
        checks.append(("autonomy_running",not self._paused()))
        if self._paused():return answer(False,f"swarm autonomy paused by the command centre ({self.control['reason'] or 'no reason given'})")
        if snap:position,building_id=self._snap_detection(position,category)
        checks.append(("airspace_permitted",not self._in_no_fly(position)))
        if self._in_no_fly(position):return answer(False,"inside a no-fly zone; aerial response not permitted")
        checks.append(("capacity",self._open_incidents()<MAX_OPEN_INCIDENTS))
        if self._open_incidents()>=MAX_OPEN_INCIDENTS:return answer(False,f"swarm at capacity ({MAX_OPEN_INCIDENTS} open incidents)")
        capped=bool(self.docks) and self._launched()>=self._cap_total()
        if capped and kind!="sos":return answer(False,f"launch cap of {self._cap_total()} airborne aircraft reached; retry later")
        feasible=None
        for drone in sorted((d for d in self.drones if d["active"] and d["link"]),key=lambda d:(_distance(d["position"],position),d["id"]))[:6]:
            home=drone.get("home",self._base)
            out=self._drone_path({**drone,"position":list(home)},position);back=self._drone_path({**drone,"position":position},home)
            if out is None or back is None:continue
            if drone["capacity_wh"]*.9>=(self._route_distance(home,out)+self._route_distance(position,back))*0.018+29:feasible=drone;break
        checks.append(("energy_feasible_aircraft",feasible is not None))
        if feasible is None:return answer(False,"no energy-feasible aircraft route to that position")
        title,severity,_,_,required=self.INCIDENT_TEMPLATES[category]
        before=len(self.incidents)
        incident=self._incident(position,category,str(request.get("title") or title)[:120],request["priority"],request["people"],request["needs"],required,source,building_id=building_id,defer_requests=True)
        merged=len(self.incidents)==before
        if not merged:
            incident.update(request_id=request["request_id"],requested_by=request["requested_by"],request_kind=kind)
            # The orchestrator reports facts; the swarm derives the response itself.
            incident.update(request.get("evidence") or {})
            incident.update(plan_owned=True,required_kinds=[],report={"people":request["people"],"needs":list(request["needs"]),"priority":request["priority"],
                            "kind":kind,"source":source,"text":request.get("title") or "","confidence":0.6})
        if merged:self.nodes.swarm.notify(incident["id"],"merged report",True)
        else:self._announce(incident,"orchestrator report")
        self._allocate()
        drone=next((d for d in self.drones if d["id"]==incident["drone_id"]),None)
        eta=travel_eta(drone,drone["route"]) if drone and drone["route"] else None
        plan=[]
        if drone:plan.append(f"launch {drone['id']}"+(f" from {drone['dock_id']}" if drone.get("dock_id") else "")+" to assess and brief")
        elif capped:plan.append(f"queued: next aircraft launches when one of {self._cap_total()} airborne is back at its dock")
        else:plan.append("queued: waiting for an energy-feasible aircraft")
        own=[n for n in incident["needs"] if n in SUPPLY_ITEMS]
        if own:plan.append("drop "+", ".join(own)+" if a stocked aircraft is available")
        plan.append("report the situation (people, injuries, hazards, access, nearby units) to the orchestrator")
        plan.append("hand the scene over to the first ground crew, then return to dock" if self.docks else "hand the scene over to the first ground crew, then resume search")
        reason="merged with an existing open incident" if merged else ("accepted; aircraft assigned" if drone else "accepted; aircraft queued")
        return answer(True,reason,incident,merged,eta,[drone["id"]] if drone else [],plan)

    @staticmethod
    def _report_evidence(raw,category):
        """Caller facts an orchestrator report may carry (sanitised; never trusted as instructions)."""
        raw=raw if isinstance(raw,dict) else {}
        def words(v):return [str(x)[:40] for x in v][:10] if isinstance(v,list) else []
        def count(v):return max(0,min(500,int(v))) if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) else None
        evidence={"disruptionops_category":str(raw.get("disruptionops_category") or category)[:40]}
        if isinstance(raw.get("report_text"),str):evidence["report_text"]=raw["report_text"][:240]
        for key in ("callers","reported_people"):
            if count(raw.get(key)) is not None:evidence[key]=count(raw.get(key))
        if words(raw.get("vulnerable")):evidence["vulnerable"]=words(raw.get("vulnerable"))
        injured=count(raw.get("injured_reported", raw.get("injured")))
        injuries=words(raw.get("injuries")) or (["injured"]*min(injured,20) if injured else [])
        if injuries:evidence["injuries"]=injuries
        if words(raw.get("hazards")):evidence["hazards_reported"]=words(raw.get("hazards"))
        return evidence

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
        if category in self.ROOF_CATEGORIES and self._building_index():
            reach=self._reachable_road_nodes(self._base)
            pool=sorted(self._building_index())
            r.shuffle(pool)
            for building_id,c,pts,height in pool[:400]:
                if not usable(c):continue
                wet=self._submerged(c)
                if category in ("flood","person_stranded"):
                    # Boat case: a roof inside the flood corridor.
                    if not wet:continue
                else:
                    link=self._road_connector(c)
                    if wet or link is None or link[1] not in reach or link[0]>60:continue
                return [round(c[0],2),round(c[1],2),height],building_id
            if category not in ("flood","person_stranded"):return None,None
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
        if building and category in self.ROOF_CATEGORIES:
            return [round(building[1][0],2),round(building[1][1],2),building[3]],building[0]
        if category not in self.ROOF_CATEGORIES or not self._building_index():
            if building:  # a casualty is reported at the street side of the building
                position=[building[1][0],building[1][1],0]
            link=self._road_connector(position)
            if link is not None and link[0]<=80:return [round(link[3][0],2),round(link[3][1],2),0.0],None
        return [position[0],position[1],0.0 if len(position)<3 else position[2]],None

    # ---- goal-directed search: probability map, zones, lawnmower sweeps ----
    def _setup_search(self):
        b=self.world.get("bounds",[-600,-600,600,600])
        # 100 m cells on a district map; coarser on a large region so the map stays <= ~48 x 48 cells.
        extent=max(b[2]-b[0],b[3]-b[1])
        self.search_spacing=SEARCH_SPACING if extent<=4800 else int(math.ceil(extent/48/50)*50)
        cache=self.__dict__.get("_search_prior_cache")
        if cache is None:
            s=self.search_spacing;half=s//2
            xs=list(range(math.ceil((b[0]+half)/s)*s,int(b[2]-half+1),s))
            ys=list(range(math.ceil((b[1]+half)/s)*s,int(b[3]-half+1),s))
            density={}
            for building in self.world.get("buildings",[]):
                fp=building.get("footprint",[])
                if not fp:continue
                cx=sum(q[0] for q in fp)/len(fp);cy=sum(q[1] for q in fp)/len(fp)
                key=(round(cx/self.search_spacing)*self.search_spacing,round(cy/self.search_spacing)*self.search_spacing)
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
        reach=r+30;span=int(reach//self.search_spacing)+1
        gx,gy=round(x/self.search_spacing),round(y/self.search_spacing)
        for ix in range(gx-span,gx+span+1):
            for iy in range(gy-span,gy+span+1):
                cid=self._cell_by_center.get((ix*self.search_spacing,iy*self.search_spacing))
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

    def sweep_lanes(self,cells,spacing,origin):
        """Boustrophedon lanes over a zone: parallel, evenly spaced, along the zone's longest axis, clipped to
        the zone's cells, joined at the nearest ends, starting at the lane end nearest `origin`."""
        half=self.search_spacing/2
        xs=[c["center"][0] for c in cells];ys=[c["center"][1] for c in cells]
        x0,x1,y0,y1=min(xs)-half,max(xs)+half,min(ys)-half,max(ys)+half
        along_x=(x1-x0)>=(y1-y0)
        # work in (u along lane, v across lanes)
        if along_x:cl=[(c["center"][0],c["center"][1]) for c in cells];u0,u1,v0,v1=x0,x1,y0,y1
        else:cl=[(c["center"][1],c["center"][0]) for c in cells];u0,u1,v0,v1=y0,y1,x0,x1
        n=max(1,int(math.ceil((v1-v0)/spacing)));step=(v1-v0)/n
        lanes=[]
        for k in range(n):
            v=v0+step*(k+.5)
            hit=sorted(u for u,w in cl if abs(w-v)<=half)
            if not hit:continue
            seg=[hit[0]-half,hit[0]+half]
            for u in hit[1:]:
                if u-half<=seg[1]+1e-6:seg[1]=u+half
                else:lanes.append((v,seg[0],seg[1]));seg=[u-half,u+half]
            lanes.append((v,seg[0],seg[1]))
        if not lanes:return []
        def xy(u,v):return [u,v] if along_x else [v,u]
        o=origin[:2]
        # start at the nearest end of the first or last lane, then walk the lanes from that side
        ends=[(_distance(xy(lanes[i][1 if e==0 else 2],lanes[i][0]),o),i,e) for i in (0,len(lanes)-1) for e in (0,1)]
        _,i0,e0=min(ends);order=lanes if i0==0 else lanes[::-1]
        pts=[];cur=None
        for v,ua,ub in order:
            a_,b_=xy(ua,v),xy(ub,v)
            first=(e0==0) if cur is None else _distance(cur,a_)<=_distance(cur,b_)
            if cur is None and i0!=0:first=(e0==0)
            seg=[a_,b_] if first else [b_,a_]
            pts+=seg;cur=seg[-1]
        return pts

    def _next_search(self,drone):
        """Sweep the aircraft's own zone with parallel lanes (spacing = 2 x sensor radius x 0.9)."""
        zone=[self.search_cells[c] for c in drone.get("zone",[]) if not self.search_cells[c]["blocked"]]
        if not zone:return False
        probe=self._drone_path(drone,[*zone[0]["center"],0])
        altitude=probe[-1][2] if probe else 60.0
        spacing=max(20.0,2*altitude*math.tan(SENSOR_HALF_ANGLE)*.9)
        lanes=self.sweep_lanes(zone,spacing,drone["position"])
        lanes=[p for p in lanes if not self._restricted(p,p)]
        if not lanes:
            for c in zone:c["blocked"]=True
            self._zone_key=None
            return False
        path=self._drone_path(drone,[*lanes[0],0])
        if path is None:
            for c in zone:c["blocked"]=True
            self._zone_key=None
            return False
        altitude=path[-1][2]
        sweep=[[p[0],p[1],altitude] for p in lanes]
        nearest=min(zone,key=lambda c:(_distance(c["center"],lanes[0]),c["id"]))
        drone.update(state="patrolling",route=path+sweep[1:],sweep=sweep,coverage_task=tuple(nearest["center"]),search_target=nearest["id"])
        return True

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
            "people":incident["people"],"hazards":HAZARDS.get(incident["category"],["restricted access"]),
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
        if incident["briefing"]["source"]=="ground crew" and incident.get("plan_owned") and not incident.get("ground_reported"):
            incident["ground_reported"]=True
            incident["evidence"].append({"source":"ground","t_s":round(self.t,2),"confidence":0.95,"people":incident["people"],"unit":resource["id"]})
            self.nodes.swarm.notify(incident["id"],"ground-crew report",True)
        resource["acknowledged"]=True;resource["state"]="on_scene"
        if _carries_people(resource):
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
                    self.bus.post("swarm",drone["id"],"cancel",f"Ground crew {resource['id']} on scene first; {drone['id']} recalled.",incident["id"],incident.get("request_id"))
                    self.agents[drone["id"]].decide("return",f"recalled: {resource['id']} {why}",incident_id=incident["id"])
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
                    self.agents[drone["id"]].decide("return","energy reserve reached (battery below 23%)",incident_id=drone["task_id"])
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
                # Decide and act: the aircraft's own behaviour tree (swarm_ops.agents.DRONE_TREE).
                self.agents[drone["id"]].tick(self,delta)
            # Decentralized mode: radio delivery + each aircraft's own consensus step (inactive otherwise).
            self.decentral.step(delta)
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
                    transport=_carries_people(resource)
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
            self.traffic.step(delta)
            if self.t-self._last_allocate>=1:
                self._allocate();self._last_allocate=self.t
            self.nodes.pump()
        self._prune_signals()
        self.revision+=1

    def _complete_service(self,resource,incident):
        if resource["kind"]=="fire_engine" and incident["category"]=="fire" and incident["evacuated_people"]<incident["people"]:
            # Fire crews bring occupants down to the street during their (timed) service.
            moved=incident["people"]-incident["evacuated_people"];incident["evacuated_people"]=incident["people"]
            self._event("evacuation",f"{resource['id']} brought {moved} occupants down to safety; {incident['evacuated_people']}/{incident['people']} safe.",incident["id"])
        served=[k for k in incident["required_kinds"] if _serves(resource,k) and k not in incident["completed_kinds"]][:1]
        for kind in served or ([resource["kind"]] if resource["kind"] not in incident["completed_kinds"] else []):
            incident["completed_kinds"].append(kind)
        if all(k in incident["completed_kinds"] for k in incident["required_kinds"]):
            incident["status"]="resolved";incident["resolved_at"]=self.t
            self._event("resolved","Required agency actions completed; incident resolved.",incident["id"])
        resource.update(state="available",incident_id=None,eta=None,acknowledged=False,last_outcome={"incident_id":incident["id"],"t":round(self.t,2)})

    # ---- world port used by the nodes (physics and sensing only; decisions stay in the nodes) ----
    def world_incident_open(self,incident_id):
        incident=self._find_incident(incident_id)
        return incident is not None and incident["status"]!="resolved"

    def world_units(self,node_id):
        """Read-only view of the units one agency node owns."""
        keys=("id","kind","name","agency","state","incident_id","eta","position","capacity","approach_m","transport","acknowledged","last_outcome")
        return [{k:copy.deepcopy(r.get(k)) for k in keys} | {"position":[round(float(v),1) for v in r["position"][:2]]} for r in self.resources
                if node_for_kind(r["kind"])==node_id and not r.get("external") and r.get("agency")!="DisruptionOps"]

    def world_offers(self,node_id,kind,incident_id):
        """Feasible own units (available, traversable route) for one dispatch: the node chooses among them."""
        incident=self._find_incident(incident_id)
        if incident is None or incident["status"]=="resolved":return []
        offers=[]
        for resource in self.resources:
            if resource["state"]!="available" or not _serves(resource,kind) or node_for_kind(resource["kind"])!=node_id or resource.get("external"):continue
            route=self._resource_path(resource,incident)
            if route is None:continue
            # Off-map units add their real remaining distance to the district edge.
            distance=self._route_distance(resource["position"],route)+resource.get("approach_m",0)
            offers.append({"unit_id":resource["id"],"kind":resource["kind"],"exact":resource["kind"]==kind,"distance_m":round(distance,1),
                           "eta_s":round(distance/(7 if resource.get("transport")=="boat" else 9),1)})
        return offers

    def world_note_wait(self,incident_id,text):
        incident=self._find_incident(incident_id)
        if incident is not None and incident["status"]!="resolved":incident["wait_reason"]=text

    def world_send_unit(self,node_id,unit_id,incident_id,kind,dispatch_id=None):
        """Start moving one unit to an incident (the movement itself is simulated in step())."""
        incident=self._find_incident(incident_id)
        resource=next((r for r in self.resources if r["id"]==unit_id),None)
        if incident is None or resource is None or node_for_kind(resource["kind"])!=node_id or resource["state"]!="available":return None
        route=self._resource_path(resource,incident)
        if route is None:return None
        length=self._route_distance(resource["position"],route)+resource.get("approach_m",0)
        resource.update(state="enroute",incident_id=incident["id"],route=route,eta=length/(7 if resource.get("transport")=="boat" else 9),acknowledged=False,arrived_at=None)
        if resource.get("approach_m",0)>0:
            approach_s=resource["approach_m"]/(7 if resource.get("transport")=="boat" else GROUND_SPEED);resource["approach_until"]=self.t+approach_s
            self._event("ground_departure",f"{resource['id']} left {resource.get('station') or 'its station'}, ETA to district {_clock(approach_s)} ({resource['approach_m']/1000:.1f} km).",incident["id"])
        if resource["id"] not in incident["resource_ids"]:incident["resource_ids"].append(resource["id"])
        if self.t>0 and incident["drone_id"]:context=f"; {incident['drone_id']} keeps working the scene meanwhile"
        elif self.t>0 and incident["briefing"]:context="; briefing already on file"
        else:context=""
        freed=" (recently freed)" if resource.get("freed_at") is not None else ""
        why=f"nearest available {kind} of its agency"
        self._event("ground_dispatch",f"{resource['id']} ({kind}){freed} dispatched concurrently via a valid {resource.get('transport','road')} route, ETA {resource['eta']:.0f} s as nearest available {kind}{context}.",incident["id"])
        self._signal("dispatch","resource",resource["id"],["control"],resource["position"],incident["id"],f"{resource.get('name',resource['id'])} responding to {incident['id']}, ETA {resource['eta']:.0f} s.")
        resource.pop("freed_at",None)
        watcher=next((d for d in self.drones if d["task_id"]==incident["id"] and d["state"]=="observing"),None)
        if incident["briefing"] and watcher:
            self._signal("briefing","drone",watcher["id"],[resource["id"]],watcher["position"],incident["id"],
                         f"Brief to {resource.get('name',resource['id'])}: {incident['people']} people, hazards {', '.join(incident['briefing']['hazards'])}.")
        self._fulfil_request(incident,kind,resource["id"],dispatch_id)
        return {"eta_s":round(resource["eta"],1),"position":[round(float(v),1) for v in resource["position"][:2]],"capacity":resource.get("capacity"),"why":why,
                "name":resource.get("name",resource["id"]),"agency":resource.get("agency"),"approach_m":resource.get("approach_m",0),"transport":resource.get("transport")}

    def world_reassign_nearer(self,node_id,unit_id):
        resource=next((r for r in self.resources if r["id"]==unit_id),None)
        incident=self._find_incident(resource.get("incident_id")) if resource else None
        if resource is None or incident is None or node_for_kind(resource["kind"])!=node_id:return None
        self._maybe_reassign_nearer(incident,resource)

    def world_sense_units(self,position,radius,incident_id=None):
        """Ground units an aircraft can see or hear about near a scene (id, kind, agency, distance, ETA, state)."""
        out=[]
        for r in self.resources:
            if r.get("state") in ("unavailable",):continue
            distance=_distance(r["position"][:2],position[:2])+(r.get("approach_m",0) if r.get("off_map") else 0)
            if distance>radius and r.get("incident_id")!=incident_id:continue
            speed=7 if r.get("transport")=="boat" else GROUND_SPEED
            mine=r.get("incident_id")==incident_id
            if r["state"] in ("awaiting_handoff","on_scene"):eta=0.0
            elif mine and r.get("eta") is not None:eta=float(r["eta"])
            else:eta=(distance*1.3+r.get("approach_m",0))/speed
            label={"available":"available","enroute":"en_route" if mine else "en_route_elsewhere","awaiting_handoff":"on_scene","on_scene":"on_scene","evacuating":"transporting","busy":"busy"}.get(r["state"],r["state"])
            if r["state"]=="enroute" and mine:label="en_route"
            elif r["state"] in ("enroute","evacuating") and not mine:label="busy_elsewhere"
            out.append({"id":r["id"],"kind":r["kind"],"agency":r.get("agency"),"distance_m":round(distance,1),"eta_s":round(eta,1),"state":label,"assigned_here":mine})
        return sorted(out,key=lambda u:(u["distance_m"],u["id"]))[:8]

    def world_access(self,position):
        """How the scene can be reached by road, from the road map and the closures."""
        x,y=float(position[0]),float(position[1])
        edges=[tuple(e) for e in self._edges_near(x,y,150.0)]
        is_blocked=lambda e:tuple(sorted(e)) in self._blocked_edges
        wet=lambda e:self._submerged(e[0]) or self._submerged(e[1])
        blocked=sum(1 for e in edges if is_blocked(e))
        open_edges=[e for e in edges if not is_blocked(e) and not wet(e)]
        flooded=sum(1 for e in edges if wet(e) and not is_blocked(e))
        closures=[c for c in self.road_conditions if c.get("points") and min(_distance(pt[:2],(x,y)) for pt in c["points"])<=150.0]
        flooded+=sum(c.get("reason")=="flood" for c in closures)
        blocked+=sum(c.get("reason")!="flood" for c in closures)
        best=None
        if open_edges:
            target=min((q for e in open_edges for q in e),key=lambda q:(_distance(q,(x,y)),q))
            dx,dy=target[0]-x,target[1]-y
            bearing=["east","north-east","north","north-west","west","south-west","south","south-east"][int(((math.degrees(math.atan2(dy,dx))+22.5)%360)//45)]
            best=f"by road from the {bearing}, nearest open road {_distance(target,(x,y)):.0f} m away"
        return {"roads_open":len(open_edges),"roads_blocked":blocked,"roads_flooded":flooded,"best_approach":best}

    def _node_command(self,action,payload):
        """Remote agency nodes (separate processes) attach, order their own units and post their own messages."""
        node=self.nodes.by_id.get(str(payload.get("node")))
        if node is None or node is self.nodes.swarm or node is self.nodes.orchestrator:raise ValueError("node must be an agency node id")
        if action=="node_attach":
            node.remote_until=self.t+max(10.0,min(600.0,float(payload.get("lease_s",120))))
            return {"node":node.id,"remote_until":round(node.remote_until,1),"units":[u["id"] for u in self.world_units(node.id)]}
        if action=="node_detach":
            node.remote_until=-1.0
            return {"node":node.id,"remote_until":None}
        if not node.remote:raise ValueError("node is not attached; send node_attach first")
        node.remote_until=max(node.remote_until,self.t+60.0)
        if action=="node_order":
            result=self.world_send_unit(node.id,payload.get("unit_id"),payload.get("incident_id"),str(payload.get("kind") or ""),payload.get("dispatch_id"))
            if result is None:raise ValueError("unit cannot be sent: unknown, not owned by this node, not available or no route")
            return {"node":node.id,"unit_id":payload["unit_id"],**result}
        kind=payload.get("kind")
        if kind not in ("unit_status","handoff","decline","accept","status","alert"):raise ValueError("node_post kind must be unit_status, handoff, decline, accept, status or alert")
        message=self.bus.post(node.id,str(payload.get("to") or "command_center"),kind,str(payload.get("text") or "")[:240],payload.get("incident_id"),payload.get("request_id"),
                              payload.get("body") if isinstance(payload.get("body"),dict) else {})
        return {"node":node.id,"message_id":message["id"]}

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
            elif action=="control":
                mode=payload.get("orchestrator")
                if mode is not None:
                    if mode not in ("internal","external"):raise ValueError("orchestrator must be internal or external")
                    self.nodes.orchestrator.set_enabled(mode=="internal","DisruptionOps connected: it dispatches" if mode=="external" else "internal stand-in orchestrator on")
                    self.bus.post("command_center","swarm","task",f"control: orchestrator {mode}",body={"orchestrator":mode})
                    if payload.get("autonomy") is None:
                        created={"orchestrator":mode};self.revision+=1
                        return {"ok":True,"action":action,"run_id":self.run_id,"revision":self.revision,**created}
                autonomy=payload.get("autonomy")
                if autonomy not in ("paused","running"):raise ValueError("autonomy must be paused or running")
                reason=str(payload.get("reason") or "")[:160] or None
                previous=self.control["autonomy"]
                self.control.update(autonomy=autonomy,reason=reason,since=round(self.t,2),by=str(payload.get("by") or "command_center")[:40])
                self.decisions.record("swarm","autonomy_"+autonomy,(reason or "command centre instruction")+("" if previous!=autonomy else " (unchanged)"))
                self.bus.post("command_center","swarm","task",f"control: autonomy {autonomy}"+(f" ({reason})" if reason else ""),body={"autonomy":autonomy})
                self.bus.post("swarm","command_center","ack",f"Autonomy {autonomy}; "+("no new launches or requests, aircraft in flight finish or return." if autonomy=="paused" else "normal operation resumed."),body={"autonomy":autonomy})
                self._event("control",f"Command centre set swarm autonomy {autonomy}"+(f": {reason}" if reason else "")+".")
                if autonomy=="running":self._allocate()
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
                self.decisions.record("swarm","fault_response",f"{drone['id']} {kind}: "+{"crash":"release its incident for re-award","battery":"recall it; re-award its incident","link":"keep ownership (delivery uncertain); do not duplicate"}.get(kind,"unknown fault"),incident_id=incident["id"] if incident else None)
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
                category=self._category(payload.get("category","medical"))
                needs=self._validated_needs(payload.get("needs",["first_aid"]))
                severity,people=self._validated_numbers(payload)
                # UI detections are SOS requests through the same agency path as the orchestrator.
                created=self._agency_request({"request_id":self._sim_request_id(),"kind":"sos","position":position,"category":category,
                    "people":people,"needs":needs,"priority":severity,"requested_by":"operator-ui","title":payload.get("title")},"orchestrator-sim")
                if not created["accepted"]:raise ValueError(created["reason"])
            elif action=="random_incident":
                category=payload.get("category")
                if category is not None:
                    try:category=self._category(category)
                    except ValueError:raise ValueError("category must be one of "+", ".join(sorted(self.INCIDENT_TEMPLATES))) from None
                if self._open_incidents()>=MAX_OPEN_INCIDENTS:raise ValueError(f"Too many open incidents (limit {MAX_OPEN_INCIDENTS})")
                # Reproducible: depends only on (seed, counter, category), not on wall time or the sim RNG stream.
                self._random_incidents+=1
                r=random.Random(f"{self.seed}:random_incident:{self._random_incidents}")
                category=category or r.choice(self.CLASSIC_CATEGORIES)
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
                    if raw is None:
                        return {"ok":True,"action":action,"run_id":self.run_id,"revision":self.revision,"request_id":request_id,"accepted":False,"reason":"need position","reply_kind":"need_info","units":[],"assigned_drones":[],"incident_id":None}
                    if not isinstance(raw,list) or len(raw) not in (2,3) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in raw):raise ValueError("position must be two or three finite coordinates")
                    raw_category=str(payload.get("category","medical"))[:40]
                    category=self._category(raw_category)
                    evidence=self._report_evidence(payload.get("situation"),raw_category)
                    default_needs={"assess":["assessment"],"overwatch":["assessment"]}.get(kind,self.INCIDENT_TEMPLATES[category][3])
                    needs=self._validated_needs(payload.get("needs",default_needs))
                    severity,people=self._validated_numbers({"severity":payload.get("priority",3),"people":payload.get("people",1)})
                    created=self._agency_request({"request_id":request_id,"kind":kind,"position":([float(v) for v in raw]+[0.0])[:3],"category":category,
                        "people":people,"needs":needs,"priority":severity,"requested_by":str(payload.get("requested_by","orchestrator"))[:80],
                        "evidence":evidence,"title":evidence.get("report_text")},"orchestrator")
            elif action=="set_coordination":
                created=self._set_coordination(payload)
            elif action in ORCH_COMMANDS:
                created=self._orchestrator_command(action,payload)
            elif action=="recall_drone":
                drone=next((d for d in self.drones if d["id"]==payload.get("drone_id")),None)
                if drone is None:raise ValueError("Unknown drone")
                incident=self._find_incident(drone["task_id"])
                if incident and incident.get("control_mode")=="orchestrated":
                    self._orch_release(incident,str(payload.get("reason") or "recalled by DisruptionOps")[:100],[drone["id"]],[])
                    self._orch_say(incident,f"{drone['id']} recalled by DisruptionOps.")
                elif drone["state"] in ("enroute","observing","patrolling"):self._return(drone,str(payload.get("reason") or "recalled by DisruptionOps")[:100])
                created={"drone_id":drone["id"],"state":drone["state"]}
            elif action=="request_situation":
                incident=self._orch_incident(payload)
                self.nodes.swarm.notify(incident["id"],"need_info",True)
                created={"incident_id":incident["id"],"queued":True}
            elif action in ("node_attach","node_detach","node_order","node_post"):
                created=self._node_command(action,payload)
            elif action=="external_resource_update":
                if payload.get("source")!="disruptionops":raise ValueError("External updates require DisruptionOps source")
                if payload.get("run_id") not in (None,self.run_id):raise ValueError("External update belongs to a different run")
                if not self.external_ground:self.resources=[]
                self.external_ground=True
                self.nodes.orchestrator.set_enabled(False,"DisruptionOps ground state is authoritative (external_resource_update)")
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
            self.nodes.pump()
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
        view["route_kind"]={"patrolling":"sweep","returning":"return","enroute":"mission","observing":"mission"}.get(drone["state"],"none")
        view["sweep"]=[list(p) for p in drone.get("sweep") or []] if drone["state"]=="patrolling" else []
        view["last_decision"]=drone.get("last_decision")
        if drone["task_id"] and drone["state"] in ("enroute","observing"):
            # Hover (stand-off) point of the current incident task.
            view["standoff"]=list(drone["route"][-1]) if drone["state"]=="enroute" and drone["route"] else list(drone["position"])
        view["zone"]=list(drone.get("zone",[]))
        view["coordination"]=self.decentral.drone_view(drone)
        if drone.get("dc_working") and drone.get("subtask") and drone["state"] in ("enroute","observing"):
            task=self.agents[drone["id"]].cbba.tasks.get(drone["subtask"],{})
            view["target"]={"type":"subtask","id":drone["subtask"],"kind":task.get("kind"),"incident_id":task.get("mission_id"),"position":[*task.get("position",[0,0]),0]}
        elif drone["state"]=="patrolling" and drone.get("search_target"):
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
        for incident in self.incidents:
            led=self.nodes.orchestrator.ledger.get(incident["id"])
            if led and led.get("plan"):incident["plan"]=led["plan"]
        return copy.deepcopy({"schema_version":2,"run_id":self.run_id,"revision":self.revision,"t":round(self.t,3),
            "paused":self.paused,"speed":self.speed,"scenario":self.scenario,"anchor":ANCHOR,"flood":self.flood,"road_conditions":self.road_conditions,
            **({"world":self.world} if include_world else {}),
            "drones":[self._drone_view(d) for d in self.drones],
            "resources":[{**r,"model":r.get("model") or ("boat" if r.get("transport")=="boat" else RESOURCE_KINDS.get(r["kind"],{}).get("model",r["kind"]))} for r in self.resources],"incidents":self.incidents,"events":self.events,
            "signals":[s for s in self.signals if self.t-s["t"]<=SIGNAL_WINDOW],
            "docks":[{**dock,"docked":sum(1 for d in self.drones if d.get("dock_id")==dock["id"] and d["state"] in ("ready","charging")),
                      "launched":sum(1 for d in self.drones if d.get("dock_id")==dock["id"] and d["state"] in ("enroute","observing","returning","holding"))} for dock in self.docks],
            "coverage":[{"position":[*c["center"],0],"last_observed":c["last"]} for c in self.search_cells.values()],
            "search_map":[{"id":c["id"],"center":[*c["center"],0],"size_m":self.search_spacing,"probability":round(c["p"],3),"prior":c["prior"],
                           "last_observed":None if c["last"] is None else round(c["last"],2),
                           "status":"blocked" if c["blocked"] else "cleared" if c["p"]<.12 else "searched" if c["last"] is not None else "unsearched"} for c in self.search_cells.values()],
            "people":self._people(),
            "coordination":{"mode":self.coordination["mode"],"per_incident":{i["id"]:self._coordination_mode(i) for i in self.incidents if i["status"]!="resolved"},**self.decentral.snapshot()},
            "subtasks":self.decentral.subtasks_view(),"mesh":self.decentral.mesh.snapshot(),
            "decisions":self.decisions.recent(100),"engine":{"decisions":self.decisions.recent(100)},"messages":self.bus.recent(200),"control":dict(self.control),"nodes":self.nodes.snapshot(),
            "traffic":self.traffic.snapshot() if getattr(self,"traffic",None) else [],
            "metrics":{"fleet":len(self.drones),"active_drones":sum(d["active"] for d in self.drones),
                "open_incidents":len(active),"resolved_incidents":len(self.incidents)-len(active),
                "available_resources":sum(r["state"]=="available" for r in self.resources),
                "handoffs":sum(i["handed_over"] for i in self.incidents),"supply_drops":sum(len(i["supplied"]) for i in self.incidents),
                "faults":self._faults,"launched":self._launched() if self.docks else None,"dispatch_cap":self._cap_total() if self.docks else None,"dispatch_cap_per_incident":self._cap_per_incident() if self.docks else None,"covered_patches":sum(c["last"] is not None for c in self.search_cells.values()),"total_patches":len(self.search_cells),"simulation_only":True,"external_ground":self.external_ground,"agency":self._agency_metrics(),"allocation_mode":{"central":"drone bids, coordinator auction","decentralized":"decentralized CBBA consensus among drones (no central award)","orchestrated":"DisruptionOps assigns drones and roles"}[self.coordination["mode"]]+" + orchestrator dispatch to agency nodes over the message bus"}})


# Plug-in scenarios/worlds (swarm_ops/scenarios/*) register themselves on import. Imported last so
# their `from swarm_ops import engine` sees a fully initialised module.
try:
    import swarm_ops.scenarios  # noqa: F401,E402
except ImportError as _error:  # pragma: no cover - optional package
    if getattr(_error, "name", "") not in ("swarm_ops.scenarios",):
        raise
