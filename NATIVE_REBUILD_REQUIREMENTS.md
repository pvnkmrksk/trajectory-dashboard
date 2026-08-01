# Browser-native dashboard rebuild

## Objective

Rebuild Dari Deepa's interactive presentation layer so that a loaded dataset
feels immediate during exploration. Keep the proven Python loader, segment
model, metadata recovery, vectorised filters, and exact per-segment summaries
as the reference analysis pipeline for the first migration stage. Remove Dash
callback scheduling and Plotly figure construction from the interactive path.

The new application is deliberately available beside the existing Dash app
until the native implementation has been benchmarked and its analytical parity
has been verified.

## Performance contract

1. Loading and preprocessing may take seconds and must report honest progress.
2. After the retained dataset reaches the browser, pan, zoom, playback,
   displayed-trial fraction, section navigation, and presentation changes must
   not contact Python.
3. Data-filter and grouping changes run in a Web Worker so the page remains
   interactive. A newer request cancels the visible result of an older one.
4. A visual change must rebuild only its dependent products. Viewport changes
   never rebuild analytical data.
5. The browser receives typed binary columns once, not repeated Plotly figure
   JSON containing copies of the same coordinates.
6. Default drawing budgets are bounded. Analytical counts and segment metrics
   continue to use the complete retained frame.
7. The target for a warm visual-only interaction is one animation frame; the
   target for a worker-backed filtered view on the SubScale reference dataset
   is under one second without blocking the main thread.

## Non-negotiable analytical requirements

- `_seg_id = SourceFile + CurrentTrial + CurrentStep` remains the atomic unit,
  constructed only after numeric coercion of trial and step.
- A source file restart remains distinct even when trial and step numbers are
  reused. Animal identity (`FlyID@VR`) remains a separate grouping.
- Rows stay in their load-time segment/time order. No per-segment sort is
  introduced by the browser or API.
- Segment filtering always retains or removes complete `_seg_id` values except
  for explicitly row-level operations: jump buffering, edge trim, moving-only
  drawing, ROI tail trim, and observation windows.
- Velocity stays in raw position-units per second. The retained smoothed
  velocity and exact pre-retention segment statistics remain authoritative.
- The Unity coordinate convention remains X/Z with 0 degrees at +Z and
  positive rotation toward +X.
- Trial metrics, polar population vectors, ROI fractions, and spatial
  occupancy preserve their existing denominators and independent-unit rules.
- Exact source-row and pre-retention segment summaries remain visibly distinct
  from the endpoint-safe retained drawing/analysis frame.

## Required user workflows

### Data and state

- Load a file, folder, or recursive glob and show files, source rows, retained
  rows, segments, and animals.
- Populate config, scene, VR, fly, folder, raw-channel, trial, step, and metric
  ranges from the loaded data.
- Persist the source and principal controls in the URL without writing the URL
  during pan or zoom.
- Report working, ready, empty, stale-result, and error states.

### Filtering and grouping

- Filter by config, scene, VR, fly, source folder, trial, step, peak velocity,
  displacement, distance walked, jump threshold/buffer, minimum displacement,
  and edge trim.
- Split panels by config, scene, VR, fly, source folder, or pooled data.
- Use the adaptive 1/2/3/4-column grid policy and allow an explicit override.
- Reorder panels without recomputing the filtered dataset.

### Trajectories

- Render dense trajectories with a single GPU draw call rather than one object
  or DOM node per trial.
- Support categorical, neutral, individual/config/scene/VR/folder, trial,
  local-time, velocity, and tortuosity colour semantics.
- Keep aspect ratio square and share one X/Z viewport with every spatial view.
- Provide wheel zoom, drag pan, reset, hover coordinates, point-budget control,
  moving-only drawing, and deterministic whole-trial display sampling.
- Run playback by updating a GPU time uniform; no frame arrays or server calls.

### Spatial summaries

- Occupancy count, seconds, and percent on a zero-centred square grid.
- Linear/log colour mapping and bounded bin count.
- Local direction field using body orientation or movement heading, circular
  mean angle, resultant strength, and abundance.
- Shared instantaneous pan/zoom between trajectory, occupancy, and direction.
- Target/ROI rings and observation geometry use the same coordinate transform.

### Circular and temporal direction

- One resultant per trial with R and valid-point quality gates.
- Trial and animal population modes with the existing weighting semantics.
- Signed heading-over-time traces that break across wrap boundaries.
- Body-orientation and movement-heading sources; optional moving-only gate.

### Diagnostics

- Native velocity and displacement histograms.
- Per-trial distance, displacement, median speed, and local tortuosity grouped
  by the active panel axis, with trial/animal independent units.
- Optional raw numeric time-series loaded on demand rather than included in the
  initial binary payload.
- Segment inspection must show source file, trial, step, group metadata, and
  exact statistics; a rendered colour group must not masquerade as one trial.

### Advanced analysis parity

The migration target also includes ROI reached/residence/time-to-target/heading
error, curtain rings, observation windows, transition probability, delayed
non-parametric inference, export, and publication styling. These are retained
as explicit requirements even where the first native milestone initially marks
them as compatibility work rather than silently dropping them.

## What is implementation artifact or superfluous

The following existing mechanisms are not product requirements and should not
be reproduced:

- Plotly traces, Plotly modebars, Plotly figure dictionaries, Dash component
  IDs, callback return arity, duplicate-output rules, and `dcc.Store` objects.
- `Plotly.newPlot` heatmap/flow workarounds, typed-array-to-list conversion,
  relayout listener reattachment, hidden-graph sizing guards, WebGL context
  recovery, and CSS that reaches into Plotly's generated DOM.
- Embedding every playback frame in a figure payload.
- Rebuilding figures merely to change visibility, a colour limit, panel order,
  clean layout, displayed-trial fraction, playback position, or viewport.
- Maintaining two copies of the application or treating the 9,000-line Dash
  shell as the reusable analysis API.
- Explanatory microcopy whose only purpose is to warn about a framework bug.

## Architecture

```text
trusted Python loader + exact summaries
                |
                v
       binary typed-column API
                |
                v
      Web Worker analytical model
       |        |        |       |
       v        v        v       v
   WebGL2    Canvas2D  Canvas2D Canvas2D
 trajectory occupancy  polar   diagnostics
       \________ shared viewport _______/
```

- `native_app.py` is the local entry point.
- `native_dashboard/server.py` exposes the native page and data endpoints.
- `native_dashboard/dataset.py` bridges the current Python analysis pipeline
  into a framework-neutral binary dataset contract.
- `native_dashboard/static/worker.js` owns the retained row table and computes
  filtered products off the main thread.
- `native_dashboard/static/renderers.js` contains small purpose-built WebGL2
  and Canvas 2D renderers with no plotting dependency.
- `native_dashboard/static/app.js` owns UI state, URL persistence, render
  scheduling, and cancellation.

## Migration and verification

1. Keep the Dash app runnable for side-by-side analytical comparisons.
2. Unit-test binary column layout, segment dictionaries, metadata options, and
   native API errors.
3. Compare filtered counts, segment metrics, occupancy totals, and polar
   resultants against Python reference builders on synthetic and SubScale data.
4. Measure initial payload bytes, worker compute time, render time, pan/zoom
   frame rate, playback frame rate, and memory.
5. Promote native mode to the default only after the parity table is explicit;
   no advanced analysis disappears merely because its old implementation was
   coupled to Plotly.
