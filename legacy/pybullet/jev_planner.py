"""TypeSafe Jev decision planner: typed questions with probabilities instead of a prompted chat model.

Each drone's local observation becomes the Jev `state`; the drone's decisions are three
kinds of typed question (choice: next search sector, noul: bid on a rescue site,
noul: return home). Answers feed the same validation, auctions and PID flight stack as
AIPlanner. There is no silent rule-search fallback if the API is unreachable.
"""
import json
import math
import os
import threading
import time
import urllib.request
from pathlib import Path
from ai_planner import AIPlanner

URL = 'https://api.typesafe.ai/v1/systemone'
MAX_OPTIONS = 64
BID_THRESHOLD = .65
PICK_TTL = 12.  # seconds a peer's freshly chosen sector stays reserved
RETURN_THRESHOLD = .9


def load_env(path=None):
    """Load KEY=VALUE pairs from the project's local env file without overriding real env vars."""
    path = Path(path or Path(__file__).resolve().parents[2] / '.env')
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition('=')
            if sep and key and not key.startswith('#'):
                os.environ.setdefault(key, value.strip().strip('"\''))


class JevPlanner(AIPlanner):
    def __init__(self, model='jev-latest', transport=None, max_requests=600, url=URL):
        load_env()
        super().__init__(provider='jev', model=model, transport=transport or self.request, max_requests=max_requests)
        self.key = os.environ.get('TYPESAFE_API_KEY')
        if not self.key and transport is None:
            raise ValueError('Jev mode requires TYPESAFE_API_KEY (environment or the git-ignored .env file).')
        self.url = url
        self.picks = {}
        # Requests are serialised so each drone sees its peers' latest Jev picks and the swarm spreads out.
        self.lock = threading.Lock()

    @staticmethod
    def build(observation):
        """Translate one drone observation into a Jev request body (pure function)."""
        pos = observation['position']
        claimed_cells = {c for c in observation['claims'].values()}
        coords = {c['id']: c['xy'] for c in observation['cells']}
        # Claims of peers, by coordinates, so Jev can spread the search.
        peer_points = [coords[c] for c in claimed_cells if c in coords]
        options = {}
        for cell in sorted(observation['cells'], key=lambda c: math.dist(pos[:2], c['xy'])):
            if cell['id'] in claimed_cells:
                continue
            x, y = cell['xy']
            crowd = sum(1 for p in peer_points if math.dist(p, cell['xy']) < 6)
            options[f"cell_{cell['id']}"] = f"sector centre x={x:.0f} y={y:.0f}; {math.dist(pos[:2], cell['xy']):.0f} m from this drone; {crowd} peer claims within 6 m"
            if len(options) == MAX_OPTIONS:
                break
        state = {'drone': observation['drone'], 'position_xyz_m': [round(v, 1) for v in pos],
                 'mode': observation['state'], 'battery_0_to_1': round(observation['battery'], 2),
                 'carrying_supply': observation['supply'],
                 'current_search_sector': (observation.get('previous_goal') or {}).get('frontier'),
                 'known_rescue_sites': observation['tasks'], 'site_owner_drone': observation['owners'],
                 'peer_status': observation['peers'], 'peer_search_claims': observation['claims']}
        questions = {}
        if options:
            questions['frontier'] = {'type': 'choice', 'criteria': options,
                'instructions': 'Which unexplored sector should this drone search next? Favour nearby sectors with no peer claims so the swarm spreads out and covers the district quickly.'}
        questions['return_home'] = {'type': 'noul',
            'instructions': 'Should this drone abandon the mission and return to base now? Answer yes only if its battery is critically low or it can no longer help; answer no while it can still search or deliver.'}
        if observation['supply'] and observation['battery'] > .22:
            for key, task in observation['tasks'].items():
                if task['status'] == 'open':
                    questions[f'bid_{key}'] = {'type': 'noul',
                        'instructions': f'Should this drone bid to deliver its supply package to open rescue site {key}? Yes if it is reasonably close, has enough battery, and is carrying supply.'}
        return {'model': 'jev-latest', 'state': json.dumps(state), 'questions': questions}

    @staticmethod
    def parse(answers, observation):
        """Translate Jev's typed answers back into the mission tool fields."""
        notes = []
        frontier = None
        choice = answers.get('frontier')
        if choice:
            frontier = int(choice['choice'].removeprefix('cell_'))
            notes.append(f"sector {frontier} p={choice['probabilities'].get(choice['choice'], 0):.2f}")
        bids = []
        for name, answer in answers.items():
            if name.startswith('bid_') and answer['noul'] >= BID_THRESHOLD:
                bids.append(int(name.removeprefix('bid_')))
                notes.append(f"bid {name[4:]} p={answer['noul']:.2f}")
        home = answers['return_home']['noul']
        # Guard: a single noisy yes must not permanently ground a healthy drone.
        return_home = home >= RETURN_THRESHOLD and observation['battery'] < .5
        if return_home:
            notes.append(f'return p={home:.2f}')
        return {'frontier': frontier, 'bid_tasks': sorted(bids), 'return_home': return_home, 'reason': 'Jev: ' + '; '.join(notes)}

    def request(self, observation):
        with self.lock:
            return self._request(observation)

    def _request(self, observation):
        now = time.monotonic()
        me = observation['drone']
        claims = dict(observation['claims'])
        for drone, (cell, stamp) in list(self.picks.items()):
            if now - stamp > PICK_TTL:
                del self.picks[drone]
            elif drone != me:
                claims[f'jev{drone}'] = cell
        body = self.build({**observation, 'claims': claims})
        req = urllib.request.Request(self.url, json.dumps(body).encode(),
                                     headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=25) as response:
            result = json.load(response)
        action = self.parse(result['answers'], observation)
        if action['frontier'] is not None:
            self.picks[me] = (action['frontier'], time.monotonic())
        return action
