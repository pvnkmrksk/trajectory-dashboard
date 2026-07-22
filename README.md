# Trajectory Dashboard

An interactive web dashboard (Dash + Plotly) for exploring VR insect-trajectory
experiments. Point it at a folder of CSVs — or drag the folder onto the page —
and it pools, filters, animates, and density-maps 2-D trajectories, fast, on
millions of rows.

> **New here / an AI agent?** Read **[ARCHITECTURE.md](ARCHITECTURE.md)** — it has
> the data model, file map, callback graph, the non-obvious rendering gotchas,
> known issues, and how to verify changes. Don't scan all ~6k lines.

## Quick Start With uv

This repo has a thin Dash shell (`app.py`) plus a reusable, Dash-free
`trajectory_dashboard` package for loading, filtering, and grouping trajectory
data. There is no package build step for local use. Use `uv` to create a clean
virtual environment and install exactly what `requirements.txt` declares.

### Install uv

**macOS / Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
```

Optional macOS alternative: `brew install uv`

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
$env:Path = "$HOME\.local\bin;$env:Path"
```

### Install dashboard

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

# Include cache hits and other detailed diagnostics in the terminal
uv run python app.py --log-level DEBUG
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

## Use The Pipeline Without Dash

The preprocessing path is importable as a small library. It handles CSV
discovery, tolerant JSON metadata loading, segment ID normalization, vectorized
quality filters, histogram-range filters, and grouping. You can use it for a
plain script or notebook without starting the dashboard.

```python
from trajectory_dashboard import FilterSpec, filter_frame, group_frames, load_dataset

dataset = load_dataset("/path/to/Data/**/*_VR*.csv")
df = dataset.frame          # row-level samples, sorted with contiguous _seg_id
stats = dataset.stats       # one row per segment
metadata = dataset.metadata # sequenceConfig, fly metadata, and scene configs

spec = FilterSpec(
    vel_threshold=2500,       # raw position units / second
    min_displacement=2.0,
    jump_buffer_ms=100,
    trial_range=(0, 40),      # inclusive CurrentTrial window
    configs=("Choice_Push.json",),
)
filtered = filter_frame(df, spec).filtered

for name, group_df in group_frames(filtered, group_by="config").items():
    print(name, len(group_df), group_df["_seg_id"].nunique())
```

A minimal Matplotlib plot:

```python
import matplotlib.pyplot as plt
from trajectory_dashboard import FilterSpec, filter_frame, group_frames, load_dataset

data = load_dataset("/path/to/Data/**/*_VR*.csv")
filtered = filter_frame(data.frame, FilterSpec(jump_buffer_ms=100)).filtered

fig, ax = plt.subplots()
for seg_id, seg in next(iter(group_frames(filtered, "all").values())).groupby("_seg_id", sort=False):
    ax.plot(seg["GameObjectPosX"], seg["GameObjectPosZ"], alpha=0.25, lw=0.8)
ax.set_aspect("equal")
ax.set_xlabel("X")
ax.set_ylabel("Z")
plt.show()
```

Important invariants stay the same outside the dashboard: `_seg_id` is the
atomic trial/step segment key, and velocity is in raw position units per second.

For a quick preprocessing check on the homing enemy data, run
`python scripts/smoke_homing_enemy.py --trial-min 0 --trial-max 1`.

## Features

- **Load** by glob, folder path, or **drag-and-drop a folder** (finds every
  nested CSV and builds the glob). Reads `sequenceConfig.json` / `FlyMetaData.json`
  for readable subplot titles. Live "loading N/M files" progress.
- **Pool / group** by config (treatment), scene, VR, fly, source folder, or
  all-pooled → a 2-col grid of square, axis-synced, scrollable subplots.
- **Colour by** individual, VR, ROI outcome, trial, local time, or **velocity**
  (units/s, rolling-smoothed, reset-spikes removed).
- **Filters**: max-velocity jump removal (time-buffered), min net displacement,
  trim N edge samples/end, ROI entered-only, and after-exit ROI trim. Velocity
  and displacement have auto defaults; the top line reports final retained
  points/trials/animals and the sidebar shows serial retained/discarded counts
  per criterion. Drag-select ranges on the velocity/displacement histograms.
- **Playback**: native client-side animation with a sticky play/pause/scrub bar;
  each track grows from its first point over local time.
- **Single-page plotting workspace**: trajectories, heatmap, diagnostics,
  targets and polar stay mounted together. The sticky section bar scrolls to a
  plot without hiding/reloading graphs, so zoom, hover and legend state survive.
  Speed is the default and only thins browser drawing primitives; analytical
  counts and statistics remain exact.
- **Heatmap**: occupancy density — bin size in **data units**, lin/log with
  human-readable log labels (100 ms / 1 s / 10 s), percentile-bounded extent,
  metric = count / occupancy-seconds / % of time, explicit `cmin/cmax`
  (absolute or percentile), and faint ROI rings with left/right occupancy labels
  in each subplot's top corners. Zoom stays linked with the trajectory.
