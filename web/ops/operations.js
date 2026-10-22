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

let state=null, run=null, sequence=-1, socket=null, token=null, publicDemo=false, connected=false, selected=null, cameraMode='overview', retry=600, lastPacket=0;

let lastUI=0;const pending=new Map(), droneObjects=new Map(), resourceObjects=new Map(), incidentObjects=new Map();

const host=$('scene'), scene=new THREE.Scene(); scene.background=new THREE.Color('#d8e3e5'); scene.fog=new THREE.FogExp2('#cbdce0',0.00009);

const camera=new THREE.PerspectiveCamera(43,1,.5,9000); camera.position.set(870,1030,1000);camera.layers.enable(2);

const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'high-performance'});renderer.setPixelRatio(Math.min(devicePixelRatio,1.6));renderer.setSize(host.clientWidth,host.clientHeight);renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.06;host.appendChild(renderer.domElement);

const controls=new OrbitControls(camera,renderer.domElement); controls.enableDamping=true;controls.dampingFactor=.07;controls.zoomToCursor=true;controls.minDistance=5;controls.maxDistance=2600;controls.zoomSpeed=1.15;controls.maxPolarAngle=Math.PI*.475;controls.target.set(0,15,0);

scene.add(new THREE.HemisphereLight('#e6f5f3','#586148',2.1));const sun=new THREE.DirectionalLight('#fff0d6',2.8);sun.position.set(-500,1000,400);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);Object.assign(sun.shadow.camera,{left:-850,right:850,top:850,bottom:-850,far:2200});sun.shadow.bias=-.0003;sun.shadow.normalBias=1.8;scene.add(sun,sun.target);

const worldGroup=new THREE.Group(), actorsGroup=new THREE.Group(), routesGroup=new THREE.Group(),dynamicGroup=new THREE.Group();scene.add(worldGroup,actorsGroup,routesGroup,dynamicGroup);

const material=(color,extra={})=>new THREE.MeshStandardMaterial({color,roughness:.85,...extra});

const mats={land:material('#adb998'),curb:material('#c5c6bb'),road:material('#69747b'),roof:material('#d1c8b3'),darkRoof:material('#627477'),drone:material('#e0e7df',{metalness:.35,roughness:.4}),arm:material('#263e46',{metalness:.6}),cyan:material('#4ca8ff',{emissive:'#308cf1',emissiveIntensity:1.3}),orange:material('#f18368',{emissive:'#e58a4a',emissiveIntensity:.7}),base:material('#668f85'),tree:material('#60845c'),trunk:material('#665c48'),stripe:material('#cad0b5')};

const boxGeo=new THREE.BoxGeometry(1,1,1), cylinderGeo=new THREE.CylinderGeometry(1,1,1,12);
const sharedMat=new Set(),sharedGeo=new Set();

function box(parent,x,y,z,w,h,d,mat){const mesh=new THREE.Mesh(boxGeo,mat);mesh.position.set(x,y,z);mesh.scale.set(w,h,d);mesh.castShadow=true;mesh.receiveShadow=true;parent.add(mesh);return mesh;}

// Dispose only resources owned by the removed subtree. Global and still-visible
// resources remain valid even when a transient group shares their materials.
function disposeGroup(group){
 const keepMaterials=new Set([...Object.values(mats),...sharedMat]),keepGeometry=new Set([boxGeo,cylinderGeo,...sharedGeo]),keepTextures=new Set();
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

let waterMaterial=null,lastWorldId=null;

function buildWorld(world){terrainData=world.terrain||null;worldData=world;dynamicSignature='';disposeGroup(worldGroup);disposeGroup(regionGroup);region.roadMeshes=[];const isRegion=world.kind==='region',firstOfWorld=lastWorldId!==world.id;lastWorldId=world.id;if(!isRegion){disposeGroup(wardGroup);disposeGroup(lifeGroup);region.lifeLabels=[];region.lifeBadges=[];region.wardLabels=[];procReset();region.on=false;region.segIndex=null}const bounds=world.bounds||[-600,-600,600,600];const width=bounds[2]-bounds[0],depth=bounds[3]-bounds[1],cx=(bounds[0]+bounds[2])/2,cz=-(bounds[1]+bounds[3])/2;terrainMesh(bounds);box(worldGroup,cx,-9,cz,width,18,depth,mats.land);box(worldGroup,cx,-25,cz,width+8,16,depth+8,material('#a5b8b5'));const grid=new THREE.GridHelper(Math.max(width,depth),24,'#b5cebb','#9bb49d');grid.position.y=.2;grid.material.transparent=true;grid.material.opacity=.11;if(!terrainData&&!isRegion)worldGroup.add(grid);

 const canvas=document.createElement('canvas');canvas.width=128;canvas.height=128;const ctx=canvas.getContext('2d');ctx.fillStyle='#afbbb7';ctx.fillRect(0,0,128,128);for(let y=10;y<128;y+=19)for(let x=7;x<128;x+=19){ctx.fillStyle=(x+y)%3?'#607a7e':'#adc4bd';ctx.fillRect(x,y,8,10);ctx.fillStyle='#879996';ctx.fillRect(x,y+10,9,2)}const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;texture.wrapS=texture.wrapT=THREE.RepeatWrapping;texture.repeat.set(.035,.035);

 const buildingMats=['#f0e4ce','#cdb78f','#d6a78d','#deded5','#bccbc5','#c8b19c'].map(c=>material(c,{map:texture}));

 for(const [index,b] of (world.buildings||[]).entries()){const fp=b.footprint;if(!fp?.length)continue;const shape=new THREE.Shape();fp.forEach((p,i)=>i?shape.lineTo(p[0],p[1]):shape.moveTo(p[0],p[1]));shape.closePath();const h=b.height||18;const g=new THREE.ExtrudeGeometry(shape,{depth:h,bevelEnabled:false});g.rotateX(-Math.PI/2);const mesh=new THREE.Mesh(g,[mats.roof,buildingMats[index%buildingMats.length]]);mesh.castShadow=true;mesh.receiveShadow=true;worldGroup.add(mesh);const x=fp.reduce((s,p)=>s+p[0],0)/fp.length,z=-fp.reduce((s,p)=>s+p[1],0)/fp.length;const foundation=groundHeight(x,-z);mesh.position.y=foundation;if(index%2===0)box(worldGroup,x,foundation+h+1.5,z,6,3,5,mats.darkRoof);if(index%3===0){const tank=new THREE.Mesh(cylinderGeo,mats.roof);tank.position.set(x+5,foundation+h+3,z+3);tank.scale.set(2,6,2);worldGroup.add(tank)} }

 for(const road of isRegion?[]:world.roads||[]){const pts=road.points||[];ribbon(pts,road.width||13,.5,mats.curb);ribbon(pts,(road.width||13)-2,.65,mats.road);for(let i=1;i<pts.length;i++){const a=vec(pts[i-1]),b=vec(pts[i]),dist=a.distanceTo(b);for(let k=3;k<dist;k+=15){const p=a.clone().lerp(b,k/dist);const dash=box(worldGroup,p.x,groundHeight(p.x,-p.z)+.76,p.z,1,.07,6,mats.stripe);dash.rotation.y=Math.atan2(b.x-a.x,b.z-a.z)}}if(road.blocked){const mid=vec(pts[Math.floor(pts.length/2)]);box(worldGroup,mid.x,mid.y+2,mid.z,16,4,2,mats.orange)}}

 const river=world.river?.points||world.river||[];if(Array.isArray(river)&&river.length){ribbon(river,(world.river_width||45)+8,.8,material('#577c78'));waterMaterial=new THREE.ShaderMaterial({transparent:true,side:THREE.DoubleSide,uniforms:{time:{value:0}},vertexShader:'varying vec2 vUv; void main(){vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',fragmentShader:'varying vec2 vUv; uniform float time; void main(){float w=sin(vUv.y*44.0+vUv.x*19.0-time*.65)*sin(vUv.x*72.0+vUv.y*18.0+time*.4);float edge=smoothstep(0.0,.16,vUv.x)*smoothstep(0.0,.16,1.0-vUv.x);vec3 c=mix(vec3(.24,.48,.48),vec3(.14,.34,.39),edge)+w*.035;gl_FragColor=vec4(c,.94);}'});ribbon(river,world.river_width||45,1.15,waterMaterial);}

 let seed=12;const rand=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296};const treePositions=[];for(let n=0;n<(isRegion?0:430);n++){const x=bounds[0]+rand()*width,z=-(bounds[1]+rand()*depth);let clear=true;for(const b of world.buildings||[]){if(b.footprint?.some(p=>Math.hypot(p[0]-x,-p[1]-z)<24)){clear=false;break}}if(clear&&(Math.abs(x)>450||Math.abs(z)>430||rand()<.25))treePositions.push([x,z,4+rand()*5])}const crowns=new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1,1),mats.tree,treePositions.length),trunks=new THREE.InstancedMesh(cylinderGeo,mats.trunk,treePositions.length);const dummy=new THREE.Object3D();treePositions.forEach(([x,z,s],i)=>{dummy.position.set(x,groundHeight(x,-z)+s+4,z);dummy.scale.set(s,s*1.3,s);dummy.updateMatrix();crowns.setMatrixAt(i,dummy.matrix);dummy.position.set(x,groundHeight(x,-z)+3,z);dummy.scale.set(1,6,1);dummy.updateMatrix();trunks.setMatrixAt(i,dummy.matrix)});crowns.castShadow=true;worldGroup.add(crowns,trunks);

 if(isRegion)buildRegion(world);applyWorldScale(world,firstOfWorld);
 for(const base of isRegion?[]:[...(world.bases||[]),...(world.shelters||[])]){const p=vec(base.position);const g=new THREE.Group();g.position.copy(p);worldGroup.add(g);box(g,0,1,0,55,2,45,mats.base);const ring=new THREE.Mesh(new THREE.RingGeometry(12,13,40),new THREE.MeshBasicMaterial({color:'#bbdfbf',side:THREE.DoubleSide}));ring.rotation.x=-Math.PI/2;ring.position.y=2.1;g.add(ring);box(g,0,2.2,0,14,.1,2,mats.drone);box(g,-6,2.2,0,2,.1,13,mats.drone);box(g,6,2.2,0,2,.1,13,mats.drone);const title=label(nice(base.kind||base.id).toUpperCase(),'#b6e0bf',9);title.position.set(0,20,0);g.add(title)}

 $('map-source').textContent=`${world.metadata?.source||world.source||'Geographic district'} / simulated flooding`;let attribution=$('osm-attribution');if(!attribution){attribution=document.createElement('a');attribution.id='osm-attribution';attribution.href='https://www.openstreetmap.org/copyright';attribution.target='_blank';attribution.rel='noopener noreferrer';attribution.style.cssText='position:absolute;right:20px;bottom:5px;color:#aec7ce;font-size:8px;text-decoration:none';host.parentElement.appendChild(attribution)}attribution.textContent=world.metadata?.attribution||'Geographic reference';batchStaticMeshes();applyLayers();
}



function batchStaticMeshes(){const buckets=new Map(),remove=[];worldGroup.updateMatrixWorld(true);worldGroup.traverse(o=>{if(!o.isMesh||o.isInstancedMesh||o.material?.transparent)return;const materials=Array.isArray(o.material)?o.material:[o.material];if(materials.some(m=>m.transparent||m.isShaderMaterial))return;const g=o.geometry.index?o.geometry.toNonIndexed():o.geometry.clone();g.applyMatrix4(o.matrixWorld);const groups=g.groups.length?g.groups:[{start:0,count:g.attributes.position.count,materialIndex:0}];for(const range of groups){const m=materials[range.materialIndex]||materials[0];if(!buckets.has(m))buckets.set(m,{position:[],normal:[],uv:[]});const bucket=buckets.get(m);for(let i=range.start;i<range.start+range.count;i++){for(const [name,size]of [['position',3],['normal',3],['uv',2]]){const attr=g.attributes[name];for(let j=0;j<size;j++)bucket[name].push(attr?attr.array[i*size+j]:0)}}}g.dispose();remove.push(o)});for(const o of remove){o.removeFromParent();if(o.geometry!==boxGeo&&o.geometry!==cylinderGeo)o.geometry.dispose()}for(const [m,b]of buckets){const g=new THREE.BufferGeometry();for(const [name,size]of [['position',3],['normal',3],['uv',2]])g.setAttribute(name,new THREE.Float32BufferAttribute(b[name],size));const mesh=new THREE.Mesh(g,m);mesh.castShadow=true;mesh.receiveShadow=true;worldGroup.add(mesh)}}


function updateDynamicWorld(){if(!worldData)return;const flood=state.flood||{},roads=state.road_conditions||[],signature=JSON.stringify([flood,roads]);if(signature===dynamicSignature)return;dynamicSignature=signature;disposeGroup(dynamicGroup);const river=worldData.river?.points||worldData.river||[];if(flood.enabled&&river.length)ribbon(river,Number(flood.width)||135,Number(flood.level)||2.1,new THREE.MeshBasicMaterial({color:'#4c9ccc',transparent:true,opacity:.3,side:THREE.DoubleSide,depthWrite:false}),dynamicGroup);const closureMat=new THREE.MeshBasicMaterial({color:'#e77660'});for(const road of roads){if(!road.blocked)continue;let pts=road.points;if(!pts&&road.id)pts=worldData.roads?.find(r=>r.id===road.id)?.points;if(!pts?.length)continue;const a=vec(pts[0]),b=vec(pts[pts.length-1]),p=a.clone().lerp(b,.5);const barrier=box(dynamicGroup,p.x,groundHeight(p.x,-p.z)+2.3,p.z,10,3,1.5,closureMat);barrier.rotation.y=Math.atan2(b.x-a.x,b.z-a.z);line([a.clone().add(new THREE.Vector3(0,2,0)),b.clone().add(new THREE.Vector3(0,2,0))],'#e77660',.55,dynamicGroup)}}
function createDrone(drone){const group=new THREE.Group();const capabilityMat=material(capabilityColor(drone.payload),{emissive:capabilityColor(drone.payload),emissiveIntensity:.25});box(group,0,0,0,4.8,2.1,6.3,capabilityMat);box(group,0,-1.7,1,2,1.4,2,mats.arm);const cargo=box(group,0,-3,0,2.8,1.5,2.8,mats.orange);const propellers=[],disks=[];for(const x of [-5,5])for(const z of [-5,5]){const arm=box(group,x/2,0,z/2,1,.7,8,mats.arm);arm.rotation.y=Math.atan2(x,z);const motor=new THREE.Mesh(cylinderGeo,mats.arm);motor.position.set(x,.5,z);motor.scale.set(.8,1.5,.8);group.add(motor);const prop=box(group,x,1.5,z,7,.12,.55,mats.drone);propellers.push(prop);const disk=new THREE.Mesh(new THREE.CircleGeometry(3.6,20),new THREE.MeshBasicMaterial({color:'#a2dcd3',transparent:true,opacity:.16,side:THREE.DoubleSide,depthWrite:false}));disk.rotation.x=-Math.PI/2;disk.position.set(x,1.6,z);group.add(disk);disks.push(disk);box(group,x,.3,z,.65,.5,.65,x<0?mats.cyan:mats.orange)}for(const x of [-2,2])box(group,x,-2,0,.5,2,5,mats.arm);const tag=label(drone.id,capabilityColor(drone.payload),5);tag.position.y=18;group.add(tag);mergeStatic(group,[cargo,tag,...propellers]);group.position.copy(vec(drone.position));group.scale.setScalar(region.on?.9:1.2);group.userData={kind:'drone',id:drone.id,target:vec(drone.position),propellers,disks,tag,cargo};actorsGroup.add(group);return group;}

