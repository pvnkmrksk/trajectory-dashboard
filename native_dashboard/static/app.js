import {
  TrajectoryRenderer, HeatmapRenderer, DirectionRenderer, NATIVE_PALETTE, formatNumber,
} from "/static/renderers.js";
import {
  EChartsPolarRenderer, EChartsHeadingRenderer, EChartsMetricsRenderer,
  EChartsRoiRenderer, EChartsHistogramRenderer,
} from "/static/echarts_renderers.js";

const byId = id => document.getElementById(id);
const shell = byId("app-shell");
const statusDock = byId("status-dock");
const statusTitle = byId("status-title");
const statusDetail = byId("status-detail");
const sourceInput = byId("source-input");
const applyButton = byId("apply-button");
const resetViewButton = byId("reset-view-button");

let datasetHeader = null;
let worker = null;
let workerReady = false;
let workerBusy = false;
let pendingCompute = null;
let latestRequest = 0;
let displayedRequest = 0;
let latestInspectRequest = 0;
let products = {};
let sharedView = null;
let newDataset = false;
let sampleSeed = 0;
let playbackFrame = null;
let playbackLast = 0;
let rings = [{x: 0, z: 0, r: 3}];
let activeRing = 0;
let animalVisibility = [];
let displayNames = {};
let visibleSegmentOptions = [];
let currentDurationSummary = null;
let lastSummary = null;
let ringFrame = null;
let currentLens = "trajectory";
let currentView = "trajectory";
let panelOrders = {};
const rangeControls = new Map();

function setStatus(kind, title, detail) {
  statusDock.className = `status-dock ${kind || ""}`;
  statusDock.title = detail ? `${title} — ${detail}` : title;
  statusTitle.textContent = title;
  statusDetail.textContent = detail || "";
}

function syncView(view, source) {
  sharedView = {...view};
  for (const renderer of spatialRenderers) if (renderer !== source && renderer.data) renderer.setView(sharedView, false);
}

let trajectoryRenderer;
let heatmapRenderer;
let directionRenderer;
let polarRenderer;
let headingRenderer;
let metricsRenderer;
let roiRenderer;
let velocityHistogram;
let displacementHistogram;

try {
  trajectoryRenderer = new TrajectoryRenderer(byId("trajectory-plot"), syncView);
  heatmapRenderer = new HeatmapRenderer(byId("heatmap-plot"), syncView);
  directionRenderer = new DirectionRenderer(byId("direction-plot"), syncView);
  polarRenderer = new EChartsPolarRenderer(byId("polar-plot"));
  headingRenderer = new EChartsHeadingRenderer(byId("heading-plot"));
  metricsRenderer = new EChartsMetricsRenderer(byId("metrics-plot"));
  roiRenderer = new EChartsRoiRenderer(byId("roi-plot"));
  velocityHistogram = new EChartsHistogramRenderer(byId("velocity-hist"), "velocity-distribution");
  displacementHistogram = new EChartsHistogramRenderer(byId("displacement-hist"), "displacement-distribution");
} catch (error) {
  setStatus("error", "Renderer unavailable", error.message);
  throw error;
}
const spatialRenderers = [trajectoryRenderer, heatmapRenderer, directionRenderer];
trajectoryRenderer.setInspectHandler((point, view) => {
  if (!workerReady || !worker) return;
  const requestId = ++latestInspectRequest;
  worker.postMessage({
    type: "inspect", requestId, panel: point.panel, x: point.x, z: point.z,
    tolerance: Math.max(view.xmax - view.xmin, view.zmax - view.zmin) * .018,
  });
  byId("segment-inspector").textContent = "Finding the nearest retained segment…";
});
trajectoryRenderer.setRingMoveHandler((index, x, z, final, radius = null) => {
  if (!rings[index]) return;
  rings[index] = {...rings[index], x, z,
    r: Number.isFinite(radius) ? Math.max(.01, radius) : rings[index].r};
  activeRing = index;
  updateRingControlValues();
  scheduleLocalRingObserver(final);
});

function parseBinary(buffer) {
  const view = new DataView(buffer);
  const headerLength = view.getUint32(0, true);
  const headerText = new TextDecoder().decode(new Uint8Array(buffer, 4, headerLength));
  const parsed = JSON.parse(headerText);
  return {header: parsed, bodyOffset: 4 + headerLength + (parsed.bodyPadding || 0)};
}

function formatCount(value) {
  return new Intl.NumberFormat().format(Number(value) || 0);
}

function displayRangeBound(value, integer = false, upper = false) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  if (integer) return Math.round(number);
  const scaled = number * 10;
  return (upper ? Math.ceil(scaled - 1e-9) : Math.floor(scaled + 1e-9)) / 10;
}

function datasetSummary(visible = null) {
  const counts = datasetHeader?.counts;
  if (!counts) return;
  const rows = visible?.visibleRows ?? counts.retainedRows;
  const segments = visible?.visibleSegments ?? counts.segments;
  byId("dataset-summary").innerHTML = `
    <div><b>${formatCount(rows)}</b><span>visible / ${formatCount(counts.retainedRows)} rows</span></div>
    <div><b>${formatCount(segments)}</b><span>visible / ${formatCount(counts.segments)} segments</span></div>
    <div><b>${formatCount(counts.animals)}</b><span>animals</span></div>
    <div><b>${formatCount(counts.files)}</b><span>files${counts.duplicateFilesSkipped ? ` · ${formatCount(counts.duplicateFilesSkipped)} duplicate copies skipped` : ""}</span></div>`;
}

function applyAnimalVisibility() {
  trajectoryRenderer.setAnimalVisibility(animalVisibility);
  const charts = [polarRenderer, headingRenderer, metricsRenderer, roiRenderer];
  for (const chart of charts) chart.animalVisibility = [...animalVisibility];
  const activeSection = {
    polar: "polar-section", heading: "heading-section", metrics: "metrics-section",
    roi: "roi-section",
  }[currentView];
  const activeChart = {
    "polar-section": polarRenderer,
    "heading-section": headingRenderer,
    "metrics-section": metricsRenderer,
    "roi-section": roiRenderer,
  }[activeSection];
  activeChart?.syncAnimalLegend?.();
}

function renderAnimalVisibility() {
  const host = byId("animal-visibility");
  host.replaceChildren();
  const names = datasetHeader?.categories?.animal || [];
  if (animalVisibility.length !== names.length) animalVisibility = names.map(() => true);
  names.forEach((name, index) => {
    const label = document.createElement("label");
    label.className = "animal-toggle";
    label.style.setProperty("--animal-color", NATIVE_PALETTE[index % 16]);
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox"; checkbox.checked = animalVisibility[index] !== false;
    checkbox.setAttribute("aria-label", `Show ${name}`);
    const dot = document.createElement("span"); dot.className = "animal-dot";
    const text = document.createElement("span"); text.textContent = name;
    label.append(checkbox, dot, text); host.appendChild(label);
    checkbox.addEventListener("change", () => {
      animalVisibility[index] = checkbox.checked;
      label.classList.toggle("muted", !checkbox.checked);
      applyAnimalVisibility();
    });
  });
  byId("animals-all").disabled = !names.length;
  byId("animals-none").disabled = !names.length;
}

function drawMiniHistogram(canvas, histogram, selected) {
  const width = Math.max(180, canvas.clientWidth || 260), height = 46;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  const context = canvas.getContext("2d");
  context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, width, height);
  const counts = histogram.counts || [], edges = histogram.edges || [0, 1];
  const maximum = Math.max(1, ...counts);
  const span = Math.max(1e-12, edges[edges.length - 1] - edges[0]);
  for (let index = 0; index < counts.length; index += 1) {
    const x0 = (edges[index] - edges[0]) / span * width;
    const x1 = (edges[index + 1] - edges[0]) / span * width;
    const bar = Math.max(1, counts[index] / maximum * (height - 5));
    const midpoint = (edges[index] + edges[index + 1]) / 2;
    context.fillStyle = midpoint >= selected[0] && midpoint <= selected[1]
      ? "rgba(8,124,114,.76)" : "rgba(113,132,126,.22)";
    context.fillRect(x0, height - bar, Math.max(1, x1 - x0 - .5), bar);
  }
  if (histogram.overflow) {
    context.fillStyle = "rgba(221,101,72,.72)";
    context.fillRect(width - 3, 0, 3, height);
  }
}

