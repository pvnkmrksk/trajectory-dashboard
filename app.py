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
import base64
import copy
import concurrent.futures
import glob
import json
import logging
import math
import os
import platform
import re
import struct
import threading
import time
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.colors as pcolors
from scipy import stats as scipy_stats
from plotly.subplots import make_subplots
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update
from flask import request

from trajectory_dashboard import grouping as td_grouping
from trajectory_dashboard import io as td_io
from trajectory_dashboard import ui_contract as td_ui

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
    progress = globals().get("_OP_PROGRESS", {})
    if progress.get("active"):
        globals().get("_progress_finish")(
            progress.get("id"),
            f"Error — {type(exc).__name__}: {exc}",
            failed=True,
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
_USER_GROUP_ORDERS: dict[str, dict[str, int]] = {}


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
    """Map ConfigFile (short name) to target ROIs across all loaded folders.

    Configs with no target objects inherit the modal target geometry found in
    the loaded metadata. This makes "none"/empty treatments use the experiment's
    representative targets instead of silently losing ROI diagnostics.
    """
    out: dict[str, list[dict]] = {}
    all_keys: list[str] = []
    geometry_samples: list[list[dict]] = []
    for m in metas or []:
        all_keys.extend(_short_config_name(v) for v in (m.get("sequence_order") or []))
        for fname, data in (m.get("configs") or {}).items():
            key = _short_config_name(fname)
            all_keys.append(key)
            rois = rois_from_config(data)
            if rois:
                geometry_samples.append(rois)
            if rois and key not in out:
                out[key] = rois
    signatures: dict[tuple, tuple[int, list[dict]]] = {}
    for rois in geometry_samples:
        sig = tuple(sorted(
            (
                round(float(r.get("x", 0.0)), 4),
                round(float(r.get("z", 0.0)), 4),
                str(r.get("side", "")),
                str(r.get("type", "")),
            )
            for r in rois
        ))
        count, _ = signatures.get(sig, (0, rois))
        signatures[sig] = (count + 1, rois)
    modal = (max(signatures.values(), key=lambda item: item[0])[1]
             if signatures else [])
    if modal:
        for key in dict.fromkeys(all_keys):
            if key and key not in out:
                out[key] = [dict(r, inferred=True, inferred_from="modal targets")
                            for r in modal]
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


def _ordered_group_values(vals, group_by="config") -> list:
    """Order values for the current panel axis, including user drag order."""
    vals = [v for v in vals if pd.notna(v)]
    if not vals:
        return []
    group_by = str(group_by or "config")
    user_order = _USER_GROUP_ORDERS.get(group_by) or {}
    if user_order:
        return sorted(
            vals,
            key=lambda value: (
                user_order.get(str(value), 10**9),
                _group_label(group_by, str(value)).lower(),
                str(value),
            ),
        )
    if group_by == "config" and _CONFIG_ORDER:
        return sorted(
            vals,
            key=lambda value: (
                _CONFIG_ORDER.get(str(value), 10**9),
                humanise_config(str(value)).lower(),
                str(value),
            ),
        )
    # Non-config groupings historically preserve load-time encounter order.
    # Keep that stable until the user explicitly drags a new order.
    return list(dict.fromkeys(vals))


def _ordered_values(vals) -> list:
    """Backward-compatible config ordering helper."""
    return _ordered_group_values(vals, "config")


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
    n = int(len(df))
    if n == 0:
        return ("empty", token, tuple(df.columns))
    idx = df.index
    seg = df["_seg_id"] if "_seg_id" in df else None
    if token is not None:
        # pandas propagates ``attrs`` through row subsets. Include the concrete
        # row identity so a slice cannot reuse ROI masks cached for its parent
        # frame (or for a different same-sized subset).
        index_hash = int(
            pd.util.hash_array(idx.to_numpy()).sum(dtype=np.uint64)
        )
        return (
            "token", token, n, index_hash,
            str(idx[0]), str(idx[-1]),
            str(seg.iloc[0]) if seg is not None else "",
            str(seg.iloc[-1]) if seg is not None else "",
        )
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
    if "_smoothed_velocity" in df.columns:
        return pd.to_numeric(
            df["_smoothed_velocity"], errors="coerce"
        ).to_numpy(dtype=float)
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


def _tortuosity_window_samples(
        df: pd.DataFrame, seconds: float | None = None) -> int:
    """Convert the configured tortuosity time span to a stable sample window.

    The dashboard colours paths by local curvature, but an individual-frame
    ratio is mostly tracking noise.  A time-based span also gives comparable
    smoothing when recordings use different frame rates.
    """
    configured = (
        _visual("trajectory", "tortuosity_window_seconds", 2.0)
        if seconds is None else seconds
    )
    try:
        span = max(0.5, float(configured))
    except (TypeError, ValueError):
        span = 2.0
    if df is None or len(df) < 2:
        return 3
    times = (
        df["Current Time"].to_numpy()
        .astype("datetime64[ns]").astype("int64") / 1e9
    )
    delta = np.diff(times)
    seg = df["_seg_id"].to_numpy()
    valid = delta[(seg[1:] == seg[:-1]) & np.isfinite(delta) & (delta > 0)]
    dt = float(np.median(valid)) if len(valid) else 1.0
    # N samples span N-1 intervals.
    return max(3, int(math.ceil(span / max(dt, 1e-6))) + 1)


def compute_tortuosity(
        df: pd.DataFrame, window: int | None = None) -> np.ndarray:
    """Per-row local tortuosity = (path length over the last `window` steps) /
    (straight-line chord across that window), within each segment. 1 = straight,
    higher = more winding. When ``window`` is omitted, use the configurable
    time span (2 seconds by default). Vectorised."""
    window = (
        _tortuosity_window_samples(df)
        if window is None else max(3, int(window))
    )
    x = df["GameObjectPosX"].to_numpy()
    z = df["GameObjectPosZ"].to_numpy()
    seg = df["_seg_id"].to_numpy()
    ddx = np.empty(len(df)); ddx[0] = 0.0; ddx[1:] = np.diff(x)
    ddz = np.empty(len(df)); ddz[0] = 0.0; ddz[1:] = np.diff(z)
    step = np.sqrt(ddx * ddx + ddz * ddz)
    seg_start = np.empty(len(df), bool); seg_start[0] = True
    seg_start[1:] = seg[1:] != seg[:-1]
    step[seg_start] = 0.0
    # A ``window``-sample chord spans ``window - 1`` step lengths. Summing
    # ``window`` steps would bias a perfectly straight path above 1.
    step_window = max(1, int(window) - 1)
    s = pd.Series(step, index=df.index)
    path = (s.groupby(seg, sort=False)
             .rolling(step_window, min_periods=step_window).sum()
             .reset_index(level=0, drop=True).reindex(df.index).to_numpy())
    xb = pd.Series(x, index=df.index).groupby(seg, sort=False).shift(window - 1).to_numpy()
    zb = pd.Series(z, index=df.index).groupby(seg, sort=False).shift(window - 1).to_numpy()
    chord = np.sqrt((x - xb) ** 2 + (z - zb) ** 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        tort = path / chord
    tort[~np.isfinite(tort)] = np.nan
    return np.clip(tort, 1.0, None)


def compute_segment_stats(
    df: pd.DataFrame,
    vel: np.ndarray | None = None,
    tort: np.ndarray | None = None,
) -> pd.DataFrame:
    """Per-segment stats from contiguous `_seg_id` blocks.

    Velocity summaries deliberately use the same smoothed series as trajectory
    colouring. Passing ``vel`` lets the streaming loader compute these exact
    summaries while the complete source file is still available.
    """
    cols = ["seg_id", "n_points", "distance_walked", "displacement",
            "peak_velocity", "median_velocity", "median_local_tortuosity",
            "config", "vr", "fly_id", "scene", "source_folder"]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=cols)
    vel = smoothed_velocity(df) if vel is None else np.asarray(vel, dtype=float)
    tort = compute_tortuosity(df) if tort is None else np.asarray(tort, dtype=float)
    seg = df["_seg_id"].to_numpy()
    starts = np.concatenate(([0], np.flatnonzero(seg[1:] != seg[:-1]) + 1))
    ends = np.concatenate((starts[1:], [len(df)]))
    lens = ends - starts
    keep = lens >= 2
    x = df["GameObjectPosX"].to_numpy()
    z = df["GameObjectPosZ"].to_numpy()
    dx = np.empty(len(df)); dx[0] = 0.0; dx[1:] = np.diff(x)
    dz = np.empty(len(df)); dz[0] = 0.0; dz[1:] = np.diff(z)
    path_step = np.hypot(dx, dz)
    path_step[starts] = 0.0
    distance_walked = np.add.reduceat(path_step, starts)
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
    median_tort = (
        pd.Series(tort)
        .groupby(seg, sort=False)
        .median()
        .fillna(1.0)
        .to_numpy()
    )

    out = pd.DataFrame({
        "seg_id": seg[starts],
        "n_points": lens,
        "distance_walked": distance_walked,
        "displacement": np.hypot(x[ends - 1] - x[starts], z[ends - 1] - z[starts]),
        "peak_velocity": peak,
        "median_velocity": median,
        "median_local_tortuosity": median_tort,
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
    # Muted, contemporary categorical hues chosen to stay calm when hundreds
    # of translucent paths accumulate.  The order deliberately alternates hue
    # families so neighbouring subplot categories remain distinguishable.
    "#5f7f92", "#a97863", "#6f8d78", "#8f7182", "#7779a0",
    "#9a875f", "#5f8b88", "#887c70", "#6f86ad", "#9b725f",
    "#7f946c", "#927da0", "#66879b", "#a18472", "#738b82",
    "#8b788d", "#7c8398", "#95896f", "#688f91", "#857e78",
]

_VISUAL_STYLE_DEFAULTS = {
    "group_labels": {
        "config": {},
        "scene": {},
        "vr": {},
        "flyid": {},
        "file": {},
    },
    "trajectory": {
        "name": "Trajectory paths",
        "line_width": 1.2,
        "opacity": 0.50,
        "gray_color": "#737b85",
        "gray_opacity": 0.25,
        "tortuosity_window_seconds": 2.0,
        "palette": COLORS,
    },
    "spatial_layout": {
        "name": "Spatial presentation",
        "unit_scale": 1.0,
        "unit_label": "cm",
        "scale_bar_color": "#38444f",
        "scale_bar_width": 3.0,
    },
    "loop_observer": {
        "name": "Curtain rings",
        "ring_color": "#c88a00",
        "ring_fill": "rgba(245,183,0,0.10)",
        "inactive_ring_color": "rgba(190,134,14,0.55)",
        "before_color": "#7b8798",
        "before_opacity": 0.34,
        "future_opacity": 0.90,
        "entry_fill": "#fff7d1",
        "entry_line": "#6b4800",
    },
    "region_observer": {
        "name": "Observation windows",
        "active_line": "#b87917",
        "inactive_line": "rgba(168,122,52,0.62)",
        "fill": "rgba(207,157,68,0.065)",
        "label_background": "rgba(255,250,235,0.88)",
        "line_width": 2.2,
    },
    "gandiva": {
        "name": "Gandiva plot",
        "arrow_color": "#594324",
        "arrow_widths": [1.0, 1.35, 1.8, 2.3, 3.0],
        "arrow_opacities": [0.10, 0.24, 0.44, 0.70, 0.94],
        "density_breaks": [0.0, 0.12, 0.28, 0.48, 0.72, 1.000001],
        "marginal_line": "rgba(183,126,28,0.92)",
        "marginal_fill": "rgba(218,164,55,0.20)",
        "quadrant_line": "rgba(130,91,27,0.58)",
        "quadrant_label_bg": "rgba(255,250,235,0.82)",
        "raster_saturation": 0.60,
        "raster_value_min": 0.84,
        "raster_value_span": 0.09,
    },
    "heatmap": {
        "name": "Occupancy heatmap",
        "colorscale": "Viridis",
    },
    "transition": {
        "name": "Transition probability",
        "colorscale": [
            [0.00, "#f5f3f8"],
            [0.20, "#ddd5e8"],
            [0.45, "#b7a8ce"],
            [0.70, "#806ca8"],
            [1.00, "#49356f"],
        ],
        "split_line": "rgba(57,45,76,0.82)",
        "selected_fill": "rgba(198,151,45,0.12)",
        "selected_line": "#b87917",
        "before_color": "#89919d",
        "future_color": "#5e4a82",
    },
    "series": {
        "individual": {},
    },
}
_VISUAL_STYLE = copy.deepcopy(_VISUAL_STYLE_DEFAULTS)


def _deep_merge(base, override):
    """Return a recursively merged copy while preserving shipped defaults."""
    out = copy.deepcopy(base)
    if not isinstance(override, dict):
        return out
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _style_diff_paths(old, new, prefix=()):
    """Return leaf JSON paths whose values differ between two style payloads."""
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    changed = []
    for key in sorted(set(old) | set(new), key=str):
        old_value = old.get(key, object())
        new_value = new.get(key, object())
        path = prefix + (str(key),)
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            changed.extend(_style_diff_paths(old_value, new_value, path))
        elif old_value != new_value:
            changed.append(path)
    return changed


def _style_rename_entries(old, new, paths):
    """Describe group-label renames so mounted Plotly figures can patch text."""
    renames = []
    old_groups = (
        old.get("group_labels", {}) if isinstance(old, dict) else {})
    new_groups = (
        new.get("group_labels", {}) if isinstance(new, dict) else {})
    for path in paths:
        if (len(path) != 4 or path[0] != "group_labels"
                or path[3] != "name"):
            continue
        kind, raw = path[1], path[2]
        fallback = humanise_config(raw) if kind == "config" else str(raw)
        old_entry = old_groups.get(kind, {}).get(raw, {})
        new_entry = new_groups.get(kind, {}).get(raw, {})
        old_name = (
            old_entry.get("name", fallback)
            if isinstance(old_entry, dict) else fallback
        )
        new_name = (
            new_entry.get("name", fallback)
            if isinstance(new_entry, dict) else fallback
        )
        if str(old_name) != str(new_name):
            renames.append({
                "kind": str(kind),
                "raw": str(raw),
                "old": str(old_name),
                "new": str(new_name),
            })
    return renames


def _visual(section, key, default=None):
    return _VISUAL_STYLE.get(section, {}).get(key, default)


def _category_style(kind, raw):
    kind, raw = str(kind), str(raw)
    for section in ("group_labels", "series", "categories"):
        entry = _VISUAL_STYLE.get(section, {}).get(kind, {}).get(raw, {})
        if isinstance(entry, dict) and entry:
            return entry
    return {}


_GROUP_STYLE_KIND = {
    "config": "config",
    "scene": "scene",
    "vr": "vr",
    "flyid": "flyid",
    "file": "file",
}


def _group_label(group_by, raw):
    """Human-readable, style-overridable label for any panel grouping."""
    text = str(raw)
    entry = _category_style(_GROUP_STYLE_KIND.get(str(group_by), ""), text)
    if entry.get("name") not in (None, ""):
        return str(entry["name"])
    return humanise_config(text) if group_by == "config" else text


def _visual_style_payload(df=None):
    """Prefilled, self-documenting style JSON with current category labels."""
    payload = copy.deepcopy(_VISUAL_STYLE)
    # Migrate older saved JSON without making users memorise a new schema.
    legacy = payload.pop("categories", {})
    group_labels = payload.setdefault("group_labels", {})
    series = payload.setdefault("series", {})
    for kind, entries in legacy.items():
        target = series if kind == "individual" else group_labels
        target.setdefault(kind, {}).update(entries if isinstance(entries, dict) else {})
    if df is not None and len(df):
        category_specs = (
            ("config", "ConfigFile"),
            ("scene", "SceneName"),
            ("vr", "VR"),
            ("flyid", "FlyID"),
            ("file", "SourceFolder"),
        )
        for kind, column in category_specs:
            if column not in df:
                continue
            entries = payload["group_labels"].setdefault(kind, {})
            for index, raw in enumerate(_ordered_group_values(
                    df[column].dropna().astype(str).unique(), kind)):
                current = entries.get(str(raw), {})
                entries[str(raw)] = {
                    "name": (
                        humanise_config(str(raw)) if kind == "config"
                        else str(raw)
                    ),
                    "color": current.get(
                        "color", COLORS[index % len(COLORS)]),
                    "line_width": current.get(
                        "line_width",
                        float(_visual("trajectory", "line_width", 1.2))),
                }
        if {"VR", "FlyID"}.issubset(df.columns):
            entries = payload["series"].setdefault("individual", {})
            pairs = sorted(
                df[["VR", "FlyID"]].drop_duplicates()
                .astype(str).itertuples(index=False, name=None))
            for index, (vr_value, fly_value) in enumerate(pairs):
                key = f"{fly_value}@{vr_value}"
                current = entries.get(key, {})
                entries[key] = {
                    "name": current.get(
                        "name", f"{vr_value} fly{fly_value}"),
                    "color": current.get(
                        "color", COLORS[index % len(COLORS)]),
                    "line_width": current.get(
                        "line_width",
                        float(_visual("trajectory", "line_width", 1.2))),
                }
    return payload


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
BUDGET_HEADING_TIME = 180_000
BUDGET_GL_SPEED = 140_000
BUDGET_SVG_SPEED = 24_000
BUDGET_RAW_SPEED = 10_000
BUDGET_POLAR_SPEED = 12_000
BUDGET_HEADING_TIME_SPEED = 90_000
BUDGET_HEAT_SPEED = 220_000
BUDGET_ROI_SPEED = 180_000
PLOT_DEBOUNCE_MS = 120
_POLAR_RAY_CACHE: dict = {}
_POLAR_RAY_CACHE_ORDER: list = []
_POLAR_RAY_CACHE_MAX = 8
_TRANSITION_CACHE: dict = {}
_TRANSITION_CACHE_ORDER: list = []
_TRANSITION_CACHE_MAX = 4

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


def _trial_display_fraction(value) -> float:
    """Normalise the trajectory-only whole-segment sampling control to 0..1."""
    try:
        pct = float(value)
    except (TypeError, ValueError):
        pct = 100.0
    if not np.isfinite(pct):
        pct = 100.0
    return min(1.0, max(0.01, pct / 100.0))


def _sample_trajectory_segments(
        df: pd.DataFrame, display_percent=100, seed=0) -> pd.DataFrame:
    """Keep a stable random subset of complete ``_seg_id`` segments.

    This is a drawing modifier, not an analytical filter: callers apply it only
    to the trajectory figure (and therefore the browser-local loop observer)
    after all data/ROI filters have been evaluated. Row order remains the
    load-time order and no segment is split.
    """
    if df is None or len(df) == 0:
        return df
    fraction = _trial_display_fraction(display_percent)
    segids = pd.unique(df["_seg_id"])
    if fraction >= 1.0 or len(segids) <= 1:
        return df

    keep_count = max(1, int(math.ceil(fraction * len(segids))))
    try:
        seed_value = int(seed or 0)
    except (TypeError, ValueError):
        seed_value = 0
    rng = np.random.default_rng(seed_value)
    chosen = segids[rng.choice(len(segids), size=keep_count, replace=False)]
    keep = df["_seg_id"].isin(chosen).to_numpy()
    out = df.loc[keep]
    out.attrs["_frame_token"] = (
        "trajectory-segment-sample", _frame_cache_token(df),
        round(fraction, 6), seed_value, int(len(out)),
    )
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
    """Use the shared density-aware sizing policy for every spatial view."""
    return td_ui.subplot_pixel_height(nrows, ncols)


def _subplot_spacing(nrows):
    """Small vertical gaps keep multi-row Plotly drag targets easy to hit."""
    return min(0.035, 0.10 / max(int(nrows) - 1, 1))


def _horizontal_legend_layout(labels, ncols, base_top=78):
    """Reserve enough top margin and height for a wrapping horizontal legend."""
    labels = [str(label) for label in labels if label not in (None, "")]
    if not labels:
        return 50, 0
    available_px = max(360, int(ncols or 1) * 455 - 95)
    item_widths = [min(260, 38 + 6 * len(label)) for label in labels]
    rows = 1
    used = 0
    for width in item_widths:
        if used and used + width > available_px:
            rows += 1
            used = 0
        used += width
    extra = max(0, rows - 1) * 24
    return int(base_top + extra), int(extra)


def _group_frames(df, group_by, pool_mode, ncols):
    groups = td_grouping.group_frames(
        df, group_by, pool_mode, config_order=_CONFIG_ORDER,
        labeler=humanise_config,
    )
    if len(groups) <= 1:
        return groups
    ordered = _ordered_group_values(groups.keys(), group_by)
    return {name: groups[name] for name in ordered if name in groups}


def _resolve_panel_columns(requested, df, group_by="config", pool_mode="separate"):
    """Resolve the persisted override or auto-fit the current panel count."""
    panel_count = len(_group_frames(df, group_by, pool_mode, 1))
    return td_ui.resolve_panel_columns(requested, panel_count)


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
    palette = _visual("trajectory", "palette", COLORS)
    if not isinstance(palette, list) or not palette:
        palette = COLORS
    individuals = sorted(df[["VR", "FlyID"]].drop_duplicates().itertuples(index=False, name=None))
    ind_color = {
        k: _category_style(
            "individual", f"{str(k[1])}@{str(k[0])}").get(
                "color", palette[i % len(palette)])
        for i, k in enumerate(individuals)
    }
    vr_cats = sorted(df["VR"].dropna().unique())
    vr_color = {
        v: _category_style("vr", str(v)).get(
            "color", palette[i % len(palette)])
        for i, v in enumerate(vr_cats)
    }
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
    color_by = str(color_by or "categorical")
    # Old saved URLs used "one"; it is now the active panel's categorical hue.
    if color_by == "one":
        color_by = "categorical"
    groups = _group_frames(df, group_by, pool_mode, ncols)
    group_names = list(groups.keys())
    total_segs = sum(g["_seg_id"].nunique() for g in groups.values())
    pts_lim = max(2, int(budget) // max(total_segs, 1))

    ind_color, vr_color, tmin, tmax = _color_maps(df)
    tspan = (tmax - tmin) or 1.0

    # Per-point sequential metrics share one scale across subplots.
    vel_series, vel_cmax = None, 1.0
    if color_by == "velocity":
        vel_series = pd.Series(smoothed_velocity(df, 10), index=df.index)
        finite = vel_series.to_numpy()
        finite = finite[np.isfinite(finite)]
        vel_cmax = float(np.percentile(finite, 99)) if finite.size else 1.0
    tort_series, tort_cmax = None, 1.1
    if color_by == "tortuosity":
        tort_series = pd.Series(compute_tortuosity(df), index=df.index)
        finite = tort_series.to_numpy()
        finite = finite[np.isfinite(finite)]
        tort_cmax = (
            max(1.1, float(np.percentile(finite, 99)))
            if finite.size else 1.1
        )
    category_specs = {
        "config": ("ConfigFile", "config"),
        "scene": ("SceneName", "scene"),
        "folder": ("SourceFolder", "file"),
    }
    category_maps = {}
    if color_by in category_specs:
        column, kind = category_specs[color_by]
        values = pd.unique(df[column].dropna().astype(str))
        palette = _visual("trajectory", "palette", COLORS)
        if not isinstance(palette, list) or not palette:
            palette = COLORS
        category_maps[color_by] = {
            str(value): _category_style(kind, str(value)).get(
                "color", palette[index % len(palette)])
            for index, value in enumerate(values)
        }
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
        gd = dec.groupby("_seg_id", sort=False, observed=True)
        dpos = gd.cumcount().to_numpy()
        dlen = gd["_seg_id"].transform("size").to_numpy()
        vr = dec["VR"].to_numpy()

        mc_all = None
        if color_by == "vr":
            ck = vr.astype(str)
        elif color_by in category_specs:
            ck = dec[category_specs[color_by][0]].astype(str).to_numpy()
        elif color_by == "trial":
            ck = dec["CurrentTrial"].to_numpy().astype(float).astype(str)
        elif color_by == "local_time":
            ck = np.zeros(len(dec), dtype=int)   # whole subplot = one trace
            g2 = dec.groupby(
                "_seg_id", sort=False, observed=True)["Current Time"]
            t0, t1 = g2.transform("first"), g2.transform("last")
            dur = (t1 - t0).dt.total_seconds().replace(0, 1.0)
            mc_all = ((dec["Current Time"] - t0).dt.total_seconds() / dur).to_numpy()
        elif color_by == "velocity":
            ck = np.zeros(len(dec), dtype=int)   # whole subplot = one trace
            mc_all = vel_series.loc[dec.index].to_numpy()
        elif color_by == "tortuosity":
            ck = np.zeros(len(dec), dtype=int)   # whole subplot = one trace
            mc_all = tort_series.loc[dec.index].to_numpy()
        elif color_by == "roi":
            ck = (dec["_seg_id"].astype(str).map(outcome_map)
                  .fillna("No ROI").to_numpy(dtype=str))
        elif color_by in ("one", "none", "gray", "categorical"):
            ck = np.zeros(len(dec), dtype=int)
        else:  # individual
            fid = dec["FlyID"].to_numpy()
            ck = np.char.add(np.char.add(vr.astype(str), "|"), fid.astype(str))

        for key in pd.unique(ck):
            m = ck == key
            rec = dict(row=row, col=col, segids=segids[m], x=x[m], y=y[m],
                       dpos=dpos[m], dlen=dlen[m], customdata=custom_all[m],
                       mc=None, mode="lines",
                       group_value=str(gname),
                       color=COLORS[0], label="", legendgroup=None,
                       line_width=float(_visual(
                           "trajectory", "line_width", 1.2)),
                       opacity=float(_visual(
                           "trajectory", "opacity", 0.58)),
                       showlegend=False, colorscale=None, cmin=None, cmax=None)

            if color_by == "vr":
                rec["color"], rec["label"] = vr_color.get(key, COLORS[0]), str(key)
                entry = _category_style("vr", str(key))
                rec["label"] = str(entry.get("name", rec["label"]))
                rec["line_width"] = float(entry.get(
                    "line_width", rec["line_width"]))
            elif color_by in category_specs:
                _column, kind = category_specs[color_by]
                entry = _category_style(kind, str(key))
                rec["color"] = category_maps[color_by].get(str(key), COLORS[0])
                rec["label"] = str(entry.get(
                    "name",
                    humanise_config(str(key))
                    if color_by == "config" else str(key),
                ))
                rec["line_width"] = float(entry.get(
                    "line_width", rec["line_width"]))
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
            elif color_by == "tortuosity":
                rec["mode"], rec["mc"] = "markers", mc_all[m]
                rec["colorscale"], rec["cmin"], rec["cmax"] = (
                    SEQ_COLORSCALE, 1.0, tort_cmax)
            elif color_by == "roi":
                label = str(key)
                rec["color"] = _ROI_OUTCOME_COLOR.get(label, _ROI_OUTCOME_COLOR["No ROI"])
                rec["label"] = label
            elif color_by in ("none", "gray"):
                rec["color"] = _visual(
                    "trajectory", "gray_color", "#737b85")
                rec["label"] = "All trajectories · neutral"
                rec["opacity"] = float(_visual(
                    "trajectory", "gray_opacity", 0.28))
            elif color_by == "categorical":
                palette = _visual("trajectory", "palette", COLORS)
                if not isinstance(palette, list) or not palette:
                    palette = COLORS
                entry = _category_style(
                    _GROUP_STYLE_KIND.get(str(group_by), ""), str(gname))
                rec["color"] = entry.get(
                    "color", palette[idx % len(palette)])
                rec["label"] = _group_label(group_by, gname)
                rec["line_width"] = float(entry.get(
                    "line_width", rec["line_width"]))
            else:  # individual
                vrv, fidv = str(key).split("|", 1)
                rec["color"] = ind_color.get((vrv, fidv), COLORS[0])
                entry = _category_style(
                    "individual", f"{fidv}@{vrv}")
                parts = [p for p in (vrv if vrv and vrv != "unknown" else None,
                                     f"fly{fidv}" if fidv and fidv != "unknown" else None) if p]
                rec["label"] = str(
                    entry.get("name", " ".join(parts) or str(key)))
                rec["line_width"] = float(entry.get(
                    "line_width", rec["line_width"]))

            if color_by in ("individual", "vr", "roi", *category_specs):
                if color_by == "individual":
                    rec["legendgroup"] = f"individual:{fidv}@{vrv}"
                else:
                    rec["legendgroup"] = f"{color_by}:{rec['label']}"
                rec["showlegend"] = rec["legendgroup"] not in legend_seen
                legend_seen.add(rec["legendgroup"])
            records.append(rec)

    return group_names, records


def _add_traj_trace(fig, td, TraceType, hover=True):
    common = dict(name=td["label"], legendgroup=td["legendgroup"],
                  showlegend=td["showlegend"],
                  meta={"td_group_value": str(td.get("group_value", ""))},
                  opacity=float(td.get(
                      "opacity", _visual("trajectory", "opacity", 0.58))))
    if td.get("customdata") is not None:
        common["customdata"] = td["customdata"]
    if td["mode"] == "markers":
        common["marker"] = dict(size=3, color=td["marker_color"],
                                 colorscale=td["colorscale"],
                                 cmin=td["cmin"], cmax=td["cmax"])
    else:
        common["line"] = dict(
            color=td["line_color"],
            width=float(td.get(
                "line_width", _visual("trajectory", "line_width", 1.2))))
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


def mask_stationary_trajectory_points(df, moving_only=False, walk_thresh=None):
    """Blank slow X/Z samples for trajectory drawing without regrouping rows.

    Keeping rows (and replacing only coordinates with NaN) preserves the
    load-time segment order and creates Plotly line gaps across stationary
    stretches.  Analytical frames remain untouched.
    """
    if df is None or len(df) == 0 or not moving_only:
        return df
    threshold = max(0.0, float(walk_thresh or 0.0))
    speed = smoothed_velocity(df, 10)
    slow = ~np.isfinite(speed) | (speed < threshold)
    if not np.any(slow):
        return df
    out = df.copy()
    out.loc[slow, ["GameObjectPosX", "GameObjectPosZ"]] = np.nan
    out.attrs["_frame_token"] = (
        "moving-drawing", _frame_cache_token(df), round(threshold, 8),
    )
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
                name="td-target-overlay",
                x0=roi["x"] - reach, x1=roi["x"] + reach,
                y0=roi["z"] - reach, y1=roi["z"] + reach, opacity=0.12,
                fillcolor=col, line=dict(color=col, width=1.4, dash="dot")))
            shapes.append(dict(type="circle", xref=sx, yref=sy, layer="above",
                name="td-target-overlay",
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
            name="td-target-overlay",
            xref=f"{sx} domain", yref=f"{sy} domain", x=0.01, y=0.98,
            xanchor="left", yanchor="top", align="left",
            font=dict(size=10, color=_ROI_SIDE_COLOR["left"]),
            bgcolor="rgba(255,255,255,0.76)",
            bordercolor=_ROI_SIDE_COLOR["left"], borderwidth=0.6))
        anns.append(dict(text=right_txt, showarrow=False,
            name="td-target-overlay",
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
                            ncols=2, color_by="categorical", animate=True,
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
    titles = [_group_label(group_by, t) for t in group_names]

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
                    label=rec["label"], line_width=rec.get("line_width"),
                    opacity=rec.get("opacity"),
                    group_value=rec.get("group_value", ""),
                    customdata=rec.get("customdata_joined"))

    # Base traces (full extent)
    for rec in records:
        x, y, mc, custom = _record_arrays(rec, 1.0)
        rec["customdata_joined"] = custom
        _add_traj_trace(fig, _rec_to_td(rec, x, y, mc), go.Scattergl)

    # Colourbar for sequential modes (hidden anchor trace, added AFTER the data
    # traces so animation frames update only the data traces)
    if color_by in ("trial", "local_time", "velocity", "tortuosity") and records:
        cmin = records[0]["cmin"] if records[0]["cmin"] is not None else 0.0
        cmax = records[0]["cmax"] if records[0]["cmax"] is not None else 1.0
        title = {"trial": "Trial", "local_time": "Local time",
                 "velocity": "Speed (units/s)",
                 "tortuosity": (
                     f"Tortuosity ({float(_visual('trajectory', 'tortuosity_window_seconds', 2.0)):g} s)"
                 )}[color_by]
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
                        type="scattergl", x=x, y=y, mode="markers",
                        opacity=rec.get("opacity", 0.58),
                        marker=dict(size=3, color=mc, colorscale=SEQ_COLORSCALE,
                                    cmin=rec["cmin"], cmax=rec["cmax"])))
                else:
                    frame_traces.append(dict(
                        type="scattergl", x=x, y=y, mode="lines",
                        opacity=rec.get("opacity", 0.58),
                        line=dict(color=rec["color"],
                                  width=rec.get("line_width", 1.2))))
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

    show_legend = color_by in (
        "individual", "vr", "roi", "config", "scene", "folder")
    legend_labels = [
        trace.name for trace in fig.data
        if bool(getattr(trace, "showlegend", False))
    ]
    legend_top, legend_extra = (
        _horizontal_legend_layout(legend_labels, ncols)
        if show_legend else (50, 0)
    )
    fig.update_layout(
        height=60 + nrows * _subplot_px(nrows, ncols) + legend_extra,
        showlegend=show_legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="left", x=0,
                    font_size=10, itemclick="toggle", itemdoubleclick="toggleothers"),
        margin=dict(l=50, r=35, t=legend_top, b=40),
        template="plotly_white", dragmode="pan",
        meta={
            "panel_order_values": [str(name) for name in group_names],
            "panel_order_labels": titles,
            "spatial_axis_count": len(group_names),
            "trial_subset_signature": "|".join([
                "trajectory",
                repr(_frame_cache_token(df)),
                str(group_by), str(pool_mode), str(color_by),
                str(bool(animate)), str(max_points),
                repr(view_range),
                json.dumps(
                    _VISUAL_STYLE.get(
                        "trajectory", _VISUAL_STYLE_DEFAULTS["trajectory"]),
                    sort_keys=True, separators=(",", ":"),
                ),
            ]),
        },
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


def _sample_time_weights(df) -> np.ndarray:
    """Duration represented by each sorted sample without crossing trials."""
    if df is None or len(df) == 0:
        return np.zeros(0, dtype=float)
    fallback = _median_dt(df) if "Current Time" in df else 1.0
    fallback = fallback if np.isfinite(fallback) and fallback > 0 else 1.0
    if "Current Time" not in df or "_seg_id" not in df:
        return np.full(len(df), fallback, dtype=float)
    times = (
        df["Current Time"].to_numpy().astype("datetime64[ns]")
        .astype("int64") / 1e9
    )
    seg = df["_seg_id"].astype(str).to_numpy()
    duration = np.full(len(df), fallback, dtype=float)
    if len(df) > 1:
        forward = np.diff(times)
        valid = (
            (seg[1:] == seg[:-1])
            & np.isfinite(forward)
            & (forward > 0)
        )
        duration[:-1] = np.where(valid, forward, fallback)
    return duration


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