/* ====================== responder vehicle models (front = +x, y up) ====================== */
const VM={},vm=(c,extra={})=>{const k=c+JSON.stringify(extra);if(!VM[k]){VM[k]=material(c,{roughness:.6,...extra});sharedMat.add(VM[k])}return VM[k]};
const lightMat=(c)=>vm(c,{emissive:c,emissiveIntensity:.9});
const wheelGeo=new THREE.CylinderGeometry(1,1,1,12).rotateX(Math.PI/2),coneGeo=new THREE.ConeGeometry(1,1,10).rotateZ(-Math.PI/2);sharedGeo.add(wheelGeo);sharedGeo.add(coneGeo);
function mergeStatic(g,keep=[]){
 const keepSet=new Set(keep),buckets=new Map(),rm=[],v=new THREE.Vector3();
 for(const c of g.children){if(!c.isMesh||keepSet.has(c)||Array.isArray(c.material)||c.material.transparent||!c.geometry.index)continue;c.updateMatrix();const geo=c.geometry;let b=buckets.get(c.material);if(!b)buckets.set(c.material,b={pos:[],nor:[],uv:[],idx:[],n:0});const m=c.matrix,nm=new THREE.Matrix3().getNormalMatrix(m),P=geo.attributes.position,N=geo.attributes.normal,U=geo.attributes.uv;
  for(let i=0;i<P.count;i++){v.fromBufferAttribute(P,i).applyMatrix4(m);b.pos.push(v.x,v.y,v.z);v.fromBufferAttribute(N,i).applyMatrix3(nm).normalize();b.nor.push(v.x,v.y,v.z);b.uv.push(U?U.getX(i):0,U?U.getY(i):0)}
  for(let i=0;i<geo.index.count;i++)b.idx.push(geo.index.getX(i)+b.n);b.n+=P.count;rm.push(c)}
 for(const c of rm)g.remove(c);
 for(const [mat,b] of buckets){const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.Float32BufferAttribute(b.pos,3));geo.setAttribute('normal',new THREE.Float32BufferAttribute(b.nor,3));geo.setAttribute('uv',new THREE.Float32BufferAttribute(b.uv,2));geo.setIndex(b.idx);const mesh=new THREE.Mesh(geo,mat);mesh.castShadow=true;mesh.receiveShadow=true;g.add(mesh)}
 return g;
}
function part(g,x,y,z,w,h,d,mat,rz=0){const m=box(g,x,y,z,w,h,d,mat);m.rotation.z=rz;return m}
function wheelAt(g,x,z,r=.55,w=.4){const m=new THREE.Mesh(wheelGeo,vm('#23282d'));m.position.set(x,r,z);m.scale.set(r,r,w);m.castShadow=true;g.add(m);return m}
function wheelsOf(g,xs,zOff,r,w){for(const x of xs)for(const s of [-1,1])wheelAt(g,x,s*zOff,r,w)}
function cyl(g,x,y,z,r,len,mat,axis='x'){const m=new THREE.Mesh(wheelGeo,mat);m.position.set(x,y,z);if(axis==='x'){m.rotation.y=Math.PI/2}m.scale.set(r,r,len);m.castShadow=true;g.add(m);return m}
function resourceKind(r){
 const t=String(r.model||r.kind||'').toLowerCase();
 if(r.transport==='boat'||/boat/.test(t))return 'boat';
 if(/fire/.test(t))return 'fire_engine';if(/ambul/.test(t))return 'ambulance';if(/rescue/.test(t))return 'rescue_team';if(/pump/.test(t))return 'pump';if(/jcb|excav|backhoe/.test(t))return 'jcb';
 if(/tank/.test(t))return 'water_tanker';if(/supply|truck/.test(t))return 'supply_truck';if(/bus/.test(t))return 'bus';return 'van';
}
const RESOURCE_COLOR={fire_engine:'#d63b32',ambulance:'#e5483a',boat:'#e08a2e',rescue_team:'#e9812a',pump:'#c9503a',jcb:'#f2b705',water_tanker:'#2f80ed',supply_truck:'#3f8f5a',bus:'#2e8b6a',van:'#8a949c'};
function buildVehicle(kind){
 const g=new THREE.Group();
 if(kind==='fire_engine'){
  part(g,-.6,1.9,0,8.4,2.5,2.6,vm('#d63b32'));part(g,3.7,2.2,0,2.4,3,2.6,vm('#d63b32'));part(g,4.95,2.6,0,.15,1.2,2.3,vm('#6f8fa3'));part(g,-.6,.9,0,8.4,.8,2.5,vm('#23282d'));
  part(g,-.5,3.5,.55,6.4,.28,.35,vm('#d8dde0'));part(g,-.5,3.5,-.55,6.4,.28,.35,vm('#d8dde0'));for(let i=0;i<7;i++)part(g,-3.3+i*.95,3.5,0,.14,.14,1.3,vm('#d8dde0'));
  part(g,-.6,1.5,1.32,8.2,.35,.1,vm('#f2f2ee'));part(g,3.6,3.85,0,.6,.35,2,lightMat('#2d7ff0'));part(g,3.6,3.85,.7,.6,.36,.5,lightMat('#e5483a'));wheelsOf(g,[-2.8,.6,3.9],1.35,.75,.5);
 }else if(kind==='ambulance'){
  part(g,-.8,2,0,5,2.8,2.3,vm('#f4f4f0'));part(g,2.4,1.55,0,2.2,1.9,2.3,vm('#f4f4f0'));part(g,3.5,1.9,0,.12,.9,2,vm('#6f8fa3'));part(g,-.8,1.2,1.16,5,.4,.08,vm('#e5483a'));part(g,-.8,2.3,1.16,1.5,.5,.06,vm('#d63b32'));part(g,-.8,2.3,1.17,.5,1.5,.06,vm('#d63b32'));
  part(g,-.8,2.3,-1.16,1.5,.5,.06,vm('#d63b32'));part(g,-.8,2.3,-1.17,.5,1.5,.06,vm('#d63b32'));part(g,1.8,3.5,0,.5,.3,1.8,lightMat('#2d7ff0'));part(g,-.8,3.45,0,1.4,.12,1.4,vm('#d63b32'));wheelsOf(g,[-2.2,2.3],1.05,.62,.4);
 }else if(kind==='rescue_team'){
  part(g,-.4,1.95,0,5.2,2.6,2.2,vm('#e9812a'));part(g,2.6,1.55,0,1.8,1.8,2.2,vm('#e9812a'));part(g,3.4,1.9,0,.12,.8,1.9,vm('#6f8fa3'));part(g,-.4,1.1,1.12,5.2,.3,.08,vm('#f2f2ee'));
  part(g,-.4,3.4,.7,4,.12,.12,vm('#23282d'));part(g,-.4,3.4,-.7,4,.12,.12,vm('#23282d'));part(g,-.8,3.55,0,1.4,.28,.7,vm('#d8dde0'));part(g,2.5,2.65,0,.5,.3,1.6,lightMat('#e5483a'));wheelsOf(g,[-1.7,2.4],1.0,.6,.4);
 }else if(kind==='pump'){
  part(g,0,1.2,0,3.4,1.4,1.8,vm('#c9503a'));part(g,-.3,2.15,0,1.4,.9,1.2,vm('#8a949c'));cyl(g,.9,1.5,0,.55,1.7,vm('#2f4a5a'),'x');part(g,-2.9,.95,0,2.4,.18,.18,vm('#23282d'));part(g,2.2,.5,0,.1,.1,.1,vm('#23282d'));wheelAt(g,0,1.0,.65,.35);wheelAt(g,0,-1.0,.65,.35);
 }else if(kind==='jcb'){
  part(g,-.4,1.5,0,3.6,1.5,2.1,vm('#f2b705'));part(g,-.3,2.7,0,1.9,1.5,1.9,vm('#f2b705'));part(g,.5,2.75,0,.1,1.0,1.7,vm('#6f8fa3'));part(g,-1.2,3.05,0,.1,1.1,1.7,vm('#6f8fa3'));
  part(g,2.2,1.9,.55,2.2,.35,.35,vm('#f2b705'),.35);part(g,2.2,1.9,-.55,2.2,.35,.35,vm('#f2b705'),.35);part(g,3.5,.95,0,1.0,.8,2.4,vm('#4d5560'));part(g,-2.5,2.0,0,2.4,.35,.45,vm('#f2b705'),-.5);part(g,-3.7,1.2,0,.8,1.1,.8,vm('#4d5560'));
  wheelAt(g,1.3,1.1,.85,.6);wheelAt(g,1.3,-1.1,.85,.6);wheelAt(g,-1.3,1.1,.6,.5);wheelAt(g,-1.3,-1.1,.6,.5);
 }else if(kind==='water_tanker'){
  part(g,-.2,1.0,0,6.8,.7,2.2,vm('#23282d'));cyl(g,-.4,2.25,0,1.3,6.2,vm('#8fb4c9',{metalness:.45,roughness:.35}),'x');part(g,3.6,1.9,0,2,2.2,2.2,vm('#2f80ed'));part(g,4.62,2.3,0,.1,.9,1.9,vm('#6f8fa3'));part(g,3.6,3.15,0,.5,.25,1.6,lightMat('#e9a21c'));wheelsOf(g,[-2.6,-1,3.4],1.1,.7,.5);
 }else if(kind==='supply_truck'){
  part(g,-.9,2.2,0,6,3,2.4,vm('#e8e4d6'));part(g,-.9,2.0,1.22,5.6,.4,.06,vm('#3f8f5a'));part(g,3,1.65,0,2,2.1,2.4,vm('#3f8f5a'));part(g,4.02,2.0,0,.1,.9,2.1,vm('#6f8fa3'));part(g,-.9,.9,0,6.4,.6,2.2,vm('#23282d'));wheelsOf(g,[-3,.2,3.4],1.15,.7,.5);
 }else if(kind==='bus'){
  part(g,0,1.8,0,10.8,2.7,2.5,vm('#2e8b6a'));part(g,0,2.45,0,10.2,.9,2.55,vm('#2c3a44'));part(g,0,3.28,0,10.8,.12,2.5,vm('#d8dde0'));part(g,5.45,2.0,0,.1,1.2,2.3,vm('#2c3a44'));part(g,0,1.1,0,10.8,.2,2.52,vm('#f2f2ee'));wheelsOf(g,[-3.6,3.6],1.2,.7,.5);
 }else if(kind==='boat'){
  part(g,-.8,.8,0,8,1.4,3.4,vm('#f2f2ee'));const bow=new THREE.Mesh(coneGeo,vm('#f2f2ee'));bow.position.set(4.2,.8,0);bow.scale.set(3,1.4,1.8);bow.castShadow=true;g.add(bow);
  part(g,-.8,.25,0,8.2,.5,3.5,vm('#e08a2e'));part(g,-1.8,2.0,0,2.6,1.4,2.2,vm('#e8e4d6'));part(g,-.7,2.1,0,.1,.7,1.9,vm('#6f8fa3'));part(g,-1.8,2.85,0,.5,.3,1.4,lightMat('#e5483a'));part(g,-5.2,.9,0,.8,1.1,.8,vm('#23282d'));
 }else{
  part(g,0,1.5,0,4.6,1.8,2,vm('#8a949c'));part(g,.4,2.5,0,2.4,.9,1.8,vm('#6f8fa3'));wheelsOf(g,[-1.4,1.5],.95,.55,.4);
 }
 return mergeStatic(g);
}
function createResource(resource){
 const kind=resourceKind(resource),g=buildVehicle(kind),big=kind==='bus'?1.2:1.35;
 const tag=label(String(resource.id),RESOURCE_COLOR[kind]||'#e2d4b8',5);tag.position.y=kind==='bus'?7:6.5;tag.userData.s0=tag.scale.clone();tag.userData.prio=3;g.add(tag);
 g.position.copy(vec(resource.position));g.scale.setScalar(big);g.userData={kind:'resource',id:resource.id,vkind:kind,target:vec(resource.position),tag,tagText:String(resource.id)};actorsGroup.add(g);return g;
}

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
const SCENARIO_TEXT={rooftop_flood:'Residents are stranded on rooftops as the river rises. Find them, brief ground teams and rescue by boat.',medical_supply:'Critical medical supplies must reach cut-off patients while the flood isolates the district.',blocked_bridge:'A blocked bridge forces evacuation by alternative routes while drones keep watch.',pccoe_pawana_night:'Night flood along the Pawana river in the PCMC region: coordinate drones, responders and lifelines across the wards.',pcmc_random:'Random incidents across the PCMC region. Drones scout, responders and the command center coordinate by message.',dock_fire:'Fires break out near the drone docks. Launch drones in waves, scout the fires and direct fire engines and rescue teams.'};
const SIGNAL_KIND={sos:['SOS','#e5533d'],briefing:['Briefing','#2f80ed'],ack:['Acknowledged','#2e9e6b'],recall:['Recall','#8a6fd1'],dispatch:['Dispatch','#e08a2e'],arrival:['Arrived','#1f9d8f'],supply_request:['Supply request','#c2528a'],resource_request:['Needs resource','#d9822b'],dispatch_order:['Dispatch order','#e08a2e']};
const kindMap=()=>({drone:droneObjects,resource:resourceObjects,incident:incidentObjects,dock:dockObjects});
const itemsOf=kind=>kind==='drone'?state?.drones:kind==='resource'?state?.resources:kind==='dock'?state?.docks:state?.incidents;

/* ====================== extra scene groups ====================== */
const footGroup=new THREE.Group(),coverageGroup=new THREE.Group(),docksGroup=new THREE.Group(),signalsGroup=new THREE.Group(),selGroup=new THREE.Group(),trailGroup=new THREE.Group();
scene.add(footGroup,coverageGroup,docksGroup,signalsGroup,selGroup,trailGroup);
const dockObjects=new Map(),footObjects=new Map(),routeLines=new Map(),signalObjects=new Map(),seenSignals=new Map();
const MAXR=400;let packetDur=140,uiDirty=false,selSignature='',coverageSignature='',flight=null;

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
 const boost=region.on?Math.max(1,Math.min(14,camDist/320)):1;
 for(const map of [droneObjects,resourceObjects,incidentObjects]){
  for(const [id,o] of map){
   const u=o.userData,item=u.item;
   if(u.target){const k=Math.min(1,(now-u.t0)/(packetDur*1.1));o.position.lerpVectors(u.from,u.target,k)}
   const hidden=!!u.leaving||(u.kind==='incident'&&item?.status==='resolved');
   u.vis+=((hidden?0:1)-u.vis)*(1-Math.exp(-dt*(hidden?4:6)));
   if(u.vis<.015&&hidden){o.visible=false;if(u.leaving&&now-u.leaving>700){actorsGroup.remove(o);disposeGroup(o);map.delete(id)}continue}
   o.visible=true;o.scale.setScalar(Math.max(.0001,u.base*(u.kind==='incident'?Math.sqrt(boost):boost)*ease(Math.min(1,u.vis))));
   if(u.kind==='resource'){const dx=u.target.x-u.from.x,dn=-(u.target.z-u.from.z);if(dx*dx+dn*dn>.5)u.hg=Math.atan2(dn,dx);else if(u.hg==null&&item?.heading!=null){let h=Number(item.heading)||0;if(Math.abs(h)>Math.PI*2)h*=Math.PI/180;u.hg=h}if(u.hg!=null){if(u.hgInit==null){o.rotation.y=u.hg;u.hgInit=1}else o.rotation.y+=angDiff(o.rotation.y,u.hg)*(1-Math.exp(-dt*7))}}
   else if(item?.heading!=null&&u.kind!=='incident'){let heading=-item.heading;if(Math.abs(heading)>Math.PI*2)heading*=Math.PI/180;o.rotation.y=heading}
   if(u.disks){const far=camDist>1100;if(far===u.partsVisible){u.partsVisible=!far;u.propellers.forEach(p=>p.visible=!far);u.disks.forEach(d=>d.visible=!far)}else if(u.partsVisible==null){u.partsVisible=true}}
   if(u.propellers&&live&&item?.active!==false&&!/docked|charging|failed/.test(item?.activity||'')&&!/crash|land|idle|charg/i.test(item?.state||''))u.propellers.forEach(p=>p.rotation.y+=dt*65);
   if(u.tag){compensateTag(o,u);if(u.kind==='drone'){const want=(!!item?.task_id||(selected?.kind==='drone'&&selected.id===id));u.tag.material.opacity+=((want?1:0)-u.tag.material.opacity)*(1-Math.exp(-dt*8));u.tag.visible=u.tag.material.opacity>.03&&!u.tag.userData.dc}}
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
const lmCache={};const lineMat=(color,dash,op)=>{const k=color+dash+op;if(!lmCache[k]){lmCache[k]=dash?new THREE.LineDashedMaterial({color,dashSize:9,gapSize:7,transparent:true,opacity:op}):new THREE.LineBasicMaterial({color,transparent:true,opacity:op});sharedMat.add(lmCache[k])}return lmCache[k]};
function routeLineFor(key){let r=routeLines.get(key);if(!r){const geo=new THREE.BufferGeometry(),arr=new Float32Array(MAXR*3);geo.setAttribute('position',new THREE.BufferAttribute(arr,3));geo.setDrawRange(0,0);const l=new THREE.Line(geo,routeMat);l.frustumCulled=false;routesGroup.add(l);r={line:l,geo,arr,cap:MAXR,n:0,owner:null,sig:'',anchored:false,dashed:false};routeLines.set(key,r)}return r}
function setLine(key,owner,pts,mat,dashed,anchored){
 const r=routeLineFor(key);r.owner=owner;
 if(!pts||!pts.length||(anchored&&!owner)){r.n=0;r.geo.setDrawRange(0,0);r.sig='';r.line.visible=false;return}
 const off=anchored?1:0,need=pts.length+off;
 if(need>r.cap){r.cap=need+64;r.arr=new Float32Array(r.cap*3);r.geo.setAttribute('position',new THREE.BufferAttribute(r.arr,3));r.sig=''}
 const sig=mat.uuid+'|'+pts.map(p=>p.x.toFixed(1)+','+p.y.toFixed(1)+','+p.z.toFixed(1)).join(';');
 if(sig!==r.sig){r.sig=sig;pts.forEach((p,i)=>{r.arr[(i+off)*3]=p.x;r.arr[(i+off)*3+1]=p.y;r.arr[(i+off)*3+2]=p.z});r.n=need;r.geo.attributes.position.needsUpdate=true}
 r.anchored=anchored;r.dashed=dashed;r.line.material=mat;r.line.visible=true;r.geo.setDrawRange(0,r.n);if(dashed&&!anchored)r.line.computeLineDistances();
}
// drone paths are drawn at flight altitude; only the vertical drop line at the hover point ever reaches the ground
function airPts(d,arr){const alt=Math.max(20,Number(d.position?.[2])||0);return (arr||[]).filter(p=>Array.isArray(p)).map(p=>{const v=vec(p);v.y=Math.max(v.y,groundHeight(p[0],p[1])+alt);return v})}
const droneKind=d=>d.route_kind||(d.task_id?'mission':'sweep');
function updateRoutes(){
 const keep=new Set();
 for(const d of state.drones||[]){
  const kind=droneKind(d),airborne=d.activity!=='docked'&&d.state!=='crashed',o=droneObjects.get(d.id),sel=selected?.kind==='drone'&&selected.id===d.id;
  keep.add('d:'+d.id);keep.add('s:'+d.id);
  if(airborne&&kind==='mission')setLine('d:'+d.id,o,airPts(d,d.route),lineMat('#1d1d1f',false,.75),false,true);
  else if(airborne&&kind==='return')setLine('d:'+d.id,o,airPts(d,d.route),lineMat('#8e8e93',true,.85),true,true);
  else setLine('d:'+d.id,null,null);
  const sw=Array.isArray(d.sweep)&&d.sweep.length?d.sweep:(kind==='sweep'&&airborne?d.route:null);
  setLine('s:'+d.id,null,airborne&&sw&&(sel||showSearch)?airPts(d,sw):null,lineMat('#6e6e73',false,sel?.55:.3),false,false);
 }
 for(const r of state.resources||[]){keep.add('r:'+r.id);const o=resourceObjects.get(r.id),pts=(r.route||[]).filter(p=>Array.isArray(p)).map(p=>{const v=vec(p);v.y+=.8;return v});setLine('r:'+r.id,o,pts,lineMat(RESOURCE_COLOR[resourceKind(r)]||'#0066cc',false,.9),false,true)}
 for(const [k,r] of routeLines)if(!keep.has(k)){routesGroup.remove(r.line);r.geo.dispose();routeLines.delete(k)}
 routesGroup.visible=showRoutes;updateCoverage();updateSelectedRoute();
}
function stepRouteAnchors(){
 for(const r of routeLines.values()){if(!r.n||!r.owner||!r.anchored)continue;r.arr[0]=r.owner.position.x;r.arr[1]=r.owner.position.y;r.arr[2]=r.owner.position.z;r.geo.attributes.position.needsUpdate=true;if(r.dashed)r.line.computeLineDistances()}
}
let leadIn=null,trailLine=null;
function updateSelectedRoute(){
 updateZone();
 const d=selected?.kind==='drone'?(state.drones||[]).find(x=>x.id===selected.id):null;
 const kind=d?droneKind(d):'',sig=d&&showRoutes&&d.route?.length&&d.activity!=='docked'&&kind!=='sweep'?d.id+kind+JSON.stringify(d.route)+(d.position?.[2]|0):'';
 if(sig===selSignature)return;selSignature=sig;disposeGroup(selGroup);leadIn=null;if(!sig)return;
 const black=new THREE.MeshBasicMaterial({color:kind==='return'?'#8e8e93':ROUTE_COLOR}),pts=airPts(d,d.route);
const tubeR=region.on?Math.max(.7,Math.min(6,camDist/900)):.7;
 for(let i=1;i<pts.length;i++){const a=pts[i-1],b=pts[i];if(a.distanceToSquared(b)<1e-4)continue;selGroup.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.LineCurve3(a,b),1,tubeR,6,false),black))}
 leadIn=new THREE.Mesh(new THREE.CylinderGeometry(.7,.7,1,6),black);leadIn.userData.to=pts[0].clone();selGroup.add(leadIn);
 pts.forEach((p,index)=>{const node=new THREE.Mesh(new THREE.SphereGeometry(2.4,12,8),black);node.position.copy(p);selGroup.add(node);const tag=label(String(index+1),capabilityColor(d.payload),5);tag.position.copy(p).add(new THREE.Vector3(0,9,0));selGroup.add(tag)});
 // stand-off hover point (mission only): ring in the air plus the single vertical drop line to the ground
 if(kind==='mission'&&pts.length){const hover=Array.isArray(d.standoff)?airPts(d,[d.standoff])[0]:pts[pts.length-1];
 const hr=new THREE.Mesh(new THREE.RingGeometry(6,8,32).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color:'#ffffff',side:THREE.DoubleSide,depthTest:false,transparent:true,opacity:.95}));hr.position.copy(hover);hr.renderOrder=6;selGroup.add(hr);
 const hs=new THREE.Mesh(new THREE.RingGeometry(3.2,4.6,24).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color:'#000000',side:THREE.DoubleSide,depthTest:false}));hs.position.copy(hover);hs.renderOrder=7;selGroup.add(hs);
 const htag=label('Hover point','#000000',5);htag.position.copy(hover).add(new THREE.Vector3(0,24,0));selGroup.add(htag);
 const g0=groundHeight(hover.x,-hover.z)+.8,hh=Math.max(1,hover.y-g0),pole=new THREE.Mesh(new THREE.CylinderGeometry(.45,.45,hh,6),new THREE.MeshBasicMaterial({color:'#000000'}));pole.position.set(hover.x,g0+hh/2,hover.z);selGroup.add(pole);
 const gr=new THREE.Mesh(new THREE.RingGeometry(3.2,4.4,28).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color:'#000000',side:THREE.DoubleSide,depthTest:false,transparent:true,opacity:.85}));gr.position.set(hover.x,g0+.3,hover.z);gr.renderOrder=7;selGroup.add(gr)}
}
function stepSelectedFX(){
 const o=selected?.kind==='drone'?droneObjects.get(selected.id):null;
 if(leadIn&&o){const a=o.position,b=leadIn.userData.to,dir=b.clone().sub(a),len=dir.length();leadIn.visible=len>.5;if(len>.5){leadIn.position.copy(a).addScaledVector(dir,.5);leadIn.scale.set(1,len,1);leadIn.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),dir.normalize())}}
 if(!o||!showRoutes){if(trailLine)trailLine.visible=false;return}
 if(!trailLine){const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.BufferAttribute(new Float32Array(42*3),3));geo.setAttribute('color',new THREE.BufferAttribute(new Float32Array(42*3),3));trailLine=new THREE.Line(geo,new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:.9,depthTest:false}));trailLine.frustumCulled=false;trailLine.renderOrder=4;trailGroup.add(trailLine)}
 const tr=[...(o.userData.trail||[]),[o.position.x,o.position.y,o.position.z]].filter(q=>q[1]>groundHeight(q[0],-q[2])+12),pa=trailLine.geometry.attributes.position,ca=trailLine.geometry.attributes.color;
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
function frameEntity(kind,id){const o=kindMap()[kind]?.get(id);if(!o)return;flyTo(o.position,{dist:kind==='incident'?170:kind==='dock'?180:kind==='resource'?130:150,entity:o,dur:1500})}
function select(kind,id){selected={kind,id};cameraMode='focus';frameEntity(kind,id);selSignature='';renderUI();updateSelectedRoute();refreshResTags()}
function focusSelected(){if(selected)frameEntity(selected.kind,selected.id);else toast('Select a drone, incident, responder or dock first.')}

