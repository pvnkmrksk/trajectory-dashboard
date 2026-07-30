/*
 * Browser-local target overlay visibility.
 *
 * Target geometry and diagnostics stay mounted in the complete figures.
 * Turning the checkbox off only hides those mounted layers and the diagnostics
 * card; it never asks the server to filter, aggregate or rebuild plots.
 */
(function () {
  "use strict";

  var GRAPH_IDS = [
    "trajectory-plot", "loop-observer-plot", "heatmap-plot",
    "flow-plot", "polar-plot"
  ];

  function graphDiv(id) {
    var container = document.getElementById(id);
    return container && container.querySelector(".js-plotly-plot");
  }

  function applyGraph(id, visible) {
    var gd = graphDiv(id);
    if (!gd || !window.Plotly || !gd.layout) return;
    var traceIndices = [];
    (gd.data || []).forEach(function (trace, index) {
      if (trace && trace.meta && trace.meta.td_target_overlay) {
        traceIndices.push(index);
      }
    });
    if (traceIndices.length) {
      window.Plotly.restyle(
        gd, {visible: visible ? true : false}, traceIndices
      );
    }
    var update = {};
    (gd.layout.shapes || []).forEach(function (shape, index) {
      if (String((shape && shape.name) || "").indexOf(
          "td-target-overlay") === 0) {
        update["shapes[" + index + "].visible"] = visible;
      }
    });
    (gd.layout.annotations || []).forEach(function (annotation, index) {
      if (String((annotation && annotation.name) || "").indexOf(
          "td-target-overlay") === 0) {
        update["annotations[" + index + "].visible"] = visible;
      }
    });
    if (Object.keys(update).length) window.Plotly.relayout(gd, update);
  }

  function render(value) {
    var visible = Array.isArray(value) && value.indexOf("on") >= 0;
    window.setTimeout(function () {
      GRAPH_IDS.forEach(function (id) { applyGraph(id, visible); });
      var section = document.getElementById("view-roi");
      if (section) section.style.display = visible ? "" : "none";
    }, 30);
    return visible ? "Targets shown locally." : "Targets hidden locally.";
  }

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    target_visibility: {render: render}
  });
}());
