(function () {
  var pendingViewport = null;
  var viewportTimer = null;
  var lastViewportSig = '';
  var DEBOUNCE_MS = 450;

  function normaliseRelayout(source, ed) {
    if (!ed) return;
    var acc = {};
    Object.keys(ed).forEach(function (k) {
      if (k.indexOf('autorange') >= 0 && ed[k] === true) {
        acc.reset = true;
        return;
      }
      var whole = k.match(/^(x|y)axis\d*\.range$/);
      if (whole && Array.isArray(ed[k]) && ed[k].length === 2) {
        acc[whole[1] + 'axis'] = [ed[k][0], ed[k][1]];
        return;
      }
      var m = k.match(/^(x|y)axis\d*\.range\[(0|1)\]$/);
      if (m) {
        var ax = m[1] + 'axis';
        (acc[ax] = acc[ax] || [null, null])[+m[2]] = ed[k];
      }
    });
    var out = { source: source };
    if (acc.reset) out.reset = true;
    if (acc.xaxis && acc.xaxis.indexOf(null) < 0) out.xaxis = acc.xaxis;
    if (acc.yaxis && acc.yaxis.indexOf(null) < 0) out.yaxis = acc.yaxis;
    return (out.xaxis || out.yaxis || out.reset) ? out : null;
  }

  function graphFor(source) {
    var id = source === 'traj' ? 'trajectory-plot' : 'heatmap-plot';
    var host = document.getElementById(id);
    return host && host.querySelector('.js-plotly-plot');
  }

  function syncPeer(source, out) {
    if (!out || !window.Plotly) return;
    var peer = graphFor(source === 'traj' ? 'heat' : 'traj');
    if (!peer) return;
    var update = {};
    if (out.reset) {
      update['xaxis.autorange'] = true;
      update['yaxis.autorange'] = true;
    } else {
      if (out.xaxis) update['xaxis.range'] = out.xaxis.slice();
      if (out.yaxis) update['yaxis.range'] = out.yaxis.slice();
    }
    if (!Object.keys(update).length) return;
    peer.__vpApplying = true;
    try {
      Promise.resolve(window.Plotly.relayout(peer, update)).finally(function () {
        window.setTimeout(function () { peer.__vpApplying = false; }, 40);
      });
    } catch (e) {
      peer.__vpApplying = false;
    }
  }

  function queueViewport(source, ed) {
    if (source === 'heat' && window.__hmSuppress) return;
    var out = normaliseRelayout(source, ed);
    if (!out || !window.dash_clientside || !window.dash_clientside.set_props) return;
    syncPeer(source, out);
    pendingViewport = out;
    clearTimeout(viewportTimer);
    viewportTimer = setTimeout(function () {
      if (!pendingViewport) return;
      var sig = JSON.stringify(pendingViewport);
      if (sig !== lastViewportSig) {
        lastViewportSig = sig;
        window.dash_clientside.set_props('viewport-store', { data: pendingViewport });
      }
      pendingViewport = null;
    }, DEBOUNCE_MS);
  }

  window.__attachViewportSync = function (gd, source, force) {
    if (!gd) return;
    if (!force && gd.__vpSyncSource === source && gd.__vpSyncHandler) return;
    if (gd.__vpSyncHandler && gd.removeListener) {
      try { gd.removeListener('plotly_relayout', gd.__vpSyncHandler); } catch (e) {}
    }
    gd.__vpSyncSource = source;
    gd.__vpSyncHandler = function (ed) {
      if (gd.__vpApplying) return;
      queueViewport(source, ed);
    };
    gd.on('plotly_relayout', gd.__vpSyncHandler);
  };

  // The heatmap is re-initialised via Plotly.newPlot, which detaches event
  // listeners. Re-attach the same debounced viewport listener after each newPlot.
  window.__attachHeatSync = function (hg, force) {
    if (!hg || (!force && hg.__heatSync)) return;
    hg.__heatSync = true;
    window.__attachViewportSync(hg, 'heat', !!force);
  };
})();
