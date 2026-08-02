/* Retained analytical table and all expensive view preparation live here. */

const NEUTRAL_INDEX = 20;
const SEQUENTIAL_OFFSET = 24;
const CATEGORY_COLORS = 16;
let header = null;
let sourceBuffer = null;
let bodyOffset = 0;
let data = null;
let starts = null;
let ends = null;
let medianDt = .01;
let lastAnalysis = null;
let lastAnalysisKey = "";

function typedArray(dtype, buffer, offset, length) {
  const byteOffset = bodyOffset + offset;
  if (dtype === "<f4" || dtype === "=f4") return new Float32Array(buffer, byteOffset, length);
  if (dtype === "<f8" || dtype === "=f8") return new Float64Array(buffer, byteOffset, length);
  if (dtype === "<u4" || dtype === "=u4") return new Uint32Array(buffer, byteOffset, length);
  if (dtype === "<u2" || dtype === "=u2") return new Uint16Array(buffer, byteOffset, length);
  if (dtype === "|u1") return new Uint8Array(buffer, byteOffset, length);
  if (dtype === "|i1") return new Int8Array(buffer, byteOffset, length);
  throw new Error(`Unsupported native column dtype: ${dtype}`);
}

function unpack(headerValue, buffer) {
  const result = {};
  for (const [name, descriptor] of Object.entries(headerValue.arrays)) {
    result[name] = typedArray(descriptor.dtype, buffer, descriptor.offset, descriptor.length);
  }
  return result;
}

function initDataset(message) {
  header = message.header;
  sourceBuffer = message.buffer;
  bodyOffset = message.bodyOffset;
  data = unpack(header, sourceBuffer);
  const n = data.segment.length;
  const startList = [0];
  for (let i = 1; i < n; i += 1) if (data.segment[i] !== data.segment[i - 1]) startList.push(i);
  starts = Uint32Array.from(startList);
  ends = new Uint32Array(starts.length);
  for (let i = 0; i < starts.length; i += 1) ends[i] = i + 1 < starts.length ? starts[i + 1] : n;
  const dtSample = [];
  const stride = Math.max(1, Math.floor(n / 100000));
  for (let i = 1; i < n; i += stride) {
    if (data.segment[i] === data.segment[i - 1]) {
      const dt = data.time[i] - data.time[i - 1];
      if (dt > 0 && Number.isFinite(dt)) dtSample.push(dt);
    }
  }
  dtSample.sort((a, b) => a - b);
  medianDt = dtSample.length ? dtSample[Math.floor(dtSample.length / 2)] : .01;
  postMessage({type: "ready", counts: header.counts, medianDt});
}

function inRange(value, range) {
  if (!range) return true;
  return value >= range[0] && value <= range[1];
}

function setOrNull(values) {
  return values && values.length ? new Set(values.map(Number)) : null;
}

function analysisKey(state) {
  return JSON.stringify({
    filters: state.filters, ranges: state.ranges,
    jumpThreshold: state.jumpThreshold, jumpBufferMs: state.jumpBufferMs,
    minDisplacement: state.minDisplacement, edgeTrim: state.edgeTrim,
    roiReach: state.roiReach, roiEntered: state.roiEntered, roiTrim: state.roiTrim,
    groupBy: state.groupBy, labels: state.labels,
  });
}

function targetsForSegment(seg) {
  const config = header.categories.config[data.segmentConfig[seg]];
  return header.rois?.[config] || [];
}

function wrapAngle(value) {
  return ((value + 180) % 360 + 360) % 360 - 180;
}

