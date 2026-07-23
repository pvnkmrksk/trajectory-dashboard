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
import logging
import math
import os
import platform
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.colors as pcolors
from plotly.subplots import make_subplots
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

from trajectory_dashboard import grouping as td_grouping
from trajectory_dashboard import io as td_io

REPO_URL = "https://github.com/pvnkmrksk/trajectory-dashboard"
LOGGER = logging.getLogger("trajectory_dashboard")


def _configure_logging(level="INFO"):
    """Send concise, structured runtime diagnostics to the server terminal."""
    numeric = getattr(logging, str(level or "INFO").upper(), logging.INFO)
    if not LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(numeric)
    LOGGER.propagate = False


def _dash_error_handler(exc):
    """Log callback failures with the complete traceback before Dash responds."""
    LOGGER.error(
        "callback.failed type=%s message=%s",
        type(exc).__name__, exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )

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
_USER_CONFIG_ORDER: dict[str, int] = {}


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

    Prefer the sequenceConfig that covers the most loaded configs. Missing
    configs sort alphabetically after the known sequenceConfig entries.
    """
    if _USER_CONFIG_ORDER:
        _CONFIG_ORDER.clear()
        _CONFIG_ORDER.update(_USER_CONFIG_ORDER)
        return

    _CONFIG_ORDER.clear()
    all_cfgs = set()
    candidates = []
    for m in metas or []:
        cfgs = {_short_config_name(k) for k in (m.get("configs") or {}).keys()}
        cfgs.update(m.get("sequence_order") or [])
        all_cfgs.update(cfgs)
        order = []
        seen = set()
        for cfg in m.get("sequence_order") or []:
            if cfg not in seen:
                seen.add(cfg)
                order.append(cfg)
        if order:
            candidates.append(order)

    if not candidates:
        return

    best = max(candidates,
               key=lambda order: (len(set(order) & all_cfgs), len(order)))
    for cfg in best:
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


_ROI_RESIDENCE_COLS = ["_seg_id", "ConfigFile", "animal", "VR", "FlyID",
                       "residence_left", "residence_right"]


def roi_residence_table(df, rois_by_cfg, reach) -> pd.DataFrame:
    """Per-trial seconds spent inside left/right ROI. Trials that never enter
    contribute zero, so per-animal means are directly comparable to the reached
    fraction swarm."""
    if df is None or len(df) == 0 or not rois_by_cfg:
        return pd.DataFrame(columns=_ROI_RESIDENCE_COLS)
    reach2 = float(reach) ** 2
    parts = []
    for cfg, sub in df.groupby("ConfigFile", sort=False, observed=True):
        rois = rois_by_cfg.get(cfg)
        if not rois:
            continue
        gx = sub["GameObjectPosX"].to_numpy()
        gz = sub["GameObjectPosZ"].to_numpy()
        seg = sub["_seg_id"].to_numpy()
        starts = np.concatenate(([0], np.flatnonzero(seg[1:] != seg[:-1]) + 1))

        t = sub["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
        dt = np.empty(len(sub), dtype=float)
        if len(sub) > 1:
            same_next = seg[1:] == seg[:-1]
            raw_dt = np.diff(t)
            good = same_next & np.isfinite(raw_dt) & (raw_dt > 0)
            fallback = float(np.median(raw_dt[good])) if good.any() else _median_dt(sub)
            dt[:-1] = np.where(good, raw_dt, fallback)
            dt[-1] = fallback
        else:
            dt[0] = _median_dt(sub)

        dwell = {}
        for side in ("left", "right"):
            centers = [r for r in rois if r["side"] == side]
            if not centers:
                continue
            inside = np.zeros(len(sub), bool)
            for r in centers:
                inside |= (gx - r["x"]) ** 2 + (gz - r["z"]) ** 2 <= reach2
            dwell[side] = np.add.reduceat(np.where(inside, dt, 0.0), starts)
        if not dwell:
            continue
        parts.append(pd.DataFrame({
            "_seg_id": seg[starts],
            "ConfigFile": sub["ConfigFile"].to_numpy()[starts],
            "VR": sub["VR"].to_numpy()[starts],
            "FlyID": sub["FlyID"].to_numpy()[starts],
            "residence_left": dwell.get("left", np.zeros(len(starts))),
            "residence_right": dwell.get("right", np.zeros(len(starts))),
        }))

    if not parts:
        return pd.DataFrame(columns=_ROI_RESIDENCE_COLS)
    out = pd.concat(parts, ignore_index=True)
    out["animal"] = out["FlyID"].astype(str) + "@" + out["VR"].astype(str)
    return out[_ROI_RESIDENCE_COLS]


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
    keep = np.ones(len(df), bool)
    row_pos = pd.Series(np.arange(len(df)), index=df.index)
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
        starts = np.concatenate(([0], np.flatnonzero(seg[1:] != seg[:-1]) + 1))
        lens = np.diff(np.concatenate((starts, [len(sub)])))
        # Cumulative hit count reset at segment boundaries: entered is true
        # from the first in-ROI sample onward, without a per-segment Python loop.
        cs = np.cumsum(inside.astype(np.int64))
        base = np.repeat(cs[starts] - inside[starts].astype(np.int64), lens)
        entered = (cs - base) > 0
        exit_flag = entered & (~inside)
        big = len(sub) + 1
        first_exit = np.minimum.reduceat(
            np.where(exit_flag, np.arange(len(sub)), big), starts)
        sub_keep = np.arange(len(sub)) <= np.repeat(first_exit, lens)
        keep[row_pos.loc[sub.index].to_numpy()] = sub_keep
    return keep


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
    return td_grouping.jump_buffer_seconds(v)


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


def _frame_cache_token(df):
    """Stable identity for cached frame-derived calculations.

    Python's ``id(df)`` is only an address for the lifetime of one object. It
    prevents useful reuse across equivalent slices and can be recycled after a
    dataset is released. Loaded, filtered, ROI-masked and decimated frames carry
    an explicit lineage token; small ad-hoc frames used by tests get a bounded
    structural fallback.
    """
    if df is None:
        return ("none",)
    token = getattr(df, "attrs", {}).get("_frame_token")
    if token is not None:
        return token
    n = int(len(df))
    if n == 0:
        return ("empty", tuple(df.columns))
    idx = df.index
    seg = df["_seg_id"] if "_seg_id" in df else None
    return (
        "frame", n, str(idx[0]), str(idx[-1]),
        str(seg.iloc[0]) if seg is not None else "",
        str(seg.iloc[-1]) if seg is not None else "",
    )


def _roi_mask_key(df, pattern, reach):
    return (_frame_cache_token(df), pattern, round(float(reach or 3.0), 6))


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
    if df_view is not df_f:
        df_view.attrs["_frame_token"] = (
            "roi", _frame_cache_token(df_f), round(reach_v, 6),
            bool(entered_only), bool(trim), int(len(df_view)))
    return df_view, table


def load_csv_fast(filepath: str) -> pd.DataFrame | None:
    return td_io.load_csv_fast(filepath)


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


def compute_segment_stats(df: pd.DataFrame, vel: np.ndarray | None = None) -> pd.DataFrame:
    """Per-segment stats from contiguous `_seg_id` blocks."""
    cols = ["seg_id", "n_points", "displacement", "peak_velocity",
            "median_velocity", "config", "vr", "fly_id", "scene", "source_folder"]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=cols)
    vel = velocity_all(df) if vel is None else vel
    seg = df["_seg_id"].to_numpy()
    starts = np.concatenate(([0], np.flatnonzero(seg[1:] != seg[:-1]) + 1))
    ends = np.concatenate((starts[1:], [len(df)]))
    lens = ends - starts
    keep = lens >= 2
    x = df["GameObjectPosX"].to_numpy()
    z = df["GameObjectPosZ"].to_numpy()
    peak_in = np.where(np.isfinite(vel), vel, -np.inf)
    peak = np.maximum.reduceat(peak_in, starts)
    peak[~np.isfinite(peak)] = 0.0
    median = (
        pd.Series(vel)
        .groupby(seg, sort=False)
        .median()
        .fillna(0.0)
        .to_numpy()
    )

    out = pd.DataFrame({
        "seg_id": seg[starts],
        "n_points": lens,
        "displacement": np.hypot(x[ends - 1] - x[starts], z[ends - 1] - z[starts]),
        "peak_velocity": peak,
        "median_velocity": median,
    })
    meta_cols = {"config": "ConfigFile", "vr": "VR", "fly_id": "FlyID",
                 "scene": "SceneName", "source_folder": "SourceFolder"}
    for outcol, src in meta_cols.items():
        out[outcol] = df[src].to_numpy()[starts] if src in df.columns else ""
    return out.loc[keep].reset_index(drop=True)


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

    changed = False
    # 1) Velocity-jump removal with a time buffer around each jump
    if vel_threshold is not None and vel_threshold > 0:
        vel = velocity_all(df)
        is_jump = np.nan_to_num(vel, nan=0.0) > vel_threshold
        if is_jump.any():
            seg = df["_seg_id"].to_numpy()
            t = df["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
            keep = _dilate_keep(seg, t, is_jump, float(jump_buffer))
            df = df[keep]
            changed = True

    # 2) Minimum net-displacement per segment
    if min_disp is not None and min_disp > 0 and len(df):
        g = df.groupby("_seg_id", sort=False)
        x0 = g["GameObjectPosX"].transform("first")
        z0 = g["GameObjectPosZ"].transform("first")
        x1 = g["GameObjectPosX"].transform("last")
        z1 = g["GameObjectPosZ"].transform("last")
        disp = np.sqrt((x1 - x0)**2 + (z1 - z0)**2)
        df = df[disp >= min_disp]
        changed = True

    # 3) Trim N samples from each segment end
    if trim_samples is not None and trim_samples > 0 and len(df):
        g = df.groupby("_seg_id", sort=False)
        pos = g.cumcount()
        size = g["_seg_id"].transform("size")
        df = df[(pos >= trim_samples) & (pos < size - trim_samples)]
        changed = True

    return df.reset_index(drop=True) if changed else df


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
BUDGET_GL = 300_000      # static WebGL trajectories, Accuracy mode
BUDGET_SVG = 40_000      # animated trajectories, Accuracy mode
BUDGET_RAW = 25_000      # raw time-series plot, Accuracy mode
BUDGET_POLAR = 30_000    # polar plot (SVG Scatterpolar), Accuracy mode
BUDGET_GL_SPEED = 140_000
BUDGET_SVG_SPEED = 24_000
BUDGET_RAW_SPEED = 10_000
BUDGET_POLAR_SPEED = 12_000
BUDGET_HEAT_SPEED = 220_000
BUDGET_ROI_SPEED = 180_000
PLOT_DEBOUNCE_MS = 750
_POLAR_RAY_CACHE: dict = {}
_POLAR_RAY_CACHE_ORDER: list = []
_POLAR_RAY_CACHE_MAX = 8

# Per-subplot pixel height. With a 2-col layout each subplot is ~half the main
# width, so ~480px tall keeps each box roughly square; the page scrolls when
# there are many rows rather than squishing them.
SUBPLOT_PX = 480
SUBPLOT_PX_COMPACT = 390

SEQ_COLORSCALE = "Viridis"


def _render_mode(mode) -> str:
    return "accuracy" if str(mode or "").lower() == "accuracy" else "speed"


def _budget(default_budget, speed_budget, mode, override=None) -> int:
    if override and override > 0:
        return int(override)
    return int(speed_budget if _render_mode(mode) == "speed" else default_budget)


def _segment_endpoint_keep(segids, max_points=None, points_per_segment=None) -> np.ndarray:
    """Endpoint-safe segment decimation mask for already-contiguous segments."""

    seg = np.asarray(segids)
    n = len(seg)
    if n == 0:
        return np.zeros(0, dtype=bool)
    starts = np.concatenate(([0], np.flatnonzero(seg[1:] != seg[:-1]) + 1))
    lens = np.diff(np.concatenate((starts, [n])))
    if points_per_segment is None:
        if not max_points or max_points <= 0 or n <= int(max_points):
            return np.ones(n, dtype=bool)
        points_per_segment = max(2, int(max_points) // max(len(starts), 1))
    pts = max(2, int(points_per_segment))
    if np.all(lens <= pts):
        return np.ones(n, dtype=bool)

    pos = np.arange(n) - np.repeat(starts, lens)
    seg_len = np.repeat(lens, lens)
    denom = max(1, pts - 1)
    step = np.repeat(np.maximum(1, np.ceil((lens - 1) / denom).astype(int)), lens)
    return (pos == 0) | (pos == (seg_len - 1)) | ((pos % step) == 0)


def _decimate_frame(df: pd.DataFrame, max_points=None) -> pd.DataFrame:
    if df is None or len(df) == 0 or not max_points or len(df) <= int(max_points):
        return df
    keep = _segment_endpoint_keep(df["_seg_id"].to_numpy(), max_points=max_points)
    out = df[keep]
    out.attrs["_frame_token"] = (
        "decimated", _frame_cache_token(df), int(max_points), int(len(out)))
    return out


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
    return td_grouping.group_frames(
        df, group_by, pool_mode, config_order=_CONFIG_ORDER,
        labeler=humanise_config,
    )


def _sample_scale(t):
    t = 0.0 if not np.isfinite(t) else max(0.0, min(1.0, float(t)))
    return pcolors.sample_colorscale(SEQ_COLORSCALE, [t])[0]


def _numeric_labels(values) -> np.ndarray:
    arr = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy()
    out = np.asarray(values).astype(str)
    whole = np.isfinite(arr) & (arr == np.floor(arr))
    if whole.any():
        out[whole] = arr[whole].astype(np.int64).astype(str)
    return out


def _color_maps(df):
    individuals = sorted(df[["VR", "FlyID"]].drop_duplicates().itertuples(index=False, name=None))
    ind_color = {k: COLORS[i % len(COLORS)] for i, k in enumerate(individuals)}
    vr_cats = sorted(df["VR"].dropna().unique())
    vr_color = {v: COLORS[i % len(COLORS)] for i, v in enumerate(vr_cats)}
    tmin = float(df["CurrentTrial"].min()) if "CurrentTrial" in df else 0.0
    tmax = float(df["CurrentTrial"].max()) if "CurrentTrial" in df else 1.0
    return ind_color, vr_color, tmin, tmax


def _nan_join(x, y, segids, mc=None, customdata=None):
    """Concatenate already-contiguous segments inserting NaN gaps between them."""
    if len(x) == 0:
        return x, y, mc, customdata
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    bnd = np.flatnonzero(segids[1:] != segids[:-1]) + 1
    xx = np.insert(x, bnd, np.nan)
    yy = np.insert(y, bnd, np.nan)
    mm = np.insert(mc, bnd, np.nan) if mc is not None else None
    cc = None
    if customdata is not None:
        custom = np.asarray(customdata, dtype=object)
        if custom.ndim == 1:
            custom = custom.reshape(-1, 1)
        gap = np.empty((len(bnd), custom.shape[1]), dtype=object)
        gap[:] = ""
        cc = np.insert(custom, bnd, gap, axis=0)
    return xx, yy, mm, cc


def _record_arrays(rec, frac=1.0):
    """Build NaN-joined arrays for a record, optionally truncated to time `frac`."""
    if frac >= 1.0:
        return _nan_join(rec["x"], rec["y"], rec["segids"], rec["mc"], rec.get("customdata"))
    keepn = np.ceil(np.maximum(frac, 1e-9) * rec["dlen"]).astype(int)
    m = rec["dpos"] < keepn
    mc = rec["mc"][m] if rec["mc"] is not None else None
    custom = rec["customdata"][m] if rec.get("customdata") is not None else None
    return _nan_join(rec["x"][m], rec["y"][m], rec["segids"][m], mc, custom)


def _prepare_merged_groups(df, group_by, pool_mode, ncols, color_by, budget,
                           roi_outcomes=None):
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
    outcome_map = {str(k): str(v) for k, v in (roi_outcomes or {}).items()}

    legend_seen, records = set(), []
    for idx, gname in enumerate(group_names):
        gdf = groups[gname]
        row, col = idx // ncols + 1, idx % ncols + 1

        # Vectorised endpoint-safe decimation: every segment keeps its first and
        # last sample, then thins the interior to the point budget.
        keep = _segment_endpoint_keep(gdf["_seg_id"].to_numpy(),
                                      points_per_segment=pts_lim)
        dec = gdf.loc[keep]
        if len(dec) == 0:
            continue

        segids = dec["_seg_id"].to_numpy()
        x = dec["GameObjectPosX"].to_numpy()
        y = dec["GameObjectPosZ"].to_numpy()
        custom_all = np.column_stack([
            _numeric_labels(dec["CurrentTrial"].to_numpy()),
            _numeric_labels(dec["CurrentStep"].to_numpy()),
            dec["FlyID"].astype(str).to_numpy(),
            dec["VR"].astype(str).to_numpy(),
            dec["ConfigFile"].astype(str).to_numpy(),
            dec["SourceFile"].astype(str).to_numpy(),
            dec["_seg_id"].astype(str).to_numpy(),
        ])
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
        elif color_by == "roi":
            ck = (dec["_seg_id"].astype(str).map(outcome_map)
                  .fillna("No ROI").to_numpy(dtype=str))
        else:  # individual
            fid = dec["FlyID"].to_numpy()
            ck = np.char.add(np.char.add(vr.astype(str), "|"), fid.astype(str))

        for key in pd.unique(ck):
            m = ck == key
            rec = dict(row=row, col=col, segids=segids[m], x=x[m], y=y[m],
                       dpos=dpos[m], dlen=dlen[m], customdata=custom_all[m],
                       mc=None, mode="lines",
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
            elif color_by == "roi":
                label = str(key)
                rec["color"] = _ROI_OUTCOME_COLOR.get(label, _ROI_OUTCOME_COLOR["No ROI"])
                rec["label"] = label
            else:  # individual
                vrv, fidv = str(key).split("|", 1)
                rec["color"] = ind_color.get((vrv, fidv), COLORS[0])
                parts = [p for p in (vrv if vrv and vrv != "unknown" else None,
                                     f"fly{fidv}" if fidv and fidv != "unknown" else None) if p]
                rec["label"] = " ".join(parts) or str(key)

            if color_by in ("individual", "vr", "roi"):
                rec["legendgroup"] = rec["label"]
                rec["showlegend"] = rec["label"] not in legend_seen
                legend_seen.add(rec["label"])
            records.append(rec)

    return group_names, records


def _add_traj_trace(fig, td, TraceType, hover=True):
    common = dict(name=td["label"], legendgroup=td["legendgroup"],
                  showlegend=td["showlegend"], opacity=0.75)
    if td.get("customdata") is not None:
        common["customdata"] = td["customdata"]
    if td["mode"] == "markers":
        common["marker"] = dict(size=3, color=td["marker_color"],
                                 colorscale=td["colorscale"],
                                 cmin=td["cmin"], cmax=td["cmax"])
    else:
        common["line"] = dict(color=td["line_color"], width=1.2)
    if hover:
        common["hovertemplate"] = (
            "<b>%{customdata[2]} @ %{customdata[3]}</b><br>"
            "trial=%{customdata[0]}<br>"
            "step=%{customdata[1]}<br>"
            "config=%{customdata[4]}<br>"
            "file=%{customdata[5]}<br>"
            "segment=%{customdata[6]}<br>"
            "x=%{x:.1f} z=%{y:.1f}<extra></extra>"
        ) if td.get("customdata") is not None else (
            f"<b>{td['label']}</b><br>x=%{{x:.1f}} z=%{{y:.1f}}<extra></extra>"
        )
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
    out.attrs["_frame_token"] = ("rebased", _frame_cache_token(df), int(len(out)))
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
_ROI_OUTCOME_COLOR = {
    "Left ROI": "#1f77b4",
    "Right ROI": "#ff7f0e",
    "No ROI": "#8a8f98",
}


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


def _roi_count_texts(gname, gdf, counts, outcomes=None) -> tuple[str, str]:
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
        if outcomes:
            vals = [outcomes.get(str(sid), "No ROI") for sid in sub["_seg_id"].to_numpy()]
            left = sum(v == "Left ROI" for v in vals)
            right = sum(v == "Right ROI" for v in vals)
            return (f"L-first {left}/{total} ({100 * left / total:.0f}%)",
                    f"R-first {right}/{total} ({100 * right / total:.0f}%)")
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


def _roi_count_annotations(group_items, counts, outcomes=None) -> list:
    """Left/right corner-tally annotations per subplot. Fixed slots — index
    n+2*i / n+2*i+1 for group i — so the reach slider can Patch text by index."""
    anns = []
    for i, (gname, gdf) in enumerate(group_items):
        sx, sy = _subplot_axis(i + 1)
        left_txt, right_txt = _roi_count_texts(gname, gdf, counts, outcomes)
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


def roi_outcome_by_segment(df, rois_by_cfg, reach) -> dict[str, str]:
    """Map each segment to the first side it reaches: left, right, or neither."""
    if df is None or len(df) == 0:
        return {}
    out = {str(s): "No ROI" for s in pd.unique(df["_seg_id"])}
    if not rois_by_cfg:
        return out
    reach2 = float(reach or 3.0) ** 2
    for cfg, sub in df.groupby("ConfigFile", sort=False, observed=True):
        rois = rois_by_cfg.get(str(cfg)) or []
        if not rois:
            continue
        gx = sub["GameObjectPosX"].to_numpy()
        gz = sub["GameObjectPosZ"].to_numpy()
        left = np.zeros(len(sub), dtype=bool)
        right = np.zeros(len(sub), dtype=bool)
        for roi in rois:
            side = roi.get("side")
            if side not in ("left", "right"):
                continue
            hit = (gx - float(roi["x"])) ** 2 + (gz - float(roi["z"])) ** 2 <= reach2
            if side == "left":
                left |= hit
            else:
                right |= hit
        if not (left.any() or right.any()):
            continue
        seg = sub["_seg_id"].to_numpy()
        starts = np.concatenate(([0], np.flatnonzero(seg[1:] != seg[:-1]) + 1))
        pos = np.arange(len(sub))
        big = len(sub) + 1
        first_left = np.minimum.reduceat(np.where(left, pos, big), starts)
        first_right = np.minimum.reduceat(np.where(right, pos, big), starts)
        segs = seg[starts]
        for sid, fl, fr in zip(segs, first_left, first_right):
            if fl == big and fr == big:
                continue
            out[str(sid)] = "Left ROI" if fl <= fr else "Right ROI"
    return out


def build_trajectory_figure(df, group_by="config", pool_mode="separate",
                            ncols=2, color_by="individual", animate=True,
                            max_points=None, rois=None, reach_radius=3.0,
                            show_rois=False, roi_counts=None,
                            roi_outcomes=None, view_range=None):
    if df is None or len(df) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No trajectories match the active filters.", showarrow=False,
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
                                        color_by, budget,
                                        roi_outcomes=roi_outcomes)

    def _rec_to_td(rec, x, y, mc):
        return dict(x=x, y=y, row=rec["row"], col=rec["col"], mode=rec["mode"],
                    line_color=rec["color"], marker_color=mc,
                    colorscale=rec["colorscale"], cmin=rec["cmin"], cmax=rec["cmax"],
                    showlegend=rec["showlegend"], legendgroup=rec["legendgroup"],
                    label=rec["label"], customdata=rec.get("customdata_joined"))

    # Base traces (full extent)
    for rec in records:
        x, y, mc, custom = _record_arrays(rec, 1.0)
        rec["customdata_joined"] = custom
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
                x, y, mc, _custom = _record_arrays(rec, frac)
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

    _apply_axis_sync(fig, nrows, ncols, df, uirev="traj_view", rng=view_range)

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
                                               roi_counts if overlay else None,
                                               roi_outcomes if overlay else None))

    show_legend = color_by in ("individual", "vr", "roi")
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
MAX_HEATMAP_CELLS = 500_000  # total cells per subplot before auto-coarsening
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
        return f"{v:.3g}%"
    if metric == "time":
        if v >= 600:
            return f"{v/60:.3g}m"
        if v >= 1:
            return f"{v:.3g}s"
        return f"{v*1000:.3g}ms"
    # count
    if v >= 1000:
        return f"{v/1000:.3g}k"
    return f"{v:.3g}"


def _rgba(hex_color: str, alpha: float) -> str:
    h = str(hex_color or "#666").lstrip("#")
    if len(h) != 6:
        return f"rgba(102,102,102,{alpha:g})"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:g})"


def _roi_metric_value(count: float, total: float, metric: str, dt: float) -> float:
    if metric == "time":
        return float(count) * float(dt)
    if metric == "percent":
        return 100.0 * float(count) / max(float(total), 1.0)
    return float(count)


def _heatmap_roi_label(side_name: str, count: float, total: float,
                       metric: str, dt: float) -> str:
    side = str(side_name or "?")[:1].upper()
    frac = 100.0 * count / max(total, 1.0)
    val = _roi_metric_value(count, total, metric, dt)
    if metric == "count":
        return f"{side} {_fmt_metric(val, metric)} samples ({frac:.1f}%)"
    if metric == "percent":
        return f"{side} {_fmt_metric(val, metric)}"
    return f"{side} {_fmt_metric(val, metric)} ({frac:.1f}%)"


def _heatmap_roi_corner_texts(group_roi_stats, metric: str, dt: float) -> list[str]:
    texts = []
    for stats in group_roi_stats or []:
        total = max((float(s.get("total", 0.0) or 0.0) for s in stats), default=0.0)
        by_side = {"left": 0.0, "right": 0.0}
        for stat in stats:
            side = stat.get("side")
            if side in by_side:
                by_side[side] = max(
                    by_side[side],
                    float(stat.get("side_total", stat.get("count", 0.0)) or 0.0),
                )
        for side in ("left", "right"):
            texts.append(_heatmap_roi_label(side, by_side[side], total, metric, dt)
                         if total and by_side[side] else "")
    return texts


def _heatmap_roi_stats(group_items, rois_by_cfg, reach) -> list[list[dict]]:
    """Per-subplot ROI occupancy in raw samples, matching each row's config."""
    if not rois_by_cfg:
        return [[] for _ in group_items]
    reach2 = float(reach or 3.0) ** 2
    out = []
    for _, gdf in group_items:
        total = int(len(gdf)) if gdf is not None else 0
        by_sig = {}
        side_hits = {side: np.zeros(total, dtype=bool) for side in ("left", "right")}
        if total and "ConfigFile" in gdf:
            for cfg, sub in gdf.groupby("ConfigFile", sort=False, observed=True):
                rois = rois_by_cfg.get(str(cfg)) or []
                if not rois:
                    continue
                gx = sub["GameObjectPosX"].to_numpy()
                gz = sub["GameObjectPosZ"].to_numpy()
                sub_pos = gdf.index.get_indexer(sub.index)
                for roi in rois:
                    side = roi.get("side")
                    if side not in ("left", "right", "centre"):
                        continue
                    sig = (side, round(float(roi["x"]), 4), round(float(roi["z"]), 4))
                    stat = by_sig.setdefault(sig, {
                        "side": side, "x": float(roi["x"]), "z": float(roi["z"]),
                        "count": 0, "total": total,
                    })
                    hit = (gx - stat["x"]) ** 2 + (gz - stat["z"]) ** 2 <= reach2
                    stat["count"] += int(hit.sum())
                    if side in side_hits and len(sub_pos) == len(hit):
                        side_hits[side][sub_pos] |= hit
        side_totals = {side: int(mask.sum()) for side, mask in side_hits.items()}
        for stat in by_sig.values():
            side = stat.get("side")
            if side in side_totals:
                stat["side_total"] = side_totals[side]
        out.append(list(by_sig.values()))
    return out


