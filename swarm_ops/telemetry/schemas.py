"""Dataset schemas (versioned), validation and the data dictionary.

Every record carries the envelope ``v, seq, run_id, sim_t, wall_time, wall_ts`` plus dataset fields.
Bump ``version`` when a field is removed or changes meaning; adding optional fields keeps the version.
"""
from __future__ import annotations

NUM = (int, float)
STR = (str,)
ANY = (str, int, float, bool, list, dict, type(None))

ENVELOPE = {
    'v': ('int', 'Dataset schema version of this record (see manifest.schemas).'),
    'seq': ('int', 'Per-dataset sequence number, 1-based, strictly increasing. Use as the ?after= cursor.'),
    'run_id': ('str', 'Simulation run id (matches engine.run_id and /ops/events run_id).'),
    'sim_t': ('float', 'Simulation seconds at which the recorder observed the record (non-decreasing within a dataset).'),
    'wall_time': ('str', 'UTC ISO-8601 wall clock when the record was observed.'),
    'wall_ts': ('float', 'Same instant as unix epoch seconds.'),
}

# field -> (python types, required, description)
DATASETS: dict[str, dict] = {
    'events': {
        'version': 1, 'id_field': 'id',
        'description': 'Engine events (incident, dispatch, briefing, handoff, resolved, faults...), one per engine event id.',
        'fields': {
            'id': (STR, True, 'Engine event id "<run_id>:<n>" (dedupe key).'),
            'kind': (STR, True, 'Event kind, e.g. incident, drone_dispatch, drone_on_scene, briefing, resolved.'),
            'message': (STR, False, 'Human readable message.'),
            'event_t': (NUM, False, 'Sim time the engine stamped on the event.'),
            'incident_id': (STR, False, 'Incident the event concerns.'),
            'request_id': (STR, False, 'Orchestrator request id when linked.'),
            'status': (STR, False, 'Orchestrator status (accepted, launched, on_scene, ...).'),
            'data': ((dict, type(None)), False, 'Structured event payload (drone_id, unit_id, request_id...).'),
        }},
    'decisions': {
        'version': 1, 'id_field': 'id',
        'description': 'Onboard/central decisions with inputs and factors when the engine provides them; unknown keys land in detail.',
        'fields': {
            'id': (STR, True, 'Decision id (engine id, or generated "<run_id>:dec:<n>").'),
            'agent': (STR, False, 'Deciding agent id (drone, resource or "command").'),
            'kind': (STR, False, 'Decision type (assign, retask, return, hold, supply...).'),
            'summary': (STR, False, 'What was decided / why, one line.'),
            'incident_id': (STR, False, ''), 'drone_id': (STR, False, ''), 'resource_id': (STR, False, ''), 'request_id': (STR, False, ''),
            'source': (STR, False, 'rule | llm | fallback | operator ...'),
            'fallback': ((bool, type(None)), False, 'True when the primary reasoner failed and a rule fallback was used.'),
            'latency_ms': (NUM + (type(None),), False, 'Reasoning latency.'),
            'inputs': (ANY, False, 'Inputs the decision saw.'), 'factors': (ANY, False, 'Scored factors / alternatives.'),
            'decision_t': (NUM + (type(None),), False, 'Sim time the engine stamped.'),
            'detail': (ANY, False, 'Remaining engine fields, verbatim.'),
        }},
    'messages': {
        'version': 1, 'id_field': 'id',
        'description': 'Command-center <-> agent messages.',
        'fields': {
            'id': (STR, True, 'Message id (engine id or generated).'),
            'sender': (STR, False, 'Sender id/role.'), 'recipient': (ANY, False, 'Recipient id/role or list.'),
            'kind': (STR, False, 'Message type/channel.'), 'text': (STR, False, 'Message body.'),
            'incident_id': (STR, False, ''), 'request_id': (STR, False, ''),
            'message_t': (NUM + (type(None),), False, 'Sim time the engine stamped.'),
            'detail': (ANY, False, 'Remaining engine fields, verbatim.'),
        }},
    'signals': {
        'version': 1, 'id_field': 'id',
        'description': 'Communication pulses (SOS, resource requests, relays) shown in the viewer.',
        'fields': {
            'id': (STR, True, 'Signal id "<run_id>:sig:<n>".'), 'kind': (STR, True, 'sos, resource_request, ...'),
            'from_type': (STR, False, ''), 'from_id': (ANY, False, ''), 'to_ids': (ANY, False, ''),
            'position': (ANY, False, '[x, y, z] metres in the local frame.'), 'incident_id': (ANY, False, ''), 'text': (STR, False, ''),
            'signal_t': (NUM, False, 'Sim time of the pulse.'),
        }},
    'incidents': {
        'version': 1, 'id_field': 'id',
        'description': 'Incident lifecycle: one record per stage transition.',
        'fields': {
            'id': (STR, True, '"<incident_id>:<stage>[:<qualifier>]" (dedupe key).'),
            'incident_id': (STR, True, ''),
            'stage': (STR, True, 'created | accepted | drone_on_scene | first_on_scene | briefing | resource_requested | resource_fulfilled | supplied | handoff | resolved'),
            'occurred_t': (NUM + (type(None),), False, 'Sim time the stage actually happened (sim_t is when it was observed).'),
            'category': (STR, False, ''), 'severity': (NUM + (type(None),), False, ''), 'people': (NUM + (type(None),), False, ''),
            'request_id': (ANY, False, 'Orchestrator/resource request id.'), 'drone_id': (ANY, False, ''), 'resource_id': (ANY, False, ''),
            'first_on_scene': (ANY, False, 'drone | ground (on first_on_scene stage).'),
            'time_since_created_s': (NUM + (type(None),), False, 'occurred_t - created_at.'),
            'detail': (ANY, False, 'Stage specific extras.'),
        }},
    'resources': {
        'version': 1, 'id_field': None,
        'description': 'Ground unit lifecycle: one record per state or assignment change (dispatch, ETA, arrival, service, return).',
        'fields': {
            'resource_id': (STR, True, ''), 'kind': (STR, False, 'ambulance, rescue, fire_engine ...'),
            'transition': (STR, True, 'dispatch | state_change | released'),
            'from_state': (ANY, False, ''), 'to_state': (STR, True, 'available, busy, enroute, on_scene, awaiting_handoff, returning...'),
            'incident_id': (ANY, False, ''), 'eta': (NUM + (type(None),), False, 'Engine ETA value (seconds) when known.'),
            'position': (ANY, False, '[x, y, z]'), 'agency': (ANY, False, ''), 'load': (ANY, False, 'People carried.'),
        }},
    'drones': {
        'version': 1, 'id_field': None,
        'description': 'Drone telemetry time series, default 1 Hz of sim time per drone.',
        'fields': {
            'drone_id': (STR, True, ''), 'x': (NUM, True, 'Local metres east.'), 'y': (NUM, True, 'Local metres north.'),
            'alt': (NUM, True, 'Altitude metres.'), 'speed_mps': (NUM, False, ''), 'heading': (NUM + (type(None),), False, 'Degrees/radians as engine reports.'),
            'battery': (NUM, False, '0..1 state of charge.'), 'state': (STR, False, 'Engine state (ready, enroute, observing...).'),
            'activity': (STR, False, 'transit, delivering, scanning, assessing, returning, docked, charging, holding, failed.'),
            'behavior': (STR, False, 'search, confirm, assess, relay, return, charge, idle, hold, failed.'),
            'task_id': (ANY, False, 'Incident id the drone serves.'), 'zone_n': (NUM, False, 'Cells in its search zone.'),
            'link': (ANY, False, 'Communications link up.'), 'active': (ANY, False, ''), 'payload': (ANY, False, ''),
            'payload_stock': (ANY, False, ''), 'dock_id': (ANY, False, ''), 'target': (ANY, False, '{type,id}'),
        }},
    'ground': {
        'version': 1, 'id_field': None,
        'description': 'Ground unit time series (default every 2 s of sim time per unit).',
        'fields': {
            'resource_id': (STR, True, ''), 'kind': (STR, False, ''), 'state': (STR, False, ''),
            'x': (NUM, True, 'Local metres east.'), 'y': (NUM, True, 'Local metres north.'),
            'incident_id': (ANY, False, ''), 'eta': (NUM + (type(None),), False, ''), 'transport': (ANY, False, 'road | boat'),
            'load': (ANY, False, ''), 'agency': (ANY, False, ''),
        }},
    'kpis': {
        'version': 1, 'id_field': None,
        'description': 'KPI snapshot every simulated minute (and at run end).',
        'fields': {
            'minute': (NUM, True, 'Simulated minute index (final record may repeat the last minute with final=true).'),
            'final': (bool, False, 'True on the end-of-run record.'),
            'open_incidents': (NUM, True, ''), 'resolved_incidents': (NUM, True, ''), 'total_incidents': (NUM, False, ''),
            'airborne': (NUM, True, 'Drones above 1 m.'), 'active_drones': (NUM, False, ''),
            'drone_utilisation': (NUM, True, 'Share of active drones not docked/charging (0..1).'),
            'ground_utilisation': (NUM, False, 'Share of ground units not available (0..1).'),
            'coverage_pct': (NUM + (type(None),), False, 'Search patches observed / total.'),
            'mean_time_to_first_on_scene_s': (NUM + (type(None),), False, 'Mean (first drone on scene - created) over incidents with a drone arrival.'),
            'mean_briefing_latency_s': (NUM + (type(None),), False, 'Mean (briefed_at - created_at).'),
            'unmet_needs': (NUM, False, 'Open-incident needs not yet supplied.'),
            'open_resource_requests': (NUM, False, ''), 'drone_first': (NUM, False, ''), 'ground_first': (NUM, False, ''),
            'faults': (NUM, False, ''), 'decisions': (NUM, False, 'Decisions recorded so far.'), 'fallbacks': (NUM, False, 'Fallback decisions so far.'),
            'dropped_records': (NUM, False, 'Recorder queue drops so far (data-quality signal).'),
        }},
}