function buildAnalysis(state) {
  const started = performance.now();
  const ns = starts.length;
  const segmentKeep = new Uint8Array(ns);
  segmentKeep.fill(1);
  const categoryFields = {
    config: data.segmentConfig, scene: data.segmentScene, vr: data.segmentVr,
    fly: data.segmentFly, folder: data.segmentFolder,
  };
  for (const [key, field] of Object.entries(categoryFields)) {
    const selected = setOrNull(state.filters?.[key]);
    if (!selected) continue;
    for (let seg = 0; seg < ns; seg += 1) if (!selected.has(field[seg])) segmentKeep[seg] = 0;
  }
  const r = state.ranges || {};
  for (let seg = 0; seg < ns; seg += 1) {
    if (!segmentKeep[seg]) continue;
    if (!inRange(data.segmentTrial[seg], r.trial)
      || !inRange(data.segmentStep[seg], r.step)
      || !inRange(data.segmentPeakSpeed[seg], r.peak)
      || !inRange(data.segmentDisplacement[seg], r.displacement)
      || !inRange(data.segmentDistance[seg], r.distance)) segmentKeep[seg] = 0;
  }

  const rowKeep = new Uint8Array(data.segment.length);
  for (let seg = 0; seg < ns; seg += 1) {
    if (segmentKeep[seg]) rowKeep.fill(1, starts[seg], ends[seg]);
  }

  const jump = Number(state.jumpThreshold) || 0;
  const buffer = Math.max(0, Number(state.jumpBufferMs) || 0) / 1000;
  if (jump > 0) {
    for (let seg = 0; seg < ns; seg += 1) {
      if (!segmentKeep[seg]) continue;
      let lastJump = -Infinity;
      for (let i = starts[seg]; i < ends[seg]; i += 1) {
        if (data.rawSpeed[i] > jump) lastJump = data.time[i];
        if (data.time[i] - lastJump <= buffer) rowKeep[i] = 0;
      }
      let nextJump = Infinity;
      for (let i = ends[seg] - 1; i >= starts[seg]; i -= 1) {
        if (data.rawSpeed[i] > jump) nextJump = data.time[i];
        if (nextJump - data.time[i] <= buffer) rowKeep[i] = 0;
      }
    }
  }

  const minDisplacement = Math.max(0, Number(state.minDisplacement) || 0);
  if (minDisplacement > 0) {
    for (let seg = 0; seg < ns; seg += 1) {
      if (!segmentKeep[seg]) continue;
      let first = -1, last = -1;
      for (let i = starts[seg]; i < ends[seg]; i += 1) if (rowKeep[i]) { if (first < 0) first = i; last = i; }
      if (first < 0 || Math.hypot(data.x[last] - data.x[first], data.z[last] - data.z[first]) < minDisplacement) {
        segmentKeep[seg] = 0; rowKeep.fill(0, starts[seg], ends[seg]);
      }
    }
  }

  const trim = Math.max(0, Math.floor(Number(state.edgeTrim) || 0));
  if (trim > 0) {
    for (let seg = 0; seg < ns; seg += 1) {
      if (!segmentKeep[seg]) continue;
      let removed = 0;
      for (let i = starts[seg]; i < ends[seg] && removed < trim; i += 1) if (rowKeep[i]) { rowKeep[i] = 0; removed += 1; }
      removed = 0;
      for (let i = ends[seg] - 1; i >= starts[seg] && removed < trim; i -= 1) if (rowKeep[i]) { rowKeep[i] = 0; removed += 1; }
    }
  }

  // ROI reach is calculated from the quality-filtered table before the
  // entered-only/after-exit mask. This preserves the established denominator
  // contract for the fraction-reaching panel.
  const roiReach = Math.max(.000001, Number(state.roiReach) || 3);
  const roiStats = new Array(ns);
  const roiBaseSegments = [];
  const segmentOutcome = new Uint8Array(ns);
  for (let seg = 0; seg < ns; seg += 1) {
    if (!segmentKeep[seg]) continue;
    const targets = targetsForSegment(seg);
    const stats = {
      firstLeft: Infinity, firstRight: Infinity,
      leftRows: 0, rightRows: 0, entered: false,
    };
    let wasInside = false, enteredAt = -1, exitAt = -1;
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!rowKeep[row]) continue;
      let insideAny = false;
      for (const target of targets) {
        if (Math.hypot(data.x[row] - target.x, data.z[row] - target.z) > roiReach) continue;
        insideAny = true;
        if (target.side === "left") {
          stats.leftRows += 1; stats.firstLeft = Math.min(stats.firstLeft, data.time[row]);
        } else if (target.side === "right") {
          stats.rightRows += 1; stats.firstRight = Math.min(stats.firstRight, data.time[row]);
        }
      }
      if (insideAny && enteredAt < 0) enteredAt = row;
      if (!insideAny && wasInside && exitAt < 0) exitAt = row;
      wasInside = insideAny;
    }
    stats.entered = enteredAt >= 0;
    stats.firstLeft = Number.isFinite(stats.firstLeft) ? stats.firstLeft : NaN;
    stats.firstRight = Number.isFinite(stats.firstRight) ? stats.firstRight : NaN;
    roiStats[seg] = stats; roiBaseSegments.push(seg);
    if (Number.isFinite(stats.firstLeft) || Number.isFinite(stats.firstRight)) {
      segmentOutcome[seg] = !Number.isFinite(stats.firstRight) || stats.firstLeft < stats.firstRight
        ? 1 : (!Number.isFinite(stats.firstLeft) || stats.firstRight < stats.firstLeft ? 2 : 3);
    }
    if (state.roiEntered && !stats.entered) {
      segmentKeep[seg] = 0; rowKeep.fill(0, starts[seg], ends[seg]);
    } else if (state.roiTrim && exitAt >= 0) {
      rowKeep.fill(0, exitAt, ends[seg]);
    }
  }

  const visibleSegments = [];
  let visibleRows = 0;
  for (let seg = 0; seg < ns; seg += 1) {
    if (!segmentKeep[seg]) continue;
    let count = 0;
    for (let i = starts[seg]; i < ends[seg]; i += 1) count += rowKeep[i];
    if (!count) { segmentKeep[seg] = 0; continue; }
    visibleRows += count; visibleSegments.push(seg);
  }

  let panelNames = ["All data"];
  const segmentPanel = new Int32Array(ns); segmentPanel.fill(-1);
  if (state.groupBy === "all") {
    for (const seg of visibleSegments) segmentPanel[seg] = 0;
  } else {
    const field = categoryFields[state.groupBy] || data.segmentConfig;
    const labels = state.labels?.[state.groupBy]
      || header.displayCategories?.[state.groupBy]
      || header.categories[state.groupBy]
      || header.categories.config;
    const panelByCategory = new Map();
    panelNames = [];
    for (const seg of visibleSegments) {
      const category = field[seg];
      if (!panelByCategory.has(category)) {
        panelByCategory.set(category, panelNames.length);
        panelNames.push(labels[category] ?? "unknown");
      }
      segmentPanel[seg] = panelByCategory.get(category);
    }
    if (!panelNames.length) panelNames = ["No matching data"];
  }

  const animals = new Set();
  for (const seg of visibleSegments) animals.add(`${data.segmentFly[seg]}@${data.segmentVr[seg]}`);
  const durations = visibleSegments
    .map(seg => data.segmentDuration[seg])
    .filter(Number.isFinite)
    .sort((a, b) => a - b);
  const durationSummary = {
    median: percentile(durations, .50),
    p95: percentile(durations, .95),
    p99: percentile(durations, .99),
    max: durations.length ? durations[durations.length - 1] : 0,
  };
  const panelRois = [], roiSeen = new Set();
  const roiLeft = new Uint32Array(panelNames.length), roiRight = new Uint32Array(panelNames.length);
  for (const seg of visibleSegments) {
    const panel = segmentPanel[seg];
    if (segmentOutcome[seg] === 1) roiLeft[panel] += 1;
    else if (segmentOutcome[seg] === 2) roiRight[panel] += 1;
    for (const target of targetsForSegment(seg)) {
      const key = `${panel}|${Number(target.x).toFixed(4)}|${Number(target.z).toFixed(4)}|${target.side}`;
      if (roiSeen.has(key)) continue;
      roiSeen.add(key);
      panelRois.push({panel, x: Number(target.x), z: Number(target.z), side: target.side, reach: roiReach});
    }
  }
  return {
    rowKeep, segmentKeep, visibleSegments, segmentPanel, panelNames,
    panelCount: panelNames.length, visibleRows, animals: animals.size,
    roiStats, roiBaseSegments, segmentOutcome, panelRois,
    roiCounts: {left: roiLeft, right: roiRight}, roiReach,
    durationSummary,
    filterMs: performance.now() - started,
  };
}

