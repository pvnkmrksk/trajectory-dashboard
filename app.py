#!/usr/bin/env python3
"""
Interactive trajectory dashboard.

Usage:
    python app.py
    python app.py --port 8051
    python app.py --glob "Data/2025*/*_VR*.csv"
    python app.py --glob "MatrexVR_data/20250423_131431"
"""

import argparse
import glob
import json
import math
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.colors as pcolors
from plotly.subplots import make_subplots
from dash import Dash, Input, Output, State, Patch, ctx, dcc, html, no_update

# ---------------------------------------------------------------------------
# Config name humaniser
# ---------------------------------------------------------------------------

_MANUAL_LUT: dict[str, str] = {
    "Choice_00.json": "Blank",
    "Choice_All.json": "All (Push+Pull+Shear)",
    "Choice_Push.json": "Push",
    "Choice_Pull.json": "Pull",
    "Choice_Shear.json": "Shear",
    "Choice_empty.json": "Empty",
    "Choice_Empty_Empty.json": "Empty vs Empty",
    "choice____.json": "No stimuli",
    "Choice_uniBG_empty.json": "Uniform BG, empty",
    "bifurcation_empty_empty.json": "Bifurc. empty",
    "bilateral_bandM_empty.json": "Band (M) empty",
    "bilateral_bandM_noTextureBG_empty.json": "Band (M) no-tex empty",
}


# User-supplied overrides (edited live via the LUT editor). Checked first.
_USER_LUT: dict[str, str] = {}
# Names auto-derived from the config's OBJECTS at load (tree vs empty, …).
_AUTO_LUT: dict[str, str] = {}
# When on, subplot titles show the raw config filename instead of a readable name.
_SHOW_RAW_CONFIG: dict[str, bool] = {"on": False}
_CONFIG_ORDER: dict[str, int] = {}


