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
      send(folder, files);
    });

    function hi(on) {
      zone.style.background = on ? '#e6ecff' : '#f4f6fb';
      zone.style.borderColor = on ? '#0d6efd' : '#aac';
    }
    ['dragenter', 'dragover'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); e.stopPropagation(); hi(true); });
    });
    ['dragleave', 'dragend'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); e.stopPropagation(); hi(false); });
    });

    zone.addEventListener('drop', function (e) {
      e.preventDefault(); e.stopPropagation(); hi(false);
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
    });
  }

  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();
