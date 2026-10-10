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
