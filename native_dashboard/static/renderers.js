const PANEL_PAD_X = 0.04;
const PANEL_PAD_Y = 0.065;
const CATEGORY_COLORS = 16;
export const NEUTRAL_INDEX = 20;
export const SEQUENTIAL_OFFSET = 24;

const CATEGORICAL = [
  "#246b78", "#c95b3f", "#71813f", "#7a5b91", "#c58b2f", "#3f7a5d",
  "#9f4e68", "#506d9a", "#a56a39", "#567c73", "#8b664d", "#5d5f91",
  "#bd7160", "#6f874f", "#7c5571", "#2c768d", "#7f8c87", "#52645e",
  "#ad806a", "#496b64", "#8d918e", "#777b78", "#c6cbc8", "#9aa19d",
];

function sequentialColor(t) {
  const stops = [
    [0.00, [68, 1, 84]], [0.13, [71, 44, 122]], [0.25, [59, 82, 139]],
    [0.38, [44, 113, 142]], [0.50, [33, 145, 140]], [0.63, [39, 173, 129]],
    [0.75, [92, 200, 99]], [0.88, [170, 220, 50]], [1.00, [253, 231, 37]],
  ];
  t = Math.max(0, Math.min(1, t));
  let i = 1;
  while (i < stops.length && t > stops[i][0]) i += 1;
  const [aT, a] = stops[i - 1];
  const [bT, b] = stops[Math.min(i, stops.length - 1)];
  const f = bT === aT ? 0 : (t - aT) / (bT - aT);
  return `rgb(${a.map((v, j) => Math.round(v + (b[j] - v) * f)).join(",")})`;
}

const PALETTE = Array.from({length: 64}, (_, i) => {
  if (i >= SEQUENTIAL_OFFSET) return sequentialColor((i - SEQUENTIAL_OFFSET) / 31);
  return CATEGORICAL[i % CATEGORICAL.length];
});
PALETTE[NEUTRAL_INDEX] = "#7f8985";
export const NATIVE_PALETTE = PALETTE;

function hexRgb(color) {
  if (color.startsWith("rgb")) return color.match(/[\d.]+/g).slice(0, 3).map(Number);
  const value = color.replace("#", "");
  return [0, 2, 4].map(i => parseInt(value.slice(i, i + 2), 16));
}

const PALETTE_FLOATS = new Float32Array(PALETTE.flatMap(color => {
  const [r, g, b] = hexRgb(color);
  return [r / 255, g / 255, b / 255, color === "#7f8985" ? .32 : .46];
}));