def _normalise_custom_regions(regions):
    """Validate browser-edited rectangular observation windows."""
    clean = []
    for index, region in enumerate(regions or []):
        if not isinstance(region, dict):
            continue
        try:
            x0, x1 = sorted((
                float(region.get("x0", -3.0)),
                float(region.get("x1", 3.0)),
            ))
            z0, z1 = sorted((
                float(region.get("z0", -3.0)),
                float(region.get("z1", 3.0)),
            ))
        except (TypeError, ValueError):
            continue
        if not all(np.isfinite(v) for v in (x0, x1, z0, z1)):
            continue
        if x1 <= x0 or z1 <= z0:
            continue
        clean.append({
            "id": str(region.get("id", f"region-{index + 1}")),
            "name": str(region.get("name", f"Window {index + 1}")),
            "x0": x0, "x1": x1, "z0": z0, "z1": z1,
        })
    return clean


def _custom_region_masks(frame, regions):
    """One vectorised point-membership mask per custom window."""
    clean = _normalise_custom_regions(regions)
    if frame is None or len(frame) == 0:
        return clean, [np.zeros(0, dtype=bool) for _ in clean]
    x = frame["GameObjectPosX"].to_numpy(dtype=float)
    z = frame["GameObjectPosZ"].to_numpy(dtype=float)
    masks = [
        ((x >= region["x0"]) & (x <= region["x1"])
         & (z >= region["z0"]) & (z <= region["z1"]))
        for region in clean
    ]
    return clean, masks


def _custom_region_subset(frame, regions, position_frame=None):
    """Return rows inside the union of windows while preserving `_seg_id`."""
    if frame is None or len(frame) == 0:
        return frame
    positions = position_frame if position_frame is not None else frame
    clean, masks = _custom_region_masks(positions, regions)
    if not clean:
        return frame
    union = np.logical_or.reduce(masks) if masks else np.zeros(len(frame), dtype=bool)
    out = frame.loc[union]
    out.attrs["_frame_token"] = (
        "custom-regions", _frame_cache_token(frame),
        tuple((r["id"], r["x0"], r["x1"], r["z0"], r["z1"]) for r in clean),
        int(len(out)),
    )
    return out


_CUSTOM_REGION_STATS_COLUMNS = [
    "seg_id", "n_points", "distance_walked", "displacement",
    "peak_velocity", "median_velocity", "median_local_tortuosity",
    "config", "vr", "fly_id", "scene", "source_folder",
]


def _finite_median(values):
    """Median of finite numeric values, or NaN without noisy empty warnings."""
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    numeric = numeric[np.isfinite(numeric)]
    return float(np.median(numeric)) if len(numeric) else float("nan")


def _custom_region_segment_stats(
        frame, regions, position_frame=None, *,
        _velocity=None, _tortuosity=None):
    """Summarise each trial using only its observed window sections.

    Membership is the union of all supplied rectangles. Distance only includes
    steps whose two endpoints are inside that union. Net displacement is the
    sum of the start-to-end chords of each contiguous observed visit, so leaving
    a window and later re-entering never creates an artificial jump across the
    unobserved portion. The original load-time `_seg_id` remains the grouping
    key throughout.
    """
    if frame is None or len(frame) == 0:
        return pd.DataFrame(columns=_CUSTOM_REGION_STATS_COLUMNS)
    positions = position_frame if position_frame is not None else frame
    clean, masks = _custom_region_masks(positions, regions)
    if not clean or len(positions) != len(frame):
        return pd.DataFrame(columns=_CUSTOM_REGION_STATS_COLUMNS)

    selected = np.logical_or.reduce(masks)
    if not selected.any():
        return pd.DataFrame(columns=_CUSTOM_REGION_STATS_COLUMNS)

    seg = frame["_seg_id"].astype(str).to_numpy()
    x = positions["GameObjectPosX"].to_numpy(dtype=float)
    z = positions["GameObjectPosZ"].to_numpy(dtype=float)
    same = np.zeros(len(frame), dtype=bool)
    same[1:] = seg[1:] == seg[:-1]
    previous_selected = np.concatenate(([False], selected[:-1]))
    inside_step = selected & previous_selected & same
    step = np.zeros(len(frame), dtype=float)
    step[1:] = np.hypot(np.diff(x), np.diff(z))
    inside_distance = np.where(inside_step, step, 0.0)

    selected_seg = seg[selected]
    seg_order = pd.Index(pd.unique(selected_seg), name="seg_id")
    n_points = (
        pd.Series(1, index=selected_seg)
        .groupby(level=0, sort=False).sum().reindex(seg_order)
    )
    distance = (
        pd.Series(inside_distance[selected], index=selected_seg)
        .groupby(level=0, sort=False).sum().reindex(seg_order).fillna(0.0)
    )

    run_start = selected & ~(previous_selected & same)
    run_id = np.cumsum(run_start)[selected]
    visits = pd.DataFrame({
        "seg_id": selected_seg,
        "run_id": run_id,
        "x": x[selected],
        "z": z[selected],
    })
    visit_groups = visits.groupby(
        ["seg_id", "run_id"], sort=False, observed=True)
    visit_dx = visit_groups["x"].last() - visit_groups["x"].first()
    visit_dz = visit_groups["z"].last() - visit_groups["z"].first()
    displacement = (
        pd.Series(np.hypot(
            visit_dx.to_numpy(dtype=float),
            visit_dz.to_numpy(dtype=float),
        ), index=visit_dx.index)
        .groupby(level=0, sort=False).sum().reindex(seg_order).fillna(0.0)
    )

    velocity = (
        smoothed_velocity(frame, 10)
        if _velocity is None else np.asarray(_velocity, dtype=float)
    )
    tortuosity = (
        compute_tortuosity(positions)
        if _tortuosity is None else np.asarray(_tortuosity, dtype=float)
    )
    velocity_by_seg = pd.Series(
        velocity[selected], index=selected_seg)
    median_velocity = (
        velocity_by_seg.groupby(level=0, sort=False)
        .median().reindex(seg_order)
    )
    peak_velocity = (
        velocity_by_seg.groupby(level=0, sort=False)
        .max().reindex(seg_order)
    )
    median_tortuosity = (
        pd.Series(tortuosity[selected], index=selected_seg)
        .groupby(level=0, sort=False).median().reindex(seg_order)
    )

    first_rows = (
        frame.loc[selected]
        .drop_duplicates("_seg_id", keep="first")
        .assign(_seg_text=lambda value: value["_seg_id"].astype(str))
        .set_index("_seg_text")
        .reindex(seg_order)
    )
    out = pd.DataFrame({
        "seg_id": seg_order.astype(str),
        "n_points": n_points.to_numpy(dtype=int),
        "distance_walked": distance.to_numpy(dtype=float),
        "displacement": displacement.to_numpy(dtype=float),
        "peak_velocity": peak_velocity.to_numpy(dtype=float),
        "median_velocity": median_velocity.to_numpy(dtype=float),
        "median_local_tortuosity": median_tortuosity.to_numpy(dtype=float),
    })
    metadata = {
        "config": "ConfigFile",
        "vr": "VR",
        "fly_id": "FlyID",
        "scene": "SceneName",
        "source_folder": "SourceFolder",
    }
    for output, source in metadata.items():
        out[output] = (
            first_rows[source].astype(str).to_numpy()
            if source in first_rows else ""
        )
    return out[_CUSTOM_REGION_STATS_COLUMNS].reset_index(drop=True)


def _custom_region_stats(frame, regions, group_by="config",
                         pool_mode="separate", ncols=2, position_frame=None):
    """Window summaries globally and split by the active panel grouping.

    The returned ``panels`` payload is deliberately figure-ready: every active
    scene/config/VR/fly/folder group receives comparable sample and entering
    trial percentages plus robust per-entered-trial movement summaries.
    """
    positions = position_frame if position_frame is not None else frame
    clean, masks = _custom_region_masks(positions, regions)
    if frame is None or len(frame) == 0 or not clean:
        return {"enabled": bool(clean), "regions": [], "panels": []}

    seg = frame["_seg_id"].astype(str).to_numpy()
    velocity = smoothed_velocity(frame, 10)
    tortuosity = compute_tortuosity(positions)
    segment_order = pd.Index(pd.unique(seg), name="seg_id")
    total_trials = max(1, int(len(segment_order)))
    total_samples_by_segment = (
        pd.Series(1, index=seg)
        .groupby(level=0, sort=False).sum().reindex(segment_order)
    )
    sample_seconds = _sample_time_weights(frame)
    total_seconds_by_segment = (
        pd.Series(sample_seconds, index=seg)
        .groupby(level=0, sort=False).sum().reindex(segment_order)
    )
    first_by_segment = (
        frame.drop_duplicates("_seg_id", keep="first")
        .assign(_seg_text=lambda value: value["_seg_id"].astype(str))
        .set_index("_seg_text")
        .reindex(segment_order)
    )
    region_rows = []
    per_region_stats = {}
    per_region_sample_percent = {}
    per_region_inside_seconds = {}
    per_region_time_percent = {}

    for region, mask in zip(clean, masks):
        stats = _custom_region_segment_stats(
            frame, [region], positions,
            _velocity=velocity, _tortuosity=tortuosity,
        )
        per_region_stats[region["id"]] = stats
        inside_by_segment = (
            pd.Series(mask.astype(np.int64), index=seg)
            .groupby(level=0, sort=False).sum().reindex(
                segment_order, fill_value=0)
        )
        per_region_sample_percent[region["id"]] = (
            100.0 * inside_by_segment
            / total_samples_by_segment.clip(lower=1)
        )
        inside_seconds = (
            pd.Series(sample_seconds * mask.astype(float), index=seg)
            .groupby(level=0, sort=False).sum().reindex(
                segment_order, fill_value=0.0)
        )
        per_region_inside_seconds[region["id"]] = inside_seconds
        per_region_time_percent[region["id"]] = (
            100.0 * inside_seconds
            / total_seconds_by_segment.clip(lower=np.finfo(float).eps)
        )
        sample_count = int(mask.sum())
        entered = int(len(stats))
        distance = float(stats["distance_walked"].sum()) if entered else 0.0
        displacement = float(stats["displacement"].sum()) if entered else 0.0
        med_tort = (
            _finite_median(stats["median_local_tortuosity"])
            if entered else float("nan")
        )
        med_vel = (
            _finite_median(stats["median_velocity"])
            if entered else float("nan")
        )
        region_rows.append({
            **region,
            "samples": sample_count,
            "sample_percent": 100.0 * sample_count / max(1, len(frame)),
            "trials": entered,
            "total_trials": total_trials,
            "trial_percent": 100.0 * entered / total_trials,
            "distance_walked": distance,
            "net_displacement": displacement,
            "median_tortuosity": med_tort,
            "median_velocity": med_vel,
        })

    panels = []
    for raw_name, group in _group_frames(
            positions, group_by, pool_mode, ncols).items():
        group_membership = positions.index.isin(group.index)
        group_segments = pd.Index(group["_seg_id"].astype(str).unique())
        group_trial_count = max(1, len(group_segments))
        summaries = []
        for region, mask in zip(clean, masks):
            stats = per_region_stats[region["id"]]
            substats = stats[
                stats["seg_id"].astype(str).isin(group_segments)]
            aligned = (
                stats.assign(_seg_text=stats["seg_id"].astype(str))
                .set_index("_seg_text").reindex(group_segments)
            )
            aligned_meta = first_by_segment.reindex(group_segments)
            sample_percent = (
                per_region_sample_percent[region["id"]]
                .reindex(group_segments).fillna(0.0)
            )
            inside_seconds = (
                per_region_inside_seconds[region["id"]]
                .reindex(group_segments).fillna(0.0)
            )
            time_percent = (
                per_region_time_percent[region["id"]]
                .reindex(group_segments).fillna(0.0)
            )
            total_seconds = (
                total_seconds_by_segment
                .reindex(group_segments).fillna(0.0)
            )
            count = int((mask & group_membership).sum())
            entered = int(len(substats))
            summaries.append({
                "id": region["id"],
                "name": region["name"],
                "samples": count,
                "total_samples": int(len(group)),
                "percent": 100.0 * count / max(1, len(group)),
                "trials": entered,
                "total_trials": int(group_trial_count),
                "trial_percent": 100.0 * entered / group_trial_count,
                "total_distance_walked": (
                    float(substats["distance_walked"].sum())
                    if entered else 0.0
                ),
                "total_displacement": (
                    float(substats["displacement"].sum())
                    if entered else 0.0
                ),
                "median_distance_walked": (
                    _finite_median(substats["distance_walked"])
                    if entered else float("nan")
                ),
                "median_displacement": (
                    _finite_median(substats["displacement"])
                    if entered else float("nan")
                ),
                "median_tortuosity": (
                    _finite_median(substats["median_local_tortuosity"])
                    if entered else float("nan")
                ),
                "median_velocity": (
                    _finite_median(substats["median_velocity"])
                    if entered else float("nan")
                ),
                # Retained only long enough to build the diagnostic figure.
                # The compact browser store strips this field below.
                "segment_values": {
                    "seg_id": group_segments.astype(str).tolist(),
                    "n_points": (
                        pd.to_numeric(aligned["n_points"], errors="coerce")
                        .fillna(0).astype(int).tolist()
                    ),
                    "sample_percent": sample_percent.astype(float).tolist(),
                    "inside_seconds": inside_seconds.astype(float).tolist(),
                    "total_seconds": total_seconds.astype(float).tolist(),
                    "time_percent": time_percent.astype(float).tolist(),
                    "entered": (
                        sample_percent.to_numpy(dtype=float) > 0
                    ).astype(int).tolist(),
                    "trial_percent": (
                        100.0 * (sample_percent.to_numpy(dtype=float) > 0)
                    ).tolist(),
                    "distance_walked": (
                        pd.to_numeric(
                            aligned["distance_walked"], errors="coerce"
                        ).astype(float).tolist()
                    ),
                    "displacement": (
                        pd.to_numeric(
                            aligned["displacement"], errors="coerce"
                        ).astype(float).tolist()
                    ),
                    "median_local_tortuosity": (
                        pd.to_numeric(
                            aligned["median_local_tortuosity"],
                            errors="coerce",
                        ).astype(float).tolist()
                    ),
                    "median_velocity": (
                        pd.to_numeric(
                            aligned["median_velocity"], errors="coerce"
                        ).astype(float).tolist()
                    ),
                    "fly_id": (
                        aligned_meta["FlyID"].astype(object)
                        .where(aligned_meta["FlyID"].notna(), "")
                        .astype(str).tolist()
                        if "FlyID" in aligned_meta else [""] * len(group_segments)
                    ),
                    "vr": (
                        aligned_meta["VR"].astype(object)
                        .where(aligned_meta["VR"].notna(), "")
                        .astype(str).tolist()
                        if "VR" in aligned_meta else [""] * len(group_segments)
                    ),
                    "source_folder": (
                        aligned_meta["SourceFolder"].astype(object)
                        .where(aligned_meta["SourceFolder"].notna(), "")
                        .astype(str).tolist()
                        if "SourceFolder" in aligned_meta
                        else [""] * len(group_segments)
                    ),
                },
            })
        panels.append({
            "raw": str(raw_name),
            "name": _group_label(group_by, raw_name),
            "regions": summaries,
        })
    return {"enabled": True, "regions": region_rows, "panels": panels}


def _custom_region_store_payload(payload):
    """Strip diagnostic-only segment arrays from the browser-facing store."""
    payload = payload or {}
    return {
        "enabled": bool(payload.get("enabled")),
        "regions": payload.get("regions") or [],
        "panels": [
            {
                **{key: value for key, value in panel.items()
                   if key != "regions"},
                "regions": [
                    {key: value for key, value in region.items()
                     if key != "segment_values"}
                    for region in panel.get("regions", [])
                ],
            }
            for panel in (payload.get("panels") or [])
        ],
    }


def _distribution_choice(mode, counts, threshold=200):
    """Resolve one mark type for an entire multi-panel diagnostic."""
    requested = str(mode or "auto").lower()
    if requested in {"swarm", "violin"}:
        return requested
    largest = max((int(value) for value in counts), default=0)
    return "swarm" if largest <= int(threshold) else "violin"


def _observation_distribution_values(summary, key, stats_unit="trial"):
    """Return aligned window observations at trial or animal level."""
    values = summary.get("segment_values") or {}
    seg_ids = np.asarray(values.get("seg_id") or [], dtype=object)
    support = np.asarray(values.get("n_points") or [], dtype=float)
    requested_key = str(key)
    source_key = "entered" if requested_key == "trial_count" else requested_key
    numeric = np.asarray(values.get(source_key) or [], dtype=float)
    n = min(len(numeric), len(seg_ids), len(support))
    if not n:
        return np.array([]), np.array([], dtype=object), np.array([])
    numeric, seg_ids, support = numeric[:n], seg_ids[:n], support[:n]

    fly = np.asarray(values.get("fly_id") or [""] * n, dtype=object)[:n]
    vr = np.asarray(values.get("vr") or [""] * n, dtype=object)[:n]
    folder = np.asarray(
        values.get("source_folder") or [""] * n, dtype=object)[:n]
    animal = np.asarray([
        f"{f}@{v}" if str(f).strip() else (
            f"{folder_name}@{v}" if str(folder_name).strip() else str(seg)
        )
        for f, v, folder_name, seg in zip(fly, vr, folder, seg_ids)
    ], dtype=object)

    # Entry is an animal-level count by definition: each animal contributes one
    # paired count per window, irrespective of the display unit used elsewhere.
    if requested_key == "trial_count":
        entered = np.where(np.isfinite(numeric), numeric, 0.0)
        pooled = pd.DataFrame({
            "animal": animal.astype(str),
            "value": entered,
            "trials": np.ones(n, dtype=float),
        }).groupby("animal", sort=False, observed=True).agg(
            value=("value", "sum"),
            support=("trials", "sum"),
        )
        return (
            pooled["value"].to_numpy(dtype=float),
            pooled.index.to_numpy(dtype=object),
            pooled["support"].to_numpy(dtype=float),
        )

    # Percent tracked time must pool numerators and denominators. Averaging trial
    # percentages would give short trials the same weight as long trials.
    if requested_key == "time_percent":
        inside = np.asarray(values.get("inside_seconds") or [], dtype=float)[:n]
        total = np.asarray(values.get("total_seconds") or [], dtype=float)[:n]
        if str(stats_unit or "trial") != "animal":
            keep = np.isfinite(numeric) & np.isfinite(total) & (total > 0)
            return numeric[keep], seg_ids[keep], total[keep]
        pooled = pd.DataFrame({
            "animal": animal.astype(str),
            "inside": np.where(np.isfinite(inside), inside, 0.0),
            "total": np.where(np.isfinite(total), total, 0.0),
        }).groupby("animal", sort=False, observed=True).sum()
        valid = pooled["total"].to_numpy(dtype=float) > 0
        ratio = (
            100.0 * pooled["inside"].to_numpy(dtype=float)[valid]
            / pooled["total"].to_numpy(dtype=float)[valid]
        )
        return (
            ratio,
            pooled.index.to_numpy(dtype=object)[valid],
            pooled["total"].to_numpy(dtype=float)[valid],
        )

    keep = np.isfinite(numeric)
    numeric, seg_ids, support = numeric[keep], seg_ids[keep], support[keep]
    animal = animal[keep]
    if str(stats_unit or "trial") != "animal" or not len(numeric):
        return numeric, seg_ids, support

    pooled = pd.DataFrame({
        "animal": animal.astype(str),
        "value": numeric,
        "support": support,
    }).groupby("animal", sort=False, observed=True).agg(
        value=("value", "mean"),
        support=("support", "sum"),
    )
    return (
        pooled["value"].to_numpy(dtype=float),
        pooled.index.to_numpy(dtype=object),
        pooled["support"].to_numpy(dtype=float),
    )


def build_custom_region_diagnostics_figure(
        payload, distribution_mode="auto", show_violin_points=True,
        stats_unit="trial", spatial_unit_scale=1.0,
        spatial_unit_label="cm"):
    """Plot six consistent window distributions by the active panel grouping.

    Time occupancy pools tracked seconds with the correct trial/animal
    denominator. Entry is a paired per-animal trial count. Movement estimates
    use only the observed sections of each entered `_seg_id`.
    """
    panels = (payload or {}).get("panels") or []
    region_rows = (payload or {}).get("regions") or []
    if not panels or not region_rows:
        return _msg_figure(
            "Enable an observation window to inspect its local trajectories.",
            260,
        )

    try:
        distance_scale = float(spatial_unit_scale)
    except (TypeError, ValueError):
        distance_scale = 1.0
    if not np.isfinite(distance_scale) or distance_scale <= 0:
        distance_scale = 1.0
    distance_unit = str(spatial_unit_label or "cm").strip() or "cm"
    unit_label = "animal" if stats_unit == "animal" else "trial"
    specs = (
        ("time_percent", "Time inside", f"% tracked time per {unit_label}"),
        ("trial_count", "Trials entering", "count per animal"),
        (
            "distance_walked", "Distance walked",
            f"{distance_unit} per {unit_label}",
        ),
        (
            "displacement", "Net displacement",
            f"{distance_unit} per {unit_label}",
        ),
        (
            "median_local_tortuosity",
            "Local tortuosity",
            f"ratio per {unit_label}",
        ),
        (
            "median_velocity", "Velocity",
            f"{distance_unit}/s per {unit_label}",
        ),
    )
    fig = make_subplots(
        rows=2, cols=3,
        subplot_titles=[f"{title}<br><sup>{subtitle}</sup>"
                        for _key, title, subtitle in specs],
        horizontal_spacing=0.08, vertical_spacing=0.22,
    )
    panel_names = [str(panel.get("name", panel.get("raw", "Group")))
                   for panel in panels]
    region_ids = [str(region["id"]) for region in region_rows]
    region_names = {
        str(region["id"]): str(region["name"]) for region in region_rows
    }

    group_count = len(panels)
    region_count = max(1, len(region_ids))
    region_spacing = 0.72 / region_count
    half_width = min(0.28, region_spacing * 0.38)
    observations = {}
    counts = []
    for metric_index, (key, _title, _subtitle) in enumerate(specs):
        for group_index, panel in enumerate(panels):
            summaries = {
                str(item.get("id")): item
                for item in panel.get("regions", [])
            }
            for region_index, region_id in enumerate(region_ids):
                values = _observation_distribution_values(
                    summaries.get(region_id, {}), key, stats_unit)
                if key in {"distance_walked", "displacement", "median_velocity"}:
                    values = (
                        values[0] * distance_scale,
                        values[1],
                        values[2],
                    )
                observations[(metric_index, group_index, region_index)] = values
                counts.append(len(values[0]))
    mark = _distribution_choice(distribution_mode, counts)

    for metric_index, (key, title, _subtitle) in enumerate(specs):
        row, col = metric_index // 3 + 1, metric_index % 3 + 1
        for group_index, panel in enumerate(panels):
            # Connect the same trial/animal only inside explicit adjacent
            # window pairs (1↔2, 3↔4, ...). Entry counts always use animals,
            # so each pair shares the animal's trial effort.
            if region_count > 1:
                paired = {}
                centres = []
                for region_index, _region_id in enumerate(region_ids):
                    centres.append(
                        group_index
                        + (region_index - (region_count - 1) / 2)
                        * region_spacing
                    )
                    region_values, identities, _support = observations[
                        (metric_index, group_index, region_index)
                    ]
                    for identity, value in zip(identities, region_values):
                        paired.setdefault(str(identity), {})[
                            region_index] = float(value)
                pair_x, pair_y = [], []
                for identity_values in paired.values():
                    for left_index in range(0, region_count - 1, 2):
                        right_index = left_index + 1
                        if not (
                                left_index in identity_values
                                and right_index in identity_values
                                and np.isfinite(identity_values[left_index])
                                and np.isfinite(identity_values[right_index])):
                            continue
                        pair_x.extend([
                            centres[left_index], centres[right_index], None,
                        ])
                        pair_y.extend([
                            identity_values[left_index],
                            identity_values[right_index],
                            None,
                        ])
                if pair_x:
                    fig.add_trace(go.Scatter(
                        x=pair_x, y=pair_y, mode="lines",
                        line=dict(color="rgba(71,84,103,0.20)", width=0.8),
                        hoverinfo="skip", showlegend=False,
                        meta={
                            "td_group_value": str(
                                panel.get("raw", panel_names[group_index])),
                            "td_region_pairing": True,
                            "td_pairing": True,
                        },
                    ), row=row, col=col)
            for region_index, region_id in enumerate(region_ids):
                values, identities, point_counts = observations[
                    (metric_index, group_index, region_index)]
                if not len(values):
                    continue
                centre = (
                    group_index
                    + (region_index - (region_count - 1) / 2) * region_spacing
                )
                color = COLORS[region_index % len(COLORS)]
                inside_time = (
                    values * point_counts / 100.0
                    if key == "time_percent"
                    else np.full(len(values), np.nan)
                )
                custom = np.column_stack([
                    identities.astype(str), point_counts, inside_time,
                ]).tolist()
                support_label = (
                    "tracked seconds" if key == "time_percent"
                    else "available trials" if key == "trial_count"
                    else "samples inside"
                )
                identity_label = (
                    "animal" if key == "trial_count" else unit_label
                )
                hover = (
                    f"<b>{panel_names[group_index]} · "
                    f"{region_names[region_id]}</b>"
                    f"<br>{title}: %{{y:.4g}}"
                    f"<br>{identity_label}: %{{customdata[0]}}"
                )
                if key == "time_percent":
                    hover += (
                        "<br>time inside: %{customdata[2]:.4g}s / "
                        "%{customdata[1]:.4g}s tracked"
                    )
                elif key == "trial_count":
                    hover += (
                        "<br>entered: %{y:.0f} / "
                        "%{customdata[1]:.0f} available trials"
                    )
                else:
                    hover += f"<br>{support_label}: %{{customdata[1]:.4g}}"
                hover += "<extra></extra>"
                if mark == "swarm":
                    rng = np.random.default_rng(
                        2903 + metric_index * 1009
                        + group_index * 101 + region_index * 17
                    )
                    jitter = rng.uniform(
                        -half_width, half_width, len(values))
                    fig.add_trace(go.Scatter(
                        x=(centre + jitter).tolist(),
                        y=values.tolist(),
                        mode="markers",
                        name=region_names[region_id],
                        legendgroup=f"window:{region_id}",
                        showlegend=False,
                        marker=dict(color=color, size=5, opacity=0.62),
                        meta={
                            "td_group_value": str(
                                panel.get("raw", panel_names[group_index])),
                            "td_region_value": str(region_id),
                        },
                        customdata=custom,
                        hovertemplate=hover,
                    ), row=row, col=col)
                else:
                    show_points = bool(show_violin_points) and len(values) <= 200
                    fig.add_trace(go.Violin(
                        x=[centre] * len(values),
                        y=values.tolist(),
                        name=region_names[region_id],
                        legendgroup=f"window:{region_id}",
                        scalegroup=(
                            f"window:{region_id}:metric:{key}"
                        ),
                        showlegend=False,
                        scalemode="count",
                        spanmode="hard",
                        box_visible=False,
                        meanline_visible=False,
                        points="all" if show_points else False,
                        jitter=0.22 if show_points else 0,
                        pointpos=0,
                        marker=dict(color=color, size=4, opacity=0.48),
                        width=half_width * 2,
                        line_color=color,
                        fillcolor=color,
                        opacity=0.48,
                        meta={
                            "td_group_value": str(
                                panel.get("raw", panel_names[group_index])),
                            "td_region_value": str(region_id),
                        },
                        customdata=custom,
                        hovertemplate=hover,
                    ), row=row, col=col)

                q1, median, q3 = np.percentile(values, [25, 50, 75])
                fig.add_shape(
                    type="rect",
                    x0=centre - half_width,
                    x1=centre + half_width,
                    y0=float(q1),
                    y1=float(q3),
                    fillcolor=_rgba(color, 0.14),
                    line=dict(color=_rgba(color, 0.72), width=1.3),
                    layer="above",
                    name=(
                        f"td-group-shape:"
                        f"{panel.get('raw', panel_names[group_index])}"
                    ),
                    row=row,
                    col=col,
                )
                fig.add_shape(
                    type="line",
                    x0=centre - half_width,
                    x1=centre + half_width,
                    y0=float(median),
                    y1=float(median),
                    line=dict(color=color, width=2.1),
                    layer="above",
                    name=(
                        f"td-group-shape:"
                        f"{panel.get('raw', panel_names[group_index])}"
                    ),
                    row=row,
                    col=col,
                )
        fig.update_xaxes(
            tickmode="array",
            tickvals=list(range(group_count)),
            ticktext=panel_names,
            tickangle=-22,
            range=[-0.55, max(0.55, group_count - 0.45)],
            row=row,
            col=col,
        )
        if key == "time_percent":
            fig.update_yaxes(range=[-3, 103], ticksuffix="%",
                             row=row, col=col)
        elif key == "trial_count":
            fig.update_yaxes(rangemode="tozero", row=row, col=col)
        elif key in {"distance_walked", "displacement"}:
            fig.update_yaxes(
                title_text=distance_unit, rangemode="tozero", row=row, col=col)
        elif key == "median_velocity":
            fig.update_yaxes(
                title_text=f"{distance_unit}/s", rangemode="tozero",
                row=row, col=col)
    fig.update_layout(
        template="plotly_white",
        height=680,
        violinmode="overlay",
        margin=dict(l=52, r=28, t=118, b=90),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.10,
            xanchor="left", x=0,
        ),
        hoverlabel=dict(namelength=-1),
        meta={
            "panel_order_values": [
                str(panel.get("raw", panel.get("name", "Group")))
                for panel in panels
            ],
            "panel_order_labels": panel_names,
            "region_count": region_count,
        },
    )
    fig.add_annotation(
        x=0.5, y=-0.16, xref="paper", yref="paper", showarrow=False,
        text=(
            f"{mark.title()} selected for all six panels · observations are "
            f"{unit_label}s (entry counts are animals) · faint lines pair the "
            "same unit across windows · shaded bands are IQR; bold lines are "
            "medians."
        ),
        font=dict(size=10, color="#667085"),
    )
    return fig


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
                name="td-target-overlay",
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
                name=f"td-target-overlay:hm-roi-{side}",
                text=text, showarrow=False,
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

    def plain(value):
        if value >= 1:
            if abs(value - round(value)) < max(1e-9, value * 1e-10):
                return f"{int(round(value)):,}"
            return f"{value:,.3g}"
        return f"{value:.8f}".rstrip("0").rstrip(".")

    for d in decades:
        for m in mults:
            v = m * (10.0 ** d)
            if mmin * 0.999 <= v <= mmax * 1.001:
                vals.append(np.log10(v))
                text.append(
                    f"{plain(v)}%" if metric == "percent" else plain(v)
                )
    if not vals:  # degenerate
        vals = [np.log10(max(mmax, 1e-9))]
        text = [f"{plain(mmax)}%" if metric == "percent" else plain(mmax)]
    return vals, text


def _heatmap_edges(df, bin_size, bound_pct):
    """Shared zero-centred bin edges + range (metric-independent).

    Every lattice has a cell centred exactly at ``(0, 0)``.  Anchoring edges
    to a data-dependent minimum made zero drift between the middle and edge of
    a cell as filters changed, which produced especially distracting boundary
    artefacts in occupancy, Gandiva, and transition views.
    """
    rng = _robust_range(df, bound_pct) if bound_pct and bound_pct < 100 else _shared_range(df)
    rx, rz = rng
    bs = float(bin_size) if bin_size and bin_size > 0 else default_bin_size(df)
    radius_x = max(abs(float(rx[0])), abs(float(rx[1])))
    radius_z = max(abs(float(rz[0])), abs(float(rz[1])))
    span_x = max(2.0 * radius_x, 0.0)
    span_z = max(2.0 * radius_z, 0.0)
    span = max(span_x, span_z)
    if not np.isfinite(bs) or bs <= 0:
        bs = max(span / 20.0, 1.0)
    if not np.isfinite(span) or span <= 0:
        span = bs
        span_x = span_z = span
    n_x = max(1, 2 * max(0, int(np.ceil(radius_x / bs - 0.5))) + 1)
    n_z = max(1, 2 * max(0, int(np.ceil(radius_z / bs - 0.5))) + 1)
    axis_scale = max(n_x, n_z) / MAX_HEATMAP_BINS
    cell_scale = math.sqrt((n_x * n_z) / MAX_HEATMAP_CELLS)
    scale = max(1.0, axis_scale, cell_scale)
    if scale > 1.0:
        bs *= scale
    half_x = max(0, int(np.ceil(radius_x / bs - 0.5)))
    half_z = max(0, int(np.ceil(radius_z / bs - 0.5)))
    xedges = (np.arange(-half_x, half_x + 2, dtype=float) - 0.5) * bs
    yedges = (np.arange(-half_z, half_z + 2, dtype=float) - 0.5) * bs
    centred_range = (
        (float(xedges[0]), float(xedges[-1])),
        (float(yedges[0]), float(yedges[-1])),
    )
    return xedges, yedges, centred_range


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
    return dict(group_names=group_names, group_by=group_by,
                nrows=nrows, xc=xc.tolist(),
                yc=yc.tolist(), rng=rng, counts=counts, dt=_median_dt(df),
                reach=reach_v,
                roi_stats=_heatmap_roi_stats(group_items, rois_by_cfg,
                                             reach_v))


def _heatmap_metric_mats(bins, metric):
    """Materialise one heatmap metric from the already-computed count bins."""
    metric = metric if metric in METRIC_UNITS else "count"
    mats = []
    for H in bins["counts"]:
        M = H.copy()
        if metric == "time":
            M = M * bins["dt"]
        elif metric == "percent":
            total = M.sum()
            M = (100.0 * M / total) if total > 0 else M
        mats.append(M)
    return mats


