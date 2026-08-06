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
let segmentMinX = null;
let segmentMaxX = null;
let segmentMinZ = null;
let segmentMaxZ = null;
let mirroredX = null;
let mirroredOrientation = null;
let mirroredMovement = null;
let activeX = null;
let activeZ = null;
let activeOrientation = null;
let activeMovement = null;
let mirrorFrameEnabled = false;
let mirroredRois = null;
let activeSegmentMinX = null;
let activeSegmentMaxX = null;
let activeSegmentMinZ = null;
let activeSegmentMaxZ = null;
let customMirrorKey = "";
let customMirrorGroup = null;
let customMirrorLabels = null;
let customSegmentReflected = null;
let customSegmentAxis = null;
let customSegmentCoordinate = null;
let lastBaseAnalysis = null;
let lastAnalysis = null;
let lastAnalysisKey = "";
let lastCurtainKey = "";
let lastPanelKey = "";
let lastOccupancy = null;
let lastOccupancyKey = "";
let lastOccupancyPlan = null;
let lastOccupancyPlanKey = "";
let lastDirectionPlan = null;
let lastDirectionPlanKey = "";
let lastTransitionGeometry = null;

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
  segmentMinX = new Float32Array(starts.length); segmentMinX.fill(Infinity);
  segmentMaxX = new Float32Array(starts.length); segmentMaxX.fill(-Infinity);
  segmentMinZ = new Float32Array(starts.length); segmentMinZ.fill(Infinity);
  segmentMaxZ = new Float32Array(starts.length); segmentMaxZ.fill(-Infinity);
  for (let row = 0; row < n; row += 1) {
    const seg = data.segment[row], x = data.x[row], z = data.z[row];
    if (Number.isFinite(x)) { segmentMinX[seg] = Math.min(segmentMinX[seg], x); segmentMaxX[seg] = Math.max(segmentMaxX[seg], x); }
    if (Number.isFinite(z)) { segmentMinZ[seg] = Math.min(segmentMinZ[seg], z); segmentMaxZ[seg] = Math.max(segmentMaxZ[seg], z); }
  }
  mirroredX = new Float32Array(n);
  mirroredOrientation = new Float32Array(n);
  mirroredMovement = new Float32Array(n);
  for (let row = 0; row < n; row += 1) {
    const sign = data.segmentMirrorSign?.[data.segment[row]] < 0 ? -1 : 1;
    mirroredX[row] = data.x[row] * sign;
    mirroredOrientation[row] = wrapAngle(data.orientation[row] * sign);
    mirroredMovement[row] = wrapAngle(data.movement[row] * sign);
  }
  activeX = data.x; activeZ = data.z;
  activeOrientation = data.orientation; activeMovement = data.movement;
  activeSegmentMinX = segmentMinX; activeSegmentMaxX = segmentMaxX;
  activeSegmentMinZ = segmentMinZ; activeSegmentMaxZ = segmentMaxZ;
  mirroredRois = Object.fromEntries(Object.entries(header.rois || {}).map(([config, targets]) => [
    config, (targets || []).map(target => ({
      ...target, x: -Number(target.x), angle: -Number(target.angle || 0),
      side: target.side === "left" ? "right" : (target.side === "right" ? "left" : target.side),
    })),
  ]));
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

function stablePanelKeys(state, labels, visibleCategories) {
  const selected = state.mirrorPool
    ? null : state.filters?.[state.groupBy];
  const candidates = Array.isArray(selected) && selected.length
    ? selected.map(Number)
    : labels.map((_, index) => index);
  const valid = candidates.filter(category => Number.isInteger(category)
    && category >= 0 && category < labels.length);
  const orderKey = state.mirrorPool
    ? (state.groupBy === "config" ? "mirrorConfig" : `mirror:${state.groupBy}`)
    : state.groupBy;
  const requested = (state.panelOrders?.[orderKey] || []).map(Number);
  const ordered = requested.filter(category => valid.includes(category));
  for (const category of valid) if (!ordered.includes(category)) ordered.push(category);
  // Preserve a category that is present in the data even if an older URL or
  // recipe omitted it from its saved selector state.
  for (const category of visibleCategories) if (!ordered.includes(category)) ordered.push(category);
  return ordered;
}

function analysisKey(state) {
  return JSON.stringify({
    filters: state.filters, ranges: state.ranges,
    angleSource: state.angleSource,
    mirrorPool: !!state.mirrorPool,
    mirrorRules: state.mirrorRules,
    jumpThreshold: state.jumpThreshold, jumpBufferMs: state.jumpBufferMs,
    minDisplacement: state.minDisplacement, edgeTrim: state.edgeTrim,
    roiReach: state.roiReach, roiEntered: state.roiEntered, roiTrim: state.roiTrim,
  });
}

function curtainKey(state) {
  return JSON.stringify({enabled: state.ringEnabled, match: state.ringMatch, rings: state.rings});
}

function panelKey(state) {
  return JSON.stringify({
    groupBy: state.groupBy, mirrorPool: !!state.mirrorPool, mirrorRules: state.mirrorRules,
    labels: state.labels, panelOrders: state.panelOrders,
  });
}

function targetsForSegment(seg) {
  const config = header.categories.config[data.segmentConfig[seg]];
  if (mirrorFrameEnabled && customSegmentReflected?.[seg]) {
    const axis = customSegmentAxis[seg] === 1 ? "z" : "x";
    const coordinate = customSegmentCoordinate[seg];
    return (header.rois?.[config] || []).map(target => axis === "z" ? {
      ...target, z: 2 * coordinate - Number(target.z),
      angle: wrapAngle(180 - Number(target.angle || 0)),
    } : {
      ...target, x: 2 * coordinate - Number(target.x),
      angle: wrapAngle(-Number(target.angle || 0)),
      side: target.side === "left" ? "right" : (target.side === "right" ? "left" : target.side),
    });
  }
  return mirrorFrameEnabled && data.segmentMirrorSign?.[seg] < 0
    ? (mirroredRois?.[config] || []) : (header.rois?.[config] || []);
}

function wrapAngle(value) {
  return ((value + 180) % 360 + 360) % 360 - 180;
}

function selectCoordinateFrame(state) {
  const rules = state.mirrorPool && Array.isArray(state.mirrorRules)
    ? state.mirrorRules.filter(rule => Number.isInteger(Number(rule.reference))
      && Number.isInteger(Number(rule.reflected)) && Number(rule.reference) !== Number(rule.reflected)
      && (rule.groupBy || "config") === state.groupBy) : [];
  const automaticConfigFrame = state.groupBy === "config" && !!data.segmentMirrorConfig;
  mirrorFrameEnabled = !!state.mirrorPool && (rules.length > 0 || automaticConfigFrame);
  if (rules.length) {
    const categoryFields = {
      config: data.segmentConfig, scene: data.segmentScene, vr: data.segmentVr,
      fly: data.segmentFly, folder: data.segmentFolder,
    };
    const categoryField = categoryFields[state.groupBy] || data.segmentConfig;
    const rawNames = header.categories[state.groupBy] || header.categories.config;
    const names = state.labels?.[state.groupBy]
      || header.displayCategories?.[state.groupBy] || rawNames;
    const key = JSON.stringify({groupBy: state.groupBy, rules, labels: names});
    if (key !== customMirrorKey || !customMirrorGroup) {
      const ns = starts.length, n = data.x.length;
      customMirrorGroup = new Int32Array(ns); customMirrorGroup.fill(-1);
      customSegmentReflected = new Uint8Array(ns);
      customSegmentAxis = new Uint8Array(ns);
      customSegmentCoordinate = new Float32Array(ns);
      const categoryGroup = new Map(), reflectedByCategory = new Map();
      customMirrorLabels = [];
      rules.forEach((rule, group) => {
        const reference = Number(rule.reference), reflected = Number(rule.reflected);
        categoryGroup.set(reference, group); categoryGroup.set(reflected, group);
        reflectedByCategory.set(reflected, {axis: rule.axis === "z" ? "z" : "x", coordinate: Number(rule.coordinate) || 0});
        customMirrorLabels[group] = rule.label || `${names[reference] ?? rawNames[reference]} ↔ ${names[reflected] ?? rawNames[reflected]}`;
      });
      let nextGroup = rules.length;
      for (let category = 0; category < rawNames.length; category += 1) {
        if (!categoryGroup.has(category)) {
          categoryGroup.set(category, nextGroup++); customMirrorLabels.push(names[category] ?? rawNames[category]);
        }
      }
      for (let seg = 0; seg < ns; seg += 1) {
        const category = categoryField[seg], transform = reflectedByCategory.get(category);
        customMirrorGroup[seg] = categoryGroup.get(category);
        if (transform) {
          customSegmentReflected[seg] = 1;
          customSegmentAxis[seg] = transform.axis === "z" ? 1 : 0;
          customSegmentCoordinate[seg] = transform.coordinate;
        }
      }
      const x = new Float32Array(n), z = new Float32Array(n);
      const orientation = new Float32Array(n), movement = new Float32Array(n);
      const minX = new Float32Array(ns), maxX = new Float32Array(ns), minZ = new Float32Array(ns), maxZ = new Float32Array(ns);
      minX.fill(Infinity); maxX.fill(-Infinity); minZ.fill(Infinity); maxZ.fill(-Infinity);
      for (let row = 0; row < n; row += 1) {
        const seg = data.segment[row], reflected = customSegmentReflected[seg] === 1;
        const axis = customSegmentAxis[seg], coordinate = customSegmentCoordinate[seg];
        x[row] = reflected && axis === 0 ? 2 * coordinate - data.x[row] : data.x[row];
        z[row] = reflected && axis === 1 ? 2 * coordinate - data.z[row] : data.z[row];
        orientation[row] = reflected ? wrapAngle(axis === 1 ? 180 - data.orientation[row] : -data.orientation[row]) : data.orientation[row];
        movement[row] = reflected ? wrapAngle(axis === 1 ? 180 - data.movement[row] : -data.movement[row]) : data.movement[row];
        if (Number.isFinite(x[row])) { minX[seg] = Math.min(minX[seg], x[row]); maxX[seg] = Math.max(maxX[seg], x[row]); }
        if (Number.isFinite(z[row])) { minZ[seg] = Math.min(minZ[seg], z[row]); maxZ[seg] = Math.max(maxZ[seg], z[row]); }
      }
      selectCoordinateFrame.custom = {x, z, orientation, movement, minX, maxX, minZ, maxZ};
      customMirrorKey = key;
    }
    const custom = selectCoordinateFrame.custom;
    activeX = custom.x; activeZ = custom.z; activeOrientation = custom.orientation; activeMovement = custom.movement;
    activeSegmentMinX = custom.minX; activeSegmentMaxX = custom.maxX;
    activeSegmentMinZ = custom.minZ; activeSegmentMaxZ = custom.maxZ;
    return;
  }
  customMirrorGroup = null; customMirrorLabels = null; customSegmentReflected = null;
  activeX = mirrorFrameEnabled ? mirroredX : data.x;
  activeZ = data.z;
  activeOrientation = mirrorFrameEnabled ? mirroredOrientation : data.orientation;
  activeMovement = mirrorFrameEnabled ? mirroredMovement : data.movement;
  if (mirrorFrameEnabled) {
    activeSegmentMinX = new Float32Array(starts.length); activeSegmentMaxX = new Float32Array(starts.length);
    for (let seg = 0; seg < starts.length; seg += 1) {
      const reflected = data.segmentMirrorSign?.[seg] < 0;
      activeSegmentMinX[seg] = reflected ? -segmentMaxX[seg] : segmentMinX[seg];
      activeSegmentMaxX[seg] = reflected ? -segmentMinX[seg] : segmentMaxX[seg];
    }
  } else { activeSegmentMinX = segmentMinX; activeSegmentMaxX = segmentMaxX; }
  activeSegmentMinZ = segmentMinZ; activeSegmentMaxZ = segmentMaxZ;
}

