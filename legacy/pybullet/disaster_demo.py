"""Independent-agent swarm rescue with repository Crazyflie flight physics."""
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
from agentic_swarm import AgenticSwarm
from city_map import load_city, PLACEMENTS
from ai_planner import AIPlanner
from jev_planner import JevPlanner
REPO_ROOT=Path(__file__).resolve().parents[2]
RESULTS=Path(__file__).resolve().parent/'results'
RESULTS.mkdir(exist_ok=True)

parser = argparse.ArgumentParser()
parser.add_argument('--headless', action='store_true')
parser.add_argument('--duration', type=int, default=600)
parser.add_argument('--overview', action='store_true', help='Start with the whole district visible')
parser.add_argument('--scripted', action='store_true', help='Use the previous central mission controller')
parser.add_argument('--failure-demo', action='store_true', help='Make the first task owner unavailable and demonstrate reassignment')
parser.add_argument('--report-prefix', default='', help='Optional artifact prefix for independent validation runs')
parser.add_argument('--web', action='store_true', help='Run headless in real time and stream to browsers over WebSocket')
parser.add_argument('--port', type=int, default=8765)
parser.add_argument('--host', default='0.0.0.0')
parser.add_argument('--ai', choices=['openai','ollama','jev'], help='Use a real LLM for frontier selection and rescue participation')
parser.add_argument('--ai-model', help='Backend model name, or set SWARM_AI_MODEL')
parser.add_argument('--ai-max-requests', type=int, default=None)
args = parser.parse_args()
if args.ai and args.scripted:
    parser.error('--ai and --scripted cannot be combined')
try:
    if args.ai=='jev':
        ai=JevPlanner(args.ai_model or 'jev-latest',max_requests=args.ai_max_requests or 600)
    else:
        ai=AIPlanner(args.ai,args.ai_model,max_requests=args.ai_max_requests or 120) if args.ai else None
except ValueError as exc:
    parser.error(str(exc))
web=args.web
if web:
    args.headless=True
    import web_stream
else:
    web_stream=None
def say(msg, **kw):
    print(msg,**kw)
    if web:
        web_stream.log(msg)
