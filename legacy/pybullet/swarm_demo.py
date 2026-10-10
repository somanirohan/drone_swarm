import time
import numpy as np
import pybullet as p
from gym_pybullet_drones.envs.CtrlAviary import CtrlAviary
from gym_pybullet_drones.control.DSLPIDControl import DSLPIDControl
from gym_pybullet_drones.utils.enums import DroneModel

n = 12
angles = np.arange(n) * 2 * np.pi / n
initial = np.array([[0.65*np.cos(a), 0.65*np.sin(a), 0.8 + 0.35*(i%2)] for i,a in enumerate(angles)])
env = CtrlAviary(num_drones=n, initial_xyzs=initial, gui=True, obstacles=False, user_debug_gui=False, pyb_freq=240, ctrl_freq=48)
client = env.getPyBulletClient()
p.resetDebugVisualizerCamera(cameraDistance=2.1, cameraYaw=35, cameraPitch=-25, cameraTargetPosition=[0,0,0.95], physicsClientId=client)
p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0, physicsClientId=client)
controllers = [DSLPIDControl(drone_model=DroneModel.CF2X) for _ in range(n)]
actions = np.full((n,4), env.HOVER_RPM)
labels = [-1]*n
markers = [[-1,-1] for _ in range(n)]
start = time.time()
try:
    for step in range(48*600):
        obs, *_ = env.step(actions)
        t = step/48
        for i in range(n):
            angle = angles[i] + 0.12*t
            target = [0.65*np.cos(angle), 0.65*np.sin(angle), initial[i,2]]
            actions[i], _, _ = controllers[i].computeControlFromState(control_timestep=env.CTRL_TIMESTEP, state=obs[i], target_pos=target)
            if step%4 == 0:
                pos = obs[i,:3]
                color = [0.1,0.5,1] if i%2 == 0 else [1,0.35,0.05]
                labels[i] = p.addUserDebugText(f'Drone {i+1}', pos+[0,0,0.08], textColorRGB=color, textSize=1.2, replaceItemUniqueId=labels[i], physicsClientId=client)
                for j,offset in enumerate(([0.08,0,0],[0,0.08,0])):
                    markers[i][j] = p.addUserDebugLine(pos-np.array(offset), pos+np.array(offset), lineColorRGB=color, lineWidth=3, replaceItemUniqueId=markers[i][j], physicsClientId=client)
        wait = start+(step+1)/48-time.time()
        if wait > 0:
            time.sleep(wait)
finally:
    env.close()
