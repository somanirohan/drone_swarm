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
 if(current.paused){await page.getByRole('button',{name:'Resume exercise'}).click();await wait(()=>!current.paused);}
 await page.getByRole('button',{name:'Pause exercise'}).click();await wait(()=>current.paused);
 const frozen=current.t;await new Promise(r=>setTimeout(r,400));assert.equal(current.t,frozen);
 await page.getByRole('button',{name:'Resume exercise'}).click();await wait(()=>!current.paused);
 for(const id of ['medical_supply','blocked_bridge','rooftop_flood']){
  await page.locator('#scenario-select').selectOption(id);await wait(()=>current.scenario.id===id);
  assert(current.incidents.length>=2);assert(current.resources.some(r=>r.incident_id),'Ground dispatch must exist alongside drone response');
 }
 const previousRun=current.run_id;await page.locator('#randomize').click();await wait(()=>current.run_id!==previousRun);
 await page.locator('#toggle-coverage').click();assert.equal(await page.locator('#toggle-coverage').getAttribute('aria-pressed'),'true');
 await page.locator('#toggle-routes').click();assert.equal(await page.locator('#toggle-routes').getAttribute('aria-pressed'),'false');
 await page.locator('#toggle-routes').click();
 await page.locator('[data-select="drone"]').first().click();await page.locator('#selection').filter({hasText:'D01'}).waitFor();
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
 await page.locator('#scenario-select').selectOption('rooftop_flood');await wait(()=>current.scenario.id==='rooftop_flood');
 await page.locator('#speed').selectOption('5');await wait(()=>current.speed===5);
 await wait(()=>current.t>=18);await page.getByRole('button',{name:'Pause exercise'}).click();await wait(()=>current.paused);
 await page.locator('#speed').selectOption('1');await wait(()=>current.speed===1);
 if(await page.locator('#close-selection').isVisible())await page.locator('#close-selection').click();
 const out=path.join(root,'docs/implementation/screenshots');await fs.mkdir(out,{recursive:true});
 await page.screenshot({path:path.join(out,'operations-overview.png')});
 await page.locator('#camera-top').click();await page.screenshot({path:path.join(out,'operations-top.png')});
 await page.setViewportSize({width:390,height:844});
 await wait(()=>page.locator('#scene canvas').isVisible());
 const horizontalOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
 await page.screenshot({path:path.join(out,'operations-mobile.png')});
 assert.equal(horizontalOverflow,false,'Mobile layout should not overflow horizontally');
 await page.setViewportSize({width:1600,height:1000});await page.locator('#camera-overview').click();
 assert.deepEqual(errors,[]);assert(frames>20);
 await fs.writeFile(path.join(root,'docs/implementation/browser-results.json'),JSON.stringify({passed:true,fleet:current.drones.length,frames,checks:['all scenarios','concurrent ground dispatch','random seed/run','pause/resume','coverage/routes','selection/cameras','crash reassignment','offline controls/reconnect','desktop/mobile layout'],javascript_errors:errors},null,2));
 console.log(JSON.stringify({passed:true,fleet:current.drones.length,frames,javascript_errors:errors}));
 await browser.close();
})().catch(async error=>{console.error(error);if(browser)await browser.close();process.exitCode=1;});
