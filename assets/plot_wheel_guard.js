// Keep Plotly wheel zoom from also scrolling the page/panel. This only captures
// the central plot drag plane; margins/axes still scroll the dashboard normally.
(function () {
  function overPlotPlane(target) {
    for (var el = target; el && el !== document; el = el.parentElement) {
      if (el.classList && el.classList.contains('nsewdrag')) return true;
      if (el.classList && el.classList.contains('modebar')) return false;
    }
    return false;
  }

  document.addEventListener('wheel', function (e) {
    if (!e.target || !overPlotPlane(e.target)) return;
    e.preventDefault();
  }, { capture: true, passive: false });
})();
