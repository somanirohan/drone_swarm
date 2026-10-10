import * as THREE from 'three';

import { OrbitControls } from 'three/addons/OrbitControls.js';

const $ = id => document.getElementById(id);

const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const nice = value => String(value ?? 'pending').replace(/_/g,' ');

const clock = t => `${String(Math.floor((t || 0)/60)).padStart(2,'0')}:${String(Math.floor((t || 0)%60)).padStart(2,'0')}`;

/* Sidebar tabs: registered first so the panels work even if the 3D scene cannot start. */
const tabButtons=[...document.querySelectorAll('[data-tab]')];
function setTab(name,focus){if(!tabButtons.some(b=>b.dataset.tab===name))name='mission';for(const b of tabButtons){const on=b.dataset.tab===name;b.classList.toggle('active',on);b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;if(on&&focus)b.focus()}document.querySelectorAll('.tab-panel').forEach(p=>{p.hidden=p.id!=='panel-'+name});try{localStorage.setItem('ops-tab',name)}catch{}}
tabButtons.forEach((b,i)=>{b.addEventListener('click',()=>setTab(b.dataset.tab));b.addEventListener('keydown',e=>{const step={ArrowRight:1,ArrowLeft:-1,ArrowDown:1,ArrowUp:-1}[e.key];let next=null;if(step)next=(i+step+tabButtons.length)%tabButtons.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=tabButtons.length-1;if(next!==null){e.preventDefault();setTab(tabButtons[next].dataset.tab,true)}})});
{let last='mission';try{last=localStorage.getItem('ops-tab')||'mission'}catch{}setTab(last)}
const ROUTE_COLOR='#000000';let terrainData=null,worldData=null,dynamicSignature='',showRoutes=true,showCoverage=false;const capabilityColor=payload=>({first_aid:'#458fe5',food:'#299e93',medical:'#9976d7'}[payload]||'#458fe5');
function groundHeight(x,north){if(!terrainData?.elevations?.length)return 0;const n=terrainData.size,b=terrainData.bounds||[-600,-600,600,600],u=Math.max(0,Math.min(n-1,(x-b[0])/(b[2]-b[0])*(n-1))),v=Math.max(0,Math.min(n-1,(north-b[1])/(b[3]-b[1])*(n-1))),i=Math.min(n-2,Math.floor(u)),j=Math.min(n-2,Math.floor(v)),fx=u-i,fy=v-j,e=terrainData.elevations;const h=(a,c)=>Number(Array.isArray(e[c])?e[c][a]:e[c*n+a])||0;return h(i,j)*(1-fx)*(1-fy)+h(i+1,j)*fx*(1-fy)+h(i,j+1)*(1-fx)*fy+h(i+1,j+1)*fx*fy}
const vec=p=>{const x=Number(p?.[0])||0,north=Number(p?.[1])||0;return new THREE.Vector3(x,groundHeight(x,north)+(Number(p?.[2])||0),-north)};

let state=null, run=null, sequence=-1, socket=null, token=null, connected=false, selected=null, cameraMode='overview', retry=600, lastPacket=0;

let lastUI=0;const pending=new Map(), droneObjects=new Map(), resourceObjects=new Map(), incidentObjects=new Map();

const host=$('scene'), scene=new THREE.Scene(); scene.background=new THREE.Color('#d8e3e5'); scene.fog=new THREE.FogExp2('#cbdce0',0.00009);

const camera=new THREE.PerspectiveCamera(43,1,.5,7000); camera.position.set(870,1030,1000);camera.layers.enable(2);

const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'high-performance'});renderer.setPixelRatio(Math.min(devicePixelRatio,1.6));renderer.setSize(host.clientWidth,host.clientHeight);renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.06;host.appendChild(renderer.domElement);

const controls=new OrbitControls(camera,renderer.domElement); controls.enableDamping=true;controls.dampingFactor=.07;controls.zoomToCursor=true;controls.minDistance=5;controls.maxDistance=2600;controls.zoomSpeed=1.15;controls.maxPolarAngle=Math.PI*.475;controls.target.set(0,15,0);

scene.add(new THREE.HemisphereLight('#e6f5f3','#586148',2.1));const sun=new THREE.DirectionalLight('#fff0d6',2.8);sun.position.set(-500,1000,400);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);Object.assign(sun.shadow.camera,{left:-850,right:850,top:850,bottom:-850,far:2200});sun.shadow.bias=-.0003;sun.shadow.normalBias=1.8;scene.add(sun);

const worldGroup=new THREE.Group(), actorsGroup=new THREE.Group(), routesGroup=new THREE.Group(),dynamicGroup=new THREE.Group();scene.add(worldGroup,actorsGroup,routesGroup,dynamicGroup);

const material=(color,extra={})=>new THREE.MeshStandardMaterial({color,roughness:.85,...extra});

const mats={land:material('#adb998'),curb:material('#c5c6bb'),road:material('#69747b'),roof:material('#d1c8b3'),darkRoof:material('#627477'),drone:material('#e0e7df',{metalness:.35,roughness:.4}),arm:material('#263e46',{metalness:.6}),cyan:material('#4ca8ff',{emissive:'#308cf1',emissiveIntensity:1.3}),orange:material('#f18368',{emissive:'#e58a4a',emissiveIntensity:.7}),base:material('#668f85'),tree:material('#60845c'),trunk:material('#665c48'),stripe:material('#cad0b5')};

const boxGeo=new THREE.BoxGeometry(1,1,1), cylinderGeo=new THREE.CylinderGeometry(1,1,1,12);

function box(parent,x,y,z,w,h,d,mat){const mesh=new THREE.Mesh(boxGeo,mat);mesh.position.set(x,y,z);mesh.scale.set(w,h,d);mesh.castShadow=true;mesh.receiveShadow=true;parent.add(mesh);return mesh;}

// Dispose only resources owned by the removed subtree. Global and still-visible
// resources remain valid even when a transient group shares their materials.
function disposeGroup(group){
 const keepMaterials=new Set(Object.values(mats)),keepGeometry=new Set([boxGeo,cylinderGeo]),keepTextures=new Set();
 const materials=new Set(),geometries=new Set(),textures=new Set();
 const eachMaterial=(object,visit)=>{for(const m of Array.isArray(object.material)?object.material:[object.material])if(m)visit(m)};
 const materialTextures=(m,visit)=>{for(const value of Object.values(m))if(value?.isTexture)visit(value);for(const uniform of Object.values(m.uniforms||{})){const value=uniform?.value;if(value?.isTexture)visit(value);else if(Array.isArray(value))value.forEach(t=>{if(t?.isTexture)visit(t)})}};
 scene.traverse(object=>{let inside=false;for(let node=object;node;node=node.parent)if(node===group){inside=true;break}if(inside)return;if(object.geometry)keepGeometry.add(object.geometry);eachMaterial(object,m=>keepMaterials.add(m))});
 for(const m of keepMaterials)materialTextures(m,t=>keepTextures.add(t));
 group.traverse(object=>{if(object.geometry&&!keepGeometry.has(object.geometry))geometries.add(object.geometry);eachMaterial(object,m=>{if(!keepMaterials.has(m)){materials.add(m);materialTextures(m,t=>{if(!keepTextures.has(t))textures.add(t)})}})});
 group.clear();for(const geometry of geometries)geometry.dispose();for(const texture of textures)texture.dispose();for(const m of materials)m.dispose();
}


function label(text,color='#6e8997',size=12){const c=document.createElement('canvas');let ctx=c.getContext('2d');ctx.font='600 34px -apple-system,Segoe UI,Arial';const title=String(text).slice(0,34);c.width=Math.max(112,Math.ceil(ctx.measureText(title).width)+34);c.height=64;ctx=c.getContext('2d');ctx.fillStyle='rgba(255,255,255,.97)';ctx.beginPath();ctx.roundRect(2,2,c.width-4,60,11);ctx.fill();ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();ctx.font='600 34px -apple-system,Segoe UI,Arial';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='#253e50';ctx.fillText(title,c.width/2,33);const texture=new THREE.CanvasTexture(c);texture.colorSpace=THREE.SRGBColorSpace;const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,depthTest:false,depthWrite:false,transparent:true,toneMapped:false,fog:false,sizeAttenuation:false}));const height=Math.max(.012,size*.0019);sprite.scale.set(height*c.width/64,height,1);sprite.renderOrder=10;sprite.layers.set(2);return sprite;}

function line(points,color,opacity=.7,parent=worldGroup){if(points.length<2)return;const geometry=new THREE.BufferGeometry().setFromPoints(points);const mesh=new THREE.Line(geometry,new THREE.LineBasicMaterial({color,transparent:true,opacity}));parent.add(mesh);return mesh;}

function ribbon(points,width,y,mat,parent=worldGroup){if(points.length<2)return;const samples=[];for(let i=1;i<points.length;i++){const a=points[i-1],b=points[i],steps=Math.max(1,Math.ceil(Math.hypot(b[0]-a[0],b[1]-a[1])/15));for(let j=0;j<steps;j++)samples.push([a[0]+(b[0]-a[0])*j/steps,a[1]+(b[1]-a[1])*j/steps])}samples.push(points[points.length-1]);const vertices=[],uvs=[],indices=[];for(let i=0;i<samples.length;i++){const p=vec(samples[i]),before=vec(samples[Math.max(0,i-1)]),after=vec(samples[Math.min(samples.length-1,i+1)]),dir=after.sub(before);dir.y=0;dir.normalize();const normal=new THREE.Vector3(-dir.z,0,dir.x).multiplyScalar(width/2);for(const sign of [1,-1]){const x=p.x+normal.x*sign,z=p.z+normal.z*sign;vertices.push(x,groundHeight(x,-z)+y,z)}uvs.push(0,i*.22,1,i*.22);if(i){const a=(i-1)*2;indices.push(a,a+2,a+1,a+1,a+2,a+3)}}const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));g.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));g.setIndex(indices);g.computeVertexNormals();const mesh=new THREE.Mesh(g,mat);mesh.receiveShadow=true;parent.add(mesh);return mesh;}
function terrainMesh(bounds){const steps=48,vertices=[],uv=[],indices=[];for(let j=0;j<=steps;j++)for(let i=0;i<=steps;i++){const x=bounds[0]+(bounds[2]-bounds[0])*i/steps,north=bounds[1]+(bounds[3]-bounds[1])*j/steps;vertices.push(x,groundHeight(x,north),-north);uv.push(i/steps,j/steps);if(i<steps&&j<steps){const a=j*(steps+1)+i;indices.push(a,a+1,a+steps+1,a+1,a+steps+2,a+steps+1)}}const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));g.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));g.setIndex(indices);g.computeVertexNormals();const mesh=new THREE.Mesh(g,mats.land);mesh.receiveShadow=true;worldGroup.add(mesh);const skirtVertices=[],skirtIndices=[];const boundary=[];for(let i=0;i<=steps;i++)boundary.push([bounds[0]+(bounds[2]-bounds[0])*i/steps,bounds[1]]);for(let i=1;i<=steps;i++)boundary.push([bounds[2],bounds[1]+(bounds[3]-bounds[1])*i/steps]);for(let i=1;i<=steps;i++)boundary.push([bounds[2]-(bounds[2]-bounds[0])*i/steps,bounds[3]]);for(let i=1;i<=steps;i++)boundary.push([bounds[0],bounds[3]-(bounds[3]-bounds[1])*i/steps]);boundary.forEach(([x,n],i)=>{skirtVertices.push(x,groundHeight(x,n),-n,x,-9,-n);if(i){const a=(i-1)*2;skirtIndices.push(a,a+1,a+2,a+1,a+3,a+2)}});const skirt=new THREE.BufferGeometry();skirt.setAttribute('position',new THREE.Float32BufferAttribute(skirtVertices,3));skirt.setIndex(skirtIndices);skirt.computeVertexNormals();worldGroup.add(new THREE.Mesh(skirt,material('#9eac96',{side:THREE.DoubleSide})));}