function createRangeControl(key, label, idMin, idMax) {
  const histogram = datasetHeader.filterHistograms[key];
  const exact = histogram.range, display = histogram.displayRange;
  const integer = key === "trial" || key === "step";
  const step = integer ? 1 : Math.max(1e-6, (display[1] - display[0]) / 500);
  const inputStep = integer ? 1 : .1;
  const shownExact = [
    displayRangeBound(exact[0], integer, false),
    displayRangeBound(exact[1], integer, true),
  ];
  const host = document.createElement("section"); host.className = "range-filter";
  host.innerHTML = `
    <div class="range-filter-head"><b>${label}</b><small>${histogram.overflow ? `${formatCount(histogram.overflow)} above mini-chart` : "complete distribution"}</small></div>
    <canvas class="mini-hist" aria-label="${label} distribution"></canvas>
    <div class="dual-range">
      <input class="range-low" type="range" min="${display[0]}" max="${display[1]}" step="${step}" value="${display[0]}" aria-label="${label} lower bound">
      <input class="range-high" type="range" min="${display[0]}" max="${display[1]}" step="${step}" value="${display[1]}" aria-label="${label} upper bound">
    </div>
    <div class="range-values">
      <label>Minimum<input id="${idMin}" type="number" step="${inputStep}" value="${shownExact[0]}"></label>
      <label>Maximum<input id="${idMax}" type="number" step="${inputStep}" value="${shownExact[1]}"></label>
    </div>`;
  byId("range-filters").appendChild(host);
  const canvas = host.querySelector("canvas"), low = host.querySelector(".range-low"), high = host.querySelector(".range-high");
  const loInput = byId(idMin), hiInput = byId(idMax);
  const redraw = () => drawMiniHistogram(canvas, histogram, [Number(loInput.value), Number(hiInput.value)]);
  const fromSlider = changed => {
    if (Number(low.value) > Number(high.value)) {
      if (changed === low) high.value = low.value; else low.value = high.value;
    }
    loInput.value = displayRangeBound(low.value, integer, false);
    hiInput.value = displayRangeBound(high.value, integer, true);
    redraw();
    scheduleCompute("full", 120);
  };
  low.addEventListener("input", () => fromSlider(low));
  high.addEventListener("input", () => fromSlider(high));
  for (const input of [loInput, hiInput]) input.addEventListener("change", () => {
    low.value = Math.max(Number(low.min), Math.min(Number(low.max), Number(loInput.value)));
    high.value = Math.max(Number(high.min), Math.min(Number(high.max), Number(hiInput.value)));
    redraw(); scheduleCompute("full", 100);
  });
  new ResizeObserver(redraw).observe(canvas);
  redraw();
  rangeControls.set(key, {host, redraw, low, high, loInput, hiInput});
}

function loadDisplayNames() {
  displayNames = structuredClone(datasetHeader.displayCategories || datasetHeader.categories || {});
  try {
    const stored = JSON.parse(localStorage.getItem("daari-deepa-labels") || "{}");
    for (const key of ["config", "scene", "vr", "fly", "folder"]) {
      const raw = datasetHeader.categories[key] || [];
      const overrides = stored[key] && typeof stored[key] === "object" ? stored[key] : {};
      displayNames[key] = raw.map((name, index) =>
        overrides[name] || (key === "config" ? stored[name] : null)
        || displayNames[key]?.[index] || name);
    }
  } catch (_) { /* device-local overrides are optional */ }
}

function renderDisplayLabels() {
  const key = byId("label-axis").value;
  const host = byId("panel-labels"); host.replaceChildren();
  const raw = datasetHeader.categories[key] || [];
  raw.forEach((name, index) => {
    const label = document.createElement("label");
    const source = document.createElement("span"); source.textContent = name;
    const input = document.createElement("input"); input.value = displayNames[key]?.[index] || name;
    label.append(source, input); host.appendChild(label);
    input.addEventListener("change", () => {
      displayNames[key][index] = input.value.trim() || name;
      try {
        const stored = JSON.parse(localStorage.getItem("daari-deepa-labels") || "{}");
        stored[key] = stored[key] && typeof stored[key] === "object" ? stored[key] : {};
        stored[key][name] = displayNames[key][index];
        localStorage.setItem("daari-deepa-labels", JSON.stringify(stored));
      } catch (_) { /* local persistence is best-effort */ }
      const option = byId(`filter-${key}`)?.options[index];
      if (option) option.textContent = displayNames[key][index];
      scheduleCompute("full", 20);
    });
  });
}

function populateControls() {
  datasetSummary();
  loadDisplayNames();
  const categories = byId("category-filters");
  categories.replaceChildren();
  const labels = {config: "Treatments", scene: "Scenes", vr: "VR arenas", fly: "Animals", folder: "Source folders"};
  for (const key of ["config", "scene", "vr", "fly", "folder"]) {
    const label = document.createElement("label");
    label.textContent = labels[key];
    const select = document.createElement("select");
    select.multiple = true; select.id = `filter-${key}`; select.dataset.scope = "full";
    for (const [index, text] of datasetHeader.categories[key].entries()) {
      const option = document.createElement("option"); option.value = index;
      option.textContent = displayNames[key]?.[index] || text; select.appendChild(option);
    }
    label.appendChild(select); categories.appendChild(label);
    select.addEventListener("change", () => scheduleCompute("full", 140));
  }
  byId("range-filters").replaceChildren(); rangeControls.clear();
  createRangeControl("trial", "Trial number", "trial-min", "trial-max");
  createRangeControl("step", "Step / segment", "step-min", "step-max");
  createRangeControl("peak", "Peak smoothed velocity", "peak-min", "peak-max");
  createRangeControl("displacement", "Net displacement", "disp-min", "disp-max");
  createRangeControl("distance", "Distance walked", "distance-min", "distance-max");
  const grouped = byId("group-by").value;
  if (["config", "scene", "vr", "fly", "folder"].includes(grouped)) byId("label-axis").value = grouped;
  renderDisplayLabels();
  applyButton.disabled = false; resetViewButton.disabled = false; byId("resample-button").disabled = false;
  byId("export-button").disabled = false;
  const maxTime = Math.max(0, datasetHeader.playbackQuantiles?.p95 ?? datasetHeader.playbackMax ?? 0);
  byId("time-scrubber").max = maxTime; byId("time-scrubber").value = maxTime;
  byId("time-output").textContent = "all time";
  restoreViewStateFromUrl();
  renderRingControls();
  animalVisibility = (datasetHeader.categories.animal || []).map(() => true);
  renderAnimalVisibility();
}

function selectedCodes(key) {
  const element = byId(`filter-${key}`);
  return element ? [...element.selectedOptions].map(option => Number(option.value)) : [];
}

function numberValue(id, fallback = 0) {
  const value = Number(byId(id)?.value);
  return Number.isFinite(value) ? value : fallback;
}

function rangeValue(minId, maxId, fallback) {
  let lo = numberValue(minId, fallback[0]), hi = numberValue(maxId, fallback[1]);
  if (lo > hi) [lo, hi] = [hi, lo];
  return [lo, hi];
}

