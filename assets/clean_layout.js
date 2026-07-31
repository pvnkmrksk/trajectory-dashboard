/*
 * Publication presentation mode.
 *
 * Clean layout is deliberately presentation-only. It never calls
 * Plotly.relayout/restyle: pan, zoom, shape editing and viewport
 * synchronisation therefore behave exactly as they do in the full layout.
 * Scale bars are passive DOM overlays computed from the mounted axes.
 */
(function () {
  "use strict";

  var SPATIAL_IDS = [
    "trajectory-plot", "loop-observer-plot", "heatmap-plot",
    "transition-plot", "flow-plot"
  ];
  var POLAR_IDS = ["polar-plot", "initial-heading-plot"];
  var CARTESIAN_IDS = [
    "roi-plot", "custom-region-diagnostics-plot", "trial-metrics-plot",
    "vel-histogram", "disp-histogram", "raw-trace-plot"
  ];
  var ALL_IDS = SPATIAL_IDS.concat(POLAR_IDS, CARTESIAN_IDS);
  var enabled = false;
  var unitScale = 1;
  var unitLabel = "cm";
  var refreshTimer = null;

  function niceFloor(value) {
    if (!(value > 0)) return 1;
    var power = Math.pow(10, Math.floor(Math.log10(value)));
    var scaled = value / power;
    var nice = scaled >= 5 ? 5 : (scaled >= 2 ? 2 : 1);
    return nice * power;
  }

  function formatNumber(value) {
    if (!(value > 0)) return "";
    if (value >= 1000) return value.toLocaleString(undefined, {
      maximumFractionDigits: 0
    });
    if (value >= 10) return value.toLocaleString(undefined, {
      maximumFractionDigits: 1
    });
    return value.toLocaleString(undefined, {maximumSignificantDigits: 3});
  }

  function clearScaleBars(container) {
    if (!container) return;
    container.querySelectorAll(".td-clean-scale-overlay").forEach(
      function (node) { node.remove(); }
    );
  }

  function graphDiv(container) {
    return container && container.querySelector(".js-plotly-plot");
  }

  function axisKey(prefix, index) {
    return prefix + "axis" + (index === 1 ? "" : String(index));
  }

  function scaleBars(id) {
    var container = document.getElementById(id);
    clearScaleBars(container);
    if (!enabled || !container) return;
    var gd = graphDiv(container);
    var full = gd && gd._fullLayout;
    if (!full || !full._size) return;
    var meta = (gd.layout && gd.layout.meta) || {};
    var panelValues = meta.panel_order_values || [];
    var count = Math.max(
      1,
      Number(meta.spatial_axis_count || 0),
      Number(panelValues.length || 0)
    );
    if (id === "loop-observer-plot") count = 1;
    var size = full._size;
    container.style.position = "relative";
    for (var index = 1; index <= count; index += 1) {
      var xaxis = full[axisKey("x", index)];
      var yaxis = full[axisKey("y", index)];
      if (!xaxis || !yaxis || !Array.isArray(xaxis.range)) continue;
      var lo = Number(xaxis.range[0]);
      var hi = Number(xaxis.range[1]);
      var span = Math.abs(hi - lo);
      if (!(span > 0)) continue;
      var physicalSpan = span * unitScale;
      var physicalLength = niceFloor(physicalSpan * 0.20);
      var fraction = physicalLength / physicalSpan;
      var xdomain = xaxis.domain || [0, 1];
      var ydomain = yaxis.domain || [0, 1];
      var panelWidth = size.w * Math.abs(xdomain[1] - xdomain[0]);
      var panelHeight = size.h * Math.abs(ydomain[1] - ydomain[0]);
      var barWidth = Math.max(26, Math.min(panelWidth * 0.28,
                                          panelWidth * fraction));
      var right = size.l + size.w * xdomain[1] - 12;
      var bottom = size.b + size.h * ydomain[0] + 12;
      var overlay = document.createElement("div");
      overlay.className = "td-clean-scale-overlay";
      overlay.style.left = Math.round(right - barWidth) + "px";
      overlay.style.top = Math.round(
        full.height - bottom - Math.min(28, panelHeight * 0.08)
      ) + "px";
      overlay.style.width = Math.round(barWidth) + "px";
      overlay.innerHTML =
        '<i></i><span>' + formatNumber(physicalLength) + " " +
        String(unitLabel || "cm").replace(/[<>&]/g, "") + "</span>";
      container.appendChild(overlay);
    }
  }

  function scheduleScaleBars() {
    if (refreshTimer) window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(function () {
      SPATIAL_IDS.forEach(scaleBars);
    }, 40);
  }

  function bindAfterPlot(id) {
    var container = document.getElementById(id);
    var gd = graphDiv(container);
    if (!gd || !gd.on || gd.__tdCleanAfterPlot) return;
    gd.__tdCleanAfterPlot = true;
    gd.on("plotly_afterplot", scheduleScaleBars);
  }

  function toggleGraph(id) {
    var container = document.getElementById(id);
    if (!container || !container.classList) return;
    container.classList.toggle("td-clean-mode", enabled);
    container.classList.toggle(
      "td-clean-spatial", enabled && SPATIAL_IDS.indexOf(id) >= 0
    );
    container.classList.toggle(
      "td-clean-polar", enabled && POLAR_IDS.indexOf(id) >= 0
    );
    container.classList.toggle(
      "td-clean-cartesian", enabled && CARTESIAN_IDS.indexOf(id) >= 0
    );
  }

  function applyClasses() {
    if (document.body && document.body.classList) {
      document.body.classList.toggle("td-clean-layout", enabled);
    }
    ALL_IDS.forEach(toggleGraph);
    SPATIAL_IDS.forEach(bindAfterPlot);
    scheduleScaleBars();
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    clean_layout: {
      render: function (on, scale, label) {
        enabled = Boolean(on);
        var numericScale = Number(scale);
        unitScale = Number.isFinite(numericScale) && numericScale > 0 ?
          numericScale : 1;
        unitLabel = String(label || "cm");
        applyClasses();
        return enabled ?
          "Switch to the full interactive axes, grids and legends." :
          "Hide spatial axes and Cartesian grids without changing plot data.";
      },
      // Structural plot callbacks still call refresh for backwards
      // compatibility. Re-applying classes is synchronous and emits no Plotly
      // event, so it cannot race a viewport gesture.
      refresh: function () {
        applyClasses();
      }
    }
  });
}());