export function formatNumber(value, digits = 1) {
  if (!Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  if (abs >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(abs >= 1e4 ? 0 : 1)}k`;
  if (abs > 0 && abs < .01) return value.toExponential(1);
  return value.toFixed(digits).replace(/\.0+$|(?<=\.[0-9])0+$/, "");
}

function autoColumns(count, requested = 0) {
  if (requested > 0) return Math.max(1, Math.min(4, requested));
  if (count <= 1) return 1;
  if (count <= 4) return 2;
  if (count <= 9) return 3;
  return 4;
}

function panelLayout(width, height, count, requested = 0) {
  const cols = autoColumns(count, requested);
  const rows = Math.max(1, Math.ceil(Math.max(1, count) / cols));
  return {cols, rows, cellW: width / cols, cellH: height / rows};
}

function panelPane(layout, panel) {
  const col = panel % layout.cols;
  const row = Math.floor(panel / layout.cols);
  const cellLeft = col * layout.cellW;
  const cellTop = row * layout.cellH;
  const left = cellLeft + Math.max(34, layout.cellW * PANEL_PAD_X);
  const right = cellLeft + layout.cellW - Math.max(15, layout.cellW * .025);
  const top = cellTop + Math.max(27, layout.cellH * PANEL_PAD_Y * .55);
  const bottom = cellTop + layout.cellH - Math.max(30, layout.cellH * .045);
  return {
    left, right, top, bottom,
    width: Math.max(2, right - left), height: Math.max(2, bottom - top),
    size: Math.max(2, Math.min(right - left, bottom - top)),
    cellLeft, cellTop,
  };
}

function equalScaleView(view, pane) {
  if (!view) return null;
  const cx = (view.xmin + view.xmax) / 2, cz = (view.zmin + view.zmax) / 2;
  let xr = Math.max(1e-9, view.xmax - view.xmin);
  let zr = Math.max(1e-9, view.zmax - view.zmin);
  const paneAspect = Math.max(1e-9, pane.width / pane.height);
  if (xr / zr < paneAspect) xr = zr * paneAspect;
  else zr = xr / paneAspect;
  return {xmin: cx - xr / 2, xmax: cx + xr / 2, zmin: cz - zr / 2, zmax: cz + zr / 2};
}

function niceScaleDistance(span) {
  const raw = Math.max(1e-12, span * .18);
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  return (unit >= 5 ? 5 : (unit >= 2 ? 2 : 1)) * power;
}

function niceTickStep(span, target = 5) {
  const raw = Math.max(1e-12, Math.abs(span) / Math.max(2, target));
  const power = 10 ** Math.floor(Math.log10(raw));
  const scaled = raw / power;
  const unit = scaled <= 1 ? 1 : (scaled <= 2 ? 2 : (scaled <= 5 ? 5 : 10));
  return unit * power;
}

function niceTicks(minimum, maximum, target = 5) {
  const step = niceTickStep(maximum - minimum, target);
  const first = Math.ceil((minimum - step * 1e-9) / step) * step;
  const values = [];
  for (let value = first; value <= maximum + step * 1e-9 && values.length < 12; value += step) {
    values.push(Math.abs(value) < step * 1e-9 ? 0 : value);
  }
  return {step, values};
}

function formatTick(value, step) {
  if (!Number.isFinite(value)) return "—";
  const digits = step >= 1 ? 0 : Math.min(3, Math.max(1, Math.ceil(-Math.log10(step))));
  return value.toFixed(digits).replace(/\.0+$|(?<=\.[0-9])0+$/, "");
}

function circleEntryFraction(x0, z0, x1, z1, ring) {
  const rx = x0 - ring.x, rz = z0 - ring.z;
  const radius2 = ring.r * ring.r;
  if (rx * rx + rz * rz <= radius2) return 0;
  const dx = x1 - x0, dz = z1 - z0;
  const a = dx * dx + dz * dz;
  if (!(a > 0)) return null;
  const b = 2 * (rx * dx + rz * dz);
  const c = rx * rx + rz * rz - radius2;
  const discriminant = b * b - 4 * a * c;
  if (discriminant < 0) return null;
  const root = Math.sqrt(discriminant);
  const first = (-b - root) / (2 * a), second = (-b + root) / (2 * a);
  if (first >= 0 && first <= 1) return first;
  if (second >= 0 && second <= 1) return second;
  return null;
}

function squareBounds(bounds) {
  if (!bounds) return {xmin: -1, xmax: 1, zmin: -1, zmax: 1};
  const cx = (bounds.xmin + bounds.xmax) / 2;
  const cz = (bounds.zmin + bounds.zmax) / 2;
  const half = Math.max(bounds.xmax - bounds.xmin, bounds.zmax - bounds.zmin, 1e-6) * .54;
  return {xmin: cx - half, xmax: cx + half, zmin: cz - half, zmax: cz + half};
}

function setupCanvas(canvas, width, height) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.round(width * dpr));
  const h = Math.max(1, Math.round(height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  return dpr;
}

function drawPanelChrome(ctx, width, height, data, view, options = {}) {
  const count = Math.max(1, data?.panelCount || 1);
  const layout = panelLayout(width, height, count, data?.columns || 0);
  ctx.save();
  ctx.font = "600 11px Inter, system-ui, sans-serif";
  ctx.textBaseline = "top";
  for (let panel = 0; panel < count; panel += 1) {
    const pane = panelPane(layout, panel);
    const {left, right, top, bottom} = pane;
    const shown = equalScaleView(view, pane);
    const xTicks = shown ? niceTicks(shown.xmin, shown.xmax, 5) : null;
    const zTicks = shown ? niceTicks(shown.zmin, shown.zmax, 5) : null;
    if (shown && options.showGrid) {
      ctx.save();
      for (const value of xTicks.values) {
        const x = left + (value - shown.xmin) / (shown.xmax - shown.xmin) * pane.width;
        ctx.strokeStyle = value === 0 ? "rgba(28,58,52,.22)" : "rgba(34,63,57,.075)";
        ctx.lineWidth = value === 0 ? 1.25 : 1;
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
      }
      for (const value of zTicks.values) {
        const y = bottom - (value - shown.zmin) / (shown.zmax - shown.zmin) * pane.height;
        ctx.strokeStyle = value === 0 ? "rgba(28,58,52,.22)" : "rgba(34,63,57,.075)";
        ctx.lineWidth = value === 0 ? 1.25 : 1;
        ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
      }
      ctx.restore();
    }
    ctx.strokeStyle = "rgba(24,34,31,.14)";
    ctx.lineWidth = 1;
    ctx.strokeRect(left + .5, top + .5, right - left - 1, bottom - top - 1);
    ctx.fillStyle = "#27332f";
    const title = data?.panelNames?.[panel] || "All data";
    ctx.fillText(String(title).slice(0, 42), left + 3, Math.max(pane.cellTop + 3, top - 18));
    if (view && !options.hideTicks) {
      ctx.font = "9px ui-monospace, SFMono-Regular, monospace";
      ctx.fillStyle = "#7a827e";
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      for (const value of xTicks.values) {
        const x = left + (value - shown.xmin) / (shown.xmax - shown.xmin) * pane.width;
        ctx.fillText(formatTick(value, xTicks.step), x, bottom + 5);
      }
      ctx.textAlign = "right"; ctx.textBaseline = "middle";
      for (const value of zTicks.values) {
        const y = bottom - (value - shown.zmin) / (shown.zmax - shown.zmin) * pane.height;
        ctx.fillText(formatTick(value, zTicks.step), left - 5, y);
      }
      ctx.textBaseline = "top";
      ctx.textAlign = "left";
      ctx.font = "600 11px Inter, system-ui, sans-serif";
    }
    if (shown && options.scaleBar) {
      const distance = niceScaleDistance(shown.xmax - shown.xmin);
      const pixels = distance / (shown.xmax - shown.xmin) * pane.width;
      const x = left + 12, y = bottom - 14;
      ctx.strokeStyle = "rgba(28,47,43,.82)"; ctx.fillStyle = "rgba(28,47,43,.88)";
      ctx.lineWidth = 2.5;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + pixels, y); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x, y - 4); ctx.lineTo(x, y + 4);
      ctx.moveTo(x + pixels, y - 4); ctx.lineTo(x + pixels, y + 4); ctx.stroke();
      ctx.font = "700 9px Inter, system-ui, sans-serif";
      ctx.textBaseline = "bottom";
      ctx.fillText(`${formatNumber(distance, 1)} cm`, x, y - 5);
    }
  }
  ctx.restore();
  return layout;
}

function drawPanelRois(ctx, width, height, data, view, visible = true) {
  if (!visible || !data?.rois?.length || !view) return;
  const layout = panelLayout(width, height, data.panelCount, data.columns);
  ctx.save();
  for (const roi of data.rois) {
    if (roi.panel < 0 || roi.panel >= data.panelCount) continue;
    const {left, right, top, bottom} = panelPane(layout, roi.panel);
    const shown = equalScaleView(view, panelPane(layout, roi.panel));
    const x = left + (roi.x - shown.xmin) / (shown.xmax - shown.xmin) * (right - left);
    const y = bottom - (roi.z - shown.zmin) / (shown.zmax - shown.zmin) * (bottom - top);
    const radius = Math.abs(roi.reach / (shown.xmax - shown.xmin) * (right - left));
    ctx.strokeStyle = roi.side === "left" ? "rgba(49,95,140,.68)" : "rgba(201,91,63,.68)";
    ctx.fillStyle = roi.side === "left" ? "rgba(49,95,140,.05)" : "rgba(201,91,63,.05)";
    ctx.setLineDash([4, 3]); ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }
  ctx.setLineDash([]);
  if (data.roiCounts) {
    ctx.font = "600 9px Inter, system-ui, sans-serif";
    for (let panel = 0; panel < data.panelCount; panel += 1) {
      const {left, right, top} = panelPane(layout, panel);
      ctx.fillStyle = "#315f8c"; ctx.textAlign = "left";
      ctx.fillText(`L-first ${data.roiCounts.left?.[panel] || 0}`, left + 5, top + 5);
      ctx.fillStyle = "#a64530"; ctx.textAlign = "right";
      ctx.fillText(`R-first ${data.roiCounts.right?.[panel] || 0}`, right - 5, top + 5);
    }
  }
  ctx.restore();
}

function drawPanelRings(ctx, width, height, data, view) {
  if (!data?.ringEnabled || !data?.rings?.length || !view) return;
  const layout = panelLayout(width, height, data.panelCount, data.columns);
  ctx.save(); ctx.setLineDash([]); ctx.font = "700 9px Inter, system-ui, sans-serif";
  for (let panel = 0; panel < data.panelCount; panel += 1) {
    const {left, right, top, bottom} = panelPane(layout, panel);
    const shown = equalScaleView(view, panelPane(layout, panel));
    ctx.save();
    ctx.beginPath(); ctx.rect(left, top, right - left, bottom - top); ctx.clip();
    for (let index = 0; index < data.rings.length; index += 1) {
      const ring = data.rings[index];
      const x = left + (ring.x - shown.xmin) / (shown.xmax - shown.xmin) * (right - left);
      const y = bottom - (ring.z - shown.zmin) / (shown.zmax - shown.zmin) * (bottom - top);
      const radius = Math.abs(ring.r / (shown.xmax - shown.xmin) * (right - left));
      ctx.strokeStyle = "rgba(24,34,31,.82)"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = "rgba(24,34,31,.82)"; ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill();
      ctx.fillText(String(index + 1), x + 5, y - 12);
    }
    ctx.restore();
  }
  ctx.restore();
}

function drawPanelWindows(ctx, width, height, data, view) {
  if (!data?.windows?.length || !view) return;
  const layout = panelLayout(width, height, data.panelCount, data.columns);
  const colors = ["49,95,140", "201,91,63", "14,124,115", "197,139,47"];
  ctx.save(); ctx.font = "700 9px Inter, system-ui, sans-serif";
  for (let panel = 0; panel < data.panelCount; panel += 1) {
    const pane = panelPane(layout, panel), shown = equalScaleView(view, pane);
    for (let index = 0; index < data.windows.length; index += 1) {
      const window = data.windows[index], color = colors[index % colors.length];
      const left = pane.left + (window.xmin - shown.xmin) / (shown.xmax - shown.xmin) * pane.width;
      const right = pane.left + (window.xmax - shown.xmin) / (shown.xmax - shown.xmin) * pane.width;
      const top = pane.bottom - (window.zmax - shown.zmin) / (shown.zmax - shown.zmin) * pane.height;
      const bottom = pane.bottom - (window.zmin - shown.zmin) / (shown.zmax - shown.zmin) * pane.height;
      ctx.fillStyle = `rgba(${color},.035)`; ctx.strokeStyle = `rgba(${color},.58)`;
      ctx.setLineDash([5, 3]); ctx.lineWidth = 1;
      ctx.fillRect(left, top, right - left, bottom - top); ctx.strokeRect(left, top, right - left, bottom - top);
      ctx.setLineDash([]); ctx.fillStyle = `rgb(${color})`;
      ctx.fillText(String.fromCharCode(65 + index), left + 4, top + 4);
    }
  }
  ctx.restore();
}

function installResize(renderer) {
  renderer._resizeObserver = new ResizeObserver(entries => {
    const rect = entries[0]?.contentRect;
    if (!rect) return;
    renderer.width = Math.max(1, rect.width);
    renderer.height = Math.max(1, rect.height);
    renderer.resize();
    renderer.draw();
  });
  renderer._resizeObserver.observe(renderer.host);
}

function installSpatialInteraction(renderer, canvas) {
  let drag = null;
  const tooltip = document.createElement("div");
  tooltip.className = "plot-tooltip";
  tooltip.hidden = true;
  renderer.host.appendChild(tooltip);
  renderer.tooltip = tooltip;
  canvas.tabIndex = 0;
  const trash = document.createElement("div");
  trash.className = "ring-trash-target";
  trash.setAttribute("aria-hidden", "true");
  trash.textContent = "Release to delete";
  renderer.host.appendChild(trash);
  const overTrash = event => {
    const rect = trash.getBoundingClientRect();
    return event.clientX >= rect.left && event.clientX <= rect.right
      && event.clientY >= rect.top && event.clientY <= rect.bottom;
  };

  canvas.addEventListener("pointerdown", event => {
    if (!renderer.data || !renderer.view) return;
    const rect = canvas.getBoundingClientRect();
    const point = renderer.pixelToWorld(event.clientX - rect.left, event.clientY - rect.top);
    if (!point) {
      if (renderer.backgroundHandler) drag = {mode: "background", moved: false, x: event.clientX, y: event.clientY};
      return;
    }
    canvas.setPointerCapture(event.pointerId);
    const ringHit = renderer.hitRing?.(point) || null;
    if (ringHit && renderer.ringMoveHandler) {
      const ring = renderer.data.rings[ringHit.index];
      drag = {
        mode: ringHit.mode, ringIndex: ringHit.index,
        offsetX: point.x - ring.x, offsetZ: point.z - ring.z,
        startRadius: ring.r, moved: false,
      };
      renderer.activeRing = ringHit.index;
      renderer.ringSelectHandler?.(ringHit.index);
      renderer.host.classList.add("ring-dragging");
      canvas.style.cursor = "grabbing";
    } else {
      const layout = panelLayout(renderer.width, renderer.height, renderer.data.panelCount, renderer.data.columns);
      drag = {
        mode: "pan", x: event.clientX, y: event.clientY,
        view: {...renderer.view}, panel: point.panel,
        moved: false,
      };
    }
  });
  canvas.addEventListener("pointerup", event => {
    const finished = drag;
    drag = null;
    canvas.style.cursor = "";
    renderer.host.classList.remove("ring-dragging", "ring-delete-ready");
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) { /* no-op */ }
    if (finished?.mode === "ring-move" || finished?.mode === "ring-resize") {
      if (overTrash(event)) {
        renderer.ringDeleteHandler?.(finished.ringIndex);
        return;
      }
      const rect = canvas.getBoundingClientRect();
      const point = renderer.pixelToWorld(event.clientX - rect.left, event.clientY - rect.top);
      if (point && finished.mode === "ring-resize") renderer.resizeRing(
        finished.ringIndex, Math.hypot(
          point.x - renderer.data.rings[finished.ringIndex].x,
          point.z - renderer.data.rings[finished.ringIndex].z,
        ), true,
      );
      else if (point) renderer.moveRing(finished.ringIndex, point.x - finished.offsetX, point.z - finished.offsetZ, true);
      return;
    }
    if (finished?.mode === "background" && !finished.moved) {
      renderer.backgroundHandler?.();
      return;
    }
    if (finished && !finished.moved && renderer.inspectHandler) {
      const rect = canvas.getBoundingClientRect();
      const point = renderer.pixelToWorld(event.clientX - rect.left, event.clientY - rect.top);
      if (point) renderer.inspectHandler(point, renderer.view);
    }
  });
  canvas.addEventListener("pointercancel", () => {
    drag = null; canvas.style.cursor = "";
    renderer.host.classList.remove("ring-dragging", "ring-delete-ready");
  });
  canvas.addEventListener("pointermove", event => {
    const rect = canvas.getBoundingClientRect();
    if (drag) {
      if (drag.mode === "ring-move" || drag.mode === "ring-resize") {
        renderer.host.classList.toggle("ring-delete-ready", overTrash(event));
        const point = renderer.pixelToWorld(event.clientX - rect.left, event.clientY - rect.top);
        if (point && drag.mode === "ring-resize") renderer.resizeRing(
          drag.ringIndex,
          Math.hypot(point.x - renderer.data.rings[drag.ringIndex].x,
            point.z - renderer.data.rings[drag.ringIndex].z),
          false,
        );
        else if (point) renderer.moveRing(drag.ringIndex, point.x - drag.offsetX, point.z - drag.offsetZ, false);
        drag.moved = true;
        tooltip.hidden = true;
        return;
      }
      if (drag.mode === "background") {
        drag.moved = Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 3;
        return;
      }
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 3) drag.moved = true;
      const pane = panelPane(
        panelLayout(renderer.width, renderer.height, renderer.data.panelCount, renderer.data.columns),
        drag.panel || 0,
      );
      const shown = equalScaleView(drag.view, pane);
      const dx = (event.clientX - drag.x) / pane.width * (shown.xmax - shown.xmin);
      const dz = (event.clientY - drag.y) / pane.height * (shown.zmax - shown.zmin);
      renderer.setView({
        xmin: drag.view.xmin - dx, xmax: drag.view.xmax - dx,
        zmin: drag.view.zmin + dz, zmax: drag.view.zmax + dz,
      }, true);
      renderer.pauseAnimation?.(190);
      tooltip.hidden = true;
      return;
    }
    const point = renderer.pixelToWorld(event.clientX - rect.left, event.clientY - rect.top);
    if (!point) { tooltip.hidden = true; return; }
    const hit = renderer.hitRing?.(point);
    canvas.style.cursor = hit?.mode === "ring-resize" ? "nwse-resize" : (hit ? "grab" : "");
    tooltip.hidden = false;
    tooltip.style.left = `${event.clientX - rect.left}px`;
    tooltip.style.top = `${event.clientY - rect.top}px`;
    tooltip.textContent = renderer.tooltipText?.(point)
      || `${renderer.data.panelNames?.[point.panel] || "All"} · X ${formatNumber(point.x, 1)} · Z ${formatNumber(point.z, 1)}`;
  });
  canvas.addEventListener("pointerleave", () => { if (!drag) tooltip.hidden = true; });
  canvas.addEventListener("wheel", event => {
    if (!renderer.data || !renderer.view) return;
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const point = renderer.pixelToWorld(event.clientX - rect.left, event.clientY - rect.top);
    if (!point) return;
    const factor = Math.exp(Math.max(-700, Math.min(700, event.deltaY)) * .0011);
    const view = renderer.view;
    renderer.setView({
      xmin: point.x + (view.xmin - point.x) * factor,
      xmax: point.x + (view.xmax - point.x) * factor,
      zmin: point.z + (view.zmin - point.z) * factor,
      zmax: point.z + (view.zmax - point.z) * factor,
    }, true);
    renderer.pauseAnimation?.(190);
  }, {passive: false});
  canvas.addEventListener("dblclick", event => {
    event.preventDefault();
    renderer.resetView(true);
  });
  canvas.addEventListener("keydown", event => {
    if (!renderer.data?.ringEnabled || !renderer.data?.rings?.length) return;
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      renderer.ringDeleteHandler?.(Math.max(0, renderer.activeRing || 0));
    }
  });
}

class SpatialBase {
  constructor(host, onViewChange) {
    this.host = host;
    this.onViewChange = onViewChange;
    this.data = null;
    this.view = null;
    this.width = host.clientWidth || 800;
    this.height = host.clientHeight || 500;
    this.roisVisible = true;
    this.gridVisible = true;
    this.cleanMode = false;
  }
  setRoisVisible(value) { this.roisVisible = !!value; this.draw(); }
  setGridVisible(value) { this.gridVisible = !!value; this.draw(); }
  setCleanMode(value) { this.cleanMode = !!value; this.draw(); }
  setColumns(columns) {
    if (!this.data) return;
    this.data.columns = Number(columns) || 0;
    this.draw();
  }
  setInspectHandler(handler) { this.inspectHandler = handler; }
  setBackgroundHandler(handler) { this.backgroundHandler = handler; }
  setRingMoveHandler(handler) { this.ringMoveHandler = handler; }
  setRingDeleteHandler(handler) { this.ringDeleteHandler = handler; }
  setRingSelectHandler(handler) { this.ringSelectHandler = handler; }
  setRings(rings, enabled = true, match = "any") {
    if (!this.data) return;
    this.data.rings = (rings || []).map(ring => ({...ring}));
    this.data.ringEnabled = !!enabled && this.data.rings.length > 0;
    this.data.ringMatch = match;
    this.activeRing = Math.max(0, Math.min(this.data.rings.length - 1, this.activeRing || 0));
    this.draw();
  }
  hitRing(point) {
    if (!this.data?.ringEnabled || !this.data?.rings?.length || !this.view) return null;
    const layout = panelLayout(this.width, this.height, this.data.panelCount, this.data.columns);
    const pane = panelPane(layout, point.panel);
    const shown = equalScaleView(this.view, pane);
    const tolerance = (shown.xmax - shown.xmin) / Math.max(1, pane.width) * 11;
    for (let index = this.data.rings.length - 1; index >= 0; index -= 1) {
      const ring = this.data.rings[index];
      const distance = Math.hypot(point.x - ring.x, point.z - ring.z);
      if (Math.abs(distance - ring.r) <= tolerance) return {index, mode: "ring-resize"};
      if (distance <= tolerance * 1.5) return {index, mode: "ring-move"};
    }
    return null;
  }
  moveRing(index, x, z, final = false) {
    if (!this.data?.rings?.[index] || !Number.isFinite(x) || !Number.isFinite(z)) return;
    this.data.rings[index] = {...this.data.rings[index], x, z};
    if (typeof this.drawOverlay === "function") this.drawOverlay();
    else this.draw();
    this.ringMoveHandler?.(index, x, z, final);
  }
  resizeRing(index, radius, final = false) {
    if (!this.data?.rings?.[index] || !Number.isFinite(radius)) return;
    const ring = this.data.rings[index];
    ring.r = Math.max(.01, radius);
    if (typeof this.drawOverlay === "function") this.drawOverlay();
    else this.draw();
    this.ringMoveHandler?.(index, ring.x, ring.z, final, ring.r);
  }
  setView(view, notify = false) {
    if (!view || !Object.values(view).every(Number.isFinite)) return;
    this.view = {...view};
    this.draw();
    if (notify && this.onViewChange) this.onViewChange({...view}, this);
  }
  resetView(notify = false) {
    if (!this.data?.bounds) return;
    this.setView(squareBounds(this.data.bounds), notify);
  }
  pixelToWorld(px, py) {
    if (!this.data || !this.view) return null;
    const layout = panelLayout(this.width, this.height, this.data.panelCount, this.data.columns);
    const col = Math.max(0, Math.min(layout.cols - 1, Math.floor(px / layout.cellW)));
    const row = Math.max(0, Math.min(layout.rows - 1, Math.floor(py / layout.cellH)));
    const panel = row * layout.cols + col;
    if (panel >= this.data.panelCount) return null;
    const pane = panelPane(layout, panel);
    const shown = equalScaleView(this.view, pane);
    const u = (px - pane.left) / pane.width;
    const v = 1 - (py - pane.top) / pane.height;
    if (u < 0 || u > 1 || v < 0 || v > 1) return null;
    return {
      panel,
      x: shown.xmin + u * (shown.xmax - shown.xmin),
      z: shown.zmin + v * (shown.zmax - shown.zmin),
    };
  }
}

function shader(gl, type, source) {
  const result = gl.createShader(type);
  gl.shaderSource(result, source);
  gl.compileShader(result);
  if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(result));
  return result;
}

function program(gl, vertex, fragment) {
  const result = gl.createProgram();
  gl.attachShader(result, shader(gl, gl.VERTEX_SHADER, vertex));
  gl.attachShader(result, shader(gl, gl.FRAGMENT_SHADER, fragment));
  gl.linkProgram(result);
  if (!gl.getProgramParameter(result, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(result));
  return result;
}

export class TrajectoryRenderer extends SpatialBase {
  constructor(host, onViewChange) {
    super(host, onViewChange);
    this.canvas = document.createElement("canvas");
    this.overlay = document.createElement("canvas");
    host.append(this.canvas, this.overlay);
    this.gl = this.canvas.getContext("webgl2", {antialias: true, alpha: false});
    if (!this.gl) throw new Error("This dashboard requires WebGL2 for dense trajectories.");
    this.ctx = this.overlay.getContext("2d");
    this.fraction = 1;
    this.timeLimit = 1e30;
    this.selectedSegment = -1;
    this.lineWidth = 1.5;
    this.lineOpacity = .58;
    this._initGl();
    installSpatialInteraction(this, this.overlay);
    installResize(this);
  }
  _initGl() {
    const gl = this.gl;
    this.program = program(gl, `#version 300 es
      precision highp float;
      layout(location=0) in vec4 a_segment;
      layout(location=1) in float a_panel;
      layout(location=2) in float a_color;
      layout(location=3) in vec2 a_time;
      layout(location=4) in float a_sample;
      layout(location=5) in float a_visible;
      layout(location=6) in float a_segment_id;
      uniform vec4 u_view;
      uniform vec2 u_grid;
      uniform vec2 u_canvas;
      uniform vec4 u_pane;
      uniform float u_fraction;
      uniform float u_time;
      uniform float u_selected_segment;
      uniform float u_line_width;
      uniform float u_opacity;
      uniform sampler2D u_ring_entries;
      uniform float u_ring_enabled;
      uniform float u_ring_context;
      uniform float u_ring_texture_width;
      uniform vec4 u_palette[64];
      out vec4 v_color;
      out float v_inside;
      out vec2 v_world;

      vec2 worldToClip(vec2 world) {
        float ux = (world.x - u_view.x) / (u_view.y - u_view.x);
        float uz = (world.y - u_view.z) / (u_view.w - u_view.z);
        float col = mod(a_panel, u_grid.x);
        float row = floor(a_panel / u_grid.x);
        vec2 cell = u_canvas / u_grid;
        float px = col * cell.x + u_pane.x + ux * u_pane.z;
        float py = row * cell.y + u_pane.y + (1.0 - uz) * u_pane.w;
        return vec2(px / u_canvas.x * 2.0 - 1.0, 1.0 - py / u_canvas.y * 2.0);
      }

      void main() {
        vec2 world0 = a_segment.xy;
        vec2 world1 = a_segment.zw;
        float fraction = clamp(
          (u_time - a_time.x) / max(0.000001, a_time.y - a_time.x), 0.0, 1.0
        );
        world1 = mix(world0, world1, fraction);
        vec2 p0 = worldToClip(world0);
        vec2 p1 = worldToClip(world1);
        vec2 directionPx = (p1 - p0) * u_canvas * .5;
        vec2 normalPx = length(directionPx) > .0001
          ? normalize(vec2(-directionPx.y, directionPx.x)) * u_line_width * .5
          : vec2(0.0);
        vec2 normal = normalPx * 2.0 / u_canvas;
        int vertex = gl_VertexID % 6;
        bool atEnd = vertex == 2 || vertex == 3 || vertex == 5;
        bool positive = vertex == 1 || vertex == 4 || vertex == 5;
        vec2 base = atEnd ? p1 : p0;
        gl_Position = vec4(base + (positive ? normal : -normal), 0.0, 1.0);
        v_world = atEnd ? world1 : world0;
        int segmentId = max(0, int(a_segment_id + .5));
        int textureWidth = max(1, int(u_ring_texture_width + .5));
        float ringEntry = texelFetch(
          u_ring_entries, ivec2(segmentId % textureWidth, segmentId / textureWidth), 0
        ).r;
        bool ringMatch = ringEntry >= 0.0;
        bool contextPath = u_ring_enabled > .5 && !ringMatch && u_ring_context > .5;
        bool ringVisible = u_ring_enabled < .5 || ringMatch || contextPath;
        float vertexTime = atEnd ? a_time.y : a_time.x;
        bool beforeRing = u_ring_enabled > .5 && ringMatch && vertexTime < ringEntry;
        int colorIndex = (beforeRing || contextPath) ? 20 : clamp(int(a_color + .5), 0, 63);
        v_color = u_palette[colorIndex];
        v_color.a = u_opacity * (contextPath ? .10 : (colorIndex == 20 ? .72 : 1.0));
        bool selected = u_selected_segment < 0.0 || abs(a_segment_id - u_selected_segment) < .5;
        v_inside = (a_sample <= u_fraction && a_time.x <= u_time && a_visible > .5
          && selected && ringVisible) ? 1.0 : 0.0;
      }`, `#version 300 es
      precision highp float;
      in vec4 v_color;
      in float v_inside;
      in vec2 v_world;
      uniform vec4 u_view;
      out vec4 outColor;
      void main() {
        if (v_inside < .5 || v_world.x < u_view.x || v_world.x > u_view.y
          || v_world.y < u_view.z || v_world.y > u_view.w) discard;
        outColor = v_color;
      }
    `);
    this.vao = gl.createVertexArray();
    this.buffers = Array.from({length: 7}, () => gl.createBuffer());
    this.ringTexture = gl.createTexture();
    this.ringEntries = new Float32Array([-1]);
    this.ringTextureWidth = 1;
    this.ringEnabled = false;
    this.ringContext = true;
    this._uploadRingEntries();
  }
  _attribute(index, data, size, type, normalized = false, stride = 0) {
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffers[index]);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(index);
    gl.vertexAttribPointer(index, size, type, normalized, stride, 0);
    gl.vertexAttribDivisor(index, 1);
  }
  setData(data, preserveView = true) {
    this.data = data;
    const gl = this.gl;
    gl.bindVertexArray(this.vao);
    this._attribute(0, data.vertices, 4, gl.FLOAT);
    this._attribute(1, data.panels, 1, gl.UNSIGNED_SHORT, false, 4);
    this._attribute(2, data.colors, 1, gl.UNSIGNED_BYTE, false, 2);
    this._attribute(3, data.times, 2, gl.FLOAT);
    this._attribute(4, data.samples, 1, gl.FLOAT, false, 8);
    this.visibility = new Uint8Array(data.links);
    this.visibility.fill(255);
    this._attribute(5, this.visibility, 1, gl.UNSIGNED_BYTE, true);
    this._attribute(6, data.segments, 1, gl.UNSIGNED_INT, false, 8);
    gl.bindVertexArray(null);
    this.instanceCount = data.links;
    this.ringEntries = new Float32Array(Math.max(1, data.segmentCount || 1));
    this.ringEntries.fill(-1);
    this.ringEnabled = false;
    this._uploadRingEntries();
    if (!preserveView || !this.view) this.view = squareBounds(data.bounds);
    this.draw();
  }
  _uploadRingEntries() {
    const gl = this.gl;
    const maxWidth = Math.max(1, Math.min(2048, gl.getParameter(gl.MAX_TEXTURE_SIZE)));
    this.ringTextureWidth = Math.min(maxWidth, Math.max(1, this.ringEntries.length));
    const height = Math.max(1, Math.ceil(this.ringEntries.length / this.ringTextureWidth));
    const packed = new Float32Array(this.ringTextureWidth * height);
    packed.fill(-1); packed.set(this.ringEntries);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.ringTexture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, this.ringTextureWidth, height, 0, gl.RED, gl.FLOAT, packed);
  }
  setRingObserver(enabled, rings, match = "any", showContext = true) {
    if (!this.data) return {matches: 0, buildMs: 0};
    const started = performance.now();
    const active = (rings || []).filter(ring => Number(ring.r) > 0).map(ring => ({
      x: Number(ring.x) || 0, z: Number(ring.z) || 0, r: Math.max(.01, Number(ring.r) || .01),
    }));
    this.data.rings = active; this.data.ringEnabled = !!enabled; this.data.ringMatch = match;
    this.ringEnabled = !!enabled && active.length > 0;
    this.ringContext = !!showContext;
    this.ringEntries.fill(-1);
    let matches = this.ringEnabled ? 0 : (this.data.visibleSegments || 0);
    if (this.ringEnabled) {
      const hitTimes = active.map(() => {
        const values = new Float32Array(this.ringEntries.length); values.fill(-1); return values;
      });
      for (let link = 0; link < this.data.links; link += 1) {
        const segment = this.data.segments[link * 2];
        const vertex = link * 4, time = link * 2;
        for (let index = 0; index < active.length; index += 1) {
          if (hitTimes[index][segment] >= 0) continue;
          const fraction = circleEntryFraction(
            this.data.vertices[vertex], this.data.vertices[vertex + 1],
            this.data.vertices[vertex + 2], this.data.vertices[vertex + 3], active[index],
          );
          if (fraction != null) hitTimes[index][segment] = this.data.times[time]
            + fraction * (this.data.times[time + 1] - this.data.times[time]);
        }
      }
      for (let segment = 0; segment < this.ringEntries.length; segment += 1) {
        let entry = match === "all" ? -Infinity : Infinity, reached = 0;
        for (const values of hitTimes) if (values[segment] >= 0) {
          reached += 1;
          entry = match === "all" ? Math.max(entry, values[segment]) : Math.min(entry, values[segment]);
        }
        const qualifies = match === "all" ? reached === active.length : reached > 0;
        if (qualifies) { this.ringEntries[segment] = entry; matches += 1; }
      }
    }
    this.data.ringMatches = matches;
    this._uploadRingEntries();
    this.drawGl(); this.drawOverlay();
    return {matches, buildMs: performance.now() - started};
  }
  setAnimalVisibility(visible) {
    this.animalVisibility = visible ? [...visible] : null;
    if (!this.data?.animals || !this.visibility) return;
    for (let i = 0; i < this.visibility.length; i += 1) {
      this.visibility[i] = this.animalVisibility?.[this.data.animals[i * 2]] === false ? 0 : 255;
    }
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffers[5]);
    gl.bufferData(gl.ARRAY_BUFFER, this.visibility, gl.DYNAMIC_DRAW);
    this.drawGl();
  }
  setFraction(value) { this.fraction = Math.max(.01, Math.min(1, value)); this.drawGl(); }
  setTime(value) { this.timeLimit = Number.isFinite(value) ? value : 1e30; this.drawGl(); }
  setPlaybackSegment(value) {
    this.selectedSegment = Number.isFinite(Number(value)) ? Number(value) : -1;
    this.drawGl();
  }
  setLineStyle(width, opacity) {
    this.lineWidth = Math.max(.5, Math.min(8, Number(width) || 1.5));
    this.lineOpacity = Math.max(.03, Math.min(1, Number(opacity) || .58));
    this.drawGl();
  }
  setObservationWindows(windows) {
    if (!this.data) return;
    this.data.windows = (windows || []).map(window => ({...window}));
    this.drawOverlay();
  }
  setSegmentObserver(segmentCodes, showContext = true) {
    if (!this.data) return;
    this.ringEntries.fill(-1);
    for (const code of segmentCodes || []) if (code >= 0 && code < this.ringEntries.length) this.ringEntries[code] = 0;
    this.ringEnabled = !!segmentCodes?.length;
    this.ringContext = !!showContext;
    this.data.ringEnabled = false;
    this._uploadRingEntries(); this.draw();
  }
  resize() {
    const dpr = setupCanvas(this.canvas, this.width, this.height);
    setupCanvas(this.overlay, this.width, this.height);
    this.gl.viewport(0, 0, Math.round(this.width * dpr), Math.round(this.height * dpr));
  }
  drawGl() {
    const gl = this.gl;
    gl.clearColor(1, 1, 1, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (!this.data || !this.view || !this.instanceCount) return;
    const layout = panelLayout(this.width, this.height, this.data.panelCount, this.data.columns);
    const pane = panelPane(layout, 0);
    const shown = equalScaleView(this.view, pane);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(this.program);
    gl.uniform4f(gl.getUniformLocation(this.program, "u_view"), shown.xmin, shown.xmax, shown.zmin, shown.zmax);
    gl.uniform2f(gl.getUniformLocation(this.program, "u_grid"), layout.cols, layout.rows);
    gl.uniform2f(gl.getUniformLocation(this.program, "u_canvas"), this.width, this.height);
    gl.uniform4f(gl.getUniformLocation(this.program, "u_pane"),
      pane.left - pane.cellLeft, pane.top - pane.cellTop, pane.width, pane.height);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_fraction"), this.fraction);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_time"), this.timeLimit);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_selected_segment"), this.selectedSegment);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_line_width"), this.lineWidth);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_opacity"), this.lineOpacity);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.ringTexture);
    gl.uniform1i(gl.getUniformLocation(this.program, "u_ring_entries"), 0);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_ring_enabled"), this.ringEnabled ? 1 : 0);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_ring_context"), this.ringContext ? 1 : 0);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_ring_texture_width"), this.ringTextureWidth);
    gl.uniform4fv(gl.getUniformLocation(this.program, "u_palette[0]"), PALETTE_FLOATS);
    gl.bindVertexArray(this.vao);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, this.instanceCount);
    gl.bindVertexArray(null);
  }
  draw() {
    this.drawGl();
    this.drawOverlay();
  }
  drawOverlay() {
    const dpr = setupCanvas(this.overlay, this.width, this.height);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.clearRect(0, 0, this.width, this.height);
    if (this.data) {
      drawPanelChrome(this.ctx, this.width, this.height, this.data, this.view, {
        hideTicks: this.cleanMode, showGrid: this.gridVisible && !this.cleanMode,
        scaleBar: true,
      });
      drawPanelRois(this.ctx, this.width, this.height, this.data, this.view, this.roisVisible);
      drawPanelRings(this.ctx, this.width, this.height, this.data, this.view);
      drawPanelWindows(this.ctx, this.width, this.height, this.data, this.view);
    }
  }
}

class CanvasSpatialRenderer extends SpatialBase {
  constructor(host, onViewChange) {
    super(host, onViewChange);
    this.canvas = document.createElement("canvas");
    host.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    installSpatialInteraction(this, this.canvas);
    installResize(this);
  }
  resize() { this.dpr = setupCanvas(this.canvas, this.width, this.height); }
  setData(data, preserveView = true) {
    this.data = data;
    if (!preserveView || !this.view) this.view = squareBounds(data.bounds);
    this.onDataChanged();
    this.draw();
  }
  onDataChanged() {}
  begin() {
    this.dpr = setupCanvas(this.canvas, this.width, this.height);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.ctx.clearRect(0, 0, this.width, this.height);
    this.ctx.fillStyle = "#ffffff";
    this.ctx.fillRect(0, 0, this.width, this.height);
    return this.data ? panelLayout(this.width, this.height, this.data.panelCount, this.data.columns) : null;
  }
  pane(panel, layout) {
    return panelPane(layout, panel);
  }
  worldToPixel(x, z, pane) {
    const shown = equalScaleView(this.view, pane);
    return [
      pane.left + (x - shown.xmin) / (shown.xmax - shown.xmin) * pane.width,
      pane.bottom - (z - shown.zmin) / (shown.zmax - shown.zmin) * pane.height,
    ];
  }
  drawSharedOverlays() {
    drawPanelChrome(this.ctx, this.width, this.height, this.data, this.view, {
      hideTicks: this.cleanMode, showGrid: this.gridVisible && !this.cleanMode,
      scaleBar: true,
    });
    drawPanelRois(this.ctx, this.width, this.height, this.data, this.view, this.roisVisible);
    drawPanelRings(this.ctx, this.width, this.height, this.data, this.view);
    drawPanelWindows(this.ctx, this.width, this.height, this.data, this.view);
  }
}

export class HeatmapRenderer extends CanvasSpatialRenderer {
  constructor(host, onViewChange) {
    super(host, onViewChange);
    this.metric = "time";
    this.scale = "linear";
    this.clipMode = "percentile";
    this.clipMin = 0;
    this.clipMax = 99;
    this.textures = [];
  }
  setMetricScale(metric, scale, clipMode = this.clipMode,
                 clipMin = this.clipMin, clipMax = this.clipMax) {
    this.metric = metric;
    this.scale = scale;
    this.clipMode = clipMode || "percentile";
    this.clipMin = clipMin;
    this.clipMax = clipMax;
    this.buildTextures();
    this.draw();
  }
  onDataChanged() { this.buildTextures(); }
  tooltipText(point) {
    const ix = Math.floor((point.x - this.data.x0) / this.data.bin);
    const iz = Math.floor((point.z - this.data.z0) / this.data.bin);
    if (ix < 0 || ix >= this.data.nx || iz < 0 || iz >= this.data.nz) return null;
    const cells = this.data.nx * this.data.nz;
    const index = point.panel * cells + iz * this.data.nx + ix;
    let value = this.metric === "count" ? this.data.count[index] : this.data.time[index];
    let unit = this.metric === "count" ? "samples" : "s";
    if (this.metric === "percent") {
      let total = 0;
      for (let i = 0; i < cells; i += 1) total += this.data.time[point.panel * cells + i];
      value = total > 0 ? this.data.time[index] / total * 100 : 0;
      unit = "%";
    }
    return `${this.data.panelNames?.[point.panel] || "All"} · ${formatNumber(value, 1)} ${unit} · X ${formatNumber(point.x, 1)} · Z ${formatNumber(point.z, 1)}`;
  }
  buildTextures() {
    this.textures = [];
    if (!this.data) return;
    const {nx, nz, panelCount, count, time} = this.data;
    const cells = nx * nz;
    const panelValues = [];
    const positive = [];
    for (let panel = 0; panel < panelCount; panel += 1) {
      let total = 0;
      for (let i = 0; i < cells; i += 1) total += time[panel * cells + i];
      const values = new Float32Array(cells);
      for (let i = 0; i < cells; i += 1) {
        const index = panel * cells + i;
        const value = this.metric === "count" ? count[index]
          : (this.metric === "percent" ? (total > 0 ? time[index] / total * 100 : 0) : time[index]);
        values[i] = value;
        if (value > 0 && Number.isFinite(value)) positive.push(value);
      }
      panelValues.push(values);
    }
    positive.sort((a, b) => a - b);
    const autoLo = positive[0] || 0, autoHi = positive[positive.length - 1] || 1;
    let lo, hi;
    if (this.clipMode === "percentile") {
      lo = quantile(positive, Math.max(0, Math.min(100, Number(this.clipMin) || 0)) / 100);
      hi = quantile(positive, Math.max(0, Math.min(100, Number(this.clipMax) || 99)) / 100);
    } else {
      lo = this.clipMin != null && Number.isFinite(Number(this.clipMin)) ? Number(this.clipMin) : autoLo;
      hi = this.clipMax != null && Number.isFinite(Number(this.clipMax)) ? Number(this.clipMax) : autoHi;
    }
    if (!Number.isFinite(lo)) lo = autoLo;
    if (!Number.isFinite(hi) || hi <= lo) hi = Math.max(autoHi, lo + 1e-9);
    this.valueRange = {lo, hi};
    const transform = value => this.scale === "log" ? Math.log1p(Math.max(0, value)) : value;
    const scaledLo = transform(lo), scaledHi = transform(hi);
    for (let panel = 0; panel < panelCount; panel += 1) {
      const values = panelValues[panel];
      const canvas = document.createElement("canvas");
      canvas.width = nx; canvas.height = nz;
      const context = canvas.getContext("2d");
      const image = context.createImageData(nx, nz);
      for (let iz = 0; iz < nz; iz += 1) {
        for (let ix = 0; ix < nx; ix += 1) {
          const value = values[iz * nx + ix];
          const norm = value > 0
            ? Math.max(0, Math.min(1, (transform(value) - scaledLo) / Math.max(1e-12, scaledHi - scaledLo)))
            : 0;
          const rgb = hexRgb(sequentialColor(norm));
          const target = ((nz - 1 - iz) * nx + ix) * 4;
          image.data[target] = rgb[0]; image.data[target + 1] = rgb[1]; image.data[target + 2] = rgb[2];
          image.data[target + 3] = value > 0 ? 255 : 0;
        }
      }
      context.putImageData(image, 0, 0);
      this.textures.push(canvas);
    }
  }
  draw() {
    const layout = this.begin();
    if (!layout || !this.view) return;
    const gridX0 = this.data.x0;
    const gridX1 = gridX0 + this.data.nx * this.data.bin;
    const gridZ0 = this.data.z0;
    const gridZ1 = gridZ0 + this.data.nz * this.data.bin;
    const shown = equalScaleView(this.view, this.pane(0, layout));
    const ix0 = Math.max(0, (shown.xmin - gridX0) / (gridX1 - gridX0) * this.data.nx);
    const ix1 = Math.min(this.data.nx, (shown.xmax - gridX0) / (gridX1 - gridX0) * this.data.nx);
    const iz0 = Math.max(0, (gridZ1 - shown.zmax) / (gridZ1 - gridZ0) * this.data.nz);
    const iz1 = Math.min(this.data.nz, (gridZ1 - shown.zmin) / (gridZ1 - gridZ0) * this.data.nz);
    for (let panel = 0; panel < this.data.panelCount; panel += 1) {
      const pane = this.pane(panel, layout);
      const [dx0, dy1] = this.worldToPixel(Math.max(shown.xmin, gridX0), Math.max(shown.zmin, gridZ0), pane);
      const [dx1, dy0] = this.worldToPixel(Math.min(shown.xmax, gridX1), Math.min(shown.zmax, gridZ1), pane);
      this.ctx.save();
      this.ctx.beginPath(); this.ctx.rect(pane.left, pane.top, pane.right - pane.left, pane.bottom - pane.top); this.ctx.clip();
      if (ix1 > ix0 && iz1 > iz0) {
        this.ctx.imageSmoothingEnabled = false;
        this.ctx.drawImage(this.textures[panel], ix0, iz0, ix1 - ix0, iz1 - iz0, dx0, dy0, dx1 - dx0, dy1 - dy0);
      }
      this.ctx.imageSmoothingEnabled = true;
      if (this.valueRange) {
        const barW = 7, barH = Math.min(82, pane.height * .18);
        const bx = pane.right - barW - 7, by = pane.top + 8;
        const gradient = this.ctx.createLinearGradient(0, by + barH, 0, by);
        for (let stop = 0; stop <= 1; stop += .2) gradient.addColorStop(stop, sequentialColor(stop));
        this.ctx.fillStyle = gradient; this.ctx.fillRect(bx, by, barW, barH);
        this.ctx.strokeStyle = "rgba(25,45,40,.25)"; this.ctx.strokeRect(bx + .5, by + .5, barW - 1, barH - 1);
        this.ctx.fillStyle = "#44514d"; this.ctx.font = "700 8px Inter, system-ui";
        this.ctx.textAlign = "right"; this.ctx.textBaseline = "top";
        this.ctx.fillText(formatNumber(this.valueRange.hi, 1), bx - 3, by);
        this.ctx.textBaseline = "bottom";
        this.ctx.fillText(formatNumber(this.valueRange.lo, 1), bx - 3, by + barH);
      }
      this.ctx.restore();
    }
    this.drawSharedOverlays();
  }
}

export class TransitionRenderer extends HeatmapRenderer {
  constructor(host, onViewChange) {
    super(host, onViewChange);
    this.visualOptions = {outcome: "crossed", display: "fraction", minimumSupport: 5};
  }
  setVisualOptions(options = {}) {
    Object.assign(this.visualOptions, options);
    this.buildTextures();
    this.draw();
  }
  cellAt(point) {
    if (!this.data) return null;
    const ix = Math.floor((point.x - this.data.x0) / this.data.bin);
    const iz = Math.floor((point.z - this.data.z0) / this.data.bin);
    if (ix < 0 || ix >= this.data.nx || iz < 0 || iz >= this.data.nz) return null;
    const index = point.panel * this.data.nx * this.data.nz + iz * this.data.nx + ix;
    const entered = Number(this.data.entered[index]) || 0;
    const support = Math.max(1, Number(this.visualOptions.minimumSupport) || 1);
    if (entered < support) return null;
    const outcome = this.visualOptions.outcome === "ended" ? this.data.ended : this.data.crossed;
    const numerator = Number(outcome[index]) || 0;
    return {panel: point.panel, ix, iz, x: point.x, z: point.z, entered, numerator,
      value: this.visualOptions.display === "count" ? numerator : numerator / entered};
  }
  tooltipText(point) {
    const cell = this.cellAt(point);
    if (!cell) return `${this.data?.panelNames?.[point.panel] || "All"} · no supported transition cell`;
    const shown = this.visualOptions.display === "count"
      ? `${formatNumber(cell.value, 0)} paths`
      : `${formatNumber(cell.value * 100, 1)}%`;
    return `${this.data.panelNames?.[point.panel] || "All"} · ${shown} · ${formatNumber(cell.entered, 0)} entered · click for raw paths`;
  }
  buildTextures() {
    this.textures = [];
    if (!this.data) return;
    const {nx, nz, panelCount, entered} = this.data;
    const outcome = this.visualOptions.outcome === "ended" ? this.data.ended : this.data.crossed;
    const cells = nx * nz, support = Math.max(1, Number(this.visualOptions.minimumSupport) || 1);
    const panelValues = [], positive = [];
    for (let panel = 0; panel < panelCount; panel += 1) {
      const values = new Float32Array(cells);
      for (let cell = 0; cell < cells; cell += 1) {
        const index = panel * cells + cell;
        if (entered[index] < support) continue;
        const value = this.visualOptions.display === "count"
          ? outcome[index] : outcome[index] / Math.max(1, entered[index]);
        values[cell] = value;
        if (value > 0 && Number.isFinite(value)) positive.push(value);
      }
      panelValues.push(values);
    }
    positive.sort((a, b) => a - b);
    const lo = 0, hi = this.visualOptions.display === "fraction" ? 1 : Math.max(1, positive[positive.length - 1] || 1);
    this.valueRange = {lo, hi};
    for (let panel = 0; panel < panelCount; panel += 1) {
      const canvas = document.createElement("canvas");
      canvas.width = nx; canvas.height = nz;
      const context = canvas.getContext("2d");
      const image = context.createImageData(nx, nz);
      for (let iz = 0; iz < nz; iz += 1) for (let ix = 0; ix < nx; ix += 1) {
        const value = panelValues[panel][iz * nx + ix];
        const norm = Math.max(0, Math.min(1, value / Math.max(1e-12, hi)));
        const rgb = hexRgb(sequentialColor(norm));
        const target = ((nz - 1 - iz) * nx + ix) * 4;
        image.data[target] = rgb[0]; image.data[target + 1] = rgb[1]; image.data[target + 2] = rgb[2];
        image.data[target + 3] = value > 0 ? 255 : 0;
      }
      context.putImageData(image, 0, 0);
      this.textures.push(canvas);
    }
  }
}

function angleColor(angle, alpha = 1) {
  const hue = ((angle % 360) + 360) % 360;
  return `hsla(${hue}, 62%, 43%, ${alpha})`;
}

export class DirectionRenderer extends CanvasSpatialRenderer {
  constructor(host, onViewChange) {
    super(host, onViewChange);
    this.particleCanvas = document.createElement("canvas");
    this.particleCanvas.style.pointerEvents = "none";
    this.host.appendChild(this.particleCanvas);
    this.particleCtx = this.particleCanvas.getContext("2d");
    this.particles = [];
    this.spawnTables = [];
    this.visualOptions = {
      particleRate: 1, trailLength: 1, speed: 1, variability: 1,
      metric: "time", clipMode: "percentile", clipMin: 0, clipMax: 99,
    };
    this._rng = 0x6d2b79f5;
    this._pausedUntil = 0;
    this._visible = false;
    this.resize();
    this._lastFrame = 0;
    this._animate = now => {
      if (this.data && this._visible && document.visibilityState === "visible"
          && now >= this._pausedUntil && now - this._lastFrame > 35) {
        this.drawParticles(now);
        this._lastFrame = now;
      }
      this._animationFrame = requestAnimationFrame(this._animate);
    };
    this._visibilityObserver = new IntersectionObserver(entries => {
      this._visible = entries.some(entry => entry.isIntersecting);
    }, {rootMargin: "120px"});
    this._visibilityObserver.observe(host);
    this._animationFrame = requestAnimationFrame(this._animate);
  }
  setVisualOptions(options = {}) {
    Object.assign(this.visualOptions, options);
    this._updateAbundanceReference();
    this._buildSpawnTables();
    this._resetParticles();
    this.draw();
  }
  setView(view, notify = false) {
    this.pauseAnimation(190);
    super.setView(view, notify);
  }
  pauseAnimation(delay = 190) {
    this._pausedUntil = performance.now() + delay;
    this._particleTime = 0;
    clearTimeout(this._resumeTimer);
    if (this.particleCtx) this.particleCtx.clearRect(0, 0, this.width, this.height);
    this._resumeTimer = setTimeout(() => {
      this._resetParticles();
      this._pausedUntil = 0;
    }, delay);
  }
  onDataChanged() {
    this._updateAbundanceReference();
    this._buildSpawnTables();
    this._resetParticles();
  }
  _updateAbundanceReference() {
    if (!this.data) return;
    const cells = this.data.nx * this.data.nz;
    this.abundanceValues = new Float32Array(this.data.abundance.length);
    for (let panel = 0; panel < this.data.panelCount; panel += 1) {
      let total = 0;
      for (let cell = 0; cell < cells; cell += 1) total += this.data.time[panel * cells + cell];
      for (let cell = 0; cell < cells; cell += 1) {
        const index = panel * cells + cell;
        this.abundanceValues[index] = this.visualOptions.metric === "count"
          ? this.data.abundance[index]
          : (this.visualOptions.metric === "percent"
            ? (total > 0 ? this.data.time[index] / total * 100 : 0)
            : this.data.time[index]);
      }
    }
    const positive = [];
    for (const value of this.abundanceValues) if (value > 0) positive.push(value);
    positive.sort((a, b) => a - b);
    if (!positive.length) { this.abundanceReference = 1; return; }
    if (this.visualOptions.clipMode === "absolute") {
      this.abundanceFloor = Math.max(0, Number(this.visualOptions.clipMin) || 0);
      this.abundanceReference = Math.max(1e-9,
        Number(this.visualOptions.clipMax) || positive[positive.length - 1]);
    } else {
      const floorQ = Math.max(0, Math.min(100,
        Number(this.visualOptions.clipMin) || 0)) / 100;
      const q = Math.max(0, Math.min(100,
        Number(this.visualOptions.clipMax) || 99)) / 100;
      this.abundanceFloor = Math.max(0, quantile(positive, floorQ) || 0);
      this.abundanceReference = Math.max(1e-9, quantile(positive, q));
    }
  }
  _abundanceScale(value) {
    const floor = Math.max(0, this.abundanceFloor || 0);
    return Math.max(0, Math.min(1,
      (value - floor) / Math.max(1e-9, (this.abundanceReference || 1) - floor)
    ));
  }
  resize() {
    super.resize();
    if (this.particleCanvas) {
      this.particleDpr = setupCanvas(this.particleCanvas, this.width, this.height);
      this.particleCtx.setTransform(this.particleDpr, 0, 0, this.particleDpr, 0, 0);
      this.particleCtx.clearRect(0, 0, this.width, this.height);
      this._resetParticles();
    }
  }
  _random() {
    this._rng = (Math.imul(this._rng, 1664525) + 1013904223) >>> 0;
    return this._rng / 4294967296;
  }
  _normalRandom() {
    return Math.sqrt(-2 * Math.log(Math.max(1e-9, this._random())))
      * Math.cos(2 * Math.PI * this._random());
  }
  _buildSpawnTables() {
    this.spawnTables = [];
    if (!this.data) return;
    const {nx, nz, panelCount, angle} = this.data;
    const abundance = this.abundanceValues;
    const cells = nx * nz;
    for (let panel = 0; panel < panelCount; panel += 1) {
      const entries = [];
      let total = 0;
      for (let cell = 0; cell < cells; cell += 1) {
        const index = panel * cells + cell;
        const scaled = this._abundanceScale(abundance[index]);
        if (!(scaled > 0) || !Number.isFinite(angle[index])) continue;
        total += Math.pow(scaled, .68);
        entries.push({cell, cumulative: total});
      }
      this.spawnTables.push({entries, total});
    }
  }
  _respawn(particle, panel = particle.panel) {
    const table = this.spawnTables[panel];
    if (!table?.entries.length) { particle.age = -1; return; }
    const target = this._random() * table.total;
    let lo = 0, hi = table.entries.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (table.entries[mid].cumulative < target) lo = mid + 1; else hi = mid;
    }
    const cell = table.entries[lo].cell;
    const ix = cell % this.data.nx, iz = Math.floor(cell / this.data.nx);
    const index = panel * this.data.nx * this.data.nz + cell;
    const strength = Math.max(.001, Math.min(.999999, this.data.strength[index] || 0));
    const abundanceScale = Math.sqrt(this._abundanceScale(this.abundanceValues[index] || 0));
    const circularSigma = Math.sqrt(Math.max(0, -2 * Math.log(strength)));
    particle.panel = panel;
    particle.x = this.data.x0 + (ix + .08 + this._random() * .84) * this.data.bin;
    particle.z = this.data.z0 + (iz + .08 + this._random() * .84) * this.data.bin;
    particle.age = 0;
    particle.bias = this._normalRandom() * circularSigma * this.visualOptions.variability;
    const directedMass = abundanceScale * strength;
    particle.maxAge = Math.max(8, Math.round(
      (16 + 76 * directedMass) * this.visualOptions.trailLength
      * (.82 + this._random() * .36)
    ));
  }
  _resetParticles() {
    if (!this.data?.panelCount || !this.spawnTables.length) return;
    const perPanel = Math.max(18, Math.min(420, Math.floor(
      900 / this.data.panelCount * this.visualOptions.particleRate
    )));
    this.particles = [];
    for (let panel = 0; panel < this.data.panelCount; panel += 1) {
      for (let i = 0; i < perPanel; i += 1) {
        const particle = {panel, x: 0, z: 0, age: -1, maxAge: 1, bias: 0};
        this._respawn(particle, panel);
        particle.age = Math.floor(this._random() * particle.maxAge);
        this.particles.push(particle);
      }
    }
    if (this.particleCtx) {
      this.particleCtx.setTransform(this.particleDpr || 1, 0, 0, this.particleDpr || 1, 0, 0);
      this.particleCtx.clearRect(0, 0, this.width, this.height);
    }
  }
  _sampleVector(panel, x, z) {
    const gx = (x - this.data.x0) / this.data.bin - .5;
    const gz = (z - this.data.z0) / this.data.bin - .5;
    const ix0 = Math.floor(gx), iz0 = Math.floor(gz);
    const fx = gx - ix0, fz = gz - iz0;
    let vx = 0, vz = 0, abundance = 0, weight = 0;
    let strongestWeight = -1, fallbackAngle = NaN, fallbackStrength = 0;
    const cells = this.data.nx * this.data.nz;
    for (let dz = 0; dz <= 1; dz += 1) for (let dx = 0; dx <= 1; dx += 1) {
      const ix = ix0 + dx, iz = iz0 + dz;
      if (ix < 0 || ix >= this.data.nx || iz < 0 || iz >= this.data.nz) continue;
      const index = panel * cells + iz * this.data.nx + ix;
      const angle = this.data.angle[index], strength = this.data.strength[index];
      if (!Number.isFinite(angle) || !(strength > 0)) continue;
      const w = (dx ? fx : 1 - fx) * (dz ? fz : 1 - fz);
      const radians = angle * Math.PI / 180;
      vx += Math.sin(radians) * strength * w;
      vz += Math.cos(radians) * strength * w;
      abundance += this.abundanceValues[index] * w;
      weight += w;
      const candidate = this.abundanceValues[index] * w;
      if (candidate > strongestWeight) {
        strongestWeight = candidate; fallbackAngle = angle; fallbackStrength = strength;
      }
    }
    if (!(weight > .08)) return null;
    vx /= weight; vz /= weight; abundance /= weight;
    const vectorStrength = Math.hypot(vx, vz);
    const angle = vectorStrength > .005
      ? Math.atan2(vx, vz) * 180 / Math.PI
      : fallbackAngle;
    if (!Number.isFinite(angle)) return null;
    return {
      strength: Math.max(.001, Math.min(1, vectorStrength > .005 ? vectorStrength : fallbackStrength)),
      abundance, angle,
    };
  }
  tooltipText(point) {
    const ix = Math.floor((point.x - this.data.x0) / this.data.bin);
    const iz = Math.floor((point.z - this.data.z0) / this.data.bin);
    if (ix < 0 || ix >= this.data.nx || iz < 0 || iz >= this.data.nz) return null;
    const index = point.panel * this.data.nx * this.data.nz + iz * this.data.nx + ix;
    if (!Number.isFinite(this.data.angle[index])) return null;
    const unit = this.visualOptions.metric === "count" ? "samples"
      : (this.visualOptions.metric === "percent" ? "%" : "s");
    return `${this.data.panelNames?.[point.panel] || "All"} · ${formatNumber(this.data.angle[index], 1)}° · R ${formatNumber(this.data.strength[index], 1)} · ${formatNumber(this.abundanceValues[index], 1)} ${unit}`;
  }
  draw() {
    const layout = this.begin();
    if (!layout || !this.view) return;
    const {nx, nz, panelCount, angle, strength} = this.data;
    const abundance = this.abundanceValues;
    const cells = nx * nz;
    for (let panel = 0; panel < panelCount; panel += 1) {
      const pane = this.pane(panel, layout);
      const shown = equalScaleView(this.view, pane);
      this.ctx.save();
      this.ctx.beginPath(); this.ctx.rect(pane.left, pane.top, pane.right - pane.left, pane.bottom - pane.top); this.ctx.clip();
      for (let iz = 0; iz < nz; iz += 1) {
        const z = this.data.z0 + (iz + .5) * this.data.bin;
        if (z < shown.zmin - this.data.bin || z > shown.zmax + this.data.bin) continue;
        for (let ix = 0; ix < nx; ix += 1) {
          const x = this.data.x0 + (ix + .5) * this.data.bin;
          if (x < shown.xmin - this.data.bin || x > shown.xmax + this.data.bin) continue;
          const index = panel * cells + iz * nx + ix;
          const count = abundance[index];
          if (!(count > 0) || !Number.isFinite(angle[index])) continue;
          if ((ix + iz) % 2) continue;
          const [cx, cy] = this.worldToPixel(x, z, pane);
          const [x0] = this.worldToPixel(x - this.data.bin / 2, z, pane);
          const [x1] = this.worldToPixel(x + this.data.bin / 2, z, pane);
          const cellPx = Math.abs(x1 - x0);
          const abundanceScale = Math.sqrt(this._abundanceScale(count));
          const len = cellPx * .72 * strength[index];
          const radians = angle[index] * Math.PI / 180;
          const dx = Math.sin(radians) * len * .5;
          const dy = -Math.cos(radians) * len * .5;
          this.ctx.lineCap = "round";
          this.ctx.strokeStyle = angleColor(angle[index], .035 + .11 * abundanceScale);
          this.ctx.lineWidth = .35 + .5 * abundanceScale;
          this.ctx.beginPath();
          this.ctx.moveTo(cx - dx, cy - dy);
          this.ctx.lineTo(cx + dx, cy + dy);
          this.ctx.stroke();
        }
      }
      this.ctx.restore();
    }
    this.drawSharedOverlays();
  }
  drawParticles(now) {
    if (!this.data || !this.view || !this.particleCtx || !this.particles.length) return;
    const ctx = this.particleCtx;
    const layout = panelLayout(this.width, this.height, this.data.panelCount, this.data.columns);
    const dt = this._particleTime ? Math.min(.08, (now - this._particleTime) / 1000) : .045;
    this._particleTime = now;
    ctx.setTransform(this.particleDpr || 1, 0, 0, this.particleDpr || 1, 0, 0);
    ctx.globalCompositeOperation = "destination-out";
    const fade = Math.max(.025, Math.min(.24, .11 / Math.max(.25, this.visualOptions.trailLength)));
    ctx.fillStyle = `rgba(0,0,0,${fade})`;
    ctx.fillRect(0, 0, this.width, this.height);
    ctx.globalCompositeOperation = "source-over";
    ctx.lineCap = "round";
    for (const particle of this.particles) {
      if (particle.age < 0 || particle.age >= particle.maxAge) {
        this._respawn(particle);
        continue;
      }
      const vector = this._sampleVector(particle.panel, particle.x, particle.z);
      if (!vector) { this._respawn(particle); continue; }
      const abundanceScale = Math.sqrt(this._abundanceScale(vector.abundance));
      const sigma = Math.sqrt(Math.max(0, -2 * Math.log(Math.max(.001, Math.min(.999999, vector.strength)))))
        * this.visualOptions.variability;
      particle.bias += (this._normalRandom() * sigma - particle.bias) * .035;
      const radians = (vector.angle + particle.bias * 180 / Math.PI) * Math.PI / 180;
      const speed = this.data.bin * this.visualOptions.speed
        * (.45 + 3.9 * Math.min(1, vector.strength))
        * (.72 + .55 * abundanceScale);
      const nx = particle.x + Math.sin(radians) * speed * dt;
      const nz = particle.z + Math.cos(radians) * speed * dt;
      const pane = this.pane(particle.panel, layout);
      const shown = equalScaleView(this.view, pane);
      if (nx < shown.xmin || nx > shown.xmax || nz < shown.zmin || nz > shown.zmax) {
        this._respawn(particle);
        continue;
      }
      const [x0, y0] = this.worldToPixel(particle.x, particle.z, pane);
      const [x1, y1] = this.worldToPixel(nx, nz, pane);
      const life = Math.sin(Math.PI * particle.age / Math.max(1, particle.maxAge));
      ctx.strokeStyle = angleColor(vector.angle, (.12 + .58 * abundanceScale) * (.3 + .7 * life));
      ctx.lineWidth = .5 + 1.55 * abundanceScale;
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      particle.x = nx; particle.z = nz; particle.age += 1;
    }
    drawPanelRings(ctx, this.width, this.height, this.data, this.view);
  }
}

class CanvasRenderer {
  constructor(host) {
    this.host = host;
    this.canvas = document.createElement("canvas");
    host.appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.width = host.clientWidth || 800;
    this.height = host.clientHeight || 400;
    this.data = null;
    installResize(this);
  }
  resize() { this.dpr = setupCanvas(this.canvas, this.width, this.height); }
  begin() {
    this.dpr = setupCanvas(this.canvas, this.width, this.height);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.ctx.clearRect(0, 0, this.width, this.height);
    this.ctx.fillStyle = "#ffffff"; this.ctx.fillRect(0, 0, this.width, this.height);
  }
  setData(data) { this.data = data; this.draw(); }
  draw() { this.begin(); }
}

export class PolarRenderer extends CanvasRenderer {
  constructor(host) { super(host); this.fraction = 1; }
  setFraction(value) { this.fraction = Math.max(.01, Math.min(1, value)); this.draw(); }
  draw() {
    this.begin();
    if (!this.data) return;
    const layout = panelLayout(this.width, this.height, this.data.panelCount, this.data.columns);
    const radius = Math.min(layout.cellW, layout.cellH) * .34;
    this.ctx.font = "600 11px Inter, system-ui, sans-serif";
    for (let panel = 0; panel < this.data.panelCount; panel += 1) {
      const col = panel % layout.cols, row = Math.floor(panel / layout.cols);
      const cx = col * layout.cellW + layout.cellW / 2;
      const cy = row * layout.cellH + layout.cellH * .55;
      this.ctx.strokeStyle = "rgba(24,34,31,.13)"; this.ctx.lineWidth = 1;
      for (const f of [.25, .5, .75, 1]) { this.ctx.beginPath(); this.ctx.arc(cx, cy, radius * f, 0, Math.PI * 2); this.ctx.stroke(); }
      this.ctx.beginPath(); this.ctx.moveTo(cx - radius, cy); this.ctx.lineTo(cx + radius, cy); this.ctx.moveTo(cx, cy - radius); this.ctx.lineTo(cx, cy + radius); this.ctx.stroke();
      this.ctx.fillStyle = "#27332f"; this.ctx.textAlign = "left"; this.ctx.textBaseline = "top";
      this.ctx.fillText(String(this.data.panelNames?.[panel] || "All data").slice(0, 42), col * layout.cellW + 12, row * layout.cellH + 8);
      for (let i = 0; i < this.data.angle.length; i += 1) {
        if (this.data.panel[i] !== panel || this.data.sample[i] > this.fraction) continue;
        const a = this.data.angle[i] * Math.PI / 180;
        const rr = radius * this.data.r[i];
        this.ctx.strokeStyle = PALETTE[this.data.color[i] % PALETTE.length];
        this.ctx.globalAlpha = .28; this.ctx.lineWidth = .8;
        this.ctx.beginPath(); this.ctx.moveTo(cx, cy); this.ctx.lineTo(cx + Math.sin(a) * rr, cy - Math.cos(a) * rr); this.ctx.stroke();
      }
      this.ctx.globalAlpha = 1;
      const pa = this.data.populationAngle[panel] * Math.PI / 180;
      const pr = radius * this.data.populationR[panel];
      if (Number.isFinite(pa) && Number.isFinite(pr)) {
        this.ctx.strokeStyle = "#17221e"; this.ctx.lineWidth = 3;
        this.ctx.beginPath(); this.ctx.moveTo(cx, cy); this.ctx.lineTo(cx + Math.sin(pa) * pr, cy - Math.cos(pa) * pr); this.ctx.stroke();
        this.ctx.fillStyle = "#17221e"; this.ctx.beginPath(); this.ctx.arc(cx + Math.sin(pa) * pr, cy - Math.cos(pa) * pr, 3, 0, Math.PI * 2); this.ctx.fill();
      }
    }
    this.ctx.globalAlpha = 1;
  }
}

export class HeadingRenderer extends CanvasRenderer {
  constructor(host) { super(host); this.fraction = 1; }
  setFraction(value) { this.fraction = Math.max(.01, Math.min(1, value)); this.draw(); }
  draw() {
    this.begin();
    if (!this.data) return;
    const layout = panelLayout(this.width, this.height, this.data.panelCount, this.data.columns);
    const margin = {x: 45, top: 30, bottom: 28, right: 12};
    for (let p = 0; p < this.data.panelCount; p += 1) {
      const col = p % layout.cols, row = Math.floor(p / layout.cols);
      const left = col * layout.cellW + margin.x, right = (col + 1) * layout.cellW - margin.right;
      const top = row * layout.cellH + margin.top, bottom = (row + 1) * layout.cellH - margin.bottom;
      this.ctx.strokeStyle = "rgba(24,34,31,.13)"; this.ctx.strokeRect(left, top, right - left, bottom - top);
      for (const angle of [-180, -90, 0, 90, 180]) {
        const y = bottom - (angle + 180) / 360 * (bottom - top);
        this.ctx.beginPath(); this.ctx.moveTo(left, y); this.ctx.lineTo(right, y); this.ctx.stroke();
      }
      this.ctx.font = "600 11px Inter, system-ui, sans-serif"; this.ctx.fillStyle = "#27332f";
      this.ctx.fillText(this.data.panelNames?.[p] || "All data", left, row * layout.cellH + 8);
      for (let i = 0; i < this.data.panels.length; i += 2) {
        if (this.data.panels[i] !== p || this.data.samples[i] > this.fraction) continue;
        const x0 = left + this.data.vertices[i * 2] / Math.max(1e-9, this.data.maxTime) * (right - left);
        const y0 = bottom - (this.data.vertices[i * 2 + 1] + 180) / 360 * (bottom - top);
        const x1 = left + this.data.vertices[(i + 1) * 2] / Math.max(1e-9, this.data.maxTime) * (right - left);
        const y1 = bottom - (this.data.vertices[(i + 1) * 2 + 1] + 180) / 360 * (bottom - top);
        this.ctx.strokeStyle = PALETTE[this.data.colors[i] % PALETTE.length]; this.ctx.globalAlpha = .24; this.ctx.lineWidth = .7;
        this.ctx.beginPath(); this.ctx.moveTo(x0, y0); this.ctx.lineTo(x1, y1); this.ctx.stroke();
      }
    }
    this.ctx.globalAlpha = 1;
  }
}

function quantile(sorted, q) {
  if (!sorted.length) return NaN;
  const index = (sorted.length - 1) * q;
  const lo = Math.floor(index), hi = Math.ceil(index), f = index - lo;
  return sorted[lo] * (1 - f) + sorted[hi] * f;
}

export class MetricsRenderer extends CanvasRenderer {
  draw() {
    this.begin();
    if (!this.data) return;
    const metrics = [
      ["distance", "Distance walked"], ["displacement", "Net displacement"],
      ["speed", "Median velocity"], ["tortuosity", "Local tortuosity"],
    ];
    const cols = 2, rows = 2, cellW = this.width / cols, cellH = this.height / rows;
    metrics.forEach(([key, title], metricIndex) => {
      const col = metricIndex % cols, row = Math.floor(metricIndex / cols);
      const left = col * cellW + 48, right = (col + 1) * cellW - 14;
      const top = row * cellH + 35, bottom = (row + 1) * cellH - 40;
      const values = this.data[key];
      let max = 0;
      for (let i = 0; i < values.length; i += 1) if (Number.isFinite(values[i])) max = Math.max(max, values[i]);
      max = max || 1;
      this.ctx.fillStyle = "#27332f"; this.ctx.font = "600 12px Inter, system-ui, sans-serif"; this.ctx.fillText(title, left, row * cellH + 10);
      this.ctx.strokeStyle = "rgba(24,34,31,.16)"; this.ctx.strokeRect(left, top, right - left, bottom - top);
      for (let panel = 0; panel < this.data.panelCount; panel += 1) {
        const group = [];
        for (let i = 0; i < values.length; i += 1) if (this.data.panel[i] === panel && Number.isFinite(values[i])) group.push(values[i]);
        group.sort((a, b) => a - b);
        if (!group.length) continue;
        const x = left + (panel + .5) / this.data.panelCount * (right - left);
        const y = value => bottom - value / max * (bottom - top);
        const q1 = quantile(group, .25), med = quantile(group, .5), q3 = quantile(group, .75);
        this.ctx.fillStyle = "rgba(14,124,115,.14)"; this.ctx.fillRect(x - 12, y(q3), 24, y(q1) - y(q3));
        this.ctx.strokeStyle = "#0e7c73"; this.ctx.lineWidth = 2; this.ctx.beginPath(); this.ctx.moveTo(x - 14, y(med)); this.ctx.lineTo(x + 14, y(med)); this.ctx.stroke();
        const stride = Math.max(1, Math.ceil(group.length / 140));
        this.ctx.fillStyle = "rgba(49,95,140,.32)";
        for (let i = 0; i < group.length; i += stride) {
          const jitter = (((i * 2654435761) >>> 0) / 4294967295 - .5) * 22;
          this.ctx.beginPath(); this.ctx.arc(x + jitter, y(group[i]), 1.5, 0, Math.PI * 2); this.ctx.fill();
        }
        this.ctx.save(); this.ctx.translate(x + 3, bottom + 8); this.ctx.rotate(Math.PI / 5);
        this.ctx.fillStyle = "#68716d"; this.ctx.font = "9px Inter, system-ui, sans-serif";
        this.ctx.fillText(String(this.data.panelNames?.[panel] || panel).slice(0, 15), 0, 0); this.ctx.restore();
      }
    });
  }
}

export class RoiRenderer extends CanvasRenderer {
  _paired(valuesLeft, valuesRight, panels, box, title, maxValue, suffix = "") {
    const {left, right, top, bottom} = box;
    this.ctx.fillStyle = "#27332f"; this.ctx.font = "600 12px Inter, system-ui, sans-serif";
    this.ctx.fillText(title, left, top - 20);
    this.ctx.strokeStyle = "rgba(24,34,31,.16)"; this.ctx.strokeRect(left, top, right - left, bottom - top);
    const xLeft = left + (right - left) * .34, xRight = left + (right - left) * .66;
    const y = value => bottom - Math.max(0, Math.min(maxValue, value)) / Math.max(1e-9, maxValue) * (bottom - top);
    for (let i = 0; i < valuesLeft.length; i += 1) {
      const l = valuesLeft[i], r = valuesRight[i];
      const color = PALETTE[panels[i] % CATEGORY_COLORS];
      if (Number.isFinite(l) && Number.isFinite(r)) {
        this.ctx.strokeStyle = color; this.ctx.globalAlpha = .18; this.ctx.lineWidth = .8;
        this.ctx.beginPath(); this.ctx.moveTo(xLeft, y(l)); this.ctx.lineTo(xRight, y(r)); this.ctx.stroke();
      }
      this.ctx.globalAlpha = .55; this.ctx.fillStyle = color;
      if (Number.isFinite(l)) { this.ctx.beginPath(); this.ctx.arc(xLeft + ((i * 17) % 11 - 5), y(l), 2.2, 0, Math.PI * 2); this.ctx.fill(); }
      if (Number.isFinite(r)) { this.ctx.beginPath(); this.ctx.arc(xRight + ((i * 23) % 11 - 5), y(r), 2.2, 0, Math.PI * 2); this.ctx.fill(); }
    }
    this.ctx.globalAlpha = 1; this.ctx.fillStyle = "#65706b"; this.ctx.font = "9px Inter, system-ui, sans-serif";
    this.ctx.textAlign = "center"; this.ctx.fillText("Left", xLeft, bottom + 14); this.ctx.fillText("Right", xRight, bottom + 14);
    this.ctx.textAlign = "left"; this.ctx.fillText(`${formatNumber(maxValue)}${suffix}`, left + 3, top + 3);
  }
  _split(values, sides, panels, box, title, fixedRange = null) {
    const leftValues = [], rightValues = [], leftPanels = [], rightPanels = [];
    for (let i = 0; i < values.length; i += 1) {
      if (!Number.isFinite(values[i])) continue;
      if (sides[i] === 0) { leftValues.push(values[i]); leftPanels.push(panels[i]); }
      else { rightValues.push(values[i]); rightPanels.push(panels[i]); }
    }
    const lo = fixedRange ? fixedRange[0] : Math.min(0, ...leftValues, ...rightValues);
    const hi = fixedRange ? fixedRange[1] : Math.max(1e-9, ...leftValues, ...rightValues);
    const {left, right, top, bottom} = box;
    this.ctx.fillStyle = "#27332f"; this.ctx.font = "600 12px Inter, system-ui, sans-serif"; this.ctx.fillText(title, left, top - 20);
    this.ctx.strokeStyle = "rgba(24,34,31,.16)"; this.ctx.strokeRect(left, top, right - left, bottom - top);
    const xs = [left + (right - left) * .34, left + (right - left) * .66];
    const y = value => bottom - (value - lo) / Math.max(1e-9, hi - lo) * (bottom - top);
    [[leftValues, leftPanels], [rightValues, rightPanels]].forEach(([group, colors], side) => {
      const stride = Math.max(1, Math.ceil(group.length / 800));
      for (let i = 0; i < group.length; i += stride) {
        this.ctx.fillStyle = PALETTE[colors[i] % CATEGORY_COLORS]; this.ctx.globalAlpha = .32;
        const jitter = (((i * 2654435761) >>> 0) / 4294967295 - .5) * 34;
        this.ctx.beginPath(); this.ctx.arc(xs[side] + jitter, y(group[i]), 1.7, 0, Math.PI * 2); this.ctx.fill();
      }
    });
    this.ctx.globalAlpha = 1; this.ctx.fillStyle = "#65706b"; this.ctx.font = "9px Inter, system-ui, sans-serif"; this.ctx.textAlign = "center";
    this.ctx.fillText("Left", xs[0], bottom + 14); this.ctx.fillText("Right", xs[1], bottom + 14); this.ctx.textAlign = "left";
  }
  draw() {
    this.begin();
    if (!this.data) return;
    const gap = 58, cellW = this.width / 2, cellH = this.height / 2;
    const box = (col, row) => ({left: col * cellW + 48, right: (col + 1) * cellW - 20, top: row * cellH + gap, bottom: (row + 1) * cellH - 38});
    this._paired(this.data.leftFraction, this.data.rightFraction, this.data.animalPanel, box(0, 0), "Fraction reaching", 1);
    let maxResidence = 0; for (const v of this.data.leftResidence) maxResidence = Math.max(maxResidence, v); for (const v of this.data.rightResidence) maxResidence = Math.max(maxResidence, v);
    this._paired(this.data.leftResidence, this.data.rightResidence, this.data.animalPanel, box(1, 0), "Residence seconds / trial", maxResidence || 1, " s");
    this._split(this.data.timeValues, this.data.timeSides, this.data.timePanels, box(0, 1), "Time to first reach");
    this._split(this.data.errorValues, this.data.errorSides, this.data.errorPanels, box(1, 1), "Heading error", [-180, 180]);
  }
}

export class HistogramRenderer extends CanvasRenderer {
  draw() {
    this.begin();
    if (!this.data) return;
    const left = 44, right = this.width - 14, top = 16, bottom = this.height - 30;
    let max = 0; for (const count of this.data.counts) max = Math.max(max, count);
    const n = this.data.counts.length;
    this.ctx.fillStyle = "rgba(49,95,140,.68)";
    for (let i = 0; i < n; i += 1) {
      const x0 = left + i / n * (right - left), x1 = left + (i + 1) / n * (right - left);
      const y = bottom - this.data.counts[i] / Math.max(1, max) * (bottom - top);
      this.ctx.fillRect(x0 + .5, y, Math.max(1, x1 - x0 - 1), bottom - y);
    }
    this.ctx.strokeStyle = "rgba(24,34,31,.2)"; this.ctx.strokeRect(left, top, right - left, bottom - top);
    this.ctx.fillStyle = "#69726e"; this.ctx.font = "9px ui-monospace, monospace";
    this.ctx.fillText(formatNumber(this.data.edges[0]), left, bottom + 15);
    const label = formatNumber(this.data.edges[this.data.edges.length - 1]);
    this.ctx.fillText(label, right - this.ctx.measureText(label).width, bottom + 15);
  }
}

export class RawRenderer extends CanvasRenderer {
  draw() {
    this.begin();
    if (!this.data) return;
    const left = 50, right = this.width - 15, top = 15, bottom = this.height - 30;
    const {vertices} = this.data;
    let ymin = Infinity, ymax = -Infinity;
    for (let i = 1; i < vertices.length; i += 2) { const v = vertices[i]; if (Number.isFinite(v)) { ymin = Math.min(ymin, v); ymax = Math.max(ymax, v); } }
    if (!Number.isFinite(ymin) || ymax === ymin) { ymin = 0; ymax = 1; }
    this.ctx.strokeStyle = "rgba(14,124,115,.28)"; this.ctx.lineWidth = .7;
    for (let i = 0; i < vertices.length; i += 4) {
      const x0 = left + vertices[i] / Math.max(1e-9, this.data.maxTime) * (right - left);
      const y0 = bottom - (vertices[i + 1] - ymin) / (ymax - ymin) * (bottom - top);
      const x1 = left + vertices[i + 2] / Math.max(1e-9, this.data.maxTime) * (right - left);
      const y1 = bottom - (vertices[i + 3] - ymin) / (ymax - ymin) * (bottom - top);
      this.ctx.beginPath(); this.ctx.moveTo(x0, y0); this.ctx.lineTo(x1, y1); this.ctx.stroke();
    }
    this.ctx.strokeStyle = "rgba(24,34,31,.2)"; this.ctx.strokeRect(left, top, right - left, bottom - top);
    this.ctx.fillStyle = "#69726e"; this.ctx.font = "9px ui-monospace, monospace";
    this.ctx.fillText(formatNumber(ymax), 4, top + 4); this.ctx.fillText(formatNumber(ymin), 4, bottom);
  }
}
