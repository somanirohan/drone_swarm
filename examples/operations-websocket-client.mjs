/** Reference client for the friend handoff document. No automatic mutations. */
export function connectSwarm({baseUrl, token='', onState=()=>{}, onAck=()=>{}, onStatus=()=>{}}) {
  const base=new URL(baseUrl.endsWith('/')?baseUrl:baseUrl+'/');
  const url=new URL('ops/ws',base);
  url.protocol=base.protocol==='https:'?'wss:':'ws:';
  if(token)url.searchParams.set('token',token);
  let socket,stopped=false,retryTimer,delay=600,lastFrame=0,run=null,seq=-1,world=null,state=null;
  const pending=new Map();
  function rejectPending(reason){for(const item of pending.values()){clearTimeout(item.timer);item.reject(new Error(reason));}pending.clear();}
  function open(){
    if(stopped)return;
    onStatus('connecting');socket=new WebSocket(url);
    socket.onmessage=event=>{
      let packet;try{packet=JSON.parse(event.data);}catch{return;}
      if(packet.type==='ack'){
        const item=pending.get(packet.id);
        if(item){clearTimeout(item.timer);pending.delete(packet.id);if(packet.ok)item.resolve(packet);else item.reject(new Error(packet.error||'Command rejected'));}
        onAck(packet);return;
      }
      if(packet.type!=='snapshot'||packet.protocol!=='disruptionops.swarm.v2'||packet.state?.schema_version!==2)return;
      const next=packet.state;
      if(next.run_id!==run){run=next.run_id;seq=-1;world=null;}
      if(packet.seq<=seq)return;
      seq=packet.seq;lastFrame=Date.now();delay=600;
      const worldUpdated=Object.prototype.hasOwnProperty.call(next,'world');
      if(worldUpdated)world=next.world;
      state={...next,world};onStatus('connected');onState(state,{seq,serverTime:packet.server_time,worldUpdated});
    };
    socket.onclose=()=>{
      rejectPending('Disconnected; command outcome may be unknown. Inspect state before retrying.');
      if(stopped)return;
      onStatus('disconnected');retryTimer=setTimeout(open,delay);delay=Math.min(15000,delay*1.8);
    };
    socket.onerror=()=>socket.close();
  }
  const staleTimer=setInterval(()=>{if(!stopped&&lastFrame&&Date.now()-lastFrame>7000)onStatus('stale');},1000);
  open();
  return {
    get state(){return state;},
    makeCommand(action,payload={}){
      if(!run)throw new Error('Wait for the first snapshot.');
      return {type:'command',id:crypto.randomUUID(),expected_run_id:run,action,payload};
    },
    send(command){
      if(!token)return Promise.reject(new Error('An operator token is required.'));
      if(stopped||socket?.readyState!==WebSocket.OPEN||!lastFrame||Date.now()-lastFrame>7000)return Promise.reject(new Error('No current connection.'));
      if(pending.has(command.id))return Promise.reject(new Error('This ID already has an outstanding command.'));
      const raw=JSON.stringify(command);
      if(new TextEncoder().encode(raw).length>16384)return Promise.reject(new Error('Command exceeds 16 KiB.'));
      return new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>{pending.delete(command.id);reject(new Error('ACK timeout; outcome may be unknown. Retry only the identical envelope or inspect state.'));},8000);
        pending.set(command.id,{resolve,reject,timer});
        try{socket.send(raw);}catch(error){clearTimeout(timer);pending.delete(command.id);reject(error);}
      });
    },
    close(){stopped=true;clearInterval(staleTimer);clearTimeout(retryTimer);rejectPending('Client closed.');socket?.close(1000);}
  };
}

/** Print only what is new: ground dispatches, orchestrator status events and key signals. */
export function describeNew(state, printed, log=console.log) {
  const byId=new Map((state.resources||[]).map(r=>[r.id,r]));
  for(const e of state.events||[]){
    const key='e'+e.id;if(printed.has(key))continue;printed.add(key);
    if(e.kind==='ground_dispatch'){
      const id=e.message.split(' ')[0],r=byId.get(id)||{};
      log(`DISPATCH t=${e.time}s ${id} kind=${r.kind} agency=${r.agency} -> ${e.incident_id} state=${r.state} eta=${r.eta} route_points=${(r.route||[]).length}`);
    } else if(e.status) log(`STATUS   t=${e.time}s ${e.status} incident=${e.incident_id} request=${e.request_id} data=${JSON.stringify(e.data)}`);
  }
  for(const s of state.signals||[]){
    const key='s'+s.id;if(printed.has(key))continue;printed.add(key);
    if(['sos','dispatch','resource_request','supply_request','recall','arrival'].includes(s.kind))
      log(`SIGNAL   ${s.kind} ${s.from_type}:${s.from_id} -> ${JSON.stringify(s.to_ids)} incident=${s.incident_id} ${s.text||''}`);
  }
  return state.metrics?.agency;
}

// CLI: node examples/operations-websocket-client.mjs http://localhost:8821 [watch-seconds] [category]
// With OPS_FRIEND_TOKEN set (operator token) it fires one random_incident after the first frame.
if (process.argv[1]?.endsWith('operations-websocket-client.mjs')) {
  const [baseUrl='http://localhost:8787',watch='20',category]=process.argv.slice(2);
  const token=process.env.OPS_FRIEND_TOKEN||'',printed=new Set();let fired=false,agency=null;
  const client=connectSwarm({baseUrl,token,onStatus:()=>{},
    onState:state=>{
      agency=describeNew(state,printed);
      if(token&&!fired){fired=true;
        client.send(client.makeCommand('random_incident',category?{category}:{})).then(a=>console.log('ACK',JSON.stringify(a.result))).catch(e=>console.log('REJECTED',e.message));}
    }});
  setTimeout(()=>{console.log('AGENCY METRICS',JSON.stringify(agency));client.close();process.exit(0);},Number(watch)*1000);
}