let waterMaterial=null;

function buildWorld(world){terrainData=world.terrain||null;worldData=world;dynamicSignature='';disposeGroup(worldGroup);const bounds=world.bounds||[-600,-600,600,600];const width=bounds[2]-bounds[0],depth=bounds[3]-bounds[1],cx=(bounds[0]+bounds[2])/2,cz=-(bounds[1]+bounds[3])/2;terrainMesh(bounds);box(worldGroup,cx,-9,cz,width,18,depth,mats.land);box(worldGroup,cx,-25,cz,width+8,16,depth+8,material('#a5b8b5'));const grid=new THREE.GridHelper(Math.max(width,depth),24,'#b5cebb','#9bb49d');grid.position.y=.2;grid.material.transparent=true;grid.material.opacity=.11;if(!terrainData)worldGroup.add(grid);

 const canvas=document.createElement('canvas');canvas.width=128;canvas.height=128;const ctx=canvas.getContext('2d');ctx.fillStyle='#afbbb7';ctx.fillRect(0,0,128,128);for(let y=10;y<128;y+=19)for(let x=7;x<128;x+=19){ctx.fillStyle=(x+y)%3?'#607a7e':'#adc4bd';ctx.fillRect(x,y,8,10);ctx.fillStyle='#879996';ctx.fillRect(x,y+10,9,2)}const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;texture.wrapS=texture.wrapT=THREE.RepeatWrapping;texture.repeat.set(.035,.035);

 const buildingMats=['#f0e4ce','#cdb78f','#d6a78d','#deded5','#bccbc5','#c8b19c'].map(c=>material(c,{map:texture}));

 for(const [index,b] of (world.buildings||[]).entries()){const fp=b.footprint;if(!fp?.length)continue;const shape=new THREE.Shape();fp.forEach((p,i)=>i?shape.lineTo(p[0],p[1]):shape.moveTo(p[0],p[1]));shape.closePath();const h=b.height||18;const g=new THREE.ExtrudeGeometry(shape,{depth:h,bevelEnabled:false});g.rotateX(-Math.PI/2);const mesh=new THREE.Mesh(g,[mats.roof,buildingMats[index%buildingMats.length]]);mesh.castShadow=true;mesh.receiveShadow=true;worldGroup.add(mesh);const x=fp.reduce((s,p)=>s+p[0],0)/fp.length,z=-fp.reduce((s,p)=>s+p[1],0)/fp.length;const foundation=groundHeight(x,-z);mesh.position.y=foundation;if(index%2===0)box(worldGroup,x,foundation+h+1.5,z,6,3,5,mats.darkRoof);if(index%3===0){const tank=new THREE.Mesh(cylinderGeo,mats.roof);tank.position.set(x+5,foundation+h+3,z+3);tank.scale.set(2,6,2);worldGroup.add(tank)} }

 for(const road of world.roads||[]){const pts=road.points||[];ribbon(pts,road.width||13,.5,mats.curb);ribbon(pts,(road.width||13)-2,.65,mats.road);for(let i=1;i<pts.length;i++){const a=vec(pts[i-1]),b=vec(pts[i]),dist=a.distanceTo(b);for(let k=3;k<dist;k+=15){const p=a.clone().lerp(b,k/dist);const dash=box(worldGroup,p.x,groundHeight(p.x,-p.z)+.76,p.z,1,.07,6,mats.stripe);dash.rotation.y=Math.atan2(b.x-a.x,b.z-a.z)}}if(road.blocked){const mid=vec(pts[Math.floor(pts.length/2)]);box(worldGroup,mid.x,mid.y+2,mid.z,16,4,2,mats.orange)}}

 const river=world.river?.points||world.river||[];if(Array.isArray(river)&&river.length){ribbon(river,(world.river_width||45)+8,.8,material('#577c78'));waterMaterial=new THREE.ShaderMaterial({transparent:true,side:THREE.DoubleSide,uniforms:{time:{value:0}},vertexShader:'varying vec2 vUv; void main(){vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',fragmentShader:'varying vec2 vUv; uniform float time; void main(){float w=sin(vUv.y*44.0+vUv.x*19.0-time*.65)*sin(vUv.x*72.0+vUv.y*18.0+time*.4);float edge=smoothstep(0.0,.16,vUv.x)*smoothstep(0.0,.16,1.0-vUv.x);vec3 c=mix(vec3(.24,.48,.48),vec3(.14,.34,.39),edge)+w*.035;gl_FragColor=vec4(c,.94);}'});ribbon(river,world.river_width||45,1.15,waterMaterial);}

 let seed=12;const rand=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296};const treePositions=[];for(let n=0;n<430;n++){const x=bounds[0]+rand()*width,z=-(bounds[1]+rand()*depth);let clear=true;for(const b of world.buildings||[]){if(b.footprint?.some(p=>Math.hypot(p[0]-x,-p[1]-z)<24)){clear=false;break}}if(clear&&(Math.abs(x)>450||Math.abs(z)>430||rand()<.25))treePositions.push([x,z,4+rand()*5])}const crowns=new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1,1),mats.tree,treePositions.length),trunks=new THREE.InstancedMesh(cylinderGeo,mats.trunk,treePositions.length);const dummy=new THREE.Object3D();treePositions.forEach(([x,z,s],i)=>{dummy.position.set(x,groundHeight(x,-z)+s+4,z);dummy.scale.set(s,s*1.3,s);dummy.updateMatrix();crowns.setMatrixAt(i,dummy.matrix);dummy.position.set(x,groundHeight(x,-z)+3,z);dummy.scale.set(1,6,1);dummy.updateMatrix();trunks.setMatrixAt(i,dummy.matrix)});crowns.castShadow=true;worldGroup.add(crowns,trunks);

 for(const base of [...(world.bases||[]),...(world.shelters||[])]){const p=vec(base.position);const g=new THREE.Group();g.position.copy(p);worldGroup.add(g);box(g,0,1,0,55,2,45,mats.base);const ring=new THREE.Mesh(new THREE.RingGeometry(12,13,40),new THREE.MeshBasicMaterial({color:'#bbdfbf',side:THREE.DoubleSide}));ring.rotation.x=-Math.PI/2;ring.position.y=2.1;g.add(ring);box(g,0,2.2,0,14,.1,2,mats.drone);box(g,-6,2.2,0,2,.1,13,mats.drone);box(g,6,2.2,0,2,.1,13,mats.drone);const title=label(nice(base.kind||base.id).toUpperCase(),'#b6e0bf',9);title.position.set(0,20,0);g.add(title)}

 $('map-source').textContent=`${world.metadata?.source||world.source||'Geographic district'} / simulated flooding`;let attribution=$('osm-attribution');if(!attribution){attribution=document.createElement('a');attribution.id='osm-attribution';attribution.href='https://www.openstreetmap.org/copyright';attribution.target='_blank';attribution.rel='noopener noreferrer';attribution.style.cssText='position:absolute;right:20px;bottom:5px;color:#aec7ce;font-size:8px;text-decoration:none';host.parentElement.appendChild(attribution)}attribution.textContent=world.metadata?.attribution||'Geographic reference';batchStaticMeshes();applyLayers();
}



function batchStaticMeshes(){const buckets=new Map(),remove=[];worldGroup.updateMatrixWorld(true);worldGroup.traverse(o=>{if(!o.isMesh||o.isInstancedMesh||o.material?.transparent)return;const materials=Array.isArray(o.material)?o.material:[o.material];if(materials.some(m=>m.transparent||m.isShaderMaterial))return;const g=o.geometry.index?o.geometry.toNonIndexed():o.geometry.clone();g.applyMatrix4(o.matrixWorld);const groups=g.groups.length?g.groups:[{start:0,count:g.attributes.position.count,materialIndex:0}];for(const range of groups){const m=materials[range.materialIndex]||materials[0];if(!buckets.has(m))buckets.set(m,{position:[],normal:[],uv:[]});const bucket=buckets.get(m);for(let i=range.start;i<range.start+range.count;i++){for(const [name,size]of [['position',3],['normal',3],['uv',2]]){const attr=g.attributes[name];for(let j=0;j<size;j++)bucket[name].push(attr?attr.array[i*size+j]:0)}}}g.dispose();remove.push(o)});for(const o of remove){o.removeFromParent();if(o.geometry!==boxGeo&&o.geometry!==cylinderGeo)o.geometry.dispose()}for(const [m,b]of buckets){const g=new THREE.BufferGeometry();for(const [name,size]of [['position',3],['normal',3],['uv',2]])g.setAttribute(name,new THREE.Float32BufferAttribute(b[name],size));const mesh=new THREE.Mesh(g,m);mesh.castShadow=true;mesh.receiveShadow=true;worldGroup.add(mesh)}}


function updateDynamicWorld(){if(!worldData)return;const flood=state.flood||{},roads=state.road_conditions||[],signature=JSON.stringify([flood,roads]);if(signature===dynamicSignature)return;dynamicSignature=signature;disposeGroup(dynamicGroup);const river=worldData.river?.points||worldData.river||[];if(flood.enabled&&river.length)ribbon(river,Number(flood.width)||135,Number(flood.level)||2.1,new THREE.MeshBasicMaterial({color:'#4c9ccc',transparent:true,opacity:.3,side:THREE.DoubleSide,depthWrite:false}),dynamicGroup);const closureMat=new THREE.MeshBasicMaterial({color:'#e77660'});for(const road of roads){if(!road.blocked)continue;let pts=road.points;if(!pts&&road.id)pts=worldData.roads?.find(r=>r.id===road.id)?.points;if(!pts?.length)continue;const a=vec(pts[0]),b=vec(pts[pts.length-1]),p=a.clone().lerp(b,.5);const barrier=box(dynamicGroup,p.x,groundHeight(p.x,-p.z)+2.3,p.z,10,3,1.5,closureMat);barrier.rotation.y=Math.atan2(b.x-a.x,b.z-a.z);line([a.clone().add(new THREE.Vector3(0,2,0)),b.clone().add(new THREE.Vector3(0,2,0))],'#e77660',.55,dynamicGroup)}}
function createDrone(drone){const group=new THREE.Group();const capabilityMat=material(capabilityColor(drone.payload),{emissive:capabilityColor(drone.payload),emissiveIntensity:.25});box(group,0,0,0,4.8,2.1,6.3,capabilityMat);box(group,0,-1.7,1,2,1.4,2,mats.arm);const cargo=box(group,0,-3,0,2.8,1.5,2.8,mats.orange);const propellers=[];for(const x of [-5,5])for(const z of [-5,5]){const arm=box(group,x/2,0,z/2,1,.7,8,mats.arm);arm.rotation.y=Math.atan2(x,z);const motor=new THREE.Mesh(cylinderGeo,mats.arm);motor.position.set(x,.5,z);motor.scale.set(.8,1.5,.8);group.add(motor);const prop=box(group,x,1.5,z,7,.12,.55,mats.drone);propellers.push(prop);const disk=new THREE.Mesh(new THREE.CircleGeometry(3.6,20),new THREE.MeshBasicMaterial({color:'#a2dcd3',transparent:true,opacity:.16,side:THREE.DoubleSide,depthWrite:false}));disk.rotation.x=-Math.PI/2;disk.position.set(x,1.6,z);group.add(disk);box(group,x,.3,z,.65,.5,.65,x<0?mats.cyan:mats.orange)}for(const x of [-2,2])box(group,x,-2,0,.5,2,5,mats.arm);const tag=label(drone.id,capabilityColor(drone.payload),5);tag.position.y=18;group.add(tag);group.position.copy(vec(drone.position));group.scale.setScalar(1.2);group.userData={kind:'drone',id:drone.id,target:vec(drone.position),propellers,tag,cargo};actorsGroup.add(group);return group;}

