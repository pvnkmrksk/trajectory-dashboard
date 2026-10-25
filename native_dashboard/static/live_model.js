/** A bounded stream adapter for the recorded-data trajectory/heading renderers. */
export const LIVE_MAX_RIGS =32;
export const LIVE_MAX_SAMPLES =1800;
export const LIVE_MAX_SECONDS =120;
export const signedHeading = value => ((value +180) %360 +360) %360 -180;

export function rigColor(id) {
  const numbered = /^VR([1-9][0-9]*)$/.exec(String(id));
  if (numbered) return (Number(numbered[1]) -1) %16;
  let hash =0;for (const char of String(id)) hash = Math.imul(hash,31) +char.codePointAt(0) |0;
  return (hash >>>0) %16;
}

export function streamBounds(histories) {
  let xmin=Infinity,xmax=-Infinity,zmin=Infinity,zmax=-Infinity;
  for (const {points,active} of histories.values()) for (const point of active===false?[]:points) {
    xmin=Math.min(xmin,point.x);xmax=Math.max(xmax,point.x);
    zmin=Math.min(zmin,point.z);zmax=Math.max(zmax,point.z);
  }
  if (!Number.isFinite(xmin)) return {xmin:-1,xmax:1,zmin:-1,zmax:1};
  const span=Math.max(.1,xmax-xmin,zmax-zmin),pad=span*.1;
  return {xmin:xmin-pad,xmax:xmax+pad,zmin:zmin-pad,zmax:zmax+pad};
}

/** Expand immediately; shrink only after leaving a generous quiet margin. */
export function followBounds(previous,next) {
  if (!previous) return {...next};
  const width=previous.xmax-previous.xmin,height=previous.zmax-previous.zmin;
  const outside=next.xmin<previous.xmin||next.xmax>previous.xmax||next.zmin<previous.zmin||next.zmax>previous.zmax;
  if (outside) return {
    xmin:Math.min(previous.xmin,next.xmin),xmax:Math.max(previous.xmax,next.xmax),
    zmin:Math.min(previous.zmin,next.zmin),zmax:Math.max(previous.zmax,next.zmax),
  };
  const small=(next.xmax-next.xmin)<width*.45&&(next.zmax-next.zmin)<height*.45;
  if (!small) return previous;
  const mix=(a,b)=>a+(b-a)*.08;
  return {xmin:mix(previous.xmin,next.xmin),xmax:mix(previous.xmax,next.xmax),
    zmin:mix(previous.zmin,next.zmin),zmax:mix(previous.zmax,next.zmax)};
}

