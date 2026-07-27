(function () {
  "use strict";

  var graphIds = ["trajectory-plot", "polar-plot"];
  var syncing = false;

  function graph(id) {
    var host = document.getElementById(id);
    if (!host) return null;
    return host.classList.contains("js-plotly-plot")
      ? host
      : host.querySelector(".js-plotly-plot");
  }

  function isVisible(trace) {
    return trace && trace.visible !== false && trace.visible !== "legendonly";
  }

  function finiteCount(values) {
    // Plotly 6 keeps scattergl coordinates in typed arrays.  Restricting this
    // to plain Arrays made every visible WebGL trajectory report zero points.
    if (!values || typeof values.length !== "number") return 0;
    var count = 0;
    for (var i = 0; i < values.length; i += 1) {
      var value = values[i];
      if (value !== null && value !== undefined && Number.isFinite(Number(value))) {
        count += 1;
      }
    }
    return count;
  }

  function updateCount() {
    var trajectory = graph("trajectory-plot");
    var polar = graph("polar-plot");
    var trajectoryTraces = trajectory && Array.isArray(trajectory._fullData)
      ? trajectory._fullData : (trajectory && trajectory.data);
    var polarTraces = polar && Array.isArray(polar._fullData)
      ? polar._fullData : (polar && polar.data);
    var pointCount = 0;
    var trialCount = 0;
    if (Array.isArray(trajectoryTraces)) {
      trajectoryTraces.forEach(function (trace) {
        if (isVisible(trace)) pointCount += finiteCount(trace.x);
      });
    }
    if (Array.isArray(polarTraces)) {
      polarTraces.forEach(function (trace) {
        if (isVisible(trace) && trace.legendgroup && Array.isArray(trace.r)) {
          trialCount += Math.floor(finiteCount(trace.r) / 2);
        }
      });
    }
    var label = document.getElementById("visible-layer-count");
    if (label) {
      var text =
        "Visible layers: " + pointCount.toLocaleString() +
        " trajectory pts · " + trialCount.toLocaleString() + " polar trials";
      if (label.textContent !== text) label.textContent = text;
    }
  }

  function syncLegend(source, event) {
    if (syncing || !source || !source.data || !event) return;
    var trace = source.data[event.curveNumber];
    var group = trace && trace.legendgroup;
    if (!group) return;
    var nextVisibility = isVisible(trace) ? "legendonly" : true;
    syncing = true;
    graphIds.forEach(function (id) {
      var target = graph(id);
      if (!target || target === source || !Array.isArray(target.data)) return;
      var indices = [];
      target.data.forEach(function (candidate, index) {
        if (candidate.legendgroup === group) indices.push(index);
      });
      if (indices.length && window.Plotly) {
        window.Plotly.restyle(target, { visible: nextVisibility }, indices);
      }
    });
    window.setTimeout(function () {
      syncing = false;
      updateCount();
    }, 40);
  }

  function bind() {
    graphIds.forEach(function (id) {
      var gd = graph(id);
      if (!gd || gd.__sharedLegendBound || typeof gd.on !== "function") return;
      gd.__sharedLegendBound = true;
      gd.on("plotly_legendclick", function (event) {
        syncLegend(gd, event);
        window.setTimeout(updateCount, 40);
      });
      gd.on("plotly_legenddoubleclick", function () {
        window.setTimeout(updateCount, 40);
      });
      gd.on("plotly_afterplot", updateCount);
      gd.on("plotly_restyle", updateCount);
    });
    updateCount();
  }

  new MutationObserver(bind).observe(document.documentElement, {
    childList: true,
    subtree: true
  });
  window.setInterval(bind, 1200);
  document.addEventListener("DOMContentLoaded", bind);
})();
