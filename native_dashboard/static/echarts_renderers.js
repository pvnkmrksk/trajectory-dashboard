import {NATIVE_PALETTE, formatNumber} from "/static/renderers.js";

const ECHARTS = globalThis.echarts;
const INK = "#17211f";
const MUTED = "#64706c";
const GRID = "rgba(31, 45, 41, .11)";
const PAPER = "#ffffff";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>\"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;",
  })[character]);
}

function autoColumns(count, requested = 0) {
  if (requested > 0) return Math.max(1, Math.min(4, Math.max(1, count), requested));
  if (count <= 1) return 1;
  if (count <= 4) return 2;
  if (count <= 9) return 3;
  return 4;
}

function quantile(sorted, q) {
  if (!sorted.length) return NaN;
  const at = (sorted.length - 1) * q;
  const lo = Math.floor(at), hi = Math.ceil(at), f = at - lo;
  return sorted[lo] * (1 - f) + sorted[hi] * f;
}

function deterministicJitter(index, trial, step, animal, width = 13) {
  let hash = (index + 1) ^ ((Number(trial) || 0) * 2654435761)
    ^ ((Number(step) || 0) * 2246822519) ^ ((Number(animal) || 0) * 3266489917);
  hash = Math.imul(hash ^ (hash >>> 16), 2246822507) >>> 0;
  return (hash / 4294967295 - .5) * width;
}

function densityProfile(sorted, bins = 27) {
  if (!sorted.length) return null;
  const lower = quantile(sorted, .01), upper = quantile(sorted, .99);
  const span = Math.max(1e-9, upper - lower);
  const bandwidth = Math.max(span / 10, Math.abs(quantile(sorted, .75) - quantile(sorted, .25)) * .28, 1e-9);
  const stride = Math.max(1, Math.ceil(sorted.length / 2400));
  const points = [];
  let maximum = 0;
  for (let bin = 0; bin < bins; bin += 1) {
    const value = lower + span * bin / (bins - 1);
    let density = 0;
    for (let index = 0; index < sorted.length; index += stride) {
      const distance = (sorted[index] - value) / bandwidth;
      density += Math.exp(-.5 * distance * distance);
    }
    maximum = Math.max(maximum, density);
    points.push([value, density]);
  }
  for (const point of points) point[1] = maximum > 0 ? point[1] / maximum : 0;
  return {median: quantile(sorted, .5), points};
}

function animalColor(code) {
  return NATIVE_PALETTE[Number(code) % 16];
}

function toolbox(name, zoom = true) {
  const feature = {
    restore: {title: "Reset"},
    saveAsImage: {title: "Save image", name, pixelRatio: 2},
  };
  if (zoom) feature.dataZoom = {title: {zoom: "Box zoom", back: "Undo zoom"}};
  return {show: true, right: 10, top: 6, itemSize: 14, feature};
}

function axisBase() {
  return {
    axisLine: {lineStyle: {color: "#aeb8b4"}},
    axisTick: {lineStyle: {color: "#aeb8b4"}},
    axisLabel: {color: MUTED, fontSize: 10, hideOverlap: true},
    splitLine: {lineStyle: {color: GRID}},
    nameTextStyle: {color: MUTED, fontSize: 10},
  };
}

function selectedMap(data, visible) {
  const result = {};
  for (let i = 0; i < (data?.animalNames?.length || 0); i += 1) {
    result[data.animalNames[i]] = visible?.[i] !== false;
  }
  return result;
}

function compactLetters(count, tests = []) {
  let columns = [new Set(Array.from({length: count}, (_, index) => index))];
  for (const test of tests.filter(test => Number(test.adjustedP) < .05)) {
    const next = [];
    for (const column of columns) {
      if (column.has(test.first) && column.has(test.second)) {
        const a = new Set(column); a.delete(test.first);
        const b = new Set(column); b.delete(test.second);
        if (a.size) next.push(a); if (b.size) next.push(b);
      } else next.push(column);
    }
    columns = next.filter((column, index, all) => !all.some((other, otherIndex) =>
      otherIndex !== index && column.size < other.size && [...column].every(value => other.has(value))));
  }
  return Array.from({length: count}, (_, panel) => columns.map((column, index) =>
    column.has(panel) ? String.fromCharCode(65 + index) : "").join(""));
}

function significanceStars(value) {
  return value < .001 ? "***" : value < .01 ? "**" : value < .05 ? "*" : "";
}

