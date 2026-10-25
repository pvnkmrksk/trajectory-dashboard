// Usage: node scripts/benchmark_native.mjs dataset.bin [worker.js]
// A real retained binary payload exercises the worker without browser timing noise.
import fs from 'node:fs';
import vm from 'node:vm';
import {performance} from 'node:perf_hooks';

const binary = fs.readFileSync(process.argv[2]);
const buffer = binary.buffer.slice(binary.byteOffset, binary.byteOffset + binary.byteLength);
const headerLength = new DataView(buffer).getUint32(0, true);
const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength)));
let response;
const context = vm.createContext({performance, console, self: {}, postMessage: message => { response = message; }});
vm.runInContext(fs.readFileSync(process.argv[3] || 'native_dashboard/static/worker.js', 'utf8'), context);
context.self.onmessage({data: {type: 'init', header, buffer, bodyOffset: 4 + headerLength + header.bodyPadding}});
if (response.type === 'error') throw new Error(response.error);
const state = {
  filters: Object.fromEntries(['config', 'scene', 'vr', 'fly', 'folder'].map(key =>
    [key, header.categories[key].map((_, index) => index)])),
  ranges: {}, groupBy: 'config', colorBy: 'categorical', pointBudget: 150000,
  roiReach: 3, roiNullProbability: .5, binSize: 0, boundPercent: 98,
  angleSource: 'orientation', statsUnit: 'trial', panelColumns: 2,
  polarR: [0, 1], polarValidMin: 0, headingBin: 2, headingSectors: 36,
  windows: [{xmin: -10, xmax: 0, zmin: -10, zmax: 10}, {xmin: 0, xmax: 10, zmin: -10, zmax: 10}],
};
let requestId = 0;
const output = {counts: header.counts, runs: {}};
for (const scope of ['trajectory', 'heatmap', 'direction', 'polar', 'roi', 'statistics']) {
  const timings = [];
  for (let repeat = 0; repeat < 4; repeat++) {
    const started = performance.now();
    context.self.onmessage({data: {type: 'compute', requestId: ++requestId, scope, state}});
    if (response.type === 'error') throw new Error(response.error);
    timings.push(+(performance.now() - started).toFixed(2));
    if (scope === 'roi' && response.products.roi.choice) output.choices = response.products.roi.choice;
  }
  output.runs[scope] = {firstMs: timings[0], warmMedianMs: timings.slice(1).sort((a,b) => a-b)[1]};
}
// Burst updates must leave the last request with exactly its requested category subset.
const stress = performance.now();
for (let i = 0; i < 30; i++) {
  state.filters.vr = i === 29 ? [] : [i % header.categories.vr.length];
  context.self.onmessage({data: {type: 'compute', requestId: ++requestId, scope: 'trajectory', state}});
  if (response.type === 'error') throw new Error(response.error);
}
output.stress = {updates: 30, totalMs: +(performance.now() - stress).toFixed(2),
  finalVisibleSegments: response.summary.visibleSegments};
process.stdout.write(JSON.stringify(output, null, 2) + '\n');