function groupField(state, fields) {
  if (state.mirrorPool && customMirrorGroup) return customMirrorGroup;
  if (state.groupBy === "config" && state.mirrorPool && data.segmentMirrorConfig) {
    return data.segmentMirrorConfig;
  }
  return fields[state.groupBy] || data.segmentConfig;
}

function groupLabels(state) {
  if (state.mirrorPool && customMirrorLabels) return customMirrorLabels;
  if (state.groupBy === "config" && state.mirrorPool) {
    return header.displayCategories?.mirrorConfig || header.categories.mirrorConfig;
  }
  return state.labels?.[state.groupBy]
    || header.displayCategories?.[state.groupBy]
    || header.categories[state.groupBy]
    || header.categories.config;
}

function linkHitsRing(x0, z0, x1, z1, ring) {
  const dx = x1 - x0, dz = z1 - z0;
  const length2 = dx * dx + dz * dz;
  const fraction = length2 > 0
    ? Math.max(0, Math.min(1, ((ring.x - x0) * dx + (ring.z - z0) * dz) / length2))
    : 0;
  const px = x0 + dx * fraction, pz = z0 + dz * fraction;
  return (px - ring.x) ** 2 + (pz - ring.z) ** 2 <= ring.r ** 2;
}

function retainedTimeMaximum(seg, rowKeep) {
  for (let row = ends[seg] - 1; row >= starts[seg]; row -= 1) {
    if (rowKeep[row] && Number.isFinite(data.time[row])) return data.time[row];
  }
  return NaN;
}

function buildAnalysis(state) {
  const started = performance.now();
  const ns = starts.length;
  const filterAudit = [{label: "Loaded source", detail: "Unfiltered retained table", segments: ns, rows: data.segment.length}];
  const audit = (label, detail, segmentKeep, rowKeep = null) => {
    let segments = 0, rows = 0;
    for (let seg = 0; seg < ns; seg += 1) {
      if (!segmentKeep[seg]) continue;
      segments += 1;
      if (!rowKeep) rows += ends[seg] - starts[seg];
      else for (let row = starts[seg]; row < ends[seg]; row += 1) rows += rowKeep[row];
    }
    filterAudit.push({label, detail, segments, rows});
  };
  const unchangedAudit = (label, detail) => {
    const previous = filterAudit[filterAudit.length - 1];
    filterAudit.push({label, detail, segments: previous.segments, rows: previous.rows});
  };
  const rangeText = range => Array.isArray(range) && range.length >= 2
    ? `${Number(range[0]).toLocaleString()} to ${Number(range[1]).toLocaleString()}` : "Full range";
  const segmentKeep = new Uint8Array(ns);
  segmentKeep.fill(1);
  const categoryFields = {
    config: data.segmentConfig, scene: data.segmentScene, vr: data.segmentVr,
    fly: data.segmentFly, folder: data.segmentFolder,
  };
  const categoryLabels = {
    config: "Treatments", scene: "Scenes", vr: "VR arenas",
    fly: "Animals", folder: "Source folders",
  };
  for (const [key, field] of Object.entries(categoryFields)) {
    const selected = setOrNull(state.filters?.[key]);
    const total = header.categories[key]?.length || 0;
    const selectedValues = selected ? [...selected].filter(value => value >= 0 && value < total) : [];
    if (selected) for (let seg = 0; seg < ns; seg += 1) if (!selected.has(field[seg])) segmentKeep[seg] = 0;
    audit(categoryLabels[key], `${selectedValues.length} of ${total} values selected`, segmentKeep);
  }
  const r = state.ranges || {};
  const segmentResultant = state.angleSource === "movement"
    ? data.segmentMovementR : data.segmentOrientationR;
  const segmentRanges = [
    ["Trial number", r.trial, data.segmentTrial],
    ["Step / segment", r.step, data.segmentStep],
    ["Replicate order", r.replicate, data.segmentReplicate],
    [`${state.angleSource === "movement" ? "Movement" : "Body"} resultant R`, r.resultant, segmentResultant],
    ["Peak smoothed velocity", r.peak, data.segmentPeakSpeed],
    ["Net displacement", r.displacement, data.segmentDisplacement],
    ["Distance walked", r.distance, data.segmentDistance],
  ];
  for (const [label, range, field] of segmentRanges) {
    if (range) for (let seg = 0; seg < ns; seg += 1) {
      if (segmentKeep[seg] && !inRange(field[seg], range)) segmentKeep[seg] = 0;
    }
    audit(label, rangeText(range), segmentKeep);
  }

  const rowKeep = new Uint8Array(data.segment.length);
  for (let seg = 0; seg < ns; seg += 1) {
    if (segmentKeep[seg]) rowKeep.fill(1, starts[seg], ends[seg]);
  }

  const timeRange = r.time;
  if (timeRange) {
    for (let seg = 0; seg < ns; seg += 1) {
      if (!segmentKeep[seg]) continue;
      for (let row = starts[seg]; row < ends[seg]; row += 1) {
        if (!inRange(data.time[row], timeRange)) rowKeep[row] = 0;
      }
    }
  }
  if (timeRange) audit("Local trial time", `${rangeText(timeRange)} seconds`, segmentKeep, rowKeep);
  else unchangedAudit("Local trial time", "Full duration");

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
  if (jump > 0) audit("Jump rejection", `Raw velocity > ${jump.toLocaleString()} · ±${(buffer * 1000).toLocaleString()} ms`, segmentKeep, rowKeep);
  else unchangedAudit("Jump rejection", "Off");

  const minDisplacement = Math.max(0, Number(state.minDisplacement) || 0);
  if (minDisplacement > 0) {
    for (let seg = 0; seg < ns; seg += 1) {
      if (!segmentKeep[seg]) continue;
      let first = -1, last = -1;
      for (let i = starts[seg]; i < ends[seg]; i += 1) if (rowKeep[i]) { if (first < 0) first = i; last = i; }
      if (first < 0 || Math.hypot(activeX[last] - activeX[first], activeZ[last] - activeZ[first]) < minDisplacement) {
        segmentKeep[seg] = 0; rowKeep.fill(0, starts[seg], ends[seg]);
      }
    }
  }
  if (minDisplacement > 0) audit("Minimum displacement", `At least ${minDisplacement.toLocaleString()}`, segmentKeep, rowKeep);
  else unchangedAudit("Minimum displacement", "Off");

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
  if (trim > 0) audit("Edge trim", `${trim.toLocaleString()} retained samples from each end`, segmentKeep, rowKeep);
  else unchangedAudit("Edge trim", "Off");

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
        if (Math.hypot(activeX[row] - target.x, activeZ[row] - target.z) > roiReach) continue;
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
  if (state.roiEntered || state.roiTrim) audit("ROI mask",
    `${state.roiEntered ? "Reached trials only" : "All trials"}${state.roiTrim ? " · trim after exit" : ""} · radius ${roiReach.toLocaleString()}`,
    segmentKeep, rowKeep);
  else unchangedAudit("ROI mask", `Off · reach radius ${roiReach.toLocaleString()}`);

  const visibleSegments = [];
  let visibleRows = 0;
  for (let seg = 0; seg < ns; seg += 1) {
    if (!segmentKeep[seg]) continue;
    let count = 0;
    for (let i = starts[seg]; i < ends[seg]; i += 1) count += rowKeep[i];
    if (!count) { segmentKeep[seg] = 0; continue; }
    visibleRows += count; visibleSegments.push(seg);
  }

  let panelNames = ["All data"], panelKeys = [0];
  const segmentPanel = new Int32Array(ns); segmentPanel.fill(-1);
  if (state.groupBy === "all") {
    for (const seg of visibleSegments) segmentPanel[seg] = 0;
  } else {
    const field = groupField(state, categoryFields);
    const labels = groupLabels(state);
    const visibleCategories = [];
    const seenCategories = new Set();
    for (const seg of visibleSegments) {
      const category = field[seg];
      if (!seenCategories.has(category)) { seenCategories.add(category); visibleCategories.push(category); }
    }
    panelKeys = stablePanelKeys(state, labels, visibleCategories);
    const panelByCategory = new Map(panelKeys.map((category, index) => [category, index]));
    panelNames = panelKeys.map(category => labels[category] ?? "unknown");
    for (const seg of visibleSegments) {
      const category = field[seg];
      segmentPanel[seg] = panelByCategory.get(category);
    }
    if (!panelNames.length) { panelNames = ["No matching data"]; panelKeys = []; }
  }

  const animals = new Set();
  for (const seg of visibleSegments) animals.add(`${data.segmentFly[seg]}@${data.segmentVr[seg]}`);
  const durations = visibleSegments
    .map(seg => retainedTimeMaximum(seg, rowKeep))
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
  const result = {
    rowKeep, segmentKeep, visibleSegments, segmentPanel, panelNames, panelKeys,
    panelCount: panelNames.length, visibleRows, animals: animals.size,
    roiStats, roiBaseSegments, segmentOutcome, panelRois,
    roiCounts: {left: roiLeft, right: roiRight}, roiReach,
    durationSummary,
    filterAudit,
    filterMs: performance.now() - started,
  };
  result.boundsSource = result;
  return result;
}