class EChartRenderer {
  constructor(host) {
    if (!ECHARTS) throw new Error("Apache ECharts did not load.");
    this.host = host;
    this.data = null;
    this.fraction = 1;
    this.animalVisibility = null;
    this.appliedAnimalVisibility = [];
    this.chart = ECHARTS.init(host, null, {renderer: "canvas", useDirtyRect: true});
    this.resizeObserver = new ResizeObserver(() => {
      this.chart.resize({animation: {duration: 0}});
      this.draw();
    });
    this.resizeObserver.observe(host);
    this.visibilityObserver = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) this.syncAnimalLegend();
    });
    this.visibilityObserver.observe(host);
  }
  setData(data) { this.data = data; this.draw(); }
  setStatistics(data) { this.statistics = data; this.draw(); }
  setColumns(value) {
    if (!this.data) return;
    this.data.columns = Number(value) || 0;
    this.draw();
  }
  setFraction(value) {
    this.fraction = Math.max(.01, Math.min(1, Number(value) || 1));
    this.draw();
  }
  setAnimalVisibility(value) {
    this.animalVisibility = value ? [...value] : null;
    const rect = this.host.getBoundingClientRect();
    if (rect.bottom > 0 && rect.top < innerHeight) this.syncAnimalLegend();
  }
  syncAnimalLegend() {
    if (!this.data?.animalNames) return;
    for (let index = 0; index < this.data.animalNames.length; index += 1) {
      const visible = this.animalVisibility?.[index] !== false;
      if (this.appliedAnimalVisibility[index] === visible) continue;
      this.chart.dispatchAction({
        type: visible ? "legendSelect" : "legendUnSelect",
        name: this.data.animalNames[index],
      });
      this.appliedAnimalVisibility[index] = visible;
    }
  }
  base(name, zoom = true) {
    return {
      backgroundColor: PAPER,
      animation: false,
      textStyle: {fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif", color: INK},
      color: NATIVE_PALETTE,
      aria: {enabled: true},
      toolbox: toolbox(name, zoom),
    };
  }
  draw() {
    if (!this.data) return;
    this.chart.setOption(this.option(), {notMerge: true, lazyUpdate: false});
    this.appliedAnimalVisibility = (this.data.animalNames || []).map(
      (_, index) => this.animalVisibility?.[index] !== false,
    );
  }
}

