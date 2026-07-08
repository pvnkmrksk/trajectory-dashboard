# Trajectory Dashboard Audit

Date: 2026-07-07
Last updated: 2026-07-08
Branch: `codex/audit-trajectory-dashboard`
Scope: `app.py`, `assets/*.js`, `ARCHITECTURE.md`, and runtime smoke checks against a synthetic dataset.

## Executive summary

The dashboard is functionally sophisticated and already contains several important performance fixes: segment identity is file-scoped, filtering is mostly vectorized, trajectory traces are merged into NaN-separated traces, and heatmap `z`/`customdata` are serialized as Python lists. The basic smoke checks passed: the app compiles, Dash dependencies load, trajectory pan/zoom works, legend single-click and double-click isolation work, and heatmap pan sync worked in a first-run browser probe.

The original "Plotly stops responding" symptom was reproducible as a stale render-state problem: a fresh browser session could inherit a server-global "already rendered" signature and skip the Heatmap figure it did not yet have. This branch moves rendered-view signatures into a client-side store and fixes heatmap listener reattachment after `Plotly.newPlot`. Remaining global mutable state should still be reviewed, but plot reuse is no longer driven by a process-global heatmap flag.

The strongest architectural recommendation is to stop treating `app.py` as the unit of composition. Extract the data model, loading, filtering, ROI calculations, and figure builders into importable modules first, then move callbacks/layout into a thin Dash shell. That preserves the fast pipeline while making it reusable outside Dash and testable without a browser.

## Branch follow-up status

This branch now addresses several audited items while keeping the original findings below for review context:

- Extracted Dash-free loading/filtering/grouping into `trajectory_dashboard/`.
- Fixed heatmap relayout listener reattachment after `Plotly.newPlot`.
- Added per-client view signatures so tab switches reuse mounted trajectory, diagnostics, heatmap, ROI, and polar figures unless the effective state changed.
- Added a polar clientside fingerprint so unchanged tab switches do not resize or `newPlot` reinitialise.
- Added graph config constants with `displaylogo=False`, pinned Dash/Plotly ranges, and capped total heatmap cells.
- Added progressive background preparation for non-active tabs after the focused view starts updating.
- Made same-glob data reloads sensitive to matched file list, mtime, and size so newly-added folders invalidate heatmap/filter caches.
- Scoped folder drop handling to the folder control and plot workspace so config-order drag is not intercepted.
- Replaced the radio view selector with mounted-panel tabs and removed the visible rebase-path control.
- Verified the reported Julius/SubScale URL: Heatmap loads, background Polar prepares, and Heatmap ↔ Polar tab switches keep stable dimensions and summaries without restarting the preload queue.
- Preserved user drag plot order across cached reload/replot by separating `_USER_CONFIG_ORDER` from metadata-derived default order.
- Added serial retention accounting in the sidebar and a top-line final retention summary for points, trials, and animals.
- Fixed heatmap ROI occupancy labels to use per-side union counts instead of summing overlapping ROI hits; this prevents >100% side labels under entered-only plus after-exit trim states.
- Added trajectory colour mode "ROI outcome" for left-ROI, right-ROI, and no-ROI segment highlighting.
- Removed the heatmap opacity dip during guarded `newPlot`; reinitialisation remains fingerprinted but no longer intentionally flickers.
- Hid the empty Diagnostics raw-trace container unless raw columns are selected.
- Added Polar to exported HTML and omitted the raw-trace placeholder when no raw columns are selected.
- Profiled the SubScale regression on 2026-07-08 and fixed the main slowdown:
  the serial filter audit dropped from 12.4s to 2.9s, focused trajectory callback
  dropped from ~4.2s to ~1.0s, and large-dataset background preloading is now
  deferred so hidden tabs do not contend with the visible plot.
- Changed trajectory ROI corner counts from independent left/right "ever reached"
  counts to exclusive first-reached outcome counts (`L-first`, `R-first`), so
  the two sides partition the visible ROI trials and cannot exceed 100% together.

## Verification performed

- `python -c "import py_compile; py_compile.compile('app.py', doraise=True)"` passed.
- Runtime versions observed: Dash 4.2.0, Plotly 6.8.0, pandas 2.3.2, numpy 2.2.6.
- Booted local server on port 8066 with synthetic CSV/config/metadata.
- `GET /_dash-dependencies` returned 200.
- Direct data-path smoke:
  - 960 rows, 12 `_seg_id` segments, 6 `(CurrentTrial, CurrentStep)` groups. This confirms why grouping by trial/step alone would be wrong.
  - `_filtered_df()` returned 960 visible rows and 12 segment stats.
  - `build_trajectory_figure(... animate=False)` returned 4 merged traces and no frames.
  - `build_heatmap_and_variants()` returned 2 heatmap traces and 6 variants; `to_plotly_json()` has list-backed `z`, `customdata`, `x`.
  - `_roi_masks()` returned a 12-row reached table.