function createResource(resource){const g=new THREE.Group(),isBoat=/boat|water/i.test(resource.transport||resource.kind);const mat=material(isBoat?'#d19e61':resource.kind==='fire_engine'?'#d63b32':'#ddd9c2');box(g,0,2,0,isBoat?6:5,3,isBoat?15:11,mat);box(g,0,4,-1,4,3,5,mats.drone);box(g,0,5.8,-1,3,.5,1,mats.cyan);if(!isBoat)for(const x of [-2.7,2.7])for(const z of [-3.5,3.5]){const wheel=new THREE.Mesh(cylinderGeo,mats.arm);wheel.rotation.z=Math.PI/2;wheel.scale.set(1.4,1,1.4);wheel.position.set(x,1,z);g.add(wheel)}else{const bow=new THREE.Mesh(new THREE.ConeGeometry(3.7,5,4),mat);bow.rotation.x=Math.PI/2;bow.rotation.z=Math.PI/4;bow.position.set(0,2,9);g.add(bow)}const tag=label(resource.agency?`${resource.id} · ${resource.agency}`:(resource.name||resource.id),resource.kind==='fire_engine'?'#d63b32':'#e2d4b8',5);tag.position.y=15;g.add(tag);g.position.copy(vec(resource.position));g.scale.setScalar(1.5);g.userData={kind:'resource',id:resource.id,target:vec(resource.position),tag};actorsGroup.add(g);return g;}

function createIncident(incident){const group=new THREE.Group(),p=vec(incident.position);group.position.copy(p);const color=incident.severity==='critical'||incident.severity>=4?'#e8755e':'#e5985b';const ring=new THREE.Mesh(new THREE.RingGeometry(13,16,48),new THREE.MeshBasicMaterial({color,side:THREE.DoubleSide,transparent:true,opacity:.8,depthWrite:false}));ring.rotation.x=-Math.PI/2;ring.position.y=2;group.add(ring);const beam=new THREE.Mesh(new THREE.CylinderGeometry(2,13,110,20,1,true),new THREE.MeshBasicMaterial({color,transparent:true,opacity:.24,depthWrite:false,side:THREE.DoubleSide}));beam.position.y=55;group.add(beam);const orb=new THREE.Mesh(new THREE.OctahedronGeometry(8),mats.orange);orb.position.y=62;group.add(orb);const tag=label(`${incident.id} · ${nice(incident.category)}`,color,7);tag.position.y=90;group.add(tag);group.userData={kind:'incident',id:incident.id,ring,orb,tag};if(incident.category==='fire'||/fire/i.test(incident.title||''))addFire(group);actorsGroup.add(group);return group;}
function addFire(group){const flames=[],smoke=[];for(let i=0;i<5;i++){const h=18+((i*7)%5)*3.5,r=4+(i%3)*1.2,cone=new THREE.Mesh(new THREE.ConeGeometry(r,h,8,1,true),new THREE.MeshBasicMaterial({color:i%2?'#ffb02e':'#ff5a1f',transparent:true,opacity:.8,blending:THREE.AdditiveBlending,depthWrite:false,side:THREE.DoubleSide})),a=i/5*Math.PI*2;cone.position.set(i?Math.cos(a)*6:0,2+h/2,i?Math.sin(a)*6:0);cone.userData={h,phase:i*1.7};group.add(cone);flames.push(cone)}for(let i=0;i<7;i++){const s=new THREE.Sprite(new THREE.SpriteMaterial({map:cloudTex,color:'#5b626b',transparent:true,opacity:0,depthWrite:false}));s.userData={phase:i/7};group.add(s);smoke.push(s)}group.userData.fire={flames,smoke}}


/* ====================== shared helpers ====================== */
const ease=t=>t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;
const setHTML=(el,html)=>{if(el&&el._html!==html){el._html=html;el.innerHTML=html}};
const patchList=(el,items,render,empty='')=>{if(!el)return;if(!items.length){setHTML(el,empty);return}el._html=null;const old=new Map();for(const c of [...el.children]){if(c.dataset?.key)old.set(c.dataset.key,c);else c.remove()}const seen=new Set();items.forEach((item,i)=>{const key=String(item.id),html=render(item);seen.add(key);let node=old.get(key);if(!node||node._html!==html){const t=document.createElement('template');t.innerHTML=html.trim();const n=t.content.firstElementChild;n.dataset.key=key;n._html=html;if(node)node.replaceWith(n);node=n}if(el.children[i]!==node)el.insertBefore(node,el.children[i]||null)});for(const [k,c] of old)if(!seen.has(k))c.remove()};
const cloudTex=(()=>{const c=document.createElement('canvas');c.width=c.height=64;const g=c.getContext('2d'),r=g.createRadialGradient(32,32,2,32,32,31);r.addColorStop(0,'rgba(255,255,255,.95)');r.addColorStop(.5,'rgba(255,255,255,.45)');r.addColorStop(1,'rgba(255,255,255,0)');g.fillStyle=r;g.fillRect(0,0,64,64);const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;return t})();
const ACTIVITY_COLOR={scanning:'#1fa59a',assessing:'#e9a21c',delivering:'#2d7ff0'};
const FOOT_FALLBACK='#8fa3b1';
const unitState=r=>({available:'Available',enroute:`En route${r.eta!=null?` · ETA ${Math.ceil(r.eta)}s`:''}`,on_scene:'On scene',awaiting_handoff:'On scene · awaiting briefing',evacuating:'Evacuating',busy:'Busy',holding:'Holding',unavailable:'Unavailable'}[r.state]||`${nice(r.state)}${r.eta!=null?` · ETA ${Math.ceil(r.eta)}s`:''}`);
const ACTIVITY_TEXT={docked:'Parked on its dock pad, ready to launch',launching:'Taking off from the dock',transit:'Flying to its next task',scanning:'Sweeping the ground with its sensor to find people and hazards',assessing:'Hovering over an incident to assess it and brief responders',delivering:'Delivering supplies to people on the ground',returning:'Returning to its dock',charging:'Recharging on its dock',holding:'Holding position and waiting for tasking',failed:'Out of service'};
const SCENARIO_TEXT={rooftop_flood:'Residents are stranded on rooftops as the river rises. Find them, brief ground teams and rescue by boat.',medical_supply:'Critical medical supplies must reach cut-off patients while the flood isolates the district.',blocked_bridge:'A blocked bridge forces evacuation by alternative routes while drones keep watch.',dock_fire:'Fires break out near the drone docks. Launch drones in waves, scout the fires and direct fire engines and rescue teams.'};
const SIGNAL_KIND={sos:['SOS','#e5533d'],briefing:['Briefing','#2f80ed'],ack:['Acknowledged','#2e9e6b'],recall:['Recall','#8a6fd1'],dispatch:['Dispatch','#e08a2e'],arrival:['Arrived','#1f9d8f'],supply_request:['Supply request','#c2528a'],resource_request:['Needs resource','#d9822b']};
const kindMap=()=>({drone:droneObjects,resource:resourceObjects,incident:incidentObjects,dock:dockObjects});
const itemsOf=kind=>kind==='drone'?state?.drones:kind==='resource'?state?.resources:kind==='dock'?state?.docks:state?.incidents;

/* ====================== extra scene groups ====================== */
const footGroup=new THREE.Group(),coverageGroup=new THREE.Group(),docksGroup=new THREE.Group(),signalsGroup=new THREE.Group(),selGroup=new THREE.Group(),trailGroup=new THREE.Group();
scene.add(footGroup,coverageGroup,docksGroup,signalsGroup,selGroup,trailGroup);
const dockObjects=new Map(),footObjects=new Map(),routeLines=new Map(),signalObjects=new Map(),seenSignals=new Map();
const MAXR=64;let packetDur=140,uiDirty=false,selSignature='',coverageSignature='',flight=null;

/* ====================== actors ====================== */
function dockById(id){return (state?.docks||[]).find(d=>d.id===id)}
function dockRadius(dock){const n=Math.max(1,(dock.drone_ids||[]).length),cols=Math.ceil(Math.sqrt(n));return Math.max(16,cols*5.8+9)}
function padSlot(dock,droneId){const ids=dock.drone_ids||[],i=Math.max(0,ids.indexOf(droneId)),n=Math.max(1,ids.length),cols=Math.ceil(Math.sqrt(n)),sp=11.5,cx=i%cols-(cols-1)/2,cz=Math.floor(i/cols)-(Math.ceil(n/cols)-1)/2;const base=vec(dock.position);return new THREE.Vector3(base.x+cx*sp,groundHeight(base.x+cx*sp,-(base.z+cz*sp))+5.6,base.z+cz*sp)}
function actorTarget(item,kind){if(kind==='drone'&&item.activity==='docked'){const dock=dockById(item.dock_id)||(state.docks||[]).find(d=>(d.drone_ids||[]).includes(item.id));if(dock&&Array.isArray(dock.position))return padSlot(dock,item.id)}return vec(item.position)}

function spawnPulse(pos,color){const m=new THREE.Mesh(new THREE.RingGeometry(.9,1,48).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color,transparent:true,opacity:.9,depthTest:false,side:THREE.DoubleSide}));m.position.copy(pos).add(new THREE.Vector3(0,3,0));m.renderOrder=7;m.userData.born=performance.now();signalsGroup.add(m);pulses.push(m)}
const pulses=[];
function stepPulses(now){for(let i=pulses.length-1;i>=0;i--){const m=pulses[i],a=(now-m.userData.born)/1400;if(a>=1){signalsGroup.remove(m);m.geometry.dispose();m.material.dispose();pulses.splice(i,1);continue}m.scale.setScalar(6+a*60);m.material.opacity=.9*(1-a)}}
function syncActors(items,map,create,kind){
 const ids=new Set(),now=performance.now();
 for(const item of items||[]){
  if(!Array.isArray(item.position))continue; // never spawn an actor at the origin
  ids.add(item.id);let o=map.get(item.id);const target=actorTarget(item,kind);
  if(!o){o=create(item);o.userData.base=o.scale.x;o.userData.vis=0;o.scale.setScalar(.0001);o.position.copy(target);o.userData.target=target.clone();o.userData.from=target.clone();o.userData.t0=now;map.set(item.id,o)}
  else{
   o.userData.leaving=0;
   if(o.userData.target){const from=o.position.clone();if(from.distanceTo(target)>500)from.copy(target);o.userData.from=from;o.userData.target.copy(target);o.userData.t0=now}
  }
  if(kind==='resource'){const was=o.userData.item?.incident_id;if(item.incident_id&&!was&&o.userData.item)spawnPulse(o.position,'#e08a2e')}o.userData.item=item;if(o.userData.cargo)o.userData.cargo.visible=!!item.payload&&!item.delivered;
 }
 for(const [id,o] of map)if(!ids.has(id)&&!o.userData.leaving)o.userData.leaving=now;
}

