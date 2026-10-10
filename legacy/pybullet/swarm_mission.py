"""Feedback-driven sector search and rescue with local formation control."""
import numpy as np


class SwarmMission:
    def __init__(self, initial, map_scale=1., cruise_height=1.55):
        self.initial = initial.copy()
        self.map_scale=map_scale
        self.cruise_height=cruise_height
        self.altitude_step=.35*map_scale
        self.offsets = np.array([[-.28,-.28,0],[.28,-.28,0],[-.28,.28,0],[.28,.28,0]])*map_scale
        self.centers = np.array([-2.25, 0., 2.25])*map_scale
        self.legs = np.zeros(3, dtype=int)
        self.found = {}
        self.assignments = {}
        self.released = set()
        self.delivered = set()
        self.phase = 'TAKEOFF'
        self.approached=set()
        self.landing_groups=set()

    def update(self, positions, velocities):
        if self.phase == 'TAKEOFF' and np.all(positions[:,2] > self.cruise_height-.2):
            self.phase = 'SEARCH'
        if len(self.delivered) == 3:
            self.phase = 'RETURN'
        goals = np.empty_like(positions)
        for group in range(3):
            ids = np.arange(group*4,group*4+4)
            center = positions[ids].mean(axis=0)
            if self.phase == 'TAKEOFF':
                goals[ids] = self.initial[ids]
                goals[ids,2] = self.cruise_height + self.altitude_step*group
            elif self.phase in ('RETURN','COMPLETE'):
                goals[ids] = self.initial[ids]
                goals[ids,2] = self.cruise_height + self.altitude_step*group
                if np.max(np.linalg.norm(positions[ids,:2]-self.initial[ids,:2],axis=1)) < .12:
                    self.landing_groups.add(group)
                if group in self.landing_groups:
                    goals[ids,2] = .14
            else:
                # Search lanes advance only when the actual group reaches its goal.
                lane = self.legs[group] % 4
                anchor = np.array([self.centers[group]+(-.35 if lane>=2 else .35)*self.map_scale,
                                   (1.85 if lane%2 == 0 else -1.4)*self.map_scale, self.cruise_height + self.altitude_step*group])
                if np.linalg.norm(center-anchor) < .22:
                    self.legs[group] += 1
                goals[ids] = anchor + self.offsets
                for survivor, drone in self.assignments.items():
                    if drone not in ids or survivor in self.delivered:
                        continue
                    destination = self.found[survivor].copy()
                    destination[2] = self.cruise_height + self.altitude_step*group
                    goals[ids] = destination + self.offsets
                    goals[drone] = destination
                    if np.linalg.norm(positions[drone,:2]-destination[:2]) < .12 and np.linalg.norm(velocities[drone]) < .2:
                        self.approached.add(survivor)
                    if survivor in self.approached:
                        goals[drone,2] = .85
        if self.phase == 'RETURN' and np.all(positions[:,2]<.22):
            self.phase = 'COMPLETE'
        # Climb clear of the district before leaving a low delivery/launch site.
        for i in range(len(positions)):
            if goals[i,2] >= self.cruise_height and positions[i,2] < self.cruise_height-.5:
                goals[i,:2]=positions[i,:2]
        # Formation cohesion and neighbour separation use measured state, not time.
        desired = .8*(goals-positions)
        for i in range(len(positions)):
            group_ids = np.arange(i//4*4,i//4*4+4)
            if self.phase == 'SEARCH' and i not in self.assignments.values():
                desired[i] += .12*(velocities[group_ids].mean(axis=0)-velocities[i])
            for j in range(len(positions)):
                if i == j:
                    continue
                delta = positions[i]-positions[j]
                distance = np.linalg.norm(delta)
                if .001 < distance < .3:
                    desired[i] += .35*(.3-distance)/.3*delta/distance
            speed = np.linalg.norm(desired[i])
            max_speed=min(.45*self.map_scale,.65)
            if speed > max_speed:
                desired[i] *= max_speed/speed
        # Project approaching pairs onto a velocity safety constraint. Both agents
        # share the correction so a neighbour can yield instead of merely fleeing.
        for _ in range(6):
            for i in range(len(positions)):
                for j in range(i):
                    delta = positions[i]-positions[j]+.35*(velocities[i]-velocities[j])
                    distance = np.linalg.norm(delta)
                    if .001 < distance < .65:
                        normal = delta/distance
                        approach = np.dot(desired[i]-desired[j],normal)
                        bound = -1.5*(distance-.23)
                        if approach < bound:
                            correction = .5*(bound-approach)*normal
                            desired[i] += correction
                            desired[j] -= correction
        return desired

    def discover(self, survivor, position, positions):
        if survivor in self.found:
            return
        self.found[survivor] = position.copy()
        available = [i for i in range(len(positions)) if i not in self.assignments.values()]
        drone = min(available,key=lambda i:np.linalg.norm(positions[i]-position))
        self.assignments[survivor] = drone
