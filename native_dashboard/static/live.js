import {TrajectoryRenderer, setDisplayUnits, NATIVE_PALETTE} from '/static/renderers.js';
import {EChartsHeadingRenderer} from '/static/echarts_renderers.js';
import {LivePlotModel, followBounds, signedHeading} from '/static/live_model.js';

(() => {
  'use strict';
  const $=id=>document.getElementById(id),model=new LivePlotModel();
  let pathRenderer=null,rendererError='',autoRange=true,range=null;
  try {pathRenderer=new TrajectoryRenderer($('paths-plot'),()=>{autoRange=false;range=pathRenderer.view;updateRangeButton();});pathRenderer.setLineStyle(1.8,.8);}
  catch(error){rendererError=error.message;}
  const headingRenderer=new EChartsHeadingRenderer($('heading-plot'));
  let paused=false,generation='',dirty=true,displayed=null;
  const text=(id,value)=>{$(id).textContent=value??'—';};
  const number=(v,digits=1)=>Number.isFinite(v)?v.toFixed(digits):'—';
  function updateRangeButton(){text('fit-paths',autoRange?'Auto range on':'Fit & follow');$('fit-paths').dataset.following=String(autoRange);}
  function clear(){model.clear();displayed=null;range=null;autoRange=true;dirty=true;updateRangeButton();paint(true);}
  $('clear-view').onclick=clear;
  $('fit-paths').onclick=()=>{autoRange=true;range=null;updateRangeButton();dirty=true;paint(true);};
  $('pause-view').onclick=()=>{paused=!paused;text('pause-view',paused?'Resume view':'Pause view');$('pause-view').ariaPressed=String(paused);$('history-window').disabled=paused;if(paused)text('view-window','Paused · '+$('view-window').textContent);if(!paused){dirty=true;paint();}};
  $('history-window').onchange=()=>{model.setWindow($('history-window').value);dirty=true;};
  function cell(row, primary, secondary, stale=false){const td=document.createElement('td');td.textContent=primary??'—';td.dataset.stale=String(stale);if(secondary){const small=document.createElement('small');small.textContent=secondary;td.append(small);}row.append(td);}
  function receive(data){
    let f=data.telemetry; const candidate=data.context||{}, r=data.receiver||{};
    const pairs=[[f?.experimentContext?.sessionId,candidate.sessionId],[f?.system?.sceneName,candidate.experiment?.sceneName||candidate.scene?.name],[f?.system?.sceneNumber,candidate.experiment?.sceneNumber],[f?.system?.trialNumber,candidate.experiment?.trialNumber]];
    const mismatch=!!f&&pairs.some(([a,b])=>a!=null&&b!=null&&String(a)!==String(b));
    const ct=Date.parse(candidate.timestampUtc), ft=Date.parse(f?.timestampUtc);
    const contextNewer=Number.isFinite(ct)&&Number.isFinite(ft)?ct>ft:r.contextAgeSeconds!=null&&(r.snapshotAgeSeconds==null||r.contextAgeSeconds<r.snapshotAgeSeconds);
    const waitingForPose=mismatch&&contextNewer, c=mismatch&&!contextNewer?{}:candidate;
    if(waitingForPose)f=null;
    text('connection',({receiving:'Telemetry receiving',waiting:'Waiting for publisher',stale:'Telemetry stale',error:'Receiver error',disconnected:'Choose a publisher'})[r.state]||'Waiting');$('connection').dataset.state=r.state||'waiting';
    text('diagnostics',`${r.endpoint||'No publisher selected'} · ${r.snapshots||0} snapshots · ${r.contexts||0} context heartbeats · ${r.sequenceGaps||0} source-sequence gaps · ${r.invalidPackets||0} invalid packets · ${r.publisherRestarts||0} publisher restarts · snapshot age ${number(r.snapshotAgeSeconds,2)} s · context age ${number(r.contextAgeSeconds,2)} s`);
    text('error',rendererError||r.error||(waitingForPose?'Experiment context advanced; waiting for a matching pose snapshot.':f?.system?.error||''));
    const sys=f?.system||c.experiment||{}, scene=sys.sceneName||c.experiment?.sceneName;
    text('run-id',c.experimentName||c.sessionId||f?.experimentContext?.sessionId||'Waiting for context heartbeat');
    text('scene',scene?`${scene} · block ${sys.sceneNumber||'—'}`:'—');
    text('phase',`Trial ${sys.trialNumber||'—'} · ${sys.phase||'idle'}${Number.isFinite(sys.remainingStepSeconds)?' · '+number(sys.remainingStepSeconds)+' s':''}`);
    const timing=f?.frameTiming||{};text('fps',Number.isFinite(timing.framesPerSecond)?number(timing.framesPerSecond)+' FPS':Number.isFinite(timing.frameMeanMs)?number(timing.frameMeanMs)+' ms':'—');
    text('config',c.experiment?.configFile||c.experiment?.design||f?.experimentContext?.configFile||'Waiting for context heartbeat');
    const kind=c.inputKind||f?.experimentContext?.inputKind;
    text('input-kind',kind==='demonstration'?'Demonstration input':kind==='tracker'?'Tracker input · verify source':'Source unspecified');
    const units=c.scene?.worldUnitsPerMetre??f?.experimentContext?.worldUnitsPerMetre; text('position-units',`World X / Z · ${units===1?'metres':units===100?'centimetres':'scene world units'} · current viewing window`);
    const key=`${r.endpoint||''}|${c.sessionId||f?.experimentContext?.sessionId||''}|${sys.sceneName||''}|${sys.trialNumber||0}|${sys.sceneNumber||0}`;
    if(generation && key!==generation)clear(); generation=key;
    if(!f){const body=$('animals');body.replaceChildren();const row=document.createElement('tr');cell(row,'Waiting for a matching pose snapshot');row.children[0].colSpan=7;body.append(row);return;}
    const profiles=new Map((c.animals||[]).map(a=>[a.vrId,a]));
    const body=$('animals');body.replaceChildren();
    const visible=(f.rigs||[]).slice(0,32);
    for(const rig of visible){
      const p=rig.pose, profile=profiles.get(rig.vrId)||{}, row=document.createElement('tr');
      cell(row,profile.animalId||rig.vrId,profile.animalId?rig.vrId:'');
      cell(row,profile.speciesName||'Species unspecified',profile.backend||rig.input?.backend||rig.input?.source||'');
      const inputAge=rig.input?.latest?.ageSeconds!=null?rig.input.latest.ageSeconds+(r.snapshotAgeSeconds||0):null;
      cell(row,rig.input?.state||'Inactive',inputAge!=null?number(inputAge,2)+' s since input':'',rig.input?.latest?.fresh===false||(inputAge!=null&&inputAge>1));
      cell(row,p?`${number(p.x,2)} / ${number(p.z,2)}`:'—',p?`${number(signedHeading(p.yaw))}° heading`:'');
      cell(row,number(rig.closedLoop?.yawGain,2),`DC ${number(rig.closedLoop?.yawDCOffsetDegrees,2)}°`);
      cell(row,rig.autoTrim?.supported?(rig.autoTrim.flying?'Flying':'Not flight ready'):'Walking / other',rig.autoTrim?.state||'');
      cell(row,number(rig.closedLoop?.yawOutputDegPerSecond)+' °/s',rig.closedLoop?.appliedThisFrame?'Applied this frame':'No applied tracking this frame');body.append(row);
    }
    model.receive(f,c.animals||[],r.snapshotAgeSeconds||0);
    const perMetre=c.scene?.worldUnitsPerMetre??f.experimentContext?.worldUnitsPerMetre;
    setDisplayUnits(perMetre===100?{perData:1,name:'cm'}:Number.isFinite(perMetre)&&perMetre>0?{perData:1/perMetre,name:'m'}:{perData:1,name:'world units'});
    dirty=true;
  }
  function paint(force=false){
    if(!dirty||paused&&!force||document.visibilityState==='hidden')return;
    dirty=false;const data=paused&&displayed?displayed:model.payloads();displayed=data;
    if(pathRenderer){
      pathRenderer.setData(data.trajectory,true);
      if(autoRange){range=followBounds(range,data.trajectory.bounds);pathRenderer.setView(range);}
    }
    headingRenderer.setData(data.heading,true);
    text('view-window',`${paused?'Paused · ':''}${data.rigs} rig${data.rigs===1?'':'s'} · ${data.points.toLocaleString()} displayed samples · up to ${model.seconds}s per block${model.droppedForLimit?' · sample cap reached':''}`);
    $('paths-empty').hidden=data.points>0;$('heading-empty').hidden=data.heading.vertices.length>0;
    const legend=$('plot-legend');legend.replaceChildren();
    for(const pose of data.trajectory.currentPoses){
      const item=document.createElement('span'),swatch=document.createElement('i');swatch.style.background=NATIVE_PALETTE[pose.color];
      item.append(swatch,document.createTextNode(`${pose.name} · ${number(pose.yaw)}°`));item.dataset.stale=String(pose.stale);legend.append(item);
    }
  }
  setInterval(paint,180);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState!=='hidden'){dirty=true;paint();}});
  async function initial(){const data=await fetch('/api/live/snapshot',{cache:'no-store'}).then(r=>r.json());$('connect-button').disabled=!data.controlAvailable;$('endpoint').disabled=!data.controlAvailable;$('topic').disabled=!data.controlAvailable;if(data.receiver?.endpoint)$('endpoint').value=data.receiver.endpoint;else $('connection-details').open=true;if(!data.controlAvailable)text('connect-message','Read-only remote viewer. The server operator controls the publisher connection.');receive(data);}
  const events=new EventSource('/api/live/stream');events.addEventListener('snapshot',e=>{try{receive(JSON.parse(e.data));}catch(error){text('error','Cannot display packet: '+error.message);}});events.onerror=()=>{text('connection','Viewer connection lost · reconnecting');$('connection').dataset.state='stale';};
  $('connect-form').onsubmit=async e=>{e.preventDefault();$('connect-button').disabled=true;try{const response=await fetch('/api/live/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({endpoint:$('endpoint').value,topic:$('topic').value})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Connection failed');clear();text('connect-message','Subscribed. Waiting for the next published snapshot and context heartbeat.');}catch(error){text('connect-message',error.message);}finally{$('connect-button').disabled=false;}};
  initial().catch(error=>text('error',error.message));
})();
