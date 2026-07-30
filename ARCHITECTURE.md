# Architecture & context — Trajectory Dashboard

> One-stop context for a developer or coding agent. The dashboard shell and
> Plotly figure builders live in `app.py`; reusable Dash-free loading, filtering,
> and grouping live under `trajectory_dashboard/`. A sibling `Plotting/dashboard.py`
> exists but currently lags this repo; treat this repo's `app.py` plus
> `trajectory_dashboard/` as source of truth unless explicitly asked to sync the
> copy.

---

## 1. What it is

An interactive Dash + Plotly web app for exploring **VR insect-trajectory
experiments**. You point it at a folder of CSVs and it pools, filters, animates,
and density-maps 2-D trajectories — fast, on millions of rows.

Stack in this environment: **Dash 4.2, Plotly 6.8, pandas 2, numpy**. (Dash 4 /
Plotly 6 matter — see the rendering gotchas in §7.)

The importable library entry points are `trajectory_dashboard.load_dataset`,
`FilterSpec`, `filter_frame`, and `group_frames`. They do not import Dash or
Plotly.

---

## 2. Data model (the one thing to internalise)

- Input CSVs have columns: `Current Time, CurrentTrial, CurrentStep,
  GameObjectPosX, GameObjectPosZ` (X/Z is the ground plane; **not** Y), plus
  optional rotation/sensor columns.
- Sibling JSON is auto-detected per folder: `*_ControlScene_sequenceConfig.json`
  maps `CurrentStep → ConfigFile` (the treatment); `*FlyMetaData.json` maps
  `VR → FlyID/Sex`. VR labels are normalized from filename/CSV text
  (`VR2 Cube` → `VR2`). If fly metadata is absent, an existing CSV FlyID-like
  column is preserved; if neither exists, `FlyID` falls back to a stable
  `session:VR` label so grouping by fly/individual does not collapse into a
  single `unknown` bucket.
- Scene grouping resolves row labels in priority order:
  `SceneName → CurrentSequenceScene → Scene → CurrentScene → SequenceScene`.
  Blank/placeholder labels fall through. The chosen labels are then reduced to
  one modal `SceneName` per `_seg_id`, because one CurrentStep must never be
  split across scene panels by a stale Unity transition frame. This raw-column
  path is the normal fallback when sibling JSON metadata is absent.
- **A _segment_ is the atomic unit:** `_seg_id = SourceFile + CurrentTrial +
  CurrentStep`, built **after** numeric coercion from the **integer** trial/step.
  Everything groups/filters by this. Two gotchas, both real bugs: (a) never key on
  `(Trial, Step)` alone — different files reuse the numbers; keying on `SourceFile`
  also keeps a crash+restart CSV distinct. (b) The raw trial/step text mixes int
  and float (`"0"` vs `"0.0"`) within one file, so building the id with
  `.astype(str)` on the pre-coercion values split one trial into two ids that
  interleaved after the time-sort and inflated every per-trial count ~5×. Coerce
  first, format as int. (Animal identity — `FlyID@VR` — is a *separate* grouping
  that intentionally merges files.)
- `TrialIndex` is a derived 1-based per-`SourceFile` ordinal over contiguous
  `_seg_id` segments after the load-time sort. It is internal/helper metadata;
  the dashboard's trial-range control, trial colour mode, and
  `FilterSpec.trial_range` and `FilterSpec.step_range` use the dataset's raw
  numeric `CurrentTrial`/`CurrentStep` values and keep complete `_seg_id`
  segments.
- **Velocity is in raw position-units per second, NOT cm/s.** The trajectory
  colour, native velocity histogram, and per-segment peak/median statistics all
  use the same 10-frame within-segment smoothed series after reset-spikes above
  the 99.5th percentile are removed. The peak-range UI is displayed through the
  99th percentile, with optional unbounded exact inputs.

---

## 3. File map (top to bottom)

