(function () {
  function finiteNumber(value, fallback) {
    var number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function quantile(sorted, percentile) {
    if (!sorted.length) return 0;
    var p = Math.max(0, Math.min(100, finiteNumber(percentile, 0))) / 100;
    var position = p * (sorted.length - 1);
    var lower = Math.floor(position);
    var upper = Math.ceil(position);
    if (lower === upper) return sorted[lower];
    var fraction = position - lower;
    return sorted[lower] * (1 - fraction) + sorted[upper] * fraction;
  }

  function metricLabel(value, metric) {
    if (!Number.isFinite(value)) return '';
    if (metric === 'percent') return value.toPrecision(3) + '%';
    if (metric === 'time') return value.toPrecision(3) + 's';
    return value >= 1000 ? value.toExponential(1) : Number(value.toPrecision(3)).toString();
  }

  function logTicks(minimum, maximum, metric) {
    var lowDecade = Math.floor(Math.log10(minimum));
    var highDecade = Math.ceil(Math.log10(maximum));
    var multipliers = highDecade - lowDecade > 4 ? [1] : [1, 2, 5];
    var values = [];
    var labels = [];
    for (var decade = lowDecade; decade <= highDecade; decade += 1) {
      for (var index = 0; index < multipliers.length; index += 1) {
        var raw = multipliers[index] * Math.pow(10, decade);
        if (raw >= minimum * 0.999 && raw <= maximum * 1.001) {
          values.push(Math.log10(raw));
          labels.push(metricLabel(raw, metric));
        }
      }
    }
    if (!values.length) {
      values.push(Math.log10(Math.max(maximum, 1e-9)));
      labels.push(metricLabel(maximum, metric));
    }
    return { values: values, labels: labels };
  }

  function colorBounds(metric, scale, range, rangeMode, distribution, variant) {
    var values = ((distribution && distribution.values) || [])
      .map(Number).filter(Number.isFinite).sort(function (a, b) { return a - b; });
    var rawMin = scale === 'log'
      ? finiteNumber(distribution && distribution.min_positive, values[0] || 1)
      : 0;
    var rawMax = finiteNumber(distribution && distribution.max,
      values.length ? values[values.length - 1] : 1);
    var selected = Array.isArray(range) && range.length === 2
      ? [finiteNumber(range[0], 0), finiteNumber(range[1], 100)] : null;

    if (selected && rangeMode === 'percentile') {
      rawMin = selected[0] <= 1e-9 ? rawMin : quantile(values, selected[0]);
      rawMax = selected[1] >= 100 - 1e-9 ? rawMax : quantile(values, selected[1]);
    } else if (selected) {
      var displayLow = finiteNumber(distribution && distribution.lo, 0);
      var displayHigh = finiteNumber(distribution && distribution.hi, rawMax);
      var epsilon = Math.max(Math.abs(displayHigh - displayLow), 1) * 1e-9;
      rawMin = selected[0] <= displayLow + epsilon ? rawMin : selected[0];
      rawMax = selected[1] >= displayHigh - epsilon ? rawMax : selected[1];
    }

    if (metric === 'time') rawMin = Math.max(rawMin, 0.1);
    if (scale === 'log') rawMin = Math.max(rawMin, 1e-9);
    if (!(rawMax > rawMin)) rawMax = scale === 'log' ? rawMin * 10 : rawMin + 1;

    var colorbar = Object.assign({}, (variant && variant.colorbar) || {});
    if (scale === 'log') {
      var ticks = logTicks(rawMin, rawMax, metric);
      colorbar.tickvals = ticks.values;
      colorbar.ticktext = ticks.labels;
      return {
        zmin: Math.log10(rawMin), zmax: Math.log10(rawMax), colorbar: colorbar
      };
    }
    colorbar.tickvals = null;
    colorbar.ticktext = null;
    return { zmin: rawMin, zmax: rawMax, colorbar: colorbar };
  }

  window.__restyleHeatmap = function (graph, variant, metric, scale, range,
                                      rangeMode, distribution) {
    if (!graph || !window.Plotly || !variant || !graph.data || !graph.data.length) {
      return false;
    }
    var traceIndices = variant.z.map(function (_, index) { return index; });
    var bounds = colorBounds(metric || 'time', scale || 'lin', range,
      rangeMode || 'percentile', distribution || {}, variant);
    window.Plotly.restyle(graph, {
      z: variant.z,
      customdata: variant.customdata
    }, traceIndices);
    window.Plotly.restyle(graph, {
      zmin: bounds.zmin,
      zmax: bounds.zmax,
      hovertemplate: variant.hovertemplate
    });
    window.Plotly.restyle(graph, { colorbar: [bounds.colorbar] }, [0]);
    if (variant.roi_texts && graph.layout && graph.layout.annotations) {
      var textIndex = 0;
      var annotations = graph.layout.annotations.map(function (annotation) {
        var updated = Object.assign({}, annotation);
        if ((updated.name || '').indexOf('hm-roi') === 0) {
          updated.text = variant.roi_texts[textIndex++] || '';
        }
        return updated;
      });
      window.Plotly.relayout(graph, { annotations: annotations });
    }
    return true;
  };
})();
