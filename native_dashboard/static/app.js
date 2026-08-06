import {
  TrajectoryRenderer, HeatmapRenderer, DirectionRenderer, TransitionRenderer,
  NATIVE_PALETTE, formatNumber,
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
let transitionSelectionActive = false;
let panelOrders = {};
let mirrorRules = [];
let initialUrlStateAvailable = true;
let playbackPlaying = false;
let playbackActive = false;
const rangeControls = new Map();

function syncAngleSourceControls(value) {
  const next = value === "movement" ? "movement" : "orientation";
  for (const id of ["angle-source", "polar-angle-source"]) {
    const control = byId(id);
    if (control) control.value = next;
  }
}

function setStatus(kind, title, detail) {
  statusDock.className = `status-dock ${kind || ""}`;
  statusDock.title = detail ? `${title} — ${detail}` : title;
  statusTitle.textContent = title;
  statusDetail.textContent = detail || "";
}

function setLoadProgress(value = null) {
  const progress = byId("load-progress");
  if (!progress) return;
  progress.hidden = value == null;
  if (value != null) progress.value = Math.max(0, Math.min(100, Number(value) || 0));
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
let transitionRenderer;

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
  transitionRenderer = new TransitionRenderer(byId("transition-plot"), syncView);
} catch (error) {
  setStatus("error", "Renderer unavailable", error.message);
  throw error;
}
const spatialRenderers = [trajectoryRenderer, heatmapRenderer, directionRenderer, transitionRenderer];
trajectoryRenderer.setInspectHandler((point, view) => {
  if (!workerReady || !worker) return;
  const requestId = ++latestInspectRequest;
  worker.postMessage({
    type: "inspect", requestId, panel: point.panel, x: point.x, z: point.z,
    tolerance: Math.max(view.xmax - view.xmin, view.zmax - view.zmin) * .018,
  });
  byId("segment-inspector").textContent = "Finding the nearest retained segment…";
});
function handleRingMove(source, index, x, z, final, radius = null) {
  if (!rings[index]) return;
  rings[index] = {...rings[index], x, z,
    r: Number.isFinite(radius) ? Math.max(.01, radius) : rings[index].r};
  activeRing = index;
  updateRingControlValues();
  for (const renderer of spatialRenderers) {
    if (renderer === source || !renderer.data) continue;
    renderer.setRings(rings, byId("ring-enabled").checked, byId("ring-match").value, false);
  }
  scheduleLocalRingObserver(final, source);
}

function selectRing(index) {
  activeRing = Math.max(0, Math.min(rings.length - 1, Number(index) || 0));
  renderRingControls();
}

function deleteRing(index = activeRing) {
  if (!rings.length) return;
  rings.splice(Math.max(0, Math.min(rings.length - 1, Number(index) || 0)), 1);
  activeRing = Math.max(0, Math.min(rings.length - 1, activeRing));
  if (!rings.length) byId("ring-enabled").checked = false;
  renderRingControls();
  scheduleLocalRingObserver(true);
}

for (const renderer of spatialRenderers) {
  renderer.setRingMoveHandler((...args) => handleRingMove(renderer, ...args));
  renderer.setRingSelectHandler(selectRing);
  renderer.setRingDeleteHandler(deleteRing);
}

function clearTransitionSelection() {
  if (!transitionSelectionActive) return;
  transitionSelectionActive = false;
  transitionRenderer.clearTrajectoryOverlay();
  byId("segment-inspector").textContent = "Transition selection cleared. Click a supported cell to reveal its raw paths.";
  setStatus("ready", "Transition selection cleared", "The shared curtain and analytical subset remain active.");
}

transitionRenderer.setInspectHandler(point => {
  const cell = transitionRenderer.cellAt(point);
  if (!cell) { clearTransitionSelection(); return; }
  if (!workerReady || !worker) return;
  const requestId = ++latestInspectRequest;
  worker.postMessage({
    type: "transition-inspect", requestId,
    panel: cell.panel, ix: cell.ix, iz: cell.iz, x: cell.x, z: cell.z,
  });
  setStatus("working", "Selecting transition paths", "Finding the unique segments that entered the selected spatial cell.");
});
transitionRenderer.setBackgroundHandler(clearTransitionSelection);

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

function renderFilterAudit(audit = []) {
  const host = byId("filter-audit");
  if (!host) return;
  const source = audit[0] || {segments: 0, rows: 0};
  const base = Math.max(1, Number(source.rows) || 1);
  host.innerHTML = audit.map((stage, index) => {
    const previous = audit[Math.max(0, index - 1)] || source;
    const kept = Math.max(0, Math.min(100, Number(stage.rows) / base * 100));
    const lostRows = Math.max(0, (Number(previous.rows) || 0) - (Number(stage.rows) || 0));
    const lostSegments = Math.max(0, (Number(previous.segments) || 0) - (Number(stage.segments) || 0));
    const title = `${stage.label}: ${formatCount(stage.rows)} rows and ${formatCount(stage.segments)} segments retained (${formatNumber(kept, 1)}%).${index ? ` This step removed ${formatCount(lostRows)} rows and ${formatCount(lostSegments)} segments.` : ""}`;
    return `<div class="filter-audit-row" title="${escapeHtml(title)}"><span>${escapeHtml(stage.label)}</span><i style="--kept:${kept.toFixed(1)}%"></i><small>${formatCount(stage.rows)}${index ? ` · −${formatCount(lostRows)}` : ""}</small></div>`;
  }).join("");
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
  byId("animals-invert").disabled = !names.length;
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
  let histogram = datasetHeader.filterHistograms[key];
  const exact = histogram.range, display = histogram.displayRange;
  const integer = key === "trial" || key === "step" || key === "replicate";
  const step = integer ? 1 : Math.max(1e-6, (display[1] - display[0]) / 500);
  const inputStep = integer ? 1 : .1;
  // The mini-chart may clip extreme outliers, but an untouched control always
  // means the exact full dataset range. Playback has its own p95/p99 cap.
  const initialRange = exact;
  const shownExact = [
    displayRangeBound(initialRange[0], integer, false),
    displayRangeBound(initialRange[1], integer, true),
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
  const distributionNote = host.querySelector(".range-filter-head small");
  const loInput = byId(idMin), hiInput = byId(idMax);
  const redraw = () => drawMiniHistogram(canvas, histogram, [Number(loInput.value), Number(hiInput.value)]);
  const fromSlider = changed => {
    if (Number(low.value) > Number(high.value)) {
      if (changed === low) high.value = low.value; else low.value = high.value;
    }
    loInput.value = displayRangeBound(low.value, integer, false);
    hiInput.value = displayRangeBound(high.value, integer, true);
    redraw();
    scheduleDataUpdate(120);
  };
  low.addEventListener("input", () => fromSlider(low));
  high.addEventListener("input", () => fromSlider(high));
  for (const input of [loInput, hiInput]) input.addEventListener("change", () => {
    low.value = Math.max(Number(low.min), Math.min(Number(low.max), Number(loInput.value)));
    high.value = Math.max(Number(high.min), Math.min(Number(high.max), Number(hiInput.value)));
    redraw(); scheduleDataUpdate(100);
  });
  new ResizeObserver(redraw).observe(canvas);
  redraw();
  const setHistogram = next => {
    if (!next) return;
    histogram = {...histogram, ...next};
    distributionNote.textContent = `${formatCount(next.visible)} in current AND subset${next.overflow ? ` · ${formatCount(next.overflow)} above chart` : ""}`;
    redraw();
  };
  rangeControls.set(key, {host, redraw, setHistogram, low, high, loInput, hiInput});
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
      const option = byId(`filter-${key}`)?.querySelector(`span[data-code="${index}"]`);
      if (option) option.textContent = displayNames[key][index];
      scheduleDataUpdate(20);
    });
  });
}

function filterChoices(key) {
  return [...(byId(`filter-${key}`)?.querySelectorAll('input[type="checkbox"][data-code]') || [])];
}

function setFilterSelection(key, selected, defaultAll = true) {
  const choices = filterChoices(key);
  const wanted = new Set((selected || []).map(Number));
  for (const choice of choices) choice.checked = defaultAll && !wanted.size
    ? true : wanted.has(Number(choice.value));
}

function inferredMirrorRules() {
  const configs = datasetHeader?.categories?.config || [];
  const groups = new Map();
  configs.forEach((name, code) => {
    const presentation = datasetHeader?.configPresentation?.[name];
    if (!presentation?.poolable || !presentation.mirrorKey) return;
    const group = groups.get(presentation.mirrorKey) || [];
    group.push({code, sign: Number(presentation.mirrorSign) || 1, label: presentation.mirrorLabel});
    groups.set(presentation.mirrorKey, group);
  });
  return [...groups.values()].filter(group => group.length >= 2).map(group => {
    const reference = group.find(item => item.sign >= 0) || group[0];
    const reflected = group.find(item => item !== reference && item.sign < 0) || group.find(item => item !== reference);
    return {reference: reference.code, reflected: reflected.code, axis: "x", coordinate: 0,
      label: reference.label || "Mirrored pair", automatic: true};
  });
}

function setMirrorGuide(rule = null) {
  for (const renderer of spatialRenderers) renderer.setMirrorGuide(rule);
}

function renderMirrorPairs() {
  const host = byId("mirror-pair-list");
  host.replaceChildren();
  const configs = datasetHeader?.categories?.config || [];
  const optionHtml = configs.map((name, code) => `<option value="${code}">${escapeHtml(name)}</option>`).join("");
  if (!mirrorRules.length) {
    const empty = document.createElement("span"); empty.className = "empty-control";
    empty.textContent = configs.length > 1 ? "No pairs yet. Add one to define a common frame." : "At least two treatments are required.";
    host.appendChild(empty);
  }
  mirrorRules.forEach((rule, index) => {
    const row = document.createElement("div"); row.className = "mirror-pair";
    row.innerHTML = `
      <label>Keep as reference<select class="mirror-reference">${optionHtml}</select></label>
      <span class="mirror-arrow" title="Reflect the second treatment into the first treatment's frame">↑ reflect into reference frame</span>
      <label>Reflect this treatment<select class="mirror-reflected">${optionHtml}</select></label>
      <label class="mirror-axis">Axis<select><option value="x">X</option><option value="z">Z</option></select></label>
      <label class="mirror-coordinate">Line<input type="number" step="0.1" value="${Number(rule.coordinate) || 0}"></label>
      <button class="quiet mirror-remove" type="button" aria-label="Delete pair">×</button>`;
    const reference = row.querySelector(".mirror-reference"), reflected = row.querySelector(".mirror-reflected");
    const axis = row.querySelector(".mirror-axis select"), coordinate = row.querySelector(".mirror-coordinate input");
    reference.value = rule.reference; reflected.value = rule.reflected; axis.value = rule.axis === "z" ? "z" : "x";
    const update = () => {
      rule.reference = Number(reference.value); rule.reflected = Number(reflected.value);
      rule.axis = axis.value === "z" ? "z" : "x"; rule.coordinate = Number(coordinate.value) || 0;
      rule.automatic = false;
      setMirrorGuide(rule); byId("mirror-pool").disabled = false;
      scheduleDataUpdate(120);
    };
    for (const control of [reference, reflected, axis, coordinate]) {
      control.addEventListener("focus", () => setMirrorGuide(rule));
      control.addEventListener("change", update);
    }
    row.querySelector(".mirror-remove").addEventListener("click", () => {
      mirrorRules.splice(index, 1); renderMirrorPairs(); setMirrorGuide(null);
      byId("mirror-pool").disabled = (datasetHeader?.categories?.config?.length || 0) < 2;
      scheduleDataUpdate(80);
    });
    host.appendChild(row);
  });
  byId("mirror-pair-add").disabled = configs.length < 2;
  byId("mirror-pair-clear").disabled = !mirrorRules.length;
}