for _spec in DATASETS.values():  # normalise single types to tuples
    _spec['fields'] = {k: (v[0] if isinstance(v[0], tuple) else (v[0],), v[1], v[2]) for k, v in _spec['fields'].items()}


def validate(dataset: str, record: dict) -> str | None:
    """Return an error string, or None when the record satisfies the dataset schema."""
    spec = DATASETS.get(dataset)
    if spec is None:
        return f'unknown dataset {dataset}'
    for name, (types, required, _doc) in spec['fields'].items():
        if name not in record:
            if required:
                return f'missing required field {name}'
            continue
        value = record[name]
        if isinstance(value, bool) and types is not ANY and bool not in types:
            return f'field {name} has type bool'
        if not isinstance(value, types):
            return f'field {name} has type {type(value).__name__}'
        if isinstance(value, float) and (value != value or value in (float('inf'), float('-inf'))):
            return f'field {name} is not finite'
    return None


def data_dictionary_markdown() -> str:
    lines = []
    for name, spec in DATASETS.items():
        lines += [f'### `{name}` (schema v{spec["version"]})', '', spec['description'], '',
                  '| field | type | required | description |', '|---|---|---|---|']
        for field, (types, required, doc) in spec['fields'].items():
            tname = 'any' if types is ANY else '/'.join(t.__name__.replace('NoneType', 'null') for t in types)
            lines.append(f'| `{field}` | {tname} | {"yes" if required else ""} | {doc} |')
        lines.append('')
    envelope = ['### Envelope (all datasets)', '', '| field | type | description |', '|---|---|---|']
    envelope += [f'| `{k}` | {t} | {d} |' for k, (t, d) in ENVELOPE.items()]
    return '\n'.join(envelope + [''] + lines)