function applyCurtain(state, base) {
  const activeRings = state.ringEnabled && Array.isArray(state.rings)
    ? state.rings.map(ring => ({
      x: Number(ring.x) || 0, z: Number(ring.z) || 0,
      r: Math.max(.000001, Number(ring.r) || .000001),
    })) : [];
  if (!activeRings.length) return base;
  const started = performance.now(), requireAll = state.ringMatch === "all";
  const rowKeep = base.rowKeep.slice(), segmentKeep = base.segmentKeep.slice();
  const visibleSegments = [];
  let visibleRows = 0;
  for (const seg of base.visibleSegments) {
    const hits = new Uint8Array(activeRings.length);
    let hitCount = 0, previous = -1;
    const minX = activeSegmentMinX[seg];
    const maxX = activeSegmentMaxX[seg];
    for (let index = 0; index < activeRings.length; index += 1) {
      const ring = activeRings[index];
      if (maxX < ring.x - ring.r || minX > ring.x + ring.r
          || activeSegmentMaxZ[seg] < ring.z - ring.r || activeSegmentMinZ[seg] > ring.z + ring.r) hits[index] = 2;
    }
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!rowKeep[row]) continue;
      for (let index = 0; index < activeRings.length; index += 1) {
        if (hits[index]) continue;
        const ring = activeRings[index];
        const inside = (activeX[row] - ring.x) ** 2 + (activeZ[row] - ring.z) ** 2 <= ring.r ** 2;
        if (inside || (previous >= 0 && linkHitsRing(
          activeX[previous], activeZ[previous], activeX[row], activeZ[row], ring,
        ))) { hits[index] = 1; hitCount += 1; }
      }
      previous = row;
      if ((requireAll && hitCount === activeRings.length) || (!requireAll && hitCount > 0)) break;
    }
    const qualifies = requireAll ? hitCount === activeRings.length : hitCount > 0;
    if (!qualifies) {
      segmentKeep[seg] = 0; rowKeep.fill(0, starts[seg], ends[seg]);
      continue;
    }
    visibleSegments.push(seg);
    for (let row = starts[seg]; row < ends[seg]; row += 1) visibleRows += rowKeep[row];
  }
  const animals = new Set();
  for (const seg of visibleSegments) animals.add(`${data.segmentFly[seg]}@${data.segmentVr[seg]}`);
  const durations = visibleSegments.map(seg => retainedTimeMaximum(seg, rowKeep)).filter(Number.isFinite).sort((a, b) => a - b);
  const left = new Uint32Array(base.panelCount), right = new Uint32Array(base.panelCount);
  for (const seg of visibleSegments) {
    const panel = base.segmentPanel[seg];
    if (base.segmentOutcome[seg] === 1) left[panel] += 1;
    else if (base.segmentOutcome[seg] === 2) right[panel] += 1;
  }
  return {
    ...base, rowKeep, segmentKeep, visibleSegments, visibleRows, animals: animals.size,
    roiCounts: {left, right},
    durationSummary: {
      median: percentile(durations, .5), p95: percentile(durations, .95),
      p99: percentile(durations, .99), max: durations.length ? durations[durations.length - 1] : 0,
    },
    boundsSource: base,
    filterAudit: [...(base.filterAudit || []), {
      label: "Curtain observer",
      detail: `${activeRings.length} ring${activeRings.length === 1 ? "" : "s"} · ${requireAll ? "all rings" : "any ring"}`,
      segments: visibleSegments.length, rows: visibleRows,
    }],
    filterMs: base.filterMs + performance.now() - started,
  };
}

function pooledAnalysis(analysis) {
  const sourceBase = analysis.boundsSource || analysis;
  const pool = (source, pooledBounds = null) => {
    const segmentPanel = new Int32Array(starts.length); segmentPanel.fill(-1);
    for (const segment of source.visibleSegments) segmentPanel[segment] = 0;
    const panelRois = [], seen = new Set();
    for (const roi of source.panelRois || []) {
      const key = `${Number(roi.x).toFixed(4)}|${Number(roi.z).toFixed(4)}|${roi.side}`;
      if (seen.has(key)) continue;
      seen.add(key); panelRois.push({...roi, panel: 0});
    }
    const left = Array.from(source.roiCounts?.left || []).reduce((sum, value) => sum + value, 0);
    const right = Array.from(source.roiCounts?.right || []).reduce((sum, value) => sum + value, 0);
    const result = {
      ...source, segmentPanel, panelNames: ["All visible panels"], panelKeys: [0],
      panelCount: 1, panelRois, roiCounts: {left: Uint32Array.of(left), right: Uint32Array.of(right)},
      overviewPooled: true,
    };
    result.boundsSource = pooledBounds || result;
    return result;
  };
  const pooledBase = pool(sourceBase);
  return analysis === sourceBase ? pooledBase : pool(analysis, pooledBase);
}

function percentile(sorted, q) {
  if (!sorted.length) return 0;
  const at = Math.max(0, Math.min(sorted.length - 1, (sorted.length - 1) * q));
  const lo = Math.floor(at), hi = Math.ceil(at), f = at - lo;
  return sorted[lo] * (1 - f) + sorted[hi] * f;
}

