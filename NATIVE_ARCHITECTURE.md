# Native dashboard architecture and migration status

## Why this branch exists

The Python pipeline was not the main source of interactive latency. On the
SubScale reference workload, preprocessing is seconds-scale but acceptable as
an initial cost; the old presentation path then repeatedly constructed Plotly
figure dictionaries, serialized arrays into large callback responses, scheduled
dependent Dash callbacks, and asked Plotly to reconcile a large mounted graph
tree. Animation was especially expensive because every frame lived in figure
JSON.

The native path treats initial loading and warm interaction as different
systems. Python loads and summarizes once. The browser then owns the retained
table and every warm analytical product.

## Runtime boundary

`native_app.py` imports neither Dash nor Plotly. It starts a small Flask server
with three responsibilities:

1. serve the static application;
2. run the existing Dash-free CSV/metadata/statistics package with the same
   endpoint-safe 2-million-row retention policy; and
3. dictionary-encode the retained table into one aligned typed binary payload.

The payload stores row-varying values only at row granularity: X, Z, local time,
smoothed/raw speed, body/movement heading, local tortuosity, and segment code.
Trial, step, source/category codes, and exact summaries are stored once per
segment. Filter mini-histograms and robust segment-duration quantiles are stored
once in the compact header. The generic raw-channel explorer is intentionally
not part of the product.

## Browser model

`worker.js` receives ownership of the binary buffer. It keeps the full retained
table off the main thread and prepares narrow drawing products:

- complete-segment metadata/statistic filters plus row-level jump/trim gates;
- active-panel codes and labels;
- a bounded line-pair buffer for one WebGL trajectory draw call;
- zero-centred occupancy grids and circular direction accumulators;
- trial/animal polar resultants and signed heading-time pairs;
- exact per-segment or per-animal movement metrics;
- target reach, residence, time-to-target, and heading-error products; and
- editable observation-window summaries with within-segment paired inference;
- cell-entry transition probability and on-demand Holm/Rayleigh inference; and
- load-time velocity/displacement histograms.

Newer UI requests replace a pending request instead of accumulating a callback
queue. Results from an obsolete request are ignored. Filtering changes run a
full worker pass. Panel labels/order remap the cached visible segments without
rerunning the filter. The worker also caches sparse per-segment occupancy and
direction-cell contributions, so curtain-only previews add matched segments'
existing cells instead of rescanning every retained row. Occupancy remains
cached separately from direction, and colour, movement, heading, polar,
playback, statistics, and layout changes rebuild only their dependent products.

The rendering boundary is deliberately hybrid:

- trajectories are instanced WebGL2 line quads with panel placement, tunable
  pixel width/opacity, colour, displayed-trial fraction, animal visibility,
  selected-segment visibility, and playback time handled by shaders/buffers;
- occupancy is a crisp nearest-neighbour per-panel raster with a visible colour
  bar, compact value-density profile, and absolute or percentile colour clipping;
- transition probability is a second native raster layer on the same grid,
  viewport, and interaction transform as paths/occupancy/flow; supported cells
  drill into their exact raw segments and blank space clears that observer;
- the direction field uses a lightweight Canvas particle layer because its
  spatial transform must remain exactly synchronized with trajectory/occupancy;
  abundance drives spawning, R drives mean alignment/angular spread, measured
  cell velocity can drive particle motion, and trails stay in the originating
  cell neighbourhood. A constant-lightness/chroma cyclic border is the
  unobtrusive direction key; the layer sleeps when off-screen or while a
  viewport gesture is active;
- polar, heading time, ROI diagnostics, metrics, and histograms
  use vendored Apache ECharts 6.1 (Canvas renderer) for maintained hover,
  interactive legends, data zoom, reset, accessibility, and export;
- pan/zoom uses one shared world rectangle and never enters the worker; and
- one compact, wheel/keyboard/arrow-snapping view rail moves through the
  spatial workspace, Targets, Windows, Heading, Metrics, Statistics, and
  Diagnostics. Paths, Occupancy, Flow, Polar, and Transitions are layers of
  that workspace. Its compact 2×2 overview defaults to four pooled
  representations, with an explicit all-panels comparison when needed; and
- the source picker and complete analysis controls are drawers. They remain
  available without permanently taking plot area, and the controls drawer
  starts collapsed so data is the dominant visual element.