function collectState() {
  const ranges = datasetHeader.ranges;
  return {
    filters: Object.fromEntries(["config", "scene", "vr", "fly", "folder"].map(key => [key, selectedCodes(key)])),
    ranges: {
      trial: rangeValue("trial-min", "trial-max", ranges.trial),
      step: rangeValue("step-min", "step-max", ranges.step),
      peak: rangeValue("peak-min", "peak-max", ranges.peakSpeed),
      displacement: rangeValue("disp-min", "disp-max", ranges.displacement),
      distance: rangeValue("distance-min", "distance-max", ranges.distance),
    },
    jumpThreshold: numberValue("jump-threshold"),
    jumpBufferMs: numberValue("jump-buffer", 100),
    minDisplacement: numberValue("min-displacement"),
    edgeTrim: numberValue("edge-trim"),
    groupBy: byId("group-by").value,
    panelColumns: numberValue("panel-columns"),
    colorBy: byId("color-by").value,
    pointBudget: numberValue("point-budget", 250000),
    movingOnly: byId("moving-only").checked,
    walkThreshold: numberValue("walk-threshold"),
    binSize: numberValue("bin-size", 0),
    boundPercent: numberValue("bound-percent", 98),
    angleSource: byId("angle-source").value,
    statsUnit: byId("stats-unit").value,
    polarR: [numberValue("polar-r-min", 0), numberValue("polar-r-max", 1)],
    polarValidMin: numberValue("polar-valid-min", 0),
    roiReach: numberValue("roi-reach", 3),
    roiEntered: byId("roi-entered").checked,
    roiTrim: byId("roi-trim").checked,
    ringEnabled: byId("ring-enabled").checked,
    ringContext: byId("ring-context").checked,
    ringMatch: byId("ring-match").value,
    rings: rings.map(ring => ({...ring})),
    labels: displayNames,
    playbackPercentile: byId("playback-cap").value,
    headingMode: byId("heading-mode").value,
    headingBin: numberValue("heading-bin", .25),
    headingSectors: numberValue("heading-sectors", 36),
    lens: currentLens,
    view: currentView,
    panelOrders,
    sampleSeed,
  };
}

function persistState() {
  if (!datasetHeader) return;
  const state = collectState();
  const url = new URL(location.href);
  const params = url.searchParams;
  params.set("source", sourceInput.value);
  params.set("group", state.groupBy); params.set("color", state.colorBy);
  if (state.panelColumns) params.set("cols", state.panelColumns); else params.delete("cols");
  if (state.binSize) params.set("bin", state.binSize); else params.delete("bin");
  params.set("bound", state.boundPercent); params.set("angle", state.angleSource); params.set("unit", state.statsUnit);
  params.set("pcap", state.playbackPercentile);
  params.set("hmode", state.headingMode); params.set("hbin", state.headingBin); params.set("hsec", state.headingSectors);
  params.set("lens", currentLens);
  params.set("view", currentView);
  if (Object.keys(panelOrders).length) params.set("order", JSON.stringify(panelOrders));
  else params.delete("order");
  params.set("filters", JSON.stringify(state.filters));
  params.set("ranges", JSON.stringify(state.ranges));
  params.set("quality", JSON.stringify({
    jumpThreshold: state.jumpThreshold, jumpBufferMs: state.jumpBufferMs,
    minDisplacement: state.minDisplacement, edgeTrim: state.edgeTrim,
    movingOnly: state.movingOnly, walkThreshold: state.walkThreshold,
    roiReach: state.roiReach, roiEntered: state.roiEntered, roiTrim: state.roiTrim,
    polarR: state.polarR, polarValidMin: state.polarValidMin,
  }));
  params.set("tw", byId("trajectory-width").value); params.set("topacity", byId("trajectory-opacity").value);
  params.set("hrange", byId("heat-range-mode").value); params.set("hcmin", byId("heat-cmin").value); params.set("hcmax", byId("heat-cmax").value);
  params.set("fmetric", byId("flow-metric").value); params.set("frange", byId("flow-range-mode").value);
  params.set("fcmin", byId("flow-cmin").value); params.set("fcmax", byId("flow-cmax").value);
  params.set("prate", byId("particle-rate").value); params.set("trail", byId("trail-length").value);
  params.set("fspeed", byId("flow-speed").value); params.set("fvar", byId("flow-variability").value);
  if (state.ringEnabled) params.set("ring", "1"); else params.delete("ring");
  if (state.ringContext) params.set("ringcontext", "1"); else params.set("ringcontext", "0");
  params.set("rings", JSON.stringify(rings)); params.set("ringmatch", state.ringMatch);
  history.replaceState(null, "", url);
}

function restoreViewStateFromUrl() {
  const params = new URLSearchParams(location.search);
  const values = {
    "group-by": params.get("group"), "color-by": params.get("color"),
    "panel-columns": params.get("cols"), "bin-size": params.get("bin"),
    "bound-percent": params.get("bound"), "angle-source": params.get("angle"),
    "stats-unit": params.get("unit"), "playback-cap": params.get("pcap"),
    "heading-mode": params.get("hmode"), "heading-bin": params.get("hbin"),
    "heading-sectors": params.get("hsec"), "trajectory-width": params.get("tw"),
    "trajectory-opacity": params.get("topacity"), "heat-range-mode": params.get("hrange"),
    "heat-cmin": params.get("hcmin"), "heat-cmax": params.get("hcmax"),
    "flow-metric": params.get("fmetric"), "flow-range-mode": params.get("frange"),
    "flow-cmin": params.get("fcmin"), "flow-cmax": params.get("fcmax"),
    "particle-rate": params.get("prate"), "trail-length": params.get("trail"),
    "flow-speed": params.get("fspeed"), "flow-variability": params.get("fvar"),
  };
  for (const [id, value] of Object.entries(values)) if (value !== null && byId(id)) byId(id).value = value;
  setDashboardView(params.get("view") || params.get("lens") || "trajectory", false);
  try {
    const restoredOrder = JSON.parse(params.get("order") || "{}");
    panelOrders = restoredOrder && typeof restoredOrder === "object" ? restoredOrder : {};
  } catch (_) { panelOrders = {}; }
  try {
    const filters = JSON.parse(params.get("filters") || "{}");
    for (const [key, selected] of Object.entries(filters)) {
      const select = byId(`filter-${key}`); if (!select || !Array.isArray(selected)) continue;
      for (const option of select.options) option.selected = selected.map(Number).includes(Number(option.value));
    }
  } catch (_) { /* malformed optional URL state is ignored */ }
  try {
    const ranges = JSON.parse(params.get("ranges") || "{}");
    const ids = {trial:["trial-min","trial-max"], step:["step-min","step-max"], peak:["peak-min","peak-max"], displacement:["disp-min","disp-max"], distance:["distance-min","distance-max"]};
    for (const [key, valuesForRange] of Object.entries(ranges)) {
      if (!ids[key] || !Array.isArray(valuesForRange)) continue;
      const integer = key === "trial" || key === "step";
      byId(ids[key][0]).value = displayRangeBound(valuesForRange[0], integer, false);
      byId(ids[key][1]).value = displayRangeBound(valuesForRange[1], integer, true);
      const control = rangeControls.get(key);
      if (control) { control.low.value = valuesForRange[0]; control.high.value = valuesForRange[1]; control.redraw(); }
    }
  } catch (_) { /* malformed optional URL state is ignored */ }
  try {
    const quality = JSON.parse(params.get("quality") || "{}");
    const numbers = {jumpThreshold:"jump-threshold",jumpBufferMs:"jump-buffer",minDisplacement:"min-displacement",edgeTrim:"edge-trim",walkThreshold:"walk-threshold",roiReach:"roi-reach",polarValidMin:"polar-valid-min"};
    for (const [key, id] of Object.entries(numbers)) if (quality[key] != null) byId(id).value = quality[key];
    if (Array.isArray(quality.polarR)) { byId("polar-r-min").value = quality.polarR[0]; byId("polar-r-max").value = quality.polarR[1]; }
    for (const [key, id] of Object.entries({movingOnly:"moving-only",roiEntered:"roi-entered",roiTrim:"roi-trim"})) if (quality[key] != null) byId(id).checked = !!quality[key];
  } catch (_) { /* malformed optional URL state is ignored */ }
  byId("ring-enabled").checked = params.get("ring") === "1";
  byId("ring-context").checked = params.get("ringcontext") !== "0";
  byId("ring-match").value = params.get("ringmatch") || "any";
  try {
    const restored = JSON.parse(params.get("rings") || "null");
    if (Array.isArray(restored) && restored.length) rings = restored.map(ring => ({x:Number(ring.x)||0,z:Number(ring.z)||0,r:Math.max(.01,Number(ring.r)||3)}));
  } catch (_) { /* retain safe default */ }
}