export class EChartsPolarRenderer extends EChartRenderer {
  constructor(host) {
    super(host);
    this.radialMax = 1;
    host.addEventListener("wheel", event => {
      if (!this.data || (this.data.mode || "vectors") !== "vectors") return;
      event.preventDefault();
      const factor = event.deltaY < 0 ? .82 : 1 / .82;
      this.radialMax = Math.max(.15, Math.min(1, this.radialMax * factor));
      this.draw();
    }, {passive: false});
    host.addEventListener("dblclick", () => { this.radialMax = 1; this.draw(); });
  }
  option() {
    const data = this.data;
    const count = Math.max(1, data.panelCount);
    const cols = autoColumns(count, data.columns), rows = Math.ceil(count / cols);
    const width = Math.max(320, this.host.clientWidth), height = Math.max(320, this.host.clientHeight);
    const top = 54, cellW = width / cols, cellH = (height - top) / rows;
    const polar = [], angleAxis = [], radiusAxis = [], titles = [];
    const circularLetters = compactLetters(count, this.statistics?.circularPairwise || []);
    for (let panel = 0; panel < count; panel += 1) {
      const col = panel % cols, row = Math.floor(panel / cols);
      polar.push({
        center: [col * cellW + cellW / 2, top + row * cellH + cellH * .55],
        radius: Math.max(34, Math.min(cellW, cellH) * .39),
      });
      angleAxis.push({
        polarIndex: panel, type: "value", min: -180, max: 180, interval: 90,
        startAngle: -90, clockwise: true,
        axisLabel: {color: MUTED, fontSize: 9, formatter: value => `${value}°`},
        axisLine: {lineStyle: {color: "#aeb8b4"}}, splitLine: {lineStyle: {color: GRID}},
      });
      radiusAxis.push({
        polarIndex: panel, type: "value", min: 0, max: this.radialMax, interval: this.radialMax / 4,
        axisLabel: {color: MUTED, fontSize: 9, formatter: value => formatNumber(value, 1)},
        axisLine: {show: false}, splitLine: {lineStyle: {color: GRID}},
      });
      const rayleigh = this.statistics?.rayleigh?.find(result => result.panel === panel);
      const annotation = rayleigh?.p < .05
        ? `  ${circularLetters[panel] || ""}${significanceStars(rayleigh.p)}` : "";
      titles.push({
        text: `${data.panelNames?.[panel] || "All data"}${annotation}`,
        left: col * cellW + 12, top: top + row * cellH + 2,
        textStyle: {fontSize: 11, fontWeight: 650, color: INK},
      });
    }

    const mode = data.mode || "vectors";
    if (mode !== "vectors") {
      const bins = mode === "histogram" ? 36 : data.densityBins;
      const counts = Array.from({length: count}, () => new Float64Array(bins));
      if (mode === "histogram") {
        for (let index = 0; index < data.angle.length; index += 1) {
          if (data.sample[index] > this.fraction || this.animalVisibility?.[data.animal[index]] === false) continue;
          const bin = Math.max(0, Math.min(bins - 1,
            Math.floor((((data.angle[index] + 180) % 360 + 360) % 360) / 360 * bins)));
          counts[data.panel[index]][bin] += 1;
        }
      } else {
        const animalCount = Math.max(1, data.animalNames?.length || 1);
        for (let panel = 0; panel < count; panel += 1) for (let animal = 0; animal < animalCount; animal += 1) {
          if (this.animalVisibility?.[animal] === false) continue;
          const offset = (panel * animalCount + animal) * bins;
          for (let bin = 0; bin < bins; bin += 1) counts[panel][bin] += data.headingDensity[offset + bin] || 0;
        }
      }
      let maximum = 0;
      for (let panel = 0; panel < count; panel += 1) {
        if (mode === "density") {
          const source = counts[panel], smoothed = new Float64Array(bins);
          const kernel = [1, 4, 7, 10, 7, 4, 1], kernelTotal = 34;
          for (let bin = 0; bin < bins; bin += 1) for (let shift = -3; shift <= 3; shift += 1) {
            smoothed[bin] += source[(bin + shift + bins) % bins] * kernel[shift + 3] / kernelTotal;
          }
          counts[panel] = smoothed;
        }
        const total = counts[panel].reduce((sum, value) => sum + value, 0);
        if (total > 0) for (let bin = 0; bin < bins; bin += 1) {
          counts[panel][bin] = counts[panel][bin] / total * 100;
          maximum = Math.max(maximum, counts[panel][bin]);
        }
      }
      maximum = Math.max(1, maximum * 1.08);
      for (const axis of radiusAxis) Object.assign(axis, {
        min: 0, max: maximum, interval: null,
        axisLabel: {color: MUTED, fontSize: 9, formatter: value => `${formatNumber(value, 1)}%`},
      });
      const series = [];
      for (let panel = 0; panel < count; panel += 1) {
        const values = Array.from({length: bins}, (_, bin) => [
          counts[panel][bin], -180 + (bin + .5) * 360 / bins,
        ]);
        series.push({
          name: data.panelNames?.[panel] || "All data", type: "line",
          coordinateSystem: "polar", polarIndex: panel,
          data: [...values, values[0]], encode: {radius: 0, angle: 1},
          showSymbol: false, smooth: mode === "density" ? .42 : false,
          step: mode === "histogram" ? "middle" : false,
          lineStyle: {color: "#0e7c73", width: mode === "histogram" ? 1.4 : 2},
          areaStyle: {color: mode === "histogram" ? "rgba(14,124,115,.30)" : "rgba(14,124,115,.20)"},
        });
      }
      return {
        ...this.base(mode === "histogram" ? "polar-resultant-histogram" : "polar-heading-density", false),
        title: titles, legend: {show: false},
        tooltip: {trigger: "item", confine: true, formatter: params => {
          const value = params.data || [];
          return `<b>${escapeHtml(params.seriesName)}</b><br>${formatNumber(value[1], 1)}° · ${formatNumber(value[0], 1)}%`;
        }},
        polar, angleAxis, radiusAxis, series,
        graphic: [{type: "text", right: 16, top: 17, silent: true, style: {
          text: mode === "histogram" ? "replicate direction histogram" : "circularly smoothed heading density",
          fill: MUTED, font: "10px Inter, system-ui",
        }}],
      };
    }

    const grouped = new Map();
    for (let i = 0; i < data.angle.length; i += 1) {
      if (data.sample[i] > this.fraction) continue;
      const key = `${data.panel[i]}|${data.animal[i]}`;
      if (!grouped.has(key)) grouped.set(key, []);
      const animal = data.animalNames?.[data.animal[i]] || `Animal ${data.animal[i] + 1}`;
      grouped.get(key).push({
        coords: [[0, data.angle[i]], [data.r[i], data.angle[i]]],
        value: [data.r[i], data.angle[i]], animal,
        panel: data.panelNames?.[data.panel[i]] || "All data",
        replicate: data.replicate[i], trial: data.trial[i], step: data.step[i],
      });
    }
    const series = [];
    for (const [key, values] of grouped) {
      const [panel, animal] = key.split("|").map(Number);
      const name = data.animalNames?.[animal] || `Animal ${animal + 1}`;
      series.push({
        name,
        type: "lines", coordinateSystem: "polar", polarIndex: panel,
        data: values, polyline: false, silent: true, large: values.length > 2000,
        lineStyle: {color: animalColor(animal), width: 1, opacity: .34},
        emphasis: {disabled: true}, select: {disabled: true},
      });
      series.push({
        name, type: "scatter", coordinateSystem: "polar", polarIndex: panel,
        data: values.map(item => ({...item, value: item.value})),
        symbolSize: 4,
        itemStyle: {color: animalColor(animal), opacity: .48},
        emphasis: {scale: 1.7, itemStyle: {opacity: 1}},
      });
    }
    for (let panel = 0; panel < count; panel += 1) {
      const angle = data.populationAngle[panel], r = data.populationR[panel];
      if (!Number.isFinite(angle) || !Number.isFinite(r)) continue;
      series.push({
        name: "Population mean", type: "lines", coordinateSystem: "polar", polarIndex: panel,
        data: [{coords: [[0, angle], [r, angle]], value: [r, angle], population: true,
          panel: data.panelNames?.[panel] || "All data"}],
        lineStyle: {color: INK, width: 4, opacity: .92}, silent: true,
        emphasis: {disabled: true}, z: 10,
      });
      series.push({
        name: "Population mean", type: "scatter", coordinateSystem: "polar", polarIndex: panel,
        data: [{value: [r, angle], population: true,
          panel: data.panelNames?.[panel] || "All data"}],
        symbolSize: 7, itemStyle: {color: INK}, z: 11,
      });
    }
    return {
      ...this.base("polar-direction", false), title: titles,
      legend: {
        type: "scroll", left: 12, right: 100, top: 8, height: 28,
        selected: selectedMap(data, this.animalVisibility),
        textStyle: {fontSize: 10, color: MUTED}, itemWidth: 16, itemHeight: 7,
      },
      tooltip: {
        trigger: "item", confine: true,
        formatter: params => {
          const item = params.data || {};
          if (item.population) return `<b>${escapeHtml(item.panel)}</b><br>Population mean<br>Angle ${formatNumber(item.value?.[1], 1)}° · R ${formatNumber(item.value?.[0], 1)}`;
          const trial = Number.isFinite(item.trial) ? `<br>Replicate ${formatNumber(item.replicate, 0)} · trial ${formatNumber(item.trial, 0)} · step ${formatNumber(item.step, 0)}` : "";
          return `<b>${escapeHtml(item.animal)}</b> · ${escapeHtml(item.panel)}${trial}<br>Angle ${formatNumber(item.value?.[1], 1)}° · R ${formatNumber(item.value?.[0], 1)}`;
        },
      },
      polar, angleAxis, radiusAxis, series,
      graphic: [{
        type: "text", right: 92, top: 31, silent: true,
        style: {text: this.radialMax < .999 ? `radial zoom 0–${formatNumber(this.radialMax, 1)} · double-click to reset` : "wheel to radial zoom",
          fill: MUTED, font: "10px Inter, system-ui"},
      }],
    };
  }
}