function percentile(sorted, q) {
  if (!sorted.length) return 0;
  const at = Math.max(0, Math.min(sorted.length - 1, (sorted.length - 1) * q));
  const lo = Math.floor(at), hi = Math.ceil(at), f = at - lo;
  return sorted[lo] * (1 - f) + sorted[hi] * f;
}

function spatialBounds(analysis, pct = 98) {
  const sampleX = [], sampleZ = [];
  const stride = Math.max(1, Math.floor(Math.max(1, analysis.visibleRows) / 60000));
  let seen = 0;
  for (let i = 0; i < data.x.length; i += 1) {
    if (!analysis.rowKeep[i]) continue;
    if ((seen++ % stride) !== 0) continue;
    if (Number.isFinite(data.x[i]) && Number.isFinite(data.z[i])) { sampleX.push(data.x[i]); sampleZ.push(data.z[i]); }
  }
  if (!sampleX.length) return {xmin: -1, xmax: 1, zmin: -1, zmax: 1};
  sampleX.sort((a, b) => a - b); sampleZ.sort((a, b) => a - b);
  const tail = Math.max(0, (100 - Math.max(50, Math.min(100, pct))) / 200);
  return {
    xmin: percentile(sampleX, tail), xmax: percentile(sampleX, 1 - tail),
    zmin: percentile(sampleZ, tail), zmax: percentile(sampleZ, 1 - tail),
  };
}

function hashSample(value, seed) {
  let x = ((value + 1) * 0x9e3779b1) ^ ((seed + 1) * 0x85ebca6b);
  x ^= x >>> 16; x = Math.imul(x, 0x7feb352d); x ^= x >>> 15; x = Math.imul(x, 0x846ca68b); x ^= x >>> 16;
  return (x >>> 0) / 4294967295;
}

function sequentialIndex(value, range) {
  const span = Math.max(1e-12, range[1] - range[0]);
  const t = Math.max(0, Math.min(1, (value - range[0]) / span));
  return SEQUENTIAL_OFFSET + Math.min(31, Math.floor(t * 31.999));
}

function rowColor(row, seg, state, analysis) {
  switch (state.colorBy) {
    case "none": return NEUTRAL_INDEX;
    case "fly": return data.segmentFly[seg] % CATEGORY_COLORS;
    case "config": return data.segmentConfig[seg] % CATEGORY_COLORS;
    case "scene": return data.segmentScene[seg] % CATEGORY_COLORS;
    case "vr": return data.segmentVr[seg] % CATEGORY_COLORS;
    case "folder": return data.segmentFolder[seg] % CATEGORY_COLORS;
    case "roi": return analysis.segmentOutcome[seg] === 1 ? 0 : (analysis.segmentOutcome[seg] === 2 ? 1 : 17);
    case "trial": return sequentialIndex(data.segmentTrial[seg], header.ranges.trial);
    case "time": return sequentialIndex(data.time[row], header.ranges.time);
    case "speed": return sequentialIndex(data.speed[row], header.ranges.speed);
    case "tortuosity": return sequentialIndex(data.tortuosity[row], [1, Math.max(2, header.ranges.distance[1])]);
    default: return Math.max(0, analysis.segmentPanel[seg]) % CATEGORY_COLORS;
  }
}

function eligibleForDrawing(row, state) {
  return !state.movingOnly || (Number.isFinite(data.speed[row]) && data.speed[row] >= (Number(state.walkThreshold) || 0));
}

function playbackLimit(state, analysis) {
  const key = state.playbackPercentile === "99" ? "p99"
    : (state.playbackPercentile === "max" ? "max" : "p95");
  return Math.max(0, Number(analysis.durationSummary?.[key]) || 0);
}

function segmentIntersectsCircle(x0, z0, x1, z1, ring) {
  const dx = x1 - x0, dz = z1 - z0;
  const length2 = dx * dx + dz * dz;
  let t = length2 > 0 ? ((ring.x - x0) * dx + (ring.z - z0) * dz) / length2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(x0 + t * dx - ring.x, z0 + t * dz - ring.z) <= ring.r;
}

function ringEntryTable(state, analysis) {
  const rings = (state.rings || []).filter(ring => Number(ring.r) > 0);
  if (!state.ringEnabled || !rings.length) return null;
  const entries = new Int32Array(starts.length); entries.fill(-1);
  let matches = 0;
  for (const seg of analysis.visibleSegments) {
    const hits = new Int32Array(rings.length); hits.fill(-1);
    let previous = -1;
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!analysis.rowKeep[row]) continue;
      for (let index = 0; index < rings.length; index += 1) {
        if (hits[index] >= 0) continue;
        const ring = rings[index];
        const inside = Math.hypot(data.x[row] - ring.x, data.z[row] - ring.z) <= ring.r;
        const crossed = previous >= 0 && segmentIntersectsCircle(
          data.x[previous], data.z[previous], data.x[row], data.z[row], ring
        );
        if (inside || crossed) hits[index] = row;
      }
      previous = row;
    }
    const reached = [...hits].filter(value => value >= 0);
    const qualifies = state.ringMatch === "all" ? reached.length === rings.length : reached.length > 0;
    if (qualifies) {
      entries[seg] = state.ringMatch === "all" ? Math.max(...reached) : Math.min(...reached);
      matches += 1;
    }
  }
  return {entries, matches, rings};
}

