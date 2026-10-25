'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map(), charts = [], intervals = [];
function element() { return {textContent:'', dataset:{}, children:[], clientWidth:720, clientHeight:420, append(v){this.children.push(v);}, replaceChildren(){this.children=[];}}; }
const $ = id => { if(!elements.has(id))elements.set(id,element());return elements.get(id); };
let stream;
const state = {receiver:{endpoint:'tcp://host:9880',state:'receiving',snapshotAgeSeconds:2},context:{sessionId:'run-A',scene:{worldUnitsPerMetre:100},animals:[{vrId:'VR1',animalId:'animal β',speciesName:'Agrotis infusa',backend:'HejjéTrac'}]},telemetry:{schemaVersion:2,sequence:1,timestampUtc:'2026-10-25T12:00:00Z',experimentContext:{sessionId:'run-A'},system:{sceneName:'Choice',sceneNumber:1,trialNumber:1},frameTiming:{framesPerSecond:120,frameMeanMs:8.3},rigs:[{vrId:'VR1',pose:{x:1,z:2,yaw:45},input:{state:'connected',latest:{ageSeconds:.1,fresh:true}}}]}};
const context = {document:{getElementById:$,createElement:element},echarts:{init(){const c={series:[],setOption(o){if(o.series)this.series=o.series;},clear(){this.series=[];},on(){},dispatchAction(){},resize(){}};charts.push(c);return c;}},ResizeObserver:class{observe(){}},EventSource:class{constructor(){stream=this;this.handlers={};}addEventListener(name,fn){this.handlers[name]=fn;}},fetch:async()=>({json:async()=>({...state,controlAvailable:true})}),setInterval:fn=>intervals.push(fn),Date,Map,Set,Number,JSON,Infinity,Math};
vm.createContext(context);
vm.runInContext(fs.readFileSync('native_dashboard/static/live.js','utf8'),context);
function emit(s){stream.handlers.snapshot({data:JSON.stringify(s)});intervals[0]();}
(async()=>{
 await new Promise(resolve=>setImmediate(resolve));
 emit(state);
 assert.equal($('fps').textContent,'120.0 FPS');
 assert.match($('position-units').textContent,/centimetres/);
 assert.equal($('animals').children[0].children[0].textContent,'animal β');
 assert.equal($('animals').children[0].children[2].dataset.stale,'true','A fresh heartbeat must not make an old pose/input fresh');
 for(let i=2;i<=2200;i++){state.telemetry.sequence=i;state.telemetry.rigs[0].pose.x=i;emit(state);}
 assert.equal(charts[0].series[0].data.length,1800);
 state.receiver.endpoint='tcp://other-host:9880';state.telemetry.sequence=2201;emit(state);
 assert.equal(charts[0].series[0].data.length,1,'Remote viewers clear trails when the server changes endpoint');
 state.telemetry.experimentContext.sessionId='run-B';state.telemetry.sequence=2202;emit(state);
 assert.equal($('run-id').textContent,'run-B');
 assert.equal($('animals').children[0].children[0].textContent,'VR1','Do not label new-session poses with old-session animals');
 state.telemetry.sequence=1;emit(state);
 assert.equal(charts[0].series[0].data.length,1,'Publisher restart clears history');
 state.telemetry.sequence=2;state.telemetry.rigs=Array.from({length:40},(_,i)=>({vrId:`VR${i+1}`,pose:{x:i,z:i,yaw:0}}));emit(state);
 assert.equal(charts[0].series.length,32);
 state.telemetry.sequence=3;state.telemetry.rigs=[{vrId:'VR99',pose:{x:0,z:0,yaw:0}}];emit(state);
 assert.equal(charts[0].series.length,1,'Changing rigs cannot grow retained histories without bound');
 $('pause-view').onclick();state.telemetry.sequence=4;emit(state);assert.equal($('pause-view').textContent,'Resume view');
 $('pause-view').onclick();intervals[0]();assert.equal(charts[0].series[0].data.length,2);
 $('clear-view').onclick();intervals[0]();assert.equal(charts[0].series.length,0);
 console.log('PASS: live frontend units/FPS, Unicode, stale input, bounded histories, endpoint/session/restart changes and pause/clear controls');
})().catch(error=>{console.error(error);process.exitCode=1;});