function populateControls(restoreUrl = false) {
  datasetSummary();
  loadDisplayNames();
  const mirrorPairs = Object.values(datasetHeader.configPresentation || {})
    .filter(config => config?.poolable).length / 2;
  const canPairManually = (datasetHeader.categories.config || []).length >= 2;
  if (!restoreUrl || !mirrorRules.length) mirrorRules = inferredMirrorRules();
  byId("mirror-pool").disabled = !canPairManually;
  if (byId("mirror-pool").disabled) byId("mirror-pool").checked = false;
  byId("mirror-pool").closest("label").title = mirrorPairs >= 1
    ? `${formatCount(mirrorPairs)} mirrored left/right pair${mirrorPairs === 1 ? "" : "s"} detected. Pool by reflecting X and heading into one canonical frame.`
    : (canPairManually ? "No automatic pair was detected. Open Mirror pairing to match treatments explicitly." : "At least two treatments are required for mirrored pooling.");
  const categories = byId("category-filters");
  categories.replaceChildren();
  const labels = {config: "Treatments", scene: "Scenes", vr: "VR arenas", fly: "Animals", folder: "Source folders"};
  for (const key of ["config", "scene", "vr", "fly", "folder"]) {
    const fieldset = document.createElement("fieldset"); fieldset.className = "category-filter";
    const legend = document.createElement("legend"); legend.textContent = labels[key];
    const actions = document.createElement("span"); actions.className = "category-filter-actions";
    const all = document.createElement("button"); all.type = "button"; all.textContent = "All";
    const none = document.createElement("button"); none.type = "button"; none.textContent = "None";
    const invert = document.createElement("button"); invert.type = "button"; invert.textContent = "Invert";
    actions.append(all, none, invert); legend.appendChild(actions);
    const checklist = document.createElement("div"); checklist.id = `filter-${key}`; checklist.className = "category-checklist";
    for (const [index, text] of datasetHeader.categories[key].entries()) {
      const option = document.createElement("label"); option.className = "category-check";
      const input = document.createElement("input"); input.type = "checkbox";
      input.value = index; input.dataset.code = index; input.checked = true; input.dataset.scope = "full";
      const caption = document.createElement("span"); caption.dataset.code = index;
      caption.textContent = displayNames[key]?.[index] || text;
      option.append(input, caption); checklist.appendChild(option);
    }
    fieldset.append(legend, checklist); categories.appendChild(fieldset);
    checklist.addEventListener("change", () => scheduleDataUpdate(140));
    all.addEventListener("click", () => { setFilterSelection(key, []); scheduleDataUpdate(80); });
    none.addEventListener("click", () => { setFilterSelection(key, [], false); scheduleDataUpdate(80); });
    invert.addEventListener("click", () => {
      for (const choice of filterChoices(key)) choice.checked = !choice.checked;
      scheduleDataUpdate(80);
    });
  }
  byId("range-filters").replaceChildren(); rangeControls.clear();
  createRangeControl("trial", "Trial number", "trial-min", "trial-max");
  createRangeControl("step", "Step / segment", "step-min", "step-max");
  createRangeControl("replicate", "Replicate order (trial × step)", "replicate-min", "replicate-max");
  createRangeControl("time", "Local trial time (seconds)", "time-min", "time-max");
  createRangeControl("resultant", "Trial resultant R", "resultant-min", "resultant-max");
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
  byId("time-scrubber").disabled = false; byId("play-button").disabled = false;
  byId("time-output").textContent = "all time";
  if (restoreUrl) restoreViewStateFromUrl();
  else {
    byId("overview-grouping").value = "panels";
    byId("mirror-pool").checked = false;
    byId("moving-only").checked = false;
    byId("roi-entered").checked = false;
    byId("roi-trim").checked = false;
    byId("ring-enabled").checked = false;
  }
  byId("mirror-pool").disabled = !canPairManually;
  renderMirrorPairs();
  byId("transition-split-label").textContent = `Split ${byId("transition-axis").value.toUpperCase()}`;
  renderRingControls();
  animalVisibility = (datasetHeader.categories.animal || []).map(() => true);
  renderAnimalVisibility();
}

