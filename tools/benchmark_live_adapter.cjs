// Bounded preparation cost only; this is not a browser/GPU/acquisition benchmark.
'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({Map,Set,Number,Date,Math,Infinity,Float32Array,Uint16Array,Uint32Array,Uint8Array});
vm.runInContext(fs.readFileSync('native_dashboard/static/live_model.js','utf8').replace(/^export /gm,'')+'\nthis.Model=LivePlotModel;',context);
const measurements=[];
for(const rigCount of [4,16,32]) {
  const model=new context.Model();
  for(let sample=0;sample<2000;sample++)model.receive({sequence:sample,realtimeSeconds:sample*.01,
    rigs:Array.from({length:rigCount},(_,rig)=>({vrId:`VR${rig+1}`,pose:{
      x:Math.sin(sample*.01)*100+rig,z:Math.cos(sample*.01)*100,yaw:sample%360,
    }})),
  });
  for(let warmup=0;warmup<3;warmup++)model.payloads();
  const elapsed=[];let data;
  for(let repeat=0;repeat<12;repeat++) {
    const start=performance.now();data=model.payloads();elapsed.push(performance.now()-start);
  }
  assert.equal(data.points,rigCount*1800);assert.equal(data.trajectory.links,rigCount*1799);
  elapsed.sort((a,b)=>a-b);
  measurements.push({rigs:rigCount,samples:data.points,pathLinks:data.trajectory.links,
    medianMs:elapsed[6],p95Ms:elapsed[11]});
}
console.log(JSON.stringify({scope:'Node stream adapter only; not browser/GPU/acquisition timing',measurements},null,2));