def _heatmap_variant(bins, metric, log_scale, cmin=None, cmax=None,
                     crange_mode="value"):
    """Cheap: turn the raw-count bins into one metric/scale variant's per-trace
    data (z / customdata / zmin / zmax / colorbar / hover). Used both to assemble
    a figure and to precompute every variant for instant client-side swapping."""
    metric = metric if metric in METRIC_UNITS else "count"
    unit = METRIC_UNITS[metric]
    dt = bins["dt"]
    mats = _heatmap_metric_mats(bins, metric)
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
                        subplot_titles=[
                            _group_label(bins.get("group_by", "config"), t)
                            for t in group_names
                        ],
                        horizontal_spacing=0.05,
                        vertical_spacing=_subplot_spacing(nrows))
    for idx, (z, cd) in enumerate(zip(var["z"], var["customdata"])):
        fig.add_trace(
            go.Heatmap(x=bins["xc"], y=bins["yc"], z=z, customdata=cd,
                       colorscale=_visual(
                           "heatmap", "colorscale", HEATMAP_COLORSCALE),
                       zmin=var["zmin"],
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


# ---------------------------------------------------------------------------
# Conditional half-transition probability
# ---------------------------------------------------------------------------

TRANSITION_OUTCOMES = {
    "crossed": "crossed the opposite half after first cell entry",
    "ended": "ended in the opposite half after first cell entry",
}
TRANSITION_METRICS = {
    "fraction": "fraction of entering trials",
    "count": "number of successful trials",
}


def _transition_default_split(
        df: pd.DataFrame, yedges: np.ndarray) -> tuple[float, str]:
    """Choose a reproducible start-centred horizontal split.

    The modal segment-start row is used rather than a frame-weighted centre.
    Its median start position becomes the exact boundary; transition-only bin
    edges are then rebuilt outward from it, so the automatic line can never
    straddle an arbitrarily offset pre-existing row.
    """
    starts = df.drop_duplicates("_seg_id", keep="first")
    values = pd.to_numeric(
        starts["GameObjectPosZ"], errors="coerce").to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        target = 0.0
    else:
        counts, _ = np.histogram(values, bins=yedges)
        index = int(np.argmax(counts)) if len(counts) else 0
        target = float(np.median(
            values[(values >= yedges[index]) & (values <= yedges[index + 1])]
        )) if len(counts) and counts[index] else float(np.median(values))
    return float(target), "automatic modal-start boundary"


def _transition_edges(
        df: pd.DataFrame, bin_size, bound_pct, split_z=None,
) -> tuple[np.ndarray, np.ndarray, tuple, float, str]:
    """Return the shared zero-centred grid and an independent half split.

    The split is analytical geometry, not bin geometry: moving it must not
    shift every spatial cell or make transition maps incomparable with the
    occupancy and Gandiva grids.
    """
    xedges, base_yedges, rng = _heatmap_edges(
        df, bin_size=bin_size, bound_pct=bound_pct)
    if split_z in (None, ""):
        split_value, split_source = _transition_default_split(
            df, base_yedges)
    else:
        try:
            split_value = float(split_z)
        except (TypeError, ValueError):
            split_value = np.nan
        if np.isfinite(split_value):
            split_source = "manual split line"
        else:
            split_value, split_source = _transition_default_split(
                df, base_yedges)

    return xedges, base_yedges, rng, split_value, split_source


def _transition_group_probabilities(
        frame: pd.DataFrame, xedges: np.ndarray, yedges: np.ndarray,
        split_z: float) -> dict[str, np.ndarray]:
    """Count unique trial transitions from each spatial cell.

    Each ``(_seg_id, cell)`` contributes once, at its first cell entry. The
    future suffix minimum/maximum and final Z are computed with vectorised
    groupby transforms, so repeated visits never inflate the denominator.
    """
    nx, nz = len(xedges) - 1, len(yedges) - 1
    size = nx * nz
    empty = np.zeros(size, dtype=np.int64)
    if frame is None or len(frame) == 0 or size <= 0:
        return {
            "entrants": empty.reshape(nz, nx),
            "crossed": empty.reshape(nz, nx),
            "ended": empty.reshape(nz, nx),
            "side": empty.reshape(nz, nx),
        }

    x = pd.to_numeric(
        frame["GameObjectPosX"], errors="coerce").to_numpy(dtype=float)
    z = pd.to_numeric(
        frame["GameObjectPosZ"], errors="coerce").to_numpy(dtype=float)
    seg = frame["_seg_id"].astype(str).to_numpy()
    ix = np.searchsorted(xedges, x, side="right") - 1
    iz = np.searchsorted(yedges, z, side="right") - 1
    # Match numpy.histogram2d: its final edge belongs to the final bin.
    ix[x == xedges[-1]] = nx - 1
    iz[z == yedges[-1]] = nz - 1
    good = (
        np.isfinite(x) & np.isfinite(z)
        & (ix >= 0) & (ix < nx) & (iz >= 0) & (iz < nz)
    )
    if not np.any(good):
        return {
            "entrants": empty.reshape(nz, nx),
            "crossed": empty.reshape(nz, nx),
            "ended": empty.reshape(nz, nx),
            "side": empty.reshape(nz, nx),
        }

    positions = np.flatnonzero(good)
    flat = (iz[good] * nx + ix[good]).astype(np.int64, copy=False)
    pairs = pd.DataFrame({
        "_seg_id": seg[good],
        "cell": flat,
        "position": positions,
    })
    first = pairs.loc[
        ~pairs.duplicated(["_seg_id", "cell"], keep="first")]
    entry_cell = first["cell"].to_numpy(dtype=np.int64)
    entry_position = first["position"].to_numpy(dtype=np.int64)

    seg_series = pd.Series(seg, copy=False)
    z_series = pd.Series(z, copy=False)
    reversed_seg = seg_series.iloc[::-1].reset_index(drop=True)
    reversed_z = z_series.iloc[::-1].reset_index(drop=True)
    future_min = (
        reversed_z.groupby(reversed_seg, sort=False, observed=True)
        .cummin().to_numpy(dtype=float)[::-1]
    )
    future_max = (
        reversed_z.groupby(reversed_seg, sort=False, observed=True)
        .cummax().to_numpy(dtype=float)[::-1]
    )
    final_z = (
        z_series.groupby(seg_series, sort=False, observed=True)
        .transform("last").to_numpy(dtype=float)
    )

    entry_iz = entry_cell // nx
    lower = yedges[entry_iz + 1] <= split_z
    upper = yedges[entry_iz] >= split_z
    unambiguous = lower | upper
    entry_cell = entry_cell[unambiguous]
    entry_position = entry_position[unambiguous]
    lower = lower[unambiguous]
    upper = upper[unambiguous]

    crossed_success = (
        (lower & (future_max[entry_position] > split_z))
        | (upper & (future_min[entry_position] < split_z))
    )
    ended_success = (
        (lower & (final_z[entry_position] > split_z))
        | (upper & (final_z[entry_position] < split_z))
    )
    entrants = np.bincount(entry_cell, minlength=size).astype(np.int64)
    crossed = np.bincount(
        entry_cell, weights=crossed_success.astype(np.int64),
        minlength=size).round().astype(np.int64)
    ended = np.bincount(
        entry_cell, weights=ended_success.astype(np.int64),
        minlength=size).round().astype(np.int64)

    side = np.zeros((nz, nx), dtype=np.int8)
    side[yedges[1:] <= split_z, :] = -1
    side[yedges[:-1] >= split_z, :] = 1
    return {
        "entrants": entrants.reshape(nz, nx),
        "crossed": crossed.reshape(nz, nx),
        "ended": ended.reshape(nz, nx),
        "side": side,
    }


def _transition_variant(results, outcome: str, min_trials: int) -> dict:
    """Materialise one selected transition definition from shared counts."""
    outcome = outcome if outcome in TRANSITION_OUTCOMES else "crossed"
    probability_values, count_values, custom_values = [], [], []
    fraction_x_marginals, fraction_z_marginals = [], []
    count_x_marginals, count_z_marginals = [], []
    total_entrants = total_successes = informative_cells = 0
    maximum_successes = 0
    for result in results:
        entrants = result["entrants"].astype(np.int64, copy=False)
        successes = result[outcome].astype(np.int64, copy=False)
        side = result["side"]
        probability = np.divide(
            100.0 * successes,
            entrants,
            out=np.full(entrants.shape, np.nan, dtype=float),
            where=entrants > 0,
        )
        informative = (entrants >= min_trials) & (side != 0)
        probability[~informative] = np.nan
        success_count = successes.astype(float)
        success_count[~informative] = np.nan
        stayed = entrants - successes
        raw_probability = np.divide(
            100.0 * successes,
            entrants,
            out=np.zeros(entrants.shape, dtype=float),
            where=entrants > 0,
        )
        custom = np.stack(
            [successes, entrants, stayed, raw_probability], axis=-1)
        probability_values.append(probability.tolist())
        count_values.append(success_count.tolist())
        custom_values.append(custom.tolist())
        visible_entrants = np.where(informative, entrants, 0)
        visible_successes = np.where(informative, successes, 0)
        x_entrants = visible_entrants.sum(axis=0)
        z_entrants = visible_entrants.sum(axis=1)
        x_successes = visible_successes.sum(axis=0)
        z_successes = visible_successes.sum(axis=1)
        fraction_x_marginals.append(np.divide(
            100.0 * x_successes,
            x_entrants,
            out=np.full(x_entrants.shape, np.nan, dtype=float),
            where=x_entrants > 0,
        ).tolist())
        fraction_z_marginals.append(np.divide(
            100.0 * z_successes,
            z_entrants,
            out=np.full(z_entrants.shape, np.nan, dtype=float),
            where=z_entrants > 0,
        ).tolist())
        count_x_marginals.append(
            x_successes.astype(float, copy=False).tolist())
        count_z_marginals.append(
            z_successes.astype(float, copy=False).tolist())
        total_entrants += int(entrants.sum())
        total_successes += int(successes.sum())
        informative_cells += int(np.count_nonzero(informative))
        if np.any(informative):
            maximum_successes = max(
                maximum_successes,
                int(successes[informative].max(initial=0)),
            )
    return {
        "customdata": custom_values,
        "displays": {
            "fraction": {
                "z": probability_values,
                "x_marginal": fraction_x_marginals,
                "z_marginal": fraction_z_marginals,
                "zmin": 0,
                "zmax": 100,
                "colorbar": {
                    "title": {
                        "text": "opposite-half<br>transition (%)",
                    },
                    "thickness": 12,
                    "len": 0.5,
                    "tickvals": [0, 25, 50, 75, 100],
                    "ticktext": ["0", "25", "50", "75", "100"],
                },
                "hovertemplate": (
                    "x=%{x:.2f} z=%{y:.2f}<br>"
                    "<b>%{z:.1f}% transitioned</b><br>"
                    "%{customdata[0]:,.0f}/%{customdata[1]:,.0f} "
                    "entering trials<br>"
                    "%{customdata[2]:,.0f} did not transition"
                    f"<br>{TRANSITION_OUTCOMES[outcome]}<extra></extra>"
                ),
                "x_marginal_hovertemplate": (
                    "X=%{x:.2f}<br><b>%{y:.1f}% transitioned</b>"
                    "<extra>X marginal</extra>"
                ),
                "z_marginal_hovertemplate": (
                    "Z=%{y:.2f}<br><b>%{x:.1f}% transitioned</b>"
                    "<extra>Z marginal</extra>"
                ),
            },
            "count": {
                "z": count_values,
                "x_marginal": count_x_marginals,
                "z_marginal": count_z_marginals,
                "zmin": 0,
                "zmax": max(1, maximum_successes),
                "colorbar": {
                    "title": {
                        "text": "successful<br>trials (n)",
                    },
                    "thickness": 12,
                    "len": 0.5,
                },
                "hovertemplate": (
                    "x=%{x:.2f} z=%{y:.2f}<br>"
                    "<b>%{z:,.0f} successful trials</b><br>"
                    "%{customdata[0]:,.0f}/%{customdata[1]:,.0f} "
                    "entering trials "
                    "(%{customdata[3]:.1f}%)<br>"
                    "%{customdata[2]:,.0f} did not transition"
                    f"<br>{TRANSITION_OUTCOMES[outcome]}<extra></extra>"
                ),
                "x_marginal_hovertemplate": (
                    "X=%{x:.2f}<br><b>%{y:,.0f} successful trials</b>"
                    "<extra>X marginal</extra>"
                ),
                "z_marginal_hovertemplate": (
                    "Z=%{y:.2f}<br><b>%{x:,.0f} successful trials</b>"
                    "<extra>Z marginal</extra>"
                ),
            },
        },
        "total_entrants": total_entrants,
        "total_successes": total_successes,
        "informative_cells": informative_cells,
    }


def _add_transition_marginals(fig, display, group_names, xc, yc,
                              nrows, ncols):
    """Attach compact top/right conditional-transition marginals per panel."""
    total_main_axes = nrows * ncols
    main_fraction = 0.82
    gap_fraction = 0.025
    line_color = _visual(
        "gandiva", "marginal_line", "rgba(183,126,28,0.92)")
    fill_color = _visual(
        "gandiva", "marginal_fill", "rgba(218,164,55,0.20)")
    for index, raw_name in enumerate(group_names):
        main_x_ref, main_y_ref = _subplot_axis(index + 1)
        main_x_key = _layout_axis_key(main_x_ref)
        main_y_key = _layout_axis_key(main_y_ref)
        xdomain = list(getattr(fig.layout, main_x_key).domain)
        ydomain = list(getattr(fig.layout, main_y_key).domain)
        xspan = xdomain[1] - xdomain[0]
        yspan = ydomain[1] - ydomain[0]
        x_main_end = xdomain[0] + main_fraction * xspan
        y_main_end = ydomain[0] + main_fraction * yspan
        x_margin_start = xdomain[0] + (main_fraction + gap_fraction) * xspan
        y_margin_start = ydomain[0] + (main_fraction + gap_fraction) * yspan
        getattr(fig.layout, main_x_key).domain = [xdomain[0], x_main_end]
        getattr(fig.layout, main_y_key).domain = [ydomain[0], y_main_end]

        top_number = total_main_axes + index * 2 + 1
        side_number = top_number + 1
        top_x_ref, top_y_ref = f"x{top_number}", f"y{top_number}"
        side_x_ref, side_y_ref = f"x{side_number}", f"y{side_number}"
        fig.update_layout(**{
            _layout_axis_key(top_x_ref): dict(
                domain=[xdomain[0], x_main_end], anchor=top_y_ref,
                matches=main_x_ref, showticklabels=False, showgrid=False,
                zeroline=False, ticks="",
            ),
            _layout_axis_key(top_y_ref): dict(
                domain=[y_margin_start, ydomain[1]], anchor=top_x_ref,
                rangemode="tozero", showticklabels=False, showgrid=False,
                zeroline=False, fixedrange=True, ticks="",
            ),
            _layout_axis_key(side_x_ref): dict(
                domain=[x_margin_start, xdomain[1]], anchor=side_y_ref,
                rangemode="tozero", showticklabels=False, showgrid=False,
                zeroline=False, fixedrange=True, ticks="",
            ),
            _layout_axis_key(side_y_ref): dict(
                domain=[ydomain[0], y_main_end], anchor=side_x_ref,
                matches=main_y_ref, showticklabels=False, showgrid=False,
                zeroline=False, ticks="",
            ),
        })
        meta = {
            "td_group_value": str(raw_name),
            "td_transition_panel_index": index,
        }
        fig.add_trace(go.Scatter(
            x=xc, y=display["x_marginal"][index],
            xaxis=top_x_ref, yaxis=top_y_ref,
            mode="lines", fill="tozeroy", showlegend=False,
            line=dict(color=line_color, width=1.5), fillcolor=fill_color,
            name="X transition marginal",
            meta={**meta, "td_transition_marginal": "x"},
            hovertemplate=display["x_marginal_hovertemplate"],
        ))
        fig.add_trace(go.Scatter(
            x=display["z_marginal"][index], y=yc,
            xaxis=side_x_ref, yaxis=side_y_ref,
            mode="lines", fill="tozerox", showlegend=False,
            line=dict(color=line_color, width=1.5), fillcolor=fill_color,
            name="Z transition marginal",
            meta={**meta, "td_transition_marginal": "z"},
            hovertemplate=display["z_marginal_hovertemplate"],
        ))


def build_transition_probability_bundle(
        df, group_by="config", pool_mode="separate", ncols=2,
        bin_size=20.0, bound_pct=98.0, split_z=None, min_trials=3,
        outcome="crossed", display_metric="fraction"):
    """Build both transition definitions on the occupancy heatmap grid."""
    if df is None or len(df) == 0:
        return {
            "enabled": True,
            "figure": _msg_figure(
                "No trajectories match the active transition filters."
            ).to_plotly_json(),
            "variants": {},
            "message": "No trajectories match the active transition filters.",
        }
    groups = _group_frames(df, group_by, pool_mode, ncols)
    group_names = list(groups.keys())
    nrows = max(1, (len(group_names) + ncols - 1) // ncols)
    xedges, yedges, rng, split_value, split_source = _transition_edges(
        df, bin_size=bin_size, bound_pct=bound_pct, split_z=split_z)
    min_count = max(1, int(min_trials or 1))
    results = [
        _transition_group_probabilities(
            groups[name], xedges, yedges, split_value)
        for name in group_names
    ]
    variants = {
        key: _transition_variant(results, key, min_count)
        for key in TRANSITION_OUTCOMES
    }
    selected = outcome if outcome in variants else "crossed"
    active = variants[selected]
    selected_metric = (
        display_metric
        if display_metric in TRANSITION_METRICS else "fraction")
    active_display = active["displays"][selected_metric]
    xc = (0.5 * (xedges[:-1] + xedges[1:])).tolist()
    yc = (0.5 * (yedges[:-1] + yedges[1:])).tolist()
    fig = make_subplots(
        rows=nrows, cols=ncols,
        subplot_titles=[_group_label(group_by, name) for name in group_names],
        horizontal_spacing=0.05, vertical_spacing=_subplot_spacing(nrows))
    for index, name in enumerate(group_names):
        fig.add_trace(go.Heatmap(
            x=xc, y=yc,
            z=active_display["z"][index],
            customdata=active["customdata"][index],
            meta={"td_group_value": str(name)},
            colorscale=_visual(
                "transition", "colorscale",
                _VISUAL_STYLE_DEFAULTS["transition"]["colorscale"]),
            zmin=active_display["zmin"],
            zmax=active_display["zmax"],
            connectgaps=False,
            showscale=(index == 0),
            colorbar=active_display["colorbar"],
            hovertemplate=active_display["hovertemplate"],
        ), row=index // ncols + 1, col=index % ncols + 1)
    _apply_axis_sync(
        fig, nrows, ncols, df, uirev="transition_view", rng=rng)
    _add_transition_marginals(
        fig, active_display, group_names, xc, yc, nrows, ncols)
    split_line = _visual(
        "transition", "split_line", "rgba(57,45,76,0.82)")
    for index in range(len(group_names)):
        xref, yref = _subplot_axis(index + 1)
        fig.add_shape(
            type="line", x0=float(xedges[0]), x1=float(xedges[-1]),
            y0=split_value, y1=split_value, xref=xref, yref=yref,
            name="transition-half-split",
            line=dict(color=split_line, width=1.8, dash="dash"),
            layer="above",
        )
    labels = [_group_label(group_by, name) for name in group_names]
    for index, annotation in enumerate(fig.layout.annotations):
        if index < len(group_names):
            annotation.update(
                hovertext=str(group_names[index]), font=dict(size=12))
    fig.update_layout(
        height=60 + nrows * _subplot_px(nrows, ncols),
        margin=dict(l=50, r=80, t=58, b=40),
        template="plotly_white", dragmode="pan", showlegend=False,
        meta={
            "panel_order_values": [str(name) for name in group_names],
            "panel_order_labels": labels,
            "spatial_axis_count": len(group_names),
            "transition_split_z": split_value,
            "transition_split_source": split_source,
            "transition_min_trials": min_count,
            "transition_xedges": xedges.tolist(),
            "transition_yedges": yedges.tolist(),
            "transition_marginals": True,
        },
    )
    message = (
        f"Transition grid ready · split Z={split_value:g} ({split_source}) · "
        f"at least {min_count} entering trial"
        f"{'s' if min_count != 1 else ''} per visible cell."
    )
    signature = "|".join([
        repr(_frame_cache_token(df)),
        str(group_by), str(pool_mode), str(ncols),
        (
            f"x:{float(xedges[0]):.10g}:{float(xedges[-1]):.10g}:"
            f"{len(xedges)}"
        ),
        (
            f"z:{float(yedges[0]):.10g}:{float(yedges[-1]):.10g}:"
            f"{len(yedges)}"
        ),
        f"{split_value:.10g}", str(min_count),
        ",".join(str(name) for name in group_names),
        json.dumps(
            _VISUAL_STYLE.get(
                "transition", _VISUAL_STYLE_DEFAULTS["transition"]),
            sort_keys=True, separators=(",", ":")),
    ])
    return {
        "enabled": True,
        "figure": fig.to_plotly_json(),
        "variants": variants,
        "outcome": selected,
        "display_metric": selected_metric,
        "split_z": split_value,
        "split_source": split_source,
        "min_trials": min_count,
        "xedges": xedges.tolist(),
        "yedges": yedges.tolist(),
        "groups": [str(name) for name in group_names],
        "style": copy.deepcopy(
            _VISUAL_STYLE.get(
                "transition", _VISUAL_STYLE_DEFAULTS["transition"])),
        "message": message,
        "signature": signature,
    }


# ---------------------------------------------------------------------------
# Local direction / abundance field
# ---------------------------------------------------------------------------

FLOW_ARROW_DENSITY_BREAKS = np.array(
    [0.0, 0.12, 0.28, 0.48, 0.72, 1.000001])
FLOW_ARROW_OPACITY = (0.10, 0.24, 0.44, 0.70, 0.94)
FLOW_ARROW_WIDTH = (1.0, 1.35, 1.8, 2.3, 3.0)


def _direction_unit_vectors(df, angle_source="orientation", moving_only=False,
                            walk_thresh=None):
    """Unit heading components aligned to ``df`` plus the source actually used.

    The coordinate convention matches the polar view: ``ux=sin(theta)`` points
    along +X, ``uz=cos(theta)`` points along +Z, and positive angles turn right.
    Movement headings are calculated only within contiguous ``_seg_id`` blocks.
    """
    n = 0 if df is None else len(df)
    source = str(angle_source or "orientation").lower()
    if source not in ("orientation", "movement"):
        source = "orientation"
    if source == "orientation" and (df is None or "GameObjectRotY" not in df):
        source = "movement"
    if n == 0:
        return np.array([], dtype=float), np.array([], dtype=float), source

    if source == "orientation":
        angles = pd.to_numeric(
            df["GameObjectRotY"], errors="coerce").to_numpy(dtype=float)
        angles = np.radians(angles)
        ux = np.sin(angles)
        uz = np.cos(angles)
        invalid = ~np.isfinite(angles)
        ux[invalid] = np.nan
        uz[invalid] = np.nan
    else:
        x = df["GameObjectPosX"].to_numpy(dtype=float)
        z = df["GameObjectPosZ"].to_numpy(dtype=float)
        seg = df["_seg_id"].to_numpy()
        dx = np.empty(n, dtype=float)
        dz = np.empty(n, dtype=float)
        dx[0] = dz[0] = np.nan
        dx[1:] = np.diff(x)
        dz[1:] = np.diff(z)
        starts = np.empty(n, dtype=bool)
        starts[0] = True
        starts[1:] = seg[1:] != seg[:-1]
        dx[starts] = np.nan
        dz[starts] = np.nan
        magnitude = np.hypot(dx, dz)
        with np.errstate(invalid="ignore", divide="ignore"):
            ux = dx / magnitude
            uz = dz / magnitude
        invalid = ~np.isfinite(ux) | ~np.isfinite(uz)
        ux[invalid] = np.nan
        uz[invalid] = np.nan

    if moving_only:
        threshold = float(walk_thresh or 0.0)
        speed = smoothed_velocity(df, 10)
        slow = ~np.isfinite(speed) | (speed < threshold)
        ux[slow] = np.nan
        uz[slow] = np.nan
    return ux, uz, source


def _direction_field_bins(df, group_by, pool_mode, ncols, bin_size, bound_pct,
                          angle_source, moving_only, walk_thresh,
                          metric="time", log_scale=False, cmin=None, cmax=None,
                          crange_mode="value"):
    """Vectorised local circular means on the shared heatmap grid."""
    groups = _group_frames(df, group_by, pool_mode, ncols)
    group_items = list(groups.items())
    xedges, yedges, rng = _heatmap_edges(df, bin_size, bound_pct)
    nx, nz = len(xedges) - 1, len(yedges) - 1
    ux, uz, actual_source = _direction_unit_vectors(
        df, angle_source, moving_only, walk_thresh)
    ux_by_index = pd.Series(ux, index=df.index)
    uz_by_index = pd.Series(uz, index=df.index)
    results = []
    starts = df.drop_duplicates("_seg_id", keep="first")
    start_x = pd.to_numeric(
        starts["GameObjectPosX"], errors="coerce").to_numpy(dtype=float)
    start_z = pd.to_numeric(
        starts["GameObjectPosZ"], errors="coerce").to_numpy(dtype=float)
    good_start = np.isfinite(start_x) & np.isfinite(start_z)
    if np.any(good_start):
        start_hist, _, _ = np.histogram2d(
            start_x[good_start], start_z[good_start],
            bins=[xedges, yedges])
        start_ix, start_iz = np.unravel_index(
            int(np.argmax(start_hist)), start_hist.shape)
        start_cut = (
            float(0.5 * (xedges[start_ix] + xedges[start_ix + 1])),
            float(0.5 * (yedges[start_iz] + yedges[start_iz + 1])),
        )
    else:
        start_cut = (0.0, 0.0)

    for gname, gdf in group_items:
        gx = gdf["GameObjectPosX"].to_numpy(dtype=float)
        gz = gdf["GameObjectPosZ"].to_numpy(dtype=float)
        gux = ux_by_index.loc[gdf.index].to_numpy(dtype=float)
        guz = uz_by_index.loc[gdf.index].to_numpy(dtype=float)
        ix = np.searchsorted(xedges, gx, side="right") - 1
        iz = np.searchsorted(yedges, gz, side="right") - 1
        good = (
            np.isfinite(gx) & np.isfinite(gz)
            & np.isfinite(gux) & np.isfinite(guz)
            & (ix >= 0) & (ix < nx) & (iz >= 0) & (iz < nz)
        )
        flat = (iz[good] * nx + ix[good]).astype(np.int64, copy=False)
        count = np.bincount(flat, minlength=nx * nz).astype(np.int64)
        sum_x = np.bincount(
            flat, weights=gux[good], minlength=nx * nz)
        sum_z = np.bincount(
            flat, weights=guz[good], minlength=nx * nz)
        denom = np.maximum(count, 1)
        mean_x = sum_x / denom
        mean_z = sum_z / denom
        strength = np.hypot(mean_x, mean_z)
        theta = np.degrees(np.arctan2(mean_x, mean_z))
        strength[count == 0] = np.nan
        theta[count == 0] = np.nan
        results.append({
            "name": gname,
            "frame": gdf,
            "count": count,
            "R": strength,
            "theta": theta,
        })

    metric = metric if metric in METRIC_UNITS else "time"
    dt = _median_dt(df)
    metric_arrays = []
    for result in results:
        values = result["count"].astype(float)
        if metric == "time":
            values = values * dt
        elif metric == "percent":
            total = values.sum()
            values = (100.0 * values / total) if total > 0 else values
        result["metric"] = values
        metric_arrays.append(values)

    nonzero_parts = [values[values > 0] for values in metric_arrays
                     if np.any(values > 0)]
    nonzero = (
        np.concatenate(nonzero_parts)
        if nonzero_parts else np.array([], dtype=float)
    )
    auto_lo = (
        float(nonzero.min()) if log_scale and nonzero.size else 0.0)
    auto_hi = (
        max(float(values.max()) for values in metric_arrays)
        if metric_arrays else 1.0)
    if auto_hi <= 0:
        auto_hi = 1.0

    def _resolve(value, default):
        if value is None or value == "":
            return default
        value = float(value)
        if crange_mode == "percentile" and nonzero.size:
            percentile = max(0.0, min(100.0, value))
            return float(np.percentile(nonzero, percentile))
        return value

    metric_min = _resolve(cmin, auto_lo)
    metric_max = _resolve(cmax, auto_hi)
    if metric == "time":
        metric_min = max(metric_min, 0.1)
    if log_scale:
        metric_min = max(metric_min, 1e-9)
    if metric_max <= metric_min:
        metric_max = metric_min * 10 if log_scale else metric_min + 1.0

    if log_scale:
        scale_min = float(np.log10(metric_min))
        scale_max = float(np.log10(metric_max))
    else:
        scale_min = float(metric_min)
        scale_max = float(metric_max)
    scale_span = scale_max - scale_min
    for result in results:
        values = result["metric"]
        display = np.full(values.shape, scale_min, dtype=float)
        occupied = values > 0
        if log_scale:
            display[occupied] = np.log10(
                np.maximum(values[occupied], metric_min))
        else:
            display[occupied] = values[occupied]
        result["abundance"] = np.where(
            occupied,
            np.clip((display - scale_min) / scale_span, 0.0, 1.0),
            0.0,
        )

    return {
        "groups": results,
        "xedges": xedges,
        "yedges": yedges,
        "xc": 0.5 * (xedges[:-1] + xedges[1:]),
        "yc": 0.5 * (yedges[:-1] + yedges[1:]),
        "rng": rng,
        "nrows": max(1, (len(results) + ncols - 1) // ncols),
        "source": actual_source,
        "dt": dt,
        "metric": metric,
        "metric_unit": METRIC_UNITS[metric],
        "metric_scale": "log" if log_scale else "linear",
        "metric_min": float(metric_min),
        "metric_max": float(metric_max),
        "start_cut": start_cut,
    }


def _hsv_rgb_arrays(hue, saturation, value):
    """Vectorised HSV → RGB conversion for equally-shaped arrays."""
    hue = np.mod(np.asarray(hue, dtype=float), 1.0)
    saturation = np.clip(np.asarray(saturation, dtype=float), 0.0, 1.0)
    value = np.clip(np.asarray(value, dtype=float), 0.0, 1.0)
    sector_float = hue * 6.0
    sector = np.floor(sector_float).astype(np.int8) % 6
    frac = sector_float - np.floor(sector_float)
    p = value * (1.0 - saturation)
    q = value * (1.0 - frac * saturation)
    t = value * (1.0 - (1.0 - frac) * saturation)
    red = np.choose(sector, [value, q, p, p, t, value])
    green = np.choose(sector, [t, value, value, q, p, p])
    blue = np.choose(sector, [p, p, t, value, value, q])
    return np.column_stack([red, green, blue])


def _direction_field_rgba(result, nz, nx):
    """RGBA pixels: soft circular hue=direction, saturation=R, alpha=abundance."""
    rgba = np.zeros((nz, nx, 4), dtype=np.uint8)
    occupied = result["count"] > 0
    if not np.any(occupied):
        return rgba
    theta = result["theta"][occupied]
    strength = np.clip(result["R"][occupied], 0.0, 1.0)
    abundance = np.clip(result["abundance"][occupied], 0.0, 1.0)
    hue = np.mod(theta, 360.0) / 360.0
    # Low-R bins are neutral grey rather than displaying a meaningless hue.
    # High-R bins approach a soft, high-chroma cyclic direction colour without
    # the eye-searing value/saturation of a raw HSV wheel.
    saturation = float(_visual(
        "gandiva", "raster_saturation", 0.60)) * strength
    value = float(_visual(
        "gandiva", "raster_value_min", 0.84)) + float(_visual(
            "gandiva", "raster_value_span", 0.09)) * strength
    rgb = np.rint(
        255.0 * _hsv_rgb_arrays(hue, saturation, value)).astype(np.uint8)
    alpha = np.rint(255.0 * abundance).astype(np.uint8)
    pixels = rgba.reshape(-1, 4)
    pixels[occupied, :3] = rgb
    pixels[occupied, 3] = alpha
    return rgba


def _rgba_png_data_uri(rgba):
    """Encode a small RGBA array as a PNG data URI without extra dependencies."""
    rgba = np.ascontiguousarray(np.asarray(rgba, dtype=np.uint8))
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("RGBA image must have shape (height, width, 4)")
    height, width = rgba.shape[:2]
    # PNG scanlines are top-to-bottom; direction grids are stored low-Z first.
    top_down = np.flipud(rgba).reshape(height, width * 4)
    scanlines = np.concatenate(
        [np.zeros((height, 1), dtype=np.uint8), top_down], axis=1).tobytes()

    def chunk(kind, payload):
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return (
            struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", crc)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines, level=6))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _flow_arrow_arrays(x, z, strength, theta_deg, cell_size,
                       max_radius=0.49):
    """NaN-joined direction strokes built in one vectorised allocation."""
    if len(x) == 0:
        return [], []
    theta = np.radians(theta_deg)
    vx, vz = np.sin(theta), np.cos(theta)
    radius = max(0.01, min(0.98, float(max_radius or 0.49)))
    length = radius * float(cell_size) * strength
    tip_x = x + length * vx
    tip_z = z + length * vz

    out_x = np.full(len(x) * 3, np.nan, dtype=float)
    out_z = np.full(len(x) * 3, np.nan, dtype=float)
    out_x[0::3], out_z[0::3] = x, z
    out_x[1::3], out_z[1::3] = tip_x, tip_z
    return out_x.tolist(), out_z.tolist()


def _layout_axis_key(axis_ref):
    """Plotly trace axis ref (``x2``) → layout key (``xaxis2``)."""
    return f"{axis_ref[0]}axis{axis_ref[1:]}"


def _add_direction_marginals(fig, bins, ncols):
    """Add fine marginals and modal-start quadrant percentages to each panel."""
    results = bins["groups"]
    total_main_axes = bins["nrows"] * ncols
    main_fraction = 0.82
    gap_fraction = 0.025
    line_color = _visual(
        "gandiva", "marginal_line", "rgba(183,126,28,0.92)")
    fill_color = _visual(
        "gandiva", "marginal_fill", "rgba(218,164,55,0.20)")
    quadrant_line = _visual(
        "gandiva", "quadrant_line", "rgba(130,91,27,0.58)")
    quadrant_bg = _visual(
        "gandiva", "quadrant_label_bg", "rgba(255,250,235,0.82)")
    unit = bins["metric_unit"]
    # Four subdivisions per heatmap cell preserve the shared extent while
    # producing genuinely finer marginal histograms.
    fine_xedges = np.linspace(
        bins["xedges"][0], bins["xedges"][-1],
        (len(bins["xedges"]) - 1) * 4 + 1)
    fine_yedges = np.linspace(
        bins["yedges"][0], bins["yedges"][-1],
        (len(bins["yedges"]) - 1) * 4 + 1)
    fine_xc = 0.5 * (fine_xedges[:-1] + fine_xedges[1:])
    fine_yc = 0.5 * (fine_yedges[:-1] + fine_yedges[1:])
    cut_x, cut_z = bins.get("start_cut", (0.0, 0.0))

    for index, result in enumerate(results):
        main_x_ref, main_y_ref = _subplot_axis(index + 1)
        main_x_key = _layout_axis_key(main_x_ref)
        main_y_key = _layout_axis_key(main_y_ref)
        xdomain = list(getattr(fig.layout, main_x_key).domain)
        ydomain = list(getattr(fig.layout, main_y_key).domain)
        xspan = xdomain[1] - xdomain[0]
        yspan = ydomain[1] - ydomain[0]
        x_main_end = xdomain[0] + main_fraction * xspan
        y_main_end = ydomain[0] + main_fraction * yspan
        x_margin_start = xdomain[0] + (main_fraction + gap_fraction) * xspan
        y_margin_start = ydomain[0] + (main_fraction + gap_fraction) * yspan

        getattr(fig.layout, main_x_key).domain = [xdomain[0], x_main_end]
        getattr(fig.layout, main_y_key).domain = [ydomain[0], y_main_end]

        top_number = total_main_axes + index * 2 + 1
        side_number = top_number + 1
        top_x_ref, top_y_ref = f"x{top_number}", f"y{top_number}"
        side_x_ref, side_y_ref = f"x{side_number}", f"y{side_number}"
        positions_x = pd.to_numeric(
            result["frame"]["GameObjectPosX"], errors="coerce").to_numpy(dtype=float)
        positions_z = pd.to_numeric(
            result["frame"]["GameObjectPosZ"], errors="coerce").to_numpy(dtype=float)
        spatial_ok = np.isfinite(positions_x) & np.isfinite(positions_z)
        x_marginal, _ = np.histogram(
            positions_x[spatial_ok], bins=fine_xedges)
        z_marginal, _ = np.histogram(
            positions_z[spatial_ok], bins=fine_yedges)
        x_marginal = x_marginal.astype(float)
        z_marginal = z_marginal.astype(float)
        if bins["metric"] == "time":
            x_marginal *= bins["dt"]
            z_marginal *= bins["dt"]
        elif bins["metric"] == "percent":
            x_total = max(float(x_marginal.sum()), 1.0)
            z_total = max(float(z_marginal.sum()), 1.0)
            x_marginal *= 100.0 / x_total
            z_marginal *= 100.0 / z_total

        fig.update_layout(**{
            _layout_axis_key(top_x_ref): dict(
                domain=[xdomain[0], x_main_end], anchor=top_y_ref,
                matches=main_x_ref, showticklabels=False, showgrid=False,
                zeroline=False, ticks="",
            ),
            _layout_axis_key(top_y_ref): dict(
                domain=[y_margin_start, ydomain[1]], anchor=top_x_ref,
                rangemode="tozero", showticklabels=False, showgrid=False,
                zeroline=False, fixedrange=True, ticks="",
            ),
            _layout_axis_key(side_x_ref): dict(
                domain=[x_margin_start, xdomain[1]], anchor=side_y_ref,
                rangemode="tozero", showticklabels=False, showgrid=False,
                zeroline=False, fixedrange=True, ticks="",
            ),
            _layout_axis_key(side_y_ref): dict(
                domain=[ydomain[0], y_main_end], anchor=side_x_ref,
                matches=main_y_ref, showticklabels=False, showgrid=False,
                zeroline=False, ticks="",
            ),
        })
        fig.add_trace(go.Scatter(
            x=fine_xc.tolist(), y=x_marginal.tolist(),
            xaxis=top_x_ref, yaxis=top_y_ref,
            mode="lines", fill="tozeroy", showlegend=False,
            line=dict(color=line_color, width=1.5), fillcolor=fill_color,
            name="X abundance",
            hovertemplate=(
                "X=%{x:.2f}<br>X marginal=%{y:.3g} "
                f"{unit}<extra></extra>"
            ),
        ))
        fig.add_trace(go.Scatter(
            x=z_marginal.tolist(), y=fine_yc.tolist(),
            xaxis=side_x_ref, yaxis=side_y_ref,
            mode="lines", fill="tozerox", showlegend=False,
            line=dict(color=line_color, width=1.5), fillcolor=fill_color,
            name="Z abundance",
            hovertemplate=(
                "Z=%{y:.2f}<br>Z marginal=%{x:.3g} "
                f"{unit}<extra></extra>"
            ),
        ))

        # The cut is the densest shared segment-start bin (usually 0,0). These
        # percentages use spatial samples, independent of heading validity.
        x_valid = positions_x[spatial_ok]
        z_valid = positions_z[spatial_ok]
        total = max(len(x_valid), 1)
        quadrant_masks = (
            (x_valid < cut_x) & (z_valid >= cut_z),
            (x_valid >= cut_x) & (z_valid >= cut_z),
            (x_valid < cut_x) & (z_valid < cut_z),
            (x_valid >= cut_x) & (z_valid < cut_z),
        )
        percentages = [100.0 * np.count_nonzero(mask) / total
                       for mask in quadrant_masks]
        xlo, xhi = float(bins["xedges"][0]), float(bins["xedges"][-1])
        zlo, zhi = float(bins["yedges"][0]), float(bins["yedges"][-1])
        x_left = xlo + 0.07 * (xhi - xlo)
        x_right = xhi - 0.07 * (xhi - xlo)
        z_bottom = zlo + 0.07 * (zhi - zlo)
        z_top = zhi - 0.07 * (zhi - zlo)
        fig.add_shape(
            type="line", x0=cut_x, x1=cut_x, y0=zlo, y1=zhi,
            xref=main_x_ref, yref=main_y_ref,
            line=dict(color=quadrant_line, width=1, dash="dot"))
        fig.add_shape(
            type="line", x0=xlo, x1=xhi, y0=cut_z, y1=cut_z,
            xref=main_x_ref, yref=main_y_ref,
            line=dict(color=quadrant_line, width=1, dash="dot"))
        for xpos, zpos, pct, xanchor, yanchor in (
            (x_left, z_top, percentages[0], "left", "top"),
            (x_right, z_top, percentages[1], "right", "top"),
            (x_left, z_bottom, percentages[2], "left", "bottom"),
            (x_right, z_bottom, percentages[3], "right", "bottom"),
        ):
            fig.add_annotation(
                x=xpos, y=zpos, xref=main_x_ref, yref=main_y_ref,
                text=f"{pct:.1f}%", showarrow=False,
                xanchor=xanchor, yanchor=yanchor,
                bgcolor=quadrant_bg, borderpad=2,
                font=dict(size=9, color="#594324"))


def build_direction_field_figure(
        df, group_by="config", pool_mode="separate", ncols=2,
        bin_size=20.0, bound_pct=98.0, angle_source="orientation",
        moving_only=False, walk_thresh=None, rois=None, reach_radius=3.0,
        show_rois=False, metric="time", log_scale=False, cmin=None, cmax=None,
        crange_mode="value", max_radius=0.49):
    """Local circular direction field with an abundance-weighted colour raster.

    Every occupied spatial bin is a polar summary of its valid sample headings:
    arrow angle / hue are the circular mean, arrow length / saturation are the
    resultant strength R (0..1), and arrow visibility / raster alpha follow the
    active heatmap abundance metric, scale, and colour range.
    """
    if df is None or len(df) == 0:
        return _msg_figure("No trajectories match the active filters.")
    ncols = max(1, int(ncols or 2))
    bins = _direction_field_bins(
        df, group_by, pool_mode, ncols, bin_size, bound_pct,
        angle_source, moving_only, walk_thresh,
        metric=metric, log_scale=log_scale, cmin=cmin, cmax=cmax,
        crange_mode=crange_mode)
    results = bins["groups"]
    if not results:
        return _msg_figure("No panel groups are available for the direction field.")

    fig = make_subplots(
        rows=bins["nrows"], cols=ncols,
        subplot_titles=[
            _group_label(group_by, result["name"]) for result in results
        ],
        horizontal_spacing=0.05,
        vertical_spacing=_subplot_spacing(bins["nrows"]),
    )
    xedges, yedges = bins["xedges"], bins["yedges"]
    xc, yc = bins["xc"], bins["yc"]
    nx, nz = len(xc), len(yc)
    cell_size = min(
        float(np.median(np.diff(xedges))),
        float(np.median(np.diff(yedges))),
    )
    images = []
    occupied_total = 0

    for index, result in enumerate(results):
        row, col = index // ncols + 1, index % ncols + 1
        sx, sy = _subplot_axis(index + 1)
        rgba = _direction_field_rgba(result, nz, nx)
        images.append(dict(
            source=_rgba_png_data_uri(rgba),
            xref=sx, yref=sy,
            x=float(xedges[0]), y=float(yedges[-1]),
            sizex=float(xedges[-1] - xedges[0]),
            sizey=float(yedges[-1] - yedges[0]),
            xanchor="left", yanchor="top",
            sizing="stretch", opacity=1.0, layer="below",
        ))

        occupied = result["count"] > 0
        occupied_total += int(np.count_nonzero(occupied))
        flat = np.flatnonzero(occupied)
        iy, ix = np.divmod(flat, nx)
        cell_x, cell_z = xc[ix], yc[iy]
        cell_count = result["count"][occupied]
        cell_r = result["R"][occupied]
        cell_theta = result["theta"][occupied]
        cell_abundance = result["abundance"][occupied]
        cell_metric = result["metric"][occupied]
        custom = np.column_stack([
            cell_count.astype(float),
            cell_count.astype(float) * bins["dt"],
            cell_metric,
            cell_theta,
            cell_r,
            100.0 * cell_abundance,
        ]).tolist()
        fig.add_trace(go.Scattergl(
            x=cell_x.tolist(), y=cell_z.tolist(),
            mode="markers", showlegend=False,
            marker=dict(size=15, color="rgba(0,0,0,0.001)"),
            customdata=custom,
            hovertemplate=(
                "x=%{x:.2f} z=%{y:.2f}<br>"
                "%{customdata[0]:,.0f} valid heading samples"
                "<br>≈ %{customdata[1]:.2f} s occupancy"
                f"<br>{bins['metric_unit']}=%{{customdata[2]:.3g}}"
                "<br>mean direction=%{customdata[3]:.1f}°"
                "<br>resultant R=%{customdata[4]:.3f}"
                "<br>visible abundance=%{customdata[5]:.0f}%"
                "<extra></extra>"
            ),
        ), row=row, col=col)

        arrow_ok = (
            np.isfinite(cell_r) & np.isfinite(cell_theta)
            & (cell_r >= 0.04) & (cell_abundance >= 0.02)
        )
        density_breaks = np.asarray(_visual(
            "gandiva", "density_breaks",
            FLOW_ARROW_DENSITY_BREAKS.tolist()), dtype=float)
        arrow_opacities = tuple(_visual(
            "gandiva", "arrow_opacities", list(FLOW_ARROW_OPACITY)))
        arrow_widths = tuple(_visual(
            "gandiva", "arrow_widths", list(FLOW_ARROW_WIDTH)))
        if len(density_breaks) != 6:
            density_breaks = FLOW_ARROW_DENSITY_BREAKS
        if len(arrow_opacities) != 5:
            arrow_opacities = FLOW_ARROW_OPACITY
        if len(arrow_widths) != 5:
            arrow_widths = FLOW_ARROW_WIDTH
        tier = np.digitize(
            cell_abundance, density_breaks[1:-1], right=False)
        for tier_index, (opacity, width) in enumerate(
                zip(arrow_opacities, arrow_widths)):
            take = arrow_ok & (tier == tier_index)
            if not np.any(take):
                continue
            arrow_x, arrow_z = _flow_arrow_arrays(
                cell_x[take], cell_z[take], cell_r[take],
                cell_theta[take], cell_size, max_radius=max_radius)
            fig.add_trace(go.Scattergl(
                x=arrow_x, y=arrow_z, mode="lines",
                line=dict(
                    color=_visual("gandiva", "arrow_color", "#594324"),
                    width=float(width)),
                opacity=opacity, hoverinfo="skip", showlegend=False,
                meta={"gandiva_arrow": True},
            ), row=row, col=col)

    fig.update_layout(images=images)

    _apply_axis_sync(
        fig, bins["nrows"], ncols, df, uirev="traj_view", rng=bins["rng"])
    _add_direction_marginals(fig, bins, ncols)
    if show_rois and rois:
        fig.update_layout(
            shapes=_roi_overlay_shapes(
                [(result["name"], result["frame"]) for result in results],
                rois, float(reach_radius or 3.0)),
        )
    source_label = (
        "body orientation" if bins["source"] == "orientation"
        else "movement heading"
    )
    moving_label = (
        f" · moving ≥ {float(walk_thresh or 0):g} units/s"
        if moving_only else ""
    )
    fig.add_annotation(
        xref="paper", yref="paper", x=0.5, y=-0.035,
        xanchor="center", yanchor="top", showarrow=False,
        text=(
            f"{source_label}{moving_label} · hue/angle = mean direction · "
            f"length/saturation = R · opacity/width = "
            f"{bins['metric_unit']} ({bins['metric_scale']}) · "
            "top/right = 4×-fine X/Z marginals · dotted cut = modal start"
        ),
        font=dict(size=10, color="#5b6472"),
    )
    for i, annotation in enumerate(fig.layout.annotations):
        if i < len(results):
            annotation.update(
                hovertext=results[i]["name"], font=dict(size=12))
    fig.update_layout(
        height=92 + bins["nrows"] * int(round(
            1.20 * _subplot_px(bins["nrows"], ncols))),
        margin=dict(l=50, r=45, t=50, b=65),
        template="plotly_white", dragmode="pan", showlegend=False,
        meta={
            "flow_cells": occupied_total,
            "heading_source": bins["source"],
            "max_radius": max(0.01, min(0.98, float(max_radius or 0.49))),
            "abundance_metric": bins["metric"],
            "abundance_scale": bins["metric_scale"],
            "abundance_range": [bins["metric_min"], bins["metric_max"]],
            # The last grid cell can be empty when the active grouping has an
            # odd number of panels.  Region overlays and viewport restoration
            # must only address subplots that actually contain data.
            "spatial_axis_count": len(results),
            "marginals": "active heatmap metric",
            "marginal_resolution_multiplier": 4,
            "quadrant_cut": list(bins.get("start_cut", (0.0, 0.0))),
        },
    )
    return fig


# metric/scale combinations precomputed so the client can swap between them
# instantly (Plotly.restyle, no server round-trip, no re-init flash).
HEATMAP_METRICS = ("time", "percent", "count")
HEATMAP_SCALES = ("lin", "log")


def _heatmap_color_distributions(bins) -> dict:
    """Small browser-safe colour distributions derived from binned matrices.

    Colour-limit changes only need the few thousand occupied heatmap cells, not
    another pass over a multi-million-row dataframe.  The sorted sample is used
    for interactive percentile lookup; exact current figures still come from
    the complete bin matrices in ``_heatmap_variant``.
    """
    out = {}
    for metric in HEATMAP_METRICS:
        mats = _heatmap_metric_mats(bins, metric)
        nonzero = (np.concatenate([m[m > 0].ravel() for m in mats])
                   if mats else np.array([], dtype=float))
        nonzero = nonzero[np.isfinite(nonzero)]
        lo, hi = _range_bounds(nonzero, floor_zero=True,
                               upper_pct=MINI_HIST_UPPER_PCT)
        out[metric] = {
            "values": _sample_for_store(nonzero),
            "lo": float(lo),
            "hi": float(hi),
            "max": float(nonzero.max()) if nonzero.size else 1.0,
            "min_positive": float(nonzero.min()) if nonzero.size else 1.0,
        }
    return out


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
                                max_points=None, metric="time", log_scale=False,
                                include_rois=True):
    """Current ROI-mask heatmap + metric/scale variants for that one state.

    Earlier builds precomputed all four entered-only × tail-trim states. That
    made tab-open expensive on million-row folders and blocked the very plot the
    user was trying to pan. ROI mask toggles now rebuild the current state; metric
    and scale still swap clientside from this state's variants.
    """
    if df_f is None or len(df_f) == 0:
        return _msg_figure("No trajectories match the active filters."), {}, {}
    reach_v = float(reach) if reach else 3.0
    df_view, _ = (
        _roi_apply(df_f, pattern, reach_v, entered_only, trim_tail)
        if entered_only or trim_tail else (df_f, None)
    )
    base = rebase_to_origin(df_view) if (do_rebase and len(df_view)) else df_view
    base = _decimate_frame(base, max_points)
    if len(base) == 0:
        return _msg_figure("No trajectories remain after target filtering."), {}, {}
    xedges, yedges, rng = _heatmap_edges(base, bin_size, bound_pct)
    xc = (0.5 * (xedges[:-1] + xedges[1:])).tolist()
    yc = (0.5 * (yedges[:-1] + yedges[1:])).tolist()
    group_names = list(_group_frames(base, group_by, pool_mode, ncols).keys())
    nrows = max(1, (len(group_names) + ncols - 1) // ncols)
    dt = _median_dt(base)

    e, t = int(bool(entered_only)), int(bool(trim_tail))
    groups = _group_frames(base, group_by, pool_mode, ncols)
    rois = (
        rois_by_config(_load_data(pattern)[2])
        if include_rois and not do_rebase else None
    )
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
    return base_fig, store, _heatmap_color_distributions(bins)


def build_velocity_histogram(df, vel_threshold=None, velocity_values=None):
    if df is None or len(df) == 0:
        return go.Figure().update_layout(height=190, template="plotly_white")

    vel = velocity_all(df) if velocity_values is None else np.asarray(velocity_values)
    vel = vel[np.isfinite(vel)]
    if len(vel) == 0:
        return go.Figure().update_layout(height=190, template="plotly_white")

    cap = float(np.quantile(vel, 0.99))
    shown = vel[(vel >= 0) & (vel <= cap)] if cap > 0 else vel
    edges = _histogram_edges(shown, 0.0, cap, max_bins=144)
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
    edges = _histogram_edges(shown, 0.0, cap, max_bins=120)
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


MINI_HIST_BINS = 72
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
            raw = value.get("range")
            rng = raw.get("x") if isinstance(raw, dict) else raw
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
                         color="#2563eb", x_range=None,
                         uniform_bins=False) -> go.Figure:
    vals = _finite_values(values)
    rng = _numeric_range(selected)
    fig = go.Figure()
    lo, hi = x_range if x_range else _range_bounds(vals)
    plot_vals = vals
    if vals.size and lo is not None and hi is not None:
        plot_vals = vals[(vals >= float(lo)) & (vals <= float(hi))]
    if plot_vals.size:
        edges = (np.linspace(float(lo), float(hi), int(bins) + 1)
                 if uniform_bins else
                 _histogram_edges(plot_vals, lo, hi, max_bins=bins))
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
    explicit = isinstance(rng, dict) and bool(rng.get("explicit"))
    rng = _numeric_range(rng)
    if rng is None or stats_df is None or len(stats_df) == 0 or stat_col not in stats_df:
        return rng
    vals = _finite_values(stats_df[stat_col].to_numpy())
    if vals.size == 0:
        return None
    upper_pct = 99.0 if stat_col == "peak_velocity" else MINI_HIST_UPPER_PCT
    lo, hi = _range_bounds(vals, floor_zero=True, upper_pct=upper_pct)
    span = max(float(hi) - float(lo), 1.0)
    eps = span * 1e-9
    if not explicit and rng[0] <= float(lo) + eps and rng[1] >= float(hi) - eps:
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


def build_initial_heading_distribution(df, ncols=2, bins=36) -> go.Figure:
    """Raw first-sample body-heading distribution for every treatment.

    This is intentionally a load-time diagnostic: it uses the first sorted row
    of each globally unique ``_seg_id`` in the native dataset and never follows
    the interactive analysis filters.  The fixed 36 bins are 10-degree sectors.
    """
    if df is None or len(df) == 0:
        return _msg_figure("No raw segments are available for starting headings.", 360)
    if "GameObjectRotY" not in df:
        return _msg_figure("GameObjectRotY is unavailable; starting headings cannot be shown.",
                           360)
    first = df.drop_duplicates("_seg_id", keep="first")
    angles = pd.to_numeric(first["GameObjectRotY"], errors="coerce")
    # Shift to [-half-bin, 360-half-bin) so 0° is a sector centre rather than
    # the edge shared by the 350–360° and 0–10° sectors.
    bin_width = 360.0 / max(1, int(bins))
    first = first.assign(
        _initial_heading=np.mod(angles + bin_width / 2.0, 360.0)
        - bin_width / 2.0
    )
    first = first[np.isfinite(first["_initial_heading"].to_numpy(dtype=float))]
    if len(first) == 0:
        return _msg_figure("No valid first-sample body headings were found.", 360)

    names = _ordered_values(first["ConfigFile"].dropna().unique())
    ncols = max(1, int(ncols or 2))
    nrows = max(1, (len(names) + ncols - 1) // ncols)
    specs = [[{"type": "polar"} for _ in range(ncols)] for _ in range(nrows)]
    edges = np.linspace(-bin_width / 2.0, 360.0 - bin_width / 2.0,
                        int(bins) + 1)
    centres = (0.5 * (edges[:-1] + edges[1:])).tolist()
    widths = np.diff(edges).tolist()
    titles = []
    counts_by_name = {}
    for name in names:
        vals = first.loc[first["ConfigFile"] == name, "_initial_heading"].to_numpy()
        counts, _ = np.histogram(vals, bins=edges)
        counts_by_name[name] = counts
        titles.append(
            f"{_wrap_subplot_title(humanise_config(name))}<br>{len(vals):,} segment starts")
    fig = make_subplots(
        rows=nrows, cols=ncols, specs=specs, subplot_titles=titles,
        horizontal_spacing=0.08, vertical_spacing=min(0.14, 0.7 / nrows))
    for idx, name in enumerate(names):
        row, col = idx // ncols + 1, idx % ncols + 1
        fig.add_trace(go.Barpolar(
            r=counts_by_name[name].tolist(), theta=centres, width=widths,
            marker_color=COLORS[idx % len(COLORS)], marker_line_width=0.3,
            opacity=0.78, showlegend=False,
            hovertemplate="start heading %{theta:.0f}°<br>%{r:,} segments<extra></extra>"),
            row=row, col=col)
    fig.update_polars(
        angularaxis=dict(rotation=90, direction="clockwise", thetaunit="degrees",
                         tickmode="array", tickvals=list(range(0, 360, 45))),
        radialaxis=dict(angle=90, tickangle=90, rangemode="tozero"),
        bgcolor="white")
    for index, ann in enumerate(fig.layout.annotations):
        ann.update(font=dict(size=10), yshift=14)
        if index < len(names):
            ann.update(hovertext=str(names[index]))
    fig.update_layout(
        height=80 + nrows * 380, template="plotly_white",
        margin=dict(l=45, r=45, t=72, b=35), showlegend=False,
        uirevision="raw_initial_heading")
    return fig


def build_polar_r_histogram(ray: pd.DataFrame | None, r_range=None) -> go.Figure:
    lo, hi = _polar_r_range(r_range)
    values = np.array([], dtype=float)
    if ray is not None and len(ray):
        values = _finite_values(ray["R"].to_numpy(dtype=float))
    fig = build_mini_histogram(
        values, [lo, hi], bins=36, color="#2563eb", x_range=(0, 1),
        uniform_bins=True)
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
    raw_config_order = _ordered_group_values(
        grp["ConfigFile"].astype(str).unique(), "config")
    labels = list(dict.fromkeys(
        humanise_config(raw) for raw in raw_config_order))
    raw_by_label = {
        humanise_config(raw): str(raw) for raw in raw_config_order
    }
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
        hoverinfo="skip", showlegend=False,
        meta={"td_pairing": True}), row=1, col=1)
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
    rgrp = pd.DataFrame()
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
                hoverinfo="skip", showlegend=False,
                meta={"td_pairing": True}), row=2, col=1)
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
        margin=dict(l=60, r=20, t=50, b=80), dragmode="pan",
        meta={
            "panel_order_values": [
                raw_by_label[label] for label in labels],
            "panel_order_labels": labels,
            "td_mixed_group_x": True,
        })
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

    def _paired_side_tests(source, left_column, right_column):
        tests = []
        if source is None or len(source) == 0:
            return tests
        for label, sub in source.groupby("label", sort=False, observed=True):
            left = pd.to_numeric(
                sub[left_column], errors="coerce").to_numpy(dtype=float)
            right = pd.to_numeric(
                sub[right_column], errors="coerce").to_numpy(dtype=float)
            keep = np.isfinite(left) & np.isfinite(right)
            left, right = left[keep], right[keep]
            if len(left) < 2:
                continue
            try:
                p_value = (
                    1.0 if np.allclose(left, right, equal_nan=True)
                    else float(scipy_stats.wilcoxon(
                        left, right, alternative="two-sided").pvalue)
                )
            except ValueError:
                p_value = np.nan
            tests.append({
                "left": str(label), "right": "L↔R",
                "raw_p": p_value,
            })
        return tests

    def _unpaired_side_tests(source, value_column, circular=False):
        tests = []
        if source is None or len(source) == 0:
            return tests
        for label, sub in source.groupby("label", sort=False, observed=True):
            left = pd.to_numeric(
                sub.loc[sub["side"] == "left", value_column],
                errors="coerce",
            ).to_numpy(dtype=float)
            right = pd.to_numeric(
                sub.loc[sub["side"] == "right", value_column],
                errors="coerce",
            ).to_numpy(dtype=float)
            left, right = (
                left[np.isfinite(left)], right[np.isfinite(right)])
            if not len(left) or not len(right):
                continue
            try:
                if circular:
                    test_frame = pd.DataFrame({
                        "theta_deg": np.concatenate([left, right]),
                        "group": (
                            ["left"] * len(left) + ["right"] * len(right)),
                    })
                    p_value, _method = _circular_group_test(test_frame)
                else:
                    p_value = float(scipy_stats.mannwhitneyu(
                        left, right, alternative="two-sided").pvalue)
            except ValueError:
                p_value = np.nan
            tests.append({
                "left": str(label), "right": "L↔R",
                "raw_p": p_value,
            })
        return tests

    # Heading samples are autocorrelated, so reduce them to one circular mean
    # per trial/side before comparing left versus right target bearings.
    ang_trials = pd.DataFrame()
    if len(ang) and "_seg_id" in ang:
        angle_rad = np.radians(pd.to_numeric(
            ang["angle_deg"], errors="coerce").to_numpy(dtype=float))
        angular = ang[["label", "side", "_seg_id"]].copy()
        angular["_sin"] = np.sin(angle_rad)
        angular["_cos"] = np.cos(angle_rad)
        angular = angular.groupby(
            ["label", "side", "_seg_id"],
            sort=False, observed=True,
        )[["_sin", "_cos"]].mean().reset_index()
        angular["angle_deg"] = np.degrees(np.arctan2(
            angular["_sin"].to_numpy(dtype=float),
            angular["_cos"].to_numpy(dtype=float),
        ))
        ang_trials = angular

    panel_tests = [
        _paired_side_tests(grp, "frac_left", "frac_right"),
        _paired_side_tests(
            rgrp, "residence_left", "residence_right"),
        _unpaired_side_tests(ttt, "t"),
        _unpaired_side_tests(ang_trials, "angle_deg", circular=True),
    ]
    for row, tests in enumerate(panel_tests, start=1):
        adjusted = _holm_adjust([item["raw_p"] for item in tests])
        for item, q_value in zip(tests, adjusted):
            item["holm_p"] = (
                float(q_value) if np.isfinite(q_value) else None)
            item["stars"] = _p_stars(q_value)
            if item["left"] not in xpos:
                continue
            xref, yref = _subplot_axis(row)
            q_text = (
                f"{item['holm_p']:.3g}"
                if item["holm_p"] is not None else "n/a"
            )
            fig.add_annotation(
                name=(
                    f"td-stats:"
                    f"{raw_by_label.get(item['left'], item['left'])}"
                ),
                x=xpos[item["left"]], y=0.98,
                xref=xref, yref=f"{yref} domain",
                xanchor="center", yanchor="top",
                showarrow=False, text=f"<b>{item['stars']}</b>",
                hovertext=(
                    f"{item['left']} · left versus right"
                    f"<br>Holm q={q_text}"
                ),
                bgcolor="rgba(255,255,255,0.76)", borderpad=1,
                font=dict(size=10, color="#7a4f00"),
            )
    return fig