function stepActors(now,dt,live){
 for(const map of [droneObjects,resourceObjects,incidentObjects]){
  for(const [id,o] of map){
   const u=o.userData,item=u.item;
   if(u.target){const k=Math.min(1,(now-u.t0)/(packetDur*1.1));o.position.lerpVectors(u.from,u.target,k)}
   const hidden=!!u.leaving||(u.kind==='incident'&&item?.status==='resolved');
   u.vis+=((hidden?0:1)-u.vis)*(1-Math.exp(-dt*(hidden?4:6)));
   if(u.vis<.015&&hidden){o.visible=false;if(u.leaving&&now-u.leaving>700){actorsGroup.remove(o);disposeGroup(o);map.delete(id)}continue}
   o.visible=true;o.scale.setScalar(Math.max(.0001,u.base*ease(Math.min(1,u.vis))));
   if(item?.heading!=null&&u.kind!=='incident'){let heading=-item.heading;if(Math.abs(heading)>Math.PI*2)heading*=Math.PI/180;o.rotation.y=heading}
   if(u.propellers&&live&&item?.active!==false&&!/docked|charging|failed/.test(item?.activity||'')&&!/crash|land|idle|charg/i.test(item?.state||''))u.propellers.forEach(p=>p.rotation.y+=dt*65);
   if(u.tag){const want=u.kind==='drone'?(!!item?.task_id||(selected?.kind==='drone'&&selected.id===id)):1;u.tag.material.opacity+=((want?1:0)-u.tag.material.opacity)*(1-Math.exp(-dt*8));u.tag.visible=u.tag.material.opacity>.03}
   if(u.kind==='drone'){u.trailT=(u.trailT||0)+dt;if(u.trailT>.25){u.trailT=0;const tr=u.trail||(u.trail=[]),l=tr[tr.length-1];if(!l||Math.hypot(l[0]-o.position.x,l[1]-o.position.y,l[2]-o.position.z)>1.5){tr.push([o.position.x,o.position.y,o.position.z]);if(tr.length>40)tr.shift()}}}
  }
 }
}

function stepIncidentFX(now,live,dt){
 for(const o of incidentObjects.values()){
  const u=o.userData;if(!o.visible)continue;if(live)u.orb.rotation.y+=dt*.6;u.ring.material.opacity=.5+Math.sin(now*.0015)*.2;
  if(u.fire){for(const c of u.fire.flames){const d=c.userData,s=1+.28*Math.sin(now*.011+d.phase)+.16*Math.sin(now*.029+d.phase*2.3);c.scale.set(1+.12*Math.sin(now*.017+d.phase),s,1+.12*Math.cos(now*.015+d.phase));c.position.y=2+d.h*s/2;c.rotation.z=Math.sin(now*.004+d.phase)*.08}
   for(const s of u.fire.smoke){const t=((now*.00022+s.userData.phase)%1);s.position.set(t*16,16+t*78,t*8);s.scale.setScalar(10+t*38);s.material.opacity=.4*Math.sin(Math.PI*t)}}
 }
}

/* ====================== docks ====================== */
function createDock(dock){
 const g=new THREE.Group(),r=dockRadius(dock),base=vec(dock.position);g.position.copy(base);
 const pad=new THREE.Mesh(new THREE.CylinderGeometry(r,r+1.5,1.2,48),material('#c9d2d6'));pad.position.y=.6;pad.receiveShadow=true;g.add(pad);
 const ring=new THREE.Mesh(new THREE.RingGeometry(r-3,r-1.8,64),new THREE.MeshBasicMaterial({color:'#2d7ff0',side:THREE.DoubleSide,transparent:true,opacity:.85}));ring.rotation.x=-Math.PI/2;ring.position.y=1.3;g.add(ring);
 const h=new THREE.Mesh(new THREE.RingGeometry(r*.28,r*.34,40),new THREE.MeshBasicMaterial({color:'#ffffff',side:THREE.DoubleSide,transparent:true,opacity:.7}));h.rotation.x=-Math.PI/2;h.position.y=1.35;g.add(h);
 const pole=new THREE.Mesh(cylinderGeo,mats.arm);pole.scale.set(.7,26,.7);pole.position.set(r*.85,13,r*.85);g.add(pole);
 const beacon=new THREE.Mesh(new THREE.SphereGeometry(1.8,12,8),new THREE.MeshBasicMaterial({color:'#2d7ff0'}));beacon.position.set(r*.85,27,r*.85);g.add(beacon);
 g.userData={kind:'dock',id:dock.id,beacon,r,item:dock,labelText:''};docksGroup.add(g);return g;
}
function syncDocks(){
 const ids=new Set();
 for(const dock of state.docks||[]){
  if(!Array.isArray(dock.position))continue;ids.add(dock.id);
  let o=dockObjects.get(dock.id);if(!o){o=createDock(dock);dockObjects.set(dock.id,o)}
  o.userData.item=dock;const docked=dock.docked??(dock.drone_ids||[]).length,text=`${String(dock.id||dock.name).toUpperCase()} · ${docked} docked`;
  if(o.userData.labelText!==text){if(o.userData.tag){o.remove(o.userData.tag);o.userData.tag.material.map.dispose();o.userData.tag.material.dispose()}const tag=label(text,'#2d7ff0',7);tag.position.y=44;o.add(tag);o.userData.tag=tag;o.userData.labelText=text}
 }
 for(const [id,o] of dockObjects)if(!ids.has(id)){docksGroup.remove(o);disposeGroup(o);dockObjects.delete(id)}
}

/* ====================== footprints & coverage ====================== */
const footMats={},footMat=(color,op)=>footMats[color+op]||(footMats[color+op]=new THREE.MeshBasicMaterial({color,transparent:true,opacity:op,depthWrite:false,depthTest:false,side:THREE.DoubleSide}));
const footDisc=new THREE.CircleGeometry(1,56).rotateX(-Math.PI/2),footRing=new THREE.RingGeometry(.955,1,72).rotateX(-Math.PI/2);
function syncFootprints(){
 const ids=new Set();
 for(const d of state.drones||[]){
  ids.add(d.id);let f=footObjects.get(d.id);
  if(!f){f={disc:new THREE.Mesh(footDisc,footMat(FOOT_FALLBACK,.14)),ring:new THREE.Mesh(footRing,footMat(FOOT_FALLBACK,.6)),r:0};f.disc.renderOrder=2;f.ring.renderOrder=3;f.disc.visible=f.ring.visible=false;footGroup.add(f.disc,f.ring);footObjects.set(d.id,f)}
  f.want=Number(d.sensor_radius_m)||0;const c=ACTIVITY_COLOR[d.activity]||FOOT_FALLBACK;if(f.color!==c){f.color=c;f.disc.material=footMat(c,.15);f.ring.material=footMat(c,.7)}
 }
 for(const [id,f] of footObjects)if(!ids.has(id)){footGroup.remove(f.disc,f.ring);footObjects.delete(id)}
}
function stepFootprints(dt){
 for(const [id,f] of footObjects){
  const o=droneObjects.get(id);f.r+=((o&&o.visible?f.want:0)-f.r)*(1-Math.exp(-dt*5));const show=f.r>.8;f.disc.visible=f.ring.visible=show;if(!show||!o)continue;
  const y=groundHeight(o.position.x,-o.position.z)+1.6;f.disc.position.set(o.position.x,y,o.position.z);f.ring.position.copy(f.disc.position);f.disc.scale.setScalar(f.r);f.ring.scale.setScalar(f.r);
 }
}
function updateCoverage(){
 const sig=showCoverage?(state.coverage||[]).map(c=>c.last_observed==null?0:1).join(''):'off';if(sig===coverageSignature)return;coverageSignature=sig;disposeGroup(coverageGroup);if(!showCoverage)return;
 for(const cell of state.coverage||[]){const x=cell.position[0],n=cell.position[1];ribbon([[x,n-80],[x,n+80]],160,.95,new THREE.MeshBasicMaterial({color:cell.last_observed==null?'#788b9a':'#51aa98',transparent:true,opacity:cell.last_observed==null?.09:.19,side:THREE.DoubleSide,depthWrite:false}),coverageGroup)}
}

/* ====================== routes ====================== */
const routeMat=new THREE.LineBasicMaterial({color:ROUTE_COLOR,transparent:true,opacity:.55});
function routeLineFor(key){let r=routeLines.get(key);if(!r){const geo=new THREE.BufferGeometry(),arr=new Float32Array(MAXR*3);geo.setAttribute('position',new THREE.BufferAttribute(arr,3));geo.setDrawRange(0,0);const l=new THREE.Line(geo,routeMat);l.frustumCulled=false;routesGroup.add(l);r={line:l,geo,arr,n:0,owner:null,sig:''};routeLines.set(key,r)}return r}
function setRoute(key,owner,route,active){
 const r=routeLineFor(key);r.owner=owner;
 if(!active||!route?.length||!owner){r.n=0;r.geo.setDrawRange(0,0);r.sig='';return}
 const pts=route.slice(0,MAXR-1),sig=JSON.stringify(pts);
 if(sig!==r.sig){r.sig=sig;pts.forEach((p,i)=>{const v=vec(p);r.arr[(i+1)*3]=v.x;r.arr[(i+1)*3+1]=v.y;r.arr[(i+1)*3+2]=v.z});r.n=pts.length+1}
 r.geo.setDrawRange(0,r.n);
}
function updateRoutes(){
 const keep=new Set();
 for(const d of state.drones||[]){keep.add('d:'+d.id);setRoute('d:'+d.id,droneObjects.get(d.id),d.route,d.activity!=='docked'&&d.state!=='crashed')}
 for(const r of state.resources||[]){keep.add('r:'+r.id);const inc=r.incident_id&&(state.incidents||[]).find(i=>i.id===r.incident_id),moving=!['available','unavailable','on_scene','awaiting_handoff','evacuating','holding','busy'].includes(r.state);setRoute('r:'+r.id,resourceObjects.get(r.id),r.route?.length?r.route:(inc&&moving&&inc.position?[inc.position]:[]),true)}
 for(const [k,r] of routeLines)if(!keep.has(k)){routesGroup.remove(r.line);r.geo.dispose();routeLines.delete(k)}
 routesGroup.visible=showRoutes;updateCoverage();updateSelectedRoute();
}
function stepRouteAnchors(){
 for(const r of routeLines.values()){if(!r.n||!r.owner)continue;r.arr[0]=r.owner.position.x;r.arr[1]=r.owner.position.y;r.arr[2]=r.owner.position.z;r.geo.attributes.position.needsUpdate=true}
}
let leadIn=null,trailLine=null;
function updateSelectedRoute(){
 updateZone();
 const d=selected?.kind==='drone'?(state.drones||[]).find(x=>x.id===selected.id):null;
 const sig=d&&showRoutes&&d.route?.length&&d.activity!=='docked'?d.id+JSON.stringify(d.route):'';
 if(sig===selSignature)return;selSignature=sig;disposeGroup(selGroup);leadIn=null;if(!sig)return;
 const black=new THREE.MeshBasicMaterial({color:ROUTE_COLOR}),pts=d.route.map(vec);
 if(pts.length>1){const path=new THREE.CurvePath();for(let i=1;i<pts.length;i++)path.add(new THREE.LineCurve3(pts[i-1],pts[i]));const tube=new THREE.Mesh(new THREE.TubeGeometry(path,Math.min(400,pts.length*10),.7,6,false),black);selGroup.add(tube)}
 leadIn=new THREE.Mesh(new THREE.CylinderGeometry(.7,.7,1,6),black);leadIn.userData.to=pts[0].clone();selGroup.add(leadIn);
 pts.forEach((p,index)=>{const node=new THREE.Mesh(new THREE.SphereGeometry(2.4,12,8),black);node.position.copy(p);selGroup.add(node);const tag=label(String(index+1),capabilityColor(d.payload),5);tag.position.copy(p).add(new THREE.Vector3(0,9,0));selGroup.add(tag)});
}
function stepSelectedFX(){
 const o=selected?.kind==='drone'?droneObjects.get(selected.id):null;
 if(leadIn&&o){const a=o.position,b=leadIn.userData.to,dir=b.clone().sub(a),len=dir.length();leadIn.visible=len>.5;if(len>.5){leadIn.position.copy(a).addScaledVector(dir,.5);leadIn.scale.set(1,len,1);leadIn.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),dir.normalize())}}
 if(!o||!showRoutes){if(trailLine)trailLine.visible=false;return}
 if(!trailLine){const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.BufferAttribute(new Float32Array(42*3),3));geo.setAttribute('color',new THREE.BufferAttribute(new Float32Array(42*3),3));trailLine=new THREE.Line(geo,new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:.9,depthTest:false}));trailLine.frustumCulled=false;trailLine.renderOrder=4;trailGroup.add(trailLine)}
 const tr=[...(o.userData.trail||[]),[o.position.x,o.position.y,o.position.z]],pa=trailLine.geometry.attributes.position,ca=trailLine.geometry.attributes.color;
 tr.forEach((p,i)=>{pa.setXYZ(i,p[0],p[1],p[2]);const f=i/Math.max(1,tr.length-1),shade=.85*(1-f)+.1*f;ca.setXYZ(i,shade,shade+.01,shade+.02)});pa.needsUpdate=ca.needsUpdate=true;trailLine.geometry.setDrawRange(0,tr.length);trailLine.visible=tr.length>1;
}