- Browser smoke on synthetic data:
  - Trajectory Pan mode was active and dragging the plot changed axis ticks.
  - Clicking Zoom mode activated it and box-zoom changed axis ticks.
  - Legend single-click hid one legend item; double-click isolated one legend item.
  - Heatmap rendered as image tiles, had no console errors, and panning changed heatmap ticks.
  - Heatmap pan synced back to the trajectory view on first reveal/rebuild path.
- Server-side synthetic benchmark, 250,000 rows and 250 segments:
  - `compute_segment_stats`: 0.040 s.
  - `apply_filters`: 0.041 s.
  - `build_trajectory_figure`: 0.333 s, 120 traces, 1980 px height.
  - `fig.to_plotly_json`: 0.021 s.
  - `build_heatmap_and_variants`: 0.177 s, 12 traces, 6 variants.
- Julius/SubScale verification on 2026-07-08:
  - Direct load: 4,185,786 rows, 4,668 `_seg_id` trials, 12 animals, 36 files.
  - Active URL-quality filters (`vel=8.131`, `disp=0.001`, `jb=100`) retained 3,896,196 rows and 4,609 trials.
  - ROI entered-only plus after-exit trim retained 407,929 rows and 646 trials; heatmap ROI labels maxed at 30.5%, confirming the side-union fix stayed below 100%.
  - ROI-outcome trajectory built 14 merged traces; Polar built 28 traces.
  - `export_html()` returned a downloadable artifact containing `<h3>Polar</h3>`.
  - Browser smoke on port 8061: `/_dash-dependencies` returned 200; Trajectory, Heatmap, Polar, and Diagnostics mounted and rendered; Diagnostics raw trace wrapper was hidden with no selected raw columns; no browser console errors were observed.
- SubScale performance profile after the slowdown fix:
  - Full load: 11.6s for 4,185,786 rows, including CSV parse, concat, sort, and segment stats.
  - Filter: 2.7s for velocity/displacement quality filters.
  - ROI entered+after-exit trim: 1.0s; the vectorized exit mask alone is 0.38s.
  - Filter audit: 2.9s, down from 12.4s before optimization.
  - Focused trajectory callback with ROI entered+trim: about 1.0s after filter/load caches are warm.
  - Trajectory figure build itself: 0.44s; heatmap mask variants: 0.30s; polar: 0.25s. The slowdown was callback/accounting work, not Plotly trace generation.

## Critical findings

### P0: Heatmap relayout listener can fail to reattach after `newPlot` — fixed on this branch

Evidence:
- `assets/heatsync.js:42-48` returns early when `gd.__vpSyncSource === source`.
- `assets/heatsync.js:52-56` calls `__attachViewportSync(hg, "heat")`.
- `app.py:4241-4243` calls `Plotly.newPlot(...)`, then sets `hg.__heatSync = false`, then calls `__attachHeatSync(hg)`.

Why this matters:
- `Plotly.newPlot` can detach graph event listeners. The code resets `__heatSync`, but not `__vpSyncSource`. On a second heatmap `newPlot`, `__attachHeatSync()` runs, but `__attachViewportSync()` sees the old `__vpSyncSource` and returns without adding a new `plotly_relayout` handler.
- Result: heatmap pan/zoom still moves the heatmap locally, but `viewport-store` may stop updating. Switching views can then restore stale ranges and feel like zoom/pan stopped responding.

Resolution:
- `assets/heatsync.js` now tracks listener attachment per graph/source pair and reattaches after heatmap `newPlot`.
- Still worth adding a browser regression test: heatmap open -> pan -> change bin size or bound -> pan again -> switch to trajectory -> assert trajectory ticks reflect the second heatmap pan.

### P0: Global mutable callback state is not session-safe — partially fixed on this branch

Evidence:
- Global state: `_USER_LUT`, `_AUTO_LUT`, `_SHOW_RAW_CONFIG`, `_CONFIG_ORDER` at `app.py:49-55`.
- Global caches: `_DATA_CACHE`, `_STATS_CACHE`, `_META_CACHE`, `_LOAD_PROGRESS` at `app.py:2402-2407`.
- Remaining global render/support state includes `_LAST_TRAJ_SIG` and process-wide display metadata. Per-view rendered figure signatures now live in `dcc.Store(id="view-render-state")`.

