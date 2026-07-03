// The heatmap is re-initialised via Plotly.newPlot (see the clientside callback),
// which detaches Dash's own relayout listener. Re-attach a light handler so that
// zooming/panning the heatmap records the viewbox into the shared viewport-store
// (used to keep the trajectory in sync and to persist the zoom in the URL).
window.__attachHeatSync = function (hg) {
  if (!hg || hg.__heatSync) return;
  hg.__heatSync = true;
  hg.on('plotly_relayout', function (ed) {
    if (!ed) return;
    var acc = {};
    Object.keys(ed).forEach(function (k) {
      if (k.indexOf('autorange') >= 0) { acc.reset = true; return; }
      var m = k.match(/^(x|y)axis\d*\.range\[(0|1)\]$/);
      if (m) {
        var ax = m[1] + 'axis';
        (acc[ax] = acc[ax] || [null, null])[+m[2]] = ed[k];
      }
    });
    var out = {};
    if (acc.reset) out.reset = true;
    if (acc.xaxis && acc.xaxis.indexOf(null) < 0) out.xaxis = acc.xaxis;
    if (acc.yaxis && acc.yaxis.indexOf(null) < 0) out.yaxis = acc.yaxis;
    if ((out.xaxis || out.yaxis || out.reset) &&
        window.dash_clientside && window.dash_clientside.set_props) {
      window.dash_clientside.set_props('viewport-store', { data: out });
    }
  });
};
