/*
 * Browser-local curtain-ring trajectory observer.
 *
 * The server already sends one point-budgeted, whole-trial-sampled trajectory
 * figure. This module reuses that figure as its source, finds every _seg_id
 * whose polyline intersects a movable circle, and renders the path before and
 * after first entry without another Dash callback.
 */
(function () {
  "use strict";

  var state = {
    source: null,
    enabled: false,
    rings: [],
    active: "ring-1",
    matchMode: "any",
    style: {},
    painting: false
  };

  function numberOr(value, fallback) {
    var numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : fallback;
  }

  function clone(value) {
    if (value === undefined || value === null) return value;
    return JSON.parse(JSON.stringify(value));
  }

  function typedArray(value) {
    if (Array.isArray(value)) return value;
    if (ArrayBuffer.isView(value)) return Array.from(value);
    if (!value || typeof value !== "object" || !value.bdata) return [];

    try {
      var binary = window.atob(value.bdata);
      var bytes = new Uint8Array(binary.length);
      for (var i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }
      var constructors = {
        f8: Float64Array,
        f4: Float32Array,
        i4: Int32Array,
        i2: Int16Array,
        i1: Int8Array,
        u4: Uint32Array,
        u2: Uint16Array,
        u1: Uint8Array
      };
      var Constructor = constructors[value.dtype];
      if (!Constructor) return [];
      return Array.from(new Constructor(bytes.buffer));
    } catch (_error) {
      return [];
    }
  }

  function customRow(customdata, index) {
    if (!customdata || !Array.isArray(customdata)) return null;
    var row = customdata[index];
    return Array.isArray(row) ? row : null;
  }

  function segmentId(customdata, index) {
    var row = customRow(customdata, index);
    if (!row || row.length < 7 || row[6] === null || row[6] === "") return null;
    return String(row[6]);
  }

  function firstIntersection(xs, ys, start, end, cx, cz, radius2) {
    for (var i = start; i < end; i += 1) {
      var x = Number(xs[i]);
      var z = Number(ys[i]);
      if (!Number.isFinite(x) || !Number.isFinite(z)) continue;
      var px = x - cx;
      var pz = z - cz;
      if (px * px + pz * pz <= radius2) return i;
      if (i === start) continue;

      var x0 = Number(xs[i - 1]);
      var z0 = Number(ys[i - 1]);
      if (!Number.isFinite(x0) || !Number.isFinite(z0)) continue;
      var dx = x - x0;
      var dz = z - z0;
      var denom = dx * dx + dz * dz;
      if (!(denom > 0)) continue;
      var t = ((cx - x0) * dx + (cz - z0) * dz) / denom;
      t = Math.max(0, Math.min(1, t));
      var qx = x0 + t * dx - cx;
      var qz = z0 + t * dz - cz;
      if (qx * qx + qz * qz <= radius2) return i;
    }
    return -1;
  }

  function appendSlice(targetX, targetY, targetCustom, targetColor,
                       xs, ys, customdata, markerColor, start, end) {
    for (var i = start; i < end; i += 1) {
      targetX.push(Number(xs[i]));
      targetY.push(Number(ys[i]));
      targetCustom.push(customRow(customdata, i) || ["", "", "", "", "", "", ""]);
      if (targetColor) targetColor.push(markerColor[i]);
    }
    targetX.push(null);
    targetY.push(null);
    targetCustom.push(["", "", "", "", "", "", ""]);
    if (targetColor) targetColor.push(null);
  }

  function subplotPairs(data) {
    var seen = {};
    var pairs = [];
    data.forEach(function (trace) {
      if (!trace || !trace.customdata) return;
      var xaxis = trace.xaxis || "x";
      var yaxis = trace.yaxis || "y";
      var key = xaxis + "|" + yaxis;
      if (!seen[key]) {
        seen[key] = true;
        pairs.push([xaxis, yaxis]);
      }
    });
    return pairs;
  }

  function normaliseRings(rings) {
    var input = Array.isArray(rings) ? rings : [];
    var clean = input.map(function (ring, index) {
      var radius = Math.max(0.001, numberOr(ring && ring.radius, 3));
      return {
        id: String((ring && ring.id) || ("ring-" + (index + 1))),
        name: String((ring && ring.name) || ("Ring " + (index + 1))),
        x: numberOr(ring && ring.x, 0),
        z: numberOr(ring && ring.z, 0),
        radius: radius
      };
    });
    return clean.length ? clean : [
      {id: "ring-1", name: "Ring 1", x: 0, z: 0, radius: 3}
    ];
  }

  function ringShapes(pairs, rings, active, style) {
    var shapes = [];
    pairs.forEach(function (pair) {
      rings.forEach(function (ring) {
        var selected = ring.id === active;
        shapes.push({
          type: "circle",
          name: "loop-observer-ring:" + ring.id,
          xref: pair[0],
          yref: pair[1],
          x0: ring.x - ring.radius,
          x1: ring.x + ring.radius,
          y0: ring.z - ring.radius,
          y1: ring.z + ring.radius,
          editable: true,
          layer: "above",
          fillcolor: style.ring_fill || "rgba(245,183,0,0.10)",
          line: {
            color: selected ?
              (style.ring_color || "#c88a00") :
              (style.inactive_ring_color || "rgba(190,134,14,0.55)"),
            width: selected ? 3.2 : 1.8,
            dash: selected ? "solid" : "dot"
          }
        });
      });
    });
    return shapes;
  }

  function titleAnnotations(layout) {
    return (layout.annotations || []).filter(function (annotation) {
      return annotation && annotation.xref === "paper" && annotation.yref === "paper";
    });
  }

  function observerFigure(source, rings, active, matchMode, visualStyle) {
    // Backward-compatible standalone export call: build(source, x, z, radius).
    if (typeof rings === "number") {
      rings = [{
        id: "ring-1", name: "Ring 1", x: numberOr(rings, 0),
        z: numberOr(active, 0), radius: Math.max(0.001, numberOr(matchMode, 3))
      }];
      active = "ring-1";
      matchMode = "any";
      visualStyle = {};
    }
    rings = normaliseRings(rings);
    matchMode = matchMode === "all" ? "all" : "any";
    var style = (visualStyle && visualStyle.loop_observer) || visualStyle || {};
    var sourceData = (source && source.data) || [];
    var output = [];
    var candidateIds = {};
    var matchingIds = {};
    var displayedPoints = 0;
    var showedPastLegend = false;
    var showedEntryLegend = false;

    sourceData.forEach(function (base) {
      if (!base || !base.customdata || String(base.type || "").toLowerCase() !== "scattergl") {
        return;
      }
      var xs = typedArray(base.x);
      var ys = typedArray(base.y);
      var customdata = base.customdata;
      if (!xs.length || xs.length !== ys.length) return;

      var markerColor = base.marker ? typedArray(base.marker.color) : [];
      var keepMarkerColor = markerColor.length === xs.length;
      var pastX = [];
      var pastY = [];
      var pastCustom = [];
      var futureX = [];
      var futureY = [];
      var futureCustom = [];
      var futureColor = keepMarkerColor ? [] : null;
      var entryX = [];
      var entryY = [];
      var entryCustom = [];

      var start = 0;
      while (start < xs.length) {
        var sid = segmentId(customdata, start);
        if (!sid || !Number.isFinite(Number(xs[start])) || !Number.isFinite(Number(ys[start]))) {
          start += 1;
          continue;
        }
        var end = start + 1;
        while (
          end < xs.length &&
          segmentId(customdata, end) === sid &&
          Number.isFinite(Number(xs[end])) &&
          Number.isFinite(Number(ys[end]))
        ) {
          end += 1;
        }
        candidateIds[sid] = true;
        var hits = rings.map(function (ring) {
          return firstIntersection(
            xs, ys, start, end, ring.x, ring.z, ring.radius * ring.radius
          );
        });
        var matched = matchMode === "all" ?
          hits.every(function (hitIndex) { return hitIndex >= 0; }) :
          hits.some(function (hitIndex) { return hitIndex >= 0; });
        if (matched) {
          var availableHits = hits.filter(function (hitIndex) {
            return hitIndex >= 0;
          });
          var hit = matchMode === "all" ?
            Math.max.apply(null, availableHits) :
            Math.min.apply(null, availableHits);
          matchingIds[sid] = true;
          appendSlice(
            pastX, pastY, pastCustom, null,
            xs, ys, customdata, markerColor, start, hit + 1
          );
          appendSlice(
            futureX, futureY, futureCustom, futureColor,
            xs, ys, customdata, markerColor, hit, end
          );
          entryX.push(Number(xs[hit]));
          entryY.push(Number(ys[hit]));
          entryCustom.push(customRow(customdata, hit) || ["", "", "", "", "", "", sid]);
          displayedPoints += end - start;
        }
        start = Math.max(end, start + 1);
      }

      if (!futureX.length) return;
      var axes = {};
      if (base.xaxis) axes.xaxis = base.xaxis;
      if (base.yaxis) axes.yaxis = base.yaxis;

      output.push(Object.assign({
        type: "scattergl",
        x: pastX,
        y: pastY,
        customdata: pastCustom,
        mode: "lines",
        name: "Before first entry",
        legendgroup: "loop-before",
        showlegend: !showedPastLegend,
        opacity: numberOr(style.before_opacity, 0.34),
        line: {color: style.before_color || "#7b8798", width: 1},
        hovertemplate:
          "<b>%{customdata[2]} @ %{customdata[3]}</b><br>" +
          "before ring entry<br>trial=%{customdata[0]} step=%{customdata[1]}<br>" +
          "x=%{x:.1f} z=%{y:.1f}<extra></extra>"
      }, axes));
      showedPastLegend = true;

      var future = Object.assign({
        type: "scattergl",
        x: futureX,
        y: futureY,
        customdata: futureCustom,
        mode: base.mode === "markers" ? "markers" : "lines",
        name: base.name || "After first entry",
        legendgroup: base.legendgroup || "loop-after",
        showlegend: Boolean(base.showlegend),
        opacity: numberOr(style.future_opacity, 0.9),
        hovertemplate:
          "<b>%{customdata[2]} @ %{customdata[3]}</b><br>" +
          "after ring entry<br>trial=%{customdata[0]} step=%{customdata[1]}<br>" +
          "config=%{customdata[4]}<br>x=%{x:.1f} z=%{y:.1f}<extra></extra>"
      }, axes);
      if (future.mode === "markers" && keepMarkerColor) {
        future.marker = Object.assign({}, clone(base.marker || {}), {
          color: futureColor,
          size: Math.max(3, numberOr(base.marker && base.marker.size, 3))
        });
      } else {
        future.mode = "lines";
        future.line = Object.assign({}, clone(base.line || {}), {
          width: Math.max(2, numberOr(base.line && base.line.width, 1.2))
        });
      }
      output.push(future);

      output.push(Object.assign({
        type: "scattergl",
        x: entryX,
        y: entryY,
        customdata: entryCustom,
        mode: "markers",
        name: "First ring entry",
        legendgroup: "loop-entry",
        showlegend: !showedEntryLegend,
        marker: {
          size: 6,
          color: style.entry_fill || "#fff7d1",
          line: {color: style.entry_line || "#6b4800", width: 1.5},
          symbol: "diamond"
        },
        hovertemplate:
          "<b>First ring entry</b><br>trial=%{customdata[0]} step=%{customdata[1]}" +
          "<br>x=%{x:.1f} z=%{y:.1f}<extra></extra>"
      }, axes));
      showedEntryLegend = true;
    });

    var layout = clone((source && source.layout) || {});
    layout.shapes = ringShapes(
      subplotPairs(sourceData), rings, active, style
    );
    layout.annotations = titleAnnotations(layout);
    layout.showlegend = true;
    layout.legend = Object.assign({}, layout.legend || {}, {
      orientation: "h",
      yanchor: "bottom",
      y: 1.02,
      xanchor: "left",
      x: 0
    });
    layout.dragmode = "pan";
    layout.uirevision = "loop-observer-view";
    delete layout.updatemenus;
    delete layout.sliders;

    var candidateCount = Object.keys(candidateIds).length;
    var matchCount = Object.keys(matchingIds).length;
    var status = matchCount.toLocaleString() + " of " +
      candidateCount.toLocaleString() + " displayed trials cross " +
      (matchMode === "all" ? "all " : "any of ") +
      rings.length.toLocaleString() + " curtain ring" +
      (rings.length === 1 ? "" : "s") +
      " · " + displayedPoints.toLocaleString() + " source points" +
      " · drag a ring to select and move it";
    return {data: output, layout: layout, status: status};
  }

  function graphDiv() {
    var container = document.getElementById("loop-observer-plot");
    return container && container.querySelector(".js-plotly-plot");
  }

  function emitRing(shape) {
    if (!shape) return;
    var match = /^loop-observer-ring:(.+)$/.exec(String(shape.name || ""));
    if (!match) return;
    var ringId = match[1];
    var ring = state.rings.filter(function (item) {
      return item.id === ringId;
    })[0];
    if (!ring) return;
    var x0 = numberOr(shape.x0, ring.x - ring.radius);
    var x1 = numberOr(shape.x1, ring.x + ring.radius);
    var y0 = numberOr(shape.y0, ring.z - ring.radius);
    var y1 = numberOr(shape.y1, ring.z + ring.radius);
    var cx = (x0 + x1) / 2;
    var cz = (y0 + y1) / 2;
    var radius = Math.max(0.001, (Math.abs(x1 - x0) + Math.abs(y1 - y0)) / 4);
    if (Math.abs(radius - ring.radius) <= Math.max(1e-6, ring.radius * 5e-4)) {
      radius = ring.radius;
    }
    var clean = function (value) {
      return Number(Number(value).toPrecision(10));
    };
    if (window.dash_clientside && window.dash_clientside.set_props) {
      state.rings = state.rings.map(function (item) {
        if (item.id !== ringId) return item;
        return Object.assign({}, item, {
          x: clean(cx), z: clean(cz), radius: clean(radius)
        });
      });
      state.active = ringId;
      window.dash_clientside.set_props(
        "loop-rings-store", {data: clone(state.rings)}
      );
      window.dash_clientside.set_props(
        "loop-active-ring", {value: ringId}
      );
      window.dash_clientside.set_props("loop-x", {value: clean(cx)});
      window.dash_clientside.set_props("loop-z", {value: clean(cz)});
      window.dash_clientside.set_props("loop-radius", {value: clean(radius)});
    }
  }

  function attachDrag(gd) {
    if (!gd || !gd.on) return;
    function changedShape(eventData) {
      if (!eventData || gd.__loopObserverPainting) return null;
      var shapeIndex = null;
      Object.keys(eventData).some(function (key) {
        var match = /^shapes\[(\d+)\]\./.exec(key);
        if (!match) return false;
        shapeIndex = Number(match[1]);
        return true;
      });
      if (shapeIndex === null) return null;
      var shape = ((gd.layout && gd.layout.shapes) || [])[shapeIndex];
      return shape ? {index: shapeIndex, shape: shape} : null;
    }

    if (gd.__loopObserverRelayout && gd.removeListener) {
      gd.removeListener("plotly_relayout", gd.__loopObserverRelayout);
    }
    gd.__loopObserverRelayout = function (eventData) {
      if (gd.__loopObserverConstraining) return;
      var changed = changedShape(eventData);
      if (changed) emitRing(changed.shape);
    };
    gd.on("plotly_relayout", gd.__loopObserverRelayout);

    if (gd.__loopObserverRelayouting && gd.removeListener) {
      gd.removeListener("plotly_relayouting", gd.__loopObserverRelayouting);
    }
    gd.__loopObserverRelayouting = function (eventData) {
      var changed = changedShape(eventData);
      if (!changed || gd.__loopObserverConstraining || !window.Plotly) return;
      var shape = changed.shape;
      var x0 = numberOr(shape.x0, 0);
      var x1 = numberOr(shape.x1, 0);
      var y0 = numberOr(shape.y0, 0);
      var y1 = numberOr(shape.y1, 0);
      var width = Math.abs(x1 - x0);
      var height = Math.abs(y1 - y0);
      if (Math.abs(width - height) <= Math.max(1e-8, Math.max(width, height) * 1e-4)) {
        return;
      }
      if (gd.__loopObserverFrame) {
        window.cancelAnimationFrame(gd.__loopObserverFrame);
      }
      gd.__loopObserverFrame = window.requestAnimationFrame(function () {
        var cx = (x0 + x1) / 2;
        var cz = (y0 + y1) / 2;
        var radius = Math.max(0.001, (width + height) / 4);
        var prefix = "shapes[" + changed.index + "].";
        var update = {};
        update[prefix + "x0"] = cx - radius;
        update[prefix + "x1"] = cx + radius;
        update[prefix + "y0"] = cz - radius;
        update[prefix + "y1"] = cz + radius;
        gd.__loopObserverConstraining = true;
        window.Plotly.relayout(gd, update).then(function () {
          gd.__loopObserverConstraining = false;
        }).catch(function () {
          gd.__loopObserverConstraining = false;
        });
        gd.__loopObserverFrame = null;
      });
      if (gd.__loopObserverCommitTimer) {
        window.clearTimeout(gd.__loopObserverCommitTimer);
      }
      gd.__loopObserverCommitTimer = window.setTimeout(function () {
        if (gd.__loopObserverConstraining) return;
        var latest = ((gd.layout && gd.layout.shapes) || [])[changed.index];
        if (latest) emitRing(latest);
      }, 160);
    };
    gd.on("plotly_relayouting", gd.__loopObserverRelayouting);
  }

  function paint(built, attempt) {
    attempt = attempt || 0;
    var gd = graphDiv();
    var wrap = document.getElementById("loop-observer-wrap");
    if (!gd || !wrap || wrap.style.display === "none" || !window.Plotly) {
      if (attempt < 12) {
        window.setTimeout(function () { paint(built, attempt + 1); }, 40);
      }
      return;
    }
    gd.__loopObserverPainting = true;
    var config = {
      scrollZoom: true,
      displayModeBar: true,
      displaylogo: false,
      toImageButtonOptions: {format: "png", scale: 3},
      edits: {shapePosition: true}
    };
    var method = gd.__loopObserverPainted ?
      window.Plotly.react : window.Plotly.newPlot;
    method(gd, built.data, built.layout, config)
      .then(function () {
        gd.__loopObserverPainting = false;
        gd.__loopObserverPainted = true;
        attachDrag(gd);
        if (window.dash_clientside.clean_layout) {
          window.dash_clientside.clean_layout.refresh();
        }
      })
      .catch(function () {
        gd.__loopObserverPainting = false;
      });
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    loop_observer: {
      render: function (source, enabled, rings, active, matchMode, visualStyle) {
        state.source = source || state.source;
        state.enabled = Boolean(enabled && enabled.indexOf("on") >= 0);
        state.rings = normaliseRings(rings);
        state.active = String(active || state.rings[0].id);
        state.matchMode = matchMode === "all" ? "all" : "any";
        state.style = visualStyle || {};
        if (!state.enabled) return "Loop observer off.";
        if (!state.source || !state.source.data || !state.source.data.length) {
          return "Waiting for rendered trajectories.";
        }
        var built = observerFigure(
          state.source, state.rings, state.active,
          state.matchMode, state.style
        );
        window.setTimeout(function () { paint(built, 0); }, 30);
        return built.status;
      }
    }
  });
  window.TrajectoryLoopObserver = Object.assign(
    {}, window.TrajectoryLoopObserver, {build: observerFigure}
  );
}());