const scopeProducts = {
  layout: [], trajectory: ["trajectory"], playback: ["heading"], heading: ["heading"],
  polar: ["polar"], spatial: ["heatmap", "direction"],
  color: ["trajectory", "polar", "heading"], sample: ["trajectory", "polar", "heading"],
  statistics: ["polar", "metrics", "roi"],
  movement: ["trajectory", "direction", "polar", "heading", "roi"],
};

function mergeScopes(first, second) {
  if (!first || first === second) return second || first || null;
  if (first === "full" || second === "full") return "full";
  const needed = new Set([...(scopeProducts[first] || []), ...(scopeProducts[second] || [])]);
  const candidates = Object.entries(scopeProducts)
    .filter(([, productsForScope]) => [...needed].every(product => productsForScope.includes(product)))
    .sort((a, b) => a[1].length - b[1].length);
  return candidates[0]?.[0] || "full";
}

let computeTimer = null;
let scheduledScope = null;
let scheduledExtra = {};
function scheduleCompute(scope = "full", delay = 0, extra = {}) {
  if (!workerReady || !datasetHeader) return;
  clearTimeout(computeTimer);
  scheduledScope = mergeScopes(mergeScopes(pendingCompute?.scope, scheduledScope), scope);
  scheduledExtra = {...scheduledExtra, ...extra};
  computeTimer = setTimeout(() => {
    const chosenScope = scheduledScope || "full";
    const chosenExtra = scheduledExtra;
    scheduledScope = null; scheduledExtra = {};
    queueCompute(chosenScope, chosenExtra);
  }, delay);
}

function queueCompute(scope, extra = {}) {
  const requestId = ++latestRequest;
  pendingCompute = {type: "compute", requestId, state: collectState(), scope, ...extra};
  persistState();
  setStatus("working", scope === "full" ? "Updating analysis" : "Refreshing view", "The retained table is running in a browser worker; the page remains interactive.");
  flushCompute();
}

function flushCompute() {
  if (!workerReady || workerBusy || !pendingCompute) return;
  const message = pendingCompute; pendingCompute = null; workerBusy = true;
  worker.postMessage(message);
}

function updateColumns() {
  const columns = numberValue("panel-columns");
  for (const value of Object.values(products)) if (value && typeof value === "object" && "columns" in value) value.columns = columns;
  for (const renderer of [...spatialRenderers, polarRenderer, headingRenderer]) renderer.setColumns?.(columns);
  persistState();
}

function optionalNumber(id) {
  const text = byId(id)?.value;
  if (text === "" || text == null) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

function applyHeatmapVisuals() {
  if (!heatmapRenderer.data) return;
  heatmapRenderer.setMetricScale(
    byId("heat-metric").value, byId("heat-scale").value,
    byId("heat-range-mode").value,
    optionalNumber("heat-cmin"), optionalNumber("heat-cmax"),
  );
}

function applyDirectionVisuals() {
  if (!directionRenderer.data) return;
  directionRenderer.setVisualOptions({
    metric: byId("flow-metric").value,
    clipMode: byId("flow-range-mode").value,
    clipMin: optionalNumber("flow-cmin"), clipMax: optionalNumber("flow-cmax"),
    particleRate: numberValue("particle-rate", 1),
    trailLength: numberValue("trail-length", 1),
    speed: numberValue("flow-speed", 1),
    variability: numberValue("flow-variability", 1),
  });
}

function populatePlaybackSegments() {
  const select = byId("playback-trial");
  const previous = Number(select.value);
  select.replaceChildren();
  for (const item of visibleSegmentOptions) {
    select.add(new Option(`${item.label} · ${formatNumber(item.duration, 1)} s`, String(item.code)));
  }
  const next = visibleSegmentOptions.some(item => item.code === previous)
    ? previous : visibleSegmentOptions[0]?.code;
  if (next != null) select.value = String(next);
  updatePlaybackScope();
}

function selectedSegmentOption() {
  const code = Number(byId("playback-trial").value);
  return visibleSegmentOptions.find(item => item.code === code) || null;
}

function updatePlaybackLimit(summary = currentDurationSummary) {
  if (summary) currentDurationSummary = summary;
  if (!datasetHeader) return;
  const single = byId("playback-scope").value === "single";
  const selected = selectedSegmentOption();
  const key = byId("playback-cap").value === "99" ? "p99"
    : (byId("playback-cap").value === "max" ? "max" : "p95");
  const maximum = Math.max(.001, single && selected
    ? Number(selected.duration) || .001
    : Number(currentDurationSummary?.[key] ?? datasetHeader.playbackQuantiles?.[key] ?? datasetHeader.playbackMax) || .001);
  const scrubber = byId("time-scrubber");
  scrubber.max = maximum;
  if (Number(scrubber.value) > maximum) scrubber.value = maximum;
  byId("time-output").title = single && selected
    ? `Exact duration of ${selected.label}`
    : `${key.toUpperCase()} of visible segment durations`;
}

function updatePlaybackScope() {
  const single = byId("playback-scope").value === "single";
  const select = byId("playback-trial");
  select.disabled = !single || !visibleSegmentOptions.length;
  byId("trial-prev").disabled = !single || visibleSegmentOptions.length < 2;
  byId("trial-next").disabled = !single || visibleSegmentOptions.length < 2;
  const selected = single ? Number(select.value) : -1;
  trajectoryRenderer.setPlaybackSegment(single && Number.isFinite(selected) ? selected : -1);
  updatePlaybackLimit();
  updatePlaybackTime();
}

function stepPlaybackSegment(delta) {
  if (!visibleSegmentOptions.length) return;
  const current = visibleSegmentOptions.findIndex(item => item.code === Number(byId("playback-trial").value));
  const next = (Math.max(0, current) + delta + visibleSegmentOptions.length) % visibleSegmentOptions.length;
  byId("playback-trial").value = String(visibleSegmentOptions[next].code);
  byId("time-scrubber").value = 0;
  updatePlaybackScope();
}

function renderProducts(incoming, summary) {
  lastSummary = summary;
  Object.assign(products, incoming);
  datasetSummary(summary);
  if (summary.segmentOptions) {
    visibleSegmentOptions = summary.segmentOptions;
    populatePlaybackSegments();
  }
  updatePlaybackLimit(summary.durationSummary);
  if (summary.panelKeys) renderPanelOrder(summary.panelKeys);
  const preserve = !newDataset && !!sharedView;
  if (incoming.trajectory) {
    incoming.trajectory.columns = numberValue("panel-columns");
    trajectoryRenderer.setData(incoming.trajectory, preserve);
    trajectoryRenderer.setLineStyle(numberValue("trajectory-width", 2.2), numberValue("trajectory-opacity", .28));
    updatePlaybackScope();
    if (!preserve) sharedView = {...trajectoryRenderer.view};
    syncView(sharedView, trajectoryRenderer);
    updateTrajectorySummary(applyLocalRingObserver(false));
  }
  if (incoming.heatmap) {
    incoming.heatmap.columns = numberValue("panel-columns");
    heatmapRenderer.setData(incoming.heatmap, !!sharedView);
    applyHeatmapVisuals();
    if (sharedView) heatmapRenderer.setView(sharedView, false);
  }
  if (incoming.direction) {
    incoming.direction.columns = numberValue("panel-columns");
    directionRenderer.setData(incoming.direction, !!sharedView);
    applyDirectionVisuals();
    if (sharedView) directionRenderer.setView(sharedView, false);
  }
  if (incoming.polar) {
    incoming.polar.columns = numberValue("panel-columns"); polarRenderer.setData(incoming.polar);
    byId("polar-summary").textContent = `${formatCount(incoming.polar.units)} ${byId("stats-unit").value === "animal" ? "animal" : "trial"} resultants retained by the quality gates`;
  }
  if (incoming.heading) { incoming.heading.columns = numberValue("panel-columns"); headingRenderer.setData(incoming.heading); }
  if (incoming.metrics) metricsRenderer.setData(incoming.metrics);
  if (incoming.roi) {
    roiRenderer.setData(incoming.roi);
    byId("roi-summary").textContent = `${formatCount(incoming.roi.baseSegments)} quality-filtered segments contribute to fraction and residence denominators.`;
  }
  if (incoming.diagnostics) {
    velocityHistogram.setData(incoming.diagnostics.velocity);
    displacementHistogram.setData(incoming.diagnostics.displacement);
  }
  const fraction = numberValue("trial-fraction", 100) / 100;
  trajectoryRenderer.setFraction(fraction); polarRenderer.setFraction(fraction); headingRenderer.setFraction(fraction);
  applyAnimalVisibility();
  for (const renderer of spatialRenderers) renderer.setRoisVisible(byId("roi-show").checked);
  for (const renderer of spatialRenderers) renderer.setGridVisible(byId("spatial-grid").checked);
  newDataset = false;
}

function handleWorkerMessage(event) {
  const message = event.data;
  if (message.type === "ready") {
    workerReady = true;
    setStatus("working", "Preparing native views", "Filtering, spatial bins, circular summaries, and exact metrics are running off the main thread.");
    queueCompute("full");
    return;
  }
  if (message.type === "result") {
    workerBusy = false;
    if (message.requestId === latestRequest) {
      displayedRequest = message.requestId;
      renderProducts(message.products, message.summary);
      setStatus("ready", "Ready for exploration", `${formatCount(message.summary.visibleRows)} points · ${formatCount(message.summary.visibleSegments)} segments · worker filter ${message.summary.filterMs.toFixed(0)} ms`);
    }
    flushCompute();
    return;
  }
  if (message.type === "inspect-result") {
    const match = message.match;
    byId("segment-inspector").textContent = match
      ? `${match.sourceFile} · trial ${formatNumber(match.trial, 0)} / step ${formatNumber(match.step, 0)} · ${match.config} · ${match.fly}@${match.vr} · path ${formatNumber(match.distance, 1)}, displacement ${formatNumber(match.displacement, 1)}, peak ${formatNumber(match.peakSpeed, 1)}, median ${formatNumber(match.medianSpeed, 1)}, tortuosity ${formatNumber(match.tortuosity, 1)}`
      : "No retained path was close enough; click nearer a visible line.";
    return;
  }
  if (message.type === "error") {
    workerBusy = false;
    setStatus("error", "Browser analysis failed", String(message.error).split("\n")[0]);
    console.error(message.error);
    flushCompute();
  }
}

async function loadDataset(source) {
  source = String(source || "").trim();
  if (!source) { setStatus("error", "A data source is required", "Enter a CSV, folder, or recursive glob."); return; }
  stopPlayback();
  setStatus("working", "Loading and preprocessing", "Python is applying the trusted loader once, then packaging typed browser columns.");
  byId("load-button").disabled = true; applyButton.disabled = true;
  try {
    const response = await fetch("/api/load", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source})});
    if (!response.ok) {
      const error = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
      throw new Error(error.error || `Load failed with HTTP ${response.status}`);
    }
    const buffer = await response.arrayBuffer();
    const parsed = parseBinary(buffer);
    datasetHeader = parsed.header; sourceInput.value = source;
    byId("source-popover").open = false;
    visibleSegmentOptions = []; currentDurationSummary = datasetHeader.playbackQuantiles || null;
    populateControls(); products = {}; sharedView = null; newDataset = true; sampleSeed = 0;
    if (worker) worker.terminate();
    workerReady = false; workerBusy = false; pendingCompute = null;
    worker = new Worker("/static/worker.js"); worker.onmessage = handleWorkerMessage;
    worker.onerror = event => setStatus("error", "Worker crashed", event.message);
    worker.postMessage({type: "init", header: datasetHeader, buffer, bodyOffset: parsed.bodyOffset}, [buffer]);
    setStatus("working", "Data loaded", `${formatCount(datasetHeader.counts.retainedRows)} retained rows from ${formatCount(datasetHeader.counts.files)} files transferred once${datasetHeader.counts.duplicateFilesSkipped ? `; ${formatCount(datasetHeader.counts.duplicateFilesSkipped)} duplicate copies skipped` : ""}. Building local views.`);
  } catch (error) {
    setStatus("error", "Could not load data", error.message);
  } finally {
    byId("load-button").disabled = false;
    applyButton.disabled = !datasetHeader;
  }
}

