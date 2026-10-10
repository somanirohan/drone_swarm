import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'legacy' / 'pybullet'))
import json
import time
import unittest
from unittest.mock import patch
import numpy as np
from jev_planner import JevPlanner
from agentic_swarm import AgenticSwarm


def observation(**overrides):
    obs = {'drone': 1, 'position': [-13., -8., 10.], 'state': 'EXPLORE', 'battery': .9, 'supply': True,
           'previous_goal': {}, 'cells': [{'id': 0, 'xy': [-13, -8]}, {'id': 1, 'xy': [-13, -4]}, {'id': 2, 'xy': [11, 8]}],
           'claims': {'2': 1}, 'tasks': {'0': {'point': [-13, 1.25, .08], 'status': 'open'}},
           'owners': {}, 'peers': {}}
    obs.update(overrides)
    return obs


class JevPlannerTests(unittest.TestCase):
    def test_build_excludes_claimed_cells_and_asks_typed_questions(self):
        body = JevPlanner.build(observation())
        q = body['questions']
        self.assertEqual(q['frontier']['type'], 'choice')
        self.assertEqual(list(q['frontier']['criteria']), ['cell_0', 'cell_2'])  # cell 1 is claimed, nearest first
        self.assertEqual(q['return_home']['type'], 'noul')
        self.assertEqual(q['bid_0']['type'], 'noul')
        self.assertNotIn('survivors', body['state'])

    def test_build_skips_bids_without_supply_and_caps_options(self):
        body = JevPlanner.build(observation(supply=False))
        self.assertNotIn('bid_0', body['questions'])
        cells = [{'id': i, 'xy': [i, 0]} for i in range(100)]
        self.assertEqual(len(JevPlanner.build(observation(cells=cells, claims={}))['questions']['frontier']['criteria']), 64)

    def test_parse_maps_answers_to_mission_fields(self):
        answers = {'frontier': {'type': 'choice', 'choice': 'cell_2', 'probabilities': {'cell_0': .1, 'cell_2': .9}, 'confidence': .8},
                   'return_home': {'type': 'noul', 'noul': .1}, 'bid_0': {'type': 'noul', 'noul': .8}}
        action = JevPlanner.parse(answers, observation())
        self.assertEqual((action['frontier'], action['bid_tasks'], action['return_home']), (2, [0], False))

    def test_return_home_needs_high_probability_and_low_battery(self):
        answers = {'return_home': {'type': 'noul', 'noul': .95}}
        self.assertFalse(JevPlanner.parse(answers, observation(battery=.9))['return_home'])
        self.assertTrue(JevPlanner.parse(answers, observation(battery=.3))['return_home'])
        self.assertFalse(JevPlanner.parse({'return_home': {'type': 'noul', 'noul': .6}}, observation(battery=.3))['return_home'])

    def test_wire_format_and_auth_header(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({'answers': {'frontier': {'type': 'choice', 'choice': 'cell_0', 'probabilities': {'cell_0': 1}},
                                               'return_home': {'type': 'noul', 'noul': 0}}}).encode()
        planner = JevPlanner(transport=lambda o: {})
        planner.key = 'test-only'
        try:
            with patch('urllib.request.urlopen', return_value=Response()) as call:
                self.assertEqual(planner.request(observation())['frontier'], 0)
                req = call.call_args.args[0]
                self.assertEqual(req.full_url, 'https://api.typesafe.ai/v1/systemone')
                self.assertEqual(req.get_header('Authorization'), 'Bearer test-only')
                self.assertEqual(json.loads(req.data)['model'], 'jev-latest')
        finally:
            planner.close()

    def test_peer_picks_are_reserved_so_drones_do_not_herd(self):
        sent = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({'answers': {'frontier': {'type': 'choice', 'choice': 'cell_0', 'probabilities': {'cell_0': 1}},
                                                           'return_home': {'type': 'noul', 'noul': 0}}}).encode()
        planner = JevPlanner(transport=lambda o: {})
        planner.key = 'test-only'
        try:
            with patch('urllib.request.urlopen', side_effect=lambda req, timeout: sent.append(json.loads(req.data)) or Response()):
                planner.request(observation(drone=1, claims={}))
                planner.request(observation(drone=2, claims={}))
            self.assertIn('cell_0', sent[0]['questions']['frontier']['criteria'])
            self.assertNotIn('cell_0', sent[1]['questions']['frontier']['criteria'])
        finally:
            planner.close()

    def test_jev_decision_drives_agent_through_existing_validation(self):
        planner = JevPlanner(transport=lambda obs: {'frontier': 39, 'bid_tasks': [], 'return_home': False, 'reason': 'Jev: test'})
        try:
            agent = AgenticSwarm(np.array([[0., 0., .16]]), 10, planner=planner).agents[0]
            agent.decide(np.array([0., 0., 10.]), np.zeros(3), 0)
            deadline = time.monotonic() + 2
            while not planner.pending[0].done() and time.monotonic() < deadline:
                time.sleep(.005)
            agent.decide(np.array([0., 0., 10.]), np.zeros(3), .5)
            self.assertEqual(agent.frontier, 39)
            self.assertEqual(planner.metrics()['ai_provider'], 'jev')
        finally:
            planner.close()


if __name__ == '__main__': unittest.main()
