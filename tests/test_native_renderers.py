"""Headless contracts for analytical chart options and framebuffer exports."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _run_node(assertions):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for native renderer contracts")
    harness = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = process.argv[1];
function load(file, names, extras = {}) {
  const context = vm.createContext(extras);
  let source = fs.readFileSync(root + '/native_dashboard/static/' + file, 'utf8');
  source = source.replace(/^import .*;\n/m, '').replace(/^export /gm, '');
  vm.runInContext(source + '\nthis.exports = {' + names.join(',') + '};', context);
  return {context, ...context.exports};
}
const native = load('renderers.js', ['TrajectoryRenderer', 'PolarRenderer', 'NATIVE_PALETTE', 'NEUTRAL_INDEX', 'SEQUENTIAL_OFFSET', 'formatNumber', 'drawCurrentPoses', 'setDisplayUnits', 'displayUnits']);
const charts = load('echarts_renderers.js', ['EChartsMetricsRenderer', 'EChartsRoiRenderer', 'EChartsHistogramRenderer', 'EChartsPolarRenderer', 'EChartsHeadingRenderer', 'compactLetters'], native);
function renderer(Type, data) {
  const result = Object.create(Type.prototype);
  Object.assign(result, {data, host: {clientWidth: 1200, clientHeight: 750}, fraction: 1});
  return result;
}
"""
    result = subprocess.run(
        [node, "-e", harness + "\n" + assertions, str(ROOT)],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_metric_p99_view_preserves_full_values_and_statistical_labels():
    _run_node(r"""
const values = Array.from({length: 101}, (_, i) => i === 100 ? 1e9 : i);
const data = {
  panelCount: 2, panelNames: ['A', 'B'], animalNames: ['Animal'],
  distance: values, displacement: values, speed: values, tortuosity: values,
  panel: values.map((_, i) => i % 2), animal: values.map(() => 0),
  replicate: values, trial: values, step: values.map(() => 0),
};
const chart = renderer(charts.EChartsMetricsRenderer, data);
chart.statistics = {metrics: [{metric: 'distance', pairwise: [{first: 0, second: 1, adjustedP: .001}]}]};
const option = chart.option();
assert.equal(option.dataZoom[0].endValue, 99);
assert.match(option.title[0].subtext, /1 above view/);
assert.match(option.xAxis[0].axisLabel.formatter('A', 0), /\n[A-Z]/);
const points = option.series.filter(series => series.name === 'Animal' && series.yAxisIndex === 0).flatMap(series => series.data);
assert.equal(points.length, values.length);
assert.equal(Math.max(...points.map(point => point.value[1])), 1e9);
const boxes = option.series.find(series => series.type === 'boxplot').data;
assert.equal(Math.max(...boxes.map(box => box[4])), 1e9);
const cache = chart.metricCache;
chart.option(); assert.equal(chart.metricCache, cache);
chart.data = {...data}; chart.option(); assert.notEqual(chart.metricCache, cache);
""")


def test_roi_fraction_tooltips_show_exact_counts_and_distinguish_first_reach():
    _run_node(r"""
const chart = renderer(charts.EChartsRoiRenderer, {
  panelNames: ['<panel>'], animalNames: ['<animal>'], animalCode: [0], animalPanel: [0],
  animalTrials: [10], leftReachedCount: [7], rightReachedCount: [5],
  leftFraction: [.7], rightFraction: [.5], leftResidence: [2], rightResidence: [3],
  timeValues: [], timeSides: [], timeAnimals: [], timeTrials: [], timePanels: [],
  errorValues: [], errorSides: [], errorAnimals: [], errorTrials: [], errorPanels: [],
  choice: [{left: 6, right: 3, ambiguous: 1, neither: 0}],
});
const option = chart.option();
assert.match(option.title[0].subtext, /6 left.*3 right.*1 tied.*0 neither/);
const item = option.series[0].data[0];
const tip = option.tooltip.formatter({data: item});
assert.match(tip, /70\.0%|70%/); assert.match(tip, /7 \/ 10 trials/);
assert.match(tip, /each side counted independently/);
assert.ok(tip.includes('&lt;animal&gt;')); assert.ok(!tip.includes('<animal>'));
assert.equal(option.yAxis[0].axisLabel.formatter(.5), '50%');
""")


def test_histogram_labels_excluded_tails_without_folding_counts():
    _run_node(r"""
const chart = renderer(charts.EChartsHistogramRenderer, {
  edges: [0, 1, 2], counts: [50, 49], finiteCount: 100, displayedCount: 99,
  overflow: 1, upperPercentile: .99,
});
chart.name = 'displacement-distribution';
const option = chart.option();
assert.equal(option.series[0].data.reduce((sum, item) => sum + item.value[1], 0), 99);
assert.match(option.graphic[0].style.text, /99 \/ 100 trials.*1 above P99/);
assert.match(option.tooltip.formatter({data: option.series[0].data[1]}), /49.*trials/);
assert.equal(option.xAxis.min, 0); assert.equal(option.xAxis.max, 2);
""")


def test_snapshot_flips_pixels_keeps_empty_overlays_and_restores_after_failure():
    _run_node(r"""
let captured;
const output = {
  getContext: () => ({putImageData: image => captured = Array.from(image.data), drawImage() {}}),
  toDataURL: () => 'data:image/png;base64,ok',
};
native.context.document = {createElement: () => output};
native.context.ImageData = class {constructor(data) {this.data = data;}};
const chart = renderer(native.TrajectoryRenderer, {panelCount: 1});
Object.assign(chart, {width: 640, height: 480, instanceCount: 0, resize() {}, draw() {}, overlay: {}});
chart.gl = {drawingBufferWidth: 2, drawingBufferHeight: 2, isContextLost: () => false,
  readPixels: (...args) => args.at(-1).set([1,2,3,255,4,5,6,255,7,8,9,255,10,11,12,255])};
assert.equal(chart.snapshotDataUrl(1200, 700), 'data:image/png;base64,ok');
assert.deepEqual(captured.slice(0, 8), [7,8,9,255,10,11,12,255]);
assert.equal(chart.width, 640); assert.equal(chart.height, 480);
chart.gl.readPixels = (...args) => args.at(-1).fill(255);
assert.ok(chart.snapshotDataUrl(1200, 700)); assert.equal(chart.lastSnapshotInk, 0);
chart.gl.readPixels = () => {throw new Error('capture failed');};
assert.throws(() => chart.snapshotDataUrl(1200, 700), /capture failed/);
assert.equal(chart.width, 640); assert.equal(chart.height, 480);
""")


def test_compact_letters_split_many_significant_pairs_without_duplicate_columns():
    _run_node(r"""
const pairs = [];
for (let first = 0; first < 32; first++) for (let second = first + 1; second < 32; second++) {
  pairs.push({first, second, adjustedP: .001});
}
const letters = charts.compactLetters(32, pairs);
assert.equal(new Set(letters).size, 32);
assert.equal(letters.length, 32);
assert.ok(letters.every(letter => letter.length > 0 && !/[\[\\\]^_`]/.test(letter)));
""")


def test_statistics_refresh_preserves_zoom_and_replaces_stale_series():
    _run_node(r"""
const chart = renderer(charts.EChartsMetricsRenderer, {animalNames: []});
chart.option = () => ({dataZoom: [{type: 'inside', yAxisIndex: 0, startValue: 0, endValue: 99}], series: []});
let captured;
chart.chart = {setOption: (option, settings) => captured = {option, settings}};
chart.setStatistics({metrics: []});
assert.equal(captured.settings.notMerge, false);
assert.ok(captured.settings.replaceMerge.includes('series'));
assert.equal(captured.option.dataZoom[0].endValue, undefined);
assert.equal(captured.option.dataZoom[0].startValue, undefined);
chart.setData({animalNames: []});
assert.equal(captured.settings.notMerge, true);
assert.equal(captured.option.dataZoom[0].endValue, 99);
""")


def test_polar_visibility_rebuilds_series_so_hidden_animals_can_be_restored():
    _run_node(r"""
const chart = renderer(charts.EChartsPolarRenderer, {animalNames: ['A', 'B']});
chart.animalVisibility = [true, true];
let redraws = 0;
chart.draw = () => redraws++;
chart.setAnimalVisibility([false, true]);
chart.setAnimalVisibility([true, true]);
chart.setAnimalVisibility([true, true]);
assert.equal(redraws, 2);
assert.equal(chart.animalVisibility[0], true);
""")


def test_zero_resultant_has_no_direction_histogram_or_ray_but_preserves_heading_density():
    _run_node(r"""
const rawDensity = new Uint32Array(72);
rawDensity[0] = 1; rawDensity[36] = 1;
const data = {panelCount: 1, panelNames: ['All'], animalNames: ['Animal'],
  angle: [NaN], r: [0], panel: [0], animal: [0], sample: [0], color: [0],
  replicate: [1], trial: [0], step: [0],
  headingDensity: rawDensity, densityBins: 72,
  populationAngle: [NaN], populationR: [0], mode: 'histogram',
};
const chart = renderer(charts.EChartsPolarRenderer, data);
chart.radialMax = 1;
const histogram = chart.option();
assert.ok(histogram.series[0].data.every(point => point[0] === 0));
data.mode = 'vectors';
assert.equal(chart.option().series.length, 0);
data.mode = 'density';
assert.ok(chart.option().series[0].data.some(point => point[0] > 0));
assert.equal(rawDensity.reduce((total, count) => total + count, 0), 2);
const legacy = renderer(native.PolarRenderer, data);
legacy.width = 400; legacy.height = 400; legacy.begin = () => {};
const nonfinite = [];
legacy.ctx = new Proxy({}, {get: (_target, key) => (...args) => {
  if (['lineTo', 'moveTo', 'arc'].includes(key) && args.some(value => !Number.isFinite(value))) nonfinite.push(args);
}});
legacy.draw();
assert.equal(nonfinite.length, 0);
""")


def test_live_heading_options_reuse_recorded_axes_and_colour_without_changing_defaults():
    _run_node(r"""
const data = {panelCount: 1, panelNames: ['Current block'], maxTime: 125,
  panels: [0, 0], animals: [0, 0], samples: [0, 0], vertices: [124, -90, 125, 0],
  trials: [1, 1], steps: [1, 1], animalNames: ['VR2'], mode: 'trial'};
const saved = renderer(charts.EChartsHeadingRenderer, data).option();
assert.equal(saved.xAxis[0].min, 0);assert.equal(saved.yAxis[0].min, -180);
assert.equal(saved.yAxis[0].max, 180);assert.equal(saved.series[0].lineStyle.width, .8);
const live = renderer(charts.EChartsHeadingRenderer, {...data,minTime: 65,
  lineWidth: 1.8,lineOpacity: .85,animalColors: [1]}).option();
assert.equal(live.xAxis[0].min, 65);assert.equal(live.xAxis[0].max, 125);
assert.equal(live.series[0].lineStyle.color, native.NATIVE_PALETTE[1]);
assert.equal(live.series[0].lineStyle.width, 1.8);
assert.equal(live.series[0].data[0].coords[0][1], -90);
""")


def test_live_current_heading_markers_follow_unity_xz_coordinates_and_honest_units():
    _run_node(r"""
const rotations = [], positions = [];
const ctx = new Proxy({}, {set: () => true, get: (_, key) => {
  if (key === 'rotate') return value => rotations.push(value);
  if (key === 'translate') return (x,y) => positions.push([x,y]);
  return () => {};
}});
native.drawCurrentPoses(ctx,800,500,{panelCount:1,currentPoses:[
  {x:0,z:0,yaw:0,color:0},{x:1,z:1,yaw:90,color:1},{x:0,z:0,yaw:NaN},
]}, {xmin:-2,xmax:2,zmin:-2,zmax:2});
assert.equal(rotations.length, 2);assert.equal(rotations[0], 0);
assert.ok(Math.abs(rotations[1] - Math.PI/2) < 1e-10);
assert.ok(positions[1][0] > positions[0][0]);
assert.ok(positions[1][1] < positions[0][1], '+Z must render upward as in recorded paths');
native.setDisplayUnits({perData:1,name:'world units'});
assert.equal(native.displayUnits.name, 'world units', 'Unknown scene scale cannot be labelled centimetres');
""")