def humanise_config(raw: str) -> str:
    if _SHOW_RAW_CONFIG["on"]:
        return raw
    if raw in _USER_LUT:
        return _USER_LUT[raw]
    if raw in _MANUAL_LUT:
        return _MANUAL_LUT[raw]
    if raw in _AUTO_LUT:
        return _AUTO_LUT[raw]

    name = raw.replace(".json", "")

    # --- Choice_locust patterns ---
    m = re.match(r"Choice_locust(?:_uniBG)?(_black)?(?:_(\d+))?_(\d+)", name)
    if m:
        colour = "black" if m.group(1) else "green"
        count = m.group(2) or "1"
        angle = m.group(3)
        bg = " uniBG" if "_uniBG" in name else ""
        return f"Locust {colour}{bg} {angle}°" + (f" ×{count}" if count != "1" else "")

    m = re.match(r"Choice_locust(?:_uniBG)?(_black)?_(\d+)", name)
    if m:
        colour = "black" if m.group(1) else "green"
        angle = m.group(2)
        bg = " uniBG" if "_uniBG" in name else ""
        return f"Locust {colour}{bg} {angle}°"

    # --- Lemon/fruit patterns ---
    m = re.match(r"Choice_(\w+?)_(\w+?)(?:_(sym|asym))?$", name)
    if m and any(k in name for k in ("Lemon", "Empty")):
        a, b = m.group(1), m.group(2)
        sym = f" ({m.group(3)})" if m.group(3) else ""
        a = a.replace("Far", " far").replace("Red", " red")
        b = b.replace("Far", " far").replace("Red", " red")
        return f"{a} vs {b}{sym}"

    # --- choice_LSM size patterns ---
    m = re.match(r"choice_(L?)(S?)(M?)(_?)(agl(\d+))?", name)
    if m and any(c != "_" for c in (m.group(1), m.group(2), m.group(3))):
        sizes = []
        if m.group(1) == "L": sizes.append("Large")
        if m.group(2) == "S": sizes.append("Small")
        if m.group(3) == "M": sizes.append("Medium")
        label = "+".join(sizes) if sizes else "None"
        if m.group(6):
            label += f" agl={m.group(6)}"
        return label

    # --- Bifurcation patterns ---
    m = re.match(r"bifurcation_(\w+?)_(\w+?)_dir(?:_loc(\d+))?", name)
    if m:
        a = m.group(1).replace("glocust", "green").replace("blacklocust", "black").replace("blackcylinder", "blk cyl")
        b = m.group(2).replace("glocust", "green").replace("blacklocust", "black").replace("blackcylinder", "blk cyl")
        loc = f" @{m.group(3)}" if m.group(3) else ""
        return f"Bifurc. {a} vs {b}{loc}"

    # --- Bilateral band patterns ---
    m = re.match(r"bilateral_band(H\d+|M)_(\w+?)_speed_(.+)", name)
    if m:
        heading = m.group(1)
        motion = m.group(2)
        stim = m.group(3).replace("_x_", " × ").replace("_", " ")
        return f"Bilateral {heading} {motion} {stim}"
    m = re.match(r"bilateral_band(H\d+|M)_(\w+?)_distance_(.+)", name)
    if m:
        heading = m.group(1)
        motion = m.group(2)
        stim = m.group(3).replace("_x_", " × ").replace("_", " ")
        return f"Bilateral {heading} {motion} {stim}"

    # --- Bifurcation gregarious speed patterns ---
    m = re.match(r"bifurcation_gregarious_locust_(\d+)_distance(\d+)_speed(\d+)", name)
    if m:
        angle, dist, speed = m.group(1), m.group(2), m.group(3)
        return f"Bifurc. greg. {angle}° d={dist} v={speed}"

    # --- Fallback: strip prefix, underscores to spaces ---
    for prefix in ("Choice_", "choice_", "bifurcation_", "bilateral_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.replace("_", " ").strip()


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def find_csv_files(pattern: str) -> list[str]:
    if os.path.isfile(pattern):
        return [pattern]
    if os.path.isdir(pattern):
        found = sorted(glob.glob(os.path.join(pattern, "*_VR*_.csv")))
        if not found:
            found = sorted(glob.glob(os.path.join(pattern, "*.csv")))
        return found
    found = sorted(glob.glob(pattern, recursive=True))
    if not found and not pattern.endswith(".csv"):
        found = sorted(glob.glob(pattern + ".csv", recursive=True))
    return [f for f in found if f.endswith(".csv") and os.path.isfile(f)]


def _find_sequence_config(csv_dir, csv_basename):
    parts = csv_basename.split("_")
    prefixes = (["_".join(parts[:2]), "_".join(parts[:3]), parts[0]]
                if len(parts) >= 2 else [parts[0]])
    for pfx in prefixes:
        p = os.path.join(csv_dir, f"{pfx}_ControlScene_sequenceConfig.json")
        if os.path.exists(p):
            return p
    return None


def _find_fly_metadata(csv_dir):
    for pat in ("*FlyMetaData.json", "*metadata.json"):
        hits = list(Path(csv_dir).glob(pat))
        if hits:
            return str(hits[0])
    return None


def _loads_tolerant(text: str):
    """json.loads, but forgiving of the trailing commas the Unity Choice configs
    ship with (``{"a":1,}`` / ``[1,2,]``) — strict json.loads rejects those, which
    silently dropped every ROI-bearing config from metadata."""
    try:
        return json.loads(text)
    except Exception:
        try:
            return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
        except Exception:
            return None


def load_folder_metadata(folder: str) -> dict:
    meta = {"folder": folder, "configs": {}, "sequence_order": [], "fly_metadata": None}
    for f in Path(folder).glob("*.json"):
        data = _loads_tolerant(f.read_text())
        if data is None:
            continue
        if "FlyMetaData" in f.name or "metadata" in f.name.lower():
            meta["fly_metadata"] = data
        elif "sequenceConfig" in f.name:
            order = []
            for s in data.get("sequences", []) if isinstance(data, dict) else []:
                cf = s.get("parameters", {}).get("configFile") if isinstance(s, dict) else None
                if cf and cf not in order:
                    order.append(cf)
            meta["sequence_order"].extend(order)
        else:
            meta["configs"][f.name] = data
    return meta


# ---------------------------------------------------------------------------
# ROI geometry (targets pulled from the Choice-scene configs)
# ---------------------------------------------------------------------------
# Objects are placed in Unity's LEFT-HANDED ground plane at polar (radius, angle°):
#   X = r*sin(angle),  Z = r*cos(angle)      [ = Euler(0,angle,0) * forward ]
# so angle 0 = forward/+Z (up on screen), 90 = +X (right), 180 = -Z (down),
# -90/270 = -X (left). Left ROI ⇔ X<0, right ROI ⇔ X>0. The same convention is
# reused for headings/polar (theta = atan2(dx, dz)) so overlay, counts and polar
# all agree.

def roi_xz(radius: float, angle_deg: float) -> tuple[float, float]:
    a = math.radians(angle_deg)
    return radius * math.sin(a), radius * math.cos(a)


def rois_from_config(cfg_data: dict) -> list[dict]:
    """Extract ROI targets from one parsed config dict → list of
    {x, z, angle, r, type, side, scale}.

    Handles both placement styles Unity emits:
      * polar     ``position: {radius, angle}``  (Choice/MormonBand scenes)
      * cartesian ``position: {x, y, z}``        (BinaryChoice tree targets; y up)
    """
    out = []
    objs = cfg_data.get("objects", []) if isinstance(cfg_data, dict) else []
    for o in objs:
        pos = o.get("position") or {}
        if pos.get("radius") is not None and pos.get("angle") is not None:
            r = float(pos["radius"]); a = float(pos["angle"])
            if r <= 0:                  # radius 0 = at the animal → not a target
                continue
            x, z = roi_xz(r, a)
        elif pos.get("x") is not None and pos.get("z") is not None:
            x = float(pos["x"]); z = float(pos["z"])
            r = math.hypot(x, z); a = math.degrees(math.atan2(x, z))
        else:
            continue
        scale = o.get("scale") or {}
        sc = abs(float(scale.get("x", 1) or 1))     # object half-size hint
        side = "left" if x < -1e-6 else "right" if x > 1e-6 else "centre"
        out.append({"x": x, "z": z, "angle": a, "r": r, "scale": sc,
                    "type": o.get("type", "object"), "side": side})
    return out


def _short_config_name(fname: str) -> str:
    """On-disk configs are ``<prefix>_ControlScene_Choice_X.json`` but the CSV's
    ConfigFile column carries the short ``Choice_X.json`` (the sequenceConfig
    reference). Normalise to the short form so ROIs key by ConfigFile."""
    return fname.split("_ControlScene_")[-1] if "_ControlScene_" in fname else fname


def rois_by_config(metas: list[dict]) -> dict[str, list[dict]]:
    """Map ConfigFile (short name) → its ROI list, pooled across all folders."""
    out: dict[str, list[dict]] = {}
    for m in metas or []:
        for fname, data in (m.get("configs") or {}).items():
            key = _short_config_name(fname)
            rois = rois_from_config(data)
            if rois and key not in out:
                out[key] = rois
    return out


def _canonical_side_targets(rois_by_cfg) -> dict[str, list[dict]]:
    """Representative left/right target centres from the loaded config set."""
    out = {}
    for side in ("left", "right"):
        vals = [r for rois in (rois_by_cfg or {}).values()
                for r in rois if r.get("side") == side]
        if vals:
            out[side] = [dict(x=float(np.median([r["x"] for r in vals])),
                              z=float(np.median([r["z"] for r in vals])),
                              side=side, inferred=True)]
    return out


def _heading_targets_for_config(cfg, rois_by_cfg, canonical) -> dict[str, list[dict]]:
    """Left/right heading targets for a config, with inferred missing sides.

    Choice/none-like configs sometimes lack one or both physical target objects,
    but for heading diagnostics we still want the same left/right reference frame.
    Missing targets come from the loaded config set; if only one side exists, the
    opposite side is mirrored in X as a last-resort imagined counterpart.
    """
    actual = {side: [r for r in (rois_by_cfg.get(cfg, []) if rois_by_cfg else [])
                     if r.get("side") == side]
              for side in ("left", "right")}
    out = {side: list(actual[side]) for side in ("left", "right") if actual[side]}
    for side, other in (("left", "right"), ("right", "left")):
        if side in out:
            continue
        if canonical.get(side):
            out[side] = [dict(canonical[side][0])]
        elif actual.get(other):
            r = actual[other][0]
            out[side] = [dict(r, x=-float(r["x"]), side=side, inferred=True)]
        elif canonical.get(other):
            r = canonical[other][0]
            out[side] = [dict(r, x=-float(r["x"]), side=side, inferred=True)]
    return out


# Readable stimulus name per object type. Extend as new stimuli appear.
_OBJECT_NAME = {
    "tree01": "tree", "tree01_windy": "windytree", "tree01_upside": "upsidetree",
    "MormonBand": "band", "": "empty",
}


def _object_name(t: str) -> str:
    if t in _OBJECT_NAME:
        return _OBJECT_NAME[t]
    if not t:
        return "empty"
    return (t.replace("01", "").replace("_windy", " windy")
             .replace("_upside", " upside").replace("_", " ").strip() or "empty")


def config_display_name(cfg_data) -> str | None:
    """Readable name from the config's OBJECTS, e.g. 'tree vs empty' — sorted
    left→right by X, so the (mirror-only) flip in the filename is irrelevant."""
    rois = rois_from_config(cfg_data)
    if not rois:
        return None
    names = [_object_name(r["type"]) for r in sorted(rois, key=lambda r: r["x"])]
    return " vs ".join(names) if len(names) > 1 else names[0]


def _populate_auto_lut(metas: list[dict]) -> None:
    """Fill _AUTO_LUT (config filename → object-derived name) and persist the
    combined LUT to config_names.json so edits/labels survive restarts."""
    for m in metas or []:
        for fname, data in (m.get("configs") or {}).items():
            key = _short_config_name(fname)
            if key in _AUTO_LUT:
                continue
            name = config_display_name(data)
            if name:
                _AUTO_LUT[key] = name
    _save_config_lut()


def _set_config_order(metas: list[dict]) -> None:
    """Sequence-config order for subplot/filter option ordering.

    Missing configs sort alphabetically after the known sequenceConfig entries.
    """
    _CONFIG_ORDER.clear()
    for m in metas or []:
        for cfg in m.get("sequence_order") or []:
            _CONFIG_ORDER.setdefault(cfg, len(_CONFIG_ORDER))


def _ordered_values(vals) -> list:
    vals = [v for v in vals if pd.notna(v)]
    if not vals:
        return []
    if _CONFIG_ORDER:
        return sorted(vals, key=lambda v: (_CONFIG_ORDER.get(v, 10**9), humanise_config(str(v)).lower(), str(v)))
    return sorted(vals, key=lambda v: humanise_config(str(v)).lower())


_CONFIG_LUT_PATH = "config_names.json"


def _load_config_lut() -> None:
    """Load a previously-saved config-name LUT into the user overrides."""
    data = _loads_tolerant(Path(_CONFIG_LUT_PATH).read_text()) if os.path.exists(_CONFIG_LUT_PATH) else None
    if isinstance(data, dict):
        _USER_LUT.update({str(k): str(v) for k, v in data.items()})


def _save_config_lut() -> None:
    """Persist the sanitised names (auto + manual + user edits) to disk."""
    merged = {**_AUTO_LUT, **_MANUAL_LUT, **_USER_LUT}
    try:
        Path(_CONFIG_LUT_PATH).write_text(json.dumps(merged, indent=2, sort_keys=True))
    except Exception:
        pass


_ROI_TABLE_COLS = ["_seg_id", "ConfigFile", "animal", "VR", "FlyID",
                   "reached_left", "reached_right"]


def roi_reached_table(df, rois_by_cfg, reach) -> pd.DataFrame:
    """Per-trial (segment) reached flags for the left/right ROI of each trial's
    config. Vectorised per config. `animal` = FlyID@VR (same animal across files
    when both match — e.g. a crash + restart). Only configs that actually carry
    a left and/or right ROI contribute rows."""
    if df is None or len(df) == 0 or not rois_by_cfg:
        return pd.DataFrame(columns=_ROI_TABLE_COLS)
    reach2 = float(reach) ** 2
    parts = []
    for cfg, sub in df.groupby("ConfigFile", sort=False, observed=True):
        rois = rois_by_cfg.get(cfg)
        if not rois:
            continue
        gx = sub["GameObjectPosX"].to_numpy()
        gz = sub["GameObjectPosZ"].to_numpy()
        ml = np.zeros(len(sub), bool)
        mr = np.zeros(len(sub), bool)
        has_l = has_r = False
        for r in rois:
            hit = (gx - r["x"]) ** 2 + (gz - r["z"]) ** 2 <= reach2
            if r["side"] == "left":
                ml |= hit; has_l = True
            elif r["side"] == "right":
                mr |= hit; has_r = True
        if not (has_l or has_r):
            continue
        # Segments are contiguous (load-time sort), so per-trial ANY is a
        # reduceat over the segment start indices — much faster than groupby.
        seg = sub["_seg_id"].to_numpy()
        starts = np.concatenate(([0], np.flatnonzero(seg[1:] != seg[:-1]) + 1))
        parts.append(pd.DataFrame({
            "_seg_id": seg[starts],
            "ConfigFile": sub["ConfigFile"].to_numpy()[starts],
            "VR": sub["VR"].to_numpy()[starts],
            "FlyID": sub["FlyID"].to_numpy()[starts],
            "reached_left": np.logical_or.reduceat(ml, starts),
            "reached_right": np.logical_or.reduceat(mr, starts)}))
    if not parts:
        return pd.DataFrame(columns=_ROI_TABLE_COLS)
    out = pd.concat(parts, ignore_index=True)
    out["animal"] = out["FlyID"].astype(str) + "@" + out["VR"].astype(str)
    return out[_ROI_TABLE_COLS]


def roi_config_summary(table: pd.DataFrame) -> dict:
    """Per-config totals for the subplot-corner tally."""
    out = {}
    if table is None or len(table) == 0:
        return out
    for cfg, sub in table.groupby("ConfigFile", sort=False, observed=True):
        tot = len(sub)
        lr = int(sub["reached_left"].sum())
        rr = int(sub["reached_right"].sum())
        out[cfg] = {"total": tot, "left_reached": lr, "right_reached": rr,
                    "left_frac": lr / tot if tot else 0.0,
                    "right_frac": rr / tot if tot else 0.0}
    return out


def time_to_target_table(df, rois_by_cfg, reach) -> pd.DataFrame:
    """Per trial that reached a side: seconds from the trial's start to the first
    sample within the reach radius of that side's ROI. Vectorised per config."""
    cols = ["ConfigFile", "side", "_seg_id", "t"]
    if df is None or len(df) == 0 or not rois_by_cfg:
        return pd.DataFrame(columns=cols)
    reach2 = float(reach) ** 2
    parts = []
    for cfg, sub in df.groupby("ConfigFile", sort=False, observed=True):
        rois = rois_by_cfg.get(cfg)
        if not rois:
            continue
        gx = sub["GameObjectPosX"].to_numpy(); gz = sub["GameObjectPosZ"].to_numpy()
        t = sub["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
        seg = sub["_seg_id"].to_numpy()
        starts = np.concatenate(([0], np.flatnonzero(seg[1:] != seg[:-1]) + 1))
        lens = np.diff(np.concatenate((starts, [len(sub)])))
        rel_t = t - np.repeat(t[starts], lens)     # seconds since each trial's start
        for side in ("left", "right"):
            centers = [r for r in rois if r["side"] == side]
            if not centers:
                continue
            inside = np.zeros(len(sub), bool)
            for r in centers:
                inside |= (gx - r["x"]) ** 2 + (gz - r["z"]) ** 2 <= reach2
            first_t = np.minimum.reduceat(np.where(inside, rel_t, np.inf), starts)
            reached = np.isfinite(first_t)
            if reached.any():
                parts.append(pd.DataFrame({
                    "ConfigFile": cfg, "side": side,
                    "_seg_id": seg[starts][reached], "t": first_t[reached]}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)


def heading_target_angle_table(df, rois_by_cfg, moving_thresh=None) -> pd.DataFrame:
    """Per-sample signed heading error relative to left and right target centres.

    Angles are degrees in [-180, 180]. Each valid sample contributes separately
    to the left and right target distributions. Configs without explicit targets
    use inferred left/right centres from the loaded config set, so "none" or
    one-target trials still share the same target-reference frame.
    """
    cols = ["ConfigFile", "side", "_seg_id", "angle_deg"]
    if df is None or len(df) == 0 or not rois_by_cfg:
        return pd.DataFrame(columns=cols)
    x = df["GameObjectPosX"].to_numpy()
    z = df["GameObjectPosZ"].to_numpy()
    n = len(df)
    seg = df["_seg_id"].to_numpy()
    dx = np.empty(n); dz = np.empty(n)
    dx[0] = np.nan; dz[0] = np.nan
    dx[1:] = np.diff(x); dz[1:] = np.diff(z)
    seg_start = np.empty(len(df), bool); seg_start[0] = True
    seg_start[1:] = seg[1:] != seg[:-1]
    dx[seg_start] = np.nan; dz[seg_start] = np.nan
    speed = np.hypot(dx, dz)
    valid = np.isfinite(speed) & (speed > 0)
    if moving_thresh:
        v = smoothed_velocity(df, 10)
        valid &= v >= float(moving_thresh)

    parts = []
    cfg_arr = df["ConfigFile"].to_numpy()
    canonical = _canonical_side_targets(rois_by_cfg)
    for cfg, sub in df.groupby("ConfigFile", sort=False, observed=True):
        side_targets = _heading_targets_for_config(cfg, rois_by_cfg, canonical)
        if not side_targets:
            continue
        idx = np.flatnonzero(cfg_arr == cfg)
        px = x[idx]; pz = z[idx]
        hx = dx[idx]; hz = dz[idx]
        m0 = valid[idx]
        htheta = np.degrees(np.arctan2(hx, hz))
        for side in ("left", "right"):
            targets = side_targets.get(side) or []
            if not targets:
                continue
            best = np.full(len(sub), np.nan)
            best_abs = np.full(len(sub), np.inf)
            for r in targets:
                ttheta = np.degrees(np.arctan2(r["x"] - px, r["z"] - pz))
                delta = ((htheta - ttheta + 180.0) % 360.0) - 180.0
                use = np.abs(delta) < best_abs
                best_abs[use] = np.abs(delta[use])
                best[use] = delta[use]
            m = m0 & np.isfinite(best)
            if m.any():
                parts.append(pd.DataFrame({
                    "ConfigFile": cfg, "side": side,
                    "_seg_id": sub["_seg_id"].to_numpy()[m],
                    "angle_deg": best[m]}))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)


def roi_exit_keep_mask(df, rois_by_cfg, reach) -> np.ndarray:
    """Boolean mask for keeping samples through the first post-ROI exit."""
    if df is None or len(df) == 0 or not rois_by_cfg:
        return np.ones(0 if df is None else len(df), bool)
    reach2 = float(reach) ** 2
    keep = pd.Series(True, index=df.index)
    for cfg, sub in df.groupby("ConfigFile", sort=False, observed=True):
        rois = rois_by_cfg.get(cfg)
        if not rois:
            continue
        gx = sub["GameObjectPosX"].to_numpy()
        gz = sub["GameObjectPosZ"].to_numpy()
        inside = np.zeros(len(sub), bool)
        for r in rois:
            inside |= (gx - r["x"]) ** 2 + (gz - r["z"]) ** 2 <= reach2
        if not inside.any():
            continue
        seg = sub["_seg_id"].to_numpy()
        entered = pd.Series(inside, index=sub.index).groupby(seg, sort=False).cummax().to_numpy()
        pos = sub.groupby("_seg_id", sort=False).cumcount().to_numpy()
        exit_flag = entered & (~inside)
        big = len(sub) + 1
        expos = pd.Series(np.where(exit_flag, pos, big), index=sub.index)
        first_exit = expos.groupby(seg, sort=False).transform("min").to_numpy()
        keep.loc[sub.index] = pos <= first_exit
    return keep.to_numpy()


def trim_after_roi_exit(df, rois_by_cfg, reach) -> pd.DataFrame:
    """For each trial that ENTERS then LEAVES an ROI, drop every sample after the
    first exit (keep the approach + first contact). Trials that never enter, or
    enter and stay, are untouched. Vectorised per config."""
    if df is None or len(df) == 0 or not rois_by_cfg:
        return df
    return df[roi_exit_keep_mask(df, rois_by_cfg, reach)]


def _on(v):
    return bool(v) and "on" in (v or [])


def _jump_buffer_seconds(v) -> float:
    """Jump buffer UI is milliseconds; old URLs/direct calls may pass seconds."""
    if v in (None, ""):
        return 0.1
    try:
        f = float(v)
    except Exception:
        return 0.1
    # Back-compat: historical URLs used jb=0.1 for 100 ms.
    return f / 1000.0 if f > 10 else f


def _compact_count(n) -> str:
    n = 0 if n is None else float(n)
    a = abs(n)
    if a >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{int(n)}"


_ROI_MASK_CACHE: dict = {}
_ROI_MASK_CACHE_ORDER: list = []
_ROI_MASK_CACHE_MAX = 8


def _roi_mask_key(df, pattern, reach):
    return (id(df), len(df), pattern, round(float(reach or 3.0), 6))


def _roi_masks(df_f, pattern, reach):
    """Cached ROI reached table + per-row masks for a filtered frame."""
    key = _roi_mask_key(df_f, pattern, reach)
    if key in _ROI_MASK_CACHE:
        return _ROI_MASK_CACHE[key]
    rois = rois_by_config(_load_data(pattern)[2])
    if not rois:
        return None, set(), np.ones(0 if df_f is None else len(df_f), bool), None
    reach_v = float(reach) if reach else 3.0
    table = roi_reached_table(df_f, rois, reach_v)
    entered_ids = set()
    if table is not None and len(table):
        entered_ids = set(table.loc[
            table["reached_left"] | table["reached_right"], "_seg_id"])
    trim_keep = roi_exit_keep_mask(df_f, rois, reach_v)
    result = (table, entered_ids, trim_keep, rois)
    _ROI_MASK_CACHE[key] = result
    _ROI_MASK_CACHE_ORDER.append(key)
    if len(_ROI_MASK_CACHE_ORDER) > _ROI_MASK_CACHE_MAX:
        old = _ROI_MASK_CACHE_ORDER.pop(0)
        _ROI_MASK_CACHE.pop(old, None)
    return result


def _roi_apply(df_f, pattern, reach, entered_only, trim):
    """Return (df_view, table).

    `table` is the UNMASKED per-trial reached table — counts/violins use it so the
    denominator is always the full number of trials in each config. `df_view` is
    the frame the trajectory/heatmap/polar actually draw: optionally restricted to
    whole trials that entered an ROI (entered_only) and then tail-trimmed. Both
    operate per whole trial (segment), so masks never bleed between trials.
    """
    if df_f is None or len(df_f) == 0:
        return df_f, None
    reach_v = float(reach) if reach else 3.0
    table, entered_ids, trim_keep, rois = _roi_masks(df_f, pattern, reach_v)
    if not rois:
        return df_f, None
    keep = np.ones(len(df_f), bool)
    if entered_only:
        keep &= df_f["_seg_id"].isin(entered_ids).to_numpy()
    if trim:
        keep &= trim_keep
    df_view = df_f[keep] if (entered_only or trim) else df_f
    return df_view, table


def load_csv_fast(filepath: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(filepath, parse_dates=["Current Time"])
    except Exception:
        return None

    required = ["Current Time", "CurrentTrial", "CurrentStep",
                 "GameObjectPosX", "GameObjectPosZ"]
    if not all(c in df.columns for c in required):
        return None

    csv_dir = os.path.dirname(filepath)
    csv_base = os.path.basename(filepath)

    if "ConfigFile" not in df.columns:
        seq_path = _find_sequence_config(csv_dir, csv_base)
        if seq_path:
            try:
                seq = json.loads(Path(seq_path).read_text())
                mapping = {}
                for i, s in enumerate(seq.get("sequences", [])):
                    cf = s.get("parameters", {}).get("configFile")
                    if cf:
                        mapping[i] = cf
                if mapping:
                    df["ConfigFile"] = df["CurrentStep"].map(mapping).fillna("unknown")
            except Exception:
                df["ConfigFile"] = "unknown"
        else:
            df["ConfigFile"] = "unknown"

    if "SceneName" not in df.columns:
        df["SceneName"] = df.get("Scene", "unknown")

    vr_number = None
    if "_VR" in csv_base:
        try:
            vr_part = csv_base.split("_VR")[1].split("_")[0].rstrip(".")
            vr_number = f"VR{vr_part}"
        except Exception:
            pass
    df["VR"] = vr_number or (df["VR"] if "VR" in df.columns else "unknown")

    meta_path = _find_fly_metadata(csv_dir)
    fly_id = "unknown"
    if meta_path and vr_number:
        try:
            meta = json.loads(Path(meta_path).read_text())
            fly = next((f for f in meta.get("Flies", []) if f.get("VR") == vr_number), None)
            if fly:
                fly_id = fly.get("FlyID", "unknown")
                df["Sex"] = fly.get("Sex", "unknown")
        except Exception:
            pass
    df["FlyID"] = str(fly_id)
    df["SourceFolder"] = os.path.basename(csv_dir)
    df["SourceFile"] = csv_base

    for c in ["CurrentTrial", "CurrentStep", "GameObjectPosX", "GameObjectPosZ"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.dropna(subset=required, inplace=True)

    # Build _seg_id from the INTEGER trial/step, AFTER numeric coercion. The raw
    # columns can mix int/float text within one file ("0" vs "0.0"); .astype(str)
    # on that splits a single real trial into two ids ("T0_S2" and "T0.0_S2.0")
    # that then interleave by time — which ballooned the per-config trial count
    # ~5x. Key on SourceFile so a crash+restart (a second CSV whose trial numbers
    # restart from 0) stays a distinct run; animal identity (FlyID@VR) still
    # merges the two files, which is a separate grouping.
    df["_seg_id"] = (df["SourceFile"] + "_T"
                     + df["CurrentTrial"].astype("int64").astype(str) + "_S"
                     + df["CurrentStep"].astype("int64").astype(str))
    base_keep = {
        "Current Time", "CurrentTrial", "CurrentStep", "GameObjectPosX",
        "GameObjectPosZ", "ConfigFile", "SceneName", "VR", "FlyID", "Sex",
        "SourceFolder", "SourceFile", "_seg_id",
    }
    numeric_keep = {
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and c not in base_keep
        and not df[c].isna().all()
    }
    keep_cols = [c for c in df.columns if c in (base_keep | numeric_keep)]
    df = df[keep_cols]
    for c in ("ConfigFile", "SceneName", "VR", "FlyID", "Sex",
              "SourceFolder", "SourceFile"):
        if c in df.columns:
            df[c] = df[c].astype("category")
    return df


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def compute_velocity_series(df: pd.DataFrame) -> pd.Series:
    """Per-row velocity for a SINGLE already-time-sorted segment."""
    dx = df["GameObjectPosX"].diff()
    dz = df["GameObjectPosZ"].diff()
    dt = df["Current Time"].diff().dt.total_seconds().replace(0, np.nan)
    return np.sqrt(dx**2 + dz**2) / dt


def velocity_all(df: pd.DataFrame) -> np.ndarray:
    """
    Vectorised per-row velocity across the whole (load-time-sorted) frame.
    NaN at each segment's first row so velocity never spans two segments.
    """
    dx = df["GameObjectPosX"].to_numpy()
    dz = df["GameObjectPosZ"].to_numpy()
    t = df["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
    ddx = np.empty(len(df)); ddx[0] = np.nan; ddx[1:] = np.diff(dx)
    ddz = np.empty(len(df)); ddz[0] = np.nan; ddz[1:] = np.diff(dz)
    ddt = np.empty(len(df)); ddt[0] = np.nan; ddt[1:] = np.diff(t)
    with np.errstate(invalid="ignore", divide="ignore"):
        vel = np.sqrt(ddx * ddx + ddz * ddz) / ddt
    seg = df["_seg_id"].to_numpy()
    seg_start = np.empty(len(df), bool); seg_start[0] = True
    seg_start[1:] = seg[1:] != seg[:-1]
    vel[seg_start] = np.nan
    vel[~np.isfinite(vel)] = np.nan
    return vel


def smoothed_velocity(df: pd.DataFrame, window: int = 10, spike_pct: float = 99.5) -> np.ndarray:
    """
    Per-row speed (position units / second), with reset-spikes removed and a
    rolling-mean smoothing applied within each segment.

    Reset spikes (position teleports) produce huge velocities; values above the
    `spike_pct` percentile are dropped (NaN) before smoothing so they neither
    colour a point nor leak into the rolling mean.
    """
    v = velocity_all(df)                       # NaN at seg starts / non-finite
    finite = v[np.isfinite(v)]
    if finite.size:
        thr = np.percentile(finite, spike_pct)
        v = np.where(v > thr, np.nan, v)
    s = pd.Series(v, index=df.index)
    sm = (s.groupby(df["_seg_id"].to_numpy(), sort=False)
           .rolling(window, min_periods=1).mean()
           .reset_index(level=0, drop=True))
    return sm.reindex(df.index).to_numpy()


def compute_tortuosity(df: pd.DataFrame, window: int = 15) -> np.ndarray:
    """Per-row local tortuosity = (path length over the last `window` steps) /
    (straight-line chord across that window), within each segment. 1 = straight,
    higher = more winding. Vectorised."""
    x = df["GameObjectPosX"].to_numpy()
    z = df["GameObjectPosZ"].to_numpy()
    seg = df["_seg_id"].to_numpy()
    ddx = np.empty(len(df)); ddx[0] = 0.0; ddx[1:] = np.diff(x)
    ddz = np.empty(len(df)); ddz[0] = 0.0; ddz[1:] = np.diff(z)
    step = np.sqrt(ddx * ddx + ddz * ddz)
    seg_start = np.empty(len(df), bool); seg_start[0] = True
    seg_start[1:] = seg[1:] != seg[:-1]
    step[seg_start] = 0.0
    s = pd.Series(step, index=df.index)
    path = (s.groupby(seg, sort=False).rolling(window, min_periods=2).sum()
             .reset_index(level=0, drop=True).reindex(df.index).to_numpy())
    xb = pd.Series(x, index=df.index).groupby(seg, sort=False).shift(window - 1).to_numpy()
    zb = pd.Series(z, index=df.index).groupby(seg, sort=False).shift(window - 1).to_numpy()
    chord = np.sqrt((x - xb) ** 2 + (z - zb) ** 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        tort = path / chord
    tort[~np.isfinite(tort)] = np.nan
    return np.clip(tort, 1.0, None)


def compute_segment_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorised per-segment stats (no Python-level per-segment loop)."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["seg_id", "n_points", "displacement",
                                     "peak_velocity", "median_velocity", "config",
                                     "vr", "fly_id", "scene", "source_folder"])
    vel = velocity_all(df)
    work = pd.DataFrame({
        "_seg_id": df["_seg_id"].to_numpy(),
        "x": df["GameObjectPosX"].to_numpy(),
        "z": df["GameObjectPosZ"].to_numpy(),
        "vel": vel,
    })
    g = work.groupby("_seg_id", sort=False)
    agg = g.agg(n_points=("x", "size"),
                x0=("x", "first"), z0=("z", "first"),
                x1=("x", "last"), z1=("z", "last"),
                peak_velocity=("vel", "max"),
                median_velocity=("vel", "median"))
    agg["displacement"] = np.sqrt((agg["x1"] - agg["x0"])**2 + (agg["z1"] - agg["z0"])**2)
    agg["peak_velocity"] = agg["peak_velocity"].fillna(0)
    agg["median_velocity"] = agg["median_velocity"].fillna(0)

    # Categorical metadata: one value per segment (first row)
    meta_cols = {"config": "ConfigFile", "vr": "VR", "fly_id": "FlyID",
                 "scene": "SceneName", "source_folder": "SourceFolder"}
    first = df.groupby("_seg_id", sort=False).first()
    out = pd.DataFrame({
        "seg_id": agg.index,
        "n_points": agg["n_points"].to_numpy(),
        "displacement": agg["displacement"].to_numpy(),
        "peak_velocity": agg["peak_velocity"].to_numpy(),
        "median_velocity": agg["median_velocity"].to_numpy(),
    })
    for outcol, src in meta_cols.items():
        out[outcol] = first[src].to_numpy() if src in first.columns else ""
    return out[out["n_points"] >= 2].reset_index(drop=True)


def _dilate_keep(seg, t, is_jump, buf):
    """Vectorised per-segment time-buffer dilation of a jump mask -> keep mask."""
    n = len(seg)
    keep = np.ones(n, bool)
    if not is_jump.any():
        return keep
    bnd = np.flatnonzero(seg[1:] != seg[:-1]) + 1
    starts = np.concatenate(([0], bnd))
    ends = np.concatenate((bnd, [n]))
    for s, e in zip(starts, ends):
        jm = is_jump[s:e]
        if not jm.any():
            continue
        tt = t[s:e]
        jt = tt[jm]
        idx = np.searchsorted(jt, tt)
        li = np.clip(idx - 1, 0, len(jt) - 1)
        ri = np.clip(idx, 0, len(jt) - 1)
        left = np.where(idx > 0, tt - jt[li], np.inf)
        right = np.where(idx < len(jt), jt[ri] - tt, np.inf)
        keep[s:e] = np.minimum(left, right) > buf
    return keep


def apply_filters(df, vel_threshold, min_disp, trim_samples, jump_buffer=0.1):
    """Fully vectorised. Assumes df is time-sorted within segments (load does this)."""
    if df is None or len(df) == 0:
        return df

    # 1) Velocity-jump removal with a time buffer around each jump
    if vel_threshold is not None and vel_threshold > 0:
        vel = velocity_all(df)
        is_jump = np.nan_to_num(vel, nan=0.0) > vel_threshold
        if is_jump.any():
            seg = df["_seg_id"].to_numpy()
            t = df["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
            keep = _dilate_keep(seg, t, is_jump, float(jump_buffer))
            df = df[keep]

    # 2) Minimum net-displacement per segment
    if min_disp is not None and min_disp > 0 and len(df):
        g = df.groupby("_seg_id", sort=False)
        x0 = g["GameObjectPosX"].transform("first")
        z0 = g["GameObjectPosZ"].transform("first")
        x1 = g["GameObjectPosX"].transform("last")
        z1 = g["GameObjectPosZ"].transform("last")
        disp = np.sqrt((x1 - x0)**2 + (z1 - z0)**2)
        df = df[disp >= min_disp]

    # 3) Trim N samples from each segment end
    if trim_samples is not None and trim_samples > 0 and len(df):
        g = df.groupby("_seg_id", sort=False)
        pos = g.cumcount()
        size = g["_seg_id"].transform("size")
        df = df[(pos >= trim_samples) & (pos < size - trim_samples)]

    return df.reset_index(drop=True)


def filter_by_stat_range(df, stats, stat_col, lo, hi):
    """Keep only segments whose stat value falls in [lo, hi]."""
    if stats is None or len(stats) == 0:
        return df
    keep = stats[(stats[stat_col] >= lo) & (stats[stat_col] <= hi)]["seg_id"]
    return df[df["_seg_id"].isin(keep)]


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
    "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5",
]


def _downsample(x, y, max_pts=5000):
    n = len(x)
    if n <= max_pts:
        return x, y
    step = max(1, n // max_pts)
    return x[::step], y[::step]


N_ANIM_FRAMES = 25
MAX_ANIM_TRACES = 150

# Total rendered-point budgets (dynamic decimation targets). Effective points
# across all trajectory traces are kept near these so a normal browser stays
# responsive. WebGL (Scattergl) handles far more than animated SVG (Scatter).
BUDGET_GL = 300_000      # static WebGL trajectories
BUDGET_SVG = 40_000      # animated trajectories (every frame is embedded → keep light)
BUDGET_RAW = 25_000      # raw time-series plot
BUDGET_POLAR = 30_000    # polar plot (SVG Scatterpolar)
_POLAR_RAY_CACHE: dict = {}
_POLAR_RAY_CACHE_ORDER: list = []
_POLAR_RAY_CACHE_MAX = 8

# Per-subplot pixel height. With a 2-col layout each subplot is ~half the main
# width, so ~480px tall keeps each box roughly square; the page scrolls when
# there are many rows rather than squishing them.
SUBPLOT_PX = 480
SUBPLOT_PX_COMPACT = 390

SEQ_COLORSCALE = "Viridis"


def _decimation_budget(n_traces, animate, max_points=None):
    """
    Decide (can_animate, total_point_budget).

    max_points (Advanced override) wins when > 0; otherwise the budget is
    chosen dynamically from the render path so the plot stays snappy.
    """
    can_animate = bool(animate) and n_traces <= MAX_ANIM_TRACES
    if max_points and max_points > 0:
        return can_animate, int(max_points)
    return can_animate, (BUDGET_SVG if can_animate else BUDGET_GL)


def _subplot_px(nrows, ncols):
    """Keep common 2x2-ish views visible on one desktop screen; let larger grids scroll."""
    return SUBPLOT_PX_COMPACT if ncols == 2 and nrows <= 2 else SUBPLOT_PX


def _subplot_spacing(nrows):
    """Small vertical gaps keep multi-row Plotly drag targets easy to hit."""
    return min(0.035, 0.10 / max(int(nrows) - 1, 1))


def _group_frames(df, group_by, pool_mode, ncols):
    col_map = {"config": "ConfigFile", "vr": "VR", "flyid": "FlyID",
               "scene": "SceneName", "file": "SourceFolder"}
    if pool_mode == "pooled" or group_by == "all":
        groups = {"All Data": df}
    else:
        gcol = col_map.get(group_by, "ConfigFile")
        if gcol not in df.columns:
            groups = {"All Data": df}
        elif gcol == "ConfigFile":
            vals = _ordered_values(pd.unique(df[gcol]))
            groups = {str(v): df[df[gcol] == v] for v in vals}
        else:
            groups = {str(k): v for k, v in df.groupby(gcol, sort=False)}
    return groups


def _sample_scale(t):
    t = 0.0 if not np.isfinite(t) else max(0.0, min(1.0, float(t)))
    return pcolors.sample_colorscale(SEQ_COLORSCALE, [t])[0]


def _color_maps(df):
    individuals = sorted(df[["VR", "FlyID"]].drop_duplicates().itertuples(index=False, name=None))
    ind_color = {k: COLORS[i % len(COLORS)] for i, k in enumerate(individuals)}
    vr_cats = sorted(df["VR"].dropna().unique())
    vr_color = {v: COLORS[i % len(COLORS)] for i, v in enumerate(vr_cats)}
    tmin = float(df["CurrentTrial"].min()) if "CurrentTrial" in df else 0.0
    tmax = float(df["CurrentTrial"].max()) if "CurrentTrial" in df else 1.0
    return ind_color, vr_color, tmin, tmax


def _nan_join(x, y, segids, mc=None):
    """Concatenate already-contiguous segments inserting NaN gaps between them."""
    if len(x) == 0:
        return x, y, mc
    bnd = np.flatnonzero(segids[1:] != segids[:-1]) + 1
    xx = np.insert(x, bnd, np.nan)
    yy = np.insert(y, bnd, np.nan)
    mm = np.insert(mc, bnd, np.nan) if mc is not None else None
    return xx, yy, mm


def _record_arrays(rec, frac=1.0):
    """Build NaN-joined arrays for a record, optionally truncated to time `frac`."""
    if frac >= 1.0:
        return _nan_join(rec["x"], rec["y"], rec["segids"], rec["mc"])
    keepn = np.ceil(np.maximum(frac, 1e-9) * rec["dlen"]).astype(int)
    m = rec["dpos"] < keepn
    mc = rec["mc"][m] if rec["mc"] is not None else None
    return _nan_join(rec["x"][m], rec["y"][m], rec["segids"][m], mc)


def _prepare_merged_groups(df, group_by, pool_mode, ncols, color_by, budget):
    """
    Vectorised. Returns (group_names, records). Each record is ONE merged trace
    (all segments sharing a colour within a subplot). Records hold flat
    decimated arrays plus per-segment structure (dpos/dlen) so animation frames
    can be sliced by time without any re-grouping.
    """
    groups = _group_frames(df, group_by, pool_mode, ncols)
    group_names = list(groups.keys())
    total_segs = sum(g["_seg_id"].nunique() for g in groups.values())
    pts_lim = max(2, int(budget) // max(total_segs, 1))

    ind_color, vr_color, tmin, tmax = _color_maps(df)
    tspan = (tmax - tmin) or 1.0

    # Per-point speed for the "velocity" colour mode (shared scale across subplots)
    vel_series, vel_cmax = None, 1.0
    if color_by == "velocity":
        vel_series = pd.Series(smoothed_velocity(df, 10), index=df.index)
        finite = vel_series.to_numpy()
        finite = finite[np.isfinite(finite)]
        vel_cmax = float(np.percentile(finite, 99)) if finite.size else 1.0

    legend_seen, records = set(), []
    for idx, gname in enumerate(group_names):
        gdf = groups[gname]
        row, col = idx // ncols + 1, idx % ncols + 1

        # Vectorised decimation: keep every step-th row within each segment,
        # where step scales with segment length to hit the point budget.
        gg = gdf.groupby("_seg_id", sort=False)
        pos = gg.cumcount().to_numpy()
        size = gg["_seg_id"].transform("size").to_numpy()
        step = np.maximum(1, size // pts_lim)
        keep = (pos % step) == 0
        dec = gdf.loc[keep]
        if len(dec) == 0:
            continue

        segids = dec["_seg_id"].to_numpy()
        x = dec["GameObjectPosX"].to_numpy()
        y = dec["GameObjectPosZ"].to_numpy()
        gd = dec.groupby("_seg_id", sort=False)
        dpos = gd.cumcount().to_numpy()
        dlen = gd["_seg_id"].transform("size").to_numpy()
        vr = dec["VR"].to_numpy()

        mc_all = None
        if color_by == "vr":
            ck = vr.astype(str)
        elif color_by == "trial":
            ck = dec["CurrentTrial"].to_numpy().astype(float).astype(str)
        elif color_by == "local_time":
            ck = np.zeros(len(dec), dtype=int)   # whole subplot = one trace
            g2 = dec.groupby("_seg_id", sort=False)["Current Time"]
            t0, t1 = g2.transform("first"), g2.transform("last")
            dur = (t1 - t0).dt.total_seconds().replace(0, 1.0)
            mc_all = ((dec["Current Time"] - t0).dt.total_seconds() / dur).to_numpy()
        elif color_by == "velocity":
            ck = np.zeros(len(dec), dtype=int)   # whole subplot = one trace
            mc_all = vel_series.loc[dec.index].to_numpy()
        else:  # individual
            fid = dec["FlyID"].to_numpy()
            ck = np.char.add(np.char.add(vr.astype(str), "|"), fid.astype(str))

        for key in pd.unique(ck):
            m = ck == key
            rec = dict(row=row, col=col, segids=segids[m], x=x[m], y=y[m],
                       dpos=dpos[m], dlen=dlen[m], mc=None, mode="lines",
                       color=COLORS[0], label="", legendgroup=None,
                       showlegend=False, colorscale=None, cmin=None, cmax=None)

            if color_by == "vr":
                rec["color"], rec["label"] = vr_color.get(key, COLORS[0]), str(key)
            elif color_by == "trial":
                tv = float(key)
                rec["color"] = _sample_scale((tv - tmin) / tspan)
                rec["label"] = f"T{int(tv)}"
                rec["colorscale"], rec["cmin"], rec["cmax"] = SEQ_COLORSCALE, tmin, tmax
            elif color_by == "local_time":
                rec["mode"], rec["mc"] = "markers", mc_all[m]
                rec["colorscale"], rec["cmin"], rec["cmax"] = SEQ_COLORSCALE, 0.0, 1.0
            elif color_by == "velocity":
                rec["mode"], rec["mc"] = "markers", mc_all[m]
                rec["colorscale"], rec["cmin"], rec["cmax"] = SEQ_COLORSCALE, 0.0, vel_cmax
            else:  # individual
                vrv, fidv = str(key).split("|", 1)
                rec["color"] = ind_color.get((vrv, fidv), COLORS[0])
                parts = [p for p in (vrv if vrv and vrv != "unknown" else None,
                                     f"fly{fidv}" if fidv and fidv != "unknown" else None) if p]
                rec["label"] = " ".join(parts) or str(key)

            if color_by in ("individual", "vr"):
                rec["legendgroup"] = rec["label"]
                rec["showlegend"] = rec["label"] not in legend_seen
                legend_seen.add(rec["label"])
            records.append(rec)

    return group_names, records


def _add_traj_trace(fig, td, TraceType, hover=True):
    common = dict(name=td["label"], legendgroup=td["legendgroup"],
                  showlegend=td["showlegend"], opacity=0.75)
    if td["mode"] == "markers":
        common["marker"] = dict(size=3, color=td["marker_color"],
                                 colorscale=td["colorscale"],
                                 cmin=td["cmin"], cmax=td["cmax"])
    else:
        common["line"] = dict(color=td["line_color"], width=1.2)
    if hover and td["label"]:
        common["hovertemplate"] = (f"<b>{td['label']}</b><br>"
                                    "x=%{x:.1f} z=%{y:.1f}<extra></extra>")
    fig.add_trace(TraceType(x=td["x"], y=td["y"], mode=td["mode"], **common),
                  row=td["row"], col=td["col"])


def _square_range(xmin, xmax, zmin, zmax, pad=1.08):
    span = max(float(xmax) - float(xmin), float(zmax) - float(zmin)) * pad
    if not np.isfinite(span) or span <= 0:
        vals = [v for v in (xmin, xmax, zmin, zmax) if np.isfinite(v)]
        scale = max(1.0, max(abs(float(v)) for v in vals) if vals else 1.0)
        span = scale * 0.2
    cx, cz = (xmin + xmax) / 2, (zmin + zmax) / 2
    return ([cx - span / 2, cx + span / 2], [cz - span / 2, cz + span / 2])


def _shared_range(df):
    return _square_range(df["GameObjectPosX"].min(), df["GameObjectPosX"].max(),
                         df["GameObjectPosZ"].min(), df["GameObjectPosZ"].max())


def _robust_range(df, pct=98.0):
    """Square range covering the central `pct`% of the pooled data (drops outliers)."""
    lo, hi = (100 - pct) / 2, 100 - (100 - pct) / 2
    x = df["GameObjectPosX"].to_numpy()
    z = df["GameObjectPosZ"].to_numpy()
    return _square_range(np.percentile(x, lo), np.percentile(x, hi),
                         np.percentile(z, lo), np.percentile(z, hi))


def rebase_to_origin(df):
    """Translate every segment so its first sample sits at (0, 0)."""
    g = df.groupby("_seg_id", sort=False)
    out = df.copy()
    out["GameObjectPosX"] = df["GameObjectPosX"].to_numpy() - g["GameObjectPosX"].transform("first").to_numpy()
    out["GameObjectPosZ"] = df["GameObjectPosZ"].to_numpy() - g["GameObjectPosZ"].transform("first").to_numpy()
    return out


def default_bin_size(df) -> float:
    """~1/20 of the 95th-percentile spatial extent — a sensible heatmap pixel."""
    if df is None or len(df) == 0:
        return 20.0
    rx, rz = _robust_range(df, 95.0)
    span = max(rx[1] - rx[0], rz[1] - rz[0])
    bs = span / 20.0
    if bs <= 0:
        return 20.0
    # round to 1 significant figure for a clean default
    import math
    mag = 10 ** math.floor(math.log10(bs))
    return round(bs / mag) * mag


def _apply_axis_sync(fig, nrows, ncols, df, uirev="traj", rng=None):
    total_axes = nrows * ncols
    for i in range(2, total_axes + 1):
        fig.update_layout(**{f"xaxis{i}": dict(matches="x"),
                             f"yaxis{i}": dict(matches="y")})
    fig.update_layout(yaxis=dict(scaleanchor="x", scaleratio=1))
    rx, rz = rng if rng is not None else _shared_range(df)
    fig.update_xaxes(range=rx)
    fig.update_yaxes(range=rz)
    # uirevision keeps zoom state stable across re-renders / tab switches
    fig.update_layout(uirevision=uirev)


_ROI_SIDE_COLOR = {"left": "#1f77b4", "right": "#ff7f0e", "centre": "#6c757d"}


def _subplot_axis(n: int) -> tuple[str, str]:
    """1-based subplot number → its ('x'|'xN', 'y'|'yN') axis refs."""
    return ("x" if n == 1 else f"x{n}"), ("y" if n == 1 else f"y{n}")


def _group_config_keys(gname, gdf, known_keys) -> list[str]:
    keys = []
    seen = set()
    if gname in known_keys:
        keys.append(gname)
        seen.add(gname)
    if gdf is not None and "ConfigFile" in gdf:
        for val in pd.unique(gdf["ConfigFile"].dropna()):
            key = str(val)
            if key in known_keys and key not in seen:
                keys.append(key)
                seen.add(key)
    return keys


def _rois_for_group(gname, gdf, rois_by_cfg) -> list[dict]:
    """ROI centres represented by this subplot, de-duplicated across configs."""
    out = []
    seen = set()
    for cfg in _group_config_keys(gname, gdf, rois_by_cfg or {}):
        for roi in (rois_by_cfg.get(cfg) or []):
            sig = (roi.get("side"),
                   round(float(roi.get("x", 0.0)), 4),
                   round(float(roi.get("z", 0.0)), 4))
            if sig in seen:
                continue
            seen.add(sig)
            out.append(roi)
    return out


def _roi_overlay_shapes(group_items, rois_by_cfg, reach) -> list:
    """Reach circle + centre dot per ROI, as plain shape dicts (so the same list
    can be applied at build time and blitted via Patch on the reach slider)."""
    shapes = []
    dot = max(0.4, reach * 0.08)
    for i, (gname, gdf) in enumerate(group_items):
        rlist = _rois_for_group(gname, gdf, rois_by_cfg)
        if not rlist:
            continue
        sx, sy = _subplot_axis(i + 1)
        for roi in rlist:
            col = _ROI_SIDE_COLOR.get(roi["side"], "#6c757d")
            shapes.append(dict(type="circle", xref=sx, yref=sy, layer="below",
                x0=roi["x"] - reach, x1=roi["x"] + reach,
                y0=roi["z"] - reach, y1=roi["z"] + reach, opacity=0.12,
                fillcolor=col, line=dict(color=col, width=1.4, dash="dot")))
            shapes.append(dict(type="circle", xref=sx, yref=sy, layer="above",
                x0=roi["x"] - dot, x1=roi["x"] + dot,
                y0=roi["z"] - dot, y1=roi["z"] + dot, opacity=0.95,
                fillcolor=col, line=dict(color=col, width=0)))
    return shapes


def _roi_count_texts(gname, gdf, counts) -> tuple[str, str]:
    if counts is None:
        return "", ""

    if isinstance(counts, pd.DataFrame):
        if len(counts) == 0 or gdf is None or len(gdf) == 0 or "_seg_id" not in gdf:
            return "", ""
        segs = pd.unique(gdf["_seg_id"])
        sub = counts[counts["_seg_id"].isin(segs)]
        if len(sub) == 0:
            return "", ""
        total = len(sub)
        left = int(sub["reached_left"].sum())
        right = int(sub["reached_right"].sum())
        return (f"L {left}/{total} ({100 * left / total:.0f}%)",
                f"R {right}/{total} ({100 * right / total:.0f}%)")

    # Backwards-compatible path for callers that still pass roi_config_summary().
    rows = []
    for cfg in _group_config_keys(gname, gdf, counts):
        cc = counts.get(cfg)
        if cc:
            rows.append(cc)
    if not rows:
        return "", ""
    total = sum(int(r["total"]) for r in rows)
    if total <= 0:
        return "", ""
    left = sum(int(r["left_reached"]) for r in rows)
    right = sum(int(r["right_reached"]) for r in rows)
    return (f"L {left}/{total} ({100 * left / total:.0f}%)",
            f"R {right}/{total} ({100 * right / total:.0f}%)")


def _roi_count_annotations(group_items, counts) -> list:
    """Left/right corner-tally annotations per subplot. Fixed slots — index
    n+2*i / n+2*i+1 for group i — so the reach slider can Patch text by index."""
    anns = []
    for i, (gname, gdf) in enumerate(group_items):
        sx, sy = _subplot_axis(i + 1)
        left_txt, right_txt = _roi_count_texts(gname, gdf, counts)
        anns.append(dict(text=left_txt, showarrow=False,
            xref=f"{sx} domain", yref=f"{sy} domain", x=0.01, y=0.98,
            xanchor="left", yanchor="top", align="left",
            font=dict(size=10, color=_ROI_SIDE_COLOR["left"]),
            bgcolor="rgba(255,255,255,0.76)",
            bordercolor=_ROI_SIDE_COLOR["left"], borderwidth=0.6))
        anns.append(dict(text=right_txt, showarrow=False,
            xref=f"{sx} domain", yref=f"{sy} domain", x=0.99, y=0.98,
            xanchor="right", yanchor="top", align="right",
            font=dict(size=10, color=_ROI_SIDE_COLOR["right"]),
            bgcolor="rgba(255,255,255,0.76)",
            bordercolor=_ROI_SIDE_COLOR["right"], borderwidth=0.6))
    return anns


def build_trajectory_figure(df, group_by="config", pool_mode="separate",
                            ncols=2, color_by="individual", animate=True,
                            max_points=None, rois=None, reach_radius=3.0,
                            show_rois=False, roi_counts=None):
    if df is None or len(df) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No data after filtering", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5, font_size=18)
        fig.update_layout(height=400, template="plotly_white")
        return fig

    groups = _group_frames(df, group_by, pool_mode, ncols)
    group_items = list(groups.items())
    group_names = list(groups.keys())
    n = len(group_names)
    nrows = max(1, (n + ncols - 1) // ncols)
    titles = [humanise_config(t) for t in group_names]

    fig = make_subplots(rows=nrows, cols=ncols, subplot_titles=titles,
                        horizontal_spacing=0.05,
                        vertical_spacing=_subplot_spacing(nrows))

    # Point budget. Animation uses a tighter budget because the figure embeds
    # every frame; static (animate off) can afford the full WebGL budget.
    if max_points and max_points > 0:
        budget = int(max_points)
    else:
        budget = BUDGET_SVG if animate else BUDGET_GL

    # Merged, NaN-separated traces (few traces total) — vectorised.
    _, records = _prepare_merged_groups(df, group_by, pool_mode, ncols,
                                        color_by, budget)

    def _rec_to_td(rec, x, y, mc):
        return dict(x=x, y=y, row=rec["row"], col=rec["col"], mode=rec["mode"],
                    line_color=rec["color"], marker_color=mc,
                    colorscale=rec["colorscale"], cmin=rec["cmin"], cmax=rec["cmax"],
                    showlegend=rec["showlegend"], legendgroup=rec["legendgroup"],
                    label=rec["label"])

    # Base traces (full extent)
    for rec in records:
        x, y, mc = _record_arrays(rec, 1.0)
        _add_traj_trace(fig, _rec_to_td(rec, x, y, mc), go.Scattergl)

    # Colourbar for sequential modes (hidden anchor trace, added AFTER the data
    # traces so animation frames update only the data traces)
    if color_by in ("trial", "local_time", "velocity") and records:
        cmin = records[0]["cmin"] if records[0]["cmin"] is not None else 0.0
        cmax = records[0]["cmax"] if records[0]["cmax"] is not None else 1.0
        title = {"trial": "Trial", "local_time": "Local time",
                 "velocity": "Speed (units/s)"}[color_by]
        fig.add_trace(go.Scattergl(
            x=[None], y=[None], mode="markers", showlegend=False, hoverinfo="skip",
            marker=dict(colorscale=SEQ_COLORSCALE, cmin=cmin, cmax=cmax,
                        color=[cmin], showscale=True,
                        colorbar=dict(title=title, thickness=12, len=0.5,
                                      x=1.0, xanchor="left")),
        ), row=1, col=1)

    if animate and records:
        # Frames only — playback is driven by a sticky HTML bar above the graph
        # (always visible regardless of scroll), via clientside Plotly.animate.
        # Build frame traces as plain dicts, not go.Scattergl/go.Frame: Plotly's
        # per-attribute validation on ~250 trace objects was ~1.2 s of pure
        # overhead per replot (dicts cut it to a fraction).
        frames = []
        for fi in range(N_ANIM_FRAMES + 1):
            frac = fi / N_ANIM_FRAMES
            frame_traces = []
            for rec in records:
                x, y, mc = _record_arrays(rec, frac)
                if rec["mode"] == "markers":
                    frame_traces.append(dict(
                        type="scattergl", x=x, y=y, mode="markers", opacity=0.75,
                        marker=dict(size=3, color=mc, colorscale=SEQ_COLORSCALE,
                                    cmin=rec["cmin"], cmax=rec["cmax"])))
                else:
                    frame_traces.append(dict(
                        type="scattergl", x=x, y=y, mode="lines", opacity=0.75,
                        line=dict(color=rec["color"], width=1.2)))
            frames.append(dict(data=frame_traces, name=str(fi)))
        fig.frames = frames

    _apply_axis_sync(fig, nrows, ncols, df, uirev="traj_view")

    for i, ann in enumerate(fig.layout.annotations):
        if i < len(group_names):
            ann.update(hovertext=group_names[i], font=dict(size=12))

    # ROI overlay: reach circles (shapes) + fixed left/right count-annotation
    # slots per subplot (index n+2*i / n+2*i+1), so the reach slider can blit
    # both via Patch without a data rebuild. Slots are always reserved (empty
    # when ROIs are off/rebased).
    reach_v = float(reach_radius or 3.0)
    overlay = bool(show_rois and rois)
    if overlay:
        fig.update_layout(shapes=_roi_overlay_shapes(group_items, rois, reach_v))
    fig.update_layout(annotations=list(fig.layout.annotations)
                      + _roi_count_annotations(group_items,
                                               roi_counts if overlay else None))

    show_legend = color_by in ("individual", "vr")
    fig.update_layout(
        height=60 + nrows * _subplot_px(nrows, ncols),
        showlegend=show_legend,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.01,
                    font_size=10, itemclick="toggle", itemdoubleclick="toggleothers"),
        margin=dict(l=50, r=160, t=50, b=40),
        template="plotly_white", dragmode="pan",
    )
    return fig


MAX_HEATMAP_BINS = 2000  # per axis safety cap
HEATMAP_COLORSCALE = "Viridis"

# Colourbar metrics: each bin's sample count converted to a human unit.
#   count   : raw number of samples in the bin
#   time    : occupancy = count × median sample interval  (seconds)
#   percent : 100 × count / total samples in that subplot  (comparable)
METRIC_UNITS = {"count": "samples", "time": "occupancy (s)", "percent": "% of time"}


def _median_dt(df) -> float:
    """Median sampling interval (seconds), ignoring segment boundaries."""
    t = df["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
    dt = np.diff(t)
    seg = df["_seg_id"].to_numpy()
    same = seg[1:] == seg[:-1]
    dt = dt[same & (dt > 0)]
    return float(np.median(dt)) if len(dt) else 1.0


def _fmt_metric(v: float, metric: str) -> str:
    """Human-readable tick label for a metric value."""
    if metric == "percent":
        return f"{v:g}%"
    if metric == "time":
        if v >= 600:
            return f"{round(v/60):g}m"
        if v >= 1:
            return f"{v:g}s"
        return f"{v*1000:g}ms"
    # count
    if v >= 1000:
        return f"{v/1000:g}k"
    return f"{v:g}"


def _log_colorbar(mmin, mmax, metric):
    """
    Tick positions (in log10 space) + human labels spanning [mmin, mmax],
    so the colourbar reads in real units instead of raw log values.
    """
    if mmin <= 0:
        mmin = mmax / 1e4 if mmax > 0 else 1.0
    lo, hi = np.floor(np.log10(mmin)), np.ceil(np.log10(mmax))
    decades = np.arange(lo, hi + 1)
    # If the range is narrow, add 1-2-5 sub-ticks for readability
    mults = [1] if (hi - lo) > 4 else [1, 2, 5]
    vals, text = [], []
    for d in decades:
        for m in mults:
            v = m * (10.0 ** d)
            if mmin * 0.999 <= v <= mmax * 1.001:
                vals.append(np.log10(v))
                text.append(_fmt_metric(v, metric))
    if not vals:  # degenerate
        vals = [np.log10(max(mmax, 1e-9))]
        text = [_fmt_metric(mmax, metric)]
    return vals, text


def _heatmap_edges(df, bin_size, bound_pct):
    """Shared bin edges + range for a heatmap (metric-independent)."""
    rng = _robust_range(df, bound_pct) if bound_pct and bound_pct < 100 else _shared_range(df)
    rx, rz = rng
    bs = float(bin_size) if bin_size and bin_size > 0 else default_bin_size(df)
    span = max(rx[1] - rx[0], rz[1] - rz[0])
    if not np.isfinite(bs) or bs <= 0:
        bs = max(span / 20.0, 1.0)
    if not np.isfinite(span) or span <= 0:
        span = bs
    if span / bs > MAX_HEATMAP_BINS:        # only clamps in pathological cases
        bs = span / MAX_HEATMAP_BINS
    return np.arange(rx[0], rx[1] + bs, bs), np.arange(rz[0], rz[1] + bs, bs), rng


def _counts_for_groups(groups, group_names, xedges, yedges):
    """Raw-count matrix per group name (all-zero tile when a group is absent), so
    every ROI-mask state shares the SAME grid + subplot set and stays swappable."""
    empty = np.zeros((len(yedges) - 1, len(xedges) - 1))
    out = []
    for gname in group_names:
        gdf = groups.get(gname)
        if gdf is None or len(gdf) == 0:
            out.append(empty.copy())
            continue
        H, _, _ = np.histogram2d(gdf["GameObjectPosX"].values,
                                 gdf["GameObjectPosZ"].values,
                                 bins=[xedges, yedges])
        out.append(H.T.astype(float))       # [row=y, col=x] raw counts
    return out


def _heatmap_bins(df, group_by, pool_mode, ncols, bin_size, bound_pct):
    """The expensive, metric-independent part: 2-D histogram (raw counts) per
    subplot. All metric/scale variants derive from this, so it's computed once."""
    groups = _group_frames(df, group_by, pool_mode, ncols)
    group_names = list(groups.keys())
    nrows = max(1, (len(group_names) + ncols - 1) // ncols)
    xedges, yedges, rng = _heatmap_edges(df, bin_size, bound_pct)
    counts = _counts_for_groups(groups, group_names, xedges, yedges)
    xc = 0.5 * (xedges[:-1] + xedges[1:])
    yc = 0.5 * (yedges[:-1] + yedges[1:])
    return dict(group_names=group_names, nrows=nrows, xc=xc.tolist(),
                yc=yc.tolist(), rng=rng, counts=counts, dt=_median_dt(df))


def _heatmap_variant(bins, metric, log_scale, cmin=None, cmax=None,
                     crange_mode="value"):
    """Cheap: turn the raw-count bins into one metric/scale variant's per-trace
    data (z / customdata / zmin / zmax / colorbar / hover). Used both to assemble
    a figure and to precompute every variant for instant client-side swapping."""
    metric = metric if metric in METRIC_UNITS else "count"
    unit = METRIC_UNITS[metric]
    dt = bins["dt"]
    mats = []
    for H in bins["counts"]:
        M = H.copy()
        if metric == "time":
            M = M * dt
        elif metric == "percent":
            tot = M.sum()
            M = (100.0 * M / tot) if tot > 0 else M
        mats.append(M)
    gmax = max((float(m.max()) if m.size else 0.0) for m in mats) if mats else 0.0
    nonzero = np.concatenate([m[m > 0].ravel() for m in mats]) if mats else np.array([])
    auto_lo = (float(nonzero.min()) if nonzero.size else 1.0) if log_scale else 0.0
    auto_hi = gmax if gmax > 0 else 1.0

    def _resolve(v, default):
        if v is None or v == "":
            return default
        v = float(v)
        if crange_mode == "percentile" and nonzero.size:
            return float(np.percentile(nonzero, max(0.0, min(100.0, v))))
        return v

    mmin = _resolve(cmin, auto_lo)
    mmax = _resolve(cmax, auto_hi)
    if metric == "time":
        mmin = max(mmin, 0.1)
    if log_scale:
        mmin = max(mmin, 1e-9)
    if mmax <= mmin:
        mmax = mmin * 10 if log_scale else mmin + 1

    if log_scale:
        zmin, zmax = float(np.log10(mmin)), float(np.log10(mmax))
        tickvals, ticktext = _log_colorbar(mmin, mmax, metric)
        cbar = dict(title=f"{unit} (log)", thickness=12, len=0.5,
                    tickvals=tickvals, ticktext=ticktext)
    else:
        zmin, zmax = float(mmin), float(mmax)
        cbar = dict(title=unit, thickness=12, len=0.5, tickvals=None, ticktext=None)

    z_list, cd_list = [], []
    for M in mats:
        disp = M.copy()
        disp[disp == 0] = np.nan            # blank empty cells
        z = np.log10(disp) if log_scale else disp
        z_list.append(z.tolist())
        cd_list.append(M.tolist())
    hov = "x=%{x:.1f} z=%{y:.1f}<br>%{customdata:.3g} " + unit + "<extra></extra>"
    return dict(z=z_list, customdata=cd_list, zmin=zmin, zmax=zmax,
                colorbar=cbar, hovertemplate=hov)


def _assemble_heatmap(bins, var, ncols, df):
    """Build the go.Figure structure from the binning + one variant's data.
    z/customdata are plain lists (2-D numpy breaks Dash/Plotly-6 serialisation)."""
    group_names, nrows = bins["group_names"], bins["nrows"]
    fig = make_subplots(rows=nrows, cols=ncols,
                        subplot_titles=[humanise_config(t) for t in group_names],
                        horizontal_spacing=0.05,
                        vertical_spacing=_subplot_spacing(nrows))
    for idx, (z, cd) in enumerate(zip(var["z"], var["customdata"])):
        fig.add_trace(
            go.Heatmap(x=bins["xc"], y=bins["yc"], z=z, customdata=cd,
                       colorscale=HEATMAP_COLORSCALE, zmin=var["zmin"],
                       zmax=var["zmax"], showscale=(idx == 0),
                       colorbar=var["colorbar"], hovertemplate=var["hovertemplate"]),
            row=idx // ncols + 1, col=idx % ncols + 1)
    _apply_axis_sync(fig, nrows, ncols, df, uirev="traj_view", rng=bins["rng"])
    for i, ann in enumerate(fig.layout.annotations):
        if i < len(group_names):
            ann.update(hovertext=group_names[i], font=dict(size=12))
    fig.update_layout(height=60 + nrows * _subplot_px(nrows, ncols),
                      margin=dict(l=50, r=80, t=50, b=40), template="plotly_white",
                      dragmode="pan", showlegend=False)
    return fig


def build_heatmap_figure(df, group_by="config", pool_mode="separate", ncols=2,
                         bin_size=20.0, log_scale=False, bound_pct=98.0,
                         metric="count", cmin=None, cmax=None, crange_mode="value"):
    if df is None or len(df) == 0:
        return _msg_figure("No data after filtering")
    bins = _heatmap_bins(df, group_by, pool_mode, ncols, bin_size, bound_pct)
    var = _heatmap_variant(bins, log_scale=log_scale, metric=metric, cmin=cmin,
                           cmax=cmax, crange_mode=crange_mode)
    return _assemble_heatmap(bins, var, ncols, df)


# metric/scale combinations precomputed so the client can swap between them
# instantly (Plotly.restyle, no server round-trip, no re-init flash).
HEATMAP_METRICS = ("time", "percent", "count")
HEATMAP_SCALES = ("lin", "log")


def _all_variants_from_bins(bins, cmin, cmax, crange_mode):
    return {f"{m}_{s}": _heatmap_variant(bins, log_scale=(s == "log"), metric=m,
                                         cmin=cmin, cmax=cmax, crange_mode=crange_mode)
            for m in HEATMAP_METRICS for s in HEATMAP_SCALES}


def build_heatmap_and_variants(df, group_by, pool_mode, ncols, bin_size, bound_pct,
                               metric, log_scale, cmin, cmax, crange_mode):
    """(figure for the current metric/scale, {all metric×scale variants}) — bins
    ONCE and reuses it, so the store of swap-in data is essentially free."""
    if df is None or len(df) == 0:
        return _msg_figure("No data after filtering"), {}
    bins = _heatmap_bins(df, group_by, pool_mode, ncols, bin_size, bound_pct)
    cur = _heatmap_variant(bins, log_scale=log_scale, metric=metric, cmin=cmin,
                           cmax=cmax, crange_mode=crange_mode)
    fig = _assemble_heatmap(bins, cur, ncols, df)
    return fig, _all_variants_from_bins(bins, cmin, cmax, crange_mode)


def build_heatmap_mask_variants(df_f, pattern, reach, group_by, pool_mode, ncols,
                                bin_size, bound_pct, cmin, cmax, crange_mode,
                                do_rebase, entered_only=False, trim_tail=False):
    """Current ROI-mask heatmap + metric/scale variants for that one state.

    Earlier builds precomputed all four entered-only × tail-trim states. That
    made tab-open expensive on million-row folders and blocked the very plot the
    user was trying to pan. ROI mask toggles now rebuild the current state; metric
    and scale still swap clientside from this state's variants.
    """
    if df_f is None or len(df_f) == 0:
        return _msg_figure("No data after filtering"), {}
    reach_v = float(reach) if reach else 3.0
    df_view, _ = _roi_apply(df_f, pattern, reach_v, entered_only, trim_tail)
    base = rebase_to_origin(df_view) if (do_rebase and len(df_view)) else df_view
    if len(base) == 0:
        return _msg_figure("No data after ROI filtering"), {}
    xedges, yedges, rng = _heatmap_edges(base, bin_size, bound_pct)
    xc = (0.5 * (xedges[:-1] + xedges[1:])).tolist()
    yc = (0.5 * (yedges[:-1] + yedges[1:])).tolist()
    group_names = list(_group_frames(base, group_by, pool_mode, ncols).keys())
    nrows = max(1, (len(group_names) + ncols - 1) // ncols)
    dt = _median_dt(base)

    e, t = int(bool(entered_only)), int(bool(trim_tail))
    groups = _group_frames(base, group_by, pool_mode, ncols)
    bins = dict(group_names=group_names, nrows=nrows, xc=xc, yc=yc, rng=rng,
                counts=_counts_for_groups(groups, group_names, xedges, yedges),
                dt=dt)
    store = {}
    for m in HEATMAP_METRICS:
        for s in HEATMAP_SCALES:
            store[f"e{e}_t{t}_{m}_{s}"] = _heatmap_variant(
                bins, log_scale=(s == "log"), metric=m, cmin=cmin, cmax=cmax,
                crange_mode=crange_mode)
    base_fig = _assemble_heatmap(bins, store[f"e{e}_t{t}_time_lin"], ncols, base)
    return base_fig, store


def build_velocity_histogram(df, vel_threshold=None):
    if df is None or len(df) == 0:
        return go.Figure().update_layout(height=190, template="plotly_white")

    vel = velocity_all(df)
    vel = vel[np.isfinite(vel)]
    if len(vel) == 0:
        return go.Figure().update_layout(height=190, template="plotly_white")

    cap = np.quantile(vel, 0.99)
    vel_show = vel[vel <= cap]

    fig = go.Figure()
    fig.add_trace(go.Histogram(x=vel_show, nbinsx=120, marker_color="#1f77b4",
                                opacity=0.85, name="Velocity"))
    if vel_threshold and vel_threshold > 0:
        fig.add_vline(x=vel_threshold, line_dash="dash", line_color="red", line_width=2)
        pct = 100 * (vel > vel_threshold).sum() / len(vel) if len(vel) else 0
        fig.add_annotation(text=f"Cut {pct:.1f}%", xref="paper", yref="paper",
                           x=0.97, y=0.9, showarrow=False,
                           font=dict(color="red", size=11))

    fig.update_layout(
        height=190, margin=dict(l=40, r=10, t=28, b=25),
        xaxis_title="Velocity (units/s)", yaxis_title="Count",
        title=dict(text="Velocity (99th pctl)", font_size=11, x=0.5),
        template="plotly_white", dragmode="pan",
    )
    return fig


def build_displacement_histogram(stats_df, min_disp=None):
    if stats_df is None or len(stats_df) == 0:
        return go.Figure().update_layout(height=190, template="plotly_white")

    fig = go.Figure()
    fig.add_trace(go.Histogram(x=stats_df["displacement"], nbinsx=50,
                                marker_color="#2ca02c", opacity=0.85, name="Disp"))
    if min_disp and min_disp > 0:
        fig.add_vline(x=min_disp, line_dash="dash", line_color="red", line_width=2)
        n_below = (stats_df["displacement"] < min_disp).sum()
        pct = 100 * n_below / len(stats_df) if len(stats_df) else 0
        fig.add_annotation(text=f"Cut {n_below}/{len(stats_df)} ({pct:.0f}%)",
                           xref="paper", yref="paper", x=0.97, y=0.9, showarrow=False,
                           font=dict(color="red", size=11))

    fig.update_layout(
        height=190, margin=dict(l=40, r=10, t=28, b=25),
        xaxis_title="Net displacement (units)", yaxis_title="Segments",
        title=dict(text="Displacement per segment", font_size=11, x=0.5),
        template="plotly_white", dragmode="pan",
    )
    return fig


def build_raw_trace_figure(df, columns, max_points=None):
    if df is None or len(df) == 0 or not columns:
        return go.Figure().update_layout(height=180, template="plotly_white")

    n = len(columns)
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True,
                        subplot_titles=columns, vertical_spacing=0.15)
    # SVG (go.Scatter), not WebGL: this plot lives in a panel that starts hidden,
    # and a WebGL canvas created while hidden won't paint. Use a smaller budget
    # so SVG stays light.
    budget = int(max_points) if (max_points and max_points > 0) else 8000
    step = max(1, len(df) // budget)
    sub = df.sort_values("Current Time").iloc[::step]
    for i, col in enumerate(columns):
        if col not in sub.columns:
            continue
        fig.add_trace(
            go.Scatter(x=sub["Current Time"], y=sub[col], mode="lines",
                       opacity=0.55,
                       line=dict(width=1, color=COLORS[i % len(COLORS)]), name=col),
            row=i + 1, col=1,
        )
    fig.update_layout(height=max(180, n * 140), margin=dict(l=50, r=10, t=25, b=20),
                       template="plotly_white", showlegend=False, dragmode="pan")
    return fig


def _msg_figure(text, height=440):
    fig = go.Figure()
    fig.add_annotation(text=text, showarrow=False, xref="paper", yref="paper",
                       x=0.5, y=0.5, font_size=15)
    fig.update_layout(height=height, template="plotly_white")
    return fig


def build_roi_swarm_figure(df, rois_by_cfg, reach, table=None):
    """Stacked ROI diagnostics:
      1. Paired swarm — per-animal fraction of trials reaching left vs right, with
         faint left↔right pairing lines and a median bar per side.
      2. Split violin of time-to-reach the target (left half / right half), area
         proportional to the number of trials that reached (scalemode='count').
      3. Split violin of signed heading error to left/right target centres
         (0° = pointing at target, span -180..180°).
    `df` is already the filtered/visible subset; pass `table` to skip recompute."""
    tbl = roi_reached_table(df, rois_by_cfg, reach) if table is None else table
    if tbl is None or len(tbl) == 0:
        return _msg_figure("No left/right ROI targets in these configs — "
                           "nothing to count. Load Choice/BinaryChoice data.")
    grp = tbl.groupby(["ConfigFile", "animal"], sort=False, observed=True).agg(
        frac_left=("reached_left", "mean"),
        frac_right=("reached_right", "mean"),
        reach_left=("reached_left", "sum"),
        reach_right=("reached_right", "sum"),
        trials=("_seg_id", "size")).reset_index()
    grp["label"] = grp["ConfigFile"].map(humanise_config)
    labels = sorted(grp["label"].unique())
    xpos = {lab: i for i, lab in enumerate(labels)}
    n_animals = grp["animal"].nunique()

    base = grp["label"].map(xpos).to_numpy().astype(float)
    rng = np.random.default_rng(0)
    jit = lambda: (rng.random(len(grp)) - 0.5) * 0.18
    lx, rx = base - 0.2 + jit(), base + 0.2 + jit()
    ly, ry = grp["frac_left"].to_numpy(), grp["frac_right"].to_numpy()
    px = np.empty(len(grp) * 3); px[0::3], px[1::3], px[2::3] = lx, rx, np.nan
    py = np.empty(len(grp) * 3); py[0::3], py[1::3], py[2::3] = ly, ry, np.nan
    med = grp.groupby("label").agg(ml=("frac_left", "median"), mr=("frac_right", "median"))
    mlx, mly, mrx, mry = [], [], [], []
    for lab, i in xpos.items():
        if lab in med.index:
            mlx += [i - 0.36, i - 0.04, None]; mly += [med.loc[lab, "ml"]] * 2 + [None]
            mrx += [i + 0.04, i + 0.36, None]; mry += [med.loc[lab, "mr"]] * 2 + [None]

    lc, rc = _ROI_SIDE_COLOR["left"], _ROI_SIDE_COLOR["right"]
    fig = make_subplots(rows=3, cols=1, vertical_spacing=0.10, subplot_titles=(
        f"Fraction of trials reaching each ROI — per animal "
        f"(reach {reach:g} u · {n_animals} animals; bars = median)",
        "Time to reach target (split violin; area ∝ trials reached; box = median/IQR)",
        "Heading error to left/right target centres (split violin; box = median/IQR)"))

    fig.add_trace(go.Scatter(x=px.tolist(), y=py.tolist(), mode="lines",
        line=dict(color="rgba(120,120,120,0.35)", width=1),
        hoverinfo="skip", showlegend=False), row=1, col=1)
    left_cd = grp[["animal", "reach_left", "trials"]].to_numpy()
    right_cd = grp[["animal", "reach_right", "trials"]].to_numpy()
    fig.add_trace(go.Scatter(x=lx.tolist(), y=ly.tolist(), mode="markers", name="Left",
        legendgroup="left", marker=dict(color=lc, size=6, opacity=0.75,
        line=dict(width=0.5, color="#333")), customdata=left_cd,
        hovertemplate=("Left %{customdata[1]:.0f}/%{customdata[2]:.0f} trials"
                       "<br>fraction %{y:.2f}<br>%{customdata[0]}<extra></extra>")),
        row=1, col=1)
    fig.add_trace(go.Scatter(x=rx.tolist(), y=ry.tolist(), mode="markers", name="Right",
        legendgroup="right", marker=dict(color=rc, size=6, opacity=0.75,
        line=dict(width=0.5, color="#333")), customdata=right_cd,
        hovertemplate=("Right %{customdata[1]:.0f}/%{customdata[2]:.0f} trials"
                       "<br>fraction %{y:.2f}<br>%{customdata[0]}<extra></extra>")),
        row=1, col=1)
    fig.add_trace(go.Scatter(x=mlx, y=mly, mode="lines", showlegend=False,
        line=dict(color=lc, width=3), hoverinfo="skip"), row=1, col=1)
    fig.add_trace(go.Scatter(x=mrx, y=mry, mode="lines", showlegend=False,
        line=dict(color=rc, width=3), hoverinfo="skip"), row=1, col=1)

    # --- panel 2: time-to-target split violin ---
    ttt = time_to_target_table(df, rois_by_cfg, reach)
    if len(ttt):
        ttt["label"] = ttt["ConfigFile"].map(humanise_config)
        for side, sd, color in (("left", "negative", lc), ("right", "positive", rc)):
            s = ttt[ttt["side"] == side]
            if not len(s):
                continue
            fig.add_trace(go.Violin(
                x=s["label"].map(xpos), y=s["t"], side=sd, scalemode="count", scalegroup="ttt",
                line_color=color, fillcolor=color, opacity=0.55, points=False,
                meanline_visible=False, box_visible=True, showlegend=False, spanmode="hard",
                hovertemplate=side + " %{y:.1f}s<extra></extra>"), row=2, col=1)

    ang = heading_target_angle_table(df, rois_by_cfg)
    if len(ang):
        ang["label"] = ang["ConfigFile"].map(humanise_config)
        budget = 40_000
        if len(ang) > budget:
            ang = ang.iloc[np.linspace(0, len(ang) - 1, budget).astype(int)]
        for side, sd, color in (("left", "negative", lc), ("right", "positive", rc)):
            s = ang[ang["side"] == side]
            if not len(s):
                continue
            fig.add_trace(go.Violin(
                x=s["label"].map(xpos), y=s["angle_deg"], side=sd,
                scalemode="count", scalegroup="angle", line_color=color,
                fillcolor=color, opacity=0.45, points=False,
                meanline_visible=False, box_visible=True, showlegend=False,
                span=[-180, 180],
                hovertemplate=side + " %{y:.0f}° from target centre<extra></extra>"),
                row=3, col=1)

    fig.update_layout(template="plotly_white", height=980, violinmode="overlay",
        legend=dict(orientation="h", y=1.05, yanchor="bottom", x=1, xanchor="right"),
        margin=dict(l=60, r=20, t=50, b=80), dragmode="pan")
    fig.update_yaxes(title_text="fraction reaching", range=[-0.03, 1.03], row=1, col=1)
    fig.update_yaxes(title_text="time to reach (s)", rangemode="tozero", row=2, col=1)
    fig.update_yaxes(title_text="heading error (deg)", range=[-180, 180],
                     zeroline=True, zerolinewidth=1.5, zerolinecolor="#555",
                     row=3, col=1)
    fig.update_xaxes(tickmode="array", tickvals=list(range(len(labels))),
                     ticktext=labels, range=[-0.6, len(labels) - 0.4], row=1, col=1)
    fig.update_xaxes(tickmode="array", tickvals=list(range(len(labels))),
                     ticktext=labels, range=[-0.6, len(labels) - 0.4],
                     title_text="config", row=2, col=1)
    fig.update_xaxes(tickmode="array", tickvals=list(range(len(labels))),
                     ticktext=labels, range=[-0.6, len(labels) - 0.4],
                     title_text="config", row=3, col=1)
    fig.update_xaxes(matches="x")
    return fig


def _ray_cache_key(df, moving_only, walk_thresh, color_by):
    return (id(df), len(df), bool(moving_only),
            round(float(walk_thresh or 0), 6), color_by or "none")


def _cache_ray(key, val):
    _POLAR_RAY_CACHE[key] = val
    _POLAR_RAY_CACHE_ORDER.append(key)
    while len(_POLAR_RAY_CACHE_ORDER) > _POLAR_RAY_CACHE_MAX:
        _POLAR_RAY_CACHE.pop(_POLAR_RAY_CACHE_ORDER.pop(0), None)
    return val


def rayleigh_by_segment(df, moving_only=False, walk_thresh=None,
                        color_by="velocity", use_cache=True) -> pd.DataFrame:
    """Per-trial Rayleigh (mean resultant) vector of the per-sample headings.
    Returns _seg_id, ConfigFile, animal, R (0..1 concentration), theta_deg (mean
    direction, Unity frame: 0=forward, via atan2(dx,dz)), and a per-trial colour
    value (mean speed / tortuosity). Fully vectorised — no per-segment Python."""
    cols = ["_seg_id", "ConfigFile", "animal", "R", "theta_deg", "cval"]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=cols)
    key = _ray_cache_key(df, moving_only, walk_thresh, color_by)
    if use_cache and key in _POLAR_RAY_CACHE:
        return _POLAR_RAY_CACHE[key]
    x = df["GameObjectPosX"].to_numpy(); z = df["GameObjectPosZ"].to_numpy()
    seg = df["_seg_id"].to_numpy()
    n = len(df)
    dx = np.empty(n); dx[0] = np.nan; dx[1:] = np.diff(x)
    dz = np.empty(n); dz[0] = np.nan; dz[1:] = np.diff(z)
    seg_start = np.empty(n, bool); seg_start[0] = True
    seg_start[1:] = seg[1:] != seg[:-1]
    dx[seg_start] = np.nan; dz[seg_start] = np.nan
    mag = np.hypot(dx, dz)
    with np.errstate(invalid="ignore", divide="ignore"):
        ux = dx / mag; uz = dz / mag                 # unit heading components
    ux[~np.isfinite(ux)] = np.nan
    uz[~np.isfinite(uz)] = np.nan

    speed = None
    if moving_only and walk_thresh:
        speed = smoothed_velocity(df, 10)
        slow = ~(speed >= float(walk_thresh))
        ux[slow] = np.nan; uz[slow] = np.nan

    if color_by == "velocity":
        cvals = speed if speed is not None else smoothed_velocity(df, 10)
    elif color_by == "tortuosity":
        cvals = compute_tortuosity(df)
    else:
        cvals = np.full(n, np.nan)

    agg = (pd.DataFrame({"_seg_id": seg, "ux": ux, "uz": uz, "cval": cvals})
           .groupby("_seg_id", sort=False)
           .agg(ux=("ux", "mean"), uz=("uz", "mean"), cval=("cval", "mean")))
    R = np.hypot(agg["ux"].to_numpy(), agg["uz"].to_numpy())
    theta = np.degrees(np.arctan2(agg["ux"].to_numpy(), agg["uz"].to_numpy()))
    meta = df.groupby("_seg_id", sort=False).agg(
        ConfigFile=("ConfigFile", "first"), VR=("VR", "first"),
        FlyID=("FlyID", "first"))
    out = pd.DataFrame({"_seg_id": agg.index, "R": R, "theta_deg": theta,
                        "cval": agg["cval"].to_numpy()}).merge(
        meta.reset_index(), on="_seg_id")
    out["animal"] = out["FlyID"].astype(str) + "@" + out["VR"].astype(str)
    return _cache_ray(key, out[cols]) if use_cache else out[cols]


def precache_polar_rays(df, walk_thresh, color_by):
    if df is None or len(df) == 0:
        return
    rayleigh_by_segment(df, False, walk_thresh, color_by)
    rayleigh_by_segment(df, True, walk_thresh, color_by)


def build_polar_figure(df, group_by="config", pool_mode="separate", ncols=2,
                       color_by="velocity", moving_only=False, walk_thresh=None,
                       max_points=None, rois=None, reach_radius=3.0, show_rois=False):
    """One **Rayleigh mean-vector per trial**: angle = the trial's mean heading,
    radius R (0..1) = how directed it was (0 = wandering, 1 = perfectly straight).
    Unity left-handed frame (0° = forward, clockwise) so it lines up with the
    trajectory view and the ROI directions. Colour = per-trial mean speed or
    tortuosity. ROI target directions are drawn as reference spokes."""
    if df is None or len(df) == 0:
        return _msg_figure("No data after filtering")
    ray = rayleigh_by_segment(df, moving_only, walk_thresh, color_by)
    ray = ray.dropna(subset=["R", "theta_deg"])
    if len(ray) == 0:
        return _msg_figure("No headings to plot — lower the walk-speed threshold "
                           "(it may be above every sample's speed).")

    groups = _group_frames(df, group_by, pool_mode, ncols)
    names = list(groups.keys())
    n = len(names)
    nrows = max(1, (n + ncols - 1) // ncols)
    # seg -> subplot-group map (vectorised via concat of per-group index labels)
    seg_group = pd.concat([pd.Series(gname, index=g["_seg_id"].unique())
                           for gname, g in groups.items()]) if names else pd.Series(dtype=object)
    ray = ray.assign(group=ray["_seg_id"].map(seg_group))

    specs = [[{"type": "polar"} for _ in range(ncols)] for _ in range(nrows)]
    vspace = min(0.06, 0.5 / max(nrows, 1))
    fig = make_subplots(rows=nrows, cols=ncols, specs=specs,
                        subplot_titles=[humanise_config(t) for t in names],
                        horizontal_spacing=0.06, vertical_spacing=vspace)

    for idx, gname in enumerate(names):
        row, col = idx // ncols + 1, idx % ncols + 1
        sub = ray[ray["group"] == gname]

        # ROI target directions (dotted spokes), under the lines.
        if show_rois and rois:
            for roi in rois.get(gname, []):
                th_ = math.degrees(math.atan2(roi["x"], roi["z"]))
                fig.add_trace(go.Scatterpolar(
                    r=[0, 1], theta=[th_, th_], mode="lines", showlegend=False,
                    hoverinfo="skip", line=dict(width=1.4, dash="dot",
                    color=_ROI_SIDE_COLOR.get(roi["side"], "#999"))),
                    row=row, col=col)

        R = sub["R"].to_numpy(); th = sub["theta_deg"].to_numpy()
        # one radial LINE per trial: centre → (R, θ)
        rr = np.empty(len(sub) * 3); rr[0::3], rr[1::3], rr[2::3] = 0.0, R, np.nan
        tt = np.empty(len(sub) * 3); tt[0::3], tt[1::3], tt[2::3] = th, th, np.nan
        fig.add_trace(go.Scatterpolar(
            r=rr.tolist(), theta=tt.tolist(), mode="lines", showlegend=False,
            hoverinfo="skip", line=dict(color="rgba(46,160,80,0.4)", width=1)),
            row=row, col=col)

        # population mean resultant vector (bold) + Rayleigh significance mark
        thr = np.radians(th)
        vx, vz = R * np.sin(thr), R * np.cos(thr)
        mvx, mvz = float(np.nanmean(vx)), float(np.nanmean(vz))
        Rpop = math.hypot(mvx, mvz)
        thpop = math.degrees(math.atan2(mvx, mvz))
        fig.add_trace(go.Scatterpolar(
            r=[0, Rpop], theta=[thpop, thpop], mode="lines", showlegend=False,
            hovertemplate=f"mean R={Rpop:.2f} θ={thpop:.0f}°<extra></extra>",
            line=dict(color="#0b6b2e", width=3)), row=row, col=col)
        p = math.exp(-len(sub) * Rpop * Rpop) if len(sub) else 1.0
        star = "***" if p < 1e-3 else "**" if p < 1e-2 else "*" if p < 5e-2 else ""
        if star:
            ann = fig.layout.annotations[idx]
            ann.update(text=f"{ann.text} {star}")

    # 0° at top, clockwise — matches the trajectory frame. R is a 0..1 unit disk.
    fig.update_polars(angularaxis=dict(rotation=90, direction="clockwise",
                                       thetaunit="degrees"),
                      radialaxis=dict(range=[0, 1], angle=90, tickangle=90,
                                      tickvals=[0.25, 0.5, 0.75, 1.0]),
                      bgcolor="white")
    fig.update_layout(height=60 + nrows * 420, template="plotly_white",
                      margin=dict(l=40, r=90, t=50, b=40), showlegend=False)
    return fig



# ---------------------------------------------------------------------------
# Dash App
# ---------------------------------------------------------------------------

app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "Trajectory Dashboard"

_load_config_lut()      # restore any saved / hand-edited config names

_DATA_CACHE: dict[str, pd.DataFrame] = {}
_STATS_CACHE: dict[str, pd.DataFrame] = {}
_META_CACHE: dict[str, list[dict]] = {}

# Live load progress, polled by a dcc.Interval while a load runs.
_LOAD_PROGRESS = {"done": 0, "total": 0, "active": False, "label": ""}


_DROP_PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__",
               ".next", "dist", "build", ".cache", "Library", ".Trash"}


def _search_roots() -> list[str]:
    """Sensible places a dropped data folder might live: the working dir and a
    couple of ancestors (data usually sits in a sibling ``Data/`` tree, not under
    the app dir). Optional env override for data kept elsewhere."""
    cwd = os.path.abspath(os.getcwd())
    roots = [cwd, os.path.dirname(cwd), os.path.dirname(os.path.dirname(cwd))]
    env = os.environ.get("TRAJ_DATA_ROOT")
    if env:
        roots.insert(0, os.path.abspath(os.path.expanduser(env)))
    seen, out = set(), []
    for r in roots:
        if r and r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def resolve_dropped_folder(folder: str, files: list[str]) -> str | None:
    """
    Turn a dropped folder (top name + relative CSV paths) into a glob pattern by
    locating that folder on disk. Browsers never expose the absolute path, so we
    search the working dir *and nearby ancestors* (a bounded, pruned walk that
    stops at the first confirmed match) — data commonly lives in a sibling
    ``Data/`` tree, not under the app directory, which is why a cwd-only search
    used to fail with "couldn't locate '<folder>' on disk".
    """
    files = [f for f in (files or []) if f.lower().endswith(".csv")]
    if not files:
        return None
    names = [f.rsplit("/", 1)[-1] for f in files]
    star = "*_VR*.csv" if any("_VR" in n for n in names) else "*.csv"
    sample_sub = files[0].split("/", 1)[1] if "/" in files[0] else None

    def _match(dirpath: str) -> bool:
        if folder and os.path.basename(dirpath) != folder:
            return False
        if sample_sub is not None:
            return os.path.exists(os.path.join(dirpath, sample_sub))
        # No sub-path (flat folder): confirm it actually holds one of the CSVs.
        return os.path.exists(os.path.join(dirpath, names[0]))

    base = None
    visited = 0
    for root in _search_roots():
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, _ in os.walk(root):
            visited += 1
            if visited > 120_000:                 # hard cap so a miss can't hang
                break
            if dirpath.count(os.sep) - base_depth >= 8:
                dirnames[:] = []                   # depth-limit the descent
                continue
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d not in _DROP_PRUNE]
            if _match(dirpath):
                base = dirpath
                break
        if base:
            break
    if not base:
        return None

    pat = os.path.join(base, "**", star)
    if not glob.glob(pat, recursive=True):
        pat = os.path.join(base, "**", "*.csv")
    cwd = os.getcwd()
    return os.path.relpath(pat, cwd) if pat.startswith(cwd + os.sep) else pat


def _load_data(pattern):
    key = pattern.strip()
    if key in _DATA_CACHE:
        metas = _META_CACHE.get(key, [])
        _set_config_order(metas)
        return _DATA_CACHE[key], _STATS_CACHE.get(key), metas

    files = find_csv_files(pattern)
    _LOAD_PROGRESS.update(done=0, total=len(files), active=True, label="scanning")
    if not files:
        _LOAD_PROGRESS.update(active=False)
        return None, None, []

    dfs, metas, seen = [], [], set()
    for i, f in enumerate(files):
        d = load_csv_fast(f)
        if d is not None:
            dfs.append(d)
        folder = os.path.dirname(f)
        if folder not in seen:
            seen.add(folder)
            metas.append(load_folder_metadata(folder))
        _LOAD_PROGRESS.update(done=i + 1, total=len(files), active=True,
                              label=os.path.basename(f))

    if not dfs:
        _LOAD_PROGRESS.update(active=False)
        return None, None, metas

    _LOAD_PROGRESS.update(label="concatenating")
    df = pd.concat(dfs, ignore_index=True)
    # Sort by SourceFile (not just folder+VR) so a restart file's rows don't
    # interleave by time with the original's — keeps every _seg_id contiguous,
    # which the vectorised segment reductions rely on.
    df.sort_values(["SourceFolder", "SourceFile", "CurrentTrial", "CurrentStep",
                    "Current Time"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    stats = compute_segment_stats(df)
    for c in ("ConfigFile", "SceneName", "VR", "FlyID", "Sex",
              "SourceFolder", "SourceFile"):
        if c in df.columns:
            df[c] = df[c].astype("category")
    _set_config_order(metas)
    _populate_auto_lut(metas)           # readable config names from objects
    _DATA_CACHE[key] = df
    _STATS_CACHE[key] = stats
    _META_CACHE[key] = metas
    _LOAD_PROGRESS.update(active=False)
    return df, stats, metas


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

_EMPTY = go.Figure().update_layout(height=190, template="plotly_white")
_INPUT_STYLE = {"width": "100%", "fontSize": "11px", "padding": "3px",
                "boxSizing": "border-box"}

# Each view panel fills the panels-wrapper and is hidden via `visibility` so its
# graph keeps full dimensions (Plotly never sees a 0-size container).
_PANEL_STYLE = {"position": "absolute", "top": 0, "left": 0, "right": 0,
                "bottom": 0, "overflowY": "auto"}

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),

    # Header
    html.Div([
        html.H3("Trajectory Dashboard",
                style={"margin": "0", "flex": "1", "fontSize": "17px"}),
        html.Button("Export HTML", id="btn-export", n_clicks=0,
                    style={"fontSize": "11px", "padding": "4px 10px"}),
        dcc.Download(id="download-html"),
    ], style={"display": "flex", "alignItems": "center", "padding": "6px 14px",
              "borderBottom": "2px solid #ddd", "background": "#f8f9fa", "gap": "10px"}),

    html.Div([
        # ---- Sidebar ----
        html.Div([
            html.Label("Glob / Folder Path", style={"fontWeight": "bold", "fontSize": "12px"}),
            # Drag-and-drop a folder (or click to pick) → auto-builds a glob.
            html.Div([
                html.Div("📁", style={"fontSize": "26px", "lineHeight": "1",
                                       "pointerEvents": "none"}),
                html.Div("Drop a data folder here", id="drop-label",
                         style={"fontSize": "13px", "fontWeight": "bold", "color": "#445",
                                "marginTop": "4px", "pointerEvents": "none"}),
                html.Div("or click to choose — finds every nested CSV",
                         id="drop-sub",
                         style={"fontSize": "10px", "color": "#99a", "marginTop": "2px",
                                "pointerEvents": "none"}),
            ], id="drop-zone",
               style={"border": "2px dashed #aac", "borderRadius": "8px",
                      "padding": "22px 10px", "textAlign": "center", "cursor": "pointer",
                      "background": "#f4f6fb", "marginBottom": "5px",
                      "display": "flex", "flexDirection": "column", "alignItems": "center",
                      "justifyContent": "center", "minHeight": "92px",
                      "transition": "background .15s, border-color .15s"}),
            dcc.Input(id="glob-input", type="text", value="", debounce=True,
                      placeholder="Data/2025*/*_VR*.csv",
                      style={"width": "100%", "padding": "4px", "fontSize": "11px",
                             "fontFamily": "monospace", "boxSizing": "border-box"}),
            html.Button("Load & Plot", id="btn-load", n_clicks=0,
                        style={"width": "100%", "marginTop": "3px", "padding": "5px",
                               "background": "#0d6efd", "color": "white", "border": "none",
                               "cursor": "pointer", "fontSize": "12px", "borderRadius": "3px"}),
            # Loading progress UI
            html.Div([
                html.Div(id="load-progress-bar",
                         style={"height": "100%", "width": "0%", "background": "#0d6efd",
                                "borderRadius": "3px", "transition": "width .2s"}),
            ], id="load-progress-track",
               style={"display": "none", "height": "6px", "background": "#e3e6ee",
                      "borderRadius": "3px", "marginTop": "4px", "overflow": "hidden"}),
            html.Div(id="load-status", style={"fontSize": "10px", "color": "#666",
                                                "marginTop": "2px"}),
            html.Hr(style={"margin": "6px 0"}),

            html.Label("Group By", style={"fontWeight": "bold", "fontSize": "12px"}),
            dcc.Dropdown(id="group-by", options=[
                {"label": "Config / Treatment", "value": "config"},
                {"label": "Scene", "value": "scene"},
                {"label": "VR", "value": "vr"},
                {"label": "Fly ID", "value": "flyid"},
                {"label": "Source Folder", "value": "file"},
                {"label": "All Pooled", "value": "all"},
            ], value="config", clearable=False, style={"fontSize": "11px"}),

            dcc.RadioItems(id="pool-mode", options=[
                {"label": "Separate subplots", "value": "separate"},
                {"label": "Pool into one", "value": "pooled"},
            ], value="separate", style={"fontSize": "11px", "marginTop": "3px"}),
            dcc.Checklist(id="show-raw-config",
                          options=[{"label": " Show raw config filenames",
                                    "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "3px"}),
            html.Div("Titles default to readable stimulus names from the config "
                     "objects (e.g. 'tree vs empty'); flip/noflip is mirror "
                     "symmetry and is hidden. Edit names in the Advanced LUT.",
                     style={"fontSize": "9px", "color": "#888"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Colour By", style={"fontWeight": "bold", "fontSize": "12px"}),
            dcc.Dropdown(id="color-by", options=[
                {"label": "Individual (VR+Fly)", "value": "individual"},
                {"label": "VR", "value": "vr"},
                {"label": "Trial (sequential)", "value": "trial"},
                {"label": "Local time (sequential)", "value": "local_time"},
                {"label": "Velocity (units/s, smoothed)", "value": "velocity"},
            ], value="individual", clearable=False, style={"fontSize": "11px"}),
            dcc.Checklist(id="animate-toggle",
                          options=[{"label": " Playback animation", "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "3px"}),
            dcc.Checklist(id="rebase-origin",
                          options=[{"label": " Start each track at origin (0,0)", "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "1px"}),
            html.Div("Playback on: slider + play/pause (≤20k pts). Off: crisp static "
                     "(≤300k pts). Rebase overlays every track's first point at 0.",
                     style={"fontSize": "9px", "color": "#888"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Heatmap", style={"fontWeight": "bold", "fontSize": "12px"}),
            html.Div([
                html.Div([
                    html.Label("Bin size (units)", style={"fontSize": "10px"}),
                    dcc.Input(id="heatmap-binsize", type="number", value=None, min=0,
                              step="any", debounce=True, placeholder="auto",
                              style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("Bound %", style={"fontSize": "10px"}),
                    dcc.Input(id="heatmap-bound", type="number", value=98, min=50,
                              max=100, step="any", debounce=True,
                              style=_INPUT_STYLE),
                ], style={"flex": "1"}),
            ], style={"display": "flex", "gap": "6px"}),
            html.Div([
                html.Div([
                    html.Label("Scale", style={"fontSize": "10px"}),
                    dcc.RadioItems(id="heatmap-scale", options=[
                        {"label": "lin", "value": "lin"},
                        {"label": "log", "value": "log"},
                    ], value="lin", style={"fontSize": "10px"}, inline=True),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("Metric", style={"fontSize": "10px"}),
                    dcc.Dropdown(id="heatmap-metric", options=[
                        {"label": "Occupancy (s)", "value": "time"},
                        {"label": "% of time", "value": "percent"},
                        {"label": "Sample count", "value": "count"},
                    ], value="time", clearable=False, style={"fontSize": "10px"}),
                ], style={"flex": "1.4"}),
            ], style={"display": "flex", "gap": "6px", "marginTop": "3px"}),
            html.Div([
                html.Div([
                    html.Label("cmin", style={"fontSize": "10px"}),
                    dcc.Input(id="heatmap-cmin", type="number", value=None,
                              placeholder="auto", step="any", debounce=True,
                              style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("cmax", style={"fontSize": "10px"}),
                    dcc.Input(id="heatmap-cmax", type="number", value=None,
                              placeholder="auto", step="any", debounce=True,
                              style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("as", style={"fontSize": "10px"}),
                    dcc.RadioItems(id="heatmap-crange", options=[
                        {"label": "val", "value": "value"},
                        {"label": "pct", "value": "percentile"},
                    ], value="value", style={"fontSize": "10px"}, inline=True),
                ], style={"flex": "1.2"}),
            ], style={"display": "flex", "gap": "6px", "marginTop": "3px"}),
            html.Div("Pixel = N data units (square; decimals ok). Bound% clips "
                     "extent to that central percentile (100 = full). "
                     "cmin/cmax blank = auto; 'pct' reads them as data percentiles. "
                     "Occupancy floored at 100 ms. Bin/Bound/cmin/cmax apply on Enter or blur.",
                     style={"fontSize": "9px", "color": "#888"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("ROI / Targets", style={"fontWeight": "bold", "fontSize": "12px"}),
            dcc.Checklist(id="roi-show",
                          options=[{"label": " Show target ROIs + reached counts",
                                    "value": "on"}],
                          value=["on"], style={"fontSize": "11px"}),
            html.Label("Reach radius (units)", style={"fontSize": "10px",
                                                       "marginTop": "4px"}),
            dcc.Slider(id="roi-reach", min=0.5, max=30, step=0.5, value=3,
                       marks={1: "1", 10: "10", 20: "20", 30: "30"},
                       tooltip={"placement": "bottom", "always_visible": True}),
            dcc.Checklist(id="roi-entered",
                          options=[{"label": " Only trials that entered an ROI",
                                    "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "3px"}),
            dcc.Checklist(id="roi-trim",
                          options=[{"label": " Trim trial tail after ROI exit",
                                    "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "1px"}),
            html.Div("Targets auto-load from the scene configs (Choice / "
                     "BinaryChoice; polar or cartesian). A trial 'reaches' an ROI "
                     "if its path comes within the radius. Left = −X, right = +X. "
                     "Tail-trim drops each trial's path after it first leaves an "
                     "ROI it entered (keeps approach + first contact).",
                     style={"fontSize": "9px", "color": "#888", "marginTop": "2px"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Polar", style={"fontWeight": "bold", "fontSize": "12px"}),
            html.Label("Colour by", style={"fontSize": "10px"}),
            dcc.RadioItems(id="polar-color", options=[
                {"label": " Velocity", "value": "velocity"},
                {"label": " Tortuosity", "value": "tortuosity"},
                {"label": " Plain", "value": "none"},
            ], value="velocity", inline=True, style={"fontSize": "10px"}),
            dcc.Checklist(id="polar-moving",
                          options=[{"label": " Moving samples only", "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "3px"}),
            html.Label("Walk speed threshold (units/s)", style={"fontSize": "10px",
                                                             "marginTop": "2px"}),
            dcc.Input(id="polar-walk", type="number", value=1, min=0, step="any",
                      debounce=True, style=_INPUT_STYLE),
            html.Div("Each trial's path as r (distance from origin) vs angle "
                     "(0° = forward, clockwise — same frame as trajectories). "
                     "Moving-only keeps samples above the walk speed; ROI targets "
                     "appear as rings.",
                     style={"fontSize": "9px", "color": "#888", "marginTop": "2px"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Filters", style={"fontWeight": "bold", "fontSize": "12px"}),
            html.Div("Filters apply on Re-Plot.",
                     style={"fontSize": "9px", "color": "#888"}),
            html.Div([
                html.Label("Max velocity (units/s)", style={"fontSize": "10px"}),
                dcc.Input(id="vel-threshold", type="number", value=None,
                          placeholder="blank = no cut", debounce=True,
                          style=_INPUT_STYLE),
                dcc.Checklist(id="vel-auto",
                              options=[{"label": " auto (99th pct)", "value": "on"}],
                              value=["on"], style={"fontSize": "9px"}),
            ], style={"marginBottom": "3px"}),
            html.Div([
                html.Label("Min displacement", style={"fontSize": "10px"}),
                dcc.Input(id="min-disp", type="number", value=None,
                          placeholder="blank = no cut", debounce=True,
                          style=_INPUT_STYLE),
                dcc.Checklist(id="disp-auto",
                              options=[{"label": " auto (5% of median)", "value": "on"}],
                              value=["on"], style={"fontSize": "9px"}),
            ], style={"marginBottom": "3px"}),
            html.Div([
                html.Div([
                    html.Label("Trim edge samples", style={"fontSize": "10px"}),
                    dcc.Input(id="trim-samples", type="number", value=100,
                              debounce=True, style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("Spike buffer (ms)", style={"fontSize": "10px"}),
                    dcc.Input(id="jump-buffer", type="number", value=100, min=0,
                              step=10, debounce=True, style=_INPUT_STYLE),
                ], style={"flex": "1"}),
            ], style={"display": "flex", "gap": "6px", "marginBottom": "3px"}),

            html.Button("Re-Plot", id="btn-plot", n_clicks=0,
                        style={"width": "100%", "marginTop": "4px", "padding": "5px",
                               "border": "1px solid #0d6efd", "background": "white",
                               "color": "#0d6efd", "cursor": "pointer", "fontSize": "12px",
                               "borderRadius": "3px"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Subset Filters", style={"fontWeight": "bold", "fontSize": "12px"}),
            html.Label("Configs", style={"fontSize": "10px"}),
            dcc.Dropdown(id="filter-configs", multi=True, placeholder="All",
                         style={"fontSize": "10px"}),
            html.Label("VRs", style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Dropdown(id="filter-vrs", multi=True, placeholder="All",
                         style={"fontSize": "10px"}),
            html.Label("Fly IDs", style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Dropdown(id="filter-flyids", multi=True, placeholder="All",
                         style={"fontSize": "10px"}),
            html.Label("Scenes", style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Dropdown(id="filter-scenes", multi=True, placeholder="All",
                         style={"fontSize": "10px"}),
            html.Label("Folders", style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Dropdown(id="filter-folders", multi=True, placeholder="All",
                         style={"fontSize": "10px"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
                html.Summary("Advanced", style={"fontSize": "12px", "cursor": "pointer",
                                                  "fontWeight": "bold"}),
                html.Label("Subplot cols", style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Input(id="subplot-ncols", type="number", value=2, min=1, max=6,
                          debounce=True, style=_INPUT_STYLE),
                html.Label("Max plot points", style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Input(id="plot-points", type="number", value=None, min=500,
                          placeholder="auto (dynamic)", debounce=True,
                          style=_INPUT_STYLE),
                html.Div("Blank = auto-decimate to a browser-safe budget.",
                         style={"fontSize": "9px", "color": "#888"}),
                html.Label("Raw trace cols", style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Dropdown(id="raw-columns", multi=True,
                             value=["GameObjectPosX", "GameObjectPosZ"],
                             style={"fontSize": "10px"}),

                html.Hr(style={"margin": "6px 0"}),
                html.Label("Config name LUT (JSON)", style={"fontSize": "10px",
                                                             "fontWeight": "bold"}),
                html.Div("Map raw config filename → display name. Overrides built-in names.",
                         style={"fontSize": "9px", "color": "#888"}),
                dcc.Textarea(id="lut-editor", value="{}",
                             style={"width": "100%", "height": "120px", "fontSize": "10px",
                                    "fontFamily": "monospace", "marginTop": "3px"}),
                html.Button("Apply Names", id="btn-apply-lut", n_clicks=0,
                            style={"width": "100%", "marginTop": "3px", "padding": "4px",
                                   "fontSize": "11px", "cursor": "pointer"}),
                html.Button("Pre-fill from current configs", id="btn-prefill-lut", n_clicks=0,
                            style={"width": "100%", "marginTop": "3px", "padding": "4px",
                                   "fontSize": "10px", "cursor": "pointer"}),
                html.Div(id="lut-status", style={"fontSize": "9px", "color": "#666",
                                                  "marginTop": "2px"}),
            ]),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
                html.Summary("Metadata", style={"fontSize": "12px", "cursor": "pointer",
                                                  "fontWeight": "bold"}),
                html.Pre(id="metadata-display",
                         style={"fontSize": "9px", "maxHeight": "200px", "overflow": "auto",
                                "background": "#f0f0f0", "padding": "4px", "borderRadius": "3px",
                                "whiteSpace": "pre-wrap"}),
            ]),

        ], style={"width": "255px", "padding": "8px", "overflowY": "auto",
                   "overflowX": "hidden",
                   "borderRight": "1px solid #ddd", "background": "#fafafa",
                   "flexShrink": "0", "height": "calc(100vh - 46px)"}),

        # ---- Main ----
        html.Div([
            # Summary
            html.Div(id="data-summary",
                     style={"fontSize": "11px", "padding": "3px 8px", "background": "#e9ecef",
                            "borderRadius": "3px", "margin": "0 0 3px 0", "flexShrink": "0"}),
            html.Div(id="exclusion-info",
                     style={"fontSize": "10px", "color": "#777", "padding": "0 8px 2px",
                            "flexShrink": "0"}),

            # View switch.
            dcc.RadioItems(id="view-mode", options=[
                {"label": "Trajectories", "value": "traj"},
                {"label": "Heatmap", "value": "heat"},
                {"label": "ROI counts", "value": "roi"},
                {"label": "Polar", "value": "polar"},
                {"label": "Diagnostics", "value": "diag"},
            ], value="traj", inline=True,
               labelStyle={"marginRight": "16px", "cursor": "pointer"},
               style={"fontSize": "12px", "fontWeight": "bold", "padding": "3px 4px",
                      "borderBottom": "1px solid #ddd", "marginBottom": "2px",
                      "flexShrink": "0"}),

            # Panels wrapper. Every panel is absolutely positioned to FILL this
            # box and is hidden with `visibility` (NOT display:none). That keeps
            # each graph at full size the whole time — so Plotly always measures
            # correctly and there is no 0-size render when a panel is first shown.
            html.Div([
                # --- Trajectories ---
                html.Div([
                    html.Div([
                        html.Button("▶", id="anim-play", n_clicks=0, title="Play",
                                    style={"fontSize": "13px", "padding": "1px 9px",
                                           "cursor": "pointer"}),
                        html.Button("⏸", id="anim-pause", n_clicks=0, title="Pause",
                                    style={"fontSize": "13px", "padding": "1px 9px",
                                           "cursor": "pointer"}),
                        html.Div(dcc.Slider(id="anim-slider", min=0, max=100, step=1,
                                            value=100, marks=None,
                                            tooltip={"placement": "bottom",
                                                     "always_visible": False}),
                                 style={"flex": "1", "minWidth": "0"}),
                        html.Span("time", style={"fontSize": "10px", "color": "#888"}),
                        html.Div(id="anim-dummy", style={"display": "none"}),
                    ], id="anim-bar",
                       style={"display": "flex", "alignItems": "center", "gap": "8px",
                              "padding": "4px 10px 2px", "background": "#fff",
                              "position": "sticky", "top": "0", "zIndex": "5",
                              "borderBottom": "1px solid #e3e6ee"}),
                    dcc.Loading(
                        dcc.Graph(id="trajectory-plot", figure=_EMPTY,
                                  config={"scrollZoom": True, "displayModeBar": True},
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"}),
                ], id="view-traj", style={**_PANEL_STYLE}),

                # --- Heatmap ---
                html.Div(
                    dcc.Loading(
                        dcc.Graph(id="heatmap-plot", figure=_EMPTY,
                                  config={"scrollZoom": True, "displayModeBar": True},
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"}),
                    id="view-heat", style={**_PANEL_STYLE, "visibility": "hidden"}),

                # --- Diagnostics ---
                html.Div([
                    html.Div([
                        dcc.Graph(id="vel-histogram", figure=_EMPTY,
                                  config={"displayModeBar": False},
                                  style={"flex": "1", "minWidth": "0"}),
                        dcc.Graph(id="disp-histogram", figure=_EMPTY,
                                  config={"displayModeBar": False},
                                  style={"flex": "1", "minWidth": "0"}),
                    ], style={"display": "flex", "gap": "6px"}),
                    dcc.Loading(
                        dcc.Graph(id="raw-trace-plot", figure=_EMPTY,
                                  config={"scrollZoom": True}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"}),
                ], id="view-diag", style={**_PANEL_STYLE, "visibility": "hidden"}),

                # --- ROI counts (violins) ---
                html.Div(
                    dcc.Loading(
                        dcc.Graph(id="roi-plot", figure=_EMPTY,
                                  config={"scrollZoom": True, "displayModeBar": True},
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"}),
                    id="view-roi", style={**_PANEL_STYLE, "visibility": "hidden"}),

                # --- Polar ---
                html.Div(
                    dcc.Loading(
                        dcc.Graph(id="polar-plot", figure=_EMPTY, responsive=False,
                                  config={"scrollZoom": True, "displayModeBar": True},
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"}),
                    id="view-polar", style={**_PANEL_STYLE, "visibility": "hidden"}),
            ], style={"position": "relative", "flex": "1", "minHeight": "0",
                      "minWidth": "0"}),
        ], style={"flex": "1", "padding": "4px 8px", "display": "flex",
                   "flexDirection": "column", "height": "calc(100vh - 46px)",
                   "minWidth": "0", "overflow": "hidden"}),
    ], style={"display": "flex", "height": "calc(100vh - 46px)"}),

    # Stores
    dcc.Store(id="store-glob"),
    dcc.Store(id="viewport-store"),
    dcc.Store(id="heatmap-figure-store"),
    dcc.Store(id="heatmap-variants"),
    dcc.Store(id="auto-thresholds"),
    dcc.Store(id="drop-data"),
    dcc.Store(id="url-restored", data=False),
    dcc.Interval(id="autoload-interval", interval=500, max_intervals=1),
    dcc.Interval(id="load-progress-interval", interval=200, disabled=True),
], style={"fontFamily": "system-ui, -apple-system, sans-serif", "margin": "0"})


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

# Full URL <-> state. Keep these keys in sync with update_url().
_URL_NUM = {"vel": "vel-threshold", "disp": "min-disp", "trim": "trim-samples",
            "jb": "jump-buffer", "hbin": "heatmap-binsize", "hbound": "heatmap-bound",
            "hcmin": "heatmap-cmin", "hcmax": "heatmap-cmax", "ncols": "subplot-ncols",
            "pts": "plot-points"}
_URL_STR = {"groupby": "group-by", "pool": "pool-mode", "color": "color-by",
            "hscale": "heatmap-scale", "hmetric": "heatmap-metric",
            "hcrange": "heatmap-crange"}
_URL_LIST = {"fcfg": "filter-configs", "fvr": "filter-vrs", "ffly": "filter-flyids",
             "fscn": "filter-scenes", "ffld": "filter-folders", "raw": "raw-columns"}


@app.callback(
    Output("glob-input", "value"),
    Output("vel-threshold", "value", allow_duplicate=True),
    Output("min-disp", "value", allow_duplicate=True),
    Output("trim-samples", "value", allow_duplicate=True),
    Output("jump-buffer", "value", allow_duplicate=True),
    Output("group-by", "value", allow_duplicate=True),
    Output("pool-mode", "value", allow_duplicate=True),
    Output("color-by", "value", allow_duplicate=True),
    Output("animate-toggle", "value", allow_duplicate=True),
    Output("rebase-origin", "value", allow_duplicate=True),
    Output("heatmap-binsize", "value", allow_duplicate=True),
    Output("heatmap-scale", "value", allow_duplicate=True),
    Output("heatmap-bound", "value", allow_duplicate=True),
    Output("heatmap-metric", "value", allow_duplicate=True),
    Output("heatmap-cmin", "value", allow_duplicate=True),
    Output("heatmap-cmax", "value", allow_duplicate=True),
    Output("heatmap-crange", "value", allow_duplicate=True),
    Output("filter-configs", "value", allow_duplicate=True),
    Output("filter-vrs", "value", allow_duplicate=True),
    Output("filter-flyids", "value", allow_duplicate=True),
    Output("filter-scenes", "value", allow_duplicate=True),
    Output("filter-folders", "value", allow_duplicate=True),
    Output("raw-columns", "value", allow_duplicate=True),
    Output("subplot-ncols", "value", allow_duplicate=True),
    Output("plot-points", "value", allow_duplicate=True),
    Output("view-mode", "value", allow_duplicate=True),
    Output("viewport-store", "data", allow_duplicate=True),
    Output("url-restored", "data"),
    Input("url", "search"),
    State("url-restored", "data"),
    prevent_initial_call="initial_duplicate",
)
def restore_from_url(search, already):
    n_out = 27
    # Restore exactly once (the first time the URL is seen). Later URL writes
    # come from update_url echoing current state — ignore them to avoid a loop.
    if already:
        return (no_update,) * n_out + (no_update,)
    if not search:
        return (no_update,) * n_out + (True,)
    p = parse_qs(search.lstrip("?"))

    def num(k):
        if k not in p:
            return no_update
        try:
            v = float(p[k][0]); return int(v) if v.is_integer() else v
        except Exception:
            return no_update

    def jump_ms():
        if "jb" not in p:
            return no_update
        try:
            v = float(p["jb"][0])
            # Historical URLs stored seconds (0.1). The control now shows ms.
            out = v * 1000 if v <= 10 else v
            return int(out) if float(out).is_integer() else out
        except Exception:
            return no_update

    def s(k):
        return p[k][0] if k in p else no_update

    def lst(k):
        return p[k][0].split(",") if (k in p and p[k][0]) else no_update

    anim = (["on"] if p["anim"][0] == "1" else []) if "anim" in p else no_update
    rebase = (["on"] if p["rebase"][0] == "1" else []) if "rebase" in p else no_update
    view = p["view"][0] if p.get("view", [""])[0] in ("traj", "heat", "roi", "polar", "diag") else no_update

    vp = no_update
    if all(k in p for k in ("vbx0", "vbx1", "vby0", "vby1")):
        try:
            vp = {"xaxis": [float(p["vbx0"][0]), float(p["vbx1"][0])],
                  "yaxis": [float(p["vby0"][0]), float(p["vby1"][0])]}
        except Exception:
            vp = no_update

    return (
        s("glob"), num("vel"), num("disp"), num("trim"), jump_ms(),
        s("groupby"), s("pool"), s("color"), anim, rebase,
        num("hbin"), s("hscale"), num("hbound"), s("hmetric"),
        num("hcmin"), num("hcmax"), s("hcrange"),
        lst("fcfg"), lst("fvr"), lst("ffly"), lst("fscn"), lst("ffld"),
        lst("raw"), num("ncols"), num("pts"), view, vp, True,
    )


@app.callback(
    Output("btn-load", "n_clicks"),
    Input("autoload-interval", "n_intervals"),
    State("glob-input", "value"),
    State("btn-load", "n_clicks"),
    prevent_initial_call=True,
)
def auto_trigger(n_intervals, glob_val, clicks):
    if glob_val and glob_val.strip():
        return (clicks or 0) + 1
    return no_update


# Dropped folder -> resolve to a glob, fill the input, and auto-load.
@app.callback(
    Output("glob-input", "value", allow_duplicate=True),
    Output("btn-load", "n_clicks", allow_duplicate=True),
    Output("load-status", "children", allow_duplicate=True),
    Input("drop-data", "data"),
    State("btn-load", "n_clicks"),
    prevent_initial_call=True,
)
def on_folder_drop(data, clicks):
    if not data or not data.get("files"):
        return no_update, no_update, "No CSVs found in drop."
    pat = resolve_dropped_folder(data.get("folder", ""), data.get("files", []))
    if not pat:
        return (no_update, no_update,
                f"Couldn't locate '{data.get('folder','')}' on disk — type a path instead.")
    return pat, (clicks or 0) + 1, f"Resolved → {pat}"


# Show the progress bar the moment a load is requested.
@app.callback(
    Output("load-progress-interval", "disabled", allow_duplicate=True),
    Output("load-progress-track", "style", allow_duplicate=True),
    Input("btn-load", "n_clicks"),
    State("load-progress-track", "style"),
    prevent_initial_call=True,
)
def start_progress(n, style):
    style = dict(style or {})
    style["display"] = "block"
    return False, style


# Poll the global progress while loading; hide + stop when done.
@app.callback(
    Output("load-progress-bar", "style"),
    Output("load-status", "children", allow_duplicate=True),
    Output("load-progress-interval", "disabled", allow_duplicate=True),
    Output("load-progress-track", "style", allow_duplicate=True),
    Input("load-progress-interval", "n_intervals"),
    State("load-progress-bar", "style"),
    State("load-progress-track", "style"),
    prevent_initial_call=True,
)
def tick_progress(n, barstyle, trackstyle):
    p = _LOAD_PROGRESS
    total = p["total"] or 1
    pct = int(100 * p["done"] / total)
    bs = dict(barstyle or {})
    if p["active"]:
        bs["width"] = f"{pct}%"
        return (bs, f"Loading {p['done']}/{p['total']} files… {pct}%",
                no_update, no_update)
    # finished: fill, then hide track + stop interval (status set by load_data_cb)
    bs["width"] = "100%"
    ts = dict(trackstyle or {})
    ts["display"] = "none"
    return bs, no_update, True, ts


@app.callback(
    Output("url", "search"),
    Input("btn-plot", "n_clicks"),
    Input("glob-input", "value"),
    Input("vel-threshold", "value"),
    Input("min-disp", "value"),
    Input("trim-samples", "value"),
    Input("jump-buffer", "value"),
    Input("group-by", "value"),
    Input("pool-mode", "value"),
    Input("color-by", "value"),
    Input("animate-toggle", "value"),
    Input("rebase-origin", "value"),
    Input("heatmap-binsize", "value"),
    Input("heatmap-scale", "value"),
    Input("heatmap-bound", "value"),
    Input("heatmap-metric", "value"),
    Input("heatmap-cmin", "value"),
    Input("heatmap-cmax", "value"),
    Input("heatmap-crange", "value"),
    Input("filter-configs", "value"),
    Input("filter-vrs", "value"),
    Input("filter-flyids", "value"),
    Input("filter-scenes", "value"),
    Input("filter-folders", "value"),
    Input("raw-columns", "value"),
    Input("subplot-ncols", "value"),
    Input("plot-points", "value"),
    Input("view-mode", "value"),
    State("viewport-store", "data"),
    State("url-restored", "data"),
    prevent_initial_call=True,
)
def update_url(n, g, vel, disp, trim, jb, gb, pm, color, anim, rebase,
               hbin, hscale, hbound, hmetric, hcmin, hcmax, hcrange,
               fcfg, fvr, ffly, fscn, ffld, raw, ncols, pts, view, vp, restored):
    if not restored:
        return no_update
    params = {}
    if g:
        params["glob"] = g
    nums = {"vel": vel, "disp": disp, "trim": trim, "jb": jb, "hbin": hbin,
            "hbound": hbound, "hcmin": hcmin, "hcmax": hcmax, "ncols": ncols, "pts": pts}
    for k, v in nums.items():
        if v is not None and v != "":
            params[k] = v
    strs = {"groupby": gb, "pool": pm, "color": color, "hscale": hscale,
            "hmetric": hmetric, "hcrange": hcrange, "view": view}
    for k, v in strs.items():
        if v:
            params[k] = v
    params["anim"] = "1" if (anim and "on" in anim) else "0"
    params["rebase"] = "1" if (rebase and "on" in rebase) else "0"
    lists = {"fcfg": fcfg, "fvr": fvr, "ffly": ffly, "fscn": fscn, "ffld": ffld, "raw": raw}
    for k, v in lists.items():
        if v:
            params[k] = ",".join(str(x) for x in v)
    if vp and not vp.get("reset") and "xaxis" in vp and "yaxis" in vp:
        params["vbx0"], params["vbx1"] = vp["xaxis"]
        params["vby0"], params["vby1"] = vp["yaxis"]
    return "?" + urlencode(params) if params else ""


@app.callback(
    Output("load-status", "children"),
    Output("store-glob", "data"),
    Output("filter-configs", "options"),
    Output("filter-vrs", "options"),
    Output("filter-flyids", "options"),
    Output("filter-scenes", "options"),
    Output("filter-folders", "options"),
    Output("raw-columns", "options"),
    Output("metadata-display", "children"),
    Output("vel-histogram", "figure"),
    Output("disp-histogram", "figure"),
    Output("heatmap-binsize", "value", allow_duplicate=True),
    Output("btn-plot", "n_clicks"),
    Output("auto-thresholds", "data"),
    Input("btn-load", "n_clicks"),
    State("glob-input", "value"),
    State("btn-plot", "n_clicks"),
    State("heatmap-binsize", "value"),
    prevent_initial_call=True,
)
def load_data_cb(n_clicks, pattern, plot_clicks, cur_binsize):
    empty = go.Figure().update_layout(height=190, template="plotly_white")
    nope = ("No pattern.", None, [], [], [], [], [], [], "", empty, empty,
            no_update, no_update, None)
    if not pattern:
        return nope

    t0 = time.time()
    df, stats, metas = _load_data(pattern)
    elapsed = time.time() - t0

    if df is None or len(df) == 0:
        return (f"No data for: {pattern}", None, [], [], [], [], [], [], "",
                empty, empty, no_update, no_update, None)

    n_files = df["SourceFile"].nunique()
    n_segs = df["_seg_id"].nunique()
    status = f"{len(df):,} rows | {n_files} files | {n_segs} segments | {elapsed:.1f}s"

    def opts(col):
        if col not in df.columns:
            return []
        vals = _ordered_values(df[col].unique()) if col == "ConfigFile" else sorted(df[col].unique())
        if col == "ConfigFile":
            return [{"label": humanise_config(v), "value": v, "title": v} for v in vals]
        return [{"label": str(v), "value": v} for v in vals]

    num_cols = sorted([c for c in df.columns
                       if df[c].dtype in (np.float64, np.int64, np.float32, np.int32)
                       and c not in ("CurrentTrial", "CurrentStep")])
    col_opts = [{"label": c, "value": c} for c in num_cols]

    meta_parts = []
    for m in metas[:5]:
        fm = m.get("fly_metadata")
        if not fm:
            continue
        meta_parts.append(f"--- {m['folder']} ---")
        for k in ("ExperimenterName", "Comments"):
            if fm.get(k):
                meta_parts.append(f"  {k}: {fm[k]}")
        for fly in fm.get("Flies", []):
            meta_parts.append(f"  {fly.get('VR','')}: fly{fly.get('FlyID','')}"
                              f" {fly.get('Sex','')}")

    vel_fig = build_velocity_histogram(df)
    disp_fig = build_displacement_histogram(stats)

    # Auto filter defaults: 99th-pct velocity, and 5% of the median net
    # displacement (a scale-free "barely moved" cut). Stored for the auto boxes.
    vv = velocity_all(df); vv = vv[np.isfinite(vv)]
    disp = stats["displacement"].to_numpy() if stats is not None and len(stats) else np.array([])
    auto = {"vel": round(float(np.percentile(vv, 99)), 3) if vv.size else None,
            "disp": round(float(0.05 * np.median(disp)), 3) if disp.size else None}

    # Smart default bin size on a fresh load; respect any value already set
    # (e.g. restored from the URL).
    binsize_out = no_update if (cur_binsize not in (None, "")) else default_bin_size(df)

    return (
        status, pattern,
        opts("ConfigFile"), opts("VR"), opts("FlyID"), opts("SceneName"),
        opts("SourceFolder"), col_opts,
        "\n".join(meta_parts) or "No metadata",
        vel_fig, disp_fig, binsize_out,
        (plot_clicks or 0) + 1, auto,
    )


# Auto thresholds: when a box is ticked, fill its field with the computed value
# and disable it; when unticked, re-enable it (blank = no cut). Also triggers a
# re-filter so the change actually reaches the plots.
@app.callback(
    Output("vel-threshold", "value"),
    Output("vel-threshold", "disabled"),
    Output("min-disp", "value"),
    Output("min-disp", "disabled"),
    Output("btn-plot", "n_clicks", allow_duplicate=True),
    Input("vel-auto", "value"),
    Input("disp-auto", "value"),
    Input("auto-thresholds", "data"),
    State("btn-plot", "n_clicks"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)
def apply_auto_thresholds(vel_auto, disp_auto, auto, clicks, pattern):
    vel_val = (auto or {}).get("vel") if _on(vel_auto) else no_update
    disp_val = (auto or {}).get("disp") if _on(disp_auto) else no_update
    bump = (clicks or 0) + 1 if pattern else no_update
    return vel_val, _on(vel_auto), disp_val, _on(disp_auto), bump


def _filter_summary(df_sub, vel_thresh, min_disp, trim, jump_buf,
                    polar_moving=None, walk=None):
    """Report active filters with counts from the actual filter pipeline."""
    if df_sub is None or len(df_sub) == 0:
        return ""
    parts = []
    cur = df_sub
    start_n = len(cur)

    if vel_thresh is not None and vel_thresh > 0:
        vel = velocity_all(cur)
        spikes = np.nan_to_num(vel, nan=0.0) > float(vel_thresh)
        removed = 0
        buf_s = _jump_buffer_seconds(jump_buf)
        if spikes.any():
            seg = cur["_seg_id"].to_numpy()
            t = cur["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
            keep = _dilate_keep(seg, t, spikes, buf_s)
            removed = int((~keep).sum())
            cur = cur[keep]
        parts.append(
            f"vel > {float(vel_thresh):g} units/s"
            f" (buffer {buf_s * 1000:g} ms): {_compact_count(removed)}/{_compact_count(start_n)} pts"
            f" ({100*removed/max(start_n, 1):.1f}%)")

    if min_disp is not None and min_disp > 0 and len(cur):
        stats = compute_segment_stats(cur)
        before = int(cur["_seg_id"].nunique())
        bad = (stats["displacement"] < float(min_disp)) if len(stats) else pd.Series([], dtype=bool)
        bad_ids = set(stats.loc[bad, "seg_id"]) if len(stats) else set()
        removed_trials = len(bad_ids)
        if bad_ids:
            cur = cur[~cur["_seg_id"].isin(bad_ids)]
        parts.append(
            f"disp < {float(min_disp):g}: {_compact_count(removed_trials)}/{_compact_count(before)} trials"
            f" ({100*removed_trials/max(before, 1):.1f}%)")

    if trim is not None and trim > 0 and len(cur):
        before_pts = len(cur)
        before_trials = int(cur["_seg_id"].nunique())
        g = cur.groupby("_seg_id", sort=False)
        pos = g.cumcount()
        size = g["_seg_id"].transform("size")
        keep = (pos >= int(trim)) & (pos < size - int(trim))
        removed = int((~keep).sum())
        parts.append(
            f"trim {int(trim)} samples/side: {_compact_count(removed)}/{_compact_count(before_pts)} pts"
            f" across {_compact_count(before_trials)} trials ({100*removed/max(before_pts, 1):.1f}%)")

    if _on(polar_moving) and walk is not None and walk > 0:
        v = velocity_all(df_sub)
        v = v[np.isfinite(v)]
        if v.size:
            moving = int((v >= float(walk)).sum())
            parts.append(
                f"polar moving >= {float(walk):g} units/s: {_compact_count(moving)}/{_compact_count(v.size)} samples"
                f" ({100*moving/v.size:.0f}%)")

    return ("Excluded - " + " · ".join(parts)) if parts else "Active filters - none"


@app.callback(
    Output("exclusion-info", "children", allow_duplicate=True),
    Input("store-glob", "data"),
    Input("btn-plot", "n_clicks"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("vel-histogram", "selectedData"),
    State("disp-histogram", "selectedData"),
    State("polar-moving", "value"),
    State("polar-walk", "value"),
    prevent_initial_call=True,
)
def update_filter_summary(pattern, n_plot, vel_thresh, min_disp, trim, jump_buf,
                          cfg, vrs, fids, scenes, folders, vel_sel, disp_sel,
                          polar_moving, polar_walk):
    if not pattern:
        return no_update
    _, df_sub, _ = _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                                cfg, vrs, fids, scenes, folders,
                                vel_sel, disp_sel)
    if df_sub is None or len(df_sub) == 0:
        return ""
    return _filter_summary(df_sub, vel_thresh, min_disp, trim, jump_buf,
                           polar_moving, polar_walk)


_FILTER_CACHE: dict = {}        # signature -> (df_f, df_sub, stats_sub)
_FILTER_CACHE_ORDER: list = []
_FILTER_CACHE_MAX = 4


def _filter_signature(pattern, vel_thresh, min_disp, trim, jump_buf,
                      cfg, vrs, fids, scenes, folders, vel_selection, disp_selection):
    def rng(sel):
        return tuple(sel["range"]["x"]) if sel and sel.get("range") else None
    def lst(v):
        return tuple(sorted(v)) if v else None
    return (pattern, vel_thresh, min_disp, trim, round(_jump_buffer_seconds(jump_buf), 6),
            lst(cfg), lst(vrs), lst(fids), lst(scenes), lst(folders),
            rng(vel_selection), rng(disp_selection))


def _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                 cfg, vrs, fids, scenes, folders,
                 vel_selection, disp_selection):
    """
    Shared filtering pipeline (cached). Returns (df_f, df_sub, stats_sub).

    Caching makes heatmap-only changes (lin/log, metric, bins, percentile)
    cheap — they reuse the already-filtered frame instead of re-running the
    full velocity/displacement/trim pipeline.
    """
    sig = _filter_signature(pattern, vel_thresh, min_disp, trim, jump_buf,
                            cfg, vrs, fids, scenes, folders,
                            vel_selection, disp_selection)
    if sig in _FILTER_CACHE:
        return _FILTER_CACHE[sig]

    df, stats, _ = _load_data(pattern)
    if df is None or len(df) == 0:
        return None, None, None

    mask = pd.Series(True, index=df.index)
    if cfg:     mask &= df["ConfigFile"].isin(cfg)
    if vrs:     mask &= df["VR"].isin(vrs)
    if fids:    mask &= df["FlyID"].isin(fids)
    if scenes:  mask &= df["SceneName"].isin(scenes)
    if folders: mask &= df["SourceFolder"].isin(folders)
    df_sub = df[mask].copy()
    if len(df_sub) == 0:
        return df_sub, df_sub, None

    stats_sub = compute_segment_stats(df_sub)

    if disp_selection and disp_selection.get("range"):
        rng = disp_selection["range"]["x"]
        df_sub = filter_by_stat_range(df_sub, stats_sub, "displacement", rng[0], rng[1])
    if vel_selection and vel_selection.get("range"):
        rng = vel_selection["range"]["x"]
        df_sub = filter_by_stat_range(df_sub, stats_sub, "peak_velocity", rng[0], rng[1])
    stats_sub = compute_segment_stats(df_sub) if len(df_sub) else stats_sub

    df_f = apply_filters(df_sub, vel_thresh, min_disp, trim,
                         _jump_buffer_seconds(jump_buf))
    result = (df_f, df_sub, stats_sub)

    _FILTER_CACHE[sig] = result
    _FILTER_CACHE_ORDER.append(sig)
    if len(_FILTER_CACHE_ORDER) > _FILTER_CACHE_MAX:
        old = _FILTER_CACHE_ORDER.pop(0)
        _FILTER_CACHE.pop(old, None)
    return result


def _apply_viewport(fig, viewport, df, max_span_mult=3.0):
    """Re-apply a stored shared viewbox to `fig`, but reject garbage ranges.

    A scaleanchor plot that fires a relayout while briefly mis-sized can report a
    range many times larger than the data — applying it zooms everything out to
    an empty view. We only honour a stored range whose span is within a generous
    multiple of the data's natural extent; anything wilder is treated as "no
    viewbox" so the figure keeps its own sensible autoscale.
    """
    if not viewport or viewport.get("reset") or df is None or len(df) == 0:
        return
    try:
        rx, rz = _shared_range(df)
    except Exception:
        rx = rz = None

    def _ok(rng, natural):
        if not rng or len(rng) != 2 or rng[0] is None or rng[1] is None:
            return False
        if natural is None:
            return True
        span = abs(rng[1] - rng[0])
        nat = abs(natural[1] - natural[0]) or 1.0
        return span <= nat * float(max_span_mult)

    if _ok(viewport.get("xaxis"), rx):
        fig.update_xaxes(range=viewport["xaxis"])
    if _ok(viewport.get("yaxis"), rz):
        fig.update_yaxes(range=viewport["yaxis"])


def _apply_viewport_to_current_range(fig, viewport, max_span_mult=2.0):
    """Apply a viewport only if it is close to the figure's own current range.

    Heatmap bounds can be clipped (`hbound`), so validating against the raw data
    extent can accept a stale, much broader viewbox that leaves the actual
    heatmap as a tiny island and makes wheel-zoom feel like it vanished.
    """
    if not viewport or viewport.get("reset"):
        return

    def _layout_range(axis_name):
        ax = getattr(fig.layout, axis_name, None)
        rng = getattr(ax, "range", None) if ax is not None else None
        return list(rng) if rng and len(rng) == 2 else None

    def _ok(vp_rng, natural):
        if not vp_rng or len(vp_rng) != 2 or natural is None:
            return False
        span = abs(float(vp_rng[1]) - float(vp_rng[0]))
        nat = abs(float(natural[1]) - float(natural[0])) or 1.0
        if span > nat * float(max_span_mult):
            return False
        lo = max(min(vp_rng), min(natural))
        hi = min(max(vp_rng), max(natural))
        return hi > lo

    xr = _layout_range("xaxis")
    yr = _layout_range("yaxis")
    if _ok(viewport.get("xaxis"), xr):
        fig.update_xaxes(range=viewport["xaxis"])
    if _ok(viewport.get("yaxis"), yr):
        fig.update_yaxes(range=viewport["yaxis"])


@app.callback(
    Output("trajectory-plot", "figure"),
    Output("raw-trace-plot", "figure"),
    Output("data-summary", "children"),
    Output("vel-histogram", "figure", allow_duplicate=True),
    Output("disp-histogram", "figure", allow_duplicate=True),
    Output("roi-plot", "figure", allow_duplicate=True),
    Output("exclusion-info", "children"),
    Input("btn-plot", "n_clicks"),
    Input("view-mode", "value"),
    State("store-glob", "data"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("group-by", "value"),
    State("pool-mode", "value"),
    State("color-by", "value"),
    State("animate-toggle", "value"),
    State("rebase-origin", "value"),
    State("heatmap-binsize", "value"),
    State("heatmap-scale", "value"),
    State("heatmap-bound", "value"),
    State("heatmap-metric", "value"),
    State("heatmap-cmin", "value"),
    State("heatmap-cmax", "value"),
    State("heatmap-crange", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("raw-columns", "value"),
    State("subplot-ncols", "value"),
    State("plot-points", "value"),
    State("vel-histogram", "selectedData"),
    State("disp-histogram", "selectedData"),
    State("viewport-store", "data"),
    State("roi-show", "value"),
    State("roi-reach", "value"),
    State("roi-trim", "value"),
    State("roi-entered", "value"),
    State("polar-moving", "value"),
    State("polar-walk", "value"),
    prevent_initial_call=True,
)
def update_plots(n, view, pattern, vel_thresh, min_disp, trim, jump_buf,
                 group_by, pool_mode, color_by, animate, rebase, hm_binsize, hm_scale,
                 hm_bound, hm_metric, hm_cmin, hm_cmax, hm_crange, cfg, vrs, fids,
                 scenes, folders, raw_cols, ncols, max_points, vel_selection,
                 disp_selection, viewport, roi_show, roi_reach, roi_trim, roi_entered,
                 polar_moving, polar_walk):
    empty = go.Figure().update_layout(height=400, template="plotly_white")
    if not pattern:
        return empty, empty, "Load data first.", no_update, no_update, no_update, ""
    # Main builder owns only the regular trajectory and diagnostic raw views.
    # Heatmap, ROI and polar are lazy visible-tab callbacks so a harmless tab
    # switch or heatmap control cannot rebuild every plot.
    if view not in ("traj", "diag"):
        return (no_update, no_update, no_update, no_update, no_update,
                no_update, no_update)

    df_f, df_sub, stats_sub = _filtered_df(
        pattern, vel_thresh, min_disp, trim, jump_buf,
        cfg, vrs, fids, scenes, folders, vel_selection, disp_selection)

    if df_sub is None:
        return empty, empty, "No data.", no_update, no_update, no_update, ""
    if len(df_sub) == 0:
        return empty, empty, "All filtered out.", no_update, no_update, no_update, ""

    # Non-invasive exclusion tally (what the filters remove; fraction moving).
    exclusion = _filter_summary(df_sub, vel_thresh, min_disp, trim, jump_buf,
                                polar_moving, polar_walk)

    df, _, metas = _load_data(pattern)
    vel_fig = no_update
    disp_fig = no_update
    if view in ("traj", "diag"):
        # Histograms reflect the subset before velocity/disp cuts.
        mask = pd.Series(True, index=df.index)
        if cfg:     mask &= df["ConfigFile"].isin(cfg)
        if vrs:     mask &= df["VR"].isin(vrs)
        if fids:    mask &= df["FlyID"].isin(fids)
        if scenes:  mask &= df["SceneName"].isin(scenes)
        if folders: mask &= df["SourceFolder"].isin(folders)
        df_hist = df[mask]
        vel_fig = build_velocity_histogram(df_hist, vel_thresh)
        disp_fig = build_displacement_histogram(compute_segment_stats(df_hist), min_disp)

    t0 = time.time()
    ncols_val = int(ncols) if ncols and ncols >= 1 else 2
    do_animate = bool(animate) and "on" in (animate or [])
    do_rebase = bool(rebase) and "on" in (rebase or [])

    # ROI masking + counts. The reached table is built from the UNMASKED
    # filtered data; trajectory corner labels intersect it with each subplot's
    # visible segments, while the ROI tab uses the unmasked table. df_view is
    # what trajectory/heatmap/raw draw: optionally restricted to whole trials
    # that entered an ROI, then tail-trimmed.
    rois = rois_by_config(metas)
    reach = float(roi_reach) if roi_reach else 3.0
    want_rois = _on(roi_show) and bool(rois)
    df_view, table = _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
    roi_counts = table if (want_rois and table is not None) else None
    roi_fig = no_update            # the ROI violin is lazily built by its own tab
    if want_rois and view == "roi" and table is not None:
        roi_fig = build_roi_swarm_figure(df_view, rois, reach)

    df_plot = rebase_to_origin(df_view) if do_rebase else df_view

    traj_fig = no_update
    raw_fig = no_update
    drawn = None
    n_frames = None
    if view == "traj":
        traj_fig = build_trajectory_figure(df_plot, group_by, pool_mode,
                                            ncols=ncols_val,
                                            color_by=color_by or "individual",
                                            animate=do_animate,
                                            max_points=max_points,
                                            rois=rois, reach_radius=reach,
                                            show_rois=want_rois and not do_rebase,
                                            roi_counts=roi_counts)
        _apply_viewport(traj_fig, viewport, df_plot)
        drawn = sum(len(t.x) for t in traj_fig.data
                    if getattr(t, "x", None) is not None)
        n_frames = len(traj_fig.frames)
        # Record the mask state this trajectory reflects, so returning to the
        # traj tab rebuilds only when it actually changed.
        _mask_on = _on(roi_trim) or _on(roi_entered)
        _LAST_TRAJ_SIG["v"] = (tuple(roi_trim or []), tuple(roi_entered or []),
                               reach if _mask_on else None)
    if view == "diag":
        raw_fig = build_raw_trace_figure(
            df_view, raw_cols or ["GameObjectPosX", "GameObjectPosZ"],
            max_points=max_points)
    bt = time.time() - t0

    # Effective drawn points (post-decimation) for the summary
    n_traces = int(df_view["_seg_id"].nunique()) if len(df_view) else 0
    budget_str = (f"{int(max_points):,}" if (max_points and max_points > 0)
                  else (f"anim {BUDGET_SVG//1000}k" if do_animate else f"{BUDGET_GL//1000}k"))
    drawn_str = f"~{drawn:,} ({budget_str})" if drawn is not None else "not rebuilt"
    frame_str = str(n_frames) if n_frames is not None else "—"

    n_segs_before = df_sub["_seg_id"].nunique()
    summary = (f"{_compact_count(len(df_view))}/{_compact_count(len(df_sub))} pts | "
               f"{_compact_count(n_traces)}/{_compact_count(n_segs_before)} segs | "
               f"drawn {drawn_str} | "
               f"{frame_str} frames | "
               f"build {bt:.2f}s | colour: {color_by}")

    return traj_fig, raw_fig, summary, vel_fig, disp_fig, roi_fig, exclusion


# Rebuild the heatmap on its own controls AND whenever the Heatmap view is
# opened. Re-pushing the figure to the now-visible graph is what makes it draw
# reliably (a graph born in a hidden panel won't render an earlier figure push).
#
# On a *plain* tab switch where nothing that affects the heatmap changed, we
# return no_update instead of rebuilding: the clientside callback paints the
# already-correct figure on first reveal and merely resizes on later reveals, so
# flipping tabs costs zero server work and zero re-init flash. A rebuild only
# happens when a heatmap control, the filter, or the shared viewport changed.
_LAST_HEAT_SIG: dict = {"v": None}


@app.callback(
    Output("heatmap-figure-store", "data", allow_duplicate=True),
    Output("heatmap-variants", "data", allow_duplicate=True),
    Output("data-summary", "children", allow_duplicate=True),
    Input("heatmap-binsize", "value"),
    Input("heatmap-bound", "value"),
    Input("heatmap-cmin", "value"),
    Input("heatmap-cmax", "value"),
    Input("heatmap-crange", "value"),
    Input("view-mode", "value"),
    Input("btn-plot", "n_clicks"),
    Input("store-glob", "data"),
    Input("roi-reach", "value"),
    Input("roi-entered", "value"),
    Input("roi-trim", "value"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("group-by", "value"),
    State("pool-mode", "value"),
    State("subplot-ncols", "value"),
    State("rebase-origin", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("vel-histogram", "selectedData"),
    State("disp-histogram", "selectedData"),
    State("viewport-store", "data"),
    prevent_initial_call=True,
)
def update_heatmap_only(hm_binsize, hm_bound, hm_cmin, hm_cmax, hm_crange, view,
                        n_plot, pattern, roi_reach, roi_entered, roi_trim,
                        vel_thresh, min_disp, trim, jump_buf, group_by, pool_mode,
                        ncols, rebase, cfg, vrs, fids, scenes, folders,
                        vel_selection, disp_selection, viewport):
    if not pattern:
        return no_update, no_update, no_update
    # Do not push a real heatmap figure into the hidden graph. Plotly.react can
    # throw "Something went wrong with axis scaling" when applying subplot
    # heatmaps to a hidden/settling panel; opening the Heatmap tab is the safe
    # moment to build and push it, where the clientside newPlot takes over.
    if view != "heat":
        return no_update, no_update, no_update
    # Signature covers only what changes the binning, mask geometry, grouping or
    # filtered data. It deliberately excludes metric/scale (client restyle) and
    # viewport (client relayout) so pan/zoom and colour-mode swaps do not re-bin.
    def _sig_of(v):
        try:
            return json.dumps(v, sort_keys=True, default=str)
        except Exception:
            return repr(v)
    sig = _sig_of([pattern, hm_binsize, hm_bound, hm_cmin, hm_cmax, hm_crange,
                   group_by, pool_mode, ncols, rebase, cfg, vrs, fids, scenes,
                   folders, vel_thresh, min_disp, trim,
                   round(_jump_buffer_seconds(jump_buf), 6), vel_selection,
                   disp_selection, roi_reach, roi_entered, roi_trim])
    if sig == _LAST_HEAT_SIG["v"]:
        return no_update, no_update, no_update
    df_f, df_sub, _ = _filtered_df(
        pattern, vel_thresh, min_disp, trim, jump_buf,
        cfg, vrs, fids, scenes, folders, vel_selection, disp_selection)
    if df_sub is None or len(df_sub) == 0:
        return no_update, no_update, no_update
    ncols_val = int(ncols) if ncols and ncols >= 1 else 2
    reach_v = float(roi_reach) if roi_reach else 3.0
    df_view, _ = _roi_apply(df_f, pattern, reach_v,
                            _on(roi_entered), _on(roi_trim))
    heat, variants = build_heatmap_mask_variants(
        df_f, pattern, reach_v, group_by, pool_mode, ncols_val,
        bin_size=hm_binsize, bound_pct=hm_bound if hm_bound else 100,
        cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange, do_rebase=_on(rebase),
        entered_only=_on(roi_entered), trim_tail=_on(roi_trim))
    _apply_viewport_to_current_range(heat, viewport, max_span_mult=1.5)
    _LAST_HEAT_SIG["v"] = sig
    n_traces = int(df_view["_seg_id"].nunique()) if len(df_view) else 0
    n_segs_before = int(df_sub["_seg_id"].nunique()) if len(df_sub) else 0
    summary = (f"Heatmap {_compact_count(len(df_view))}/{_compact_count(len(df_sub))} pts | "
               f"{_compact_count(n_traces)}/{_compact_count(n_segs_before)} segs | "
               f"bin {hm_binsize or default_bin_size(df_view):g} u | "
               f"build current visible tab")
    return heat.to_plotly_json(), variants, summary


# Re-apply a stored trajectory/or-restored viewbox to the heatmap on reveal,
# clientside only. Ignore viewport events that came from the heatmap itself so a
# live pan/zoom gesture is not followed by a redundant relayout of the same plot.
app.clientside_callback(
    "function(view, vp, hfig){"
    "var cb=window.dash_clientside&&window.dash_clientside.callback_context;"
    "var trig=(cb&&cb.triggered&&cb.triggered[0]&&cb.triggered[0].prop_id)||'';"
    "if(view!=='heat'||!vp||vp.reset)return '';"
    "if(trig.indexOf('viewport-store')===0&&vp.source==='heat')return '';"
    "setTimeout(function(){var g=document.querySelector('#heatmap-plot .js-plotly-plot');"
    "if(!g||!window.Plotly)return;var u={};"
    "function ok(r,cur){if(!r||!cur)return false;"
    "var s=Math.abs(r[1]-r[0]), n=Math.abs(cur[1]-cur[0])||1;"
    "if(s>1.5*n)return false;return Math.min(r[1],cur[1])>Math.max(r[0],cur[0]);}"
    "var lx=g.layout||{};"
    "if(ok(vp.xaxis,lx.xaxis&&lx.xaxis.range))u['xaxis.range']=vp.xaxis;"
    "if(ok(vp.yaxis,lx.yaxis&&lx.yaxis.range))u['yaxis.range']=vp.yaxis;"
    "if(!Object.keys(u).length)return;window.__hmSuppress=true;"
    "try{window.Plotly.relayout(g,u);}catch(e){}"
    "setTimeout(function(){window.__hmSuppress=false;},180);},120);return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("view-mode", "value"),
    Input("viewport-store", "data"),
    Input("heatmap-figure-store", "data"),
    prevent_initial_call=True,
)


# Show exactly one mounted panel (graphs are never unmounted, so their figures
# and zoom persist).
@app.callback(
    Output("view-traj", "style"),
    Output("view-heat", "style"),
    Output("view-diag", "style"),
    Output("view-roi", "style"),
    Output("view-polar", "style"),
    Input("view-mode", "value"),
)
def switch_view(v):
    def st(name):
        return {**_PANEL_STYLE, "visibility": "visible" if v == name else "hidden"}
    return st("traj"), st("heat"), st("diag"), st("roi"), st("polar")


# Live-rebuild the ROI violins when the reach radius changes or the ROI tab is
# opened — cheap enough (per-trial reached test on the filtered data) to feel
# snappy without a full trajectory replot. The slider fires on release (mouseup).
@app.callback(
    Output("roi-plot", "figure", allow_duplicate=True),
    Output("data-summary", "children", allow_duplicate=True),
    Input("btn-plot", "n_clicks"),
    Input("roi-reach", "value"),
    Input("roi-show", "value"),
    Input("roi-trim", "value"),
    Input("roi-entered", "value"),
    Input("view-mode", "value"),
    Input("store-glob", "data"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("vel-histogram", "selectedData"),
    State("disp-histogram", "selectedData"),
    prevent_initial_call=True,
)
def update_roi_view(n_plot, reach, roi_show, roi_trim, roi_entered, view, pattern,
                    vel_thresh, min_disp, trim, jump_buf, cfg, vrs, fids,
                    scenes, folders, vel_sel, disp_sel):
    if not pattern:
        return no_update, no_update
    if view != "roi":
        return no_update, no_update
    if not (roi_show and "on" in roi_show):
        return (_msg_figure("Enable 'Show target ROIs + reached counts' to see "
                            "reached-fraction violins."),
                "ROI diagnostics disabled.")
    df_f, df_sub, _ = _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                                   cfg, vrs, fids, scenes, folders, vel_sel, disp_sel)
    if df_sub is None or len(df_sub) == 0:
        return no_update, no_update
    # Fraction counts reuse the unmasked reached table; time/heading panels use
    # df_view so they respect entered-only / trim visibility toggles.
    _, _, metas = _load_data(pattern)
    rois = rois_by_config(metas)
    if not rois:
        return _msg_figure("No ROI targets in these configs."), "No ROI targets."
    reach_v = float(reach) if reach else 3.0
    df_view, table = _roi_apply(df_f, pattern, reach_v,
                                _on(roi_entered), _on(roi_trim))
    n_traces = int(df_view["_seg_id"].nunique()) if len(df_view) else 0
    n_before = int(df_sub["_seg_id"].nunique()) if len(df_sub) else 0
    summary = (f"ROI {_compact_count(len(df_view))}/{_compact_count(len(df_sub))} pts | "
               f"{_compact_count(n_traces)}/{_compact_count(n_before)} segs | reach {reach_v:g} u | "
               f"heading error = left/right target centres")
    return build_roi_swarm_figure(df_view, rois, reach_v, table=table), summary


# Reach slider / show toggle → BLIT the trajectory overlay: recompute reach
# circles + corner counts and Patch just the figure's shapes + count annotations
# (fixed slots at index n+i). No trace/data rebuild, so dragging the reach slider
# is instant. (When tail-trim is on, the trimmed *data* still needs a Re-Plot.)
@app.callback(
    Output("trajectory-plot", "figure", allow_duplicate=True),
    Input("roi-reach", "value"),
    Input("roi-show", "value"),
    Input("view-mode", "value"),
    State("store-glob", "data"),
    State("group-by", "value"),
    State("pool-mode", "value"),
    State("subplot-ncols", "value"),
    State("rebase-origin", "value"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("vel-histogram", "selectedData"),
    State("disp-histogram", "selectedData"),
    State("roi-entered", "value"),
    State("roi-trim", "value"),
    prevent_initial_call=True,
)
def update_roi_overlay(reach, roi_show, view, pattern, group_by, pool_mode, ncols,
                       rebase, vel_thresh, min_disp, trim, jump_buf, cfg, vrs,
                       fids, scenes, folders, vel_sel, disp_sel, roi_entered,
                       roi_trim):
    if not pattern or view != "traj":
        return no_update
    df_f, df_sub, _ = _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                                   cfg, vrs, fids, scenes, folders, vel_sel, disp_sel)
    if df_sub is None or len(df_sub) == 0:
        return no_update
    ncols_val = int(ncols) if ncols and ncols >= 1 else 2
    reach_v = float(reach) if reach else 3.0
    do_rebase = _on(rebase)
    rois = rois_by_config(_load_data(pattern)[2])
    # Match the trajectory's subplot set: same view masking, with labels derived
    # from the unmasked reached table intersected with visible segments.
    df_view, table = _roi_apply(df_f, pattern, reach_v, _on(roi_entered), _on(roi_trim))
    group_items = list(_group_frames(df_view, group_by, pool_mode, ncols_val).items())
    show = _on(roi_show) and not do_rebase and bool(rois)
    counts = table if (show and table is not None) else None
    patch = Patch()
    patch["layout"]["shapes"] = _roi_overlay_shapes(group_items, rois, reach_v) if show else []
    n = len(group_items)
    for i, (gname, gdf) in enumerate(group_items):
        left_txt, right_txt = _roi_count_texts(gname, gdf, counts)
        patch["layout"]["annotations"][n + 2 * i]["text"] = left_txt
        patch["layout"]["annotations"][n + 2 * i + 1]["text"] = right_txt
    return patch

# The ROI masks change the plotted trajectory data. They also rebuild the
# heatmap when the heatmap tab is visible. This callback only nudges the
# trajectory, and only when it is actually on screen.
_LAST_TRAJ_SIG: dict = {"v": None}


@app.callback(
    Output("btn-plot", "n_clicks", allow_duplicate=True),
    Input("roi-trim", "value"),
    Input("roi-entered", "value"),
    Input("roi-reach", "value"),
    Input("view-mode", "value"),
    State("btn-plot", "n_clicks"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)
def traj_mask_refresh(roi_trim, roi_entered, roi_reach, view, clicks, pattern):
    if not pattern or view != "traj":
        return no_update
    mask_on = _on(roi_trim) or _on(roi_entered)
    sig = (tuple(roi_trim or []), tuple(roi_entered or []),
           roi_reach if mask_on else None)
    if sig == _LAST_TRAJ_SIG["v"]:      # nothing that affects the trajectory changed
        return no_update
    return (clicks or 0) + 1


# Raw-filename vs readable-name toggle: flips the global, invalidates the cached
# figure signatures (subplot titles change), and replots.
@app.callback(
    Output("btn-plot", "n_clicks", allow_duplicate=True),
    Input("show-raw-config", "value"),
    State("btn-plot", "n_clicks"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)
def toggle_raw_config(val, clicks, pattern):
    _SHOW_RAW_CONFIG["on"] = _on(val)
    _LAST_HEAT_SIG["v"] = None
    _LAST_TRAJ_SIG["v"] = None
    if not pattern:
        return no_update
    return (clicks or 0) + 1


# Polar is built lazily (only when its tab is open or a polar control changes) —
# it's WebGL and heavy, and a WebGL plot created in a hidden panel won't paint,
# so we push the figure while the panel is visible.
@app.callback(
    Output("polar-plot", "figure", allow_duplicate=True),
    Input("view-mode", "value"),
    Input("btn-plot", "n_clicks"),
    Input("store-glob", "data"),
    Input("polar-color", "value"),
    Input("polar-moving", "value"),
    Input("polar-walk", "value"),
    Input("roi-reach", "value"),
    Input("roi-show", "value"),
    Input("roi-trim", "value"),
    Input("roi-entered", "value"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("group-by", "value"),
    State("pool-mode", "value"),
    State("subplot-ncols", "value"),
    State("plot-points", "value"),
    State("rebase-origin", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("vel-histogram", "selectedData"),
    State("disp-histogram", "selectedData"),
    prevent_initial_call=True,
)
def update_polar_view(view, n_plot, pattern, polar_color, polar_moving, polar_walk,
                      reach, roi_show, roi_trim, roi_entered, vel_thresh,
                      min_disp, trim, jump_buf, group_by, pool_mode, ncols,
                      max_points, rebase, cfg, vrs, fids, scenes, folders,
                      vel_sel, disp_sel):
    if not pattern or view != "polar":
        return no_update
    df_f, df_sub, _ = _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                                   cfg, vrs, fids, scenes, folders, vel_sel, disp_sel)
    if df_sub is None or len(df_sub) == 0:
        return _msg_figure("All filtered out.")
    ncols_val = int(ncols) if ncols and ncols >= 1 else 2
    df_f, _ = _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
    if rebase and "on" in rebase:
        df_f = rebase_to_origin(df_f)
    _, _, metas = _load_data(pattern)
    rois = rois_by_config(metas)
    want_rois = bool(roi_show) and "on" in (roi_show or []) and bool(rois)
    precache_polar_rays(df_f, polar_walk, polar_color or "velocity")
    return build_polar_figure(
        df_f, group_by, pool_mode, ncols=ncols_val,
        color_by=polar_color or "velocity",
        moving_only=bool(polar_moving) and "on" in (polar_moving or []),
        walk_thresh=polar_walk, max_points=max_points, rois=rois,
        reach_radius=float(reach) if reach else 3.0, show_rois=want_rois)


# Re-apply the shared viewbox to the trajectory when it is opened (the heatmap
# side is handled in update_heatmap_only). A Patch on the WebGL trajectory is
# smooth — no re-init — so switching views keeps the same zoom without glitches.
@app.callback(
    Output("trajectory-plot", "figure", allow_duplicate=True),
    Input("view-mode", "value"),
    State("viewport-store", "data"),
    prevent_initial_call=True,
)
def apply_viewport_traj(view, vp):
    if view != "traj" or not vp or vp.get("reset"):
        return no_update
    patch = Patch()
    if vp.get("xaxis"):
        patch["layout"]["xaxis"]["range"] = vp["xaxis"]
    if vp.get("yaxis"):
        patch["layout"]["yaxis"]["range"] = vp["yaxis"]
    return patch


# Attach a debounced Plotly relayout listener directly to the visible
# trajectory graph. This avoids feeding every drag/wheel event through Dash's
# `relayoutData` callback machinery while the gesture is in progress.
app.clientside_callback(
    "function(fig, view){setTimeout(function(){"
    "var g=document.querySelector('#trajectory-plot .js-plotly-plot');"
    "if(g&&window.__attachViewportSync){window.__attachViewportSync(g,'traj');}"
    "},120);return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("trajectory-plot", "figure"),
    Input("view-mode", "value"),
    prevent_initial_call=True,
)


# The heatmap uses a 1:1 aspect lock (scaleanchor). Dash's Plotly.react update
# path crashes on that with "axis scaling" when the figure is applied to a graph
# that isn't at full size yet, and never recovers — so the heatmap stays blank.
# A fresh Plotly.newPlot re-initialises cleanly and renders.
#
# But re-initialising on EVERY figure change and EVERY tab switch is what made
# the heatmap flash. Instead we fingerprint the figure and only newPlot when:
#   (a) the content actually changed, or
#   (b) it's the first time the panel is revealed while VISIBLE (the initial draw
#       may have happened while the panel was hidden — that paint isn't reliable).
# A plain tab switch with an unchanged, already-visible figure just resizes — no
# re-init, no flash. Genuine re-inits get a short opacity fade so they read as a
# crossfade rather than a white flash.
app.clientside_callback(
    "function(hfig, view, metric, scale, entered, trim, variants){setTimeout(function(){"
    "var hc=document.getElementById('heatmap-plot');"
    "var hg=hc&&hc.querySelector('.js-plotly-plot');"
    "var panel=document.getElementById('view-heat');"
    "var vis=panel&&getComputedStyle(panel).visibility!=='hidden';"
    "var fp='';try{var L=(hfig&&hfig.layout)||{};"
    # Fingerprint tracks BINNING/structure only (trace count, z/x dimensions,
    # height, axis ranges) — NOT zmin/zmax, which are colouring that the client
    # restyles in place. Including them made a metric/scale swap look like a
    # structural change (dcc.Graph syncs restyled zmin back to the prop) and
    # triggered a needless newPlot flash on the *next* swap.
    "fp=JSON.stringify((hfig&&hfig.data||[]).map(function(t){"
    "return [t.type,(t.z&&t.z.length)||0,(t.x&&t.x.length)||0];}))"
    "+'|'+(L.height||0)+'|'+JSON.stringify(L.xaxis&&L.xaxis.range)"
    "+'|'+JSON.stringify(L.yaxis&&L.yaxis.range);}catch(e){}"
    "if(hg&&window.Plotly&&hfig&&hfig.data&&hfig.data.length){"
    "var changed=hg.__hmfp!==fp;"
    "var needPaint=changed||(!hg.__hmVis&&vis);"
    "if(needPaint){"
    "window.__hmSuppress=true;"
    "try{hc.style.transition='opacity .16s';hc.style.opacity=changed?'0.3':'1';}catch(e){}"
    "try{window.Plotly.newPlot(hg,hfig.data,hfig.layout,{scrollZoom:true,displayModeBar:true});"
    "hg.__hmfp=fp;hg.__hmVis=vis;"
    "if(window.__attachHeatSync){hg.__heatSync=false;window.__attachHeatSync(hg);}}catch(e){}"
    "try{requestAnimationFrame(function(){hc.style.opacity='1';});}catch(e){hc.style.opacity='1';}"
    "setTimeout(function(){window.__hmSuppress=false;},250);"
    "}else if(vis){try{window.Plotly.Plots.resize(hg);}catch(e){}}"
    # Swap in the current metric/scale variant IN PLACE (Plotly.restyle) — instant,
    # no re-init, no flash. Every metric×scale was precomputed at bin time, so
    # flipping the metric/scale radios only touches z/zmin/zmax/colorbar here.
    "try{var e=(entered&&entered.indexOf('on')>=0)?1:0;"
    "var t=(trim&&trim.indexOf('on')>=0)?1:0;"
    "var key='e'+e+'_t'+t+'_'+(metric||'time')+'_'+(scale||'lin');"
    "var v=variants&&variants[key];"
    "if(v&&hg&&hg.data&&hg.data.length){"
    "var vi=v.z.map(function(_,i){return i;});"
    "window.Plotly.restyle(hg,{z:v.z,customdata:v.customdata},vi);"
    "window.Plotly.restyle(hg,{zmin:v.zmin,zmax:v.zmax,hovertemplate:v.hovertemplate});"
    "window.Plotly.restyle(hg,{colorbar:[v.colorbar]},[0]);}}catch(e){}}"
    "},90);return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("heatmap-figure-store", "data"),
    Input("view-mode", "value"),
    Input("heatmap-metric", "value"),
    Input("heatmap-scale", "value"),
    Input("roi-entered", "value"),
    Input("roi-trim", "value"),
    State("heatmap-variants", "data"),
    prevent_initial_call=True,
)


# Resize only the graph in the panel that just became visible or just received a
# fresh figure. This is deliberately separate from heatmap/polar newPlot logic:
# resizing hidden scaleanchor plots can emit bogus relayout ranges and make zoom
# feel broken.
app.clientside_callback(
    "function(view,tfig,rfig,vfig,dfig,roifig){"
    "function rs(id,panel){var p=document.getElementById(panel);"
    "if(!p||getComputedStyle(p).visibility==='hidden')return;"
    "var c=document.getElementById(id);var g=c&&c.querySelector('.js-plotly-plot');"
    "if(g&&window.Plotly&&window.Plotly.Plots){try{window.Plotly.Plots.resize(g);}catch(e){}}}"
    "setTimeout(function(){"
    "if(view==='traj')rs('trajectory-plot','view-traj');"
    "if(view==='diag'){rs('vel-histogram','view-diag');rs('disp-histogram','view-diag');rs('raw-trace-plot','view-diag');}"
    "if(view==='roi')rs('roi-plot','view-roi');"
    "},140);return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("view-mode", "value"),
    Input("trajectory-plot", "figure"),
    Input("raw-trace-plot", "figure"),
    Input("vel-histogram", "figure"),
    Input("disp-histogram", "figure"),
    Input("roi-plot", "figure"),
    prevent_initial_call=True,
)


# The polar plot is born in a hidden panel, and Dash's Plotly.react updates its
# traces but NOT the figure height (the SVG stays at the placeholder size and the
# subplots collapse). A fresh newPlot with the container pinned to the figure
# height renders it correctly. SVG Scatterpolar makes newPlot safe here (the
# WebGL variant crashed on re-render). Runs when the polar figure changes/opens.
app.clientside_callback(
    "function(pfig, view){if(view!=='polar')return '';setTimeout(function(){"
    "var c=document.getElementById('polar-plot');"
    "var g=c&&c.querySelector('.js-plotly-plot');"
    "if(g&&window.Plotly&&pfig&&pfig.data&&pfig.data.length){"
    "var h=(pfig.layout&&pfig.layout.height)||600;"
    "try{c.style.height=h+'px';g.style.height=h+'px';"
    "window.Plotly.newPlot(g,pfig.data,pfig.layout,{displayModeBar:true});}"
    "catch(e){}}"
    "},130);return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("polar-plot", "figure"),
    Input("view-mode", "value"),
    prevent_initial_call=True,
)


# The ROI-counts plot is lazily built (SVG, born hidden), so its figure arrives
# after the generic reveal-resize has already run — leaving it drawn at a narrow
# width. Resize it once its own figure lands.
app.clientside_callback(
    "function(fig, view){if(view!=='roi')return '';"
    "function rs(){var g=document.querySelector('#roi-plot .js-plotly-plot');"
    "if(g&&window.Plotly&&window.Plotly.Plots){try{window.Plotly.Plots.resize(g);}"
    "catch(e){}}}"
    "[150,450,900].forEach(function(d){setTimeout(rs,d);});return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("roi-plot", "figure"),
    Input("view-mode", "value"),
    prevent_initial_call=True,
)




# Pre-fill LUT editor with current configs → their auto-humanised names
@app.callback(
    Output("lut-editor", "value"),
    Input("btn-prefill-lut", "n_clicks"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)
def prefill_lut(n, pattern):
    if not pattern:
        return no_update
    df, _, _ = _load_data(pattern)
    if df is None:
        return no_update
    configs = sorted(df["ConfigFile"].unique())
    mapping = {c: humanise_config(c) for c in configs}
    return json.dumps(mapping, indent=2)


# Apply LUT overrides and trigger a replot
@app.callback(
    Output("lut-status", "children"),
    Output("btn-plot", "n_clicks", allow_duplicate=True),
    Input("btn-apply-lut", "n_clicks"),
    State("lut-editor", "value"),
    State("btn-plot", "n_clicks"),
    prevent_initial_call=True,
)
def apply_lut(n, lut_text, plot_clicks):
    global _USER_LUT
    try:
        parsed = json.loads(lut_text or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("must be a JSON object")
        _USER_LUT = {str(k): str(v) for k, v in parsed.items()}
        return f"Applied {len(_USER_LUT)} name(s)", (plot_clicks or 0) + 1
    except Exception as e:
        return f"Error: {e}", no_update


# Export — rebuild figures server-side so the HTML always embeds real data.
@app.callback(
    Output("download-html", "data"),
    Input("btn-export", "n_clicks"),
    State("store-glob", "data"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("group-by", "value"),
    State("pool-mode", "value"),
    State("color-by", "value"),
    State("animate-toggle", "value"),
    State("heatmap-binsize", "value"),
    State("heatmap-scale", "value"),
    State("heatmap-bound", "value"),
    State("heatmap-metric", "value"),
    State("heatmap-cmin", "value"),
    State("heatmap-cmax", "value"),
    State("heatmap-crange", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("raw-columns", "value"),
    State("subplot-ncols", "value"),
    State("plot-points", "value"),
    State("rebase-origin", "value"),
    State("vel-histogram", "selectedData"),
    State("disp-histogram", "selectedData"),
    State("viewport-store", "data"),
    State("data-summary", "children"),
    State("url", "search"),
    prevent_initial_call=True,
)
def export_html(n, pattern, vel_thresh, min_disp, trim, jump_buf, group_by, pool_mode,
                color_by, animate, hm_binsize, hm_scale, hm_bound, hm_metric,
                hm_cmin, hm_cmax, hm_crange, cfg, vrs, fids, scenes, folders,
                raw_cols, ncols, max_points, rebase, vel_selection, disp_selection,
                viewport, summary, url_search):
    if not pattern:
        return no_update

    df_f, df_sub, stats_sub = _filtered_df(
        pattern, vel_thresh, min_disp, trim, jump_buf,
        cfg, vrs, fids, scenes, folders, vel_selection, disp_selection)
    if df_f is None or len(df_f) == 0:
        return no_update

    ncols_val = int(ncols) if ncols and ncols >= 1 else 2
    do_animate = bool(animate) and "on" in (animate or [])
    df_plot = rebase_to_origin(df_f) if (rebase and "on" in rebase) else df_f
    traj = build_trajectory_figure(df_plot, group_by, pool_mode, ncols=ncols_val,
                                   color_by=color_by or "individual",
                                   animate=do_animate, max_points=max_points)
    heat = build_heatmap_figure(df_plot, group_by, pool_mode, ncols=ncols_val,
                                bin_size=hm_binsize, log_scale=(hm_scale == "log"),
                                bound_pct=hm_bound if hm_bound else 100,
                                metric=hm_metric or "time",
                                cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange)
    vel_fig = build_velocity_histogram(df_sub, vel_thresh)
    disp_fig = build_displacement_histogram(stats_sub, min_disp)
    raw = build_raw_trace_figure(df_f, raw_cols or [], max_points=max_points)

    if viewport and not viewport.get("reset"):
        for f in (traj, heat):
            if viewport.get("xaxis"):
                f.update_xaxes(range=viewport["xaxis"])
            if viewport.get("yaxis"):
                f.update_yaxes(range=viewport["yaxis"])

    cfgd = dict(scrollZoom=True, displaylogo=False)
    # First figure pulls in plotly.js; the rest reuse it.
    traj_h = traj.to_html(full_html=False, include_plotlyjs="cdn", config=cfgd)
    heat_h = heat.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
    vel_h = vel_fig.to_html(full_html=False, include_plotlyjs=False)
    disp_h = disp_fig.to_html(full_html=False, include_plotlyjs=False)
    raw_h = raw.to_html(full_html=False, include_plotlyjs=False, config=cfgd)

    share = f"{url_search or ''}"
    content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Trajectory Export</title>
<style>body{{font-family:system-ui,sans-serif;margin:18px;color:#222}}
h2{{margin:0 0 6px}} h3{{margin:18px 0 4px;font-size:14px;color:#555}}
.info{{background:#e9ecef;padding:8px;border-radius:4px;font-size:13px;margin:6px 0}}
.row{{display:flex;gap:10px}}.row>div{{flex:1;min-width:0}}
.share{{font-size:11px;color:#888;word-break:break-all}}</style>
</head><body>
<h2>Trajectory Export</h2>
<div class="info">{summary or ''}</div>
<div class="share">State: <code>{share}</code></div>
<h3>Trajectories</h3>{traj_h}
<h3>Heatmap</h3>{heat_h}
<h3>Velocity / Displacement</h3><div class="row"><div>{vel_h}</div><div>{disp_h}</div></div>
<h3>Raw traces</h3>{raw_h}
</body></html>"""

    ts = time.strftime("%Y%m%d_%H%M%S")
    return dict(content=content, filename=f"trajectory_export_{ts}.html")


# ---------------------------------------------------------------------------
# Clientside playback (sticky bar drives native Plotly frames, no round-trips)
# ---------------------------------------------------------------------------

_JS_GD = ("var c=document.getElementById('trajectory-plot');"
          "var gd=c&&c.querySelector('.js-plotly-plot');")

app.clientside_callback(
    "function(n){" + _JS_GD +
    "if(gd&&window.Plotly){window.Plotly.animate(gd,null,{frame:{duration:120,redraw:true},"
    "fromcurrent:true,transition:{duration:0},mode:'immediate'});}"
    "return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("anim-play", "n_clicks"), prevent_initial_call=True,
)

app.clientside_callback(
    "function(n){" + _JS_GD +
    "if(gd&&window.Plotly){window.Plotly.animate(gd,[null],{mode:'immediate',"
    "frame:{duration:0,redraw:false},transition:{duration:0}});}"
    "return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("anim-pause", "n_clicks"), prevent_initial_call=True,
)

app.clientside_callback(
    "function(v){" + _JS_GD +
    "if(gd&&window.Plotly){var fr=(gd._transitionData&&gd._transitionData._frames)||[];"
    "var nf=fr.length; if(!nf) return '';"
    "var f=Math.round(v/100*(nf-1));"
    "window.Plotly.animate(gd,[String(f)],{mode:'immediate',frame:{duration:0,redraw:true},"
    "transition:{duration:0}});}"
    "return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("anim-slider", "value"), prevent_initial_call=True,
)

# Show the playback bar only when the trajectory figure actually has frames.
app.clientside_callback(
    "function(fig){var has=fig&&fig.frames&&fig.frames.length>0;"
    "return {display: has?'flex':'none', alignItems:'center', gap:'8px',"
    "padding:'4px 10px 2px', background:'#fff', borderBottom:'1px solid #e3e6ee'};}",
    Output("anim-bar", "style"),
    Input("trajectory-plot", "figure"),
)


# Fix the occasional scaleanchor blow-up: the WebGL trajectory can render its
# aspect-locked axes at a huge range if it's first drawn before its container is
# sized (data ends up a speck at ±16000). Detect it — rendered span ≫ the range
# the figure asked for — and autorange back to the data extent. Only fires on the
# rare blow-up, so a genuine zoom is left alone.
app.clientside_callback(
    "function(tfig, view){if(view!=='traj')return '';setTimeout(function(){"
    "var c=document.getElementById('trajectory-plot');"
    "var g=c&&c.querySelector('.js-plotly-plot');"
    "if(g&&window.Plotly&&tfig&&tfig.layout){"
    "var L=tfig.layout;var xr=L.xaxis&&L.xaxis.range;"
    "var rx=g.layout&&g.layout.xaxis&&g.layout.xaxis.range;"
    "if(xr&&rx&&Math.abs(rx[1]-rx[0])>3*Math.abs(xr[1]-xr[0])){"
    "try{window.Plotly.relayout(g,{'xaxis.autorange':true,'yaxis.autorange':true});}"
    "catch(e){}}}"
    "},160);return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("trajectory-plot", "figure"),
    Input("view-mode", "value"),
    prevent_initial_call=True,
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Trajectory Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python app.py
  python app.py --glob "Data/2025*/*_VR*.csv"
  python app.py --glob "MatrexVR_data/20250423_131431"
""")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--glob", default="")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.glob:
        print(f"Pre-loading: {args.glob}")
        _load_data(args.glob)
        for child in app.layout.children:
            if hasattr(child, "id") and child.id == "url":
                child.search = "?" + urlencode({"glob": args.glob})
                break

    print(f"Dashboard: http://{args.host}:{args.port}/")
    app.run(host=args.host, port=args.port, debug=args.debug)