function selectedCodes(key) {
  const selected = filterChoices(key).filter(choice => choice.checked).map(choice => Number(choice.value));
  return selected.length ? selected : [-1];
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

function observationWindows() {
  return ["a", "b"].map((key, index) => {
    let xmin = numberValue(`window-${key}-xmin`), xmax = numberValue(`window-${key}-xmax`);
    let zmin = numberValue(`window-${key}-zmin`), zmax = numberValue(`window-${key}-zmax`);
    if (xmin > xmax) [xmin, xmax] = [xmax, xmin];
    if (zmin > zmax) [zmin, zmax] = [zmax, zmin];
    return {name: `Window ${String.fromCharCode(65 + index)}`, xmin, xmax, zmin, zmax};
  });
}

function collectState() {
  const ranges = datasetHeader.ranges;
  return {
    filters: Object.fromEntries(["config", "scene", "vr", "fly", "folder"].map(key => [key, selectedCodes(key)])),
    ranges: {
      trial: rangeValue("trial-min", "trial-max", ranges.trial),
      step: rangeValue("step-min", "step-max", ranges.step),
      replicate: rangeValue("replicate-min", "replicate-max", ranges.replicate),
      time: rangeValue("time-min", "time-max", [0, datasetHeader.playbackQuantiles?.median ?? ranges.time[1]]),
      resultant: rangeValue("resultant-min", "resultant-max", ranges.resultant),
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
    overviewGrouping: byId("overview-grouping").value,
    colorBy: byId("color-by").value,
    pointBudget: numberValue("point-budget", 150000),
    movingOnly: byId("moving-only").checked,
    mirrorPool: byId("mirror-pool").checked,
    mirrorRules: mirrorRules.map(rule => ({reference:Number(rule.reference), reflected:Number(rule.reflected),
      axis: rule.axis === "z" ? "z" : "x", coordinate:Number(rule.coordinate) || 0, label: rule.label || ""})),
    walkThreshold: numberValue("walk-threshold"),
    binSize: numberValue("bin-size", 0),
    boundPercent: numberValue("bound-percent", 98),
    angleSource: byId("angle-source").value,
    statsUnit: byId("stats-unit").value,
    polarR: [numberValue("polar-r-min", 0), numberValue("polar-r-max", 1)],
    polarValidMin: numberValue("polar-valid-min", 0),
    polarMode: byId("polar-mode").value,
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
    headingBin: numberValue("heading-bin", 2),
    transitionSplit: optionalNumber("transition-split"),
    transitionAxis: byId("transition-axis").value,
    showMarginals: byId("show-marginals").checked,
    headingSectors: numberValue("heading-sectors", 36),
    windows: observationWindows(),
    windowsVisible: byId("window-show").checked,
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
  if (state.mirrorPool) params.set("mirror", "1"); else params.delete("mirror");
  if (state.mirrorRules.length) params.set("mirrorRules", JSON.stringify(state.mirrorRules)); else params.delete("mirrorRules");
  if (state.panelColumns) params.set("cols", state.panelColumns); else params.delete("cols");
  if (state.binSize) params.set("bin", state.binSize); else params.delete("bin");
  params.set("bound", state.boundPercent); params.set("angle", state.angleSource); params.set("unit", state.statsUnit);
  params.set("pmode", state.polarMode);
  params.set("pcap", state.playbackPercentile);
  params.set("hmode", state.headingMode); params.set("hbin", state.headingBin); params.set("hsec", state.headingSectors);
  params.set("windows", JSON.stringify(state.windows));
  if (state.windowsVisible) params.set("wshow", "1"); else params.delete("wshow");
  params.set("lens", currentLens);
  params.set("view", currentView);
  params.set("overview", state.overviewGrouping);
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
  params.set("hmetric", byId("heat-metric").value); params.set("hscale", byId("heat-scale").value);
  params.set("hrange", byId("heat-range-mode").value); params.set("hcmin", byId("heat-cmin").value); params.set("hcmax", byId("heat-cmax").value);
  params.set("fmetric", byId("flow-metric").value); params.set("frange", byId("flow-range-mode").value);
  params.set("fcmin", byId("flow-cmin").value); params.set("fcmax", byId("flow-cmax").value);
  params.set("prate", byId("particle-rate").value); params.set("trail", byId("trail-length").value);
  params.set("fspeed", byId("flow-speed").value); params.set("fvar", byId("flow-variability").value);
  params.set("fcolor", byId("flow-color-mode").value); params.set("fvelocity", byId("flow-velocity-mode").value);
  if (state.transitionSplit == null) params.delete("tsplit"); else params.set("tsplit", state.transitionSplit);
  params.set("taxis", state.transitionAxis);
  if (state.showMarginals) params.set("marginals", "1"); else params.delete("marginals");
  params.set("toutcome", byId("transition-outcome").value); params.set("tdisplay", byId("transition-display").value);
  params.set("tsupport", byId("transition-support").value);
  if (state.ringEnabled) params.set("ring", "1"); else params.delete("ring");
  if (state.ringContext) params.set("ringcontext", "1"); else params.set("ringcontext", "0");
  params.set("rings", JSON.stringify(rings)); params.set("ringmatch", state.ringMatch);
  history.replaceState(null, "", url);
}

function restoreViewStateFromUrl() {
  const params = new URLSearchParams(location.search);
  const values = {
    "group-by": params.get("group"), "color-by": params.get("color"),
    "overview-grouping": params.get("overview"),
    "panel-columns": params.get("cols"), "bin-size": params.get("bin"),
    "bound-percent": params.get("bound"), "angle-source": params.get("angle"),
    "polar-mode": params.get("pmode"),
    "stats-unit": params.get("unit"), "playback-cap": params.get("pcap"),
    "heading-mode": params.get("hmode"), "heading-bin": params.get("hbin"),
    "heading-sectors": params.get("hsec"), "trajectory-width": params.get("tw"),
    "trajectory-opacity": params.get("topacity"), "heat-metric": params.get("hmetric"),
    "heat-scale": params.get("hscale"), "heat-range-mode": params.get("hrange"),
    "heat-cmin": params.get("hcmin"), "heat-cmax": params.get("hcmax"),
    "flow-metric": params.get("fmetric"), "flow-range-mode": params.get("frange"),
    "flow-cmin": params.get("fcmin"), "flow-cmax": params.get("fcmax"),
    "particle-rate": params.get("prate"), "trail-length": params.get("trail"),
    "flow-speed": params.get("fspeed"), "flow-variability": params.get("fvar"),
    "flow-color-mode": params.get("fcolor"), "flow-velocity-mode": params.get("fvelocity"),
    "transition-split": params.get("tsplit"),
    "transition-axis": params.get("taxis"),
    "transition-outcome": params.get("toutcome"), "transition-display": params.get("tdisplay"),
    "transition-support": params.get("tsupport"),
  };
  for (const [id, value] of Object.entries(values)) if (value !== null && byId(id)) byId(id).value = value;
  syncAngleSourceControls(byId("angle-source").value);
  byId("mirror-pool").checked = params.get("mirror") === "1" && !byId("mirror-pool").disabled;
  byId("show-marginals").checked = params.get("marginals") === "1";
  try {
    const restoredRules = JSON.parse(params.get("mirrorRules") || "null");
    if (Array.isArray(restoredRules)) {
      mirrorRules = restoredRules;
      if (mirrorRules.length) byId("mirror-pool").disabled = false;
      if (params.get("mirror") === "1") byId("mirror-pool").checked = true;
    }
  } catch (_) { /* malformed mirror rules are ignored */ }
  byId("window-show").checked = params.get("wshow") === "1";
  setDashboardView(params.get("view") || params.get("lens") || "trajectory", false);
  try {
    const restoredOrder = JSON.parse(params.get("order") || "{}");
    panelOrders = restoredOrder && typeof restoredOrder === "object" ? restoredOrder : {};
  } catch (_) { panelOrders = {}; }
  try {
    const restoredWindows = JSON.parse(params.get("windows") || "null");
    if (Array.isArray(restoredWindows)) for (const [index, window] of restoredWindows.slice(0, 2).entries()) {
      const key = index ? "b" : "a";
      for (const bound of ["xmin", "xmax", "zmin", "zmax"]) if (window?.[bound] != null) byId(`window-${key}-${bound}`).value = window[bound];
    }
  } catch (_) { /* malformed optional observation windows are ignored */ }
  try {
    const filters = JSON.parse(params.get("filters") || "{}");
    for (const [key, selected] of Object.entries(filters)) {
      if (!byId(`filter-${key}`) || !Array.isArray(selected)) continue;
      setFilterSelection(key, selected, false);
    }
  } catch (_) { /* malformed optional URL state is ignored */ }
  try {
    const ranges = JSON.parse(params.get("ranges") || "{}");
    const ids = {
      trial:["trial-min","trial-max"], step:["step-min","step-max"],
      replicate:["replicate-min","replicate-max"], time:["time-min","time-max"],
      resultant:["resultant-min","resultant-max"], peak:["peak-min","peak-max"],
      displacement:["disp-min","disp-max"], distance:["distance-min","distance-max"],
    };
    for (const [key, valuesForRange] of Object.entries(ranges)) {
      if (!ids[key] || !Array.isArray(valuesForRange)) continue;
      const integer = key === "trial" || key === "step" || key === "replicate";
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
    if (Array.isArray(restored)) rings = restored.map(ring => ({x:Number(ring.x)||0,z:Number(ring.z)||0,r:Math.max(.01,Number(ring.r)||3)}));
  } catch (_) { /* retain safe default */ }
}

const scopeProducts = {
  layout: [], trajectory: ["trajectory"], playback: ["heading"], heading: ["heading"],
  polar: ["polar"], spatial: ["heatmap", "direction", "transition"],
  heatmap: ["heatmap"], direction: ["direction"], overview: ["trajectory", "heatmap", "direction", "polar"],
  overviewPreview: ["heatmap", "direction"],
  color: ["trajectory", "polar", "heading"], sample: ["trajectory", "polar", "heading"],
  metrics: ["metrics"], roi: ["roi"], diagnostics: ["diagnostics"],
  background: ["polar", "heading", "metrics", "roi", "windows"],
  statistics: ["polar", "metrics", "roi", "statistics"],
  transition: ["transition"],
  windows: ["windows"],
  movement: ["trajectory", "direction", "polar", "heading", "roi"],
};

let computeTimer = null;
let scheduledScope = null;
let scheduledExtra = {};
function scheduleCompute(scope = "full", delay = 0, extra = {}) {
  if (!workerReady || !datasetHeader) return;
  clearTimeout(computeTimer);
  // A newer interaction replaces a pending request. Merging stale scopes made
  // small filter changes quietly expand back into an all-view rebuild.
  scheduledScope = scope;
  scheduledExtra = {...extra};
  computeTimer = setTimeout(() => {
    const chosenScope = scheduledScope || "full";
    const chosenExtra = scheduledExtra;
    scheduledScope = null; scheduledExtra = {};
    queueCompute(chosenScope, chosenExtra);
  }, delay);
}

let deferredAnalysisTimer = null;
let deferredStatisticsTimer = null;

function scopeForCurrentView() {
  return {
    trajectory: "trajectory", occupancy: "heatmap", direction: "direction",
    polar: "polar", compare: "overview", transitions: "transition",
    roi: "roi", windows: "windows", heading: "heading", metrics: "metrics",
    statistics: "statistics", diagnostics: "diagnostics",
  }[currentView] || "trajectory";
}

function invalidateDerivedProducts() {
  for (const key of ["trajectory", "heatmap", "direction", "polar", "heading",
    "metrics", "roi", "statistics", "transition", "windows"]) delete products[key];
  byId("statistics-content").innerHTML = '<div class="analysis-empty">Statistics will refresh after 16 seconds of inactivity, or immediately when this view is opened.</div>';
  byId("windows-content").innerHTML = '<div class="analysis-empty">Window summaries will refresh in the background, or immediately when this view is opened.</div>';
}

function invalidateCurtainProducts() {
  clearTimeout(computeTimer);
  scheduledScope = null; scheduledExtra = {};
  pendingCompute = null;
  latestRequest += 1;
  clearTimeout(deferredAnalysisTimer); clearTimeout(deferredStatisticsTimer);
  for (const key of ["heatmap", "direction", "polar", "heading", "metrics",
    "roi", "statistics", "transition", "windows"]) delete products[key];
  byId("statistics-content").innerHTML = '<div class="analysis-empty">Open Statistics to calculate inference for the current curtain and filter intersection.</div>';
  byId("windows-content").innerHTML = '<div class="analysis-empty">Open Windows to calculate summaries for the current curtain and filter intersection.</div>';
}

function armDeferredAnalysis() {
  clearTimeout(deferredAnalysisTimer); clearTimeout(deferredStatisticsTimer);
  const panels = Math.max(1, Number(lastSummary?.panels)
    || (byId("group-by")?.value === "config" && byId("mirror-pool")?.checked
      ? datasetHeader?.categories?.mirrorConfig?.length
      : datasetHeader?.categories?.[byId("group-by")?.value]?.length) || 1);
  const analysisDelay = panels > 8 ? 35000 : (panels > 4 ? 18000 : 8000);
  const statisticsDelay = panels > 8 ? 75000 : (panels > 4 ? 36000 : 16000);
  deferredAnalysisTimer = setTimeout(() => {
    scheduleCompute("background", 0, {quiet: true});
  }, analysisDelay);
  deferredStatisticsTimer = setTimeout(() => {
    scheduleCompute("statistics", 0, {quiet: true});
  }, statisticsDelay);
}

function scheduleDataUpdate(delay = 0) {
  if (!workerReady || !datasetHeader) return;
  invalidateDerivedProducts();
  scheduleCompute(scopeForCurrentView(), delay);
  armDeferredAnalysis();
}

function queueCompute(scope, extra = {}) {
  const requestId = ++latestRequest;
  pendingCompute = {type: "compute", requestId, state: collectState(), scope, ...extra};
  persistState();
  if (!extra.quiet) setStatus("working", scope === "full" ? "Updating analysis" : "Refreshing view", "The retained table is running in a browser worker; the page remains interactive.");
  flushCompute();
  return requestId;
}

let reportRequestId = null;

function flushCompute() {
  if (!workerReady || workerBusy || !pendingCompute) return;
  const message = pendingCompute; pendingCompute = null; workerBusy = true;
  worker.postMessage(message);
}

const panelPlotSpecs = [
  ["trajectory", "trajectory-plot"], ["heatmap", "heatmap-plot"],
  ["direction", "direction-plot"], ["transition", "transition-plot"],
  ["polar", "polar-plot"], ["heading", "heading-plot"],
];

function sizePanelPlot(host, product) {
  if (!host || !product) return;
  const panelCount = Math.max(1, Number(product.panelCount) || 1);
  const requested = Math.max(1, Number(product.columns) || numberValue("panel-columns", 2));
  const columns = Math.min(panelCount, requested);
  const rows = Math.max(1, Math.ceil(panelCount / columns));
  const workspaceWidth = Math.max(360, byId("workspace")?.clientWidth || innerWidth);
  const plotWidth = Math.max(320,
    host.clientWidth || host.closest(".lens-panel, .plot-section")?.clientWidth || workspaceWidth);

  // Give every panel the same fraction of the viewport in both dimensions:
  // two columns are about half-page width by half-page height, four columns
  // about quarter-page width by quarter-page height. A floor prevents dense
  // grids from becoming unreadable; extra rows extend the scrollable document.
  const viewportAspect = Math.max(.35, innerHeight / Math.max(640, innerWidth));
  const rowHeight = Math.round(Math.max(280,
    Math.min(Math.max(420, innerHeight - 92), (plotWidth / columns) * viewportAspect)));
  const gridHeight = rows * rowHeight;

  host.dataset.panelGrid = "true";
  host.dataset.panelCount = String(panelCount);
  host.dataset.panelColumns = String(columns);
  host.dataset.panelRows = String(rows);
  host.style.setProperty("--panel-grid-height", `${gridHeight}px`);
}

function updatePanelGridSizing() {
  for (const [productName, hostId] of panelPlotSpecs) {
    sizePanelPlot(byId(hostId), products[productName]);
  }
}

function updateColumns() {
  const columns = numberValue("panel-columns");
  for (const value of Object.values(products)) if (value && typeof value === "object" && "columns" in value) value.columns = columns;
  updatePanelGridSizing();
  for (const renderer of [...spatialRenderers, polarRenderer, headingRenderer]) renderer.setColumns?.(columns);
  requestAnimationFrame(updatePanelGridSizing);
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
    colorMode: byId("flow-color-mode").value,
    velocityMode: byId("flow-velocity-mode").value,
  });
}

function applyTransitionVisuals() {
  if (!transitionRenderer.data) return;
  transitionRenderer.setVisualOptions({
    outcome: byId("transition-outcome").value,
    display: byId("transition-display").value,
    minimumSupport: numberValue("transition-support", 5),
    split: optionalNumber("transition-split"),
    pathWidth: numberValue("transition-path-width", 1.4),
    pathOpacity: numberValue("transition-path-opacity", .42),
  });
}

function formatP(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  if (number < .001) return "<.001";
  return number.toFixed(3).replace(/^0/, "");
}

function compactLetters(count, tests = []) {
  let columns = [new Set(Array.from({length: count}, (_, index) => index))];
  for (const test of tests.filter(test => Number(test.adjustedP) < .05)) {
    const next = [];
    for (const column of columns) {
      if (column.has(test.first) && column.has(test.second)) {
        const withoutFirst = new Set(column); withoutFirst.delete(test.first);
        const withoutSecond = new Set(column); withoutSecond.delete(test.second);
        if (withoutFirst.size) next.push(withoutFirst);
        if (withoutSecond.size) next.push(withoutSecond);
      } else next.push(column);
    }
    columns = next.filter((column, index, all) => !all.some((other, otherIndex) =>
      otherIndex !== index && column.size < other.size && [...column].every(value => other.has(value))));
  }
  const letter = index => index < 26 ? String.fromCharCode(65 + index)
    : `${String.fromCharCode(65 + Math.floor(index / 26) - 1)}${String.fromCharCode(65 + index % 26)}`;
  return Array.from({length: count}, (_, panel) => columns
    .map((column, index) => column.has(panel) ? letter(index) : "").join(""));
}

function renderStatistics(data) {
  const host = byId("statistics-content");
  const metricLabels = {
    distance: "Distance walked", displacement: "Net displacement",
    speed: "Median velocity", tortuosity: "Local tortuosity",
  };
  const metricCards = data.metrics.map(result => {
    const letters = compactLetters(data.panels.length, result.pairwise);
    const significant = result.pairwise.filter(test => Number(test.adjustedP) < .05);
    const rows = significant.length ? significant.map(test => `
      <tr><td>${escapeHtml(data.panels[test.first])}</td><td>${escapeHtml(data.panels[test.second])}</td>
      <td><span class="stat-pill significant">${formatP(test.adjustedP)}</span></td></tr>`).join("")
      : `<tr><td colspan="3">No Holm-adjusted pairwise differences at α = .05.</td></tr>`;
    return `<article class="statistics-card"><h3>${escapeHtml(metricLabels[result.metric] || result.metric)}</h3>
      <small>Kruskal–Wallis H(${result.omnibus.df}) = ${formatNumber(result.omnibus.h, 1)} · p ${formatP(result.omnibus.p)} · groups ${letters.join(" / ")} · n ${result.counts.join(" / ")}</small>
      <table><thead><tr><th>Panel</th><th>Panel</th><th>Holm p</th></tr></thead><tbody>${rows}</tbody></table></article>`;
  }).join("");
  const circularLetters = compactLetters(data.panels.length, data.circularPairwise);
  const rayleighRows = data.rayleigh.map(result => `<tr><td>${escapeHtml(data.panels[result.panel])}</td><td>${result.p < .05 ? escapeHtml(circularLetters[result.panel]) : "—"}</td><td>${formatNumber(result.n, 0)}</td><td>${formatNumber(result.angle, 1)}°</td><td>${formatNumber(result.r, 1)}</td><td><span class="stat-pill ${result.p < .05 ? "significant" : ""}">${formatP(result.p)}${result.p < .001 ? " ***" : result.p < .01 ? " **" : result.p < .05 ? " *" : ""}</span></td></tr>`).join("");
  const circularRows = (data.circularPairwise || []).map(test => `<tr><td>${escapeHtml(data.panels[test.first])}</td><td>${escapeHtml(data.panels[test.second])}</td><td>${formatNumber(test.difference, 1)}°</td><td><span class="stat-pill ${test.adjustedP < .05 ? "significant" : ""}">${test.comparable === false ? "not directional" : formatP(test.adjustedP)}</span></td></tr>`).join("");
  const audit = lastSummary?.filterAudit || [];
  const sourceRows = Math.max(1, Number(audit[0]?.rows) || 1);
  const auditRows = audit.map((stage, index) => {
    const previous = audit[Math.max(0, index - 1)] || stage;
    const rows = Number(stage.rows) || 0, segments = Number(stage.segments) || 0;
    const removed = index ? Math.max(0, (Number(previous.rows) || 0) - rows) : 0;
    const retained = Math.max(0, Math.min(100, rows / sourceRows * 100));
    return `<tr><td>${escapeHtml(stage.label)}</td><td>${formatCount(rows)}</td><td>${formatCount(segments)}</td>
      <td>${index ? `−${formatCount(removed)}` : "—"}</td><td>${formatNumber(retained, 1)}%</td></tr>`;
  }).join("");
  const auditCard = `<article class="statistics-card filter-statistics-card"><h3>Included-data audit</h3>
    <small>Every result below uses the final intersection of category, range, quality, ROI, local-time, and curtain filters.</small>
    <table><thead><tr><th>Stage</th><th>Rows kept</th><th>Segments</th><th>Removed here</th><th>Source retained</th></tr></thead><tbody>${auditRows}</tbody></table></article>`;
  host.innerHTML = `<div class="statistics-grid">${auditCard}${metricCards}<article class="statistics-card"><h3>Circular direction</h3>
    <small>Rayleigh stars show non-uniformity; letters summarize Holm-adjusted mean-angle comparisons among directional groups.</small>
    <table><thead><tr><th>Panel</th><th>Group</th><th>n</th><th>Mean</th><th>R</th><th>Rayleigh p</th></tr></thead><tbody>${rayleighRows}</tbody></table>
    <table><thead><tr><th>Panel</th><th>Panel</th><th>Δ mean</th><th>Holm p</th></tr></thead><tbody>${circularRows || '<tr><td colspan="4">At least two directional panels are needed.</td></tr>'}</tbody></table></article>
    <article class="statistics-card"><h3>Method</h3><small>${escapeHtml(data.method)}</small><p class="control-note">Inferential work is calculated on demand in the browser worker, so trajectory interaction and playback remain responsive.</p></article></div>`;
}

function renderWindows(data) {
  const host = byId("windows-content");
  const cards = data.panels.map((panelName, panel) => {
    const summaries = data.summaries.filter(item => item.panel === panel);
    const inference = data.paired.find(item => item.panel === panel);
    const rows = summaries.map(item => `<tr><td>${escapeHtml(data.windows[item.window].name)}</td><td>${formatCount(item.segments)}</td><td>${formatNumber(item.seconds, 1)}</td><td>${formatNumber(item.distance, 1)}</td><td>${formatNumber(item.angle, 1)}° / ${formatNumber(item.r, 1)}</td></tr>`).join("");
    return `<article class="statistics-card"><h3>${escapeHtml(panelName)}</h3><small>Totals inside each editable spatial rectangle.</small>
      <table><thead><tr><th>Window</th><th>Segments</th><th>Seconds</th><th>Path</th><th>Direction / R</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="control-note">Paired residence p <span class="stat-pill ${inference?.residence?.p < .05 ? "significant" : ""}">${formatP(inference?.residence?.p)}</span> · paired path p <span class="stat-pill ${inference?.distance?.p < .05 ? "significant" : ""}">${formatP(inference?.distance?.p)}</span></p></article>`;
  }).join("");
  host.innerHTML = `<div class="statistics-grid">${cards}<article class="statistics-card"><h3>Window geometry</h3>
    <table><thead><tr><th>Window</th><th>X</th><th>Z</th></tr></thead><tbody>${data.windows.map(window => `<tr><td>${escapeHtml(window.name)}</td><td>${formatNumber(window.xmin, 1)} to ${formatNumber(window.xmax, 1)}</td><td>${formatNumber(window.zmin, 1)} to ${formatNumber(window.zmax, 1)}</td></tr>`).join("")}</tbody></table><p class="control-note">${escapeHtml(data.method)}</p></article></div>`;
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

function renderProducts(incoming, summary, quiet = false) {
  lastSummary = summary;
  Object.assign(products, incoming);
  if (!quiet) {
    datasetSummary(summary);
    renderFilterAudit(summary.filterAudit || []);
    if (summary.segmentOptions) {
      visibleSegmentOptions = summary.segmentOptions;
      populatePlaybackSegments();
    }
    updatePlaybackLimit(summary.durationSummary);
    if (summary.panelKeys) renderPanelOrder(summary.panelKeys);
  }
  const preserve = !newDataset && !!sharedView;
  if (incoming.trajectory) {
    incoming.trajectory.columns = numberValue("panel-columns");
    trajectoryRenderer.setData(incoming.trajectory, preserve);
    trajectoryRenderer.setLineStyle(numberValue("trajectory-width", 1.1), numberValue("trajectory-opacity", .5));
    trajectoryRenderer.setObservationWindows(byId("window-show").checked ? observationWindows() : []);
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
    incoming.polar.columns = numberValue("panel-columns");
    if (currentView === "polar" || currentView === "compare") polarRenderer.setData(incoming.polar);
    else polarRenderer.data = incoming.polar;
    const polarMode = byId("polar-mode").value;
    byId("polar-summary").textContent = polarMode === "density"
      ? "Circular density of all retained headings in the selected trial-time window"
      : `${formatCount(incoming.polar.units)} ${byId("stats-unit").value === "animal" ? "animal" : "replicate"} resultants retained by the quality gates`;
  }
  if (incoming.heading) {
    incoming.heading.columns = numberValue("panel-columns");
    if (currentView === "heading") headingRenderer.setData(incoming.heading);
    else headingRenderer.data = incoming.heading;
  }
  if (incoming.metrics) {
    if (currentView === "metrics") metricsRenderer.setData(incoming.metrics);
    else metricsRenderer.data = incoming.metrics;
  }
  if (incoming.statistics) {
    renderStatistics(incoming.statistics);
    if (currentView === "polar" || currentView === "compare") polarRenderer.setStatistics?.(incoming.statistics);
    else polarRenderer.statistics = incoming.statistics;
    if (currentView === "metrics") metricsRenderer.setStatistics?.(incoming.statistics);
    else metricsRenderer.statistics = incoming.statistics;
  }
  if (incoming.windows) renderWindows(incoming.windows);
  if (incoming.transition) {
    incoming.transition.columns = numberValue("panel-columns");
    transitionRenderer.setData(incoming.transition, !!sharedView);
    transitionRenderer.clearTrajectoryOverlay(); transitionSelectionActive = false;
    applyTransitionVisuals();
    if (sharedView) transitionRenderer.setView(sharedView, false);
  }
  if (incoming.roi) {
    if (currentView === "roi") roiRenderer.setData(incoming.roi);
    else roiRenderer.data = incoming.roi;
    byId("roi-summary").textContent = `${formatCount(incoming.roi.baseSegments)} quality-filtered segments contribute to fraction and residence denominators.`;
  }
  if (incoming.diagnostics) {
    velocityHistogram.setData(incoming.diagnostics.velocity);
    displacementHistogram.setData(incoming.diagnostics.displacement);
  }
  if (incoming.filterHistograms) for (const [key, histogram] of Object.entries(incoming.filterHistograms)) {
    rangeControls.get(key)?.setHistogram(histogram);
  }
  const fraction = numberValue("trial-fraction", 100) / 100;
  trajectoryRenderer.setFraction(fraction); polarRenderer.setFraction(fraction); headingRenderer.setFraction(fraction);
  applyAnimalVisibility();
  for (const renderer of spatialRenderers) renderer.setRoisVisible(byId("roi-show").checked);
  for (const renderer of spatialRenderers) renderer.setGridVisible(byId("spatial-grid").checked);
  for (const renderer of spatialRenderers) renderer.setMarginalsVisible(byId("show-marginals").checked);
  updatePanelGridSizing();
  requestAnimationFrame(() => requestAnimationFrame(updatePanelGridSizing));
  newDataset = false;
}

function handleWorkerMessage(event) {
  const message = event.data;
  if (message.type === "ready") {
    workerReady = true;
    setStatus("working", "Preparing visible view", "The current layer is calculated first; other analyses refresh after the interface is idle.");
    scheduleDataUpdate(0);
    return;
  }
  if (message.type === "result") {
    workerBusy = false;
    if (message.requestId === latestRequest) {
      displayedRequest = message.requestId;
      renderProducts(message.products, message.summary, message.quiet);
      if (!message.quiet) setStatus("ready", "Ready for exploration", `${formatCount(message.summary.visibleRows)} points · ${formatCount(message.summary.visibleSegments)} segments · worker filter ${message.summary.filterMs.toFixed(0)} ms`);
      if (message.requestId === reportRequestId) {
        reportRequestId = null;
        void captureNativeReport();
      }
    }
    flushCompute();
    return;
  }
  if (message.type === "inspect-result") {
    const match = message.match;
    if (!match && transitionSelectionActive) {
      clearTransitionSelection();
      return;
    }
    byId("segment-inspector").textContent = match
      ? `${match.sourceFile} · replicate ${formatNumber(match.replicate, 0)} · trial ${formatNumber(match.trial, 0)} / step ${formatNumber(match.step, 0)} · ${match.config} · ${match.fly}@${match.vr} · path ${formatNumber(match.distance, 1)}, displacement ${formatNumber(match.displacement, 1)}, peak ${formatNumber(match.peakSpeed, 1)}, median ${formatNumber(match.medianSpeed, 1)}, tortuosity ${formatNumber(match.tortuosity, 1)}`
      : "No retained path was close enough; click nearer a visible line.";
    return;
  }
  if (message.type === "transition-inspect-result") {
    transitionSelectionActive = true;
    transitionRenderer.setTrajectoryOverlay(products.trajectory, message.segments);
    byId("segment-inspector").textContent = `${formatCount(message.segments.length)} unique segments entered the selected transition cell at X ${formatNumber(message.x, 1)}, Z ${formatNumber(message.z, 1)}. Their decimated raw paths are overlaid here; click unsupported white space to clear.`;
    setStatus("ready", "Transition paths selected", `${formatCount(message.segments.length)} unique segments entered the selected cell.`);
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
  stopPlayback(true);
  const urlSource = new URLSearchParams(location.search).get("source") || "";
  const restoreUrl = initialUrlStateAvailable && urlSource === source;
  initialUrlStateAvailable = false;
  setStatus("working", "Loading and preprocessing", "Python is applying the trusted loader once, then packaging typed browser columns.");
  setLoadProgress(4);
  const progressTimer = setInterval(() => {
    const progress = byId("load-progress");
    if (!progress || progress.hidden) return;
    progress.value = Math.min(72, progress.value + Math.max(.2, (76 - progress.value) * .018));
  }, 180);
  byId("load-button").disabled = true; applyButton.disabled = true;
  try {
    const response = await fetch("/api/load", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({source})});
    if (!response.ok) {
      const error = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
      throw new Error(error.error || `Load failed with HTTP ${response.status}`);
    }
    setLoadProgress(78);
    const buffer = await response.arrayBuffer();
    setLoadProgress(92);
    const parsed = parseBinary(buffer);
    datasetHeader = parsed.header; sourceInput.value = source;
    byId("source-popover").open = false;
    visibleSegmentOptions = []; currentDurationSummary = datasetHeader.playbackQuantiles || null;
    populateControls(restoreUrl); products = {}; sharedView = null; newDataset = true; sampleSeed = 0;
    if (worker) worker.terminate();
    workerReady = false; workerBusy = false; pendingCompute = null;
    worker = new Worker("/static/worker.js"); worker.onmessage = handleWorkerMessage;
    worker.onerror = event => setStatus("error", "Worker crashed", event.message);
    worker.postMessage({type: "init", header: datasetHeader, buffer, bodyOffset: parsed.bodyOffset}, [buffer]);
    setLoadProgress(100);
    setStatus("working", "Data loaded", `${formatCount(datasetHeader.counts.retainedRows)} retained rows from ${formatCount(datasetHeader.counts.files)} files transferred once${datasetHeader.counts.duplicateFilesSkipped ? `; ${formatCount(datasetHeader.counts.duplicateFilesSkipped)} duplicate copies skipped` : ""}. Building local views.`);
  } catch (error) {
    setStatus("error", "Could not load data", error.message);
  } finally {
    clearInterval(progressTimer);
    setTimeout(() => setLoadProgress(null), 450);
    byId("load-button").disabled = false;
    applyButton.disabled = !datasetHeader;
  }
}

function talkGrid(product, spatialCanvas = false) {
  const panelCount = Math.max(1, Number(product?.panelCount) || 1);
  const columns = Math.min(2, panelCount);
  const rows = Math.max(1, Math.ceil(panelCount / columns));
  const scale = spatialCanvas ? 2 / Math.min(devicePixelRatio || 1, 2) : 1;
  return {panelCount, columns, rows, width: columns * 800 * scale, height: rows * 450 * scale};
}

function snapshotSpatialRenderer(renderer, product) {
  if (!renderer?.data || !product) return null;
  const layout = talkGrid(product, true);
  const previousColumns = renderer.data.columns;
  const previousWidth = renderer.width, previousHeight = renderer.height;
  renderer.data.columns = layout.columns;
  try {
    if (typeof renderer.snapshotDataUrl === "function") {
      return renderer.snapshotDataUrl(layout.width, layout.height);
    }
    renderer.width = layout.width; renderer.height = layout.height;
    renderer.resize(); renderer.draw();
    const canvases = [...renderer.host.querySelectorAll("canvas")]
      .filter(canvas => canvas.width > 0 && canvas.height > 0);
    if (!canvases.length) return null;
    const output = document.createElement("canvas");
    output.width = canvases[0].width; output.height = canvases[0].height;
    const context = output.getContext("2d");
    context.imageSmoothingEnabled = false;
    for (const canvas of canvases) context.drawImage(canvas, 0, 0, output.width, output.height);
    return output.toDataURL("image/png");
  } finally {
    renderer.data.columns = previousColumns;
    if (typeof renderer.snapshotDataUrl === "function") renderer.draw();
    else {
      renderer.width = previousWidth; renderer.height = previousHeight;
      renderer.resize(); renderer.draw();
    }
  }
}

function snapshotChart(renderer, height = 700, product = null, visualCells = null) {
  if (!renderer?.data || !globalThis.echarts) return null;
  const layout = visualCells
    ? talkGrid({panelCount: visualCells})
    : (product ? talkGrid(product) : {width: 1200, height});
  const previousColumns = renderer.data.columns;
  const previousExportMode = renderer.exportMode;
  if (product) renderer.data.columns = layout.columns;
  renderer.exportMode = true;
  const staging = document.createElement("div");
  staging.style.cssText = `position:fixed;left:-20000px;top:0;width:${layout.width}px;height:${layout.height}px;background:white`;
  document.body.appendChild(staging);
  const originalHost = renderer.host;
  let chart = null;
  try {
    renderer.host = staging;
    const option = renderer.option();
    renderer.host = originalHost;
    chart = globalThis.echarts.init(staging, null, {renderer: "canvas"});
    chart.setOption(option, {notMerge: true, lazyUpdate: false});
    return chart.getDataURL({type: "png", pixelRatio: 2, backgroundColor: "#ffffff"});
  } finally {
    renderer.host = originalHost;
    renderer.data.columns = previousColumns;
    renderer.exportMode = previousExportMode;
    chart?.dispose(); staging.remove();
  }
}

function setDashboardView(view, save = true) {
  const allowed = new Set([
    "trajectory", "occupancy", "direction", "polar", "compare",
    "roi", "windows", "heading", "metrics", "statistics", "transitions", "diagnostics",
  ]);
  const previousView = currentView;
  currentView = allowed.has(view) ? view : "trajectory";
  polarRenderer.setRadialZoomEnabled?.(currentView === "polar");
  const spatial = new Set(["trajectory", "occupancy", "direction", "polar", "transitions", "compare"]);
  const exploreViews = new Set(spatial);
  if (exploreViews.has(currentView)) currentLens = currentView;
  byId("explore-section").dataset.lens = currentLens;
  for (const button of document.querySelectorAll("[data-view-button]")) {
    const selected = spatial.has(currentView) ? "trajectory" : currentView;
    button.classList.toggle("active", button.dataset.viewButton === selected);
  }
  for (const button of document.querySelectorAll("[data-layer-button]")) {
    button.classList.toggle("active", button.dataset.layerButton === currentLens);
    button.setAttribute("aria-selected", String(button.dataset.layerButton === currentLens));
  }
  const railView = spatial.has(currentView) ? "trajectory" : currentView;
  const activeButton = document.querySelector(`[data-view-button="${railView}"]`);
  activeButton?.scrollIntoView({behavior: "smooth", block: "nearest", inline: "center"});
  const viewMeta = {
    trajectory: ["Paths", "Spatial trajectories"], occupancy: ["Occupancy", "Spatial density"],
    direction: ["Flow", "Local direction field"], polar: ["Polar", "Trial resultants"],
    roi: ["Targets", "ROI outcomes"], heading: ["Heading", "Local trial time"],
    windows: ["Windows", "Spatial subset comparison"],
    metrics: ["Metrics", "Exact segment summaries"], statistics: ["Statistics", "Adjusted inference"],
    transitions: ["Spatial · Transitions", "Cell-entry outcomes"], diagnostics: ["Diagnostics", "Load-time distributions"],
    compare: ["Compare 2×2", "Linked spatial views"],
  }[currentView] || ["Paths", "Spatial trajectories"];
  byId("view-title").textContent = viewMeta[0]; byId("view-subtitle").textContent = viewMeta[1];
  const activeSection = exploreViews.has(currentView) ? "explore-section" : `${currentView}-section`;
  const order = [...document.querySelectorAll("[data-view-button]")].map(button => button.dataset.viewButton);
  const previousRail = spatial.has(previousView) ? "trajectory" : previousView;
  const direction = order.indexOf(railView) >= order.indexOf(previousRail) ? "nav-forward" : "nav-back";
  for (const section of document.querySelectorAll(".plot-section")) {
    section.classList.toggle("active-section", section.id === activeSection);
    section.classList.remove("nav-forward", "nav-back");
    if (section.id === activeSection && previousView !== currentView) section.classList.add(direction);
  }
  const productForView = {
    trajectory: "trajectory", occupancy: "heatmap", direction: "direction",
    polar: "polar", transitions: "transition", roi: "roi", windows: "windows",
    heading: "heading", metrics: "metrics", statistics: "statistics",
    diagnostics: "diagnostics",
  }[currentView];
  if (datasetHeader && (currentView === "compare" || previousView === "compare"
      || !products[productForView])) scheduleCompute(scopeForCurrentView(), 0);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    updatePanelGridSizing();
    const shown = currentView === "compare"
      ? new Set(["trajectory", "occupancy", "direction", "polar"])
      : new Set([currentView]);
    if (shown.has("trajectory")) { trajectoryRenderer.resize(); trajectoryRenderer.draw(); }
    if (shown.has("occupancy")) { heatmapRenderer.resize(); heatmapRenderer.draw(); }
    if (shown.has("direction")) { directionRenderer.resize(); directionRenderer.draw(); }
    if (shown.has("polar")) { polarRenderer.chart.resize({animation: {duration: 0}}); polarRenderer.draw(); }
    if (shown.has("heading")) { headingRenderer.chart.resize({animation: {duration: 0}}); headingRenderer.draw(); }
    if (shown.has("metrics")) { metricsRenderer.chart.resize({animation: {duration: 0}}); metricsRenderer.draw(); }
    if (shown.has("transitions")) { transitionRenderer.resize(); transitionRenderer.draw(); }
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
  const exports = {
    trajectory: {product: products.trajectory, capture: () => snapshotSpatialRenderer(trajectoryRenderer, products.trajectory)},
    occupancy: {product: products.heatmap, capture: () => snapshotSpatialRenderer(heatmapRenderer, products.heatmap)},
    direction: {product: products.direction, capture: () => snapshotSpatialRenderer(directionRenderer, products.direction)},
    transitions: {product: products.transition, capture: () => snapshotSpatialRenderer(transitionRenderer, products.transition)},
    polar: {product: products.polar, capture: () => snapshotChart(polarRenderer, 700, products.polar)},
    heading: {product: products.heading, capture: () => snapshotChart(headingRenderer, 700, products.heading)},
    roi: {product: {panelCount: 4}, capture: () => snapshotChart(roiRenderer, 700, null, 4)},
    metrics: {product: {panelCount: 4}, capture: () => snapshotChart(metricsRenderer, 700, null, 4)},
    diagnostics: {product: {panelCount: 1}, capture: () => snapshotChart(velocityHistogram, 430, null, 1)},
  };
  setStatus("working", "Rendering talk-ready PNG", "Using a fixed two-column grid with 1600 × 900 output pixels per panel.");
  try {
    const spec = exports[lens];
    const url = spec?.capture();
    if (!url) throw new Error("Open a rendered plot before exporting it.");
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `daari-deepa-${lens}-${new Date().toISOString().slice(0, 10)}.png`;
    anchor.click();
    const layout = talkGrid(spec.product || {panelCount: 1});
    setStatus("ready", "Talk-ready PNG exported", `${layout.panelCount} panel${layout.panelCount === 1 ? "" : "s"} · fixed 2-column sheet · 1600 × 900 pixels per panel.`);
  } catch (error) {
    setStatus("error", "PNG export failed", error.message || String(error));
  }
}

const recipeVisualIds = [
  "trajectory-width", "trajectory-opacity", "heat-metric", "heat-scale",
  "heat-range-mode", "heat-cmin", "heat-cmax", "flow-metric",
  "flow-range-mode", "flow-cmin", "flow-cmax", "particle-rate",
  "trail-length", "flow-speed", "flow-variability", "flow-color-mode",
  "flow-velocity-mode", "polar-mode", "transition-split", "trial-fraction",
  "transition-outcome", "transition-display", "transition-support",
  "transition-path-width", "transition-path-opacity",
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
    targetFrame: {
      convention: "Unity left-handed X/Z; negative X is left, positive X is right",
      mirroredPoolTransform: "X → -X; orientation and movement heading → -angle; Z and time unchanged",
      configs: datasetHeader?.configPresentation || {},
    },
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
    if (!byId(`filter-${key}`)) continue;
    let selected = Array.isArray(filterCodes[key]) ? filterCodes[key].map(Number) : [];
    const labels = recipe.filtersByLabel?.[key];
    if (Array.isArray(labels)) selected = labels.map(label => datasetHeader.categories[key].indexOf(label)).filter(code => code >= 0);
    setFilterSelection(key, selected);
  }
  const rangeIds = {
    trial:["trial-min","trial-max"], step:["step-min","step-max"],
    replicate:["replicate-min","replicate-max"], time:["time-min","time-max"],
    resultant:["resultant-min","resultant-max"], peak:["peak-min","peak-max"],
    displacement:["disp-min","disp-max"], distance:["distance-min","distance-max"],
  };
  for (const [key, ids] of Object.entries(rangeIds)) {
    const values = state.ranges?.[key]; if (!Array.isArray(values)) continue;
    const integer = key === "trial" || key === "step" || key === "replicate";
    byId(ids[0]).value = displayRangeBound(values[0], integer, false);
    byId(ids[1]).value = displayRangeBound(values[1], integer, true);
    const control = rangeControls.get(key);
    if (control) { control.low.value = values[0]; control.high.value = values[1]; control.redraw(); }
  }
  const valueIds = {
    jumpThreshold:"jump-threshold", jumpBufferMs:"jump-buffer",
    minDisplacement:"min-displacement", edgeTrim:"edge-trim", groupBy:"group-by",
    panelColumns:"panel-columns", colorBy:"color-by", pointBudget:"point-budget",
    overviewGrouping:"overview-grouping",
    walkThreshold:"walk-threshold", binSize:"bin-size", boundPercent:"bound-percent",
    angleSource:"angle-source", statsUnit:"stats-unit", polarValidMin:"polar-valid-min",
    polarMode:"polar-mode",
    roiReach:"roi-reach", ringMatch:"ring-match", playbackPercentile:"playback-cap",
    headingMode:"heading-mode", headingBin:"heading-bin", headingSectors:"heading-sectors",
    transitionAxis:"transition-axis",
  };
  for (const [key, id] of Object.entries(valueIds)) if (state[key] != null) byId(id).value = state[key];
  syncAngleSourceControls(byId("angle-source").value);
  for (const [key, id] of Object.entries({movingOnly:"moving-only",mirrorPool:"mirror-pool",roiEntered:"roi-entered",roiTrim:"roi-trim",ringEnabled:"ring-enabled",ringContext:"ring-context",windowsVisible:"window-show",showMarginals:"show-marginals"})) {
    if (state[key] != null && !(id === "mirror-pool" && byId(id).disabled)) byId(id).checked = !!state[key];
  }
  if (Array.isArray(state.polarR)) { byId("polar-r-min").value = state.polarR[0]; byId("polar-r-max").value = state.polarR[1]; }
  if (state.labels && typeof state.labels === "object") {
    displayNames = structuredClone(state.labels);
    for (const key of ["config", "scene", "vr", "fly", "folder"]) {
      const choices = filterChoices(key);
      for (let index = 0; index < choices.length; index += 1) {
        const caption = choices[index].closest("label")?.querySelector("span[data-code]");
        if (caption) caption.textContent = displayNames[key]?.[index] || datasetHeader.categories[key][index];
      }
    }
  }
  panelOrders = state.panelOrders && typeof state.panelOrders === "object" ? structuredClone(state.panelOrders) : panelOrders;
  if (Array.isArray(state.mirrorRules)) mirrorRules = structuredClone(state.mirrorRules);
  if (Array.isArray(state.rings)) rings = state.rings.map(ring => ({x:Number(ring.x)||0,z:Number(ring.z)||0,r:Math.max(.01,Number(ring.r)||.01)}));
  if (Array.isArray(state.windows)) for (const [index, window] of state.windows.slice(0, 2).entries()) {
    const key = index ? "b" : "a";
    for (const bound of ["xmin", "xmax", "zmin", "zmax"]) if (window?.[bound] != null) byId(`window-${key}-${bound}`).value = window[bound];
  }
  sampleSeed = Number(state.sampleSeed) || 0;
  for (const [id, value] of Object.entries(recipe.visuals || {})) if (byId(id) && value != null) byId(id).value = value;
  setDashboardView(state.view || state.lens || "trajectory", false);
  renderRingControls(); renderMirrorPairs(); renderDisplayLabels(); renderPanelOrder(panelOrders[byId("group-by").value] || []);
  trajectoryRenderer.setLineStyle(numberValue("trajectory-width", 1.1), numberValue("trajectory-opacity", .5));
  trajectoryRenderer.setObservationWindows(byId("window-show").checked ? observationWindows() : []);
  for (const renderer of spatialRenderers) renderer.setMarginalsVisible(byId("show-marginals").checked);
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
  downloadBlob(blob, `daari-deepa-view-${new Date().toISOString().slice(0, 10)}.json`);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>\"]/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[character]));
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = filename; anchor.hidden = true;
  document.body.appendChild(anchor);
  anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

async function exportNativeReport() {
  if (!datasetHeader || reportRequestId != null) return;
  setMirrorGuide(null);
  const exportButton = byId("export-button");
  exportButton.disabled = true; exportButton.textContent = "Preparing report…";
  setStatus("working", "Preparing native report", "Capturing the current linked views and analytical tables locally in your browser.");
  const coreProducts = ["trajectory", "heatmap", "direction", "polar", "roi", "heading", "metrics", "diagnostics"];
  if (coreProducts.some(product => !products[product])) {
    reportRequestId = queueCompute("full", {quiet: true});
    return;
  }
  await captureNativeReport();
}

async function captureNativeReport() {
  const exportButton = byId("export-button");
  await new Promise(resolve => requestAnimationFrame(() => resolve()));
  try {
  const trajectoryImage = snapshotSpatialRenderer(trajectoryRenderer, products.trajectory);
  if (!trajectoryImage) throw new Error("The trajectory framebuffer was empty; the report was not downloaded.");
  const sections = [
    ["Trajectory field", () => trajectoryImage],
    ["Occupancy", () => snapshotSpatialRenderer(heatmapRenderer, products.heatmap)],
    ["Local direction", () => snapshotSpatialRenderer(directionRenderer, products.direction)],
    ["Polar direction", () => snapshotChart(polarRenderer, 700, products.polar)],
    ["ROI outcomes", () => snapshotChart(roiRenderer, 700, null, 4)],
    ["Heading over time", () => snapshotChart(headingRenderer, 700, products.heading)],
    ["Trial metrics", () => snapshotChart(metricsRenderer, 700, null, 4)],
    ["Velocity", () => snapshotChart(velocityHistogram, 430, null, 1)],
    ["Displacement", () => snapshotChart(displacementHistogram, 430, null, 1)],
    ["Transition probability", () => products.transition ? snapshotSpatialRenderer(transitionRenderer, products.transition) : null],
  ];
  const figures = sections.map(([title, capture]) => [title, capture()]).filter(([, image]) => image);
  const flowSnapshot = products.direction ? {
    nx: products.direction.nx, nz: products.direction.nz, x0: products.direction.x0,
    z0: products.direction.z0, bin: products.direction.bin,
    panelCount: products.direction.panelCount, panelNames: products.direction.panelNames,
    bounds: products.direction.bounds,
    angle: Array.from(products.direction.angle), strength: Array.from(products.direction.strength),
    abundance: Array.from(products.direction.abundance),
  } : null;
  const embeddedFlow = JSON.stringify(flowSnapshot).replaceAll("<", "\\u003c");
  const tableSections = [
    ["Observation windows", products.windows ? byId("windows-content").innerHTML : ""],
    ["Inferential statistics", products.statistics ? byId("statistics-content").innerHTML : ""],
  ].filter(([, content]) => content);
  const counts = datasetHeader.counts;
  const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Daari Deepa native report</title>
    <style>body{margin:24px auto;max-width:1840px;padding:0 24px;color:#18221f;background:#f4f1ea;font:15px system-ui}h1,h2{font-family:Georgia,serif;font-weight:500}header{border-bottom:1px solid #ccc;padding-bottom:16px}.figure{margin:24px 0;padding:16px;background:#fffdf8;border:1px solid #d9d5ca;border-radius:12px;overflow:hidden}.figure h2{margin:0 0 6px}.figure-stage{position:relative;overflow:hidden;min-height:280px;border:1px solid #e5e1d8;background:#fff;cursor:grab;touch-action:none}.figure-stage:active{cursor:grabbing}.figure img{display:block;width:100%;height:auto;image-rendering:auto;transform-origin:0 0;will-change:transform;user-select:none}.figure-hint{display:block;margin:0 0 9px;color:#6b7672}#flow-live{display:block;width:100%;height:auto;background:#fff;border:1px solid #e5e1d8}small{color:#66716d}.statistics-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.statistics-card{padding:12px;border:1px solid #ddd;border-radius:8px}table{width:100%;border-collapse:collapse}th,td{padding:5px;border-top:1px solid #eee;text-align:left;font-size:12px}@media print{body{max-width:none;margin:0;padding:0;background:#fff}.figure{break-before:page;border:0;border-radius:0;margin:0;padding:12mm;overflow:visible}.figure-stage{min-height:0;overflow:visible;border:0}.figure img{max-width:100%;transform:none!important;page-break-inside:avoid}#flow-live{display:none}}</style>
    <header><h1>Daari Deepa — native analysis report</h1><p>${escapeHtml(sourceInput.value)}</p><small>${formatCount(counts.retainedRows)} retained of ${formatCount(counts.sourceRows)} source rows · ${formatCount(counts.segments)} segments · ${formatCount(counts.animals)} animals</small></header>
    ${figures.map(([title, image]) => `<section class="figure"><h2>${escapeHtml(title)}</h2><small class="figure-hint">Drag to pan · wheel to zoom · double-click to reset</small><div class="figure-stage"><img draggable="false" src="${image}" alt="${escapeHtml(title)}"></div>${title === "Local direction" && flowSnapshot ? '<canvas id="flow-live" aria-label="Animated local direction field"></canvas>' : ""}</section>`).join("")}
    ${tableSections.map(([title, content]) => `<section class="figure"><h2>${escapeHtml(title)}</h2>${content}</section>`).join("")}
    <footer><small>Exported ${escapeHtml(new Date().toISOString())}. Images retain local pan and zoom; the flow field is animated from the exported binned vectors. No data leaves this file.</small></footer>
    <script>const FLOW=${embeddedFlow};document.querySelectorAll('.figure-stage').forEach(stage=>{const image=stage.querySelector('img');let scale=1,x=0,y=0,drag=null;const draw=()=>image.style.transform='translate('+x+'px,'+y+'px) scale('+scale+')';stage.addEventListener('wheel',event=>{event.preventDefault();const rect=stage.getBoundingClientRect(),next=Math.max(1,Math.min(8,scale*Math.exp(-event.deltaY*.001)));x=event.clientX-rect.left-(event.clientX-rect.left-x)*next/scale;y=event.clientY-rect.top-(event.clientY-rect.top-y)*next/scale;scale=next;draw()},{passive:false});stage.addEventListener('pointerdown',event=>{drag={x:event.clientX,y:event.clientY,ox:x,oy:y};stage.setPointerCapture(event.pointerId)});stage.addEventListener('pointermove',event=>{if(!drag)return;x=drag.ox+event.clientX-drag.x;y=drag.oy+event.clientY-drag.y;draw()});stage.addEventListener('pointerup',()=>drag=null);stage.addEventListener('dblclick',()=>{scale=1;x=0;y=0;draw()})});if(FLOW){const canvas=document.getElementById('flow-live'),cols=Math.min(2,FLOW.panelCount),rows=Math.ceil(FLOW.panelCount/cols),cellW=800,cellH=450;canvas.width=cols*cellW;canvas.height=rows*cellH;const ctx=canvas.getContext('2d'),cells=FLOW.nx*FLOW.nz,active=[];let max=1;for(const value of FLOW.abundance)max=Math.max(max,value||0);for(let i=0;i<FLOW.angle.length;i++){if(FLOW.angle[i]==null||!(FLOW.abundance[i]>0))continue;const copies=1+Math.floor(3*Math.sqrt(FLOW.abundance[i]/max));for(let n=0;n<copies;n++)active.push(i)}const particles=Array.from({length:Math.min(3500,active.length*2)},(_,i)=>({cell:active[i%active.length],phase:(i*.61803398875)%1,jitter:((i*16807)%997)/997-.5}));const bounds=FLOW.bounds,span=Math.max(bounds.xmax-bounds.xmin,bounds.zmax-bounds.zmin)||1,pad=34,plotW=cellW-pad*2,plotH=cellH-pad*2;function frame(time){ctx.fillStyle='rgba(255,255,255,.18)';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.lineCap='round';for(let panel=0;panel<FLOW.panelCount;panel++){const left=(panel%cols)*cellW+pad,top=Math.floor(panel/cols)*cellH+pad;ctx.fillStyle='#26332f';ctx.font='700 15px system-ui';ctx.fillText(FLOW.panelNames[panel]||'All data',left,top-10);ctx.strokeStyle='rgba(60,80,74,.12)';ctx.strokeRect(left,top,plotW,plotH)}for(const p of particles){const panel=Math.floor(p.cell/cells),local=p.cell%cells,ix=local%FLOW.nx,iz=Math.floor(local/FLOW.nx),angle=FLOW.angle[p.cell]*Math.PI/180,strength=FLOW.strength[p.cell]||0,age=(time*.00024+p.phase)%1,cx=FLOW.x0+(ix+.5)*FLOW.bin,cz=FLOW.z0+(iz+.5)*FLOW.bin,length=FLOW.bin*(.3+1.4*strength),dx=Math.sin(angle)*length,dz=Math.cos(angle)*length,left=(panel%cols)*cellW+pad,top=Math.floor(panel/cols)*cellH+pad,px=value=>left+(value-bounds.xmin)/span*plotW,py=value=>top+plotH-(value-bounds.zmin)/span*plotH,x0=px(cx+dx*(age-.18)+p.jitter*FLOW.bin*.35),y0=py(cz+dz*(age-.18)+p.jitter*FLOW.bin*.35),x1=px(cx+dx*age+p.jitter*FLOW.bin*.35),y1=py(cz+dz*age+p.jitter*FLOW.bin*.35);ctx.strokeStyle='oklch(66% .135 '+(((FLOW.angle[p.cell]%360)+360)%360)+')';ctx.globalAlpha=.18+.55*strength;ctx.lineWidth=.7+1.2*Math.sqrt(FLOW.abundance[p.cell]/max);ctx.beginPath();ctx.moveTo(x0,y0);ctx.lineTo(x1,y1);ctx.stroke()}ctx.globalAlpha=1;requestAnimationFrame(frame)}if(active.length)requestAnimationFrame(frame)}</script>`;
  const blob = new Blob([html], {type: "text/html"});
  downloadBlob(blob, `daari-deepa-native-${new Date().toISOString().slice(0,10)}.html`);
  setStatus("ready", "Report exported", `${figures.length} figures embedded locally, including ${formatCount(trajectoryRenderer.lastSnapshotInk || 0)} sampled trajectory pixels.`);
  } catch (error) {
    setStatus("error", "Report export failed", error.message || String(error));
  } finally {
    exportButton.disabled = false; exportButton.textContent = "Export native report";
  }
}

function updateFraction() {
  const value = numberValue("trial-fraction", 100);
  byId("fraction-output").textContent = `${value}%`;
  trajectoryRenderer.setFraction(value / 100); polarRenderer.setFraction(value / 100); headingRenderer.setFraction(value / 100);
}

function renderRingControls() {
  activeRing = rings.length ? Math.max(0, Math.min(rings.length - 1, activeRing)) : 0;
  for (const renderer of spatialRenderers) renderer.activeRing = activeRing;
  const select = byId("ring-active"); select.replaceChildren();
  rings.forEach((_, index) => select.add(new Option(`Ring ${index + 1}`, String(index))));
  if (!rings.length) select.add(new Option("No rings", ""));
  select.value = String(activeRing);
  updateRingControlValues();
  byId("ring-delete").disabled = !rings.length;
  byId("ring-quick-delete").disabled = !rings.length;
  byId("curtain-state").textContent = byId("ring-enabled").checked && rings.length
    ? `${rings.length} curtain ${rings.length === 1 ? "ring" : "rings"}` : "Curtain off";
  updateCurtainLabels();
}

function updateCurtainLabels() {
  const enabled = byId("ring-enabled").checked && rings.length;
  byId("curtain-toggle").innerHTML = `<span class="curtain-icon" aria-hidden="true"></span>Curtain${enabled ? ` · ${rings.length}` : ""}`;
  const center = byId("curtain-center");
  center.classList.toggle("active", !!enabled);
  center.querySelector("b").textContent = enabled ? `${rings.length} ring${rings.length === 1 ? "" : "s"}` : "Curtain";
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
  const orderKey = key === "config" && byId("mirror-pool").checked ? "mirrorConfig" : key;
  const names = orderKey === "mirrorConfig"
    ? (datasetHeader?.displayCategories?.mirrorConfig || datasetHeader?.categories?.mirrorConfig || [])
    : (displayNames[key] || datasetHeader?.categories?.[key] || []);
  const commit = next => {
    panelOrders = {...panelOrders, [orderKey]: next};
    renderPanelOrder(next);
    scheduleDataUpdate(30);
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
  const enabled = byId("ring-enabled").checked && rings.length > 0;
  const stats = trajectoryRenderer.setRingObserver(
    enabled, rings, byId("ring-match").value,
    byId("ring-context").checked,
  );
  const activeRenderer = {
    occupancy: heatmapRenderer, direction: directionRenderer, transitions: transitionRenderer,
  }[currentView];
  for (const renderer of [heatmapRenderer, directionRenderer, transitionRenderer]) {
    renderer.setRings(rings, enabled, byId("ring-match").value,
      currentView === "compare" || renderer === activeRenderer);
  }
  byId("curtain-state").textContent = enabled
    ? `${rings.length} curtain ${rings.length === 1 ? "ring" : "rings"}` : "Curtain off";
  updateCurtainLabels();
  updateTrajectorySummary(stats);
  if (save) persistState();
  return stats;
}

let curtainPreviewTimer = null;
let curtainPreviewAt = 0;
function curtainPreviewScope() {
  return {
    occupancy: "heatmap", direction: "direction", transitions: "transition",
    polar: "polar", heading: "heading", compare: "overviewPreview",
  }[currentView] || null;
}
function scheduleCurtainPreview() {
  const scope = curtainPreviewScope();
  if (!scope || currentView === "trajectory" || curtainPreviewTimer || workerBusy) return;
  const panels = Math.max(1, Number(lastSummary?.panels) || 1);
  const cadence = panels > 8 ? 1200 : (panels > 4 ? 750 : 420);
  const wait = Math.max(0, cadence - (performance.now() - curtainPreviewAt));
  curtainPreviewTimer = setTimeout(() => {
    curtainPreviewTimer = null; curtainPreviewAt = performance.now();
    scheduleCompute(scope, 0, {quiet: true});
  }, wait);
}

function scheduleLocalRingObserver(final = false, source = null) {
  if (final && ringFrame) cancelAnimationFrame(ringFrame);
  if (final) {
    if (curtainPreviewTimer) clearTimeout(curtainPreviewTimer);
    curtainPreviewTimer = null;
    ringFrame = null;
    applyLocalRingObserver(true);
    transitionSelectionActive = false;
    transitionRenderer.clearTrajectoryOverlay();
    // Paths remain a local WebGL observer. Every analytical product is marked
    // stale so polar, heading, metrics and inference cannot silently show the
    // pre-curtain population; only the currently open view is rebuilt.
    invalidateCurtainProducts();
    const scope = curtainPreviewScope();
    if (scope) scheduleCompute(scope, currentView === "compare" ? 220 : 120);
    return;
  }
  if (ringFrame) return;
  ringFrame = requestAnimationFrame(() => {
    ringFrame = null;
    const enabled = byId("ring-enabled").checked && rings.length > 0;
    if (currentView === "trajectory" || currentView === "compare" || source === trajectoryRenderer) {
      const stats = trajectoryRenderer.setRingObserver(
        enabled, rings, byId("ring-match").value, byId("ring-context").checked,
      );
      updateTrajectorySummary(stats);
    }
    for (const renderer of [heatmapRenderer, directionRenderer, transitionRenderer]) {
      if (renderer !== source) renderer.setRings(rings, enabled, byId("ring-match").value, false);
    }
    if (currentView !== "trajectory") scheduleCurtainPreview();
  });
}

function updateActiveRing() {
  if (!rings.length) rings.push({x: 0, z: 0, r: 3});
  rings[activeRing] = {
    x: numberValue("ring-x"), z: numberValue("ring-z"),
    r: Math.max(.01, numberValue("ring-radius", 3)),
  };
  byId("ring-radius-slider").value = rings[activeRing].r;
  scheduleLocalRingObserver(false);
}

function updatePlaybackTime() {
  const value = numberValue("time-scrubber", 0);
  const single = byId("playback-scope").value === "single";
  trajectoryRenderer.setPlaybackSegment(single ? Number(byId("playback-trial").value) : -1);
  trajectoryRenderer.setTime(playbackActive ? value : Number.POSITIVE_INFINITY);
  byId("time-output").textContent = playbackActive
    ? `${formatNumber(value, 1)} / ${formatNumber(Number(byId("time-scrubber").max), 1)} s`
    : (single ? "full segment" : "all time");
}

function playbackTick(now) {
  if (!playbackPlaying) { playbackFrame = null; return; }
  const maxTime = Number(byId("time-scrubber").max) || 1;
  const elapsed = playbackLast ? (now - playbackLast) / 1000 : 0; playbackLast = now;
  let value = numberValue("time-scrubber") + elapsed * numberValue("playback-speed", 1);
  if (value >= maxTime) value = 0;
  byId("time-scrubber").value = value; updatePlaybackTime();
  playbackFrame = requestAnimationFrame(playbackTick);
}

function stopPlayback(reset = false) {
  if (playbackFrame) cancelAnimationFrame(playbackFrame);
  playbackFrame = null; playbackLast = 0;
  playbackPlaying = false;
  if (reset) playbackActive = false;
  if (byId("play-button")) {
    byId("play-button").textContent = "▶ Play";
    byId("play-button").setAttribute("aria-pressed", "false");
  }
  updatePlaybackTime();
}

byId("source-form").addEventListener("submit", event => { event.preventDefault(); loadDataset(sourceInput.value); });
byId("controls-toggle").addEventListener("click", () => {
  const collapsed = shell.classList.toggle("controls-collapsed");
  if (collapsed) setMirrorGuide(null);
  byId("controls-toggle").setAttribute("aria-expanded", String(!collapsed));
});
byId("sidebar-close").addEventListener("click", () => { setMirrorGuide(null); byId("controls-toggle").click(); });
byId("mirror-pair-list").addEventListener("focusout", () => setTimeout(() => {
  if (!byId("mirror-pair-list").contains(document.activeElement)) setMirrorGuide(null);
}, 0));

function setCurtainPalette(open) {
  const palette = byId("curtain-palette");
  palette.hidden = !open;
  byId("curtain-toggle").setAttribute("aria-expanded", String(open));
  if (open) {
    byId("source-popover").open = false;
    for (const details of document.querySelectorAll(".context-settings[open]")) details.open = false;
    renderRingControls();
  }
}
byId("curtain-toggle").addEventListener("click", () => setCurtainPalette(byId("curtain-palette").hidden));
byId("curtain-center").addEventListener("click", () => setCurtainPalette(true));
byId("curtain-close").addEventListener("click", () => setCurtainPalette(false));
applyButton.addEventListener("click", () => scheduleDataUpdate(0));
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
for (const button of document.querySelectorAll("[data-layer-button]")) {
  button.addEventListener("click", () => setDashboardView(button.dataset.layerButton));
}
function stepDashboardView(delta) {
  const views = [...document.querySelectorAll("[data-view-button]")].map(button => button.dataset.viewButton);
  const currentRailView = ["trajectory", "occupancy", "direction", "polar", "transitions", "compare"].includes(currentView)
    ? "trajectory" : currentView;
  const index = Math.max(0, views.indexOf(currentRailView));
  setDashboardView(views[(index + delta + views.length) % views.length]);
}
byId("view-prev").addEventListener("click", () => stepDashboardView(-1));
byId("view-next").addEventListener("click", () => stepDashboardView(1));
byId("overview-grouping").addEventListener("change", () => scheduleCompute("overview", 0));
let railWheelLocked = false;
byId("workspace").addEventListener("wheel", event => {
  const onRail = event.target.closest(".view-rail-track, .rail-context");
  if (!onRail || Math.abs(event.deltaY) + Math.abs(event.deltaX) < 18) return;
  event.preventDefault();
  if (railWheelLocked) return;
  railWheelLocked = true;
  stepDashboardView((Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY) > 0 ? 1 : -1);
  setTimeout(() => { railWheelLocked = false; }, 320);
}, {passive: false});
let panelResizeFrame = null;
window.addEventListener("resize", () => {
  cancelAnimationFrame(panelResizeFrame);
  panelResizeFrame = requestAnimationFrame(updatePanelGridSizing);
});
document.addEventListener("keydown", event => {
  if (event.target.closest("input, select, textarea") || event.metaKey || event.ctrlKey) return;
  const spatialViews = ["trajectory", "occupancy", "direction", "polar", "transitions", "compare"];
  if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && spatialViews.includes(currentView)) {
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const index = spatialViews.indexOf(currentView);
    setDashboardView(spatialViews[(index + delta + spatialViews.length) % spatialViews.length]);
  } else if (event.key === "ArrowDown" || (event.key === "ArrowRight" && !spatialViews.includes(currentView))) stepDashboardView(1);
  else if (event.key === "ArrowUp" || (event.key === "ArrowLeft" && !spatialViews.includes(currentView))) stepDashboardView(-1);
  else if (event.key.toLowerCase() === "c") setCurtainPalette(byId("curtain-palette").hidden);
});
for (const details of document.querySelectorAll(".context-settings")) details.addEventListener("toggle", () => {
  if (!details.open) return;
  setCurtainPalette(false);
  for (const other of document.querySelectorAll(".context-settings[open]")) if (other !== details) other.open = false;
});
byId("source-popover").addEventListener("toggle", () => {
  if (!byId("source-popover").open) return;
  setCurtainPalette(false);
  for (const details of document.querySelectorAll(".context-settings[open]")) details.open = false;
});
byId("animals-all").addEventListener("click", () => {
  animalVisibility.fill(true); renderAnimalVisibility(); applyAnimalVisibility();
});
byId("animals-none").addEventListener("click", () => {
  animalVisibility.fill(false); renderAnimalVisibility(); applyAnimalVisibility();
});
byId("animals-invert").addEventListener("click", () => {
  animalVisibility = animalVisibility.map(value => value === false);
  renderAnimalVisibility(); applyAnimalVisibility();
});
byId("trial-fraction").addEventListener("input", updateFraction);
byId("resample-button").addEventListener("click", () => {
  sampleSeed += 1;
  delete products.trajectory; delete products.polar; delete products.heading;
  const scope = scopeForCurrentView();
  if (["trajectory", "polar", "heading", "overview"].includes(scope)) scheduleCompute(scope);
});
byId("roi-show").addEventListener("change", () => {
  for (const renderer of spatialRenderers) renderer.setRoisVisible(byId("roi-show").checked);
});
byId("ring-enabled").addEventListener("change", () => scheduleLocalRingObserver(true));
byId("ring-context").addEventListener("change", () => applyLocalRingObserver(true));
byId("ring-match").addEventListener("change", () => scheduleLocalRingObserver(true));
byId("ring-active").addEventListener("change", () => { activeRing = Number(byId("ring-active").value) || 0; renderRingControls(); });
for (const id of ["ring-x", "ring-z", "ring-radius"]) {
  byId(id).addEventListener("input", updateActiveRing);
  byId(id).addEventListener("change", () => scheduleLocalRingObserver(true));
  byId(id).addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); scheduleLocalRingObserver(true); byId(id).blur(); }
  });
}
byId("ring-radius-slider").addEventListener("input", () => {
  byId("ring-radius").value = byId("ring-radius-slider").value;
  updateActiveRing();
});
byId("ring-radius-slider").addEventListener("change", () => scheduleLocalRingObserver(true));
function addRing() {
  const current = rings[activeRing] || {x:0,z:0,r:3}; rings.push({...current, x: current.x + current.r * .5}); activeRing = rings.length - 1;
  byId("ring-enabled").checked = true;
  renderRingControls(); scheduleLocalRingObserver(true);
}
byId("ring-add").addEventListener("click", addRing);
byId("ring-quick-add").addEventListener("click", addRing);
byId("ring-delete").addEventListener("click", () => deleteRing(activeRing));
byId("ring-quick-delete").addEventListener("click", () => deleteRing(activeRing));
for (const id of ["heat-metric", "heat-scale", "heat-range-mode", "heat-cmin", "heat-cmax"]) {
  byId(id).addEventListener(id.startsWith("heat-c") ? "input" : "change", () => {
    applyHeatmapVisuals(); persistState();
  });
}
for (const id of ["flow-metric", "flow-color-mode", "flow-velocity-mode", "flow-range-mode", "flow-cmin", "flow-cmax",
  "particle-rate", "trail-length", "flow-speed", "flow-variability"]) {
  const event = ["flow-metric", "flow-color-mode", "flow-velocity-mode", "flow-range-mode"].includes(id) ? "change" : "input";
  byId(id).addEventListener(event, () => { applyDirectionVisuals(); persistState(); });
}
for (const id of ["trajectory-width", "trajectory-opacity"]) {
  byId(id).addEventListener("input", () => {
    trajectoryRenderer.setLineStyle(numberValue("trajectory-width", 1.1), numberValue("trajectory-opacity", .5));
    persistState();
  });
}
for (const id of ["transition-outcome", "transition-display", "transition-support"]) {
  byId(id).addEventListener(id === "transition-support" ? "input" : "change", applyTransitionVisuals);
}
for (const id of ["transition-path-width", "transition-path-opacity"]) {
  byId(id).addEventListener("input", () => {
    transitionRenderer.setOverlayStyle(
      numberValue("transition-path-width", 1.4),
      numberValue("transition-path-opacity", .42),
    );
    persistState();
  });
}
byId("transition-split").addEventListener("change", () => scheduleCompute("transition", 20));
byId("transition-axis").addEventListener("change", () => {
  byId("transition-split-label").textContent = `Split ${byId("transition-axis").value.toUpperCase()}`;
  scheduleCompute("transition", 20);
});
byId("window-show").addEventListener("change", () => {
  trajectoryRenderer.setObservationWindows(byId("window-show").checked ? observationWindows() : []);
  persistState();
});
byId("spatial-grid").addEventListener("change", () => {
  for (const renderer of spatialRenderers) renderer.setGridVisible(byId("spatial-grid").checked);
});
byId("show-marginals").addEventListener("change", () => {
  for (const renderer of spatialRenderers) renderer.setMarginalsVisible(byId("show-marginals").checked);
  persistState();
});
byId("mirror-pair-add").addEventListener("click", () => {
  const count = datasetHeader?.categories?.config?.length || 0;
  if (count < 2) return;
  const used = new Set(mirrorRules.flatMap(rule => [Number(rule.reference), Number(rule.reflected)]));
  const available = Array.from({length: count}, (_, code) => code).filter(code => !used.has(code));
  mirrorRules.push({reference: available[0] ?? 0, reflected: available[1] ?? (available[0] === 0 ? 1 : 0), axis: "x", coordinate: 0});
  renderMirrorPairs(); setMirrorGuide(mirrorRules[mirrorRules.length - 1]);
  byId("mirror-pool").disabled = false;
  persistState();
});
byId("mirror-pair-clear").addEventListener("click", () => {
  mirrorRules = []; renderMirrorPairs(); setMirrorGuide(null);
  byId("mirror-pool").disabled = (datasetHeader?.categories?.config?.length || 0) < 2;
  scheduleDataUpdate(80);
});
byId("time-scrubber").addEventListener("input", () => {
  playbackActive = true; updatePlaybackTime();
});
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
  if (playbackPlaying) { stopPlayback(); return; }
  const maximum = Number(byId("time-scrubber").max) || 1;
  if (!playbackActive || numberValue("time-scrubber") >= maximum) byId("time-scrubber").value = 0;
  playbackActive = true; playbackPlaying = true;
  byId("play-button").textContent = "❚❚ Pause";
  byId("play-button").setAttribute("aria-pressed", "true");
  playbackLast = 0; updatePlaybackTime(); playbackFrame = requestAnimationFrame(playbackTick);
});

for (const id of ["angle-source", "polar-angle-source"]) {
  byId(id).addEventListener("change", event => syncAngleSourceControls(event.target.value));
}
syncAngleSourceControls(byId("angle-source").value);

for (const control of document.querySelectorAll("[data-scope]")) {
  control.addEventListener("change", () => {
    const scope = control.dataset.scope;
    if (scope === "layout") updateColumns();
    else if (scope === "windows") {
      trajectoryRenderer.setObservationWindows(byId("window-show").checked ? observationWindows() : []);
      delete products.windows;
      if (currentView === "windows") scheduleCompute("windows", 40);
      else armDeferredAnalysis();
    }
    else if (scope === "full" || scope === "movement") scheduleDataUpdate(scope === "full" ? 180 : 80);
    else if (scope === "spatial") {
      delete products.heatmap; delete products.direction; delete products.transition;
      if (["occupancy", "direction", "transitions", "compare"].includes(currentView)) scheduleCompute(scopeForCurrentView(), 80);
    } else {
      const dependencies = new Set(scopeProducts[scope] || []);
      for (const product of dependencies) delete products[product];
      const visibleScope = scopeForCurrentView();
      const visibleProducts = scopeProducts[visibleScope] || [];
      if (visibleProducts.some(product => dependencies.has(product))) scheduleCompute(visibleScope, 80);
      else armDeferredAnalysis();
    }
  });
}

async function readDroppedEntry(entry, prefix, paths) {
  if (entry.isFile) {
    if (/\.csv(?:\.gz)?$/i.test(entry.name)) {
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
      if (/\.csv(?:\.gz)?$/i.test(path)) paths.push({path, size: file.size});
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