n = 12
initial = np.array([[-14 + (i%4)*.6, -11.4+(i//4)*.6, .16] for i in range(n)])
map_scale=5.
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

# A full-size district with imported textured ruined building collision meshes.
p.changeVisualShape(env.PLANE_ID,-1,rgbaColor=[.3,.29,.27,1],textureUniqueId=-1,physicsClientId=c)
ground=box([0,0,-.05],[16.25,12.75,.05],[.48,.45,.4,1])
box([0,-1.25,.012],[16,.95,.012],[.16,.17,.18,1])
box([-1.75,0,.013],[.9,12.5,.013],[.16,.17,.18,1])
for x in np.arange(-15.5,16,2):
    box([x,-1.25,.03],[.55,.035,.005],[.79,.73,.51,1],False)
for y in np.arange(-12,12,2):
    box([-1.75,y,.032],[.035,.55,.005],[.79,.73,.51,1],False)
launch_pad=box([-13,-11,.025],[2,1.3,.025],[.24,.27,.24,1])
cruise_height=load_city(c)
text('RESCUE LAUNCH',[-15,-12,.1],[.05,.15,.12],1.)
text('EARTHQUAKE DISTRICT | imported CC0 ruined-city assets',[-14,12.8,2],[.1,.12,.13],1.3)
# Textured ground: the supplied rock photograph texture, preserving its source.
ground_texture=p.loadTexture(str(REPO_ROOT/'assets/legacy/apocalyptic-city/LowPoly-Apocalyptic-Buildings-By-Majadroid/textures/Rocks007_1K_Color.jpg'),physicsClientId=c)
p.changeVisualShape(ground,-1,textureUniqueId=ground_texture,physicsClientId=c)
smoke=[]

survivors = np.array([[-13,1.25,.08],[3.5,2.25,.08],[12,6.5,.08]])
people=[]
for k,pos in enumerate(survivors):
    box(pos+[0,0,.6],[.18,.12,.6],[1,.15,.15,1], False)
    people.append(sphere(pos+[0,0,1.4],.22,[1,.7,.5,1]))
    text(f'HELP {k+1}', pos+[.3,0,1.85], [.8,.05,.05])

if web:
    web_stream.start(args.host,args.port,{
        'survivors':survivors.tolist(),'placements':PLACEMENTS,'launch_pad':[-13,-11],
        'manifest':json.loads((REPO_ROOT/'assets'/'legacy'/'city-ready'/'manifest.json').read_text()),
        'drones':n,'ground':[16.25,12.75],'mode':f'AI ({ai.provider})' if ai else 'scripted' if args.scripted else 'rule agents'})
    print(f'WEB: viewer on http://localhost:{args.port}',flush=True)
p.resetDebugVisualizerCamera(7.4, 25, -48, [0,0,.35], physicsClientId=c)
if not args.headless:
    p.configureDebugVisualizer(p.COV_ENABLE_GUI,0,physicsClientId=c)
controllers=[DSLPIDControl(drone_model=DroneModel.CF2X) for _ in range(n)]
actions=np.full((n,4), env.HOVER_RPM)
agentic=not args.scripted
mission=AgenticSwarm(initial,cruise_height,args.failure_demo,planner=ai) if agentic else SwarmMission(initial,map_scale=map_scale,cruise_height=cruise_height)
labels=[-1]*n
phase_label=-1
payloads={}
min_separation=float('inf')
contact_steps=0
unsafe_contact_steps=0
landing_contact_steps=0
contact_samples=[]
finite=True
follow=not args.overview
camera_position=initial[:4].mean(axis=0)
start=time.time()
report={}
event_index=0
ai_event_index=0
complete_steps=0
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
        if (args.headless and not web) or step%12==0:
            contacts=[(i,hit) for i,body in enumerate(env.DRONE_IDS) for hit in p.getContactPoints(bodyA=int(body),physicsClientId=c)]
            if contacts:
                contact_steps+=1
                landing_only=agentic and all(hit[2] in (env.PLANE_ID,ground,launch_pad) and mission.agents[i].state in ('RETURN','COMPLETE') and np.linalg.norm(positions[i,:2]-initial[i,:2])<.3 and positions[i,2]<.3 for i,hit in contacts)
                if landing_only:
                    landing_contact_steps+=1
                else:
                    unsafe_contact_steps+=1
                if len(contact_samples)<10:
                    i,hit=contacts[0]
                    contact_samples.append({'time':round(t,2),'drone':i+1,'other_body':int(hit[2]),'other_drone':int(np.where(env.DRONE_IDS==hit[2])[0][0])+1 if hit[2] in env.DRONE_IDS else None,'position':positions[i].tolist()})
        # Range-limited ideal sensor with geometric line-of-sight occlusion.
        # The planner only receives survivor coordinates after this observation.
        for k,survivor in enumerate(survivors):
            if k in mission.found or (not agentic and mission.phase != 'SEARCH'):
                continue
            for i,pos in enumerate(positions):
                if agentic and (not mission.agents[i].active or mission.agents[i].state not in ('EXPLORE','RELAY')):
                    continue
                if np.linalg.norm(pos[:2]-survivor[:2]) < 3.6 and pos[2] > 1.1:
                    ray_start=pos+np.array([0,0,-.08])
                    hit=p.rayTest(ray_start,survivor+[0,0,.23],physicsClientId=c)[0]
                    if hit[0] == -1:
                        mission.discover(k,survivor,i if agentic else positions)
                        text(f'LOCATED {k+1}',survivor+[-.25,0,.55],[.05,.55,.1])
                        say(f'SENSOR: D{i+1} located site {k+1}; '+('broadcast to peer agents for auction' if agentic else f'assigned D{mission.assignments[k]+1}'),flush=True)
                        break
        for k,drone in mission.assignments.items():
            pos=positions[drone]
            if k not in mission.released and np.linalg.norm(pos[:2]-mission.found[k][:2])<.12 and .65<pos[2]<.95 and np.linalg.norm(velocities[drone])<.15:
                visual=p.createVisualShape(p.GEOM_BOX,halfExtents=[.025,.025,.02],rgbaColor=[.95,.9,.85,1],physicsClientId=c)
                shape=p.createCollisionShape(p.GEOM_BOX,halfExtents=[.025,.025,.02],physicsClientId=c)
                body=p.createMultiBody(baseMass=.002,baseCollisionShapeIndex=shape,baseVisualShapeIndex=visual,basePosition=pos-[0,0,.1],physicsClientId=c)
                p.resetBaseVelocity(body,linearVelocity=velocities[drone],physicsClientId=c)
                payloads[k]=body
                if agentic:
                    mission.release(k,drone)
                else:
                    mission.released.add(k)
                say(f'PAYLOAD: D{drone+1} released at site {k+1}',flush=True)
        for k,body in payloads.items():
            pos=np.array(p.getBasePositionAndOrientation(body,physicsClientId=c)[0])
            speed=np.linalg.norm(p.getBaseVelocity(body,physicsClientId=c)[0])
            if k not in mission.delivered and np.linalg.norm(pos[:2]-survivors[k,:2])<.3 and pos[2]<.25 and speed<.08:
                if agentic:
                    mission.confirm_delivery(k)
                else:
                    mission.delivered.add(k)
                text('SUPPLY LANDED',survivors[k]+[-.2,-.2,.35],[.05,.5,.1])
                say(f'CONFIRMED: payload landed at site {k+1}',flush=True)
        desired=mission.update(positions,velocities)
        if ai:
            for event in ai.events[ai_event_index:]:
                if event['event']=='ai_tool':
                    say(f"AI: D{event['drone']} -> {event['reason']}")
                else:
                    say(f"AI: D{event['drone']} request failed ({event['type']})")
            ai_event_index=len(ai.events)
        if agentic:
            for event in mission.events[event_index:]:
                if event['event'] in ('assignment','unavailable','obstacle_detour'):
                    say('AGENT: '+json.dumps(event),flush=True)
            event_index=len(mission.events)
        if agentic and step%24==0:
            # Short forward range probes trigger an agent's temporary detour.
            for i,pos in enumerate(positions):
                speed=np.linalg.norm(desired[i])
                if speed>.1 and pos[2]>1.2:
                    direction=desired[i]/speed
                    hit=p.rayTest(pos+direction*.09,pos+direction*.9,physicsClientId=c)[0]
                    if hit[0]!=-1 and hit[0] not in env.DRONE_IDS:
                        mission.obstacle(i,pos,direction)
        for i in range(n):
            # Incremental setpoints and velocity feed-forward command motor RPMs.
            target=positions[i]+desired[i]*.25
            actions[i],_,_=controllers[i].computeControlFromState(control_timestep=env.CTRL_TIMESTEP,state=obs[i],target_pos=target,target_vel=desired[i])
            if step%12==0:
                label=f'D{i+1} {mission.agents[i].state}' if agentic else f'D{i+1}'
                labels[i]=text(label,positions[i]+[0,0,.09],[.2,.5,.8],.8,labels[i])
        if step%12==0:
            phase_pos=camera_position+[-1,0,.8] if follow else [-14,13.5,3]
            phase_label=text(f'{mission.phase} | Detected {len(mission.found)}/3 | Supplies {len(mission.delivered)}/3 | F: camera',phase_pos,[.15,.25,.38],1.2,phase_label)
            for j,body in enumerate(smoke):
                p.resetBasePositionAndOrientation(body,[.7+.12*np.sin(t*.5+j*.4),1.35+.035*j,.7+(j*.13+t*.09)%.95],[0,0,0,1],physicsClientId=c)
        if not args.headless and step%2==0:
            keys=p.getKeyboardEvents(physicsClientId=c)
            if keys.get(ord('f'),0)&p.KEY_WAS_TRIGGERED:
                follow=not follow
            camera_target=positions[:4].mean(axis=0) if follow else np.array([0,0,3.5])
            camera_position += .12*(camera_target-camera_position)
            p.resetDebugVisualizerCamera(2.6 if follow else 34,25,-35 if follow else -48,camera_position,physicsClientId=c)
        if step%480==0 or step==48*args.duration-1:
            report={'located':len(mission.found),'aid_sites':len(mission.delivered),'released':len(mission.released),'finite_states':finite,'drones':n,'phase':mission.phase,'minimum_separation_m':min_separation,'contact_steps':contact_steps,'mission':'range/LOS sensing, shared task allocation, formation/separation feedback, physical falling payloads, RPM flight with drag'}
            if agentic:
                report['mission']='independent rule-based agents, frontier memory, peer auctions, delayed messaging, availability recovery, local detours, PID/RPM physics'
                if ai:
                    report['mission']='LLM frontier and rescue-bid decisions; deterministic auctions, safety and PID flight'
                    (RESULTS/f'{args.report_prefix or "agentic"}-ai-events.json').write_text(json.dumps(ai.events,indent=2))
                report.update(mission.metrics())
                report['contact_samples']=contact_samples
                report['unsafe_contact_steps']=unsafe_contact_steps
                report['landing_contact_steps']=landing_contact_steps
                event_file=f'{args.report_prefix}-events.json' if args.report_prefix else 'agentic-events.json'
                (RESULTS/event_file).write_text(json.dumps(mission.events,indent=2))
            prefix=args.report_prefix or 'disaster'
            (RESULTS/(f'{prefix}-validation.json' if args.headless else f'{prefix}-live-status.json')).write_text(json.dumps(report,indent=2))
            print(f'TELEMETRY t={t:.0f}s phase={mission.phase} altitude={positions[:,2].min():.2f}..{positions[:,2].max():.2f} located={len(mission.found)} landed={len(mission.delivered)}',flush=True)
        if web and step%4==0:
            web_stream.publish({'t':round(t,2),'phase':mission.phase,'found':sorted(int(k) for k in mission.found),'delivered':sorted(int(k) for k in mission.delivered),
                'drones':[[*(round(float(v),3) for v in obs[i,:3]),*(round(float(v),4) for v in obs[i,3:7]),mission.agents[i].state if agentic else 'SCRIPTED',round(mission.agents[i].battery,3) if agentic else 1,int(mission.agents[i].active) if agentic else 1] for i in range(n)],
                'payloads':{str(k):[round(float(v),3) for v in p.getBasePositionAndOrientation(b,physicsClientId=c)[0]] for k,b in payloads.items()}})
            if agentic and mission.phase.startswith('COMPLETE'):
                complete_steps+=4
                if complete_steps>48*12:
                    say('MISSION COMPLETE: restarting scenario')
                    break
        if step==48*72 and not web:
            view=p.computeViewMatrixFromYawPitchRoll([0,0,3.5],34,25,-48,0,2)
            proj=p.computeProjectionMatrixFOV(55,16/9,.1,100)
            pixels=p.getCameraImage(1280,720,view,proj,renderer=p.ER_TINY_RENDERER,physicsClientId=c)[2]
            Image.fromarray(np.asarray(pixels,dtype=np.uint8)).save((RESULTS/f'{args.report_prefix or "disaster"}-scene.png'))
        if not args.headless or web:
            delay=start+(step+1)/48-time.time()
            if delay>0: time.sleep(delay)
finally:
    if ai:
        ai.close()
    if p.isConnected(c):
        env.close()
print(json.dumps(report))