const isOrch=i=>!!i&&(i.control_mode==='orchestrated'||/disruptionops/i.test(String(i.assigned_by||'')));
const orchIncidents=new Set(),orchActors=new Set();
function syncOrch(){orchIncidents.clear();orchActors.clear();
 for(const i of state.incidents||[])if(isOrch(i)&&!['resolved','completed'].includes(i.status)){orchIncidents.add(i.id);for(const id of [i.drone_id,...(i.assigned_drones||[]),...(i.assigned_units||[])])if(id)orchActors.add(id);for(const r of state.resources||[])if(r.incident_id===i.id)orchActors.add(r.id)}
 for(const map of [droneObjects,resourceObjects,incidentObjects])for(const [id,o] of map){const on=orchActors.has(id)||orchIncidents.has(id);let ring=o.userData.orch;
  if(on&&!ring){const radius=o.userData.kind==='incident'?20:o.userData.kind==='drone'?9:8;ring=new THREE.Mesh(new THREE.RingGeometry(radius,radius+1.4,40).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color:'#0066cc',transparent:true,opacity:.95,depthTest:false,side:THREE.DoubleSide}));ring.position.y=o.userData.kind==='drone'?-4:.6;ring.renderOrder=8;o.add(ring);o.userData.orch=ring}
  if(ring)ring.visible=on;
  if(o.userData.kind==='incident'){if(on&&!o.userData.orchTag){const tag=label('Assigned by DisruptionOps','#0066cc',6);tag.position.y=104;tag.userData.s0=tag.scale.clone();o.add(tag);o.userData.orchTag=tag}if(o.userData.orchTag)o.userData.orchTag.visible=on}}
}
function renderSelection(){
 const el=$('selection');if(!selected){el.classList.add('hidden');return}
 const item=itemsOf(selected.kind)?.find(x=>x.id===selected.id);if(!item){el.classList.add('hidden');return}
 el.classList.remove('hidden');
 const list=values=>(values||[]).map(nice).join(', ')||'None';const row=(name,value)=>`<div class="detail-row"><span>${escapeHTML(name)}</span><strong>${escapeHTML(value)}</strong></div>`;
 let html='';const head=`<div class="detail-heading"><h3>${escapeHTML(item.title||item.name||item.id)}</h3><span class="detail-actions"><button data-focus title="Fly the camera to this item">Focus</button><button id="close-selection" aria-label="Close details">×</button></span></div>`;
 if(selected.kind==='incident'){
  const assigned=(state.resources||[]).filter(r=>r.incident_id===item.id),unmet=(item.needs||[]).filter(n=>!(item.supplied||[]).includes(n)),remaining=(item.required_kinds||[]).filter(n=>!(item.completed_kinds||[]).includes(n)),observed=item.last_observed??item.briefing?.observed_at;
  if(isOrch(item))html+='<p class="orch-badge">Assigned by DisruptionOps</p>';
  html+=`<p class="detail-status">${escapeHTML(nice(item.category))} / ${escapeHTML(nice(item.status))} / Priority ${escapeHTML(item.severity)}</p>`;
  html+=row('People',(()=>{const c=peopleCounts(item.id);return c.total?`${c.waiting} waiting · ${c.progress} being helped · ${c.safe} safe`:`${item.people||0}`})())+row('Evacuated',`${item.evacuated_people||0} / ${item.people||0} people`)+row('Still needed',list(unmet))+row('Supplied',list(item.supplied))+row('Required response',list(item.required_kinds))+row('Completed response',list(item.completed_kinds))+row('Outstanding response',list(remaining))+row('Assigned drone',item.drone_id||'Awaiting assignment')+row('First on scene',item.first_on_scene?nice(item.first_on_scene):'Nobody yet'); 
  {const sr=[...(state.messages||[])].reverse().find(m=>m.kind==='situation_report'&&m.incident_id===item.id);
   if(sr)html+=`<div class="sit-card"><h4><span>Latest situation report</span><time>${clock(sr.t)}</time></h4>${sitInner(sr.body||{})}</div>`;
   const orders=item.resource_requests||[];
   html+=`<div class="sit-card"><h4><span>Dispatch orders</span></h4>${orders.length?`<div class="dsp-list">${orders.map(r=>{const ag=agencyOf(r.ordered_to||r.needed_kind);return `<div style="--c:${ag?ag[1]:'#8e8e93'}"><i></i><span><b>${escapeHTML(r.qty)}&times; ${escapeHTML(nice(r.needed_kind))}</b> from ${escapeHTML(nice(r.ordered_to||'agency'))}, ordered by ${escapeHTML(nice(r.ordered_by||'command center'))}${r.urgency?` &middot; ${escapeHTML(nice(r.urgency))}`:''} &middot; ${escapeHTML(nice(r.status))}${r.fulfilled_by?.length?` &rarr; ${escapeHTML(r.fulfilled_by.join(', '))}`:''}</span></div>`}).join('')}</div>`:'<p class="muted" style="margin:0">None yet. The command center has not ordered any unit.</p>'}</div>`}
  html+='<div class="detail-divider"></div>';
  html+=row('Assigned agencies',assigned.map(r=>`${r.name||r.agency||nice(r.kind)} (${r.id}${r.transport==='boat'?', boat':''})`).join(', ')||'Awaiting dispatch')+row('Ground briefing',item.handed_over?'Acknowledged':'Pending acknowledgment')+row('Last observation',observed==null?'Not yet observed':`${Math.max(0,Math.floor(state.t-observed))}s ago`)+row('Briefing source',item.briefing?.source||'No briefing yet');
  if(item.briefing?.summary)html+=`<p>${escapeHTML(item.briefing.summary)}</p>`;
  if(item.uncertain_delivery||item.uncertainty_delivery)html+='<p class="detail-warning">Delivery is unconfirmed after a telemetry interruption. Supplies must not be assumed delivered.</p>';
  if(item.wait_reason)html+=`<p class="detail-warning">${escapeHTML(nice(item.wait_reason))}</p>`;
  for(const r of assigned.filter(r=>r.state==='awaiting_handoff'&&!r.acknowledged))html+=`<button class="handoff-button" data-handoff="${escapeHTML(item.id)}" data-resource="${escapeHTML(r.id)}" ${(token||publicDemo)&&connected?'':'disabled'}>Acknowledge ${escapeHTML(r.id)} briefing</button>`;
 }else if(selected.kind==='drone'){
  const r=Number(item.sensor_radius_m)||0,speed=Number(item.speed_mps)||0,area=Math.PI*r*r/10000,rate=2*r*speed*60/10000,act=item.activity;
  html+=`<p class="detail-status"><i class="activity-dot" style="background:${ACTIVITY_COLOR[act]||FOOT_FALLBACK}"></i>${escapeHTML(nice(act||item.state))}</p>`;
  html+=`<p class="doing-now">${escapeHTML(ACTIVITY_TEXT[act]||nice(item.state))}${item.task_id?` Task ${escapeHTML(item.task_id)}.`:''}</p>`;
  if(act!==undefined)html+=row('Sensor footprint',r>0?`${r.toFixed(0)} m radius · ${area.toFixed(2)} ha`:'Not airborne')+row('Scan rate',r>0&&speed>0?`${rate.toFixed(1)} ha/min`:'—')+row('Ground speed',`${speed.toFixed(1)} m/s`);
  html+=row('State',nice(item.state))+row('Task',item.task_id||'No incident assignment')+row('Dock',item.dock_id||'—')+row('Radio link',item.link===false?'Lost':'Connected')+row('Battery',`${Math.round((item.battery||0)*100)}%`)+row('Energy remaining',`${Number(item.energy_wh||0).toFixed(1)} / ${Number(item.capacity_wh||0).toFixed(0)} Wh`)+row('Payload',nice(item.payload))+row('Supply stock',`${item.payload_stock??'—'} / ${item.payload_capacity??'—'}`)+row('Delivery',item.delivered?'Recorded delivered':'Not recorded delivered');
  if(item.link===false)html+='<p class="detail-warning">Radio link unavailable. Position and delivery status may be stale.</p>';
  {const co=item.coordination;if(co&&(co.mode==='decentralized'||co.bundle?.length)){const nb=co.neighbours||[],lq=co.link_quality||{},bids=Object.entries(co.winning_bids||{}).slice(0,4);
   html+='<div class="detail-divider"></div><p class="doing-now"><b>Decentralized coordination</b></p>'+row('Mode',nice(co.mode))+row('Gateway',co.gateway?'Yes, holds the command link':`No, ${co.hops_to_gateway??'?'} hop${co.hops_to_gateway===1?'':'s'} to a gateway`)+row('Bundle',co.bundle?.length?co.bundle.join(' > '):'Empty')+row('Winning bids',bids.map(([k,v])=>`${k} ${Number(v).toFixed(1)}`).join(' · ')||'None')+row('Neighbours',nb.length?`${nb.length}: ${nb.slice(0,6).map(id=>id+(lq[id]!=null?` ${Math.round(lq[id]*100)}%`:'')).join(', ')}${nb.length>6?', ...':''}`:'None in range')+row('Last consensus',co.last_consensus_t!=null?`${Math.max(0,state.t-co.last_consensus_t).toFixed(0)}s ago`:'-')+(co.known_peers_lost?.length?row('Lost peers',co.known_peers_lost.join(', ')):'')}}
  html+=decisionHTML(item,row);
 }else if(selected.kind==='dock'){
  const ids=item.drone_ids||[];html+=row('Docked',String(item.docked??'—'))+row('Launched',String(item.launched??'—'))+row('Drones',String(ids.length))+`<div class="dock-drones">${ids.map(id=>`<button data-select="drone" data-id="${escapeHTML(id)}">${escapeHTML(id)}</button>`).join('')}</div>`;
 }else{
  html+=row('Agency',item.agency||nice(item.kind))+row('Vehicle',item.name||item.id)+row('Transport',nice(item.transport||'road'))+row('State',nice(item.state))+row('Incident',item.incident_id||'Unassigned')+row('Capacity',`${item.capacity??'—'} people`)+row('On board',`${item.load||0} people`)+row('Briefing',item.acknowledged?'Acknowledged':'Pending');
  if(item.transfer_destination)html+=row('Transfer destination',item.transfer_destination);
  if(item.model)html+=row('Model',nice(item.model));
  html+=decisionHTML(item,row);
 }
 if(el._head!==head+selected.kind+selected.id){el._head=head+selected.kind+selected.id;el._html=null;el.innerHTML=head+'<div class="sel-body"></div>'}setHTML(el.querySelector('.sel-body'),html);
}