function applyPanelMapping(state, analysis) {
  const ns = starts.length;
  const fields = {
    config: data.segmentConfig, scene: data.segmentScene, vr: data.segmentVr,
    fly: data.segmentFly, folder: data.segmentFolder,
  };
  let panelNames = ["All data"], panelKeys = [0];
  const segmentPanel = new Int32Array(ns); segmentPanel.fill(-1);
  if (state.groupBy === "all") {
    for (const seg of analysis.visibleSegments) segmentPanel[seg] = 0;
  } else {
    const field = groupField(state, fields);
    const labels = groupLabels(state);
    const visibleCategories = [], seen = new Set();
    for (const seg of analysis.visibleSegments) {
      const category = field[seg];
      if (!seen.has(category)) { seen.add(category); visibleCategories.push(category); }
    }
    panelKeys = stablePanelKeys(state, labels, visibleCategories);
    const indices = new Map(panelKeys.map((category, index) => [category, index]));
    panelNames = panelKeys.map(category => labels[category] ?? "unknown");
    for (const seg of analysis.visibleSegments) segmentPanel[seg] = indices.get(field[seg]);
    if (!panelNames.length) { panelNames = ["No matching data"]; panelKeys = []; }
  }
  const panelRois = [], roiSeen = new Set();
  const left = new Uint32Array(panelNames.length), right = new Uint32Array(panelNames.length);
  for (const seg of analysis.visibleSegments) {
    const panel = segmentPanel[seg];
    if (analysis.segmentOutcome[seg] === 1) left[panel] += 1;
    else if (analysis.segmentOutcome[seg] === 2) right[panel] += 1;
    for (const target of targetsForSegment(seg)) {
      const key = `${panel}|${Number(target.x).toFixed(4)}|${Number(target.z).toFixed(4)}|${target.side}`;
      if (roiSeen.has(key)) continue;
      roiSeen.add(key);
      panelRois.push({panel, x: Number(target.x), z: Number(target.z), side: target.side, reach: analysis.roiReach});
    }
  }
  Object.assign(analysis, {
    segmentPanel, panelNames, panelKeys, panelCount: panelNames.length,
    panelRois, roiCounts: {left, right},
  });
}