| Lines (~) | Section | Key functions |
|---|---|---|
| package | **Reusable pipeline** | `trajectory_dashboard.io.load_dataset`, `trajectory_dashboard.filters`, `trajectory_dashboard.grouping.FilterSpec`. |
| 28-390 | **Config + ROI geometry** | `humanise_config`, ROI extraction, readable config LUT. |
| 395-670 | **ROI tables/masks + CSV loader bridge** | `roi_reached_table`, `time_to_target_table`, `heading_target_angle_table`, `_roi_masks`, `_roi_apply`, `load_csv_fast`. |
| 763-947 | **Filtering bridge / stats** | Compatibility wrappers; canonical implementations live in `trajectory_dashboard.filters`. |
| 948-3410 | **Plotting** | `_prepare_merged_groups`, `build_trajectory_figure`, heatmap builders + variants, explicit-bin histograms, raw trace, ROI panels, circular/polar statistics, and grouped trial metrics. |
| 3141-3980 | **Dash app, caches + layout** | `app`, data/filter/ROI/polar caches, sidebar controls, and seven continuously mounted scroll sections. |
| 3981-end | **Callbacks + clientside interaction** | URL/load state, the atomic all-section renderer, viewport sync, LUT, export, playback and guards. |

Assets (Dash auto-serves `/assets`):
- `assets/dashboard.css` — dashboard chrome, tabs, buttons, drop target, and
  workspace styling.