/* ====================== side panel ====================== */
function renderUI(){
 if(!state)return;uiDirty=false;
 const drones=state.drones||[],incidents=state.incidents||[],resources=state.resources||[],metrics=state.metrics||{};
 const scenario=typeof state.scenario==='object'&&state.scenario?state.scenario:{id:state.scenario};
 $('mission-title').textContent=scenario.title||scenario.name||nice(scenario.id)||'Monsoon response';
 renderScenarioOptions(scenario.id);if(scenario.id&&document.activeElement!==$('scenario-select'))$('scenario-select').value=scenario.id;
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
 patchList($('incident-list'),[...incidents].sort((a,b)=>(parseInt(String(b.id).replace(/\D/g,''))||0)-(parseInt(String(a.id).replace(/\D/g,''))||0)),i=>`<button class="incident-card ${selected?.id===i.id?'selected':''} ${['resolved','completed'].includes(i.status)?'resolved':''}" data-select="incident" data-id="${escapeHTML(i.id)}"><div class="card-top"><span class="badge">${['resolved','completed'].includes(i.status)?'✓':escapeHTML(nice(i.severity))}</span>${isOrch(i)?'<span class="badge orch">DisruptionOps</span>':''}<small>${escapeHTML(i.id)}</small></div><strong>${escapeHTML(i.title||nice(i.category))}</strong><div class="card-bottom"><span>${(()=>{const c=peopleCounts(i.id);return c.total?`${i.people||c.total} people · ${c.waiting} waiting${c.safe?` · ${c.safe} safe`:''}`:`${i.people||0} people`})()}</span><span>${escapeHTML(nice(i.status))}</span></div></button>`,'<p class="muted">No incidents yet.</p>');
 patchList($('fleet-list'),drones,d=>`<button class="drone-card ${selected?.id===d.id?'selected':''}" data-select="drone" data-id="${escapeHTML(d.id)}"><div class="drone-top"><span><i class="capability-dot" style="background:${capabilityColor(d.payload)}"></i>${escapeHTML(d.id)}</span><span>${Math.round((d.battery||0)*100)}%</span></div><div class="drone-state"><i class="activity-dot" style="background:${ACTIVITY_COLOR[d.activity]||FOOT_FALLBACK}"></i>${escapeHTML(nice(d.activity||d.state))}</div><div class="battery-bar"><i style="width:${Math.round(Math.max(0,Math.min(100,(d.battery||0)*100)))}%;${d.battery<.25?'background:#eea17d':''}"></i></div></button>`);
 patchList($('resource-list'),resources,r=>`<div class="resource-card"><span class="resource-icon ${r.kind==='fire_engine'?'fire':''}">${/boat/i.test(r.transport||r.kind)?'◒':'▰'}</span><div><button class="resource-name" data-select="resource" data-id="${escapeHTML(r.id)}">${escapeHTML(r.name||`${r.id} · ${nice(r.kind)}`)}${!r.name&&r.transport==='boat'?' boat':''}</button><small>${escapeHTML(r.agency?r.agency+' · ':'')}<b class="unit-state ${escapeHTML(r.state)}">${escapeHTML(unitState(r))}</b>${r.incident_id?` · ${escapeHTML(r.incident_id)}`:''}</small></div><button data-release="${escapeHTML(r.id)}" title="${r.incident_id?'Committed until response completes':r.state==='available'?'Already available':'Make responder available'}" ${(token||publicDemo)&&connected&&!r.incident_id&&r.state!=='available'?'':'disabled'}>Release</button></div>`);
 if(document.activeElement!==$('fault-drone')){const old=$('fault-drone').value;setHTML($('fault-drone'),drones.map(d=>`<option value="${escapeHTML(d.id)}">${escapeHTML(d.id)}</option>`).join(''));if(drones.some(d=>d.id===old))$('fault-drone').value=old}
 const done=incidents.filter(i=>['resolved','completed'].includes(i.status)).length;$('timeline-progress').style.width=`${incidents.length?done/incidents.length*100:0}%`;
 renderSelection();renderCommand();renderMeshChip();
 const ag=metrics.agency,card=$('agency-card');if(ag){card.hidden=false;$('agency-orders').textContent=`${ag.resource_requests_raised??0} orders`;const t=v=>v==null?'—':v>=90?`${Math.floor(v/60)}m ${Math.round(v%60)}s`:`${Math.round(v)}s`,cell=(k,v)=>`<div><strong>${escapeHTML(v)}</strong><span>${escapeHTML(k)}</span></div>`;
  setHTML($('agency-grid'),(ag.requests_received?cell('Requests accepted',`${ag.accepted??0} / ${ag.requests_received??0}`)+cell('Declined',ag.declined??0):'')+cell('Time to first drone',t(ag.median_time_to_first_drone_s))+cell('Time to briefing',t(ag.median_time_to_briefing_s))+cell('Dispatch orders filled',`${ag.resource_requests_fulfilled??0} / ${ag.resource_requests_raised??0}`)+cell('Supplies delivered',ag.supplies_delivered??0)+cell('Handoffs',ag.handoffs??0)+cell('Drone first',ag.drone_first??0)+cell('Ground first',ag.ground_first??0))}else card.hidden=true;
}

function setConnection(on,message){connected=on;$('connection').textContent=on?'Connected':'Connection interrupted';$('connection').classList.toggle('offline',!on);$('operator-role').textContent=publicDemo?'Public demo':token?'Operator':'View only';$('operator-role').title=publicDemo?'Anyone can control this exercise':token?'Authenticated exercise operator':'Open locally or use an operator control link to enable commands';const note=$('view-only-note');if(note)note.hidden=!!(token||publicDemo);if(!token&&!publicDemo&&addMode)setAddMode(false);document.querySelectorAll('[data-control]').forEach(el=>el.disabled=!on||!(token||publicDemo));$('scene-notice').classList.toggle('hidden',on);$('scene-notice').classList.toggle('stale',!!state);$('scene-notice').textContent=message||(state?'Connection interrupted · displaying last received state':'Connecting to the operations server…');if(state&&!on)renderUI();}

function clearRun(){
 disposeGroup(actorsGroup);disposeGroup(selGroup);disposeGroup(docksGroup);disposeGroup(footGroup);disposeGroup(coverageGroup);disposeGroup(trailGroup);disposeGroup(peopleGroup);disposeGroup(zoneGroup);peopleObjects.clear();zoneSignature='';
 for(const [id,r] of routeLines){routesGroup.remove(r.line);r.geo.dispose()}for(const [id,rec] of signalObjects)removeSignal(id,rec);
 for(const g of mesh.subs.values()){scene.remove(g);disposeGroup(g)}mesh.subs.clear();mesh.pairs=[];mesh.subPairs=[];droneObjects.clear();resourceObjects.clear();incidentObjects.clear();dockObjects.clear();footObjects.clear();routeLines.clear();seenSignals.clear();traffic.map.clear();logReset();leadIn=null;trailLine=null;selSignature='';coverageSignature='';
}

function applySnapshot(packet){
 if(packet.type!=='snapshot'||!packet.state)return;const next=packet.state;if(next.schema_version!==2)return;if(run===next.run_id&&packet.seq<=sequence)return;
 if(run!==next.run_id){run=next.run_id;sequence=-1;selected=null;clearRun();if(!next.world)disposeGroup(worldGroup)}
 sequence=packet.seq;state=next;const now=performance.now();if(lastPacket){const gap=now-lastPacket;if(gap<2000)packetDur=Math.max(60,Math.min(500,packetDur*.7+gap*.3))}lastPacket=now;
 if(next.world)buildWorld(next.world);updateDynamicWorld();
 syncDocks();syncActors(state.drones,droneObjects,createDrone,'drone');syncActors(state.resources,resourceObjects,createResource,'resource');syncActors(state.incidents,incidentObjects,createIncident,'incident');syncTraffic();syncOrch();syncMesh();ingestLog(state);
 for(const o of incidentObjects.values()){if(o.userData.item?.handed_over)o.userData.ring.material.color.set('#89d8a8')}
 syncFootprints();syncSignals();syncPeople();syncSearch();checkNewIncident();updateRoutes();uiDirty=true;
 if(now-lastUI>250||!connected){renderUI();lastUI=now}
 if(!connected)setConnection(true);
}

function toast(message){$('toast').textContent=message;$('toast').style.opacity=1;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').style.opacity=0,4500)}

function command(action,payload={}){if(!(token||publicDemo)||!connected||socket?.readyState!==WebSocket.OPEN){toast('An active operator session is required.');return}const id=crypto.randomUUID();socket.send(JSON.stringify({type:'command',id,action,expected_run_id:run,payload}));const timeout=setTimeout(()=>{pending.delete(id);toast('Command acknowledgment timed out.')},8000);pending.set(id,{action,timeout});}

async function connect(){const fragment=new URLSearchParams(location.hash.slice(1));const supplied=fragment.get('control');if(supplied){sessionStorage.setItem('ops-control',supplied);history.replaceState(null,'',location.pathname+location.search)}token=sessionStorage.getItem('ops-control')||null;publicDemo=false;try{const response=await fetch('/ops/session',{credentials:'same-origin',cache:'no-store'});if(response.ok){const session=await response.json();publicDemo=session.public_demo===true&&session.can_control===true;token=session.control_token||token||null;if(session.control_token)sessionStorage.setItem('ops-control',token)}}catch{}const url=new URL('/ops/ws',location.href);url.protocol=location.protocol==='https:'?'wss:':'ws:';if(token)url.searchParams.set('token',token);socket=new WebSocket(url);socket.onopen=()=>{retry=600};socket.onmessage=event=>{try{const packet=JSON.parse(event.data);if(packet.type==='ack'){const record=pending.get(packet.id);if(record){clearTimeout(record.timeout);pending.delete(packet.id);if(!packet.ok&&record.action==='fleet'){uiDirty=true;lastUI=0}toast(packet.ok?(record.action==='random_incident'?'Random issue raised':record.action==='detect'?'Issue placed on the map':`${nice(record.action)} accepted`):`${nice(record.action)} rejected: ${packet.error?.message||packet.error||'Command rejected'}`)}}else applySnapshot(packet)}catch(error){console.warn('Invalid operations message',error)}};socket.onclose=()=>{setConnection(false);setTimeout(connect,retry);retry=Math.min(retry*1.8,15000)};socket.onerror=()=>socket.close();}

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
let showLabels=true,showBuildings=true,showRoads=true,showMesh=true;
function layerMeshes(){const b=[],r=[];const roadSet=new Set([mats.curb,mats.road,mats.stripe]);for(const o of worldGroup.children){if(!o.isMesh)continue;const m=Array.isArray(o.material)?o.material[0]:o.material;if(roadSet.has(m))r.push(o);else if(m.map||m===mats.roof||m===mats.darkRoof)b.push(o)}return{b,r}}
function applyLayers(){const {b,r}=layerMeshes();b.forEach(o=>o.visible=showBuildings);r.forEach(o=>o.visible=showRoads);if(showLabels)camera.layers.enable(2);else camera.layers.disable(2)}
for(const [id,key] of [['toggle-labels','Labels'],['toggle-buildings','Buildings'],['toggle-roads','Roads'],['toggle-search','Search'],['toggle-mesh','Mesh']]){const el=$(id);if(el)el.onclick=()=>{const on=!el.classList.contains('active');el.classList.toggle('active',on);el.setAttribute('aria-pressed',String(on));if(key==='Labels')showLabels=on;if(key==='Buildings')showBuildings=on;if(key==='Roads')showRoads=on;if(key==='Mesh')showMesh=on;if(key==='Search'){showSearch=on;searchSignature='';if(state){syncSearch();updateRoutes()}}applyLayers()}}

/* ====================== rescue issues ====================== */
const ISSUE_DEFAULT={medical:{title:'Operator-raised medical emergency',needs:['first_aid']},flood:{title:'Operator-raised flood rescue',needs:['rescue']},fire:{title:'Operator-raised fire',needs:['assessment']},trapped:{title:'Operator-raised trapped residents',needs:['rescue']},blocked_route:{title:'Operator-raised blocked route',needs:['assessment']}};
let addMode=false,pendingPoint=null,watchNew=null,issueMarker=null;
const popover=$('issue-popover');
function setAddMode(on){addMode=on&&!!(token||publicDemo);$('add-issue').classList.toggle('active',addMode);$('add-issue').setAttribute('aria-pressed',String(addMode));$('issue-hint').hidden=!addMode;renderer.domElement.style.cursor=addMode?'crosshair':'';if(!addMode)closePopover()}
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
$('toggle-wards').onclick=()=>{showWards=!showWards;$('toggle-wards').classList.toggle('active',showWards);$('toggle-wards').setAttribute('aria-pressed',String(showWards))};
$('toggle-traffic').onclick=()=>{showTraffic=!showTraffic;$('toggle-traffic').classList.toggle('active',showTraffic);$('toggle-traffic').setAttribute('aria-pressed',String(showTraffic))};
$('toggle-coverage').onclick=()=>{showCoverage=!showCoverage;$('toggle-coverage').classList.toggle('active',showCoverage);$('toggle-coverage').setAttribute('aria-pressed',String(showCoverage));if(state)updateCoverage()};
$('randomize').onclick=()=>command('random_scenario');$('scenario-select').onchange=e=>command('scenario',{id:e.target.value});$('fleet-select').onchange=e=>{command('fleet',{value:Number(e.target.value)});e.target.blur()};
$('pause').onclick=()=>command(state?.paused?'resume':'pause');$('speed').onchange=e=>command('speed',{value:Number(e.target.value)});$('reset').onclick=()=>command('reset');
document.addEventListener('click',e=>{const selectButton=e.target.closest('[data-select]');if(selectButton)select(selectButton.dataset.select,selectButton.dataset.id);const fault=e.target.closest('[data-fault]');if(fault)command('fault',{drone_id:$('fault-drone').value,kind:fault.dataset.fault});const release=e.target.closest('[data-release]');if(release)command('release_resource',{resource_id:release.dataset.release});const handoff=e.target.closest('[data-handoff]');if(handoff)command('ack_handoff',{incident_id:handoff.dataset.handoff,resource_id:handoff.dataset.resource});if(e.target.closest('[data-focus]'))focusSelected();const mm=e.target.closest('[data-msg-incident]');if(mm)focusMessage(mm.dataset.msgIncident,mm.dataset.msgAgent);const mc=e.target.closest('[data-msg-focus-incident]');if(mc)focusMessage(mc.dataset.msgFocusIncident);if(e.target.id==='close-selection'){selected=null;selSignature='';renderSelection();updateSelectedRoute();refreshResTags()}});
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
 cameraMode=mode;document.querySelectorAll('.camera-tools button').forEach(b=>b.classList.toggle('active',b.id===`camera-${mode}`));
 if(mode==='overview'){const o=overviewPose();flyCamera(o.pos,o.target,{dur:region.on?1600:1100})}
 if(mode==='top'){const o=topPose();flyCamera(o.pos,o.target,{dur:region.on?1600:1100})}
 if(mode==='follow'){if(!selected&&state?.drones?.length){selected={kind:'drone',id:state.drones[0].id};renderSelection()}if(selected)frameEntity(selected.kind,selected.id)}
}['overview','top','follow'].forEach(mode=>$(`camera-${mode}`).onclick=()=>view(mode));

const raycaster=new THREE.Raycaster(),pointer=new THREE.Vector2();let down=null;raycaster.layers.enableAll();
const canvasEl=renderer.domElement;
function setPointer(e){const rect=canvasEl.getBoundingClientRect();pointer.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);raycaster.setFromCamera(pointer,camera)}
const visibleChain=o=>{for(let n=o;n;n=n.parent)if(!n.visible)return false;return true};
function pickActor(){for(const hit of raycaster.intersectObjects([...actorsGroup.children,...docksGroup.children],true)){if(!visibleChain(hit.object))continue;let o=hit.object;while(o&&!o.userData.kind)o=o.parent;if(o?.userData.kind)return o}return null}
function pickGround(){
 const hits=raycaster.intersectObjects(worldGroup.children,true);if(hits.length)return hits[0].point.clone();
 const o=raycaster.ray.origin,d=raycaster.ray.direction;for(let t=0;t<60000;t+=Math.max(4,t*.01)){const x=o.x+d.x*t,y=o.y+d.y*t,z=o.z+d.z*t;if(y<=groundHeight(x,-z))return new THREE.Vector3(x,groundHeight(x,-z),z)}return null;
}
canvasEl.addEventListener('pointerdown',e=>{down=[e.clientX,e.clientY];cancelFlight()});
canvasEl.addEventListener('wheel',cancelFlight,{passive:true});
let hoverRes=null,hoverT=0;
const resFull=r=>r.name&&r.name!==r.id?`${r.id} · ${r.name}`:(r.agency?`${r.id} · ${r.agency}${r.kind?' '+nice(r.kind):''}`:String(r.id));
function applyResTag(o){const u=o.userData;if(!u.tag||u.kind!=='resource')return;const full=hoverRes===o||(selected?.kind==='resource'&&selected.id===u.id),text=full?resFull(u.item||{id:u.id}):String(u.id);if(u.tagText===text)return;
 const old=u.tag,pos=old.position.clone();o.remove(old);old.material.map.dispose();old.material.dispose();const t=label(text,RESOURCE_COLOR[u.vkind]||'#e2d4b8',5);t.position.copy(pos);t.userData.s0=t.scale.clone();t.userData.prio=full?9:3;o.add(t);u.tag=t;u.tagText=text}
function refreshResTags(){for(const o of resourceObjects.values())applyResTag(o)}
function setHover(o){if(hoverRes===o)return;const prev=hoverRes;hoverRes=o;if(prev)applyResTag(prev);if(o)applyResTag(o)}
canvasEl.addEventListener('pointermove',e=>{if(e.buttons||performance.now()-hoverT<100)return;hoverT=performance.now();setPointer(e);const o=pickActor();setHover(o&&o.userData.kind==='resource'?o:null);canvasEl.style.cursor=addMode?'crosshair':(o?'pointer':'')});
canvasEl.addEventListener('pointerleave',()=>setHover(null));
canvasEl.addEventListener('pointerup',e=>{if(!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>5)return;setPointer(e);if(addMode){const p=pickGround();if(p)openPopover(p,e);return}const o=pickActor();if(o)select(o.userData.kind,o.userData.id)});
canvasEl.addEventListener('dblclick',e=>{setPointer(e);const o=pickActor();if(o){select(o.userData.kind,o.userData.id);return}const p=pickGround();if(p){cameraMode='free';document.querySelectorAll('.camera-tools button').forEach(b=>b.classList.remove('active'));flyTo(p,{dist:48,elev:.3,dur:1200})}});

new ResizeObserver(()=>{camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();renderer.setSize(host.clientWidth,host.clientHeight)}).observe(host);