function spatialBounds(analysis, pct = 98) {
  const source = analysis?.boundsSource || analysis;
  const sampleX = [], sampleZ = [];
  const stride = Math.max(1, Math.floor(Math.max(1, source.visibleRows) / 60000));
  let seen = 0;
  for (let i = 0; i < activeX.length; i += 1) {
    if (!source.rowKeep[i]) continue;
    if ((seen++ % stride) !== 0) continue;
    if (Number.isFinite(activeX[i]) && Number.isFinite(activeZ[i])) { sampleX.push(activeX[i]); sampleZ.push(activeZ[i]); }
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
    case "config": return (state.mirrorPool && customMirrorGroup
      ? customMirrorGroup[seg] : (state.mirrorPool && data.segmentMirrorConfig
        ? data.segmentMirrorConfig[seg] : data.segmentConfig[seg])) % CATEGORY_COLORS;
    case "scene": return data.segmentScene[seg] % CATEGORY_COLORS;
    case "vr": return data.segmentVr[seg] % CATEGORY_COLORS;
    case "folder": return data.segmentFolder[seg] % CATEGORY_COLORS;
    case "roi": return analysis.segmentOutcome[seg] === 1 ? 0 : (analysis.segmentOutcome[seg] === 2 ? 1 : 17);
    case "trial": return sequentialIndex(data.segmentTrial[seg], header.ranges.trial);
    case "replicate": return sequentialIndex(data.segmentReplicate[seg], header.ranges.replicate);
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

function buildTrajectory(state, analysis) {
  const started = performance.now();
  const ns = starts.length;
  const trajectorySegments = analysis.visibleSegments;
  const eligibleCount = new Uint32Array(ns);
  let totalLinks = 0;
  for (const seg of trajectorySegments) {
    let count = 0;
    for (let i = starts[seg]; i < ends[seg]; i += 1) if (analysis.rowKeep[i] && eligibleForDrawing(i, state)) count += 1;
    eligibleCount[seg] = count;
    if (count > 1) totalLinks += count - 1;
  }
  const budget = Math.max(1000, Math.floor(Number(state.pointBudget) || 150000));
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
  const segmentLabels = new Array(starts.length);
  for (const seg of trajectorySegments) {
    segmentLabels[seg] = `${header.categories.file[data.segmentFile[seg]]} · replicate ${data.segmentReplicate[seg]} · trial ${data.segmentTrial[seg]} / step ${data.segmentStep[seg]}`;
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
          vertices[v] = activeX[previous]; vertices[v + 1] = activeZ[previous];
          vertices[v + 2] = activeX[i]; vertices[v + 3] = activeZ[i];
          panels[a] = analysis.segmentPanel[seg]; panels[a + 1] = analysis.segmentPanel[seg];
          colors[a] = rowColor(previous, seg, state, analysis);
          colors[a + 1] = rowColor(i, seg, state, analysis);
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
    rings: state.rings || [],
    ringEnabled: !!state.ringEnabled,
    ringMatch: state.ringMatch || "any",
    ringMatches: analysis.visibleSegments.length,
    visibleSegments: analysis.visibleSegments.length,
    segmentCount: starts.length,
    segmentLabels,
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
  const centreOrigin = state.gridOrigin !== "edge";
  const axisCells = () => centreOrigin ? half * 2 + 1 : half * 2;
  if (axisCells() > maxAxis) {
    half = Math.max(5, Math.floor((maxAxis - (centreOrigin ? 1 : 0)) / 2));
    bin = extent / half;
  }
  const nx = axisCells(), nz = nx;
  const x0 = centreOrigin ? -(half + .5) * bin : -half * bin;
  return {bin, nx, nz, x0, z0: x0, gridOrigin: centreOrigin ? "center" : "edge", bounds};
}

function spatialCommon(state, analysis, grid) {
  return {
    ...grid, panelCount: analysis.panelCount, panelNames: analysis.panelNames,
    columns: Number(state.panelColumns) || 0,
    rois: analysis.panelRois,
    roiCounts: {left: Array.from(analysis.roiCounts.left), right: Array.from(analysis.roiCounts.right)},
    reach: analysis.roiReach,
    rings: state.rings || [], ringEnabled: !!state.ringEnabled,
    ringMatch: state.ringMatch || "any",
  };
}

function occupancyPlanFor(state, analysis) {
  const base = analysis.boundsSource || analysis;
  const key = `${lastAnalysisKey}|${lastPanelKey}|${state.binSize}|${state.boundPercent}|${state.gridOrigin}|${base.overviewPooled ? "pooled" : "panels"}`;
  if (lastOccupancyPlan && lastOccupancyPlanKey === key) return lastOccupancyPlan;
  const grid = gridGeometry(state, base), cells = grid.nx * grid.nz;
  const contributions = new Array(starts.length);
  for (const segment of base.visibleSegments) {
    const panel = base.segmentPanel[segment];
    if (panel < 0) continue;
    const map = new Map();
    for (let row = starts[segment]; row < ends[segment]; row += 1) {
      if (!base.rowKeep[row]) continue;
      const ix = Math.floor((activeX[row] - grid.x0) / grid.bin);
      const iz = Math.floor((activeZ[row] - grid.z0) / grid.bin);
      if (ix < 0 || ix >= grid.nx || iz < 0 || iz >= grid.nz) continue;
      const index = panel * cells + iz * grid.nx + ix;
      map.set(index, (map.get(index) || 0) + 1);
    }
    contributions[segment] = {
      indices: Uint32Array.from(map.keys()), counts: Uint32Array.from(map.values()),
    };
  }
  lastOccupancyPlan = {key, grid, cells, contributions}; lastOccupancyPlanKey = key;
  return lastOccupancyPlan;
}

function buildOccupancy(state, analysis) {
  const started = performance.now(), plan = occupancyPlanFor(state, analysis);
  const {grid, cells, contributions} = plan;
  const count = new Float32Array(analysis.panelCount * cells);
  for (const segment of analysis.visibleSegments) {
    const contribution = contributions[segment];
    if (!contribution) continue;
    for (let index = 0; index < contribution.indices.length; index += 1) {
      count[contribution.indices[index]] += contribution.counts[index];
    }
  }
  const time = new Float32Array(count.length);
  for (let i = 0; i < count.length; i += 1) time[i] = count[i] * medianDt;
  const common = spatialCommon(state, analysis, grid);
  lastOccupancy = {common, count, time};
  lastOccupancyKey = `${lastAnalysisKey}|${lastCurtainKey}|${lastPanelKey}|${state.binSize}|${state.boundPercent}|${state.gridOrigin}|${analysis.overviewPooled ? "pooled" : "panels"}`;
  return {...common, count: count.slice(), time: time.slice(), buildMs: performance.now() - started};
}

function occupancyFor(state, analysis) {
  const key = `${lastAnalysisKey}|${lastCurtainKey}|${lastPanelKey}|${state.binSize}|${state.boundPercent}|${state.gridOrigin}|${analysis.overviewPooled ? "pooled" : "panels"}`;
  if (lastOccupancy && lastOccupancyKey === key) return lastOccupancy;
  buildOccupancy(state, analysis);
  return lastOccupancy;
}

function buildDirection(state, analysis) {
  const started = performance.now();
  const occupancy = occupancyFor(state, analysis);
  const {common, count, time} = occupancy;
  const cells = common.nx * common.nz;
  const sumSin = new Float64Array(count.length), sumCos = new Float64Array(count.length);
  const sumSpeed = new Float64Array(count.length), validSpeed = new Uint32Array(count.length);
  const validDirection = new Uint32Array(count.length);
  const base = analysis.boundsSource || analysis;
  const planKey = `${lastOccupancyPlanKey}|${state.angleSource}|${!!state.movingOnly}|${Number(state.walkThreshold) || 0}`;
  if (!lastDirectionPlan || lastDirectionPlanKey !== planKey) {
    const heading = state.angleSource === "movement" ? activeMovement : activeOrientation;
    const contributions = new Array(starts.length);
    for (const segment of base.visibleSegments) {
      const panel = base.segmentPanel[segment];
      if (panel < 0) continue;
      const map = new Map();
      for (let row = starts[segment]; row < ends[segment]; row += 1) {
        if (!base.rowKeep[row]) continue;
        const angle = heading[row];
        if (!Number.isFinite(angle) || (state.movingOnly && data.speed[row] < (Number(state.walkThreshold) || 0))) continue;
        const ix = Math.floor((activeX[row] - common.x0) / common.bin);
        const iz = Math.floor((activeZ[row] - common.z0) / common.bin);
        if (ix < 0 || ix >= common.nx || iz < 0 || iz >= common.nz) continue;
        const index = panel * cells + iz * common.nx + ix;
        const value = map.get(index) || {sin: 0, cos: 0, n: 0, speed: 0, speedN: 0};
        const radians = angle * Math.PI / 180;
        value.sin += Math.sin(radians); value.cos += Math.cos(radians); value.n += 1;
        if (Number.isFinite(data.speed[row]) && data.speed[row] >= 0) { value.speed += data.speed[row]; value.speedN += 1; }
        map.set(index, value);
      }
      contributions[segment] = [...map].map(([index, value]) => ({index, ...value}));
    }
    lastDirectionPlan = {contributions}; lastDirectionPlanKey = planKey;
  }
  for (const segment of analysis.visibleSegments) {
    for (const value of lastDirectionPlan.contributions[segment] || []) {
      sumSin[value.index] += value.sin; sumCos[value.index] += value.cos;
      validDirection[value.index] += value.n; sumSpeed[value.index] += value.speed; validSpeed[value.index] += value.speedN;
    }
  }
  const angle = new Float32Array(count.length), strength = new Float32Array(count.length);
  const meanSpeed = new Float32Array(count.length);
  angle.fill(NaN);
  for (let i = 0; i < count.length; i += 1) {
    if (validDirection[i]) {
      angle[i] = Math.atan2(sumSin[i], sumCos[i]) * 180 / Math.PI;
      strength[i] = Math.hypot(sumSin[i], sumCos[i]) / validDirection[i];
      meanSpeed[i] = validSpeed[i] ? sumSpeed[i] / validSpeed[i] : 0;
    }
  }
  return {...common, angle, strength, meanSpeed, abundance: count.slice(), time: time.slice(),
    buildMs: performance.now() - started};
}

function buildTransition(state, analysis) {
  const started = performance.now();
  const grid = gridGeometry(state, analysis), cells = grid.nx * grid.nz;
  const axis = state.transitionAxis === "x" ? "x" : "z";
  const coordinate = axis === "x" ? activeX : activeZ;
  const entered = new Uint32Array(analysis.panelCount * cells);
  const crossed = new Uint32Array(entered.length), ended = new Uint32Array(entered.length);
  const startsByBin = new Map();
  for (const seg of analysis.visibleSegments) {
    let first = -1;
    for (let row = starts[seg]; row < ends[seg]; row += 1) if (analysis.rowKeep[row]) { first = row; break; }
    if (first < 0) continue;
    const bin = Math.round(coordinate[first] / Math.max(grid.bin, 1e-9));
    startsByBin.set(bin, (startsByBin.get(bin) || 0) + 1);
  }
  let modalBin = 0, modalCount = -1;
  for (const [bin, count] of startsByBin) if (count > modalCount || (count === modalCount && Math.abs(bin) < Math.abs(modalBin))) {
    modalBin = bin; modalCount = count;
  }
  const split = state.transitionSplit != null && Number.isFinite(Number(state.transitionSplit))
    ? Number(state.transitionSplit) : modalBin * grid.bin;
  for (const seg of analysis.visibleSegments) {
    const start = starts[seg], end = ends[seg], length = end - start;
    const suffixMin = new Float32Array(length + 1), suffixMax = new Float32Array(length + 1);
    suffixMin[length] = Infinity; suffixMax[length] = -Infinity;
    let minimum = Infinity, maximum = -Infinity, finalCoordinate = NaN;
    for (let offset = length - 1; offset >= 0; offset -= 1) {
      const row = start + offset;
      if (analysis.rowKeep[row] && Number.isFinite(coordinate[row])) {
        minimum = Math.min(minimum, coordinate[row]); maximum = Math.max(maximum, coordinate[row]);
        if (!Number.isFinite(finalCoordinate)) finalCoordinate = coordinate[row];
      }
      suffixMin[offset] = minimum; suffixMax[offset] = maximum;
    }
    const seen = new Set(), panel = analysis.segmentPanel[seg];
    for (let offset = 0; offset < length; offset += 1) {
      const row = start + offset;
      if (!analysis.rowKeep[row]) continue;
      const ix = Math.floor((activeX[row] - grid.x0) / grid.bin);
      const iz = Math.floor((activeZ[row] - grid.z0) / grid.bin);
      if (ix < 0 || ix >= grid.nx || iz < 0 || iz >= grid.nz || panel < 0) continue;
      const local = iz * grid.nx + ix;
      if (seen.has(local)) continue;
      seen.add(local);
      const index = panel * cells + local, upper = coordinate[row] >= split;
      entered[index] += 1;
      if (upper ? suffixMin[Math.min(length, offset + 1)] < split : suffixMax[Math.min(length, offset + 1)] > split) crossed[index] += 1;
      if (Number.isFinite(finalCoordinate) && (upper ? finalCoordinate < split : finalCoordinate > split)) ended[index] += 1;
    }
  }
  return {...spatialCommon(state, analysis, grid), entered, crossed, ended, split, axis,
    buildMs: performance.now() - started};
}

function unitColor(seg, state, analysis) {
  return rowColor(starts[seg], seg, state, analysis);
}

function buildPolar(state, analysis) {
  const started = performance.now();
  const ns = starts.length, heading = state.angleSource === "movement" ? activeMovement : activeOrientation;
  const densityBins = 72;
  const animalCount = Math.max(1, header.categories.animal.length);
  const headingDensity = new Uint32Array(analysis.panelCount * animalCount * densityBins);
  const sumSin = new Float64Array(ns), sumCos = new Float64Array(ns), valid = new Uint32Array(ns), available = new Uint32Array(ns);
  for (const seg of analysis.visibleSegments) {
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!analysis.rowKeep[row]) continue;
      available[seg] += 1;
      const angle = heading[row];
      if (!Number.isFinite(angle) || (state.movingOnly && data.speed[row] < (Number(state.walkThreshold) || 0))) continue;
      const densityBin = Math.max(0, Math.min(densityBins - 1,
        Math.floor((wrapAngle(angle) + 180) / 360 * densityBins)));
      headingDensity[(analysis.segmentPanel[seg] * animalCount
        + data.segmentAnimal[seg]) * densityBins + densityBin] += 1;
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
        replicate: NaN, trial: NaN, step: NaN, sin: 0, cos: 0, count: 0,
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
        replicate: data.segmentReplicate[seg],
        trial: data.segmentTrial[seg], step: data.segmentStep[seg],
        sin: sumSin[seg], cos: sumCos[seg], count: valid[seg],
        color: unitColor(seg, state, analysis), seed: seg,
      });
    }
  }
  const angle = new Float32Array(units.length), r = new Float32Array(units.length), panel = new Uint16Array(units.length), color = new Uint8Array(units.length), sample = new Float32Array(units.length);
  const animal = new Uint16Array(units.length), replicate = new Float32Array(units.length), trial = new Float32Array(units.length), step = new Float32Array(units.length);
  const popSin = new Float64Array(analysis.panelCount), popCos = new Float64Array(analysis.panelCount), popWeight = new Float64Array(analysis.panelCount);
  for (let i = 0; i < units.length; i += 1) {
    const unit = units[i], magnitude = Math.hypot(unit.sin, unit.cos);
    angle[i] = Math.atan2(unit.sin, unit.cos) * 180 / Math.PI; r[i] = magnitude / Math.max(1, unit.count);
    panel[i] = unit.panel; color[i] = unit.color; sample[i] = hashSample(unit.seed, state.sampleSeed || 0);
    animal[i] = unit.animal; replicate[i] = unit.replicate; trial[i] = unit.trial; step[i] = unit.step;
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
    mode: state.polarMode || "vectors",
    angle, r, panel, color, sample, animal, replicate, trial, step,
    headingDensity, densityBins,
    populationAngle, populationR, panelCount: analysis.panelCount,
    panelNames: analysis.panelNames, animalNames: header.categories.animal,
    columns: Number(state.panelColumns) || 0, units: units.length,
    buildMs: performance.now() - started,
  };
}

