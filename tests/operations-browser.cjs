/* Local UI acceptance checks. Install optional tooling with:
 * npm install --prefix .tools/browser-check playwright
 * node tests/operations-browser.cjs
 */
const path=require('node:path');
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
const {chromium}=require(path.resolve(__dirname,'../.tools/browser-check/node_modules/playwright'));
const root=path.resolve(__dirname,'..');
let browser;
const base=process.env.OPS_TEST_URL||'http://localhost:8787';
const wait=async(predicate,timeout=10000)=>{const end=Date.now()+timeout;while(Date.now()<end){if(await predicate())return;await new Promise(r=>setTimeout(r,100));}throw new Error('Acceptance condition timed out');};

(async()=>{
 browser=await chromium.launch({headless:true,executablePath:process.env.OPS_BROWSER_EXE||'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 const context=await browser.newContext({viewport:{width:1600,height:1000}});
 const page=await context.newPage();const errors=[];let current=null,frames=0;
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{const NativeWebSocket=window.WebSocket;window.WebSocket=class extends NativeWebSocket{constructor(...args){super(...args);window.__acceptanceSocket=this;}};});
 page.on('websocket',socket=>socket.on('framereceived',({payload})=>{try{const packet=JSON.parse(payload);if(packet.type==='snapshot'){current=packet.state;frames++;}}catch{}}));
 await wait(async()=>{try{return (await fetch(base+'/health')).ok;}catch{return false;}},30000);
 await page.goto(base,{waitUntil:'networkidle'});
 await page.locator('#connection').filter({hasText:'Connected'}).waitFor();
 assert.equal(await page.locator('#operator-role').textContent(),'Operator');
 assert(current.drones.length>=20);assert.equal(await page.locator('#fleet-list button').count(),current.drones.length);
 // Tabs: every sidebar tab must reveal exactly its own panel; arrow keys move between tabs.
 for(const tab of ['incidents','fleet','response','mission']){await page.locator(`#tab-${tab}`).click();assert.equal(await page.locator(`#tab-${tab}`).getAttribute('aria-selected'),'true');assert.equal(await page.locator('.tab-panel:visible').count(),1);assert(await page.locator(`#panel-${tab}`).isVisible(),`${tab} panel must open`)}
 await page.locator('#tab-mission').focus();await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#tab-incidents').getAttribute('aria-selected'),'true');await page.locator('#tab-mission').click();
 await page.locator('#tab-fleet').click();assert.equal(await page.locator('#fleet-list button').count(),current.drones.length);await page.locator('#tab-mission').click();
 // Command tab: chat-like timeline of command-center <-> agent messages (skipped gracefully when the server does not publish them).
 await page.locator('#rtab-command').click();assert.equal(await page.locator('#rtab-command').getAttribute('aria-selected'),'true');assert(await page.locator('#msg-filter').isVisible(),'command tab has an incident filter');
 if(Array.isArray(current.messages)&&current.messages.length){await wait(async()=>(await page.locator('#msg-list .msg').count())>0,8000);const first=page.locator('#msg-list .msg').first();assert((await first.textContent()).length>3);
  const inc=current.messages.find(m=>m.incident_id&&current.incidents.some(i=>i.id===m.incident_id))?.incident_id;
  if(inc){await page.locator('#msg-filter').selectOption(inc);await wait(async()=>(await page.locator('#msg-list .msg').evaluateAll((els,id)=>els.every(e=>e.dataset.msgIncident===id),inc)),5000);await page.locator('#msg-list .msg').first().click();await page.locator('#selection').waitFor({state:'visible'});await page.locator('#close-selection').click();await page.locator('#msg-filter').selectOption('')}
  assert.equal(await page.locator('#msg-list .msg.sit').count()>0||!current.messages.some(m=>m.kind==='situation_report'),true,'situation reports render as cards');}
 else console.log('note: state.messages absent; command timeline not exercised');
 await page.locator('#rtab-log').click();assert(await page.locator('#oplog-scroll').isVisible(),'live log tab');
 await page.locator('#tab-mission').click();
 assert.equal(await page.locator('#event-log').count(),0,'duplicate latest-activity list removed');
 assert((await page.locator('.map-tools button:visible').count())<=8,'toolbar collapsed into camera control + layers popover');
 // Scenario selector is grouped by world.
 assert((await page.locator('#scenario-select optgroup').count())>=1,'scenario dropdown grouped by world');
 // Civilian traffic (when published) must be instanced and animated, never labelled.
 if(Array.isArray(current.traffic)&&current.traffic.length){const t0=await page.evaluate(()=>__ops.trafficCount);assert(t0>0,'traffic must be tracked');assert.equal(await page.evaluate(()=>[...__ops.traffic.map.values()].length),await page.evaluate(()=>__ops.trafficCount))}
 else console.log('note: state.traffic absent; traffic not exercised');
 // Fleet-size selector sends the fleet command and reflects the snapshot.
 const fleetOptions=await page.locator('#fleet-select option').evaluateAll(o=>o.map(x=>x.value));assert.deepEqual(fleetOptions,['5','10','20','25','40','50']);
 assert.equal(await page.locator('#view-only-note').isHidden(),true,'operator sessions do not show the view-only note');
 await page.locator('#fleet-select').selectOption('25');await wait(()=>current.drones.length===25,15000);await wait(async()=>(await page.locator('#fleet-select').inputValue())==='25',3000);
 await page.locator('#fleet-select').selectOption('20');await wait(()=>current.drones.length===20,15000);
 // Double-click flies to the exact point under the cursor and reaches a ground-level view.
 await page.mouse.dblclick(900,560);await wait(async()=>!(await page.evaluate(()=>window.__ops.flying)),8000);
 const near=await page.evaluate(()=>{const o=window.__ops,c=o.camera.position,t=o.controls.target;return{dist:c.distanceTo(t),above:c.y-o.groundHeight(c.x,-c.z)}});assert(near.dist<80&&near.above<90&&near.above>0,'double-click must reach a close, above-ground view '+JSON.stringify(near));
 await page.locator('#camera-overview').click();await wait(async()=>!(await page.evaluate(()=>window.__ops.flying)),8000);
 if(current.paused){await page.getByRole('button',{name:'Resume exercise'}).click();await wait(()=>!current.paused);}
 await page.getByRole('button',{name:'Pause exercise'}).click();await wait(()=>current.paused);
 const frozen=current.t;await new Promise(r=>setTimeout(r,400));assert.equal(current.t,frozen);
 await page.getByRole('button',{name:'Resume exercise'}).click();await wait(()=>!current.paused);
 await page.locator('#scenario-select').selectOption('dock_fire');await wait(()=>current.scenario.id==='dock_fire',15000);
 if(current.docks){assert(current.docks.length>=1);await wait(async()=>(await page.evaluate(()=>window.__ops.dockObjects.size))===current.docks.length,5000);assert.match(await page.locator('#dock-summary').textContent(),/docked/);}
 assert((await page.locator('#scenario-description').textContent()).length>20,'scenario description must update');
 await page.locator('#tab-fleet').click();await page.locator('[data-select="drone"]').first().click();await page.locator('#selection').filter({hasText:'D01'}).waitFor();if(current.drones.find(d=>d.id==='D01')?.last_decision)await page.locator('#selection').filter({hasText:'Agent decision'}).waitFor({timeout:5000});await page.locator('#camera-focus').click();await wait(async()=>!(await page.evaluate(()=>window.__ops.flying)),8000);await page.evaluate(()=>document.getElementById('close-selection')?.click());await page.locator('#tab-mission').click();
 if((current.signals||[]).length)await wait(async()=>(await page.evaluate(()=>window.__ops.signalObjects.size))>0,5000);
 // Rescue-issue controls: Esc cancels placement mode; clicking the map opens the popover; placing raises a new incident.
 await page.locator('#camera-overview').click();await wait(async()=>!(await page.evaluate(()=>window.__ops.flying)),8000);
 assert.equal(await page.locator('#random-issue').isEnabled(),true);
 await page.locator('#add-issue').click();assert(await page.locator('#issue-hint').isVisible());await page.keyboard.press('Escape');assert.equal(await page.locator('#add-issue').getAttribute('aria-pressed'),'false');
 const issuesBefore=current.incidents.length;await page.locator('#issue-category').selectOption('trapped');await page.locator('#add-issue').click();await page.mouse.click(1000,620);await page.locator('#issue-popover').waitFor({state:'visible'});
 await page.locator('#issue-pop-people').fill('3');await page.locator('#issue-pop-place').click();await wait(()=>current.incidents.length>issuesBefore&&current.incidents.some(i=>/Operator-raised/.test(i.title)),10000);
 await page.locator('#tab-mission').click();await page.locator('#issue-category').selectOption('');
 if(current.people)assert(current.people.length>0);
 for(const id of ['medical_supply','blocked_bridge','rooftop_flood']){
  await page.locator('#scenario-select').selectOption(id);await wait(()=>current.scenario.id===id);
  assert(current.incidents.length>=2);assert(current.resources.some(r=>r.incident_id),'Ground dispatch must exist alongside drone response');
 }
 const previousRun=current.run_id;await page.locator('#randomize').click();await wait(()=>current.run_id!==previousRun);
 await page.locator('#layers-btn').click();assert(await page.locator('#layers-pop').isVisible(),'layers popover opens');for(const id of ['routes','search','coverage','wards','traffic','mesh','labels','buildings','roads'])assert(await page.locator('#toggle-'+id).count()===1,'layer '+id);
 await page.locator('#toggle-coverage').click();assert.equal(await page.locator('#toggle-coverage').getAttribute('aria-pressed'),'true');
 await page.locator('#toggle-routes').click();assert.equal(await page.locator('#toggle-routes').getAttribute('aria-pressed'),'false');
 await page.locator('#toggle-routes').click();await page.locator('#layers-btn').click();assert(await page.locator('#layers-pop').isHidden());
 await page.locator('#tab-fleet').click();await page.locator('[data-select="drone"]').first().click();await page.locator('#selection').filter({hasText:'D01'}).waitFor();
 await page.locator('#camera-follow').click();await page.locator('#camera-top').click();await page.locator('#camera-overview').click();
 await page.locator('.exercise-tools summary').click();
 const assigned=current.incidents.find(i=>i.drone_id)?.drone_id||'D01';
 await page.locator('#fault-drone').selectOption(assigned);await page.locator('[data-fault="crash"]').click();
 await wait(()=>current.drones.find(d=>d.id===assigned)?.state==='crashed');
 const crashed=current.drones.find(d=>d.id===assigned);assert.equal(crashed.active,false);
 assert(current.incidents.every(i=>i.drone_id!==assigned),'Failed aircraft task must not remain assigned');
 await context.setOffline(true);await page.evaluate(()=>window.__acceptanceSocket.close());
 await page.locator('#connection').filter({hasText:'Connection interrupted'}).waitFor();assert.equal(await page.locator('#randomize').isDisabled(),true);
 await context.setOffline(false);await page.locator('#connection').filter({hasText:'Connected'}).waitFor({timeout:30000});
 assert.equal(await page.locator('#randomize').isDisabled(),false);
 await page.locator('#tab-mission').click();
 await page.locator('#scenario-select').selectOption('rooftop_flood');await wait(()=>current.scenario.id==='rooftop_flood');
 await page.locator('#speed').selectOption('5');await wait(()=>current.speed===5);
 await wait(()=>current.t>=18);await page.getByRole('button',{name:'Pause exercise'}).click();await wait(()=>current.paused);
 await page.locator('#speed').selectOption('1');await wait(()=>current.speed===1);
 if(await page.locator('#close-selection').isVisible())await page.locator('#close-selection').click();
 const out=path.join(process.env.OPS_TEST_OUT||path.join(root,'docs/implementation'),'screenshots');await fs.mkdir(out,{recursive:true});
 await page.screenshot({path:path.join(out,'operations-overview.png')});
 await page.locator('#camera-top').click();await page.screenshot({path:path.join(out,'operations-top.png')});
 await page.setViewportSize({width:390,height:844});
 await wait(()=>page.locator('#scene canvas').isVisible());
 const horizontalOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
 await page.screenshot({path:path.join(out,'operations-mobile.png')});
 assert.equal(horizontalOverflow,false,'Mobile layout should not overflow horizontally');
 await page.setViewportSize({width:1600,height:1000});await page.locator('#camera-overview').click();
 assert.deepEqual(errors,[]);assert(frames>20);
 await fs.writeFile(path.join(process.env.OPS_TEST_OUT||path.join(root,'docs/implementation'),'browser-results.json'),JSON.stringify({passed:true,fleet:current.drones.length,frames,checks:['all scenarios','concurrent ground dispatch','random seed/run','pause/resume','coverage/routes','selection/cameras','tabs/arrow keys','fleet selector','double-click zoom','dock scenario/focus','command tab/messages (if published)','traffic (if published)','grouped scenario selector','rescue issue controls','crash reassignment','offline controls/reconnect','desktop/mobile layout'],javascript_errors:errors},null,2));
 console.log(JSON.stringify({passed:true,fleet:current.drones.length,frames,javascript_errors:errors}));
 await browser.close();
})().catch(async error=>{console.error(error);if(browser)await browser.close();process.exitCode=1;});
