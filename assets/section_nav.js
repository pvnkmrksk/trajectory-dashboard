(function () {
  var LABEL_TO_VIEW = {
    trajectories: 'traj',
    heatmap: 'heat',
    targets: 'roi',
    polar: 'polar',
    diagnostics: 'diag'
  };

  window.__scrollTrajectorySection = function (view, behavior) {
    var scroller = document.getElementById('main-scroll');
    var target = document.getElementById('view-' + view);
    if (!scroller || !target) return false;
    window.requestAnimationFrame(function () {
      var tabs = scroller.querySelector('.view-tabs-wrap');
      var offset = (tabs && tabs.offsetHeight || 0) + 4;
      scroller.scrollTo({
        top: Math.max(0, target.offsetTop - offset),
        behavior: behavior || 'smooth'
      });
    });
    return true;
  };

  // Dash does not emit a value change when an already-selected tab is clicked.
  // Listen to the actual click so a second Trajectories/Heatmap/etc. click is a
  // reliable "take me back to this section" action.
  document.addEventListener('click', function (event) {
    var node = event.target && event.target.closest
      ? event.target.closest('#view-mode [role="tab"], #view-mode .tab')
      : null;
    if (!node) return;
    var label = String(node.textContent || '').trim().toLowerCase();
    var view = LABEL_TO_VIEW[label];
    if (view) window.__scrollTrajectorySection(view, 'smooth');
  }, true);
})();
