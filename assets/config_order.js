// Drag-to-reorder the active panel axis (config, scene, VR, fly or folder).
// Domains are swapped on the mounted Plotly graphs: traces, bins and statistics
// remain untouched. The compact store only preserves the order for later full
// analytical renders.
(function () {
  "use strict";

  var GRAPH_IDS = [
    "trajectory-plot", "loop-observer-plot", "heatmap-plot",
    "flow-plot", "polar-plot", "initial-heading-plot",
    "roi-plot", "custom-region-diagnostics-plot", "trial-metrics-plot"
  ];
  var activeOrder = null;

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

  function numericCache(gd) {
    var meta = (gd.layout && gd.layout.meta) || {};
    var raw = (meta.panel_order_values || []).map(String);
    if (!raw.length) return null;
    var oldIndex = {};
    raw.forEach(function (name, index) { oldIndex[name] = index; });
    return {
      dataRef: gd.data,
      raw: raw,
      labels: (meta.panel_order_labels || raw).map(String),
      mixedX: Boolean(meta.td_mixed_group_x),
      oldIndex: oldIndex,
      traces: (gd.data || []).map(function (trace) {
        return {
          group: String(((trace || {}).meta || {}).td_group_value || ""),
          x: Array.isArray(trace && trace.x) ?
            trace.x.slice() : (
              ArrayBuffer.isView(trace && trace.x) ?
                Array.from(trace.x) : null
            )
        };
      }),
      shapes: (gd.layout.shapes || []).map(function (shape) {
        var match = /^td-group-shape:(.*)$/.exec(
          String((shape && shape.name) || "")
        );
        return {
          group: match ? match[1] : "",
          x0: Number(shape && shape.x0),
          x1: Number(shape && shape.x1)
        };
      })
    };
  }

  function applyNumericGraph(gd, order) {
    var cache = gd.__tdNumericOrder;
    if (!cache || cache.dataRef !== gd.data) {
      cache = numericCache(gd);
      gd.__tdNumericOrder = cache;
    }
    if (!cache) return false;
    var desired = (order || []).map(String).filter(function (name) {
      return Object.prototype.hasOwnProperty.call(cache.oldIndex, name);
    });
    cache.raw.forEach(function (name) {
      if (desired.indexOf(name) < 0) desired.push(name);
    });
    if (desired.length !== cache.raw.length) return false;
    var newIndex = {};
    desired.forEach(function (name, index) { newIndex[name] = index; });
    var jobs = [];
    cache.traces.forEach(function (item, index) {
      if (!item.x || !(item.group in newIndex)) return;
      var delta = newIndex[item.group] - cache.oldIndex[item.group];
      jobs.push(window.Plotly.restyle(
        gd, {x: [item.x.map(function (value) {
          var numeric = Number(value);
          return Number.isFinite(numeric) ? numeric + delta : value;
        })]}, [index]
      ));
    });
    if (cache.mixedX) {
      cache.traces.forEach(function (item, index) {
        if (!item.x || item.group) return;
        jobs.push(window.Plotly.restyle(
          gd, {x: [item.x.map(function (value) {
            var numeric = Number(value);
            if (!Number.isFinite(numeric)) return value;
            var baseIndex = Math.round(numeric);
            if (baseIndex < 0 || baseIndex >= cache.raw.length) return value;
            var rawName = cache.raw[baseIndex];
            return numeric + newIndex[rawName] - baseIndex;
          })]}, [index]
        ));
      });
    }
    var update = {};
    cache.shapes.forEach(function (item, index) {
      if (!(item.group in newIndex)) return;
      var delta = newIndex[item.group] - cache.oldIndex[item.group];
      if (Number.isFinite(item.x0)) {
        update["shapes[" + index + "].x0"] = item.x0 + delta;
      }
      if (Number.isFinite(item.x1)) {
        update["shapes[" + index + "].x1"] = item.x1 + delta;
      }
    });
    var labelByRaw = {};
    cache.raw.forEach(function (name, index) {
      labelByRaw[name] = cache.labels[index] || name;
    });
    Object.keys(gd.layout || {}).forEach(function (key) {
      if (!/^xaxis\d*$/.test(key)) return;
      update[key + ".tickvals"] = desired.map(function (_name, index) {
        return index;
      });
      update[key + ".ticktext"] = desired.map(function (name) {
        return labelByRaw[name] || name;
      });
    });
    var annotations = clone(gd.layout.annotations || []);
    annotations.forEach(function (annotation) {
      var match = /^td-stats:(.*)$/.exec(
        String((annotation && annotation.name) || "")
      );
      if (!match || !(match[1] in newIndex)) return;
      var offset = Number(annotation.x) - cache.oldIndex[match[1]];
      annotation.x = newIndex[match[1]] +
        (Number.isFinite(offset) ? offset : 0);
    });
    update.annotations = annotations;
    jobs.push(window.Plotly.relayout(gd, update));
    Promise.all(jobs);
    return true;
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
    if ((id === "roi-plot" ||
         id === "custom-region-diagnostics-plot" ||
         id === "trial-metrics-plot") &&
        applyNumericGraph(gd, order)) return;
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
    activeOrder = clone(orderData);
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
    panel_order: {
      apply: apply,
      reapply: function () {
        if (activeOrder) apply(activeOrder);
      }
    }
  });
})();