function buildHeading(state, analysis) {
  const heading = state.angleSource === "movement" ? activeMovement : activeOrientation;
  const maxTime = Math.max(.001, playbackLimit(state, analysis));
  const mode = state.headingMode || "trial";
  const timeBin = Math.max(.05, Number(state.headingBin) || 2);
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
  const segmentMetric = seg => {
    let complete = true;
    for (let row = starts[seg]; row < ends[seg]; row += 1) if (!analysis.rowKeep[row]) { complete = false; break; }
    if (complete) return {
      distance: data.segmentDistance[seg], displacement: data.segmentDisplacement[seg],
      speed: data.segmentMedianSpeed[seg], tortuosity: data.segmentTortuosity[seg],
    };
    let first = -1, last = -1, previous = -1, distance = 0;
    const speeds = [], tortuosities = [];
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!analysis.rowKeep[row]) { previous = -1; continue; }
      if (first < 0) first = row;
      last = row;
      if (previous >= 0) distance += Math.hypot(
        activeX[row] - activeX[previous], activeZ[row] - activeZ[previous],
      );
      previous = row;
      if (Number.isFinite(data.speed[row])) speeds.push(data.speed[row]);
      if (Number.isFinite(data.tortuosity[row])) tortuosities.push(data.tortuosity[row]);
    }
    speeds.sort((a, b) => a - b); tortuosities.sort((a, b) => a - b);
    return {
      distance,
      displacement: first >= 0 && last >= 0
        ? Math.hypot(activeX[last] - activeX[first], activeZ[last] - activeZ[first]) : NaN,
      speed: speeds.length ? percentile(speeds, .5) : NaN,
      tortuosity: tortuosities.length ? percentile(tortuosities, .5) : NaN,
    };
  };
  const segmentValues = new Map();
  const metricFor = seg => {
    if (!segmentValues.has(seg)) segmentValues.set(seg, segmentMetric(seg));
    return segmentValues.get(seg);
  };
  const units = [];
  if (state.statsUnit === "animal") {
    const map = new Map();
    for (const seg of analysis.visibleSegments) {
      const metric = metricFor(seg);
      const key = `${analysis.segmentPanel[seg]}|${data.segmentFly[seg]}|${data.segmentVr[seg]}`;
      const unit = map.get(key) || {
        panel: analysis.segmentPanel[seg], animal: data.segmentAnimal[seg],
        replicate: NaN, trial: NaN, step: NaN, distance: 0, displacement: 0,
        speed: 0, tortuosity: 0, n: 0,
      };
      unit.distance += metric.distance; unit.displacement += metric.displacement;
      unit.speed += metric.speed; unit.tortuosity += metric.tortuosity; unit.n += 1; map.set(key, unit);
    }
    for (const unit of map.values()) {
      unit.distance /= unit.n; unit.displacement /= unit.n; unit.speed /= unit.n; unit.tortuosity /= unit.n; units.push(unit);
    }
  } else {
    for (const seg of analysis.visibleSegments) {
      const metric = metricFor(seg);
      units.push({
        panel: analysis.segmentPanel[seg], animal: data.segmentAnimal[seg],
        replicate: data.segmentReplicate[seg],
        trial: data.segmentTrial[seg], step: data.segmentStep[seg],
        distance: metric.distance, displacement: metric.displacement,
        speed: metric.speed, tortuosity: metric.tortuosity,
      });
    }
  }
  return {
    panel: Uint16Array.from(units.map(u => u.panel)),
    animal: Uint16Array.from(units.map(u => u.animal)),
    replicate: Float32Array.from(units.map(u => u.replicate)),
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

function errorFunction(value) {
  const sign = value < 0 ? -1 : 1, x = Math.abs(value);
  const t = 1 / (1 + .3275911 * x);
  const polynomial = (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - .284496736) * t + .254829592) * t;
  return sign * (1 - polynomial * Math.exp(-x * x));
}

function normalCdf(value) { return .5 * (1 + errorFunction(value / Math.SQRT2)); }

function rankCombined(groups) {
  const combined = [];
  groups.forEach((values, group) => values.forEach(value => {
    if (Number.isFinite(value)) combined.push({value, group, rank: 0});
  }));
  combined.sort((a, b) => a.value - b.value);
  let tieCorrection = 0;
  for (let start = 0; start < combined.length;) {
    let end = start + 1;
    while (end < combined.length && combined[end].value === combined[start].value) end += 1;
    const rank = (start + 1 + end) / 2;
    for (let index = start; index < end; index += 1) combined[index].rank = rank;
    const tie = end - start; tieCorrection += tie * tie * tie - tie; start = end;
  }
  return {combined, tieCorrection};
}

function mannWhitney(first, second) {
  const groups = [first.filter(Number.isFinite), second.filter(Number.isFinite)];
  const n1 = groups[0].length, n2 = groups[1].length, n = n1 + n2;
  if (!n1 || !n2) return {u: NaN, z: NaN, p: NaN};
  const ranked = rankCombined(groups); let r1 = 0;
  for (const item of ranked.combined) if (item.group === 0) r1 += item.rank;
  const u1 = r1 - n1 * (n1 + 1) / 2, u2 = n1 * n2 - u1, u = Math.min(u1, u2);
  const tieFactor = n > 1 ? ranked.tieCorrection / (n * (n - 1)) : 0;
  const variance = n1 * n2 / 12 * ((n + 1) - tieFactor);
  const z = variance > 0 ? (Math.abs(u1 - n1 * n2 / 2) - .5) / Math.sqrt(variance) : 0;
  return {u, z, p: Math.max(0, Math.min(1, 2 * (1 - normalCdf(Math.abs(z)))))};
}

function kruskalWallis(groups) {
  const usable = groups.map(values => values.filter(Number.isFinite));
  const ranked = rankCombined(usable), n = ranked.combined.length;
  if (n < 2 || usable.filter(values => values.length).length < 2) return {h: NaN, df: 0, p: NaN};
  const sums = new Float64Array(usable.length);
  for (const item of ranked.combined) sums[item.group] += item.rank;
  let h = 0;
  for (let group = 0; group < usable.length; group += 1) if (usable[group].length) h += sums[group] * sums[group] / usable[group].length;
  h = 12 * h / (n * (n + 1)) - 3 * (n + 1);
  const correction = 1 - ranked.tieCorrection / Math.max(1, n * n * n - n);
  h = correction > 0 ? h / correction : h;
  const df = usable.filter(values => values.length).length - 1;
  const cube = Math.pow(Math.max(0, h) / Math.max(1, df), 1 / 3);
  const z = (cube - (1 - 2 / (9 * Math.max(1, df)))) / Math.sqrt(2 / (9 * Math.max(1, df)));
  return {h, df, p: Math.max(0, Math.min(1, 1 - normalCdf(z)))};
}

function holmAdjust(tests) {
  const ordered = tests.map((test, index) => ({index, p: test.p})).filter(item => Number.isFinite(item.p)).sort((a, b) => a.p - b.p);
  let previous = 0;
  for (let rank = 0; rank < ordered.length; rank += 1) {
    const adjusted = Math.min(1, Math.max(previous, ordered[rank].p * (ordered.length - rank)));
    tests[ordered[rank].index].adjustedP = adjusted; previous = adjusted;
  }
  return tests;
}

function rayleigh(values) {
  const finite = values.filter(Number.isFinite), n = finite.length;
  if (!n) return {n: 0, angle: NaN, r: NaN, z: NaN, p: NaN};
  let sine = 0, cosine = 0;
  for (const value of finite) { const radians = value * Math.PI / 180; sine += Math.sin(radians); cosine += Math.cos(radians); }
  const r = Math.hypot(sine, cosine) / n, z = n * r * r;
  let p = Math.exp(-z);
  if (n < 50) p *= 1 + (2 * z - z * z) / (4 * n) - (24 * z - 132 * z * z + 76 * z ** 3 - 9 * z ** 4) / (288 * n * n);
  return {n, angle: Math.atan2(sine, cosine) * 180 / Math.PI, r, z, p: Math.max(0, Math.min(1, p))};
}

function circularMean(values) {
  let sine = 0, cosine = 0;
  for (const value of values) {
    const radians = value * Math.PI / 180;
    sine += Math.sin(radians); cosine += Math.cos(radians);
  }
  return Math.atan2(sine, cosine) * 180 / Math.PI;
}

function circularDistance(first, second) {
  return Math.abs(wrapAngle(first - second));
}

function circularMeanPermutation(firstValues, secondValues, seed = 1, permutations = 399) {
  const first = firstValues.filter(Number.isFinite), second = secondValues.filter(Number.isFinite);
  if (first.length < 3 || second.length < 3) return {difference: NaN, p: NaN, permutations: 0};
  const rayFirst = rayleigh(first), raySecond = rayleigh(second);
  const difference = circularDistance(rayFirst.angle, raySecond.angle);
  if (!(rayFirst.p < .05) || !(raySecond.p < .05)) {
    return {difference, p: NaN, permutations: 0, comparable: false};
  }
  const cap = 2000;
  const sample = values => values.length <= cap ? values
    : Array.from({length: cap}, (_, index) => values[Math.floor(index * values.length / cap)]);
  const a = sample(first), b = sample(second), combined = [...a, ...b];
  const indices = Uint32Array.from({length: combined.length}, (_, index) => index);
  let random = (seed ^ 0x9e3779b9) >>> 0, extreme = 0;
  const nextRandom = () => {
    random = (Math.imul(random, 1664525) + 1013904223) >>> 0;
    return random / 4294967296;
  };
  for (let iteration = 0; iteration < permutations; iteration += 1) {
    for (let index = indices.length - 1; index > 0; index -= 1) {
      const other = Math.floor(nextRandom() * (index + 1));
      const temporary = indices[index]; indices[index] = indices[other]; indices[other] = temporary;
    }
    const permutedFirst = [], permutedSecond = [];
    for (let index = 0; index < indices.length; index += 1) {
      (index < a.length ? permutedFirst : permutedSecond).push(combined[indices[index]]);
    }
    if (circularDistance(circularMean(permutedFirst), circularMean(permutedSecond)) >= difference - 1e-9) extreme += 1;
  }
  return {difference, p: (extreme + 1) / (permutations + 1), permutations, comparable: true};
}

