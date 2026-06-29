# Trajectory Dashboard

An interactive web dashboard for exploring VR insect-trajectory experiments
(Dash + Plotly). Point it at a folder of CSVs and it pools, filters, animates,
and density-maps trajectories — fast, even on millions of rows.

![status](https://img.shields.io/badge/status-research%20tool-blue)

## Quick start

```bash
pip install -r requirements.txt
python app.py                                   # then open http://127.0.0.1:8050
python app.py --glob "data/2025*/*_VR*.csv"     # pre-load a pattern
python app.py --port 8051 --host 0.0.0.0
```

Or just **drag a data folder onto the drop zone** in the sidebar — it finds every
nested CSV and builds the glob for you.

## What it does

- **Load** by glob pattern, a folder path, or drag-and-drop a folder. Recurses
  `**`, reads `sequenceConfig.json` (config per step) and `FlyMetaData.json`
  (VR → fly id / sex) for meaningful labels. Live "loading N/M files" progress.
- **Pool / group** by config (treatment), scene, VR, fly id, source folder, or
  all-pooled — into a 2-column grid of square, axis-synced subplots.
- **Colour by** individual, VR, trial (sequential), local time (sequential), or
  **velocity** (units/s, rolling-smoothed with reset-spikes removed).
- **Filters**: max-velocity jump removal (time-buffered), min net displacement,
  trim N samples per segment end. Plus interactive range-selection on the
  velocity / displacement histograms.
- **Playback**: native client-side animation with a sticky play / pause / scrub
  bar that reveals each track from its first point over local time.
- **Heatmap**: occupancy density with bin size in *data units*, lin/log colour,
  human-readable log tick labels (100 ms / 1 s / 10 s), percentile-bounded
  extent, and explicit `cmin`/`cmax` (absolute or percentile). Trajectory and
  heatmap share one linked viewport.
- **Diagnostics tab**: velocity + displacement histograms and raw time-series.
- **Shareable URL**: every control *and the current zoom box* is encoded in the
  URL — paste it to a colleague and they see exactly your view.
- **Export**: a single self-contained `.html` with all panels and real data
  embedded (rebuilt server-side, never an empty shell).

## Data assumptions

CSV columns required: `Current Time`, `CurrentTrial`, `CurrentStep`,
`GameObjectPosX`, `GameObjectPosZ` (X/Z is the ground plane). Optional metadata
JSON in the same folder is auto-detected. A **segment** is one
`(SourceFolder, VR, CurrentTrial, CurrentStep)` — the unit everything groups by.

> **Units note:** velocity is in *position units per second*, not cm/s. Values
> can be large; histograms cap at the 99th percentile and the velocity colour
> mode drops reset-spikes above the 99.5th percentile before smoothing.

## Performance

Designed for 1M+ rows. The replot pipeline is fully vectorised (no per-segment
Python loops); a 3.8M-row dataset filters + builds in ~4 s. The single biggest
lever is **trace count** — segments sharing a colour are merged into one
NaN-separated WebGL trace, so the browser draws ~100 traces instead of ~4000.
See [ARCHITECTURE.md](ARCHITECTURE.md) for the why.

## Layout

```
app.py              # the whole application (single file, sectioned)
assets/dropzone.js  # folder drag-and-drop (Dash auto-serves /assets)
requirements.txt
ARCHITECTURE.md     # structure + invariants for humans and coding agents
```