def _heatmap_roi_shapes(group_roi_stats, reach) -> list[dict]:
    shapes = []
    for i, stats in enumerate(group_roi_stats or []):
        sx, sy = _subplot_axis(i + 1)
        for stat in stats:
            col = _ROI_SIDE_COLOR.get(stat.get("side"), "#6c757d")
            x, z = float(stat["x"]), float(stat["z"])
            shapes.append(dict(
                type="circle", xref=sx, yref=sy, layer="above",
                x0=x - reach, x1=x + reach, y0=z - reach, y1=z + reach,
                fillcolor="rgba(0,0,0,0)", opacity=0.32,
                line=dict(color=_rgba(col, 0.72), width=1.1, dash="dot"),
            ))
    return shapes


def _heatmap_roi_annotations(group_roi_stats, roi_texts) -> list[dict]:
    anns = []
    for i, _stats in enumerate(group_roi_stats or []):
        sx, sy = _subplot_axis(i + 1)
        left_txt = roi_texts[2 * i] if 2 * i < len(roi_texts) else ""
        right_txt = roi_texts[2 * i + 1] if 2 * i + 1 < len(roi_texts) else ""
        for side, text, x, anchor in (
            ("left", left_txt, 0.01, "left"),
            ("right", right_txt, 0.99, "right"),
        ):
            col = _ROI_SIDE_COLOR[side]
            anns.append(dict(
                name=f"hm-roi-{side}", text=text, showarrow=False,
                xref=f"{sx} domain", yref=f"{sy} domain",
                x=x, y=1.025, xanchor=anchor, yanchor="bottom",
                align=anchor, font=dict(size=10, color=_rgba(col, 0.95)),
                bgcolor="rgba(255,255,255,0)",
                bordercolor="rgba(0,0,0,0)", borderwidth=0,
            ))
    return anns


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
    span_x = max(float(rx[1] - rx[0]), 0.0)
    span_z = max(float(rz[1] - rz[0]), 0.0)
    span = max(span_x, span_z)
    if not np.isfinite(bs) or bs <= 0:
        bs = max(span / 20.0, 1.0)
    if not np.isfinite(span) or span <= 0:
        span = bs
        span_x = span_z = span
    n_x = max(1, int(np.ceil(span_x / bs)))
    n_z = max(1, int(np.ceil(span_z / bs)))
    axis_scale = max(n_x, n_z) / MAX_HEATMAP_BINS
    cell_scale = math.sqrt((n_x * n_z) / MAX_HEATMAP_CELLS)
    scale = max(1.0, axis_scale, cell_scale)
    if scale > 1.0:
        bs *= scale
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


def _heatmap_bins(df, group_by, pool_mode, ncols, bin_size, bound_pct,
                  rois_by_cfg=None, reach_radius=3.0):
    """The expensive, metric-independent part: 2-D histogram (raw counts) per
    subplot. All metric/scale variants derive from this, so it's computed once."""
    groups = _group_frames(df, group_by, pool_mode, ncols)
    group_items = list(groups.items())
    group_names = list(groups.keys())
    nrows = max(1, (len(group_names) + ncols - 1) // ncols)
    xedges, yedges, rng = _heatmap_edges(df, bin_size, bound_pct)
    counts = _counts_for_groups(groups, group_names, xedges, yedges)
    xc = 0.5 * (xedges[:-1] + xedges[1:])
    yc = 0.5 * (yedges[:-1] + yedges[1:])
    reach_v = float(reach_radius or 3.0)
    return dict(group_names=group_names, nrows=nrows, xc=xc.tolist(),
                yc=yc.tolist(), rng=rng, counts=counts, dt=_median_dt(df),
                reach=reach_v,
                roi_stats=_heatmap_roi_stats(group_items, rois_by_cfg,
                                             reach_v))


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
    roi_texts = _heatmap_roi_corner_texts(bins.get("roi_stats", []), metric, dt)
    return dict(z=z_list, customdata=cd_list, zmin=zmin, zmax=zmax,
                colorbar=cbar, hovertemplate=hov, roi_texts=roi_texts)


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
    if bins.get("roi_stats"):
        fig.update_layout(
            shapes=_heatmap_roi_shapes(bins["roi_stats"], bins.get("reach", 3.0)),
            annotations=list(fig.layout.annotations)
            + _heatmap_roi_annotations(bins["roi_stats"], var.get("roi_texts", [])),
        )
    fig.update_layout(height=60 + nrows * _subplot_px(nrows, ncols),
                      margin=dict(l=50, r=80, t=50, b=40), template="plotly_white",
                      dragmode="pan", showlegend=False)
    return fig


def build_heatmap_figure(df, group_by="config", pool_mode="separate", ncols=2,
                         bin_size=20.0, log_scale=False, bound_pct=98.0,
                         metric="count", cmin=None, cmax=None, crange_mode="value",
                         rois=None, reach_radius=3.0):
    if df is None or len(df) == 0:
        return _msg_figure("No trajectories match the active filters.")
    bins = _heatmap_bins(df, group_by, pool_mode, ncols, bin_size, bound_pct,
                         rois_by_cfg=rois, reach_radius=reach_radius)
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
                               metric, log_scale, cmin, cmax, crange_mode,
                               rois=None, reach_radius=3.0):
    """(figure for the current metric/scale, {all metric×scale variants}) — bins
    ONCE and reuses it, so the store of swap-in data is essentially free."""
    if df is None or len(df) == 0:
        return _msg_figure("No trajectories match the active filters."), {}
    bins = _heatmap_bins(df, group_by, pool_mode, ncols, bin_size, bound_pct,
                         rois_by_cfg=rois, reach_radius=reach_radius)
    cur = _heatmap_variant(bins, log_scale=log_scale, metric=metric, cmin=cmin,
                           cmax=cmax, crange_mode=crange_mode)
    fig = _assemble_heatmap(bins, cur, ncols, df)
    return fig, _all_variants_from_bins(bins, cmin, cmax, crange_mode)


def build_heatmap_mask_variants(df_f, pattern, reach, group_by, pool_mode, ncols,
                                bin_size, bound_pct, cmin, cmax, crange_mode,
                                do_rebase, entered_only=False, trim_tail=False,
                                max_points=None, metric="time", log_scale=False):
    """Current ROI-mask heatmap + metric/scale variants for that one state.

    Earlier builds precomputed all four entered-only × tail-trim states. That
    made tab-open expensive on million-row folders and blocked the very plot the
    user was trying to pan. ROI mask toggles now rebuild the current state; metric
    and scale still swap clientside from this state's variants.
    """
    if df_f is None or len(df_f) == 0:
        return _msg_figure("No trajectories match the active filters."), {}
    reach_v = float(reach) if reach else 3.0
    df_view, _ = _roi_apply(df_f, pattern, reach_v, entered_only, trim_tail)
    base = rebase_to_origin(df_view) if (do_rebase and len(df_view)) else df_view
    base = _decimate_frame(base, max_points)
    if len(base) == 0:
        return _msg_figure("No trajectories remain after target filtering."), {}
    xedges, yedges, rng = _heatmap_edges(base, bin_size, bound_pct)
    xc = (0.5 * (xedges[:-1] + xedges[1:])).tolist()
    yc = (0.5 * (yedges[:-1] + yedges[1:])).tolist()
    group_names = list(_group_frames(base, group_by, pool_mode, ncols).keys())
    nrows = max(1, (len(group_names) + ncols - 1) // ncols)
    dt = _median_dt(base)

    e, t = int(bool(entered_only)), int(bool(trim_tail))
    groups = _group_frames(base, group_by, pool_mode, ncols)
    rois = None if do_rebase else rois_by_config(_load_data(pattern)[2])
    bins = dict(group_names=group_names, nrows=nrows, xc=xc, yc=yc, rng=rng,
                counts=_counts_for_groups(groups, group_names, xedges, yedges),
                dt=dt, reach=reach_v,
                roi_stats=_heatmap_roi_stats(list(groups.items()), rois, reach_v))
    store = {}
    for m in HEATMAP_METRICS:
        for s in HEATMAP_SCALES:
            store[f"e{e}_t{t}_{m}_{s}"] = _heatmap_variant(
                bins, log_scale=(s == "log"), metric=m, cmin=cmin, cmax=cmax,
                crange_mode=crange_mode)
    metric = metric if metric in HEATMAP_METRICS else "time"
    scale = "log" if log_scale else "lin"
    base_fig = _assemble_heatmap(bins, store[f"e{e}_t{t}_{metric}_{scale}"], ncols, base)
    return base_fig, store


def build_velocity_histogram(df, vel_threshold=None, velocity_values=None):
    if df is None or len(df) == 0:
        return go.Figure().update_layout(height=190, template="plotly_white")

    vel = velocity_all(df) if velocity_values is None else np.asarray(velocity_values)
    vel = vel[np.isfinite(vel)]
    if len(vel) == 0:
        return go.Figure().update_layout(height=190, template="plotly_white")

    cap = float(np.quantile(vel, 0.99))
    shown = vel[(vel >= 0) & (vel <= cap)] if cap > 0 else vel
    edges = _histogram_edges(shown, 0.0, cap, max_bins=72)
    counts, edges = np.histogram(shown, bins=edges)
    centres = 0.5 * (edges[:-1] + edges[1:])

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=centres.tolist(), y=counts.tolist(), width=(np.diff(edges) * 0.96).tolist(),
        marker_color="#1f77b4", opacity=0.85, name="Velocity",
        customdata=np.column_stack([edges[:-1], edges[1:]]).tolist(),
        hovertemplate="%{customdata[0]:.3g}–%{customdata[1]:.3g}<br>%{y:,} samples<extra></extra>"))
    if vel_threshold and vel_threshold > 0:
        fig.add_vline(x=vel_threshold, line_dash="dash", line_color="red", line_width=2)
        pct = 100 * (vel > vel_threshold).sum() / len(vel) if len(vel) else 0
        fig.add_annotation(text=f"Cut {pct:.1f}%", xref="paper", yref="paper",
                           x=0.97, y=0.9, showarrow=False,
                           font=dict(color="red", size=11))

    fig.update_layout(
        height=190, margin=dict(l=40, r=10, t=28, b=25),
        xaxis_title="Velocity (units/s)", yaxis_title="Count",
        title=dict(text=f"Velocity (0–99th percentile; {(vel > cap).sum():,} above view)",
                   font_size=11, x=0.5),
        template="plotly_white", dragmode="zoom",
    )
    fig.update_xaxes(range=[0, cap] if cap > 0 else None)
    return fig


def build_displacement_histogram(stats_df, min_disp=None):
    if stats_df is None or len(stats_df) == 0:
        return go.Figure().update_layout(height=190, template="plotly_white")

    disp = stats_df["displacement"].to_numpy()
    disp = disp[np.isfinite(disp)]
    cap = float(np.quantile(disp, 0.99)) if disp.size else None
    shown = disp[(disp >= 0) & (disp <= cap)] if cap and cap > 0 else disp
    edges = _histogram_edges(shown, 0.0, cap, max_bins=60)
    counts, edges = np.histogram(shown, bins=edges)
    centres = 0.5 * (edges[:-1] + edges[1:])
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=centres.tolist(), y=counts.tolist(), width=(np.diff(edges) * 0.96).tolist(),
        marker_color="#2ca02c", opacity=0.85, name="Disp",
        customdata=np.column_stack([edges[:-1], edges[1:]]).tolist(),
        hovertemplate="%{customdata[0]:.3g}–%{customdata[1]:.3g}<br>%{y:,} segments<extra></extra>"))
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
        title=dict(text=f"Displacement (0–99th percentile; {(disp > cap).sum() if cap else 0:,} above view)",
                   font_size=11, x=0.5),
        template="plotly_white", dragmode="zoom",
    )
    if cap and cap > 0:
        fig.update_xaxes(range=[0, cap])
    return fig


MINI_HIST_BINS = 36
MINI_HIST_UPPER_PCT = 99.5


def _finite_values(values) -> np.ndarray:
    arr = np.asarray(values if values is not None else [], dtype=float)
    return arr[np.isfinite(arr)]


def _histogram_edges(values, lo=None, hi=None, max_bins=MINI_HIST_BINS) -> np.ndarray:
    """Deterministic, data-aware histogram edges.

    Plotly's automatic bins use the full trace extent even when the visible axis
    is percentile-clipped, which made the small control histograms look empty or
    lumped into a few arbitrary bars. These edges are calculated for the visible
    range, use one-bin-per-value for compact integer data, and otherwise use a
    bounded Freedman–Diaconis width.
    """
    vals = _finite_values(values)
    if lo is None:
        lo = float(np.min(vals)) if vals.size else 0.0
    if hi is None:
        hi = float(np.max(vals)) if vals.size else float(lo) + 1.0
    lo, hi = float(lo), float(hi)
    if not np.isfinite(lo):
        lo = 0.0
    if not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    visible = vals[(vals >= lo) & (vals <= hi)]
    max_bins = max(1, int(max_bins or MINI_HIST_BINS))
    if visible.size:
        rounded = np.rint(visible)
        integer_like = np.all(np.abs(visible - rounded) < 1e-9)
        integer_span = int(math.floor(hi) - math.ceil(lo) + 1)
        if integer_like and 0 < integer_span <= max_bins:
            start = math.floor(lo) - 0.5
            stop = math.ceil(hi) + 0.5
            return np.arange(start, stop + 1.0, 1.0, dtype=float)
        q25, q75 = np.percentile(visible, [25, 75])
        iqr = float(q75 - q25)
        width = 2.0 * iqr / np.cbrt(visible.size) if iqr > 0 else 0.0
        n_bins = int(math.ceil((hi - lo) / width)) if width > 0 else 12
        n_bins = max(8, min(max_bins, n_bins))
    else:
        n_bins = min(max_bins, 12)
    return np.linspace(lo, hi, n_bins + 1, dtype=float)


def _numeric_range(value):
    """Accept either Plotly selectedData or a Dash RangeSlider value."""
    try:
        if isinstance(value, dict):
            rng = value.get("range", {}).get("x")
        else:
            rng = value
        if rng is None or len(rng) < 2:
            return None
        lo, hi = float(rng[0]), float(rng[1])
        if not (np.isfinite(lo) and np.isfinite(hi)):
            return None
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi)
    except Exception:
        return None


def _fmt_slider_tick(v) -> str:
    try:
        v = float(v)
    except Exception:
        return ""
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{v / 1_000:.1f}K"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 10:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{v:.2g}"


def _slider_marks(lo, hi):
    if hi <= lo:
        hi = lo + 1.0
    vals = [lo, (lo + hi) / 2.0, hi]
    out = {}
    for v in vals:
        key = round(float(v), 6)
        out[key] = _fmt_slider_tick(v)
    return out


def _slider_step(lo, hi):
    span = max(float(hi) - float(lo), 1e-9)
    raw = span / 200.0
    if raw <= 0:
        return 1
    power = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 5, 10):
        step = mult * power
        if step >= raw:
            return step
    return raw


def _looks_like_initial_range(rng, lo, hi) -> bool:
    if rng is None:
        return True
    # Layout placeholders start at [0, 1]. Treat that as "unset" when the data
    # bounds clearly are not [0, 1], so a fresh load does not become a hidden cut.
    return (abs(rng[0]) < 1e-12 and abs(rng[1] - 1.0) < 1e-12
            and (abs(float(lo)) > 1e-9 or abs(float(hi) - 1.0) > 1e-9))


def _range_control_value(current, lo, hi):
    rng = _numeric_range(current)
    if _looks_like_initial_range(rng, lo, hi):
        return [float(lo), float(hi)]
    lo_v = max(float(lo), rng[0])
    hi_v = min(float(hi), rng[1])
    if lo_v > hi_v:
        return [float(lo), float(hi)]
    return [lo_v, hi_v]


def _range_bounds(values, default=(0.0, 1.0), floor_zero=True,
                  upper_pct=MINI_HIST_UPPER_PCT):
    vals = _finite_values(values)
    if vals.size == 0:
        return default
    lo = float(np.nanmin(vals))
    if upper_pct is not None and 0 < float(upper_pct) < 100 and vals.size > 1:
        hi = float(np.nanpercentile(vals, float(upper_pct)))
    else:
        hi = float(np.nanmax(vals))
    if floor_zero:
        lo = min(0.0, lo)
    if not hi > lo:
        hi = lo + 1.0
    return lo, hi


def build_mini_histogram(values, selected=None, *, bins=MINI_HIST_BINS,
                         color="#2563eb", x_range=None) -> go.Figure:
    vals = _finite_values(values)
    rng = _numeric_range(selected)
    fig = go.Figure()
    lo, hi = x_range if x_range else _range_bounds(vals)
    plot_vals = vals
    if vals.size and lo is not None and hi is not None:
        plot_vals = vals[(vals >= float(lo)) & (vals <= float(hi))]
    if plot_vals.size:
        edges = _histogram_edges(plot_vals, lo, hi, max_bins=bins)
        counts, edges = np.histogram(plot_vals, bins=edges)
        centres = 0.5 * (edges[:-1] + edges[1:])
        fig.add_trace(go.Bar(
            x=centres.tolist(), y=counts.tolist(),
            width=(np.diff(edges) * 0.96).tolist(),
            customdata=np.column_stack([edges[:-1], edges[1:]]).tolist(),
            marker_color=color, opacity=0.72,
            hovertemplate=("%{customdata[0]:.3g}–%{customdata[1]:.3g}"
                           "<br>%{y:,} items<extra></extra>"),
            showlegend=False))
        if rng:
            fig.add_vrect(x0=rng[0], x1=rng[1],
                          fillcolor="rgba(37,99,235,0.14)",
                          line_width=0, layer="below")
    fig.update_layout(
        height=58, template="plotly_white", margin=dict(l=4, r=4, t=2, b=14),
        bargap=0.04, dragmode=False, showlegend=False,
        xaxis=dict(range=[lo, hi], fixedrange=True, showgrid=False,
                   tickmode="array", tickvals=list(_slider_marks(lo, hi).keys()),
                   tickfont=dict(size=8)),
        yaxis=dict(fixedrange=True, visible=False),
    )
    return fig


def build_percentile_mini_histogram(values, selected=None, *, bins=MINI_HIST_BINS,
                                    color="#0f766e") -> go.Figure:
    """Preserve the raw histogram silhouette on a 0–100 percentile axis.

    Bin counts are computed in value space, then their x coordinates are
    linearly mapped to 0–100. This keeps the familiar histogram shape while the
    range control and labels correctly communicate percentile inputs.
    """
    vals = _finite_values(values)
    rng = _numeric_range(selected) or (0.0, 100.0)
    fig = go.Figure()
    if vals.size:
        raw_lo, raw_hi = _range_bounds(vals, floor_zero=True,
                                       upper_pct=MINI_HIST_UPPER_PCT)
        shown = vals[(vals >= raw_lo) & (vals <= raw_hi)]
        edges = _histogram_edges(shown, raw_lo, raw_hi, max_bins=bins)
        counts, edges = np.histogram(shown, bins=edges)
        span = max(float(raw_hi - raw_lo), 1e-12)
        pedges = 100.0 * (edges - raw_lo) / span
        centres = 0.5 * (pedges[:-1] + pedges[1:])
        fig.add_trace(go.Bar(
            x=centres.tolist(), y=counts.tolist(),
            width=(np.diff(pedges) * 0.96).tolist(),
            customdata=np.column_stack([edges[:-1], edges[1:]]).tolist(),
            marker_color=color, opacity=0.72, showlegend=False,
            hovertemplate=("value %{customdata[0]:.3g}–%{customdata[1]:.3g}"
                           "<br>%{y:,} bins/items<extra></extra>")))
        fig.add_vrect(x0=rng[0], x1=rng[1],
                      fillcolor="rgba(37,99,235,0.14)",
                      line_width=0, layer="below")
    fig.update_layout(
        height=58, template="plotly_white", margin=dict(l=4, r=4, t=2, b=14),
        bargap=0.04, dragmode=False, showlegend=False,
        xaxis=dict(range=[0, 100], fixedrange=True, showgrid=False,
                   tickmode="array", tickvals=[0, 50, 100],
                   ticktext=["0", "50", "100"], tickfont=dict(size=8)),
        yaxis=dict(fixedrange=True, visible=False),
    )
    return fig


