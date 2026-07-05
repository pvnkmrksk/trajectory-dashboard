# Agent instructions

Single-file Dash app (`app.py`) for VR trajectory analysis. A sibling
`Plotting/dashboard.py` exists but can lag; treat this repo's `app.py` as the
current source of truth unless explicitly asked to sync the sibling copy.

**Read [ARCHITECTURE.md](ARCHITECTURE.md) first.** It has the file map, data
model, callback graph, the rendering gotchas (§7), known issues (§8), and the
verify workflow (§9). [README.md](README.md) covers features.

Before editing:
- A *segment* is `_seg_id = SourceFile+CurrentTrial+CurrentStep`, built after
  numeric coercion/formatting of trial and step. Never regroup by `(Trial, Step)`
  alone.
- Keep everything **vectorised**; rely on the load-time sort
  (`groupby(..., sort=False)`, no per-segment re-sorting).
- Trajectories are merged into few NaN-separated traces — don't emit one per
  segment.
- **Do not** revert the heatmap fixes without understanding §7: `z`/`customdata`
  must be Python lists (2-D numpy breaks Dash/Plotly-6 serialisation), and the
  heatmap renders via a clientside `Plotly.newPlot(hg, hfig.data, hfig.layout)`
  reading the FRESH figure (not `hg.data`).

After editing:
- `python -c "import py_compile; py_compile.compile('app.py', doraise=True)"`
- Boot and confirm `GET /_dash-dependencies` → **200** (catches
  duplicate-output / missing-id errors).
- Data-path logic: `import app; df,_,_ = app._load_data("<glob>")` then call the
  builder/callback directly. UI/rendering: drive Chrome over CDP (ARCHITECTURE §9).
- New persisted control → add to BOTH `update_url` and `restore_from_url` (keep
  return arity in sync); secondary Outputs need `allow_duplicate=True`.
- ROI view notes: fraction counts use the unmasked filtered trial table, while
  time-to-target and heading-error panels use the visible ROI-filtered subset.
  `_roi_masks()` caches entered/trim masks per filtered frame and reach radius.