export class LivePlotModel {
  constructor() {this.seconds=60;this.clear();}
  clear() {this.histories=new Map();this.sequence=null;this.origin=null;this.now=0;this.droppedForLimit=false;}
  setWindow(seconds) {this.seconds=Math.max(10,Math.min(LIVE_MAX_SECONDS,Number(seconds)||60));this.trim();}
  trim() {
    const before=this.now-this.seconds;
    for (const history of this.histories.values()) {
      const points=history.points;let first=0;
      while (first<points.length&&points[first].t<before) first++;
      if (first) points.splice(0,first);
      if (points.length>LIVE_MAX_SAMPLES) {points.splice(0,points.length-LIVE_MAX_SAMPLES);this.droppedForLimit=true;}
    }
  }
  receive(frame,profiles=[],snapshotAge=0) {
    const rigs=(frame.rigs||[]).filter(r=>r&&typeof r.vrId==='string').slice(0,LIVE_MAX_RIGS);
    const ids=new Set(rigs.map(r=>r.vrId));
    for (const id of this.histories.keys()) if (!ids.has(id)) this.histories.delete(id);
    if (this.sequence!=null&&Number.isFinite(frame.sequence)&&frame.sequence<this.sequence) this.clear();
    const identity=Number.isFinite(frame.sequence)?frame.sequence:`${frame.timestampUtc}|${frame.frame??''}`;
    const fresh=identity!==this.sequence;
    const stamp=Number.isFinite(frame.realtimeSeconds)?frame.realtimeSeconds:Date.parse(frame.timestampUtc)/1000;
    const validTime=Number.isFinite(stamp);
    if (fresh&&validTime&&this.origin!=null&&stamp-this.origin<this.now) this.clear();
    this.sequence=identity;
    if (this.origin==null&&validTime) this.origin=stamp;
    if (fresh&&validTime) this.now=Math.max(0,stamp-this.origin);
    const profileMap=new Map(profiles.map(p=>[p.vrId,p]));
    for (const rig of rigs) {
      if (!this.histories.has(rig.vrId)) this.histories.set(rig.vrId,{points:[],color:rigColor(rig.vrId),gap:false});
      const history=this.histories.get(rig.vrId),profile=profileMap.get(rig.vrId)||{};
      history.name=profile.animalId?`${rig.vrId} · ${profile.animalId}`:rig.vrId;
      history.stale=snapshotAge>3||rig.input?.latest?.fresh===false||((rig.input?.latest?.ageSeconds??0)+snapshotAge)>1;
      history.active=rig.active!==false;
      const pose=rig.pose;
      if (!fresh) continue;
      if (!validTime||!pose||![pose.x,pose.z,pose.yaw].every(Number.isFinite)||!history.active) {history.gap=true;continue;}
      const previous=history.points.at(-1),t=this.now;
      // SSE intentionally skips source frames; break only real time gaps/invalid poses,
      // not normal source sequence gaps caused by the latest-state transport.
      const broken=history.gap||!!previous&&(t<=previous.t||t-previous.t>3);
      history.points.push({t,x:pose.x,z:pose.z,yaw:signedHeading(pose.yaw),broken});history.gap=false;
    }
    this.trim();
    return fresh;
  }
  payloads() {
    const entries=[...this.histories].filter(([,h])=>h.active),names=entries.map(([,h])=>h.name);
    const capacity=entries.reduce((sum,[,h])=>sum+Math.max(0,h.points.length-1),0),pairs=capacity*2;
    // One bounded allocation per attribute, rather than repeatedly growing large
    // JavaScript arrays and copying each of them into a typed buffer every draw.
    const vertices=new Float32Array(capacity*4),panels=new Uint16Array(pairs),colors=new Uint8Array(pairs);
    const animals=new Uint16Array(pairs),segments=new Uint32Array(pairs),times=new Float32Array(pairs),samples=new Float32Array(pairs);
    const hv=new Float32Array(capacity*4),hp=new Uint16Array(pairs),hc=new Uint8Array(pairs);
    const ha=new Uint16Array(pairs),hs=new Float32Array(pairs),ht=new Float32Array(pairs),hstep=new Float32Array(pairs);
    ht.fill(NaN);hstep.fill(NaN);
    let links=0,headingLinks=0;const currentPoses=[];
    entries.forEach(([,history],animal)=>{
      const points=history.points;
      for (let index=1;index<points.length;index++) {
        const previous=points[index-1],point=points[index];if(point.broken)continue;
        const v=links*4,a=links*2;
        vertices[v]=previous.x;vertices[v+1]=previous.z;vertices[v+2]=point.x;vertices[v+3]=point.z;
        colors[a]=colors[a+1]=history.color;animals[a]=animals[a+1]=animal;segments[a]=segments[a+1]=animal;
        times[a]=previous.t;times[a+1]=point.t;links++;
        // Recorded heading plots break at +/-180 rather than drawing a false turn.
        if (Math.abs(point.yaw-previous.yaw)>180) continue;
        const h=headingLinks*4,b=headingLinks*2;
        hv[h]=previous.t;hv[h+1]=previous.yaw;hv[h+2]=point.t;hv[h+3]=point.yaw;
        hc[b]=hc[b+1]=history.color;ha[b]=ha[b+1]=animal;headingLinks++;
      }
      const latest=points.at(-1);if(latest) currentPoses.push({...latest,panel:0,color:history.color,name:history.name,stale:history.stale});
    });
    const bounds=streamBounds(this.histories);
    return {trajectory:{vertices:vertices.subarray(0,links*4),panels:panels.subarray(0,links*2),colors:colors.subarray(0,links*2),
      animals:animals.subarray(0,links*2),segments:segments.subarray(0,links*2),times:times.subarray(0,links*2),samples:samples.subarray(0,links*2),
      panelCount:1,panelNames:['Live trajectories'],animalNames:names,columns:1,links,
      segmentCount:entries.length,segmentLabels:names,bounds,rois:[],currentPoses},
      heading:{mode:'trial',vertices:hv.subarray(0,headingLinks*4),panels:hp.subarray(0,headingLinks*2),colors:hc.subarray(0,headingLinks*2),
        samples:hs.subarray(0,headingLinks*2),animals:ha.subarray(0,headingLinks*2),trials:ht.subarray(0,headingLinks*2),steps:hstep.subarray(0,headingLinks*2),
        minTime:Math.max(0,this.now-this.seconds),maxTime:Math.max(.001,this.now),panelCount:1,
        panelNames:['Seconds since joining this block'],animalNames:names,animalColors:entries.map(([,h])=>h.color),columns:1,
        lineWidth:1.8,lineOpacity:.85},
      points:entries.reduce((count,[,h])=>count+h.points.length,0),rigs:entries.length};
  }
}
