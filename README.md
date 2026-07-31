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
    step_range=(1, 3),        # inclusive CurrentStep window
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
  for readable subplot titles. When scene metadata is absent, grouping falls
  back to the CSV's `CurrentSequenceScene`, then `Scene`; placeholder values are
  ignored and each `_seg_id` is assigned one stable modal scene. Each file is
  normalized and summarized before endpoint-safe retention, so large folders
  never accumulate every raw CSV in RAM. A dropped folder is acknowledged
  immediately, its inferred glob appears before parsing begins, and the header
  reports file/stage progress while a small parallel worker pool preprocesses
  source files in their original order.
- **Pool / group** by config (treatment), scene, VR, fly, source folder, or
  all-pooled → a 2-col grid of square, axis-synced, scrollable subplots.
- **Colour by** the current panel categories (default), neutral gray (“None”),
  individual, config, scene, VR, source folder, ROI outcome, trial, local time,
  smoothed velocity, or time-smoothed tortuosity. Categorical modes use
  distinct but deliberately muted, translucent hues for dense overplotting.
- **Filters**: max-velocity jump removal (time-buffered), min net displacement,
  inclusive trial and step ranges, trim N edge samples/end, ROI entered-only,
  and after-exit ROI trim. Velocity
  and displacement have auto defaults; the top line reports final retained
  points/trials/animals and the sidebar shows serial retained/discarded counts
  per criterion. Drag-select ranges on the velocity/displacement histograms.
- **Playback**: native client-side animation with a sticky play/pause/scrub bar;
  each track grows from its first point over local time.
- **Curtain-ring loop observer**: add, select, drag and delete circular probes
  in trajectory space, then match trials that crossed **any** or **all** rings.
  The browser immediately keeps only matching displayed `_seg_id` paths and
  shows the muted past, saturated future, and first/last qualifying entry point.
  Ring edits do not refilter data or call the server.
- **Observation windows**: add, select, drag, resize and delete rectangular
  regions on Trajectory, Heatmap or Gandiva. Gandiva labels the sample share in
  each window; polar and trial metrics use the union of window samples; the
  diagnostics show per-trial or per-animal distributions of sample occupancy,
  entry, distance walked, net displacement, tortuosity and velocity. Window
  edits refresh only those dependent analyses.
- **Whole-trial display sampling**: instantly hide/show a deterministic random
  1–100% of complete `_seg_id` segments in the mounted browser plots, with a
  button for a fresh sample. The same selected trials feed trajectories, the
  loop observer, polar vectors and the visible polar population ray; heatmaps,
  Gandiva, targets and movement-metric denominators keep the complete filtered
  frame. The browser retains an immutable copy of the complete mounted drawing,
  so moving the percentage upward restores trials as quickly as moving it down.
- **Single-page plotting workspace**: trajectories, heatmap, Gandiva, polar,
  targets and diagnostics stay mounted together. The sticky section bar scrolls to a
  plot without hiding/reloading graphs, so zoom, hover and legend state survive.
  An optional comparison workspace puts trajectories and polar side-by-side,
  with the heatmap below. Speed is the default and adds a tighter browser
  drawing budget; both modes share the same memory-bounded retained frame.
- **Clean layout**: one passive browser-only button prepares uncluttered Plotly
  downloads without rebuilding data or touching the viewport. Spatial axes,
  Cartesian grids/zero-lines, legends and colourbars disappear; polar rings
  and angular ticks remain for context. Each spatial panel receives a passive
  scale bar whose numeric conversion and unit label are editable (default:
  `1 data unit = 1 cm`). The implementation only toggles CSS classes and
  lightweight DOM overlays, so pan, zoom and editable shapes keep their native
  Plotly behavior.
- **Heatmap**: occupancy density — bin size in **data units**, lin/log with
  plain log labels (`1`, `10`, `100`, `1,000`, never `1e+3`),
  percentile-bounded extent,
  metric = count / occupancy-seconds / % of time, explicit `cmin/cmax`
  (absolute or percentile), and faint ROI rings with left/right occupancy labels
  in each subplot's top corners. Heatmap and trajectory start from the same
  central-98% square extent, and pan/zoom propagate immediately in both
  directions without a server render.