- `assets/dropzone.js` — folder drag-and-drop → `set_props('drop-data', …)`.
- `assets/heatsync.js` — re-attaches a relayout→`viewport-store` handler after the heatmap is `newPlot`-ed (Dash's own listener is lost on newPlot).
- `assets/heatmap_colors.js` — applies metric/scale variants and color-limit
  restyles directly to the mounted heatmap from the binned cell distribution.
- `assets/plot_wheel_guard.js` — prevents page/panel scroll while the pointer is
  over Plotly's central wheel-zoom plane; margins still scroll normally.
- `assets/config_order.js` — drag-to-reorder the visible values of the active
  config/scene/VR/fly/folder panel axis via `panel-order-store`.
- `assets/shared_legend.js` — shares categorical layer visibility between the
  trajectory and polar figures and reports counts for the currently visible
  layers.
- `assets/loop_observer.js` — browser-local circle/polyline intersection and
  first-entry path splitting for the movable curtain-ring trajectory observer.
  The same module is inlined into offline exports.
- `assets/region_observer.js` — editable rectangular window overlays for the
  trajectory, heatmap and Gandiva graphs; shape edits update the compact region
  store, not the master renderer.
- `assets/clean_layout.js` — one-pass browser-only publication styling:
  title-only spatial panels with zoom-aware 1/2/5 scale bars, despined
  statistical panels, clean polar axes, and exact Full-layout restoration.

---

## 4. The processing pipeline

```
glob / dropped folder
   └─ trajectory_dashboard.io.find_csv_files → load_csv_fast (per file)
      → smoothed velocity + exact segment stats → endpoint-safe retain/downcast
      → concat retained frames → sort ONCE by time
      └─ _load_data(pattern)                         cached in _DATA_CACHE
         └─ _filtered_df(...)                        cached in _FILTER_CACHE (last 4)
            ├─ trajectory_dashboard.grouping.subset_frame + trial/histogram range selections
            └─ trajectory_dashboard.filters.apply_filters
               velocity-jump (time-buffered), min-displacement, trim
               └─ _roi_apply(...)                    cached masks in _ROI_MASK_CACHE
                  ├─ analytical build_* figures → dcc.Graph / figure stores
                  └─ _sample_trajectory_segments(...) (drawing only)
                     └─ trajectory figure → browser-local loop observer
```

**Everything downstream assumes the load-time time-sort** and uses
`groupby(..., sort=False)`. Do not re-sort per segment (that was the original
perf killer).

The dashboard retains at most `TRAJ_LOAD_ROW_BUDGET` normalized rows across the
matched files (default 2,000,000; `0` opts into retaining all rows). Quotas are
proportional to source-file byte size and preserve every segment's endpoints.
Only one complete source file is resident during preprocessing. Exact
per-segment point counts, displacement, and smoothed velocity summaries are
finalized before sampling; spatial bins, ROI sample masks, and circular panels
operate on the retained frame.

`apply_filters` is fully vectorised: the velocity-jump buffer is a
`np.searchsorted` "dilation" (`_dilate_keep`), displacement/trim are groupby
transforms. This took a 3.8M-row replot from ~30 s to ~4 s.

`_jump_buffer_seconds` keeps old URL values like `jb=0.1` compatible with the
current millisecond UI (`100`), and `_filter_signature` normalises both to the
same cache key.

---

## 5. Rendering model & tuning knobs

- **Trace count, not point count, drives Plotly render cost.** Segments sharing
  a colour collapse into ONE NaN-separated trace per (subplot, colour) via
  `_prepare_merged_groups` (vectorised). ~100 traces instead of ~4000.
- **The plot workspace is one mounted document.** Trajectory, heatmap, local
  direction field, diagnostics, target and polar figures stay in normal layout flow. The top
  navigation only scrolls `.td-main`; it never hides graphs or asks the server
  to rebuild a tab. This preserves pan/zoom, hover, legends and WebGL contexts.
- **Decimation budgets** (`_decimation_budget` / build): static WebGL
  `BUDGET_GL=300k`; animated `BUDGET_SVG=40k` (every frame is embedded in the
  figure JSON — Plotly cannot stream frames, so the budget is the payload lever);
  raw plot `BUDGET_RAW=25k`. "Point budget" (Advanced) overrides.
  Speed is the default and applies a second browser drawing budget. Both modes
  share the same retained analytical frame; file-level segment statistics were
  finalized before load-time sampling.
- **Whole-trial drawing sample**: `traj-trial-fraction` is applied by
  `_sample_trajectory_segments` after analytical/ROI filtering but before point
  decimation. It keeps complete `_seg_id` values using a stable seeded random
  choice; `btn-traj-resample.n_clicks` is the seed. Trajectories, the loop
  observer and polar use the same sample. Heatmap, Gandiva, ROI, trial-metric
  and raw analytical inputs keep the complete filtered frame.
- **Curtain-ring observer**: the main trajectory figure is also the browser
  source for `assets/loop_observer.js`; no duplicate trajectory payload or
  server callback is needed when the ring moves. The asset scans the
  NaN-separated traces by the `_seg_id` stored in `customdata[6]`, tests both
  vertices and polyline/circle intersections, and emits a few merged WebGL
  traces per source trace: muted pre-entry, saturated post-entry, and entry
  diamonds. Multiple editable Plotly rings can be added/deleted and combined
  with Any/All matching. In All mode the future begins at the latest first hit,
  i.e. the moment every ring has been satisfied.
  Matching therefore has the spatial fidelity of the active rendered point
  budget; Accuracy or a larger point budget is appropriate for tiny rings.
- **Observation windows**: `_normalise_custom_regions`,
  `_custom_region_subset` and `_custom_region_stats` use vectorised X/Z masks.
  The main renderer applies the union only to polar rows and returns the small
  diagnostics/panel-share payload. Subsequent shape edits call
  `update_custom_region_analysis`, which rebuilds only polar and the
  observation-window table; `assets/region_observer.js` repaints rectangles and
  Gandiva percentage labels without rebuilding direction vectors.
- **Colour modes** (`color_by`): the UI intentionally exposes only
  `categorical` (default: one muted hue per current panel) and `none` (neutral
  translucent gray). Older URL values are restored as `categorical` so obsolete
  modes cannot leave the dropdown in an invalid hidden state.
- **Layout**: 2-col grid, `SUBPLOT_PX=480` per subplot → the figure is its
  natural full height and the panel scrolls (no squishing). Subplot vertical
  spacing is deliberately tight so Plotly drag rectangles are easy to hit. 1:1
  aspect on trajectories via `scaleanchor` (see §7 for why the heatmap can't use
  it). The optional comparison workspace places trajectory and polar sections
  side-by-side, with heatmap and diagnostics full-width below.
  `minimal-layout-store` is presentation-only state: `assets/clean_layout.js`
  performs one debounced in-place styling pass. Spatial figures lose all axis
  chrome and gain a 1/2/5 scale bar; Cartesian diagnostics keep labels/ticks but
  use left/bottom despined axes; polar grids are removed. Legends and trace
  colourbars are hidden. `spatial_layout.unit_scale` maps one position unit to
  `unit_label` (default `1 cm`). No dataframe or figure builder runs.
- **Heatmap**: `build_heatmap_figure` bins X/Z with `np.histogram2d`.
  `bin_size` is in **data units** (blank → `default_bin_size` ≈ 1/20 of the
  95th-pct extent); `bound_pct` clips the extent to a central percentile;
  `metric ∈ {count, time=count×median_dt seconds, percent}`; `log_scale` with
  human tick labels (`_log_colorbar`/`_fmt_metric`); `cmin/cmax` blank→auto,
  absolute or `crange_mode="percentile"`; occupancy floored at 100 ms. When ROIs
  are available and paths are not rebased, the heatmap overlays faint target
  rings and puts left/right ROI occupancy labels in each subplot's top corners
  using the active metric; metric/scale swaps restyle those labels clientside
  from the variant store. Per-side ROI heatmap labels use a boolean union of
  samples hit by any same-side ROI, not a sum over ROI centers, so percentages
  cannot exceed 100% under pooled/overlapping target states.
- **Gandiva local direction field**: `build_direction_field_figure` uses the heatmap's
  shared spatial grid and computes one circular mean from valid heading samples
  per occupied cell. Hue/stroke angle encode mean direction, colour
  saturation/stroke length encode resultant `R`, and raster alpha/stroke
  visibility encode abundance using the heatmap's active metric, linear/log
  scale, and value/percentile range semantics. The colour field is a tiny
  in-memory RGBA PNG attached as a Plotly layout image; headless strokes are merged into
  five abundance-tier `Scattergl` traces per subplot, avoiding one trace or
  shape per cell. The persisted maximum radius control spans 0.05–0.98 cell
  widths; changes rescale the mounted NaN-joined strokes clientside using the
  prior radius, without recomputing vector statistics. Top/right SVG marginals
  use four subdivisions per heatmap bin. Dotted X/Z cuts use the densest shared
  segment-start cell and annotate each quadrant's spatial-sample percentage. Body
  orientation and within-`_seg_id` movement heading share the polar convention.
  Direction-source and moving-only controls refresh this field and polar;
  heatmap metric/scale/range controls refresh only the field, without rebuilding
  trajectories or occupancy bins. A CSS circular wheel and opacity/width key
  provide the direction and abundance legends.
- **Delayed inference**: `stats-delay-interval` arms after a completed visual
  render. A separate callback uses SciPy Mann–Whitney/Kruskal–Wallis tests with
  Holm correction for the four movement metrics, pooled-centred circular ranks
  for polar groups, and Rayleigh uniformity for per-config start angles. A
  clientside relayout appends the labels/stars to already-mounted plots.
- **Style JSON**: Advanced exposes `_VISUAL_STYLE_DEFAULTS` with
  `group_labels` first (config, scene, VR, fly, source folder), then core
  trajectory/spatial/ring/region/Gandiva/heatmap sections and finally
  per-series overrides. `_deep_merge` preserves shipped keys and still reads
  legacy `categories` objects.
- **Diagnostics**: velocity/displacement histograms are native load-time
  distributions and deliberately do not follow interactive filters. The
  starting-heading null diagnostic takes the first sorted sample of every
  `_seg_id` and renders 36 fixed 10-degree `Barpolar` sectors per treatment.
  Sidebar mini-histograms and diagnostic histograms are server-aggregated into
  explicit bounded bar bins; no multi-million-value arrays are sent to the
  browser for Plotly auto-binning. The raw
  trace graph remains mounted for callback wiring but its wrapper is hidden
  until raw columns are selected.
- **Trial metrics**: `build_trial_metrics_figure` selects the exact pre-retention
  segment summaries for currently visible `_seg_id` values and groups them by
  the same panel axis. It shows path length, net displacement, median smoothed
  speed, and the median 15-sample local path/chord ratio. Up to 200 trials per
  group render as deterministic jittered points; larger groups render as
  count-scaled violins. Both encodings share a full-width IQR band and median
  line overlay.
  The starting-heading diagnostic uses 36 fixed sectors with edges
  `[-5°, 5°], [5°, 15°], …`, so cardinal 0° is a bin centre.
- **Trajectory ROI labels**: corner labels are exclusive first-reached outcome
  counts (`L-first`, `R-first`) over the visible ROI-capable trials in that
  subplot. Do not switch them back to independent reached-left/reached-right
  counts; trials can visit both sides and the labels would sum past 100%.
- **ROI tab**: one figure with four synced-x panels: per-animal fraction
  reaching left/right (hover includes reached/trials), per-animal residence time
  inside each ROI, time-to-target split violins, and instantaneous heading-error
  split violins. The violins have explicit median/IQR line overlays, not native
  boxes. Heading error is each sample's movement heading minus the bearing from
  that same sample to the left/right target centre; missing sides use inferred
  centres from the loaded config set.
- **Polar**: each trial vector is the circular mean of Unity
  `GameObjectRotY` by default (degrees; 0° = +Z/forward, positive = +X/right).
  Movement heading is an explicit alternative. The bold population vector is
  pooled over all valid samples by weighting each trial resultant by its
  `valid_points`; it is calculated before display thinning, so Speed and
  Accuracy return identical circular statistics.

---

## 6. Callback graph (what talks to what)

- `restore_from_url` (fires **once**, guarded by `url-restored`) ⇄ `update_url`
  (fires on settings/view changes, **not** live pan/zoom). Full bidirectional URL
  state includes the last known viewbox (`vbx0…vby1`) and the current `view`, but
  the viewbox is read as `State` so dragging a plot does not rewrite
  `location.search`. The once-guard breaks the echo loop.
- `on_folder_drop` ← `drop-data` (set by dropzone.js) → `resolve_dropped_folder`
  → glob + auto-load.
- `start_progress`/`tick_progress` poll the unified `_OP_PROGRESS` snapshot
  (works because the dev server is threaded). Load, render, polar-only, and
  export operations publish checklist stages, progress fractions, and timings
  into the one header status bar.
- `load_data_cb` populates filter options, histograms and the smart default bin
  size. `update_range_controls` then applies/reset ranges and increments the
  plot epoch as a load barrier; `update_plots` cannot race the previous
  dataset's slider values. `_load_data` is keyed by the matched
  file list plus mtime/size, so adding files under the same glob invalidates the
  stale dataframe/filter/heatmap signatures. Refreshing the stored automatic
  threshold suggestions does not issue another plot click when both automatic
  cuts are off, so a load produces one master render rather than two identical
  epoch-1 renders.
- ROI reach radius is stored as the unbounded positive `reach=` URL parameter.
  `roi-reach` is the authoritative exact number input; `roi-reach-slider` is a
  0.5–100 convenience view whose handle clamps visually without changing an
  exact value above 100.
- The loop state persists as `loop=`, `lx=`, `lz=`, and `lr=`. The displayed
  whole-trial percentage persists as `tf=`. Ring movement updates these small
  controls/URL state, but the geometry scan and redraw remain browser-local.
- Observation windows persist as `region=`, `regions=` and `ractive=`; clean
  presentation state persists as `minimal=`. Both restore alongside the plot
  controls without forcing viewport interaction through the server.
- Peak velocity's robust slider can be overridden by unbounded exact min/max
  inputs; those values persist as `vrmin=`/`vrmax=`. Workspace mode persists as
  `layout=sections|compare`.
- `render_config_order_list`/`apply_config_order` expose all loaded configs as a
  draggable order list. The default order uses the sequenceConfig with the best
  coverage; missing configs remain alphabetic at the bottom.
- `update_plots` takes one filtered snapshot and returns trajectory, heatmap
  store/variants, local direction field, target diagnostics, custom-window
  diagnostics/shares, polar, trial metrics, raw traces, summary and render
  state atomically. Retired
  split-view/lazy callbacks are not registered. `update_polar_only` owns
  direction-source/moving/R/quality changes and all three polar mini-histograms;
  moving/source changes also refresh the local direction field, while heatmap
  metric/scale/range changes refresh only that field. It reuses the
  filtered-frame and Rayleigh caches rather than triggering the master renderer.
  Heatmap-colour distributions are derived
  from the already-computed bin matrices and sent as a small sorted sample;
  value/percentile changes update only `zmin`, `zmax`, and colorbar ticks in
  `assets/heatmap_colors.js`, without a dataframe pass or server render.
- `_filtered_df` normalizes jump-buffer units for cache keys (`100` ms and old
  `0.1` second URLs share a signature). `_roi_masks` caches reached table,
  entered segment ids, and trim masks for fast ROI toggles.
- The master renderer writes heatmap JSON to `heatmap-figure-store`, not to
  `heatmap-plot.figure`, so Dash's `Plotly.react` path never applies the heatmap
  subplot figure. Metric/scale variants still update clientside without
  re-binning. The local direction field similarly writes to
  `flow-figure-store`; its RGBA layout images plus subplot scale lock trigger the
  same Plotly-6 axis-scaling failure through `Plotly.react`, so a clientside
  `Plotly.newPlot` paints each rebuilt field.
- asset-level viewport sync — `assets/heatsync.js` attaches directly to Plotly
  `plotly_relayout`, immediately relayouts the peer spatial graph, and writes
  `viewport-store` only after an idle delay.
  The plots' `relayoutData` props are NOT Dash callback Inputs. This keeps live
  pan/wheel gestures out of Dash's callback scheduler and out of the URL-update
  loop. The master renderer applies a validated stored range to both spatial
  figures; the heatmap accepts only close, overlapping ranges so a stale URL
  viewbox cannot make the binned heatmap a tiny island inside a mostly blank
  plot.
- `view-mode` is navigation state only. A clientside callback scrolls the main
  container to the requested section while the sticky section bar stays visible;
  `assets/section_nav.js` also handles clicks on the already-active tab. No graph
  style, figure or server callback depends on a section switch.
- `update_filter_summary` reports final retained points/trials/animals and a
  serial per-criterion retention audit. It is triggered from `view-render-state`
  after a view finishes rendering, not directly from `btn-plot`, so the audit
  does not race the focused plot callback. Each stage's percentage is relative
  to the previous stage, mirroring the actual filter pipeline.
- `export_html` rebuilds figures server-side and emits one self-contained file.
  Plotly is embedded once (no CDN dependency). It includes trajectories,
  an interactive curtain-ring observer, heatmap, local direction field, polar,
  target diagnostics, trial metrics, native velocity/displacement and
  starting-heading diagnostics, and selected raw traces.
- The header `status-dock` mirrors load/filter/render/export state and uses
  Dash's body loading class for immediate Working/Ready feedback. Its hover text
  exposes the latest stage timings. Python logging records load, cache, polar,
  render and export timings; Dash's `on_error` hook writes uncaught
  callback exceptions with full tracebacks to the server terminal.

### Trigger contract

Keep this split tight; it is what prevents tiny datasets from feeling glitchy:

| Control / event | What it may update | What it must not update |
|---|---|---|
| Load / dropped folder | Load/cache data, options and metadata; reset range controls on a changed source; render once after that barrier | Stale prior-dataset ranges; URL from pan/zoom |
| Update all plots (`btn-plot`) | Build every mounted section from one filtered state | Competing per-section builders; direct heatmap `dcc.Graph.figure` |
| Heatmap bin/bound or data filter | Debounced all-section update; heatmap store + variants are built exactly | Concurrent heatmap sidebar aggregation |
| Heatmap metric/scale/color range mode/value | Clientside heatmap `Plotly.restyle` from current binning variants; rebuild the local direction field from the cached filtered frame so abundance matches | Dataframe refiltering, heatmap rebinning or master-section rebuild |
| ROI entered/trim | Debounced atomic update of all affected sections | A second ROI/trajectory refresh callback |
| Displayed-trial fraction / resample | Rebuild trajectory and polar drawings from one whole-`_seg_id` sample; analytical spatial/ROI/metric panels retain the complete filtered frame | Row-level random sampling; changes to target/metric denominators |
| Loop add/delete/select/match, centre/radius or ring drag | Browser-local multi-circle intersection, qualifying-entry split and observer redraw; persist small ring-set state in the URL | Dataframe filtering, master render, heatmap/polar/ROI changes |
| Observation-window add/delete/select/bounds or box drag | Rebuild polar + custom diagnostics from cached filtered rows; repaint rectangles and Gandiva percentages in the browser | Trajectory, heatmap or Gandiva-vector recomputation; master render |
| Clean layout | Browser-local relayout, adaptive scale bars and URL state | Figure/data rebuilds or viewport callback traffic |
| Gandiva maximum radius | Browser-local scaling of existing arrow tips and URL state | Direction recomputation, dataframe filtering or another figure build |
| Trajectory/heatmap pan/zoom | Immediate clientside peer relayout plus debounced `viewport-store` after idle | URL writes, server rebuilds, Dash `relayoutData` callbacks, live-patching hidden graphs |
| Section navigation | Clientside scroll only, including replay of the active tab | Any server render, graph hide/show or Plotly reinitialisation |
| ROI reach/show | Debounced atomic update | Competing overlay/ROI callbacks |
| Polar direction source/moving controls | Gandiva + cached polar figure and quality histograms; delayed inferential stats follow the visual render | Master trajectory/heatmap/ROI/raw rebuild |
| Polar R/quality controls | Cached polar figure + three quality histograms only | Direction field or master trajectory/heatmap/ROI/raw rebuild |

---

## 7. Hard-won rendering gotchas (do not "simplify" these away)

These cost a very long debugging session; each is confirmed via Chrome CDP
(`--remote-debugging-port=9222 --remote-allow-origins=*` + a websocket
`Runtime.evaluate` — see §9). The `claude-in-chrome` MCP was unavailable.

1. **2-D numpy arrays don't round-trip through Dash + Plotly 6.**
   `go.Heatmap(z=<2-D numpy>)` serialises with Plotly-6's typed-array (`bdata`)
   encoding, which Dash does **not** decode for 2-D — `z` arrives `undefined` in
   the browser and the heatmap is blank. **Fix: pass `z`/`customdata`/`x`/`y` as
   plain Python lists (`.tolist()`).** 1-D arrays (scattergl x/y) are fine.

2. **The heatmap and local direction field crash `Plotly.react` (Dash's update
   path).** With a subplot grid, applying a new heatmap or layout-image field to
   a graph that isn't full-size yet throws
   *"Something went wrong with axis scaling"* in `setScale`, and it then never
   repaints. It happens **even without `scaleanchor`** (it's the subplot axis
   layout at a bad size). A fresh `Plotly.newPlot` re-initialises cleanly. **Fix:
   the server writes fresh figures to `heatmap-figure-store` /
   `flow-figure-store`; clientside callbacks re-run `Plotly.newPlot` with the
   fresh data and layout.** Do not restore server Outputs to either graph's
   `figure`: even when the panel is
   visible, Dash's `Plotly.react` path can throw the axis-scaling error before
   the clientside newPlot gets a chance to recover.
   - **The newPlot is fingerprint-guarded (do not revert to unconditional).**
     It only re-initialises when figure content changes. Section navigation does
     not touch the graph, so an unchanged heatmap retains interaction state and
     incurs zero render work.

3. **Never return to hidden plot panels.** WebGL graphs created at zero/hidden
   size may never paint, and resizing a hidden aspect-locked graph can emit a
   bogus relayout that poisons the shared viewport. The single-page layout is the
   fix: every plot section remains in normal flow at a measurable width. The raw
   trace wrapper alone may hide because it is empty until columns are selected.

4. **Polar stays SVG and its arrays stay plain lists.**
   - Use **SVG `go.Scatterpolar`, not `Scatterpolargl`.** WebGL polar crashes on
     re-render (`Cannot read properties of undefined (reading '_scene')`), so the
     polar uses SVG with a tighter point budget (`BUDGET_POLAR`).
   - **Pass `r`/`theta`/`marker.color` as plain Python lists** (`.tolist()`), same
     reason as the heatmap `z` (§7.1): Plotly-6 encodes numpy as typed-array
     `bdata` that arrives empty through the clientside newPlot.
   - Because polar is born visible in the mounted document, Dash's normal
     `Plotly.react` path now updates it reliably; do not add a second newPlot or
     resize path.

**Coordinate convention (ROIs + polar).** Unity is left-handed: objects at polar
`(radius, angle°)` sit at `X = r·sin(angle)`, `Z = r·cos(angle)` (0° = forward/+Z
= top of screen). Headings/polar use `theta = atan2(dx, dz)` so 0° = forward too,
and the polar axis is `rotation=90, direction="clockwise"` — so the ROI overlay,
the reached counts, and the polar all agree. Left ROI ⇔ X<0, right ⇔ X>0.

---

## 8. Known issues / glitches / limitations

- **Heatmap "flash" on rebuild — largely resolved.** The heatmap re-inits only on
  a real *binning* change (bin size/bound or filter); opacity stays
  stable during the guarded `newPlot`, and section navigation does no plot work.
  **Metric/scale swaps
  are instant, in-place, flash-free:** every metric×scale variant is precomputed
  at bin time (`build_heatmap_and_variants` → `heatmap-variants` store, ~0.7 MB)
  and the clientside `Plotly.restyle`s z/customdata/zmin/zmax/colorbar — no server
  round-trip, no newPlot. Metric, scale and color limits are therefore NOT
  master-renderer inputs; the fingerprint tracks binning only (no zmin/zmax).
  *Cleanest future fix remains:* a `Plotly.react`-safe subplot state that drops
  the newPlot/heatsync machinery entirely.
- **Heatmap→trajectory zoom sync depends on `assets/heatsync.js`.** newPlot drops
  Dash's relayout listener; the asset re-attaches one that writes `viewport-store`
  via `set_props`. If you refactor the heatmap rendering, keep or drop this in
  tandem.
- **Playback frames use `Scattergl` and are re-drawn client-side.** On very large
  animated selections the embedded frames make the figure JSON heavy (~tens of
  MB). Animation auto-uses the tighter `BUDGET_SVG`; still, prefer "Playback off"
  for the biggest datasets or lower "Point budget".
- **Drag-drop can only resolve folders under searched local roots.** Browsers
  don't expose absolute paths; `resolve_dropped_folder` searches the working dir,
  nearby ancestors, and optional `TRAJ_DATA_ROOT`. Data elsewhere -> type/paste a
  path. Drop handling is scoped to the folder control and plot workspace, and
  ignores internal drags so active-panel ordering remains reliable.
- **Two copies of the code** (`Plotting/dashboard.py` and `trajectory-dashboard/
  app.py`) can drift. Decide on one source of truth.
- **`raw-columns` default** doesn't always stick in the control; `update_plots`
  defaults it to `[GameObjectPosX, GameObjectPosZ]` so the raw plot isn't empty.
- Filter cache holds up to 4 views of the retained frame
  (`_FILTER_CACHE_MAX`). Lower it or `TRAJ_LOAD_ROW_BUDGET` on
  memory-constrained systems.

---

## 9. How to verify UI changes

Server-side callbacks: `import app; app._load_data("<glob>")` then call the
builder/callback directly and assert on the returned figure. For anything that
only shows up in the browser (rendering, clientside, drag-drop), use the
available browser plugin or drive Chrome over the DevTools Protocol:

```bash
Google\ Chrome --remote-debugging-port=9222 "--remote-allow-origins=*" \
  --user-data-dir=/tmp/cdpchrome "http://127.0.0.1:PORT/?glob=..."
# then websocket to the page's webSocketDebuggerUrl and Runtime.evaluate JS,
# e.g. count g.querySelectorAll('image').length on #heatmap-plot, read
# location.search, or push figures via window.dash_clientside.set_props(...).
```

Always confirm `GET /_dash-dependencies` returns **200** after editing callbacks
(catches duplicate-output / missing-id errors). New persisted controls must be
added to BOTH `update_url` and `restore_from_url` (keep the return arity in
sync); secondary Outputs need `allow_duplicate=True`. Run with
`--log-level DEBUG` when investigating cache invalidation or callback ordering;
normal load/render/export milestones are already present at `INFO`.

See [HANDOFF.md](HANDOFF.md) for the current SmallSubScale smoke test and the
browser checks that caught the recent Plotly drag/pan regression.

---

## 10. Scope for improvement (nice-to-haves, roughly ordered)

1. Remove the heatmap `newPlot` workaround (see §8) for flash-free updates.
2. Background/long callback for loading (the current per-file loader is
   memory-bounded and reports live threaded-global progress, but still occupies
   a request worker).
3. Server-side figure/HTML caching for exports and repeat views.
4. Persist the config LUT to disk so renames survive restarts.
5. Downsample-on-zoom (send more points only for the visible window) instead of a
   fixed global budget.
6. Unit tests around `apply_filters`, `_prepare_merged_groups`, and
   `resolve_dropped_folder`; a smoke test that boots the app and checks
   `/_dash-dependencies`.
7. Optional hexbin/KDE heatmap; per-subplot colour ranges.
8. Consolidate to a single source file / package instead of two copies.
