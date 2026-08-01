const PANEL_PAD_X = 0.055;
const PANEL_PAD_Y = 0.09;
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
    [0.00, [38, 37, 72]], [0.18, [44, 82, 126]], [0.38, [34, 132, 139]],
    [0.58, [72, 166, 116]], [0.78, [173, 195, 75]], [1.00, [240, 190, 55]],
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

function hexRgb(color) {
  if (color.startsWith("rgb")) return color.match(/[\d.]+/g).slice(0, 3).map(Number);
  const value = color.replace("#", "");
  return [0, 2, 4].map(i => parseInt(value.slice(i, i + 2), 16));
}

const PALETTE_FLOATS = new Float32Array(PALETTE.flatMap(color => {
  const [r, g, b] = hexRgb(color);
  return [r / 255, g / 255, b / 255, color === "#7f8985" ? .32 : .46];
}));

export function formatNumber(value, digits = 2) {
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
    const col = panel % layout.cols;
    const row = Math.floor(panel / layout.cols);
    const x = col * layout.cellW;
    const y = row * layout.cellH;
    const left = x + layout.cellW * PANEL_PAD_X;
    const right = x + layout.cellW * (1 - PANEL_PAD_X);
    const top = y + layout.cellH * PANEL_PAD_Y;
    const bottom = y + layout.cellH * (1 - PANEL_PAD_Y);
    ctx.strokeStyle = "rgba(24,34,31,.14)";
    ctx.lineWidth = 1;
    ctx.strokeRect(left + .5, top + .5, right - left - 1, bottom - top - 1);
    ctx.fillStyle = "#27332f";
    const title = data?.panelNames?.[panel] || "All data";
    ctx.fillText(String(title).slice(0, 42), left + 3, y + 7);
    if (view && !options.hideTicks) {
      ctx.font = "9px ui-monospace, SFMono-Regular, monospace";
      ctx.fillStyle = "#7a827e";
      ctx.textBaseline = "bottom";
      ctx.fillText(formatNumber(view.xmin), left, bottom + 15);
      const xr = formatNumber(view.xmax);
      ctx.fillText(xr, right - ctx.measureText(xr).width, bottom + 15);
      ctx.save();
      ctx.translate(left - 5, bottom);
      ctx.rotate(-Math.PI / 2);
      ctx.fillText(formatNumber(view.zmin), 0, 0);
      ctx.restore();
      ctx.textBaseline = "top";
      ctx.font = "600 11px Inter, system-ui, sans-serif";
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
    const col = roi.panel % layout.cols, row = Math.floor(roi.panel / layout.cols);
    const left = (col + PANEL_PAD_X) * layout.cellW;
    const right = (col + 1 - PANEL_PAD_X) * layout.cellW;
    const top = (row + PANEL_PAD_Y) * layout.cellH;
    const bottom = (row + 1 - PANEL_PAD_Y) * layout.cellH;
    const x = left + (roi.x - view.xmin) / (view.xmax - view.xmin) * (right - left);
    const y = bottom - (roi.z - view.zmin) / (view.zmax - view.zmin) * (bottom - top);
    const radius = Math.abs(roi.reach / (view.xmax - view.xmin) * (right - left));
    ctx.strokeStyle = roi.side === "left" ? "rgba(49,95,140,.68)" : "rgba(201,91,63,.68)";
    ctx.fillStyle = roi.side === "left" ? "rgba(49,95,140,.05)" : "rgba(201,91,63,.05)";
    ctx.setLineDash([4, 3]); ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }
  ctx.setLineDash([]);
  if (data.roiCounts) {
    ctx.font = "600 9px Inter, system-ui, sans-serif";
    for (let panel = 0; panel < data.panelCount; panel += 1) {
      const col = panel % layout.cols, row = Math.floor(panel / layout.cols);
      const left = (col + PANEL_PAD_X) * layout.cellW;
      const right = (col + 1 - PANEL_PAD_X) * layout.cellW;
      const top = (row + PANEL_PAD_Y) * layout.cellH;
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
    const col = panel % layout.cols, row = Math.floor(panel / layout.cols);
    const left = (col + PANEL_PAD_X) * layout.cellW, right = (col + 1 - PANEL_PAD_X) * layout.cellW;
    const top = (row + PANEL_PAD_Y) * layout.cellH, bottom = (row + 1 - PANEL_PAD_Y) * layout.cellH;
    for (let index = 0; index < data.rings.length; index += 1) {
      const ring = data.rings[index];
      const x = left + (ring.x - view.xmin) / (view.xmax - view.xmin) * (right - left);
      const y = bottom - (ring.z - view.zmin) / (view.zmax - view.zmin) * (bottom - top);
      const radius = Math.abs(ring.r / (view.xmax - view.xmin) * (right - left));
      ctx.strokeStyle = "rgba(24,34,31,.82)"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.stroke();
      ctx.fillStyle = "rgba(24,34,31,.82)"; ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill();
      ctx.fillText(String(index + 1), x + 5, y - 12);
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

  canvas.addEventListener("pointerdown", event => {
    if (!renderer.data || !renderer.view) return;
    canvas.setPointerCapture(event.pointerId);
    drag = {x: event.clientX, y: event.clientY, view: {...renderer.view}, moved: false};
  });
  canvas.addEventListener("pointerup", event => {
    const finished = drag;
    drag = null;
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) { /* no-op */ }
    if (finished && !finished.moved && renderer.inspectHandler) {
      const rect = canvas.getBoundingClientRect();
      const point = renderer.pixelToWorld(event.clientX - rect.left, event.clientY - rect.top);
      if (point) renderer.inspectHandler(point, renderer.view);
    }
  });
  canvas.addEventListener("pointercancel", () => { drag = null; });
  canvas.addEventListener("pointermove", event => {
    const rect = canvas.getBoundingClientRect();
    if (drag) {
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 3) drag.moved = true;
      const layout = panelLayout(renderer.width, renderer.height, renderer.data.panelCount, renderer.data.columns);
      const innerW = layout.cellW * (1 - 2 * PANEL_PAD_X);
      const innerH = layout.cellH * (1 - 2 * PANEL_PAD_Y);
      const dx = (event.clientX - drag.x) / innerW * (drag.view.xmax - drag.view.xmin);
      const dz = (event.clientY - drag.y) / innerH * (drag.view.zmax - drag.view.zmin);
      renderer.setView({
        xmin: drag.view.xmin - dx, xmax: drag.view.xmax - dx,
        zmin: drag.view.zmin + dz, zmax: drag.view.zmax + dz,
      }, true);
      tooltip.hidden = true;
      return;
    }
    const point = renderer.pixelToWorld(event.clientX - rect.left, event.clientY - rect.top);
    if (!point) { tooltip.hidden = true; return; }
    tooltip.hidden = false;
    tooltip.style.left = `${event.clientX - rect.left}px`;
    tooltip.style.top = `${event.clientY - rect.top}px`;
    tooltip.textContent = `${renderer.data.panelNames?.[point.panel] || "All"} · X ${formatNumber(point.x, 3)} · Z ${formatNumber(point.z, 3)}`;
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
  }, {passive: false});
  canvas.addEventListener("dblclick", event => {
    event.preventDefault();
    renderer.resetView(true);
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
  }
  setRoisVisible(value) { this.roisVisible = !!value; this.draw(); }
  setColumns(columns) {
    if (!this.data) return;
    this.data.columns = Number(columns) || 0;
    this.draw();
  }
  setInspectHandler(handler) { this.inspectHandler = handler; }
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
    const ux = (px - col * layout.cellW) / layout.cellW;
    const uy = (py - row * layout.cellH) / layout.cellH;
    const u = (ux - PANEL_PAD_X) / (1 - 2 * PANEL_PAD_X);
    const v = 1 - (uy - PANEL_PAD_Y) / (1 - 2 * PANEL_PAD_Y);
    if (u < 0 || u > 1 || v < 0 || v > 1) return null;
    return {
      panel,
      x: this.view.xmin + u * (this.view.xmax - this.view.xmin),
      z: this.view.zmin + v * (this.view.zmax - this.view.zmin),
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
    this.timeLimit = Number.POSITIVE_INFINITY;
    this._initGl();
    installSpatialInteraction(this, this.overlay);
    installResize(this);
  }
  _initGl() {
    const gl = this.gl;
    this.program = program(gl, `#version 300 es
      precision highp float;
      layout(location=0) in vec2 a_position;
      layout(location=1) in float a_panel;
      layout(location=2) in float a_color;
      layout(location=3) in float a_time;
      layout(location=4) in float a_sample;
      uniform vec4 u_view;
      uniform vec2 u_grid;
      uniform float u_fraction;
      uniform float u_time;
      uniform vec4 u_palette[64];
      out vec4 v_color;
      out float v_inside;
      void main() {
        float ux = (a_position.x - u_view.x) / (u_view.y - u_view.x);
        float uz = (a_position.y - u_view.z) / (u_view.w - u_view.z);
        float col = mod(a_panel, u_grid.x);
        float row = floor(a_panel / u_grid.x);
        float px = (col + ${PANEL_PAD_X.toFixed(4)} + ux * ${(1 - 2 * PANEL_PAD_X).toFixed(4)}) / u_grid.x;
        float py = (u_grid.y - 1.0 - row + ${PANEL_PAD_Y.toFixed(4)} + uz * ${(1 - 2 * PANEL_PAD_Y).toFixed(4)}) / u_grid.y;
        gl_Position = vec4(px * 2.0 - 1.0, py * 2.0 - 1.0, 0.0, 1.0);
        int colorIndex = clamp(int(a_color + .5), 0, 63);
        v_color = u_palette[colorIndex];
        v_inside = (ux >= 0.0 && ux <= 1.0 && uz >= 0.0 && uz <= 1.0 && a_sample <= u_fraction && a_time <= u_time) ? 1.0 : 0.0;
      }`, `#version 300 es
      precision highp float;
      in vec4 v_color;
      in float v_inside;
      out vec4 outColor;
      void main() { if (v_inside < .5) discard; outColor = v_color; }
    `);
    this.vao = gl.createVertexArray();
    this.buffers = Array.from({length: 5}, () => gl.createBuffer());
  }
  _attribute(index, data, size, type, normalized = false) {
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffers[index]);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(index);
    gl.vertexAttribPointer(index, size, type, normalized, 0, 0);
  }
  setData(data, preserveView = true) {
    this.data = data;
    const gl = this.gl;
    gl.bindVertexArray(this.vao);
    this._attribute(0, data.vertices, 2, gl.FLOAT);
    this._attribute(1, data.panels, 1, gl.UNSIGNED_SHORT);
    this._attribute(2, data.colors, 1, gl.UNSIGNED_BYTE);
    this._attribute(3, data.times, 1, gl.FLOAT);
    this._attribute(4, data.samples, 1, gl.FLOAT);
    gl.bindVertexArray(null);
    this.vertexCount = data.panels.length;
    if (!preserveView || !this.view) this.view = squareBounds(data.bounds);
    this.draw();
  }
  setFraction(value) { this.fraction = Math.max(.01, Math.min(1, value)); this.drawGl(); }
  setTime(value) { this.timeLimit = Number.isFinite(value) ? value : Number.POSITIVE_INFINITY; this.drawGl(); }
  resize() {
    const dpr = setupCanvas(this.canvas, this.width, this.height);
    setupCanvas(this.overlay, this.width, this.height);
    this.gl.viewport(0, 0, Math.round(this.width * dpr), Math.round(this.height * dpr));
  }
  drawGl() {
    const gl = this.gl;
    gl.clearColor(1, .994, .976, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (!this.data || !this.view || !this.vertexCount) return;
    const layout = panelLayout(this.width, this.height, this.data.panelCount, this.data.columns);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(this.program);
    gl.uniform4f(gl.getUniformLocation(this.program, "u_view"), this.view.xmin, this.view.xmax, this.view.zmin, this.view.zmax);
    gl.uniform2f(gl.getUniformLocation(this.program, "u_grid"), layout.cols, layout.rows);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_fraction"), this.fraction);
    gl.uniform1f(gl.getUniformLocation(this.program, "u_time"), this.timeLimit);
    gl.uniform4fv(gl.getUniformLocation(this.program, "u_palette[0]"), PALETTE_FLOATS);
    gl.bindVertexArray(this.vao);
    gl.drawArrays(gl.LINES, 0, this.vertexCount);
    gl.bindVertexArray(null);
  }
  draw() {
    this.drawGl();
    const dpr = setupCanvas(this.overlay, this.width, this.height);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.clearRect(0, 0, this.width, this.height);
    if (this.data) {
      drawPanelChrome(this.ctx, this.width, this.height, this.data, this.view);
      drawPanelRois(this.ctx, this.width, this.height, this.data, this.view, this.roisVisible);
      drawPanelRings(this.ctx, this.width, this.height, this.data, this.view);
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
    this.ctx.fillStyle = "#fffdf9";
    this.ctx.fillRect(0, 0, this.width, this.height);
    return this.data ? panelLayout(this.width, this.height, this.data.panelCount, this.data.columns) : null;
  }
  pane(panel, layout) {
    const col = panel % layout.cols;
    const row = Math.floor(panel / layout.cols);
    return {
      left: (col + PANEL_PAD_X) * layout.cellW,
      right: (col + 1 - PANEL_PAD_X) * layout.cellW,
      top: (row + PANEL_PAD_Y) * layout.cellH,
      bottom: (row + 1 - PANEL_PAD_Y) * layout.cellH,
    };
  }
  worldToPixel(x, z, pane) {
    return [
      pane.left + (x - this.view.xmin) / (this.view.xmax - this.view.xmin) * (pane.right - pane.left),
      pane.bottom - (z - this.view.zmin) / (this.view.zmax - this.view.zmin) * (pane.bottom - pane.top),
    ];
  }
}

export class HeatmapRenderer extends CanvasSpatialRenderer {
  constructor(host, onViewChange) {
    super(host, onViewChange);
    this.metric = "time";
    this.scale = "linear";
    this.textures = [];
  }
  setMetricScale(metric, scale) {
    this.metric = metric;
    this.scale = scale;
    this.buildTextures();
    this.draw();
  }
  onDataChanged() { this.buildTextures(); }
  buildTextures() {
    this.textures = [];
    if (!this.data) return;
    const {nx, nz, panelCount, count, time} = this.data;
    const cells = nx * nz;
    for (let panel = 0; panel < panelCount; panel += 1) {
      const source = this.metric === "count" ? count : time;
      let total = 0;
      let max = 0;
      for (let i = 0; i < cells; i += 1) {
        const v = source[panel * cells + i];
        total += v;
        if (v > max) max = v;
      }
      const canvas = document.createElement("canvas");
      canvas.width = nx; canvas.height = nz;
      const context = canvas.getContext("2d");
      const image = context.createImageData(nx, nz);
      for (let iz = 0; iz < nz; iz += 1) {
        for (let ix = 0; ix < nx; ix += 1) {
          const sourceIndex = panel * cells + iz * nx + ix;
          let value = source[sourceIndex];
          if (this.metric === "percent") value = total > 0 ? time[sourceIndex] / total * 100 : 0;
          const vmax = this.metric === "percent" ? 100 : max;
          const norm = value > 0 && vmax > 0
            ? (this.scale === "log" ? Math.log1p(value) / Math.log1p(vmax) : value / vmax)
            : 0;
          const rgb = hexRgb(sequentialColor(norm));
          const target = ((nz - 1 - iz) * nx + ix) * 4;
          image.data[target] = rgb[0]; image.data[target + 1] = rgb[1]; image.data[target + 2] = rgb[2];
          image.data[target + 3] = value > 0 ? Math.round(42 + norm * 208) : 0;
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
    const ix0 = Math.max(0, (this.view.xmin - gridX0) / (gridX1 - gridX0) * this.data.nx);
    const ix1 = Math.min(this.data.nx, (this.view.xmax - gridX0) / (gridX1 - gridX0) * this.data.nx);
    const iz0 = Math.max(0, (gridZ1 - this.view.zmax) / (gridZ1 - gridZ0) * this.data.nz);
    const iz1 = Math.min(this.data.nz, (gridZ1 - this.view.zmin) / (gridZ1 - gridZ0) * this.data.nz);
    for (let panel = 0; panel < this.data.panelCount; panel += 1) {
      const pane = this.pane(panel, layout);
      const [dx0, dy1] = this.worldToPixel(Math.max(this.view.xmin, gridX0), Math.max(this.view.zmin, gridZ0), pane);
      const [dx1, dy0] = this.worldToPixel(Math.min(this.view.xmax, gridX1), Math.min(this.view.zmax, gridZ1), pane);
      this.ctx.save();
      this.ctx.beginPath(); this.ctx.rect(pane.left, pane.top, pane.right - pane.left, pane.bottom - pane.top); this.ctx.clip();
      if (ix1 > ix0 && iz1 > iz0) {
        this.ctx.imageSmoothingEnabled = true;
        this.ctx.drawImage(this.textures[panel], ix0, iz0, ix1 - ix0, iz1 - iz0, dx0, dy0, dx1 - dx0, dy1 - dy0);
      }
      this.ctx.restore();
    }
    drawPanelChrome(this.ctx, this.width, this.height, this.data, this.view);
    drawPanelRois(this.ctx, this.width, this.height, this.data, this.view, this.roisVisible);
  }
}

function angleColor(angle, alpha = 1) {
  const hue = ((angle % 360) + 360) % 360;
  return `hsla(${hue}, 62%, 43%, ${alpha})`;
}

export class DirectionRenderer extends CanvasSpatialRenderer {
  draw() {
    const layout = this.begin();
    if (!layout || !this.view) return;
    const {nx, nz, panelCount, angle, strength, abundance} = this.data;
    const cells = nx * nz;
    let maxAbundance = 0;
    for (let i = 0; i < abundance.length; i += 1) maxAbundance = Math.max(maxAbundance, abundance[i]);
    for (let panel = 0; panel < panelCount; panel += 1) {
      const pane = this.pane(panel, layout);
      this.ctx.save();
      this.ctx.beginPath(); this.ctx.rect(pane.left, pane.top, pane.right - pane.left, pane.bottom - pane.top); this.ctx.clip();
      for (let iz = 0; iz < nz; iz += 1) {
        const z = this.data.z0 + (iz + .5) * this.data.bin;
        if (z < this.view.zmin - this.data.bin || z > this.view.zmax + this.data.bin) continue;
        for (let ix = 0; ix < nx; ix += 1) {
          const x = this.data.x0 + (ix + .5) * this.data.bin;
          if (x < this.view.xmin - this.data.bin || x > this.view.xmax + this.data.bin) continue;
          const index = panel * cells + iz * nx + ix;
          const count = abundance[index];
          if (!(count > 0) || !Number.isFinite(angle[index])) continue;
          const [cx, cy] = this.worldToPixel(x, z, pane);
          const [x0] = this.worldToPixel(x - this.data.bin / 2, z, pane);
          const [x1] = this.worldToPixel(x + this.data.bin / 2, z, pane);
          const cellPx = Math.abs(x1 - x0);
          const abundanceScale = Math.sqrt(count / Math.max(1, maxAbundance));
          const len = cellPx * .43 * strength[index];
          const radians = angle[index] * Math.PI / 180;
          const dx = Math.sin(radians) * len;
          const dy = -Math.cos(radians) * len;
          this.ctx.fillStyle = angleColor(angle[index], .04 + .14 * abundanceScale);
          this.ctx.fillRect(cx - cellPx / 2, cy - cellPx / 2, cellPx, cellPx);
          this.ctx.strokeStyle = angleColor(angle[index], .35 + .65 * abundanceScale);
          this.ctx.lineWidth = .7 + 2 * abundanceScale;
          this.ctx.beginPath(); this.ctx.moveTo(cx - dx, cy - dy); this.ctx.lineTo(cx + dx, cy + dy); this.ctx.stroke();
          this.ctx.fillStyle = this.ctx.strokeStyle;
          this.ctx.beginPath(); this.ctx.arc(cx + dx, cy + dy, 1.4 + abundanceScale, 0, Math.PI * 2); this.ctx.fill();
        }
      }
      this.ctx.restore();
    }
    drawPanelChrome(this.ctx, this.width, this.height, this.data, this.view);
    drawPanelRois(this.ctx, this.width, this.height, this.data, this.view, this.roisVisible);
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
    this.ctx.fillStyle = "#fffdf9"; this.ctx.fillRect(0, 0, this.width, this.height);
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