Why this matters:
- Before this branch, one browser tab could suppress another tab's heatmap rebuild via a process-global signature. The reported URL hit this failure mode.
- Raw-config title mode and config order are process-global, not user/session-global.
- Load progress is a single global shared by all loads.
- A threaded dev server can interleave callbacks; a production multi-worker deployment would make behavior inconsistent in a different way.

Recommendation:
- Keep immutable data caches process-global if needed, but key them by normalized pattern plus data file mtimes.
- Move UI/session state into `dcc.Store` or a server-side session key.
- Continue removing remaining render/UI globals such as `_LAST_TRAJ_SIG`; compute idempotence from clientside figure fingerprints or per-session stores.

### P1: Heatmap bin cap can still generate browser-killing payloads

Evidence:
- `MAX_HEATMAP_BINS = 2000` at `app.py:1557`.
- `_heatmap_edges()` caps only each axis span/bin ratio at `app.py:1739-1741`.
- Variants store materializes 6 full `z` matrices at `app.py:1892-1910`.

Why this matters:
- 2000 x 2000 is 4,000,000 cells per subplot, before multiplying by subplots and 6 variants.
- A user-entered tiny bin size can produce enormous Python lists, huge JSON, and a browser freeze even though the axis cap technically fires.

Recommendation:
- Cap total cells, not just bins per axis. Example: max 250k to 500k cells across all visible heatmap subplots by default.
- Show a UI warning and auto-coarsen bins when the requested grid exceeds the cap.
- Include variant-store byte size in debug summary or logs.

### P1: The UI exposes a "trace selection" mental model that the renderer cannot support

Evidence:
- `_prepare_merged_groups()` intentionally merges many segments into one trace by subplot/color at `app.py:1147-1245`.
- Legend grouping is by individual or VR at `app.py:1239-1242`.
- Figure layout sets legend item behavior at `app.py:1549-1550`.

Why this matters:
- This is the right performance technique, but a user clicking "one trace" is not selecting one trial/segment. They are toggling a merged color group across one or more subplots.
- The smoke test showed legend interactions work technically, but the semantic unit is not the user's likely unit of inspection.

Recommendation:
- Keep merged traces for default performance.
- Add an explicit "Inspect segment" workflow: click/box-select points -> show `_seg_id`, source file, trial, step, stats, and optionally isolate that segment in a lightweight overlay.
- Avoid promising per-trace behavior in UI copy; use "legend group" or "individual/VR group" wording if copy is needed.

### P1: Requirements do not match the documented runtime contract

Evidence:
- `requirements.txt` says `dash>=2.16` and `plotly>=5.20`.
- `ARCHITECTURE.md` says Dash 4 / Plotly 6 matter, especially for typed-array and heatmap rendering gotchas.
- Runtime observed during audit: Dash 4.2.0 and Plotly 6.8.0.

Why this matters:
- The code contains Plotly-6-specific workarounds and Dash callback behavior assumptions. Loose lower bounds invite unsupported combinations that can reintroduce blank heatmaps or callback failures.

Recommendation:
- Pin tested major/minor ranges, or add a compatibility matrix and CI jobs for every supported range.
- At app startup, log the Dash/Plotly versions and warn on untested combinations.

## High-priority findings

### P1: `load_csv_fast()` does sequence config parsing before numeric step normalization

Evidence:
- ConfigFile mapping happens at `app.py:766-778`.
- Numeric coercion of `CurrentTrial` and `CurrentStep` happens later at `app.py:810-812`.

Why this matters:
- If a CSV lacks `ConfigFile` and has step values as strings like `"0.0"`, mapping integer sequence indexes can miss and fill `ConfigFile` with `"unknown"`.
- The segment ID fix correctly normalizes trial/step before building `_seg_id`; config mapping should follow the same rule.

Recommendation:
- Coerce `CurrentTrial` and `CurrentStep` before sequenceConfig mapping.
- Use the same integer-normalized step series for both `ConfigFile` mapping and `_seg_id`.
- Add a unit test with `"0"`/`"0.0"` step text and no `ConfigFile`.

### P1: `load_csv_fast()` uses strict JSON for sequence configs

Evidence:
- `_loads_tolerant()` exists at `app.py:180-190`.
- `load_folder_metadata()` uses it at `app.py:196`.
- `load_csv_fast()` uses `json.loads(Path(seq_path).read_text())` at `app.py:770`.

Why this matters:
- Tolerant parsing was added because Unity JSON can have trailing commas. Sequence config parsing can still fail and silently mark configs as unknown.

Recommendation:
- Use `_loads_tolerant()` for sequenceConfig parsing in `load_csv_fast()`.
- Surface parse failures in load metadata/status instead of silently continuing.

