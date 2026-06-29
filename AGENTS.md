# Agent instructions

You are working on a single-file Dash app (`app.py`) for VR trajectory analysis.

**Read [ARCHITECTURE.md](ARCHITECTURE.md) first** — it has the section map,
hard invariants, the callback graph, and recipes for common changes. The
[README.md](README.md) covers features and data assumptions.

Before you touch code:
- A *segment* is `_seg_id = SourceFolder+VR+Trial+Step`. Never regroup by
  `(Trial, Step)` alone.
- Keep everything **vectorised** and rely on the load-time sort
  (`groupby(..., sort=False)`, no per-segment re-sorting).
- Trajectories are merged into few NaN-separated WebGL traces — don't emit one
  trace per segment.

After you change anything:
- `python -c "import py_compile; py_compile.compile('app.py', doraise=True)"`
- Start the app and confirm `GET /_dash-dependencies` returns **200** (catches
  duplicate-output / missing-id callback errors).
- For data-path logic, exercise functions directly:
  `import app; df,_,_ = app._load_data("<glob>")` then call the builder/callback.

Conventions: match the existing comment density and naming; new persisted
controls must be added to BOTH `update_url` and `restore_from_url`; secondary
Outputs need `allow_duplicate=True`.