function compositePlot(hostId) {
  const canvases = [...byId(hostId).querySelectorAll("canvas")];
  if (!canvases.length || !canvases[0].width) return null;
  const output = document.createElement("canvas");
  output.width = canvases[0].width; output.height = canvases[0].height;
  const context = output.getContext("2d");
  for (const canvas of canvases) context.drawImage(canvas, 0, 0, output.width, output.height);
  return output.toDataURL("image/png");
}

function setDashboardView(view, save = true) {
  const allowed = new Set([
    "trajectory", "occupancy", "direction", "polar", "compare",
    "roi", "heading", "metrics", "diagnostics",
  ]);
  currentView = allowed.has(view) ? view : "trajectory";
  const spatial = new Set(["trajectory", "occupancy", "direction", "polar", "compare"]);
  if (spatial.has(currentView)) currentLens = currentView;
  byId("explore-section").dataset.lens = currentLens;
  for (const button of document.querySelectorAll("[data-view-button]")) {
    button.classList.toggle("active", button.dataset.viewButton === currentView);
  }
  const activeSection = spatial.has(currentView) ? "explore-section" : `${currentView}-section`;
  for (const section of document.querySelectorAll(".plot-section")) {
    section.classList.toggle("active-section", section.id === activeSection);
  }
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const shown = currentView === "compare"
      ? new Set(["trajectory", "occupancy", "direction", "polar"])
      : new Set([currentView]);
    if (shown.has("trajectory")) { trajectoryRenderer.resize(); trajectoryRenderer.draw(); }
    if (shown.has("occupancy")) { heatmapRenderer.resize(); heatmapRenderer.draw(); }
    if (shown.has("direction")) { directionRenderer.resize(); directionRenderer.draw(); }
    if (shown.has("polar")) { polarRenderer.chart.resize({animation: {duration: 0}}); polarRenderer.draw(); }
    if (shown.has("heading")) { headingRenderer.chart.resize({animation: {duration: 0}}); headingRenderer.draw(); }
    if (shown.has("metrics")) { metricsRenderer.chart.resize({animation: {duration: 0}}); metricsRenderer.draw(); }
    if (shown.has("roi")) { roiRenderer.chart.resize({animation: {duration: 0}}); roiRenderer.draw(); }
    if (shown.has("diagnostics")) {
      velocityHistogram.chart.resize({animation: {duration: 0}}); velocityHistogram.draw();
      displacementHistogram.chart.resize({animation: {duration: 0}}); displacementHistogram.draw();
    }
    applyAnimalVisibility();
  }));
  if (save) persistState();
}

function setSpatialLens(lens, save = true) { setDashboardView(lens, save); }

function downloadActivePlot() {
  const lens = currentView === "compare" ? "trajectory" : currentView;
  const host = {
    trajectory: "trajectory-plot", occupancy: "heatmap-plot",
    direction: "direction-plot", polar: "polar-plot", roi: "roi-plot",
    heading: "heading-plot", metrics: "metrics-plot",
    diagnostics: "velocity-hist",
  }[lens];
  const url = compositePlot(host);
  if (!url) return;
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `daari-deepa-${lens}-${new Date().toISOString().slice(0, 10)}.png`;
  anchor.click();
}

