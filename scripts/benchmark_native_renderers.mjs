#!/usr/bin/env node
/**
 * Benchmark chart-option assembly, without a browser or third-party packages.
 * This measures JavaScript product preparation, not ECharts painting or UI FPS.
 *
 * Reproduce the pre-polish comparison:
 *   node scripts/benchmark_native_renderers.mjs --baseline 282230d
 * Optional: --repeats 12. The baseline is read from Git without changing files.
 */
import fs from "node:fs";
import vm from "node:vm";
import {execFileSync} from "node:child_process";
import {performance} from "node:perf_hooks";
import {fileURLToPath} from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const options = process.argv.slice(2);
function argument(name, fallback) {
  const index = options.indexOf(name);
  return index >= 0 ? options[index + 1] : fallback;
}
const baseline = argument("--baseline", "HEAD");
const repeats = Math.max(1, Math.min(100, Number(argument("--repeats", 6)) || 6));
const rendererPath = "native_dashboard/static/echarts_renderers.js";

function loadRenderers(source) {
  // The measured option builders require a palette/formatter, but never create
  // a DOM node or ECharts instance. Keep the same lightweight dependencies in
  // both versions, so observer setup and library loading do not enter timings.
  const context = vm.createContext({
    NATIVE_PALETTE: Array(100).fill("#123456"),
    NEUTRAL_INDEX: 20, SEQUENTIAL_OFFSET: 24,
    formatNumber: value => String(value),
  });
  const script = source.replace(/^import .*;\n/m, "").replace(/^export /gm, "");
  vm.runInContext(`${script}\nthis.Renderers={EChartsMetricsRenderer,EChartsRoiRenderer};`, context);
  return context.Renderers;
}

const observations = 6000, animalCount = 40, panelCount = 12, errorCount = 200000;
const values = Array.from({length: observations}, (_, index) => (index % 1000) * .1);
const times = Array.from({length: observations}, (_, index) => index % 50);
const errors = Array.from({length: errorCount}, (_, index) => (index % 360) - 180);
const animalNames = Array.from({length: animalCount}, (_, index) => `Animal ${index}`);
const panelNames = Array.from({length: panelCount}, (_, index) => `Panel ${index}`);
const metrics = {
  panelCount, panelNames, animalNames,
  distance: values, displacement: values, speed: values, tortuosity: values,
  panel: values.map((_, index) => index % panelCount),
  animal: values.map((_, index) => index % animalCount),
  replicate: values, trial: values, step: values,
};
const pairedCount = animalCount * panelCount;
const roi = {
  panelNames, animalNames,
  animalCode: Array.from({length: pairedCount}, (_, index) => index % animalCount),
  animalPanel: Array.from({length: pairedCount}, (_, index) => Math.floor(index / animalCount)),
  leftFraction: Array(pairedCount).fill(.4), rightFraction: Array(pairedCount).fill(.6),
  leftResidence: Array(pairedCount).fill(2), rightResidence: Array(pairedCount).fill(3),
  timeValues: times, timeSides: times.map((_, index) => index % 2),
  timeAnimals: times.map((_, index) => index % animalCount),
  timePanels: times.map((_, index) => index % panelCount), timeTrials: times,
  errorValues: errors, errorSides: errors.map((_, index) => index % 2),
  errorAnimals: errors.map((_, index) => index % animalCount),
  errorPanels: errors.map((_, index) => index % panelCount), errorTrials: errors,
};

console.log(JSON.stringify({
  measurement: "chart option assembly only", baseline, repeats,
  observationsPerMetric: observations, metrics: 4, panels: panelCount,
  animals: animalCount, roiPairedSummaries: pairedCount,
  roiTimeValues: times.length, roiHeadingErrors: errors.length,
}));

const sources = [
  ["baseline", execFileSync("git", ["show", `${baseline}:${rendererPath}`], {cwd: root, encoding: "utf8"})],
  ["working-tree", fs.readFileSync(path.join(root, rendererPath), "utf8")],
];
for (const [version, source] of sources) {
  const types = loadRenderers(source);
  for (const [type, data] of [["EChartsMetricsRenderer", metrics], ["EChartsRoiRenderer", roi]]) {
    const renderer = Object.assign(Object.create(types[type].prototype), {
      data, host: {clientWidth: 1200, clientHeight: 800},
    });
    const coldStart = performance.now();
    renderer.option();
    const coldMs = performance.now() - coldStart;
    const samples = [];
    for (let repeat = 0; repeat < repeats; repeat += 1) {
      const started = performance.now(); renderer.option();
      samples.push(performance.now() - started);
    }
    console.log(JSON.stringify({
      version, renderer: type, coldMs: +coldMs.toFixed(2),
      warmMeanMs: +(samples.reduce((total, value) => total + value, 0) / repeats).toFixed(2),
      warmMinMs: +Math.min(...samples).toFixed(2),
    }));
  }
}
