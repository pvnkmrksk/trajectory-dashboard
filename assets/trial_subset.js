/*
 * Browser-local whole-trial display sampling.
 *
 * The server sends one complete, already-decimated drawing. This module hides
 * points belonging to unselected `_seg_id` values with Plotly.restyle, so the
 * displayed fraction and "new subset" button never rebuild analytical panels.
 */
(function () {
  "use strict";

  var baseFigures = {};
  var filteredTraceCache = new WeakMap();
  var segmentInventoryCache = new WeakMap();
  var renderTimer = null;
  var renderVersion = 0;
  var applyQueue = Promise.resolve();

  function clone(value) {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value, function (_key, item) {
      return ArrayBuffer.isView(item) ? Array.from(item) : item;
    }));
  }

  function sequence(value) {
    if (Array.isArray(value)) return value;
    if (ArrayBuffer.isView(value)) return Array.from(value);
    // Plotly 6 keeps numeric 1-D arrays in Dash figure props as compact
    // `{dtype,bdata}` objects. Decode those browser-side instead of forcing
    // the server to expand every trajectory coordinate into JSON numbers.
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
        if (Constructor) {
          return Array.from(new Constructor(bytes.buffer));
        }
      } catch (_error) {
        return null;
      }
    }
    return null;
  }

  function hash(text) {
    var out = 2166136261;
    for (var index = 0; index < text.length; index += 1) {
      out ^= text.charCodeAt(index);
      out = Math.imul(out, 16777619);
    }
    return out >>> 0;
  }

  function segId(row) {
    if (!Array.isArray(row) || row.length < 7) return "";
    return String(row[6] || "");
  }

  function figureSignature(fig) {
    var meta = (fig && fig.layout && fig.layout.meta) || {};
    if (meta.trial_subset_signature) {
      return String(meta.trial_subset_signature);
    }
    var ids = {};
    var points = 0;
    ((fig && fig.data) || []).forEach(function (trace) {
      var customdata = sequence(trace && trace.customdata) || [];
      points += customdata.length;
      customdata.forEach(function (row) {
        var id = segId(row);
        if (id) ids[id] = true;
      });
    });
    return [
      ((fig && fig.data) || []).length,
      points,
      Object.keys(ids).sort().join("\u001f"),
      JSON.stringify(meta.panel_order_values || []),
      String(meta.stats_unit || "trial")
    ].join("\u001e");
  }

  function finiteCoordinateCount(fig) {
    var total = 0;
    ((fig && fig.data) || []).forEach(function (trace) {
      ["x", "y", "r", "theta"].forEach(function (key) {
        var values = sequence(trace && trace[key]);
        if (!values) return;
        values.forEach(function (value) {
          if (Number.isFinite(Number(value))) total += 1;
        });
      });
    });
    return total;
  }

  function sourceFigure(id, figure) {
    var signature = figureSignature(figure);
    var cached = baseFigures[id];
    if (cached && cached.signature === signature) return cached.figure;
    // A mounted Plotly restyle can flow its masked arrays back through the
    // Dash figure prop. Never promote that reduced figure to the new source
    // merely because it has the same structural signature. A real server
    // render supplies a new explicit signature in layout.meta.
    if (!cached || cached.signature !== signature) {
      var finite = finiteCoordinateCount(figure);
      cached = {
        signature: signature,
        finite: finite,
        figure: clone(figure || {data: [], layout: {}})
      };
      baseFigures[id] = cached;
    }
    return cached.figure;
  }

  function segmentInventory(fig) {
    var cached = segmentInventoryCache.get(fig);
    if (cached) return cached;
    var found = {};
    ((fig && fig.data) || []).forEach(function (trace) {
      (sequence(trace.customdata) || []).forEach(function (row) {
        var id = segId(row);
        if (id) found[id] = true;
      });
    });
    cached = {found: found, ids: Object.keys(found), rankedBySeed: {}};
    segmentInventoryCache.set(fig, cached);
    return cached;
  }

  function selectedSegments(fig, fraction, seed) {
    var inventory = segmentInventory(fig);
    var found = inventory.found;
    var ids = inventory.ids;
    var pct = Math.max(1, Math.min(100, Number(fraction || 100)));
    if (pct >= 100 || ids.length <= 1) {
      return {
        all: true, ids: found, count: ids.length, total: ids.length,
        key: "all"
      };
    }
    var seedKey = String(seed || 0);
    var ranked = inventory.rankedBySeed[seedKey];
    if (!ranked) {
      ranked = ids.slice().sort(function (left, right) {
        var difference = hash(seedKey + "|" + left) -
          hash(seedKey + "|" + right);
        return difference || left.localeCompare(right);
      });
      inventory.rankedBySeed[seedKey] = ranked;
    }
    var count = Math.max(1, Math.ceil(ids.length * pct / 100));
    var kept = {};
    ranked.slice(0, count).forEach(function (id) { kept[id] = true; });
    return {
      all: false, ids: kept, count: count, total: ids.length,
      key: seedKey + "|" + String(count) + "|" + String(ids.length)
    };
  }

  function filteredTrace(trace, selected) {
    var customdata = sequence(trace && trace.customdata);
    if (!trace || !customdata) return null;
    var keys = ["x", "y", "r", "theta"];
    var available = keys.filter(function (key) {
      return sequence(trace[key]) !== null;
    });
    if (!available.length) return null;
    var cached = filteredTraceCache.get(trace);
    if (cached && cached.key === selected.key) return cached.output;
    var output = {};
    var valuesByKey = {};
    available.forEach(function (key) {
      valuesByKey[key] = sequence(trace[key]);
      output[key] = new Array(valuesByKey[key].length).fill(null);
    });
    customdata.forEach(function (row, index) {
      var id = segId(row);
      var keep = id && selected.ids[id];
      if (keep) {
        available.forEach(function (key) {
          var values = valuesByKey[key];
          if (index < values.length) output[key][index] = values[index];
        });
      }
    });
    filteredTraceCache.set(trace, {key: selected.key, output: output});
    return output;
  }

  function baseSegmentOrder(trace) {
    var order = [];
    var previous = "";
    (sequence(trace && trace.customdata) || []).forEach(function (row) {
      var id = segId(row);
      if (id && id !== previous) order.push(id);
      previous = id;
    });
    return order;
  }

  function filteredFrameTrace(frameTrace, baseTrace, selected) {
    var output = clone(frameTrace || {});
    var order = baseSegmentOrder(baseTrace);
    if (!order.length) return output;
    var x = sequence(frameTrace.x) || [];
    var y = sequence(frameTrace.y) || [];
    var colors = (
      frameTrace.marker && sequence(frameTrace.marker.color)
    ) || null;
    var nextX = new Array(x.length).fill(null);
    var nextY = new Array(y.length).fill(null);
    var nextColors = colors ? Array.from(colors) : [];
    var segmentIndex = 0;
    for (var index = 0; index < x.length; index += 1) {
      var gap = x[index] === null || y[index] === null ||
        !Number.isFinite(Number(x[index])) || !Number.isFinite(Number(y[index]));
      var id = order[Math.min(segmentIndex, order.length - 1)];
      var keep = Boolean(selected.ids[id]);
      if (keep && !gap) {
        nextX[index] = x[index];
        nextY[index] = y[index];
      } else if (colors && !keep) nextColors[index] = null;
      if (gap) segmentIndex += 1;
    }
    output.x = nextX;
    output.y = nextY;
    if (colors) {
      output.marker = Object.assign({}, output.marker || {}, {
        color: nextColors
      });
    }
    return output;
  }

  function replaceFrames(gd, figure, selected) {
    if (!window.Plotly || !Array.isArray(figure && figure.frames)) return;
    var frames = figure.frames.map(function (frame) {
      var output = clone(frame);
      output.data = (frame.data || []).map(function (trace, index) {
        return filteredFrameTrace(trace, figure.data[index], selected);
      });
      return output;
    });
    var mounted = (
      gd._transitionData && Array.isArray(gd._transitionData._frames)
    ) ? gd._transitionData._frames.length : 0;
    var remove = [];
    for (var index = 0; index < mounted; index += 1) remove.push(index);
    var deleted = remove.length ?
      window.Plotly.deleteFrames(gd, remove) : null;
    Promise.resolve(deleted).then(function () {
      if (frames.length) window.Plotly.addFrames(gd, frames);
    });
  }

  function filterFigure(fig, fraction, seed) {
    var sourceBase = sourceFigure("trajectory-plot", fig);
    var selected = selectedSegments(sourceBase, fraction, seed);
    if (selected.all) return sourceBase;
    // The observer only reads this figure. Shallow-copy the traces whose
    // coordinate arrays are replaced instead of cloning the complete payload.
    var source = Object.assign({}, sourceBase, {
      data: (sourceBase.data || []).map(function (trace) {
        return Object.assign({}, trace);
      })
    });
    source.data = (source.data || []).map(function (trace, index) {
      var filtered = filteredTrace(sourceBase.data[index], selected);
      if (!filtered) return trace;
      Object.keys(filtered).forEach(function (key) {
        trace[key] = filtered[key];
      });
      return trace;
    });
    return source;
  }

  function graphDiv(id) {
    var container = document.getElementById(id);
    return container && container.querySelector(".js-plotly-plot");
  }

  function applyGraph(id, figure, selected) {
    var gd = graphDiv(id);
    if (!gd || !window.Plotly || !figure || !figure.data) return;
    var jobs = [];
    var coordinateGroups = {};
    figure.data.forEach(function (trace, index) {
      if (!trace || !sequence(trace.customdata)) return;
      var filtered = selected.all ? {
        x: sequence(trace.x), y: sequence(trace.y),
        r: sequence(trace.r), theta: sequence(trace.theta)
      } : filteredTrace(trace, selected);
      if (!filtered) return;
      var update = {};
      ["x", "y", "r", "theta"].forEach(function (key) {
        if (filtered[key]) update[key] = filtered[key];
      });
      var keys = Object.keys(update).sort();
      if (!keys.length) return;
      var signature = keys.join("|");
      var group = coordinateGroups[signature] || {
        keys: keys, indices: [], updates: {}
      };
      keys.forEach(function (key) {
        group.updates[key] = group.updates[key] || [];
        group.updates[key].push(update[key]);
      });
      group.indices.push(index);
      coordinateGroups[signature] = group;
    });
    Object.keys(coordinateGroups).forEach(function (signature) {
      var group = coordinateGroups[signature];
      jobs.push(window.Plotly.restyle(
        gd, group.updates, group.indices
      ));
    });
    if (id === "trajectory-plot") replaceFrames(gd, figure, selected);
    if (id === "polar-plot") {
      var bySubplot = {};
      var seen = {};
      figure.data.forEach(function (trace) {
        if (!(trace && trace.meta && trace.meta.td_trial_source)) return;
        var subplot = String(trace.subplot || "polar");
        (sequence(trace.customdata) || []).forEach(function (row) {
          var idValue = segId(row);
          var uniqueKey = subplot + "|" + idValue;
          if (!idValue || !selected.ids[idValue] || seen[uniqueKey]) return;
          seen[uniqueKey] = true;
          var strength = Number(row[7]);
          var theta = Number(row[8]) * Math.PI / 180;
          var animalMode = String(
            ((figure.layout || {}).meta || {}).stats_unit || "trial"
          ) === "animal";
          var weight = animalMode ? 1 : Number(row[10]);
          if (![strength, theta, weight].every(Number.isFinite) || weight <= 0) {
            return;
          }
          var item = bySubplot[subplot] || {x: 0, z: 0, weight: 0};
          item.x += weight * strength * Math.sin(theta);
          item.z += weight * strength * Math.cos(theta);
          item.weight += weight;
          bySubplot[subplot] = item;
        });
      });
      figure.data.forEach(function (trace, index) {
        if (!(trace && trace.meta && trace.meta.td_population)) return;
        var subplot = String(trace.subplot || "polar");
        var item = bySubplot[subplot] || {x: 0, z: 0, weight: 0};
        var x = item.weight ? item.x / item.weight : 0;
        var z = item.weight ? item.z / item.weight : 0;
        var strength = Math.sqrt(x * x + z * z);
        var theta = Math.atan2(x, z) * 180 / Math.PI;
        jobs.push(window.Plotly.restyle(
          gd, {r: [[0, strength]], theta: [[theta, theta]]}, [index]
        ));
      });
    }
    return Promise.all(jobs);
  }

  function render(trajectoryFigure, polarFigure, fraction, seed) {
    var trajectorySource = sourceFigure(
      "trajectory-plot", trajectoryFigure || {data: [], layout: {}}
    );
    var polarSource = sourceFigure(
      "polar-plot", polarFigure || {data: [], layout: {}}
    );
    var selected = selectedSegments(trajectorySource, fraction, seed);
    var polarAnimalMode = String(
      ((polarSource.layout || {}).meta || {}).stats_unit || "trial"
    ) === "animal";
    var polarSelected = polarAnimalMode ?
      selectedSegments(polarSource, fraction, seed) : selected;
    renderVersion += 1;
    var version = renderVersion;
    if (renderTimer) window.clearTimeout(renderTimer);
    renderTimer = window.setTimeout(function () {
      renderTimer = null;
      if (version !== renderVersion) return;
      applyQueue = applyQueue.catch(function () {}).then(function () {
        if (version !== renderVersion) return null;
        // Trial-mode polar and trajectory customdata share `_seg_id`. Animal
        // mode deliberately samples its independent animal vectors instead.
        return Promise.all([
          applyGraph("trajectory-plot", trajectorySource, selected),
          applyGraph("polar-plot", polarSource, polarSelected)
        ]);
      });
    }, 20);
    return {
      selected: selected.count,
      total: selected.total,
      fraction: Math.max(1, Math.min(100, Number(fraction || 100))),
      seed: Number(seed || 0),
      updated: Date.now()
    };
  }

  window.TrajectoryTrialSubset = {
    filterFigure: filterFigure,
    selectedSegments: selectedSegments
  };
  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    trial_subset: {render: render}
  });
}());