const recipeVisualIds = [
  "trajectory-width", "trajectory-opacity", "heat-metric", "heat-scale",
  "heat-range-mode", "heat-cmin", "heat-cmax", "flow-metric",
  "flow-range-mode", "flow-cmin", "flow-cmax", "particle-rate",
  "trail-length", "flow-speed", "flow-variability", "trial-fraction",
];

function currentRecipe() {
  const state = collectState();
  return {
    schema: "daari-deepa-view/v1",
    source: sourceInput.value,
    filtersByLabel: Object.fromEntries(Object.entries(state.filters).map(([key, codes]) => [
      key, codes.map(code => datasetHeader?.categories?.[key]?.[code]).filter(Boolean),
    ])),
    state,
    visuals: Object.fromEntries(recipeVisualIds.map(id => [id, byId(id).value])),
  };
}

function captureRecipe() {
  if (!datasetHeader) return null;
  const recipe = currentRecipe();
  byId("recipe-json").value = JSON.stringify(recipe, null, 2);
  return recipe;
}

function applyRecipeControls(recipe) {
  const state = recipe?.state || {};
  const filterCodes = state.filters || {};
  for (const key of ["config", "scene", "vr", "fly", "folder"]) {
    const select = byId(`filter-${key}`); if (!select) continue;
    let selected = Array.isArray(filterCodes[key]) ? filterCodes[key].map(Number) : [];
    const labels = recipe.filtersByLabel?.[key];
    if (Array.isArray(labels)) selected = labels.map(label => datasetHeader.categories[key].indexOf(label)).filter(code => code >= 0);
    for (const option of select.options) option.selected = selected.includes(Number(option.value));
  }
  const rangeIds = {trial:["trial-min","trial-max"], step:["step-min","step-max"], peak:["peak-min","peak-max"], displacement:["disp-min","disp-max"], distance:["distance-min","distance-max"]};
  for (const [key, ids] of Object.entries(rangeIds)) {
    const values = state.ranges?.[key]; if (!Array.isArray(values)) continue;
    const integer = key === "trial" || key === "step";
    byId(ids[0]).value = displayRangeBound(values[0], integer, false);
    byId(ids[1]).value = displayRangeBound(values[1], integer, true);
    const control = rangeControls.get(key);
    if (control) { control.low.value = values[0]; control.high.value = values[1]; control.redraw(); }
  }
  const valueIds = {
    jumpThreshold:"jump-threshold", jumpBufferMs:"jump-buffer",
    minDisplacement:"min-displacement", edgeTrim:"edge-trim", groupBy:"group-by",
    panelColumns:"panel-columns", colorBy:"color-by", pointBudget:"point-budget",
    walkThreshold:"walk-threshold", binSize:"bin-size", boundPercent:"bound-percent",
    angleSource:"angle-source", statsUnit:"stats-unit", polarValidMin:"polar-valid-min",
    roiReach:"roi-reach", ringMatch:"ring-match", playbackPercentile:"playback-cap",
    headingMode:"heading-mode", headingBin:"heading-bin", headingSectors:"heading-sectors",
  };
  for (const [key, id] of Object.entries(valueIds)) if (state[key] != null) byId(id).value = state[key];
  for (const [key, id] of Object.entries({movingOnly:"moving-only",roiEntered:"roi-entered",roiTrim:"roi-trim",ringEnabled:"ring-enabled",ringContext:"ring-context"})) {
    if (state[key] != null) byId(id).checked = !!state[key];
  }
  if (Array.isArray(state.polarR)) { byId("polar-r-min").value = state.polarR[0]; byId("polar-r-max").value = state.polarR[1]; }
  if (state.labels && typeof state.labels === "object") {
    displayNames = structuredClone(state.labels);
    for (const key of ["config", "scene", "vr", "fly", "folder"]) {
      const select = byId(`filter-${key}`);
      if (select) for (let index = 0; index < select.options.length; index += 1) {
        select.options[index].textContent = displayNames[key]?.[index] || datasetHeader.categories[key][index];
      }
    }
  }
  panelOrders = state.panelOrders && typeof state.panelOrders === "object" ? structuredClone(state.panelOrders) : panelOrders;
  if (Array.isArray(state.rings) && state.rings.length) rings = state.rings.map(ring => ({x:Number(ring.x)||0,z:Number(ring.z)||0,r:Math.max(.01,Number(ring.r)||.01)}));
  sampleSeed = Number(state.sampleSeed) || 0;
  for (const [id, value] of Object.entries(recipe.visuals || {})) if (byId(id) && value != null) byId(id).value = value;
  setDashboardView(state.view || state.lens || "trajectory", false);
  renderRingControls(); renderDisplayLabels(); renderPanelOrder(panelOrders[byId("group-by").value] || []);
  trajectoryRenderer.setLineStyle(numberValue("trajectory-width", 2.2), numberValue("trajectory-opacity", .28));
  applyHeatmapVisuals(); applyDirectionVisuals(); updateFraction(); updatePlaybackLimit(); applyLocalRingObserver(false);
}

async function applyRecipe() {
  try {
    const recipe = JSON.parse(byId("recipe-json").value);
    if (!recipe || recipe.schema !== "daari-deepa-view/v1") throw new Error("Expected a daari-deepa-view/v1 recipe.");
    const requestedSource = String(recipe.source || "").trim();
    if (requestedSource && requestedSource !== sourceInput.value) await loadDataset(requestedSource);
    applyRecipeControls(recipe);
    if (workerReady) scheduleCompute("full", 0);
    setStatus("ready", "View recipe applied", "The source, subsets, ordering, labels, analysis gates, and visual settings were restored.");
  } catch (error) {
    setStatus("error", "Could not apply recipe", error.message);
  }
}

async function copyRecipe() {
  if (!byId("recipe-json").value.trim()) captureRecipe();
  await navigator.clipboard.writeText(byId("recipe-json").value);
}

function downloadRecipe() {
  if (!byId("recipe-json").value.trim()) captureRecipe();
  const blob = new Blob([byId("recipe-json").value], {type: "application/json"});
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = `daari-deepa-view-${new Date().toISOString().slice(0, 10)}.json`;
  anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>\"]/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[character]));
}

