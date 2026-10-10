"""Feedback-driven swarm rescue using repository Crazyflie models and RPM physics."""
import argparse
import json
import time
from pathlib import Path
import numpy as np
import pybullet as p
from PIL import Image
from gym_pybullet_drones.envs.CtrlAviary import CtrlAviary
from gym_pybullet_drones.control.DSLPIDControl import DSLPIDControl
from gym_pybullet_drones.utils.enums import DroneModel, Physics
from swarm_mission import SwarmMission

parser = argparse.ArgumentParser()
parser.add_argument('--headless', action='store_true')
parser.add_argument('--duration', type=int, default=600)
args = parser.parse_args()
n = 12
initial = np.array([[-2.8 + (i%4)*.25, -2.28+(i//4)*.25, .16] for i in range(n)])
# The repository downwash approximation is singular for near-level neighbours.
# Use its rigid-body motor physics and drag model for this coplanar formation.
env = CtrlAviary(num_drones=n, initial_xyzs=initial, gui=not args.headless, physics=Physics.PYB_DRAG, obstacles=False, user_debug_gui=False, pyb_freq=240, ctrl_freq=48)
c = env.getPyBulletClient()
rng = np.random.default_rng(7)
def box(pos, size, color, collision=True, yaw=0):
    visual = p.createVisualShape(p.GEOM_BOX, halfExtents=size, rgbaColor=color, physicsClientId=c)
    shape = p.createCollisionShape(p.GEOM_BOX, halfExtents=size, physicsClientId=c) if collision else -1
    return p.createMultiBody(baseMass=0, baseCollisionShapeIndex=shape, baseVisualShapeIndex=visual, basePosition=pos, baseOrientation=p.getQuaternionFromEuler([0,0,yaw]), physicsClientId=c)
def sphere(pos, radius, color):
    visual = p.createVisualShape(p.GEOM_SPHERE, radius=radius, rgbaColor=color, physicsClientId=c)
    return p.createMultiBody(baseMass=0, baseVisualShapeIndex=visual, basePosition=pos, physicsClientId=c)
def text(msg, pos, color=[.12,.12,.12], size=1.2, replace=-1):
    return p.addUserDebugText(msg, pos, textColorRGB=color, textSize=size, replaceItemUniqueId=replace, physicsClientId=c)

# Land, roads, flooded district and rescue base.
p.changeVisualShape(env.PLANE_ID, -1, rgbaColor=[.65,.68,.58,1], textureUniqueId=-1, physicsClientId=c)
ground = box([0,0,-.025], [3.25,2.55,.04], [.43,.53,.31,1])
box([0,-.25,.015], [3.15,.25,.015], [.25,.27,.28,1])
box([-.35,0,.016], [.22,2.5,.015], [.25,.27,.28,1])
for x in np.arange(-3,3,.45):
    box([x,-.25,.036], [.12,.012,.006], [.95,.85,.2,1], False)
box([2.3,.75,.045], [.8,1.7,.045], [.08,.4,.7,.92], False)
box([-2.3,-1.9,.025], [.65,.5,.025], [.18,.55,.25,1], False)
text('RESCUE BASE', [-2.95,-2.4,.16], [.05,.3,.1])
text('FLOODED DISTRICT', [1.55,2.15,.17], [.05,.25,.65])

# Ruined structures, exposed walls, fallen slabs and scattered debris.
for x,y in [(-2,1.35),(-.95,1.45),(.75,1.4),(-1.6,-1),(.75,-1.5)]:
    box([x,y,.08],[.42,.35,.08],[.49,.43,.36,1])
    box([x-.36,y,.4],[.06,.35,.32],[.66,.56,.42,1])
    box([x,y+.29,.32],[.35,.06,.24],[.59,.5,.39,1])
    box([x+.12,y-.05,.25],[.38,.28,.055],[.37,.38,.39,1], yaw=.5)
    for _ in range(8):
        dx,dy=rng.uniform(-.55,.55,2)
        box([x+dx,y+dy,.09],[.09,.07,.09],[.45,.4,.34,1], yaw=float(rng.uniform(0,3)))
for x in [-.5,-.1,.3]:
    box([x,-.25,.16],[.13,.23,.16],[.45,.39,.32,1], yaw=.3)
text('ROAD BLOCKED', [-.8,-.65,.35], [.65,.1,.05])
# Symbolic fire, hazard boundary and a damaged communications tower.
for j in range(7):
    sphere([.7+.12*np.cos(j),1.35+.12*np.sin(j),.32+.055*j], .12, [1,.25+.05*(j%3),.02,.9])
text('FIRE / KEEP CLEAR', [.2,1.95,.65], [.8,.12,.02])
box([2.65,-1.6,.2],[.035,.035,.65],[.3,.3,.32,1], yaw=.8)
text('COMMS DOWN', [2.2,-2.2,.2], [.5,.15,.1])

# Surface grain, street furniture and broken architecture give the scene scale.
texture_rng = np.random.default_rng(19)
grain = texture_rng.integers(65, 105, (256,256), dtype=np.uint8)
terrain = np.stack([grain, (grain*.94).astype(np.uint8), (grain*.78).astype(np.uint8)],axis=2)
texture_path = Path(__file__).with_name('disaster-ground.png')
Image.fromarray(terrain).save(texture_path)
ground_texture = p.loadTexture(str(texture_path),physicsClientId=c)
p.changeVisualShape(ground,-1,textureUniqueId=ground_texture,physicsClientId=c)
for x,y in [(-2,1.35),(-.95,1.45),(.75,1.4),(-1.6,-1),(.75,-1.5)]:
    # Window openings, exposed floor beams and fractured masonry.
    for z in [.22,.48]:
        for dx in [-.22,.04,.28]:
            box([x+dx,y+.355,z],[.075,.008,.075],[.08,.09,.1,1],False)
    for dx in [-.26,0,.26]:
        box([x+dx,y,.52],[.013,.29,.015],[.24,.22,.19,1],False,yaw=.18)
    for _ in range(20):
        dx,dy= rng.uniform(-.6,.6,2)
        box([x+dx,y+dy,.045],[.035,.025,.035],[.53,.45,.38,1],False,yaw=float(rng.uniform(0,6)))
for y in np.arange(-2.1,2.3,.4):
    box([-.61,y,.045],[.035,.17,.03],[.56,.54,.49,1],False)
for x,y in [(-2.8,1.95),(-2.9,-.9),(1.25,-2.2),(1.3,2.1)]:
    box([x,y,.23],[.035,.035,.23],[.24,.18,.12,1])
    for j in range(3):
        sphere([x+.08*np.cos(j*2),y+.08*np.sin(j*2),.5+j*.04],.18,[.24,.29,.16,1])
# Stranded car and a marked rescue vehicle.
for x,y,color in [(1.95,.7,[.37,.16,.12,1]),(-2.45,-1.55,[.88,.88,.8,1])]:
    box([x,y,.14],[.22,.11,.07],color)
    box([x-.02,y,.24],[.12,.095,.06],color)
    box([x-.02,y-.097,.25],[.085,.005,.035],[.12,.2,.23,1],False)
    for dx in [-.14,.14]:
        for dy in [-.115,.115]:
            sphere([x+dx,y+dy,.095],.055,[.055,.055,.055,1])
    if x<0:
        box([x,y,.31],[.055,.025,.012],[.95,.08,.06,1],False)
# Flood ripples and drifting debris.
for _ in range(35):
    x,y=rng.uniform(1.55,3.05),rng.uniform(-.8,2.3)
    box([x,y,.095],[rng.uniform(.04,.16),.004,.001],[.36,.5,.53,.65],False,yaw=.15)
for _ in range(8):
    box([rng.uniform(1.6,3),rng.uniform(-.7,2.2),.105],[.08,.025,.015],[.32,.24,.16,1],False,yaw=float(rng.uniform(0,6)))
smoke=[sphere([.7,1.35,.7+j*.13],.09+j*.017,[.22,.21,.2,.32]) for j in range(7)]

survivors = np.array([[-2.6,.25,.08],[.7,.45,.08],[2.4,1.3,.15]])
people=[]
for k,pos in enumerate(survivors):
    box(pos+[0,0,.09],[.06,.04,.09],[1,.15,.15,1], False)
    people.append(sphere(pos+[0,0,.23],.06,[1,.7,.5,1]))
    text(f'HELP {k+1}', pos+[.12,0,.35], [.8,.05,.05])

p.resetDebugVisualizerCamera(7.4, 25, -48, [0,0,.35], physicsClientId=c)
if not args.headless:
    p.configureDebugVisualizer(p.COV_ENABLE_GUI,0,physicsClientId=c)
controllers=[DSLPIDControl(drone_model=DroneModel.CF2X) for _ in range(n)]
actions=np.full((n,4), env.HOVER_RPM)
mission=SwarmMission(initial)
labels=[-1]*n
phase_label=-1
payloads={}
min_separation=float('inf')
contact_steps=0
finite=True
follow=True
camera_position=initial[:4].mean(axis=0)
start=time.time()
report={}
try:
    for step in range(48*args.duration):
        if not p.isConnected(c):
            break
        t=step/48
        obs,*_=env.step(actions)
        positions=obs[:,:3]
        velocities=obs[:,10:13]
        finite=finite and bool(np.isfinite(obs).all())
        if not finite:
            raise RuntimeError('Non-finite flight state')
        distances=np.linalg.norm(positions[:,None,:]-positions[None,:,:],axis=2)
        np.fill_diagonal(distances,np.inf)
        min_separation=min(min_separation,float(distances.min()))
        if args.headless or step%12==0:
            if any(p.getContactPoints(bodyA=int(body),physicsClientId=c) for body in env.DRONE_IDS):
                contact_steps+=1
        # Range-limited ideal sensor with geometric line-of-sight occlusion.
        # The planner only receives survivor coordinates after this observation.
        for k,survivor in enumerate(survivors):
            if k in mission.found or mission.phase != 'SEARCH':
                continue
            for i,pos in enumerate(positions):
                if np.linalg.norm(pos[:2]-survivor[:2]) < .72 and pos[2] > 1.1:
                    ray_start=pos+np.array([0,0,-.08])
                    hit=p.rayTest(ray_start,survivor+[0,0,.23],physicsClientId=c)[0]
                    if hit[0] == -1:
                        mission.discover(k,survivor,positions)
                        text(f'LOCATED {k+1}',survivor+[-.25,0,.55],[.05,.55,.1])
                        print(f'SENSOR: D{i+1} located site {k+1}; assigned D{mission.assignments[k]+1}',flush=True)
                        break
        for k,drone in mission.assignments.items():
            pos=positions[drone]
            if k not in mission.released and np.linalg.norm(pos[:2]-mission.found[k][:2])<.12 and .65<pos[2]<.95 and np.linalg.norm(velocities[drone])<.15:
                visual=p.createVisualShape(p.GEOM_BOX,halfExtents=[.025,.025,.02],rgbaColor=[.95,.9,.85,1],physicsClientId=c)
                shape=p.createCollisionShape(p.GEOM_BOX,halfExtents=[.025,.025,.02],physicsClientId=c)
                body=p.createMultiBody(baseMass=.002,baseCollisionShapeIndex=shape,baseVisualShapeIndex=visual,basePosition=pos-[0,0,.1],physicsClientId=c)
                p.resetBaseVelocity(body,linearVelocity=velocities[drone],physicsClientId=c)
                payloads[k]=body
                mission.released.add(k)
                print(f'PAYLOAD: D{drone+1} released at site {k+1}',flush=True)
        for k,body in payloads.items():
            pos=np.array(p.getBasePositionAndOrientation(body,physicsClientId=c)[0])
            speed=np.linalg.norm(p.getBaseVelocity(body,physicsClientId=c)[0])
            if k not in mission.delivered and np.linalg.norm(pos[:2]-survivors[k,:2])<.3 and pos[2]<.25 and speed<.08:
                mission.delivered.add(k)
                text('SUPPLY LANDED',survivors[k]+[-.2,-.2,.35],[.05,.5,.1])
                print(f'CONFIRMED: payload landed at site {k+1}',flush=True)
        desired=mission.update(positions,velocities)
        for i in range(n):
            # Incremental setpoints and velocity feed-forward command motor RPMs.
            target=positions[i]+desired[i]*.25
            actions[i],_,_=controllers[i].computeControlFromState(control_timestep=env.CTRL_TIMESTEP,state=obs[i],target_pos=target,target_vel=desired[i])
            if step%12==0:
                labels[i]=text(f'D{i+1}',positions[i]+[0,0,.09],[.12,.25,.4],.8,labels[i])
        if step%12==0:
            phase_label=text(f'{mission.phase} | Detected {len(mission.found)}/3 | Landed supplies {len(mission.delivered)}/3 | F: follow / overview',[-3.1,2.75,1.4],[.05,.12,.22],1.2,phase_label)
            for j,body in enumerate(smoke):
                p.resetBasePositionAndOrientation(body,[.7+.12*np.sin(t*.5+j*.4),1.35+.035*j,.7+(j*.13+t*.09)%.95],[0,0,0,1],physicsClientId=c)
        if not args.headless and step%2==0:
            keys=p.getKeyboardEvents(physicsClientId=c)
            if keys.get(ord('f'),0)&p.KEY_WAS_TRIGGERED:
                follow=not follow
            camera_target=positions[:4].mean(axis=0) if follow else np.array([0,0,.65])
            camera_position += .12*(camera_target-camera_position)
            p.resetDebugVisualizerCamera(1.8 if follow else 6.6,25,-35 if follow else -48,camera_position,physicsClientId=c)
        if step%480==0 or step==48*args.duration-1:
            report={'located':len(mission.found),'aid_sites':len(mission.delivered),'released':len(mission.released),'finite_states':finite,'drones':n,'phase':mission.phase,'minimum_separation_m':min_separation,'contact_steps':contact_steps,'mission':'range/LOS sensing, shared task allocation, formation/separation feedback, physical falling payloads, RPM flight with drag'}
            (Path(__file__).resolve().parent/'results'/('disaster-validation.json' if args.headless else 'disaster-live-status.json')).write_text(json.dumps(report,indent=2))
            print(f'TELEMETRY t={t:.0f}s phase={mission.phase} altitude={positions[:,2].min():.2f}..{positions[:,2].max():.2f} located={len(mission.found)} landed={len(mission.delivered)}',flush=True)
        if step==48*72:
            view=p.computeViewMatrixFromYawPitchRoll(positions[:4].mean(axis=0),1.8,25,-35,0,2)
            proj=p.computeProjectionMatrixFOV(55,16/9,.1,30)
            pixels=p.getCameraImage(1280,720,view,proj,renderer=p.ER_TINY_RENDERER,physicsClientId=c)[2]
            Image.fromarray(np.asarray(pixels,dtype=np.uint8)).save((Path(__file__).resolve().parent/'results'/'disaster-scene.png'))
        if not args.headless:
            delay=start+(step+1)/48-time.time()
            if delay>0: time.sleep(delay)
finally:
    if p.isConnected(c):
        env.close()
print(json.dumps(report))
