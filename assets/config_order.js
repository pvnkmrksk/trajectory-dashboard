// Drag-to-reorder the config plotting order list. The list is generated from
// all configs in the loaded dataset; dropping updates config-order-store.
(function () {
  function values(list) {
    return Array.prototype.map.call(list.querySelectorAll('li[data-cfg]'), function (li) {
      return li.getAttribute('data-cfg');
    });
  }

  function publish(list) {
    if (!window.dash_clientside || !window.dash_clientside.set_props) return;
    window.dash_clientside.set_props('config-order-store', {
      data: { order: values(list), ts: Date.now() }
    });
  }

  function after(list, y) {
    var els = Array.prototype.filter.call(list.querySelectorAll('li[data-cfg]:not(.dragging)'), function (el) {
      return true;
    });
    var best = { offset: Number.NEGATIVE_INFINITY, el: null };
    els.forEach(function (el) {
      var box = el.getBoundingClientRect();
      var offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > best.offset) best = { offset: offset, el: el };
    });
    return best.el;
  }

  function bind() {
    var list = document.getElementById('config-order-list');
    if (!list) { setTimeout(bind, 300); return; }
    if (list.__orderBound) return;
    list.__orderBound = true;

    list.addEventListener('dragstart', function (e) {
      var li = e.target && e.target.closest && e.target.closest('li[data-cfg]');
      if (!li) return;
      li.classList.add('dragging');
      li.style.opacity = '0.45';
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
    });

    list.addEventListener('dragend', function (e) {
      var li = e.target && e.target.closest && e.target.closest('li[data-cfg]');
      if (!li) return;
      li.classList.remove('dragging');
      li.style.opacity = '1';
      publish(list);
    });

    list.addEventListener('dragover', function (e) {
      e.preventDefault();
      var dragging = list.querySelector('.dragging');
      if (!dragging) return;
      var next = after(list, e.clientY);
      if (next == null) list.appendChild(dragging);
      else list.insertBefore(dragging, next);
    });
  }

  if (document.readyState !== 'loading') bind();
  else document.addEventListener('DOMContentLoaded', bind);
})();