function exportNativeReport() {
  if (!datasetHeader) return;
  const sections = [
    ["Trajectory field", "trajectory-plot"], ["Occupancy", "heatmap-plot"],
    ["Local direction", "direction-plot"], ["Polar direction", "polar-plot"],
    ["ROI outcomes", "roi-plot"], ["Heading over time", "heading-plot"],
    ["Trial metrics", "metrics-plot"], ["Velocity", "velocity-hist"],
    ["Displacement", "displacement-hist"],
  ];
  const figures = sections.map(([title, id]) => [title, compositePlot(id)]).filter(([, image]) => image);
  const counts = datasetHeader.counts;
  const html = `<!doctype html><meta charset="utf-8"><title>Daari Deepa native report</title>
    <style>body{margin:32px auto;max-width:1500px;padding:0 24px;color:#18221f;background:#f4f1ea;font:14px system-ui}h1,h2{font-family:Georgia,serif;font-weight:500}header{border-bottom:1px solid #ccc;padding-bottom:16px}section{margin:28px 0;padding:14px;background:#fffdf8;border:1px solid #d9d5ca;border-radius:12px}img{display:block;width:100%;height:auto}small{color:#66716d}</style>
    <header><h1>Daari Deepa — native analysis report</h1><p>${escapeHtml(sourceInput.value)}</p><small>${formatCount(counts.retainedRows)} retained of ${formatCount(counts.sourceRows)} source rows · ${formatCount(counts.segments)} segments · ${formatCount(counts.animals)} animals</small></header>
    ${figures.map(([title, image]) => `<section><h2>${escapeHtml(title)}</h2><img src="${image}" alt="${escapeHtml(title)}"></section>`).join("")}
    <footer><small>Exported ${escapeHtml(new Date().toISOString())}. Figures are a static record of the current native browser state.</small></footer>`;
  const blob = new Blob([html], {type: "text/html"});
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = `dari-deepa-native-${new Date().toISOString().slice(0,10)}.html`;
  anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function updateFraction() {
  const value = numberValue("trial-fraction", 100);
  byId("fraction-output").textContent = `${value}%`;
  trajectoryRenderer.setFraction(value / 100); polarRenderer.setFraction(value / 100); headingRenderer.setFraction(value / 100);
}

function renderRingControls() {
  activeRing = Math.max(0, Math.min(rings.length - 1, activeRing));
  const select = byId("ring-active"); select.replaceChildren();
  rings.forEach((_, index) => select.add(new Option(`Ring ${index + 1}`, String(index))));
  select.value = String(activeRing);
  updateRingControlValues();
  byId("ring-delete").disabled = rings.length <= 1;
}

function updateRingControlValues() {
  const ring = rings[activeRing] || {x: 0, z: 0, r: 3};
  byId("ring-x").value = Number(ring.x.toFixed(1));
  byId("ring-z").value = Number(ring.z.toFixed(1));
  byId("ring-radius").value = Number(ring.r.toFixed(1));
  const slider = byId("ring-radius-slider");
  slider.max = Math.max(30, ring.r * 2,
    Math.abs(datasetHeader?.ranges?.x?.[1] || 0), Math.abs(datasetHeader?.ranges?.z?.[1] || 0));
  slider.value = ring.r;
}

function renderPanelOrder(codes = lastSummary?.panelKeys || []) {
  const host = byId("panel-order");
  host.replaceChildren();
  const key = byId("group-by").value;
  if (key === "all" || !codes.length) {
    const empty = document.createElement("span"); empty.className = "empty-control";
    empty.textContent = key === "all" ? "All data is pooled into one panel." : "No visible panels.";
    host.appendChild(empty); return;
  }
  const names = displayNames[key] || datasetHeader?.categories?.[key] || [];
  const commit = next => {
    panelOrders = {...panelOrders, [key]: next};
    renderPanelOrder(next);
    scheduleCompute("full", 30);
  };
  codes.map(Number).forEach((code, index) => {
    const item = document.createElement("div"); item.className = "panel-order-item";
    item.draggable = true; item.dataset.code = code;
    const grip = document.createElement("span"); grip.className = "grip"; grip.textContent = "⋮⋮";
    const text = document.createElement("span"); text.textContent = names[code] || `Panel ${code + 1}`;
    const up = document.createElement("button"); up.type = "button"; up.textContent = "↑"; up.disabled = index === 0;
    const down = document.createElement("button"); down.type = "button"; down.textContent = "↓"; down.disabled = index === codes.length - 1;
    up.addEventListener("click", () => { const next = codes.map(Number); [next[index - 1], next[index]] = [next[index], next[index - 1]]; commit(next); });
    down.addEventListener("click", () => { const next = codes.map(Number); [next[index + 1], next[index]] = [next[index], next[index + 1]]; commit(next); });
    item.addEventListener("dragstart", event => { item.classList.add("dragging"); event.dataTransfer.setData("text/plain", String(code)); });
    item.addEventListener("dragend", () => item.classList.remove("dragging"));
    item.addEventListener("dragover", event => event.preventDefault());
    item.addEventListener("drop", event => {
      event.preventDefault();
      const moved = Number(event.dataTransfer.getData("text/plain"));
      const next = codes.map(Number).filter(value => value !== moved);
      next.splice(next.indexOf(code), 0, moved); commit(next);
    });
    item.append(grip, text, up, down); host.appendChild(item);
  });
}

function updateTrajectorySummary(ringStats = null) {
  const trajectory = products.trajectory;
  if (!trajectory || !lastSummary) return;
  const ringText = byId("ring-enabled").checked
    ? ` · ${formatCount(ringStats?.matches ?? trajectory.ringMatches)} ring matches · local ${formatNumber(ringStats?.buildMs ?? 0, 1)} ms`
    : "";
  byId("trajectory-summary").textContent = `${formatCount(lastSummary.visibleSegments)} segments · ${formatCount(lastSummary.visibleRows)} retained points · ${formatCount(trajectory.links)} GPU line segments${ringText}`;
}

function applyLocalRingObserver(save = false) {
  const stats = trajectoryRenderer.setRingObserver(
    byId("ring-enabled").checked, rings, byId("ring-match").value,
    byId("ring-context").checked,
  );
  updateTrajectorySummary(stats);
  if (save) persistState();
  return stats;
}

function scheduleLocalRingObserver(final = false) {
  if (final && ringFrame) cancelAnimationFrame(ringFrame);
  if (final) {
    ringFrame = null;
    applyLocalRingObserver(true);
    return;
  }
  if (ringFrame) return;
  ringFrame = requestAnimationFrame(() => {
    ringFrame = null;
    applyLocalRingObserver(false);
  });
}

function updateActiveRing() {
  rings[activeRing] = {
    x: numberValue("ring-x"), z: numberValue("ring-z"),
    r: Math.max(.01, numberValue("ring-radius", 3)),
  };
  byId("ring-radius-slider").value = rings[activeRing].r;
  scheduleLocalRingObserver(false);
}

function updatePlaybackTime() {
  const enabled = byId("playback-enabled").checked;
  const value = numberValue("time-scrubber", 0);
  const single = byId("playback-scope").value === "single";
  trajectoryRenderer.setPlaybackSegment(single ? Number(byId("playback-trial").value) : -1);
  trajectoryRenderer.setTime(enabled ? value : Number.POSITIVE_INFINITY);
  byId("time-output").textContent = enabled
    ? `${formatNumber(value, 1)} / ${formatNumber(Number(byId("time-scrubber").max), 1)} s`
    : (single ? "full segment" : "all time");
}

function playbackTick(now) {
  if (!byId("playback-enabled").checked || byId("play-button").textContent !== "Pause") { playbackFrame = null; return; }
  const maxTime = Number(byId("time-scrubber").max) || 1;
  const elapsed = playbackLast ? (now - playbackLast) / 1000 : 0; playbackLast = now;
  let value = numberValue("time-scrubber") + elapsed * numberValue("playback-speed", 1);
  if (value >= maxTime) value = 0;
  byId("time-scrubber").value = value; updatePlaybackTime();
  playbackFrame = requestAnimationFrame(playbackTick);
}

function stopPlayback() {
  if (playbackFrame) cancelAnimationFrame(playbackFrame);
  playbackFrame = null; playbackLast = 0;
  if (byId("play-button")) byId("play-button").textContent = "Play";
}

byId("source-form").addEventListener("submit", event => { event.preventDefault(); loadDataset(sourceInput.value); });
byId("controls-toggle").addEventListener("click", () => {
  const collapsed = shell.classList.toggle("controls-collapsed");
  byId("controls-toggle").setAttribute("aria-expanded", String(!collapsed));
  setTimeout(() => {
    for (const renderer of spatialRenderers) { renderer.resize(); renderer.draw(); }
    for (const renderer of [polarRenderer, headingRenderer, metricsRenderer, roiRenderer, velocityHistogram, displacementHistogram]) {
      renderer.chart.resize({animation: {duration: 0}});
      renderer.draw();
    }
  }, 220);
});
applyButton.addEventListener("click", () => scheduleCompute("full"));
resetViewButton.addEventListener("click", () => trajectoryRenderer.resetView(true));
function setCleanMode(enabled) {
  shell.classList.toggle("clean-mode", enabled);
  byId("clean-button").textContent = shell.classList.contains("clean-mode") ? "Full view" : "Clean view";
  for (const renderer of spatialRenderers) renderer.setCleanMode(enabled);
  setTimeout(() => spatialRenderers.forEach(renderer => { renderer.resize(); renderer.draw(); }), 40);
}
byId("clean-button").addEventListener("click", () => setCleanMode(!shell.classList.contains("clean-mode")));
byId("clean-exit").addEventListener("click", () => setCleanMode(false));
document.addEventListener("keydown", event => { if (event.key === "Escape" && shell.classList.contains("clean-mode")) setCleanMode(false); });
byId("export-button").addEventListener("click", exportNativeReport);
byId("download-plot-button").addEventListener("click", downloadActivePlot);
byId("recipe-capture").addEventListener("click", captureRecipe);
byId("recipe-apply").addEventListener("click", applyRecipe);
byId("recipe-copy").addEventListener("click", () => copyRecipe().catch(error => setStatus("error", "Could not copy recipe", error.message)));
byId("recipe-download").addEventListener("click", downloadRecipe);
byId("label-axis").addEventListener("change", renderDisplayLabels);
byId("group-by").addEventListener("change", () => {
  const grouped = byId("group-by").value;
  if (["config", "scene", "vr", "fly", "folder"].includes(grouped)) {
    byId("label-axis").value = grouped;
    renderDisplayLabels();
  }
  renderPanelOrder([]);
});
for (const button of document.querySelectorAll("[data-view-button]")) {
  button.addEventListener("click", () => setDashboardView(button.dataset.viewButton));
}
byId("animals-all").addEventListener("click", () => {
  animalVisibility.fill(true); renderAnimalVisibility(); applyAnimalVisibility();
});
byId("animals-none").addEventListener("click", () => {
  animalVisibility.fill(false); renderAnimalVisibility(); applyAnimalVisibility();
});
byId("trial-fraction").addEventListener("input", updateFraction);
byId("resample-button").addEventListener("click", () => { sampleSeed += 1; scheduleCompute("sample"); });
byId("roi-show").addEventListener("change", () => {
  for (const renderer of spatialRenderers) renderer.setRoisVisible(byId("roi-show").checked);
});
byId("ring-enabled").addEventListener("change", () => applyLocalRingObserver(true));
byId("ring-context").addEventListener("change", () => applyLocalRingObserver(true));
byId("ring-match").addEventListener("change", () => applyLocalRingObserver(true));
byId("ring-active").addEventListener("change", () => { activeRing = Number(byId("ring-active").value) || 0; renderRingControls(); });
for (const id of ["ring-x", "ring-z", "ring-radius"]) {
  byId(id).addEventListener("input", updateActiveRing);
  byId(id).addEventListener("change", () => applyLocalRingObserver(true));
}
byId("ring-radius-slider").addEventListener("input", () => {
  byId("ring-radius").value = byId("ring-radius-slider").value;
  updateActiveRing();
});
byId("ring-radius-slider").addEventListener("change", () => applyLocalRingObserver(true));
byId("ring-add").addEventListener("click", () => {
  const current = rings[activeRing] || {x:0,z:0,r:3}; rings.push({...current, x: current.x + current.r * .5}); activeRing = rings.length - 1;
  renderRingControls(); applyLocalRingObserver(true);
});
byId("ring-delete").addEventListener("click", () => {
  if (rings.length <= 1) return; rings.splice(activeRing, 1); activeRing = Math.max(0, activeRing - 1);
  renderRingControls(); applyLocalRingObserver(true);
});
for (const id of ["heat-metric", "heat-scale", "heat-range-mode", "heat-cmin", "heat-cmax"]) {
  byId(id).addEventListener(id.startsWith("heat-c") ? "input" : "change", () => {
    applyHeatmapVisuals(); persistState();
  });
}
for (const id of ["flow-metric", "flow-range-mode", "flow-cmin", "flow-cmax",
  "particle-rate", "trail-length", "flow-speed", "flow-variability"]) {
  const event = id === "flow-metric" || id === "flow-range-mode" ? "change" : "input";
  byId(id).addEventListener(event, () => { applyDirectionVisuals(); persistState(); });
}
for (const id of ["trajectory-width", "trajectory-opacity"]) {
  byId(id).addEventListener("input", () => {
    trajectoryRenderer.setLineStyle(numberValue("trajectory-width", 2.2), numberValue("trajectory-opacity", .28));
    persistState();
  });
}
byId("spatial-grid").addEventListener("change", () => {
  for (const renderer of spatialRenderers) renderer.setGridVisible(byId("spatial-grid").checked);
});
byId("playback-enabled").addEventListener("change", () => {
  const enabled = byId("playback-enabled").checked;
  byId("play-button").disabled = !enabled; byId("time-scrubber").disabled = !enabled;
  if (enabled) byId("time-scrubber").value = 0; else stopPlayback();
  updatePlaybackTime();
});
byId("time-scrubber").addEventListener("input", updatePlaybackTime);
byId("playback-scope").addEventListener("change", () => {
  byId("time-scrubber").value = 0; updatePlaybackScope();
});
byId("playback-trial").addEventListener("change", () => {
  byId("time-scrubber").value = 0; updatePlaybackScope();
});
byId("trial-prev").addEventListener("click", () => stepPlaybackSegment(-1));
byId("trial-next").addEventListener("click", () => stepPlaybackSegment(1));
byId("playback-cap").addEventListener("change", () => {
  updatePlaybackLimit(); scheduleCompute("playback", 20);
});
byId("play-button").addEventListener("click", () => {
  const playing = byId("play-button").textContent === "Pause";
  if (playing) stopPlayback();
  else { byId("play-button").textContent = "Pause"; playbackLast = 0; playbackFrame = requestAnimationFrame(playbackTick); }
});

for (const control of document.querySelectorAll("[data-scope]")) {
  control.addEventListener("change", () => {
    const scope = control.dataset.scope;
    if (scope === "layout") updateColumns();
    else scheduleCompute(scope, scope === "full" ? 180 : 80);
  });
}

async function readDroppedEntry(entry, prefix, paths) {
  if (entry.isFile) {
    if (entry.name.toLowerCase().endsWith(".csv")) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      paths.push({path: `${prefix}${entry.name}`, size: file.size});
    }
    return;
  }
  if (!entry.isDirectory) return;
  const reader = entry.createReader();
  while (true) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    for (const child of batch) await readDroppedEntry(child, `${prefix}${entry.name}/`, paths);
  }
}