/* ====================== signals ====================== */
function entityPos(id){return (droneObjects.get(id)||resourceObjects.get(id)||incidentObjects.get(id)||dockObjects.get(id))?.position}
function syncSignals(){
 const now=performance.now(),speed=Math.max(1,state.speed||1),present=new Set();
 for(const s of state.signals||[]){
  if(s?.id==null)continue;present.add(s.id);if(seenSignals.has(s.id))continue;seenSignals.set(s.id,now);
  const age0=Math.max(0,(state.t||0)-(s.t||0));if(age0>8)continue;
  const src=Array.isArray(s.position)?vec(s.position):entityPos(s.from_id)?.clone();if(!src)continue;
  let [text,color]=SIGNAL_KIND[s.kind]||[nice(s.kind),'#6e8997'];const group=new THREE.Group();const extraTargets=[];
  if(s.kind==='resource_request'){const inc=(state.incidents||[]).find(i=>i.id===s.incident_id),rr=[...(inc?.resource_requests||[])].reverse().find(r=>r.status==='open')||(inc?.resource_requests||[]).slice(-1)[0],need=rr?.needed_kind||(/Request \d+x (\w+)/.exec(s.text||'')||[])[1];if(need){text=`Needs ${nice(need)}`;let best=null,bd=1e9;for(const r of state.resources||[]){if(r.kind!==need||!Array.isArray(r.position))continue;const d=Math.hypot(r.position[0]-s.position[0],r.position[1]-s.position[1]);if(d<bd){bd=d;best=r}}if(best)extraTargets.push(best.id)}}group.position.copy(src);
  const puffs=[];let seed=0;for(const ch of String(s.id))seed=(seed*31+ch.charCodeAt(0))>>>0;const rnd=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296};
  for(let i=0;i<4+(seed%2);i++){const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:cloudTex,color,transparent:true,opacity:0,depthWrite:false,depthTest:false,fog:false}));sp.userData={ox:(rnd()-.5)*12,oy:rnd()*6,oz:(rnd()-.5)*12,size:14+rnd()*10};sp.renderOrder=6;group.add(sp);puffs.push(sp)}
  const tag=label(text,color,6);tag.position.y=18;tag.material.opacity=0;group.add(tag);signalsGroup.add(group);
  const lines=[];for(const id of [...(s.to_ids||[]),...extraTargets]){const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.BufferAttribute(new Float32Array(6),3));const l=new THREE.Line(geo,new THREE.LineDashedMaterial({color,dashSize:4,gapSize:3,transparent:true,opacity:0,depthTest:false}));l.frustumCulled=false;l.renderOrder=5;signalsGroup.add(l);lines.push({line:l,id})}
  signalObjects.set(s.id,{group,puffs,tag,lines,src,born:now-Math.min(age0/speed,1.5)*1000});
 }
 for(const [id,at] of seenSignals)if(!present.has(id)&&now-at>20000)seenSignals.delete(id);
}
function removeSignal(id,rec){signalsGroup.remove(rec.group);for(const p of rec.puffs)p.material.dispose();rec.tag.material.map.dispose();rec.tag.material.dispose();for(const l of rec.lines){signalsGroup.remove(l.line);l.line.geometry.dispose();l.line.material.dispose()}signalObjects.delete(id)}
function stepSignals(now){
 for(const [id,rec] of signalObjects){
  const age=(now-rec.born)/1000;if(age>=4){removeSignal(id,rec);continue}
  const fade=Math.pow(1-age/4,1.3)*Math.min(1,age/.25);
  for(const p of rec.puffs){const u=p.userData,grow=1+age*.5;p.position.set(u.ox*(1+age*.5),u.oy+age*5,u.oz*(1+age*.5));p.scale.setScalar(u.size*grow);p.material.opacity=.45*fade}
  rec.tag.position.y=18+age*5;rec.tag.material.opacity=Math.min(1,fade*1.4);
  for(const l of rec.lines){const t=entityPos(l.id);l.line.visible=!!t;if(!t)continue;const a=l.line.geometry.attributes.position;a.setXYZ(0,rec.src.x,rec.src.y+4,rec.src.z);a.setXYZ(1,t.x,t.y+4,t.z);a.needsUpdate=true;l.line.computeLineDistances();l.line.material.opacity=.6*fade}
 }
}

/* ====================== selection ====================== */
function frameEntity(kind,id){const o=kindMap()[kind]?.get(id);if(!o)return;flyTo(o.position,{dist:kind==='incident'?95:kind==='dock'?130:kind==='resource'?80:75,entity:o})}
function select(kind,id){selected={kind,id};cameraMode='focus';frameEntity(kind,id);selSignature='';renderUI();updateSelectedRoute()}
function focusSelected(){if(selected)frameEntity(selected.kind,selected.id);else toast('Select a drone, incident, responder or dock first.')}

function renderSelection(){
 const el=$('selection');if(!selected){el.classList.add('hidden');return}
 const item=itemsOf(selected.kind)?.find(x=>x.id===selected.id);if(!item){el.classList.add('hidden');return}
 el.classList.remove('hidden');
 const list=values=>(values||[]).map(nice).join(', ')||'None';const row=(name,value)=>`<div class="detail-row"><span>${escapeHTML(name)}</span><strong>${escapeHTML(value)}</strong></div>`;
 let html='';const head=`<div class="detail-heading"><h3>${escapeHTML(item.title||item.name||item.id)}</h3><span class="detail-actions"><button data-focus title="Fly the camera to this item">Focus</button><button id="close-selection" aria-label="Close details">×</button></span></div>`;
 if(selected.kind==='incident'){
  const assigned=(state.resources||[]).filter(r=>r.incident_id===item.id),unmet=(item.needs||[]).filter(n=>!(item.supplied||[]).includes(n)),remaining=(item.required_kinds||[]).filter(n=>!(item.completed_kinds||[]).includes(n)),observed=item.last_observed??item.briefing?.observed_at;
  html+=`<p class="detail-status">${escapeHTML(nice(item.category))} / ${escapeHTML(nice(item.status))} / Priority ${escapeHTML(item.severity)}</p>`;
  html+=row('People',(()=>{const c=peopleCounts(item.id);return c.total?`${c.waiting} waiting · ${c.progress} being helped · ${c.safe} safe`:`${item.people||0}`})())+row('Evacuated',`${item.evacuated_people||0} / ${item.people||0} people`)+row('Still needed',list(unmet))+row('Supplied',list(item.supplied))+row('Required response',list(item.required_kinds))+row('Completed response',list(item.completed_kinds))+row('Outstanding response',list(remaining))+row('Assigned drone',item.drone_id||'Awaiting assignment')+row('First on scene',item.first_on_scene?nice(item.first_on_scene):'Nobody yet')+row('Resource requests',(item.resource_requests||[]).map(r=>`${r.qty}x ${nice(r.needed_kind)} (${nice(r.status)}${r.fulfilled_by?.length?': '+r.fulfilled_by.join(', '):''})`).join('; ')||'None');
  html+='<div class="detail-divider"></div>';
  html+=row('Assigned agencies',assigned.map(r=>`${r.name||r.agency||nice(r.kind)} (${r.id}${r.transport==='boat'?', boat':''})`).join(', ')||'Awaiting dispatch')+row('Ground briefing',item.handed_over?'Acknowledged':'Pending acknowledgment')+row('Last observation',observed==null?'Not yet observed':`${Math.max(0,Math.floor(state.t-observed))}s ago`)+row('Briefing source',item.briefing?.source||'No briefing yet');
  if(item.briefing?.summary)html+=`<p>${escapeHTML(item.briefing.summary)}</p>`;
  if(item.uncertain_delivery||item.uncertainty_delivery)html+='<p class="detail-warning">Delivery is unconfirmed after a telemetry interruption. Supplies must not be assumed delivered.</p>';
  if(item.wait_reason)html+=`<p class="detail-warning">${escapeHTML(nice(item.wait_reason))}</p>`;
  for(const r of assigned.filter(r=>r.state==='awaiting_handoff'&&!r.acknowledged))html+=`<button class="handoff-button" data-handoff="${escapeHTML(item.id)}" data-resource="${escapeHTML(r.id)}" ${token&&connected?'':'disabled'}>Acknowledge ${escapeHTML(r.id)} briefing</button>`;
 }else if(selected.kind==='drone'){
  const r=Number(item.sensor_radius_m)||0,speed=Number(item.speed_mps)||0,area=Math.PI*r*r/10000,rate=2*r*speed*60/10000,act=item.activity;
  html+=`<p class="detail-status"><i class="activity-dot" style="background:${ACTIVITY_COLOR[act]||FOOT_FALLBACK}"></i>${escapeHTML(nice(act||item.state))}</p>`;
  html+=`<p class="doing-now">${escapeHTML(ACTIVITY_TEXT[act]||nice(item.state))}${item.task_id?` Task ${escapeHTML(item.task_id)}.`:''}</p>`;
  if(act!==undefined)html+=row('Sensor footprint',r>0?`${r.toFixed(0)} m radius · ${area.toFixed(2)} ha`:'Not airborne')+row('Scan rate',r>0&&speed>0?`${rate.toFixed(1)} ha/min`:'—')+row('Ground speed',`${speed.toFixed(1)} m/s`);
  html+=row('State',nice(item.state))+row('Task',item.task_id||'No incident assignment')+row('Dock',item.dock_id||'—')+row('Radio link',item.link===false?'Lost':'Connected')+row('Battery',`${Math.round((item.battery||0)*100)}%`)+row('Energy remaining',`${Number(item.energy_wh||0).toFixed(1)} / ${Number(item.capacity_wh||0).toFixed(0)} Wh`)+row('Payload',nice(item.payload))+row('Supply stock',`${item.payload_stock??'—'} / ${item.payload_capacity??'—'}`)+row('Delivery',item.delivered?'Recorded delivered':'Not recorded delivered');
  if(item.link===false)html+='<p class="detail-warning">Radio link unavailable. Position and delivery status may be stale.</p>';
 }else if(selected.kind==='dock'){
  const ids=item.drone_ids||[];html+=row('Docked',String(item.docked??'—'))+row('Launched',String(item.launched??'—'))+row('Drones',String(ids.length))+`<div class="dock-drones">${ids.map(id=>`<button data-select="drone" data-id="${escapeHTML(id)}">${escapeHTML(id)}</button>`).join('')}</div>`;
 }else{
  html+=row('Agency',item.agency||nice(item.kind))+row('Vehicle',item.name||item.id)+row('Transport',nice(item.transport||'road'))+row('State',nice(item.state))+row('Incident',item.incident_id||'Unassigned')+row('Capacity',`${item.capacity??'—'} people`)+row('On board',`${item.load||0} people`)+row('Briefing',item.acknowledged?'Acknowledged':'Pending');
  if(item.transfer_destination)html+=row('Transfer destination',item.transfer_destination);
 }
 if(el._head!==head+selected.kind+selected.id){el._head=head+selected.kind+selected.id;el._html=null;el.innerHTML=head+'<div class="sel-body"></div>'}setHTML(el.querySelector('.sel-body'),html);
}

