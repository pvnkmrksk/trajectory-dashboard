/*
 * Apply presentation-only visual-style diffs to already-mounted Plotly graphs.
 *
 * Renaming a scene/config/VR/fly/folder or changing the heatmap colour scale
 * does not alter data arrays, bins or statistics. Patching those leaf values in
 * the browser avoids an unnecessary full dashboard render.
 */
(function () {
  "use strict";

  var graphIds = [
    "trajectory-plot",
    "loop-observer-plot",
    "heatmap-plot",
    "transition-plot",
    "flow-plot",
    "polar-plot",
    "heading-time-plot",
    "roi-plot",
    "custom-region-diagnostics-plot",
    "trial-metrics-plot",
    "initial-heading-plot"
  ];

  function graphDiv(id) {
    var container = document.getElementById(id);
    return container && container.querySelector(".js-plotly-plot");
  }

  function wrapTitle(value, width, maxLines) {
    width = width || 28;
    maxLines = maxLines || 2;
    var words = String(value || "").split(/\s+/).filter(Boolean);
    var lines = [];
    var current = "";
    words.some(function (word) {
      var next = current ? current + " " + word : word;
      if (next.length <= width) {
        current = next;
        return false;
      }
      if (current) lines.push(current);
      current = word;
      return lines.length >= maxLines;
    });
    if (current && lines.length < maxLines) lines.push(current);
    var output = lines.length ? lines.join("<br>") : String(value || "");
    if (lines.length === maxLines && words.join(" ") !== lines.join(" ")) {
      output += "...";
    }
    return output;
  }

  function replaceLabel(value, rename) {
    if (typeof value !== "string" || !rename) return value;
    var output = value;
    var wrappedOld = wrapTitle(rename.old);
    var wrappedNew = wrapTitle(rename.new);
    if (wrappedOld && output.indexOf(wrappedOld) >= 0) {
      output = output.split(wrappedOld).join(wrappedNew);
    }
    if (rename.old && output.indexOf(rename.old) >= 0) {
      output = output.split(rename.old).join(rename.new);
    }
    return output;
  }

  function patchGraph(gd, renames) {
    if (!gd || !window.Plotly || !gd.layout) return;
    var relayout = {};
    var annotations = (gd.layout.annotations || []).map(function (annotation) {
      var next = Object.assign({}, annotation);
      renames.forEach(function (rename) {
        next.text = replaceLabel(next.text, rename);
        if (String(next.hovertext || "") === String(rename.raw)) {
          next.hovertext = rename.raw;
        } else {
          next.hovertext = replaceLabel(next.hovertext, rename);
        }
      });
      return next;
    });
    if (annotations.length) relayout.annotations = annotations;

    Object.keys(gd.layout).forEach(function (key) {
      if (!/^[xy]axis\d*$/.test(key)) return;
      var axis = gd.layout[key] || {};
      if (!Array.isArray(axis.ticktext)) return;
      var nextTicks = axis.ticktext.map(function (tick) {
        var output = tick;
        renames.forEach(function (rename) {
          output = replaceLabel(output, rename);
        });
        return output;
      });
      relayout[key + ".ticktext"] = nextTicks;
    });

    if (Object.keys(relayout).length) {
      try { window.Plotly.relayout(gd, relayout); } catch (error) {}
    }
    (gd.data || []).forEach(function (trace, index) {
      var nextName = trace && trace.name;
      renames.forEach(function (rename) {
        nextName = replaceLabel(nextName, rename);
      });
      if (nextName !== (trace && trace.name)) {
        try { window.Plotly.restyle(gd, {name: nextName}, [index]); }
        catch (error) {}
      }
    });
  }

  function patchHeatmapColorscale(colorscale) {
    if (!colorscale || !window.Plotly) return;
    var gd = graphDiv("heatmap-plot");
    if (!gd) return;
    (gd.data || []).forEach(function (trace, index) {
      if (String((trace && trace.type) || "").toLowerCase() !== "heatmap") {
        return;
      }
      try {
        window.Plotly.restyle(gd, {colorscale: colorscale}, [index]);
      } catch (error) {}
    });
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    style_patch: {
      render: function (diff) {
        diff = diff || {};
        var renames = Array.isArray(diff.renames) ? diff.renames : [];
        graphIds.forEach(function (id) {
          patchGraph(graphDiv(id), renames);
        });
        if ((diff.changed_paths || []).indexOf("heatmap.colorscale") >= 0) {
          patchHeatmapColorscale(diff.heatmap_colorscale);
        }
        if (!renames.length && !(diff.changed_paths || []).length) {
          return "No mounted style changes.";
        }
        return "Patched " + (diff.changed_paths || []).length +
          " mounted style value(s).";
      }
    }
  });
}());