async function droppedPaths(transfer) {
  const paths = [];
  const entries = [...(transfer.items || [])].map(item => item.webkitGetAsEntry?.()).filter(Boolean);
  if (entries.length) {
    for (const entry of entries) await readDroppedEntry(entry, "", paths);
  } else {
    for (const file of transfer.files || []) {
      const path = file.webkitRelativePath || file.name;
      if (path.toLowerCase().endsWith(".csv")) paths.push({path, size: file.size});
    }
  }
  return paths;
}

let dragDepth = 0;
const dropOverlay = byId("drop-overlay");
window.addEventListener("dragenter", event => {
  if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
  event.preventDefault(); dragDepth += 1; dropOverlay.hidden = false;
});
window.addEventListener("dragover", event => {
  if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
  event.preventDefault(); event.dataTransfer.dropEffect = "copy"; dropOverlay.hidden = false;
});
window.addEventListener("dragleave", event => {
  event.preventDefault(); dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) dropOverlay.hidden = true;
});
window.addEventListener("drop", async event => {
  event.preventDefault(); dragDepth = 0; dropOverlay.hidden = true;
  try {
    const files = await droppedPaths(event.dataTransfer);
    if (!files.length) throw new Error("No CSV files were found in that drop.");
    const folder = files[0].path.split("/")[0];
    setStatus("working", "Locating dropped folder", `${files.length.toLocaleString()} CSV files detected; resolving the local data path.`);
    const response = await fetch("/api/resolve-drop", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({folder, files}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Folder resolution failed with HTTP ${response.status}`);
    sourceInput.value = result.source;
    await loadDataset(result.source);
  } catch (error) {
    setStatus("error", "Could not load the dropped folder", error.message);
  }
});

const urlSource = new URLSearchParams(location.search).get("source");
fetch("/native-config.json").then(response => response.json()).then(config => {
  const source = urlSource || config.defaultSource || "";
  sourceInput.value = source;
  if (source) loadDataset(source);
}).catch(() => { if (urlSource) loadDataset(urlSource); });