function buildStatistics(metrics, polar) {
  const metricNames = ["distance", "displacement", "speed", "tortuosity"];
  const metricResults = metricNames.map(metric => {
    const groups = Array.from({length: metrics.panelCount}, () => []);
    for (let index = 0; index < metrics[metric].length; index += 1) groups[metrics.panel[index]]?.push(metrics[metric][index]);
    const omnibus = kruskalWallis(groups), pairwise = [];
    for (let first = 0; first < groups.length; first += 1) for (let second = first + 1; second < groups.length; second += 1) {
      pairwise.push({first, second, ...mannWhitney(groups[first], groups[second])});
    }
    holmAdjust(pairwise);
    return {metric, omnibus, counts: groups.map(group => group.length), pairwise};
  });
  const polarGroups = Array.from({length: polar.panelCount}, () => []);
  for (let index = 0; index < polar.angle.length; index += 1) polarGroups[polar.panel[index]]?.push(polar.angle[index]);
  const circularPairwise = [];
  for (let first = 0; first < polarGroups.length; first += 1) for (let second = first + 1; second < polarGroups.length; second += 1) {
    circularPairwise.push({first, second,
      ...circularMeanPermutation(polarGroups[first], polarGroups[second], (first + 1) * 8191 + (second + 1) * 131071)});
  }
  holmAdjust(circularPairwise);
  return {
    panels: metrics.panelNames,
    metrics: metricResults,
    rayleigh: polarGroups.map((values, panel) => ({panel, ...rayleigh(values)})),
    circularPairwise,
    method: "Kruskal–Wallis omnibus tests use two-sided Mann–Whitney pairwise comparisons with Holm family-wise correction. Rayleigh tests assess circular non-uniformity. Mean-angle comparisons use deterministic label permutation only when both groups have a non-uniform direction, then apply Holm correction.",
  };
}

function wilcoxonPaired(first, second) {
  const differences = [];
  for (let index = 0; index < Math.min(first.length, second.length); index += 1) {
    const difference = Number(first[index]) - Number(second[index]);
    if (Number.isFinite(difference) && Math.abs(difference) > 1e-12) differences.push({difference, magnitude: Math.abs(difference), rank: 0});
  }
  differences.sort((a, b) => a.magnitude - b.magnitude);
  for (let start = 0; start < differences.length;) {
    let end = start + 1;
    while (end < differences.length && differences[end].magnitude === differences[start].magnitude) end += 1;
    const rank = (start + 1 + end) / 2;
    for (let index = start; index < end; index += 1) differences[index].rank = rank;
    start = end;
  }
  let positive = 0, negative = 0;
  for (const item of differences) if (item.difference > 0) positive += item.rank; else negative += item.rank;
  const n = differences.length, mean = n * (n + 1) / 4, variance = n * (n + 1) * (2 * n + 1) / 24;
  const z = variance > 0 ? (Math.abs(positive - mean) - .5) / Math.sqrt(variance) : 0;
  return {n, w: Math.min(positive, negative), p: n ? Math.max(0, Math.min(1, 2 * (1 - normalCdf(Math.abs(z))))) : NaN};
}

