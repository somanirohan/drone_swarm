"""Turn engine snapshots (plus new decisions/messages) into dataset records.

Pure derivation from what the server already computes each frame; it never calls into the engine, so
engine internals can change as long as the public snapshot shape stays recognisable. Unknown or missing
fields degrade to omitted values rather than errors.
"""
from __future__ import annotations

from .recorder import RunRecorder


def _num(value, default=None):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value else default


def _pick(source: dict, *names, default=None):
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return default


def _clean(value):
    return {k: v for k, v in value.items() if v is not None}


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


class Deriver:
    def __init__(self, drone_period=1.0, ground_period=2.0, kpi_period=60.0):
        self.drone_period, self.ground_period, self.kpi_period = drone_period, ground_period, kpi_period
        self.seen_events: set[str] = set()
        self.last_signal = 0
        self.stages: dict[str, set] = {}
        self.event_times: dict[tuple, float] = {}
        self.resource_prev: dict[str, tuple] = {}
        self.next_drone = self.next_ground = 0.0
        self.next_kpi_minute = 0
        self.decisions = self.fallbacks = 0
        self.dec_seq = self.msg_seq = 0
        self.last_state: dict | None = None

    # -- main entry ------------------------------------------------------------------------------
    def process(self, rec: RunRecorder, state: dict, wall_ts: float, messages=(), decisions=()):
        sim_t = _num(state.get('t'), 0.0)
        self.last_state = state
        self._events(rec, state, sim_t, wall_ts)
        self._signals(rec, state, sim_t, wall_ts)
        self._decisions(rec, decisions, sim_t, wall_ts)
        self._messages(rec, messages, sim_t, wall_ts)
        self._incidents(rec, state, sim_t, wall_ts)
        self._resources(rec, state, sim_t, wall_ts)
        if sim_t >= self.next_drone:
            self.next_drone = sim_t + self.drone_period
            self._drones(rec, state, sim_t, wall_ts)
        if sim_t >= self.next_ground:
            self.next_ground = sim_t + self.ground_period
            self._ground(rec, state, sim_t, wall_ts)
        minute = int(sim_t // self.kpi_period)
        if minute >= self.next_kpi_minute:
            self.next_kpi_minute = minute + 1
            self._kpis(rec, state, sim_t, wall_ts, minute)

    def finish(self, rec: RunRecorder, wall_ts: float):
        state = self.last_state
        if state is not None:
            sim_t = _num(state.get('t'), 0.0)
            self._kpis(rec, state, sim_t, wall_ts, int(sim_t // self.kpi_period), final=True)

    # -- datasets --------------------------------------------------------------------------------
    def _events(self, rec, state, sim_t, wall_ts):
        for event in state.get('events') or ():
            eid = event.get('id')
            if eid is None or str(eid) in self.seen_events:
                continue
            self.seen_events.add(str(eid))
            if len(self.seen_events) > 20000:
                self.seen_events = {str(e.get('id')) for e in state.get('events') or ()}
            kind, incident_id = event.get('kind'), event.get('incident_id')
            event_t = _num(event.get('time'))
            if incident_id and event_t is not None:
                self.event_times.setdefault((incident_id, kind), event_t)
            rec.write('events', _clean({'id': str(eid), 'kind': str(kind or 'unknown'), 'message': event.get('message'), 'event_t': event_t,
                                        'incident_id': incident_id, 'request_id': event.get('request_id'),
                                        'status': event.get('status'), 'data': event.get('data')}), sim_t, wall_ts)
            if kind == 'request_accepted' and incident_id:
                self._stage(rec, incident_id, 'accepted', sim_t, wall_ts, occurred=event_t, request_id=event.get('request_id'))

    def _signals(self, rec, state, sim_t, wall_ts):
        run_id = state.get('run_id')
        for signal in state.get('signals') or ():
            sid = signal.get('id')
            if not isinstance(sid, int) or sid <= self.last_signal:
                continue
            self.last_signal = sid
            rec.write('signals', _clean({'id': f'{run_id}:sig:{sid}', 'kind': str(signal.get('kind') or 'unknown'),
                                         'from_type': signal.get('from_type'), 'from_id': signal.get('from_id'), 'to_ids': signal.get('to_ids'),
                                         'position': signal.get('position'), 'incident_id': signal.get('incident_id'),
                                         'text': signal.get('text'), 'signal_t': _num(signal.get('t'))}), sim_t, wall_ts)

    def _decisions(self, rec, decisions, sim_t, wall_ts):
        known = {'id', 'agent', 'agent_id', 'actor', 'kind', 'decision', 'action', 'type', 'summary', 'reason', 'rationale', 'incident_id',
                 'drone_id', 'resource_id', 'request_id', 'source', 'engine', 'mode', 'fallback', 'used_fallback', 'is_fallback',
                 'latency_ms', 'inputs', 'input', 'factors', 'scores', 't', 'time'}
        for item in decisions:
            if not isinstance(item, dict):
                item = {'summary': str(item)}
            self.dec_seq += 1
            did = _pick(item, 'id')
            fallback = _pick(item, 'fallback', 'used_fallback', 'is_fallback')
            source = _pick(item, 'source', 'engine', 'mode')
            if fallback is None and isinstance(source, str):
                fallback = 'fallback' in source.lower() or None
            self.decisions += 1
            self.fallbacks += bool(fallback)
            summary = _pick(item, 'summary', 'reason', 'rationale')
            rest = {k: v for k, v in item.items() if k not in known}
            rec.write('decisions', _clean({
                'id': str(did) if did is not None else f'{rec.run_id}:dec:{self.dec_seq}',
                'agent': _str(_pick(item, 'agent', 'agent_id', 'actor', 'drone_id')), 'kind': _str(_pick(item, 'kind', 'decision', 'action', 'type')),
                'summary': _str(summary), 'incident_id': _str(item.get('incident_id')), 'drone_id': _str(item.get('drone_id')),
                'resource_id': _str(item.get('resource_id')), 'request_id': _str(item.get('request_id')), 'source': _str(source),
                'fallback': None if fallback is None else bool(fallback), 'latency_ms': _num(item.get('latency_ms')),
                'inputs': _pick(item, 'inputs', 'input'), 'factors': _pick(item, 'factors', 'scores'),
                'decision_t': _num(_pick(item, 't', 'time')), 'detail': rest or None}), sim_t, wall_ts)

    def _messages(self, rec, messages, sim_t, wall_ts):
        known = {'id', 'from', 'sender', 'from_id', 'to', 'recipient', 'to_id', 'to_ids', 'kind', 'type', 'channel', 'text', 'message', 'body',
                 'incident_id', 'request_id', 't', 'time'}
        for item in messages:
            if not isinstance(item, dict):
                item = {'text': str(item)}
            self.msg_seq += 1
            mid = item.get('id')
            rest = {k: v for k, v in item.items() if k not in known}
            rec.write('messages', _clean({
                'id': str(mid) if mid is not None else f'{rec.run_id}:msg:{self.msg_seq}',
                'sender': _str(_pick(item, 'from', 'sender', 'from_id')), 'recipient': _pick(item, 'to', 'recipient', 'to_id', 'to_ids'),
                'kind': _str(_pick(item, 'kind', 'type', 'channel')), 'text': _str(_pick(item, 'text', 'message', 'body')),
                'incident_id': _str(item.get('incident_id')), 'request_id': _str(item.get('request_id')),
                'message_t': _num(_pick(item, 't', 'time')), 'detail': rest or None}), sim_t, wall_ts)

    def _stage(self, rec, incident_id, stage, sim_t, wall_ts, qualifier=None, occurred=None, incident=None, **extra):
        key = f'{incident_id}:{stage}' + (f':{qualifier}' if qualifier is not None else '')
        done = self.stages.setdefault(incident_id, set())
        if key in done:
            return
        done.add(key)
        created = _num((incident or {}).get('created_at'))
        fields = {'id': key, 'incident_id': str(incident_id), 'stage': stage, 'occurred_t': occurred,
                  'time_since_created_s': round(occurred - created, 2) if occurred is not None and created is not None else None}
        if incident:
            fields.update(category=incident.get('category'), severity=_num(incident.get('severity')), people=_num(incident.get('people')))
        fields.update(extra)
        rec.write('incidents', _clean(fields), sim_t, wall_ts)

    def _incidents(self, rec, state, sim_t, wall_ts):
        for inc in state.get('incidents') or ():
            iid = inc.get('id')
            if not iid:
                continue
            st = lambda stage, qualifier=None, occurred=None, **extra: self._stage(rec, iid, stage, sim_t, wall_ts, qualifier, occurred, inc, **extra)  # noqa: E731
            st('created', occurred=_num(inc.get('created_at')), request_id=inc.get('request_id'), detail={'source': inc.get('source'), 'needs': inc.get('needs')})
            if inc.get('request_id'):
                st('accepted', occurred=self.event_times.get((iid, 'request_accepted')), request_id=inc.get('request_id'))
            if inc.get('drone_on_scene_at') is not None:
                st('drone_on_scene', occurred=_num(inc['drone_on_scene_at']), drone_id=_str(inc.get('drone_id')))
            first = inc.get('first_on_scene')
            if first:
                occurred = _num(inc.get('drone_on_scene_at')) if first == 'drone' else self.event_times.get((iid, 'ground_arrival'))
                st('first_on_scene', occurred=occurred, first_on_scene=first)
            if inc.get('briefed_at') is not None:
                briefing = inc.get('briefing') if isinstance(inc.get('briefing'), dict) else {}
                st('briefing', occurred=_num(inc['briefed_at']), detail=_clean({'revision': briefing.get('revision'), 'confidence': briefing.get('confidence')}) or None)
            for request in inc.get('resource_requests') or ():
                rid = request.get('request_id')
                st('resource_requested', rid, _num(request.get('raised_at')), request_id=rid,
                   detail=_clean({'needed_kind': request.get('needed_kind'), 'qty': request.get('qty'), 'urgency': request.get('urgency'), 'stage': request.get('stage')}))
                if request.get('status') == 'fulfilled':
                    st('resource_fulfilled', rid, _num(request.get('fulfilled_at')), request_id=rid, resource_id=_str(','.join(map(str, request.get('fulfilled_by') or ()))))
            for item in inc.get('supplied') or ():
                st('supplied', str(item), self.event_times.get((iid, 'supply')), detail={'item': str(item)})
            if inc.get('handed_over'):
                st('handoff', occurred=self.event_times.get((iid, 'handoff')))
            if inc.get('status') == 'resolved':
                st('resolved', occurred=_num(inc.get('resolved_at')))

    def _resources(self, rec, state, sim_t, wall_ts):
        for res in state.get('resources') or ():
            rid = res.get('id')
            if not rid:
                continue
            now = (res.get('state'), res.get('incident_id'))
            before = self.resource_prev.get(rid)
            if before == now:
                continue
            self.resource_prev[rid] = now
            if before is None:
                transition = 'state_change'
            elif now[1] and now[1] != before[1]:
                transition = 'dispatch'
            elif before[1] and not now[1]:
                transition = 'released'
            else:
                transition = 'state_change'
            position = res.get('position')
            rec.write('resources', _clean({'resource_id': str(rid), 'kind': _str(res.get('kind')), 'transition': transition,
                                           'from_state': before[0] if before else None, 'to_state': str(now[0]), 'incident_id': now[1],
                                           'eta': _num(res.get('eta')), 'position': [round(float(v), 1) for v in position[:3]] if _is_vec(position) else None,
                                           'agency': res.get('agency'), 'load': res.get('load')}), sim_t, wall_ts)

    def _drones(self, rec, state, sim_t, wall_ts):
        for d in state.get('drones') or ():
            pos = d.get('position')
            if not _is_vec(pos):
                continue
            zone = d.get('zone')
            target = d.get('target')
            rec.write('drones', _clean({
                'drone_id': str(d.get('id')), 'x': round(float(pos[0]), 2), 'y': round(float(pos[1]), 2), 'alt': round(float(pos[2]), 2),
                'speed_mps': _num(d.get('speed_mps')), 'heading': _num(d.get('heading')),
                'battery': round(_num(d.get('battery'), 0.0), 4), 'state': d.get('state'), 'activity': d.get('activity'), 'behavior': d.get('behavior'),
                'task_id': d.get('task_id'), 'zone_n': len(zone) if isinstance(zone, (list, tuple)) else 0, 'link': d.get('link'),
                'active': d.get('active'), 'payload': d.get('payload'), 'payload_stock': d.get('payload_stock'), 'dock_id': d.get('dock_id'),
                'target': {'type': target.get('type'), 'id': target.get('id')} if isinstance(target, dict) else None}), sim_t, wall_ts)

    def _ground(self, rec, state, sim_t, wall_ts):
        for r in state.get('resources') or ():
            pos = r.get('position')
            if not _is_vec(pos):
                continue
            rec.write('ground', _clean({'resource_id': str(r.get('id')), 'kind': r.get('kind'), 'state': r.get('state'),
                                        'x': round(float(pos[0]), 2), 'y': round(float(pos[1]), 2), 'incident_id': r.get('incident_id'),
                                        'eta': _num(r.get('eta')), 'transport': r.get('transport'), 'load': r.get('load'), 'agency': r.get('agency')}), sim_t, wall_ts)

    def _kpis(self, rec, state, sim_t, wall_ts, minute, final=False):
        incidents = state.get('incidents') or []
        drones = state.get('drones') or []
        resources = state.get('resources') or []
        metrics = state.get('metrics') or {}
        active = [d for d in drones if d.get('active', True)]
        airborne = sum(1 for d in drones if _is_vec(d.get('position')) and d['position'][2] > 1.0 and d.get('active', True))
        busy_drones = sum(1 for d in active if d.get('state') not in ('ready', 'charging'))
        open_inc = [i for i in incidents if i.get('status') != 'resolved']
        agency = metrics.get('agency') or {}
        total_patches = metrics.get('total_patches')
        covered = metrics.get('covered_patches')
        rec.write('kpis', _clean({
            'minute': minute, 'final': True if final else None, 'open_incidents': len(open_inc), 'resolved_incidents': len(incidents) - len(open_inc),
            'total_incidents': len(incidents), 'airborne': airborne, 'active_drones': len(active),
            'drone_utilisation': round(busy_drones / len(active), 4) if active else 0.0,
            'ground_utilisation': round(sum(1 for r in resources if r.get('state') != 'available') / len(resources), 4) if resources else None,
            'coverage_pct': round(100.0 * covered / total_patches, 2) if isinstance(total_patches, (int, float)) and total_patches else None,
            'mean_time_to_first_on_scene_s': _mean([i['drone_on_scene_at'] - i['created_at'] for i in incidents
                                                    if _num(i.get('drone_on_scene_at')) is not None and _num(i.get('created_at')) is not None]),
            'mean_briefing_latency_s': _mean([i['briefed_at'] - i['created_at'] for i in incidents
                                              if _num(i.get('briefed_at')) is not None and _num(i.get('created_at')) is not None]),
            'unmet_needs': sum(1 for i in open_inc for n in i.get('needs') or () if n not in (i.get('supplied') or ())),
            'open_resource_requests': sum(1 for i in open_inc for r in i.get('resource_requests') or () if r.get('status') == 'open'),
            'drone_first': agency.get('drone_first'), 'ground_first': agency.get('ground_first'), 'faults': metrics.get('faults'),
            'decisions': self.decisions, 'fallbacks': self.fallbacks, 'dropped_records': None}), sim_t, wall_ts)


def _str(value):
    return None if value is None else str(value)


def _is_vec(value):
    return isinstance(value, (list, tuple)) and len(value) >= 3 and all(isinstance(v, (int, float)) for v in value[:3])