- **Transition probability**: an optional heatmap-grid observer conditions each
  cell on the unique trials that ever entered it, then colours it by the
  percentage that later crossed the horizontal split or ended on its opposite
  side. The automatic split is the modal starting-Z bin edge; it can also be
  entered exactly. Cells below a configurable trial count stay blank rather
  than implying zero probability. Both outcomes are calculated together, so
  their switch is immediate. Click a cell to reveal only its successful
  displayed trajectories, split into muted pre-entry and saturated future
  paths, with exact numerator/denominator counts on hover.
- **Gandiva plot**: a quiver/heatmap hybrid named for Arjuna's divine bow on the same selectable
  spatial grid. Each cell is a circular summary of its samples: stroke angle and
  hue show mean direction, stroke length and colour saturation show resultant
  strength `R` (`0` scattered → `1` aligned), and stroke visibility/width plus
  raster alpha show abundance using the heatmap's active count/time/percent
  metric, linear/log scale, and colour-range semantics. A compact circular
  colour wheel identifies direction, while a faint-to-bold stroke key explains
  abundance. The maximum stroke radius is adjustable from 0.05–0.98 cell
  widths and rescales existing browser traces without recomputing vectors.
  Aligned top/right marginals use bins 4× finer than the heatmap. A dotted
  modal-start cut reports the percentage of spatial samples in each quadrant.
  It can use Unity body orientation or movement
  heading, supports moving-only samples, follows every active data/ROI filter,
  and shares live pan/zoom with the trajectory and occupancy views.
- **ROI targets** auto-loaded from the scene configs (Choice/BinaryChoice; polar
  `{radius,angle}` or cartesian `{x,y,z}`, Unity left-handed). Adjustable **reach
  radius** slider, reach circles + per-subplot exclusive first-reached
  **L-first/R-first counts** overlaid on the trajectories, and an optional
  **tail-trim** that drops each
  trial's path after it first leaves an ROI it entered.
  Configs with no physical target entries inherit the modal target geometry
  found across the loaded files.
- **ROI counts view**: per-animal fraction reaching left/right with reached/trial
  hover counts, per-animal ROI residence time, split violins for time-to-target,
  and split violins for instantaneous heading error to left/right targets.
  Median/IQR are drawn as simple line overlays, not violin boxes.
- **Trial metrics view**: distance walked, net displacement, median smoothed
  velocity, and median local time-windowed path/chord tortuosity across the current
  panel grouping. Auto uses one encoding across all panels: deterministic
  swarms when the largest group has at most 200 observations, otherwise
  count-scaled violins. Explicit Swarm or Violin is always honored, and small
  violins can retain their dots. The independent unit can be each trial or an
  animal mean. Both encodings show a full-width shaded IQR with a bold median.
- **Delayed statistics**: plots render first, then a separate callback adds
  SciPy non-parametric pairwise tests with Holm-adjusted compact-letter labels
  directly above trial/window/target distributions. Full methods, sample sizes
  and adjusted probabilities remain available on hover instead of occupying
  plot titles. Polar puts each group's Rayleigh non-uniformity stars and
  pairwise compact letter in the padded subplot subtitle; the starting-angle
  diagnostic retains its per-group Rayleigh check there as well.
- **Polar view**: one circular resultant per trial from Unity body orientation
  (`GameObjectRotY`) by default, with movement heading as an alternative. 0° is
  forward/+Z and positive angles turn right/+X. In Trial mode the bold
  population ray exactly pools the valid samples in the currently displayed
  trial subset. In Animal mode, each animal's trials first form one
  sample-weighted circular vector and those animal vectors then contribute
  equally to the population ray and circular tests. Moving-only
  and polar-quality changes use a cached polar-only update path; their R,
  valid-point and per-animal good-trial histograms use 36 fixed bins and stay
  mounted and auditable. The angle-source and moving-only controls are shared
  with the local direction field. Each subplot title reports
  retained/available trials.
- **Diagnostics section**: load-time native velocity/displacement histograms,
  an optional 36-bin polar null distribution of the first body heading in every
  segment (10° sectors centered on 0°, 10°, …), and optional raw time-series.
  Native distributions do not change
  when analysis filters change.
- **Live activity status**: a compact header status shows loading, the active
  filter/render operation, retained-point summary and export completion. Hover
  it for a completed/current checklist, progress and per-stage timings. Detailed Python
  errors and tracebacks are written to the server terminal with timestamps,
  thread names and operation context.
- **Visual style JSON**: Advanced exposes a prefilled editable object for the
  current config, scene, VR, fly and folder display names first, followed by
  trajectory opacity/width/palette, clean-layout units, curtain rings,
  observation windows, Gandiva tiers/marginals/raster and heatmap colourscale.
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
| Drag-drop target | Drop folders on the folder control or the plotting workspace; the inferred glob and loading spinner update before file parsing starts. | Gives immediate acknowledgement and keeps loading easy without intercepting the config-order drag list. |

