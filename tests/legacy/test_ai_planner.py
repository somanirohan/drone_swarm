import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'legacy' / 'pybullet'))
import json
import time
import unittest
from unittest.mock import patch
import numpy as np
from ai_planner import AIPlanner
from agentic_swarm import AgenticSwarm


class AIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.planner=AIPlanner(model='test-model',transport=lambda obs:{
            'frontier':39,'bid_tasks':[],'return_home':False,'reason':'Search the east edge'})
        self.swarm=AgenticSwarm(np.array([[0.,0.,.16]]),10,planner=self.planner)
        self.agent=self.swarm.agents[0]

    def tearDown(self):
        self.planner.close()

    def test_actual_tool_changes_goal_without_hidden_survivors(self):
        self.agent.decide(np.array([0.,0.,10.]),np.zeros(3),0)
        deadline=time.monotonic()+2
        while not self.planner.pending[0].done() and time.monotonic()<deadline:
            time.sleep(.005)
        self.agent.decide(np.array([0.,0.,10.]),np.zeros(3),.5)
        self.assertEqual(self.agent.frontier,39)
        self.assertEqual(self.planner.accepted,1)
        self.assertTrue(np.allclose(self.agent.goal(np.array([0.,0.,10.]),np.zeros(3),.6)[:2],[13,8]))
        self.assertEqual(self.agent.tasks,{})

    def test_unknown_target_and_invalid_types_rejected(self):
        for action in [dict(frontier=999,bid_tasks=[],return_home=False,reason='bad'),
                       dict(frontier=0,bid_tasks=[999],return_home=False,reason='bad'),
                       dict(frontier=0,bid_tasks=[],return_home='false',reason='bad')]:
            with self.assertRaises(ValueError):
                self.planner.validate(action,self.agent,0)

    def test_stale_frontier_filtered_and_battery_overrides_ai(self):
        self.agent.covered.add(39)
        action=dict(frontier=39,bid_tasks=[],return_home=False,reason='old')
        self.assertIsNone(self.planner.validate(action,self.agent,0)['frontier'])
        self.agent.battery=.1
        self.agent.decide(np.array([0.,0.,10.]),np.zeros(3),0)
        self.assertEqual(self.agent.state,'RETURN')

    def test_openai_wire_format_and_tool_parsing(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self):
                return json.dumps({'output':[{'type':'function_call','name':'set_mission_goal',
                    'arguments':json.dumps(dict(frontier=2,bid_tasks=[],return_home=False,reason='search'))}]}).encode()
        self.planner.key='test-only'
        with patch('urllib.request.urlopen',return_value=Response()) as call:
            self.assertEqual(self.planner.request({'tasks':{}})['frontier'],2)
            body=json.loads(call.call_args.args[0].data)
            self.assertEqual(body['tools'][0]['name'],'set_mission_goal')
            self.assertTrue(body['tools'][0]['strict'])
            self.assertFalse(body['store'])


if __name__=='__main__': unittest.main()