/* ====================== side panel ====================== */
function renderUI(){
 if(!state)return;uiDirty=false;
 const drones=state.drones||[],incidents=state.incidents||[],resources=state.resources||[],metrics=state.metrics||{};
 const scenario=typeof state.scenario==='object'&&state.scenario?state.scenario:{id:state.scenario};
 $('mission-title').textContent=scenario.title||scenario.name||nice(scenario.id)||'Monsoon response';
 if(scenario.id&&document.activeElement!==$('scenario-select'))$('scenario-select').value=scenario.id;
 $('scenario-description').textContent=scenario.description||SCENARIO_TEXT[scenario.id]||'Coordinate assessment, supplies and ground response.';
 const fs=$('fleet-select');const fleetN=metrics.fleet||drones.length;if(fs&&fleetN){if(![...fs.options].some(o=>o.value===String(fleetN)))fs.add(new Option(`${fleetN} drones`,String(fleetN)));if(document.activeElement!==fs)fs.value=String(fleetN)}
 const docksList=state.docks||[],ds=$('dock-summary');
 if(docksList.length){const hasAct=drones.some(d=>d.activity),docked=hasAct?drones.filter(d=>d.activity==='docked').length:docksList.reduce((n,d)=>n+(d.docked??0),0),airborne=metrics.launched??drones.filter(d=>d.activity&&!/docked|charging|failed/.test(d.activity)).length;ds.hidden=false;ds.textContent=`${docked} docked · ${airborne} airborne${metrics.dispatch_cap!=null?` (cap ${metrics.dispatch_cap})`:''}`}else ds.hidden=true;
 $('mission-time').textContent=clock(state.t);$('timeline-time').textContent=`T + ${clock(state.t)}`;$('pause').textContent=state.paused?'▶':'Ⅱ';$('pause').setAttribute('aria-label',state.paused?'Resume exercise':'Pause exercise');if(document.activeElement!==$('speed'))$('speed').value=String(state.speed||1);
 $('metric-people').textContent=metrics.people_assisted??metrics.people_rescued??metrics.rescued??incidents.filter(i=>i.status==='resolved').reduce((n,i)=>n+(i.people||0),0);
 $('metric-incidents').textContent=metrics.open_incidents??incidents.filter(i=>!['resolved','completed'].includes(i.status)).length;
 const resolvedN=incidents.filter(i=>['resolved','completed'].includes(i.status)).length,openN=incidents.length-resolvedN,issueLine=`Issues: ${incidents.length} total · ${openN} open · ${resolvedN} resolved`;
 $('issue-summary').textContent=issueLine;$('issues-line').textContent=issueLine;$('header-issues').textContent=`${openN} open / ${incidents.length}`;$('header-issues').title=issueLine;$('metric-incidents').textContent=openN;
 const operational=drones.filter(d=>d.active!==false&&!['crashed','offline','failed'].includes(d.state)).length;
 $('incident-count').textContent=incidents.length;$('header-active').textContent=operational;$('header-speed').textContent=`${state.speed||1}x`;$('fleet-count').textContent=`${drones.length} drones`;$('fleet-active').textContent=operational;$('fleet-energy').textContent=`${drones.length?Math.round(drones.reduce((n,d)=>n+(d.battery||0),0)/drones.length*100):0}% avg. charge`;$('resource-count').textContent=resources.length;
 patchList($('incident-list'),[...incidents].sort((a,b)=>(parseInt(String(b.id).replace(/\D/g,''))||0)-(parseInt(String(a.id).replace(/\D/g,''))||0)),i=>`<button class="incident-card ${selected?.id===i.id?'selected':''} ${['resolved','completed'].includes(i.status)?'resolved':''}" data-select="incident" data-id="${escapeHTML(i.id)}"><div class="card-top"><span class="badge">${['resolved','completed'].includes(i.status)?'✓':escapeHTML(nice(i.severity))}</span><small>${escapeHTML(i.id)}</small></div><strong>${escapeHTML(i.title||nice(i.category))}</strong><div class="card-bottom"><span>${(()=>{const c=peopleCounts(i.id);return c.total?`${i.people||c.total} people · ${c.waiting} waiting${c.safe?` · ${c.safe} safe`:''}`:`${i.people||0} people`})()}</span><span>${escapeHTML(nice(i.status))}</span></div></button>`,'<p class="muted">No incidents yet.</p>');
 patchList($('fleet-list'),drones,d=>`<button class="drone-card ${selected?.id===d.id?'selected':''}" data-select="drone" data-id="${escapeHTML(d.id)}"><div class="drone-top"><span><i class="capability-dot" style="background:${capabilityColor(d.payload)}"></i>${escapeHTML(d.id)}</span><span>${Math.round((d.battery||0)*100)}%</span></div><div class="drone-state"><i class="activity-dot" style="background:${ACTIVITY_COLOR[d.activity]||FOOT_FALLBACK}"></i>${escapeHTML(nice(d.activity||d.state))}</div><div class="battery-bar"><i style="width:${Math.round(Math.max(0,Math.min(100,(d.battery||0)*100)))}%;${d.battery<.25?'background:#eea17d':''}"></i></div></button>`);
 patchList($('resource-list'),resources,r=>`<div class="resource-card"><span class="resource-icon ${r.kind==='fire_engine'?'fire':''}">${/boat/i.test(r.transport||r.kind)?'◒':'▰'}</span><div><button class="resource-name" data-select="resource" data-id="${escapeHTML(r.id)}">${escapeHTML(r.name||`${r.id} · ${nice(r.kind)}`)}${!r.name&&r.transport==='boat'?' boat':''}</button><small>${escapeHTML(r.agency?r.agency+' · ':'')}<b class="unit-state ${escapeHTML(r.state)}">${escapeHTML(unitState(r))}</b>${r.incident_id?` · ${escapeHTML(r.incident_id)}`:''}</small></div><button data-release="${escapeHTML(r.id)}" title="${r.incident_id?'Committed until response completes':r.state==='available'?'Already available':'Make responder available'}" ${token&&connected&&!r.incident_id&&r.state!=='available'?'':'disabled'}>Release</button></div>`);
 if(document.activeElement!==$('fault-drone')){const old=$('fault-drone').value;setHTML($('fault-drone'),drones.map(d=>`<option value="${escapeHTML(d.id)}">${escapeHTML(d.id)}</option>`).join(''));if(drones.some(d=>d.id===old))$('fault-drone').value=old}
 const events=(state.events||[]).slice(-8).reverse();setHTML($('event-log'),events.map(e=>`<div><time>${clock(e.time)}</time>${escapeHTML(e.message)}</div>`).join('')||'Assessment underway. Waiting for activity.');
 const done=incidents.filter(i=>['resolved','completed'].includes(i.status)).length;$('timeline-progress').style.width=`${incidents.length?done/incidents.length*100:0}%`;
 renderSelection();
 const ag=metrics.agency,card=$('agency-card');if(ag){card.hidden=false;const t=v=>v==null?'—':v>=90?`${Math.floor(v/60)}m ${Math.round(v%60)}s`:`${Math.round(v)}s`,cell=(k,v)=>`<div><strong>${escapeHTML(v)}</strong><span>${escapeHTML(k)}</span></div>`;
  setHTML($('agency-grid'),cell('Requests accepted',`${ag.accepted??0} / ${ag.requests_received??0}`)+cell('Declined',ag.declined??0)+cell('Time to first drone',t(ag.median_time_to_first_drone_s))+cell('Time to briefing',t(ag.median_time_to_briefing_s))+cell('Resource requests',`${ag.resource_requests_fulfilled??0} / ${ag.resource_requests_raised??0}`)+cell('Supplies delivered',ag.supplies_delivered??0)+cell('Drone first',ag.drone_first??0)+cell('Ground first',ag.ground_first??0))}else card.hidden=true;
}

function setConnection(on,message){connected=on;$('connection').textContent=on?'Connected':'Connection interrupted';$('connection').classList.toggle('offline',!on);$('operator-role').textContent=token?'Operator':'View only';$('operator-role').title=token?'Authenticated exercise operator':'Open locally or use an operator control link to enable commands';const note=$('view-only-note');if(note)note.hidden=!!token;if(!token&&addMode)setAddMode(false);document.querySelectorAll('[data-control]').forEach(el=>el.disabled=!on||!token);$('scene-notice').classList.toggle('hidden',on);$('scene-notice').classList.toggle('stale',!!state);$('scene-notice').textContent=message||(state?'Connection interrupted · displaying last received state':'Connecting to the operations server…');if(state&&!on)renderUI();}

function clearRun(){
 disposeGroup(actorsGroup);disposeGroup(selGroup);disposeGroup(docksGroup);disposeGroup(footGroup);disposeGroup(coverageGroup);disposeGroup(trailGroup);disposeGroup(peopleGroup);disposeGroup(zoneGroup);peopleObjects.clear();zoneSignature='';
 for(const [id,r] of routeLines){routesGroup.remove(r.line);r.geo.dispose()}for(const [id,rec] of signalObjects)removeSignal(id,rec);
 droneObjects.clear();resourceObjects.clear();incidentObjects.clear();dockObjects.clear();footObjects.clear();routeLines.clear();seenSignals.clear();leadIn=null;trailLine=null;selSignature='';coverageSignature='';
}

function applySnapshot(packet){
 if(packet.type!=='snapshot'||!packet.state)return;const next=packet.state;if(next.schema_version!==2)return;if(run===next.run_id&&packet.seq<=sequence)return;
 if(run!==next.run_id){run=next.run_id;sequence=-1;selected=null;clearRun();if(!next.world)disposeGroup(worldGroup)}
 sequence=packet.seq;state=next;const now=performance.now();if(lastPacket){const gap=now-lastPacket;if(gap<2000)packetDur=Math.max(60,Math.min(500,packetDur*.7+gap*.3))}lastPacket=now;
 if(next.world)buildWorld(next.world);updateDynamicWorld();
 syncDocks();syncActors(state.drones,droneObjects,createDrone,'drone');syncActors(state.resources,resourceObjects,createResource,'resource');syncActors(state.incidents,incidentObjects,createIncident,'incident');
 for(const o of incidentObjects.values()){if(o.userData.item?.handed_over)o.userData.ring.material.color.set('#89d8a8')}
 syncFootprints();syncSignals();syncPeople();syncSearch();checkNewIncident();updateRoutes();uiDirty=true;
 if(now-lastUI>250||!connected){renderUI();lastUI=now}
 if(!connected)setConnection(true);
}

