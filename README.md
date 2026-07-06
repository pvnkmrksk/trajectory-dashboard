# Trajectory Dashboard

An interactive web dashboard (Dash + Plotly) for exploring VR insect-trajectory
experiments. Point it at a folder of CSVs — or drag the folder onto the page —
and it pools, filters, animates, and density-maps 2-D trajectories, fast, on
millions of rows.

> **New here / an AI agent?** Read **[ARCHITECTURE.md](ARCHITECTURE.md)** — it has
> the data model, file map, callback graph, the non-obvious rendering gotchas,
> known issues, and how to verify changes. Don't scan all ~4k lines.

## Quick Start With uv

This repo is intentionally simple: it is a single Dash app (`app.py`) plus
static browser assets. There is no package build step. Use `uv` to create a
clean virtual environment and install exactly what `requirements.txt` declares.

Install `uv` first — copy the block for your OS:

**macOS / Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Optional macOS alternative: `brew install uv`

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Restart your shell if the installer asks you to, then verify:

```bash
uv --version
```

Clone and set up the repo (same on macOS, Linux, and Windows):

```bash
git clone https://github.com/pvnkmrksk/trajectory-dashboard.git
cd trajectory-dashboard
uv python install 3.10
uv venv --python 3.10
uv pip install -r requirements.txt
```

Known-good local runtime is Python 3.10; `uv python install` fetches it if needed.

Run the dashboard:

```bash
uv run python app.py
```

Open `http://127.0.0.1:8050/`.

Common run commands:

```bash
# Pre-load a folder/glob at startup
uv run python app.py --glob "/path/to/Data/**/*_VR*.csv"

# Use a different local port
uv run python app.py --port 8051

# Listen on the LAN instead of localhost
uv run python app.py --host 0.0.0.0 --port 8050

# Debug mode while developing callbacks
uv run python app.py --debug
```

Maintenance commands:

```bash
# Reinstall after dependency changes
uv pip install -r requirements.txt

# Confirm the app still imports/compiles
uv run python -c "import py_compile; py_compile.compile('app.py', doraise=True)"

# Update uv itself if installed with the standalone installer
uv self update
```

Official uv docs: https://docs.astral.sh/uv/

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

## CLI Arguments

`app.py` accepts a few startup flags. Most analysis settings live in the UI and
shareable URL.

| Argument | Example | What it does | Rationale |
|---|---|---|---|
| `--glob` | `--glob "Data/**/*_VR*.csv"` | Preloads matching CSVs when the server starts. | Saves a manual load step for repeated sessions or demos. |
| `--port` | `--port 8051` | Changes the Dash port. | Useful when another dashboard is already on `8050`. |
| `--host` | `--host 0.0.0.0` | Changes the bind host. | Use `127.0.0.1` for local-only, `0.0.0.0` to view from another machine on the network. |
| `--debug` | `--debug` | Enables Dash/Flask debug behavior. | Helpful while editing callbacks; avoid for regular data review. |

## Controls And Parameters

### Loading

| Control | Meaning | Rationale |
|---|---|---|
| Glob / folder path | A file glob, folder, or dropped folder. Dropped folders are expanded into nested CSV globs. | Keeps loading flexible: paste an exact experiment glob or just drop the top-level folder. |
| Load & Plot | Loads CSVs, metadata, filter choices, auto thresholds, and the first visible plot. | Separates potentially expensive disk IO from lighter parameter changes. |
| Drag-drop target | The whole page becomes receptive during a drag event. | Faster than finding the text box when exploring local folders. |

### Grouping And Layout

| Control | Meaning | Rationale |
|---|---|---|
| Group By | Subplot split: config/treatment, scene, VR, fly, source folder, or all pooled. | Lets you move between treatment-level comparison and individual-level debugging. |
| Pool Mode | Separate subplots or one pooled subplot. | Separate is better for comparison; pooled is better for quick global density/shape checks. |
| Plot order | Drag the loaded config list. | Keeps figures aligned to experimental order instead of arbitrary filename order. |
| Subplot cols | Number of columns in the grid. | Wide screens can use 2-4 columns; narrow screens are easier with 1. |
| Show raw config filenames | Uses exact config filenames instead of readable labels. | Debugs metadata/name mapping when labels look surprising. |

### Trajectories

| Control | Meaning | Rationale |
|---|---|---|
| Colour By | Individual, VR, trial, local time, or smoothed velocity. | Categorical colors identify animals/runs; sequential colors reveal progression or speed structure. |
| Playback animation | Builds animated frames and shows play/pause/scrub controls. | Good for presentations and temporal intuition; off is faster and crisper for analysis. |
| Start each track at origin | Rebases each segment to `(0, 0)`. | Compares path shape independent of absolute arena position. ROI overlays are hidden when rebased because target coordinates no longer match. |
| Max plot points | Optional decimation budget. | Larger values preserve detail but increase browser cost; blank uses the app's safe default. |

