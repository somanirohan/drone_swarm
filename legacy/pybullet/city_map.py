"""Textured earthquake district assembled from prebuilt CC0 ruined-city meshes."""
import json
from pathlib import Path
import pybullet as p

# Repository root (this file lives in legacy/pybullet/).
ROOT=Path(__file__).resolve().parents[2]
READY=ROOT/'assets'/'legacy'/'city-ready'

PLACEMENTS=[('building-01',[-10,6.75,0]),('building-02',[-4.75,7.25,0]),
            ('building-03',[3.75,7,0]),('building-04',[-8,-5,0]),
            ('building-05',[3.75,-7.5,0]),('building-06',[-13,-5.5,0]),
            ('building-07',[11,-6,0]),
            ('wreckage-3-types',[-4,-1.25,0]),
            ('wreckage-3-types',[1,-1.25,0]),
            ('wreckage-3-types',[6,-1.25,0])]

def load_city(client):

    manifest=json.loads((READY/'manifest.json').read_text())
    textures={}
    for name,position in PLACEMENTS:
        asset=manifest[name]
        collision=p.createCollisionShape(p.GEOM_MESH,fileName=str(READY/asset['collision']),flags=p.GEOM_FORCE_CONCAVE_TRIMESH,physicsClientId=client)
        collider=p.createMultiBody(baseMass=0,baseCollisionShapeIndex=collision,basePosition=position,physicsClientId=client)
        # Bullet otherwise draws an automatic untextured copy of the collider.
        p.changeVisualShape(collider,-1,rgbaColor=[0,0,0,0],physicsClientId=client)
        for part in asset['parts']:
            visual=p.createVisualShape(p.GEOM_MESH,fileName=str(READY/part['mesh']),rgbaColor=[1,1,1,1],physicsClientId=client)
            body=p.createMultiBody(baseMass=0,baseVisualShapeIndex=visual,basePosition=position,physicsClientId=client)
            path=str(ROOT/part['texture'])
            if path not in textures:
                textures[path]=p.loadTexture(path,physicsClientId=client)
            p.changeVisualShape(body,-1,textureUniqueId=textures[path],specularColor=[.05,.05,.05],physicsClientId=client)
    return max(asset['height'] for asset in manifest.values())+1.5