function buildTrajectory(state, analysis) {
  const started = performance.now();
  const ns = starts.length;
  const ringObserver = ringEntryTable(state, analysis);
  const trajectorySegments = ringObserver
    ? analysis.visibleSegments.filter(seg => ringObserver.entries[seg] >= 0)
    : analysis.visibleSegments;
  const eligibleCount = new Uint32Array(ns);
  let totalLinks = 0;
  for (const seg of trajectorySegments) {
    let count = 0;
    for (let i = starts[seg]; i < ends[seg]; i += 1) if (analysis.rowKeep[i] && eligibleForDrawing(i, state)) count += 1;
    eligibleCount[seg] = count;
    if (count > 1) totalLinks += count - 1;
  }
  const budget = Math.max(1000, Math.floor(Number(state.pointBudget) || 250000));
  const stride = Math.max(1, Math.ceil(totalLinks / budget));
  let selectedLinks = 0;
  for (const seg of trajectorySegments) {
    const count = eligibleCount[seg];
    if (count <= 1) continue;
    const regular = Math.floor((count - 1) / stride);
    const extra = ((count - 1) % stride) ? 1 : 0;
    selectedLinks += regular + extra;
  }
  const vertices = new Float32Array(selectedLinks * 4);
  const panels = new Uint16Array(selectedLinks * 2);
  const colors = new Uint8Array(selectedLinks * 2);
  const animals = new Uint16Array(selectedLinks * 2);
  const segments = new Uint32Array(selectedLinks * 2);
  const times = new Float32Array(selectedLinks * 2);
  const samples = new Float32Array(selectedLinks * 2);
  let link = 0;
  for (const seg of trajectorySegments) {
    const count = eligibleCount[seg];
    if (count <= 1) continue;
    let position = 0, previous = -1;
    const sample = hashSample(seg, state.sampleSeed || 0);
    for (let i = starts[seg]; i < ends[seg]; i += 1) {
      if (!analysis.rowKeep[i] || !eligibleForDrawing(i, state)) continue;
      const select = position === 0 || position === count - 1 || position % stride === 0;
      if (select) {
        if (previous >= 0) {
          const v = link * 4, a = link * 2;
          vertices[v] = data.x[previous]; vertices[v + 1] = data.z[previous];
          vertices[v + 2] = data.x[i]; vertices[v + 3] = data.z[i];
          panels[a] = analysis.segmentPanel[seg]; panels[a + 1] = analysis.segmentPanel[seg];
          const entry = ringObserver?.entries[seg] ?? -1;
          colors[a] = entry >= 0 && previous < entry ? NEUTRAL_INDEX : rowColor(previous, seg, state, analysis);
          colors[a + 1] = entry >= 0 && i < entry ? NEUTRAL_INDEX : rowColor(i, seg, state, analysis);
          animals[a] = data.segmentAnimal[seg]; animals[a + 1] = data.segmentAnimal[seg];
          segments[a] = seg; segments[a + 1] = seg;
          times[a] = data.time[previous]; times[a + 1] = data.time[i];
          samples[a] = sample; samples[a + 1] = sample;
          link += 1;
        }
        previous = i;
      }
      position += 1;
    }
  }
  return {
    vertices: vertices.subarray(0, link * 4), panels: panels.subarray(0, link * 2),
    colors: colors.subarray(0, link * 2), times: times.subarray(0, link * 2),
    animals: animals.subarray(0, link * 2),
    segments: segments.subarray(0, link * 2),
    samples: samples.subarray(0, link * 2), panelCount: analysis.panelCount,
    panelNames: analysis.panelNames, bounds: spatialBounds(analysis, 98),
    animalNames: header.categories.animal,
    columns: Number(state.panelColumns) || 0, links: link,
    maxTime: playbackLimit(state, analysis),
    rois: analysis.panelRois,
    roiCounts: {left: Array.from(analysis.roiCounts.left), right: Array.from(analysis.roiCounts.right)},
    reach: analysis.roiReach,
    rings: ringObserver?.rings || (state.rings || []),
    ringEnabled: !!state.ringEnabled,
    ringMatches: ringObserver?.matches ?? analysis.visibleSegments.length,
    buildMs: performance.now() - started,
  };
}

function gridGeometry(state, analysis) {
  const bounds = spatialBounds(analysis, Number(state.boundPercent) || 98);
  const extent = Math.max(Math.abs(bounds.xmin), Math.abs(bounds.xmax), Math.abs(bounds.zmin), Math.abs(bounds.zmax), 1e-6) * 1.02;
  let bin = Number(state.binSize);
  if (!(bin > 0)) bin = extent * 2 / 41;
  let half = Math.max(1, Math.ceil(extent / bin));
  const maxAxis = Math.max(11, Math.floor(Math.sqrt(250000 / Math.max(1, analysis.panelCount))));
  if (half * 2 + 1 > maxAxis) { half = Math.max(5, Math.floor((maxAxis - 1) / 2)); bin = extent / half; }
  const nx = half * 2 + 1, nz = nx;
  return {bin, nx, nz, x0: -(half + .5) * bin, z0: -(half + .5) * bin, bounds};
}

function buildSpatial(state, analysis) {
  const started = performance.now();
  const grid = gridGeometry(state, analysis);
  const cells = grid.nx * grid.nz;
  const count = new Float32Array(analysis.panelCount * cells);
  const sumSin = new Float64Array(count.length), sumCos = new Float64Array(count.length);
  const validDirection = new Uint32Array(count.length);
  const heading = state.angleSource === "movement" ? data.movement : data.orientation;
  for (let row = 0; row < data.x.length; row += 1) {
    if (!analysis.rowKeep[row]) continue;
    const seg = data.segment[row], panel = analysis.segmentPanel[seg];
    const ix = Math.floor((data.x[row] - grid.x0) / grid.bin);
    const iz = Math.floor((data.z[row] - grid.z0) / grid.bin);
    if (ix < 0 || ix >= grid.nx || iz < 0 || iz >= grid.nz || panel < 0) continue;
    const index = panel * cells + iz * grid.nx + ix;
    count[index] += 1;
    const angle = heading[row];
    if (Number.isFinite(angle) && (!state.movingOnly || data.speed[row] >= (Number(state.walkThreshold) || 0))) {
      const radians = angle * Math.PI / 180;
      sumSin[index] += Math.sin(radians); sumCos[index] += Math.cos(radians); validDirection[index] += 1;
    }
  }
  const time = new Float32Array(count.length), angle = new Float32Array(count.length), strength = new Float32Array(count.length);
  angle.fill(NaN);
  for (let i = 0; i < count.length; i += 1) {
    time[i] = count[i] * medianDt;
    if (validDirection[i]) {
      angle[i] = Math.atan2(sumSin[i], sumCos[i]) * 180 / Math.PI;
      strength[i] = Math.hypot(sumSin[i], sumCos[i]) / validDirection[i];
    }
  }
  const common = {
    ...grid, panelCount: analysis.panelCount, panelNames: analysis.panelNames,
    columns: Number(state.panelColumns) || 0,
    rois: analysis.panelRois,
    roiCounts: {left: Array.from(analysis.roiCounts.left), right: Array.from(analysis.roiCounts.right)},
    reach: analysis.roiReach,
  };
  return {
    heatmap: {...common, count, time, buildMs: performance.now() - started},
    direction: {...common, angle, strength, abundance: count, time,
      buildMs: performance.now() - started},
  };
}

