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
- **Velocity is in raw position-units per second, NOT cm/s.** Values are large
  (median ~thousands). Histograms cap at the 99th percentile; the velocity
  colour mode drops reset-spikes above the 99.5th pct before smoothing.

---

## 3. File map (top to bottom)

| Lines (~) | Section | Key functions |
|---|---|---|
| package | **Reusable pipeline** | `trajectory_dashboard.io.load_dataset`, `trajectory_dashboard.filters`, `trajectory_dashboard.grouping.FilterSpec`. |
| 28-390 | **Config + ROI geometry** | `humanise_config`, ROI extraction, readable config LUT. |
| 395-670 | **ROI tables/masks + CSV loader bridge** | `roi_reached_table`, `time_to_target_table`, `heading_target_angle_table`, `_roi_masks`, `_roi_apply`, `load_csv_fast`. |
| 763-947 | **Filtering bridge / stats** | Compatibility wrappers; canonical implementations live in `trajectory_dashboard.filters`. |
| 948-3140 | **Plotting** | `_prepare_merged_groups`, `build_trajectory_figure`, heatmap builders + variants, explicit-bin histograms, raw trace, ROI panels, circular/polar statistics. |
| 3141-3980 | **Dash app, caches + layout** | `app`, data/filter/ROI/polar caches, sidebar controls, and five continuously mounted scroll sections. |
| 3981-end | **Callbacks + clientside interaction** | URL/load state, the atomic all-section renderer, viewport sync, LUT, export, playback and guards. |

Assets (Dash auto-serves `/assets`):
- `assets/dashboard.css` — dashboard chrome, tabs, buttons, drop target, and
  workspace styling.
