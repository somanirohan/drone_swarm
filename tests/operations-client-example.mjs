/** Real-server acceptance for the copyable browser/Node reference client. */
import assert from 'node:assert/strict';
import {connectSwarm} from '../examples/operations-websocket-client.mjs';

const baseUrl=process.argv[2],token=process.env.OPS_EXAMPLE_TEST_TOKEN;
let checks=0;
function check(value,message){assert.ok(value,message);checks++;}
async function until(condition,label){
  const deadline=Date.now()+6000;
  while(!condition()){
    if(Date.now()>deadline)throw new Error(`Timed out: ${label}`);
    await new Promise(resolve=>setTimeout(resolve,30));
  }
}
let frames=0,omittedWorld=false,statuses=[];
const viewer=connectSwarm({baseUrl,onState:(state,meta)=>{
  frames++;if(!meta.worldUpdated&&state.world)omittedWorld=true;
}});
try {
  await until(()=>frames>=3&&omittedWorld,'initial world cache');
  check(viewer.state.drones.length===5,'actual fleet received');
  check(viewer.state.world&&omittedWorld,'world survives omitted-world frames');
  await assert.rejects(viewer.send(viewer.makeCommand('pause')),/operator token/i);checks++;
} finally {viewer.close();}

let worldFrames=0;
const operator=connectSwarm({baseUrl,token,onStatus:s=>statuses.push(s),
  onState:(_state,meta)=>{if(meta.worldUpdated)worldFrames++;}});
try {
  await until(()=>operator.state,'operator initial snapshot');
  const pause=operator.makeCommand('pause');
  const first=await operator.send(pause);
  await until(()=>operator.state.paused,'accepted pause observed');
  check(first.ok&&operator.state.paused,'pause ACK and state');
  check(JSON.stringify(await operator.send(pause))===JSON.stringify(first),'identical envelope retry returns identical ACK');
  await assert.rejects(operator.send({...pause,action:'resume'}),/different payload/i);checks++;
  const oldRun=operator.state.run_id;
  const reset=operator.makeCommand('reset',{seed:91});
  const resetAck=await operator.send(reset);
  await until(()=>operator.state.run_id===resetAck.result.run_id,'new run snapshot');
  check(operator.state.run_id!==oldRun&&operator.state.world&&worldFrames>=2,'reset replaces run and world');
  check(JSON.stringify(await operator.send(reset))===JSON.stringify(resetAck),'exact reset retry survives stale run expectation');
  await assert.rejects(operator.send({type:'command',id:crypto.randomUUID(),expected_run_id:oldRun,action:'pause',payload:{}}),/scenario changed/i);checks++;
  const before=worldFrames;
  console.log('REQUEST_CONNECTION_DROP');
  await until(()=>statuses.includes('disconnected')&&worldFrames>before,'automatic reconnect and full world');
  check(operator.state.world&&statuses.includes('connected'),'automatic reconnect recovers current world');
  const speed=await operator.send(operator.makeCommand('speed',{value:2}));
  await until(()=>operator.state.speed===2,'speed update');
  check(speed.ok&&operator.state.speed===2,'commands work after reconnect');
  await assert.rejects(operator.send(operator.makeCommand('speed',{value:100})),/0.25 and 16/);checks++;
  console.log(JSON.stringify({client:'javascript',checks,passed:true}));
} finally {operator.close();}