File preprocessing uses two workers by default. Set `TRAJ_LOAD_WORKERS=1` for
minimum peak memory or up to `8` when storage and memory comfortably support
more concurrent source files.

### Grouping And Layout

| Control | Meaning | Rationale |
|---|---|---|
| Panels | Subplot split: config/treatment, scene, VR, fly, source folder, or all pooled. | Lets you move between treatment-level comparison and individual-level debugging. |
| Pool Mode | Separate subplots or one pooled subplot. | Separate is better for comparison; pooled is better for quick global density/shape checks. |
| Plot order | Drag the values of the active config, scene, VR, fly, or folder grouping. The list follows the active filter selection and moves mounted subplot domains without recomputing them. | Keeps every grouped figure aligned to the comparison order you intend. |
| Panel columns | Number of columns in the grid. | Wide screens can use 2-4 columns; narrow screens are easier with 1. |
| Show raw config filenames | Uses exact config filenames instead of readable labels. | Debugs metadata/name mapping when labels look surprising. |
| Clean layout | Hides spatial axes, Cartesian grids/zero-lines, legends and colourbars while retaining polar rings; the button changes to Full layout for exact restoration. | Produces PNG-ready Plotly figures with a CSS-only appearance change and no viewport mutation. |
| Scale-bar conversion / unit | Multiplies data units for the clean-layout scale label and sets its text (default `1`, `cm`). | Keeps the same geometry usable for centimetres, metres, or experiment-specific calibration. |

### Trajectories

| Control | Meaning | Rationale |
|---|---|---|
| Colour | Categorical current panels (default), None/neutral gray, individual/config/scene/VR/folder/ROI categories, or sequential trial/local-time/velocity/tortuosity. | Low-opacity muted hues stay legible under overplotting; the 2-second default tortuosity window reveals sustained curves rather than frame noise. |
| Render mode | Speed (default) or Accuracy. | Speed reduces browser drawing primitives further; both modes use the same retained analytical frame and exact pre-retention segment summaries. |
| Playback animation | Builds animated frames and shows play/pause/scrub controls. | Good for presentations and temporal intuition; off is faster and crisper for analysis. |
| Displayed trials (%) | Browser-locally shows this fraction of complete `_seg_id` paths in trajectory, loop and polar drawings. | Reduces mounted marks by hiding whole trials without server analysis; 100% is the default and keeps everything. |
| New random subset | Changes the browser-local sampling seed at the current displayed-trial percentage. | Lets you check that a visual impression is not peculiar to one random subset without rebuilding plots. |
| Distribution marks | Auto, Swarm, or Violin for every trial/window metric panel together. | Auto uses swarm through 200 observations in the largest group and violin above it; explicit choices are never overridden. |
| Show dots on violins | Overlays observations on violin groups with at most 200 points. | Preserves individual values when they remain legible. |
| Independent unit | Treat each `_seg_id` trial as an observation, or first average trials within animal and active group. | Keeps inferential and plotted units aligned. |
| Point budget | Optional decimation budget. | Larger values preserve detail but increase browser cost; blank uses the app's safe default. |

### Loop Observer

| Control | Meaning | Rationale |
|---|---|---|
| Show curtain-ring observer | Opens a second trajectory view driven entirely by browser-side geometry. | The main trajectory overview stays intact while the observer isolates crossing trials. |
| Add / delete / selected ring | Builds a small set of curtain rings and chooses which exact X/Z/radius fields edit. | Supports sequential or distributed spatial gates without cluttering the overview. |
| Any / all rings | Keeps a trial after its earliest hit of any ring, or after it has hit every ring. | Separates alternate routes from multi-gate passage. |
| Ring X / Z | Sets the ring centre; dragging the gold circle updates both fields. | Makes coarse placement tactile and exact placement reproducible. |
| Radius | Sets the circular crossing radius. The slider spans 0.5–100 and the exact field accepts any positive value. | Supports both small local probes and large arena-scale gates. |
| Past / future paths | Splits every matching segment at its first plotted intersection with the ring. | Muted paths show where trials came from; saturated paths and entry diamonds show what happened afterward. |

