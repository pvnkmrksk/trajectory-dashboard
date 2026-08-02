import {
  TrajectoryRenderer, HeatmapRenderer, DirectionRenderer, NATIVE_PALETTE, formatNumber,
} from "/static/renderers.js";
import {
  EChartsPolarRenderer, EChartsHeadingRenderer, EChartsMetricsRenderer,
  EChartsRoiRenderer, EChartsHistogramRenderer, EChartsRawRenderer,
} from "/static/echarts_renderers.js";

const byId = id => document.getElementById(id);
const shell = byId("app-shell");
const statusDock = byId("status-dock");
const statusTitle = byId("status-title");
const statusDetail = byId("status-detail");
const sourceInput = byId("source-input");
const applyButton = byId("apply-button");
const resetViewButton = byId("reset-view-button");
const rawLoadButton = byId("raw-load");

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

function setStatus(kind, title, detail) {
  statusDock.className = `status-dock ${kind || ""}`;
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
let rawRenderer;

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
  rawRenderer = new EChartsRawRenderer(byId("raw-plot"));
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
trajectoryRenderer.setRingMoveHandler((index, x, z, final) => {
  if (!rings[index]) return;
  rings[index] = {...rings[index], x, z};
  activeRing = index;
  renderRingControls();
  if (final) scheduleCompute("trajectory", 20);
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

function datasetSummary() {
  const counts = datasetHeader?.counts;
  if (!counts) return;
  byId("dataset-summary").innerHTML = `
    <div><b>${formatCount(counts.retainedRows)}</b><span>retained rows</span></div>
    <div><b>${formatCount(counts.segments)}</b><span>segments</span></div>
    <div><b>${formatCount(counts.animals)}</b><span>animals</span></div>
    <div><b>${formatCount(counts.files)}</b><span>files</span></div>`;
}

function applyAnimalVisibility() {
  trajectoryRenderer.setAnimalVisibility(animalVisibility);
  const charts = [polarRenderer, headingRenderer, metricsRenderer, roiRenderer];
  for (const chart of charts) chart.animalVisibility = [...animalVisibility];
  const activeSection = document.querySelector(".section-nav button.active")?.dataset.target;
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

function fillRange(idMin, idMax, values) {
  const lo = byId(idMin), hi = byId(idMax);
  // Preserve the exact float32 extrema. Cosmetic rounding here silently
  // excluded the segment owning a rounded-down maximum on first render.
  lo.value = String(values[0]); hi.value = String(values[1]);
  lo.placeholder = formatNumber(values[0]); hi.placeholder = formatNumber(values[1]);
}

function populateControls() {
  datasetSummary();
  const categories = byId("category-filters");
  categories.replaceChildren();
  const labels = {config: "Treatments", scene: "Scenes", vr: "VR arenas", fly: "Animals", folder: "Source folders"};
  for (const key of ["config", "scene", "vr", "fly", "folder"]) {
    const label = document.createElement("label");
    label.textContent = labels[key];
    const select = document.createElement("select");
    select.multiple = true; select.id = `filter-${key}`; select.dataset.scope = "full";
    for (const [index, text] of datasetHeader.categories[key].entries()) {
      const option = document.createElement("option"); option.value = index; option.textContent = text; select.appendChild(option);
    }
    label.appendChild(select); categories.appendChild(label);
    select.addEventListener("change", () => scheduleCompute("full", 140));
  }
  fillRange("trial-min", "trial-max", datasetHeader.ranges.trial);
  fillRange("step-min", "step-max", datasetHeader.ranges.step);
  fillRange("peak-min", "peak-max", datasetHeader.ranges.peakSpeed);
  fillRange("disp-min", "disp-max", datasetHeader.ranges.displacement);
  fillRange("distance-min", "distance-max", datasetHeader.ranges.distance);
  const raw = byId("raw-channel"); raw.replaceChildren(new Option("Choose a channel", ""));
  for (const column of datasetHeader.rawColumns || []) raw.add(new Option(column, column));
  rawLoadButton.disabled = true;
  applyButton.disabled = false; resetViewButton.disabled = false; byId("resample-button").disabled = false;
  byId("export-button").disabled = false;
  const maxTime = Math.max(0, datasetHeader.playbackMax ?? datasetHeader.ranges.time[1]);
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
    ringMatch: byId("ring-match").value,
    rings: rings.map(ring => ({...ring})),
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
  if (state.ringEnabled) params.set("ring", "1"); else params.delete("ring");
  params.set("rings", JSON.stringify(rings)); params.set("ringmatch", state.ringMatch);
  history.replaceState(null, "", url);
}

function restoreViewStateFromUrl() {
  const params = new URLSearchParams(location.search);
  const values = {
    "group-by": params.get("group"), "color-by": params.get("color"),
    "panel-columns": params.get("cols"), "bin-size": params.get("bin"),
    "bound-percent": params.get("bound"), "angle-source": params.get("angle"),
    "stats-unit": params.get("unit"),
  };
  for (const [id, value] of Object.entries(values)) if (value !== null && byId(id)) byId(id).value = value;
  byId("ring-enabled").checked = params.get("ring") === "1";
  byId("ring-match").value = params.get("ringmatch") || "any";
  try {
    const restored = JSON.parse(params.get("rings") || "null");
    if (Array.isArray(restored) && restored.length) rings = restored.map(ring => ({x:Number(ring.x)||0,z:Number(ring.z)||0,r:Math.max(.01,Number(ring.r)||3)}));
  } catch (_) { /* retain safe default */ }
}

function scopePriority(scope) {
  return {layout: 0, trajectory: 1, direction: 2, statistics: 2, spatial: 3, raw: 3, full: 4}[scope] || 1;
}

let computeTimer = null;
function scheduleCompute(scope = "full", delay = 0, extra = {}) {
  if (!workerReady || !datasetHeader) return;
  clearTimeout(computeTimer);
  const existing = pendingCompute;
  const chosenScope = existing && scopePriority(existing.scope) > scopePriority(scope) ? existing.scope : scope;
  computeTimer = setTimeout(() => queueCompute(chosenScope, extra), delay);
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

function renderProducts(incoming, summary) {
  Object.assign(products, incoming);
  const preserve = !newDataset && !!sharedView;
  if (incoming.trajectory) {
    incoming.trajectory.columns = numberValue("panel-columns");
    trajectoryRenderer.setData(incoming.trajectory, preserve);
    if (!preserve) sharedView = {...trajectoryRenderer.view};
    syncView(sharedView, trajectoryRenderer);
    const ringText = incoming.trajectory.ringEnabled ? ` · ${formatCount(incoming.trajectory.ringMatches)} ring matches` : "";
    byId("trajectory-summary").textContent = `${formatCount(summary.visibleSegments)} segments · ${formatCount(summary.visibleRows)} retained points · ${formatCount(incoming.trajectory.links)} GPU line segments${ringText}`;
  }
  if (incoming.heatmap) {
    incoming.heatmap.columns = numberValue("panel-columns");
    heatmapRenderer.setData(incoming.heatmap, !!sharedView);
    heatmapRenderer.setMetricScale(byId("heat-metric").value, byId("heat-scale").value);
    if (sharedView) heatmapRenderer.setView(sharedView, false);
  }
  if (incoming.direction) {
    incoming.direction.columns = numberValue("panel-columns");
    directionRenderer.setData(incoming.direction, !!sharedView);
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
  if (incoming.raw) { rawRenderer.setData(incoming.raw); byId("raw-status").textContent = `${incoming.raw.column} · ${formatCount(incoming.raw.vertices.length / 4)} drawn links`; }
  const fraction = numberValue("trial-fraction", 100) / 100;
  trajectoryRenderer.setFraction(fraction); polarRenderer.setFraction(fraction); headingRenderer.setFraction(fraction);
  applyAnimalVisibility();
  for (const renderer of spatialRenderers) renderer.setRoisVisible(byId("roi-show").checked);
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
  if (message.type === "channel-ready") {
    workerBusy = false;
    queueCompute("raw", {column: message.column});
    return;
  }
  if (message.type === "inspect-result") {
    const match = message.match;
    byId("segment-inspector").textContent = match
      ? `${match.sourceFile} · trial ${formatNumber(match.trial, 0)} / step ${formatNumber(match.step, 0)} · ${match.config} · ${match.fly}@${match.vr} · path ${formatNumber(match.distance, 3)}, displacement ${formatNumber(match.displacement, 3)}, peak ${formatNumber(match.peakSpeed, 3)}, median ${formatNumber(match.medianSpeed, 3)}, tortuosity ${formatNumber(match.tortuosity, 3)}`
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
    populateControls(); products = {}; sharedView = null; newDataset = true; sampleSeed = 0;
    if (worker) worker.terminate();
    workerReady = false; workerBusy = false; pendingCompute = null;
    worker = new Worker("/static/worker.js"); worker.onmessage = handleWorkerMessage;
    worker.onerror = event => setStatus("error", "Worker crashed", event.message);
    worker.postMessage({type: "init", header: datasetHeader, buffer, bodyOffset: parsed.bodyOffset}, [buffer]);
    setStatus("working", "Data loaded", `${formatCount(datasetHeader.counts.retainedRows)} retained rows transferred once; building local views.`);
  } catch (error) {
    setStatus("error", "Could not load data", error.message);
  } finally {
    byId("load-button").disabled = false;
    applyButton.disabled = !datasetHeader;
  }
}

async function loadRawChannel() {
  const column = byId("raw-channel").value;
  if (!column || !datasetHeader || !worker) return;
  rawLoadButton.disabled = true; byId("raw-status").textContent = `Loading ${column}…`;
  try {
    const response = await fetch(`/api/channel/${datasetHeader.datasetId}?name=${encodeURIComponent(column)}`);
    if (!response.ok) throw new Error((await response.json()).error || `HTTP ${response.status}`);
    const buffer = await response.arrayBuffer(), parsed = parseBinary(buffer);
    workerBusy = true;
    worker.postMessage({type: "channel", column, header: parsed.header, buffer, bodyOffset: parsed.bodyOffset}, [buffer]);
  } catch (error) {
    byId("raw-status").textContent = error.message; rawLoadButton.disabled = false;
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
  const html = `<!doctype html><meta charset="utf-8"><title>Dari Deepa native report</title>
    <style>body{margin:32px auto;max-width:1500px;padding:0 24px;color:#18221f;background:#f4f1ea;font:14px system-ui}h1,h2{font-family:Georgia,serif;font-weight:500}header{border-bottom:1px solid #ccc;padding-bottom:16px}section{margin:28px 0;padding:14px;background:#fffdf8;border:1px solid #d9d5ca;border-radius:12px}img{display:block;width:100%;height:auto}small{color:#66716d}</style>
    <header><h1>Dari Deepa — native analysis report</h1><p>${escapeHtml(sourceInput.value)}</p><small>${formatCount(counts.retainedRows)} retained of ${formatCount(counts.sourceRows)} source rows · ${formatCount(counts.segments)} segments · ${formatCount(counts.animals)} animals</small></header>
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
  const ring = rings[activeRing] || {x: 0, z: 0, r: 3};
  byId("ring-x").value = Number(ring.x.toFixed(3));
  byId("ring-z").value = Number(ring.z.toFixed(3));
  byId("ring-radius").value = Number(ring.r.toFixed(3));
  byId("ring-delete").disabled = rings.length <= 1;
}

function updateActiveRing() {
  rings[activeRing] = {
    x: numberValue("ring-x"), z: numberValue("ring-z"),
    r: Math.max(.01, numberValue("ring-radius", 3)),
  };
  scheduleCompute("trajectory", 90);
}

function updatePlaybackTime() {
  const enabled = byId("playback-enabled").checked;
  const value = numberValue("time-scrubber", datasetHeader?.playbackMax ?? datasetHeader?.ranges.time[1] ?? 0);
  trajectoryRenderer.setTime(enabled ? value : Number.POSITIVE_INFINITY);
  byId("time-output").textContent = enabled ? `${formatNumber(value, 2)} s` : "all time";
}

function playbackTick(now) {
  if (!byId("playback-enabled").checked || byId("play-button").textContent !== "Pause") { playbackFrame = null; return; }
  const maxTime = Number(byId("time-scrubber").max) || 1;
  const elapsed = playbackLast ? (now - playbackLast) / 1000 : 0; playbackLast = now;
  let value = numberValue("time-scrubber") + elapsed * maxTime / 20;
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
applyButton.addEventListener("click", () => scheduleCompute("full"));
resetViewButton.addEventListener("click", () => trajectoryRenderer.resetView(true));
byId("clean-button").addEventListener("click", () => {
  shell.classList.toggle("clean-mode");
  byId("clean-button").textContent = shell.classList.contains("clean-mode") ? "Full view" : "Clean view";
  setTimeout(() => spatialRenderers.forEach(renderer => { renderer.resize(); renderer.draw(); }), 40);
});
byId("export-button").addEventListener("click", exportNativeReport);
byId("animals-all").addEventListener("click", () => {
  animalVisibility.fill(true); renderAnimalVisibility(); applyAnimalVisibility();
});
byId("animals-none").addEventListener("click", () => {
  animalVisibility.fill(false); renderAnimalVisibility(); applyAnimalVisibility();
});
byId("trial-fraction").addEventListener("input", updateFraction);
byId("resample-button").addEventListener("click", () => { sampleSeed += 1; scheduleCompute("trajectory"); });
byId("roi-show").addEventListener("change", () => {
  for (const renderer of spatialRenderers) renderer.setRoisVisible(byId("roi-show").checked);
});
byId("ring-enabled").addEventListener("change", () => scheduleCompute("trajectory", 30));
byId("ring-match").addEventListener("change", () => scheduleCompute("trajectory", 30));
byId("ring-active").addEventListener("change", () => { activeRing = Number(byId("ring-active").value) || 0; renderRingControls(); });
for (const id of ["ring-x", "ring-z", "ring-radius"]) byId(id).addEventListener("input", updateActiveRing);
byId("ring-add").addEventListener("click", () => {
  const current = rings[activeRing] || {x:0,z:0,r:3}; rings.push({...current, x: current.x + current.r * .5}); activeRing = rings.length - 1;
  renderRingControls(); scheduleCompute("trajectory", 30);
});
byId("ring-delete").addEventListener("click", () => {
  if (rings.length <= 1) return; rings.splice(activeRing, 1); activeRing = Math.max(0, activeRing - 1);
  renderRingControls(); scheduleCompute("trajectory", 30);
});
byId("heat-metric").addEventListener("change", () => heatmapRenderer.setMetricScale(byId("heat-metric").value, byId("heat-scale").value));
byId("heat-scale").addEventListener("change", () => heatmapRenderer.setMetricScale(byId("heat-metric").value, byId("heat-scale").value));
byId("raw-channel").addEventListener("change", () => { rawLoadButton.disabled = !byId("raw-channel").value; });
rawLoadButton.addEventListener("click", loadRawChannel);
byId("playback-enabled").addEventListener("change", () => {
  const enabled = byId("playback-enabled").checked;
  byId("play-button").disabled = !enabled; byId("time-scrubber").disabled = !enabled;
  if (enabled) byId("time-scrubber").value = 0; else stopPlayback();
  updatePlaybackTime();
});
byId("time-scrubber").addEventListener("input", updatePlaybackTime);
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

for (const button of document.querySelectorAll(".section-nav button")) {
  button.addEventListener("click", () => {
    for (const item of document.querySelectorAll(".section-nav button")) item.classList.toggle("active", item === button);
    byId(button.dataset.target).scrollIntoView({behavior: "smooth", block: "start"});
    setTimeout(() => ({
      "polar-section": polarRenderer,
      "heading-section": headingRenderer,
      "metrics-section": metricsRenderer,
      "roi-section": roiRenderer,
    }[button.dataset.target]?.syncAnimalLegend?.()), 260);
  });
}
const sectionRatios = new Map();
const sectionObserver = new IntersectionObserver(entries => {
  for (const entry of entries) sectionRatios.set(entry.target, entry.isIntersecting ? entry.intersectionRatio : 0);
  const visible = [...sectionRatios.entries()]
    .filter(([, ratio]) => ratio > 0)
    .sort((a, b) => b[1] - a[1])[0]?.[0];
  if (!visible) return;
  for (const button of document.querySelectorAll(".section-nav button")) button.classList.toggle("active", button.dataset.target === visible.id);
}, {root: byId("workspace"), threshold: [.15, .4, .7]});
for (const section of document.querySelectorAll(".plot-section")) sectionObserver.observe(section);

async function readDroppedEntry(entry, prefix, paths) {
  if (entry.isFile) {
    if (entry.name.toLowerCase().endsWith(".csv")) paths.push(`${prefix}${entry.name}`);
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
      if (path.toLowerCase().endsWith(".csv")) paths.push(path);
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
    const folder = files[0].split("/")[0];
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
