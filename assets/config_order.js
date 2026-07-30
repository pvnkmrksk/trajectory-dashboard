// Drag-to-reorder the active panel axis (config, scene, VR, fly or folder).
// Domains are swapped on the mounted Plotly graphs: traces, bins and statistics
// remain untouched. The compact store only preserves the order for later full
// analytical renders.
(function () {
  "use strict";

  var GRAPH_IDS = [
    "trajectory-plot", "loop-observer-plot", "heatmap-plot",
    "flow-plot", "polar-plot"
  ];

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function graphDiv(id) {
    var container = document.getElementById(id);
    return container && container.querySelector(".js-plotly-plot");
  }

  function axisName(prefix, index) {
    return prefix + "axis" + (index === 1 ? "" : String(index));
  }

  function polarName(index) {
    return "polar" + (index === 1 ? "" : String(index));
  }

  function panelTitles(gd, wanted) {
    var wantedSet = {};
    (wanted || []).forEach(function (value) {
      wantedSet[String(value)] = true;
    });
    return ((gd.layout && gd.layout.annotations) || []).filter(function (ann) {
      return ann && wantedSet[String(ann.hovertext)] === true;
    });
  }

  function buildCache(gd, id, wanted) {
    var titles = panelTitles(gd, wanted);
    if (!titles.length) return null;
    var raw = titles.map(function (ann) { return String(ann.hovertext); });
    var count = raw.length;
    var isPolar = Boolean(gd.layout && gd.layout.polar);
    var isFlow = id === "flow-plot";
    var mainCount = isFlow ?
      Math.max(count, Number((gd.layout.meta || {}).spatial_axis_count || 0)) :
      count;
    var slots = [];
    var panels = {};

    raw.forEach(function (name, index) {
      var slot = {title: {x: titles[index].x, y: titles[index].y}};
      var axisIndices;
      if (isPolar) {
        var polar = gd.layout[polarName(index + 1)] || {};
        slot.polar = clone(polar.domain || {});
        panels[name] = {polar: index + 1};
      } else {
        axisIndices = isFlow ?
          [
            index + 1,
            mainCount + index * 2 + 1,
            mainCount + index * 2 + 2
          ] :
          [index + 1];
        slot.axes = axisIndices.map(function (axisIndex) {
          return {
            x: clone((gd.layout[axisName("x", axisIndex)] || {}).domain),
            y: clone((gd.layout[axisName("y", axisIndex)] || {}).domain)
          };
        });
        panels[name] = {axes: axisIndices};
      }
      slots.push(slot);
    });
    return {
      dataRef: gd.data,
      raw: raw,
      slots: slots,
      panels: panels,
      isPolar: isPolar
    };
  }

  function applyToGraph(id, order) {
    var gd = graphDiv(id);
    if (!gd || !gd.layout || !window.Plotly) return;
    var cache = gd.__tdPanelOrder;
    if (!cache || cache.dataRef !== gd.data) {
      cache = buildCache(gd, id, order);
      gd.__tdPanelOrder = cache;
    }
    if (!cache) return;
    var desired = (order || []).map(String).filter(function (name) {
      return Boolean(cache.panels[name]);
    });
    cache.raw.forEach(function (name) {
      if (desired.indexOf(name) < 0) desired.push(name);
    });
    if (desired.length !== cache.slots.length) return;

    var update = {};
    desired.forEach(function (name, slotIndex) {
      var panel = cache.panels[name];
      var slot = cache.slots[slotIndex];
      if (cache.isPolar) {
        update[polarName(panel.polar) + ".domain"] = clone(slot.polar);
      } else {
        panel.axes.forEach(function (axisIndex, roleIndex) {
          var domains = slot.axes[roleIndex];
          if (!domains) return;
          update[axisName("x", axisIndex) + ".domain"] = clone(domains.x);
          update[axisName("y", axisIndex) + ".domain"] = clone(domains.y);
        });
      }
    });

    var annotations = clone(gd.layout.annotations || []);
    desired.forEach(function (name, slotIndex) {
      var destination = cache.slots[slotIndex].title;
      annotations.forEach(function (ann) {
        if (String(ann && ann.hovertext) !== name) return;
        ann.x = destination.x;
        ann.y = destination.y;
      });
    });
    update.annotations = annotations;
    window.Plotly.relayout(gd, update);
  }

  function apply(orderData) {
    var order = (orderData && orderData.order) || [];
    if (!order.length) return;
    GRAPH_IDS.forEach(function (id) { applyToGraph(id, order); });
  }

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
    var payload = {
      group_by: first.getAttribute('data-order-group'),
      order: values(list),
      ts: Date.now()
    };
    apply(payload);
    window.dash_clientside.set_props('panel-order-store', {data: payload});
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

  window.dash_clientside = Object.assign({}, window.dash_clientside, {
    panel_order: {apply: apply}
  });
})();