def _ray_cache_key(df, moving_only, walk_thresh, color_by, angle_source):
    return (_frame_cache_token(df), bool(moving_only),
            round(float(walk_thresh or 0), 6), color_by or "none",
            angle_source or "orientation",
            round(float(_visual(
                "trajectory", "tortuosity_window_seconds", 2.0)), 6)
            if color_by == "tortuosity" else None)


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
    cols = ["_seg_id", "ConfigFile", "SceneName", "SourceFolder",
            "animal", "VR", "FlyID", "CurrentTrial", "CurrentStep",
            "SourceFile", "StartTime", "R", "theta_deg", "cval",
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
           .groupby("_seg_id", sort=False, observed=True)
           .agg(ux=("ux", "mean"), uz=("uz", "mean"), cval=("cval", "mean"),
                valid_points=("valid", "sum"), n_points=("valid", "size")))
    R = np.hypot(agg["ux"].to_numpy(), agg["uz"].to_numpy())
    theta = np.degrees(np.arctan2(agg["ux"].to_numpy(), agg["uz"].to_numpy()))
    meta_specs = {
        "ConfigFile": ("ConfigFile", "first"),
        "VR": ("VR", "first"),
        "FlyID": ("FlyID", "first"),
        "CurrentTrial": ("CurrentTrial", "first"),
        "CurrentStep": ("CurrentStep", "first"),
        "SourceFile": ("SourceFile", "first"),
        "SceneName": ("SceneName", "first"),
        "SourceFolder": ("SourceFolder", "first"),
        "StartTime": ("Current Time", "first"),
    }
    meta = df.groupby("_seg_id", sort=False, observed=True).agg(**{
        output: spec for output, spec in meta_specs.items()
        if spec[0] in df.columns
    })
    for output in meta_specs:
        if output not in meta:
            meta[output] = ""
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

_POLAR_ANIMAL_HOVER = (
    "<b>%{customdata[2]} @ %{customdata[3]}</b><br>"
    "%{customdata[13]:.0f} trials pooled within animal<br>"
    "config=%{customdata[4]}<br>"
    "R=%{customdata[7]:.2f} theta=%{customdata[8]:.0f}°<br>"
    "valid heading=%{customdata[10]:.0f}/%{customdata[11]:.0f} pts "
    "(%{customdata[12]:.0%})<extra></extra>"
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
        point_values, [pfrac, 1.0], bins=36, color="#7c3aed", x_range=(0, 1),
        uniform_bins=True)
    animal_hist = build_mini_histogram(
        animal_values, [afrac, 1.0], bins=36, color="#0f766e", x_range=(0, 1),
        uniform_bins=True)
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
        pd.to_numeric(
            sub.get("unit_trials", pd.Series(1, index=sub.index)),
            errors="coerce",
        ).fillna(1).to_numpy(dtype=float),
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
    color_by = color_by or "categorical"
    if color_by == "one":
        color_by = "categorical"
    if color_by == "velocity":
        vals = ray["cval"].to_numpy(dtype=float)
        finite = vals[np.isfinite(vals)]
        cmax = float(np.percentile(finite, 99)) if finite.size else 1.0
        return vals, 0.0, cmax, "Mean speed"
    if color_by == "tortuosity":
        vals = ray["cval"].to_numpy(dtype=float)
        finite = vals[np.isfinite(vals)]
        cmax = float(np.percentile(finite, 99)) if finite.size else 1.0
        seconds = float(_visual(
            "trajectory", "tortuosity_window_seconds", 2.0))
        return vals, 1.0, max(1.1, cmax), f"Mean tortuosity ({seconds:g} s)"
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


def _polar_by_animal(ray: pd.DataFrame) -> pd.DataFrame:
    """Collapse trial rays to one sample-weighted circular vector per animal.

    The collapse happens within the active subplot group.  An animal may
    therefore contribute once to each treatment/scene panel it actually
    experienced, but never more than once within that panel.
    """
    if ray is None or len(ray) == 0:
        return ray
    required = {"animal", "R", "theta_deg", "valid_points", "group"}
    if not required.issubset(ray.columns):
        return ray
    work = ray.copy()
    radius = pd.to_numeric(work["R"], errors="coerce").to_numpy(dtype=float)
    theta = np.radians(pd.to_numeric(
        work["theta_deg"], errors="coerce").to_numpy(dtype=float))
    weight = pd.to_numeric(
        work["valid_points"], errors="coerce").to_numpy(dtype=float)
    good = (
        np.isfinite(radius) & np.isfinite(theta)
        & np.isfinite(weight) & (weight > 0)
    )
    work = work.loc[good].copy()
    if len(work) == 0:
        return work
    radius, theta, weight = radius[good], theta[good], weight[good]
    work["_wx"] = weight * radius * np.sin(theta)
    work["_wz"] = weight * radius * np.cos(theta)
    work["_weight"] = weight
    work["_trial_count"] = 1
    if "cval" in work:
        cval = pd.to_numeric(work["cval"], errors="coerce").to_numpy(dtype=float)
        work["_weighted_cval"] = np.where(
            np.isfinite(cval), cval * weight, 0.0)
        work["_cval_weight"] = np.where(np.isfinite(cval), weight, 0.0)
    else:
        work["_weighted_cval"] = 0.0
        work["_cval_weight"] = 0.0

    keys = ["group", "animal"]
    sums = work.groupby(
        keys, sort=False, observed=True, dropna=False,
    ).agg(
        _wx=("_wx", "sum"),
        _wz=("_wz", "sum"),
        valid_points=("_weight", "sum"),
        n_points=("n_points", "sum"),
        unit_trials=("_trial_count", "sum"),
        _weighted_cval=("_weighted_cval", "sum"),
        _cval_weight=("_cval_weight", "sum"),
    ).reset_index()
    first_columns = [
        column for column in (
            "ConfigFile", "SceneName", "SourceFolder", "VR", "FlyID",
            "CurrentTrial", "CurrentStep", "SourceFile", "StartTime",
        ) if column in work
    ]
    first = (
        work.groupby(keys, sort=False, observed=True, dropna=False)[
            first_columns
        ].first().reset_index()
        if first_columns else work[keys].drop_duplicates()
    )
    out = sums.merge(first, on=keys, how="left", sort=False)
    total = out["valid_points"].to_numpy(dtype=float)
    ux = np.divide(
        out["_wx"].to_numpy(dtype=float), total,
        out=np.zeros(len(out), dtype=float), where=total > 0)
    uz = np.divide(
        out["_wz"].to_numpy(dtype=float), total,
        out=np.zeros(len(out), dtype=float), where=total > 0)
    out["R"] = np.hypot(ux, uz)
    out["theta_deg"] = np.degrees(np.arctan2(ux, uz))
    cweight = out["_cval_weight"].to_numpy(dtype=float)
    out["cval"] = np.divide(
        out["_weighted_cval"].to_numpy(dtype=float), cweight,
        out=np.full(len(out), np.nan), where=cweight > 0)
    out["valid_points"] = out["valid_points"].round().astype(np.int64)
    out["n_points"] = pd.to_numeric(
        out["n_points"], errors="coerce").fillna(0).round().astype(np.int64)
    out["valid_frac"] = np.divide(
        out["valid_points"].to_numpy(dtype=float),
        out["n_points"].to_numpy(dtype=float),
        out=np.zeros(len(out), dtype=float),
        where=out["n_points"].to_numpy(dtype=float) > 0,
    )
    out["_seg_id"] = (
        "animal:" + out["group"].astype(str)
        + "|" + out["animal"].astype(str)
    )
    # Keep the trial colour mode meaningful as the mean trial number.
    work["_numeric_trial"] = pd.to_numeric(
        work.get("CurrentTrial", pd.Series(np.nan, index=work.index)),
        errors="coerce",
    )
    trial_mean = work.groupby(
        keys, sort=False, observed=True, dropna=False)["_numeric_trial"].mean()
    if len(trial_mean):
        out["CurrentTrial"] = [
            trial_mean.get((group, animal), np.nan)
            for group, animal in zip(out["group"], out["animal"])
        ]
    out["CurrentStep"] = out.get("CurrentStep", "")
    for column in (
            "ConfigFile", "SceneName", "SourceFolder", "VR", "FlyID",
            "SourceFile", "StartTime"):
        if column not in out:
            out[column] = ""
    return out.drop(columns=[
        "_wx", "_wz", "_weighted_cval", "_cval_weight",
    ], errors="ignore")


