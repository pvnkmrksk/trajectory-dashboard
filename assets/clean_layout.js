/*
 * Publication presentation mode.
 *
 * This module only edits mounted Plotly layout/style attributes. It never
 * rebuilds traces, bins, statistics or source data, so Clean/Full remains a
 * cheap appearance toggle even for large trajectory datasets.
 */
(function () {
  "use strict";

  var SCALE_NAME = "td-clean-scale";
  var SPATIAL_IDS = [
    "trajectory-plot", "loop-observer-plot", "heatmap-plot", "flow-plot"
  ];
  var POLAR_IDS = ["polar-plot", "initial-heading-plot"];
  var CARTESIAN_IDS = [
    "roi-plot", "custom-region-diagnostics-plot", "trial-metrics-plot",
    "vel-histogram", "disp-histogram", "raw-trace-plot"
  ];
  var ALL_IDS = SPATIAL_IDS.concat(POLAR_IDS, CARTESIAN_IDS);
  var state = {
    on: false,
    style: {},
    timer: null,
    frames: {},
    force: false
  };

  function clone(value) {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
  }

  function graphDiv(id) {
    var container = document.getElementById(id);
    return container && container.querySelector(".js-plotly-plot");
  }

  function presentationClass(id, on) {
    var container = document.getElementById(id);
    if (!container || !container.classList) return;
    container.classList.toggle("td-clean-mode", Boolean(on));
    container.classList.toggle(
      "td-clean-spatial", Boolean(on) && SPATIAL_IDS.indexOf(id) >= 0
    );
    container.classList.toggle(
      "td-clean-polar", Boolean(on) && POLAR_IDS.indexOf(id) >= 0
    );
    container.classList.toggle(
      "td-clean-cartesian", Boolean(on) && CARTESIAN_IDS.indexOf(id) >= 0
    );
  }

  function axisName(prefix, index) {
    return prefix + "axis" + (index === 1 ? "" : String(index));
  }

  function axisRef(prefix, index) {
    return prefix + (index === 1 ? "" : String(index));
  }

  function axisIndex(ref) {
    var match = /^[xy](\d*)$/.exec(String(ref || ""));
    if (!match) return null;
    return match[1] ? Number(match[1]) : 1;
  }

  function spatialLimit(gd) {
    var meta = gd && gd.layout && gd.layout.meta;
    var count = Number(meta && meta.spatial_axis_count);
    if (Number.isFinite(count) && count > 0) return Math.floor(count);
    var keys = Object.keys((gd && gd.layout) || {}).filter(function (key) {
      return /^xaxis\d*$/.test(key);
    });
    return Math.max(1, keys.length);
  }

  function spatialIndices(gd) {
    var limit = spatialLimit(gd);
    var found = {};
    ((gd && gd.data) || []).forEach(function (trace) {
      var xIndex = axisIndex(trace && (trace.xaxis || "x"));
      var yIndex = axisIndex(trace && (trace.yaxis || "y"));
      if (xIndex && xIndex === yIndex && xIndex <= limit) found[xIndex] = true;
    });
    var values = Object.keys(found).map(Number).sort(function (a, b) {
      return a - b;
    });
    if (values.length) return values;
    values = [];
    for (var index = 1; index <= limit; index += 1) values.push(index);
    return values;
  }

  function visibleRange(gd, prefix, index) {
    var name = axisName(prefix, index);
    var full = gd && gd._fullLayout && gd._fullLayout[name];
    var range = full && full.range;
    if (!range || range.length !== 2) {
      range = gd && gd.layout && gd.layout[name] && gd.layout[name].range;
    }
    if (!range || range.length !== 2) return null;
    var values = range.map(Number);
    return values.every(Number.isFinite) ? values : null;
  }

  function niceLength(target) {
    if (!(target > 0)) return 1;
    var magnitude = Math.pow(10, Math.floor(Math.log10(target)));
    var ratio = target / magnitude;
    var factor = ratio >= 5 ? 5 : (ratio >= 2 ? 2 : 1);
    return factor * magnitude;
  }

  function axisKeys(layout) {
    return Object.keys(layout || {}).filter(function (key) {
      return /^[xy]axis\d*$/.test(key);
    });
  }

  function polarKeys(layout) {
    return Object.keys(layout || {}).filter(function (key) {
      return /^polar\d*$/.test(key);
    });
  }

  function snapshot(gd) {
    var axes = {};
    axisKeys(gd.layout).forEach(function (name) {
      var axis = gd.layout[name] || {};
      axes[name] = {};
      [
        "showgrid", "showticklabels", "ticks", "ticklen", "title",
        "zeroline", "showline", "mirror", "linecolor", "linewidth"
      ].forEach(function (field) {
        axes[name][field] = clone(axis[field]);
      });
    });
    var polars = {};
    polarKeys(gd.layout).forEach(function (name) {
      var polar = gd.layout[name] || {};
      polars[name] = {
        bgcolor: clone(polar.bgcolor),
        angularaxis: clone(polar.angularaxis),
        radialaxis: clone(polar.radialaxis)
      };
    });
    var traceScales = [];
    ((gd && gd.data) || []).forEach(function (trace, index) {
      if (!trace) return;
      if (trace.showscale !== undefined) {
        traceScales.push({
          index: index, path: "showscale", value: Boolean(trace.showscale)
        });
      }
      if (trace.marker && trace.marker.showscale !== undefined) {
        traceScales.push({
          index: index, path: "marker.showscale",
          value: Boolean(trace.marker.showscale)
        });
      }
    });
    return {
      dataRef: gd.data,
      axes: axes,
      polars: polars,
      showlegend: clone(gd.layout.showlegend),
      paperBg: clone(gd.layout.paper_bgcolor),
      plotBg: clone(gd.layout.plot_bgcolor),
      traceScales: traceScales
    };
  }

  function freshFigure(gd) {
    var base = gd && gd.__tdCleanBase;
    return !base || base.dataRef !== gd.data;
  }

  function restoreValue(update, path, value) {
    update[path] = value === undefined ? null : clone(value);
  }

  function scaleStyle() {
    var style = (state.style && state.style.spatial_layout) || {};
    var unitScale = Number(style.unit_scale);
    var barWidth = Number(style.scale_bar_width);
    return {
      unitScale: Number.isFinite(unitScale) && unitScale > 0 ? unitScale : 1,
      unitLabel: String(style.unit_label || "cm"),
      barColor: style.scale_bar_color || "#38444f",
      barWidth: Number.isFinite(barWidth) && barWidth > 0 ? barWidth : 3
    };
  }

  function scaleObjects(gd, indices, shapes, annotations) {
    var style = scaleStyle();
    indices.forEach(function (index) {
      var xr = visibleRange(gd, "x", index);
      var yr = visibleRange(gd, "y", index);
      if (!xr || !yr) return;
      var xmin = Math.min(xr[0], xr[1]);
      var xmax = Math.max(xr[0], xr[1]);
      var ymin = Math.min(yr[0], yr[1]);
      var ymax = Math.max(yr[0], yr[1]);
      var spanX = xmax - xmin;
      var spanY = ymax - ymin;
      if (!(spanX > 0) || !(spanY > 0)) return;
      var realLength = niceLength(spanX * 0.20 * style.unitScale);
      var dataLength = realLength / style.unitScale;
      var endX = xmax - spanX * 0.075;
      var startX = endX - dataLength;
      var startY = ymin + spanY * 0.075;
      shapes.push({
        name: SCALE_NAME,
        type: "line",
        xref: axisRef("x", index),
        yref: axisRef("y", index),
        x0: startX,
        x1: endX,
        y0: startY,
        y1: startY,
        layer: "above",
        line: {color: style.barColor, width: style.barWidth}
      });
      annotations.push({
        name: SCALE_NAME,
        xref: axisRef("x", index),
        yref: axisRef("y", index),
        x: (startX + endX) / 2,
        y: startY,
        yshift: -7,
        xanchor: "center",
        yanchor: "top",
        showarrow: false,
        text: Number(realLength.toPrecision(4)).toLocaleString("en-US") +
          " " + style.unitLabel,
        font: {size: 11, color: style.barColor},
        bgcolor: "rgba(255,255,255,0.72)",
        borderpad: 1
      });
    });
  }

  function spatialUpdate(gd, update) {
    var indices = spatialIndices(gd);
    indices.forEach(function (index) {
      ["x", "y"].forEach(function (prefix) {
        var name = axisName(prefix, index);
        update[name + ".showgrid"] = false;
        update[name + ".showticklabels"] = false;
        update[name + ".ticks"] = "";
        update[name + ".title.text"] = "";
        update[name + ".zeroline"] = false;
        update[name + ".showline"] = false;
      });
    });
    var shapes = (gd.layout.shapes || []).filter(function (shape) {
      return !shape || shape.name !== SCALE_NAME;
    });
    var annotations = (gd.layout.annotations || []).filter(function (annotation) {
      return !annotation || annotation.name !== SCALE_NAME;
    });
    scaleObjects(gd, indices, shapes, annotations);
    update.shapes = shapes;
    update.annotations = annotations;
  }

  function scaleBarUpdate(gd) {
    if (!state.on || !gd || !gd.layout || !window.Plotly) {
      return Promise.resolve(false);
    }
    var shapes = (gd.layout.shapes || []).filter(function (shape) {
      return !shape || shape.name !== SCALE_NAME;
    });
    var annotations = (gd.layout.annotations || []).filter(function (annotation) {
      return !annotation || annotation.name !== SCALE_NAME;
    });
    scaleObjects(gd, spatialIndices(gd), shapes, annotations);
    gd.__tdCleanPainting = true;
    return window.Plotly.relayout(gd, {
      shapes: shapes,
      annotations: annotations
    }).then(function () {
      gd.__tdCleanPainting = false;
      return true;
    }).catch(function () {
      gd.__tdCleanPainting = false;
      return false;
    });
  }

  function cartesianUpdate(gd, update) {
    axisKeys(gd.layout).forEach(function (name) {
      update[name + ".showgrid"] = false;
      update[name + ".zeroline"] = false;
      update[name + ".showline"] = true;
      update[name + ".mirror"] = false;
      update[name + ".linecolor"] = "#6b7280";
      update[name + ".linewidth"] = 1;
      update[name + ".ticks"] = "outside";
      update[name + ".ticklen"] = 4;
    });
  }

  function polarUpdate(gd, update) {
    polarKeys(gd.layout).forEach(function (name) {
      update[name + ".bgcolor"] = "#ffffff";
      update[name + ".angularaxis.showgrid"] = false;
      update[name + ".angularaxis.showline"] = false;
      update[name + ".angularaxis.showticklabels"] = false;
      update[name + ".angularaxis.ticks"] = "";
      update[name + ".radialaxis.showgrid"] = false;
      update[name + ".radialaxis.showline"] = false;
      update[name + ".radialaxis.showticklabels"] = false;
      update[name + ".radialaxis.ticks"] = "";
    });
  }

  function restoreUpdate(gd, update) {
    var base = gd.__tdCleanBase;
    if (!base) return;
    Object.keys(base.axes || {}).forEach(function (name) {
      Object.keys(base.axes[name]).forEach(function (field) {
        restoreValue(update, name + "." + field, base.axes[name][field]);
      });
    });
    Object.keys(base.polars || {}).forEach(function (name) {
      restoreValue(update, name + ".bgcolor", base.polars[name].bgcolor);
      restoreValue(
        update, name + ".angularaxis", base.polars[name].angularaxis
      );
      restoreValue(update, name + ".radialaxis", base.polars[name].radialaxis);
    });
    restoreValue(update, "showlegend", base.showlegend);
    restoreValue(update, "paper_bgcolor", base.paperBg);
    restoreValue(update, "plot_bgcolor", base.plotBg);
    update.shapes = (gd.layout.shapes || []).filter(function (shape) {
      return !shape || shape.name !== SCALE_NAME;
    });
    update.annotations = (gd.layout.annotations || []).filter(function (annotation) {
      return !annotation || annotation.name !== SCALE_NAME;
    });
  }

  function updateTraceScales(gd, on) {
    var base = gd.__tdCleanBase;
    if (!base || !window.Plotly) return Promise.resolve();
    var jobs = (base.traceScales || []).map(function (item) {
      var value = on ? false : item.value;
      var traceUpdate = {};
      traceUpdate[item.path] = value;
      return window.Plotly.restyle(gd, traceUpdate, [item.index]);
    });
    return Promise.all(jobs);
  }

  function attachSpatialZoom(gd, id) {
    if (!gd || !gd.on) return;
    if (gd.__tdCleanRelayout && gd.removeListener) {
      gd.removeListener("plotly_relayout", gd.__tdCleanRelayout);
    }
    gd.__tdCleanRelayout = function (eventData) {
      if (!state.on || gd.__tdCleanPainting || !eventData) return;
      var changedRange = Object.keys(eventData).some(function (key) {
        return /^(xaxis|yaxis)\d*\.(range|autorange)/.test(key);
      });
      if (!changedRange) return;
      if (state.frames[id]) window.cancelAnimationFrame(state.frames[id]);
      state.frames[id] = window.requestAnimationFrame(function () {
        // Pan/zoom only changes scale-bar geometry. Reapplying every axis
        // property here caused a visible full→clean flash during interaction.
        scaleBarUpdate(gd);
        state.frames[id] = null;
      });
    };
    gd.on("plotly_relayout", gd.__tdCleanRelayout);
  }

  function paint(id) {
    var gd = graphDiv(id);
    if (!gd || !gd.layout || !window.Plotly) return Promise.resolve(false);
    presentationClass(id, state.on);
    var wasActive = Boolean(gd.__tdCleanActive);
    var fresh = freshFigure(gd);
    if (state.on && (fresh || !gd.__tdCleanActive)) {
      gd.__tdCleanBase = snapshot(gd);
    }
    if (state.on && wasActive && !fresh && !state.force) {
      if (SPATIAL_IDS.indexOf(id) >= 0) return scaleBarUpdate(gd);
      return Promise.resolve(true);
    }
    var update = {};
    if (state.on) {
      update.showlegend = false;
      update.paper_bgcolor = "#ffffff";
      update.plot_bgcolor = "#ffffff";
      if (SPATIAL_IDS.indexOf(id) >= 0) spatialUpdate(gd, update);
      else if (POLAR_IDS.indexOf(id) >= 0) polarUpdate(gd, update);
      else cartesianUpdate(gd, update);
      gd.__tdCleanActive = true;
    } else {
      if (!wasActive || freshFigure(gd)) {
        gd.__tdCleanActive = false;
        return Promise.resolve(true);
      }
      restoreUpdate(gd, update);
      gd.__tdCleanActive = false;
    }
    gd.__tdCleanPainting = true;
    return Promise.all([
      window.Plotly.relayout(gd, update),
      updateTraceScales(gd, state.on)
    ]).then(function () {
      gd.__tdCleanPainting = false;
      if (SPATIAL_IDS.indexOf(id) >= 0) attachSpatialZoom(gd, id);
      return true;
    }).catch(function () {
      gd.__tdCleanPainting = false;
      return false;
    });
  }

  function paintAll(attempt) {
    attempt = attempt || 0;
    var found = false;
    var jobs = [];
    ALL_IDS.forEach(function (id) {
      if (graphDiv(id)) {
        found = true;
        jobs.push(paint(id));
      }
    });
    var legend = document.getElementById("flow-field-legend");
    if (legend) legend.style.display = state.on ? "none" : "";
    if (!found && attempt < 10) {
      window.setTimeout(function () { paintAll(attempt + 1); }, 45);
    }
    return Promise.all(jobs).then(function (result) {
      state.force = false;
      return result;
    });
  }

  function schedule(delay) {
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = window.setTimeout(function () {
      state.timer = null;
      paintAll(0);
    }, delay === undefined ? 45 : delay);
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    clean_layout: {
      render: function (on, visualStyle) {
        state.on = Boolean(on);
        state.style = visualStyle || {};
        ALL_IDS.forEach(function (id) {
          presentationClass(id, state.on);
        });
        // This entry point is called only for an explicit mode/style change or
        // a completed structural render, never for pan/zoom. Force one complete
        // presentation pass even if Plotly reused its data-array identity.
        state.force = true;
        schedule(35);
        return state.on ?
          "Switch to the full interactive axes, grids and legends." :
          "Use a publication-ready layout without rebuilding plot data.";
      },
      refresh: function () {
        if (state.on) schedule(35);
      }
    }
  });
}());