### Filters

| Control | Meaning | Rationale |
|---|---|---|
| Max velocity (units/s) | Removes samples whose instantaneous velocity exceeds this threshold. Auto uses the 99th percentile. | Cuts teleport/reset spikes without hand-tuning every dataset. Units are raw position units per second, not cm/s. |
| Extra trim around speed spikes (ms) | Removes a time buffer on both sides of each velocity spike. | A single bad jump can contaminate neighboring samples; the buffer removes the small temporal halo around it. |
| Min displacement | Removes whole segments whose start-to-end displacement is below this value. Auto uses 5% of median segment displacement. | Drops trials where the animal effectively did not move. |
| Edge trim samples (Advanced) | Removes N samples from both ends of every segment after spike filtering. | Legacy blunt instrument for start/end artifacts; normally leave at `0` and prefer the time-based spike buffer. |
| Histogram range selections | Drag-select velocity/displacement histogram ranges. | Quick exploratory subset filtering without typing exact cutoffs. |
| Exclusion line | Reports the actual active velocity, displacement, trim, and moving-sample criteria. | Makes it obvious which filters are truly enabled and how many points/trials they remove. |

### Heatmap

| Control | Meaning | Rationale |
|---|---|---|
| Bin size (units) | Width/height of each square heatmap bin. Blank chooses a data-scaled default. | Smaller bins show detail but can get sparse/noisy; larger bins show stable occupancy fields. |
| Bound % | Clips the plotted extent to the central percentile of X/Z positions. | Prevents rare excursions/spikes from making the useful arena tiny. Use `100` for the full extent. |
| Scale | Linear or log color scaling. | Linear emphasizes dense regions; log reveals low-occupancy structure. |
| Metric | Occupancy seconds, percent of time, or sample count. | Seconds are intuitive within a subplot, percent compares across unequal trial counts, count is the rawest diagnostic. |
| cmin / cmax | Color limits. Blank auto-scales. | Fix limits across views when comparing treatments or exporting. |
| cmin/cmax as value or percentile | Interpret color limits literally or as data percentiles. | Percentiles are convenient when the absolute range changes by dataset. |

### ROI / Targets

| Control | Meaning | Rationale |
|---|---|---|
| Show target ROIs + reached counts | Adds target rings and L/R reached counts to trajectories; heatmaps get faint rings and corner occupancy labels. | Keeps target context visible without baking it into the trajectory traces. |
| Reach radius (units) | Distance from target center counted as entering/reaching. | Lets you tune strict vs forgiving target contact. |
| Only trials that entered an ROI | Shows only segments that reached either left or right ROI. | Focuses plots on successful/target-engaged behavior. Trajectory denominators change because whole trials are filtered. |
| Trim trial tail after ROI exit | Keeps approach and first contact, then drops samples after the first post-ROI exit. | Focuses heatmaps/trajectories on approach/interaction instead of post-choice wandering. Trial-level reached counts usually do not change because the trial still reached. |

### ROI Tab

| Panel | Meaning | Rationale |
|---|---|---|
| Fraction reaching | Per-animal paired swarm of left vs right reached fraction; hover shows reached/trials. | Detects lateral bias and per-animal variability without hiding sample size. |
| Residence time | Per-animal paired swarm of seconds/trial inside each ROI. | Distinguishes merely touching a target from spending time there. |
| Time to reach | Split violin by side; area scales with the number of reached trials. Median and IQR are line overlays. | Shows latency distribution while preserving the left/right split. |
| Heading error | Split violin of instantaneous heading minus target bearing, wrapped to `[-180, 180]`. | `0 deg` means pointing at the target at that sample; left/right are computed separately, including inferred missing-side references. |

### Polar

| Control | Meaning | Rationale |
|---|---|---|
| Colour by | Mean velocity or tortuosity per trial. | Velocity highlights fast directed runs; tortuosity highlights winding vs straight paths. |
| Moving samples only | Uses only samples above the walk-speed threshold. | Prevents stationary jitter from dominating heading vectors. |
| Walk speed threshold (units/s) | Minimum smoothed speed for the moving-only polar mode. | Tune this to the dataset's speed scale. |

### Diagnostics And Export

| Control | Meaning | Rationale |
|---|---|---|
| Diagnostics tab | Velocity/displacement histograms and optional raw time-series columns. | Debugs filters, spikes, and unexpected raw sensor columns. |
| Raw trace cols | Numeric columns to plot over time. Defaults to none. | Avoids needless GameObject position time-series overhead unless you explicitly need it. |
| Export HTML | Writes a self-contained dashboard snapshot. | Useful for sharing a fixed analysis state without a running Dash server. |

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