def _population_polar_vector(
        ray: pd.DataFrame, *, equal_units=False) -> tuple[float, float, int]:
    """Exact pooled mean of all valid circular samples represented by rays.

    A trial ray is the mean of its unit sample vectors. Multiplying that vector
    by ``valid_points`` before pooling reconstructs the same population vector
    as concatenating every valid sample, without expanding the data again.
    """
    if ray is None or len(ray) == 0:
        return 0.0, 0.0, 0
    r = ray["R"].to_numpy(dtype=float)
    th = np.radians(ray["theta_deg"].to_numpy(dtype=float))
    w = (
        np.ones(len(ray), dtype=float)
        if equal_units else ray["valid_points"].to_numpy(dtype=float)
    )
    good = np.isfinite(r) & np.isfinite(th) & np.isfinite(w) & (w > 0)
    if not np.any(good):
        return 0.0, 0.0, 0
    r, th, w = r[good], th[good], w[good]
    total = float(w.sum())
    vx = float(np.sum(w * r * np.sin(th)) / total)
    vz = float(np.sum(w * r * np.cos(th)) / total)
    support = int(np.sum(good)) if equal_units else int(total)
    return math.hypot(vx, vz), math.degrees(math.atan2(vx, vz)), support


def _heading_time_frame(
        df: pd.DataFrame, *, angle_source="orientation", moving_only=False,
        walk_thresh=None, r_range=None, min_point_frac=0.0,
        min_animal_trial_frac=0.0) -> tuple[pd.DataFrame, str]:
    """Return quality-filtered sample headings and segment-local elapsed time.

    The same segment-level Rayleigh gates as the polar view decide which whole
    trials enter this panel.  Sample headings themselves stay at their native
    cadence; no per-segment loop or sort is introduced.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(), str(angle_source or "orientation")

    ux, uz, source = _direction_unit_vectors(
        df, angle_source=angle_source, moving_only=moving_only,
        walk_thresh=walk_thresh)
    ray = rayleigh_by_segment(
        df, moving_only=moving_only, walk_thresh=walk_thresh,
        color_by="none", angle_source=source)
    ray, _ = _filter_polar_ray_table(
        ray, r_range, min_point_frac, min_animal_trial_frac)
    if ray is None or len(ray) == 0:
        return pd.DataFrame(), source
    keep = df["_seg_id"].isin(pd.unique(ray["_seg_id"])).to_numpy()
    if not np.any(keep):
        return pd.DataFrame(), source

    seg = df["_seg_id"].to_numpy()
    starts = np.empty(len(df), dtype=bool)
    starts[0] = True
    starts[1:] = seg[1:] != seg[:-1]
    if "Current Time" in df:
        times_ns = pd.to_datetime(
            df["Current Time"], errors="coerce").astype("int64").to_numpy()
        start_indices = np.maximum.accumulate(
            np.where(starts, np.arange(len(df)), 0))
        nat = np.iinfo(np.int64).min
        valid_time = (times_ns != nat) & (times_ns[start_indices] != nat)
        elapsed = np.full(len(df), np.nan, dtype=float)
        elapsed[valid_time] = (
            times_ns[valid_time] - times_ns[start_indices[valid_time]]
        ) / 1e9
    else:
        positions = np.arange(len(df)) - np.maximum.accumulate(
            np.where(starts, np.arange(len(df)), 0))
        elapsed = positions.astype(float)

    heading = np.degrees(np.arctan2(ux, uz))
    columns = [
        column for column in (
            "_seg_id", "ConfigFile", "SceneName", "SourceFolder", "animal",
            "VR", "FlyID", "CurrentTrial", "CurrentStep", "SourceFile",
            "Current Time",
        ) if column in df
    ]
    work = df.loc[keep, columns].copy()
    work["_heading_deg"] = heading[keep]
    work["_elapsed_s"] = elapsed[keep]
    if "animal" not in work:
        fly = (
            work["FlyID"].astype(str)
            if "FlyID" in work else pd.Series("", index=work.index)
        )
        vr = (
            work["VR"].astype(str)
            if "VR" in work else pd.Series("", index=work.index)
        )
        work["animal"] = (
            fly + "@" + vr
        )
    work.attrs["_frame_token"] = (
        "heading-time", _frame_cache_token(df), source, bool(moving_only),
        round(float(walk_thresh or 0), 6), _polar_r_range(r_range),
        round(_frac_value(min_point_frac), 6),
        round(_frac_value(min_animal_trial_frac), 6), int(len(work)),
    )
    return work, source


def _heading_time_join(
        elapsed, heading, series_ids, customdata=None, max_gap=None):
    """Insert gaps at trial boundaries and signed-angle wrap crossings."""
    elapsed = np.asarray(elapsed, dtype=float)
    heading = np.asarray(heading, dtype=float)
    series_ids = np.asarray(series_ids)
    if len(elapsed) == 0:
        return elapsed.tolist(), heading.tolist(), []
    boundary = series_ids[1:] != series_ids[:-1]
    if max_gap is not None and np.isfinite(float(max_gap)):
        boundary |= (
            np.isfinite(elapsed[1:]) & np.isfinite(elapsed[:-1])
            & ((elapsed[1:] - elapsed[:-1]) > float(max_gap))
        )
    wrap = (
        np.isfinite(heading[1:]) & np.isfinite(heading[:-1])
        & (np.abs(heading[1:] - heading[:-1]) > 180.0)
    )
    inserts = np.flatnonzero(boundary | wrap) + 1
    x = np.insert(elapsed, inserts, np.nan).tolist()
    y = np.insert(heading, inserts, np.nan).tolist()
    joined_custom = []
    if customdata is not None:
        custom = np.asarray(customdata, dtype=object)
        if custom.ndim == 1:
            custom = custom.reshape(-1, 1)
        gap = np.empty((len(inserts), custom.shape[1]), dtype=object)
        gap[:] = ""
        joined_custom = np.insert(custom, inserts, gap, axis=0).tolist()
    return x, y, joined_custom


def _heading_window(work: pd.DataFrame, requested=None) -> dict:
    """Resolve Auto / full-resolution / exact circular averaging windows."""
    native = _median_dt(work)
    native = float(native) if np.isfinite(native) and native > 0 else 1.0
    elapsed = work["_elapsed_s"].to_numpy(dtype=float)
    finite = elapsed[np.isfinite(elapsed)]
    duration = float(np.max(finite)) if finite.size else native
    auto = max(native, duration * 0.01)
    # Align the automatic window with the native cadence while keeping it close
    # to one percent of the longest selected trial.
    auto = native * max(1, int(round(auto / native)))
    if requested in (None, ""):
        effective, mode, normalised = auto, "auto", "auto"
    else:
        try:
            value = float(requested)
        except (TypeError, ValueError):
            value = np.nan
        if not np.isfinite(value) or value < 0:
            effective, mode, normalised = auto, "auto", "auto"
        elif value == 0:
            effective, mode, normalised = native, "full", "full"
        else:
            effective, mode, normalised = max(native, value), "custom", value
    return {
        "seconds": float(effective), "mode": mode,
        "requested": normalised, "native_seconds": native,
        "auto_seconds": float(auto), "duration_seconds": duration,
    }


def _heading_trial_time_bins(work: pd.DataFrame, window_seconds: float) -> pd.DataFrame:
    """Circularly average samples inside each trial/time window."""
    valid = work[
        np.isfinite(work["_elapsed_s"].to_numpy(dtype=float))
        & np.isfinite(work["_heading_deg"].to_numpy(dtype=float))
    ].copy()
    if len(valid) == 0:
        return valid
    angles = np.radians(valid["_heading_deg"].to_numpy(dtype=float))
    valid["_ux"] = np.sin(angles)
    valid["_uz"] = np.cos(angles)
    valid["_time_bin"] = np.floor(
        valid["_elapsed_s"].to_numpy(dtype=float) / window_seconds
        + 1e-9).astype(np.int64)
    metadata = [
        column for column in (
            "CurrentTrial", "CurrentStep", "FlyID", "VR", "ConfigFile",
            "SceneName", "SourceFolder", "SourceFile", "_heading_color_value",
        ) if column in valid
    ]
    aggregations = {"_ux": "mean", "_uz": "mean"}
    aggregations.update({column: "first" for column in metadata})
    aggregations["_elapsed_s"] = "mean"
    trial_bins = valid.groupby(
        ["_seg_id", "animal", "_time_bin"], sort=False,
        observed=True, dropna=False).agg(aggregations)
    trial_bins["n_samples"] = valid.groupby(
        ["_seg_id", "animal", "_time_bin"], sort=False,
        observed=True, dropna=False).size()
    trial_bins = trial_bins.reset_index()
    norm = np.hypot(
        trial_bins["_ux"].to_numpy(dtype=float),
        trial_bins["_uz"].to_numpy(dtype=float))
    usable = np.isfinite(norm) & (norm > 0)
    trial_bins = trial_bins.loc[usable].copy()
    norm = norm[usable]
    trial_bins["_ux"] /= norm
    trial_bins["_uz"] /= norm
    trial_bins["_heading_deg"] = np.degrees(np.arctan2(
        trial_bins["_ux"].to_numpy(dtype=float),
        trial_bins["_uz"].to_numpy(dtype=float)))
    # Use the common window centre so independent trials line up exactly.
    trial_bins["_elapsed_s"] = (
        trial_bins["_time_bin"].to_numpy(dtype=float) + 0.5
    ) * window_seconds
    return trial_bins


def _heading_band_polygon(x, lower, upper, mean, max_gap):
    """Return NaN-separated polygons for one signed circular band part."""
    x = np.asarray(x, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    mean = np.asarray(mean, dtype=float)
    if len(x) < 2:
        return [], []
    valid = (
        np.isfinite(x) & np.isfinite(lower) & np.isfinite(upper)
        & (lower <= upper)
    )
    breaks = (
        ~valid[1:] | ~valid[:-1]
        | ((x[1:] - x[:-1]) > float(max_gap))
        | (np.abs(mean[1:] - mean[:-1]) > 180.0)
    )
    cuts = np.concatenate(([0], np.flatnonzero(breaks) + 1, [len(x)]))
    px, py = [], []
    for start, end in zip(cuts[:-1], cuts[1:]):
        if end - start < 2 or not np.all(valid[start:end]):
            continue
        xx = x[start:end]
        px.extend(xx.tolist() + xx[::-1].tolist() + [np.nan])
        py.extend(upper[start:end].tolist()
                  + lower[start:end][::-1].tolist() + [np.nan])
    return px, py


def _heading_circular_band_parts(x, mean, spread, max_gap):
    """Represent a wrapped ±circular-SD interval as signed-angle polygons."""
    mean = np.asarray(mean, dtype=float)
    spread = np.clip(np.asarray(spread, dtype=float), 0.0, 180.0)
    lower = mean - spread
    upper = mean + spread
    candidates = [
        (np.maximum(lower, -180.0), np.minimum(upper, 180.0)),
        (np.full(len(mean), -180.0), np.where(upper > 180.0, upper - 360.0, np.nan)),
        (np.where(lower < -180.0, lower + 360.0, np.nan), np.full(len(mean), 180.0)),
    ]
    parts = []
    for lo, hi in candidates:
        px, py = _heading_band_polygon(x, lo, hi, mean, max_gap)
        if px:
            parts.append((px, py))
    return parts


def _heading_series_style(
        group: pd.DataFrame, animal: str, color_by: str, group_name: str,
        panel_index: int, group_by: str, palette: list[str],
        roi_outcomes=None, sequential_bounds=None) -> dict:
    """Resolve a static time-series colour from trajectory colour semantics."""
    color_by = str(color_by or "categorical").lower()
    if color_by == "one":
        color_by = "categorical"
    width = float(_visual("trajectory", "line_width", 1.2))
    if color_by in ("none", "gray"):
        return {
            "color": _visual("trajectory", "gray_color", "#737b85"),
            "opacity": 0.72, "width": width,
        }
    if color_by == "categorical":
        entry = _category_style(
            _GROUP_STYLE_KIND.get(str(group_by), ""), str(group_name))
        return {
            "color": entry.get("color", palette[panel_index % len(palette)]),
            "opacity": 0.9,
            "width": float(entry.get("line_width", width)),
        }
    if color_by == "individual":
        entry = _category_style("individual", animal)
        animal_order = sorted(pd.unique(group["animal"].astype(str)))
        index = animal_order.index(animal) if animal in animal_order else 0
        return {
            "color": entry.get("color", palette[index % len(palette)]),
            "opacity": 0.92,
            "width": float(entry.get("line_width", width)),
        }
    category_specs = {
        "config": ("ConfigFile", "config"),
        "scene": ("SceneName", "scene"),
        "vr": ("VR", "vr"),
        "folder": ("SourceFolder", "file"),
    }
    if color_by in category_specs:
        column, kind = category_specs[color_by]
        values = group[column].dropna().astype(str) if column in group else []
        raw = values.mode().iloc[0] if len(values) else str(group_name)
        entry = _category_style(kind, raw)
        ordered = sorted(pd.unique(values)) if len(values) else [raw]
        index = ordered.index(raw) if raw in ordered else 0
        return {
            "color": entry.get("color", palette[index % len(palette)]),
            "opacity": 0.9,
            "width": float(entry.get("line_width", width)),
        }
    if color_by == "roi" and roi_outcomes:
        outcomes = group["_seg_id"].astype(str).map(
            {str(k): str(v) for k, v in roi_outcomes.items()}).fillna("No ROI")
        raw = outcomes.mode().iloc[0] if len(outcomes) else "No ROI"
        return {"color": _ROI_OUTCOME_COLOR.get(raw, _ROI_OUTCOME_COLOR["No ROI"]),
                "opacity": 0.9, "width": width}
    if color_by in {"trial", "local_time", "velocity", "tortuosity"}:
        if color_by == "trial":
            values = pd.to_numeric(group.get("CurrentTrial"), errors="coerce")
        else:
            values = pd.to_numeric(
                group.get("_heading_color_value"), errors="coerce")
        finite = np.asarray(values, dtype=float)
        finite = finite[np.isfinite(finite)]
        lo, hi = sequential_bounds or (0.0, 1.0)
        representative = float(np.median(finite)) if finite.size else lo
        fraction = (representative - lo) / ((hi - lo) or 1.0)
        return {"color": _sample_scale(fraction), "opacity": 0.9, "width": width}
    entry = _category_style("individual", animal)
    return {"color": entry.get("color", palette[panel_index % len(palette)]),
            "opacity": 0.9, "width": width}


def build_heading_time_figure(
        df, group_by="config", pool_mode="separate", ncols=2,
        mode="trial", angle_source="orientation", moving_only=False,
        walk_thresh=None, r_range=None, min_point_frac=0.0,
        min_animal_trial_frac=0.0, max_points=None, window_seconds=None,
        show_variability=False, color_by="categorical", roi_outcomes=None,
        representation="traces", angle_bin_degrees=5.0) -> go.Figure:
    """Heading versus segment-local time as trial paths or animal means.

    Both modes circularly average samples inside a shared elapsed-time window.
    Trial mode emits NaN-separated trial paths per animal/panel. Animal mode
    gives each retained trial one equal directional vote at each time window;
    its optional ribbon is the within-animal circular standard deviation.
    """
    work, source = _heading_time_frame(
        df, angle_source=angle_source, moving_only=moving_only,
        walk_thresh=walk_thresh, r_range=r_range,
        min_point_frac=min_point_frac,
        min_animal_trial_frac=min_animal_trial_frac)
    if len(work) == 0:
        return _msg_figure(
            "No trials pass the active heading-quality filters.")

    color_by = str(color_by or "categorical").lower()
    if color_by in {"velocity", "tortuosity"}:
        values = (
            smoothed_velocity(df, 10)
            if color_by == "velocity" else compute_tortuosity(df)
        )
        work["_heading_color_value"] = pd.Series(
            values, index=df.index).reindex(work.index).to_numpy(dtype=float)
    elif color_by == "local_time":
        duration = work.groupby(
            "_seg_id", sort=False, observed=True)["_elapsed_s"].transform("max")
        work["_heading_color_value"] = (
            work["_elapsed_s"] / duration.replace(0, np.nan)
        ).to_numpy(dtype=float)
    mode = "animal" if str(mode or "trial") == "animal" else "trial"
    representation = (
        "density" if str(representation or "traces") == "density" else "traces"
    )
    try:
        angle_bin_degrees = float(angle_bin_degrees)
    except (TypeError, ValueError):
        angle_bin_degrees = 5.0
    if not np.isfinite(angle_bin_degrees):
        angle_bin_degrees = 5.0
    angle_bin_degrees = float(np.clip(angle_bin_degrees, 1.0, 90.0))
    window = _heading_window(work, window_seconds)
    dt = window["seconds"]
    trial_bins = _heading_trial_time_bins(work, dt)
    if len(trial_bins) == 0:
        return _msg_figure("No usable headings remain in the selected time windows.")
    ncols = max(1, int(ncols or 1))
    groups = _group_frames(trial_bins, group_by, pool_mode, ncols)
    group_names = list(groups.keys())
    nrows = max(1, (len(group_names) + ncols - 1) // ncols)
    titles = [_group_label(group_by, name) for name in group_names]
    fig = make_subplots(
        rows=nrows, cols=ncols, subplot_titles=titles,
        horizontal_spacing=0.055, vertical_spacing=_subplot_spacing(nrows))

    palette = _visual("trajectory", "palette", COLORS)
    if not isinstance(palette, list) or not palette:
        palette = COLORS
    sequential_bounds = None
    if color_by == "trial":
        values = pd.to_numeric(trial_bins["CurrentTrial"], errors="coerce").to_numpy()
    elif color_by in {"local_time", "velocity", "tortuosity"}:
        values = pd.to_numeric(
            trial_bins.get("_heading_color_value"), errors="coerce").to_numpy()
    else:
        values = np.array([])
    finite_values = values[np.isfinite(values)] if len(values) else values
    if len(finite_values):
        sequential_bounds = (float(np.min(finite_values)), float(np.max(finite_values)))
    legend_seen = set()
    plotted_trials = 0
    plotted_animals = set()

    if representation == "density":
        # One independently toggleable time x heading occupancy layer per
        # animal.  Each column is normalised by the number of that animal's
        # contributing trials, so alpha-compositing selected legend entries is
        # an equal-animal density comparison instead of a trial-count contest.
        for panel_index, (group_name, group) in enumerate(groups.items()):
            row, col = panel_index // ncols + 1, panel_index % ncols + 1
            plotted_trials += int(group["_seg_id"].nunique())
            time_bins = np.arange(
                0, int(group["_time_bin"].max()) + 1, dtype=np.int64)
            x_centres = (time_bins.astype(float) + 0.5) * dt
            angle_edges = np.arange(
                -180.0, 180.0 + angle_bin_degrees * 0.5,
                angle_bin_degrees, dtype=float)
            if angle_edges[-1] < 180.0:
                angle_edges = np.append(angle_edges, 180.0)
            else:
                angle_edges[-1] = 180.0
            y_centres = (angle_edges[:-1] + angle_edges[1:]) * 0.5
            for animal in pd.unique(group["animal"].astype(str)):
                sub = group[group["animal"].astype(str) == animal]
                if len(sub) == 0:
                    continue
                style = _heading_series_style(
                    sub, animal, color_by, str(group_name), panel_index,
                    group_by, palette, roi_outcomes, sequential_bounds)
                z = np.zeros((len(y_centres), len(time_bins)), dtype=float)
                time_index = sub["_time_bin"].to_numpy(dtype=np.int64)
                angle_index = np.searchsorted(
                    angle_edges,
                    sub["_heading_deg"].to_numpy(dtype=float),
                    side="right") - 1
                angle_index = np.clip(angle_index, 0, len(y_centres) - 1)
                np.add.at(z, (angle_index, time_index), 1.0)
                contributing = sub.groupby(
                    "_time_bin", sort=False, observed=True
                )["_seg_id"].nunique()
                denominator = np.ones(len(time_bins), dtype=float)
                denominator[contributing.index.to_numpy(dtype=np.int64)] = (
                    contributing.to_numpy(dtype=float)
                )
                z /= denominator[np.newaxis, :]
                show_legend = animal not in legend_seen
                legend_seen.add(animal)
                plotted_animals.add(animal)
                fig.add_trace(go.Heatmap(
                    x=x_centres.tolist(), y=y_centres.tolist(), z=z.tolist(),
                    zmin=0, zmax=1, zsmooth=False,
                    colorscale=[
                        [0.0, _rgba(style["color"], 0.0)],
                        [0.15, _rgba(style["color"], 0.04)],
                        [1.0, _rgba(style["color"], 0.88)],
                    ],
                    opacity=0.82, showscale=False,
                    name=animal, legendgroup=f"animal:{animal}",
                    showlegend=show_legend,
                    meta={"td_group_value": str(group_name),
                          "td_heading_density": True,
                          "td_heading_animal": animal},
                    hovertemplate=(
                        "animal " + animal
                        + "<br>time %{x:.2f} s · heading %{y:.1f}°"
                        "<br>%{z:.1%} of contributing trials<extra></extra>"),
                ), row=row, col=col)
    elif mode == "trial":
        budget = int(max_points or BUDGET_HEADING_TIME)
        decimated = _decimate_frame(trial_bins, budget)
        groups = _group_frames(decimated, group_by, pool_mode, ncols)
        for panel_index, (group_name, group) in enumerate(groups.items()):
            row, col = panel_index // ncols + 1, panel_index % ncols + 1
            plotted_trials += int(group["_seg_id"].nunique())
            for animal in pd.unique(group["animal"].astype(str)):
                sub = group[group["animal"].astype(str) == animal]
                style = _heading_series_style(
                    sub, animal, color_by, str(group_name), panel_index,
                    group_by, palette, roi_outcomes, sequential_bounds)
                custom = np.column_stack([
                    _numeric_labels(sub["CurrentTrial"].to_numpy()),
                    _numeric_labels(sub["CurrentStep"].to_numpy()),
                    sub["FlyID"].astype(str).to_numpy(),
                    sub["VR"].astype(str).to_numpy(),
                    sub["ConfigFile"].astype(str).to_numpy(),
                    sub["SourceFile"].astype(str).to_numpy(),
                    sub["_seg_id"].astype(str).to_numpy(),
                ])
                x, y, joined_custom = _heading_time_join(
                    sub["_elapsed_s"].to_numpy(dtype=float),
                    sub["_heading_deg"].to_numpy(dtype=float),
                    sub["_seg_id"].to_numpy(), custom, max_gap=dt * 1.5)
                show_legend = animal not in legend_seen
                legend_seen.add(animal)
                plotted_animals.add(animal)
                fig.add_trace(go.Scattergl(
                    x=x, y=y, customdata=joined_custom, mode="lines",
                    name=animal, legendgroup=f"animal:{animal}",
                    showlegend=show_legend,
                    line=dict(color=style["color"], width=max(0.8, style["width"])),
                    opacity=min(0.55, style["opacity"]), connectgaps=False,
                    meta={"td_group_value": str(group_name),
                          "td_heading_trials": True},
                    hovertemplate=(
                        "animal %{customdata[2]}@%{customdata[3]}"
                        "<br>trial %{customdata[0]} · step %{customdata[1]}"
                        "<br>time %{x:.2f} s · heading %{y:.1f}°"
                        "<br>%{customdata[4]}<extra></extra>"),
                ), row=row, col=col)
    else:
        budget = max(1, int(max_points or BUDGET_HEADING_TIME))
        for panel_index, (group_name, group) in enumerate(groups.items()):
            row, col = panel_index // ncols + 1, panel_index % ncols + 1
            means = group.groupby(
                ["animal", "_time_bin"], sort=False, observed=True,
                dropna=False).agg(
                    _ux=("_ux", "mean"), _uz=("_uz", "mean"),
                    n_trials=("_seg_id", "nunique"),
                ).reset_index()
            means["_elapsed_s"] = (
                means["_time_bin"].to_numpy(dtype=float) + 0.5
            ) * dt
            means["_heading_deg"] = np.degrees(np.arctan2(
                means["_ux"].to_numpy(dtype=float),
                means["_uz"].to_numpy(dtype=float)))
            resultant = np.hypot(
                means["_ux"].to_numpy(dtype=float),
                means["_uz"].to_numpy(dtype=float))
            resultant = np.clip(resultant, np.finfo(float).tiny, 1.0)
            means["_circular_sd_deg"] = np.degrees(np.sqrt(
                np.maximum(0.0, -2.0 * np.log(resultant))))
            means.loc[means["n_trials"] < 2, "_circular_sd_deg"] = np.nan
            n_series = max(1, int(means["animal"].nunique()))
            points_per_series = max(2, budget // n_series)
            keep = _segment_endpoint_keep(
                means["animal"].astype(str).to_numpy(),
                points_per_segment=points_per_series)
            means = means.loc[keep]
            plotted_trials += int(group["_seg_id"].nunique())
            for animal in pd.unique(means["animal"].astype(str)):
                sub = means[means["animal"].astype(str) == animal]
                source_group = group[group["animal"].astype(str) == animal]
                style = _heading_series_style(
                    source_group, animal, color_by, str(group_name), panel_index,
                    group_by, palette, roi_outcomes, sequential_bounds)
                if show_variability:
                    for band_x, band_y in _heading_circular_band_parts(
                            sub["_elapsed_s"].to_numpy(dtype=float),
                            sub["_heading_deg"].to_numpy(dtype=float),
                            sub["_circular_sd_deg"].to_numpy(dtype=float),
                            dt * 1.5):
                        fig.add_trace(go.Scattergl(
                            x=band_x, y=band_y, mode="lines",
                            line=dict(width=0, color=style["color"]),
                            fill="toself", fillcolor=_rgba(style["color"], 0.15),
                            hoverinfo="skip", showlegend=False,
                            legendgroup=f"animal:{animal}",
                            meta={"td_group_value": str(group_name),
                                  "td_heading_variability": True},
                        ), row=row, col=col)
                x, y, joined_custom = _heading_time_join(
                    sub["_elapsed_s"].to_numpy(dtype=float),
                    sub["_heading_deg"].to_numpy(dtype=float),
                    np.full(len(sub), animal),
                    sub[["n_trials", "_circular_sd_deg"]].to_numpy(),
                    max_gap=dt * 1.5)
                show_legend = animal not in legend_seen
                legend_seen.add(animal)
                plotted_animals.add(animal)
                fig.add_trace(go.Scattergl(
                    x=x, y=y, customdata=joined_custom, mode="lines",
                    name=animal, legendgroup=f"animal:{animal}",
                    showlegend=show_legend,
                    line=dict(color=style["color"], width=max(2.0, style["width"])),
                    opacity=style["opacity"], connectgaps=False,
                    meta={"td_group_value": str(group_name),
                          "td_heading_animal_mean": True},
                    hovertemplate=(
                        "animal " + animal
                        + "<br>time %{x:.2f} s · circular mean %{y:.1f}°"
                        "<br>%{customdata[0]:.0f} retained trials"
                        "<br>circular SD %{customdata[1]:.1f}°"
                        "<extra></extra>"),
                ), row=row, col=col)

    for index, annotation in enumerate(fig.layout.annotations or []):
        if index < len(group_names):
            annotation.update(hovertext=str(group_names[index]),
                              font=dict(size=12))
    source_label = (
        "body orientation" if source == "orientation" else "movement heading")
    legend_top, legend_extra = _horizontal_legend_layout(
        sorted(plotted_animals), ncols)
    fig.update_xaxes(
        showgrid=True, gridcolor="rgba(148,163,184,0.18)", zeroline=False)
    fig.update_yaxes(
        tickmode="array", tickvals=[-180, -90, 0, 90, 180],
        showgrid=True, gridcolor="rgba(148,163,184,0.22)",
        zeroline=True, zerolinecolor="rgba(71,85,105,0.42)")
    # Repeating axis titles on every facet quickly becomes the dominant visual
    # element at high panel counts.  Label only the outer axes, as in a shared
    # small-multiple grid, while retaining ticks and hover detail everywhere.
    for panel_index in range(len(group_names)):
        row, col = panel_index // ncols + 1, panel_index % ncols + 1
        if panel_index:
            fig.update_xaxes(matches="x", row=row, col=col)
        if row == nrows:
            fig.update_xaxes(
                title_text="time from trial start (s)", row=row, col=col)
        if col == 1:
            fig.update_yaxes(
                title_text=f"{source_label} (deg)", row=row, col=col)
    fig.update_layout(
        height=70 + nrows * _subplot_px(nrows, ncols) + legend_extra,
        template="plotly_white", dragmode="pan", hovermode="closest",
        showlegend=bool(plotted_animals),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="left", x=0, font_size=10,
            itemclick="toggle", itemdoubleclick="toggleothers"),
        margin=dict(l=55, r=28, t=legend_top, b=45),
        uirevision="heading_time_view",
        meta={
            "panel_order_values": [str(name) for name in group_names],
            "panel_order_labels": titles,
            "heading_time_mode": mode,
            "heading_representation": representation,
            "heading_angle_bin_degrees": float(angle_bin_degrees),
            "heading_source": source,
            "heading_color_by": color_by,
            "retained_trials": int(plotted_trials),
            "retained_animals": int(len(plotted_animals)),
            "time_bin_seconds": float(dt),
            "window_mode": window["mode"],
            "requested_window": window["requested"],
            "auto_window_seconds": window["auto_seconds"],
            "duration_seconds": window["duration_seconds"],
            "show_variability": bool(show_variability),
            "trial_subset_signature": "|".join([
                "heading-time", repr(_frame_cache_token(work)), mode,
                representation, str(angle_bin_degrees), source,
                str(group_by), str(pool_mode), str(max_points),
            ]),
        },
    )
    return fig


def build_polar_figure(df, group_by="config", pool_mode="separate", ncols=2,
                       color_by="categorical", moving_only=False, walk_thresh=None,
                       max_points=None, rois=None, reach_radius=3.0, show_rois=False,
                       roi_outcomes=None, r_range=None, min_point_frac=0.0,
                       min_animal_trial_frac=0.0, return_summary=False,
                       angle_source="orientation", stats_unit="trial"):
    """One Rayleigh vector per trial or animal from yaw/movement heading.

    Radius ``R`` is circular concentration (0 = scattered, 1 = aligned). Unity's
    left-handed convention is used throughout: 0° forward/+Z and clockwise
    positive. Trial mode uses the exact sample-weighted population mean. Animal
    mode first pools each animal's valid trial samples, then gives every animal
    equal weight in the bold population vector. ROI directions are references.
    """
    if df is None or len(df) == 0:
        return _msg_figure("No trajectories match the active filters.")
    color_by = color_by or "categorical"
    if color_by == "one":
        color_by = "categorical"
    groups = _group_frames(df, group_by, pool_mode, ncols)
    names = list(groups.keys())
    n = len(names)
    nrows = max(1, (n + ncols - 1) // ncols)
    animal_mode = str(stats_unit or "trial") == "animal"
    total_by_group = {}
    for name, group in groups.items():
        if animal_mode:
            animals = (
                group["FlyID"].astype(str) + "@"
                + group["VR"].astype(str)
            )
            total_by_group[name] = int(animals.nunique())
        else:
            total_by_group[name] = int(group["_seg_id"].nunique())
    # seg -> subplot-group map (vectorised via concat of per-group index labels)
    seg_group = pd.concat([pd.Series(gname, index=g["_seg_id"].unique())
                           for gname, g in groups.items()]) if names else pd.Series(dtype=object)
    angle_source = str(angle_source or "orientation").lower()
    if angle_source != "orientation" or "GameObjectRotY" not in df:
        angle_source = "movement"
    ray_metric = color_by if color_by in ("velocity", "tortuosity") else "none"
    ray = rayleigh_by_segment(
        df, moving_only, walk_thresh, ray_metric,
        angle_source=angle_source)
    ray = ray.dropna(subset=["R", "theta_deg"])
    ray, quality = _filter_polar_ray_table(ray, r_range, min_point_frac,
                                           min_animal_trial_frac)
    ray = ray.assign(group=ray["_seg_id"].map(seg_group))
    if animal_mode:
        ray = _polar_by_animal(ray)
    population_ray = ray
    kept_by_group = (
        population_ray.groupby("group", sort=False, observed=True)["_seg_id"]
        .nunique().to_dict() if len(population_ray) else {}
    )
    ray = _thin_ray_table(ray, max_points=max_points)
    seq_vals, seq_cmin, seq_cmax, seq_title = _polar_seq_values(ray, color_by)
    if seq_vals is not None:
        ray = ray.assign(_seq_color=seq_vals)
    ind_color, vr_color, _tmin, _tmax = _color_maps(df)
    category_specs = {
        "config": ("ConfigFile", "config"),
        "scene": ("SceneName", "scene"),
        "folder": ("SourceFolder", "file"),
    }
    category_maps = {}
    if color_by in category_specs:
        column, kind = category_specs[color_by]
        palette = _visual("trajectory", "palette", COLORS)
        if not isinstance(palette, list) or not palette:
            palette = COLORS
        category_maps[color_by] = {
            str(value): _category_style(kind, str(value)).get(
                "color", palette[index % len(palette)])
            for index, value in enumerate(
                pd.unique(df[column].dropna().astype(str)))
        }

    specs = [[{"type": "polar"} for _ in range(ncols)] for _ in range(nrows)]
    vspace = min(0.12, 0.7 / max(nrows, 1))
    titles = [
        (f"{_wrap_subplot_title(_group_label(group_by, name))}<br>"
         f"<sup>{int(kept_by_group.get(name, 0)):,}/"
         f"{total_by_group.get(name, 0):,} "
         f"{'animals' if animal_mode else 'trials'} shown</sup>")
        for name in names
    ]
    fig = make_subplots(rows=nrows, cols=ncols, specs=specs,
                        subplot_titles=titles,
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
                    meta={"td_target_overlay": True},
                    hoverinfo="skip", line=dict(width=1.4, dash="dot",
                    color=_ROI_SIDE_COLOR.get(roi["side"], "#999"))),
                    row=row, col=col)

        if len(sub) == 0:
            continue

        if seq_vals is not None:
            rr, tt, _cd = _polar_segment_arrays(sub, roi_outcomes)
            fig.add_trace(go.Scatterpolar(
                r=rr.tolist(), theta=tt.tolist(), mode="lines", showlegend=False,
                hoverinfo="skip", customdata=_cd.tolist(),
                meta={
                    "td_trial_source": True,
                    "td_independent_unit": "animal" if animal_mode else "trial",
                },
                line=dict(color="rgba(90,96,110,0.32)", width=1)),
                row=row, col=col)
            base_cd = _polar_custom_base(sub, roi_outcomes)
            fig.add_trace(go.Scatterpolar(
                r=sub["R"].to_numpy().tolist(),
                theta=sub["theta_deg"].to_numpy().tolist(),
                mode="markers", showlegend=False, customdata=base_cd.tolist(),
                meta={"td_trial_auxiliary": True},
                hovertemplate=(
                    _POLAR_ANIMAL_HOVER if animal_mode else _POLAR_HOVER),
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
            elif color_by in category_specs:
                keys = sub[category_specs[color_by][0]].astype(str).to_numpy()
            elif color_by == "roi":
                outcome_map = {str(k): str(v) for k, v in (roi_outcomes or {}).items()}
                keys = sub["_seg_id"].astype(str).map(outcome_map).fillna("No ROI").to_numpy()
            elif color_by in ("one", "none", "gray", "categorical"):
                keys = np.full(len(sub), color_by, dtype=object)
            else:
                keys = sub["animal"].astype(str).to_numpy()
            for key in pd.unique(keys):
                m = keys == key
                ss = sub.loc[m]
                rr, tt, cd = _polar_segment_arrays(ss, roi_outcomes)
                if color_by == "vr":
                    colr = vr_color.get(str(key), COLORS[0])
                    label = str(key)
                    legend_group = f"vr:{label}"
                elif color_by in category_specs:
                    _column, kind = category_specs[color_by]
                    entry = _category_style(kind, str(key))
                    label = str(entry.get(
                        "name",
                        humanise_config(str(key))
                        if color_by == "config" else str(key),
                    ))
                    colr = category_maps[color_by].get(str(key), COLORS[0])
                    legend_group = f"{color_by}:{key}"
                elif color_by == "roi":
                    label = str(key)
                    colr = _ROI_OUTCOME_COLOR.get(label, _ROI_OUTCOME_COLOR["No ROI"])
                    legend_group = f"roi:{label}"
                elif color_by in ("one", "none", "gray", "categorical"):
                    if color_by == "categorical":
                        palette = _visual("trajectory", "palette", COLORS)
                        if not isinstance(palette, list) or not palette:
                            palette = COLORS
                        entry = _category_style(
                            _GROUP_STYLE_KIND.get(str(group_by), ""), str(gname))
                        label = _group_label(group_by, gname)
                        colr = entry.get("color", palette[idx % len(palette)])
                    else:
                        label = "All vectors · neutral"
                        colr = _visual(
                            "trajectory", "gray_color", "#737b85")
                    legend_group = f"polar:{color_by}"
                else:
                    animal_key = str(key)
                    label = animal_key
                    if "@" in animal_key:
                        fidv, vrv = animal_key.split("@", 1)
                        colr = ind_color.get((vrv, fidv), COLORS[0])
                        parts = [
                            p for p in (
                                vrv if vrv and vrv != "unknown" else None,
                                f"fly{fidv}" if fidv and fidv != "unknown" else None,
                            )
                            if p
                        ]
                        label = " ".join(parts) or animal_key
                    else:
                        colr = COLORS[0]
                    legend_group = f"individual:{animal_key}"
                fig.add_trace(go.Scatterpolar(
                    r=rr.tolist(), theta=tt.tolist(), mode="lines",
                    name=label, legendgroup=legend_group,
                    showlegend=legend_group not in legend_seen,
                    customdata=cd.tolist(),
                    hovertemplate=(
                        _POLAR_ANIMAL_HOVER if animal_mode else _POLAR_HOVER),
                    meta={
                        "td_trial_source": True,
                        "td_independent_unit": (
                            "animal" if animal_mode else "trial"),
                    },
                    opacity=(
                        float(_visual("trajectory", "gray_opacity", 0.36))
                        if color_by in ("none", "gray") else
                        float(_visual("trajectory", "opacity", 0.58))
                    ),
                    line=dict(
                        color=colr,
                        width=float(_visual(
                            "trajectory", "line_width", 1.2)))),
                    row=row, col=col)
                legend_seen.add(legend_group)

        # Pooled population vector is calculated from the complete, unthinned
        # ray table and weighted by each trial's valid sample count. This exactly
        # reconstructs the circular mean across all underlying samples.
        pop_sub = population_ray[population_ray["group"] == gname]
        Rpop, thpop, n_heading = _population_polar_vector(
            pop_sub, equal_units=animal_mode)
        source_label = "body orientation" if angle_source == "orientation" else "movement heading"
        support_label = (
            f"independent animals={n_heading:,}"
            if animal_mode else f"valid samples={n_heading:,}"
        )
        fig.add_trace(go.Scatterpolar(
            r=[0, Rpop], theta=[thpop, thpop], mode="lines+markers", showlegend=False,
            meta={"td_population": True},
            hovertemplate=(
                f"pooled {source_label}<br>R={Rpop:.3f} θ={thpop:.1f}°"
                f"<br>{support_label}<extra></extra>"),
            line=dict(color="#0b6b2e", width=3),
            marker=dict(size=[0, 7], color="#0b6b2e")), row=row, col=col)
    # 0° at top, clockwise — matches the trajectory frame. R is a 0..1 unit disk.
    fig.update_polars(angularaxis=dict(rotation=90, direction="clockwise",
                                       thetaunit="degrees"),
                      radialaxis=dict(range=[0, 1], angle=90, tickangle=90,
                                      tickvals=[0.25, 0.5, 0.75, 1.0]),
                      bgcolor="white")
    for index, ann in enumerate(fig.layout.annotations):
        ann.update(font=dict(size=10), yshift=17)
        if index < len(names):
            ann.update(hovertext=names[index])
    show_legend = color_by in (
        "individual", "vr", "roi", "config", "scene", "folder")
    legend_labels = [
        trace.name for trace in fig.data
        if bool(getattr(trace, "showlegend", False))
    ]
    legend_top, legend_extra = (
        _horizontal_legend_layout(legend_labels, ncols, base_top=74)
        if show_legend else (54, 0)
    )
    fig.update_layout(
                      height=102 + nrows * _subplot_px(nrows, ncols) + legend_extra,
                      template="plotly_white",
                      margin=dict(l=42, r=112, t=legend_top + 10, b=44),
                      showlegend=show_legend,
                      meta={
                          "stats_unit": (
                              "animal" if animal_mode else "trial"),
                          "panel_order_values": [str(name) for name in names],
                          "panel_order_labels": [
                              _group_label(group_by, name) for name in names
                          ],
                          "trial_subset_signature": "|".join([
                              "polar",
                              repr(_frame_cache_token(df)),
                              str(group_by), str(pool_mode), str(color_by),
                              str(bool(moving_only)), str(walk_thresh),
                              str(angle_source), repr(_polar_r_range(r_range)),
                              str(min_point_frac),
                              str(min_animal_trial_frac),
                              "animal" if animal_mode else "trial",
                              str(max_points),
                          ]),
                      },
                      legend=dict(
                          orientation="h", yanchor="bottom", y=1.02,
                          xanchor="left", x=0, font_size=10,
                          itemclick="toggle", itemdoubleclick="toggleothers",
                      ))
    return (fig, quality) if return_summary else fig


_TRIAL_METRIC_SPECS = (
    ("distance_walked", "Distance walked / trial", "Path length (position units)"),
    ("displacement", "Net displacement / trial", "Start-to-end distance (position units)"),
    (
        "median_local_tortuosity",
        "Local tortuosity / trial",
        "Median 15-sample path/chord ratio (1 = locally straight)",
    ),
    ("median_velocity", "Median velocity / trial", "Smoothed speed (position units/s)"),
)


def _visible_segment_stats(stats: pd.DataFrame | None,
                           frame: pd.DataFrame | None) -> pd.DataFrame:
    if stats is None or frame is None or len(stats) == 0 or len(frame) == 0:
        return pd.DataFrame(columns=(stats.columns if stats is not None else []))
    visible = pd.Index(frame["_seg_id"].astype(str).unique())
    return stats[stats["seg_id"].astype(str).isin(visible)].copy()


def _metric_stat_groups(stats: pd.DataFrame, group_by="config",
                        pool_mode="separate"):
    if pool_mode == "pooled" or group_by == "all":
        return [("All Data", stats)]
    columns = {
        "config": "config",
        "scene": "scene",
        "vr": "vr",
        "flyid": "fly_id",
        "file": "source_folder",
    }
    column = columns.get(group_by, "config")
    if column not in stats:
        return [("All Data", stats)]
    values = _ordered_group_values(
        stats[column].dropna().astype(str).unique(), group_by)
    text = stats[column].astype(str)
    return [(value, stats[text == str(value)]) for value in values]


def _metric_stats_for_unit(stats, group_by="config", pool_mode="separate",
                           stats_unit="trial"):
    """Pool trial summaries to one mean per animal within the active group."""
    if (stats is None or len(stats) == 0
            or str(stats_unit or "trial") != "animal"):
        return stats
    work = stats.copy()
    fly_raw = work.get("fly_id", pd.Series("", index=work.index))
    vr_raw = work.get("vr", pd.Series("", index=work.index))
    folder_raw = work.get("source_folder", pd.Series("", index=work.index))
    fly = fly_raw.astype(object).where(fly_raw.notna(), "").astype(str)
    vr = vr_raw.astype(object).where(vr_raw.notna(), "").astype(str)
    folder = (
        folder_raw.astype(object).where(folder_raw.notna(), "").astype(str)
    )
    fallback = work.get(
        "seg_id", pd.Series(work.index.astype(str), index=work.index)
    ).astype(str)
    work["_animal"] = np.where(
        fly.str.strip().ne(""),
        fly + "@" + vr,
        np.where(folder.str.strip().ne(""), folder + "@" + vr, fallback),
    )
    columns = {
        "config": "config",
        "scene": "scene",
        "vr": "vr",
        "flyid": "fly_id",
        "file": "source_folder",
    }
    active = columns.get(group_by, "config")
    keys = ["_animal"]
    if pool_mode != "pooled" and group_by != "all" and active in work:
        keys.insert(0, active)
    numeric = {
        column: "mean"
        for column, _title, _axis in _TRIAL_METRIC_SPECS
        if column in work
    }
    if "peak_velocity" in work:
        numeric["peak_velocity"] = "mean"
    if "n_points" in work:
        numeric["n_points"] = "sum"
    pooled = work.groupby(
        keys, sort=False, observed=True, dropna=False).agg(numeric).reset_index()
    pooled["seg_id"] = pooled["_animal"].astype(str)
    # Keep all grouping columns available; the active one is already exact and
    # inactive values are merely hover metadata.
    for column in ("config", "scene", "vr", "fly_id", "source_folder"):
        if column in pooled:
            continue
        if column == "fly_id":
            pooled[column] = pooled["_animal"].astype(str)
        else:
            pooled[column] = ""
    if "n_points" not in pooled:
        pooled["n_points"] = 0
    return pooled.drop(columns=["_animal"], errors="ignore")


def build_trial_metrics_figure(stats: pd.DataFrame | None, group_by="config",
                               pool_mode="separate",
                               swarm_max=200, distribution_mode="auto",
                               show_violin_points=True,
                               stats_unit="trial",
                               spatial_unit_scale=1.0,
                               spatial_unit_label="cm") -> go.Figure:
    """Compare robust per-trial movement summaries across the active panel axis.

    Small groups use a jittered point swarm; larger groups use count-scaled
    violins. Both encodings carry the same full-width IQR band and median line.
    Tortuosity is a median of local 15-sample path/chord ratios, avoiding the
    unstable whole-trial distance/displacement shortcut.
    """

    if stats is None or len(stats) == 0:
        return _msg_figure("No per-trial metrics match the active filters.", 430)
    stats = _metric_stats_for_unit(
        stats, group_by, pool_mode, stats_unit)
    groups = [(name, group) for name, group in
              _metric_stat_groups(stats, group_by, pool_mode) if len(group)]
    if not groups:
        return _msg_figure("No per-trial metrics match the active filters.", 430)

    try:
        distance_scale = float(spatial_unit_scale)
    except (TypeError, ValueError):
        distance_scale = 1.0
    if not np.isfinite(distance_scale) or distance_scale <= 0:
        distance_scale = 1.0
    distance_unit = str(spatial_unit_label or "cm").strip() or "cm"
    independent_unit = "animal" if stats_unit == "animal" else "trial"
    axis_titles = {
        "distance_walked": f"Path length ({distance_unit})",
        "displacement": f"Start-to-end distance ({distance_unit})",
        "median_local_tortuosity": (
            "Median 15-sample path/chord ratio (1 = locally straight)"
        ),
        "median_velocity": f"Smoothed speed ({distance_unit}/s)",
    }
    subplot_titles = [
        title.replace("/ trial", f"/ {independent_unit}")
        for _column, title, _axis in _TRIAL_METRIC_SPECS
    ]
    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=subplot_titles,
        horizontal_spacing=0.09, vertical_spacing=0.17,
    )
    half_width = 0.36
    group_sizes = []
    for _raw_name, group in groups:
        for column, _title, _axis in _TRIAL_METRIC_SPECS:
            if column in group:
                values = pd.to_numeric(group[column], errors="coerce")
                group_sizes.append(int(np.isfinite(
                    values.to_numpy(dtype=float)).sum()))
    mark = _distribution_choice(distribution_mode, group_sizes, swarm_max)
    for metric_index, (column, _title, _axis_title) in enumerate(
            _TRIAL_METRIC_SPECS):
        axis_title = axis_titles[column]
        row, col = metric_index // 2 + 1, metric_index % 2 + 1
        ticktext = []
        for group_index, (raw_name, group) in enumerate(groups):
            name = _group_label(group_by, raw_name)
            if column not in group:
                ticktext.append(f"{name}<br>n=0")
                continue
            values = pd.to_numeric(group[column], errors="coerce")
            keep = np.isfinite(values.to_numpy(dtype=float))
            if not keep.any():
                ticktext.append(f"{name}<br>n=0")
                continue
            sub = group.loc[keep]
            y = values.loc[keep].to_numpy(dtype=float)
            if column in {
                    "distance_walked", "displacement", "median_velocity"}:
                y = y * distance_scale
            x_name = f"{name}<br>n={len(y):,}"
            ticktext.append(x_name)
            custom = np.column_stack([
                sub["seg_id"].astype(str).to_numpy(),
                pd.to_numeric(sub["n_points"], errors="coerce")
                .fillna(0).astype(int).to_numpy(),
            ]).tolist()
            color = COLORS[group_index % len(COLORS)]
            hover = (
                f"{axis_title}: %{{y:.4g}}"
                "<br>segment: %{customdata[0]}"
                "<br>source points: %{customdata[1]:,}<extra></extra>"
            )
            if mark == "swarm":
                # Explicit, deterministic jitter avoids opaque box-trace point
                # positioning and remains stable across rerenders.
                rng = np.random.default_rng(
                    1741 + metric_index * 1009 + group_index * 97
                )
                jitter_x = group_index + rng.uniform(
                    -half_width, half_width, len(y)
                )
                fig.add_trace(go.Scatter(
                    x=jitter_x.tolist(), y=y.tolist(), name=name,
                    legendgroup=f"metric:{raw_name}", showlegend=False,
                    mode="markers",
                    marker=dict(color=color, size=5, opacity=0.68),
                    meta={"td_group_value": str(raw_name)},
                    customdata=custom, hovertemplate=hover,
                ), row=row, col=col)
            else:
                show_points = (
                    bool(show_violin_points) and len(y) <= int(swarm_max)
                )
                fig.add_trace(go.Violin(
                    x=[group_index] * len(y), y=y.tolist(), name=name,
                    legendgroup=f"metric:{raw_name}",
                    scalegroup=f"trial-metric:{column}",
                    showlegend=False, scalemode="count", spanmode="hard",
                    box_visible=False, meanline_visible=False,
                    points="all" if show_points else False,
                    jitter=0.22 if show_points else 0,
                    pointpos=0,
                    marker=dict(color=color, size=4, opacity=0.48),
                    width=half_width * 2,
                    line_color=color, fillcolor=color, opacity=0.55,
                    meta={"td_group_value": str(raw_name)},
                    customdata=custom, hovertemplate=hover,
                ), row=row, col=col)

            # The same summary encoding sits above either primary mark. Its
            # width exactly matches the jitter/violin span.
            q1, median, q3 = np.percentile(y, [25, 50, 75])
            fig.add_shape(
                type="rect",
                x0=group_index - half_width,
                x1=group_index + half_width,
                y0=float(q1),
                y1=float(q3),
                fillcolor=_rgba(color, 0.14),
                line=dict(color=_rgba(color, 0.72), width=1.4),
                layer="above",
                name=f"td-group-shape:{raw_name}",
                row=row,
                col=col,
            )
            fig.add_shape(
                type="line",
                x0=group_index - half_width,
                x1=group_index + half_width,
                y0=float(median),
                y1=float(median),
                line=dict(color=_rgba(color, 1.0), width=3),
                layer="above",
                name=f"td-group-shape:{raw_name}",
                row=row,
                col=col,
            )
        fig.update_yaxes(title_text=axis_title, rangemode="tozero", row=row, col=col)
        fig.update_xaxes(
            tickmode="array",
            tickvals=list(range(len(groups))),
            ticktext=ticktext,
            range=[-0.55, max(len(groups) - 0.45, 0.55)],
            tickangle=-22,
            tickfont=dict(size=9),
            row=row,
            col=col,
        )

    fig.update_layout(
        height=820,
        template="plotly_white",
        margin=dict(l=70, r=25, t=72, b=105),
        violinmode="group",
        boxmode="group",
        showlegend=False,
        hovermode="closest",
        meta={
            "panel_order_values": [str(name) for name, _group in groups],
            "panel_order_labels": [
                _group_label(group_by, name) for name, _group in groups
            ],
        },
    )
    fig.add_annotation(
        x=0.5, y=-0.13, xref="paper", yref="paper", showarrow=False,
        text=(
            f"Each observation is one visible "
            f"{'animal mean' if stats_unit == 'animal' else 'trial'}. "
            f"{mark.title()} is used consistently across all four panels. "
            "Shaded bands are IQR; bold lines are medians."
        ),
        font=dict(size=10, color="#667085"),
    )
    return fig


def _p_stars(p_value) -> str:
    try:
        p_value = float(p_value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(p_value):
        return "n/a"
    if p_value < 0.0001:
        return "****"
    if p_value < 0.001:
        return "***"
    if p_value < 0.01:
        return "**"
    if p_value < 0.05:
        return "*"
    return "ns"


def _holm_adjust(p_values):
    """Small dependency-free Holm correction for the four metric omnibus tests."""
    values = np.asarray(p_values, dtype=float)
    adjusted = np.full(values.shape, np.nan, dtype=float)
    finite = np.flatnonzero(np.isfinite(values))
    if not len(finite):
        return adjusted
    ordered = finite[np.argsort(values[finite])]
    running = 0.0
    m = len(ordered)
    for rank, index in enumerate(ordered):
        running = max(running, (m - rank) * values[index])
        adjusted[index] = min(1.0, running)
    return adjusted


def _omnibus_nonparametric(groups, column):
    arrays = []
    for _name, group in groups:
        if column not in group:
            continue
        values = pd.to_numeric(
            group[column], errors="coerce").to_numpy(dtype=float)
        values = values[np.isfinite(values)]
        if len(values):
            arrays.append(values)
    if len(arrays) < 2:
        return np.nan, "not enough groups"
    try:
        if len(arrays) == 2:
            result = scipy_stats.mannwhitneyu(
                arrays[0], arrays[1], alternative="two-sided")
            return float(result.pvalue), "Mann–Whitney U"
        result = scipy_stats.kruskal(*arrays, nan_policy="omit")
        return float(result.pvalue), "Kruskal–Wallis"
    except ValueError:
        return np.nan, "constant / insufficient"


def _pairwise_nonparametric(groups, column):
    """Holm-adjusted two-sided Mann–Whitney comparisons for named groups."""
    prepared = []
    for name, group in groups:
        if column not in group:
            continue
        values = pd.to_numeric(
            group[column], errors="coerce").to_numpy(dtype=float)
        values = values[np.isfinite(values)]
        if len(values):
            prepared.append((str(name), values))
    raw = []
    for left_index in range(len(prepared)):
        for right_index in range(left_index + 1, len(prepared)):
            left_name, left = prepared[left_index]
            right_name, right = prepared[right_index]
            try:
                p_value = float(scipy_stats.mannwhitneyu(
                    left, right, alternative="two-sided").pvalue)
            except ValueError:
                p_value = np.nan
            raw.append({
                "left": left_name,
                "right": right_name,
                "raw_p": p_value,
                "n_left": int(len(left)),
                "n_right": int(len(right)),
            })
    adjusted = _holm_adjust([item["raw_p"] for item in raw])
    for item, q_value in zip(raw, adjusted):
        item["holm_p"] = float(q_value) if np.isfinite(q_value) else None
        item["stars"] = _p_stars(q_value)
    return raw


def _pairwise_label(pairs, group_by=None, max_pairs=6):
    """Compact but explicit significant-pair label for plot annotations."""
    significant = [
        item for item in pairs
        if item.get("holm_p") is not None and item["holm_p"] < 0.05
    ]
    if not pairs:
        return "pairwise n/a"
    if not significant:
        return "pairwise Holm: no q<.05"
    parts = []
    for item in significant[:max_pairs]:
        left = (
            _group_label(group_by, item["left"])
            if group_by else str(item["left"])
        )
        right = (
            _group_label(group_by, item["right"])
            if group_by else str(item["right"])
        )
        parts.append(f"{left}↔{right} {item['stars']}")
    if len(significant) > max_pairs:
        parts.append(f"+{len(significant) - max_pairs} more")
    return "pairwise Holm: " + " · ".join(parts)


def _letter_token(index: int) -> str:
    """Spreadsheet-style compact-letter token: A..Z, AA..AZ, BA..."""
    value = max(0, int(index))
    out = ""
    while True:
        out = chr(ord("A") + value % 26) + out
        value = value // 26 - 1
        if value < 0:
            return out


def _compact_letter_display(names, pairs, alpha=0.05) -> dict[str, str]:
    """Compact letter display from Holm-adjusted pairwise comparisons.

    Groups sharing a letter were not separated by a significant comparison;
    every significant pair shares no letter.  The insert/absorb construction
    keeps the result compact without putting an O(n²) comparison list in a
    subplot title.
    """
    ordered = list(dict.fromkeys(str(name) for name in names))
    if not ordered:
        return {}
    significant = {
        frozenset((str(item.get("left")), str(item.get("right"))))
        for item in (pairs or [])
        if item.get("holm_p") is not None
        and float(item["holm_p"]) < float(alpha)
        and str(item.get("left")) != str(item.get("right"))
    }
    columns = [set(ordered)]
    for pair in significant:
        if len(pair) != 2:
            continue
        left, right = tuple(pair)
        split = []
        for column in columns:
            if left in column and right in column:
                split.extend((column - {left}, column - {right}))
            else:
                split.append(column)
        unique = []
        for column in split:
            if column and column not in unique:
                unique.append(column)
        # A subset column adds no information when a superset remains.
        columns = [
            column for column in unique
            if not any(column < other for other in unique)
        ]
    rank = {name: index for index, name in enumerate(ordered)}
    columns.sort(key=lambda column: (
        min(rank[name] for name in column), -len(column),
        tuple(rank[name] for name in ordered if name in column),
    ))
    labels = {name: "" for name in ordered}
    for index, column in enumerate(columns):
        token = _letter_token(index)
        for name in ordered:
            if name in column:
                labels[name] += token
    return {name: (letters or _letter_token(i))
            for i, (name, letters) in enumerate(labels.items())}


def _compact_stat_marks(names, pairs, group_by=None) -> list[dict]:
    """Browser-ready group labels for distribution/polar annotations."""
    ordered = [str(name) for name in names]
    letters = _compact_letter_display(ordered, pairs)
    return [{
        "group": name,
        "label": _group_label(group_by, name) if group_by else name,
        "letters": letters.get(name, ""),
    } for name in ordered]


def _rayleigh_uniformity_p(angles_deg):
    """Rayleigh uniformity p-value using the standard finite-n expansion."""
    angles = np.radians(np.asarray(angles_deg, dtype=float))
    angles = angles[np.isfinite(angles)]
    n = len(angles)
    if n < 3:
        return np.nan
    resultant = abs(np.exp(1j * angles).sum())
    z_value = resultant * resultant / n
    base = math.exp(-z_value)
    correction = (
        1.0
        + (2.0 * z_value - z_value * z_value) / (4.0 * n)
        - (
            24.0 * z_value - 132.0 * z_value ** 2
            + 76.0 * z_value ** 3 - 9.0 * z_value ** 4
        ) / (288.0 * n * n)
    )
    return float(min(1.0, max(0.0, base * correction)))


def _circular_group_test(ray):
    """Non-parametric circular comparison using pooled-centred angular ranks."""
    if ray is None or len(ray) == 0 or "group" not in ray:
        return np.nan, "not enough groups"
    arrays = []
    labels = []
    for name, group in ray.groupby("group", sort=False, observed=True):
        values = pd.to_numeric(
            group["theta_deg"], errors="coerce").to_numpy(dtype=float)
        values = values[np.isfinite(values)]
        if len(values):
            arrays.append(values)
            labels.append(str(name))
    if len(arrays) < 2:
        return np.nan, "not enough groups"
    pooled = np.concatenate(arrays)
    centre = np.degrees(np.angle(
        np.exp(1j * np.radians(pooled)).mean()))
    centred = [
        ((values - centre + 180.0) % 360.0) - 180.0
        for values in arrays
    ]
    try:
        if len(centred) == 2:
            result = scipy_stats.cramervonmises_2samp(
                centred[0], centred[1], method="auto")
            return float(result.pvalue), "circular CvM"
        result = scipy_stats.kruskal(*centred, nan_policy="omit")
        return float(result.pvalue), "circular rank omnibus"
    except ValueError:
        return np.nan, "constant / insufficient"


def _paired_custom_region_tests(payload, key, stats_unit="trial"):
    """Paired adjacent-window tests within each active panel only.

    Observation windows are interpreted as explicit pairs (1↔2, 3↔4, ...).
    The same trial or animal is aligned across the two windows before a
    Wilcoxon signed-rank test. There are deliberately no cross-panel or
    cross-pair comparisons here.
    """
    panels = (payload or {}).get("panels") or []
    regions = (payload or {}).get("regions") or []
    if not panels or len(regions) < 2:
        return []
    tests = []
    for group_index, panel in enumerate(panels):
        summaries = {
            str(item.get("id")): item
            for item in panel.get("regions", [])
        }
        for left_index in range(0, len(regions) - 1, 2):
            right_index = left_index + 1
            left_region, right_region = regions[left_index], regions[right_index]
            left_id = str(left_region.get("id"))
            right_id = str(right_region.get("id"))
            left_values, left_units, _left_support = (
                _observation_distribution_values(
                    summaries.get(left_id, {}), key, stats_unit)
            )
            right_values, right_units, _right_support = (
                _observation_distribution_values(
                    summaries.get(right_id, {}), key, stats_unit)
            )
            left_map = {
                str(identity): float(value)
                for identity, value in zip(left_units, left_values)
                if np.isfinite(value)
            }
            right_map = {
                str(identity): float(value)
                for identity, value in zip(right_units, right_values)
                if np.isfinite(value)
            }
            common = [
                identity for identity in left_map if identity in right_map
            ]
            left = np.asarray(
                [left_map[identity] for identity in common], dtype=float)
            right = np.asarray(
                [right_map[identity] for identity in common], dtype=float)
            if len(common) < 2:
                p_value = np.nan
            else:
                try:
                    p_value = (
                        1.0 if np.allclose(left, right, equal_nan=True)
                        else float(scipy_stats.wilcoxon(
                            left, right, alternative="two-sided").pvalue)
                    )
                except ValueError:
                    p_value = np.nan
            raw_group = str(
                panel.get("raw", panel.get("name", "Group")))
            tests.append({
                "group": raw_group,
                "group_label": str(
                    panel.get("name", panel.get("raw", "Group"))),
                "group_index": group_index,
                "left": left_id,
                "right": right_id,
                "left_name": str(left_region.get("name", left_id)),
                "right_name": str(right_region.get("name", right_id)),
                "left_index": left_index,
                "right_index": right_index,
                "raw_p": p_value,
                "n": int(len(common)),
            })
    adjusted = _holm_adjust([item["raw_p"] for item in tests])
    for item, q_value in zip(tests, adjusted):
        item["holm_p"] = float(q_value) if np.isfinite(q_value) else None
        item["stars"] = _p_stars(q_value)
    return tests


def _custom_region_stat_labels(payload, stats_unit="trial"):
    """Within-panel paired-window labels for all six diagnostics."""
    keys = (
        "time_percent", "trial_count", "distance_walked", "displacement",
        "median_local_tortuosity", "median_velocity",
    )
    output = []
    for key in keys:
        tests = _paired_custom_region_tests(payload, key, stats_unit)
        if not tests:
            output.append("paired windows n/a")
            continue
        significant = [
            item for item in tests
            if item["holm_p"] is not None and item["holm_p"] < 0.05
        ]
        if not significant:
            output.append("paired windows · no Holm q<.05")
            continue
        output.append(
            "paired windows · " + " · ".join(
                f"{item['group_label']}: "
                f"{item['left_name']}↔{item['right_name']} {item['stars']}"
                for item in significant[:6]
            )
        )
    return output


def _custom_region_stat_marks(payload, stats_unit="trial"):
    """Local letters above paired windows; never compare across panels."""
    panels = (payload or {}).get("panels") or []
    regions = (payload or {}).get("regions") or []
    if not panels or not regions:
        return []
    keys = (
        "time_percent", "trial_count", "distance_walked", "displacement",
        "median_local_tortuosity", "median_velocity",
    )
    output = []
    for metric_index, key in enumerate(keys):
        prepared = []
        for group_index, panel in enumerate(panels):
            summaries = {
                str(item.get("id")): item
                for item in panel.get("regions", [])
            }
            for region_index, region in enumerate(regions):
                region_id = str(region.get("id"))
                values, _identities, _support = (
                    _observation_distribution_values(
                        summaries.get(region_id, {}), key, stats_unit)
                )
                values = np.asarray(values, dtype=float)
                values = values[np.isfinite(values)]
                raw_group = str(
                    panel.get("raw", panel.get("name", "Group")))
                region_name = str(region.get("name", region_id))
                category = f"{raw_group}\x1f{region_id}"
                prepared.append({
                    "group": raw_group,
                    "group_index": group_index,
                    "region": region_id,
                    "region_name": region_name,
                    "region_index": region_index,
                    "values": values,
                })
        tests = _paired_custom_region_tests(payload, key, stats_unit)
        test_by_mark = {}
        for test in tests:
            test_by_mark[(test["group"], test["left"])] = (test, "left")
            test_by_mark[(test["group"], test["right"])] = (test, "right")
        marks = []
        for item in prepared:
            if not len(item["values"]):
                continue
            paired = test_by_mark.get((item["group"], item["region"]))
            test, side = paired if paired else (None, None)
            significant = bool(
                test and test.get("holm_p") is not None
                and test["holm_p"] < 0.05
            )
            marks.append({
                "metric_index": metric_index,
                "group": item["group"],
                "group_index": item["group_index"],
                "region": item["region"],
                "region_name": item["region_name"],
                "region_index": item["region_index"],
                "letters": (
                    "A" if not significant or side == "left" else "B"
                ),
                "n": int(len(item["values"])),
                "significant_pairs": int(significant),
                "hover": (
                    (
                        f"{test['left_name']} versus {test['right_name']}"
                        f"<br>paired Wilcoxon · Holm q="
                        f"{test['holm_p']:.3g} {test['stars']}"
                        f"<br>matched n={test['n']:,}"
                    )
                    if test and test.get("holm_p") is not None else
                    "paired comparison unavailable"
                ),
            })
        output.append(marks)
    return output


def _statistics_payload(metric_stats, polar_frame, raw_frame, group_by,
                        pool_mode, polar_moving, polar_walk,
                        polar_angle_source, polar_r_range,
                        polar_min_point_frac, polar_min_animal_frac,
                        stats_unit="trial", custom_region_payload=None):
    metric_stats = _metric_stats_for_unit(
        metric_stats, group_by, pool_mode, stats_unit)
    groups = [
        item for item in _metric_stat_groups(
            metric_stats, group_by, pool_mode) if len(item[1])
    ]
    raw_metric = [
        _omnibus_nonparametric(groups, column)
        for column, _title, _axis in _TRIAL_METRIC_SPECS
    ]
    adjusted = _holm_adjust([item[0] for item in raw_metric])
    metric_pairs = [
        _pairwise_nonparametric(groups, column)
        for column, _title, _axis in _TRIAL_METRIC_SPECS
    ]
    metric_labels = []
    group_names = [str(name) for name, _group in groups]
    metric_marks = []
    for (p_value, method), q_value, pairs in zip(
            raw_metric, adjusted, metric_pairs):
        omnibus = (
            f"{method} · Holm p={q_value:.3g} {_p_stars(q_value)}"
            if np.isfinite(q_value) else f"{method} · n/a"
        )
        metric_labels.append(
            omnibus + "<br><sup>"
            + _pairwise_label(pairs, group_by) + "</sup>"
        )
        marks = _compact_stat_marks(group_names, pairs, group_by)
        for group_index, mark in enumerate(marks):
            mark["group_index"] = group_index
            mark["hover"] = (
                f"{omnibus}<br>{_pairwise_label(pairs, group_by)}"
            )
        metric_marks.append(marks)

    polar_ray = rayleigh_by_segment(
        polar_frame, _on(polar_moving), polar_walk, "none",
        angle_source=polar_angle_source)
    polar_ray, _ = _filter_polar_ray_table(
        polar_ray, polar_r_range, polar_min_point_frac,
        polar_min_animal_frac)
    polar_groups = _group_frames(
        polar_frame, group_by, pool_mode, 2)
    seg_group = (
        pd.concat([
            pd.Series(name, index=group["_seg_id"].astype(str).unique())
            for name, group in polar_groups.items()
        ])
        if polar_groups else pd.Series(dtype=object)
    )
    polar_ray = polar_ray.assign(
        group=polar_ray["_seg_id"].astype(str).map(seg_group))
    polar_ray = polar_ray[polar_ray["group"].notna()]
    if str(stats_unit or "trial") == "animal":
        polar_ray = _polar_by_animal(polar_ray)
    polar_p, polar_method = _circular_group_test(polar_ray)
    polar_omnibus = (
        f"{polar_method} · p={polar_p:.3g} {_p_stars(polar_p)}"
        if np.isfinite(polar_p) else f"{polar_method} · n/a"
    )
    polar_pairs_raw = []
    polar_names = [
        str(name) for name in pd.unique(polar_ray["group"].dropna())
    ]
    for left_index in range(len(polar_names)):
        for right_index in range(left_index + 1, len(polar_names)):
            left, right = polar_names[left_index], polar_names[right_index]
            subset = polar_ray[polar_ray["group"].astype(str).isin([left, right])]
            p_value, method = _circular_group_test(subset)
            polar_pairs_raw.append({
                "left": left, "right": right, "raw_p": p_value,
                "test": method,
            })
    polar_pairs_adjusted = _holm_adjust([
        item["raw_p"] for item in polar_pairs_raw])
    for item, q_value in zip(polar_pairs_raw, polar_pairs_adjusted):
        item["holm_p"] = float(q_value) if np.isfinite(q_value) else None
        item["stars"] = _p_stars(q_value)

    polar_uniformity = []
    for name, group in polar_ray.groupby(
            "group", sort=False, observed=True):
        p_value = _rayleigh_uniformity_p(group["theta_deg"])
        polar_uniformity.append({
            "group": str(name),
            "rayleigh_p": float(p_value) if np.isfinite(p_value) else None,
            "stars": _p_stars(p_value),
            "n": int(len(group)),
        })
    directed = [
        f"{_group_label(group_by, item['group'])} {item['stars']}"
        for item in polar_uniformity
        if item["rayleigh_p"] is not None and item["rayleigh_p"] < 0.05
    ]
    uniform_label = (
        "Rayleigh directed: " + " · ".join(directed[:6])
        if directed else "Rayleigh: no group p<.05"
    )
    polar_label = (
        polar_omnibus + "<br><sup>" + uniform_label + " · "
        + _pairwise_label(polar_pairs_raw, group_by) + "</sup>"
    )
    polar_letters = _compact_letter_display(
        polar_names, polar_pairs_raw)
    polar_marks = []
    uniform_by_group = {
        item["group"]: item for item in polar_uniformity
    }
    for name in polar_names:
        uniform = uniform_by_group.get(name, {})
        p_value = uniform.get("rayleigh_p")
        polar_marks.append({
            "group": name,
            "label": _group_label(group_by, name),
            "letters": polar_letters.get(name, ""),
            "rayleigh_p": p_value,
            "rayleigh_stars": uniform.get("stars", "n/a"),
            "n": int(uniform.get("n", 0)),
            "hover": (
                f"Rayleigh p={p_value:.3g} {_p_stars(p_value)}"
                if p_value is not None and np.isfinite(p_value)
                else "Rayleigh p=n/a"
            ) + f"<br>{polar_omnibus}<br>"
            + _pairwise_label(polar_pairs_raw, group_by),
        })

    start_parts = []
    if raw_frame is not None and len(raw_frame) and "GameObjectRotY" in raw_frame:
        first = raw_frame.drop_duplicates("_seg_id", keep="first")
        for raw_name, sub in first.groupby(
                "ConfigFile", sort=False, observed=True):
            p_value = _rayleigh_uniformity_p(
                pd.to_numeric(
                    sub["GameObjectRotY"], errors="coerce").to_numpy())
            if np.isfinite(p_value):
                start_parts.append((str(raw_name), float(p_value), len(sub)))
    significant = sum(p_value < 0.05 for _, p_value, _ in start_parts)
    min_p = min((p_value for _, p_value, _ in start_parts), default=np.nan)
    start_label = (
        f"Rayleigh uniformity · {significant}/{len(start_parts)} configs p<.05"
        f" · min p={min_p:.3g} {_p_stars(min_p)}"
        if start_parts else "Rayleigh uniformity · n/a"
    )
    region_labels = _custom_region_stat_labels(
        custom_region_payload, stats_unit)
    region_marks = _custom_region_stat_marks(
        custom_region_payload, stats_unit)
    return {
        "pending": False,
        "completed": time.time(),
        "metric_labels": metric_labels,
        "metric_marks": metric_marks,
        "region_labels": region_labels,
        "region_marks": region_marks,
        "polar_label": polar_label,
        "polar_marks": polar_marks,
        "start_label": start_label,
        "start_marks": [{
            "group": name,
            "rayleigh_p": p_value,
            "rayleigh_stars": _p_stars(p_value),
            "n": count,
            "hover": (
                f"Rayleigh p={p_value:.3g} {_p_stars(p_value)}"
                f"<br>n={count:,} initial headings"
            ),
        } for name, p_value, count in start_parts],
        "details": {
            "metrics": [
                {"metric": spec[0], "test": item[1],
                 "raw_p": item[0],
                 "holm_p": float(q) if np.isfinite(q) else None,
                 "pairwise": pairs}
                for spec, item, q, pairs in zip(
                    _TRIAL_METRIC_SPECS, raw_metric, adjusted, metric_pairs)
            ],
            "polar_uniformity": polar_uniformity,
            "polar_pairwise": polar_pairs_raw,
            "starting_heading": [
                {"config": name, "rayleigh_p": p_value, "n": count}
                for name, p_value, count in start_parts
            ],
        },
    }



# ---------------------------------------------------------------------------
# Dash App
# ---------------------------------------------------------------------------

app = Dash(
    __name__,
    suppress_callback_exceptions=True,
    update_title="Working…",
    on_error=_dash_error_handler,
)
app.title = "Daari Deepa"


@app.server.before_request
def _debug_dash_callback_request():
    """Name callback traffic at DEBUG level without logging request payloads."""
    if (LOGGER.isEnabledFor(logging.DEBUG)
            and request.path == "/_dash-update-component"):
        payload = request.get_json(silent=True) or {}
        LOGGER.debug("callback.request output=%s", payload.get("output"))

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

# Loading keeps at most ``LOAD_WORKERS`` complete source files in memory. The
# retained row budget can be raised for unusually large-memory workstations or
# set to 0 to opt into retaining every normalized row.
try:
    LOAD_ROW_BUDGET = max(0, int(os.environ.get("TRAJ_LOAD_ROW_BUDGET", "2000000")))
except (TypeError, ValueError):
    LOAD_ROW_BUDGET = 2_000_000
try:
    LOAD_WORKERS = max(1, min(
        8, int(os.environ.get("TRAJ_LOAD_WORKERS", "2"))))
except (TypeError, ValueError):
    LOAD_WORKERS = 2

# Unified operation progress, polled while loads/renders run in Dash's worker
# thread. Replacing the whole snapshot under a lock keeps hover/checklist state
# coherent while another request reads it.
_PROGRESS_LOCK = threading.RLock()
_PROGRESS_SEQ = 0
_OP_PROGRESS = {
    "id": 0,
    "kind": "idle",
    "phase": "ready",
    "message": "Ready to load data.",
    "active": False,
    "done": 0,
    "total": 1,
    "started": None,
    "updated": time.time(),
    "stages": [],
}


def _progress_begin(kind: str, stages: list[str], message: str) -> int:
    global _PROGRESS_SEQ, _OP_PROGRESS
    now = time.time()
    with _PROGRESS_LOCK:
        _PROGRESS_SEQ += 1
        op_id = _PROGRESS_SEQ
        rows = [
            {
                "label": label,
                "status": "active" if i == 0 else "pending",
                "done": 0,
                "total": 1,
                "seconds": None,
                "_started": now if i == 0 else None,
            }
            for i, label in enumerate(stages)
        ]
        _OP_PROGRESS = {
            "id": op_id,
            "kind": str(kind),
            "phase": str(stages[0] if stages else kind),
            "message": str(message),
            "active": True,
            "done": 0,
            "total": 1,
            "started": now,
            "updated": now,
            "stages": rows,
        }
    return op_id


def _progress_stage(op_id: int, index: int, *, done=0, total=1,
                    message: str | None = None) -> None:
    global _OP_PROGRESS
    now = time.time()
    with _PROGRESS_LOCK:
        if _OP_PROGRESS.get("id") != op_id:
            return
        stages = _OP_PROGRESS.get("stages", [])
        index = max(0, min(int(index), max(len(stages) - 1, 0)))
        for i, stage in enumerate(stages):
            if i < index and stage["status"] != "done":
                started = stage.get("_started") or _OP_PROGRESS.get("started") or now
                stage["seconds"] = max(0.0, now - started)
                stage["status"] = "done"
                stage["done"] = stage.get("total") or 1
            elif i == index:
                if stage["status"] != "active":
                    stage["status"] = "active"
                    stage["_started"] = now
                stage["done"] = int(done or 0)
                stage["total"] = max(1, int(total or 1))
        current = stages[index] if stages else {"label": _OP_PROGRESS.get("kind", "")}
        _OP_PROGRESS["phase"] = current["label"]
        _OP_PROGRESS["done"] = int(done or 0)
        _OP_PROGRESS["total"] = max(1, int(total or 1))
        if message is not None:
            _OP_PROGRESS["message"] = str(message)
        _OP_PROGRESS["updated"] = now


def _progress_finish(op_id: int, message: str, *, failed=False) -> None:
    global _OP_PROGRESS
    now = time.time()
    with _PROGRESS_LOCK:
        if _OP_PROGRESS.get("id") != op_id:
            return
        for stage in _OP_PROGRESS.get("stages", []):
            if stage["status"] == "active":
                started = stage.get("_started") or now
                stage["seconds"] = max(0.0, now - started)
                stage["status"] = "error" if failed else "done"
                stage["done"] = stage.get("total") or 1
        _OP_PROGRESS.update(
            phase="Error" if failed else "Ready",
            message=str(message),
            active=False,
            done=1,
            total=1,
            updated=now,
        )


def _progress_snapshot() -> dict:
    with _PROGRESS_LOCK:
        out = copy.deepcopy(_OP_PROGRESS)
    for stage in out.get("stages", []):
        stage_started = stage.get("_started")
        stage.pop("_started", None)
        if stage.get("status") == "active" and out.get("active"):
            started = stage_started or out.get("started") or time.time()
            stage["seconds"] = max(0.0, time.time() - started)
    return out


def _progress_arm(kind: str, message: str) -> None:
    """Keep the status poller awake until the real worker publishes its stages.

    Dash may schedule the interval callback just before the load/render callback
    reaches ``_progress_begin``.  A tiny queued operation closes that race; the
    real operation replaces it as soon as its worker starts.
    """
    with _PROGRESS_LOCK:
        active = bool(_OP_PROGRESS.get("active"))
    if not active:
        _progress_begin(kind, ["Queued"], message)


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
                 "_POLAR_RAY_CACHE", "_POLAR_RAY_CACHE_ORDER",
                 "_TRANSITION_CACHE", "_TRANSITION_CACHE_ORDER",
                 "_VELOCITY_CACHE"):
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


def _file_retention_quota(path: str, total_bytes: int, budget: int) -> int | None:
    if budget <= 0:
        return None
    try:
        size = max(1, int(os.path.getsize(path)))
    except OSError:
        size = 1
    return max(2, int(round(budget * size / max(1, total_bytes))))


def _retain_loaded_file(frame: pd.DataFrame, quota: int | None,
                        smooth_speed: np.ndarray) -> pd.DataFrame:
    """Return the memory-resident portion of one fully preprocessed source file."""

    work = frame
    work["_smoothed_velocity"] = np.asarray(smooth_speed, dtype="float32")
    if quota and len(work) > quota:
        keep = _segment_endpoint_keep(work["_seg_id"].to_numpy(), max_points=quota)
        work = work.loc[keep].copy()
    else:
        work = work.copy()
    # Raw sensor channels are retained for diagnostics, but float64 columns do
    # not need double precision once normalization/statistics are complete.
    for col in work.select_dtypes(include=["float64"]).columns:
        work[col] = pd.to_numeric(work[col], downcast="float")
    for col in work.select_dtypes(include=["int64"]).columns:
        if col != "Current Time":
            work[col] = pd.to_numeric(work[col], downcast="integer")
    return work


def _load_data_locked(pattern):
    started = time.perf_counter()
    pkey = _pattern_key(pattern)
    files = td_io.find_csv_files(pattern)
    key = (pkey, _files_signature(files), LOAD_ROW_BUDGET)
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
        with _PROGRESS_LOCK:
            queued_id = (
                _OP_PROGRESS.get("id")
                if _OP_PROGRESS.get("active") and _OP_PROGRESS.get("kind") == "load"
                else None
            )
        if queued_id is not None:
            raw_rows = int(cached.attrs.get("_raw_rows", len(cached)))
            _progress_finish(
                queued_id,
                f"Ready — reused {len(cached):,} retained of {raw_rows:,} source rows.",
            )
        return _DATA_CACHE[key], _STATS_CACHE.get(key), metas

    op_id = _progress_begin(
        "load",
        ["Detect files", "Load + preprocess", "Combine retained rows", "Index + cache"],
        "Detecting trajectory files…",
    )
    _progress_stage(
        op_id, 0, done=1, total=1,
        message=f"Detected {len(files):,} trajectory files.",
    )
    if not files:
        _progress_finish(op_id, "No trajectory CSVs matched the data source.", failed=True)
        LOGGER.warning("data.no_files source=%r", pkey)
        return None, None, []

    LOGGER.info("data.load_start files=%d source=%r", len(files), pkey)

    total_bytes = sum(max(1, int(sig[2] or 1)) for sig in key[1])
    dfs, stat_parts, metas, seen = [], [], [], set()
    raw_rows = 0
    retained_rows = 0
    workers = min(LOAD_WORKERS, len(files))
    _progress_stage(
        op_id, 1, done=0, total=len(files),
        message=(
            f"Loading {len(files):,} files with {workers} parallel "
            f"worker{'s' if workers != 1 else ''}…"
        ),
    )

    def _load_one(index_and_file):
        i, f = index_and_file
        file_started = time.perf_counter()
        d = td_io.load_csv_fast(f)
        if d is None:
            return i, f, None, None, 0, 0, 0, (
                time.perf_counter() - file_started)
        n_raw = len(d)
        segments = int(d["_seg_id"].nunique())
        smooth = smoothed_velocity(d, 10)
        stats_part = compute_segment_stats(d, smooth)
        quota = _file_retention_quota(f, total_bytes, LOAD_ROW_BUDGET)
        retained = _retain_loaded_file(d, quota, smooth)
        n_retained = len(retained)
        del d, smooth
        return (
            i, f, retained, stats_part, n_raw, n_retained, segments,
            time.perf_counter() - file_started,
        )

    completed_results = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="trajectory-load") as pool:
        futures = {
            pool.submit(_load_one, item): item
            for item in enumerate(files)
        }
        for completed, future in enumerate(
                concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            completed_results.append(result)
            i, f, retained, stats_part, n_raw, n_retained, segments, seconds = result
            raw_rows += n_raw
            retained_rows += n_retained
            if retained is not None:
                LOGGER.info(
                    "data.file_ready file=%r raw_rows=%d retained_rows=%d "
                    "segments=%d seconds=%.3f worker=%d",
                    f, n_raw, n_retained, segments, seconds, i,
                )
            _progress_stage(
                op_id, 1, done=completed, total=len(files),
                message=(
                    f"Preprocessed {completed:,}/{len(files):,} files in "
                    f"parallel — {retained_rows:,}/{raw_rows:,} rows retained."
                ),
            )

    for _i, _f, retained, stats_part, _raw, _kept, _segments, _seconds in sorted(
            completed_results, key=lambda item: item[0]):
        if retained is not None:
            dfs.append(retained)
            stat_parts.append(stats_part)
    for f in files:
        folder = os.path.dirname(f)
        if folder not in seen:
            seen.add(folder)
            metas.append(td_io.load_folder_metadata(folder))

    if not dfs:
        _progress_finish(op_id, "No valid trajectory rows were found.", failed=True)
        LOGGER.warning("data.no_valid_frames files=%d source=%r", len(files), pkey)
        return None, None, metas

    _progress_stage(
        op_id, 2, done=0, total=1,
        message=f"Combining {sum(len(frame) for frame in dfs):,} retained rows…",
    )
    df = pd.concat(dfs, ignore_index=True)
    # Record one old id per retained segment, then remap the exact file-level
    # statistics after restarted trials receive their global visible numbering.
    old_ids = df.drop_duplicates("_seg_id")["_seg_id"].astype(str).tolist()
    td_io.concatenate_restarted_trials(df)
    new_ids = df.drop_duplicates("_seg_id")["_seg_id"].astype(str).tolist()
    seg_id_map = dict(zip(old_ids, new_ids))
    # Per-file loading repairs repeated segment blocks; this final guard only
    # falls back to a full sort if the concatenated frame is still unsafe.
    td_io.sort_frame_for_segments(df)
    stats = pd.concat(stat_parts, ignore_index=True) if stat_parts else compute_segment_stats(df)
    if len(stats) and seg_id_map:
        stats["seg_id"] = stats["seg_id"].astype(str).map(seg_id_map).fillna(
            stats["seg_id"].astype(str)
        )
    _progress_stage(
        op_id, 3, done=0, total=1,
        message="Indexing segments, metadata and reusable statistics…",
    )
    for c in ("ConfigFile", "SceneName", "VR", "FlyID", "Sex",
              "SourceFolder", "SourceFile", "_seg_id"):
        if c in df.columns:
            df[c] = df[c].astype("category")
    df.attrs["_frame_token"] = ("data", key)
    df.attrs["_raw_rows"] = int(raw_rows)
    df.attrs["_retained_rows"] = int(len(df))
    df.attrs["_load_row_budget"] = int(LOAD_ROW_BUDGET)
    _set_config_order(metas)
    _populate_auto_lut(metas)           # readable config names from objects
    _DATA_CACHE[key] = df
    _STATS_CACHE[key] = stats
    _META_CACHE[key] = metas
    _VELOCITY_CACHE[key] = smoothed_velocity(df, 10)
    _remember_data_cache_key(key)
    retained_pct = 100.0 * len(df) / max(raw_rows, 1)
    ready_message = (
        f"Ready — loaded {len(files):,} files; retained {len(df):,} of "
        f"{raw_rows:,} rows ({retained_pct:.1f}%)."
    )
    _progress_finish(op_id, ready_message)
    LOGGER.info(
        "data.load_done files=%d raw_rows=%d retained_rows=%d retained_pct=%.2f "
        "segments=%d seconds=%.3f source=%r",
        len(files), raw_rows, len(df), retained_pct, int(df["_seg_id"].nunique()),
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
GRAPH_CONFIG = {
    "scrollZoom": True,
    "displayModeBar": True,
    "displaylogo": False,
    "edits": {"shapePosition": True},
    "toImageButtonOptions": {
        "format": "png",
        "scale": 3,
    },
}

# Every plot is part of one normal document flow. Navigation scrolls to these
# sections; no graph is ever measured, resized, or re-rendered while hidden.
_PANEL_STYLE = {"position": "relative", "overflow": "visible",
                "scrollMarginTop": "52px", "marginBottom": "12px"}

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),

    # Header
    html.Div([
        html.H3(
                "Daari Deepa",
                title=(
                    "Kannada: a lamp for the path — illuminating trajectories "
                    "so their routes and transitions can be understood."
                ),
                style={"margin": "0", "fontSize": "17px", "whiteSpace": "nowrap"}),
        # Compact live status belongs beside the title, where it remains visible
        # regardless of sidebar/main scroll position. Hover exposes stage timing.
        html.Div([
            html.Div([
                html.Span(className="status-dot"),
                html.Strong("Status", className="status-phase-label"),
                html.Span("Ready", id="status-phase", className="status-phase"),
            ], className="status-dock-heading"),
            html.Div("Choose a data source to begin.", id="status-message",
                     className="status-message"),
            html.Div("Server diagnostics appear in the terminal.",
                     id="status-detail", className="status-detail"),
            html.Div([
                html.Div(id="status-progress-bar", className="status-progress-bar"),
            ], id="status-progress-track", className="status-progress-track"),
            html.Span("Ready", id="status-progress-text", className="status-progress-text"),
            html.Div(id="load-status", className="status-raw-hidden"),
            html.Div(id="plot-status", className="status-raw-hidden"),
        ], id="status-dock", className="status-dock header-status",
           title="No completed operation yet."),
        html.Button(
            "‹", id="btn-toggle-sidebar", n_clicks=0,
            title="Collapse the control sidebar to give plots the full width.",
            className="subtle-action-button header-action-button sidebar-arrow-button",
        ),
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
            ], className="sidebar-card sidebar-data"),
            html.Hr(style={"margin": "6px 0"}),

            html.Div([
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
            html.Details([
                html.Summary(
                    "Plot order · Config / Treatment",
                    id="panel-order-summary",
                    title=(
                        "Drag the values to move mounted trajectory, heatmap, "
                        "Gandiva, loop, polar and heading-time subplots for the active panel "
                        "grouping. No analysis is recomputed."
                    ),
                    style={"fontSize": "10px", "cursor": "pointer"},
                ),
                html.Ol(id="panel-order-list", style={
                    "margin": "4px 0 0 16px", "padding": "0", "fontSize": "9px",
                    "maxHeight": "150px", "overflowY": "auto",
                }),
            ], style={"marginTop": "3px"}),
            html.Label("Workspace", style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.RadioItems(id="view-layout", options=[
                {"label": " Sections", "value": "sections"},
                {"label": " Trajectory + polar", "value": "compare"},
            ], value="sections", inline=True, className="segmented-control",
               style={"fontSize": "10px"}),
            html.Button(
                "Clean layout", id="btn-minimal-layout", n_clicks=0,
                title=(
                    "Prepare PNG-ready plots in place: spatial scale bars, "
                    "despined statistical axes, clean polar axes, no legends."
                ),
                className="subtle-action-button",
            ),
            html.Div("Readable names come from config metadata; hover a panel "
                     "title to see its raw JSON/config filename.",
                     style={"fontSize": "9px", "color": "#888"}),
            ], className="sidebar-card sidebar-panels"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Trajectories",
                title="Drawing, colour, playback and displayed-trial controls.",
            ),
            html.Label("Colour", title=(
                           "Colour trajectories and polar vectors by the same "
                           "metadata or metric. Tortuosity uses a 2-second "
                           "path/chord window by default; edit "
                           "trajectory.tortuosity_window_seconds in Visual "
                           "style JSON to change it."),
                       style={"fontSize": "10px"}),
            dcc.Dropdown(id="color-by", options=[
                {"label": "Categorical · current panels", "value": "categorical"},
                {"label": "None · neutral gray", "value": "none"},
                {"label": "Individual (VR + fly)", "value": "individual"},
                {"label": "Config", "value": "config"},
                {"label": "Scene", "value": "scene"},
                {"label": "VR", "value": "vr"},
                {"label": "Source folder", "value": "folder"},
                {"label": "ROI outcome", "value": "roi"},
                {"label": "Trial · sequential", "value": "trial"},
                {"label": "Local time · sequential", "value": "local_time"},
                {"label": "Velocity · smoothed", "value": "velocity"},
                {"label": "Tortuosity · time-smoothed", "value": "tortuosity"},
            ], value="categorical", clearable=False, style={"fontSize": "11px"}),
            html.Div([
                html.Div([
                    html.Label("Render mode", title=(
                        "Accuracy uses full filtered data for analysis views; "
                        "Speed decimates plotted data more aggressively."),
                        style={"fontSize": "10px"}),
                    dcc.RadioItems(id="render-mode", options=[
                        {"label": " Speed", "value": "speed"},
                        {"label": " Accuracy", "value": "accuracy"},
                    ], value="speed", inline=True, className="segmented-control",
                       style={"fontSize": "10px"}),
                ], style={"flex": "1", "minWidth": "0"}),
                dcc.Checklist(id="animate-toggle",
                              options=[{"label": " Playback", "value": "on",
                                        "title": (
                                            f"Playback uses {BUDGET_SVG//1000}k points; "
                                            f"static uses {BUDGET_GL//1000}k by default.") }],
                              value=[], style={"fontSize": "10px"}),
            ], className="compact-control-row",
               style={"marginTop": "4px", "alignItems": "end"}),
            html.Label(
                "Displayed trials (%)",
                title=(
                    "Browser-locally show this fraction of complete trajectory "
                    "segments in trajectory, loop-observer and polar drawings. "
                    "Analytical panels still use all filtered trials."
                ),
                style={"fontSize": "10px", "marginTop": "5px"},
            ),
            html.Div([
                html.Div(dcc.Slider(
                    id="traj-trial-fraction", min=1, max=100, step=1, value=100,
                    updatemode="mouseup",
                    marks={1: "1", 25: "25", 50: "50", 75: "75", 100: "100"},
                    tooltip={"placement": "bottom", "always_visible": False},
                ), style={"flex": "1", "minWidth": "0"}),
                html.Button(
                    "⚄", id="btn-traj-resample", n_clicks=0,
                    title=(
                        "Draw a different whole-trial sample at the selected "
                        "percentage. Sampling hides complete "
                        "SourceFile+Trial+Step segments; 100% shows every trial."
                    ),
                    className="subtle-action-button dice-button",
                ),
            ], className="compact-control-row",
               title=(
                   "Sampling hides complete SourceFile+Trial+Step segments in "
                   "mounted trajectory, curtain-ring, and polar plots. "
                   "Analytical panels continue to use all filtered trials."
               ), style={"alignItems": "center", "gap": "5px"}),
            ], open=True, className="sidebar-card sidebar-trajectories"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Trial metrics",
                title="Distribution marks, independent unit, and paired lines.",
            ),
            html.Label(
                "Distribution marks",
                title=(
                    "Auto uses one mark type across every metric: swarm when "
                    "the largest group has at most 200 observations, otherwise "
                    "violin. Explicit choices are always honoured."
                ),
                style={"fontWeight": "bold", "fontSize": "12px"},
            ),
            dcc.RadioItems(
                id="distribution-mode",
                options=[
                    {"label": " Auto", "value": "auto"},
                    {"label": " Swarm", "value": "swarm"},
                    {"label": " Violin", "value": "violin"},
                ],
                value="auto", inline=True, className="segmented-control",
                style={"fontSize": "10px"},
            ),
            dcc.Checklist(
                id="distribution-show-points",
                options=[{
                    "label": " Show dots on violins when n ≤ 200",
                    "value": "on",
                }],
                value=["on"], style={"fontSize": "10px", "marginTop": "2px"},
            ),
            html.Label(
                "Independent unit",
                title=(
                    "Trial treats every SourceFile+Trial+Step segment as one "
                    "observation. Animal first averages trials within FlyID@VR "
                    "and the active panel group."
                ),
                style={"fontSize": "10px", "marginTop": "3px"},
            ),
            dcc.RadioItems(
                id="stats-unit",
                options=[
                    {"label": " Trial", "value": "trial"},
                    {"label": " Animal", "value": "animal"},
                ],
                value="trial", inline=True, className="segmented-control",
                style={"fontSize": "10px"},
            ),
            dcc.Checklist(
                id="observation-paired-lines",
                options=[{
                    "label": " Connect matching units across paired regions",
                    "value": "on",
                    "title": (
                        "Show a line between the same trial or animal in the "
                        "two compared observation windows or target sides."
                    ),
                }],
                value=["on"], style={"fontSize": "10px", "marginTop": "2px"},
            ),
            ], className="sidebar-card sidebar-metrics"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Curtain-ring observer",
                title="Optional browser-local loop matching; no data re-analysis.",
            ),
            dcc.Checklist(
                id="loop-enabled",
                options=[{"label": " Show curtain-ring observer", "value": "on"}],
                value=[], style={"fontSize": "11px"},
            ),
            html.Div([
                dcc.Dropdown(
                    id="loop-active-ring",
                    options=[{"label": "Ring 1", "value": "ring-1"}],
                    value="ring-1", clearable=False,
                    style={"fontSize": "10px", "flex": "1", "minWidth": "0"},
                ),
                html.Button(
                    "+", id="btn-loop-add", n_clicks=0,
                    title="Add another curtain ring",
                    className="subtle-action-button",
                    style={"width": "34px", "margin": "0"},
                ),
                html.Button(
                    "×", id="btn-loop-delete", n_clicks=0,
                    title="Delete the selected curtain ring",
                    className="subtle-action-button",
                    style={"width": "34px", "margin": "0"},
                ),
            ], style={"display": "flex", "gap": "4px", "marginTop": "3px"}),
            dcc.RadioItems(
                id="loop-match-mode",
                options=[
                    {"label": " Any ring", "value": "any"},
                    {"label": " All rings", "value": "all"},
                ],
                value="any", inline=True, className="segmented-control",
                style={"fontSize": "10px", "marginTop": "3px"},
            ),
            html.Div([
                html.Div([
                    html.Label("Ring X", style={"fontSize": "10px"}),
                    dcc.Input(id="loop-x", type="number", value=0, step="any",
                              debounce=False, style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("Ring Z", style={"fontSize": "10px"}),
                    dcc.Input(id="loop-z", type="number", value=0, step="any",
                              debounce=False, style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("Radius", style={"fontSize": "10px"}),
                    dcc.Input(id="loop-radius", type="number", value=3,
                              min=0.001, step="any", debounce=False,
                              style=_INPUT_STYLE),
                ], style={"flex": "1"}),
            ], style={"display": "flex", "gap": "5px", "marginTop": "3px"}),
            dcc.Slider(
                id="loop-radius-slider", min=0.5, max=100, step=0.5, value=3,
                updatemode="mouseup",
                marks={1: "1", 25: "25", 50: "50", 75: "75", 100: "100"},
                tooltip={"placement": "bottom", "always_visible": False},
            ),
            html.Div(
                "Drag any warm-gold curtain ring in the observer, or edit the "
                "selected ring here. Resizing is always symmetric, so it "
                "remains a circle. Matching stays entirely in the browser.",
                style={"fontSize": "9px", "color": "#888", "marginTop": "2px"},
            ),
            ], className="sidebar-card sidebar-loop"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Observation windows",
                title="Optional region-scoped polar and movement analysis.",
            ),
            html.Div(
                dcc.Checklist(
                    id="custom-region-enabled",
                    options=[{
                        "label": " Use windows for polar + trial metrics",
                        "value": "on",
                    }],
                    value=[], style={"fontSize": "11px"},
                ),
                title=(
                    "When enabled, polar uses samples inside the union of all "
                    "windows. Distance, displacement, tortuosity and velocity "
                    "are recomputed from those observed trial sections."
                ),
            ),
            html.Div([
                dcc.Dropdown(
                    id="custom-region-active",
                    options=[{"label": "Window 1", "value": "region-1"}],
                    value="region-1", clearable=False,
                    style={"fontSize": "10px", "flex": "1", "minWidth": "0"},
                ),
                html.Button(
                    "+", id="btn-custom-region-add", n_clicks=0,
                    title="Add another observation window",
                    className="subtle-action-button",
                    style={"width": "34px", "margin": "0"},
                ),
                html.Button(
                    "×", id="btn-custom-region-delete", n_clicks=0,
                    title="Delete the selected observation window",
                    className="subtle-action-button",
                    style={"width": "34px", "margin": "0"},
                ),
            ], style={"display": "flex", "gap": "4px", "marginTop": "3px"}),
            html.Div([
                html.Div([
                    html.Label(
                        "X min",
                        title="Left edge. Dragging a dashed box updates this value.",
                        style={"fontSize": "10px"},
                    ),
                    dcc.Input(id="custom-region-x0", type="number", value=-3,
                              step="any", debounce=False, style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label(
                        "X max",
                        title="Right edge. Analytics wait until editing pauses.",
                        style={"fontSize": "10px"},
                    ),
                    dcc.Input(id="custom-region-x1", type="number", value=3,
                              step="any", debounce=False, style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label(
                        "Z min",
                        title="Bottom edge in the spatial Z coordinate.",
                        style={"fontSize": "10px"},
                    ),
                    dcc.Input(id="custom-region-z0", type="number", value=-3,
                              step="any", debounce=False, style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label(
                        "Z max",
                        title="Top edge in the spatial Z coordinate.",
                        style={"fontSize": "10px"},
                    ),
                    dcc.Input(id="custom-region-z1", type="number", value=3,
                              step="any", debounce=False, style=_INPUT_STYLE),
                ], style={"flex": "1"}),
            ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                      "gap": "4px", "marginTop": "3px"}),
            html.Div(
                "Drag the dashed boxes on Trajectory, Heatmap or Gandiva. "
                "The box moves immediately; analytics refresh after a 4.5 s "
                "pause. Polar and trial metrics use the union.",
                id="custom-region-status",
                style={"fontSize": "9px", "color": "#888", "marginTop": "2px"},
            ),
            ], className="sidebar-card sidebar-regions"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Occupancy + Gandiva",
                title=(
                    "Occupancy loads after trajectories. Gandiva is an opt-in "
                    "later calculation on the same zero-centred grid."
                ),
            ),
            dcc.Checklist(
                id="gandiva-enabled",
                options=[{
                    "label": " Calculate Gandiva plots",
                    "value": "on",
                    "title": (
                        "Off by default because local vector aggregation is "
                        "one of the slower dashboard stages."
                    ),
                }],
                value=[], style={"fontSize": "11px", "marginBottom": "3px"},
            ),
            html.Div([
                html.Div([
                    html.Label("Grid size (units)", style={"fontSize": "10px"}),
                    dcc.Input(id="heatmap-binsize", type="number", value=None, min=0,
                              step=0.1, debounce=True, placeholder="auto",
                              style=_INPUT_STYLE),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("Bound %", style={"fontSize": "10px"}),
                    dcc.Input(id="heatmap-bound", type="number", value=98, min=50,
                              max=100, step=1, debounce=True,
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
            html.Label(
                "Max Gandiva radius (cell widths)",
                title=(
                    "Maximum length of a fully directed local vector. "
                    "At 1.0 equal adjacent arrows just touch; 0.98 leaves a hairline gap. "
                    "This slider rescales already-built arrows in the browser."
                ),
                style={"fontSize": "10px", "marginTop": "5px"},
            ),
            dcc.Slider(
                id="flow-max-radius", min=0.05, max=0.98, step=0.01,
                value=0.49, updatemode="mouseup",
                marks={0.05: "0.05", 0.49: "0.49", 0.98: "0.98"},
                tooltip={"placement": "bottom", "always_visible": False},
            ),
            html.Div([
                html.Label("Color range",
                           title="Heatmap colour min/max using the active metric distribution.",
                           style={"fontSize": "10px"}),
                dcc.Graph(id="heatmap-color-hist",
                          figure=build_mini_histogram(None, color="#0f766e"),
                          config={"displayModeBar": False, "staticPlot": True},
                          style={"height": "58px", "margin": "0 0 -6px"}),
                dcc.RangeSlider(id="heatmap-color-range", min=0, max=100,
                                step=1, value=[0, 99], updatemode="mouseup",
                                marks={0: "0", 50: "50", 100: "100"},
                                tooltip={"placement": "bottom",
                                         "always_visible": False}),
            ], style={"marginTop": "3px"}),
            html.Label("Limits", style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.RadioItems(id="heatmap-crange", options=[
                {"label": "Value", "value": "value"},
                {"label": "Percentile", "value": "percentile"},
            ], value="percentile", inline=True, className="segmented-control",
               style={"fontSize": "10px"}),
            html.Details([
                html.Summary("Explicit colour limits",
                             style={"fontSize": "9px", "cursor": "pointer"}),
                html.Div([
                    dcc.Input(id="heatmap-cmin", type="number", value=None,
                              placeholder="auto min", step="any", debounce=True,
                              style=_INPUT_STYLE),
                    dcc.Input(id="heatmap-cmax", type="number", value=None,
                              placeholder="auto max", step="any", debounce=True,
                              style=_INPUT_STYLE),
                ], style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                          "gap": "5px", "marginTop": "3px"}),
            ], style={"marginTop": "2px"}),
            html.Div("Color limits follow the selected metric; percentile mode converts the selected metric span to percentiles.",
                     style={"fontSize": "9px", "color": "#888"}),
            ], open=True, className="sidebar-card sidebar-spatial"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Transition observer",
                title="Optional conditional transition analysis on the occupancy grid.",
            ),
            dcc.Checklist(
                id="transition-enabled",
                options=[{
                    "label": " Calculate transition probability",
                    "value": "on",
                }],
                value=[], style={"fontSize": "11px"},
            ),
            dcc.RadioItems(
                id="transition-outcome",
                options=[
                    {
                        "label": " Crossed opposite half",
                        "value": "crossed",
                        "title": (
                            "Success when a trial reaches the opposite half at "
                            "any later sample after first entering the cell."
                        ),
                    },
                    {
                        "label": " Ended opposite half",
                        "value": "ended",
                        "title": (
                            "Stricter success: the final trial sample must lie "
                            "in the opposite half."
                        ),
                    },
                ],
                value="crossed", className="segmented-control",
                style={"fontSize": "10px", "marginTop": "3px"},
            ),
            dcc.RadioItems(
                id="transition-metric",
                options=[
                    {
                        "label": " Fraction (%)",
                        "value": "fraction",
                        "title": (
                            "Colour each cell by successful trials divided by "
                            "all unique trials that entered it."
                        ),
                    },
                    {
                        "label": " Successful trials (n)",
                        "value": "count",
                        "title": (
                            "Colour each cell by the absolute number of "
                            "entering trials that crossed or ended opposite."
                        ),
                    },
                ],
                value="fraction", className="segmented-control",
                style={"fontSize": "10px", "marginTop": "3px"},
            ),
            html.Div([
                html.Div([
                    html.Label(
                        "Horizontal split Z",
                        title=(
                            "Blank uses the modal trial start. This line splits "
                            "the analytical halves without shifting the shared "
                            "zero-centred occupancy grid."
                        ),
                        style={"fontSize": "10px"},
                    ),
                    dcc.Input(
                        id="transition-split-z", type="number", value=None,
                        step="any", debounce=True, placeholder="auto",
                        className="td-plain-number", style=_INPUT_STYLE,
                    ),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label(
                        "Min entering trials",
                        title=(
                            "Hide low-support cells below this unique-trial "
                            "denominator. Hidden cells are not treated as zero."
                        ),
                        style={"fontSize": "10px"},
                    ),
                    dcc.Input(
                        id="transition-min-trials", type="number", value=3,
                        min=1, step=1, debounce=True,
                        className="td-plain-number", style=_INPUT_STYLE,
                    ),
                ], style={"flex": "1"}),
            ], style={"display": "flex", "gap": "6px", "marginTop": "3px"}),
            html.Div([
                html.Div([
                    html.Label(
                        "Count colour min",
                        title=(
                            "Only affects Successful trials (n). Leave blank "
                            "to start the colour scale at zero."
                        ),
                        style={"fontSize": "10px"},
                    ),
                    dcc.Input(
                        id="transition-count-min", type="number", value=None,
                        min=0, step="any", debounce=True, placeholder="auto",
                        className="td-plain-number", style=_INPUT_STYLE,
                    ),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label(
                        "Count colour max",
                        title=(
                            "Only affects Successful trials (n). Lower this "
                            "ceiling when a few high-count bins hide structure."
                        ),
                        style={"fontSize": "10px"},
                    ),
                    dcc.Input(
                        id="transition-count-max", type="number", value=None,
                        min=0, step="any", debounce=True, placeholder="auto",
                        className="td-plain-number", style=_INPUT_STYLE,
                    ),
                ], style={"flex": "1"}),
            ], style={"display": "flex", "gap": "6px", "marginTop": "3px"}),
            html.Div(
                "Each trial counts once per cell. Click a visible cell to "
                "overlay successful paths faintly on that panel; click a blank "
                "cell to clear them. Survival on the same half is 100% minus "
                "the crossed probability.",
                style={"fontSize": "9px", "color": "#888", "marginTop": "2px"},
            ),
            ], className="sidebar-card sidebar-transition"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Targets",
                title="ROI visibility, reach geometry, entry subset and exit trimming.",
            ),
            dcc.Checklist(id="roi-show",
                          options=[{"label": " Calculate target ROIs + reached counts",
                                    "value": "on"}],
                          value=[], style={"fontSize": "11px"}),
            html.Div([
                html.Label("Reach radius (units)",
                           title="Distance from target centre counted as ROI entry.",
                           style={"fontSize": "10px"}),
                dcc.Input(id="roi-reach", type="number", value=3, min=0.001,
                          step="any", debounce=False,
                          style={**_INPUT_STYLE, "width": "78px"}),
            ], className="compact-control-row",
               title="Exact radius. Values above the slider maximum are allowed.",
               style={"marginTop": "4px", "alignItems": "center"}),
            dcc.Slider(id="roi-reach-slider", min=0.5, max=100, step=0.5, value=3,
                       updatemode="mouseup",
                       marks={1: "1", 25: "25", 50: "50", 75: "75", 100: "100"},
                       tooltip={"placement": "bottom", "always_visible": True}),
            dcc.Checklist(id="roi-entered",
                          options=[{"label": " Only trials that entered an ROI",
                                    "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "3px"}),
            dcc.Checklist(id="roi-trim",
                          options=[{"label": " Trim trial tail after ROI exit",
                                    "value": "on"}],
                          value=[], style={"fontSize": "11px", "marginTop": "1px"}),
            html.Div("The slider covers 0.5–100; the exact box and URL accept any positive radius.",
                     style={"fontSize": "9px", "color": "#888", "marginTop": "2px"}),
            ], className="sidebar-card sidebar-targets"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Heading + movement quality",
                title=(
                    "Direction/polar quality gates. Moving only also blanks "
                    "stationary samples from trajectory drawings."
                ),
            ),
            html.Label("Angle source",
                       title="Body orientation uses Unity GameObjectRotY. Movement heading uses consecutive X/Z samples.",
                       style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Dropdown(id="polar-angle-source", options=[
                {"label": "Body orientation (RotY)", "value": "orientation"},
                {"label": "Movement heading (X/Z)", "value": "movement"},
            ], value="orientation", clearable=False, style={"fontSize": "10px"}),
            dcc.Checklist(
                id="heading-time-enabled",
                options=[{
                    "label": " Heading over time panel", "value": "on",
                    "title": (
                        "Keep an optional signed-angle time series mounted. "
                        "It updates only after its replacement is ready."),
                }],
                value=[], style={"fontSize": "10px", "marginTop": "5px"},
            ),
            dcc.RadioItems(
                id="heading-time-mode",
                options=[
                    {"label": " Trials", "value": "trial"},
                    {"label": " Animal mean", "value": "animal"},
                ],
                value="trial", inline=True, className="segmented-control",
                style={"fontSize": "10px", "marginTop": "2px"},
            ),
            dcc.RadioItems(
                id="heading-time-representation",
                options=[
                    {"label": " Traces", "value": "traces"},
                    {"label": " Density", "value": "density"},
                ],
                value="traces", inline=True, className="segmented-control",
                style={"fontSize": "10px", "marginTop": "2px"},
            ),
            dcc.Checklist(
                id="heading-time-variability",
                options=[{
                    "label": " Within-animal circular SD band", "value": "on",
                    "title": "Shown around animal-mean traces when at least two trials contribute.",
                }],
                value=[], style={"fontSize": "10px", "marginTop": "2px"},
            ),
            html.Label(
                "Time averaging window (s)",
                title="Auto uses about 1% of the longest retained trial. Zero keeps native/full resolution.",
                style={"fontSize": "10px", "marginTop": "4px"}),
            html.Div([
                dcc.Input(
                    id="heading-time-window", type="number", value=None,
                    min=0, step="any", debounce=True,
                    placeholder="Auto · 1% span",
                    className="td-plain-number",
                    style={**_INPUT_STYLE, "minWidth": "0"}),
                html.Span("blank = auto · 0 = full", style={
                    "fontSize": "9px", "color": "#888", "whiteSpace": "nowrap"}),
            ], style={"display": "grid", "gridTemplateColumns": "1fr auto",
                      "gap": "5px", "alignItems": "center"}),
            dcc.Slider(
                id="heading-time-window-slider", min=-1, max=30, step=0.5,
                value=-1, updatemode="mouseup",
                marks={-1: "Auto", 0: "Full", 5: "5", 10: "10", 20: "20", 30: "30"},
                tooltip={"placement": "bottom", "always_visible": False}),
            html.Div([
                html.Label("Angular density bin", style={"fontSize": "10px"}),
                dcc.Dropdown(
                    id="heading-time-angle-bin",
                    options=[
                        {"label": "5°", "value": 5},
                        {"label": "10°", "value": 10},
                        {"label": "15°", "value": 15},
                    ],
                    value=5, clearable=False,
                    style={"fontSize": "10px", "minWidth": "74px"}),
            ], style={"display": "grid", "gridTemplateColumns": "1fr 84px",
                      "gap": "5px", "alignItems": "center", "marginTop": "3px"}),
            html.Div(
                "Animal mean gives every selected trial one circular vote per "
                "time bin. Density creates one legend-toggleable layer per animal.",
                style={"fontSize": "9px", "color": "#888", "marginTop": "2px"},
            ),
            html.Label("Rayleigh R range",
                       title="Filter polar trial vectors by Rayleigh strength: 0 = scattered headings, 1 = strongly directed.",
                       style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Graph(id="polar-r-hist", figure=build_polar_r_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="polar-r-range", min=0, max=1, step=0.01,
                            value=[0, 1], updatemode="mouseup",
                            marks={0: "0", 0.5: "0.5", 1: "1"},
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
                       value=0, updatemode="mouseup",
                       marks={0: "0", 0.5: "0.5", 1: "1"},
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
                       value=0, updatemode="mouseup",
                       marks={0: "0", 0.5: "0.5", 1: "1"},
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
            html.Div("Moving only also removes stationary samples from trajectory drawings; heading source and quality gates drive polar and optional Gandiva. 0° is forward (+Z), positive angles turn right (+X).",
                     style={"fontSize": "9px", "color": "#888", "marginTop": "2px"}),
            ], className="sidebar-card sidebar-direction"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Quality filters",
                title="Peak speed and net-displacement inclusion ranges.",
            ),
            html.Div("Serial filters update the plots and the retention audit.",
                     style={"fontSize": "9px", "color": "#888"}),
            html.Label("Peak velocity range",
                       title="Per-trial peak velocity range. Full span is treated as no range filter.",
                       style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.Graph(id="vel-range-hist", figure=build_mini_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="vel-range", min=0, max=1, step=0.01,
                            value=[0, 1], updatemode="mouseup",
                            marks={0: "0", 1: "1"},
                            tooltip={"placement": "bottom", "always_visible": False}),
            html.Div([
                dcc.Input(id="vel-range-min", type="number", value=None,
                          placeholder="min", step="any", debounce=True,
                          className="td-plain-number", style=_INPUT_STYLE),
                dcc.Input(id="vel-range-max", type="number", value=None,
                          placeholder="max", step="any", debounce=True,
                          className="td-plain-number", style=_INPUT_STYLE),
            ], title=(
                "Editable slider bounds. Typing a value also moves and, if "
                "necessary, extends the slider."
            ), style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                      "gap": "5px", "marginTop": "3px"}),
            html.Label("Net displacement range",
                       title="Per-trial start-to-end displacement range. Full span is treated as no range filter.",
                       style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.Graph(id="disp-range-hist", figure=build_mini_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="disp-range", min=0, max=1, step=0.01,
                            value=[0, 1], updatemode="mouseup",
                            marks={0: "0", 1: "1"},
                            tooltip={"placement": "bottom", "always_visible": False}),
            html.Div([
                dcc.Input(id="disp-range-min", type="number", value=None,
                          placeholder="min", step="any", debounce=True,
                          className="td-plain-number", style=_INPUT_STYLE),
                dcc.Input(id="disp-range-max", type="number", value=None,
                          placeholder="max", step="any", debounce=True,
                          className="td-plain-number", style=_INPUT_STYLE),
            ], title=(
                "Editable slider bounds. Typing a value also moves and, if "
                "necessary, extends the slider."
            ), style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                      "gap": "5px", "marginTop": "3px"}),
            html.Label("Net distance walked range",
                       title="Per-trial cumulative path length. Unlike displacement, turns and detours add to this value.",
                       style={"fontSize": "10px", "marginTop": "3px"}),
            dcc.Graph(id="walk-range-hist", figure=build_mini_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="walk-range", min=0, max=1, step=0.01,
                            value=[0, 1], updatemode="mouseup",
                            marks={0: "0", 1: "1"},
                            tooltip={"placement": "bottom", "always_visible": False}),
            html.Div([
                dcc.Input(id="walk-range-min", type="number", value=None,
                          placeholder="min", step="any", debounce=True,
                          className="td-plain-number", style=_INPUT_STYLE),
                dcc.Input(id="walk-range-max", type="number", value=None,
                          placeholder="max", step="any", debounce=True,
                          className="td-plain-number", style=_INPUT_STYLE),
            ], title=(
                "Editable cumulative-distance bounds. Typing a value moves and, "
                "if necessary, extends the slider."
            ), style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                      "gap": "5px", "marginTop": "3px"}),
            html.Button("Update all plots", id="btn-plot", n_clicks=0,
                        title=f"Rebuild all sections now. Changes auto-update after {PLOT_DEBOUNCE_MS / 1000:g}s idle.",
                        style={"width": "100%", "marginTop": "4px", "padding": "5px",
                               "border": "1px solid #0d6efd", "background": "white",
                               "color": "#0d6efd", "cursor": "pointer", "fontSize": "12px",
                               "borderRadius": "3px"}),
            ], open=True, className="sidebar-card sidebar-filters"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
            html.Summary(
                "Data subset",
                title="Trials, steps and metadata included in every analysis.",
            ),
            html.Label("Trial range",
                       title="Subset by CurrentTrial. Full span is treated as no trial subset.",
                       style={"fontSize": "10px", "marginTop": "2px"}),
            dcc.Graph(id="trial-range-hist", figure=build_mini_histogram(None),
                      config={"displayModeBar": False, "staticPlot": True},
                      style={"height": "58px", "margin": "0 0 -6px"}),
            dcc.RangeSlider(id="trial-range", min=0, max=1, step=1,
                            value=[0, 1], updatemode="mouseup",
                            marks={0: "0", 1: "1"},
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
                            value=[0, 1], updatemode="mouseup",
                            marks={0: "0", 1: "1"},
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
            html.Div(id="filter-detail", className="filter-detail",
                     children="Load data to see retention accounting."),
            ], open=True, className="sidebar-card sidebar-subset"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
                html.Summary("Advanced", style={"fontSize": "12px", "cursor": "pointer",
                                                  "fontWeight": "bold"}),
                html.Label(
                    "Names and visual style",
                    title=(
                        "Edit only the values you need. Name-only, clean-layout, "
                        "observer and heatmap-colour changes patch mounted plots; "
                        "mark styling changes rebuild rendered traces."
                    ),
                    style={"fontSize": "10px", "fontWeight": "bold",
                           "marginTop": "4px"},
                ),
                html.Div(
                    "Prefilled with config, scene, VR, fly and folder names "
                    "first; core trajectory, clean-layout units, Gandiva, "
                    "curtain-ring and series styles follow.",
                    style={"fontSize": "9px", "color": "#888"},
                ),
                dcc.Textarea(
                    id="visual-style-editor",
                    value=json.dumps(_VISUAL_STYLE_DEFAULTS, indent=2),
                    style={"width": "100%", "height": "300px", "fontSize": "9px",
                           "fontFamily": "monospace", "marginTop": "3px"},
                ),
                html.Button(
                    "Apply names and styles", id="btn-apply-visual-style",
                    n_clicks=0,
                    title=(
                        "Diff this JSON against the active style. Lightweight "
                        "presentation changes apply in place."
                    ),
                    style={"width": "100%", "marginTop": "3px", "padding": "4px",
                           "fontSize": "11px", "cursor": "pointer"},
                ),
                html.Button(
                    "Pre-fill from current data", id="btn-prefill-visual-style",
                    n_clicks=0,
                    style={"width": "100%", "marginTop": "3px", "padding": "4px",
                           "fontSize": "10px", "cursor": "pointer"},
                ),
                html.Div(id="visual-style-status",
                         style={"fontSize": "9px", "color": "#666",
                                "marginTop": "2px"}),
                html.Hr(style={"margin": "6px 0"}),
                html.Label(
                    "Panel columns",
                    title=(
                        "Auto uses 1–4 columns from the visible panel count. "
                        "Choose a number only when preparing a fixed layout."
                    ),
                    style={"fontSize": "10px", "marginTop": "3px"},
                ),
                dcc.Dropdown(
                    id="subplot-ncols",
                    options=[
                        {"label": "Auto · fit panel count", "value": 0},
                        {"label": "1 column", "value": 1},
                        {"label": "2 columns", "value": 2},
                        {"label": "3 columns", "value": 3},
                        {"label": "4 columns", "value": 4},
                    ],
                    value=0, clearable=False, style={"fontSize": "10px"},
                ),
                html.Div([
                    html.Label(
                        "Spatial units",
                        title=(
                            "Publication scale-bar conversion. One plotted "
                            "position unit equals one cm by default."
                        ),
                        style={"fontSize": "9px", "whiteSpace": "nowrap"},
                    ),
                    dcc.Input(
                        id="spatial-unit-scale", type="number", value=1,
                        min=1e-12, step="any", debounce=True,
                        className="td-plain-number",
                        style={**_INPUT_STYLE, "width": "58px"},
                    ),
                    dcc.Input(
                        id="spatial-unit-label", type="text", value="cm",
                        debounce=True, placeholder="cm",
                        style={**_INPUT_STYLE, "width": "48px"},
                    ),
                ], className="compact-control-row",
                   style={"marginTop": "4px"}),
                html.Label("Point budget",
                           title="Optional manual cap for rendered points; blank uses the selected render mode.",
                           style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Input(id="plot-points", type="number", value=None, min=500,
                          placeholder="auto (dynamic)", debounce=True,
                          style=_INPUT_STYLE),
                html.Div("Blank = auto-decimate to a browser-safe budget.",
                         style={"fontSize": "9px", "color": "#888"}),
                html.Hr(style={"margin": "6px 0"}),
                html.Details([
                html.Summary("Rare cleanup / outliers",
                             style={"fontSize": "10px", "cursor": "pointer",
                                    "fontWeight": "bold"}),
                html.Div([
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
                ]),
                ], style={"marginTop": "2px"}),
                html.Label("Raw trace columns", style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Dropdown(id="raw-columns", multi=True,
                             value=[],
                             style={"fontSize": "10px"}),

                html.Hr(style={"margin": "6px 0"}),
                html.Details([
                html.Summary("Legacy config-only names",
                             style={"fontSize": "10px", "cursor": "pointer"}),
                html.Div("Kept for older saved mappings; prefer Names and visual style above.",
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
            ], className="sidebar-card sidebar-advanced"),

            html.Hr(style={"margin": "6px 0"}),

            html.Details([
                html.Summary("Metadata", style={"fontSize": "12px", "cursor": "pointer",
                                                  "fontWeight": "bold"}),
                html.Pre(id="metadata-display",
                         style={"fontSize": "9px", "maxHeight": "200px", "overflow": "auto",
                                "background": "#f0f0f0", "padding": "4px", "borderRadius": "3px",
                                "whiteSpace": "pre-wrap"}),
            ], className="sidebar-card sidebar-metadata"),

            html.Div([
                html.A("❤️ by pvnkmrksk", href=REPO_URL, target="_blank",
                       rel="noopener noreferrer",
                       style={"color": "#2563eb", "textDecoration": "none",
                              "fontWeight": "650"}),
            ], className="td-footer-credit",
               style={"fontSize": "10px", "marginTop": "10px", "paddingTop": "7px",
                      "borderTop": "1px solid #e7ebf2", "color": "#667085"}),

        ], id="sidebar-panel", className="td-sidebar",
           style={"width": "285px", "padding": "8px", "overflowY": "auto",
                   "overflowX": "hidden",
                   "borderRight": "1px solid #ddd", "background": "#fafafa",
                   "flexShrink": "0", "height": "calc(100vh - 46px)"}),

        # ---- Main ----
        html.Div([
            # Summary
            html.Div([
                html.Div(id="data-summary", style={"minWidth": "0"}),
                html.Div("Visible layers: waiting for plots",
                         id="visible-layer-count", className="visible-layer-count"),
            ], className="data-summary-row"),
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
                dcc.Tab(label="Transitions", value="transition",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Gandiva", value="flow",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Polar", value="polar",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Heading time", value="heading",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Targets", value="roi",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Trial metrics", value="metrics",
                        className="view-tab", selected_className="view-tab-selected"),
                dcc.Tab(label="Diagnostics", value="diag",
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
                    html.Div([
                        html.Div([
                            html.Div([
                                html.Strong("Curtain-ring selection"),
                                html.Span(
                                    "Muted paths precede first entry; saturated "
                                    "paths continue into the future.",
                                    className="loop-observer-note",
                                ),
                            ]),
                            html.Div(
                                "Enable the observer to inspect crossing trials.",
                                id="loop-observer-status",
                                className="loop-observer-status",
                            ),
                        ], className="loop-observer-heading"),
                        dcc.Graph(
                            id="loop-observer-plot", figure=_EMPTY,
                            config={
                                **GRAPH_CONFIG,
                                "edits": {"shapePosition": True},
                            },
                            style={"width": "100%"},
                        ),
                    ], id="loop-observer-wrap", className="loop-observer-wrap",
                       style={"display": "none"}),
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

                # --- Conditional transition probability + clicked paths ---
                html.Div([
                    html.Div([
                        html.H4(
                            "Transition probability",
                            title=(
                                "For each cell, the denominator is unique trials "
                                "that entered it. Colour is the fraction that "
                                "subsequently crossed or ended across the "
                                "horizontal split."
                            ),
                        ),
                        html.Span(
                            "Heatmap × curtain ring · click a bin to inspect paths",
                            className="plot-section-kicker",
                        ),
                        html.Span(
                            "Enable the transition calculation in the sidebar.",
                            id="transition-status",
                            className="stats-status-chip",
                        ),
                    ], className="plot-section-heading"),
                    dcc.Graph(
                        id="transition-plot",
                        figure=_msg_figure(
                            "Enable transition probability in the sidebar."),
                        config=GRAPH_CONFIG,
                        style={"width": "100%"},
                    ),
                ], id="view-transition", className="plot-section",
                   style={**_PANEL_STYLE}),

                # --- Gandiva local direction field ---
                html.Div(
                    [html.Div([html.H4(
                                   "Gandiva plot",
                                   title=(
                                       "Named for Arjuna's divine bow: a local field "
                                       "that can rain arrows in every direction, with "
                                       "their strength and abundance visible at once."
                                   )),
                               html.Span(
                                   "Arjuna's bow · local direction, strength and abundance",
                                   className="plot-section-kicker")],
                              className="plot-section-heading"),
                     html.Div([
                         html.Div([
                             html.Span("Mean direction",
                                       className="flow-legend-title"),
                             html.Div([
                                 html.Span("F", className=(
                                     "flow-wheel-label flow-wheel-forward")),
                                 html.Span("R", className=(
                                     "flow-wheel-label flow-wheel-right")),
                                 html.Span("B", className=(
                                     "flow-wheel-label flow-wheel-back")),
                                 html.Span("L", className=(
                                     "flow-wheel-label flow-wheel-left")),
                                 html.Span(className="flow-direction-wheel"),
                             ], className="flow-wheel-frame",
                                role="img",
                                **{"aria-label": (
                                    "Circular direction colour legend: forward "
                                    "at top, right clockwise, back at bottom, "
                                    "and left counter-clockwise.")}),
                         ], className="flow-legend-group"),
                         html.Div([
                             html.Span("Abundance",
                                       className="flow-legend-title"),
                             html.Div([
                                 html.Span([
                                     html.I(className=(
                                         "flow-arrow-sample flow-arrow-low")),
                                     html.Small("low"),
                                 ], className="flow-abundance-step"),
                                 html.Span([
                                     html.I(className=(
                                         "flow-arrow-sample flow-arrow-mid")),
                                     html.Small("medium"),
                                 ], className="flow-abundance-step"),
                                 html.Span([
                                     html.I(className=(
                                         "flow-arrow-sample flow-arrow-high")),
                                     html.Small("high"),
                                 ], className="flow-abundance-step"),
                             ], className="flow-abundance-scale",
                                role="img",
                                **{"aria-label": (
                                    "Abundance legend: direction strokes become more "
                                    "opaque and thicker from low to high.")}),
                         ], className="flow-legend-group"),
                     ], id="flow-field-legend",
                        className="flow-field-legend"),
                     dcc.Loading(
                        dcc.Graph(id="flow-plot", figure=_EMPTY,
                                  config=GRAPH_CONFIG,
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"})],
                    id="view-flow", className="plot-section", style={**_PANEL_STYLE}),

                # --- Polar ---
                html.Div(
                    [html.Div([html.H4("Polar direction"),
                               html.Span("Per-trial vectors and pooled population mean", className="plot-section-kicker"),
                               html.Span("stats queued", id="polar-stats-status",
                                         className="stats-status-chip")],
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

                # --- Optional heading / orientation over elapsed trial time ---
                html.Div(
                    [html.Div([
                        html.H4("Heading over time"),
                        html.Span(
                            "Signed angle · −180° to 180°",
                            className="plot-section-kicker"),
                        html.Span(
                            "off", id="heading-time-status",
                            className="stats-status-chip"),
                    ], className="plot-section-heading"),
                     dcc.Graph(
                         id="heading-time-plot",
                         figure=_msg_figure(
                             "Enable heading over time in the sidebar."),
                         config=GRAPH_CONFIG,
                         style={"width": "100%"},
                     )],
                    id="view-heading", className="plot-section",
                    style={**_PANEL_STYLE, "display": "none"}),

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
                                       "pointerEvents": "none"}),
                     html.Div([
                         html.H4(
                             "Observation-window diagnostics",
                             title=(
                                 "Each subplot compares the current panel groups "
                                 "(scene, config, VR, fly or folder). Bars are "
                                 "used for proportions; movement panels show "
                                 "segment-level swarms or violins per window."
                             ),
                         ),
                         html.Span(
                             "Grouped proportions + segment-level movement distributions",
                             className="plot-section-kicker",
                         ),
                     ], className="plot-section-heading"),
                     dcc.Loading(
                        dcc.Graph(id="custom-region-diagnostics-plot",
                                  figure=_msg_figure(
                                      "Enable an observation window to inspect it."),
                                  config=GRAPH_CONFIG,
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"})],
                    id="view-roi", className="plot-section", style={**_PANEL_STYLE}),

                # --- Per-trial movement metrics ---
                html.Div(
                    [html.Div([html.H4(
                                   "Trial metrics",
                                   title=(
                                       "One point is one SourceFile+Trial+Step "
                                       "segment. With observation windows enabled, "
                                       "all four values use only observed sections."
                                   )),
                               html.Span(
                                   "Per-trial distributions · window-scoped when enabled",
                                   className="plot-section-kicker"),
                               html.Span("stats queued", id="metrics-stats-status",
                                         className="stats-status-chip")],
                              className="plot-section-heading"),
                     dcc.Loading(
                        dcc.Graph(id="trial-metrics-plot", figure=_EMPTY,
                                  config=GRAPH_CONFIG,
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s",
                                       "pointerEvents": "none"})],
                    id="view-metrics", className="plot-section", style={**_PANEL_STYLE}),

                # --- Raw, load-time diagnostics (intentionally last) ---
                html.Div([
                    html.Div([html.H4("Diagnostics"),
                              html.Span("Native distributions; independent of active filters",
                                        className="plot-section-kicker")],
                             className="plot-section-heading"),
                    html.Div([
                        dcc.Graph(id="vel-histogram", figure=_EMPTY,
                                  config=GRAPH_CONFIG,
                                  style={"flex": "1", "minWidth": "0"}),
                        dcc.Graph(id="disp-histogram", figure=_EMPTY,
                                  config=GRAPH_CONFIG,
                                  style={"flex": "1", "minWidth": "0"}),
                    ], style={"display": "flex", "gap": "6px"}),
                    html.Div([
                        dcc.Checklist(
                            id="diag-start-heading-toggle",
                            options=[{"label": " Show raw starting-heading null distribution",
                                      "value": "on"}],
                            value=["on"], inline=True,
                            style={"fontSize": "12px", "color": "#475569",
                                   "padding": "4px 8px"}),
                        html.Span("start-angle stats queued",
                                  id="initial-stats-status",
                                  className="stats-status-chip"),
                        html.Div(
                            dcc.Graph(id="initial-heading-plot", figure=_EMPTY,
                                      config=GRAPH_CONFIG, style={"width": "100%"}),
                            id="diag-start-heading-wrap", style={"display": "block"}),
                    ]),
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
    dcc.Store(id="flow-figure-store"),
    dcc.Store(id="transition-data-store"),
    dcc.Store(id="heatmap-variants"),
    dcc.Store(id="heatmap-color-distributions"),
    dcc.Store(id="heatmap-color-values"),
    dcc.Store(id="vel-range-effective"),
    dcc.Store(id="panel-order-store"),
    dcc.Store(id="auto-thresholds"),
    dcc.Store(id="drop-data"),
    dcc.Store(id="view-render-state", data={}),
    dcc.Store(id="spatial-render-state", data={}),
    dcc.Store(id="polar-render-state", data={}),
    dcc.Store(id="heading-time-render-state", data={}),
    dcc.Store(id="metrics-render-state", data={}),
    dcc.Store(id="gandiva-render-state", data={}),
    dcc.Store(id="targets-render-state", data={}),
    dcc.Store(
        id="loop-rings-store",
        data=[{"id": "ring-1", "name": "Ring 1",
               "x": 0.0, "z": 0.0, "radius": 3.0}],
    ),
    dcc.Store(
        id="custom-regions-store",
        data=[{
            "id": "region-1", "name": "Window 1",
            "x0": -3.0, "x1": 3.0, "z0": -3.0, "z1": 3.0,
        }],
    ),
    dcc.Store(id="custom-region-stats-store", data={}),
    dcc.Store(id="custom-region-analysis-request", data={}),
    dcc.Store(id="custom-region-debounce-state", data={}),
    dcc.Store(id="minimal-layout-store", data=False),
    dcc.Store(id="trial-subset-state", data={}),
    dcc.Store(id="sidebar-collapsed-store", data=False),
    dcc.Store(id="visual-style-store", data=_VISUAL_STYLE_DEFAULTS),
    dcc.Store(id="visual-style-diff-store", data={}),
    dcc.Store(id="stats-overlay-store", data={}),
    dcc.Store(id="operation-progress", data=_progress_snapshot()),
    dcc.Store(id="url-restored", data=False),
    dcc.Store(id="auto-replot-state"),
    dcc.Checklist(id="rebase-origin", options=[{"label": "", "value": "on"}],
                  value=[], style={"display": "none"}),
    dcc.Interval(id="autoload-interval", interval=500, max_intervals=1),
    dcc.Interval(
        id="load-progress-interval", interval=2000, max_intervals=10,
        disabled=True,
    ),
    dcc.Interval(id="auto-replot-interval", interval=PLOT_DEBOUNCE_MS,
                 max_intervals=1, disabled=True),
    dcc.Interval(id="stats-delay-interval", interval=650,
                 max_intervals=1, disabled=True),
], className="td-app",
   style={"fontFamily": "system-ui, -apple-system, sans-serif", "margin": "0"})


from dashboard_callbacks import register_callbacks

# Keep callback functions import-compatible for direct smoke tests while the
# registration/wiring itself lives in a focused module.
globals().update(register_callbacks(globals()))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Daari Deepa — interactive trajectory analysis",
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
        # Seed the source input, not dcc.Location.search.  Mutating Location
        # here replaced a browser's full shared URL with ``?glob=...`` before
        # restore_from_url could read settings such as optional-panel state.
        pending = [app.layout]
        while pending:
            component = pending.pop()
            if getattr(component, "id", None) == "glob-input":
                component.value = args.glob
                break
            children = getattr(component, "children", None)
            if isinstance(children, (list, tuple)):
                pending.extend(children)
            elif children is not None and not isinstance(children, str):
                pending.append(children)

    LOGGER.info("server.start url=http://%s:%d/", args.host, args.port)
    app.run(host=args.host, port=args.port, debug=args.debug)