Spatial panes use the available rectangular card area. Their world ranges are
expanded to the pane's pixel aspect, so the WebGL shader and both Canvas
transforms still make one X unit exactly the same screen length as one Z unit.
Grid ticks are generated in world coordinates at stable nice-number intervals,
including a stronger zero line, so they pan and zoom with the data rather than
relabelling fixed pixel subdivisions. A physical-unit scale bar provides an
additional reference. Occupancy uses the standard Viridis palette and nearest-
neighbour cells. Metric distributions combine density violins, box summaries,
deterministically jittered observations, hover, and inside/box zoom.

At the Python boundary, every timestamp is parsed once as UTC and stored as
timezone-naive `datetime64[ns]`. This makes mixed Unity ISO strings (naive,
`Z`, or explicit offsets) comparable without NumPy timezone warnings.
Repeated export layouts can contain a root copy and a metadata-rich session
copy of the same recording. Candidates sharing basename, exact size,
nanosecond mtime, and sampled start/end digest are read once, preferring the
copy beside JSON metadata. Counts report both discovered files and skipped
copies.

## Measured reference workload

Browser smoke on 2026-08-02 used the exact `tests/SubScale` folder source:

| Measurement | Native result |
|---|---:|
| Source files | 16 |
| Source rows | about 4.19 million |
| Retained rows | 1,906,400 |
| Segments | 2,122 |
| Uncompressed typed payload | 65.6 MB |
| Python load + package | 6.96 s |
| Repeat unchanged source lookup/package | 4 ms (in-process binary cache) |
| Gzip response/transfer on localhost | about 1.45 s |
| Default filtered-table pass | 87 ms |
| Regroup to treatment, including every view | ready within 927 ms |
| Default trajectory GPU links | 239,430 |
| Browser console warnings/errors | 0 |

The user-supplied `homing_filt/home` workload was profiled separately on
2026-08-03 after timestamp normalization and duplicate-copy detection:

| Measurement | Native result |
|---|---:|
| Discovered CSV files | 188 |
| Confirmed duplicate copies skipped | 74 (about 2.52 GiB) |
| CSV files parsed | 114 (about 3.85 GiB) |
| Source rows parsed | 25,190,390 |
| Retained rows packaged | 1,922,067 |
| Segments | 4,725 |
| Animals | 30 |
| Uncompressed typed payload | 69.7 MB (66.5 MiB) |
| Cold load + exact preprocessing + package | 67.4 s |
| Timezone warnings | 0 |

That profile identifies CSV decoding plus exact segment preprocessing over
25.2 million rows as the initial-load cost. It is independent of Plotly and
does not make the warm UI slower. Reimplementing the same CSV parse in
JavaScript would still need to read and decode roughly 4 GB; a future static
browser mode is valuable for deployment convenience and privacy, but is not by
itself a load-time optimization.

Pan/zoom and displayed-trial fraction are renderer-local. Playback advances a
GPU time uniform while the application status remains Ready; it creates no
worker request or server request. Curtain geometry and the raw WebGL mask are
updated locally during a drag. At a bounded cadence, the worker applies the
exact retained path/circle intersection but rebuilds only the visible occupancy
or direction product from cached segment contributions. A settled change then
refreshes the slower analytical products, making the ring a subset shared by
paths, occupancy, flow, transitions, polar, heading, ROI, metrics, windows, and
statistics rather than a trajectory-only highlight.

## Analytical invariants preserved

- `_seg_id` remains file + normalized integer trial + normalized integer step.
- One load-time segment/time order is reused everywhere.
- Exact per-source-file segment statistics are finalized before retention.
- Filter ranges and movement metrics use raw position units.
- Unity X/Z and heading conventions are shared by target overlays, reach tests,
  direction cells, polar, and heading error.
- ROI fraction/residence denominators use the quality-filtered pre-ROI segment
  table; time-to-target and heading-error use the visible ROI-filtered table.
- Displayed-trial fraction affects mounted path/polar/heading drawings but not
  occupancy, ROI, or metric denominators.

## Parity matrix

