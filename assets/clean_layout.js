/*
 * Publication presentation mode.
 *
 * Clean layout is deliberately CSS-only. In particular, it never calls
 * Plotly.relayout/restyle and never subscribes to plotly_relayout: pan, zoom,
 * shape editing and viewport synchronisation must behave exactly as they do in
 * the full layout. The graph container survives Plotly.react/newPlot, so the
 * presentation class also survives figure updates without a repaint callback.
 */
(function () {
  "use strict";

  var SPATIAL_IDS = [
    "trajectory-plot", "loop-observer-plot", "heatmap-plot", "flow-plot"
  ];
  var POLAR_IDS = ["polar-plot", "initial-heading-plot"];
  var CARTESIAN_IDS = [
    "roi-plot", "custom-region-diagnostics-plot", "trial-metrics-plot",
    "vel-histogram", "disp-histogram", "raw-trace-plot"
  ];
  var ALL_IDS = SPATIAL_IDS.concat(POLAR_IDS, CARTESIAN_IDS);
  var enabled = false;

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
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    clean_layout: {
      render: function (on) {
        enabled = Boolean(on);
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
