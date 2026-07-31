/*
 * Conditional half-transition heatmap + clicked-bin trajectory observer.
 *
 * The server computes exact unique-trial cell denominators and both outcome
 * variants. Outcome switching and clicked-bin path selection remain entirely
 * browser-local, including in the self-contained HTML export.
 */
(function () {
  "use strict";

  var states = {};

  function clone(value) {
    return JSON.parse(JSON.stringify(value || {}));
  }

  function graphDiv(id) {
    var host = document.getElementById(id);
    if (!host) return null;
    if (host.classList && host.classList.contains("js-plotly-plot")) {
      return host;
    }
    return host.querySelector(".js-plotly-plot");
  }

  function sequence(value) {
    if (Array.isArray(value)) return value;
    if (ArrayBuffer.isView(value)) return Array.from(value);
    if (value && typeof value === "object" &&
        typeof value.bdata === "string" && value.dtype) {
      try {
        var binary = window.atob(value.bdata);
        var bytes = new Uint8Array(binary.length);
        for (var index = 0; index < binary.length; index += 1) {
          bytes[index] = binary.charCodeAt(index);
        }
        var dtype = String(value.dtype).replace(/[<>=|]/g, "");
        var constructors = {
          f8: Float64Array, f4: Float32Array,
          i4: Int32Array, i2: Int16Array, i1: Int8Array,
          u4: Uint32Array, u2: Uint16Array, u1: Uint8Array
        };
        var Constructor = constructors[dtype];
        return Constructor ? Array.from(new Constructor(bytes.buffer)) : null;
      } catch (_error) {
        return null;
      }
    }
    return null;
  }

  function setStatus(state, text) {
    var node = document.getElementById(state.statusId);
    if (node) node.textContent = text;
  }

  function blankFigure(message) {
    return {
      data: [],
      layout: {
        height: 360,
        template: "plotly_white",
        margin: {l: 30, r: 20, t: 30, b: 30},
        xaxis: {visible: false},
        yaxis: {visible: false},
        annotations: [{
          x: 0.5, y: 0.5, xref: "paper", yref: "paper",
          text: message, showarrow: false,
          font: {size: 15, color: "#667085"}
        }]
      }
    };
  }

  function variant(bundle, outcome) {
    var variants = (bundle && bundle.variants) || {};
    return variants[outcome] || variants.crossed || variants.ended || null;
  }

  function applyVariantToFigure(figure, selected) {
    if (!selected) return figure;
    (figure.data || []).forEach(function (trace, index) {
      if (String((trace && trace.type) || "").toLowerCase() !== "heatmap") {
        return;
      }
      trace.z = selected.z[index] || [];
      trace.customdata = selected.customdata[index] || [];
      trace.hovertemplate = selected.hovertemplate;
    });
    return figure;
  }

  function restyleVariant(state) {
    var selected = variant(state.bundle, state.outcome);
    var gd = graphDiv(state.heatId);
    if (!selected || !gd || !window.Plotly) return Promise.resolve();
    var indices = [];
    var z = [];
    var customdata = [];
    var hovertemplate = [];
    (gd.data || []).forEach(function (trace, index) {
      if (String((trace && trace.type) || "").toLowerCase() !== "heatmap") {
        return;
      }
      indices.push(index);
      z.push(selected.z[index] || []);
      customdata.push(selected.customdata[index] || []);
      hovertemplate.push(selected.hovertemplate);
    });
    if (!indices.length) return Promise.resolve();
    return Promise.resolve(window.Plotly.restyle(gd, {
      z: z, customdata: customdata, hovertemplate: hovertemplate
    }, indices));
  }

  function traceGroup(trace) {
    return String((((trace || {}).meta || {}).td_group_value) || "");
  }

  function pointSegmentId(row) {
    return Array.isArray(row) && row.length > 6 ? String(row[6] || "") : "";
  }

  function sourcePaths(state, group) {
    var source = graphDiv(state.sourceId);
    var paths = [];
    if (!source) return paths;
    (source.data || []).forEach(function (trace, traceIndex) {
      if (traceGroup(trace) !== String(group)) return;
      var x = sequence(trace.x);
      var y = sequence(trace.y);
      var custom = sequence(trace.customdata);
      if (!x || !y || !custom) return;
      var bySegment = {};
      var order = [];
      var length = Math.min(x.length, y.length, custom.length);
      for (var index = 0; index < length; index += 1) {
        var sid = pointSegmentId(custom[index]);
        var px = Number(x[index]);
        var py = Number(y[index]);
        if (!sid || !Number.isFinite(px) || !Number.isFinite(py)) continue;
        if (!bySegment[sid]) {
          bySegment[sid] = {id: sid, x: [], y: [], traceIndex: traceIndex};
          order.push(sid);
        }
        bySegment[sid].x.push(px);
        bySegment[sid].y.push(py);
      }
      order.forEach(function (sid) {
        if (bySegment[sid].x.length) paths.push(bySegment[sid]);
      });
    });
    return paths;
  }

  function inside(x, y, bounds) {
    return x >= bounds.x0 && x <= bounds.x1 &&
      y >= bounds.z0 && y <= bounds.z1;
  }

  // Liang–Barsky line clipping: returns the first point where a segment enters
  // the clicked rectangle, or null when it misses.
  function segmentRectangleEntry(x0, y0, x1, y1, bounds) {
    var dx = x1 - x0;
    var dy = y1 - y0;
    var p = [-dx, dx, -dy, dy];
    var q = [
      x0 - bounds.x0, bounds.x1 - x0,
      y0 - bounds.z0, bounds.z1 - y0
    ];
    var low = 0;
    var high = 1;
    for (var index = 0; index < 4; index += 1) {
      if (p[index] === 0) {
        if (q[index] < 0) return null;
        continue;
      }
      var ratio = q[index] / p[index];
      if (p[index] < 0) low = Math.max(low, ratio);
      else high = Math.min(high, ratio);
      if (low > high) return null;
    }
    return {x: x0 + low * dx, y: y0 + low * dy};
  }

  function firstEntry(path, bounds) {
    if (!path.x.length) return null;
    if (inside(path.x[0], path.y[0], bounds)) {
      return {beforeIndex: 0, futureIndex: 0, x: path.x[0], y: path.y[0]};
    }
    for (var index = 0; index < path.x.length - 1; index += 1) {
      var hit = segmentRectangleEntry(
        path.x[index], path.y[index],
        path.x[index + 1], path.y[index + 1], bounds
      );
      if (hit) {
        return {
          beforeIndex: index,
          futureIndex: index + 1,
          x: hit.x,
          y: hit.y
        };
      }
    }
    return null;
  }

  function qualifies(path, hit, split, side, outcome) {
    if (side === 0) return false;
    if (outcome === "ended") {
      var end = Number(path.y[path.y.length - 1]);
      return side < 0 ? end > split : end < split;
    }
    for (var index = hit.futureIndex; index < path.y.length; index += 1) {
      var value = Number(path.y[index]);
      if ((side < 0 && value > split) || (side > 0 && value < split)) {
        return true;
      }
    }
    return false;
  }

  function appendPath(targetX, targetY, valuesX, valuesY) {
    if (targetX.length) {
      targetX.push(null);
      targetY.push(null);
    }
    Array.prototype.push.apply(targetX, valuesX);
    Array.prototype.push.apply(targetY, valuesY);
  }

  function edgeBounds(bundle, point) {
    var xedges = (bundle && bundle.xedges) || [];
    var yedges = (bundle && bundle.yedges) || [];
    var indices = point.pointNumber || point.pointIndex || [];
    var xIndex = Math.max(0, Math.min(
      xedges.length - 2,
      Number(indices[1])
    ));
    var yIndex = Math.max(0, Math.min(
      yedges.length - 2,
      Number(indices[0])
    ));
    if (!Number.isFinite(xIndex) || !Number.isFinite(yIndex) ||
        xedges.length < 2 || yedges.length < 2) {
      return null;
    }
    return {
      x0: Number(xedges[xIndex]), x1: Number(xedges[xIndex + 1]),
      z0: Number(yedges[yIndex]), z1: Number(yedges[yIndex + 1])
    };
  }

  function renderObserver(state, point) {
    if (!point) return;
    var bundle = state.bundle || {};
    var bounds = edgeBounds(bundle, point);
    var custom = point.customdata;
    var trace = point.data || point.fullData || {};
    var group = traceGroup(trace);
    var split = Number(bundle.split_z);
    if (!bounds || !Array.isArray(custom) || !Number.isFinite(split)) return;
    var side = bounds.z1 <= split ? -1 : (bounds.z0 >= split ? 1 : 0);
    if (!side) {
      setStatus(state, "This cell straddles the split and has no defined side.");
      return;
    }
    var beforeX = [];
    var beforeY = [];
    var futureX = [];
    var futureY = [];
    var entryX = [];
    var entryY = [];
    var entryIds = [];
    var paths = sourcePaths(state, group);
    var successful = 0;
    paths.forEach(function (path) {
      var hit = firstEntry(path, bounds);
      if (!hit || !qualifies(
          path, hit, split, side, state.outcome)) return;
      successful += 1;
      appendPath(
        beforeX, beforeY,
        path.x.slice(0, hit.beforeIndex + 1).concat([hit.x]),
        path.y.slice(0, hit.beforeIndex + 1).concat([hit.y])
      );
      appendPath(
        futureX, futureY,
        [hit.x].concat(path.x.slice(hit.futureIndex)),
        [hit.y].concat(path.y.slice(hit.futureIndex))
      );
      entryX.push(hit.x);
      entryY.push(hit.y);
      entryIds.push(path.id);
    });

    var style = bundle.style || {};
    var beforeColor = style.before_color || "#89919d";
    var futureColor = style.future_color || "#5e4a82";
    var selectedLine = style.selected_line || "#b87917";
    var selectedFill = style.selected_fill || "rgba(198,151,45,0.12)";
    var data = [
      {
        type: "scattergl", mode: "lines",
        x: beforeX, y: beforeY,
        line: {color: beforeColor, width: 1.1},
        opacity: 0.28, showlegend: false,
        hoverinfo: "skip", name: "Before cell entry"
      },
      {
        type: "scattergl", mode: "lines",
        x: futureX, y: futureY,
        line: {color: futureColor, width: 1.55},
        opacity: 0.78, showlegend: false,
        hoverinfo: "skip", name: "Successful future"
      },
      {
        type: "scatter", mode: "markers",
        x: entryX, y: entryY, customdata: entryIds,
        marker: {
          size: 7, color: "#fff7d1",
          line: {color: selectedLine, width: 1.4}
        },
        showlegend: false, name: "First cell entry",
        hovertemplate: "segment=%{customdata}<br>" +
          "first bin entry x=%{x:.2f} z=%{y:.2f}<extra></extra>"
      }
    ];
    var xRange = point.xaxis && point.xaxis.range ?
      Array.from(point.xaxis.range) : [
        Number(bundle.xedges[0]),
        Number(bundle.xedges[bundle.xedges.length - 1])
      ];
    var yRange = point.yaxis && point.yaxis.range ?
      Array.from(point.yaxis.range) : [
        Number(bundle.yedges[0]),
        Number(bundle.yedges[bundle.yedges.length - 1])
      ];
    var exactSuccess = Number(custom[0] || 0);
    var exactEntrants = Number(custom[1] || 0);
    var probability = exactEntrants ?
      100 * exactSuccess / exactEntrants : 0;
    var definition = state.outcome === "ended" ?
      "ended opposite" : "crossed opposite";
    var layout = {
      height: 510,
      template: "plotly_white",
      margin: {l: 52, r: 28, t: 58, b: 42},
      dragmode: "pan",
      showlegend: false,
      uirevision: "transition-cell:" + group + ":" +
        bounds.x0 + ":" + bounds.z0 + ":" + state.outcome,
      xaxis: {range: xRange, title: "X"},
      yaxis: {
        range: yRange, title: "Z",
        scaleanchor: "x", scaleratio: 1
      },
      shapes: [
        {
          type: "rect",
          x0: bounds.x0, x1: bounds.x1,
          y0: bounds.z0, y1: bounds.z1,
          fillcolor: selectedFill,
          line: {color: selectedLine, width: 2},
          layer: "above"
        },
        {
          type: "line",
          x0: xRange[0], x1: xRange[1],
          y0: split, y1: split,
          line: {
            color: style.split_line || "rgba(57,45,76,0.82)",
            width: 1.6, dash: "dash"
          }
        }
      ],
      annotations: [{
        x: 0, y: 1.08, xref: "paper", yref: "paper",
        xanchor: "left", showarrow: false,
        text: "<b>" + group + "</b> · " +
          exactSuccess.toLocaleString() + "/" +
          exactEntrants.toLocaleString() + " trials " + definition +
          " (" + probability.toFixed(1) + "%)",
        font: {size: 12, color: "#3f315d"}
      }],
      meta: {spatial_axis_count: 1}
    };
    var observer = graphDiv(state.observerId);
    if (!observer || !window.Plotly) return;
    var config = {
      scrollZoom: true, displayModeBar: true, displaylogo: false,
      responsive: true
    };
    var promise = state.observerPainted ?
      window.Plotly.react(observer, data, layout, config) :
      window.Plotly.newPlot(observer, data, layout, config);
    Promise.resolve(promise).then(function () {
      state.observerPainted = true;
    });
    state.selectedPoint = point;
    setStatus(
      state,
      exactSuccess.toLocaleString() + "/" +
      exactEntrants.toLocaleString() + " exact entering trials " +
      definition + " (" + probability.toFixed(1) + "%); " +
      successful.toLocaleString() +
      " currently displayed path" + (successful === 1 ? "" : "s") + "."
    );
  }

  function refreshSelectedPoint(state) {
    var point = state.selectedPoint;
    var gd = graphDiv(state.heatId);
    if (!point || !gd) return point;
    var curve = Number(point.curveNumber);
    var indices = point.pointNumber || point.pointIndex || [];
    var row = Number(indices[0]);
    var column = Number(indices[1]);
    var trace = (gd.data || [])[curve];
    if (!trace || !Number.isFinite(row) || !Number.isFinite(column)) {
      return point;
    }
    var custom = sequence(trace.customdata) || [];
    var z = sequence(trace.z) || [];
    point.data = trace;
    point.fullData = trace;
    point.customdata = custom[row] && custom[row][column];
    point.z = z[row] && z[row][column];
    return point;
  }

  function bindClick(state) {
    var gd = graphDiv(state.heatId);
    if (!gd || !gd.on) return;
    if (state.clickHandler && gd.removeListener) {
      try { gd.removeListener("plotly_click", state.clickHandler); }
      catch (_error) {}
    }
    state.clickHandler = function (event) {
      var point = event && event.points && event.points[0];
      if (point) renderObserver(state, point);
    };
    gd.on("plotly_click", state.clickHandler);
  }

  function mount(state, reuseMounted) {
    var bundle = state.bundle || {};
    var enabled = state.enabled;
    var gd = graphDiv(state.heatId);
    if (!gd || !window.Plotly) return;
    if (!enabled || !bundle.enabled || (!bundle.figure && !reuseMounted)) {
      var empty = blankFigure(
        bundle.message || "Enable transition probability in the sidebar.");
      window.Plotly.react(gd, empty.data, empty.layout, {
        displayModeBar: false, responsive: true
      });
      return;
    }
    var selected = variant(bundle, state.outcome);
    if (!selected) return;
    var signature = String(bundle.signature || "");
    var structuralChange = !reuseMounted && state.signature !== signature;
    var promise;
    if (structuralChange) {
      var figure = applyVariantToFigure(clone(bundle.figure), selected);
      promise = window.Plotly.newPlot(
        gd, figure.data || [], figure.layout || {}, {
          scrollZoom: true, displayModeBar: true,
          displaylogo: false, responsive: true
        }
      );
      state.signature = signature;
    } else {
      promise = restyleVariant(state);
    }
    Promise.resolve(promise).then(function () {
      bindClick(state);
      if (window.__attachViewportSync && state.heatId === "transition-plot") {
        window.__attachViewportSync(graphDiv(state.heatId), "transition", true);
      }
      if (window.dash_clientside &&
          window.dash_clientside.clean_layout &&
          window.dash_clientside.clean_layout.refresh) {
        window.dash_clientside.clean_layout.refresh();
      }
      if (window.dash_clientside &&
          window.dash_clientside.panel_order &&
          window.dash_clientside.panel_order.reapply) {
        window.dash_clientside.panel_order.reapply();
      }
      if (state.selectedPoint) {
        renderObserver(state, refreshSelectedPoint(state));
      }
    });
  }

  function dashboardState() {
    var key = "dashboard";
    states[key] = states[key] || {
      heatId: "transition-plot",
      observerId: "transition-observer-plot",
      sourceId: "trajectory-plot",
      statusId: "transition-status",
      signature: null,
      outcome: "crossed",
      enabled: false,
      selectedPoint: null,
      observerPainted: false
    };
    return states[key];
  }

  window.TransitionProbabilityObserver = {
    renderDashboard: function (options) {
      options = options || {};
      var state = dashboardState();
      state.bundle = options.bundle || {};
      state.outcome = options.outcome === "ended" ? "ended" : "crossed";
      state.enabled = Array.isArray(options.enabled) &&
        options.enabled.indexOf("on") >= 0;
      state.trajectoryFigure = options.trajectoryFigure || {};
      state.fraction = options.fraction;
      state.seed = options.seed;
      mount(state, false);
      return state.bundle.message || (
        state.enabled ? "Calculating transition probability…" :
          "Transition observer off."
      );
    },

    attachExport: function (options) {
      options = options || {};
      var key = "export:" + String(options.heatId || "");
      var state = states[key] || {
        signature: null, selectedPoint: null, observerPainted: false
      };
      state.heatId = options.heatId;
      state.observerId = options.observerId;
      state.sourceId = options.sourceId;
      state.statusId = options.statusId;
      state.bundle = options.bundle || {};
      state.outcome = options.outcome === "ended" ? "ended" : "crossed";
      state.enabled = true;
      state.signature = String(state.bundle.signature || "");
      states[key] = state;
      mount(state, true);
      return {
        setOutcome: function (outcome) {
          state.outcome = outcome === "ended" ? "ended" : "crossed";
          state.selectedPoint = null;
          mount(state, true);
          setStatus(
            state,
            state.bundle.message || "Click a transition cell to inspect paths."
          );
        }
      };
    }
  };
}());