def _range_control_payload(values, current, *, color="#2563eb", floor_zero=True,
                           upper_pct=MINI_HIST_UPPER_PCT):
    lo, hi = _range_bounds(values, floor_zero=floor_zero, upper_pct=upper_pct)
    val = _range_control_value(current, lo, hi)
    return (
        float(lo),
        float(hi),
        _slider_step(lo, hi),
        _slider_marks(lo, hi),
        val,
        build_mini_histogram(values, val, color=color, x_range=(lo, hi)),
    )


def _sample_for_store(values, max_items=5000) -> list[float]:
    vals = np.sort(_finite_values(values))
    if vals.size == 0:
        return []
    if vals.size <= max_items:
        return vals.tolist()
    idx = np.linspace(0, vals.size - 1, max_items).astype(int)
    return vals[idx].tolist()


def _percentile_rank(values, value) -> float:
    vals = np.sort(_finite_values(values))
    if vals.size == 0:
        return 0.0
    pos = np.searchsorted(vals, float(value), side="right")
    return float(100.0 * pos / vals.size)


def _heatmap_metric_values(df, bin_size, bound_pct, metric) -> np.ndarray:
    if df is None or len(df) == 0:
        return np.array([], dtype=float)
    try:
        xedges, yedges, _ = _heatmap_edges(df, bin_size, bound_pct)
        H, _, _ = np.histogram2d(df["GameObjectPosX"].to_numpy(),
                                 df["GameObjectPosZ"].to_numpy(),
                                 bins=[xedges, yedges])
    except Exception:
        return np.array([], dtype=float)
    vals = H.T.astype(float).ravel()
    vals = vals[vals > 0]
    metric = metric if metric in METRIC_UNITS else "time"
    if metric == "time":
        vals = vals * _median_dt(df)
    elif metric == "percent":
        total = float(vals.sum())
        vals = (100.0 * vals / total) if total > 0 else vals
    return vals[np.isfinite(vals)]


def _active_stat_range(rng, stats_df, stat_col):
    """Return None when a slider spans the full displayed stat range."""
    rng = _numeric_range(rng)
    if rng is None or stats_df is None or len(stats_df) == 0 or stat_col not in stats_df:
        return rng
    vals = _finite_values(stats_df[stat_col].to_numpy())
    if vals.size == 0:
        return None
    lo, hi = _range_bounds(vals, floor_zero=True, upper_pct=MINI_HIST_UPPER_PCT)
    span = max(float(hi) - float(lo), 1.0)
    eps = span * 1e-9
    if rng[0] <= float(lo) + eps and rng[1] >= float(hi) - eps:
        return None
    return rng


def build_raw_trace_figure(df, columns, max_points=None):
    if df is None or len(df) == 0 or not columns:
        return go.Figure().update_layout(height=180, template="plotly_white")

    n = len(columns)
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True,
                        subplot_titles=columns, vertical_spacing=0.15)
    # SVG (go.Scatter), not WebGL: this plot lives in a panel that starts hidden,
    # and a WebGL canvas created while hidden won't paint. Use a smaller budget
    # so SVG stays light.
    budget = int(max_points) if (max_points and max_points > 0) else BUDGET_RAW
    sub = _decimate_frame(df, budget)
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


def build_polar_r_histogram(ray: pd.DataFrame | None, r_range=None) -> go.Figure:
    lo, hi = _polar_r_range(r_range)
    values = np.array([], dtype=float)
    if ray is not None and len(ray):
        values = _finite_values(ray["R"].to_numpy(dtype=float))
    fig = build_mini_histogram(
        values, [lo, hi], bins=24, color="#2563eb", x_range=(0, 1))
    if fig.data:
        fig.data[0].hovertemplate = ("R %{customdata[0]:.2f}–%{customdata[1]:.2f}"
                                     "<br>%{y:,} trials<extra></extra>")
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
      2. Paired swarm — per-animal mean residence time inside each ROI.
      3. Split violin of time-to-reach the target (left half / right half), area
         proportional to the number of trials that reached (scalemode='count').
      4. Split violin of instantaneous heading minus target bearing
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
    lc, rc = _ROI_SIDE_COLOR["left"], _ROI_SIDE_COLOR["right"]
    rng = np.random.default_rng(0)

    def _paired_arrays(src, left_col, right_col):
        base = src["label"].map(xpos).to_numpy().astype(float)
        jit_l = (rng.random(len(src)) - 0.5) * 0.18
        jit_r = (rng.random(len(src)) - 0.5) * 0.18
        lx, rx = base - 0.2 + jit_l, base + 0.2 + jit_r
        ly, ry = src[left_col].to_numpy(), src[right_col].to_numpy()
        px = np.empty(len(src) * 3); px[0::3], px[1::3], px[2::3] = lx, rx, np.nan
        py = np.empty(len(src) * 3); py[0::3], py[1::3], py[2::3] = ly, ry, np.nan
        return lx, rx, ly, ry, px, py

    def _median_segments(src, left_col, right_col):
        med = src.groupby("label").agg(ml=(left_col, "median"),
                                       mr=(right_col, "median"))
        mlx, mly, mrx, mry = [], [], [], []
        for lab, i in xpos.items():
            if lab in med.index:
                mlx += [i - 0.36, i - 0.04, None]; mly += [med.loc[lab, "ml"]] * 2 + [None]
                mrx += [i + 0.04, i + 0.36, None]; mry += [med.loc[lab, "mr"]] * 2 + [None]
        return mlx, mly, mrx, mry

    def _add_quantile_lines(src, value_col, row, color, side):
        x0, x1 = (-0.36, -0.04) if side == "left" else (0.04, 0.36)
        side_src = src[src["side"] == side]
        for lab, vals in side_src.groupby("label", sort=False)[value_col]:
            vals = vals.to_numpy()
            vals = vals[np.isfinite(vals)]
            if not len(vals) or lab not in xpos:
                continue
            q1, med, q3 = np.percentile(vals, [25, 50, 75])
            for y, width, alpha in ((q1, 1.4, 0.45), (med, 2.8, 0.95), (q3, 1.4, 0.45)):
                fig.add_trace(go.Scatter(
                    x=[xpos[lab] + x0, xpos[lab] + x1], y=[y, y],
                    mode="lines", showlegend=False, hoverinfo="skip",
                    line=dict(color=_rgba(color, alpha), width=width)),
                    row=row, col=1)

    fig = make_subplots(rows=4, cols=1, vertical_spacing=0.075, subplot_titles=(
        f"Fraction of trials reaching each ROI — per animal "
        f"(reach {reach:g} u · {n_animals} animals; bars = median)",
        "Residence time inside ROI — per animal mean seconds/trial (bars = median)",
        "Time to reach target (split violin; area ∝ trials reached; lines = median/IQR)",
        "Instantaneous heading error to target bearing (split violin; lines = median/IQR)"))

    lx, rx, ly, ry, px, py = _paired_arrays(grp, "frac_left", "frac_right")
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
    mlx, mly, mrx, mry = _median_segments(grp, "frac_left", "frac_right")
    fig.add_trace(go.Scatter(x=mlx, y=mly, mode="lines", showlegend=False,
        line=dict(color=lc, width=3), hoverinfo="skip"), row=1, col=1)
    fig.add_trace(go.Scatter(x=mrx, y=mry, mode="lines", showlegend=False,
        line=dict(color=rc, width=3), hoverinfo="skip"), row=1, col=1)

    # --- panel 2: residence-time paired swarm ---
    res = roi_residence_table(df, rois_by_cfg, reach)
    if len(res):
        rgrp = res.groupby(["ConfigFile", "animal"], sort=False, observed=True).agg(
            residence_left=("residence_left", "mean"),
            residence_right=("residence_right", "mean"),
            trials=("_seg_id", "size")).reset_index()
        rgrp["label"] = rgrp["ConfigFile"].map(humanise_config)
        rgrp = rgrp[rgrp["label"].isin(xpos)]
        if len(rgrp):
            rlx, rrx, rly, rry, rpx, rpy = _paired_arrays(
                rgrp, "residence_left", "residence_right")
            fig.add_trace(go.Scatter(x=rpx.tolist(), y=rpy.tolist(), mode="lines",
                line=dict(color="rgba(120,120,120,0.28)", width=1),
                hoverinfo="skip", showlegend=False), row=2, col=1)
            rleft_cd = rgrp[["animal", "trials"]].to_numpy()
            rright_cd = rgrp[["animal", "trials"]].to_numpy()
            fig.add_trace(go.Scatter(x=rlx.tolist(), y=rly.tolist(), mode="markers",
                name="Left residence", legendgroup="left", showlegend=False,
                marker=dict(color=lc, size=6, opacity=0.72,
                line=dict(width=0.5, color="#333")), customdata=rleft_cd,
                hovertemplate=("Left %{y:.2f}s/trial"
                               "<br>%{customdata[1]:.0f} trials<br>%{customdata[0]}"
                               "<extra></extra>")), row=2, col=1)
            fig.add_trace(go.Scatter(x=rrx.tolist(), y=rry.tolist(), mode="markers",
                name="Right residence", legendgroup="right", showlegend=False,
                marker=dict(color=rc, size=6, opacity=0.72,
                line=dict(width=0.5, color="#333")), customdata=rright_cd,
                hovertemplate=("Right %{y:.2f}s/trial"
                               "<br>%{customdata[1]:.0f} trials<br>%{customdata[0]}"
                               "<extra></extra>")), row=2, col=1)
            rmlx, rmly, rmrx, rmry = _median_segments(
                rgrp, "residence_left", "residence_right")
            fig.add_trace(go.Scatter(x=rmlx, y=rmly, mode="lines", showlegend=False,
                line=dict(color=lc, width=3), hoverinfo="skip"), row=2, col=1)
            fig.add_trace(go.Scatter(x=rmrx, y=rmry, mode="lines", showlegend=False,
                line=dict(color=rc, width=3), hoverinfo="skip"), row=2, col=1)

    # --- panel 3: time-to-target split violin ---
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
                meanline_visible=False, box_visible=False, showlegend=False, spanmode="hard",
                hovertemplate=side + " %{y:.1f}s<extra></extra>"), row=3, col=1)
            _add_quantile_lines(ttt, "t", 3, color, side)

    # --- panel 4: instantaneous heading error split violin ---
    ang = heading_target_angle_table(df, rois_by_cfg)
    if len(ang):
        ang["label"] = ang["ConfigFile"].map(humanise_config)
        ang_quantiles = ang
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
                meanline_visible=False, box_visible=False, showlegend=False,
                span=[-180, 180],
                hovertemplate=side + " %{y:.0f}° heading - target bearing<extra></extra>"),
                row=4, col=1)
            _add_quantile_lines(ang_quantiles, "angle_deg", 4, color, side)

    fig.update_layout(template="plotly_white", height=1220, violinmode="overlay",
        legend=dict(orientation="h", y=1.05, yanchor="bottom", x=1, xanchor="right"),
        margin=dict(l=60, r=20, t=50, b=80), dragmode="pan")
    fig.update_yaxes(title_text="fraction reaching", range=[-0.03, 1.03], row=1, col=1)
    fig.update_yaxes(title_text="residence (s/trial)", rangemode="tozero", row=2, col=1)
    fig.update_yaxes(title_text="time to reach (s)", rangemode="tozero", row=3, col=1)
    fig.update_yaxes(title_text="heading error (deg)", range=[-180, 180],
                     zeroline=True, zerolinewidth=1.5, zerolinecolor="#555",
                     row=4, col=1)
    for row in range(1, 5):
        fig.update_xaxes(tickmode="array", tickvals=list(range(len(labels))),
                         ticktext=labels, range=[-0.6, len(labels) - 0.4],
                         title_text="config" if row == 4 else None,
                         row=row, col=1)
    fig.update_xaxes(matches="x")
    return fig


def _ray_cache_key(df, moving_only, walk_thresh, color_by, angle_source):
    return (_frame_cache_token(df), bool(moving_only),
            round(float(walk_thresh or 0), 6), color_by or "none",
            angle_source or "orientation")


def _cache_ray(key, val):
    _POLAR_RAY_CACHE[key] = val
    _POLAR_RAY_CACHE_ORDER.append(key)
    while len(_POLAR_RAY_CACHE_ORDER) > _POLAR_RAY_CACHE_MAX:
        _POLAR_RAY_CACHE.pop(_POLAR_RAY_CACHE_ORDER.pop(0), None)
    return val


def rayleigh_by_segment(df, moving_only=False, walk_thresh=None,
                        color_by="velocity", use_cache=True,
                        angle_source="orientation") -> pd.DataFrame:
    """Per-trial Rayleigh vector of body orientation or movement heading.

    Unity yaw and movement heading share one convention: ``0°`` is forward
    (``+Z``), positive angles turn toward ``+X`` (clockwise in the polar plot).
    Body orientation is the default when ``GameObjectRotY`` exists because that
    is the circular variable used by the original polar analysis. Movement
    heading remains an explicit fallback/alternative.

    Returns _seg_id, ConfigFile, animal, R (0..1 concentration), theta_deg (mean
    direction), metadata for hover, and an optional per-trial colour value.
    Fully vectorised — no per-segment Python."""
    cols = ["_seg_id", "ConfigFile", "animal", "VR", "FlyID", "CurrentTrial",
            "CurrentStep", "SourceFile", "StartTime", "R", "theta_deg", "cval",
            "n_points", "valid_points", "valid_frac"]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=cols)
    source = str(angle_source or "orientation").lower()
    if source not in ("orientation", "movement"):
        source = "orientation"
    if source == "orientation" and "GameObjectRotY" not in df:
        source = "movement"
    key = _ray_cache_key(df, moving_only, walk_thresh, color_by, source)
    if use_cache and key in _POLAR_RAY_CACHE:
        return _POLAR_RAY_CACHE[key]
    seg = df["_seg_id"].to_numpy()
    n = len(df)
    if source == "orientation":
        angles = pd.to_numeric(df["GameObjectRotY"], errors="coerce").to_numpy(dtype=float)
        # GameObjectRotY is a Unity yaw export and is defined in degrees.  Do
        # not guess units from the observed range: a narrow ±5° experiment
        # would otherwise be misclassified as radians.
        angles = np.radians(angles)
        ux = np.sin(angles)
        uz = np.cos(angles)
        ux[~np.isfinite(angles)] = np.nan
        uz[~np.isfinite(angles)] = np.nan
    else:
        x = df["GameObjectPosX"].to_numpy(); z = df["GameObjectPosZ"].to_numpy()
        dx = np.empty(n); dx[0] = np.nan; dx[1:] = np.diff(x)
        dz = np.empty(n); dz[0] = np.nan; dz[1:] = np.diff(z)
        seg_start = np.empty(n, bool); seg_start[0] = True
        seg_start[1:] = seg[1:] != seg[:-1]
        dx[seg_start] = np.nan; dz[seg_start] = np.nan
        mag = np.hypot(dx, dz)
        with np.errstate(invalid="ignore", divide="ignore"):
            ux = dx / mag; uz = dz / mag
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

    valid_heading = np.isfinite(ux) & np.isfinite(uz)
    agg = (pd.DataFrame({"_seg_id": seg, "ux": ux, "uz": uz, "cval": cvals,
                         "valid": valid_heading.astype(np.int32)})
           .groupby("_seg_id", sort=False)
           .agg(ux=("ux", "mean"), uz=("uz", "mean"), cval=("cval", "mean"),
                valid_points=("valid", "sum"), n_points=("valid", "size")))
    R = np.hypot(agg["ux"].to_numpy(), agg["uz"].to_numpy())
    theta = np.degrees(np.arctan2(agg["ux"].to_numpy(), agg["uz"].to_numpy()))
    meta = df.groupby("_seg_id", sort=False).agg(
        ConfigFile=("ConfigFile", "first"), VR=("VR", "first"),
        FlyID=("FlyID", "first"), CurrentTrial=("CurrentTrial", "first"),
        CurrentStep=("CurrentStep", "first"), SourceFile=("SourceFile", "first"),
        StartTime=("Current Time", "first"))
    n_points = agg["n_points"].to_numpy(dtype=float)
    valid_points = agg["valid_points"].to_numpy(dtype=float)
    valid_frac = np.divide(valid_points, n_points, out=np.zeros_like(valid_points),
                           where=n_points > 0)
    out = pd.DataFrame({"_seg_id": agg.index, "R": R, "theta_deg": theta,
                        "cval": agg["cval"].to_numpy(),
                        "n_points": n_points.astype(np.int64),
                        "valid_points": valid_points.astype(np.int64),
                        "valid_frac": valid_frac}).merge(
        meta.reset_index(), on="_seg_id")
    out["animal"] = out["FlyID"].astype(str) + "@" + out["VR"].astype(str)
    return _cache_ray(key, out[cols]) if use_cache else out[cols]


def precache_polar_rays(df, walk_thresh, color_by, angle_source="orientation"):
    if df is None or len(df) == 0:
        return
    rayleigh_by_segment(df, False, walk_thresh, color_by,
                        angle_source=angle_source)
    rayleigh_by_segment(df, True, walk_thresh, color_by,
                        angle_source=angle_source)


def _wrap_subplot_title(text, width=28, max_lines=2):
    words = str(text).split()
    lines, cur = [], ""
    for word in words:
        nxt = word if not cur else f"{cur} {word}"
        if len(nxt) <= width:
            cur = nxt
            continue
        if cur:
            lines.append(cur)
        cur = word
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    out = "<br>".join(lines) if lines else str(text)
    if len(lines) == max_lines and " ".join(words) != " ".join(lines).replace("<br>", " "):
        out += "..."
    return out


_POLAR_HOVER = (
    "<b>%{customdata[2]} @ %{customdata[3]}</b><br>"
    "trial=%{customdata[0]} step=%{customdata[1]}<br>"
    "config=%{customdata[4]}<br>"
    "file=%{customdata[5]}<br>"
    "segment=%{customdata[6]}<br>"
    "R=%{customdata[7]:.2f} theta=%{customdata[8]:.0f}°<br>"
    "valid heading=%{customdata[10]:.0f}/%{customdata[11]:.0f} pts (%{customdata[12]:.0%})<br>"
    "%{customdata[9]}<extra></extra>"
)


def _frac_value(v, default=0.0) -> float:
    if v is None or v == "":
        return float(default)
    try:
        return max(0.0, min(1.0, float(v)))
    except Exception:
        return float(default)


def _polar_r_range(value) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            lo, hi = float(value[0]), float(value[1])
        except Exception:
            return 0.0, 1.0
        lo, hi = max(0.0, min(1.0, lo)), max(0.0, min(1.0, hi))
        return (lo, hi) if lo <= hi else (hi, lo)
    return 0.0, 1.0


def _filter_polar_ray_table(ray: pd.DataFrame, r_range=None,
                            min_point_frac=0.0,
                            min_animal_trial_frac=0.0) -> tuple[pd.DataFrame, dict]:
    """Apply trial- and animal-level polar quality gates to a ray table."""

    summary = {
        "start_trials": 0, "after_trial": 0, "after_animal": 0,
        "start_animals": 0, "after_animals": 0,
    }
    if ray is None or len(ray) == 0:
        return ray, summary
    summary["start_trials"] = int(len(ray))
    summary["start_animals"] = int(ray["animal"].nunique()) if "animal" in ray else 0
    lo, hi = _polar_r_range(r_range)
    pfrac = _frac_value(min_point_frac)
    tfrac = _frac_value(min_animal_trial_frac)
    keep_trial = (
        ray["R"].between(lo, hi, inclusive="both").to_numpy()
        & (ray["valid_frac"].to_numpy(dtype=float) >= pfrac)
    )
    filtered = ray.loc[keep_trial]
    summary["after_trial"] = int(len(filtered))
    if len(filtered) and tfrac > 0:
        total = ray.groupby("animal", sort=False, observed=True)["_seg_id"].size()
        kept = filtered.groupby("animal", sort=False, observed=True)["_seg_id"].size()
        good_animals = (kept / total.reindex(kept.index)).loc[lambda s: s >= tfrac].index
        filtered = filtered[filtered["animal"].isin(good_animals)]
    summary["after_animal"] = int(len(filtered))
    summary["after_animals"] = int(filtered["animal"].nunique()) if len(filtered) else 0
    return filtered, summary


def _polar_animal_good_fractions(ray: pd.DataFrame, r_range=None,
                                 min_point_frac=0.0) -> np.ndarray:
    if ray is None or len(ray) == 0 or "animal" not in ray:
        return np.array([], dtype=float)
    lo, hi = _polar_r_range(r_range)
    pfrac = _frac_value(min_point_frac)
    good = ray["R"].between(lo, hi, inclusive="both").to_numpy()
    good &= ray["valid_frac"].to_numpy(dtype=float) >= pfrac
    work = ray.assign(_good=good.astype(np.int8))
    frac = work.groupby("animal", sort=False, observed=True)["_good"].mean()
    return frac.to_numpy(dtype=float)


def build_polar_quality_histograms(ray: pd.DataFrame | None, r_range=None,
                                   min_point_frac=0.0,
                                   min_animal_trial_frac=0.0):
    """Build all three polar quality controls from the same cached ray table.

    These deliberately show the *pre-gate* distributions.  The shaded slider
    selection then explains which trials/animals survive each gate instead of
    making the histogram disappear as soon as a restrictive gate is selected.
    """
    pfrac = _frac_value(min_point_frac)
    afrac = _frac_value(min_animal_trial_frac)
    r_hist = build_polar_r_histogram(ray, r_range)
    point_values = (ray["valid_frac"].to_numpy(dtype=float)
                    if ray is not None and len(ray) else np.array([], dtype=float))
    animal_values = _polar_animal_good_fractions(ray, r_range, pfrac)
    point_hist = build_mini_histogram(
        point_values, [pfrac, 1.0], bins=24, color="#7c3aed", x_range=(0, 1))
    animal_hist = build_mini_histogram(
        animal_values, [afrac, 1.0], bins=24, color="#0f766e", x_range=(0, 1))
    if point_hist.data:
        point_hist.data[0].hovertemplate = (
            "valid-point fraction %{customdata[0]:.2f}–%{customdata[1]:.2f}"
            "<br>%{y:,} trials<extra></extra>")
    if animal_hist.data:
        animal_hist.data[0].hovertemplate = (
            "good-trial fraction %{customdata[0]:.2f}–%{customdata[1]:.2f}"
            "<br>%{y:,} animals<extra></extra>")
    for fig, label in (
        (r_hist, "No valid Rayleigh R values"),
        (point_hist, "No trial fractions available"),
        (animal_hist, "No animal fractions available"),
    ):
        if not fig.data:
            fig.add_annotation(text=label, x=0.5, y=0.5, xref="paper",
                               yref="paper", showarrow=False,
                               font=dict(size=9, color="#64748b"))
    return r_hist, point_hist, animal_hist


def _polar_custom_base(sub: pd.DataFrame, roi_outcomes=None) -> np.ndarray:
    outcomes = (sub["_seg_id"].astype(str).map({str(k): str(v) for k, v in (roi_outcomes or {}).items()})
                .fillna("").to_numpy())
    return np.column_stack([
        _numeric_labels(sub["CurrentTrial"].to_numpy()),
        _numeric_labels(sub["CurrentStep"].to_numpy()),
        sub["FlyID"].astype(str).to_numpy(),
        sub["VR"].astype(str).to_numpy(),
        sub["ConfigFile"].astype(str).to_numpy(),
        sub["SourceFile"].astype(str).to_numpy(),
        sub["_seg_id"].astype(str).to_numpy(),
        sub["R"].to_numpy(),
        sub["theta_deg"].to_numpy(),
        outcomes,
        sub["valid_points"].to_numpy(),
        sub["n_points"].to_numpy(),
        sub["valid_frac"].to_numpy(),
    ])


