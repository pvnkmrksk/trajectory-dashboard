"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const moduleContext=vm.createContext({Map,Set,Number,Date,Math,Infinity,Float32Array,Uint16Array,Uint32Array,Uint8Array});
vm.runInContext(fs.readFileSync('native_dashboard/static/live_model.js','utf8').replace(/^export /gm,'')+'\nthis.api={LivePlotModel,signedHeading,followBounds,rigColor};',moduleContext);
const api=moduleContext.api;
assert.deepEqual([0,90,180,270,360,-90,-540].map(api.signedHeading),[0,90,-180,-90,0,-90,-180]);
const frame=(sequence,time,yaw=45)=>({sequence,realtimeSeconds:time,timestampUtc:new Date(1790000000000+time*1000).toISOString(),rigs:[{vrId:'VR1',pose:{x:time,z:time/2,yaw},input:{latest:{fresh:true,ageSeconds:0}}}]});
const m=new api.LivePlotModel();m.receive(frame(1,10,179));m.receive(frame(2,10.1,181));
assert.equal(m.payloads().trajectory.links,1);assert.equal(m.payloads().heading.vertices.length,0,'Do not draw a false turn across the +/-180 seam');
m.receive(frame(3,10.2,182));assert.equal(m.payloads().heading.vertices.length,4);
m.receive(frame(3,10.2,182),[{vrId:'VR1',animalId:'animal β'}],5);
assert.equal(m.payloads().points,3);assert.equal(m.payloads().trajectory.currentPoses[0].stale,true);
assert.equal(m.payloads().trajectory.animalNames[0],'VR1 · animal β');
const invalid=frame(4,10.3);invalid.rigs[0].pose.x=NaN;m.receive(invalid);m.receive(frame(5,10.4));
assert.equal(m.payloads().trajectory.links,2,'Invalid poses break paths without creating phantom motion');
m.receive(frame(6,20));assert.equal(m.payloads().trajectory.links,2,'A real telemetry outage breaks the path');
const bounded=m.payloads();assert.equal(bounded.trajectory.vertices.length,bounded.trajectory.links*4);
assert.equal(bounded.heading.panels.length,bounded.heading.vertices.length/2,'Unused buffer capacity cannot create ghost links');
m.receive(frame(1,30));assert.equal(m.payloads().points,1,'Publisher reset clears retained samples');
m.receive(frame(2,29));assert.equal(m.payloads().points,1,'A source-clock reset starts a fresh viewing window');
m.receive(frame(2,29));assert.equal(m.payloads().points,1,'Repeated reset packet is not duplicated');
const capped=new api.LivePlotModel();for(let i=0;i<5000;i++)capped.receive(frame(i,i*.001));
assert.equal(capped.payloads().points,1800);assert.equal(capped.droppedForLimit,true);
const windowed=new api.LivePlotModel();windowed.setWindow(30);for(let i=0;i<1000;i++)windowed.receive(frame(i,i*.1));
assert.ok(windowed.payloads().points<=301);assert.ok(windowed.histories.get('VR1').points[0].t>=windowed.now-30);
const many=frame(1,0);many.rigs=Array.from({length:50},(_,i)=>({vrId:`VR${i+1}`,pose:{x:i,z:0,yaw:90}}));
windowed.receive(many);assert.equal(windowed.histories.size,32);windowed.receive({...frame(2,1),rigs:[{vrId:'VR99',pose:{x:0,z:0,yaw:0}}]});assert.equal(windowed.histories.size,1);
assert.equal(api.rigColor('VR2'),1);
const range={xmin:-10,xmax:10,zmin:-10,zmax:10};assert.equal(api.followBounds(range,{xmin:-8,xmax:8,zmin:-8,zmax:8}),range,'Ordinary jitter must not move the axes');
const grown=api.followBounds(range,{xmin:9,xmax:30,zmin:0,zmax:10});assert.ok(grown.xmax>=30&&grown.xmin<=-10);