/* ====================== region world (PCMC) ====================== */
const regionGroup=new THREE.Group(),wardGroup=new THREE.Group(),lifeGroup=new THREE.Group(),trafficGroup=new THREE.Group();scene.add(regionGroup,wardGroup,lifeGroup,trafficGroup);
const region={on:false,id:null,extent:1200,center:new THREE.Vector3(),roadMeshes:[],wardLabels:[],lifeLabels:[],lifeBadges:[],segIndex:null,river:[],roadsFlat:[]};
let showWards=true,showTraffic=true;
const ROAD_CLASS={motorway:{w:24,c:'#4a525c',rank:6},motorway_link:{w:12,c:'#4a525c',rank:5},trunk:{w:22,c:'#515a64',rank:6},trunk_link:{w:11,c:'#515a64',rank:5},primary:{w:18,c:'#59626c',rank:5},primary_link:{w:10,c:'#59626c',rank:4},secondary:{w:14,c:'#657079',rank:4},secondary_link:{w:9,c:'#657079',rank:3},tertiary:{w:11,c:'#737d85',rank:3},residential:{w:8,c:'#858e95',rank:2},living_street:{w:6,c:'#8f979d',rank:2},unclassified:{w:8,c:'#858e95',rank:2},service:{w:5,c:'#9ba2a7',rank:1},track:{w:4,c:'#a9a58f',rank:1}};
const roadClassOf=r=>ROAD_CLASS[r.class||r.kind||r.highway]||(r.width?{w:r.width,c:'#7a848c',rank:2}:{w:8,c:'#858e95',rank:2});
const roadMatCache={};const roadMatFor=(color,rank)=>roadMatCache[color+rank]||(roadMatCache[color+rank]=(()=>{const m=material(color,{roughness:.95,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:-rank,polygonOffsetUnits:-rank});sharedMat.add(m);return m})());
function ribbonArrays(points,width,y,pos,idx){
 const n=points.length;if(n<2)return;const base=pos.length/3;
 for(let i=0;i<n;i++){const p=points[i],a=points[Math.max(0,i-1)],b=points[Math.min(n-1,i+1)];let dx=b[0]-a[0],dy=b[1]-a[1];const l=Math.hypot(dx,dy)||1;dx/=l;dy/=l;const nx=-dy*width/2,ny=dx*width/2;
  for(const s of [1,-1]){const x=p[0]+nx*s,north=p[1]+ny*s;pos.push(x,groundHeight(x,north)+y,-north)}
  if(i){const k=base+(i-1)*2;idx.push(k,k+2,k+1,k+1,k+2,k+3)}}
}
function buildRegion(world){
 region.on=true;region.id=world.id;const b=world.bounds||[-7000,-7000,7000,7000];region.extent=Math.max(b[2]-b[0],b[3]-b[1]);region.center.set((b[0]+b[2])/2,0,-(b[1]+b[3])/2);
 // roads: one merged mesh per class (and an exaggerated wide set for the zoomed-out view)
 const groups=new Map();for(const r of world.roads||[]){const pts=(r.points||[]).filter(p=>p&&isFinite(p[0])&&isFinite(p[1]));if(pts.length<2)continue;const c=roadClassOf(r),key=c.c+'|'+c.rank+'|'+(r.width||c.w);if(!groups.has(key))groups.set(key,{c,width:Number(r.width)||c.w,list:[]});groups.get(key).list.push(pts);if(r.blocked)region.blocked=(region.blocked||0)+1}
 region.roadsFlat=[];
 for(const {c,width,list} of groups.values()){
  for(const wide of c.rank>=4?[false,true]:[false]){
   const pos=[],idx=[],w=wide?width*3:width,y=.35+c.rank*.1+(wide?.05:0);
   for(const pts of list)ribbonArrays(pts,w,y,pos,idx);
   if(!idx.length)continue;const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));g.setIndex(idx);g.computeVertexNormals();
   const m=new THREE.Mesh(g,roadMatFor(c.c,c.rank));m.receiveShadow=true;m.userData.lod=wide?'wide':'normal';m.userData.rank=c.rank;regionGroup.add(m);region.roadMeshes.push(m);
  }
  for(const pts of list)for(let i=1;i<pts.length;i++)region.roadsFlat.push([pts[i-1][0],pts[i-1][1],pts[i][0],pts[i][1],width,c.rank]);
 }
 // centre lines only on arterials (cheap dashed look from short quads would be heavy; a single line set is enough)
 const lp=[];for(const r of world.roads||[]){const c=roadClassOf(r);if(c.rank<5)continue;const pts=r.points||[];for(let i=1;i<pts.length;i++){const a=vec(pts[i-1]),d=vec(pts[i]);lp.push(a.x,a.y+1.2,a.z,d.x,d.y+1.2,d.z)}}
 if(lp.length){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(lp,3));const l=new THREE.LineSegments(g,new THREE.LineBasicMaterial({color:'#e6e2c8',transparent:true,opacity:.55}));l.userData.lod='normal';l.userData.rank=9;regionGroup.add(l);region.roadMeshes.push(l)}
 // spatial index for procedural buildings
 const idxMap=new Map(),CELL=200;region.cell=CELL;for(const [si,s] of region.roadsFlat.entries()){const len=Math.hypot(s[2]-s[0],s[3]-s[1]),steps=Math.max(1,Math.ceil(len/100));const seen=new Set();for(let k=0;k<=steps;k++){const x=s[0]+(s[2]-s[0])*k/steps,y=s[1]+(s[3]-s[1])*k/steps,key=Math.floor(x/CELL)+','+Math.floor(y/CELL);if(seen.has(key))continue;seen.add(key);(idxMap.get(key)||idxMap.set(key,[]).get(key)).push(si)}}
 region.segIndex=idxMap;
 // water bodies
 const waterMat=material('#5f93ab',{roughness:.3,metalness:.1,polygonOffset:true,polygonOffsetFactor:-8,polygonOffsetUnits:-8});sharedMat.add(waterMat);
 const waters=[...(world.water||world.waterbodies||world.water_bodies||[]),...(world.water_areas||[])];
 const wg=new THREE.Group();for(const wb of Array.isArray(waters)?waters:[]){const poly=Array.isArray(wb)?wb:(wb.polygon||wb.points||wb.footprint);if(!poly||poly.length<3)continue;const shape=new THREE.Shape();poly.forEach((p,i)=>i?shape.lineTo(p[0],p[1]):shape.moveTo(p[0],p[1]));const g=new THREE.ShapeGeometry(shape);g.rotateX(-Math.PI/2);const m=new THREE.Mesh(g,waterMat);m.position.y=.7;m.receiveShadow=true;wg.add(m)}mergeStatic(wg);for(const c of [...wg.children]){c.castShadow=false;regionGroup.add(c)}
 const riv=world.river?.points||world.river||[];region.river=Array.isArray(riv)?riv:[];
 {const pos=[],idx=[];for(const ww of world.waterways||[]){const pts=(ww.points||[]).filter(p=>p&&isFinite(p[0]));if(pts.length>1)ribbonArrays(pts,Math.max(5,Number(ww.width)||6),.75,pos,idx)}if(idx.length){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));g.setIndex(idx);g.computeVertexNormals();const m=new THREE.Mesh(g,waterMat);regionGroup.add(m)}}
 for(const fz of world.flood_zones||[]){if(!Array.isArray(fz.center))continue;const c=vec(fz.center),m=new THREE.Mesh(new THREE.CircleGeometry(Number(fz.radius)||100,40).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color:'#4c9ccc',transparent:true,opacity:.22,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-9,polygonOffsetUnits:-9}));m.position.set(c.x,c.y+1.1,c.z);regionGroup.add(m)}
 // wards
 disposeGroup(wardGroup);region.wardLabels=[];
 const wardLine=new THREE.LineBasicMaterial({color:'#3f6a8f',transparent:true,opacity:.6,depthTest:false});
 for(const w of world.wards||[]){const poly=(w.polygon||w.points||[]).filter(p=>p&&isFinite(p[0])&&isFinite(p[1]));if(poly.length<3)continue;const v=poly.map(p=>{const q=vec(p);q.y+=3;return q});v.push(v[0].clone());const l=new THREE.Line(new THREE.BufferGeometry().setFromPoints(v),wardLine);l.renderOrder=3;wardGroup.add(l);
  let cx=0,cy=0;for(const p of poly){cx+=p[0];cy+=p[1]}cx/=poly.length;cy/=poly.length;const tag=label(w.name||w.id,'#7aa6c9',7);const c=vec([cx,cy]);tag.position.set(c.x,c.y+8,c.z);tag.userData.s0=tag.scale.clone();tag.userData.prio=1;tag.userData.minDist=1600;wardGroup.add(tag);region.wardLabels.push(tag)}
 wardGroup.visible=showWards;
 // lifelines
 disposeGroup(lifeGroup);region.lifeLabels=[];region.lifeBadges=[];
 for(const lf0 of world.lifelines||[]){const lf={...lf0,id:lf0.id||lf0.source_id,position:lf0.position||lf0.local_xy||lf0.road_xy};if(!Array.isArray(lf.position))continue;lifeGroup.add(buildLifeline(lf))}
 if(!world.buildings?.length)region.proc=true;else region.proc=false;
 procReset();
}
const LIFE_STYLE={hospital:['H','#d6453d'],medical_camp:['M','#d6453d'],shelter:['S','#2e8f6b'],relief_centre:['R','#e08a2e'],relief_center:['R','#e08a2e'],relief:['R','#e08a2e'],water_point:['W','#2f80ed'],fire_station:['F','#c8342c'],dock:['D','#2d7ff0'],police:['P','#455a8a'],drone_base:['D','#2d7ff0'],rescue:['R','#e9812a']};
const badgeCache={};
function badgeTexture(glyph,color){const k=glyph+color;if(badgeCache[k])return badgeCache[k];const c=document.createElement('canvas');c.width=c.height=96;const x=c.getContext('2d');x.fillStyle='#ffffff';x.beginPath();x.arc(48,48,44,0,7);x.fill();x.fillStyle=color;x.beginPath();x.arc(48,48,37,0,7);x.fill();x.fillStyle='#fff';x.font='700 46px -apple-system,Segoe UI,Arial';x.textAlign='center';x.textBaseline='middle';x.fillText(glyph,48,51);const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;return badgeCache[k]=t}
function buildLifeline(lf){
 const [glyph,color]=LIFE_STYLE[lf.kind]||['•','#6e8997'],g=new THREE.Group(),p=vec(lf.position);g.position.copy(p);g.userData={kind:'lifeline',id:lf.id,item:lf};
 const b=(x,y,z,w,h,d,c)=>box(g,x,y,z,w,h,d,vm(c));
 if(lf.kind==='hospital'){b(0,8,0,34,16,22,'#eef0f0');b(0,16.4,0,34,.8,22,'#d9dede');b(0,17,0,12,.5,3.5,'#d6453d');b(0,17,0,3.5,.5,12,'#d6453d');b(-9,4,11.1,6,8,.4,'#6f8fa3')}
 else if(lf.kind==='medical_camp'){b(0,4,0,22,8,14,'#f1f3f3');b(0,8.4,0,23,1,15,'#d6453d')}
 else if(lf.kind==='shelter'||lf.kind==='relief_centre'||lf.kind==='relief_center'||lf.kind==='relief'){b(0,5,0,28,10,18,glyph==='S'?'#cfe3d4':'#f1d9b8');b(0,10.4,0,29,1,19,color);b(0,2,9.1,8,4,.4,'#6f8fa3')}
 else if(lf.kind==='fire_station'){b(0,6,0,26,12,16,'#e8d9d4');b(-6,5,8.1,8,9,.4,'#c8342c');b(6,5,8.1,8,9,.4,'#c8342c');b(0,12.4,0,27,1,17,'#c8342c')}
 else if(lf.kind==='water_point'){const t=new THREE.Mesh(cylinderGeo,vm('#8fb4c9',{metalness:.4}));t.scale.set(5,12,5);t.position.y=8;t.castShadow=true;g.add(t);b(0,1,0,14,2,14,'#9aa4aa')}
 else if(lf.kind==='dock'){b(0,.8,0,40,1.6,10,'#8b7b62');for(const x of [-16,-6,6,16])b(x,2.4,-5,1,3,1,'#5a4f3d')}
 else b(0,4,0,18,8,14,'#d8dde0');
 const badge=new THREE.Sprite(new THREE.SpriteMaterial({map:badgeTexture(glyph,color),depthTest:false,depthWrite:false,transparent:true,sizeAttenuation:false,fog:false,toneMapped:false}));badge.scale.set(.032,.032,1);badge.position.y=34;badge.renderOrder=11;badge.layers.set(2);badge.userData={s0:badge.scale.clone(),prio:5,isBadge:true};g.add(badge);region.lifeBadges.push(badge);
 const tag=label(lf.name||nice(lf.kind),color,6);tag.position.y=47;tag.userData.s0=tag.scale.clone();tag.userData.prio=4;tag.userData.minDist=3200;g.add(tag);region.lifeLabels.push(tag);
 return mergeStatic(g);
}
function overviewPose(){
 if(region.on){const e=region.extent;return {pos:new THREE.Vector3(region.center.x+e*.34,e*.62,region.center.z+e*.62),target:region.center.clone()}}
 return {pos:new THREE.Vector3(870,1030,1000),target:new THREE.Vector3(0,15,0)};
}
function topPose(){if(region.on){return {pos:new THREE.Vector3(region.center.x,region.extent*1.15,region.center.z+1),target:region.center.clone()}}return {pos:new THREE.Vector3(0,1600,1),target:new THREE.Vector3(0,0,0)}}
function applyWorldScale(world,first){
 const isRegion=world.kind==='region';
 if(!isRegion){region.on=false;region.extent=1200;scene.fog.density=.00009;controls.minDistance=5;controls.maxDistance=2600;camera.far=9000}
 else{const e=region.extent;scene.fog.density=.000024*Math.min(1.6,14000/Math.max(e,3000));controls.minDistance=8;controls.maxDistance=e*1.9;camera.far=e*6}
 camera.updateProjectionMatrix();
 if(first){const o=overviewPose();camera.position.copy(o.pos);controls.target.copy(o.target)}
}

/* ----- procedural low-rise buildings, only along roads near the camera and hotspots ----- */
const proc={chunks:new Map(),mesh:null,roof:null,key:'',cap:16000,last:0,count:0};
const PROC_CHUNK=400;
const bTints=['#e9dfcb','#d3bf9b','#d9aa90','#dcdcd3','#b9c8c2','#cdb6a2','#e3d2b8','#c9c4b8'].map(c=>new THREE.Color(c));
function procReset(){proc.chunks.clear();proc.key='';if(proc.mesh){scene.remove(proc.mesh,proc.roof);proc.mesh.dispose();proc.roof.dispose();proc.mesh=proc.roof=null}proc.count=0}
function segDist(px,py,s){const dx=s[2]-s[0],dy=s[3]-s[1],l2=dx*dx+dy*dy||1;let t=((px-s[0])*dx+(py-s[1])*dy)/l2;t=Math.max(0,Math.min(1,t));return Math.hypot(px-(s[0]+dx*t),py-(s[1]+dy*t))}
function riverDist(px,py){let best=1e9;const r=region.river;for(let i=1;i<r.length;i++){const d=segDist(px,py,[r[i-1][0],r[i-1][1],r[i][0],r[i][1]]);if(d<best)best=d}return best}
function genChunk(cx,cy){
 const out=[],x0=cx*PROC_CHUNK,y0=cy*PROC_CHUNK,idx=region.segIndex,C=region.cell;let seed=((cx*73856093)^(cy*19349663))>>>0;const rnd=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296};
 const segs=new Set();for(let gx=Math.floor(x0/C)-1;gx<=Math.floor((x0+PROC_CHUNK)/C)+1;gx++)for(let gy=Math.floor(y0/C)-1;gy<=Math.floor((y0+PROC_CHUNK)/C)+1;gy++)for(const si of idx.get(gx+','+gy)||[])segs.add(si);
 const riverW=(worldData?.river_width||60)/2+25;
 for(const si of segs){const s=region.roadsFlat[si];if(s[5]>=5&&s[4]>=18)continue;if(s[5]<2)continue;const mx=(s[0]+s[2])/2,my=(s[1]+s[3])/2;if(mx<x0||mx>=x0+PROC_CHUNK||my<y0||my>=y0+PROC_CHUNK)continue;
  const dx=s[2]-s[0],dy=s[3]-s[1],L=Math.hypot(dx,dy);if(L<12)continue;const ux=dx/L,uy=dy/L,nx=-uy,ny=ux,ang=Math.atan2(uy,ux);
  for(const side of [1,-1]){let t=4+rnd()*8;while(t<L-6){const w=6+rnd()*7,d=6+rnd()*5,h=(s[5]>=3?5:4)+rnd()*(s[5]>=4?12:7),off=s[4]/2+4+rnd()*5+d/2;
    if(rnd()>.14){const px=s[0]+ux*t+nx*off*side,py=s[1]+uy*t+ny*off*side;let ok=riverDist(px,py)>riverW;
     if(ok){const key=Math.floor(px/C)+','+Math.floor(py/C);for(const oj of idx.get(key)||[]){if(oj===si)continue;const o=region.roadsFlat[oj];if(segDist(px,py,o)<o[4]/2+Math.max(w,d)*.62+1){ok=false;break}}}
     if(ok)out.push([px,py,w,d,h,ang,Math.floor(rnd()*bTints.length)])}
    t+=w+2+rnd()*7}}}
 return out;
}
function stepProc(now){
 if(!region.on||!region.proc||!region.segIndex)return;if(now-proc.last<350)return;proc.last=now;
 const dist=camera.position.distanceTo(controls.target),want=new Map();
 const add=(x,y,r)=>{for(let cx=Math.floor((x-r)/PROC_CHUNK);cx<=Math.floor((x+r)/PROC_CHUNK);cx++)for(let cy=Math.floor((y-r)/PROC_CHUNK);cy<=Math.floor((y+r)/PROC_CHUNK);cy++){const key=cx+','+cy,mid=[(cx+.5)*PROC_CHUNK,(cy+.5)*PROC_CHUNK],dd=Math.hypot(mid[0]-x,mid[1]-y);if(!want.has(key)||want.get(key)>dd)want.set(key,dd)}};
 if(dist<2000){const tx=controls.target.x,ty=-controls.target.z;add(tx,ty,dist<700?900:600)}
 for(const i of state?.incidents||[])if(i.status!=='resolved'&&i.position)add(i.position[0],i.position[1],320);
 for(const lf of worldData?.lifelines||[]){const q=lf.position||lf.local_xy;if(q)add(q[0],q[1],220)}
 for(const d of state?.docks||[])if(d.position)add(d.position[0],d.position[1],220);
 const keys=[...want.keys()].sort((a,b)=>want.get(a)-want.get(b)),sig=keys.join('|');if(sig===proc.key)return;proc.key=sig;
 const spots=[];for(const i of state?.incidents||[])if(i.position)spots.push(i.position);for(const lf of worldData?.lifelines||[]){const q=lf.position||lf.local_xy;if(q)spots.push(q)}for(const d of state?.docks||[])if(d.position)spots.push(d.position);for(const b of worldData?.bases||[])if(b.position)spots.push(b.position);
 const all=[];for(const key of keys){let c=proc.chunks.get(key);if(!c){const [cx,cy]=key.split(',').map(Number);c=genChunk(cx,cy);proc.chunks.set(key,c)}for(const bd of c){if(all.length>=proc.cap)break;let clash=false;for(const sp of spots){if(Math.abs(sp[0]-bd[0])<bd[2]+22&&Math.abs(sp[1]-bd[1])<bd[2]+22){clash=true;break}}if(!clash)all.push(bd)}}
 if(proc.chunks.size>300){for(const k of [...proc.chunks.keys()].slice(0,100))if(!want.has(k))proc.chunks.delete(k)}
 const n=all.length;if(!proc.mesh||proc.mesh.instanceMatrix.count<n){if(proc.mesh){scene.remove(proc.mesh,proc.roof);proc.mesh.dispose();proc.roof.dispose()}const cap=proc.cap,g=boxGeo,mat=new THREE.MeshStandardMaterial({color:'#ffffff',roughness:.9}),rmat=new THREE.MeshStandardMaterial({color:'#7f8a8c',roughness:.9});sharedMat.add(mat);sharedMat.add(rmat);proc.mesh=new THREE.InstancedMesh(g,mat,cap);proc.roof=new THREE.InstancedMesh(g,rmat,cap);for(const m of [proc.mesh,proc.roof]){m.frustumCulled=false;m.castShadow=true;m.receiveShadow=true;scene.add(m)}proc.mesh.userData.proc=true}
 const m4=new THREE.Matrix4(),q=new THREE.Quaternion(),pos=new THREE.Vector3(),sc=new THREE.Vector3(),yAxis=new THREE.Vector3(0,1,0);
 all.forEach((b,i)=>{const [px,py,w,d,h,ang,ti]=b,gh=groundHeight(px,py);q.setFromAxisAngle(yAxis,ang);pos.set(px,gh+h/2,-py);sc.set(w,h,d);m4.compose(pos,q,sc);proc.mesh.setMatrixAt(i,m4);proc.mesh.setColorAt(i,bTints[ti]);pos.set(px,gh+h+.25,-py);sc.set(w+.6,.5,d+.6);m4.compose(pos,q,sc);proc.roof.setMatrixAt(i,m4)});
 proc.mesh.count=proc.roof.count=n;proc.count=n;proc.mesh.instanceMatrix.needsUpdate=proc.roof.instanceMatrix.needsUpdate=true;if(proc.mesh.instanceColor)proc.mesh.instanceColor.needsUpdate=true;
 proc.mesh.visible=proc.roof.visible=showBuildings;
}

