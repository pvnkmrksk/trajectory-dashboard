# Architecture & context — Trajectory Dashboard

> One-stop context for a developer or coding agent. Read this instead of scanning
> all ~2400 lines. The app is a **single file** (`app.py`, mirrored as
> `dashboard.py` in the sibling `Plotting/` project — they are identical bar the
> `python app.py` vs `python dashboard.py` line). Keep the two in sync.

---

## 1. What it is

An interactive Dash + Plotly web app for exploring **VR insect-trajectory
experiments**. You point it at a folder of CSVs and it pools, filters, animates,
and density-maps 2-D trajectories — fast, on millions of rows.

Stack in this environment: **Dash 4.2, Plotly 6.8, pandas 2, numpy**. (Dash 4 /
Plotly 6 matter — see the rendering gotchas in §7.)

---

## 2. Data model (the one thing to internalise)

- Input CSVs have columns: `Current Time, CurrentTrial, CurrentStep,
  GameObjectPosX, GameObjectPosZ` (X/Z is the ground plane; **not** Y), plus
  optional rotation/sensor columns.
- Sibling JSON is auto-detected per folder: `*_ControlScene_sequenceConfig.json`
  maps `CurrentStep → ConfigFile` (the treatment); `*FlyMetaData.json` maps
  `VR → FlyID/Sex`.
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
- **Velocity is in raw position-units per second, NOT cm/s.** Values are large
  (median ~thousands). Histograms cap at the 99th percentile; the velocity
  colour mode drops reset-spikes above the 99.5th pct before smoothing.

---

## 3. File map (top to bottom)

| Lines (~) | Section | Key functions |
|---|---|---|
| 28–131 | **Config name humaniser** | `humanise_config` + `_MANUAL_LUT` + live `_USER_LUT`. Regex rules turn messy config filenames into readable subplot titles. |
| 133–251 | **Data loading** | `find_csv_files`, `load_csv_fast` (1 CSV → tidy df with `_seg_id`+metadata), `_load_data` (concat, cache, progress). |
| 252–411 | **Filtering / stats** | `velocity_all` (vectorised per-row speed), `smoothed_velocity`, `compute_segment_stats`, `apply_filters`, `filter_by_stat_range`, `_dilate_keep`. |
| 413–1039 | **Plotting** | colour resolution + `_prepare_merged_groups`; `build_trajectory_figure`, `build_heatmap_figure`, histograms, `build_raw_trace_figure`; `_apply_axis_sync`, `_shared_range`/`_robust_range`, `rebase_to_origin`, `default_bin_size`, heatmap metric/colourbar helpers. |
| 1041–1127 | **Dash app + caches** | `app`, `_DATA_CACHE`, `_STATS_CACHE`, `_LOAD_PROGRESS`, `resolve_dropped_folder`, `_load_data`. |
| 1128–1489 | **Layout** | sidebar (glob/drop-zone/filters/colour/heatmap/advanced/LUT/metadata) + main area (radio view-switch + 3 absolutely-positioned panels + `dcc.Store`s). |
| 1490–2322 | **Callbacks** | URL state, drop-folder, progress, load, `update_plots`, `update_heatmap_only`, viewport sync, view switch, selection info, LUT, export. |
| 2323–2369 | **Clientside** | playback (Play/Pause/slider → `Plotly.animate`), heatmap `newPlot` re-init + resize, view-switch resize. |
| 2371–end | **`__main__`** | CLI (`--glob/--port/--host/--debug`), optional pre-load. |