- **ROI targets** auto-loaded from the scene configs (Choice/BinaryChoice; polar
  `{radius,angle}` or cartesian `{x,y,z}`, Unity left-handed). Adjustable **reach
  radius** slider, reach circles + per-subplot exclusive first-reached
  **L-first/R-first counts** overlaid on the trajectories, and an optional
  **tail-trim** that drops each
  trial's path after it first leaves an ROI it entered.
- **ROI counts view**: per-animal fraction reaching left/right with reached/trial
  hover counts, per-animal ROI residence time, split violins for time-to-target,
  and split violins for instantaneous heading error to left/right targets.
  Median/IQR are drawn as simple line overlays, not violin boxes.
- **Polar view**: one circular resultant per trial from Unity body orientation
  (`GameObjectRotY`) by default, with movement heading as an alternative. 0° is
  forward/+Z and positive angles turn right/+X. The bold population ray exactly
  pools all valid samples and is independent of display thinning.
- **Diagnostics section**: explicit-bin velocity/displacement histograms and
  optional raw time-series.
- **Live activity dock**: a fixed bottom-left status card shows loading,
  debounced updates, render completion and export completion. Detailed Python
  errors and tracebacks are written to the server terminal with timestamps,
  thread names and operation context.
- **Shareable URL**: every control *and the current zoom box* is in the URL.
- **Export**: one offline, self-contained `.html` with Plotly, every panel and
  the filtered data embedded.

## CLI Arguments

`app.py` accepts a few startup flags. Most analysis settings live in the UI and
shareable URL.

| Argument | Example | What it does | Rationale |
|---|---|---|---|
| `--glob` | `--glob "Data/**/*_VR*.csv"` | Preloads matching CSVs when the server starts. | Saves a manual load step for repeated sessions or demos. |
| `--port` | `--port 8051` | Changes the Dash port. | Useful when another dashboard is already on `8050`. |
| `--host` | `--host 0.0.0.0` | Changes the bind host. | Use `127.0.0.1` for local-only, `0.0.0.0` to view from another machine on the network. |
| `--debug` | `--debug` | Enables Dash/Flask debug behavior. | Helpful while editing callbacks; avoid for regular data review. |
| `--log-level` | `--log-level DEBUG` | Selects `DEBUG`, `INFO`, `WARNING`, or `ERROR` terminal output. | `INFO` records load/render/export timing; `DEBUG` also exposes cache reuse and request-level detail. |

## Controls And Parameters

### Loading

| Control | Meaning | Rationale |
|---|---|---|
| Glob / folder path | A file glob, folder, or dropped folder. Dropped folders are expanded into nested CSV globs. | Keeps loading flexible: paste an exact experiment glob or just drop the top-level folder. |
| Load | Loads CSVs, metadata, filter choices and auto thresholds, resets range controls when the data source changes, then renders all sections once. | Prevents new data from racing stale ranges from the previous source. |
| Drag-drop target | Drop folders on the folder control or the plotting workspace. | Keeps data loading easy without intercepting the config-order drag list. |

### Grouping And Layout

| Control | Meaning | Rationale |
|---|---|---|
| Panels | Subplot split: config/treatment, scene, VR, fly, source folder, or all pooled. | Lets you move between treatment-level comparison and individual-level debugging. |
| Pool Mode | Separate subplots or one pooled subplot. | Separate is better for comparison; pooled is better for quick global density/shape checks. |
| Plot order | Drag the loaded config list. | Keeps figures aligned to experimental order instead of arbitrary filename order. |
| Panel columns | Number of columns in the grid. | Wide screens can use 2-4 columns; narrow screens are easier with 1. |
| Show raw config filenames | Uses exact config filenames instead of readable labels. | Debugs metadata/name mapping when labels look surprising. |

### Trajectories

| Control | Meaning | Rationale |
|---|---|---|
| Colour | Individual, VR, trial, local time, or smoothed velocity. | Categorical colors identify animals/runs; sequential colors reveal progression or speed structure. |
| Render mode | Speed (default) or Accuracy. | Speed reduces only the number of browser drawing primitives; both modes use full filtered data for bins, counts and statistics. |
| Playback animation | Builds animated frames and shows play/pause/scrub controls. | Good for presentations and temporal intuition; off is faster and crisper for analysis. |
| Point budget | Optional decimation budget. | Larger values preserve detail but increase browser cost; blank uses the app's safe default. |

### Filters

