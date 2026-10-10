"""Convert CC0 Majadroid FBX assets to textured, Z-up OBJ meshes."""
import json
from pathlib import Path
import assimp_py
import numpy as np

REPO=Path(__file__).resolve().parents[3]
root=REPO/'assets'/'legacy'/'apocalyptic-city'/'LowPoly-Apocalyptic-Buildings-By-Majadroid'
output=REPO/'assets'/'legacy'/'city-ready'
output.mkdir(parents=True,exist_ok=True)
manifest={}
sources=sorted((root/'fbx files').glob('building-*.fbx'))+list((root/'fbx files').glob('wreckage-*.fbx'))
for source in sources:
    scene=assimp_py.import_file(str(source),assimp_py.Process_Triangulate|assimp_py.Process_PreTransformVertices)
    all_vertices=np.concatenate([np.asarray(m.vertices).reshape(-1,3) for m in scene.meshes])
    all_vertices=all_vertices[:,[0,2,1]]*np.array([1,-1,1])
    lo,hi=all_vertices.min(axis=0),all_vertices.max(axis=0)
    scale=4.1/max((hi-lo)[:2])
    origin=np.array([(lo[0]+hi[0])/2,(lo[1]+hi[1])/2,lo[2]])
    parts=[]
    collision_lines=[]
    vertex_offset=0
    for index,mesh in enumerate(scene.meshes):
        vertices=np.asarray(mesh.vertices).reshape(-1,3)[:,[0,2,1]]*np.array([1,-1,1])
        vertices=(vertices-origin)*scale
        faces=np.asarray(mesh.indices).reshape(-1,3)
        normals=np.asarray(mesh.normals).reshape(-1,3)[:,[0,2,1]]*np.array([1,-1,1])
        uv=np.asarray(mesh.texcoords[0]).reshape(mesh.num_vertices,-1)[:,:2]
        lines=[f'v {x:.6f} {y:.6f} {z:.6f}' for x,y,z in vertices]
        collision_lines.extend(lines)
        collision_lines.extend('f '+' '.join(str(int(i)+1+vertex_offset) for i in face) for face in faces)
        vertex_offset+=len(vertices)
        lines.extend(f'vt {u:.6f} {v:.6f}' for u,v in uv)
        lines.extend(f'vn {x:.6f} {y:.6f} {z:.6f}' for x,y,z in normals)
        lines.extend('f '+' '.join(f'{int(i)+1}/{int(i)+1}/{int(i)+1}' for i in face) for face in faces)
        name=f'{source.stem}-{index}.obj'
        (output/name).write_text('\n'.join(lines))
        material=scene.materials[mesh.material_index]
        texture=material.get('TEXTURE_BASE','').replace('\\','/').split('/')[-1]
        texture_path=root/'textures'/texture
        if not texture_path.is_file():
            raise FileNotFoundError(texture_path)
        parts.append({'mesh':name,'texture':str(texture_path.relative_to(REPO)).replace('\\','/')})
    collision=f'{source.stem}-collision.obj'
    (output/collision).write_text('\n'.join(collision_lines))
    manifest[source.stem]={'parts':parts,'collision':collision,'height':float((hi-lo)[2]*scale)}
(output/'manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps({name:round(asset['height'],2) for name,asset in manifest.items()}))