function buildWindows(state, analysis) {
  const requested = Array.isArray(state.windows) && state.windows.length >= 2 ? state.windows.slice(0, 2) : [
    {name: "Window A", xmin: -10, xmax: 0, zmin: -5, zmax: 5},
    {name: "Window B", xmin: 0, xmax: 10, zmin: -5, zmax: 5},
  ];
  const windows = requested.map((window, index) => ({
    name: String(window.name || `Window ${index + 1}`),
    xmin: Math.min(Number(window.xmin), Number(window.xmax)), xmax: Math.max(Number(window.xmin), Number(window.xmax)),
    zmin: Math.min(Number(window.zmin), Number(window.zmax)), zmax: Math.max(Number(window.zmin), Number(window.zmax)),
  }));
  const heading = state.angleSource === "movement" ? activeMovement : activeOrientation;
  const summaries = Array.from({length: analysis.panelCount * 2}, (_, index) => ({
    panel: Math.floor(index / 2), window: index % 2, rows: 0, segments: 0, distance: 0, sine: 0, cosine: 0, valid: 0,
  }));
  const paired = Array.from({length: analysis.panelCount}, () => ({residence: [[], []], distance: [[], []]}));
  for (const seg of analysis.visibleSegments) {
    const panel = analysis.segmentPanel[seg], segmentValues = [{rows: 0, distance: 0, previous: -1}, {rows: 0, distance: 0, previous: -1}];
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!analysis.rowKeep[row]) continue;
      for (let index = 0; index < 2; index += 1) {
        const window = windows[index];
        if (activeX[row] < window.xmin || activeX[row] > window.xmax || activeZ[row] < window.zmin || activeZ[row] > window.zmax) {
          segmentValues[index].previous = -1; continue;
        }
        const local = segmentValues[index], summary = summaries[panel * 2 + index];
        local.rows += 1; summary.rows += 1;
        if (local.previous >= 0) {
          const distance = Math.hypot(activeX[row] - activeX[local.previous], activeZ[row] - activeZ[local.previous]);
          if (Number.isFinite(distance)) { local.distance += distance; summary.distance += distance; }
        }
        local.previous = row;
        if (Number.isFinite(heading[row])) {
          const radians = heading[row] * Math.PI / 180;
          summary.sine += Math.sin(radians); summary.cosine += Math.cos(radians); summary.valid += 1;
        }
      }
    }
    for (let index = 0; index < 2; index += 1) {
      if (segmentValues[index].rows) summaries[panel * 2 + index].segments += 1;
      paired[panel].residence[index].push(segmentValues[index].rows * medianDt);
      paired[panel].distance[index].push(segmentValues[index].distance);
    }
  }
  return {
    windows, panels: analysis.panelNames,
    summaries: summaries.map(summary => ({
      panel: summary.panel, window: summary.window, rows: summary.rows, segments: summary.segments,
      seconds: summary.rows * medianDt, distance: summary.distance,
      angle: summary.valid ? Math.atan2(summary.sine, summary.cosine) * 180 / Math.PI : NaN,
      r: summary.valid ? Math.hypot(summary.sine, summary.cosine) / summary.valid : NaN,
    })),
    paired: paired.map((values, panel) => ({
      panel, residence: wilcoxonPaired(values.residence[0], values.residence[1]),
      distance: wilcoxonPaired(values.distance[0], values.distance[1]),
    })),
    method: "Within-segment Wilcoxon signed-rank comparisons between the two editable rectangles. Residence uses retained local sample time; path length only joins consecutive retained samples inside the same rectangle.",
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
        const fields = {
          config: data.segmentConfig, scene: data.segmentScene, vr: data.segmentVr,
          fly: data.segmentFly, folder: data.segmentFolder,
        };
        const field = groupField(state, fields);
        const label = groupLabels(state)[field[seg]];
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
  const heading = state.angleSource === "movement" ? activeMovement : activeOrientation;
  const stride = Math.max(1, Math.ceil(analysis.visibleRows / 100000));
  let seen = 0;
  for (const seg of analysis.visibleSegments) {
    const targets = targetsForSegment(seg);
    const sideTargets = [targets.find(target => target.side === "left"), targets.find(target => target.side === "right")];
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!analysis.rowKeep[row] || !Number.isFinite(heading[row]) || (seen++ % stride) !== 0) continue;
      for (let side = 0; side < 2; side += 1) {
        const target = sideTargets[side]; if (!target) continue;
        const bearing = Math.atan2(target.x - activeX[row], target.z - activeZ[row]) * 180 / Math.PI;
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

function buildFilterHistograms(analysis, state) {
  const segmentResultant = state.angleSource === "movement"
    ? data.segmentMovementR : data.segmentOrientationR;
  const fields = {
    trial: data.segmentTrial, step: data.segmentStep,
    replicate: data.segmentReplicate, resultant: segmentResultant,
    peak: data.segmentPeakSpeed,
    displacement: data.segmentDisplacement, distance: data.segmentDistance,
  };
  const result = {};
  for (const [key, field] of Object.entries(fields)) {
    const descriptor = header.filterHistograms?.[key];
    if (!descriptor?.edges?.length) continue;
    const edges = descriptor.edges.map(Number), counts = new Uint32Array(edges.length - 1);
    const lo = edges[0], hi = edges[edges.length - 1], span = Math.max(1e-12, hi - lo);
    let overflow = 0;
    for (const segment of analysis.visibleSegments) {
      const value = field[segment];
      if (!Number.isFinite(value) || value < lo) continue;
      if (value > hi) { overflow += 1; continue; }
      const index = Math.max(0, Math.min(counts.length - 1,
        Math.floor((value - lo) / span * counts.length)));
      counts[index] += 1;
    }
    result[key] = {edges, counts, overflow, visible: analysis.visibleSegments.length};
  }
  const timeDescriptor = header.filterHistograms?.time;
  if (timeDescriptor?.edges?.length) {
    const edges = timeDescriptor.edges.map(Number), counts = new Uint32Array(edges.length - 1);
    const lo = edges[0], hi = edges[edges.length - 1], span = Math.max(1e-12, hi - lo);
    let overflow = 0;
    for (const segment of analysis.visibleSegments) {
      for (let row = starts[segment]; row < ends[segment]; row += 1) {
        if (!analysis.rowKeep[row]) continue;
        const value = data.time[row];
        if (!Number.isFinite(value) || value < lo) continue;
        if (value > hi) { overflow += 1; continue; }
        const index = Math.max(0, Math.min(counts.length - 1,
          Math.floor((value - lo) / span * counts.length)));
        counts[index] += 1;
      }
    }
    result.time = {edges, counts, overflow, visible: analysis.visibleRows};
  }
  return result;
}

function buffers(value, into = []) {
  if (!value || typeof value !== "object") return into;
  if (ArrayBuffer.isView(value)) { into.push(value.buffer); return into; }
  for (const child of Object.values(value)) buffers(child, into);
  return into;
}

function compute(message) {
  const {state, requestId} = message;
  selectCoordinateFrame(state);
  const key = analysisKey(state);
  const ringKey = curtainKey(state);
  const layoutKey = panelKey(state);
  const analysisChanged = key !== lastAnalysisKey;
  const curtainChanged = ringKey !== lastCurtainKey;
  const panelChanged = layoutKey !== lastPanelKey;
  if (analysisChanged || !lastBaseAnalysis) {
    lastBaseAnalysis = buildAnalysis(state); lastAnalysisKey = key;
    lastPanelKey = layoutKey;
  } else if (panelChanged) {
    applyPanelMapping(state, lastBaseAnalysis); lastPanelKey = layoutKey;
  }
  if (analysisChanged || curtainChanged || panelChanged || !lastAnalysis) {
    lastAnalysis = applyCurtain(state, lastBaseAnalysis);
    lastCurtainKey = ringKey;
    lastOccupancy = null; lastOccupancyKey = "";
  }
  const scope = message.scope || "full";
  const productAnalysis = ["overview", "overviewPreview"].includes(scope) && state.overviewGrouping !== "panels"
    ? pooledAnalysis(lastAnalysis) : lastAnalysis;
  const products = {};
  if (["full", "trajectory", "movement", "color", "sample", "overview", "transition"].includes(scope)) {
    products.trajectory = buildTrajectory(state, productAnalysis);
  }
  if (["full", "spatial", "heatmap", "overview", "overviewPreview"].includes(scope)) {
    products.heatmap = buildOccupancy(state, productAnalysis);
  }
  if (["full", "spatial", "direction", "overview", "overviewPreview"].includes(scope)) {
    products.direction = buildDirection(state, productAnalysis);
    if (scope === "spatial") {
      products.transition = buildTransition(state, lastAnalysis);
      lastTransitionGeometry = {
        nx: products.transition.nx, nz: products.transition.nz,
        x0: products.transition.x0, z0: products.transition.z0, bin: products.transition.bin,
      };
    }
  } else if (scope === "movement") {
    products.direction = buildDirection(state, productAnalysis);
  }
  if (["full", "movement", "color", "sample", "statistics", "polar", "overview", "background"].includes(scope)) {
    products.polar = buildPolar(state, productAnalysis);
  }
  if (["full", "movement", "color", "sample", "heading", "playback", "background"].includes(scope)) {
    products.heading = buildHeading(state, productAnalysis);
  }
  if (["full", "statistics", "metrics", "background"].includes(scope)) {
    products.metrics = buildMetrics(state, productAnalysis);
  }
  if (["full", "statistics", "movement", "roi", "background"].includes(scope)) {
    products.roi = buildRoi(state, productAnalysis);
  }
  if (["full", "diagnostics"].includes(scope)) {
    products.diagnostics = buildDiagnostics();
  }
  if (analysisChanged || curtainChanged || panelChanged || scope === "full") {
    products.filterHistograms = buildFilterHistograms(lastAnalysis, state);
  }
  if (scope === "statistics") {
    products.statistics = buildStatistics(products.metrics, products.polar);
  }
  if (scope === "transition") {
    products.transition = buildTransition(state, lastAnalysis);
    lastTransitionGeometry = {
      nx: products.transition.nx, nz: products.transition.nz,
      x0: products.transition.x0, z0: products.transition.z0, bin: products.transition.bin,
    };
  }
  if (["windows", "background"].includes(scope)) products.windows = buildWindows(state, lastAnalysis);
  const segmentOptions = analysisChanged || curtainChanged || panelChanged || scope === "full"
    ? lastAnalysis.visibleSegments.map(seg => ({
      code: seg,
      label: `${header.categories.file[data.segmentFile[seg]]} · replicate ${data.segmentReplicate[seg]} · trial ${data.segmentTrial[seg]} / step ${data.segmentStep[seg]}`,
      duration: retainedTimeMaximum(seg, lastAnalysis.rowKeep),
      animal: data.segmentAnimal[seg],
    }))
    : null;
  const result = {
    type: "result", requestId, scope, quiet: !!message.quiet, products,
    summary: {
      visibleRows: lastAnalysis.visibleRows,
      visibleSegments: lastAnalysis.visibleSegments.length,
      animals: lastAnalysis.animals,
      panels: lastAnalysis.panelCount,
      filterMs: lastAnalysis.filterMs,
      durationSummary: lastAnalysis.durationSummary,
      panelKeys: lastAnalysis.panelKeys,
      panelNames: lastAnalysis.panelNames,
      segmentOptions,
      filterAudit: lastAnalysis.filterAudit,
    },
  };
  postMessage(result, [...new Set(buffers(products))]);
}

function inspectPoint(message) {
  if (!lastAnalysis) return;
  const tolerance = Math.max(1e-9, Number(message.tolerance) || 0);
  let bestRow = -1, bestDistance = tolerance * tolerance;
  for (let row = 0; row < activeX.length; row += 1) {
    if (!lastAnalysis.rowKeep[row]) continue;
    const seg = data.segment[row];
    if (lastAnalysis.segmentPanel[seg] !== message.panel) continue;
    const dx = activeX[row] - message.x, dz = activeZ[row] - message.z;
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
      replicate: data.segmentReplicate[seg],
      trial: data.segmentTrial[seg], step: data.segmentStep[seg],
      config: header.categories.config[data.segmentConfig[seg]],
      scene: header.categories.scene[data.segmentScene[seg]],
      vr: header.categories.vr[data.segmentVr[seg]],
      fly: header.categories.fly[data.segmentFly[seg]],
      x: activeX[bestRow], z: activeZ[bestRow], time: data.time[bestRow],
      points: data.segmentPoints[seg], distance: data.segmentDistance[seg],
      displacement: data.segmentDisplacement[seg], peakSpeed: data.segmentPeakSpeed[seg],
      medianSpeed: data.segmentMedianSpeed[seg], tortuosity: data.segmentTortuosity[seg],
    },
  });
}

function inspectTransition(message) {
  if (!lastAnalysis || !lastTransitionGeometry) return;
  const panel = Number(message.panel), wantedX = Number(message.ix), wantedZ = Number(message.iz), matches = [];
  for (const seg of lastAnalysis.visibleSegments) {
    if (lastAnalysis.segmentPanel[seg] !== panel) continue;
    let matched = false;
    for (let row = starts[seg]; row < ends[seg]; row += 1) {
      if (!lastAnalysis.rowKeep[row]) continue;
      const ix = Math.floor((activeX[row] - lastTransitionGeometry.x0) / lastTransitionGeometry.bin);
      const iz = Math.floor((activeZ[row] - lastTransitionGeometry.z0) / lastTransitionGeometry.bin);
      if (ix === wantedX && iz === wantedZ) { matched = true; break; }
    }
    if (matched) matches.push(seg);
  }
  postMessage({type: "transition-inspect-result", requestId: message.requestId, segments: matches,
    panel, ix: wantedX, iz: wantedZ, x: message.x, z: message.z});
}

self.onmessage = event => {
  try {
    const message = event.data;
    if (message.type === "init") initDataset(message);
    else if (message.type === "compute") compute(message);
    else if (message.type === "inspect") inspectPoint(message);
    else if (message.type === "transition-inspect") inspectTransition(message);
  } catch (error) {
    postMessage({type: "error", requestId: event.data?.requestId, error: error?.stack || String(error)});
  }
};