/* ----- region per-frame: LOD for roads, label visibility, sun/shadow follow, camera clipping ----- */
let camDist=1000;
function stepRegion(now,dt){
 camDist=camera.position.distanceTo(controls.target);
 peopleGroup.visible=camDist<1500;renderer.shadowMap.autoUpdate=camDist<2600;
 const near=Math.max(.5,Math.min(40,camDist/350));if(Math.abs(near-camera.near)>camera.near*.15){camera.near=near;camera.updateProjectionMatrix()}
 if(!region.on){return}
 for(const m of region.roadMeshes){const lod=m.userData.lod;m.visible=showRoads&&(lod==='wide'?camDist>2200:(m.userData.rank===9?camDist<3500:true))}
 const sx=Math.round(controls.target.x/40)*40,sz=Math.round(controls.target.z/40)*40;sun.position.set(sx-500,1000,sz+400);sun.target.position.set(sx,0,sz);sun.target.updateMatrixWorld();
 wardGroup.visible=showWards;
 if(proc.mesh)proc.mesh.visible=proc.roof.visible=showBuildings&&camDist<2600;
 stepProc(now);
}
// label scale compensation + greedy screen-space decluttering (runs ~8x/s)
const dcVec=new THREE.Vector3();let dcLast=0;
function declutter(now){
 if(now-dcLast<120)return;dcLast=now;
 const cands=[];const w=host.clientWidth,h=host.clientHeight;
 const push=(s,world,prio,extra)=>{if(!s)return;cands.push({s,prio,world,extra})};
 for(const o of incidentObjects.values())if(o.visible&&o.userData.tag)push(o.userData.tag,o,8);
 for(const o of resourceObjects.values())if(o.visible&&o.userData.tag)push(o.userData.tag,o,3+(selected?.id===o.userData.id?9:0));
 for(const o of droneObjects.values())if(o.visible&&o.userData.tag&&o.userData.tag.material.opacity>.03)push(o.userData.tag,o,selected?.id===o.userData.id?10:2);
 for(const o of dockObjects.values())if(o.userData.tag)push(o.userData.tag,o,4);
 for(const g of mesh.subs.values())if(g.visible&&g.userData.tag)push(g.userData.tag,g,2);
 if(region.on){for(const s of region.lifeLabels)push(s,s,4);for(const s of region.lifeBadges)push(s,s,5);if(showWards)for(const s of region.wardLabels)push(s,s,1)}
 cands.sort((a,b)=>b.prio-a.prio);const taken=[];
 for(const c of cands){const s=c.s;s.getWorldPosition(dcVec);dcVec.project(camera);const sx=(dcVec.x*.5+.5)*w,sy=(-dcVec.y*.5+.5)*h;
  const tw=(s.userData.isBadge?26:Math.max(40,(s.scale.x/Math.max(1e-6,s.scale.y))*14)),th=s.userData.isBadge?26:16;
  let hide=dcVec.z>1||sx<-40||sx>w+40||sy<-20||sy>h+20;
  if(!hide&&s.userData.minDist&&camDist>s.userData.minDist)hide=true;
  if(!hide&&s.userData.maxDist&&camDist<s.userData.maxDist)hide=true;
  if(!hide)for(const t of taken){if(Math.abs(t[0]-sx)<(t[2]+tw)/2&&Math.abs(t[1]-sy)<(t[3]+th)/2){hide=true;break}}
  s.userData.dc=hide;if(!hide)taken.push([sx,sy,tw,th]);
  if(s.parent?.userData?.kind!=='drone')s.visible=!hide;
 }
}
function compensateTag(o,u){
 const tag=u.tag;if(!tag)return;if(!tag.userData.s0)tag.userData.s0=tag.scale.clone();const eff=Math.max(o.scale.x,.25);const k=(u.kind==='incident'?1:1.25)/eff;tag.scale.set(tag.userData.s0.x*k,tag.userData.s0.y*k,1);
}

/* ====================== civilian traffic (instanced) ====================== */
const trMats={},trMat=(c,extra={})=>{const k=c+JSON.stringify(extra);if(!trMats[k]){trMats[k]=new THREE.MeshStandardMaterial({color:c,roughness:.7,...extra});sharedMat.add(trMats[k])}return trMats[k]};
const tp=(x,y,z,w,h,d,mat,tint)=>({m:new THREE.Matrix4().compose(new THREE.Vector3(x,y,z),new THREE.Quaternion(),new THREE.Vector3(w,h,d)),mat,tint});
const TRAFFIC_DEF={
 car:{parts:[tp(0,.35,0,4.0,.3,1.7,trMat('#20252a')),tp(0,.8,0,4.3,.75,1.8,trMat('#ffffff'),true),tp(-.25,1.4,0,2.3,.65,1.62,trMat('#6d8696',{roughness:.35}))],palette:['#8f9aa3','#c9ccd0','#3b4650','#6e7d8b','#a6968a','#7b8c7e','#b5b0a5','#5a6a78','#9b7e72','#d8d4c8','#7d3b3b','#3d5a80','#e8e8e4','#b23a3a','#2f5d8a','#d9d9d4']},
 auto:{parts:[tp(0,.35,0,2.5,.3,1.3,trMat('#20252a')),tp(0,.95,0,2.6,.9,1.35,trMat('#ffffff'),true),tp(-.15,1.55,0,2.2,.14,1.45,trMat('#23282d'))],palette:['#e6bf1c','#e6bf1c','#d9b020','#3f8f5a']},
 bike:{parts:[tp(0,.45,0,1.9,.55,.38,trMat('#ffffff'),true),tp(-.1,1.1,0,.45,.9,.5,trMat('#4b6a8f')),tp(.2,1.7,0,.32,.3,.32,trMat('#e0b896'))],palette:['#2b2f33','#7b2d2d','#3b4650','#5a6a78','#8f9aa3']},
 bus:{parts:[tp(0,.5,0,10.8,.3,2.4,trMat('#20252a')),tp(0,1.9,0,11,2.7,2.5,trMat('#ffffff'),true),tp(0,2.5,0,10.4,.85,2.56,trMat('#2c3a44',{roughness:.3})),tp(0,3.3,0,11,.12,2.5,trMat('#d8dde0'))],palette:['#2e8b6a','#d03b36','#2f6fae','#e6bf1c']}
};
const traffic={map:new Map(),inst:{},max:700,count:0,last:0};
function trafficMesh(kind){
 if(traffic.inst[kind])return traffic.inst[kind];const def=TRAFFIC_DEF[kind],meshes=def.parts.map(p=>{const m=new THREE.InstancedMesh(boxGeo,p.mat,traffic.max);m.frustumCulled=false;m.castShadow=false;m.receiveShadow=false;m.count=0;if(p.tint){m.setColorAt(0,new THREE.Color('#fff'))}trafficGroup.add(m);return m});return traffic.inst[kind]={def,meshes}
}
const hashId=id=>{let h=0;for(const c of String(id))h=(h*31+c.charCodeAt(0))>>>0;return h};
function syncTraffic(){
 const list=Array.isArray(state.traffic)?state.traffic:[],now=performance.now(),ids=new Set();
 for(const v of list){if(!Array.isArray(v.position)||v.id==null)continue;ids.add(v.id);const target=vec(v.position);let r=traffic.map.get(v.id);
  if(!r){const kind=TRAFFIC_DEF[v.kind]?v.kind:'car',def=TRAFFIC_DEF[kind];r={kind,pos:target.clone(),from:target.clone(),to:target.clone(),t0:now,hdg:null,color:new THREE.Color(def.palette[hashId(v.id)%def.palette.length]),speed:0};traffic.map.set(v.id,r)}
  else{r.from.copy(r.pos);if(r.from.distanceTo(target)>400)r.from.copy(target);r.to.copy(target);r.t0=now}
  if(r.hdg==null){let h=Number(v.heading);if(isFinite(h)){if(Math.abs(h)>Math.PI*2)h*=Math.PI/180;r.hdg=h}else r.hdg=0}else if(v.heading!=null&&r.from.distanceToSquared(r.to)<.2){let h=Number(v.heading);if(isFinite(h)){if(Math.abs(h)>Math.PI*2)h*=Math.PI/180;r.hdgGoal=h}}
  r.speed=Number(v.speed_mps)||0}
 for(const id of traffic.map.keys())if(!ids.has(id))traffic.map.delete(id);
}
const _m=new THREE.Matrix4(),_v=new THREE.Vector3(),_q=new THREE.Quaternion(),_s=new THREE.Vector3(1,1,1),_Y=new THREE.Vector3(0,1,0),_pm=new THREE.Matrix4();
const angDiff=(a,b)=>{let d=b-a;while(d>Math.PI)d-=Math.PI*2;while(d<-Math.PI)d+=Math.PI*2;return d};
function stepTraffic(now,dt,live){
 const visible={car:[],auto:[],bike:[],bus:[]},cull=region.on?1400:900;let total=0;
 const k0=Math.min(1,packetDur*1.15);
 for(const r of traffic.map.values()){
  const k=Math.min(1,(now-r.t0)/(packetDur*1.1));r.pos.lerpVectors(r.from,r.to,k);
  const dx=r.to.x-r.from.x,dn=-(r.to.z-r.from.z);if(dx*dx+dn*dn>.6)r.hdgGoal=Math.atan2(dn,dx);
  if(r.hdgGoal!=null){r.hdg+=angDiff(r.hdg,r.hdgGoal)*(1-Math.exp(-dt*8))}
  if(r.pos.distanceToSquared(camera.position)<cull*cull&&showTraffic)visible[r.kind].push(r);total++;
 }
 traffic.count=total;let drawn=0;
 for(const kind of Object.keys(visible)){
  const list=visible[kind];if(!list.length&&!traffic.inst[kind])continue;const t=trafficMesh(kind),n=Math.min(list.length,traffic.max);
  t.meshes.forEach((mesh,pi)=>{mesh.count=n});
  for(let i=0;i<n;i++){const r=list[i];_q.setFromAxisAngle(_Y,r.hdg);_v.set(r.pos.x,r.pos.y+.05,r.pos.z);_pm.compose(_v,_q,_s);
   t.meshes.forEach((mesh,pi)=>{const p=t.def.parts[pi];_m.multiplyMatrices(_pm,p.m);mesh.setMatrixAt(i,_m);if(p.tint)mesh.setColorAt(i,r.color)})}
  t.meshes.forEach((mesh,pi)=>{mesh.instanceMatrix.needsUpdate=true;if(t.def.parts[pi].tint&&mesh.instanceColor)mesh.instanceColor.needsUpdate=true});drawn+=n;
 }
 traffic.drawn=drawn;
}

/* ====================== decentralized coordination: mesh links, gateways, sub-tasks ====================== */
const meshGroup=new THREE.Group();scene.add(meshGroup);
const mesh={segs:null,cap:0,pairs:[],subs:new Map(),subSegs:null,subCap:0,subPairs:[],on:false};
const SUB_COLOR={open:'#8e8e93',assigned:'#e9a21c',enroute:'#e9812a',executing:'#2d7ff0',done:'#2e9e6b',closed:'#b0b0b5'};
const _c1=new THREE.Color('#d9534f'),_c2=new THREE.Color('#e9a21c'),_c3=new THREE.Color('#1fa59a'),_cc=new THREE.Color();
function qualityColor(q){q=Math.max(0,Math.min(1,Number(q)||0));return q<.5?_cc.copy(_c1).lerp(_c2,q/.5):_cc.copy(_c2).lerp(_c3,(q-.5)/.5)}
function makeSegs(cap,opacity){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(new Float32Array(cap*6),3));g.setAttribute('color',new THREE.BufferAttribute(new Float32Array(cap*6),3));g.setDrawRange(0,0);const l=new THREE.LineSegments(g,new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity,depthTest:false,depthWrite:false}));l.frustumCulled=false;l.renderOrder=3;meshGroup.add(l);return l}
function ensureSegs(key,capKey,need,opacity){if(mesh[key]&&mesh[capKey]>=need)return;if(mesh[key]){meshGroup.remove(mesh[key]);mesh[key].geometry.dispose();mesh[key].material.dispose()}mesh[capKey]=need+64;mesh[key]=makeSegs(mesh[capKey],opacity)}
function subMarker(st){
 const g=new THREE.Group(),col=SUB_COLOR[st.status]||'#8e8e93',m=new THREE.Mesh(new THREE.OctahedronGeometry(4.2),new THREE.MeshBasicMaterial({color:col,transparent:true,opacity:.95,depthTest:false}));m.position.y=18;m.renderOrder=6;g.add(m);
 const pole=new THREE.Mesh(cylinderGeo,new THREE.MeshBasicMaterial({color:col,transparent:true,opacity:.6}));pole.scale.set(.35,16,.35);pole.position.y=8;g.add(pole);
 const ring=new THREE.Mesh(new THREE.RingGeometry(5,6.2,28).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color:col,transparent:true,opacity:.8,side:THREE.DoubleSide,depthTest:false}));ring.position.y=.8;ring.renderOrder=5;g.add(ring);
 g.userData={marker:m,pole,ring,text:'',status:''};scene.add(g);return g}