function toast(message){$('toast').textContent=message;$('toast').style.opacity=1;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').style.opacity=0,4500)}

function command(action,payload={}){if(!token||!connected||socket?.readyState!==WebSocket.OPEN){toast('An active operator session is required.');return}const id=crypto.randomUUID();socket.send(JSON.stringify({type:'command',id,action,expected_run_id:run,payload}));const timeout=setTimeout(()=>{pending.delete(id);toast('Command acknowledgment timed out.')},8000);pending.set(id,{action,timeout});}

async function connect(){const fragment=new URLSearchParams(location.hash.slice(1));const supplied=fragment.get('control');if(supplied){sessionStorage.setItem('ops-control',supplied);history.replaceState(null,'',location.pathname+location.search)}token=sessionStorage.getItem('ops-control')||null;try{const response=await fetch('/ops/session',{credentials:'same-origin',cache:'no-store'});if(response.ok){const session=await response.json();token=session.control_token||token||null;if(session.control_token)sessionStorage.setItem('ops-control',token)}}catch{}const url=new URL('/ops/ws',location.href);url.protocol=location.protocol==='https:'?'wss:':'ws:';if(token)url.searchParams.set('token',token);socket=new WebSocket(url);socket.onopen=()=>{retry=600};socket.onmessage=event=>{try{const packet=JSON.parse(event.data);if(packet.type==='ack'){const record=pending.get(packet.id);if(record){clearTimeout(record.timeout);pending.delete(packet.id);if(!packet.ok&&record.action==='fleet'){uiDirty=true;lastUI=0}toast(packet.ok?(record.action==='random_incident'?'Random issue raised':record.action==='detect'?'Issue placed on the map':`${nice(record.action)} accepted`):`${nice(record.action)} rejected: ${packet.error?.message||packet.error||'Command rejected'}`)}}else applySnapshot(packet)}catch(error){console.warn('Invalid operations message',error)}};socket.onclose=()=>{setConnection(false);setTimeout(connect,retry);retry=Math.min(retry*1.8,15000)};socket.onerror=()=>socket.close();}

/* ====================== people who need help ====================== */
const peopleGroup=new THREE.Group(),searchGroup=new THREE.Group(),zoneGroup=new THREE.Group();scene.add(peopleGroup,searchGroup,zoneGroup);
const peopleObjects=new Map();let peopleList=[],showSearch=false,searchSignature='',zoneSignature='',searchMesh=null;
const PERSON_SCALE=.25;const PERSON_COLOR={waiting:'#e5483a',assessed:'#e9a21c',supplied:'#e9a21c',being_rescued:'#7bc96f',evacuated:'#2e9e6b',hospitalised:'#2e9e6b'};
const personMats={},personMat=c=>personMats[c]||(personMats[c]=new THREE.MeshStandardMaterial({color:c,roughness:.7,emissive:c,emissiveIntensity:.25}));
const ringMats={},ringMat=c=>ringMats[c]||(ringMats[c]=new THREE.MeshBasicMaterial({color:c,transparent:true,opacity:.7,side:THREE.DoubleSide,depthWrite:false}));
const skinMat=material('#f0bf9a'),bodyGeo=new THREE.BoxGeometry(1.7,3.4,1.1),headGeo=new THREE.SphereGeometry(1,14,10),armGeo=new THREE.BoxGeometry(.6,2.6,.6),pRing=new THREE.RingGeometry(7,9,40).rotateX(-Math.PI/2);
function derivePeople(){
 const out=[];for(const inc of state.incidents||[]){const n=Math.min(12,Number(inc.people)||0),done=inc.status==='resolved'||inc.status==='completed'?n:Math.min(n,inc.evacuated_people||0),p=inc.position||[0,0,0];
  for(let k=0;k<n;k++){const a=k/Math.max(1,n)*Math.PI*2,r=n>1?2+(k%3)*1.6:0;out.push({id:`${inc.id}#${k}`,incident_id:inc.id,position:[p[0]+Math.cos(a)*r,p[1]+Math.sin(a)*r,p[2]||0],status:k<done?'evacuated':(inc.supplied?.length?'supplied':'waiting'),derived:true})}}
 return out;
}
function createPerson(person){
 const g=new THREE.Group(),body=new THREE.Mesh(bodyGeo,personMat('#e5483a')),head=new THREE.Mesh(headGeo,skinMat),ring=new THREE.Mesh(pRing,ringMat('#e5483a')),armL=new THREE.Mesh(armGeo,personMat('#e5483a')),armR=armL.clone();
 body.position.y=3.3;head.position.y=6;head.scale.setScalar(1.05);armL.position.set(-1.5,5.2,0);armR.position.set(1.5,5.2,0);armL.rotation.z=-.5;armR.rotation.z=.5;ring.position.y=.4;
 body.castShadow=true;g.add(body,head,armL,armR,ring);g.userData={body,armL,armR,ring,status:'',phase:Math.random()*6,vis:0,id:person.id};peopleGroup.add(g);return g;
}
function syncPeople(){
 peopleList=Array.isArray(state.people)?state.people:derivePeople();const ids=new Set(),firstOfIncident=new Set();
 for(const p of peopleList){
  if(!Array.isArray(p.position))continue;ids.add(p.id);let o=peopleObjects.get(p.id);if(!o){o=createPerson(p);peopleObjects.set(p.id,o);o.position.copy(vec(p.position));o.scale.setScalar(.001)}
  const u=o.userData;u.item=p;u.target=vec(p.position);
  if(u.status!==p.status){u.status=p.status;const c=PERSON_COLOR[p.status]||'#e5483a';u.body.material=u.armL.material=u.armR.material=personMat(c);u.ring.material=ringMat(c)}
  if(!u.help&&!firstOfIncident.has(p.incident_id)&&p.status==='waiting'){u.help=label('HELP','#e5483a',6);u.help.position.y=34;o.add(u.help)}
  if(p.status==='waiting')firstOfIncident.add(p.incident_id);
  if(u.help){u.help.userData.want=p.status==='waiting'}
 }
 for(const [id,o] of peopleObjects)if(!ids.has(id)){peopleGroup.remove(o);disposeGroup(o);peopleObjects.delete(id)}
}
function stepPeople(now,dt){
 for(const o of peopleObjects.values()){
  const u=o.userData,st=u.status,waiting=st==='waiting',gone=st==='evacuated'||st==='hospitalised',want=PERSON_SCALE*(gone?.6:1);
  o.position.lerp(u.target,1-Math.exp(-dt*4));u.vis+=(want-u.vis)*(1-Math.exp(-dt*4));o.scale.setScalar(Math.max(.001,u.vis));
  u.ring.material.opacity=waiting?.45+.35*Math.sin(now*.005+u.phase):.25;u.ring.scale.setScalar(waiting?1+.2*Math.sin(now*.005+u.phase):.7);
  u.armL.rotation.z=waiting?-2.4+Math.sin(now*.008+u.phase)*.35:-.5;u.armR.rotation.z=waiting?2.4-Math.sin(now*.008+u.phase)*.35:.5;
  if(u.help){const t=u.help.userData.want?1:0;u.help.material.opacity+=(t-u.help.material.opacity)*(1-Math.exp(-dt*6));u.help.visible=u.help.material.opacity>.03}
 }
}
const peopleCounts=id=>{const c={waiting:0,progress:0,safe:0,total:0};for(const p of peopleList){if(p.incident_id!==id)continue;c.total++;if(p.status==='waiting')c.waiting++;else if(p.status==='evacuated'||p.status==='hospitalised')c.safe++;else c.progress++}return c};

/* ====================== search map & drone zone ====================== */
function syncSearch(){
 const cells=state.search_map;const sig=showSearch&&Array.isArray(cells)?cells.map(c=>`${Math.round((c.probability||0)*100)}${c.last_observed==null?'':'o'}`).join(','):'off';
 if(sig===searchSignature)return;searchSignature=sig;
 if(sig==='off'){searchGroup.visible=false;return}
 const n=cells.length;if(!searchMesh||searchMesh.count!==n){if(searchMesh){searchGroup.remove(searchMesh);searchMesh.geometry.dispose();searchMesh.material.dispose();searchMesh.dispose()}
  const xs=[...new Set(cells.map(c=>Math.round(c.position[0])))].sort((a,b)=>a-b),ys=[...new Set(cells.map(c=>Math.round(c.position[1])))].sort((a,b)=>a-b),gap=a=>a.length>1?Math.min(...a.slice(1).map((v,i)=>v-a[i])):80;
  const cw=gap(xs),ch=gap(ys);searchMesh=new THREE.InstancedMesh(new THREE.PlaneGeometry(cw*.96,ch*.96).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({transparent:true,opacity:.5,depthWrite:false,depthTest:false,toneMapped:false,fog:false}),n);searchMesh.renderOrder=1;searchMesh.frustumCulled=false;searchGroup.add(searchMesh);
  const m=new THREE.Matrix4();cells.forEach((c,i)=>{const v=vec([c.position[0],c.position[1],0]);m.makeTranslation(v.x,v.y+1.4,v.z);searchMesh.setMatrixAt(i,m)});searchMesh.instanceMatrix.needsUpdate=true}
 const maxp=Math.max(1e-6,...cells.map(c=>c.probability||0)),hi=new THREE.Color('#5b2bb8'),lo=new THREE.Color('#efe9ff'),clear=new THREE.Color('#bfe3dc'),col=new THREE.Color();
 cells.forEach((c,i)=>{const p=(c.probability||0)/maxp;if(c.last_observed!=null&&p<.25)col.copy(clear).lerp(lo,.4);else col.copy(lo).lerp(hi,Math.pow(p,.7));searchMesh.setColorAt(i,col)});searchMesh.instanceColor.needsUpdate=true;searchGroup.visible=true;
}
function zonePoints(z){
 if(!z)return null;if(Array.isArray(z)){if(z.length===4&&z.every(v=>typeof v==='number'))return [[z[0],z[1]],[z[2],z[1]],[z[2],z[3]],[z[0],z[3]],[z[0],z[1]]];if(Array.isArray(z[0]))return [...z,z[0]]}
 if(z.bounds)return zonePoints(z.bounds);if(z.polygon)return zonePoints(z.polygon);if(z.center){const r=Number(z.radius)||60,pts=[];for(let i=0;i<=40;i++)pts.push([z.center[0]+Math.cos(i/40*Math.PI*2)*r,z.center[1]+Math.sin(i/40*Math.PI*2)*r]);return pts}
 if(z.min&&z.max)return zonePoints([z.min[0],z.min[1],z.max[0],z.max[1]]);return null;
}
function updateZone(){
 const d=selected?.kind==='drone'?(state.drones||[]).find(x=>x.id===selected.id):null,sig=d&&(d.zone||d.target)?d.id+JSON.stringify([d.zone,d.target]):'';
 if(sig===zoneSignature)return;zoneSignature=sig;disposeGroup(zoneGroup);if(!sig)return;
 const pts=zonePoints(d.zone);if(pts){const verts=[];for(let i=0;i<pts.length;i++)for(const f of [0,.25,.5,.75]){}const dense=[];for(let i=1;i<pts.length;i++){const a=pts[i-1],b=pts[i],st=Math.max(1,Math.ceil(Math.hypot(b[0]-a[0],b[1]-a[1])/20));for(let k=0;k<st;k++)dense.push(vec([a[0]+(b[0]-a[0])*k/st,a[1]+(b[1]-a[1])*k/st,3]))}dense.push(dense[0]);const l=new THREE.Line(new THREE.BufferGeometry().setFromPoints(dense),new THREE.LineDashedMaterial({color:'#5b2bb8',dashSize:12,gapSize:7,depthTest:false}));l.computeLineDistances();l.renderOrder=4;zoneGroup.add(l)}
 if(Array.isArray(d.target)){const v=vec(d.target),ring=new THREE.Mesh(new THREE.RingGeometry(9,12,40).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color:'#5b2bb8',side:THREE.DoubleSide,depthTest:false,transparent:true,opacity:.9})),dia=new THREE.Mesh(new THREE.OctahedronGeometry(5),new THREE.MeshBasicMaterial({color:'#5b2bb8',depthTest:false}));ring.position.copy(v);ring.position.y+=1.5;dia.position.copy(v);dia.position.y+=14;ring.renderOrder=dia.renderOrder=5;const tag=label('Next target','#5b2bb8',6);tag.position.copy(v);tag.position.y+=26;zoneGroup.add(ring,dia,tag)}
}

/* ====================== map layers ====================== */
let showLabels=true,showBuildings=true,showRoads=true;
function layerMeshes(){const b=[],r=[];const roadSet=new Set([mats.curb,mats.road,mats.stripe]);for(const o of worldGroup.children){if(!o.isMesh)continue;const m=Array.isArray(o.material)?o.material[0]:o.material;if(roadSet.has(m))r.push(o);else if(m.map||m===mats.roof||m===mats.darkRoof)b.push(o)}return{b,r}}
function applyLayers(){const {b,r}=layerMeshes();b.forEach(o=>o.visible=showBuildings);r.forEach(o=>o.visible=showRoads);if(showLabels)camera.layers.enable(2);else camera.layers.disable(2)}
for(const [id,key] of [['toggle-labels','Labels'],['toggle-buildings','Buildings'],['toggle-roads','Roads'],['toggle-search','Search']]){const el=$(id);if(el)el.onclick=()=>{const on=!el.classList.contains('active');el.classList.toggle('active',on);el.setAttribute('aria-pressed',String(on));if(key==='Labels')showLabels=on;if(key==='Buildings')showBuildings=on;if(key==='Roads')showRoads=on;if(key==='Search'){showSearch=on;searchSignature='';if(state)syncSearch()}applyLayers()}}