def _polar_segment_arrays(sub: pd.DataFrame, roi_outcomes=None):
    r = sub["R"].to_numpy()
    th = sub["theta_deg"].to_numpy()
    rr = np.empty(len(sub) * 3)
    tt = np.empty(len(sub) * 3)
    rr[0::3], rr[1::3], rr[2::3] = 0.0, r, np.nan
    tt[0::3], tt[1::3], tt[2::3] = th, th, np.nan
    base = _polar_custom_base(sub, roi_outcomes)
    cd = np.empty((len(sub) * 3, base.shape[1]), dtype=object)
    cd[0::3] = base
    cd[1::3] = base
    cd[2::3] = ""
    return rr, tt, cd


def _thin_ray_table(ray: pd.DataFrame, max_points=None) -> pd.DataFrame:
    if ray is None or len(ray) == 0 or not max_points:
        return ray
    max_rays = max(1, int(max_points) // 3)
    if len(ray) <= max_rays:
        return ray
    idx = np.unique(np.linspace(0, len(ray) - 1, max_rays).astype(int))
    return ray.iloc[idx]


def _polar_seq_values(ray: pd.DataFrame, color_by: str):
    color_by = color_by or "individual"
    if color_by == "velocity":
        vals = ray["cval"].to_numpy(dtype=float)
        finite = vals[np.isfinite(vals)]
        cmax = float(np.percentile(finite, 99)) if finite.size else 1.0
        return vals, 0.0, cmax, "Mean speed"
    if color_by == "tortuosity":
        vals = ray["cval"].to_numpy(dtype=float)
        finite = vals[np.isfinite(vals)]
        cmax = float(np.percentile(finite, 99)) if finite.size else 1.0
        return vals, 1.0, max(1.1, cmax), "Mean tortuosity"
    if color_by == "trial":
        vals = pd.to_numeric(ray["CurrentTrial"], errors="coerce").to_numpy(dtype=float)
        finite = vals[np.isfinite(vals)]
        cmin = float(np.nanmin(finite)) if finite.size else 0.0
        cmax = float(np.nanmax(finite)) if finite.size else 1.0
        return vals, cmin, cmax if cmax > cmin else cmin + 1.0, "Trial"
    if color_by == "local_time":
        t = pd.to_datetime(ray["StartTime"], errors="coerce").astype("int64").to_numpy(dtype=float)
        finite = t[np.isfinite(t)]
        if finite.size:
            lo, hi = float(np.min(finite)), float(np.max(finite))
            span = hi - lo or 1.0
            vals = (t - lo) / span
        else:
            vals = np.zeros(len(ray), dtype=float)
        return vals, 0.0, 1.0, "Start time"
    return None, None, None, ""


def _population_polar_vector(ray: pd.DataFrame) -> tuple[float, float, int]:
    """Exact pooled mean of all valid circular samples represented by rays.

    A trial ray is the mean of its unit sample vectors. Multiplying that vector
    by ``valid_points`` before pooling reconstructs the same population vector
    as concatenating every valid sample, without expanding the data again.
    """
    if ray is None or len(ray) == 0:
        return 0.0, 0.0, 0
    r = ray["R"].to_numpy(dtype=float)
    th = np.radians(ray["theta_deg"].to_numpy(dtype=float))
    w = ray["valid_points"].to_numpy(dtype=float)
    good = np.isfinite(r) & np.isfinite(th) & np.isfinite(w) & (w > 0)
    if not np.any(good):
        return 0.0, 0.0, 0
    r, th, w = r[good], th[good], w[good]
    total = float(w.sum())
    vx = float(np.sum(w * r * np.sin(th)) / total)
    vz = float(np.sum(w * r * np.cos(th)) / total)
    return math.hypot(vx, vz), math.degrees(math.atan2(vx, vz)), int(total)


def build_polar_figure(df, group_by="config", pool_mode="separate", ncols=2,
                       color_by="individual", moving_only=False, walk_thresh=None,
                       max_points=None, rois=None, reach_radius=3.0, show_rois=False,
                       roi_outcomes=None, r_range=None, min_point_frac=0.0,
                       min_animal_trial_frac=0.0, return_summary=False,
                       angle_source="orientation"):
    """One Rayleigh vector per trial from body yaw or movement heading.

    Radius ``R`` is circular concentration (0 = scattered, 1 = aligned). Unity's
    left-handed convention is used throughout: 0° forward/+Z and clockwise
    positive. The bold population ray is the exact sample-weighted circular mean
    computed before display thinning. ROI directions are reference spokes.
    """
    if df is None or len(df) == 0:
        return _msg_figure("No trajectories match the active filters.")
    color_by = color_by or "individual"
    angle_source = str(angle_source or "orientation").lower()
    if angle_source != "orientation" or "GameObjectRotY" not in df:
        angle_source = "movement"
    ray_metric = color_by if color_by in ("velocity", "tortuosity") else "none"
    ray = rayleigh_by_segment(
        df, moving_only, walk_thresh, ray_metric,
        angle_source=angle_source)
    ray = ray.dropna(subset=["R", "theta_deg"])
    if len(ray) == 0:
        fig = _msg_figure("No headings meet the current moving-sample threshold.")
        return (fig, {"start_trials": 0, "after_trial": 0, "after_animal": 0,
                      "start_animals": 0, "after_animals": 0}) if return_summary else fig
    ray, quality = _filter_polar_ray_table(ray, r_range, min_point_frac,
                                           min_animal_trial_frac)
    if len(ray) == 0:
        fig = _msg_figure("No trials pass the polar Rayleigh/quality filters.")
        return (fig, quality) if return_summary else fig

    groups = _group_frames(df, group_by, pool_mode, ncols)
    names = list(groups.keys())
    n = len(names)
    nrows = max(1, (n + ncols - 1) // ncols)
    # seg -> subplot-group map (vectorised via concat of per-group index labels)
    seg_group = pd.concat([pd.Series(gname, index=g["_seg_id"].unique())
                           for gname, g in groups.items()]) if names else pd.Series(dtype=object)
    ray = ray.assign(group=ray["_seg_id"].map(seg_group))
    population_ray = ray
    ray = _thin_ray_table(ray, max_points=max_points)
    seq_vals, seq_cmin, seq_cmax, seq_title = _polar_seq_values(ray, color_by)
    if seq_vals is not None:
        ray = ray.assign(_seq_color=seq_vals)
    ind_color, vr_color, _tmin, _tmax = _color_maps(df)

    specs = [[{"type": "polar"} for _ in range(ncols)] for _ in range(nrows)]
    vspace = min(0.12, 0.7 / max(nrows, 1))
    fig = make_subplots(rows=nrows, cols=ncols, specs=specs,
                        subplot_titles=[_wrap_subplot_title(humanise_config(t))
                                        for t in names],
                        horizontal_spacing=0.06, vertical_spacing=vspace)

    legend_seen = set()
    seq_scale_shown = False
    for idx, gname in enumerate(names):
        row, col = idx // ncols + 1, idx % ncols + 1
        sub = ray[ray["group"] == gname]

        # ROI target directions (dotted spokes), under the lines.
        if show_rois and rois:
            for roi in _rois_for_group(gname, groups.get(gname), rois):
                th_ = math.degrees(math.atan2(roi["x"], roi["z"]))
                fig.add_trace(go.Scatterpolar(
                    r=[0, 1], theta=[th_, th_], mode="lines", showlegend=False,
                    hoverinfo="skip", line=dict(width=1.4, dash="dot",
                    color=_ROI_SIDE_COLOR.get(roi["side"], "#999"))),
                    row=row, col=col)

        if len(sub) == 0:
            continue

        if seq_vals is not None:
            rr, tt, _cd = _polar_segment_arrays(sub, roi_outcomes)
            fig.add_trace(go.Scatterpolar(
                r=rr.tolist(), theta=tt.tolist(), mode="lines", showlegend=False,
                hoverinfo="skip", line=dict(color="rgba(90,96,110,0.32)", width=1)),
                row=row, col=col)
            base_cd = _polar_custom_base(sub, roi_outcomes)
            fig.add_trace(go.Scatterpolar(
                r=sub["R"].to_numpy().tolist(),
                theta=sub["theta_deg"].to_numpy().tolist(),
                mode="markers", showlegend=False, customdata=base_cd.tolist(),
                hovertemplate=_POLAR_HOVER,
                marker=dict(size=6, opacity=0.82,
                            color=sub["_seq_color"].to_numpy().tolist(),
                            colorscale=SEQ_COLORSCALE, cmin=seq_cmin, cmax=seq_cmax,
                            showscale=not seq_scale_shown,
                            colorbar=dict(title=seq_title, thickness=12, len=0.5,
                                          x=1.0, xanchor="left"))),
                row=row, col=col)
            seq_scale_shown = True
        else:
            if color_by == "vr":
                keys = sub["VR"].astype(str).to_numpy()
            elif color_by == "roi":
                outcome_map = {str(k): str(v) for k, v in (roi_outcomes or {}).items()}
                keys = sub["_seg_id"].astype(str).map(outcome_map).fillna("No ROI").to_numpy()
            elif color_by == "none":
                keys = np.full(len(sub), "Polar", dtype=object)
            else:
                keys = sub["animal"].astype(str).to_numpy()
            for key in pd.unique(keys):
                m = keys == key
                ss = sub.loc[m]
                rr, tt, cd = _polar_segment_arrays(ss, roi_outcomes)
                if color_by == "vr":
                    colr = vr_color.get(str(key), COLORS[0])
                    label = str(key)
                elif color_by == "roi":
                    label = str(key)
                    colr = _ROI_OUTCOME_COLOR.get(label, _ROI_OUTCOME_COLOR["No ROI"])
                elif color_by == "none":
                    label, colr = "Polar", "rgba(46,160,80,0.45)"
                else:
                    label = str(key)
                    if "@" in label:
                        fidv, vrv = label.split("@", 1)
                        colr = ind_color.get((vrv, fidv), COLORS[0])
                    else:
                        colr = COLORS[0]
                fig.add_trace(go.Scatterpolar(
                    r=rr.tolist(), theta=tt.tolist(), mode="lines",
                    name=label, legendgroup=label,
                    showlegend=label not in legend_seen,
                    customdata=cd.tolist(), hovertemplate=_POLAR_HOVER,
                    line=dict(color=colr, width=1.15)),
                    row=row, col=col)
                legend_seen.add(label)

        # Pooled population vector is calculated from the complete, unthinned
        # ray table and weighted by each trial's valid sample count. This exactly
        # reconstructs the circular mean across all underlying samples.
        pop_sub = population_ray[population_ray["group"] == gname]
        Rpop, thpop, n_heading = _population_polar_vector(pop_sub)
        source_label = "body orientation" if angle_source == "orientation" else "movement heading"
        fig.add_trace(go.Scatterpolar(
            r=[0, Rpop], theta=[thpop, thpop], mode="lines+markers", showlegend=False,
            hovertemplate=(f"pooled {source_label}<br>R={Rpop:.3f} θ={thpop:.1f}°"
                           f"<br>valid samples={n_heading:,}<extra></extra>"),
            line=dict(color="#0b6b2e", width=3),
            marker=dict(size=[0, 7], color="#0b6b2e")), row=row, col=col)
    # 0° at top, clockwise — matches the trajectory frame. R is a 0..1 unit disk.
    fig.update_polars(angularaxis=dict(rotation=90, direction="clockwise",
                                       thetaunit="degrees"),
                      radialaxis=dict(range=[0, 1], angle=90, tickangle=90,
                                      tickvals=[0.25, 0.5, 0.75, 1.0]),
                      bgcolor="white")
    for ann in fig.layout.annotations:
        ann.update(font=dict(size=10), yshift=10)
    fig.update_layout(height=90 + nrows * 450, template="plotly_white",
                      margin=dict(l=42, r=112, t=74, b=44),
                      showlegend=color_by in ("individual", "vr", "roi", "none"))
    return (fig, quality) if return_summary else fig



# ---------------------------------------------------------------------------
# Dash App
# ---------------------------------------------------------------------------

app = Dash(
    __name__,
    suppress_callback_exceptions=True,
    update_title="Working…",
    on_error=_dash_error_handler,
)
app.title = "Trajectory Dashboard"

_load_config_lut()      # restore any saved / hand-edited config names

_DATA_CACHE: dict = {}
_STATS_CACHE: dict = {}
_META_CACHE: dict = {}
_VELOCITY_CACHE: dict = {}
_DATA_TOKEN_BY_PATTERN: dict[str, tuple] = {}
_DATA_CACHE_ORDER: list[tuple] = []
_DATA_CACHE_MAX = 3
_DATA_LOCK = threading.RLock()
_FILTER_LOCK = threading.RLock()

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


def _pattern_key(pattern):
    return (pattern or "").strip()


def _files_signature(files):
    sig = []
    for f in files:
        try:
            st = os.stat(f)
            sig.append((os.path.abspath(f), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((os.path.abspath(f), None, None))
    return tuple(sig)


def _invalidate_render_state():
    for name in ("_FILTER_CACHE", "_FILTER_CACHE_ORDER", "_ROI_MASK_CACHE",
                 "_POLAR_RAY_CACHE", "_POLAR_RAY_CACHE_ORDER", "_VELOCITY_CACHE"):
        obj = globals().get(name)
        if hasattr(obj, "clear"):
            obj.clear()


def _remember_data_cache_key(key):
    """Keep only a small LRU of loaded raw datasets in-process.

    A browser tab usually needs one active dataset plus maybe a recent previous
    one after a reload. Keeping every folder forever made long exploratory
    sessions quietly grow server memory.
    """
    try:
        _DATA_CACHE_ORDER.remove(key)
    except ValueError:
        pass
    _DATA_CACHE_ORDER.append(key)
    while len(_DATA_CACHE_ORDER) > _DATA_CACHE_MAX:
        old = _DATA_CACHE_ORDER.pop(0)
        _DATA_CACHE.pop(old, None)
        _STATS_CACHE.pop(old, None)
        _META_CACHE.pop(old, None)
        _VELOCITY_CACHE.pop(old, None)
        stale_patterns = [p for p, token in _DATA_TOKEN_BY_PATTERN.items()
                          if token == old]
        for p in stale_patterns:
            _DATA_TOKEN_BY_PATTERN.pop(p, None)


def _load_data_locked(pattern):
    started = time.perf_counter()
    pkey = _pattern_key(pattern)
    files = td_io.find_csv_files(pattern)
    key = (pkey, _files_signature(files))
    previous = _DATA_TOKEN_BY_PATTERN.get(pkey)
    _DATA_TOKEN_BY_PATTERN[pkey] = key
    if previous is not None and previous != key:
        _invalidate_render_state()
    if key in _DATA_CACHE:
        _remember_data_cache_key(key)
        metas = _META_CACHE.get(key, [])
        _set_config_order(metas)
        cached = _DATA_CACHE[key]
        LOGGER.debug(
            "data.cache_hit files=%d rows=%d source=%r",
            len(files), len(cached), pkey,
        )
        return _DATA_CACHE[key], _STATS_CACHE.get(key), metas

    _LOAD_PROGRESS.update(done=0, total=len(files), active=True, label="scanning")
    if not files:
        _LOAD_PROGRESS.update(active=False)
        LOGGER.warning("data.no_files source=%r", pkey)
        return None, None, []

    LOGGER.info("data.load_start files=%d source=%r", len(files), pkey)

    dfs, metas, seen = [], [], set()
    for i, f in enumerate(files):
        d = td_io.load_csv_fast(f)
        if d is not None:
            dfs.append(d)
        folder = os.path.dirname(f)
        if folder not in seen:
            seen.add(folder)
            metas.append(td_io.load_folder_metadata(folder))
        _LOAD_PROGRESS.update(done=i + 1, total=len(files), active=True,
                              label=os.path.basename(f))

    if not dfs:
        _LOAD_PROGRESS.update(active=False)
        LOGGER.warning("data.no_valid_frames files=%d source=%r", len(files), pkey)
        return None, None, metas

    _LOAD_PROGRESS.update(label="concatenating")
    df = pd.concat(dfs, ignore_index=True)
    td_io.concatenate_restarted_trials(df)
    # Per-file loading repairs repeated segment blocks; this final guard only
    # falls back to a full sort if the concatenated frame is still unsafe.
    td_io.sort_frame_for_segments(df)
    velocity = velocity_all(df)
    stats = compute_segment_stats(df, velocity)
    for c in ("ConfigFile", "SceneName", "VR", "FlyID", "Sex",
              "SourceFolder", "SourceFile"):
        if c in df.columns:
            df[c] = df[c].astype("category")
    df.attrs["_frame_token"] = ("data", key)
    _set_config_order(metas)
    _populate_auto_lut(metas)           # readable config names from objects
    _DATA_CACHE[key] = df
    _STATS_CACHE[key] = stats
    _META_CACHE[key] = metas
    _VELOCITY_CACHE[key] = velocity
    _remember_data_cache_key(key)
    _LOAD_PROGRESS.update(active=False)
    LOGGER.info(
        "data.load_done files=%d rows=%d segments=%d seconds=%.3f source=%r",
        len(files), len(df), int(df["_seg_id"].nunique()),
        time.perf_counter() - started, pkey,
    )
    return df, stats, metas


def _load_data(pattern):
    """Load once per file signature, even when several Dash callbacks arrive."""
    with _DATA_LOCK:
        return _load_data_locked(pattern)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

_EMPTY = go.Figure().update_layout(height=190, template="plotly_white")
_INPUT_STYLE = {"width": "100%", "fontSize": "11px", "padding": "3px",
                "boxSizing": "border-box"}
GRAPH_CONFIG = {"scrollZoom": True, "displayModeBar": True, "displaylogo": False}

# Every plot is part of one normal document flow. Navigation scrolls to these
# sections; no graph is ever measured, resized, or re-rendered while hidden.
_PANEL_STYLE = {"position": "relative", "overflow": "visible",
                "scrollMarginTop": "52px", "marginBottom": "12px"}

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),

    # Header
    html.Div([
        html.H3("Trajectory Dashboard",
                style={"margin": "0", "fontSize": "17px", "whiteSpace": "nowrap"}),
        # Compact live status belongs beside the title, where it remains visible
        # regardless of sidebar/main scroll position. Hover exposes stage timing.
        html.Div([
            html.Div([
                html.Span(className="status-dot"),
                html.Strong("Status", className="status-phase-label"),
                html.Span(className="status-phase"),
            ], className="status-dock-heading"),
            html.Div("Choose a data source to begin.", id="status-message",
                     className="status-message"),
            html.Div("Server diagnostics appear in the terminal.",
                     id="status-detail", className="status-detail"),
            html.Div(id="load-status", className="status-raw-hidden"),
            html.Div(id="plot-status", className="status-raw-hidden"),
        ], id="status-dock", className="status-dock header-status",
           title="No completed operation yet."),
        html.Button("Export HTML", id="btn-export", n_clicks=0,
                    title="Download a standalone HTML report with the current views.",
                    style={"fontSize": "11px", "padding": "4px 10px"}),
        dcc.Download(id="download-html"),
    ], className="td-header",
       style={"display": "flex", "alignItems": "center", "padding": "6px 14px",
              "borderBottom": "2px solid #ddd", "background": "#f8f9fa", "gap": "10px"}),

    html.Div([
        # ---- Sidebar ----
        html.Div([
            html.Label("Data Source", style={"fontWeight": "bold", "fontSize": "12px"}),
            # Drag-and-drop a folder (or click to pick) → auto-builds a glob.
            html.Div([
                html.Div("Folder", style={"fontSize": "13px", "lineHeight": "1",
                                          "fontWeight": "bold", "pointerEvents": "none"}),
                html.Div("Drop or choose a data folder", id="drop-label",
                         style={"fontSize": "13px", "fontWeight": "bold", "color": "#445",
                                 "marginTop": "4px", "pointerEvents": "none"}),
                html.Div("Nested CSVs are discovered automatically",
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
            html.Button("Load", id="btn-load", n_clicks=0,
                        title="Load CSVs and metadata from the data source.",
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
            html.Hr(style={"margin": "6px 0"}),

            html.Label("Panels", style={"fontWeight": "bold", "fontSize": "12px"}),
            dcc.Dropdown(id="group-by", options=[
                {"label": "Config / Treatment", "value": "config"},
                {"label": "Scene", "value": "scene"},
                {"label": "VR", "value": "vr"},
                {"label": "Fly ID", "value": "flyid"},
                {"label": "Source Folder", "value": "file"},
                {"label": "All Pooled", "value": "all"},
            ], value="config", clearable=False, style={"fontSize": "11px"}),

            dcc.RadioItems(id="pool-mode", options=[
                {"label": "Separate", "value": "separate"},
                {"label": "Pooled", "value": "pooled"},
            ], value="separate", className="segmented-control",
               style={"fontSize": "11px", "marginTop": "3px"}),
            dcc.Checklist(id="show-raw-config",
                          options=[{"label": " Show raw config filenames",
                                    "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "3px"}),
            html.Div("Readable names come from config metadata; custom names live in Advanced.",
                     style={"fontSize": "9px", "color": "#888"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Trajectories", style={"fontWeight": "bold", "fontSize": "12px"}),
            html.Label("Colour", title="Colour trajectories and polar vectors by the same metadata or metric.",
                       style={"fontSize": "10px"}),
            dcc.Dropdown(id="color-by", options=[
                {"label": "Individual (VR+Fly)", "value": "individual"},
                {"label": "VR", "value": "vr"},
                {"label": "ROI outcome", "value": "roi"},
                {"label": "Trial (sequential)", "value": "trial"},
                {"label": "Local time (sequential)", "value": "local_time"},
                {"label": "Velocity (units/s, smoothed)", "value": "velocity"},
            ], value="individual", clearable=False, style={"fontSize": "11px"}),
            html.Label("Render mode",
                       title="Accuracy uses full filtered data for analysis views; Speed decimates plotted data more aggressively.",
                       style={"fontSize": "10px", "marginTop": "4px"}),
            dcc.RadioItems(id="render-mode", options=[
                {"label": " Speed", "value": "speed"},
                {"label": " Accuracy", "value": "accuracy"},
            ], value="speed", inline=True, className="segmented-control",
               style={"fontSize": "10px"}),
            dcc.Checklist(id="animate-toggle",
                          options=[{"label": " Playback animation", "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "3px"}),
            html.Div(f"Playback uses {BUDGET_SVG//1000}k points; static uses {BUDGET_GL//1000}k by default.",
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
                html.Label("Scale", style={"fontSize": "10px"}),
                dcc.RadioItems(id="heatmap-scale", options=[
                    {"label": "Linear", "value": "lin"},
                    {"label": "Log", "value": "log"},
                ], value="lin", className="segmented-control",
                   style={"fontSize": "10px"}, inline=True),
            ], className="compact-control-row", style={"marginTop": "3px"}),
            html.Label("Metric", style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.Dropdown(id="heatmap-metric", options=[
                {"label": "Occupancy", "value": "time"},
                {"label": "Time %", "value": "percent"},
                {"label": "Samples", "value": "count"},
            ], value="time", clearable=False, style={"fontSize": "10px"}),
            html.Div([
                html.Label("Color range",
                           title="Heatmap colour min/max using the active metric distribution.",
                           style={"fontSize": "10px"}),
                dcc.Graph(id="heatmap-color-hist",
                          figure=build_mini_histogram(None, color="#0f766e"),
                          config={"displayModeBar": False, "staticPlot": True},
                          style={"height": "58px", "margin": "0 0 -6px"}),
                dcc.RangeSlider(id="heatmap-color-range", min=0, max=1,
                                step=0.01, value=[0, 1],
                                marks={0: "0", 1: "1"},
                                tooltip={"placement": "bottom",
                                         "always_visible": False}),
            ], style={"marginTop": "3px"}),
            html.Div([
                dcc.Input(id="heatmap-cmin", type="number", value=None,
                          placeholder="auto", step="any", debounce=True,
                          style={**_INPUT_STYLE, "display": "none"}),
                dcc.Input(id="heatmap-cmax", type="number", value=None,
                          placeholder="auto", step="any", debounce=True,
                          style={**_INPUT_STYLE, "display": "none"}),
            ], style={"display": "none"}),
            html.Label("Limits", style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.RadioItems(id="heatmap-crange", options=[
                {"label": "Value", "value": "value"},
                {"label": "Percentile", "value": "percentile"},
            ], value="percentile", inline=True, className="segmented-control",
               style={"fontSize": "10px"}),
            html.Div("Color limits follow the selected metric; percentile mode converts the selected metric span to percentiles.",
                     style={"fontSize": "9px", "color": "#888"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Targets", style={"fontWeight": "bold", "fontSize": "12px"}),
            dcc.Checklist(id="roi-show",
                          options=[{"label": " Show target ROIs + reached counts",
                                    "value": "on"}],
                          value=["on"], style={"fontSize": "11px"}),
            html.Label("Reach radius (units)", title="Distance from target centre counted as ROI entry.",
                       style={"fontSize": "10px", "marginTop": "4px"}),
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
            html.Div("Targets come from scene configs. Reach radius controls left/right entry counts.",
                     style={"fontSize": "9px", "color": "#888", "marginTop": "2px"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Polar", style={"fontWeight": "bold", "fontSize": "12px"}),
            html.Label("Angle source",
                       title="Body orientation uses Unity GameObjectRotY. Movement heading uses consecutive X/Z samples.",
                       style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Dropdown(id="polar-angle-source", options=[
                {"label": "Body orientation (RotY)", "value": "orientation"},
                {"label": "Movement heading (X/Z)", "value": "movement"},
            ], value="orientation", clearable=False, style={"fontSize": "10px"}),
            html.Label("Rayleigh R range",
                       title="Filter polar trial vectors by Rayleigh strength: 0 = scattered headings, 1 = strongly directed.",
                       style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Graph(id="polar-r-hist", figure=build_polar_r_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="polar-r-range", min=0, max=1, step=0.01,
                            value=[0, 1], marks={0: "0", 0.5: "0.5", 1: "1"},
                            tooltip={"placement": "bottom", "always_visible": False}),
            html.Label("Min valid point fraction / trial",
                       title="Minimum fraction of samples in a trial that must have a usable heading after moving-only filtering.",
                       style={"fontSize": "10px", "marginTop": "4px"}),
            dcc.Graph(id="polar-point-frac-hist",
                      figure=build_mini_histogram(None, [0, 1], color="#7c3aed",
                                                  x_range=(0, 1)),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.Slider(id="polar-min-point-frac", min=0, max=1, step=0.05,
                       value=0, marks={0: "0", 0.5: "0.5", 1: "1"},
                       tooltip={"placement": "bottom", "always_visible": False}),
            html.Label("Min good-trial fraction / animal",
                       title="Drop animals unless at least this fraction of their trials pass the polar trial gates.",
                       style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.Graph(id="polar-animal-frac-hist",
                      figure=build_mini_histogram(None, [0, 1], color="#9333ea",
                                                  x_range=(0, 1)),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.Slider(id="polar-min-animal-frac", min=0, max=1, step=0.05,
                       value=0, marks={0: "0", 0.5: "0.5", 1: "1"},
                       tooltip={"placement": "bottom", "always_visible": False}),
            html.Div([
                dcc.Checklist(id="polar-moving",
                              options=[{"label": " Moving only", "value": "on"}],
                              value=[], style={"fontSize": "10px", "flex": "1"}),
                html.Label("Min speed", title="Speed threshold for moving-only heading samples.",
                           style={"fontSize": "10px", "whiteSpace": "nowrap"}),
                dcc.Input(id="polar-walk", type="number", value=1, min=0, step="any",
                          debounce=True,
                          style={**_INPUT_STYLE, "width": "62px"}),
            ], className="compact-control-row",
               style={"marginTop": "3px", "alignItems": "center"}),
            html.Div("0° is forward (+Z), positive angles turn right (+X). The bold ray pools all valid samples exactly.",
                     style={"fontSize": "9px", "color": "#888", "marginTop": "2px"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Filters", style={"fontWeight": "bold", "fontSize": "12px"}),
            html.Div("Serial filters update the plots and the retention audit.",
                     style={"fontSize": "9px", "color": "#888"}),
            html.Label("Peak velocity range",
                       title="Per-trial peak velocity range. Full span is treated as no range filter.",
                       style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.Graph(id="vel-range-hist", figure=build_mini_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="vel-range", min=0, max=1, step=0.01,
                            value=[0, 1], marks={0: "0", 1: "1"},
                            tooltip={"placement": "bottom", "always_visible": False}),
            html.Label("Net displacement range",
                       title="Per-trial start-to-end displacement range. Full span is treated as no range filter.",
                       style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.Graph(id="disp-range-hist", figure=build_mini_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="disp-range", min=0, max=1, step=0.01,
                            value=[0, 1], marks={0: "0", 1: "1"},
                            tooltip={"placement": "bottom", "always_visible": False}),
            html.Div(id="filter-detail", className="filter-detail",
                     children="Load data to see retention accounting."),

            html.Button("Update all plots", id="btn-plot", n_clicks=0,
                        title=f"Rebuild all sections now. Changes auto-update after {PLOT_DEBOUNCE_MS / 1000:g}s idle.",
                        style={"width": "100%", "marginTop": "4px", "padding": "5px",
                               "border": "1px solid #0d6efd", "background": "white",
                               "color": "#0d6efd", "cursor": "pointer", "fontSize": "12px",
                               "borderRadius": "3px"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Subset", style={"fontWeight": "bold", "fontSize": "12px"}),
            html.Label("Trial range",
                       title="Subset by CurrentTrial. Full span is treated as no trial subset.",
                       style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Graph(id="trial-range-hist", figure=build_mini_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="trial-range", min=0, max=1, step=1,
                            value=[0, 1], marks={0: "0", 1: "1"},
                            tooltip={"placement": "bottom", "always_visible": False}),
            html.Div([
                html.Div([
                    html.Label("Min trial", style={"fontSize": "10px"}),
                    dcc.Input(id="trial-min", type="number", value=None,
                              placeholder="1", step=1, debounce=True,
                              style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("Max trial", style={"fontSize": "10px"}),
                    dcc.Input(id="trial-max", type="number", value=None,
                              placeholder="Last", step=1, debounce=True,
                              style=_INPUT_STYLE),
                ], style={"flex": "1"}),
            ], style={"display": "none"}),
            html.Div("Uses the dataset's CurrentTrial values.",
                     style={"fontSize": "9px", "color": "#888", "marginTop": "-1px",
                            "marginBottom": "3px"}),
            html.Label("Step range",
                       title="Subset complete segments by CurrentStep. Full span keeps every step.",
                       style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Graph(id="step-range-hist", figure=build_mini_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="step-range", min=0, max=1, step=1,
                            value=[0, 1], marks={0: "0", 1: "1"},
                            tooltip={"placement": "bottom", "always_visible": False}),
            html.Div([
                dcc.Input(id="step-min", type="number", value=None,
                          step=1, debounce=True),
                dcc.Input(id="step-max", type="number", value=None,
                          step=1, debounce=True),
            ], style={"display": "none"}),
            html.Div("Uses CurrentStep and preserves whole T…_S… segments.",
                     style={"fontSize": "9px", "color": "#888", "marginTop": "-1px",
                            "marginBottom": "3px"}),
            html.Label("Configs", style={"fontSize": "10px"}),
            dcc.Dropdown(id="filter-configs", multi=True, placeholder="All",
                         style={"fontSize": "10px"}),
            html.Details([
                html.Summary("Plot order", style={"fontSize": "10px", "cursor": "pointer"}),
                html.Ol(id="config-order-list", style={
                    "margin": "4px 0 0 16px", "padding": "0", "fontSize": "9px",
                    "maxHeight": "150px", "overflowY": "auto",
                }),
            ], style={"marginTop": "3px"}),
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
                html.Label("Panel columns", style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Input(id="subplot-ncols", type="number", value=2, min=1, max=6,
                          debounce=True, style=_INPUT_STYLE),
                html.Label("Point budget",
                           title="Optional manual cap for rendered points; blank uses the selected render mode.",
                           style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Input(id="plot-points", type="number", value=None, min=500,
                          placeholder="auto (dynamic)", debounce=True,
                          style=_INPUT_STYLE),
                html.Div("Blank = auto-decimate to a browser-safe budget.",
                         style={"fontSize": "9px", "color": "#888"}),
                html.Hr(style={"margin": "6px 0"}),
                html.Label("Outlier cleanup", style={"fontSize": "10px",
                                                      "fontWeight": "bold"}),
                html.Div([
                    html.Div([
                        html.Label("Spike speed", title="Optional instantaneous speed spike removal.",
                                   style={"fontSize": "10px"}),
                        dcc.Input(id="vel-threshold", type="number", value=None,
                                  placeholder="off", debounce=True,
                                  style=_INPUT_STYLE),
                        dcc.Checklist(id="vel-auto",
                                      options=[{"label": " auto p99", "value": "on"}],
                                      value=[], style={"fontSize": "9px"}),
                    ], style={"flex": "1"}),
                    html.Div([
                        html.Label("Min move", title="Optional whole-trial minimum displacement cleanup.",
                                   style={"fontSize": "10px"}),
                        dcc.Input(id="min-disp", type="number", value=None,
                                  placeholder="off", debounce=True,
                                  style=_INPUT_STYLE),
                        dcc.Checklist(id="disp-auto",
                                      options=[{"label": " auto", "value": "on"}],
                                      value=[], style={"fontSize": "9px"}),
                    ], style={"flex": "1"}),
                ], style={"display": "flex", "gap": "6px"}),
                html.Label("Spike buffer (ms)",
                           title="Extra time removed around instantaneous speed spikes.",
                           style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Input(id="jump-buffer", type="number", value=100, min=0,
                          step=10, debounce=True, style=_INPUT_STYLE),
                html.Div("These cleanup gates are optional; the visible range sliders handle normal filtering.",
                         style={"fontSize": "9px", "color": "#888"}),
                html.Label("Trim segment edges", style={"fontSize": "10px",
                                                         "marginTop": "3px"}),
                dcc.Input(id="trim-samples", type="number", value=0, min=0,
                          debounce=True, style=_INPUT_STYLE),
                html.Div("Usually 0. Removes N samples from both ends after spike filtering.",
                         style={"fontSize": "9px", "color": "#888"}),
                html.Label("Raw trace columns", style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Dropdown(id="raw-columns", multi=True,
                             value=[],
                             style={"fontSize": "10px"}),

                html.Hr(style={"margin": "6px 0"}),
                html.Label("Config display names", style={"fontSize": "10px",
                                                           "fontWeight": "bold"}),
                html.Div("JSON map from config filename to display name.",
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

            html.Div([
                html.A("❤️ by pvnkmrksk", href=REPO_URL, target="_blank",
                       rel="noopener noreferrer",
                       style={"color": "#2563eb", "textDecoration": "none",
                              "fontWeight": "650"}),
            ], className="td-footer-credit",
               style={"fontSize": "10px", "marginTop": "10px", "paddingTop": "7px",
                      "borderTop": "1px solid #e7ebf2", "color": "#667085"}),

        ], className="td-sidebar",
           style={"width": "255px", "padding": "8px", "overflowY": "auto",
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

            # Section navigation. All figures remain visible and mounted; this
            # control only scrolls the main workspace to the chosen section.
            dcc.Tabs(id="view-mode", value="traj", parent_className="view-tabs-wrap",
                     className="view-tabs", children=[
                dcc.Tab(label="Trajectories", value="traj",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Heatmap", value="heat",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Diagnostics", value="diag",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Targets", value="roi",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Polar", value="polar",
                        className="view-tab", selected_className="view-tab-selected"),
            ], style={"flexShrink": "0"}),

            # Single-page plot workspace. This deliberately mirrors the stable
            # standalone export lifecycle: all graphs are born visible.
            html.Div([
                # --- Trajectories ---
                html.Div([
                    html.Div([html.H4("Trajectories"),
                              html.Span("Merged WebGL paths", className="plot-section-kicker")],
                             className="plot-section-heading"),
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
                                  config=GRAPH_CONFIG,
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"}),
                ], id="view-traj", className="plot-section", style={**_PANEL_STYLE}),

                # --- Heatmap ---
                html.Div(
                    [html.Div([html.H4("Occupancy heatmap"),
                               html.Span("Shared spatial bins", className="plot-section-kicker")],
                              className="plot-section-heading"),
                     dcc.Loading(
                        dcc.Graph(id="heatmap-plot", figure=_EMPTY,
                                  config=GRAPH_CONFIG,
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"})],
                    id="view-heat", className="plot-section", style={**_PANEL_STYLE}),

                # --- Diagnostics ---
                html.Div([
                    html.Div([html.H4("Diagnostics"),
                              html.Span("Filter distributions and raw signals", className="plot-section-kicker")],
                             className="plot-section-heading"),
                    html.Div([
                        dcc.Graph(id="vel-histogram", figure=_EMPTY,
                                  config=GRAPH_CONFIG,
                                  style={"flex": "1", "minWidth": "0"}),
                        dcc.Graph(id="disp-histogram", figure=_EMPTY,
                                  config=GRAPH_CONFIG,
                                  style={"flex": "1", "minWidth": "0"}),
                    ], style={"display": "flex", "gap": "6px"}),
                    html.Div(
                        dcc.Loading(
                            dcc.Graph(id="raw-trace-plot", figure=_EMPTY,
                                      config=GRAPH_CONFIG),
                            type="circle", delay_show=250, delay_hide=250,
                            overlay_style={"visibility": "visible", "opacity": 0.55,
                                           "transition": "opacity .2s",
                                           "pointerEvents": "none"}),
                        id="raw-trace-wrap", style={"display": "none"}),
                ], id="view-diag", className="plot-section", style={**_PANEL_STYLE}),

                # --- ROI counts (violins) ---
                html.Div(
                    [html.Div([html.H4("Target diagnostics"),
                               html.Span("Reach, residence, latency and heading error", className="plot-section-kicker")],
                              className="plot-section-heading"),
                     dcc.Loading(
                        dcc.Graph(id="roi-plot", figure=_EMPTY,
                                  config=GRAPH_CONFIG,
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"})],
                    id="view-roi", className="plot-section", style={**_PANEL_STYLE}),

                # --- Polar ---
                html.Div(
                    [html.Div([html.H4("Polar direction"),
                               html.Span("Per-trial vectors and pooled population mean", className="plot-section-kicker")],
                              className="plot-section-heading"),
                     dcc.Loading(
                        dcc.Graph(id="polar-plot", figure=_EMPTY, responsive=False,
                                  config=GRAPH_CONFIG,
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"})],
                    id="view-polar", className="plot-section", style={**_PANEL_STYLE}),
            ], id="plot-drop-target", className="plot-drop-target",
               style={"position": "relative", "minWidth": "0"}),
        ], id="main-scroll", className="td-main",
           style={"flex": "1", "padding": "4px 8px", "display": "flex",
                   "flexDirection": "column", "height": "calc(100vh - 46px)",
                   "minWidth": "0", "overflowY": "auto", "overflowX": "hidden"}),
    ], style={"display": "flex", "height": "calc(100vh - 46px)"}),

    # Stores
    dcc.Store(id="store-glob"),
    dcc.Store(id="data-generation"),
    dcc.Store(id="viewport-store"),
    dcc.Store(id="heatmap-figure-store"),
    dcc.Store(id="heatmap-variants"),
    dcc.Store(id="heatmap-color-values"),
    dcc.Store(id="config-order-store"),
    dcc.Store(id="auto-thresholds"),
    dcc.Store(id="drop-data"),
    dcc.Store(id="view-render-state", data={}),
    dcc.Store(id="polar-render-state", data={}),
    dcc.Store(id="url-restored", data=False),
    dcc.Store(id="auto-replot-state"),
    dcc.Checklist(id="rebase-origin", options=[{"label": "", "value": "on"}],
                  value=[], style={"display": "none"}),
    dcc.Interval(id="autoload-interval", interval=500, max_intervals=1),
    dcc.Interval(id="load-progress-interval", interval=200, disabled=True),
    dcc.Interval(id="auto-replot-interval", interval=PLOT_DEBOUNCE_MS,
                 max_intervals=-1, disabled=True),
], className="td-app",
   style={"fontFamily": "system-ui, -apple-system, sans-serif", "margin": "0"})


# Keep a compact, always-visible account of the latest activity. The CSS also
# reads Dash's global loading class so the phase dot changes immediately for
# every callback, including failures that do not manage to update a message.
app.clientside_callback(
    "function(load,plot,summary,render,polar,generation){"
    "var loaded=generation&&Number(generation.loaded||0);"
    "var completed=render&&Number(render.completed||0);"
    "var pending=loaded&&(!completed||loaded>completed);"
    "var loadIssue=load&&(/^(No |Choose|Could not|Failed|Error)/i).test(load);"
    "var message=pending?(load||'Loading the selected dataset…'):"
    "(loadIssue?load:(plot||summary||load||'Choose a data source to begin.'));"
    "var bits=[];if(load)bits.push(load);"
    "if(summary&&summary!==message)bits.push(summary);"
    "var done=render&&render.completed;if(done){"
    "try{bits.push('Last render '+new Date(done*1000).toLocaleTimeString());}catch(e){}}"
    "if(generation&&generation.pattern&&!load)bits.push(generation.pattern);"
    "bits.push('Errors and tracebacks: server terminal');"
    "var op=render||{};if(polar&&Number(polar.completed||0)>Number(op.completed||0))op=polar;"
    "var tip=[];if(op.operation)tip.push('Last operation: '+op.operation);"
    "var tm=op.timings||{};Object.keys(tm).forEach(function(k){"
    "var v=Number(tm[k]);if(isFinite(v))tip.push(k+': '+v.toFixed(3)+' s');});"
    "if(!tip.length)tip.push('Timing appears after the first completed render.');"
    "tip.push('Full errors and tracebacks are in the server terminal.');"
    "return [message,bits.join(' • '),tip.join('\\n')];}",
    Output("status-message", "children"),
    Output("status-detail", "children"),
    Output("status-dock", "title"),
    Input("load-status", "children"),
    Input("plot-status", "children"),
    Input("data-summary", "children"),
    Input("view-render-state", "data"),
    Input("polar-render-state", "data"),
    Input("data-generation", "data"),
)

app.clientside_callback(
    "function(n,pattern,armed){if(!n||!pattern)return window.dash_clientside.no_update;"
    "var labels={'trial-range':'trial subset','trial-min':'trial subset',"
    "'trial-max':'trial subset','step-range':'step subset','step-min':'step subset',"
    "'step-max':'step subset','vel-range':'velocity subset',"
    "'disp-range':'displacement subset','filter-configs':'config subset',"
    "'filter-vrs':'VR subset','filter-flyids':'animal subset',"
    "'filter-scenes':'scene subset','filter-folders':'folder subset'};"
    "var fresh=armed&&((Date.now()/1000-Number(armed.ts||0))<4)&&"
    "Number(n)===Number(armed.clicks||0)+1;"
    "if(fresh){var key=String(armed.trigger||'filters');"
    "return 'Applying '+(labels[key]||key.replace(/-/g,' '))+' and rebuilding sections…';}"
    "return 'Rendering all sections… request '+n;}",
    Output("plot-status", "children", allow_duplicate=True),
    Input("btn-plot", "n_clicks"),
    State("store-glob", "data"),
    State("auto-replot-state", "data"),
    prevent_initial_call=True,
)

app.clientside_callback(
    "function(n,pattern){if(!n)return window.dash_clientside.no_update;"
    "if(!pattern)return 'Load data before exporting.';"
    "return 'Building self-contained HTML export…';}",
    Output("plot-status", "children", allow_duplicate=True),
    Input("btn-export", "n_clicks"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)

# The section tabs are navigation, not conditional rendering. Every graph stays
# mounted; changing the tab only moves the existing main scroller to that card.
app.clientside_callback(
    "function(view){if(window.__scrollTrajectorySection){"
    "window.__scrollTrajectorySection(view,'smooth');return '';}"
    "var scroller=document.getElementById('main-scroll');"
    "var target=document.getElementById('view-'+view);"
    "if(scroller&&target)scroller.scrollTo({top:target.offsetTop,behavior:'smooth'});"
    "return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("view-mode", "value"),
    prevent_initial_call=True,
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

# Full URL <-> state. Keep these keys in sync with update_url().
_URL_NUM = {"vel": "vel-threshold", "disp": "min-disp", "trim": "trim-samples",
            "jb": "jump-buffer", "hbin": "heatmap-binsize", "hbound": "heatmap-bound",
            "hcmin": "heatmap-cmin", "hcmax": "heatmap-cmax", "ncols": "subplot-ncols",
            "pts": "plot-points", "tmin": "trial-min", "tmax": "trial-max",
            "smin": "step-min", "smax": "step-max",
            "rmin": "polar-r-range", "rmax": "polar-r-range",
            "vrmin": "vel-range", "vrmax": "vel-range",
            "drmin": "disp-range", "drmax": "disp-range",
            "pmin": "polar-min-point-frac", "amin": "polar-min-animal-frac"}
_URL_STR = {"groupby": "group-by", "pool": "pool-mode", "color": "color-by",
            "hscale": "heatmap-scale", "hmetric": "heatmap-metric",
            "hcrange": "heatmap-crange", "pang": "polar-angle-source"}
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
    Output("trial-min", "value", allow_duplicate=True),
    Output("trial-max", "value", allow_duplicate=True),
    Output("step-min", "value", allow_duplicate=True),
    Output("step-max", "value", allow_duplicate=True),
    Output("raw-columns", "value", allow_duplicate=True),
    Output("subplot-ncols", "value", allow_duplicate=True),
    Output("plot-points", "value", allow_duplicate=True),
    Output("polar-r-range", "value", allow_duplicate=True),
    Output("vel-range", "value", allow_duplicate=True),
    Output("disp-range", "value", allow_duplicate=True),
    Output("heatmap-color-range", "value", allow_duplicate=True),
    Output("trial-range", "value", allow_duplicate=True),
    Output("step-range", "value", allow_duplicate=True),
    Output("polar-min-point-frac", "value", allow_duplicate=True),
    Output("polar-min-animal-frac", "value", allow_duplicate=True),
    Output("polar-angle-source", "value", allow_duplicate=True),
    Output("render-mode", "value", allow_duplicate=True),
    Output("view-mode", "value", allow_duplicate=True),
    Output("viewport-store", "data", allow_duplicate=True),
    Output("url-restored", "data"),
    Input("url", "search"),
    State("url-restored", "data"),
    prevent_initial_call="initial_duplicate",
)
def restore_from_url(search, already):
    # All outputs except the final url-restored flag. The guarded early-return
    # appends that flag below, so this count must remain one below total arity.
    n_out = 41
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

    def r_range():
        if "rmin" not in p and "rmax" not in p:
            return no_update
        lo = num("rmin")
        hi = num("rmax")
        if lo is no_update:
            lo = 0
        if hi is no_update:
            hi = 1
        lo, hi = _polar_r_range([lo, hi])
        return [lo, hi]

    def range_pair(lo_key, hi_key, default_lo=None, default_hi=None):
        if lo_key not in p and hi_key not in p:
            return no_update
        lo = num(lo_key)
        hi = num(hi_key)
        if lo is no_update:
            lo = default_lo
        if hi is no_update:
            hi = default_hi
        if lo is None or hi is None:
            return no_update
        rng = _numeric_range([lo, hi])
        return list(rng) if rng else no_update

    def trial_slider_range():
        if "tmin" not in p or "tmax" not in p:
            return no_update
        return range_pair("tmin", "tmax")

    def step_slider_range():
        if "smin" not in p or "smax" not in p:
            return no_update
        return range_pair("smin", "smax")

    def heat_color_slider_range():
        rng = range_pair("hcmin", "hcmax", 0, 100)
        if rng is no_update:
            return no_update
        mode = p.get("hcrange", ["percentile"])[0]
        if mode == "percentile":
            return rng if 0 <= rng[0] <= 100 and 0 <= rng[1] <= 100 else no_update
        return rng

    anim = (["on"] if p["anim"][0] == "1" else []) if "anim" in p else no_update
    rebase = []
    view = p["view"][0] if p.get("view", [""])[0] in ("traj", "heat", "roi", "polar", "diag") else no_update
    mode = p["mode"][0] if p.get("mode", [""])[0] in ("accuracy", "speed") else no_update
    angle_source = (p["pang"][0]
                    if p.get("pang", [""])[0] in ("orientation", "movement")
                    else no_update)

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
        num("tmin"), num("tmax"), num("smin"), num("smax"),
        lst("raw"), num("ncols"), num("pts"),
        r_range(),
        range_pair("vrmin", "vrmax"),
        range_pair("drmin", "drmax"),
        heat_color_slider_range(),
        trial_slider_range(),
        step_slider_range(),
        num("pmin"), num("amin"), angle_source, mode, view, vp, True,
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
        return no_update, no_update, "Drop a folder that contains trajectory CSVs."
    pat = resolve_dropped_folder(data.get("folder", ""), data.get("files", []))
    if not pat:
        return (no_update, no_update,
                f"Could not locate '{data.get('folder','')}' on disk. Enter the folder path instead.")
    return pat, (clicks or 0) + 1, f"Data source resolved: {pat}"


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
    Input("trial-min", "value"),
    Input("trial-max", "value"),
    Input("step-min", "value"),
    Input("step-max", "value"),
    Input("raw-columns", "value"),
    Input("subplot-ncols", "value"),
    Input("plot-points", "value"),
    Input("polar-r-range", "value"),
    Input("vel-range", "value"),
    Input("disp-range", "value"),
    Input("polar-min-point-frac", "value"),
    Input("polar-min-animal-frac", "value"),
    Input("polar-angle-source", "value"),
    Input("render-mode", "value"),
    Input("view-mode", "value"),
    State("viewport-store", "data"),
    State("url-restored", "data"),
    prevent_initial_call=True,
)
def update_url(n, g, vel, disp, trim, jb, gb, pm, color, anim,
               hbin, hscale, hbound, hmetric, hcmin, hcmax, hcrange,
               fcfg, fvr, ffly, fscn, ffld, tmin, tmax, smin, smax, raw, ncols, pts,
               rrange, vrange, drange, pmin, amin, angle_source, mode, view, vp, restored):
    if not restored:
        return no_update
    params = {}
    if g:
        params["glob"] = g
    nums = {"vel": vel, "disp": disp, "trim": trim, "jb": jb, "hbin": hbin,
            "hbound": hbound, "hcmin": hcmin, "hcmax": hcmax, "ncols": ncols,
            "pts": pts, "tmin": tmin, "tmax": tmax,
            "smin": smin, "smax": smax, "pmin": pmin, "amin": amin}
    for k, v in nums.items():
        if v is not None and v != "":
            if k == "trim" and float(v or 0) <= 0:
                continue
            params[k] = v
    strs = {"groupby": gb, "pool": pm, "color": color, "mode": mode,
            "hscale": hscale,
            "hmetric": hmetric, "hcrange": hcrange, "pang": angle_source,
            "view": view}
    for k, v in strs.items():
        if v:
            params[k] = v
    params["anim"] = "1" if (anim and "on" in anim) else "0"
    lists = {"fcfg": fcfg, "fvr": fvr, "ffly": ffly, "fscn": fscn, "ffld": ffld, "raw": raw}
    for k, v in lists.items():
        if v:
            params[k] = ",".join(str(x) for x in v)
    if vp and not vp.get("reset") and "xaxis" in vp and "yaxis" in vp:
        params["vbx0"], params["vbx1"] = vp["xaxis"]
        params["vby0"], params["vby1"] = vp["yaxis"]
    lo, hi = _polar_r_range(rrange)
    if lo > 0 or hi < 1:
        params["rmin"], params["rmax"] = lo, hi
    for prefix, value in (("vr", vrange), ("dr", drange)):
        rng = _numeric_range(value)
        if rng:
            params[f"{prefix}min"], params[f"{prefix}max"] = rng
    return "?" + urlencode(params) if params else ""


@app.callback(
    Output("auto-replot-state", "data"),
    Output("auto-replot-interval", "disabled"),
    Output("auto-replot-interval", "n_intervals"),
    Output("plot-status", "children", allow_duplicate=True),
    Input("vel-threshold", "value"),
    Input("min-disp", "value"),
    Input("trim-samples", "value"),
    Input("jump-buffer", "value"),
    Input("group-by", "value"),
    Input("pool-mode", "value"),
    Input("color-by", "value"),
    Input("render-mode", "value"),
    Input("animate-toggle", "value"),
    Input("heatmap-binsize", "value"),
    Input("heatmap-bound", "value"),
    Input("heatmap-cmin", "value"),
    Input("heatmap-cmax", "value"),
    Input("heatmap-crange", "value"),
    Input("filter-configs", "value"),
    Input("filter-vrs", "value"),
    Input("filter-flyids", "value"),
    Input("filter-scenes", "value"),
    Input("filter-folders", "value"),
    Input("vel-range", "value"),
    Input("disp-range", "value"),
    Input("trial-range", "value"),
    Input("trial-min", "value"),
    Input("trial-max", "value"),
    Input("step-range", "value"),
    Input("step-min", "value"),
    Input("step-max", "value"),
    Input("raw-columns", "value"),
    Input("subplot-ncols", "value"),
    Input("plot-points", "value"),
    Input("roi-show", "value"),
    Input("roi-reach", "value"),
    Input("roi-entered", "value"),
    Input("roi-trim", "value"),
    State("data-generation", "data"),
    State("store-glob", "data"),
    State("btn-plot", "n_clicks"),
    prevent_initial_call=True,
)
def arm_auto_replot(*values):
    generation, pattern, clicks = values[-3], values[-2], values[-1]
    if not pattern:
        return no_update, True, 0, no_update
    if (isinstance(generation, dict)
            and time.time() - float(generation.get("loaded") or 0) < 2.0):
        return no_update, True, 0, no_update
    trigger = ctx.triggered_id or "control"
    label = {
        "trial-range": "trial subset", "trial-min": "trial subset",
        "trial-max": "trial subset", "step-range": "step subset",
        "step-min": "step subset", "step-max": "step subset",
        "vel-range": "velocity subset", "disp-range": "displacement subset",
        "filter-configs": "config subset", "filter-vrs": "VR subset",
        "filter-flyids": "animal subset", "filter-scenes": "scene subset",
        "filter-folders": "folder subset",
    }.get(str(trigger), str(trigger).replace("-", " "))
    return (
        {"clicks": int(clicks or 0), "trigger": str(trigger), "ts": time.time()},
        False,
        0,
        f"Queued {label} update ({PLOT_DEBOUNCE_MS / 1000:g}s idle).",
    )


@app.callback(
    Output("btn-plot", "n_clicks", allow_duplicate=True),
    Output("auto-replot-interval", "disabled", allow_duplicate=True),
    Output("plot-status", "children", allow_duplicate=True),
    Input("auto-replot-interval", "n_intervals"),
    State("auto-replot-state", "data"),
    State("btn-plot", "n_clicks"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)
def fire_auto_replot(n, armed, clicks, pattern):
    # Arming the debounce resets n_intervals to zero.  That zero-value update
    # reaches this callback immediately; leave the interval enabled so it can
    # produce the first real tick instead of cancelling itself before 750 ms.
    if not n:
        return no_update, no_update, no_update
    if not pattern or not armed:
        return no_update, True, no_update
    if int(clicks or 0) > int((armed or {}).get("clicks") or 0):
        return no_update, True, "Manual update applied."
    return (clicks or 0) + 1, True, "Auto-updating after idle change..."


@app.callback(
    Output("load-status", "children"),
    Output("store-glob", "data"),
    Output("data-generation", "data"),
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
    Output("view-render-state", "data", allow_duplicate=True),
    Output("viewport-store", "data", allow_duplicate=True),
    Input("btn-load", "n_clicks"),
    State("glob-input", "value"),
    State("btn-plot", "n_clicks"),
    State("heatmap-binsize", "value"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)
def load_data_cb(n_clicks, pattern, plot_clicks, cur_binsize, previous_pattern):
    empty = go.Figure().update_layout(height=190, template="plotly_white")
    nope = ("Choose a folder or enter a CSV glob.", None, None, [], [], [], [], [], [], "",
            empty, empty,
            no_update, no_update, None, {}, no_update)
    if not pattern:
        LOGGER.warning("ui.load_rejected reason=missing_source")
        return nope

    t0 = time.time()
    LOGGER.info("ui.load_request click=%s source=%r", n_clicks, pattern)
    df, stats, metas = _load_data(pattern)
    elapsed = time.time() - t0

    if df is None or len(df) == 0:
        LOGGER.warning("ui.load_empty source=%r", pattern)
        return (f"No trajectory CSVs matched the current data source.", None, None, [], [], [], [], [], [], "",
                empty, empty, no_update, no_update, None, {}, {"reset": True})

    n_files = df["SourceFile"].nunique()
    n_segs = df["_seg_id"].nunique()
    status = f"Loaded {len(df):,} rows from {n_files} files | {n_segs} segments | {elapsed:.1f}s"
    LOGGER.info(
        "ui.load_ready rows=%d files=%d segments=%d seconds=%.3f reset_controls=%s",
        len(df), n_files, n_segs, elapsed,
        bool(previous_pattern) and _pattern_key(previous_pattern) != _pattern_key(pattern),
    )

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

    token = _DATA_TOKEN_BY_PATTERN.get(_pattern_key(pattern))
    reset_controls = (bool(previous_pattern)
                      and _pattern_key(previous_pattern) != _pattern_key(pattern))
    vv = _VELOCITY_CACHE.get(token)
    if vv is None:
        vv = velocity_all(df)
        if token is not None:
            _VELOCITY_CACHE[token] = vv
    vv = vv[np.isfinite(vv)]
    vel_fig = build_velocity_histogram(df, velocity_values=vv)
    disp_fig = build_displacement_histogram(stats)

    # Auto filter defaults: 99th-pct velocity, and 5% of the median net
    # displacement (a scale-free "barely moved" cut). Stored for the auto boxes.
    disp = stats["displacement"].to_numpy() if stats is not None and len(stats) else np.array([])
    auto = {"vel": round(float(np.percentile(vv, 99)), 3) if vv.size else None,
            "disp": round(float(0.05 * np.median(disp)), 3) if disp.size else None}

    # Smart default bin size on a fresh load; respect any value already set
    # (e.g. restored from the URL).
    binsize_out = no_update if (cur_binsize not in (None, "")) else default_bin_size(df)

    return (
        status, pattern,
        {"pattern": pattern, "token": repr(token), "loaded": time.time(),
         "reset_controls": reset_controls},
        opts("ConfigFile"), opts("VR"), opts("FlyID"), opts("SceneName"),
        opts("SourceFolder"), col_opts,
        "\n".join(meta_parts) or "No experiment metadata found.",
        vel_fig, disp_fig, binsize_out,
        no_update, auto, no_update,
        {"reset": True} if reset_controls else no_update,
    )


@app.callback(
    Output("vel-range", "min"),
    Output("vel-range", "max"),
    Output("vel-range", "step"),
    Output("vel-range", "marks"),
    Output("vel-range", "value"),
    Output("vel-range-hist", "figure"),
    Output("disp-range", "min"),
    Output("disp-range", "max"),
    Output("disp-range", "step"),
    Output("disp-range", "marks"),
    Output("disp-range", "value"),
    Output("disp-range-hist", "figure"),
    Output("trial-range", "min"),
    Output("trial-range", "max"),
    Output("trial-range", "step"),
    Output("trial-range", "marks"),
    Output("trial-range", "value"),
    Output("trial-range-hist", "figure"),
    Output("step-range", "min"),
    Output("step-range", "max"),
    Output("step-range", "step"),
    Output("step-range", "marks"),
    Output("step-range", "value"),
    Output("step-range-hist", "figure"),
    Output("btn-plot", "n_clicks", allow_duplicate=True),
    Input("data-generation", "data"),
    State("store-glob", "data"),
    State("vel-range", "value"),
    State("disp-range", "value"),
    State("trial-range", "value"),
    State("trial-min", "value"),
    State("trial-max", "value"),
    State("step-range", "value"),
    State("step-min", "value"),
    State("step-max", "value"),
    State("btn-plot", "n_clicks"),
    prevent_initial_call=True,
)
def update_range_controls(generation, pattern, vel_current, disp_current, trial_current,
                          trial_min, trial_max, step_current, step_min, step_max,
                          plot_clicks):
    empty = build_mini_histogram(None)
    defaults = (0, 1, 0.01, {0: "0", 1: "1"}, [0, 1], empty)
    if not pattern:
        return defaults + defaults + defaults + defaults + (no_update,)
    df, stats, _ = _load_data(pattern)
    if df is None or len(df) == 0 or stats is None:
        return defaults + defaults + defaults + defaults + (no_update,)

    reset_controls = bool((generation or {}).get("reset_controls"))
    vel_payload = _range_control_payload(
        stats["peak_velocity"].to_numpy() if "peak_velocity" in stats else [],
        None if reset_controls else vel_current,
        color="#1f77b4",
        floor_zero=True,
    )
    disp_payload = _range_control_payload(
        stats["displacement"].to_numpy() if "displacement" in stats else [],
        None if reset_controls else disp_current,
        color="#2ca02c",
        floor_zero=True,
    )

    trial_values = pd.to_numeric(df["CurrentTrial"], errors="coerce").to_numpy(dtype=float)
    lo, hi = _range_bounds(trial_values, floor_zero=False, upper_pct=None)
    restored_trial = _trial_range(trial_min, trial_max)
    trial_source = None if reset_controls else trial_current
    if restored_trial and _looks_like_initial_range(_numeric_range(trial_current), lo, hi):
        trial_source = [lo if restored_trial[0] is None else restored_trial[0],
                        hi if restored_trial[1] is None else restored_trial[1]]
    trial_value = _range_control_value(trial_source, lo, hi)
    trial_payload = (
        float(lo),
        float(hi),
        1,
        _slider_marks(lo, hi),
        trial_value,
        build_mini_histogram(trial_values, trial_value, color="#b7791f",
                             x_range=(lo, hi)),
    )
    step_values = pd.to_numeric(df["CurrentStep"], errors="coerce").to_numpy(dtype=float)
    slo, shi = _range_bounds(step_values, floor_zero=False, upper_pct=None)
    restored_step = _value_range(step_min, step_max)
    step_source = None if reset_controls else step_current
    if restored_step and _looks_like_initial_range(_numeric_range(step_current), slo, shi):
        step_source = [slo if restored_step[0] is None else restored_step[0],
                       shi if restored_step[1] is None else restored_step[1]]
    step_value = _range_control_value(step_source, slo, shi)
    step_payload = (
        float(slo), float(shi), 1, _slider_marks(slo, shi), step_value,
        build_mini_histogram(step_values, step_value, color="#0f766e",
                             x_range=(slo, shi)),
    )
    # This click is the load barrier: all slider outputs in this response are
    # applied before the master renderer reads them as State.  Triggering the
    # renderer directly from data-generation allowed it to race stale ranges
    # from the previous dataset.
    return (vel_payload + disp_payload + trial_payload + step_payload
            + ((plot_clicks or 0) + 1,))


@app.callback(
    Output("vel-range-hist", "figure", allow_duplicate=True),
    Output("disp-range-hist", "figure", allow_duplicate=True),
    Output("trial-range-hist", "figure", allow_duplicate=True),
    Output("step-range-hist", "figure", allow_duplicate=True),
    Input("vel-range", "value"),
    Input("disp-range", "value"),
    Input("trial-range", "value"),
    Input("step-range", "value"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)
def update_range_hist_selection(vel_range, disp_range, trial_range, step_range, pattern):
    if not pattern:
        return no_update, no_update, no_update, no_update
    df, stats, _ = _load_data(pattern)
    if df is None or stats is None:
        return no_update, no_update, no_update, no_update
    vel_values = stats["peak_velocity"].to_numpy() if "peak_velocity" in stats else []
    disp_values = stats["displacement"].to_numpy() if "displacement" in stats else []
    trial_values = pd.to_numeric(df["CurrentTrial"], errors="coerce").to_numpy(dtype=float)
    step_values = pd.to_numeric(df["CurrentStep"], errors="coerce").to_numpy(dtype=float)
    return (
        build_mini_histogram(vel_values, vel_range, color="#1f77b4",
                             x_range=_range_bounds(vel_values, floor_zero=True)),
        build_mini_histogram(disp_values, disp_range, color="#2ca02c",
                             x_range=_range_bounds(disp_values, floor_zero=True)),
        build_mini_histogram(trial_values, trial_range, color="#b7791f",
                             x_range=_range_bounds(trial_values, floor_zero=False,
                                                   upper_pct=None)),
        build_mini_histogram(step_values, step_range, color="#0f766e",
                             x_range=_range_bounds(step_values, floor_zero=False,
                                                   upper_pct=None)),
    )


def _input_number(value):
    try:
        v = float(value)
    except Exception:
        return None
    return int(v) if v.is_integer() else v


@app.callback(
    Output("trial-min", "value", allow_duplicate=True),
    Output("trial-max", "value", allow_duplicate=True),
    Input("trial-range", "value"),
    State("trial-range", "min"),
    State("trial-range", "max"),
    prevent_initial_call=True,
)
def sync_trial_range_to_inputs(value, full_min, full_max):
    return _range_slider_to_open_bounds(value, full_min, full_max)


@app.callback(
    Output("step-min", "value", allow_duplicate=True),
    Output("step-max", "value", allow_duplicate=True),
    Input("step-range", "value"),
    State("step-range", "min"),
    State("step-range", "max"),
    prevent_initial_call=True,
)
def sync_step_range_to_inputs(value, full_min, full_max):
    return _range_slider_to_open_bounds(value, full_min, full_max)


@app.callback(
    Output("heatmap-color-values", "data"),
    Output("heatmap-color-range", "min"),
    Output("heatmap-color-range", "max"),
    Output("heatmap-color-range", "step"),
    Output("heatmap-color-range", "marks"),
    Output("heatmap-color-range", "value"),
    Output("heatmap-color-hist", "figure"),
    # Run after the atomic figure build.  The previous callback raced that
    # build over the same multi-million-row frame and could double wall time.
    Input("view-render-state", "data"),
    Input("heatmap-metric", "value"),
    Input("heatmap-crange", "value"),
    State("store-glob", "data"),
    State("heatmap-binsize", "value"),
    State("heatmap-bound", "value"),
    State("heatmap-color-range", "value"),
    State("heatmap-color-values", "data"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("trial-min", "value"),
    State("trial-max", "value"),
    State("step-min", "value"),
    State("step-max", "value"),
    State("vel-range", "value"),
    State("disp-range", "value"),
    prevent_initial_call=True,
)
def update_heatmap_color_controls(_render_state, metric, mode, pattern, binsize, bound,
                                  current, previous, vel_thresh, min_disp, trim, jump_buf,
                                  cfg, vrs, fids, scenes, folders,
                                  trial_min, trial_max, step_min, step_max,
                                  vel_range, disp_range):
    empty = build_mini_histogram(None, color="#0f766e")
    default = ({}, 0, 1, 0.01, {0: "0", 1: "1"}, [0, 1], empty)
    if not pattern:
        return default
    df_f, df_sub, _ = _filtered_df(
        pattern, vel_thresh, min_disp, trim, jump_buf,
        cfg, vrs, fids, scenes, folders, trial_min, trial_max,
        step_min, step_max, vel_range, disp_range)
    if df_f is None or len(df_f) == 0:
        return default
    values = _heatmap_metric_values(df_f, binsize, bound if bound else 100,
                                    metric or "time")
    lo, hi = _range_bounds(values, floor_zero=True, upper_pct=MINI_HIST_UPPER_PCT)
    store = {
        "values": _sample_for_store(values),
        "lo": float(lo),
        "hi": float(hi),
        "metric": metric or "time",
        "mode": mode or "value",
    }
    if mode == "percentile":
        prior_mode = (previous or {}).get("mode")
        current_rng = _numeric_range(current)
        if prior_mode == "percentile" and current_rng:
            selected = [max(0.0, current_rng[0]), min(100.0, current_rng[1])]
        elif prior_mode == "value" and current_rng:
            selected = [_percentile_rank(values, current_rng[0]),
                        _percentile_rank(values, current_rng[1])]
        else:
            selected = [0.0, 100.0]
        selected_out = (no_update if _numeric_range(current) == _numeric_range(selected)
                        else selected)
        return (
            store, 0.0, 100.0, 1.0, {0: "0", 50: "50", 100: "100"},
            selected_out,
            build_percentile_mini_histogram(values, selected, color="#0f766e"),
        )
    selected = _range_control_value(current, lo, hi)
    selected_out = (no_update
                    if _numeric_range(current) == _numeric_range(selected)
                    else selected)
    return (
        store,
        float(lo),
        float(hi),
        _slider_step(lo, hi),
        _slider_marks(lo, hi),
        selected_out,
        build_mini_histogram(values, selected, color="#0f766e", x_range=(lo, hi)),
    )


@app.callback(
    Output("heatmap-cmin", "value", allow_duplicate=True),
    Output("heatmap-cmax", "value", allow_duplicate=True),
    Output("heatmap-color-hist", "figure", allow_duplicate=True),
    Input("heatmap-color-range", "value"),
    Input("heatmap-crange", "value"),
    State("heatmap-color-values", "data"),
    State("heatmap-cmin", "value"),
    State("heatmap-cmax", "value"),
    prevent_initial_call=True,
)
def sync_heatmap_color_range(value, mode, data, current_cmin, current_cmax):
    rng = _numeric_range(value)
    if rng is None:
        return no_update, no_update, no_update
    lo, hi = rng
    values = _finite_values((data or {}).get("values", []))
    if mode == "percentile":
        lo, hi = max(0.0, lo), min(100.0, hi)
        is_full = lo <= 1e-9 and hi >= 100.0 - 1e-9
        cmin = None if is_full or lo <= 1e-9 else _input_number(lo)
        cmax = None if is_full or hi >= 100.0 - 1e-9 else _input_number(hi)
        fig = build_percentile_mini_histogram(
            values, [lo, hi], color="#0f766e")
        return (no_update if cmin == current_cmin else cmin,
                no_update if cmax == current_cmax else cmax, fig)
    full_lo = float((data or {}).get("lo", lo))
    full_hi = float((data or {}).get("hi", hi))
    span = max(abs(full_hi - full_lo), 1.0)
    eps = span * 1e-9
    is_full = lo <= full_lo + eps and hi >= full_hi - eps
    fig = build_mini_histogram(values, [lo, hi], color="#0f766e",
                               x_range=(full_lo, full_hi))
    cmin = None if is_full else _input_number(lo)
    cmax = None if is_full else _input_number(hi)
    return (no_update if cmin == current_cmin else cmin,
            no_update if cmax == current_cmax else cmax, fig)


@app.callback(
    Output("config-order-list", "children"),
    Input("filter-configs", "options"),
)
def render_config_order_list(options):
    children = []
    for opt in options or []:
        value = opt.get("value")
        label = opt.get("label", value)
        children.append(html.Li(
            str(label), draggable="true", **{"data-cfg": value},
            style={
                "cursor": "grab", "padding": "2px 4px", "marginBottom": "2px",
                "border": "1px solid #dde2ee", "borderRadius": "3px",
                "background": "#fff", "lineHeight": "1.2",
            }))
    return children


@app.callback(
    Output("filter-configs", "options", allow_duplicate=True),
    Output("btn-plot", "n_clicks", allow_duplicate=True),
    Input("config-order-store", "data"),
    State("filter-configs", "options"),
    State("btn-plot", "n_clicks"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)
def apply_config_order(order_data, options, clicks, pattern):
    order = (order_data or {}).get("order") or []
    if not order or not options:
        return no_update, no_update
    rank = {str(v): i for i, v in enumerate(order)}
    _USER_CONFIG_ORDER.clear()
    _CONFIG_ORDER.clear()
    for cfg in order:
        _USER_CONFIG_ORDER.setdefault(str(cfg), len(_USER_CONFIG_ORDER))
        _CONFIG_ORDER.setdefault(str(cfg), len(_CONFIG_ORDER))

    def key(opt):
        value = str(opt.get("value"))
        return (rank.get(value, 10**9), humanise_config(value).lower(), value)

    return sorted(options, key=key), (clicks or 0) + 1 if pattern else no_update


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
    # Loading a dataset refreshes auto-threshold *suggestions*.  With both
    # switches off that must not issue a second btn-plot click in parallel with
    # the range-control load barrier; doing so used to start two identical
    # master renders for epoch 1.  A user toggle still replots, and a new
    # suggestion replots when either automatic cut is actually enabled.
    should_bump = bool(pattern) and (
        ctx.triggered_id != "auto-thresholds" or _on(vel_auto) or _on(disp_auto)
    )
    bump = (clicks or 0) + 1 if should_bump else no_update
    return vel_val, _on(vel_auto), disp_val, _on(disp_auto), bump


def _selected_range(sel):
    return _numeric_range(sel)


def _value_range(value_min, value_max):
    def val(x):
        if x in (None, ""):
            return None
        try:
            return float(x)
        except Exception:
            return None

    lo, hi = val(value_min), val(value_max)
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return (lo, hi)


def _trial_range(trial_min, trial_max):
    return _value_range(trial_min, trial_max)


def _range_slider_to_open_bounds(value, full_min, full_max):
    rng = _numeric_range(value)
    if rng is None:
        return no_update, no_update
    try:
        full_min = float(full_min)
        full_max = float(full_max)
    except Exception:
        full_min, full_max = rng
    span = max(abs(full_max - full_min), 1.0)
    eps = span * 1e-9
    lo = None if rng[0] <= full_min + eps else _input_number(rng[0])
    hi = None if rng[1] >= full_max - eps else _input_number(rng[1])
    return lo, hi


def _animal_count(df) -> int:
    if df is None or len(df) == 0:
        return 0
    cols = [c for c in ("FlyID", "VR") if c in df.columns]
    if not cols:
        return 0
    return int(df[cols].drop_duplicates().shape[0])


def _retention_counts(df, cache=None) -> dict[str, int]:
    key = (id(df), int(len(df)) if df is not None else 0)
    if cache is not None and key in cache:
        return cache[key]
    out = {
        "points": int(len(df)) if df is not None else 0,
        "trials": int(df["_seg_id"].nunique()) if df is not None and "_seg_id" in df else 0,
        "animals": _animal_count(df),
    }
    if cache is not None:
        cache[key] = out
    return out


def _pct(part, total) -> str:
    if not total:
        return "0.0%"
    return f"{100.0 * float(part) / float(total):.1f}%"


def _counts_phrase(c: dict[str, int]) -> str:
    return (f"{_compact_count(c['points'])} pts, "
            f"{_compact_count(c['trials'])} trials, "
            f"{_compact_count(c['animals'])} animals")


def _retention_summary(df_all, df_final) -> str:
    base = _retention_counts(df_all)
    final = _retention_counts(df_final)
    discarded = {k: max(0, base[k] - final[k]) for k in base}
    return (
        f"Retained {_compact_count(final['points'])}/{_compact_count(base['points'])} pts "
        f"({_pct(final['points'], base['points'])}); "
        f"{_compact_count(final['trials'])}/{_compact_count(base['trials'])} trials "
        f"({_pct(final['trials'], base['trials'])}); "
        f"{_compact_count(final['animals'])}/{_compact_count(base['animals'])} animals "
        f"({_pct(final['animals'], base['animals'])}). "
        f"Discarded {_counts_phrase(discarded)}."
    )


def _filter_stage_row(label: str, before, after, active=True,
                      note: str | None = None, counts_cache=None):
    b = _retention_counts(before, counts_cache)
    a = _retention_counts(after, counts_cache)
    d = {k: max(0, b[k] - a[k]) for k in b}
    status = "active" if active else "inactive"
    return html.Div([
        html.Div([
            html.Strong(label),
            html.Span(status, className=f"filter-stage-status {status}"),
        ], className="filter-stage-head"),
        html.Div(
            f"Retained {_counts_phrase(a)} "
            f"({ _pct(a['points'], b['points']) } pts, "
            f"{ _pct(a['trials'], b['trials']) } trials, "
            f"{ _pct(a['animals'], b['animals']) } animals).",
            className="filter-stage-line"),
        html.Div(f"Discarded {_counts_phrase(d)}.", className="filter-stage-line"),
        html.Div(note, className="filter-stage-note") if note else None,
    ], className="filter-stage")


def _filter_detail_children(df_all, vel_thresh, min_disp, trim, jump_buf,
                            cfg, vrs, fids, scenes, folders,
                            vel_sel, disp_sel, trial_min=None, trial_max=None,
                            step_min=None, step_max=None,
                            pattern=None,
                            roi_reach=None, roi_entered=None, roi_trim=None):
    if df_all is None or len(df_all) == 0:
        return "Load data to see retention accounting."
    counts_cache = {}
    rows = [
        html.Div("Serial accounting: each retained/discarded percentage is relative to the previous step.",
                 className="filter-detail-note")
    ]
    cur = df_all

    before = cur
    cur = td_grouping.subset_frame(df_all, configs=cfg, vrs=vrs, fly_ids=fids,
                                   scenes=scenes, folders=folders)
    rows.append(_filter_stage_row(
        "Subset selections", before, cur,
        active=bool(cfg or vrs or fids or scenes or folders),
        note="Config, VR, fly, scene, and folder selectors.",
        counts_cache=counts_cache))

    trng = _trial_range(trial_min, trial_max)
    before = cur
    if trng:
        cur = td_grouping.subset_frame(cur, trial_range=trng)
    if trng:
        lo, hi = trng
        if lo is None:
            trial_note = f"Keeps CurrentTrial <= {hi:g}."
        elif hi is None:
            trial_note = f"Keeps CurrentTrial >= {lo:g}."
        else:
            trial_note = f"Keeps CurrentTrial {lo:g} to {hi:g}, inclusive."
    else:
        trial_note = None
    rows.append(_filter_stage_row(
        "Trial range", before, cur, active=bool(trng),
        note=trial_note,
        counts_cache=counts_cache))

    srng = _value_range(step_min, step_max)
    before = cur
    if srng:
        cur = td_grouping.subset_frame(cur, step_range=srng)
    if srng:
        lo, hi = srng
        if lo is None:
            step_note = f"Keeps CurrentStep <= {hi:g}."
        elif hi is None:
            step_note = f"Keeps CurrentStep >= {lo:g}."
        else:
            step_note = f"Keeps CurrentStep {lo:g} to {hi:g}, inclusive."
    else:
        step_note = None
    rows.append(_filter_stage_row(
        "Step range", before, cur, active=bool(srng), note=step_note,
        counts_cache=counts_cache))

    disp_raw_rng = _selected_range(disp_sel)
    vel_raw_rng = _selected_range(vel_sel)
    subset_stats = compute_segment_stats(cur) if (disp_raw_rng or vel_raw_rng) else None
    disp_rng = _active_stat_range(disp_raw_rng, subset_stats, "displacement")
    vel_rng = _active_stat_range(vel_raw_rng, subset_stats, "peak_velocity")
    before = cur
    if disp_rng:
        cur = filter_by_stat_range(cur, subset_stats, "displacement", *disp_rng)
    rows.append(_filter_stage_row(
        "Displacement range", before, cur, active=bool(disp_rng),
        note=f"Range {disp_rng[0]:.3g} to {disp_rng[1]:.3g}." if disp_rng else None,
        counts_cache=counts_cache))

    before = cur
    if vel_rng:
        cur = filter_by_stat_range(cur, subset_stats, "peak_velocity", *vel_rng)
    rows.append(_filter_stage_row(
        "Peak velocity range", before, cur, active=bool(vel_rng),
        note=f"Range {vel_rng[0]:.3g} to {vel_rng[1]:.3g} units/s." if vel_rng else None,
        counts_cache=counts_cache))

    before = cur
    if vel_thresh is not None and vel_thresh > 0 and len(cur):
        vel = velocity_all(cur)
        spikes = np.nan_to_num(vel, nan=0.0) > float(vel_thresh)
        if spikes.any():
            seg = cur["_seg_id"].to_numpy()
            t = cur["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
            cur = cur[_dilate_keep(seg, t, spikes, _jump_buffer_seconds(jump_buf))]
    rows.append(_filter_stage_row(
        "Max velocity", before, cur, active=bool(vel_thresh is not None and vel_thresh > 0),
        note=(f"Removes samples above {float(vel_thresh):g} units/s with "
              f"{_jump_buffer_seconds(jump_buf) * 1000:g} ms buffer.")
        if vel_thresh is not None and vel_thresh > 0 else None,
        counts_cache=counts_cache))

    before = cur
    if min_disp is not None and min_disp > 0 and len(cur):
        grouped = cur.groupby("_seg_id", sort=False)
        x0 = grouped["GameObjectPosX"].transform("first")
        z0 = grouped["GameObjectPosZ"].transform("first")
        x1 = grouped["GameObjectPosX"].transform("last")
        z1 = grouped["GameObjectPosZ"].transform("last")
        displacement = np.hypot(x1 - x0, z1 - z0)
        cur = cur[displacement >= float(min_disp)]
    rows.append(_filter_stage_row(
        "Min displacement", before, cur, active=bool(min_disp is not None and min_disp > 0),
        note=f"Keeps trials with displacement >= {float(min_disp):g}." if min_disp is not None and min_disp > 0 else None,
        counts_cache=counts_cache))

    before = cur
    if trim is not None and trim > 0 and len(cur):
        grouped = cur.groupby("_seg_id", sort=False)
        pos = grouped.cumcount()
        size = grouped["_seg_id"].transform("size")
        trim_n = int(trim)
        cur = cur[(pos >= trim_n) & (pos < size - trim_n)]
    rows.append(_filter_stage_row(
        "Edge trim", before, cur, active=bool(trim is not None and trim > 0),
        note=f"Removes {int(trim)} samples from both ends of each trial." if trim is not None and trim > 0 else None,
        counts_cache=counts_cache))

    reach = float(roi_reach) if roi_reach else 3.0
    if pattern:
        roi_base = cur
        _table, entered_ids, trim_keep, _rois = _roi_masks(roi_base, pattern, reach)
        trim_series = pd.Series(trim_keep, index=roi_base.index)
        before = cur
        if _on(roi_entered) and len(cur):
            cur = cur[cur["_seg_id"].isin(entered_ids)]
        rows.append(_filter_stage_row(
            "ROI entered only", before, cur, active=_on(roi_entered),
            note="Keeps whole trials that enter any left/right target ROI.",
            counts_cache=counts_cache))

        before = cur
        if _on(roi_trim) and len(cur):
            cur = cur[trim_series.loc[cur.index].to_numpy()]
        rows.append(_filter_stage_row(
            "Trim after ROI exit", before, cur, active=_on(roi_trim),
            note="Keeps the approach and first post-entry exit; drops later tail samples.",
            counts_cache=counts_cache))

    return rows


@app.callback(
    Output("exclusion-info", "children", allow_duplicate=True),
    Output("filter-detail", "children"),
    Input("view-render-state", "data"),
    State("store-glob", "data"),
    State("roi-entered", "value"),
    State("roi-trim", "value"),
    State("roi-reach", "value"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("trial-min", "value"),
    State("trial-max", "value"),
    State("step-min", "value"),
    State("step-max", "value"),
    State("vel-range", "value"),
    State("disp-range", "value"),
    prevent_initial_call=True,
)
def update_filter_summary(render_state, pattern, roi_entered, roi_trim, roi_reach,
                          vel_thresh, min_disp, trim, jump_buf,
                          cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                          step_min, step_max, vel_sel, disp_sel):
    if not pattern or not render_state:
        return no_update, no_update
    df_all, _, _ = _load_data(pattern)
    df_f, df_sub, _ = _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                                   cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                                   step_min, step_max, vel_sel, disp_sel)
    if df_all is None or df_f is None or len(df_f) == 0:
        return "", _filter_detail_children(df_all, vel_thresh, min_disp, trim,
                                           jump_buf, cfg, vrs, fids, scenes,
                                           folders, vel_sel, disp_sel,
                                           trial_min, trial_max, step_min, step_max,
                                           pattern, roi_reach, roi_entered,
                                           roi_trim)
    reach = float(roi_reach) if roi_reach else 3.0
    df_view, _ = _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
    return (
        _retention_summary(df_all, df_view),
        _filter_detail_children(df_all, vel_thresh, min_disp, trim, jump_buf,
                                cfg, vrs, fids, scenes, folders,
                                vel_sel, disp_sel, trial_min, trial_max,
                                step_min, step_max,
                                pattern, roi_reach, roi_entered, roi_trim),
    )


_FILTER_CACHE: dict = {}        # signature -> (df_f, df_sub, optional stats_sub)
_FILTER_CACHE_ORDER: list = []
_FILTER_CACHE_MAX = 4
def _filter_signature(pattern, vel_thresh, min_disp, trim, jump_buf,
                      cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                      step_min, step_max, vel_selection, disp_selection):
    def rng(sel):
        return _selected_range(sel)
    def lst(v):
        return tuple(sorted(v)) if v else None
    pkey = _pattern_key(pattern)
    return (pkey, _DATA_TOKEN_BY_PATTERN.get(pkey),
            vel_thresh, min_disp, trim, round(_jump_buffer_seconds(jump_buf), 6),
            lst(cfg), lst(vrs), lst(fids), lst(scenes), lst(folders),
            _trial_range(trial_min, trial_max),
            _value_range(step_min, step_max),
            rng(vel_selection), rng(disp_selection))


def _filtered_df_locked(pattern, vel_thresh, min_disp, trim, jump_buf,
                        cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                        step_min, step_max, vel_selection, disp_selection,
                        need_stats=False):
    """
    Shared filtering pipeline (cached). Returns (df_f, df_sub, stats_sub|None).

    Caching makes heatmap-only changes (lin/log, metric, bins, percentile)
    cheap — they reuse the already-filtered frame instead of re-running the
    full velocity/displacement/trim pipeline. Segment stats are optional because
    most plot views only need rows; export can upgrade a cached result on demand.
    """
    df, stats, _ = _load_data(pattern)
    if df is None or len(df) == 0:
        return None, None, None
    sig = _filter_signature(pattern, vel_thresh, min_disp, trim, jump_buf,
                            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                            step_min, step_max, vel_selection, disp_selection)
    if sig in _FILTER_CACHE:
        result = _FILTER_CACHE[sig]
        if need_stats and result[2] is None and result[1] is not None:
            result = (result[0], result[1], compute_segment_stats(result[1]))
            _FILTER_CACHE[sig] = result
        return result

    vel_rng = _active_stat_range(_selected_range(vel_selection), stats, "peak_velocity")
    disp_rng = _active_stat_range(_selected_range(disp_selection), stats, "displacement")

    spec = td_grouping.FilterSpec(
        vel_threshold=vel_thresh,
        min_displacement=min_disp,
        edge_trim_samples=trim or 0,
        jump_buffer_ms=jump_buf,
        configs=tuple(cfg) if cfg else None,
        vrs=tuple(vrs) if vrs else None,
        fly_ids=tuple(fids) if fids else None,
        scenes=tuple(scenes) if scenes else None,
        folders=tuple(folders) if folders else None,
        trial_range=_trial_range(trial_min, trial_max),
        step_range=_value_range(step_min, step_max),
        velocity_range=vel_rng,
        displacement_range=disp_rng,
    )
    filtered = td_grouping.filter_frame(df, spec, stats, compute_stats=need_stats)
    result = (filtered.filtered, filtered.subset, filtered.stats)
    if result[0] is not None:
        result[0].attrs["_frame_token"] = ("filtered", sig, int(len(result[0])))
    if result[1] is not None:
        result[1].attrs["_frame_token"] = ("subset", sig, int(len(result[1])))

    _FILTER_CACHE[sig] = result
    _FILTER_CACHE_ORDER.append(sig)
    if len(_FILTER_CACHE_ORDER) > _FILTER_CACHE_MAX:
        old = _FILTER_CACHE_ORDER.pop(0)
        _FILTER_CACHE.pop(old, None)
    return result


def _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                 cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                 step_min, step_max, vel_selection, disp_selection,
                 need_stats=False):
    """Single-flight wrapper around the shared vectorised filter pipeline."""
    with _FILTER_LOCK:
        return _filtered_df_locked(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection,
            need_stats=need_stats)


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
    Output("heatmap-figure-store", "data", allow_duplicate=True),
    Output("heatmap-variants", "data", allow_duplicate=True),
    Output("roi-plot", "figure", allow_duplicate=True),
    Output("polar-plot", "figure", allow_duplicate=True),
    Output("raw-trace-plot", "figure"),
    Output("raw-trace-wrap", "style"),
    Output("data-summary", "children"),
    Output("vel-histogram", "figure", allow_duplicate=True),
    Output("disp-histogram", "figure", allow_duplicate=True),
    Output("exclusion-info", "children"),
    Output("view-render-state", "data", allow_duplicate=True),
    Output("plot-status", "children", allow_duplicate=True),
    Input("btn-plot", "n_clicks"),
    State("data-generation", "data"),
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
    State("trial-min", "value"),
    State("trial-max", "value"),
    State("step-min", "value"),
    State("step-max", "value"),
    State("raw-columns", "value"),
    State("subplot-ncols", "value"),
    State("plot-points", "value"),
    State("render-mode", "value"),
    State("vel-range", "value"),
    State("disp-range", "value"),
    State("viewport-store", "data"),
    State("roi-show", "value"),
    State("roi-reach", "value"),
    State("roi-trim", "value"),
    State("roi-entered", "value"),
    State("polar-moving", "value"),
    State("polar-walk", "value"),
    State("polar-angle-source", "value"),
    State("polar-r-range", "value"),
    State("polar-min-point-frac", "value"),
    State("polar-min-animal-frac", "value"),
    prevent_initial_call=True,
)
def update_plots(n, generation, pattern, vel_thresh, min_disp, trim, jump_buf,
                 group_by, pool_mode, color_by, animate, rebase, hm_binsize, hm_scale,
                 hm_bound, hm_metric, hm_cmin, hm_cmax, hm_crange, cfg, vrs, fids,
                 scenes, folders, trial_min, trial_max, step_min, step_max,
                 raw_cols, ncols, max_points,
                 render_mode, vel_selection, disp_selection, viewport, roi_show, roi_reach,
                 roi_trim, roi_entered, polar_moving, polar_walk, polar_angle_source,
                 polar_r_range,
                 polar_min_point_frac, polar_min_animal_frac):
    empty = go.Figure().update_layout(height=400, template="plotly_white")
    raw_hidden = {"display": "none"}
    if not pattern:
        return (empty, empty, {}, empty, empty, empty, raw_hidden,
                "Choose a data folder or CSV glob to begin.",
                no_update, no_update, "", {}, "Waiting for data.")

    started = time.perf_counter()
    stage_started = started
    timings = {}
    mode = _render_mode(render_mode)
    LOGGER.info(
        "render.start epoch=%s mode=%s group=%s pool=%s source=%r",
        int(n or 0), mode, group_by, pool_mode, pattern,
    )
    # Fresh layouts briefly expose [0, 1] slider placeholders before the real
    # dataset bounds arrive. A load-generation render must never interpret
    # those placeholders as intentional filters.
    if generation:
        if _numeric_range(vel_selection) == (0.0, 1.0):
            vel_selection = None
        if _numeric_range(disp_selection) == (0.0, 1.0):
            disp_selection = None
    df_f, df_sub, _stats_sub = _filtered_df(
        pattern, vel_thresh, min_disp, trim, jump_buf,
        cfg, vrs, fids, scenes, folders, trial_min, trial_max,
        step_min, step_max, vel_selection, disp_selection)
    timings["filter/cache"] = time.perf_counter() - stage_started

    if df_sub is None:
        msg = "No CSV rows matched the current data source."
        LOGGER.warning("render.empty epoch=%s reason=no_rows source=%r", n, pattern)
        return (empty, empty, {}, empty, empty, empty, raw_hidden, msg,
                no_update, no_update, "", {"epoch": int(n or 0)}, msg)
    if len(df_sub) == 0:
        msg = "No trajectories match the active filters."
        LOGGER.warning("render.empty epoch=%s reason=filters source=%r", n, pattern)
        return (empty, empty, {}, empty, empty, empty, raw_hidden, msg,
                no_update, no_update, "", {"epoch": int(n or 0)}, msg)

    df, _, metas = _load_data(pattern)
    ncols_val = int(ncols) if ncols and ncols >= 1 else 2
    do_animate = bool(animate) and "on" in (animate or [])
    do_rebase = bool(rebase) and "on" in (rebase or [])

    # ROI masking + counts. The reached table is built from the UNMASKED
    # filtered data; trajectory corner labels intersect it with each subplot's
    # visible segments, while the ROI tab uses the unmasked table. df_view is
    # what trajectory/heatmap/raw draw: optionally restricted to whole trials
    # that entered an ROI, then tail-trimmed.
    stage_started = time.perf_counter()
    rois = rois_by_config(metas)
    reach = float(roi_reach) if roi_reach else 3.0
    want_rois = _on(roi_show) and bool(rois)
    df_view, table = _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
    exclusion = _retention_summary(df, df_view)
    roi_counts = table if (want_rois and table is not None) else None
    roi_outcomes = (roi_outcome_by_segment(df_view, rois, reach)
                    if (color_by == "roi" or want_rois) and rois else None)
    if want_rois and table is not None:
        roi_fig = build_roi_swarm_figure(df_view, rois, reach, table=table)
    elif rois:
        roi_fig = _msg_figure("Enable target ROIs to view target diagnostics.")
    else:
        roi_fig = _msg_figure("No target ROIs were found for the current configs.")
    timings["ROI masks/diagnostics"] = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    df_plot = rebase_to_origin(df_view) if do_rebase else df_view
    bound_pct = float(hm_bound) if hm_bound not in (None, "") else 98.0
    shared_fit = ((_robust_range(df_plot, bound_pct)
                   if bound_pct < 100 else _shared_range(df_plot))
                  if len(df_plot) else None)
    traj_budget = _budget(BUDGET_SVG if do_animate else BUDGET_GL,
                          BUDGET_SVG_SPEED if do_animate else BUDGET_GL_SPEED,
                          mode, max_points)
    df_plot_draw = _decimate_frame(df_plot, traj_budget) if mode == "speed" else df_plot
    traj_max_points = len(df_plot_draw) if mode == "speed" else max_points
    traj_fig = build_trajectory_figure(
        df_plot_draw, group_by, pool_mode, ncols=ncols_val,
        color_by=color_by or "individual", animate=do_animate,
        max_points=traj_max_points, rois=rois, reach_radius=reach,
        show_rois=want_rois and not do_rebase, roi_counts=roi_counts,
        roi_outcomes=roi_outcomes, view_range=shared_fit)
    _apply_viewport(traj_fig, viewport, df_plot_draw)
    timings["trajectory"] = time.perf_counter() - stage_started

    # Heatmap binning and analytical panels always use the complete filtered
    # frame. Speed mode only limits browser primitives; it never changes counts,
    # circular statistics, residence time, or diagnostic distributions.
    stage_started = time.perf_counter()
    heat_fig, heat_variants = build_heatmap_mask_variants(
        df_f, pattern, reach, group_by, pool_mode, ncols_val,
        bin_size=hm_binsize, bound_pct=bound_pct,
        cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange,
        do_rebase=do_rebase, entered_only=_on(roi_entered),
        trim_tail=_on(roi_trim), max_points=None,
        metric=hm_metric or "time", log_scale=(hm_scale == "log"))
    _apply_viewport_to_current_range(heat_fig, viewport, max_span_mult=1.5)
    timings["heatmap"] = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    polar_budget = _budget(BUDGET_POLAR, BUDGET_POLAR_SPEED, mode, max_points)
    polar_fig, polar_quality = build_polar_figure(
        df_view, group_by, pool_mode, ncols=ncols_val,
        color_by=color_by or "individual", moving_only=_on(polar_moving),
        walk_thresh=polar_walk, max_points=polar_budget, rois=rois,
        reach_radius=reach, show_rois=want_rois and not do_rebase,
        roi_outcomes=roi_outcomes, r_range=polar_r_range,
        min_point_frac=polar_min_point_frac,
        min_animal_trial_frac=polar_min_animal_frac,
        return_summary=True, angle_source=polar_angle_source)
    timings["polar"] = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    raw_style = {"display": "block"} if raw_cols else raw_hidden
    raw_fig = (build_raw_trace_figure(
        df_view, raw_cols or [],
        max_points=_budget(BUDGET_RAW, BUDGET_RAW_SPEED, mode, max_points))
        if raw_cols else empty)
    vel_fig = build_velocity_histogram(df_view, vel_thresh)
    disp_fig = build_displacement_histogram(compute_segment_stats(df_view), min_disp)
    timings["diagnostic plots"] = time.perf_counter() - stage_started

    drawn = sum(len(t.x) for t in traj_fig.data
                if getattr(t, "x", None) is not None)
    n_frames = len(traj_fig.frames)
    n_traces = int(df_view["_seg_id"].nunique()) if len(df_view) else 0
    n_segs_before = df_sub["_seg_id"].nunique()
    bt = time.perf_counter() - started
    timings["total"] = bt
    summary = (f"{_compact_count(len(df_view))}/{_compact_count(len(df_sub))} pts | "
               f"{_compact_count(n_traces)}/{_compact_count(n_segs_before)} segs | "
               f"trajectory ~{drawn:,}/{traj_budget:,} drawn pts | "
               f"polar {polar_quality.get('after_animal', 0):,} trials | "
               f"{n_frames} frames | all sections {bt:.2f}s | colour: {color_by}")
    if mode == "speed":
        summary += " | Speed mode"

    LOGGER.info(
        "render.done epoch=%s mode=%s input_rows=%d visible_rows=%d "
        "segments=%d drawn_points=%d polar_trials=%d seconds=%.3f",
        int(n or 0), mode, len(df_sub), len(df_view), n_traces, drawn,
        int(polar_quality.get("after_animal", 0)), bt,
    )

    render_state = {
        "epoch": int(n or 0), "data": _DATA_TOKEN_BY_PATTERN.get(_pattern_key(pattern)),
        "mode": mode, "completed": time.time(),
        "timings": {k: round(float(v), 4) for k, v in timings.items()},
        "operation": "all sections",
    }
    return (traj_fig, heat_fig.to_plotly_json(), heat_variants, roi_fig, polar_fig,
            raw_fig, raw_style, summary, vel_fig, disp_fig, exclusion,
            render_state, f"Ready — all sections updated in {bt:.2f}s.")


# Polar quality controls are intentionally isolated from the atomic dashboard
# render above.  Moving-only, Rayleigh, and valid-fraction changes reuse the
# cached filtered frame and cached per-segment circular statistics, so they do
# not rebuild trajectories, heatmap bins, ROI diagnostics, or raw traces.
app.clientside_callback(
    "function(a,b,c,d,e,f,pattern){if(!pattern)return window.dash_clientside.no_update;"
    "return 'Updating polar filters…';}",
    Output("plot-status", "children", allow_duplicate=True),
    Input("polar-moving", "value"),
    Input("polar-walk", "value"),
    Input("polar-angle-source", "value"),
    Input("polar-r-range", "value"),
    Input("polar-min-point-frac", "value"),
    Input("polar-min-animal-frac", "value"),
    State("store-glob", "data"),
    prevent_initial_call=True,
)


@app.callback(
    Output("polar-plot", "figure", allow_duplicate=True),
    Output("polar-r-hist", "figure"),
    Output("polar-point-frac-hist", "figure"),
    Output("polar-animal-frac-hist", "figure"),
    Output("polar-render-state", "data"),
    Output("plot-status", "children", allow_duplicate=True),
    Output("data-summary", "children", allow_duplicate=True),
    Input("view-render-state", "data"),
    Input("polar-moving", "value"),
    Input("polar-walk", "value"),
    Input("polar-angle-source", "value"),
    Input("polar-r-range", "value"),
    Input("polar-min-point-frac", "value"),
    Input("polar-min-animal-frac", "value"),
    State("store-glob", "data"),
    State("vel-threshold", "value"),
    State("min-disp", "value"),
    State("trim-samples", "value"),
    State("jump-buffer", "value"),
    State("filter-configs", "value"),
    State("filter-vrs", "value"),
    State("filter-flyids", "value"),
    State("filter-scenes", "value"),
    State("filter-folders", "value"),
    State("trial-min", "value"),
    State("trial-max", "value"),
    State("step-min", "value"),
    State("step-max", "value"),
    State("vel-range", "value"),
    State("disp-range", "value"),
    State("group-by", "value"),
    State("pool-mode", "value"),
    State("color-by", "value"),
    State("subplot-ncols", "value"),
    State("plot-points", "value"),
    State("render-mode", "value"),
    State("rebase-origin", "value"),
    State("roi-show", "value"),
    State("roi-reach", "value"),
    State("roi-entered", "value"),
    State("roi-trim", "value"),
    State("data-summary", "children"),
    prevent_initial_call=True,
)
def update_polar_only(render_state, polar_moving, polar_walk, polar_angle_source,
                      polar_r_range, polar_min_point_frac,
                      polar_min_animal_frac, pattern, vel_thresh, min_disp, trim,
                      jump_buf, cfg, vrs, fids, scenes, folders, trial_min,
                      trial_max, step_min, step_max, vel_selection,
                      disp_selection, group_by, pool_mode, color_by, ncols,
                      max_points, render_mode, rebase, roi_show, roi_reach,
                      roi_entered, roi_trim, current_summary):
    empty_hists = build_polar_quality_histograms(None, polar_r_range,
                                                  polar_min_point_frac,
                                                  polar_min_animal_frac)
    if not pattern or not render_state:
        return no_update, *empty_hists, no_update, no_update, no_update

    trigger = ctx.triggered_id
    refresh_figure = trigger != "view-render-state"
    started = time.perf_counter()
    stage_started = started
    timings = {}
    LOGGER.info(
        "polar.start trigger=%s moving=%s walk=%s angle=%s source=%r",
        trigger, _on(polar_moving), polar_walk, polar_angle_source, pattern,
    )
    df_f, _, _ = _filtered_df(
        pattern, vel_thresh, min_disp, trim, jump_buf,
        cfg, vrs, fids, scenes, folders, trial_min, trial_max,
        step_min, step_max, vel_selection, disp_selection)
    timings["filter/cache"] = time.perf_counter() - stage_started
    if df_f is None or len(df_f) == 0:
        msg = _msg_figure("No trajectories match the active filters.")
        return (msg if refresh_figure else no_update), *empty_hists, no_update, (
            "Polar update skipped — no rows match the active filters."
            if refresh_figure else no_update), no_update

    stage_started = time.perf_counter()
    _, _, metas = _load_data(pattern)
    rois = rois_by_config(metas)
    reach = float(roi_reach) if roi_reach else 3.0
    df_view, _ = _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
    ray_metric = color_by if color_by in ("velocity", "tortuosity") else "none"
    ray = rayleigh_by_segment(
        df_view, _on(polar_moving), polar_walk, ray_metric,
        angle_source=polar_angle_source)
    timings["ray aggregation"] = time.perf_counter() - stage_started

    stage_started = time.perf_counter()
    hists = build_polar_quality_histograms(
        ray, polar_r_range, polar_min_point_frac, polar_min_animal_frac)
    polar_fig = no_update
    quality = _filter_polar_ray_table(
        ray, polar_r_range, polar_min_point_frac, polar_min_animal_frac)[1]
    if refresh_figure:
        ncols_val = int(ncols) if ncols and ncols >= 1 else 2
        mode = _render_mode(render_mode)
        want_rois = _on(roi_show) and bool(rois)
        roi_outcomes = (roi_outcome_by_segment(df_view, rois, reach)
                        if (color_by == "roi" or want_rois) and rois else None)
        polar_fig, quality = build_polar_figure(
            df_view, group_by, pool_mode, ncols=ncols_val,
            color_by=color_by or "individual",
            moving_only=_on(polar_moving), walk_thresh=polar_walk,
            max_points=_budget(BUDGET_POLAR, BUDGET_POLAR_SPEED, mode, max_points),
            rois=rois, reach_radius=reach,
            show_rois=want_rois and not _on(rebase), roi_outcomes=roi_outcomes,
            r_range=polar_r_range, min_point_frac=polar_min_point_frac,
            min_animal_trial_frac=polar_min_animal_frac,
            return_summary=True, angle_source=polar_angle_source)
    timings["figure/histograms"] = time.perf_counter() - stage_started
    elapsed = time.perf_counter() - started
    timings["total"] = elapsed

    if not refresh_figure:
        LOGGER.info("polar.histograms trials=%d seconds=%.3f",
                    int(quality.get("after_animal", 0)), elapsed)
        return no_update, *hists, no_update, no_update, no_update

    state = {
        "completed": time.time(), "operation": "polar filters",
        "timings": {k: round(float(v), 4) for k, v in timings.items()},
        "trigger": str(trigger),
    }
    kept = int(quality.get("after_animal", 0))
    LOGGER.info("polar.done trigger=%s trials=%d seconds=%.3f",
                trigger, kept, elapsed)
    summary_out = (re.sub(r"polar [\d,]+ trials", f"polar {kept:,} trials",
                          current_summary)
                   if isinstance(current_summary, str) else no_update)
    return (polar_fig, *hists, state,
            f"Ready — polar filters kept {kept:,} trials in {elapsed:.2f}s.",
            summary_out)


# Attach a debounced Plotly relayout listener directly to the visible
# trajectory graph. This avoids feeding every drag/wheel event through Dash's
# `relayoutData` callback machinery while the gesture is in progress.
app.clientside_callback(
    "function(fig){setTimeout(function(){"
    "var g=document.querySelector('#trajectory-plot .js-plotly-plot');"
    "if(g&&window.__attachViewportSync){window.__attachViewportSync(g,'traj');}"
    "},120);return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("trajectory-plot", "figure"),
    prevent_initial_call=True,
)


# The heatmap uses a 1:1 aspect lock (scaleanchor). Dash's Plotly.react update
# path crashes on that with "axis scaling" when the figure is applied to a graph
# that isn't at full size yet, and never recovers — so the heatmap stays blank.
# A fresh Plotly.newPlot re-initialises cleanly and renders.
#
# Fingerprint the structural content so metric/scale restyles never trigger a
# second newPlot. Section navigation is intentionally absent from this callback.
app.clientside_callback(
    "function(hfig, metric, scale, entered, trim, variants){setTimeout(function(){"
    "var hc=document.getElementById('heatmap-plot');"
    "var hg=hc&&hc.querySelector('.js-plotly-plot');"
    "var fp='';try{var L=(hfig&&hfig.layout)||{};"
    # Fingerprint tracks BINNING/structure only (trace count, z/x dimensions,
    # height, axis ranges) — NOT zmin/zmax, which are colouring that the client
    # restyles in place. Including them made a metric/scale swap look like a
    # structural change (dcc.Graph syncs restyled zmin back to the prop) and
    # triggered a needless newPlot flash on the *next* swap.
    "fp=JSON.stringify((hfig&&hfig.data||[]).map(function(t){"
    "return [t.type,(t.z&&t.z.length)||0,(t.x&&t.x.length)||0];}))"
    "+'|'+JSON.stringify((L.shapes||[]).map(function(s){return [s.x0,s.x1,s.y0,s.y1,s.xref,s.yref];}))"
    "+'|'+((L.annotations||[]).length)"
    "+'|'+(L.height||0)+'|'+JSON.stringify(L.xaxis&&L.xaxis.range)"
    "+'|'+JSON.stringify(L.yaxis&&L.yaxis.range);}catch(e){}"
    "if(hg&&window.Plotly&&hfig&&hfig.data&&hfig.data.length){"
    "var changed=hg.__hmfp!==fp;"
    "var needPaint=changed||!hg.__hmPainted;"
    "if(needPaint){"
    "window.__hmSuppress=true;"
    "try{hc.style.transition='none';hc.style.opacity='1';}catch(e){}"
    "try{window.Plotly.newPlot(hg,hfig.data,hfig.layout,{scrollZoom:true,displayModeBar:true,displaylogo:false});"
    "hg.__hmfp=fp;hg.__hmPainted=true;"
    "if(window.__attachHeatSync){window.__attachHeatSync(hg,true);}}catch(e){}"
    "try{hc.style.opacity='1';}catch(e){}"
    "setTimeout(function(){window.__hmSuppress=false;},250);"
    "}"
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
    "window.Plotly.restyle(hg,{colorbar:[v.colorbar]},[0]);"
    "if(v.roi_texts&&hg.layout&&hg.layout.annotations){"
    "var j=0;var anns=hg.layout.annotations.map(function(a){"
    "var b=Object.assign({},a);if((b.name||'').indexOf('hm-roi')===0){b.text=v.roi_texts[j++]||'';}return b;});"
    "window.Plotly.relayout(hg,{annotations:anns});}"
    "}}catch(e){}}"
    "},90);return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("heatmap-figure-store", "data"),
    Input("heatmap-metric", "value"),
    Input("heatmap-scale", "value"),
    Input("roi-entered", "value"),
    Input("roi-trim", "value"),
    State("heatmap-variants", "data"),
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


def _compose_export_html(traj, heat, roi, polar, vel, disp, raw,
                         *, include_raw, summary, share_state):
    """Build one offline-capable report with a single embedded plotly.js."""
    cfgd = dict(scrollZoom=True, displaylogo=False)
    traj_h = traj.to_html(full_html=False, include_plotlyjs=True, config=cfgd)
    heat_h = heat.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
    roi_h = roi.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
    polar_h = polar.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
    vel_h = vel.to_html(full_html=False, include_plotlyjs=False)
    disp_h = disp.to_html(full_html=False, include_plotlyjs=False)
    raw_h = (raw.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
             if include_raw else "<p>No raw trace columns selected.</p>")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Trajectory Export</title>
<style>body{{font-family:system-ui,sans-serif;margin:18px;color:#222}}
h2{{margin:0 0 6px}} h3{{margin:18px 0 4px;font-size:14px;color:#555}}
.info{{background:#e9ecef;padding:8px;border-radius:4px;font-size:13px;margin:6px 0}}
.row{{display:flex;gap:10px}}.row>div{{flex:1;min-width:0}}
.share{{font-size:11px;color:#888;word-break:break-all}}
.credit{{font-size:12px;margin-top:22px;color:#667085}}
.credit a{{color:#2563eb;text-decoration:none;font-weight:650}}</style>
</head><body>
<h2>Trajectory Export</h2>
<div class="info">{summary or ''}</div>
<div class="share">State: <code>{share_state or ''}</code></div>
<h3>Trajectories</h3>{traj_h}
<h3>Heatmap</h3>{heat_h}
<h3>Target diagnostics</h3>{roi_h}
<h3>Polar</h3>{polar_h}
<h3>Velocity / Displacement</h3><div class="row"><div>{vel_h}</div><div>{disp_h}</div></div>
<h3>Raw traces</h3>{raw_h}
<div class="credit"><a href="{REPO_URL}">❤️ by pvnkmrksk</a></div>
</body></html>"""


# Export — rebuild figures server-side so the HTML always embeds real data.
@app.callback(
    Output("download-html", "data"),
    Output("plot-status", "children", allow_duplicate=True),
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
    State("trial-min", "value"),
    State("trial-max", "value"),
    State("step-min", "value"),
    State("step-max", "value"),
    State("raw-columns", "value"),
    State("subplot-ncols", "value"),
    State("plot-points", "value"),
    State("render-mode", "value"),
    State("rebase-origin", "value"),
    State("roi-show", "value"),
    State("roi-reach", "value"),
    State("roi-entered", "value"),
    State("roi-trim", "value"),
    State("polar-r-range", "value"),
    State("polar-min-point-frac", "value"),
    State("polar-min-animal-frac", "value"),
    State("polar-moving", "value"),
    State("polar-walk", "value"),
    State("polar-angle-source", "value"),
    State("vel-range", "value"),
    State("disp-range", "value"),
    State("viewport-store", "data"),
    State("data-summary", "children"),
    State("url", "search"),
    prevent_initial_call=True,
)
def export_html(n, pattern, vel_thresh, min_disp, trim, jump_buf, group_by, pool_mode,
                color_by, animate, hm_binsize, hm_scale, hm_bound, hm_metric,
                hm_cmin, hm_cmax, hm_crange, cfg, vrs, fids, scenes, folders,
                trial_min, trial_max, step_min, step_max,
                raw_cols, ncols, max_points, render_mode,
                rebase, roi_show, roi_reach, roi_entered, roi_trim,
                polar_r_range, polar_min_point_frac, polar_min_animal_frac,
                polar_moving, polar_walk, polar_angle_source,
                vel_selection, disp_selection,
                viewport, summary, url_search):
    if not pattern:
        LOGGER.warning("export.rejected reason=missing_source")
        return no_update, "Load data before exporting."

    started = time.perf_counter()
    LOGGER.info("export.start mode=%s source=%r", _render_mode(render_mode), pattern)

    df_f, df_sub, _stats_sub = _filtered_df(
        pattern, vel_thresh, min_disp, trim, jump_buf,
        cfg, vrs, fids, scenes, folders, trial_min, trial_max,
        step_min, step_max, vel_selection, disp_selection, need_stats=True)
    if df_f is None or len(df_f) == 0:
        LOGGER.warning("export.rejected reason=no_filtered_rows source=%r", pattern)
        return no_update, "Export skipped — no rows match the active filters."

    ncols_val = int(ncols) if ncols and ncols >= 1 else 2
    do_animate = bool(animate) and "on" in (animate or [])
    do_rebase = bool(rebase) and "on" in (rebase or [])
    mode = _render_mode(render_mode)
    _, _, metas = _load_data(pattern)
    rois = rois_by_config(metas)
    reach = float(roi_reach) if roi_reach else 3.0
    df_view, table = _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
    df_plot = rebase_to_origin(df_view) if do_rebase else df_view
    want_rois = _on(roi_show) and bool(rois)
    roi_counts = table if (want_rois and table is not None) else None
    roi_outcomes = (roi_outcome_by_segment(df_view, rois, reach)
                    if (color_by == "roi" or want_rois) and rois else None)
    traj_budget = _budget(BUDGET_SVG if do_animate else BUDGET_GL,
                          BUDGET_SVG_SPEED if do_animate else BUDGET_GL_SPEED,
                          mode, max_points)
    df_traj = _decimate_frame(df_plot, traj_budget) if mode == "speed" else df_plot
    df_heat = df_plot
    df_polar = df_view
    bound_pct = float(hm_bound) if hm_bound not in (None, "") else 98.0
    shared_fit = ((_robust_range(df_plot, bound_pct)
                   if bound_pct < 100 else _shared_range(df_plot))
                  if len(df_plot) else None)
    traj = build_trajectory_figure(df_traj, group_by, pool_mode, ncols=ncols_val,
                                   color_by=color_by or "individual",
                                   animate=do_animate,
                                   max_points=len(df_traj) if mode == "speed" else max_points,
                                   rois=rois, reach_radius=reach,
                                   show_rois=want_rois and not do_rebase,
                                   roi_counts=roi_counts,
                                   roi_outcomes=roi_outcomes,
                                   view_range=shared_fit)
    heat = build_heatmap_figure(df_heat, group_by, pool_mode, ncols=ncols_val,
                                bin_size=hm_binsize, log_scale=(hm_scale == "log"),
                                bound_pct=bound_pct,
                                metric=hm_metric or "time",
                                cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange,
                                rois=rois if want_rois and not do_rebase else None,
                                reach_radius=reach)
    polar = build_polar_figure(
        df_polar, group_by, pool_mode, ncols=ncols_val,
        color_by=color_by or "individual",
        moving_only=_on(polar_moving), walk_thresh=polar_walk,
        max_points=_budget(BUDGET_POLAR, BUDGET_POLAR_SPEED, mode, max_points),
        rois=rois, reach_radius=reach, show_rois=want_rois and not do_rebase,
        roi_outcomes=roi_outcomes, r_range=polar_r_range,
        min_point_frac=polar_min_point_frac,
        min_animal_trial_frac=polar_min_animal_frac,
        angle_source=polar_angle_source)
    roi_fig = (build_roi_swarm_figure(df_view, rois, reach, table=table)
               if want_rois and table is not None
               else _msg_figure("No target diagnostics are available for this selection."))
    df_diag = df_view
    vel_fig = build_velocity_histogram(df_diag, vel_thresh)
    disp_fig = build_displacement_histogram(compute_segment_stats(df_diag), min_disp)
    raw = build_raw_trace_figure(
        df_view, raw_cols or [],
        max_points=_budget(BUDGET_RAW, BUDGET_RAW_SPEED, mode, max_points))

    if viewport and not viewport.get("reset"):
        for f in (traj, heat):
            if viewport.get("xaxis"):
                f.update_xaxes(range=viewport["xaxis"])
            if viewport.get("yaxis"):
                f.update_yaxes(range=viewport["yaxis"])

    content = _compose_export_html(
        traj, heat, roi_fig, polar, vel_fig, disp_fig, raw,
        include_raw=bool(raw_cols), summary=summary,
        share_state=url_search,
    )

    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"trajectory_export_{ts}.html"
    LOGGER.info(
        "export.done filename=%s bytes=%d rows=%d seconds=%.3f",
        filename, len(content.encode("utf-8")), len(df_view),
        time.perf_counter() - started,
    )
    return (dict(content=content, filename=filename),
            f"Export ready — {filename} ({len(content.encode('utf-8')) / 1_000_000:.1f} MB).")


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
    parser.add_argument(
        "--log-level", default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Terminal diagnostic verbosity (default: INFO).",
    )
    args = parser.parse_args()
    _configure_logging(args.log_level)

    LOGGER.info(
        "runtime python=%s pandas=%s numpy=%s pid=%d debug=%s",
        platform.python_version(), pd.__version__, np.__version__, os.getpid(), args.debug,
    )

    if args.glob:
        LOGGER.info("server.preload source=%r", args.glob)
        _load_data(args.glob)
        for child in app.layout.children:
            if hasattr(child, "id") and child.id == "url":
                child.search = "?" + urlencode({"glob": args.glob})
                break

    LOGGER.info("server.start url=http://%s:%d/", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=args.debug)