Loop matching uses the browser-resident, point-budgeted trajectory polylines and
also tests line-segment/circle intersections, not only retained vertices. Raise
the point budget or use Accuracy mode when a very small ring needs the finest
available spatial fidelity.

### Region Observer

| Control | Meaning | Rationale |
|---|---|---|
| Use observation windows | Draws editable rectangles on all three spatial views and applies their union to polar. | Lets you ask how headings and movement statistics change inside arbitrary arena windows. |
| Add / delete / selected window | Maintains a small named set of rectangular windows. | Makes side-by-side local comparisons easy while keeping deletion explicit. |
| X/Z min/max | Exact reproducible bounds; dragging/resizing any dashed box updates these fields. | Supports tactile exploration and precise repeated analyses. |
| Gandiva labels | Reports each window’s sample percentage per current panel. | Provides immediate spatial prevalence without recomputing local vectors. |
| Observation-window diagnostics | Plots per-trial/per-animal sample percentage, entry, local distance, net displacement, tortuosity and velocity with the shared Swarm/Violin control. | Makes windows and active groups comparable with the same independent-unit and non-parametric semantics as trial metrics. |

### Filters

| Control | Meaning | Rationale |
|---|---|---|
| Max velocity (units/s) | Removes samples whose instantaneous velocity exceeds this threshold. Auto uses the 99th percentile. | Cuts teleport/reset spikes without hand-tuning every dataset. Units are raw position units per second, not cm/s. |
| Extra trim around speed spikes (ms) | Removes a time buffer on both sides of each velocity spike. | A single bad jump can contaminate neighboring samples; the buffer removes the small temporal halo around it. |
| Min displacement | Removes whole segments whose start-to-end displacement is below this value. Auto uses 5% of median segment displacement. | Drops trials where the animal effectively did not move. |
| Trial range | Inclusive `CurrentTrial` min/max fields in the Subset section. | Splits early vs late trials without changing segment identity or writing a separate preprocessing script. |
| Step range | Inclusive `CurrentStep` min/max fields in the Subset section. | Selects repeated scene steps while preserving complete `SourceFile+Trial+Step` segments. |
| Trim segment edges (Advanced) | Removes N samples from both ends of every segment after spike filtering. | Blunt instrument for start/end artifacts; normally leave at `0` and prefer the time-based spike buffer. |
| Histogram range selections | Drag-select velocity/displacement histogram ranges. Both have synchronized, unbounded exact min/max boxes. | Keeps the sliders robust to outliers while still allowing a precise range outside their displayed 99th-percentile spans. |
| Retention summary | Reports final retained/discarded points, trials, and animals. The sidebar audit shows each criterion serially, relative to the previous step. | Makes active filters auditable without mixing independent and sequential denominators. |

### Heatmap

| Control | Meaning | Rationale |
|---|---|---|
| Bin size (units) | Width/height of each square heatmap bin. Blank chooses a data-scaled default. | Smaller bins show detail but can get sparse/noisy; larger bins show stable occupancy fields. |
| Bound % | Clips the plotted extent to the central percentile of X/Z positions. | Prevents rare excursions/spikes from making the useful arena tiny. Use `100` for the full extent. |
| Scale | Linear or log color scaling. | Linear emphasizes dense regions; log reveals low-occupancy structure. |
| Metric | Occupancy seconds, percent of time, or sample count. | Seconds are intuitive within a subplot, percent compares across unequal trial counts, count is the rawest diagnostic. |
| cmin / cmax | Expand “Explicit colour limits” to enter exact limits. Blank auto-scales. | Fix limits across views when comparing treatments or exporting without crowding the normal controls. |
| Color range as value or percentile | Interpret color limits literally or as data percentiles. Percentile is the default at 0–99 and its slider/histogram axis is 0–100. Range changes restyle only `zmin`/`zmax` and the colorbar in the browser. | Percentiles are convenient when the absolute range changes by dataset; changing them does not refilter rows or rebuild unrelated plots. |

### Transition Probability

| Control | Meaning | Rationale |
|---|---|---|
| Enable transition probability | Calculates a separate conditional-probability grid for each active config/scene/VR/fly/folder panel. | Keeps the ordinary occupancy heatmap unchanged and avoids doing the extra trial-level calculation when it is not needed. |
| Crossed later / Ended opposite | Defines success after a trial first enters a cell: any later sample reaches the opposite half, or the trial's final retained sample lies there. | “Crossed” captures temporary excursions; “Ended” is the stricter destination interpretation. Both reuse the same calculation. |
| Horizontal split Z | Blank uses the modal segment-start row, snapped to a heatmap edge; an exact number overrides it. | Gives a reproducible two-side definition while making the common arena midline automatic. |
| Minimum entering trials | Blanks cells whose unique-trial denominator is smaller than this value. | Prevents a one-of-one cell from visually looking as reliable as a densely sampled one. |
| Click a cell | Shows successful currently displayed paths that entered that bin, with past/future split at first entry. | Combines the heatmap overview with curtain-ring-style trajectory diagnosis without a server request. |