| Existing workflow | Native status |
|---|---|
| Bounded parallel CSV/metadata load | Complete |
| Config/scene/VR/fly/folder grouping and filters | Complete |
| Trial/step/peak/displacement/distance and quality filters | Complete |
| GPU trajectories, colour modes, moving gate, point budget | Complete |
| Shared pan/zoom, reset, responsive columns, clean layout | Complete |
| GPU playback (segment-local time; all/single; p95/p99/max caps) and displayed-trial fraction | Complete |
| Occupancy count/time/percent, crisp cells, linear/log and adjustable colour limits | Complete |
| Local body/movement flow field with abundance/R uncertainty controls and hue legend | Complete |
| Trial/animal polar and trial/mean/density heading time | Complete |
| Trial/animal movement metrics | Complete |
| ROI rings, first-reached counts, entered-only, tail trim | Complete |
| ROI fraction/residence/time/error diagnostics | Complete |
| Native filter mini-histograms with dual range and compact numeric controls | Complete (histograms refresh to the current AND subset) |
| Exact nearest-segment inspection | Complete |
| Static self-contained native HTML report | Complete |
| Shareable principal URL state and readable JSON view recipe | Complete |
| Python recipe/URL → `FilterSpec`/grouped frames bridge | Complete |
| Multi-ring curtain observer, Any/All, editable geometry | Complete (direct on every Cartesian spatial layer; center/edge drag, keyboard/UI/drag-to-trash deletion; exact shared analytical subset) |
| Whole-window folder drop, exact manifest/path resolution, strict source boundary, automatic load | Complete |
| Per-animal immediate visibility across trajectory and interactive charts | Complete |
| ECharts hover/legend/zoom/export for analytical charts | Complete |
| Device-local human-readable labels for every grouping axis | Complete |
| Draggable/keyboard panel ordering across linked views | Complete |
| Shared spatial layers and compact 2×2 overview | Complete (one viewport/subset/curtain across paths, occupancy, flow, polar, and transitions; pooled or all-panel overview) |
| Editable observation windows and paired window inference | Complete (shared spatial transform; on-demand worker-side Wilcoxon summaries) |
| Transition-probability cell observer | Complete (native shared-grid layer; unique segment/cell entry, crossed/ended outcomes, support/fraction/count controls, raw-path drill-down and blank reset) |
| Delayed Holm/Rayleigh annotation layer | Complete (on-demand worker pass; compact-letter metric comparisons, Rayleigh stars, and Holm-adjusted directional mean permutation tests) |

The old Dash app stays runnable on this branch as a reference implementation;
no Plotly code is loaded by the native process.

## Streaming roadmap (not implemented on this branch)

The current binary dataset remains an immutable snapshot. A later streaming
mode should preserve that fast warm-interaction model instead of repeatedly
reloading or recomputing the complete table:

1. Accept either a watched folder of append-only/rotating CSV files or a ZMQ
   source with an explicit schema and reconnect policy.
2. Normalize incoming rows through the same segment identity and numeric rules,
   then send compact row and segment deltas to the browser.
3. Append trajectory buffers on a roughly 250 ms to 1 s cadence. Do not rebuild
   settled segments or unrelated analytical products for every packet.
4. Refresh occupancy, flow, ROI, and metrics at a segment/trial boundary (or a
   separately throttled cadence), while the active trajectory remains live.
5. Define file rotation, partial-line, checkpoint, duplicate-row, backpressure,
   and reconnect behavior before exposing the mode as an analysis source.

## Privacy-first browser deployment roadmap (not implemented on this branch)

The target deployment remains local-first: experimental files must never be
uploaded merely to view them. The current trusted Python server is a practical
reference loader and future streaming gateway, not a requirement that data
leave the acquisition computer.

1. Keep the `daari-deepa-view/v1` recipe and typed-column payload as stable
   contracts. A captured recipe can already be passed to
   `trajectory_dashboard.load_view_recipe()` to recover a readable
   `FilterSpec`, filtered frame, and grouped frames for Matplotlib or notebooks.
2. Add a static-site mode using the File System Access API/folder drop, a
   Worker-based CSV parser, and the same segment-normalisation contract. All
   parsing and analysis must remain in that browser tab; no upload endpoint is
   involved.
3. Validate the pure-browser parser against the Python reference on malformed
   Unity timestamps, metadata fallbacks, restarted trials, and multi-file
   segment identity before presenting it as scientifically equivalent.
4. Retain the installable Python mode for very large sources, publication
   workflows, file watching, and ZMQ. A static website is a convenience path,
   while the locally trusted server is the high-capacity path.

## Verification

```bash
python -c "import sys, native_app; print('dash' in sys.modules, 'plotly' in sys.modules)"
python -c "import py_compile; py_compile.compile('native_app.py', doraise=True)"
python native_app.py --glob "tests/SubScale/**/*_VR*.csv" --port 8060
curl http://127.0.0.1:8060/api/health
```

The first command must print `False False`; the health endpoint reports
`{"ok":true,"renderer":"browser-native","plotly":false}`.