const elements=new Map(),charts=[],intervals=[];
function element(){return {textContent:'',style:{},dataset:{},children:[],clientWidth:720,clientHeight:420,append(...v){this.children.push(...v);},replaceChildren(){this.children=[];}};}
const $=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
class PathRenderer{constructor(host,callback){this.callback=callback;this.view={xmin:-1,xmax:1,zmin:-1,zmax:1};charts.push(this);}setLineStyle(){}setData(data){this.data=data;}setView(view){this.view=view;}}
class HeadingRenderer{constructor(){charts.push(this);}setData(data){this.data=data;}}
let stream;const state={receiver:{endpoint:'tcp://host:9880',state:'receiving',snapshotAgeSeconds:0},context:{sessionId:'run-A',inputKind:'demonstration',scene:{worldUnitsPerMetre:100},animals:[{vrId:'VR1',animalId:'animal β',speciesName:'Agrotis infusa',backend:'HejjéTrac'}]},telemetry:{...frame(1,0),schemaVersion:2,experimentContext:{sessionId:'run-A'},system:{sceneName:'Choice',sceneNumber:1,trialNumber:1},frameTiming:{framesPerSecond:120,frameMeanMs:8.3}}};
const context={document:{getElementById:$,createElement:element,createTextNode:value=>({textContent:value}),addEventListener(){}},...api,TrajectoryRenderer:PathRenderer,EChartsHeadingRenderer:HeadingRenderer,setDisplayUnits(){},NATIVE_PALETTE:Array(16).fill('#246b78'),EventSource:class{constructor(){stream=this;this.handlers={};}addEventListener(name,fn){this.handlers[name]=fn;}},fetch:async()=>({json:async()=>({...state,controlAvailable:true})}),setInterval:fn=>intervals.push(fn),Date,Map,Set,Number,JSON,Infinity,Math};
vm.createContext(context);vm.runInContext(fs.readFileSync('native_dashboard/static/live.js','utf8').replace(/^import .*;\n/gm,''),context);
function emit(s){stream.handlers.snapshot({data:JSON.stringify(s)});intervals[0]();}
(async()=>{
 await new Promise(resolve=>setImmediate(resolve));emit(state);
 assert.equal($('fps').textContent,'120.0 FPS');assert.match($('position-units').textContent,/centimetres/);
 assert.match($('input-kind').textContent,/Demonstration/);assert.equal($('animals').children[0].children[0].textContent,'animal β');
 assert.equal(charts[0].data.currentPoses.length,1);assert.equal(charts[0].data.links,0);
 state.receiver.snapshotAgeSeconds=4;state.context.animals[0].animalId='late context animal';emit(state);
 assert.equal($('animals').children[0].children[2].dataset.stale,'true');assert.equal(charts[0].data.currentPoses[0].stale,true);
 assert.equal(charts[0].data.links,0,'Repeating a snapshot cannot append path points');
 state.receiver.snapshotAgeSeconds=0;state.telemetry={...state.telemetry,...frame(2,.1)};emit(state);assert.equal(charts[0].data.links,1);
 charts[0].callback();assert.equal($('fit-paths').textContent,'Fit & follow');const held=charts[0].view;
 state.telemetry={...state.telemetry,...frame(3,.2)};emit(state);assert.equal(charts[0].view,held,'Manual spatial view survives incoming data');
 $('fit-paths').onclick();assert.equal($('fit-paths').textContent,'Auto range on');
 $('pause-view').onclick();state.telemetry={...state.telemetry,...frame(4,.3)};emit(state);assert.equal(charts[0].data.links,2,'Pause freezes drawings, not acquisition');
 assert.match($('view-window').textContent,/Paused/);$('fit-paths').onclick();assert.equal(charts[0].data.links,2,'Fitting a paused chart must not reveal newer samples');
 $('pause-view').onclick();assert.equal(charts[0].data.links,3);
 state.receiver.endpoint='tcp://other-host:9880';state.telemetry={...state.telemetry,...frame(5,.4)};emit(state);assert.equal(charts[0].data.links,0,'Endpoint changes clear trails');
 state.telemetry.experimentContext.sessionId='run-B';state.telemetry={...state.telemetry,...frame(6,.5)};emit(state);
 assert.equal($('run-id').textContent,'run-B');assert.equal($('animals').children[0].children[0].textContent,'VR1','New sessions cannot inherit old animals');
 $('clear-view').onclick();assert.equal(charts[0].data.currentPoses.length,0);
 state.context={sessionId:'run-B',timestampUtc:new Date(1790000010000).toISOString(),experiment:{sceneName:'Swarm',sceneNumber:2,trialNumber:1,configFile:'swarm.json'},scene:{name:'Swarm',worldUnitsPerMetre:1}};state.receiver.contextAgeSeconds=0;state.receiver.snapshotAgeSeconds=4;emit(state);
 assert.match($('scene').textContent,/Swarm/);assert.match($('position-units').textContent,/metres/);assert.equal(charts[0].data.currentPoses.length,0);assert.match($('animals').children[0].children[0].textContent,/Waiting/);
 state.telemetry={...state.telemetry,...frame(7,11),system:{sceneName:'Swarm',sceneNumber:2,trialNumber:1},experimentContext:{sessionId:'run-B',worldUnitsPerMetre:1}};emit(state);assert.equal(charts[0].data.currentPoses.length,1);
 state.context.timestampUtc=new Date(1790000009000).toISOString();state.context.experiment={sceneName:'Choice',sceneNumber:1,trialNumber:1,configFile:'old.json'};state.context.scene={name:'Choice',worldUnitsPerMetre:100};emit(state);
 assert.match($('scene').textContent,/Swarm/);assert.match($('position-units').textContent,/metres/);assert.notEqual($('config').textContent,'old.json');
 console.log('PASS: shared live rendering, signed headings, gap/clock boundaries, sample/time/rig bounds, stable auto range, metadata, pause and manual view preservation');
})().catch(error=>{console.error(error);process.exitCode=1;});
