(function () {
  "use strict";

  const BUSY = /^(Started|Rendering|Applying|Queued|Updating|Building|Loading)/i;
  const ERROR = /^(No |Choose|Could not|Failed|Error)/i;

  function text(id) {
    const node = document.getElementById(id);
    return node ? String(node.textContent || "").trim() : "";
  }

  function reconcileStatus() {
    const dock = document.getElementById("status-dock");
    const messageNode = document.getElementById("status-message");
    const phaseNode = document.getElementById("status-phase");
    if (!dock || !messageNode || !phaseNode) return;

    const plot = text("plot-status");
    const load = text("load-status");
    const summary = text("data-summary");
    const busyMessage = [load, plot].find((value) => BUSY.test(value));
    const message = busyMessage ||
      ((/^Ready/i.test(plot) && summary) ? plot :
        (load || plot || summary || "Choose a data source to begin."));
    const isBusy = Boolean(busyMessage);
    const isError = !isBusy && ERROR.test(message);

    if (messageNode.textContent !== message) messageNode.textContent = message;
    phaseNode.textContent = isBusy ? "Working" : (isError ? "Error" : "Ready");
    dock.classList.toggle("is-working", isBusy);
    dock.classList.toggle("is-error", isError);
  }

  function start() {
    reconcileStatus();
    window.setInterval(reconcileStatus, 500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
