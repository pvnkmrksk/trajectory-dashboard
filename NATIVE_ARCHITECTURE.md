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
segment. Optional raw channels are fetched separately.

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
- load-time velocity/displacement histograms.

Newer UI requests replace a pending request instead of accumulating a callback
queue. Results from an obsolete request are ignored. Filtering/grouping changes
run a full worker pass; colour, spatial-grid, direction, statistics, raw-channel,
and layout changes take narrower paths.

The rendering boundary is deliberately hybrid:

- trajectories are `gl.LINES` in WebGL2 with panel placement, colour,
  displayed-trial fraction, animal visibility, and playback time handled by
  shaders/buffers;
- occupancy is a small per-panel raster stretched through the shared viewport;
- the direction field uses a lightweight Canvas particle layer because its
  spatial transform must remain exactly synchronized with trajectory/occupancy;
- polar, heading time, ROI diagnostics, metrics, histograms, and raw channels
  use vendored Apache ECharts 6.1 (Canvas renderer) for maintained hover,
  interactive legends, data zoom, reset, accessibility, and export;
- pan/zoom uses one shared world rectangle and never enters the worker; and
- section navigation leaves every renderer mounted and measurable.

Every spatial pane is a square pixel viewport inside its responsive card. The
WebGL shader and both Canvas transforms consume the same square-pixel geometry,
so one X unit always has the same screen length as one Z unit.

## Measured reference workload

Browser smoke on 2026-08-01 used `tests/SubScale/**/*_VR*.csv`:

| Measurement | Native result |
|---|---:|
| Source files | 16 |
| Source rows | about 4.19 million |
| Retained rows | 1,906,400 |
| Segments | 2,122 |
| Uncompressed typed payload | 68.8 MB |
| Python load + package | 9.17 s |
| Repeat unchanged source lookup/package | 4 ms (in-process binary cache) |
| Gzip response/transfer on localhost | about 1.45 s |
| Default filtered-table pass | 87 ms |
| Regroup to treatment, including every view | ready within 927 ms |
| Default trajectory GPU links | 239,204 |
| Browser console warnings/errors | 0 |

Pan/zoom and displayed-trial fraction are renderer-local. Playback advanced a
GPU time uniform while the application status remained Ready; it created no
worker request or server request.

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
| GPU playback (local time, duration p99 cap) and displayed-trial fraction | Complete |
| Occupancy count/time/percent and linear/log colour | Complete |
| Local body/movement direction field | Complete |
| Trial/animal polar and heading time | Complete |
| Trial/animal movement metrics | Complete |
| ROI rings, first-reached counts, entered-only, tail trim | Complete |
| ROI fraction/residence/time/error diagnostics | Complete |
| Native histograms and on-demand raw channels | Complete |
| Exact nearest-segment inspection | Complete |
| Static self-contained native HTML report | Complete |
| Shareable principal URL state | Complete |
| Multi-ring curtain observer, Any/All, editable geometry | Complete (numeric and direct canvas drag) |
| Whole-window folder drop, bounded path resolution, automatic load | Complete |
| Per-animal immediate visibility across trajectory and interactive charts | Complete |
| ECharts hover/legend/zoom/export for analytical charts | Complete |
| Draggable panel ordering | Compatibility work |
| Editable observation windows and paired window inference | Compatibility work |
| Transition-probability cell observer | Compatibility work |
| Delayed Holm/Rayleigh annotation layer | Compatibility work |

The compatibility rows are not considered superfluous and remain in the
requirements. The old Dash app stays runnable on this branch for those
workflows until their native equivalents land; no Plotly code is loaded by the
native process.

## Verification

```bash
python -c "import sys, native_app; print('dash' in sys.modules, 'plotly' in sys.modules)"
python -c "import py_compile; py_compile.compile('native_app.py', doraise=True)"
python native_app.py --glob "tests/SubScale/**/*_VR*.csv" --port 8060
curl http://127.0.0.1:8060/api/health
```

The first command must print `False False`; the health endpoint reports
`{"ok":true,"renderer":"browser-native","plotly":false}`.