function syncMesh(){
 const links=Array.isArray(state.mesh?.links)?state.mesh.links:[],subs=Array.isArray(state.subtasks)?state.subtasks:[];
 mesh.on=links.length>0||subs.length>0;
 mesh.pairs=[];for(const l of links){const a=droneObjects.get(l[0]),b=droneObjects.get(l[1]);if(a&&b)mesh.pairs.push([a,b,Number(l[2])])}
 ensureSegs('segs','cap',mesh.pairs.length,.34);if(mesh.segs){const ca=mesh.segs.geometry.attributes.color;mesh.pairs.forEach((p,i)=>{const c=qualityColor(p[2]);ca.setXYZ(i*2,c.r,c.g,c.b);ca.setXYZ(i*2+1,c.r,c.g,c.b)});ca.needsUpdate=true}
 const gw=new Set((state.drones||[]).filter(d=>d.coordination?.gateway).map(d=>d.id)),some=gw.size>0&&gw.size<(state.drones||[]).length;
 for(const [id,o] of droneObjects){const on=some&&gw.has(id);let r=o.userData.gw;if(on&&!r){r=new THREE.Mesh(new THREE.RingGeometry(11,12.4,40).rotateX(-Math.PI/2),new THREE.MeshBasicMaterial({color:'#7c5cc4',transparent:true,opacity:.95,depthTest:false,side:THREE.DoubleSide}));r.position.y=-5;r.renderOrder=8;o.add(r);o.userData.gw=r}if(r)r.visible=on}
 const seen=new Set();mesh.subPairs=[];
 for(const st of subs){if(!Array.isArray(st.position))continue;seen.add(st.id);let g=mesh.subs.get(st.id);const v=vec(st.position);
  if(!g){g=subMarker(st);mesh.subs.set(st.id,g)}g.position.set(v.x,groundHeight(v.x,-v.z),v.z);
  const u=g.userData,text=`${nice(st.kind)} · ${st.winner||'open'}`;
  if(u.status!==st.status){u.status=st.status;const col=SUB_COLOR[st.status]||'#8e8e93';for(const m of [u.marker,u.pole,u.ring])m.material.color.set(col);const fade=st.status==='closed'?.35:1;u.marker.material.opacity=.95*fade;u.pole.material.opacity=.6*fade;u.ring.material.opacity=.8*fade}
  if(u.text!==text||u.statusLabel!==st.status){u.text=text;u.statusLabel=st.status;if(u.tag){g.remove(u.tag);u.tag.material.map.dispose();u.tag.material.dispose()}const tag=label(`${text} · ${nice(st.status)}`,SUB_COLOR[st.status]||'#8e8e93',5);tag.position.y=32;g.add(tag);u.tag=tag}
  u.item=st;if(st.winner&&droneObjects.get(st.winner))mesh.subPairs.push([droneObjects.get(st.winner),g,st.status])}
 for(const [id,g] of mesh.subs)if(!seen.has(id)){scene.remove(g);disposeGroup(g);mesh.subs.delete(id)}
 ensureSegs('subSegs','subCap',mesh.subPairs.length,.55);
 if(mesh.subSegs){const ca=mesh.subSegs.geometry.attributes.color;mesh.subPairs.forEach((p,i)=>{_cc.set(SUB_COLOR[p[2]]||'#8e8e93');ca.setXYZ(i*2,_cc.r,_cc.g,_cc.b);ca.setXYZ(i*2+1,_cc.r,_cc.g,_cc.b)});ca.needsUpdate=true}
}
function stepMesh(){
 const show=showMesh&&mesh.on;
 if(mesh.segs){mesh.segs.visible=show;if(show){const pa=mesh.segs.geometry.attributes.position;let n=0;for(const [a,b] of mesh.pairs){if(!a.visible||!b.visible)continue;pa.setXYZ(n*2,a.position.x,a.position.y,a.position.z);pa.setXYZ(n*2+1,b.position.x,b.position.y,b.position.z);n++}pa.needsUpdate=true;mesh.segs.geometry.setDrawRange(0,n*2)}}
 const k=Math.max(1,Math.min(12,camDist/320));
 for(const g of mesh.subs.values()){g.visible=show;g.scale.setScalar(k);const t=g.userData.tag;if(t){if(!t.userData.s0)t.userData.s0=t.scale.clone();t.scale.set(t.userData.s0.x/k,t.userData.s0.y/k,1)}}
 if(mesh.subSegs){mesh.subSegs.visible=show;if(show){const pa=mesh.subSegs.geometry.attributes.position;let n=0;for(const [d,g] of mesh.subPairs){pa.setXYZ(n*2,d.position.x,d.position.y,d.position.z);pa.setXYZ(n*2+1,g.position.x,g.position.y+18*k,g.position.z);n++}pa.needsUpdate=true;mesh.subSegs.geometry.setDrawRange(0,n*2)}}
}
function renderMeshChip(){
 const chip=$('mesh-chip'),m=state.mesh,mode=state.coordination?.mode;const cs=$('coord-select');if(mode&&document.activeElement!==cs)cs.value=mode;
 if(!m||(mode!=='decentralized'&&!(m.messages_sent>0))){chip.hidden=true;return}
 const part=Array.isArray(m.partitions)?m.partitions.length:Number(m.partitions)||0,cons=m.by_kind?.consensus??m.messages_sent??0;chip.hidden=false;
 setHTML(chip,`<b>${cons}</b> consensus msgs &middot; <b>${m.dropped??0}</b> dropped &middot; <b>${part}</b> partition${part===1?'':'s'}`);
}
$('coord-select').onchange=e=>{command('set_coordination',{mode:e.target.value});e.target.blur()};

/* ====================== command center timeline ====================== */
const MSG_KIND={task:['Task','#2f80ed'],accept:['Accepted','#2e9e6b'],decline:['Declined','#d9534f'],report:['Report','#7c5cc4'],status:['Status','#6e8997'],resource_request:['Resource request','#d9822b'],ack:['Ack','#1f9d8f'],cancel:['Cancelled','#9a6b6b'],handoff:['Handoff','#2f9db8'],situation_report:['Situation report','#2f80ed'],alert:['Alert','#d70015'],dispatch:['Dispatch','#e08a2e'],unit_status:['Unit status','#2e9e6b']};
const AGENCY={ambulance:['Ambulance','#d6453d'],fire:['Fire','#e9812a'],rescue:['Rescue','#1f9d8f'],police:['Police','#455a8a'],works:['Public works','#8a6f2a']};
const agencyOf=n=>{n=String(n||'').toLowerCase();return /ambul|^ems/.test(n)?AGENCY.ambulance:/fire/.test(n)?AGENCY.fire:/rescue/.test(n)?AGENCY.rescue:/polic/.test(n)?AGENCY.police:/public|works|engineer/.test(n)?AGENCY.works:null};
const alertSev=m=>{const b=m.body||{};return (/life_threat|critical|high|severe/i.test(String(b.alert)+' '+b.severity)||Number(b.severity)>=4)?'bad':'warn'};
const isCommandName=n=>/command|disruption|ops|center|centre|control|dispatch/i.test(String(n||''))&&!/^D\d/i.test(String(n||''));
function fmtBody(m){
 let b=m.body;const parts=[];if(m.text)parts.push(String(m.text));
 if(b&&typeof b==='object'){const e=b.eta_s??b.eta;if(e!=null)parts.push(`ETA ${Math.round(e)} s`);if(b.reason)parts.push(`Reason: ${nice(b.reason)}`);if(b.distance_m!=null)parts.push(`${Math.round(b.distance_m)} m away`);if(b.battery!=null)parts.push(`battery ${Math.round(b.battery*(b.battery<=1?100:1))}%`);if(b.summary)parts.push(String(b.summary));
  for(const [k,v] of m.text?[]:Object.entries(b)){if(parts.length>=6)break;if(['eta_s','eta','reason','distance_m','battery','summary','text'].includes(k)||typeof v==='object')continue;parts.push(`${nice(k)}: ${nice(v)}`)}}
 else if(b!=null&&b!==''&&b!==m.text)parts.push(String(b));
 return parts.join(' · ');
}

function sitInner(b){
 const p=b.people||{},inj=b.injured||{},hz=b.hazards||[],units=(b.nearby_units||[]).slice(0,4),conf=b.confidence,pct=conf==null?null:Math.round(conf*100);
 let h=`<div class="sit-grid"><span>People</span><b>${p.estimate!=null?`~${escapeHTML(p.estimate)}${p.low!=null&&p.high!=null?` (${escapeHTML(p.low)}&ndash;${escapeHTML(p.high)})`:''}`:'&mdash;'}</b><span>Injured</span><b>${escapeHTML(inj.estimate??0)}${inj.severity&&inj.severity!=='none'?` &middot; ${escapeHTML(nice(inj.severity))}`:''}</b>`;
 if(hz.length)h+=`<span>Hazards</span><b>${hz.map(x=>`<em class="hz ${escapeHTML(x.level)}">${escapeHTML(nice(x.kind))} ${escapeHTML(nice(x.level))}${x.trend==='rising'||x.trend==='worsening'?' &uarr;':x.trend==='falling'?' &darr;':''}</em>`).join('')}</b>`;
 if(b.access?.best_approach)h+=`<span>Access</span><b>${escapeHTML(b.access.best_approach)}</b>`;
 h+='</div>';
 if(units.length)h+=`<div class="sit-units">${units.map(u=>`<div class="${u.assigned_here?'here':''}"><span>${escapeHTML(u.id)} ${escapeHTML(nice(u.kind))}${u.assigned_here?' &middot; assigned':''}${u.state&&u.state!=='available'?` &middot; ${escapeHTML(nice(u.state))}`:''}</span><i>${Math.round(u.distance_m)} m &middot; ETA ${Math.round(u.eta_s)} s</i></div>`).join('')}</div>`;
 if(pct!=null)h+=`<div class="conf" title="Confidence ${pct}%"><i style="width:${pct}%"></i></div><div class="msg-meta"><span>confidence ${pct}%</span>${b.reason?`<span>${escapeHTML(nice(b.reason))}</span>`:''}${b.revision!=null?`<span>rev ${escapeHTML(b.revision)}</span>`:''}</div>`;
 return h;
}
function bubbleHTML(m){
 const [lab,col0]=MSG_KIND[m.kind]||[nice(m.kind),'#6e8997'],cmd=isCommandName(m.from),agent=cmd?m.to:m.from,ag=agencyOf(m.kind==='dispatch'?m.to:m.from)||agencyOf(m.to),col=ag?ag[1]:col0;
 const attrs=`data-msg-incident="${escapeHTML(m.incident_id||'')}" data-msg-agent="${escapeHTML(agent||'')}" style="--c:${col}"`;
 const head=(title,extra='')=>`<div class="msg-head"><b>${title}</b>${extra}<time>${clock(m.t)}</time></div>`;
 if(m.kind==='situation_report'){const b=m.body||{};return `<button class="msg sit" ${attrs}>${head('Situation report',`<span>${escapeHTML(m.incident_id||'')}</span>`)}${sitInner(b)}</button>`}
 if(m.kind==='alert'){const b=m.body||{},sev=alertSev(m);return `<button class="msg alert ${sev}" ${attrs}>${head(`Alert &middot; ${escapeHTML(nice(b.alert||'alert'))}`,`<span>${escapeHTML(m.incident_id||b.drone_id||'')}</span>`)}<div class="msg-text">${escapeHTML(m.text||fmtBody(m))}</div><div class="msg-meta"><span>${sev==='bad'?'urgent':'attention'}</span>${b.drone_id?`<span>${escapeHTML(b.drone_id)}</span>`:''}${b.fault?`<span>${escapeHTML(nice(b.fault))}</span>`:''}</div></button>`}
 return `<button class="msg ${cmd?'from-cmd':'from-agent'}" ${attrs}><div class="msg-head"><b>${escapeHTML(m.from||'?')}</b><span>to ${escapeHTML(m.to||'all')}</span><time>${clock(m.t)}</time></div><div class="msg-text">${escapeHTML(fmtBody(m)||nice(m.kind))}</div><div class="msg-meta"><span class="kind">${escapeHTML(lab)}</span>${ag?`<span class="agency-tag">${escapeHTML(ag[0])}</span>`:''}${m.incident_id?`<span>${escapeHTML(m.incident_id)}</span>`:''}${m.request_id?`<span>${escapeHTML(m.request_id)}</span>`:''}</div></button>`;
}
let msgFilter='',msgSig='',msgOptSig='';
function renderCommand(){
 const msgs=Array.isArray(state.messages)?state.messages:null;const list=$('msg-list'),chips=$('task-chips');if(!list)return;
 if(!msgs){setHTML(list,'<p class="muted">This server does not publish command-center messages yet.</p>');setHTML(chips,'');return}
 const incs=new Set((state.incidents||[]).map(i=>i.id));for(const m of msgs)if(m.incident_id)incs.add(m.incident_id);
 const optSig=[...incs].join(',');const filter=$('msg-filter');if(optSig!==msgOptSig){msgOptSig=optSig;filter.innerHTML='<option value="">All incidents</option>'+[...incs].map(i=>`<option value="${escapeHTML(i)}">${escapeHTML(i)}</option>`).join('');filter.value=msgFilter}
 const shown=msgs.filter(m=>!msgFilter||m.incident_id===msgFilter).slice(-150);
 const sig=shown.length+':'+(shown[shown.length-1]?.id??'')+':'+msgFilter+':'+selected?.id;
 // active task chips: latest message per open incident
 const latest=new Map();for(const m of msgs)if(m.incident_id)latest.set(m.incident_id,m);
 const chipHTML=(state.incidents||[]).filter(i=>!['resolved','completed'].includes(i.status)).map(i=>{const m=latest.get(i.id),[lab,col]=m?(MSG_KIND[m.kind]||[nice(m.kind),'#6e8997']):['Awaiting','#9aa6b0'];const who=m?(isCommandName(m.from)?m.to:m.from):i.drone_id;return `<button class="task-chip" data-msg-focus-incident="${escapeHTML(i.id)}" style="--c:${col}"><i></i><b>${escapeHTML(i.id)}</b> ${escapeHTML(lab)}${who?` · ${escapeHTML(who)}`:''}</button>`}).join('');
 setHTML(chips,chipHTML||'<span class="muted">No active tasks.</span>');
 if(sig===msgSig)return;msgSig=sig;
 const atBottom=list.scrollTop+list.clientHeight>=list.scrollHeight-30;
 const html=shown.map(bubbleHTML).join('');
 setHTML(list,html||'<p class="muted">No messages yet.</p>');if(atBottom)list.scrollTop=list.scrollHeight;
}
$('msg-filter').onchange=e=>{msgFilter=e.target.value;msgSig='';if(state)renderCommand()};
function focusMessage(incident,agent){
 if(agent&&!(state.drones||[]).some(d=>d.id===agent)&&!(state.resources||[]).some(d=>d.id===agent)){const ids=[...(state.drones||[]),...(state.resources||[])].map(x=>x.id).filter(id=>String(agent).includes(id));if(ids.length)agent=ids[0]}
 if(incident&&(state.incidents||[]).some(i=>i.id===incident))select('incident',incident);
 else if(agent&&(state.drones||[]).some(d=>d.id===agent))select('drone',agent);
 else if(agent&&(state.resources||[]).some(d=>d.id===agent))select('resource',agent);
 else if(incident)toast(`${incident} is no longer on the map.`);
}
function decisionHTML(item,row){
 let html='';const ld=item.last_decision,recent=(Array.isArray(state.decisions)?state.decisions:[]).filter(d=>d.agent===item.id||d.drone_id===item.id).slice(-5).reverse();
 if(!ld&&!recent.length)return '';
 html+='<div class="detail-divider"></div><p class="doing-now"><b>Agent decision</b></p>';
 if(ld){const o=typeof ld==='object'?ld:{decision:ld};html+=row('Decision',nice(o.decision??o.action??'—'))+(o.reason?row('Reason',nice(o.reason)):'')+((o.options||[]).length?row('Options',o.options.slice(0,4).map(x=>`${x.id} ${x.score!=null?Number(x.score).toFixed(2):''}`).join(' · ')):'')}
 if(recent.length)html+='<div class="dec-list">'+recent.map(d=>`<div><time>${clock(d.t)}</time><b>${escapeHTML(nice(d.decision))}</b>${d.reason?` <span>${escapeHTML(nice(d.reason))}</span>`:''}</div>`).join('')+'</div>';
 return html;
}