function unitColor(seg, state, analysis) {
  return rowColor(starts[seg], seg, state, analysis);
}

function buildPolar(state, analysis) {
  const started = performance.now();
  const ns = starts.length, heading = state.angleSource === "movement" ? data.movement : data.orientation;
  const sumSin = new Float64Array(ns), sumCos = new Float64Array(ns), valid = new Uint32Array(ns), available = new Uint32Array(ns);
  for (const seg of analysis.visibleSegments) {
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!analysis.rowKeep[row]) continue;
      available[seg] += 1;
      const angle = heading[row];
      if (!Number.isFinite(angle) || (state.movingOnly && data.speed[row] < (Number(state.walkThreshold) || 0))) continue;
      const radians = angle * Math.PI / 180;
      sumSin[seg] += Math.sin(radians); sumCos[seg] += Math.cos(radians); valid[seg] += 1;
    }
  }
  const rMin = Number(state.polarR?.[0]) || 0, rMax = Number(state.polarR?.[1]) || 1;
  const validMin = Number(state.polarValidMin) || 0;
  const units = [];
  if (state.statsUnit === "animal") {
    const map = new Map();
    for (const seg of analysis.visibleSegments) {
      if (!valid[seg]) continue;
      const rr = Math.hypot(sumSin[seg], sumCos[seg]) / valid[seg];
      if (rr < rMin || rr > rMax || valid[seg] / Math.max(1, available[seg]) < validMin) continue;
      const key = `${analysis.segmentPanel[seg]}|${data.segmentFly[seg]}|${data.segmentVr[seg]}`;
      const unit = map.get(key) || {
        panel: analysis.segmentPanel[seg], animal: data.segmentAnimal[seg],
        trial: NaN, step: NaN, sin: 0, cos: 0, count: 0,
        color: unitColor(seg, state, analysis), seed: seg,
      };
      unit.sin += sumSin[seg]; unit.cos += sumCos[seg]; unit.count += valid[seg]; map.set(key, unit);
    }
    for (const unit of map.values()) units.push(unit);
  } else {
    for (const seg of analysis.visibleSegments) {
      if (!valid[seg]) continue;
      const rr = Math.hypot(sumSin[seg], sumCos[seg]) / valid[seg];
      if (rr < rMin || rr > rMax || valid[seg] / Math.max(1, available[seg]) < validMin) continue;
      units.push({
        panel: analysis.segmentPanel[seg], animal: data.segmentAnimal[seg],
        trial: data.segmentTrial[seg], step: data.segmentStep[seg],
        sin: sumSin[seg], cos: sumCos[seg], count: valid[seg],
        color: unitColor(seg, state, analysis), seed: seg,
      });
    }
  }
  const angle = new Float32Array(units.length), r = new Float32Array(units.length), panel = new Uint16Array(units.length), color = new Uint8Array(units.length), sample = new Float32Array(units.length);
  const animal = new Uint16Array(units.length), trial = new Float32Array(units.length), step = new Float32Array(units.length);
  const popSin = new Float64Array(analysis.panelCount), popCos = new Float64Array(analysis.panelCount), popWeight = new Float64Array(analysis.panelCount);
  for (let i = 0; i < units.length; i += 1) {
    const unit = units[i], magnitude = Math.hypot(unit.sin, unit.cos);
    angle[i] = Math.atan2(unit.sin, unit.cos) * 180 / Math.PI; r[i] = magnitude / Math.max(1, unit.count);
    panel[i] = unit.panel; color[i] = unit.color; sample[i] = hashSample(unit.seed, state.sampleSeed || 0);
    animal[i] = unit.animal; trial[i] = unit.trial; step[i] = unit.step;
    const weight = state.statsUnit === "animal" ? 1 : unit.count;
    popSin[unit.panel] += Math.sin(angle[i] * Math.PI / 180) * weight;
    popCos[unit.panel] += Math.cos(angle[i] * Math.PI / 180) * weight;
    popWeight[unit.panel] += weight;
  }
  const populationAngle = new Float32Array(analysis.panelCount), populationR = new Float32Array(analysis.panelCount);
  populationAngle.fill(NaN); populationR.fill(NaN);
  for (let p = 0; p < analysis.panelCount; p += 1) if (popWeight[p]) {
    populationAngle[p] = Math.atan2(popSin[p], popCos[p]) * 180 / Math.PI;
    populationR[p] = Math.hypot(popSin[p], popCos[p]) / popWeight[p];
  }
  return {
    angle, r, panel, color, sample, animal, trial, step,
    populationAngle, populationR, panelCount: analysis.panelCount,
    panelNames: analysis.panelNames, animalNames: header.categories.animal,
    columns: Number(state.panelColumns) || 0, units: units.length,
    buildMs: performance.now() - started,
  };
}

