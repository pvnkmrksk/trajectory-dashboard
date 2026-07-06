# Handoff Notes

Last updated: 2026-07-06

## Current State

- `main` includes the ROI/polar/filter/interaction work through the latest
  committed dashboard refinement pass. Current behavior includes heatmap ROI
  occupancy overlays, diagnostic polish, wheel-scroll containment, config-order
  drag support, and the expanded ROI tab.
- The app remains a single-file Dash app in `app.py`; assets are
  `assets/dropzone.js`, `assets/heatsync.js`, `assets/plot_wheel_guard.js`, and
  `assets/config_order.js`.
- Segment identity is `_seg_id = SourceFile + CurrentTrial + CurrentStep`, built
  after numeric coercion. Do not group by `(Trial, Step)` alone.
- Trajectories are still rendered as few NaN-separated traces. Avoid one trace
  per segment.

## Recent Changes To Preserve

- Plot pan/zoom must stay out of Dash `relayoutData` callbacks. Viewport sync is
  debounced in `assets/heatsync.js` and writes `viewport-store` after idle.
- Heatmap figures are written to `heatmap-figure-store`; the browser paints them
  with guarded `Plotly.newPlot`. Do not wire heatmap updates back to
  `heatmap-plot.figure` without re-verifying the Plotly-6 axis-scaling bug.
- Heatmap ROI overlays use faint target rings on the binned figure plus
  left/right occupancy labels in the top corners of each subplot. Labels use the
  current heatmap metric (`time`, `count`, `%`) and are swapped clientside from
  `heatmap-variants` alongside `z`/`customdata`.
- Visible-panel resize is separated from heatmap/polar `newPlot`; hidden
  scaleanchor graphs must not be resized.
- Diagnostics histograms include all filtered data but start zoomed to the
  filtered 99th percentile. Modebars are visible there.
- Config subplot order is draggable from the sidebar's full loaded config list;
  default order chooses the sequenceConfig with the best config coverage.
- ROI masks are cached by `_roi_masks()` per filtered frame and reach radius.
  The ROI tab shows fraction-reaching and residence-time swarms plus
  time-to-target and instantaneous heading-error violins on the visible filtered
  subset. The violin median/IQR markers are explicit line traces, not native
  boxes.
- Jump buffer UI is milliseconds. Old URLs with `jb=0.1` are restored as
  `100` ms and normalized to the same filter cache signature.

## Verification Recipe

Use the SmallSubScale data for a realistic smoke test:

```bash
python -c "import py_compile; py_compile.compile('app.py', doraise=True)"
python app.py --port 8050
curl -s -o /tmp/deps.txt -w '%{http_code}\n' http://127.0.0.1:8050/_dash-dependencies
```

Direct callback smoke:

```python
import app
pat = "/Users/pavan/src/Plotting/Data/Julius Data/SubScale/SmallSubScale/**/*_VR*.csv"
df, stats, metas = app._load_data(pat)
df_f, df_sub, _ = app._filtered_df(
    pat, 8.607, 0.026, 100, 100,
    None, None, None, None, None, None, None)
fig, summary = app.update_roi_view(
    1, 3.0, ["on"], [], [], "roi", pat,
    8.607, 0.026, 100, 100,
    None, None, None, None, None, None, None)
```

Browser checks that caught recent regressions:

- `/_dash-dependencies` returns `200`.
- Heatmap, trajectory, and ROI tabs hit Plotly's own drag rectangles
  (`nsewdrag drag`) and axis tick labels change after a real drag.
- Heatmap view shows faint ROI rings and top-corner metric labels when ROIs are
  available; switching `time/count/%` changes both the colourbar and ROI label
  units.
- Browser console has no warnings/errors after tab switches.
- The exclusion line for the SmallSubScale URL reads with actual params, e.g.
  `vel > 8.607 units/s (buffer 100 ms)`, `disp < 0.026`, and trim counts.

## Good Next Work

- Add small unit/smoke tests around `_roi_apply()` for the zero-entered case,
  `_jump_buffer_seconds()`, and heatmap list serialization.
- Consider a background worker/long callback for load and heavy plot builds.
- Optional heatmap improvements: hexbin/KDE or per-subplot color ranges.
- Consider downsample-on-zoom for very dense trajectory views.
- Decide whether to keep mirroring this file into the sibling
  `Plotting/dashboard.py`; the current source of truth is this repo's `app.py`.