- `assets/dropzone.js` — folder drag-and-drop → `set_props('drop-data', …)`.
- `assets/heatsync.js` — re-attaches a relayout→`viewport-store` handler after the heatmap is `newPlot`-ed (Dash's own listener is lost on newPlot).
- `assets/plot_wheel_guard.js` — prevents page/panel scroll while the pointer is
  over Plotly's central wheel-zoom plane; margins still scroll normally.
- `assets/config_order.js` — drag-to-reorder the full loaded config subplot order
  via `config-order-store` (independent of active filters).

---

## 4. The processing pipeline

```
glob / dropped folder
   └─ trajectory_dashboard.io.find_csv_files → load_csv_fast (per file)
      → concat → sort ONCE by time
      └─ _load_data(pattern)                         cached in _DATA_CACHE
         └─ _filtered_df(...)                        cached in _FILTER_CACHE (last 4)
            ├─ trajectory_dashboard.grouping.subset_frame + trial/histogram range selections
            └─ trajectory_dashboard.filters.apply_filters
               velocity-jump (time-buffered), min-displacement, trim
               └─ _roi_apply(...)                    cached masks in _ROI_MASK_CACHE
                  └─ build_* figures → dcc.Graph / figure stores
```

**Everything downstream assumes the load-time time-sort** and uses
`groupby(..., sort=False)`. Do not re-sort per segment (that was the original
perf killer).

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
- **The plot workspace is one mounted document.** Trajectory, heatmap,
  diagnostics, target and polar figures stay in normal layout flow. The top
  navigation only scrolls `.td-main`; it never hides graphs or asks the server
  to rebuild a tab. This preserves pan/zoom, hover, legends and WebGL contexts.
- **Decimation budgets** (`_decimation_budget` / build): static WebGL
  `BUDGET_GL=300k`; animated `BUDGET_SVG=40k` (every frame is embedded in the
  figure JSON — Plotly cannot stream frames, so the budget is the payload lever);
  raw plot `BUDGET_RAW=25k`. "Point budget" (Advanced) overrides.
  Speed is the default and reduces browser primitives only. Heatmap bins, ROI
  outcomes, filter histograms and circular statistics always use the complete
  filtered frame in both modes.
- **Colour modes** (`color_by`): `individual`/`vr`/`roi` (categorical, lines,
  legend); `trial`/`local_time`/`velocity` (sequential; markers for per-point
  ones; a hidden anchor trace supplies the Viridis colourbar). ROI outcome is
  computed per segment from the first left/right ROI reached and falls back to
  "No ROI". Velocity is rolling-smoothed (10 frames) and spike-clipped.
- **Layout**: 2-col grid, `SUBPLOT_PX=480` per subplot → the figure is its
  natural full height and the panel scrolls (no squishing). Subplot vertical
  spacing is deliberately tight so Plotly drag rectangles are easy to hit. 1:1
  aspect on trajectories via `scaleanchor` (see §7 for why the heatmap can't use
  it).
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
- **Diagnostics**: velocity/displacement histograms include the full filtered
  data. Sidebar mini-histograms and diagnostic histograms are server-aggregated
  into explicit bounded bar bins; no multi-million-value arrays are sent to the
  browser for Plotly auto-binning. The raw
  trace graph remains mounted for callback wiring but its wrapper is hidden
  until raw columns are selected.
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
- `start_progress`/`tick_progress` poll the `_LOAD_PROGRESS` global (works
  because the dev server is threaded).
- `load_data_cb` populates filter options, histograms and the smart default bin
  size. `update_range_controls` then applies/reset ranges and increments the
  plot epoch as a load barrier; `update_plots` cannot race the previous
  dataset's slider values. `_load_data` is keyed by the matched
  file list plus mtime/size, so adding files under the same glob invalidates the
  stale dataframe/filter/heatmap signatures. Refreshing the stored automatic
  threshold suggestions does not issue another plot click when both automatic
  cuts are off, so a load produces one master render rather than two identical
  epoch-1 renders.
- `render_config_order_list`/`apply_config_order` expose all loaded configs as a
  draggable order list. The default order uses the sequenceConfig with the best
  coverage; missing configs remain alphabetic at the bottom.
- `update_plots` takes one filtered snapshot and returns trajectory, heatmap
  store/variants, target diagnostics, polar, raw traces, diagnostics, summary
  and render state atomically. Retired split-view/lazy callbacks are not
  registered. `update_polar_only` owns moving/R/quality changes and all three
  polar mini-histograms; it reuses the filtered-frame and Rayleigh caches rather
  than triggering the master renderer. Heatmap-colour controls wait for the
  completed render state so their aggregation does not compete with it.
- `_filtered_df` normalizes jump-buffer units for cache keys (`100` ms and old
  `0.1` second URLs share a signature). `_roi_masks` caches reached table,
  entered segment ids, and trim masks for fast ROI toggles.
- The master renderer writes heatmap JSON to `heatmap-figure-store`, not to
  `heatmap-plot.figure`, so Dash's `Plotly.react` path never applies the heatmap
  subplot figure. Metric/scale variants still update clientside without
  re-binning.
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
  heatmap, target diagnostics, polar, velocity/displacement diagnostics, and
  selected raw traces.
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
| Heatmap bin/bound/cmin/cmax | Debounced all-section update; heatmap store + variants are built exactly | Concurrent heatmap sidebar aggregation |
| Heatmap metric/scale | Clientside `Plotly.restyle` from the current binning variants | Server rebuild or `newPlot` |
| ROI entered/trim | Debounced atomic update of all affected sections | A second ROI/trajectory refresh callback |
| Trajectory/heatmap pan/zoom | Immediate clientside peer relayout plus debounced `viewport-store` after idle | URL writes, server rebuilds, Dash `relayoutData` callbacks, live-patching hidden graphs |
| Section navigation | Clientside scroll only, including replay of the active tab | Any server render, graph hide/show or Plotly reinitialisation |
| ROI reach/show | Debounced atomic update | Competing overlay/ROI callbacks |
| Polar moving/R/quality controls | Cached polar figure + three quality histograms only; exact stats precede display thinning | Master trajectory/heatmap/ROI/raw rebuild |

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

2. **The heatmap crashes `Plotly.react` (Dash's update path).** With a subplot
   grid, applying a new figure to a graph that isn't full-size yet throws
   *"Something went wrong with axis scaling"* in `setScale`, and it then never
   repaints. It happens **even without `scaleanchor`** (it's the subplot axis
   layout at a bad size). A fresh `Plotly.newPlot` re-initialises cleanly. **Fix:
   the server writes the fresh heatmap figure to `heatmap-figure-store`; a
   clientside callback re-runs `Plotly.newPlot(hg, hfig.data, hfig.layout)`.**
   Do not restore a server Output to `heatmap-plot.figure`: even when the panel is
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
  a real *binning* change (bin size/bound/cmin/cmax or filter); opacity stays
  stable during the guarded `newPlot`, and section navigation does no plot work.
  **Metric/scale swaps
  are instant, in-place, flash-free:** every metric×scale variant is precomputed
  at bin time (`build_heatmap_and_variants` → `heatmap-variants` store, ~0.7 MB)
  and the clientside `Plotly.restyle`s z/customdata/zmin/zmax/colorbar — no server
  round-trip, no newPlot. metric/scale are therefore NOT server inputs; the
  fingerprint tracks binning only (no zmin/zmax).
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
  ignores internal drags so config-order reordering remains reliable.
- **Two copies of the code** (`Plotting/dashboard.py` and `trajectory-dashboard/
  app.py`) can drift. Decide on one source of truth.
- **`raw-columns` default** doesn't always stick in the control; `update_plots`
  defaults it to `[GameObjectPosX, GameObjectPosZ]` so the raw plot isn't empty.
- Filter cache holds up to 4 filtered frames (`_FILTER_CACHE_MAX`) — a few
  hundred MB for multi-million-row frames. Lower it if memory-constrained.

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
2. Background/long callback for loading with true streamed progress (currently a
   threaded-global poll; would also decouple heavy loads from the request).
3. Server-side figure/HTML caching for exports and repeat views.
4. Persist the config LUT to disk so renames survive restarts.
5. Downsample-on-zoom (send more points only for the visible window) instead of a
   fixed global budget.
6. Unit tests around `apply_filters`, `_prepare_merged_groups`, and
   `resolve_dropped_folder`; a smoke test that boots the app and checks
   `/_dash-dependencies`.
7. Optional hexbin/KDE heatmap; per-subplot colour ranges.
8. Consolidate to a single source file / package instead of two copies.