function buildHeading(state, analysis) {
  const heading = state.angleSource === "movement" ? data.movement : data.orientation;
  const maxTime = Math.max(.001, playbackLimit(state, analysis));
  const mode = state.headingMode || "trial";
  const timeBin = Math.max(.05, Number(state.headingBin) || Math.max(.1, maxTime / 120));
  const sectors = Math.max(12, Math.min(72, Math.round(Number(state.headingSectors) || 36)));

  if (mode === "density") {
    const nTime = Math.max(1, Math.ceil(maxTime / timeBin));
    const density = new Float32Array(analysis.panelCount * nTime * sectors);
    for (const seg of analysis.visibleSegments) {
      const panel = analysis.segmentPanel[seg];
      for (let row = starts[seg]; row < ends[seg]; row += 1) {
        const angle = heading[row], time = data.time[row];
        if (!analysis.rowKeep[row] || !Number.isFinite(angle) || time < 0 || time > maxTime
          || (state.movingOnly && data.speed[row] < (Number(state.walkThreshold) || 0))) continue;
        const tx = Math.min(nTime - 1, Math.floor(time / timeBin));
        const ay = Math.min(sectors - 1, Math.floor((wrapAngle(angle) + 180) / 360 * sectors));
        density[(panel * nTime + tx) * sectors + ay] += 1;
      }
    }
    for (let panel = 0; panel < analysis.panelCount; panel += 1) {
      for (let tx = 0; tx < nTime; tx += 1) {
        const base = (panel * nTime + tx) * sectors;
        let total = 0;
        for (let ay = 0; ay < sectors; ay += 1) total += density[base + ay];
        if (total > 0) for (let ay = 0; ay < sectors; ay += 1) density[base + ay] = density[base + ay] / total * 100;
      }
    }
    return {
      mode, density, nTime, sectors, timeBin, maxTime,
      panelCount: analysis.panelCount, panelNames: analysis.panelNames,
      animalNames: header.categories.animal,
      columns: Number(state.panelColumns) || 0,
    };
  }

  if (mode === "mean") {
    const grouped = new Map();
    for (const seg of analysis.visibleSegments) {
      const perTrial = new Map();
      for (let row = starts[seg]; row < ends[seg]; row += 1) {
        const angle = heading[row], time = data.time[row];
        if (!analysis.rowKeep[row] || !Number.isFinite(angle) || time < 0 || time > maxTime
          || (state.movingOnly && data.speed[row] < (Number(state.walkThreshold) || 0))) continue;
        const bin = Math.floor(time / timeBin);
        const item = perTrial.get(bin) || {sin: 0, cos: 0};
        const radians = angle * Math.PI / 180;
        item.sin += Math.sin(radians); item.cos += Math.cos(radians);
        perTrial.set(bin, item);
      }
      for (const [bin, item] of perTrial) {
        const magnitude = Math.hypot(item.sin, item.cos);
        if (!(magnitude > 0)) continue;
        const key = `${analysis.segmentPanel[seg]}|${data.segmentAnimal[seg]}|${bin}`;
        const aggregate = grouped.get(key) || {
          panel: analysis.segmentPanel[seg], animal: data.segmentAnimal[seg],
          bin, sin: 0, cos: 0, trials: 0,
        };
        aggregate.sin += item.sin / magnitude; aggregate.cos += item.cos / magnitude;
        aggregate.trials += 1; grouped.set(key, aggregate);
      }
    }
    const bySeries = new Map();
    for (const item of grouped.values()) {
      const key = `${item.panel}|${item.animal}`;
      if (!bySeries.has(key)) bySeries.set(key, []);
      bySeries.get(key).push({
        ...item,
        time: Math.min(maxTime, (item.bin + .5) * timeBin),
        angle: Math.atan2(item.sin, item.cos) * 180 / Math.PI,
        r: Math.hypot(item.sin, item.cos) / Math.max(1, item.trials),
      });
    }
    const vertices = [], panels = [], colors = [], samples = [], animals = [], trials = [], steps = [];
    for (const values of bySeries.values()) {
      values.sort((a, b) => a.bin - b.bin);
      for (let i = 1; i < values.length; i += 1) {
        const previous = values[i - 1], current = values[i];
        if (current.bin !== previous.bin + 1 || Math.abs(current.angle - previous.angle) > 180) continue;
        vertices.push(previous.time, previous.angle, current.time, current.angle);
        panels.push(current.panel, current.panel);
        colors.push(current.animal % CATEGORY_COLORS, current.animal % CATEGORY_COLORS);
        samples.push(0, 0); animals.push(current.animal, current.animal);
        trials.push(NaN, NaN); steps.push(current.r, current.r);
      }
    }
    return {
      mode, vertices: Float32Array.from(vertices), panels: Uint16Array.from(panels),
      colors: Uint8Array.from(colors), samples: Float32Array.from(samples),
      animals: Uint16Array.from(animals), trials: Float32Array.from(trials),
      steps: Float32Array.from(steps), maxTime, timeBin,
      panelCount: analysis.panelCount, panelNames: analysis.panelNames,
      animalNames: header.categories.animal,
      columns: Number(state.panelColumns) || 0,
    };
  }

  let totalLinks = 0;
  const eligibleCount = new Uint32Array(starts.length);
  for (const seg of analysis.visibleSegments) {
    let count = 0, prev = NaN;
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!analysis.rowKeep[row] || !Number.isFinite(heading[row]) || data.time[row] > maxTime
        || (state.movingOnly && data.speed[row] < (Number(state.walkThreshold) || 0))) continue;
      if (Number.isFinite(prev) && Math.abs(heading[row] - prev) <= 180) totalLinks += 1;
      prev = heading[row]; count += 1;
    }
    eligibleCount[seg] = count;
  }
  const stride = Math.max(1, Math.ceil(totalLinks / 80000));
  const vertices = [], panels = [], colors = [], samples = [], animals = [], trials = [], steps = [];
  for (const seg of analysis.visibleSegments) {
    let position = 0, previous = -1, previousAngle = NaN;
    const sample = hashSample(seg, state.sampleSeed || 0);
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      const a = heading[row];
      if (!analysis.rowKeep[row] || !Number.isFinite(a) || data.time[row] > maxTime
        || (state.movingOnly && data.speed[row] < (Number(state.walkThreshold) || 0))) continue;
      const select = position === 0 || position === eligibleCount[seg] - 1 || position % stride === 0;
      if (select) {
        if (previous >= 0 && Math.abs(a - previousAngle) <= 180) {
          vertices.push(data.time[previous], previousAngle, data.time[row], a);
          panels.push(analysis.segmentPanel[seg], analysis.segmentPanel[seg]);
          const c = unitColor(seg, state, analysis); colors.push(c, c); samples.push(sample, sample);
          animals.push(data.segmentAnimal[seg], data.segmentAnimal[seg]);
          trials.push(data.segmentTrial[seg], data.segmentTrial[seg]);
          steps.push(data.segmentStep[seg], data.segmentStep[seg]);
        }
        previous = row; previousAngle = a;
      }
      position += 1;
    }
  }
  return {
    mode,
    vertices: Float32Array.from(vertices), panels: Uint16Array.from(panels),
    colors: Uint8Array.from(colors), samples: Float32Array.from(samples),
    animals: Uint16Array.from(animals), trials: Float32Array.from(trials),
    steps: Float32Array.from(steps), maxTime,
    panelCount: analysis.panelCount, panelNames: analysis.panelNames,
    animalNames: header.categories.animal,
    columns: Number(state.panelColumns) || 0,
  };
}

