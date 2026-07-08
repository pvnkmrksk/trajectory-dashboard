// Folder drag-and-drop / click-to-pick for the trajectory dashboard.
// Reads the dropped folder's CSV relative paths client-side and hands them to
// Dash via set_props('drop-data', ...). The server resolves them to a glob.
(function () {
  function send(folder, files) {
    if (window.dash_clientside && window.dash_clientside.set_props) {
      window.dash_clientside.set_props('drop-data', {
        data: { folder: folder, files: files.slice(0, 4000), n: files.length, ts: Date.now() }
      });
    }
  }

  function init() {
    var zone = document.getElementById('drop-zone');
    var plotTarget = document.getElementById('plot-drop-target');
    if (!zone) { setTimeout(init, 300); return; }
    if (zone.__bound) return;
    zone.__bound = true;

    // Hidden <input webkitdirectory> for click-to-pick.
    var input = document.createElement('input');
    input.type = 'file';
    input.webkitdirectory = true;
    input.multiple = true;
    input.style.display = 'none';
    document.body.appendChild(input);

    zone.addEventListener('click', function () { input.click(); });

    input.addEventListener('change', function () {
      var files = [], folder = '';
      for (var i = 0; i < input.files.length; i++) {
        var rp = input.files[i].webkitRelativePath || input.files[i].name;
        if (rp.toLowerCase().endsWith('.csv')) files.push(rp);
        if (!folder && rp.indexOf('/') >= 0) folder = rp.split('/')[0];
      }
      if (files.length && zone.__setBusy) zone.__setBusy();
      send(folder, files);
    });

    var label = document.getElementById('drop-label');
    var sub = document.getElementById('drop-sub');
    var defLabel = label ? label.textContent : 'Drop or choose a data folder';
    var defSub = sub ? sub.textContent : '';

    var overlay = document.createElement('div');
    overlay.id = 'drop-overlay';
    overlay.innerHTML = '<div>' +
      '<div style="font-size:22px;font-weight:700;margin-top:8px">Drop folder to load</div>' +
      '<div style="font-size:13px;opacity:.8;margin-top:4px">drop on the folder control or plotting workspace</div></div>';
    overlay.style.cssText = [
      'position:fixed', 'inset:0', 'z-index:9999', 'display:none',
      'align-items:center', 'justify-content:center', 'text-align:center',
      'background:rgba(13,110,253,.12)', 'backdrop-filter:blur(1px)',
      'border:5px dashed #0d6efd', 'color:#123', 'pointer-events:none'
    ].join(';');
    document.body.appendChild(overlay);
    var dragDepth = 0;

    function isFileDrag(e) {
      var types = e.dataTransfer && e.dataTransfer.types;
      if (!types) return false;
      return Array.prototype.indexOf.call(types, 'Files') >= 0;
    }

    function isDropSurface(target) {
      return !!(target && (
        zone.contains(target) ||
        (plotTarget && plotTarget.contains(target))
      ));
    }

    // React instantly to external file drags only. Internal drags, such as the
    // config order list, must pass through untouched.
    function hi(on) {
      overlay.style.display = on ? 'flex' : 'none';
      zone.style.transition = 'all .12s ease';
      zone.style.background = on ? '#e6ecff' : '#f4f6fb';
      zone.style.borderColor = on ? '#0d6efd' : '#aac';
      zone.style.minHeight = on ? '150px' : '92px';
      zone.style.transform = on ? 'scale(1.02)' : 'scale(1)';
      zone.style.boxShadow = on ? '0 4px 16px rgba(13,110,253,0.25)' : 'none';
      if (plotTarget) {
        plotTarget.style.outline = on ? '2px dashed #2563eb' : '';
        plotTarget.style.outlineOffset = on ? '-6px' : '';
      }
      if (label && !zone.__busy) label.textContent = on ? 'Drop to load' : defLabel;
    }
    function busy(msg) {
      zone.__busy = true;
      zone.style.background = '#fff8e6';
      zone.style.borderColor = '#f0ad4e';
      zone.style.minHeight = '92px';
      zone.style.transform = 'scale(1)';
      zone.style.boxShadow = 'none';
      if (label) label.textContent = msg || 'Processing...';
      if (sub) sub.textContent = 'Locating the folder on disk...';
    }
    zone.__setBusy = busy;
    // Clear the busy state once loading actually starts (progress bar shows).
    var track = document.getElementById('load-progress-track');
    if (track) {
      new MutationObserver(function () {
        if (getComputedStyle(track).display !== 'none') {
          zone.__busy = false;
          if (label) label.textContent = defLabel;
          if (sub) sub.textContent = defSub;
        }
      }).observe(track, { attributes: true, attributeFilter: ['style'] });
    }
    function dragOn(e) {
      if (!isFileDrag(e) || !isDropSurface(e.target)) return;
      e.preventDefault(); e.stopPropagation();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
      if (e.type === 'dragenter') dragDepth++;
      hi(true);
    }
    function dragOff(e) {
      if (!isFileDrag(e)) return;
      e.preventDefault(); e.stopPropagation();
      if (e.type === 'dragleave') dragDepth = Math.max(0, dragDepth - 1);
      if (e.type === 'dragend' || dragDepth === 0) hi(false);
    }
    [zone, plotTarget].filter(Boolean).forEach(function (target) {
      ['dragenter', 'dragover'].forEach(function (ev) {
        target.addEventListener(ev, dragOn, false);
      });
      ['dragleave', 'dragend'].forEach(function (ev) {
        target.addEventListener(ev, dragOff, false);
      });
    });

    function handleDrop(e) {
      if (!isFileDrag(e) || !isDropSurface(e.target)) return;
      dragDepth = 0;
      e.preventDefault(); e.stopPropagation(); hi(false); busy('Processing...');
      var items = e.dataTransfer.items;
      var files = [], folderName = '', pending = 0, done = false;

      function finish() { if (!done) { done = true; send(folderName, files); } }

      function walk(entry, path) {
        if (!entry) return;
        if (entry.isFile) {
          pending++;
          entry.file(function (f) {
            var rp = path + entry.name;
            if (rp.toLowerCase().endsWith('.csv')) files.push(rp);
            if (--pending === 0) finish();
          }, function () { if (--pending === 0) finish(); });
        } else if (entry.isDirectory) {
          if (!folderName) folderName = entry.name;
          var reader = entry.createReader();
          pending++;
          (function readBatch() {
            reader.readEntries(function (ents) {
              if (ents.length) {
                ents.forEach(function (en) { walk(en, path + entry.name + '/'); });
                readBatch();
              } else if (--pending === 0) { finish(); }
            }, function () { if (--pending === 0) finish(); });
          })();
        }
      }

      var roots = [];
      if (items) {
        for (var i = 0; i < items.length; i++) {
          var entry = items[i].webkitGetAsEntry && items[i].webkitGetAsEntry();
          if (entry) roots.push(entry);
        }
      }
      if (!roots.length) {
        var fl = e.dataTransfer.files || [];
        for (var j = 0; j < fl.length; j++) {
          var rp = fl[j].name;
          if (rp.toLowerCase().endsWith('.csv')) files.push(rp);
        }
        finish();
        return;
      }
      roots.forEach(function (r) { walk(r, ''); });
      setTimeout(function () { if (pending === 0) finish(); }, 80);
    }
    [zone, plotTarget].filter(Boolean).forEach(function (target) {
      target.addEventListener('drop', handleDrop, false);
    });
  }

  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();