Each `_seg_id` contributes at most once to a cell denominator even when it
leaves and revisits that cell. A heatmap row crossed by a manually entered split
is intentionally blank because it does not belong unambiguously to either
half. The exact transition percentage uses the complete filtered trial frame;
the clicked path count follows the current browser-side displayed-trial subset.

### ROI / Targets

| Control | Meaning | Rationale |
|---|---|---|
| Show target ROIs + reached counts | Adds target rings and exclusive first-reached L/R counts to trajectories; heatmaps get faint rings and corner occupancy labels. | Keeps target context visible without baking it into the trajectory traces while avoiding double-counted trials. |
| Reach radius (units) | Distance from target center counted as entering/reaching. The slider spans 0.5–100; the adjacent exact input and `reach=` URL parameter accept any positive value. | Lets you tune strict vs forgiving target contact without silently clipping large arenas. |
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
| Angle source | Body orientation (`GameObjectRotY`, degrees) or movement heading from consecutive X/Z samples. | Separates where the animal faced from where it moved; body orientation is the default analysis variable. |
| Rayleigh R range | Filters trial resultants by circular concentration from 0 (dispersed) to 1 (aligned). | Excludes poorly directed trials without changing the meaning of angle. |
| Valid-point / good-trial fractions | Trial and animal quality gates. | Makes missing/filtered heading coverage explicit. |
| Colour by | Uses the shared Categorical or None trajectory choice. | Keeps trajectory and polar semantics consistent and restrained. |
| Moving samples only | Uses only samples above the walk-speed threshold. | Prevents stationary jitter from dominating heading vectors. |
| Walk speed threshold (units/s) | Minimum smoothed speed for the moving-only polar mode. | Tune this to the dataset's speed scale. |

### Diagnostics And Export

| Control | Meaning | Rationale |
|---|---|---|
| Diagnostics section | Native velocity/displacement histograms, a toggleable 36-bin starting-heading null distribution per treatment, and optional raw time-series columns. The raw trace panel stays hidden until columns are selected. | Preserves the original dataset baseline while filters change and exposes unexpected directional bias at segment starts. |
| Trial metrics section | Per-trial path length, displacement, median smoothed speed, and median time-windowed local tortuosity grouped by the selected panel axis. | Makes treatment/scene/animal differences visible without reducing tortuosity to unstable whole-trial distance divided by displacement. |
| Raw trace columns | Numeric columns to plot over time. Defaults to none. | Avoids needless GameObject position time-series overhead unless you explicitly need it. |
| Export HTML | Writes an offline dashboard snapshot including trajectories, the clickable transition observer when enabled, heatmap, Gandiva, polar, target diagnostics, trial metrics, native velocity/displacement and starting-heading diagnostics, and selected raw traces. The first figure embeds Plotly once; later figures reuse it. | Useful for sharing a fixed analysis state without a running Dash server or internet connection. |
| Header activity status | Reports the current load, filter/render, debounce, or export state plus retained points; hover exposes per-stage timings. | Makes slow work and failures visible, while the terminal retains full errors and tracebacks. |

The dashboard's resident normalized frame defaults to 2,000,000 rows. Set
`TRAJ_LOAD_ROW_BUDGET` before launch to change it (`0` retains every row).
Exact segment point counts, displacement, and smoothed peak/median velocity are
computed before retention; spatial occupancy, ROI sample masks, and circular
views use the retained endpoint-safe sample.

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
assets/heatmap_colors.js       # browser-local metric/scale/color-limit restyles
assets/transition_observer.js  # local outcome switch + clicked-cell trajectories
assets/clean_layout.js         # CSS-only publication-mode class toggle
assets/trial_subset.js         # browser-local whole-segment display sampling
assets/region_observer.js      # draggable rectangular observation windows
assets/section_nav.js          # section scroll, including active-tab replay
assets/plot_wheel_guard.js     # Plotly wheel zoom without page scroll
assets/config_order.js         # draggable active-group subplot order list
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