export class EChartsHeadingRenderer extends EChartRenderer {
  option() {
    const data = this.data;
    const count = Math.max(1, data.panelCount), cols = autoColumns(count, data.columns), rows = Math.ceil(count / cols);
    const width = Math.max(320, this.host.clientWidth), height = Math.max(340, this.host.clientHeight);
    const top = 58, bottom = 42, cellW = width / cols, cellH = (height - top - bottom) / rows;
    const grid = [], xAxis = [], yAxis = [], titles = [];
    for (let panel = 0; panel < count; panel += 1) {
      const col = panel % cols, row = Math.floor(panel / cols);
      grid.push({left: col * cellW + 48, top: top + row * cellH + 24, width: Math.max(40, cellW - 66), height: Math.max(45, cellH - 42), containLabel: false});
      xAxis.push({gridIndex: panel, type: "value", min: 0, max: Math.max(.001, data.maxTime), ...axisBase()});
      yAxis.push({gridIndex: panel, type: "value", min: -180, max: 180, interval: 90, axisLabel: {...axisBase().axisLabel, formatter: value => `${value}°`}, ...axisBase()});
      titles.push({text: data.panelNames?.[panel] || "All data", left: col * cellW + 48, top: top + row * cellH, textStyle: {fontSize: 11, fontWeight: 650, color: INK}});
    }
    const axisIndices = Array.from({length: count}, (_, i) => i);
    const dataZoom = [
      {type: "inside", xAxisIndex: axisIndices, filterMode: "none", throttle: 40},
      {type: "slider", xAxisIndex: axisIndices, filterMode: "none", left: 56, right: 24, bottom: 8, height: 16, borderColor: "transparent", fillerColor: "rgba(14,124,115,.14)", handleStyle: {color: "#0e7c73"}},
    ];
    if (data.mode === "density") {
      const timeLabels = Array.from({length: data.nTime}, (_, tx) => Math.min(data.maxTime, (tx + .5) * data.timeBin));
      const angleLabels = Array.from({length: data.sectors}, (_, ay) => -180 + (ay + .5) * 360 / data.sectors);
      const timeLabelEvery = Math.max(1, Math.ceil(data.nTime / 5));
      const angleLabelEvery = Math.max(1, Math.round(data.sectors / 4));
      for (let panel = 0; panel < count; panel += 1) {
        xAxis[panel] = {
          gridIndex: panel, type: "category", data: timeLabels, boundaryGap: true,
          axisTick: {show: false}, axisLine: axisBase().axisLine, splitLine: {show: false},
          axisLabel: {
            ...axisBase().axisLabel,
            interval: (index) => index % timeLabelEvery === 0,
            formatter: value => formatNumber(Number(value), 1),
          },
        };
        yAxis[panel] = {
          gridIndex: panel, type: "category", data: angleLabels, boundaryGap: true,
          axisTick: {show: false}, axisLine: axisBase().axisLine, splitLine: {show: false},
          axisLabel: {
            ...axisBase().axisLabel,
            interval: (index) => index % angleLabelEvery === 0,
            formatter: value => `${formatNumber(Number(value), 0)}°`,
          },
        };
      }
      const series = [];
      let maximum = 0;
      for (let panel = 0; panel < count; panel += 1) {
        const values = [];
        for (let tx = 0; tx < data.nTime; tx += 1) {
          for (let ay = 0; ay < data.sectors; ay += 1) {
            const value = data.density[(panel * data.nTime + tx) * data.sectors + ay];
            maximum = Math.max(maximum, value);
            if (value > 0) values.push([tx, ay, value]);
          }
        }
        series.push({
          name: data.panelNames?.[panel] || "All data",
          type: "heatmap", xAxisIndex: panel, yAxisIndex: panel,
          data: values, progressive: 4000,
          itemStyle: {borderWidth: 0},
          emphasis: {itemStyle: {borderColor: "#1b2a26", borderWidth: 1}},
        });
      }
      return {
        ...this.base("heading-density"), title: titles,
        tooltip: {trigger: "item", confine: true, formatter: params => {
          const value = params.data || [];
          return `<b>${escapeHtml(params.seriesName)}</b><br>${formatNumber(timeLabels[value[0]], 1)} s · ${formatNumber(angleLabels[value[1]], 1)}°<br>${formatNumber(value[2], 1)}% of samples in this time bin`;
        }},
        visualMap: {
          min: 0, max: Math.max(1, maximum), calculable: true,
          orient: "horizontal", left: "center", top: 8, itemWidth: 12, itemHeight: 120,
          textStyle: {color: MUTED, fontSize: 9},
          inRange: {color: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"]},
        },
        grid, xAxis, yAxis, dataZoom, series,
      };
    }
    const grouped = new Map();
    for (let i = 0; i < data.panels.length; i += 2) {
      const animal = data.animals[i];
      if (data.samples[i] > this.fraction) continue;
      const panel = data.panels[i], key = `${panel}|${animal}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push({
        coords: [[data.vertices[i * 2], data.vertices[i * 2 + 1]], [data.vertices[(i + 1) * 2], data.vertices[(i + 1) * 2 + 1]]],
        animal: data.animalNames?.[animal] || `Animal ${animal + 1}`,
        panel: data.panelNames?.[panel] || "All data", trial: data.trials[i],
        step: data.steps[i], r: data.mode === "mean" ? data.steps[i] : NaN,
      });
    }
    const series = [];
    for (const [key, values] of grouped) {
      const [panel, animal] = key.split("|").map(Number);
      series.push({
        name: data.animalNames?.[animal] || `Animal ${animal + 1}`,
        type: "lines", coordinateSystem: "cartesian2d", xAxisIndex: panel, yAxisIndex: panel,
        data: values, polyline: false, progressive: 5000, progressiveThreshold: 3000,
        lineStyle: {color: animalColor(animal), width: data.mode === "mean" ? 1.8 : .8, opacity: data.mode === "mean" ? .82 : .26},
        emphasis: {disabled: values.length > 12000},
      });
    }
    return {
      ...this.base("heading-local-time"), title: titles,
      legend: {type: "scroll", left: 12, right: 100, top: 8, selected: selectedMap(data, this.animalVisibility), textStyle: {fontSize: 10, color: MUTED}, itemWidth: 16, itemHeight: 7},
      tooltip: {trigger: "item", confine: true, formatter: params => {
        const item = params.data || {}, end = item.coords?.[1] || [];
        if (data.mode === "mean") return `<b>${escapeHtml(item.animal)}</b> · ${escapeHtml(item.panel)}<br>${formatNumber(end[0], 1)} s · circular mean ${formatNumber(end[1], 1)}°<br>Across-trial R ${formatNumber(item.r, 1)}`;
        return `<b>${escapeHtml(item.animal)}</b> · ${escapeHtml(item.panel)}<br>Trial ${formatNumber(item.trial, 0)} · step ${formatNumber(item.step, 0)}<br>${formatNumber(end[0], 1)} s · ${formatNumber(end[1], 1)}°`;
      }},
      grid, xAxis, yAxis,
      dataZoom,
      series,
    };
  }
}

export class EChartsMetricsRenderer extends EChartRenderer {
  option() {
    const data = this.data;
    const metrics = [
      ["distance", "Distance walked"], ["displacement", "Net displacement"],
      ["speed", "Median velocity"], ["tortuosity", "Local tortuosity"],
    ];
    const width = Math.max(320, this.host.clientWidth), height = Math.max(420, this.host.clientHeight);
    const cellW = width / 2, cellH = (height - 42) / 2;
    const grid = [], xAxis = [], yAxis = [], titles = [], series = [];
    metrics.forEach(([key, title], metricIndex) => {
      const col = metricIndex % 2, row = Math.floor(metricIndex / 2);
      grid.push({left: col * cellW + 52, top: 44 + row * cellH + 35, width: Math.max(60, cellW - 76), height: Math.max(70, cellH - 66)});
      const metricStatistics = this.statistics?.metrics?.find(result => result.metric === key);
      const letters = compactLetters(data.panelCount, metricStatistics?.pairwise || []);
      xAxis.push({gridIndex: metricIndex, type: "category", data: data.panelNames,
        axisLabel: {...axisBase().axisLabel, rotate: data.panelCount > 5 ? 28 : 0,
          formatter: (value, index) => `${value}${metricStatistics ? `\n${letters[index] || ""}` : ""}`}, ...axisBase()});
      yAxis.push({gridIndex: metricIndex, type: "value", scale: true, ...axisBase()});
      titles.push({text: title, left: col * cellW + 52, top: 44 + row * cellH + 8, textStyle: {fontSize: 11, fontWeight: 650, color: INK}});
      const boxes = [], profiles = [];
      for (let panel = 0; panel < data.panelCount; panel += 1) {
        const values = [];
        for (let i = 0; i < data[key].length; i += 1) if (data.panel[i] === panel && Number.isFinite(data[key][i])) values.push(data[key][i]);
        values.sort((a, b) => a - b);
        boxes.push(values.length ? [values[0], quantile(values, .25), quantile(values, .5), quantile(values, .75), values[values.length - 1]] : [NaN, NaN, NaN, NaN, NaN]);
        const profile = densityProfile(values);
        if (profile) profiles.push({...profile, panel});
      }
      const halfWidth = Math.max(8, Math.min(25, cellW / Math.max(2, data.panelCount) * .26));
      series.push({
        type: "custom", xAxisIndex: metricIndex, yAxisIndex: metricIndex,
        data: profiles.map(profile => [profile.panel, profile.median]),
        silent: true, clip: true, z: 1,
        renderItem: (params, api) => {
          const profile = profiles[params.dataIndex];
          if (!profile) return null;
          const center = api.coord([profile.panel, profile.median])[0];
          const right = profile.points.map(([value, density]) => [center + density * halfWidth, api.coord([profile.panel, value])[1]]);
          const left = [...profile.points].reverse().map(([value, density]) => [center - density * halfWidth, api.coord([profile.panel, value])[1]]);
          return {type: "polygon", shape: {points: [...right, ...left]}, style: {
            fill: "rgba(14,124,115,.11)", stroke: "rgba(14,124,115,.42)", lineWidth: .8,
          }};
        },
      });
      series.push({
        type: "boxplot", xAxisIndex: metricIndex, yAxisIndex: metricIndex,
        data: boxes, silent: false, z: 3,
        boxWidth: [6, Math.max(10, halfWidth * .72)],
        itemStyle: {color: "rgba(255,255,255,.72)", borderColor: "#0e7c73", borderWidth: 1.15},
      });
      for (let animal = 0; animal < (data.animalNames?.length || 0); animal += 1) {
        const points = [];
        for (let i = 0; i < data[key].length; i += 1) {
          if (data.animal[i] !== animal || !Number.isFinite(data[key][i])) continue;
          points.push({
            value: [data.panel[i], data[key][i], deterministicJitter(i, data.trial[i], data.step[i], animal)],
            animal: data.animalNames[animal], panel: data.panelNames[data.panel[i]],
            replicate: data.replicate[i], trial: data.trial[i], step: data.step[i], metric: title,
          });
        }
        if (points.length) series.push({
          name: data.animalNames[animal], type: "custom", xAxisIndex: metricIndex, yAxisIndex: metricIndex,
          data: points, encode: {x: 0, y: 1}, clip: true, z: 4,
          itemStyle: {color: animalColor(animal), opacity: .38},
          renderItem: (_params, api) => {
            const point = api.coord([api.value(0), api.value(1)]);
            return {type: "circle", shape: {cx: point[0] + api.value(2), cy: point[1], r: 2.1},
              style: {fill: animalColor(animal), opacity: .38}};
          },
        });
      }
    });
    return {
      ...this.base("trial-metrics"), title: titles,
      legend: {type: "scroll", orient: "horizontal", left: 12, right: 46, top: 7, height: 28,
        pageButtonPosition: "end", pageIconSize: 9, pageTextStyle: {fontSize: 9, color: MUTED},
        selected: selectedMap(data, this.animalVisibility), textStyle: {fontSize: 9, color: MUTED}, itemWidth: 12, itemHeight: 6, itemGap: 11,
        tooltip: {show: true}},
      tooltip: {trigger: "item", confine: true, formatter: params => {
        const item = params.data || {};
        if (params.seriesType === "boxplot") return `<b>${escapeHtml(params.name)}</b><br>min ${formatNumber(item[0], 1)} · Q1 ${formatNumber(item[1], 1)}<br>median ${formatNumber(item[2], 1)} · Q3 ${formatNumber(item[3], 1)} · max ${formatNumber(item[4], 1)}`;
        return `<b>${escapeHtml(item.animal)}</b> · ${escapeHtml(item.panel)}<br>Replicate ${formatNumber(item.replicate, 0)} · trial ${formatNumber(item.trial, 0)} · step ${formatNumber(item.step, 0)}<br>${escapeHtml(item.metric)} ${formatNumber(item.value?.[1], 1)}`;
      }},
      dataZoom: metrics.map((_, index) => ({
        type: "inside", yAxisIndex: index, filterMode: "none", throttle: 35,
        zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false,
      })),
      grid, xAxis, yAxis, series,
    };
  }
}

export class EChartsRoiRenderer extends EChartRenderer {
  option() {
    const data = this.data;
    const width = Math.max(320, this.host.clientWidth), height = Math.max(420, this.host.clientHeight);
    const cellW = width / 2, cellH = (height - 42) / 2;
    const titlesText = ["Fraction reaching", "Residence seconds / trial", "Time to first reach", "Heading error"];
    const grid = [], xAxis = [], yAxis = [], titles = [], series = [];
    for (let index = 0; index < 4; index += 1) {
      const col = index % 2, row = Math.floor(index / 2);
      grid.push({left: col * cellW + 54, top: 44 + row * cellH + 36, width: Math.max(60, cellW - 78), height: Math.max(70, cellH - 68)});
      xAxis.push({gridIndex: index, type: "category", data: ["Left", "Right"], ...axisBase()});
      yAxis.push({gridIndex: index, type: "value", min: index === 0 ? 0 : (index === 3 ? -180 : null), max: index === 0 ? 1 : (index === 3 ? 180 : null), scale: index > 0, ...axisBase()});
      titles.push({text: titlesText[index], left: col * cellW + 54, top: 44 + row * cellH + 8, textStyle: {fontSize: 11, fontWeight: 650, color: INK}});
    }
    for (let animal = 0; animal < (data.animalNames?.length || 0); animal += 1) {
      const name = data.animalNames[animal], color = animalColor(animal);
      const time = [], error = [];
      for (let i = 0; i < data.animalCode.length; i += 1) if (data.animalCode[i] === animal) {
        const panel = data.panelNames[data.animalPanel[i]];
        series.push({
          name, type: "line", xAxisIndex: 0, yAxisIndex: 0,
          data: [
            {value: ["Left", data.leftFraction[i]], animal: name, panel, metric: "Fraction reaching"},
            {value: ["Right", data.rightFraction[i]], animal: name, panel, metric: "Fraction reaching"},
          ], symbolSize: 5, itemStyle: {color}, lineStyle: {color, opacity: .24, width: .8},
        });
        series.push({
          name, type: "line", xAxisIndex: 1, yAxisIndex: 1,
          data: [
            {value: ["Left", data.leftResidence[i]], animal: name, panel, metric: "Residence seconds / trial"},
            {value: ["Right", data.rightResidence[i]], animal: name, panel, metric: "Residence seconds / trial"},
          ], symbolSize: 5, itemStyle: {color}, lineStyle: {color, opacity: .24, width: .8},
        });
      }
      for (let i = 0; i < data.timeValues.length; i += 1) if (data.timeAnimals[i] === animal) time.push({value: [data.timeSides[i] ? "Right" : "Left", data.timeValues[i]], animal: name, panel: data.panelNames[data.timePanels[i]], trial: data.timeTrials[i], metric: "Time to first reach"});
      for (let i = 0; i < data.errorValues.length; i += 1) if (data.errorAnimals[i] === animal) error.push({value: [data.errorSides[i] ? "Right" : "Left", data.errorValues[i]], animal: name, panel: data.panelNames[data.errorPanels[i]], trial: data.errorTrials[i], metric: "Heading error"});
      const common = {name, itemStyle: {color}, lineStyle: {color, opacity: .25, width: .8}, symbolSize: 5};
      if (time.length) series.push({...common, type: "scatter", xAxisIndex: 2, yAxisIndex: 2, data: time, symbolSize: 4, itemStyle: {color, opacity: .38}});
      if (error.length) series.push({...common, type: "scatter", xAxisIndex: 3, yAxisIndex: 3, data: error, symbolSize: 3, large: error.length > 3000, itemStyle: {color, opacity: .28}});
    }
    return {
      ...this.base("roi-outcomes"), title: titles,
      legend: {type: "scroll", left: 12, right: 100, top: 8, selected: selectedMap(data, this.animalVisibility), textStyle: {fontSize: 10, color: MUTED}, itemWidth: 14, itemHeight: 7},
      tooltip: {trigger: "item", confine: true, formatter: params => {
        const item = params.data || {}, trial = Number.isFinite(item.trial) ? `<br>Trial ${formatNumber(item.trial, 0)}` : "";
        return `<b>${escapeHtml(item.animal)}</b> · ${escapeHtml(item.panel)}${trial}<br>${escapeHtml(item.metric)} · ${escapeHtml(item.value?.[0])}: ${formatNumber(item.value?.[1], 1)}`;
      }},
      grid, xAxis, yAxis, series,
    };
  }
}

export class EChartsHistogramRenderer extends EChartRenderer {
  constructor(host, name = "distribution") { super(host); this.name = name; }
  option() {
    const data = this.data, bars = [];
    for (let i = 0; i < data.counts.length; i += 1) bars.push({value: [(data.edges[i] + data.edges[i + 1]) / 2, data.counts[i]], lo: data.edges[i], hi: data.edges[i + 1]});
    return {
      ...this.base(this.name),
      grid: {left: 52, right: 22, top: 34, bottom: 48},
      tooltip: {trigger: "item", confine: true, formatter: params => `${formatNumber(params.data.lo, 1)} – ${formatNumber(params.data.hi, 1)}<br><b>${formatNumber(params.data.value[1], 0)}</b> samples`},
      xAxis: {type: "value", ...axisBase()}, yAxis: {type: "value", ...axisBase()},
      dataZoom: [{type: "inside", xAxisIndex: 0, filterMode: "none"}, {type: "slider", xAxisIndex: 0, bottom: 7, height: 15}],
      series: [{type: "bar", data: bars, barWidth: "98%", itemStyle: {color: "rgba(49,95,140,.72)", borderRadius: [2, 2, 0, 0]}, large: true}],
    };
  }
}