Assets (Dash auto-serves `/assets`):
- `assets/dropzone.js` — folder drag-and-drop → `set_props('drop-data', …)`.
- `assets/heatsync.js` — re-attaches a relayout→`viewport-store` handler after the heatmap is `newPlot`-ed (Dash's own listener is lost on newPlot).

---

## 4. The processing pipeline

```
glob / dropped folder
   └─ find_csv_files → load_csv_fast (per file) → concat → sort ONCE by time
      └─ _load_data(pattern)                         cached in _DATA_CACHE
         └─ _filtered_df(...)                        cached in _FILTER_CACHE (last 4)
            ├─ subset by config/vr/fly/scene/folder + histogram range-selections
            └─ apply_filters: velocity-jump (time-buffered), min-displacement, trim
               └─ build_* figures → dcc.Graph
```

**Everything downstream assumes the load-time time-sort** and uses
`groupby(..., sort=False)`. Do not re-sort per segment (that was the original
perf killer).

`apply_filters` is fully vectorised: the velocity-jump buffer is a
`np.searchsorted` "dilation" (`_dilate_keep`), displacement/trim are groupby
transforms. This took a 3.8M-row replot from ~30 s to ~4 s.

---

## 5. Rendering model & tuning knobs

- **Trace count, not point count, drives Plotly render cost.** Segments sharing
  a colour collapse into ONE NaN-separated trace per (subplot, colour) via
  `_prepare_merged_groups` (vectorised). ~100 traces instead of ~4000.
- **Decimation budgets** (`_decimation_budget` / build): static WebGL
  `BUDGET_GL=300k`; animated `BUDGET_SVG=40k` (every frame is embedded in the
  figure JSON — Plotly cannot stream frames, so the budget is the payload lever);
  raw plot `BUDGET_RAW=25k`. "Max plot points" (Advanced) overrides.
- **Colour modes** (`color_by`): `individual`/`vr` (categorical, lines, legend);
  `trial`/`local_time`/`velocity` (sequential; markers for per-point ones; a
  hidden anchor trace supplies the Viridis colourbar). Velocity is
  rolling-smoothed (10 frames) and spike-clipped.
- **Layout**: 2-col grid, `SUBPLOT_PX=480` per subplot → the figure is its
  natural full height and the panel scrolls (no squishing). 1:1 aspect on
  trajectories via `scaleanchor` (see §7 for why the heatmap can't use it).
- **Heatmap**: `build_heatmap_figure` bins X/Z with `np.histogram2d`.
  `bin_size` is in **data units** (blank → `default_bin_size` ≈ 1/20 of the
  95th-pct extent); `bound_pct` clips the extent to a central percentile;
  `metric ∈ {count, time=count×median_dt seconds, percent}`; `log_scale` with
  human tick labels (`_log_colorbar`/`_fmt_metric`); `cmin/cmax` blank→auto,
  absolute or `crange_mode="percentile"`; occupancy floored at 100 ms.

---

## 6. Callback graph (what talks to what)

- `restore_from_url` (fires **once**, guarded by `url-restored`) ⇄ `update_url`
  (fires on every setting/zoom change — all controls are Inputs). Full
  bidirectional URL state **including the viewbox** (`vbx0…vby1`) and the current
  `view`. The once-guard breaks the echo loop.
- `on_folder_drop` ← `drop-data` (set by dropzone.js) → `resolve_dropped_folder`
  → glob + auto-load.
- `start_progress`/`tick_progress` poll the `_LOAD_PROGRESS` global (works
  because the dev server is threaded).
- `load_data_cb` populates filter options, histograms, the smart default bin
  size, then auto-triggers `update_plots`.
- `update_plots` — the main replot (trajectory + heatmap + raw + summary + hists).
- `update_heatmap_only` — fast heatmap-only rebuild on any heatmap control **or
  view switch to heatmap** (reuses the filter cache; re-applies the viewbox via
  `_apply_viewport`). On a view switch it returns `no_update` when nothing
  relevant changed (signature cached in `_LAST_HEAT_SIG`) so tab-flipping is free.
- `sync_viewport` — records the current viewbox into `viewport-store` (does NOT
  live-patch the other figure — that caused glitches). `apply_viewport_traj`
  re-applies it to the trajectory on view switch (heatmap side is done in
  `update_heatmap_only`). Both apply through `_apply_viewport`, which drops
  implausible (>8× data extent) ranges from a mis-sized relayout.
- `switch_view` toggles panel `visibility`. Clientside callbacks drive playback,
  resize graphs shown for the first time, and `newPlot` the heatmap.
- `export_html` rebuilds all figures server-side and emits one self-contained file.

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
   a clientside callback re-runs `Plotly.newPlot(hg, hfig.data, hfig.layout)`.**
   It must read the **fresh figure prop `hfig`**, not the graph's own `hg.data`
   (which stays stale because react crashed) — reading `hg.data` is exactly what
   caused the "stale heatmap until hard reload" bug.
   - **The newPlot is now fingerprint-guarded (do not revert to unconditional).**
     It only re-inits when the figure content actually changed *or* on the first
     reveal while the panel is visible; a plain tab switch with an unchanged
     figure just `Plotly.Plots.resize`s (no flash). The fingerprint covers trace
     shapes, `zmin/zmax`, height, and the axis ranges (so a viewport sync still
     re-inits once). `update_heatmap_only` mirrors this server-side: it returns
     `no_update` on a view-switch when its signature (`_LAST_HEAT_SIG`) is
     unchanged, so flipping tabs costs zero server work.

3. **WebGL (`Scattergl`) graphs created in a hidden panel never paint.** Hence
   the raw time-series plot uses SVG `go.Scatter` (smaller budget). Trajectory is
   WebGL but is the default-visible panel, so it's fine; a resize is nudged when
   it's shown after being created hidden.
   - **Only resize the graph in the currently-*visible* panel** (and never a
     global `window` `resize`). Resizing a hidden `scaleanchor` plot makes Plotly
     recompute a wildly wrong aspect-locked range and fire a `relayout` that
     poisons the shared viewport — that was the intermittent "everything zooms
     out to an empty view" glitch. As a belt-and-braces guard, `_apply_viewport`
     also rejects any stored range whose span is >8× the data's natural extent.

4. **Panels hide with `visibility:hidden` + absolute positioning, not
   `display:none`.** `display:none` gives a graph 0 size at creation and it can't
   recover; `dcc.Tabs` is worse (it *remounts* and resets the figure to the
   layout default). So all five views stay mounted and only toggle visibility.

5. **The Polar view fought three separate rendering bugs — keep all three fixes.**
   - Use **SVG `go.Scatterpolar`, not `Scatterpolargl`.** WebGL polar crashes on
     re-render (`Cannot read properties of undefined (reading '_scene')`), so the
     polar uses SVG with a tighter point budget (`BUDGET_POLAR`).
   - **Pass `r`/`theta`/`marker.color` as plain Python lists** (`.tolist()`), same
     reason as the heatmap `z` (§7.1): Plotly-6 encodes numpy as typed-array
     `bdata` that arrives empty through the clientside newPlot.
   - **It needs a height-pinned clientside `newPlot`.** Born hidden, Dash's
     `Plotly.react` updates the polar's traces but *not* the figure height (the
     SVG stays at the placeholder size and the subplots collapse). A clientside
     callback sets the container height to `figure.layout.height` and re-`newPlot`s
     it; the polar `dcc.Graph` is `responsive=False`. It is NOT in the fit-to-
     container resize map (that would flatten it).

**Coordinate convention (ROIs + polar).** Unity is left-handed: objects at polar
`(radius, angle°)` sit at `X = r·sin(angle)`, `Z = r·cos(angle)` (0° = forward/+Z
= top of screen). Headings/polar use `theta = atan2(dx, dz)` so 0° = forward too,
and the polar axis is `rotation=90, direction="clockwise"` — so the ROI overlay,
the reached counts, and the polar all agree. Left ROI ⇔ X<0, right ⇔ X>0.

---

## 8. Known issues / glitches / limitations

- **Heatmap "flash" on rebuild — largely resolved.** The heatmap re-inits only on
  a real *binning* change (bin size/bound/cmin/cmax, filter, viewport sync), with
  a short opacity fade; tab switches are resize-only (§7.2). **Metric/scale swaps
  are instant, in-place, flash-free:** every metric×scale variant is precomputed
  at bin time (`build_heatmap_and_variants` → `heatmap-variants` store, ~0.7 MB)
  and the clientside `Plotly.restyle`s z/customdata/zmin/zmax/colorbar — no server
  round-trip, no newPlot. metric/scale are therefore NOT server inputs; the
  fingerprint tracks binning only (no zmin/zmax). *Residual:* toggling lin↔log
  still does one newPlot (metric swaps don't); harmless but not yet flash-free.
  *Cleanest future fix remains:* a `Plotly.react`-safe subplot state that drops
  the newPlot/heatsync machinery entirely.
- **Heatmap→trajectory zoom sync depends on `assets/heatsync.js`.** newPlot drops
  Dash's relayout listener; the asset re-attaches one that writes `viewport-store`
  via `set_props`. If you refactor the heatmap rendering, keep or drop this in
  tandem.
- **Playback frames use `Scattergl` and are re-drawn client-side.** On very large
  animated selections the embedded frames make the figure JSON heavy (~tens of
  MB). Animation auto-uses the tighter `BUDGET_SVG`; still, prefer "Playback off"
  for the biggest datasets or lower "Max plot points".
- **Drag-drop can only resolve folders under the working directory.** Browsers
  don't expose absolute paths; `resolve_dropped_folder` searches `cwd`. Data
  elsewhere → type/paste a path. Status line says when it can't locate a folder.
- **Two copies of the code** (`Plotting/dashboard.py` and `trajectory-dashboard/
  app.py`) can drift. Decide on one source of truth.
- **`raw-columns` default** doesn't always stick in the control; `update_plots`
  defaults it to `[GameObjectPosX, GameObjectPosZ]` so the raw plot isn't empty.
- Filter cache holds up to 4 filtered frames (`_FILTER_CACHE_MAX`) — a few
  hundred MB for multi-million-row frames. Lower it if memory-constrained.

---

## 9. How to verify UI changes (no browser MCP needed)

Server-side callbacks: `import dashboard as d; d._load_data("<glob>")` then call
the builder/callback directly and assert on the returned figure. For anything
that only shows up in the browser (rendering, clientside, drag-drop), drive
Chrome over the DevTools Protocol:

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
sync); secondary Outputs need `allow_duplicate=True`.

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
