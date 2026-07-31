/*
 * Browser-local rectangular observation windows.
 *
 * Shapes are painted over the already-rendered spatial plots. Dragging or
 * resizing a box updates one compact Dash store immediately; grouped window
 * diagnostics, polar and window-scoped trial metrics refresh after a quiet
 * debounce interval on the server.
 */
(function () {
  "use strict";

  var PREFIX = "custom-region:";
  var LABEL = "custom-region-label";
  var graphIds = ["trajectory-plot", "heatmap-plot", "flow-plot"];
  var state = {
    enabled: false,
    regions: [],
    active: "region-1",
    stats: {},
    style: {},
    painting: false,
    frame: null,
    timer: null
  };

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function numberOr(value, fallback) {
    var numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : fallback;
  }

  function normalise(regions) {
    var clean = (Array.isArray(regions) ? regions : []).map(function (region, index) {
      var x0 = numberOr(region && region.x0, -3);
      var x1 = numberOr(region && region.x1, 3);
      var z0 = numberOr(region && region.z0, -3);
      var z1 = numberOr(region && region.z1, 3);
      return {
        id: String((region && region.id) || ("region-" + (index + 1))),
        name: String((region && region.name) || ("Window " + (index + 1))),
        x0: Math.min(x0, x1),
        x1: Math.max(x0, x1),
        z0: Math.min(z0, z1),
        z1: Math.max(z0, z1)
      };
    }).filter(function (region) {
      return region.x1 > region.x0 && region.z1 > region.z0;
    });
    return clean.length ? clean : [
      {id: "region-1", name: "Window 1", x0: -3, x1: 3, z0: -3, z1: 3}
    ];
  }

  function graphDiv(id) {
    var container = document.getElementById(id);
    return container && container.querySelector(".js-plotly-plot");
  }

  function subplotPairs(gd) {
    var meta = gd && gd.layout && gd.layout.meta;
    var spatialCount = Number(meta && meta.spatial_axis_count);
    if (Number.isFinite(spatialCount) && spatialCount > 0) {
      var spatialPairs = [];
      for (var index = 1; index <= Math.floor(spatialCount); index += 1) {
        spatialPairs.push([
          "x" + (index === 1 ? "" : index),
          "y" + (index === 1 ? "" : index)
        ]);
      }
      return spatialPairs;
    }
    var seen = {};
    var pairs = [];
    ((gd && gd.data) || []).forEach(function (trace) {
      if (!trace || String(trace.type || "").toLowerCase() === "table") return;
      var xaxis = trace.xaxis || "x";
      var yaxis = trace.yaxis || "y";
      var key = xaxis + "|" + yaxis;
      if (!seen[key] && /^x\d*$/.test(xaxis) && /^y\d*$/.test(yaxis)) {
        seen[key] = true;
        pairs.push([xaxis, yaxis]);
      }
    });
    return pairs.length ? pairs : [["x", "y"]];
  }

  function shapeStyle(region, pair, active, style) {
    var selected = region.id === active;
    return {
      type: "rect",
      name: PREFIX + region.id,
      xref: pair[0],
      yref: pair[1],
      x0: region.x0,
      x1: region.x1,
      y0: region.z0,
      y1: region.z1,
      editable: true,
      layer: "above",
      fillcolor: style.fill || "rgba(207,157,68,0.065)",
      line: {
        color: selected ?
          (style.active_line || "#b87917") :
          (style.inactive_line || "rgba(168,122,52,0.62)"),
        width: selected ? numberOr(style.line_width, 2.2) :
          Math.max(1, numberOr(style.line_width, 2.2) - 0.7),
        dash: selected ? "dash" : "dot"
      }
    };
  }

  function panelStats(index) {
    var panels = (state.stats && state.stats.panels) || [];
    return panels[index] || {};
  }

  function annotations(gd, pairs, style) {
    if (!state.enabled || gd.__regionObserverId !== "flow-plot") return [];
    var out = [];
    pairs.forEach(function (pair, panelIndex) {
      var shares = panelStats(panelIndex).regions || [];
      state.regions.forEach(function (region, regionIndex) {
        var share = shares.filter(function (item) {
          return String(item.id) === region.id;
        })[0] || {};
        out.push({
          name: LABEL,
          xref: pair[0],
          yref: pair[1],
          x: region.x0,
          y: region.z1,
          xanchor: "left",
          yanchor: regionIndex % 2 ? "top" : "bottom",
          xshift: 3,
          yshift: regionIndex % 2 ? -3 : 3,
          showarrow: false,
          text: region.name + " · " + numberOr(share.percent, 0).toFixed(1) + "%",
          bgcolor: style.label_background || "rgba(255,250,235,0.88)",
          borderpad: 2,
          font: {size: 9, color: style.active_line || "#8a5a10"}
        });
      });
    });
    return out;
  }

  function emit(shape) {
    if (!shape) return;
    var match = new RegExp("^" + PREFIX + "(.+)$").exec(String(shape.name || ""));
    if (!match) return;
    var id = match[1];
    var current = state.regions.filter(function (region) {
      return region.id === id;
    })[0];
    if (!current) return;
    var x0 = numberOr(shape.x0, current.x0);
    var x1 = numberOr(shape.x1, current.x1);
    var z0 = numberOr(shape.y0, current.z0);
    var z1 = numberOr(shape.y1, current.z1);
    var cleanNumber = function (value) {
      return Number(Number(value).toPrecision(10));
    };
    var updated = Object.assign({}, current, {
      x0: cleanNumber(Math.min(x0, x1)),
      x1: cleanNumber(Math.max(x0, x1)),
      z0: cleanNumber(Math.min(z0, z1)),
      z1: cleanNumber(Math.max(z0, z1))
    });
    state.regions = state.regions.map(function (region) {
      return region.id === id ? updated : region;
    });
    state.active = id;
    if (window.dash_clientside && window.dash_clientside.set_props) {
      window.dash_clientside.set_props(
        "custom-regions-store", {data: clone(state.regions)}
      );
      window.dash_clientside.set_props(
        "custom-region-active", {value: id}
      );
      window.dash_clientside.set_props(
        "custom-region-x0", {value: updated.x0}
      );
      window.dash_clientside.set_props(
        "custom-region-x1", {value: updated.x1}
      );
      window.dash_clientside.set_props(
        "custom-region-z0", {value: updated.z0}
      );
      window.dash_clientside.set_props(
        "custom-region-z1", {value: updated.z1}
      );
    }
  }

  function shapeFromEvent(gd, eventData) {
    if (!eventData || !gd || gd.__regionObserverPainting) return null;
    var index = null;
    Object.keys(eventData).some(function (key) {
      var match = /^shapes\[(\d+)\]\./.exec(key);
      if (!match) return false;
      index = Number(match[1]);
      return true;
    });
    if (index === null) return null;
    return ((gd.layout && gd.layout.shapes) || [])[index] || null;
  }

  function attach(gd) {
    if (!gd || !gd.on) return;
    if (gd.__regionObserverRelayout && gd.removeListener) {
      gd.removeListener("plotly_relayout", gd.__regionObserverRelayout);
    }
    gd.__regionObserverRelayout = function (eventData) {
      var shape = shapeFromEvent(gd, eventData);
      if (!shape) return;
      // The rectangle itself follows the pointer in Plotly. Analytics update
      // once on gesture release, avoiding a burst of polar/table callbacks.
      emit(shape);
    };
    gd.on("plotly_relayout", gd.__regionObserverRelayout);
  }

  function paintGraph(id, attempt) {
    attempt = attempt || 0;
    var gd = graphDiv(id);
    if (!gd || !gd.layout || !window.Plotly) {
      if (attempt < 10) {
        window.setTimeout(function () { paintGraph(id, attempt + 1); }, 45);
      }
      return;
    }
    gd.__regionObserverId = id;
    var pairs = subplotPairs(gd);
    var style = (state.style && state.style.region_observer) || {};
    var signature = JSON.stringify({
      enabled: state.enabled,
      regions: state.regions,
      active: state.active,
      stats: state.stats,
      style: style,
      pairs: pairs
    });
    if (gd.__regionObserverSignature === signature &&
        gd.__regionObserverDataRef === gd.data) {
      attach(gd);
      return;
    }
    var baseShapes = (gd.layout.shapes || []).filter(function (shape) {
      return String((shape && shape.name) || "").indexOf(PREFIX) !== 0;
    });
    var regionShapes = [];
    if (state.enabled) {
      pairs.forEach(function (pair) {
        state.regions.forEach(function (region) {
          regionShapes.push(shapeStyle(region, pair, state.active, style));
        });
      });
    }
    var baseAnnotations = (gd.layout.annotations || []).filter(function (annotation) {
      return !annotation || annotation.name !== LABEL;
    });
    gd.__regionObserverPainting = true;
    window.Plotly.relayout(gd, {
      shapes: baseShapes.concat(regionShapes),
      annotations: baseAnnotations.concat(annotations(gd, pairs, style)),
      editrevision: "custom-regions"
    }).then(function () {
      gd.__regionObserverPainting = false;
      gd.__regionObserverSignature = signature;
      gd.__regionObserverDataRef = gd.data;
      attach(gd);
    }).catch(function () {
      gd.__regionObserverPainting = false;
    });
  }

  function paintAll() {
    graphIds.forEach(function (id) { paintGraph(id, 0); });
  }

  function schedulePaint() {
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = window.setTimeout(function () {
      state.timer = null;
      paintAll();
    }, 45);
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    region_observer: {
      render: function (enabled, regions, active, stats, visualStyle) {
        state.enabled = Boolean(enabled && enabled.indexOf("on") >= 0);
        state.regions = normalise(regions);
        state.active = String(active || state.regions[0].id);
        state.stats = stats || {};
        state.style = visualStyle || {};
        schedulePaint();
        if (!state.enabled) {
          return "Observation windows off; enable them to subset polar and trial metrics.";
        }
        var samples = ((state.stats || {}).regions || []).reduce(function (sum, row) {
          return sum + numberOr(row.samples, 0);
        }, 0);
        return state.regions.length.toLocaleString() + " observation window" +
          (state.regions.length === 1 ? "" : "s") +
          " · drag or resize any dashed box · " +
          samples.toLocaleString() + " window-memberships · analytics refresh " +
          "7 s after editing stops";
      }
    }
  });
}());