| Control | Meaning | Rationale |
|---|---|---|
| Max velocity (units/s) | Removes samples whose instantaneous velocity exceeds this threshold. Auto uses the 99th percentile. | Cuts teleport/reset spikes without hand-tuning every dataset. Units are raw position units per second, not cm/s. |
| Extra trim around speed spikes (ms) | Removes a time buffer on both sides of each velocity spike. | A single bad jump can contaminate neighboring samples; the buffer removes the small temporal halo around it. |
| Min displacement | Removes whole segments whose start-to-end displacement is below this value. Auto uses 5% of median segment displacement. | Drops trials where the animal effectively did not move. |
| Trial range | Inclusive `CurrentTrial` min/max fields in the Subset section. | Splits early vs late trials without changing segment identity or writing a separate preprocessing script. |
| Trim segment edges (Advanced) | Removes N samples from both ends of every segment after spike filtering. | Blunt instrument for start/end artifacts; normally leave at `0` and prefer the time-based spike buffer. |
| Histogram range selections | Drag-select velocity/displacement histogram ranges. | Quick exploratory subset filtering without typing exact cutoffs. |
| Retention summary | Reports final retained/discarded points, trials, and animals. The sidebar audit shows each criterion serially, relative to the previous step. | Makes active filters auditable without mixing independent and sequential denominators. |

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
| Show target ROIs + reached counts | Adds target rings and exclusive first-reached L/R counts to trajectories; heatmaps get faint rings and corner occupancy labels. | Keeps target context visible without baking it into the trajectory traces while avoiding double-counted trials. |
| Reach radius (units) | Distance from target center counted as entering/reaching. | Lets you tune strict vs forgiving target contact. |
| Only trials that entered an ROI | Shows only segments that reached either left or right ROI. | Focuses plots on successful/target-engaged behavior. Trajectory denominators change because whole trials are filtered. |
| Trim trial tail after ROI exit | Keeps approach and first contact, then drops samples after the first post-ROI exit. | Focuses heatmaps/trajectories on approach/interaction instead of post-choice wandering. Trial-level reached counts usually do not change because the trial still reached. |
| Trajectory colour: ROI outcome | Colours each segment by first reached side: left ROI, right ROI, or no ROI. | Highlights target outcome while preserving the merged-trace renderer. |

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
| Angle source | Body orientation (`GameObjectRotY`, degrees) or movement heading from consecutive X/Z samples. | Separates where the animal faced from where it moved; body orientation is the default analysis variable. |
| Rayleigh R range | Filters trial resultants by circular concentration from 0 (dispersed) to 1 (aligned). | Excludes poorly directed trials without changing the meaning of angle. |
| Valid-point / good-trial fractions | Trial and animal quality gates. | Makes missing/filtered heading coverage explicit. |
| Colour by | Individual/VR/ROI or a sequential trial metric. | Preserves the same identity/sequence encoding used by trajectories. |
| Moving samples only | Uses only samples above the walk-speed threshold. | Prevents stationary jitter from dominating heading vectors. |
| Walk speed threshold (units/s) | Minimum smoothed speed for the moving-only polar mode. | Tune this to the dataset's speed scale. |

### Diagnostics And Export

| Control | Meaning | Rationale |
|---|---|---|
| Diagnostics section | Velocity/displacement histograms and optional raw time-series columns. The raw trace panel stays hidden until columns are selected. | Debugs filters, spikes, and unexpected raw sensor columns without showing an empty plot. |
| Raw trace columns | Numeric columns to plot over time. Defaults to none. | Avoids needless GameObject position time-series overhead unless you explicitly need it. |
| Export HTML | Writes an offline dashboard snapshot including trajectories, heatmap, target diagnostics, polar, velocity/displacement diagnostics, and selected raw traces. The first figure embeds Plotly once; later figures reuse it. | Useful for sharing a fixed analysis state without a running Dash server or internet connection. |
| Activity dock | Remains fixed at the bottom-left and reports the current load, render, debounce, or export state plus the last completed render. | Makes slow work and failures visible without losing the current plot section. |

## Data assumptions

CSV columns required: `Current Time, CurrentTrial, CurrentStep, GameObjectPosX,
GameObjectPosZ` (X/Z is the ground plane). A **segment** =
`SourceFile + CurrentTrial + CurrentStep`, built after numeric coercion of
trial/step. That is the unit everything groups by; never regroup by trial/step
alone. Velocity is in **position units/second**, not cm/s (values are large).

## Layout

```
app.py                        # Dash shell, layout, callbacks, Plotly figures
trajectory_dashboard/io.py     # CSV discovery, config/metadata loading
trajectory_dashboard/filters.py # velocity, segment stats, vectorized filters
trajectory_dashboard/grouping.py # subset filters and group splitting
assets/dropzone.js             # folder drag-and-drop
assets/dashboard.css           # dashboard chrome and sticky section styling
assets/heatsync.js             # heatmap zoom viewport sync after newPlot
assets/plot_wheel_guard.js     # Plotly wheel zoom without page scroll
assets/config_order.js         # draggable config subplot order list
requirements.txt
ARCHITECTURE.md                # deep context for humans and coding agents
AGENTS.md                      # short agent entry point
HANDOFF.md                     # latest state, verification recipe, and safe next work
```

## Notes / limitations

See ARCHITECTURE.md §8 for the full list. Highlights: heatmap rendering still
uses a guarded clientside `Plotly.newPlot` workaround for Dash/Plotly-6 subplot
issues, drag-drop can only resolve folders under searched local roots, and large
animated selections make a heavy figure (prefer "Playback off" or lower "Max
plot points").
