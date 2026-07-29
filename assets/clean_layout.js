/*
 * Presentation-mode spatial plots. This is intentionally browser-only:
 * toggling grids/labels and recomputing a scale bar must never rebuild data.
 */
(function () {
  "use strict";

  var SHAPE = "td-clean-scale";
  var ANNOTATION = "td-clean-scale";
  var graphIds = [
    "trajectory-plot", "loop-observer-plot", "heatmap-plot", "flow-plot"
  ];
  var state = {on: false, style: {}, frame: null};

  function clone(value) {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
  }

  function graphDiv(id) {
    var container = document.getElementById(id);
    return container && container.querySelector(".js-plotly-plot");
  }

  function axisName(prefix, index) {
    return prefix + "axis" + (index === 1 ? "" : String(index));
  }

  function axisRef(prefix, index) {
    return prefix + (index === 1 ? "" : String(index));
  }

  function spatialCount(gd) {
    var meta = gd && gd.layout && gd.layout.meta;
    var count = Number(meta && meta.spatial_axis_count);
    if (Number.isFinite(count) && count > 0) return Math.floor(count);
    var keys = Object.keys((gd && gd.layout) || {}).filter(function (key) {
      return /^xaxis\d*$/.test(key);
    });
    return Math.max(1, keys.length);
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

  function baseSnapshot(gd) {
    var count = spatialCount(gd);
    var axes = {};
    ["x", "y"].forEach(function (prefix) {
      for (var index = 1; index <= count; index += 1) {
        var name = axisName(prefix, index);
        var axis = (gd.layout && gd.layout[name]) || {};
        axes[name] = {
          showgrid: axis.showgrid,
          showticklabels: axis.showticklabels,
          ticks: axis.ticks,
          title: clone(axis.title),
          zeroline: axis.zeroline,
          showline: axis.showline
        };
      }
    });
    return {
      axes: axes,
      showlegend: gd.layout && gd.layout.showlegend
    };
  }

  function shouldRefreshBase(gd) {
    if (!gd.__tdCleanBase) return true;
    if (!gd.__tdCleanActive) return true;
    var axis = gd.layout && gd.layout.xaxis;
    // Plotly.newPlot replaces the layout but leaves arbitrary graph-div
    // properties. A visible tick label means a fresh figure needs recapturing.
    return axis && axis.showticklabels !== false;
  }

  function paint(gd) {
    if (!gd || !gd.layout || !window.Plotly) return;
    if (state.on && shouldRefreshBase(gd)) {
      gd.__tdCleanBase = baseSnapshot(gd);
    }
    var update = {};
    var count = spatialCount(gd);
    var style = (state.style && state.style.spatial_layout) || {};
    var unitScale = Number(style.unit_scale);
    if (!Number.isFinite(unitScale) || unitScale <= 0) unitScale = 1;
    var unitLabel = String(style.unit_label || "cm");
    var barColor = style.scale_bar_color || "#38444f";
    var barWidth = Number(style.scale_bar_width);
    if (!Number.isFinite(barWidth) || barWidth <= 0) barWidth = 3;
    var shapes = (gd.layout.shapes || []).filter(function (shape) {
      return !shape || shape.name !== SHAPE;
    });
    var annotations = (gd.layout.annotations || []).filter(function (annotation) {
      return !annotation || annotation.name !== ANNOTATION;
    });

    if (state.on) {
      for (var index = 1; index <= count; index += 1) {
        ["x", "y"].forEach(function (prefix) {
          var name = axisName(prefix, index);
          update[name + ".showgrid"] = false;
          update[name + ".showticklabels"] = false;
          update[name + ".ticks"] = "";
          update[name + ".title.text"] = "";
          update[name + ".zeroline"] = false;
          update[name + ".showline"] = false;
        });
        var xr = visibleRange(gd, "x", index);
        var yr = visibleRange(gd, "y", index);
        if (!xr || !yr) continue;
        var xmin = Math.min(xr[0], xr[1]);
        var xmax = Math.max(xr[0], xr[1]);
        var ymin = Math.min(yr[0], yr[1]);
        var ymax = Math.max(yr[0], yr[1]);
        var spanX = xmax - xmin;
        var spanY = ymax - ymin;
        var realLength = niceLength(spanX * 0.20 * unitScale);
        var dataLength = realLength / unitScale;
        var startX = xmin + spanX * 0.075;
        var startY = ymin + spanY * 0.075;
        shapes.push({
          name: SHAPE, type: "line",
          xref: axisRef("x", index), yref: axisRef("y", index),
          x0: startX, x1: startX + dataLength,
          y0: startY, y1: startY,
          layer: "above",
          line: {color: barColor, width: barWidth}
        });
        annotations.push({
          name: ANNOTATION,
          xref: axisRef("x", index), yref: axisRef("y", index),
          x: startX + dataLength / 2, y: startY,
          yshift: 7, xanchor: "center", yanchor: "bottom",
          showarrow: false,
          text: Number(realLength.toPrecision(4)).toLocaleString("en-US") +
            " " + unitLabel,
          font: {size: 10, color: barColor},
          bgcolor: "rgba(255,255,255,0.58)",
          borderpad: 1
        });
      }
      update.showlegend = false;
      update.shapes = shapes;
      update.annotations = annotations;
      gd.__tdCleanActive = true;
    } else {
      var base = gd.__tdCleanBase;
      if (base) {
        Object.keys(base.axes || {}).forEach(function (name) {
          var axis = base.axes[name] || {};
          ["showgrid", "showticklabels", "ticks", "title",
           "zeroline", "showline"].forEach(function (field) {
            update[name + "." + field] = axis[field] === undefined ?
              null : axis[field];
          });
        });
        update.showlegend = base.showlegend === undefined ?
          null : base.showlegend;
      }
      update.shapes = shapes;
      update.annotations = annotations;
      gd.__tdCleanActive = false;
    }

    gd.__tdCleanPainting = true;
    window.Plotly.relayout(gd, update).then(function () {
      gd.__tdCleanPainting = false;
      attach(gd);
    }).catch(function () {
      gd.__tdCleanPainting = false;
    });
  }

  function attach(gd) {
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
      if (state.frame) window.cancelAnimationFrame(state.frame);
      state.frame = window.requestAnimationFrame(function () {
        paint(gd);
        state.frame = null;
      });
    };
    gd.on("plotly_relayout", gd.__tdCleanRelayout);
  }

  function paintAll(attempt) {
    attempt = attempt || 0;
    var found = false;
    graphIds.forEach(function (id) {
      var gd = graphDiv(id);
      if (gd) {
        found = true;
        paint(gd);
      }
    });
    var legend = document.getElementById("flow-field-legend");
    if (legend) legend.style.display = state.on ? "none" : "";
    if (!found && attempt < 10) {
      window.setTimeout(function () { paintAll(attempt + 1); }, 45);
    }
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    clean_layout: {
      render: function (on, visualStyle) {
        state.on = Boolean(on);
        state.style = visualStyle || {};
        window.setTimeout(function () { paintAll(0); }, 35);
        window.setTimeout(function () { paintAll(0); }, 190);
        return state.on ?
          "Restore axes, grids and legends." :
          "Hide spatial grids, axes and legends and use an adaptive scale bar.";
      }
    }
  });
}());