### P1: URL state schema is duplicated and arity-sensitive

Evidence:
- `_URL_NUM`, `_URL_STR`, `_URL_LIST` are defined at `app.py:3019-3028`.
- `restore_from_url()` manually sets `n_out = 27` and returns positional tuples at `app.py:3064-3118`.
- `update_url()` manually reconstructs params at `app.py:3227-3256`.

Why this matters:
- The maps are not used as a single source of truth.
- Adding a persisted control requires editing several positional lists and keeping return arity synchronized. This is exactly the kind of subtle callback error the architecture warns about.

Recommendation:
- Introduce a `UrlStateSpec` list of typed entries with component id, prop, parser, serializer, default omission rule, and optional legacy parser.
- Generate callback Outputs/Inputs and serialize/restore logic from that spec.

### P1: Dead/unreachable code inside `update_plots()` obscures ownership

Evidence:
- `update_plots()` returns early unless `view in ("traj", "diag")` at `app.py:3699-3704`.
- Later code still checks `if want_rois and view == "roi"` at `app.py:3755-3757`.

Why this matters:
- This is not currently harmful, but it makes callback ownership harder to reason about. The ROI tab is owned by `update_roi_view()`, not this branch.

Recommendation:
- Remove unreachable ROI build code from `update_plots()`.
- Make each callback own exactly one view or one state transition.

### P1: Polar point budget is declared but not enforced

Evidence:
- `BUDGET_POLAR = 30_000` at `app.py:1057`.
- `build_polar_figure(... max_points=None, ...)` accepts `max_points` at `app.py:2311-2313`.
- The function plots one radial line per trial at `app.py:2356-2363` and does not use `BUDGET_POLAR` or `max_points`.

Why this matters:
- A large number of segments can make the polar tab slow or unresponsive. The UI suggests a global max plot points control, but polar ignores it.

Recommendation:
- Apply a segment budget to polar rays, preferably stratified by group/config.
- Report sampled/total trials in the polar summary.

### P2: Synchronous folder-drop search can block the UI

Evidence:
- `resolve_dropped_folder()` does a bounded `os.walk()` over multiple roots at `app.py:2457-2472`.
- It can visit up to 120,000 directories in the request thread at `app.py:2461`.

Why this matters:
- A folder drop miss under a large ancestor can look like the app froze.

Recommendation:
- Prefer an explicit configured data root.
- Move folder resolution to a background worker or reduce the search scope.
- Show progress or a cancelable status for folder resolution.

## Medium-priority findings

### P2: `build_raw_trace_figure()` re-sorts the frame

Evidence:
- `df.sort_values("Current Time").iloc[::step]` at `app.py:2027`.

Why this matters:
- It does an O(n log n) sort in a plotting helper and can interleave unrelated files/segments. The rest of the pipeline relies on load-time ordering.

Recommendation:
- Use existing load order for raw trace decimation, or precompute a time-sorted raw view explicitly outside the plotting helper if global chronological order is required.

### P2: Rebase copies entire filtered frames

Evidence:
- `rebase_to_origin()` copies the frame at `app.py:1291`.
- It is called before trajectory, heatmap, polar, and export paths.

Why this matters:
- On multi-million-row data, this creates large temporary frames and can amplify memory pressure.

Recommendation:
- Move rebase into plotting/binning as x/z arrays derived from grouped first positions.
- Keep the original filtered frame immutable and reusable.

### P2: Filter and ROI cache memory can grow sharply

Evidence:
- `_FILTER_CACHE_MAX = 4` at `app.py:3511-3513`.
- `_ROI_MASK_CACHE_MAX = 8` at `app.py:695-697`.
- `_DATA_CACHE` has no eviction at `app.py:2402`.

Why this matters:
- Four multi-million-row filtered frames plus ROI masks can be hundreds of MB to several GB. `_DATA_CACHE` can grow without bound across glob patterns.

Recommendation:
- Use byte-aware LRU caches.
- Add a visible "clear cache" action or log cache size.
- Include data file mtimes in cache keys so stale files do not persist across reloads.

### P2: `plot_wheel_guard.js` can make page scrolling feel frozen over graphs

Evidence:
- It prevents default wheel behavior over `.nsewdrag` at `assets/plot_wheel_guard.js:12-15`.

Why this matters:
- This protects Plotly wheel zoom, but trackpad users may interpret non-scrolling over the central graph plane as a frozen dashboard.

Recommendation:
- Keep the guard if wheel zoom is primary, but add a clear modebar/interaction affordance or support a modifier key policy.
- Consider disabling wheel zoom by default on dense operational dashboards and using modebar zoom instead.

