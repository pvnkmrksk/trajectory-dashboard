// Drag-to-reorder the active panel axis (config, scene, VR, fly or folder).
// Dropping updates one compact store; the server then rebuilds in that order.
(function () {
  function values(list) {
    return Array.prototype.map.call(
      list.querySelectorAll('li[data-order-value]'), function (li) {
      return li.getAttribute('data-order-value');
    });
  }

  function publish(list) {
    if (!window.dash_clientside || !window.dash_clientside.set_props) return;
    var first = list.querySelector('li[data-order-group]');
    if (!first) return;
    window.dash_clientside.set_props('panel-order-store', {
      data: {
        group_by: first.getAttribute('data-order-group'),
        order: values(list),
        ts: Date.now()
      }
    });
  }

  function after(list, y) {
    var els = Array.prototype.slice.call(
      list.querySelectorAll('li[data-order-value]:not(.dragging)')
    );
    var best = { offset: Number.NEGATIVE_INFINITY, el: null };
    els.forEach(function (el) {
      var box = el.getBoundingClientRect();
      var offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > best.offset) best = { offset: offset, el: el };
    });
    return best.el;
  }

  function bind() {
    var list = document.getElementById('panel-order-list');
    if (!list) { setTimeout(bind, 300); return; }
    if (list.__orderBound) return;
    list.__orderBound = true;

    list.addEventListener('dragstart', function (e) {
      var li = e.target && e.target.closest &&
        e.target.closest('li[data-order-value]');
      if (!li) return;
      li.classList.add('dragging');
      li.style.opacity = '0.45';
      if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
    });

    list.addEventListener('dragend', function (e) {
      var li = e.target && e.target.closest &&
        e.target.closest('li[data-order-value]');
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
