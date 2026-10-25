(() => {
  'use strict';
  const $ = id => document.getElementById(id), palettes = ['#087c72','#dd6548','#346994','#805fa0','#a27626','#4a8d4e','#b85472','#577c91'];
  const histories = new Map(), pathChart = echarts.init($('paths-plot')), headingChart = echarts.init($('heading-plot'));
  let latest = null, paused = false, generation = '', lastSequence = null, manualZoom = false, dirty = false;
  const text = (id, value) => { $(id).textContent = value ?? '—'; };
  const number = (v, digits = 1) => Number.isFinite(v) ? v.toFixed(digits) : '—';
  const base = { animation:false, color:palettes, grid:{left:64,right:22,top:28,bottom:66}, tooltip:{trigger:'axis'}, legend:{top:0,type:'scroll'}, xAxis:{type:'value'}, yAxis:{type:'value'}, dataZoom:[{type:'inside'},{type:'slider',height:15,bottom:12}] };
  pathChart.setOption({...base,xAxis:{type:'value',name:'World X'},yAxis:{type:'value',name:'World Z'}});
  headingChart.setOption({...base,xAxis:{type:'time'},yAxis:{type:'value',name:'Heading °',min:0,max:360}});
  pathChart.on('datazoom',()=>{manualZoom=true;});
  const resize = new ResizeObserver(()=>{pathChart.resize();headingChart.resize();dirty=true;});
  resize.observe($('paths-plot')); resize.observe($('heading-plot'));
  function clear(){histories.clear();lastSequence=null;manualZoom=false;pathChart.clear();headingChart.clear();pathChart.setOption({...base,xAxis:{type:'value',name:'World X'},yAxis:{type:'value',name:'World Z'}});headingChart.setOption({...base,xAxis:{type:'time'},yAxis:{type:'value',name:'Heading °',min:0,max:360}});dirty=true;}
  $('clear-view').onclick=clear;
  $('fit-paths').onclick=()=>{manualZoom=false;pathChart.dispatchAction({type:'dataZoom',start:0,end:100});manualZoom=false;dirty=true;};
  $('pause-view').onclick=()=>{paused=!paused;$('pause-view').textContent=paused?'Resume view':'Pause view';if(!paused)dirty=true;};
  function cell(row, primary, secondary, stale=false){const td=document.createElement('td');td.textContent=primary??'—';td.dataset.stale=String(stale);if(secondary){const small=document.createElement('small');small.textContent=secondary;td.append(small);}row.append(td);}
  function receive(data){
    latest=data; let f=data.telemetry; const candidate=data.context||{}, r=data.receiver||{};
    const pairs=[[f?.experimentContext?.sessionId,candidate.sessionId],[f?.system?.sceneName,candidate.experiment?.sceneName||candidate.scene?.name],[f?.system?.sceneNumber,candidate.experiment?.sceneNumber],[f?.system?.trialNumber,candidate.experiment?.trialNumber]];
    const mismatch=!!f&&pairs.some(([a,b])=>a!=null&&b!=null&&String(a)!==String(b));
    const ct=Date.parse(candidate.timestampUtc), ft=Date.parse(f?.timestampUtc);
    const contextNewer=Number.isFinite(ct)&&Number.isFinite(ft)?ct>ft:r.contextAgeSeconds!=null&&(r.snapshotAgeSeconds==null||r.contextAgeSeconds<r.snapshotAgeSeconds);
    const waitingForPose=mismatch&&contextNewer, c=mismatch&&!contextNewer?{}:candidate;
    if(waitingForPose)f=null;
    text('connection',({receiving:'Telemetry receiving',waiting:'Waiting for publisher',stale:'Telemetry stale',error:'Receiver error',disconnected:'Choose a publisher'})[r.state]||'Waiting');$('connection').dataset.state=r.state||'waiting';
    text('diagnostics',`${r.endpoint||'No publisher selected'} · ${r.snapshots||0} snapshots · ${r.contexts||0} context heartbeats · ${r.sequenceGaps||0} source-sequence gaps · ${r.invalidPackets||0} invalid packets · ${r.publisherRestarts||0} publisher restarts · snapshot age ${number(r.snapshotAgeSeconds,2)} s · context age ${number(r.contextAgeSeconds,2)} s`);
    text('error',r.error||(waitingForPose?'Experiment context advanced; waiting for a matching pose snapshot.':f?.system?.error||''));
    const sys=f?.system||c.experiment||{}, scene=sys.sceneName||c.experiment?.sceneName;
    text('run-id',c.experimentName||c.sessionId||f?.experimentContext?.sessionId||'Waiting for context heartbeat');
    text('scene',scene?`${scene} · block ${sys.sceneNumber||'—'}`:'—');
    text('phase',`Trial ${sys.trialNumber||'—'} · ${sys.phase||'idle'}${Number.isFinite(sys.remainingStepSeconds)?' · '+number(sys.remainingStepSeconds)+' s':''}`);
    const timing=f?.frameTiming||{};text('fps',Number.isFinite(timing.framesPerSecond)?number(timing.framesPerSecond)+' FPS':Number.isFinite(timing.frameMeanMs)?number(timing.frameMeanMs)+' ms':'—');
    text('config',c.experiment?.configFile||c.experiment?.design||f?.experimentContext?.configFile||'Waiting for context heartbeat');
    const units=c.scene?.worldUnitsPerMetre??f?.experimentContext?.worldUnitsPerMetre; text('position-units',`World X / Z · ${units===1?'metres':units===100?'centimetres':'scene world units'} · current viewing window`);
    const key=`${r.endpoint||''}|${c.sessionId||f?.experimentContext?.sessionId||''}|${sys.sceneName||''}|${sys.trialNumber||0}|${sys.sceneNumber||0}`;
    if(generation && key!==generation)clear(); generation=key;
    if(!f){const body=$('animals');body.replaceChildren();const row=document.createElement('tr');cell(row,'Waiting for a matching pose snapshot');row.children[0].colSpan=6;body.append(row);return;}
    const isNewFrame=f.sequence!==lastSequence;
    if(isNewFrame&&lastSequence!==null&&f.sequence<lastSequence)clear();
    if(isNewFrame)lastSequence=f.sequence;
    const now=Date.parse(f.timestampUtc)||Date.now(), profiles=new Map((c.animals||[]).map(a=>[a.vrId,a]));
    const body=$('animals');body.replaceChildren();
    const visible=(f.rigs||[]).slice(0,32), ids=new Set(visible.map(r=>r.vrId));
    for(const id of histories.keys())if(!ids.has(id))histories.delete(id);
    for(const rig of visible){
      const p=rig.pose, profile=profiles.get(rig.vrId)||{}, row=document.createElement('tr');
      cell(row,profile.animalId||rig.vrId,profile.animalId?rig.vrId:'');
      cell(row,profile.speciesName||'Species unspecified',profile.backend||rig.input?.backend||rig.input?.source||'');
      const inputAge=rig.input?.latest?.ageSeconds!=null?rig.input.latest.ageSeconds+(r.snapshotAgeSeconds||0):null;
      cell(row,rig.input?.state||'Inactive',inputAge!=null?number(inputAge,2)+' s since input':'',rig.input?.latest?.fresh===false||(inputAge!=null&&inputAge>1));
      cell(row,number(rig.closedLoop?.yawGain,2),`DC ${number(rig.closedLoop?.yawDCOffsetDegrees,2)}°`);
      cell(row,rig.autoTrim?.supported?(rig.autoTrim.flying?'Flying':'Not flight ready'):'Walking / other',rig.autoTrim?.state||'');
      cell(row,number(rig.closedLoop?.yawOutputDegPerSecond)+' °/s',rig.closedLoop?.appliedThisFrame?'Applied this frame':'No applied tracking this frame');body.append(row);
      if(!isNewFrame||!p||![p.x,p.z,p.yaw].every(Number.isFinite))continue;
      if(!histories.has(rig.vrId))histories.set(rig.vrId,[]);
      const points=histories.get(rig.vrId);points.push({t:now,x:p.x,z:p.z,yaw:((p.yaw%360)+360)%360});
      if(points.length>1800)points.splice(0,points.length-1800);
    }
    dirty=true;
  }
  function paint(){
    if(!dirty||paused)return;dirty=false;
    const entries=[...histories.entries()];
    const paths=entries.map(([id,p])=>({id,name:id,type:'line',showSymbol:false,symbol:'none',data:p.map(v=>[v.x,v.z]),lineStyle:{width:2},connectNulls:false}));
    const headings=entries.map(([id,p])=>({id,name:id,type:'line',showSymbol:false,symbol:'none',data:p.map(v=>[v.t,v.yaw]),lineStyle:{width:1.5}}));
    const opts={series:paths};
    if(!manualZoom&&entries.length){const points=entries.flatMap(v=>v[1]);let loX=Infinity,hiX=-Infinity,loZ=Infinity,hiZ=-Infinity;for(const p of points){loX=Math.min(loX,p.x);hiX=Math.max(hiX,p.x);loZ=Math.min(loZ,p.z);hiZ=Math.max(hiZ,p.z);}const aspect=Math.max(.2,($('paths-plot').clientWidth-90)/Math.max(100,$('paths-plot').clientHeight-100));let width=Math.max(.1,hiX-loX)*1.15,height=Math.max(.1,hiZ-loZ)*1.15;width=Math.max(width,height*aspect);height=width/aspect;opts.xAxis={min:(hiX+loX-width)/2,max:(hiX+loX+width)/2};opts.yAxis={min:(hiZ+loZ-height)/2,max:(hiZ+loZ+height)/2};}
    pathChart.setOption(opts,{replaceMerge:['series'],lazyUpdate:true});headingChart.setOption({series:headings},{replaceMerge:['series'],lazyUpdate:true});
  }
  setInterval(paint,180);
  async function initial(){const data=await fetch('/api/live/snapshot',{cache:'no-store'}).then(r=>r.json());$('connect-button').disabled=!data.controlAvailable;$('endpoint').disabled=!data.controlAvailable;$('topic').disabled=!data.controlAvailable;if(data.receiver?.endpoint)$('endpoint').value=data.receiver.endpoint;if(!data.controlAvailable)text('connect-message','Read-only remote viewer. The server operator controls the publisher connection.');receive(data);}
  const events=new EventSource('/api/live/stream');events.addEventListener('snapshot',e=>{try{receive(JSON.parse(e.data));}catch(error){text('error','Cannot display packet: '+error.message);}});events.onerror=()=>{text('connection','Viewer connection lost · reconnecting');$('connection').dataset.state='stale';};
  $('connect-form').onsubmit=async e=>{e.preventDefault();$('connect-button').disabled=true;try{const response=await fetch('/api/live/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:$('endpoint').value,topic:$('topic').value})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Connection failed');clear();text('connect-message','Subscribed. Waiting for the next published snapshot and context heartbeat.');}catch(error){text('connect-message',error.message);}finally{$('connect-button').disabled=false;}};
  initial().catch(error=>text('error',error.message));
})();