### P2: Export does not match visible app scope — partially fixed on this branch

Evidence:
- `export_html()` originally exported trajectory, heatmap, velocity/displacement, and raw traces only.
- This branch now exports Polar as well, using the same ROI-masked dataframe as the visible dashboard state.
- ROI diagnostics are still not included in export.
- It rebuilds figures server-side and does not reuse visible-tab stores.

Why this matters:
- Users can analyze ROI diagnostics interactively but still lose that tab in the exported artifact.

Recommendation:
- Add an ROI diagnostics export section if the static artifact should cover the full app scope.
- Share the same pure figure-builder service used by callbacks and export.

### P3: UI polish and design system debt

Evidence:
- Layout is all inline styles from `app.py:2538-3012`.
- The sidebar uses many 9-11 px explanatory paragraphs.
- Modebars show Plotly logo because graph config does not set `displaylogo=False` at `app.py:2931-2988`.
- UI copy says playback is `<=20k pts` at `app.py:2641-2642`, but `BUDGET_SVG` is 40k at `app.py:1055`.

Why this matters:
- The app feels dense and fragile even when it works.
- Stale explanatory text increases operator error.

Recommendation:
- Move visual styling into `assets/dashboard.css`.
- Use compact section headers, tooltips, and control grouping instead of long inline prose.
- Add graph config constants and remove the Plotly logo.
- Replace stale hard-coded text with values derived from constants.

## Proposed target architecture

The target should preserve the current performance model while making data processing reusable outside Dash.

Suggested package layout:

```text
trajectory_dashboard/
  __init__.py
  config.py              # constants, graph config, budget policy
  models.py              # typed state objects, UrlStateSpec, FilterSpec
  io.py                  # find_csv_files, metadata loading, load_csv_fast
  processing/
    segments.py          # segment id normalization and validation
    filters.py           # velocity, segment stats, apply_filters
    roi.py               # ROI extraction, reached/residence/heading tables
    cache.py             # byte-aware data/filter cache
  plotting/
    trajectory.py        # merged traces, trajectory figure builder
    heatmap.py           # binning, variants, heatmap figure builder
    diagnostics.py       # histograms, raw traces
    roi.py               # ROI diagnostics figure
    polar.py             # polar/rayleigh figure
  dash_app/
    layout.py            # app shell and controls
    callbacks.py         # thin Dash callbacks only
    url_state.py         # generated restore/update URL callbacks
app.py                   # imports and runs create_app()
```

Refactor order:

1. Add tests around current behavior before moving code.
2. Extract pure data functions first: IO, segment normalization, filters, ROI tables.
3. Extract figure builders with no Dash imports.
4. Introduce typed state objects for filters, plot options, ROI options, and URL state.
5. Move layout and callbacks last, keeping component IDs stable.
6. Revisit clientside Plotly lifecycle once server callbacks are thin and testable.

## Suggested regression tests

Add pytest tests:

- Segment IDs: mixed `"0"`/`"0.0"` trial/step values produce one segment per source-file/trial/step and never group by trial/step alone.
- Sequence config: no `ConfigFile` column plus string/float step values maps to the correct config.
- Tolerant JSON: sequenceConfig with trailing commas still loads.
- Filtering: velocity spike dilation respects segment boundaries and millisecond URL compatibility.
- Heatmap serialization: `to_plotly_json()["data"][0]["z"]` and `customdata` are lists of lists.
- Heatmap caps: tiny bin size auto-coarsens to a bounded total cell count.
- URL state: generated restore/update round-trips every persisted control.
- Callback smoke: importing app and `GET /_dash-dependencies` returns 200.

Add browser tests:

- Trajectory pan and zoom change axis ticks.
- Legend single-click hides one legend group; double-click isolates one legend group.
- Heatmap first reveal renders image tiles and has no console errors.
- Heatmap rebuild reattaches relayout: pan -> rebuild via bin/bound -> pan -> switch to trajectory -> trajectory ticks reflect the second pan.
- Switching hidden panels never emits broad bogus ranges into `viewport-store`.

## Recommended first fixes

1. Fix `assets/heatsync.js` listener reattachment after `newPlot`.
2. Add the heatmap rebuild browser regression test.
3. Normalize `CurrentStep` before `ConfigFile` mapping and use `_loads_tolerant()` for sequence configs.
4. Replace heatmap per-axis cap with total-cell cap.
5. Remove unreachable ROI code in `update_plots()`.
6. Pin or validate Dash/Plotly versions.
7. Introduce the URL state spec before adding any more persisted controls.