/* ====================== rescue issues ====================== */
const ISSUE_DEFAULT={medical:{title:'Operator-raised medical emergency',needs:['first_aid']},flood:{title:'Operator-raised flood rescue',needs:['rescue']},fire:{title:'Operator-raised fire',needs:['assessment']},trapped:{title:'Operator-raised trapped residents',needs:['rescue']},blocked_route:{title:'Operator-raised blocked route',needs:['assessment']}};
let addMode=false,pendingPoint=null,watchNew=null,issueMarker=null;
const popover=$('issue-popover');
function setAddMode(on){addMode=on&&!!token;$('add-issue').classList.toggle('active',addMode);$('add-issue').setAttribute('aria-pressed',String(addMode));$('issue-hint').hidden=!addMode;renderer.domElement.style.cursor=addMode?'crosshair':'';if(!addMode)closePopover()}
function closePopover(){popover.hidden=true;pendingPoint=null;if(issueMarker){scene.remove(issueMarker);issueMarker.geometry.dispose();issueMarker.material.dispose();issueMarker=null}}
function openPopover(point,e){
 pendingPoint=point;const rect=host.getBoundingClientRect();popover.hidden=false;popover.style.left=Math.max(8,Math.min(rect.width-250,e.clientX-rect.left+14))+'px';popover.style.top=Math.max(8,Math.min(rect.height-250,e.clientY-rect.top-20))+'px';
 const cat=$('issue-category').value||'medical';$('issue-pop-category').value=cat;$('issue-pop-people').value=cat==='flood'||cat==='trapped'?4:1;
 if(issueMarker){scene.remove(issueMarker);issueMarker.geometry.dispose();issueMarker.material.dispose()}issueMarker=new THREE.Mesh(new THREE.RingGeometry(3,4.2,32).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color:'#d0382a',side:THREE.DoubleSide,depthTest:false,transparent:true,opacity:.9}));issueMarker.position.copy(point).add(new THREE.Vector3(0,.6,0));issueMarker.renderOrder=6;scene.add(issueMarker);
}
function submitIssue(){
 if(!pendingPoint)return;const category=$('issue-pop-category').value,d=ISSUE_DEFAULT[category],people=Math.max(1,Math.min(500,Number($('issue-pop-people').value)||1)),severity=Number($('issue-pop-severity').value)||3,p=pendingPoint;
 const north=-p.z,z=Math.max(0,p.y-groundHeight(p.x,north));
 watchNew={ids:new Set((state?.incidents||[]).map(i=>i.id)),until:performance.now()+12000};
 command('detect',{position:[Math.round(p.x*10)/10,Math.round(north*10)/10,Math.round(z*10)/10],category,title:d.title,severity,people,needs:d.needs});setAddMode(false);
}
$('random-issue').onclick=()=>{const c=$('issue-category').value;watchNew={ids:new Set((state?.incidents||[]).map(i=>i.id)),until:performance.now()+12000};command('random_incident',c?{category:c}:{})};
$('add-issue').onclick=()=>setAddMode(!addMode);
$('issue-pop-place').onclick=submitIssue;$('issue-pop-cancel').onclick=()=>setAddMode(false);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&(addMode||!popover.hidden))setAddMode(false)});
function checkNewIncident(){if(!watchNew||!state)return;if(performance.now()>watchNew.until){watchNew=null;return}const fresh=(state.incidents||[]).find(i=>!watchNew.ids.has(i.id));if(fresh){watchNew=null;setTab('incidents');select('incident',fresh.id);toast(`New issue ${fresh.id}: ${fresh.title||nice(fresh.category)}`)}}

/* ====================== controls ====================== */
$('toggle-routes').onclick=()=>{showRoutes=!showRoutes;$('toggle-routes').classList.toggle('active',showRoutes);$('toggle-routes').setAttribute('aria-pressed',String(showRoutes));routesGroup.visible=showRoutes;trailGroup.visible=showRoutes;selSignature='';if(state)updateRoutes()};
$('toggle-coverage').onclick=()=>{showCoverage=!showCoverage;$('toggle-coverage').classList.toggle('active',showCoverage);$('toggle-coverage').setAttribute('aria-pressed',String(showCoverage));if(state)updateCoverage()};
$('randomize').onclick=()=>command('random_scenario');$('scenario-select').onchange=e=>command('scenario',{id:e.target.value});$('fleet-select').onchange=e=>{command('fleet',{value:Number(e.target.value)});e.target.blur()};
$('pause').onclick=()=>command(state?.paused?'resume':'pause');$('speed').onchange=e=>command('speed',{value:Number(e.target.value)});$('reset').onclick=()=>command('reset');
document.addEventListener('click',e=>{const selectButton=e.target.closest('[data-select]');if(selectButton)select(selectButton.dataset.select,selectButton.dataset.id);const fault=e.target.closest('[data-fault]');if(fault)command('fault',{drone_id:$('fault-drone').value,kind:fault.dataset.fault});const release=e.target.closest('[data-release]');if(release)command('release_resource',{resource_id:release.dataset.release});const handoff=e.target.closest('[data-handoff]');if(handoff)command('ack_handoff',{incident_id:handoff.dataset.handoff,resource_id:handoff.dataset.resource});if(e.target.closest('[data-focus]'))focusSelected();if(e.target.id==='close-selection'){selected=null;selSignature='';renderSelection();updateSelectedRoute()}});
$('camera-focus').onclick=focusSelected;

/* ====================== camera ====================== */
function flyCamera(toPos,toTarget,opts={}){
 if(toPos.y<groundHeight(toPos.x,-toPos.z)+4)toPos.y=groundHeight(toPos.x,-toPos.z)+4;
 flight={t0:performance.now(),dur:opts.dur??1100,fromPos:camera.position.clone(),fromTarget:controls.target.clone(),toPos,toTarget,entity:opts.entity||null,offset:opts.entity?toPos.clone().sub(toTarget):null};controls.enabled=false;
}
function flyTo(point,opts={}){
 const dist=opts.dist??50,dir=new THREE.Vector3(camera.position.x-point.x,0,camera.position.z-point.z);if(dir.lengthSq()<1)dir.set(1,0,1);dir.normalize();
 const elev=opts.elev??.38;flyCamera(point.clone().addScaledVector(dir,dist*Math.cos(elev)).add(new THREE.Vector3(0,dist*Math.sin(elev),0)),point.clone(),opts);
}
function stepFlight(now){
 if(!flight)return;let k=Math.min(1,(now-flight.t0)/flight.dur);const e=ease(k);
 if(flight.entity){flight.toTarget.copy(flight.entity.position);flight.toPos.copy(flight.toTarget).add(flight.offset);const g=groundHeight(flight.toPos.x,-flight.toPos.z)+4;if(flight.toPos.y<g)flight.toPos.y=g}
 camera.position.lerpVectors(flight.fromPos,flight.toPos,e);controls.target.lerpVectors(flight.fromTarget,flight.toTarget,e);
 if(k>=1){flight=null;controls.enabled=true}
}
function cancelFlight(){if(flight){flight=null;controls.enabled=true}}
function clampCamera(){
 const g=groundHeight(camera.position.x,-camera.position.z)+2.5;if(camera.position.y<g)camera.position.y=g;
 const tg=groundHeight(controls.target.x,-controls.target.z);if(controls.target.y<tg)controls.target.y=tg;
}
function view(mode){
 cameraMode=mode;document.querySelectorAll('.map-tools button').forEach(b=>b.classList.toggle('active',b.id===`camera-${mode}`));
 if(mode==='overview')flyCamera(new THREE.Vector3(870,1030,1000),new THREE.Vector3(0,15,0));
 if(mode==='top')flyCamera(new THREE.Vector3(0,1600,1),new THREE.Vector3(0,0,0));
 if(mode==='follow'){if(!selected&&state?.drones?.length){selected={kind:'drone',id:state.drones[0].id};renderSelection()}if(selected)frameEntity(selected.kind,selected.id)}
}['overview','top','follow'].forEach(mode=>$(`camera-${mode}`).onclick=()=>view(mode));

const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();let down=null;raycaster.layers.enableAll();
const canvasEl=renderer.domElement;
function setPointer(e){const rect=canvasEl.getBoundingClientRect();pointer.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,camera)}
const visibleChain=o=>{for(let n=o;n;n=n.parent)if(!n.visible)return false;return true};
function pickActor(){for(const hit of raycaster.intersectObjects([...actorsGroup.children,...docksGroup.children],true)){if(!visibleChain(hit.object))continue;let o=hit.object;while(o&&!o.userData.kind)o=o.parent;if(o?.userData.kind)return o}return null}
function pickGround(){
 const hits=raycaster.intersectObjects(worldGroup.children,true);if(hits.length)return hits[0].point.clone();
 const o=raycaster.ray.origin,d=raycaster.ray.direction;for(let t=0;t<9000;t+=4){const x=o.x+d.x*t,y=o.y+d.y*t,z=o.z+d.z*t;if(y<=groundHeight(x,-z))return new THREE.Vector3(x,groundHeight(x,-z),z)}return null;
}
canvasEl.addEventListener('pointerdown',e=>{down=[e.clientX,e.clientY];cancelFlight()});
canvasEl.addEventListener('wheel',cancelFlight,{passive:true});
canvasEl.addEventListener('pointerup',e=>{if(!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>5)return;setPointer(e);if(addMode){const p=pickGround();if(p)openPopover(p,e);return}const o=pickActor();if(o)select(o.userData.kind,o.userData.id)});
canvasEl.addEventListener('dblclick',e=>{setPointer(e);const o=pickActor();if(o){select(o.userData.kind,o.userData.id);return}const p=pickGround();if(p){cameraMode='free';document.querySelectorAll('.map-tools button').forEach(b=>b.classList.remove('active'));flyTo(p,{dist:48,elev:.3,dur:1200})}});
window.__ops={peopleObjects,searchGroup,camera,controls,groundHeight,get state(){return state},get selected(){return selected},get flying(){return !!flight},droneObjects,signalObjects,dockObjects,footObjects,routeLines};

new ResizeObserver(()=>{camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();renderer.setSize(host.clientWidth,host.clientHeight)}).observe(host);


/* ====================== frame loop ====================== */
let previous=performance.now();
function animate(now){
 requestAnimationFrame(animate);const dt=Math.min((now-previous)/1000,.05);previous=now;const live=connected&&!state?.paused;
 if(waterMaterial&&live)waterMaterial.uniforms.time.value+=dt;
 stepActors(now,dt,live);stepIncidentFX(now,live,dt);stepFootprints(dt);stepRouteAnchors();stepSelectedFX();stepSignals(now);stepPulses(now);stepPeople(now,dt);
 stepFlight(now);
 if(cameraMode==='follow'&&selected&&!flight){const o=droneObjects.get(selected.id)||resourceObjects.get(selected.id);if(o){const delta=o.position.clone().sub(controls.target);controls.target.lerp(o.position,.07);camera.position.addScaledVector(delta,.07)}}
 controls.update();clampCamera();
 if(uiDirty&&state&&now-lastUI>250){renderUI();lastUI=now}
 renderer.render(scene,camera);
 if(connected&&now-lastPacket>7000)setConnection(false,'No recent telemetry · displaying last received state');
}

setConnection(false);connect();requestAnimationFrame(animate);