function buildMetrics(state, analysis) {
  const units = [];
  if (state.statsUnit === "animal") {
    const map = new Map();
    for (const seg of analysis.visibleSegments) {
      const key = `${analysis.segmentPanel[seg]}|${data.segmentFly[seg]}|${data.segmentVr[seg]}`;
      const unit = map.get(key) || {
        panel: analysis.segmentPanel[seg], animal: data.segmentAnimal[seg],
        trial: NaN, step: NaN, distance: 0, displacement: 0,
        speed: 0, tortuosity: 0, n: 0,
      };
      unit.distance += data.segmentDistance[seg]; unit.displacement += data.segmentDisplacement[seg];
      unit.speed += data.segmentMedianSpeed[seg]; unit.tortuosity += data.segmentTortuosity[seg]; unit.n += 1; map.set(key, unit);
    }
    for (const unit of map.values()) {
      unit.distance /= unit.n; unit.displacement /= unit.n; unit.speed /= unit.n; unit.tortuosity /= unit.n; units.push(unit);
    }
  } else {
    for (const seg of analysis.visibleSegments) units.push({
      panel: analysis.segmentPanel[seg], animal: data.segmentAnimal[seg],
      trial: data.segmentTrial[seg], step: data.segmentStep[seg],
      distance: data.segmentDistance[seg], displacement: data.segmentDisplacement[seg],
      speed: data.segmentMedianSpeed[seg], tortuosity: data.segmentTortuosity[seg],
    });
  }
  return {
    panel: Uint16Array.from(units.map(u => u.panel)),
    animal: Uint16Array.from(units.map(u => u.animal)),
    trial: Float32Array.from(units.map(u => u.trial)),
    step: Float32Array.from(units.map(u => u.step)),
    distance: Float32Array.from(units.map(u => u.distance)),
    displacement: Float32Array.from(units.map(u => u.displacement)),
    speed: Float32Array.from(units.map(u => u.speed)),
    tortuosity: Float32Array.from(units.map(u => u.tortuosity)),
    panelCount: analysis.panelCount, panelNames: analysis.panelNames,
    animalNames: header.categories.animal, units: units.length,
  };
}

function buildRoi(state, analysis) {
  const animals = new Map();
  for (const seg of analysis.roiBaseSegments) {
    const stats = analysis.roiStats[seg];
    if (!stats) continue;
    // Base segments can have been removed by entered-only, so reconstruct the
    // panel from the active grouping when necessary.
    let panel = analysis.segmentPanel[seg];
    if (panel < 0) {
      if (state.groupBy === "all") panel = 0;
      else {
        const field = {
          config: data.segmentConfig, scene: data.segmentScene, vr: data.segmentVr,
          fly: data.segmentFly, folder: data.segmentFolder,
        }[state.groupBy] || data.segmentConfig;
        const label = (header.categories[state.groupBy] || header.categories.config)[field[seg]];
        panel = Math.max(0, analysis.panelNames.indexOf(label));
      }
    }
    const key = `${panel}|${data.segmentFly[seg]}|${data.segmentVr[seg]}`;
    const unit = animals.get(key) || {
      panel, animal: data.segmentAnimal[seg], trials: 0,
      left: 0, right: 0, leftRows: 0, rightRows: 0,
    };
    unit.trials += 1;
    if (Number.isFinite(stats.firstLeft)) unit.left += 1;
    if (Number.isFinite(stats.firstRight)) unit.right += 1;
    unit.leftRows += stats.leftRows; unit.rightRows += stats.rightRows;
    animals.set(key, unit);
  }
  const animalUnits = [...animals.values()];
  const animalPanel = new Uint16Array(animalUnits.length);
  const animalCode = new Uint16Array(animalUnits.length);
  const leftFraction = new Float32Array(animalUnits.length), rightFraction = new Float32Array(animalUnits.length);
  const leftResidence = new Float32Array(animalUnits.length), rightResidence = new Float32Array(animalUnits.length);
  for (let i = 0; i < animalUnits.length; i += 1) {
    const unit = animalUnits[i]; animalPanel[i] = unit.panel; animalCode[i] = unit.animal;
    leftFraction[i] = unit.left / Math.max(1, unit.trials); rightFraction[i] = unit.right / Math.max(1, unit.trials);
    leftResidence[i] = unit.leftRows * medianDt / Math.max(1, unit.trials);
    rightResidence[i] = unit.rightRows * medianDt / Math.max(1, unit.trials);
  }

  const timeValues = [], timeSides = [], timePanels = [], timeAnimals = [], timeTrials = [];
  for (const seg of analysis.visibleSegments) {
    const stats = analysis.roiStats[seg]; if (!stats) continue;
    if (Number.isFinite(stats.firstLeft)) {
      timeValues.push(stats.firstLeft); timeSides.push(0); timePanels.push(analysis.segmentPanel[seg]);
      timeAnimals.push(data.segmentAnimal[seg]); timeTrials.push(data.segmentTrial[seg]);
    }
    if (Number.isFinite(stats.firstRight)) {
      timeValues.push(stats.firstRight); timeSides.push(1); timePanels.push(analysis.segmentPanel[seg]);
      timeAnimals.push(data.segmentAnimal[seg]); timeTrials.push(data.segmentTrial[seg]);
    }
  }

  const errorValues = [], errorSides = [], errorPanels = [], errorAnimals = [], errorTrials = [];
  const heading = state.angleSource === "movement" ? data.movement : data.orientation;
  const stride = Math.max(1, Math.ceil(analysis.visibleRows / 100000));
  let seen = 0;
  for (const seg of analysis.visibleSegments) {
    const targets = targetsForSegment(seg);
    const sideTargets = [targets.find(target => target.side === "left"), targets.find(target => target.side === "right")];
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!analysis.rowKeep[row] || !Number.isFinite(heading[row]) || (seen++ % stride) !== 0) continue;
      for (let side = 0; side < 2; side += 1) {
        const target = sideTargets[side]; if (!target) continue;
        const bearing = Math.atan2(target.x - data.x[row], target.z - data.z[row]) * 180 / Math.PI;
        errorValues.push(wrapAngle(heading[row] - bearing)); errorSides.push(side); errorPanels.push(analysis.segmentPanel[seg]);
        errorAnimals.push(data.segmentAnimal[seg]); errorTrials.push(data.segmentTrial[seg]);
      }
    }
  }
  return {
    animalPanel, animalCode, leftFraction, rightFraction, leftResidence, rightResidence,
    timeValues: Float32Array.from(timeValues), timeSides: Uint8Array.from(timeSides), timePanels: Uint16Array.from(timePanels),
    timeAnimals: Uint16Array.from(timeAnimals), timeTrials: Float32Array.from(timeTrials),
    errorValues: Float32Array.from(errorValues), errorSides: Uint8Array.from(errorSides), errorPanels: Uint16Array.from(errorPanels),
    errorAnimals: Uint16Array.from(errorAnimals), errorTrials: Float32Array.from(errorTrials),
    panelCount: analysis.panelCount, panelNames: analysis.panelNames,
    animalNames: header.categories.animal,
    baseSegments: analysis.roiBaseSegments.length,
  };
}

