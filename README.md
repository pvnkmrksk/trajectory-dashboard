# Trajectory Dashboard

An interactive web dashboard (Dash + Plotly) for exploring VR insect-trajectory
experiments. Point it at a folder of CSVs — or drag the folder onto the page —
and it pools, filters, animates, and density-maps 2-D trajectories, fast, on
millions of rows.

> **New here / an AI agent?** Read **[ARCHITECTURE.md](ARCHITECTURE.md)** — it has
> the data model, file map, callback graph, the non-obvious rendering gotchas,
> known issues, and how to verify changes. Don't scan all ~4k lines.

## Quick start

```bash
pip install -r requirements.txt
python app.py                                 # open http://127.0.0.1:8050
python app.py --glob "Data/2025*/*_VR*.csv"   # pre-load a pattern
python app.py --port 8051 --host 0.0.0.0
```

## Features

- **Load** by glob, folder path, or **drag-and-drop a folder** (finds every
  nested CSV and builds the glob). Reads `sequenceConfig.json` / `FlyMetaData.json`
  for readable subplot titles. Live "loading N/M files" progress.
- **Pool / group** by config (treatment), scene, VR, fly, source folder, or
  all-pooled → a 2-col grid of square, axis-synced, scrollable subplots.
- **Colour by** individual, VR, trial, local time, or **velocity** (units/s,
  rolling-smoothed, reset-spikes removed).
- **Filters**: max-velocity jump removal (time-buffered), min net displacement,
  trim N edge samples/end. Velocity and displacement have auto defaults; the
  active exclusion line reports the actual params in play. Drag-select ranges on
  the velocity/displacement histograms.
- **Playback**: native client-side animation with a sticky play/pause/scrub bar;
  each track grows from its first point over local time. "Start at origin" rebase
  toggle.
- **Heatmap**: occupancy density — bin size in **data units**, lin/log with
  human-readable log labels (100 ms / 1 s / 10 s), percentile-bounded extent,
  metric = count / occupancy-seconds / % of time, explicit `cmin/cmax`
  (absolute or percentile), and faint ROI rings with left/right occupancy labels
  in each subplot's top corners. Zoom stays linked with the trajectory.
- **ROI targets** auto-loaded from the scene configs (Choice/BinaryChoice; polar
  `{radius,angle}` or cartesian `{x,y,z}`, Unity left-handed). Adjustable **reach
  radius** slider, reach circles + per-subplot left/right **reached counts**
  overlaid on the trajectories, and an optional **tail-trim** that drops each
  trial's path after it first leaves an ROI it entered.
- **ROI counts view**: per-animal fraction reaching left/right with reached/trial
  hover counts, per-animal ROI residence time, split violins for time-to-target,
  and split violins for instantaneous heading error to left/right targets.
  Median/IQR are drawn as simple line overlays, not violin boxes.
- **Polar view**: each trial's path as r (distance from origin) vs angle
  (0° = forward, clockwise — same frame as the trajectories and ROIs), coloured
  by instantaneous velocity or local tortuosity, with a moving-only (walk-speed)
  toggle; ROI targets shown as rings.
- **Diagnostics tab**: velocity + displacement histograms and raw time-series.
- **Shareable URL**: every control *and the current zoom box* is in the URL.
- **Export**: one self-contained `.html` with all panels and real data embedded.

## Data assumptions

CSV columns required: `Current Time, CurrentTrial, CurrentStep, GameObjectPosX,
GameObjectPosZ` (X/Z is the ground plane). A **segment** =
`SourceFile + CurrentTrial + CurrentStep`, built after numeric coercion of
trial/step. That is the unit everything groups by; never regroup by trial/step
alone. Velocity is in **position units/second**, not cm/s (values are large).

## Layout

```
app.py               # the whole application (single file, sectioned)
assets/dropzone.js   # folder drag-and-drop
assets/heatsync.js   # heatmap zoom→viewport sync after newPlot
assets/plot_wheel_guard.js # Plotly wheel zoom without page scroll
assets/config_order.js     # draggable config subplot order list
requirements.txt
ARCHITECTURE.md      # deep context for humans and coding agents  ← read this
AGENTS.md            # short agent entry point
HANDOFF.md           # latest state, verification recipe, and safe next work
```

## Notes / limitations

See ARCHITECTURE.md §8 for the full list. Highlights: heatmap rendering still
uses a guarded clientside `Plotly.newPlot` workaround for Dash/Plotly-6 subplot
issues, drag-drop can only resolve folders under searched local roots, and large
animated selections make a heavy figure (prefer "Playback off" or lower "Max
plot points").