/* ====================== scenario selector (grouped by world) ====================== */
const STATIC_SCENARIOS=[{id:'rooftop_flood',name:'Rooftop flood rescue',world:'pune-district'},{id:'medical_supply',name:'Critical medical supply',world:'pune-district'},{id:'blocked_bridge',name:'Bridge evacuation',world:'pune-district'},{id:'dock_fire',name:'Dock fire response',world:'pune-district'},{id:'pccoe_pawana_night',name:'PCCOE Pawana night flood',world:'pcmc-region'},{id:'pcmc_random',name:'PCMC random incidents',world:'pcmc-region'}];
const WORLD_NAME={'pune-district':'Pune district','pcmc-region':'PCMC region'};
let scenarioSig='',catalog=null;
fetch('/ops/scenarios',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(j=>{if(j?.scenarios){catalog=j.scenarios;scenarioSig='';if(state)renderUI()}}).catch(()=>{});
function renderScenarioOptions(current){
 const sel=$('scenario-select');if(document.activeElement===sel)return;
 let list=[...(catalog||state.scenarios||state.available_scenarios||[])].map(s=>typeof s==='string'?{id:s}:s);
 if(!list.length)list=STATIC_SCENARIOS.filter(s=>s.world==='pune-district'||worldData?.id==='pcmc-region'||worldData?.kind==='region'||true);
 if(current&&!list.some(s=>s.id===current))list.push({id:current});
 const worldOf=s=>s.world_id||(s.world_id===null&&!/pcmc|pccoe|pawana/.test(s.id)?'pune-district':null)||(typeof s.world==='string'?s.world:s.world?.id)||s.world_kind||(/pcmc|pccoe|pawana/.test(s.id)?'pcmc-region':'pune-district');
 const sig=list.map(s=>s.id+worldOf(s)).join(',');if(sig===scenarioSig)return;scenarioSig=sig;
 const groups=new Map();for(const s of list){const w=worldOf(s),w2=w==='region'?'pcmc-region':w;if(!groups.has(w2))groups.set(w2,[]);groups.get(w2).push(s)}
 sel.innerHTML=[...groups].map(([w,items])=>`<optgroup label="${escapeHTML(WORLD_NAME[w]||nice(w))}">${items.map(s=>`<option value="${escapeHTML(s.id)}">${escapeHTML(s.name||s.title||STATIC_SCENARIOS.find(x=>x.id===s.id)?.name||nice(s.id))}</option>`).join('')}</optgroup>`).join('');
 if(current)sel.value=current;
}

window.__ops={meshState:mesh,subtaskObjects:mesh.subs,region,proc,traffic,get trafficCount(){return traffic.count},get trafficDrawn(){return traffic.drawn||0},resourceObjects,incidentObjects,select,flyTo,get camDist(){return camDist},renderer,THREE,get world(){return worldData},peopleObjects,searchGroup,camera,controls,groundHeight,get state(){return state},get selected(){return selected},get flying(){return !!flight},droneObjects,signalObjects,dockObjects,footObjects,routeLines};

/* ====================== live operations log (virtualised) ====================== */
const LOG={rows:[],keys:new Set(),textKeys:new Set(),filtered:[],filter:'all',q:'',live:true,ROW:50,open:true,w:340,ver:0,drawn:'',unseen:0,CAP:6000,dirty:false,expanded:new Set(),hmap:new Map(),off:[0],total:0,xv:0};
const LOG_GLYPH={task:['→','k-blue'],accept:['✓','k-ok'],decline:['×','k-bad'],report:['·','k-mute'],status:['·','k-mute'],resource_request:['+','k-warn'],ack:['✓','k-ok'],cancel:['×','k-mute'],handoff:['⇄','k-blue'],decision:['◆','k-blue'],
 incident:['!','k-warn'],sos:['!','k-bad'],briefing:['i','k-blue'],dispatch:['↗','k-blue'],arrival:['●','k-ok'],recall:['↩','k-mute'],supply_request:['+','k-warn'],fault:['!','k-bad'],crash:['!','k-bad'],flood_routes:['≈','k-mute'],situation_report:['≡','k-blue'],alert:['!','k-bad'],unit_status:['●','k-ok'],dispatch_order:['↗','k-blue']};
const LOG_ALERT=/alert|fault|crash|fail|declin|link|battery|lost|sos|blocked|critical|unavailable|collision|emergency|incident|degraded|closed/i,LOG_CMD=/command|disruption|control|dispatch/i;
function logTags(r,resIds){
 const t=new Set(),agent=String(r.agent||r.actor||''),text=r.text||'';
 if(r.src==='decision')t.add('decisions');
 if(r.src==='message'||LOG_CMD.test(String(r.actor))||['task','ack','cancel','handoff','briefing','dispatch','dispatch_order','situation_report','unit_status'].includes(r.kind))t.add('command');
 if(['situation_report','report'].includes(r.kind))t.add('reports');
 if(LOG_ALERT.test(r.kind)||(r.kind!=='accept'&&/fault|crash|declin|link loss|low battery|sos|failed/i.test(text)))t.add('alerts');
 if(/^D\d+/i.test(agent)||/^D\d+\b/.test(text)||r.src==='decision'&&/^D\d+/i.test(String(r.agent||''))||r.from_type==='drone')t.add('swarm');
 if(resIds.has(agent)||/_service|public_works/.test(agent)||/_service|public_works/.test(String(r.actor))||/\b(PC-[A-Z]+-?\d*|R\d{2}|ambulance|fire (engine|tender)|rescue team|boat|JCB|tanker)\b/i.test(text)||['arrival','dispatch','handoff'].includes(r.kind)&&!/^D\d+/i.test(agent))t.add('agencies');
 if(r.src==='message'&&!t.has('swarm')&&/swarm|^D\d+/i.test(String(r.meta||'')))t.add('swarm');
 return t;
}
function logReset(){LOG.rows=[];LOG.keys.clear();LOG.textKeys.clear();LOG.filtered=[];LOG.ver++;LOG.unseen=0;LOG.live=true;LOG.dirty=true}
function ingestLog(st){
 const out=[],resIds=new Set((st.resources||[]).map(r=>r.id));
 const add=(src,id,r)=>{const key=src+':'+id;if(LOG.keys.has(key))return;LOG.keys.add(key);const tk=Math.round(r.t||0)+'|'+(r.text||'');if(src!=='m'&&src!=='d'&&LOG.textKeys.has(tk))return;LOG.textKeys.add(tk);r.k=key;r.src=src==='e'?'event':src==='s'?'signal':src==='m'?'message':'decision';r.tags=null;out.push(r)};
 const dispIds=new Set((st.messages||[]).filter(m=>m.kind==='dispatch').map(m=>m.request_id));
 for(const e of st.events||[]){if(e.kind==='dispatch_order'&&dispIds.has(e.data?.request_id))continue;add('e',e.id??`${e.time}|${e.message}`,{t:e.time||0,kind:e.kind||'event',actor:e.drone_id||e.resource_id||e.actor||'',text:e.message||'',incident:e.incident_id,agent:e.drone_id||e.resource_id||''})}
 for(const s of st.signals||[])if(s?.id!=null)add('s',s.id,{t:s.t||0,kind:s.kind||'signal',actor:s.from_id||s.from_type||'',text:s.text||nice(s.kind),incident:s.incident_id,agent:s.from_id,from_type:s.from_type});
 for(const m of st.messages||[]){if(m?.id==null)continue;const cmd=isCommandName(m.from);add('m',m.id,{t:m.t||0,kind:m.kind||'status',actor:m.from||'',text:m.text||fmtBody(m)||nice(m.kind),meta:`${m.from||'?'} → ${m.to||'all'}`,incident:m.incident_id,agent:cmd?m.to:m.from,sev:m.kind==='alert'?alertSev(m):undefined})}
 for(const d of st.decisions||[]){const id=d.id??`${d.t}|${d.agent}|${d.decision}|${d.incident_id||''}`,why=d.why||d.reason||d.summary||'';add('d',id,{t:d.t||0,kind:'decision',actor:d.drone_id||d.agent||'swarm',agent:d.drone_id||d.agent,text:nice(d.decision||d.kind)+(d.incident_id?` for ${d.incident_id}`:''),why,incident:d.incident_id,options:d.options,routine:/^(search|patrol|hold|idle|continue|charge|return|scan|wait)/i.test(String(d.decision||d.kind||''))})}
 if(!out.length)return;
 out.sort((a,b)=>(a.t||0)-(b.t||0));
 for(const r of out){r.tags=logTags(r,resIds);LOG.rows.push(r)}
 if(LOG.rows.length>LOG.CAP)LOG.rows.splice(0,LOG.rows.length-LOG.CAP);
 LOG.ver++;LOG.dirty=true;if(!LOG.live)LOG.unseen+=out.length;
}
function logRefilter(){const f=LOG.filter,q=LOG.q.trim().toLowerCase();LOG.filtered=LOG.rows.filter(r=>(f==='all'?!r.routine:r.tags.has(f))&&(!q||(r.text+' '+(r.actor||'')+' '+(r.why||'')+' '+(r.incident||'')+' '+r.kind).toLowerCase().includes(q)))}
const logEst=r=>{const cpl=Math.max(20,Math.floor((LOG.w-110)/6.2));return 16+Math.ceil((r.text||'').length/cpl)*16+30+28};
function logLayout(){const f=LOG.filtered,off=new Array(f.length+1);let y=0;for(let i=0;i<f.length;i++){off[i]=y;const k=f[i].k;y+=LOG.expanded.has(k)?(LOG.hmap.get(k)||logEst(f[i])):LOG.ROW}off[f.length]=y;LOG.off=off;LOG.total=y}
function logIndexAt(y){let lo=0,hi=LOG.filtered.length;while(lo<hi){const mid=(lo+hi)>>1;if(LOG.off[mid+1]<=y)lo=mid+1;else hi=mid}return lo}
function renderLog(force){
 const panel=$('oplog');if(!LOG.open||!panel||$('rpanel-log').hidden)return;const sc=$('oplog-scroll');
 if(LOG.dirty||force){logRefilter();logLayout();LOG.dirty=false;$('oplog-count').textContent=LOG.filtered.length;$('oplog-spacer').style.height=LOG.total+'px';if(LOG.live)sc.scrollTop=sc.scrollHeight}
 const n=LOG.filtered.length,top=sc.scrollTop,vh=sc.clientHeight||400,a=Math.max(0,logIndexAt(top)-3),b=Math.min(n,logIndexAt(top+vh)+4),sig=a+':'+b+':'+LOG.ver+':'+LOG.filter+':'+LOG.q+':'+n+':'+LOG.xv+':'+LOG.total;
 if(sig===LOG.drawn&&!force)return;LOG.drawn=sig;
 let html='';
 if(!n)html=`<div class="logempty">${LOG.q||LOG.filter!=='all'?'Nothing matches this filter yet.':'Waiting for activity…'}</div>`;
 for(let i=a;i<b;i++){const r=LOG.filtered[i],g=r.kind==='alert'&&r.sev==='warn'?['!','k-warn']:(LOG_GLYPH[r.kind]||['·','k-mute']),open=LOG.expanded.has(r.k),meta=[r.actor,r.meta&&r.meta!==r.actor?r.meta:'',nice(r.kind),r.incident,orchIncidents.has(r.incident)?'via DisruptionOps':''].filter(Boolean).filter((v,j,arr)=>arr.indexOf(v)===j).join(' · ');
  html+=`<div class="logrow${open?' open':''}${r.sev?' sev-'+r.sev:''}" role="button" tabindex="0" aria-expanded="${open}" data-i="${i}" style="top:${LOG.off[i]}px${open?'':';height:'+LOG.ROW+'px'}"><time>${clock(r.t)}</time><i class="glyph ${g[1]}">${g[0]}</i><span class="logmain"><span class="logtext">${escapeHTML(r.text)}</span><span class="logmeta">${escapeHTML(r.why?`why: ${r.why}`:meta)}</span>${open&&(r.incident||r.agent)?`<button class="logmore" data-focus-row="${i}">Show on map</button>`:''}</span></div>`}
 $('oplog-rows').innerHTML=html;
 let changed=false;for(const el of $('oplog-rows').querySelectorAll('.logrow.open')){const r=LOG.filtered[+el.dataset.i];if(!r)continue;const h=Math.ceil(el.offsetHeight);if(Math.abs((LOG.hmap.get(r.k)||0)-h)>1){LOG.hmap.set(r.k,h);changed=true}}
 if(changed)requestAnimationFrame(()=>{LOG.xv++;renderLog(true)});
 const jump=$('oplog-jump');jump.hidden=LOG.live;jump.textContent=LOG.unseen?`Jump to live · ${LOG.unseen} new`:'Jump to live';$('oplog-live').classList.toggle('paused',!LOG.live);$('oplog-live').title=LOG.live?'Following live':'Paused - scroll to the end to follow';
}
{
 const sc=$('oplog-scroll'),viewport=host.parentElement;let raf=0;const kick=()=>{if(!raf)raf=requestAnimationFrame(()=>{raf=0;renderLog()})};
 sc.addEventListener('scroll',()=>{const live=sc.scrollHeight-sc.scrollTop-sc.clientHeight<40;if(live!==LOG.live){LOG.live=live;if(live)LOG.unseen=0;LOG.drawn=''}kick()},{passive:true});
 $('oplog-jump').onclick=()=>{LOG.live=true;LOG.unseen=0;sc.scrollTop=sc.scrollHeight;LOG.drawn='';renderLog(true)};
 $('oplog-filters').addEventListener('click',e=>{const b=e.target.closest('[data-f]');if(!b)return;LOG.filter=b.dataset.f;for(const x of $('oplog-filters').children)x.classList.toggle('active',x===b);LOG.live=true;LOG.dirty=true;renderLog(true)});
 let qt=0;$('oplog-search').addEventListener('input',e=>{clearTimeout(qt);qt=setTimeout(()=>{LOG.q=e.target.value;LOG.dirty=true;renderLog(true)},120)});
 const act=i=>{const r=LOG.filtered[Number(i)];if(r)focusMessage(r.incident,r.agent)};
 const toggleRow=el=>{const r=LOG.filtered[Number(el.dataset.i)];if(!r)return;if(LOG.expanded.has(r.k))LOG.expanded.delete(r.k);else LOG.expanded.add(r.k);LOG.xv++;LOG.drawn='';renderLog(true)};
 $('oplog-rows').addEventListener('click',e=>{const f=e.target.closest('[data-focus-row]');if(f){act(f.dataset.focusRow);return}const el=e.target.closest('.logrow');if(el)toggleRow(el)});
 $('oplog-rows').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){const el=e.target.closest('.logrow');if(el){e.preventDefault();toggleRow(el)}}});
 const setOpen=on=>{LOG.open=on;$('oplog').hidden=!on;viewport.classList.toggle('log-closed',!on);viewport.style.setProperty('--log-offset',on?(LOG.w+16)+'px':'0px');const t=$('toggle-log');t.classList.toggle('active',on);t.setAttribute('aria-pressed',String(on));try{localStorage.setItem('ops-log-open',on?'1':'0')}catch{}if(on){LOG.drawn='';LOG.dirty=true;requestAnimationFrame(()=>renderLog(true))}};
 const setWidth=w=>{LOG.w=Math.max(300,Math.min(Math.min(720,viewport.clientWidth-340),w));LOG.hmap.clear();viewport.style.setProperty('--log-w',LOG.w+'px');if(LOG.open)viewport.style.setProperty('--log-offset',(LOG.w+16)+'px')};
 $('toggle-log').onclick=()=>setOpen(!LOG.open);$('oplog-close').onclick=()=>setOpen(false);$('oplog-open').onclick=()=>setOpen(true);
 const rz=$('oplog-resize');rz.addEventListener('pointerdown',e=>{rz.setPointerCapture(e.pointerId);rz.classList.add('drag');viewport.style.setProperty('--ease','linear')});
 rz.addEventListener('pointermove',e=>{if(!rz.classList.contains('drag'))return;setWidth(viewport.getBoundingClientRect().right-e.clientX-16);LOG.drawn=''});
 rz.addEventListener('pointerup',e=>{rz.classList.remove('drag');viewport.style.removeProperty('--ease');try{localStorage.setItem('ops-log-w2',String(LOG.w))}catch{}kick()});
 rz.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'){setWidth(LOG.w+24);e.preventDefault()}if(e.key==='ArrowRight'){setWidth(LOG.w-24);e.preventDefault()}});
 let w=340,open=innerWidth>=1200;try{w=Number(localStorage.getItem('ops-log-w2'))||340;const o=localStorage.getItem('ops-log-open');if(o!=null)open=o==='1'}catch{}
 setWidth(w);setOpen(open);new ResizeObserver(kick).observe(sc);
 window.__log=LOG;
}
/* dark / light scene follows the system appearance */
{const mq=matchMedia('(prefers-color-scheme: dark)'),apply=()=>{const css=getComputedStyle(document.documentElement);const bg=css.getPropertyValue('--scene').trim(),fog=css.getPropertyValue('--scene-fog').trim();scene.background=new THREE.Color(bg);scene.fog.color.set(fog)};apply();mq.addEventListener?.('change',apply)}


/* ====================== frame loop ====================== */
let previous=performance.now(),lastLogDraw=0;
function animate(now){
 requestAnimationFrame(animate);const dt=Math.min((now-previous)/1000,.05);previous=now;const live=connected&&!state?.paused;
 if(waterMaterial&&live)waterMaterial.uniforms.time.value+=dt;
 stepRegion(now,dt);stepActors(now,dt,live);stepTraffic(now,dt,live);stepIncidentFX(now,live,dt);stepFootprints(dt);stepRouteAnchors();stepSelectedFX();stepSignals(now);stepPulses(now);stepPeople(now,dt);stepMesh();
 stepFlight(now);
 if(cameraMode==='follow'&&selected&&!flight){const o=droneObjects.get(selected.id)||resourceObjects.get(selected.id);if(o){const delta=o.position.clone().sub(controls.target);controls.target.lerp(o.position,.07);camera.position.addScaledVector(delta,.07)}}
 controls.update();clampCamera();
 if(uiDirty&&state&&now-lastUI>250){renderUI();lastUI=now}
 if(LOG.dirty&&now-lastLogDraw>200){lastLogDraw=now;renderLog()}
 renderer.render(scene,camera);declutter(now);
 if(connected&&now-lastPacket>7000)setConnection(false,'No recent telemetry · displaying last received state');
}

setConnection(false);connect();requestAnimationFrame(animate);

/* ====================== layers popover ====================== */
{const btn=$('layers-btn'),pop=$('layers-pop');const set=on=>{pop.hidden=!on;btn.setAttribute('aria-expanded',String(on))};
 btn.onclick=e=>{e.stopPropagation();set(pop.hidden)};
 document.addEventListener('click',e=>{if(!pop.hidden&&!e.target.closest('#layers-pop')&&e.target!==btn)set(false)});
 document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!pop.hidden){set(false);btn.focus()}});}

/* ====================== right panel tabs (Live log | Command) ====================== */
{const tabs=[...document.querySelectorAll('[data-rtab]')];
 const setR=(name,focus)=>{for(const b of tabs){const on=b.dataset.rtab===name;b.classList.toggle('active',on);b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;if(on&&focus)b.focus()}$('rpanel-log').hidden=name!=='log';$('panel-command').hidden=name!=='command';if(name==='log'){LOG.drawn='';LOG.dirty=true}else{msgSig='';if(state)renderCommand();const ml=$('msg-list');ml.scrollTop=ml.scrollHeight}try{localStorage.setItem('ops-rtab',name)}catch{}};
 tabs.forEach(b=>{b.addEventListener('click',()=>setR(b.dataset.rtab));b.addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();setR(tabs[(tabs.indexOf(b)+1)%tabs.length].dataset.rtab,true)}})});
 window.__setRTab=setR;let last='log';try{last=localStorage.getItem('ops-rtab')||'log'}catch{}setR(last==='command'?'command':'log');}
