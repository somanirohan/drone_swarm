import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'legacy' / 'pybullet'))
import unittest
import numpy as np
from agentic_swarm import AgenticSwarm


class AgentTests(unittest.TestCase):
    def swarm(self, failure=False):
        initial=np.array([[-14+(i%4)*.6,-11.4+(i//4)*.6,.16] for i in range(12)])
        swarm=AgenticSwarm(initial,10.,failure)
        positions=initial.copy()
        positions[:,2]=[a.cruise for a in swarm.agents]
        return swarm,positions,np.zeros_like(positions)

    def advance(self, swarm, positions, velocities, seconds=5):
        for _ in range(int(seconds*48)):
            swarm.update(positions,velocities)

    def test_no_hidden_survivor_coordinates_and_delayed_broadcast(self):
        swarm,pos,vel=self.swarm()
        self.advance(swarm,pos,vel,1)
        self.assertTrue(all(not a.tasks for a in swarm.agents))
        swarm.discover(0,np.array([-13,1,.08]),0)
        self.assertIn(0,swarm.agents[0].tasks)
        self.assertNotIn(0,swarm.agents[1].tasks)
        self.advance(swarm,pos,vel,.3)
        self.assertIn(0,swarm.agents[1].tasks)

    def test_auction_reassigns_unavailable_owner(self):
        swarm,pos,vel=self.swarm(True)
        swarm.discover(0,np.array([-13,1,.08]),0)
        self.advance(swarm,pos,vel,8)
        self.assertIsNotNone(swarm.failed)
        self.assertIn(0,swarm.assignments)
        self.assertNotEqual(swarm.assignments[0],swarm.failed)
        self.assertFalse(swarm.agents[swarm.failed].active)
        self.assertTrue(any(e['event']=='unavailable' for e in swarm.events))
        self.assertEqual(sum(a.task==0 and a.state=='DELIVER' for a in swarm.agents),1)

    def test_memory_separates_frontier_claims(self):
        swarm,pos,vel=self.swarm()
        self.advance(swarm,pos,vel,5)
        frontiers=[a.frontier for a in swarm.agents if a.frontier is not None]
        self.assertGreater(len(set(frontiers)),6)
        agent=swarm.agents[0]
        frontier=agent.frontier
        agent.emit('coverage',frontier,swarm.time)
        self.advance(swarm,pos,vel,1)
        self.assertNotEqual(agent.frontier,frontier)

    def test_obstacle_detour_and_low_battery_return(self):
        swarm,pos,vel=self.swarm()
        self.advance(swarm,pos,vel,1)
        swarm.obstacle(0,pos[0],np.array([1.,0,0]))
        target=swarm.agents[0].goal(pos[0],vel[0],swarm.time)
        self.assertGreater(target[1],pos[0,1])
        self.assertGreater(target[2],pos[0,2])
        swarm.agents[1].battery=.1
        self.advance(swarm,pos,vel,1)
        self.assertEqual(swarm.agents[1].state,'RETURN')


if __name__=='__main__':
    unittest.main()