function histogram(values, bins = 64, upperPercentile = .995) {
  const sample = [];
  const stride = Math.max(1, Math.floor(values.length / 100000));
  for (let i = 0; i < values.length; i += stride) if (Number.isFinite(values[i])) sample.push(values[i]);
  sample.sort((a, b) => a - b);
  const lo = Math.min(0, sample[0] || 0), hi = Math.max(lo + 1e-9, percentile(sample, upperPercentile));
  const counts = new Uint32Array(bins), edges = new Float32Array(bins + 1);
  for (let i = 0; i <= bins; i += 1) edges[i] = lo + (hi - lo) * i / bins;
  for (let i = 0; i < values.length; i += 1) {
    const value = values[i]; if (!Number.isFinite(value) || value < lo || value > hi) continue;
    counts[Math.min(bins - 1, Math.floor((value - lo) / (hi - lo) * bins))] += 1;
  }
  return {counts, edges};
}

function buildDiagnostics() {
  return {velocity: histogram(data.speed), displacement: histogram(data.segmentDisplacement)};
}

function buffers(value, into = []) {
  if (!value || typeof value !== "object") return into;
  if (ArrayBuffer.isView(value)) { into.push(value.buffer); return into; }
  for (const child of Object.values(value)) buffers(child, into);
  return into;
}

function compute(message) {
  const {state, requestId} = message;
  const key = analysisKey(state);
  const analysisChanged = key !== lastAnalysisKey;
  if (analysisChanged || !lastAnalysis) {
    lastAnalysis = buildAnalysis(state); lastAnalysisKey = key;
  }
  let scope = message.scope || "full";
  if (analysisChanged) scope = "full";
  const products = {};
  if (scope === "full" || scope === "trajectory" || scope === "playback") products.trajectory = buildTrajectory(state, lastAnalysis);
  if (scope === "full" || scope === "spatial") Object.assign(products, buildSpatial(state, lastAnalysis));
  if (scope === "full" || scope === "direction" || scope === "statistics" || scope === "playback") {
    if (scope !== "playback") products.polar = buildPolar(state, lastAnalysis);
    if (scope !== "statistics") products.heading = buildHeading(state, lastAnalysis);
  }
  if (scope === "full" || scope === "statistics") {
    products.metrics = buildMetrics(state, lastAnalysis);
    products.roi = buildRoi(state, lastAnalysis);
  }
  if (scope === "full") products.diagnostics = buildDiagnostics();
  const segmentOptions = scope === "full"
    ? lastAnalysis.visibleSegments.map(seg => ({
      code: seg,
      label: `${header.categories.file[data.segmentFile[seg]]} · trial ${data.segmentTrial[seg]} / step ${data.segmentStep[seg]}`,
      duration: data.segmentDuration[seg],
      animal: data.segmentAnimal[seg],
    }))
    : null;
  const result = {
    type: "result", requestId, scope, products,
    summary: {
      visibleRows: lastAnalysis.visibleRows,
      visibleSegments: lastAnalysis.visibleSegments.length,
      animals: lastAnalysis.animals,
      panels: lastAnalysis.panelCount,
      filterMs: lastAnalysis.filterMs,
      durationSummary: lastAnalysis.durationSummary,
      segmentOptions,
    },
  };
  postMessage(result, [...new Set(buffers(products))]);
}

function inspectPoint(message) {
  if (!lastAnalysis) return;
  const tolerance = Math.max(1e-9, Number(message.tolerance) || 0);
  let bestRow = -1, bestDistance = tolerance * tolerance;
  for (let row = 0; row < data.x.length; row += 1) {
    if (!lastAnalysis.rowKeep[row]) continue;
    const seg = data.segment[row];
    if (lastAnalysis.segmentPanel[seg] !== message.panel) continue;
    const dx = data.x[row] - message.x, dz = data.z[row] - message.z;
    const distance = dx * dx + dz * dz;
    if (distance <= bestDistance) { bestDistance = distance; bestRow = row; }
  }
  if (bestRow < 0) {
    postMessage({type: "inspect-result", requestId: message.requestId, match: null});
    return;
  }
  const seg = data.segment[bestRow];
  postMessage({
    type: "inspect-result", requestId: message.requestId,
    match: {
      segmentId: header.segmentIds[seg],
      sourceFile: header.categories.file[data.segmentFile[seg]],
      trial: data.segmentTrial[seg], step: data.segmentStep[seg],
      config: header.categories.config[data.segmentConfig[seg]],
      scene: header.categories.scene[data.segmentScene[seg]],
      vr: header.categories.vr[data.segmentVr[seg]],
      fly: header.categories.fly[data.segmentFly[seg]],
      x: data.x[bestRow], z: data.z[bestRow], time: data.time[bestRow],
      points: data.segmentPoints[seg], distance: data.segmentDistance[seg],
      displacement: data.segmentDisplacement[seg], peakSpeed: data.segmentPeakSpeed[seg],
      medianSpeed: data.segmentMedianSpeed[seg], tortuosity: data.segmentTortuosity[seg],
    },
  });
}

self.onmessage = event => {
  try {
    const message = event.data;
    if (message.type === "init") initDataset(message);
    else if (message.type === "compute") compute(message);
    else if (message.type === "inspect") inspectPoint(message);
  } catch (error) {
    postMessage({type: "error", requestId: event.data?.requestId, error: error?.stack || String(error)});
  }
};
