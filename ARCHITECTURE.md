# Architecture & agent guide

This document orients a developer or coding agent inside `app.py`. The whole app
is one file, organised top-to-bottom in clearly commented sections. Read this
before editing — it captures the invariants that are easy to break.

## Mental model

```
glob / dropped folder
        │  find_csv_files → load_csv_fast (per file) → concat → sort
        ▼
   _load_data(pattern)                       cached in _DATA_CACHE
        │  adds _seg_id, ConfigFile, VR, FlyID, SourceFolder; segment stats
        ▼
   _filtered_df(...)                         cached in _FILTER_CACHE (last 4)
        │  subset (config/vr/fly/scene/folder) + histogram range-selections
        │  apply_filters: velocity-jump, min-displacement, trim  (all vectorised)
        ▼
   build_trajectory_figure / build_heatmap_figure / build_raw_trace_figure
        │  _prepare_merged_groups → few NaN-separated WebGL traces
        ▼
   dcc.Graph(s) in always-mounted panels (radio toggles visibility)
```

## File sections (in order)

1. **Config name humaniser** — `humanise_config` + `_MANUAL_LUT` + `_USER_LUT`
   (live overrides from the JSON LUT editor). Regex rules map messy config
   filenames to readable subplot titles.
2. **Data loading** — `find_csv_files`, `load_csv_fast` (one CSV → tidy df with
   `_seg_id`, metadata), `_load_data` (concat + cache + progress).
3. **Filtering / stats** — `velocity_all` (vectorised per-row speed),
   `smoothed_velocity` (rolling, spike-clipped), `compute_segment_stats`,
   `apply_filters`, `filter_by_stat_range`.
4. **Plotting** — colour resolution + `_prepare_merged_groups`, the figure
   builders, range/aspect helpers, heatmap metric + colourbar helpers.
5. **Dash app** — caches, `_load_data`, the layout, then all callbacks and the
   clientside playback/resize callbacks. `__main__` parses CLI args.

## Hard invariants (don't break these)

- **`_seg_id = SourceFolder + VR + Trial + Step`.** A segment is the unit of
  everything. Never group trajectories by `(Trial, Step)` alone — different
  files reuse the same numbers and would merge.
- **Data is time-sorted at load** (`_load_data` sorts once). Downstream code
  relies on this and must NOT re-sort per segment — that was the original perf
  bug. Use `groupby(..., sort=False)`.
- **Vectorise.** `apply_filters`, velocity, and stats are array/groupby ops, not
  per-segment Python loops. Keep new filters vectorised (see `_dilate_keep` for
  the jump-buffer trick via `np.searchsorted`).
- **Trace count is the render cost**, not point count. Segments sharing a colour
  collapse into ONE NaN-separated trace per (subplot, colour) in
  `_prepare_merged_groups`. Do not emit one trace per segment.
- **1:1 aspect + linked axes.** `_apply_axis_sync` sets `scaleanchor`,
  `matches`, and a shared range. Trajectory and heatmap share `uirevision`.
- **Panels stay mounted.** The Trajectories / Heatmap / Diagnostics views are
  three always-rendered `<div>`s toggled by `view-mode` (NOT `dcc.Tabs`, which
  unmounts and breaks live zoom-sync). `switch_view` flips `display`; a
  clientside callback `Plotly.Plots.resize`s the now-visible graph.

## Decimation & animation budgets

`_decimation_budget` / the budget block in `build_trajectory_figure`:
- static WebGL view → `BUDGET_GL` (300k points total),
- animated view → `BUDGET_SVG` (40k) because **every frame is embedded in the
  figure JSON**; payload ≈ budget × (N_FRAMES/2). Plotly cannot stream frames —
  they are pre-materialised, so the budget is the cost lever. Users can override
  with "Max plot points".
- Each segment is decimated to `budget / n_segments` points, always keeping
  index 0 (so playback starts at each track's first point).

## Colour modes (`color_by`)

| value        | kind                | trace mode | colourbar      |
|--------------|---------------------|------------|----------------|
| `individual` | categorical (VR+Fly)| lines      | legend         |
| `vr`         | categorical         | lines      | legend         |
| `trial`      | sequential/segment  | lines      | Viridis bar    |
| `local_time` | sequential/point    | markers    | Viridis bar    |
| `velocity`   | sequential/point    | markers    | Viridis "u/s"  |

Sequential point modes carry a per-point `mc` array + `cmin/cmax`; the colourbar
is a hidden anchor trace added AFTER the data traces (so animation frames, which
update only the data traces by index, leave it alone).

## Heatmap

`build_heatmap_figure` bins X/Z with `np.histogram2d`. Key knobs:
- `bin_size` in **data units** (blank → `default_bin_size` ≈ 1/20 of the 95th-pct
  extent); `bound_pct` clips the extent to a central percentile;
- `metric` ∈ {count, time (occupancy = count × median dt), percent};
- `log_scale` with human tick labels via `_log_colorbar` / `_fmt_metric`;
- `cmin`/`cmax` blank→auto, as absolute value or `crange_mode="percentile"`;
  occupancy is floored at 100 ms.

## Callback map

- `restore_from_url` (once) ↔ `update_url` — full bidirectional URL state incl.
  the viewbox; a `url-restored` flag breaks the echo loop.
- `on_folder_drop` — `drop-data` (set by `assets/dropzone.js`) →
  `resolve_dropped_folder` → glob + auto-load.
- `start_progress` / `tick_progress` — poll the `_LOAD_PROGRESS` global (works
  because the dev server is threaded).
- `load_data_cb` — populate filter options, histograms, smart default bin size;
  auto-triggers `update_plots`.
- `update_plots` — the main replot (trajectory + heatmap + raw + summary).
- `update_heatmap_only` — fast heatmap-only rebuild on bin/metric/scale/cmin
  change (reuses the filter cache).
- `sync_viewport` + clientside playback (`anim-play/pause/slider`) + `switch_view`.
- `export_html` — rebuilds figures server-side and emits a self-contained file.

## Extending — common tasks

- **New colour mode**: add an option to the `color-by` dropdown, a branch in
  `_prepare_merged_groups` (set `ck`, `mode`, `mc`/`color`, `cmin/cmax`), and (if
  sequential) extend the colourbar block + title map in `build_trajectory_figure`.
- **New filter**: add the control, thread it through `_filtered_df` /
  `apply_filters` (vectorised), and include it in `_filter_signature` so the
  cache stays correct.
- **New persisted control**: add it to BOTH `update_url` (encode) and
  `restore_from_url` (decode + an Output), keeping `n_out` in sync.
- **New heatmap metric**: extend `METRIC_UNITS`, the metric conversion in
  `build_heatmap_figure`, and `_fmt_metric`.

## Gotchas

- Adding a control that is also written elsewhere needs `allow_duplicate=True`
  on the secondary Output (the dependency graph 500s otherwise — check
  `/_dash-dependencies` returns 200 after changes).
- The drop zone can only resolve folders that live under the working directory
  (browsers don't expose absolute paths); fall back to typing a path.
- Velocity/occupancy are in raw position-units, not cm — see README.
