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


def humanise_config(raw: str) -> str:
    if raw in _USER_LUT:
        return _USER_LUT[raw]
    if raw in _MANUAL_LUT:
        return _MANUAL_LUT[raw]

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
    meta = {"folder": folder, "configs": {}, "fly_metadata": None}
    for f in Path(folder).glob("*.json"):
        data = _loads_tolerant(f.read_text())
        if data is None:
            continue
        if "FlyMetaData" in f.name or "metadata" in f.name.lower():
            meta["fly_metadata"] = data
        elif "sequenceConfig" not in f.name:
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


def segment_reached(gx, gz, roi_x, roi_z, reach) -> bool:
    """True if the (already per-segment) trajectory comes within `reach` of the
    ROI centre at any sample."""
    return bool(np.any((gx - roi_x) ** 2 + (gz - roi_z) ** 2 <= reach * reach))


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
    for cfg, sub in df.groupby("ConfigFile", sort=False):
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
        t = pd.DataFrame({"_seg_id": sub["_seg_id"].to_numpy(),
                          "reached_left": ml, "reached_right": mr})
        g = t.groupby("_seg_id", sort=False).agg(
            reached_left=("reached_left", "any"),
            reached_right=("reached_right", "any")).reset_index()
        meta = sub.groupby("_seg_id", sort=False).agg(
            ConfigFile=("ConfigFile", "first"), VR=("VR", "first"),
            FlyID=("FlyID", "first")).reset_index()
        parts.append(g.merge(meta, on="_seg_id"))
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
    for cfg, sub in table.groupby("ConfigFile", sort=False):
        tot = len(sub)
        lr = int(sub["reached_left"].sum())
        rr = int(sub["reached_right"].sum())
        out[cfg] = {"total": tot, "left_reached": lr, "right_reached": rr,
                    "left_frac": lr / tot if tot else 0.0,
                    "right_frac": rr / tot if tot else 0.0}
    return out


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

    df["_seg_id"] = (df["SourceFolder"] + "_" + df["VR"].astype(str) + "_T"
                     + df["CurrentTrial"].astype(str) + "_S"
                     + df["CurrentStep"].astype(str))

    for c in ["CurrentTrial", "CurrentStep", "GameObjectPosX", "GameObjectPosZ"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.dropna(subset=required, inplace=True)
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

# Per-subplot pixel height. With a 2-col layout each subplot is ~half the main
# width, so ~480px tall keeps each box roughly square; the page scrolls when
# there are many rows rather than squishing them.
SUBPLOT_PX = 480

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


def _group_frames(df, group_by, pool_mode, ncols):
    col_map = {"config": "ConfigFile", "vr": "VR", "flyid": "FlyID",
               "scene": "SceneName", "file": "SourceFolder"}
    if pool_mode == "pooled" or group_by == "all":
        groups = {"All Data": df}
    else:
        gcol = col_map.get(group_by, "ConfigFile")
        groups = ({str(k): v for k, v in df.groupby(gcol)}
                  if gcol in df.columns else {"All Data": df})
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
    span = max(xmax - xmin, zmax - zmin) * pad
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


def _add_roi_overlay(fig, group_names, ncols, rois_by_cfg, reach, counts=None):
    """Draw each config's ROI targets (reach circle + centre dot) on its subplot,
    plus an optional corner tally of left/right reached fractions. ROIs are drawn
    as shapes so they add no traces and don't leak into animation frames."""
    for i, gname in enumerate(group_names):
        rlist = rois_by_cfg.get(gname)
        if not rlist:
            continue
        r_, c_ = i // ncols + 1, i % ncols + 1
        for roi in rlist:
            col = _ROI_SIDE_COLOR.get(roi["side"], "#6c757d")
            fig.add_shape(type="circle", x0=roi["x"] - reach, x1=roi["x"] + reach,
                          y0=roi["z"] - reach, y1=roi["z"] + reach,
                          line=dict(color=col, width=1.4, dash="dot"),
                          fillcolor=col, opacity=0.12, layer="below",
                          row=r_, col=c_)
            dot = max(0.4, reach * 0.08)
            fig.add_shape(type="circle", x0=roi["x"] - dot, x1=roi["x"] + dot,
                          y0=roi["z"] - dot, y1=roi["z"] + dot,
                          line=dict(color=col, width=0), fillcolor=col,
                          opacity=0.95, layer="above", row=r_, col=c_)
        if counts and gname in counts:
            cc = counts[gname]
            txt = (f"L {cc['left_reached']}/{cc['total']} "
                   f"({100*cc['left_frac']:.0f}%)<br>"
                   f"R {cc['right_reached']}/{cc['total']} "
                   f"({100*cc['right_frac']:.0f}%)")
            fig.add_annotation(text=txt, showarrow=False, xref="x domain",
                               yref="y domain", x=0.02, y=0.98, xanchor="left",
                               yanchor="top", align="left", font=dict(size=9),
                               bgcolor="rgba(255,255,255,0.7)", bordercolor="#ccc",
                               borderwidth=0.5, row=r_, col=c_)


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
    group_names = list(groups.keys())
    n = len(group_names)
    nrows = max(1, (n + ncols - 1) // ncols)
    titles = [humanise_config(t) for t in group_names]

    fig = make_subplots(rows=nrows, cols=ncols, subplot_titles=titles,
                        horizontal_spacing=0.05, vertical_spacing=0.07)

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
                 "velocity": "Speed (u/s)"}[color_by]
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
        frames = []
        for fi in range(N_ANIM_FRAMES + 1):
            frac = fi / N_ANIM_FRAMES
            frame_traces = []
            for rec in records:
                x, y, mc = _record_arrays(rec, frac)
                if rec["mode"] == "markers":
                    frame_traces.append(go.Scattergl(
                        x=x, y=y, mode="markers", opacity=0.75,
                        marker=dict(size=3, color=mc, colorscale=SEQ_COLORSCALE,
                                    cmin=rec["cmin"], cmax=rec["cmax"])))
                else:
                    frame_traces.append(go.Scattergl(
                        x=x, y=y, mode="lines", opacity=0.75,
                        line=dict(color=rec["color"], width=1.2)))
            frames.append(go.Frame(data=frame_traces, name=str(fi)))
        fig.frames = frames

    _apply_axis_sync(fig, nrows, ncols, df, uirev="traj_view")

    for i, ann in enumerate(fig.layout.annotations):
        if i < len(group_names):
            ann.update(hovertext=group_names[i], font=dict(size=12))

    if show_rois and rois:
        _add_roi_overlay(fig, group_names, ncols, rois,
                         float(reach_radius or 3.0), counts=roi_counts)

    show_legend = color_by in ("individual", "vr")
    fig.update_layout(
        height=60 + nrows * SUBPLOT_PX,
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


def build_heatmap_figure(df, group_by="config", pool_mode="separate", ncols=2,
                         bin_size=20.0, log_scale=False, bound_pct=98.0,
                         metric="count", cmin=None, cmax=None, crange_mode="value"):
    if df is None or len(df) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No data after filtering", showarrow=False,
                           xref="paper", yref="paper", x=0.5, y=0.5, font_size=18)
        fig.update_layout(height=400, template="plotly_white")
        return fig

    groups = _group_frames(df, group_by, pool_mode, ncols)
    group_names = list(groups.keys())
    n = len(group_names)
    nrows = max(1, (n + ncols - 1) // ncols)
    titles = [humanise_config(t) for t in group_names]

    fig = make_subplots(rows=nrows, cols=ncols, subplot_titles=titles,
                        horizontal_spacing=0.05, vertical_spacing=0.07)

    # Bound to the central `bound_pct`% of the data so a few outliers don't
    # blow the extent (and the bin count) out. bin_size is in data units —
    # each pixel is bs×bs units square; decimals allowed for tiny arenas.
    rng = _robust_range(df, bound_pct) if bound_pct and bound_pct < 100 else _shared_range(df)
    rx, rz = rng
    bs = float(bin_size) if bin_size and bin_size > 0 else default_bin_size(df)
    span = max(rx[1] - rx[0], rz[1] - rz[0])
    if span / bs > MAX_HEATMAP_BINS:        # only clamps in pathological cases
        bs = span / MAX_HEATMAP_BINS
    xedges = np.arange(rx[0], rx[1] + bs, bs)
    yedges = np.arange(rz[0], rz[1] + bs, bs)
    xc = 0.5 * (xedges[:-1] + xedges[1:])
    yc = 0.5 * (yedges[:-1] + yedges[1:])

    metric = metric if metric in METRIC_UNITS else "count"
    dt = _median_dt(df) if metric == "time" else 1.0
    unit = METRIC_UNITS[metric]

    # First pass: raw histograms + the metric-converted matrices
    mats = []
    gmax = 0.0
    for gname in group_names:
        gdf = groups[gname]
        H, _, _ = np.histogram2d(gdf["GameObjectPosX"].values,
                                 gdf["GameObjectPosZ"].values,
                                 bins=[xedges, yedges])
        M = H.T.astype(float)  # [row=y, col=x]
        if metric == "time":
            M = M * dt
        elif metric == "percent":
            tot = M.sum()
            M = (100.0 * M / tot) if tot > 0 else M
        mats.append(M)
        gmax = max(gmax, float(M.max()) if M.size else 0.0)

    # --- Resolve colour range (cmin/cmax). Blank => auto. ---
    nonzero = np.concatenate([m[m > 0].ravel() for m in mats]) if mats else np.array([])
    # Auto lower bound: log needs a positive floor (smallest nonzero); linear
    # starts at 0 so the scale isn't compressed.
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
    if metric == "time":               # occupancy residency floor: never < 100 ms
        mmin = max(mmin, 0.1)
    if log_scale:
        mmin = max(mmin, 1e-9)
    if mmax <= mmin:
        mmax = mmin * 10 if log_scale else mmin + 1

    if log_scale:
        zmin, zmax = np.log10(mmin), np.log10(mmax)
        tickvals, ticktext = _log_colorbar(mmin, mmax, metric)
        cbar = dict(title=f"{unit} (log)", thickness=12, len=0.5,
                    tickvals=tickvals, ticktext=ticktext)
    else:
        zmin, zmax = mmin, mmax
        cbar = dict(title=unit, thickness=12, len=0.5)

    # IMPORTANT: pass z / customdata as plain Python lists. 2-D numpy arrays get
    # serialised with Plotly-6's typed-array ("bdata") encoding, which Dash does
    # not round-trip for 2-D data — z arrives as `undefined` in the browser and
    # the heatmap renders blank. Lists serialise as normal JSON and always work.
    xl, yl = xc.tolist(), yc.tolist()
    hov = "x=%{x:.1f} z=%{y:.1f}<br>%{customdata:.3g} " + unit + "<extra></extra>"
    for idx, M in enumerate(mats):
        disp = M.copy()
        disp[disp == 0] = np.nan          # blank empty cells
        z = np.log10(disp) if log_scale else disp
        fig.add_trace(
            go.Heatmap(x=xl, y=yl, z=z.tolist(), customdata=M.tolist(),
                       colorscale=HEATMAP_COLORSCALE, zmin=zmin, zmax=zmax,
                       showscale=(idx == 0), colorbar=cbar, hovertemplate=hov),
            row=idx // ncols + 1, col=idx % ncols + 1,
        )

    _apply_axis_sync(fig, nrows, ncols, df, uirev="traj_view", rng=rng)

    for i, ann in enumerate(fig.layout.annotations):
        if i < len(group_names):
            ann.update(hovertext=group_names[i], font=dict(size=12))

    fig.update_layout(
        height=60 + nrows * SUBPLOT_PX,
        margin=dict(l=50, r=80, t=50, b=40),
        template="plotly_white", dragmode="pan", showlegend=False,
    )
    return fig


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
        template="plotly_white", dragmode="select",
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
        template="plotly_white", dragmode="select",
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
                       line=dict(width=1, color=COLORS[i % len(COLORS)]), name=col),
            row=i + 1, col=1,
        )
    fig.update_layout(height=max(180, n * 140), margin=dict(l=50, r=10, t=25, b=20),
                       template="plotly_white", showlegend=False)
    return fig


def _msg_figure(text, height=440):
    fig = go.Figure()
    fig.add_annotation(text=text, showarrow=False, xref="paper", yref="paper",
                       x=0.5, y=0.5, font_size=15)
    fig.update_layout(height=height, template="plotly_white")
    return fig


def build_roi_violin_figure(df, rois_by_cfg, reach):
    """Per-animal fraction of trials that reached the left vs right ROI, one pair
    of violins per config with every visible animal as a scatter point. `df` is
    already filtered to the visible subset, so the violins reflect exactly what
    is toggled on."""
    tbl = roi_reached_table(df, rois_by_cfg, reach)
    if tbl is None or len(tbl) == 0:
        return _msg_figure("No left/right ROI targets in these configs — "
                           "nothing to count. Load Choice/BinaryChoice data.")
    grp = tbl.groupby(["ConfigFile", "animal"], sort=False).agg(
        n=("_seg_id", "size"),
        frac_left=("reached_left", "mean"),
        frac_right=("reached_right", "mean")).reset_index()
    grp["label"] = grp["ConfigFile"].map(humanise_config)
    order = sorted(grp["label"].unique())
    n_animals = grp["animal"].nunique()

    fig = go.Figure()
    for side, color in (("left", _ROI_SIDE_COLOR["left"]),
                        ("right", _ROI_SIDE_COLOR["right"])):
        fig.add_trace(go.Violin(
            x=grp["label"], y=grp["frac_" + side], name=side.capitalize(),
            legendgroup=side, scalegroup=side, line_color=color, fillcolor=color,
            opacity=0.5, points="all", pointpos=0, jitter=0.35,
            marker=dict(size=5, opacity=0.75), box_visible=True,
            meanline_visible=True, spanmode="hard",
            customdata=grp["animal"],
            hovertemplate=side + " frac=%{y:.2f}<br>%{customdata}<extra></extra>"))
    fig.update_layout(
        violinmode="group", template="plotly_white", height=470,
        title=dict(text=f"Fraction of trials reaching each ROI "
                        f"(reach {reach:g} u · {n_animals} animals)", font_size=13),
        yaxis=dict(title="fraction of trials reaching", range=[-0.03, 1.03],
                   zeroline=True),
        xaxis=dict(title="config", categoryorder="array", categoryarray=order),
        legend=dict(orientation="h", y=1.02, yanchor="bottom", x=1, xanchor="right"),
        margin=dict(l=60, r=20, t=50, b=90))
    return fig


def build_polar_figure(df, group_by="config", pool_mode="separate", ncols=2,
                       color_by="velocity", moving_only=False, walk_thresh=None,
                       max_points=None, rois=None, reach_radius=3.0, show_rois=False):
    """Each trial's path in polar coordinates: r = distance from origin,
    theta = atan2(X, Z) so 0° = forward/+Z (top of the plot, clockwise) — the
    SAME left-handed frame as the trajectory view and the ROI targets. Colour by
    instantaneous velocity or tortuosity; optionally keep only moving (walking)
    samples above a speed threshold."""
    if df is None or len(df) == 0:
        return _msg_figure("No data after filtering")

    groups = _group_frames(df, group_by, pool_mode, ncols)
    names = list(groups.keys())
    n = len(names)
    nrows = max(1, (n + ncols - 1) // ncols)
    specs = [[{"type": "polar"} for _ in range(ncols)] for _ in range(nrows)]
    vspace = min(0.06, 0.5 / max(nrows, 1))
    fig = make_subplots(rows=nrows, cols=ncols, specs=specs,
                        subplot_titles=[humanise_config(t) for t in names],
                        horizontal_spacing=0.06, vertical_spacing=vspace)

    # Shared radial range from the data (WebGL polar doesn't auto-scale reliably,
    # and a common range makes subplots comparable). 98th pct drops outliers.
    r_all = np.hypot(df["GameObjectPosX"].to_numpy(), df["GameObjectPosZ"].to_numpy())
    r_all = r_all[np.isfinite(r_all)]
    rmax = float(np.percentile(r_all, 98)) if r_all.size else 1.0
    rmax = max(rmax, 1.0)

    # SVG polar (Scatterpolar): WebGL Scatterpolargl has broken scene management
    # on re-render (crashes with a "_scene" error), so we use SVG and a tighter
    # budget. Plenty for a density/heading view.
    budget = int(max_points) if (max_points and max_points > 0) else BUDGET_POLAR
    total_segs = sum(g["_seg_id"].nunique() for g in groups.values())
    pts_lim = max(2, budget // max(total_segs, 1))

    # Shared per-point colour series + scale across all subplots.
    cser, cmin, cmax, cbar = None, None, None, ""
    if color_by == "velocity":
        vv = smoothed_velocity(df, 10)
        f = vv[np.isfinite(vv)]
        cser = pd.Series(vv, index=df.index)
        cmin, cmax = 0.0, (float(np.percentile(f, 99)) if f.size else 1.0)
        cbar = "Speed (u/s)"
    elif color_by == "tortuosity":
        tt = compute_tortuosity(df)
        f = tt[np.isfinite(tt)]
        cser = pd.Series(tt, index=df.index)
        cmin, cmax = 1.0, (float(np.percentile(f, 95)) if f.size else 3.0)
        cbar = "Tortuosity"

    walk = float(walk_thresh) if (moving_only and walk_thresh) else None
    sser = pd.Series(smoothed_velocity(df, 10), index=df.index) if walk is not None else None

    for idx, gname in enumerate(names):
        gdf = groups[gname]
        row, col = idx // ncols + 1, idx % ncols + 1
        gg = gdf.groupby("_seg_id", sort=False)
        pos = gg.cumcount().to_numpy()
        size = gg["_seg_id"].transform("size").to_numpy()
        keep = (pos % np.maximum(1, size // pts_lim)) == 0
        sub = gdf[keep]
        x = sub["GameObjectPosX"].to_numpy(); z = sub["GameObjectPosZ"].to_numpy()
        r = np.hypot(x, z)
        th = np.degrees(np.arctan2(x, z))
        seg = sub["_seg_id"].to_numpy()
        if sser is not None:                       # blank the non-moving samples
            m = sser.reindex(sub.index).to_numpy() < walk
            r = r.astype(float); th = th.astype(float)
            r[m] = np.nan; th[m] = np.nan
        # Pass r/theta/colour as plain Python lists: Plotly-6 encodes numpy arrays
        # as typed-array bdata, which the clientside newPlot (§7.3) can't decode —
        # the trace would arrive empty. Lists serialise as ordinary JSON.
        if cser is not None:
            cv = cser.reindex(sub.index).to_numpy()
            rr, tt2, cc = _nan_join(r, th, seg, cv)
            fig.add_trace(go.Scatterpolar(
                r=rr.tolist(), theta=tt2.tolist(), mode="markers",
                hoverinfo="skip", showlegend=False,
                marker=dict(size=2.6, color=cc.tolist(), colorscale=SEQ_COLORSCALE,
                            cmin=cmin, cmax=cmax, showscale=(idx == 0),
                            colorbar=dict(title=cbar, thickness=12, len=0.5))),
                row=row, col=col)
        else:
            rr, tt2, _ = _nan_join(r, th, seg, None)
            fig.add_trace(go.Scatterpolar(
                r=rr.tolist(), theta=tt2.tolist(), mode="lines", hoverinfo="skip",
                showlegend=False, line=dict(width=1, color="#1f77b4")),
                row=row, col=col)

        if show_rois and rois:
            for roi in rois.get(gname, []):
                rr_ = math.hypot(roi["x"], roi["z"])
                th_ = math.degrees(math.atan2(roi["x"], roi["z"]))
                fig.add_trace(go.Scatterpolar(
                    r=[rr_], theta=[th_], mode="markers", showlegend=False,
                    hovertemplate=f"{roi['side']} ROI<extra></extra>",
                    marker=dict(size=11, symbol="circle-open",
                                line=dict(width=2),
                                color=_ROI_SIDE_COLOR.get(roi["side"], "#555"))),
                    row=row, col=col)

    # 0° at top, clockwise — matches the trajectory subplot orientation. Explicit
    # radial range so WebGL points (and the ROI rings) are actually on-scale.
    fig.update_polars(angularaxis=dict(rotation=90, direction="clockwise",
                                       thetaunit="degrees"),
                      radialaxis=dict(range=[0, rmax], angle=90, tickangle=90),
                      bgcolor="white")
    fig.update_layout(height=60 + nrows * 420, template="plotly_white",
                      margin=dict(l=40, r=90, t=50, b=40), showlegend=False)
    return fig



# ---------------------------------------------------------------------------
# Dash App
# ---------------------------------------------------------------------------

app = Dash(__name__, suppress_callback_exceptions=True)
app.title = "Trajectory Dashboard"

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
        return _DATA_CACHE[key], _STATS_CACHE.get(key), _META_CACHE.get(key, [])

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
    df.sort_values(["SourceFolder", "VR", "CurrentTrial", "CurrentStep", "Current Time"],
                   inplace=True)
    df.reset_index(drop=True, inplace=True)
    stats = compute_segment_stats(df)
    _DATA_CACHE[key] = df
    _STATS_CACHE[key] = stats
    _META_CACHE[key] = metas
    _LOAD_PROGRESS.update(active=False)
    return df, stats, metas


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

_EMPTY = go.Figure().update_layout(height=190, template="plotly_white")

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
                             "fontFamily": "monospace"}),
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
                          value=["on"], style={"fontSize": "11px", "marginTop": "3px"}),
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
                              style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("Bound %", style={"fontSize": "10px"}),
                    dcc.Input(id="heatmap-bound", type="number", value=98, min=50,
                              max=100, step="any", debounce=True,
                              style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
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
                              style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
                ], style={"flex": "1"}),
                html.Div([
                    html.Label("cmax", style={"fontSize": "10px"}),
                    dcc.Input(id="heatmap-cmax", type="number", value=None,
                              placeholder="auto", step="any", debounce=True,
                              style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
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
            html.Div("Targets auto-load from the scene configs (Choice / "
                     "BinaryChoice; polar or cartesian). A trial 'reaches' an ROI "
                     "if its path comes within the radius. Left = −X, right = +X.",
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
            html.Label("Walk speed threshold (u/s)", style={"fontSize": "10px",
                                                             "marginTop": "2px"}),
            dcc.Input(id="polar-walk", type="number", value=50, min=0, step="any",
                      debounce=True, style={"width": "100%", "fontSize": "11px",
                                            "padding": "3px"}),
            html.Div("Each trial's path as r (distance from origin) vs angle "
                     "(0° = forward, clockwise — same frame as trajectories). "
                     "Moving-only keeps samples above the walk speed; ROI targets "
                     "appear as rings.",
                     style={"fontSize": "9px", "color": "#888", "marginTop": "2px"}),

            html.Hr(style={"margin": "6px 0"}),

            html.Label("Filters", style={"fontWeight": "bold", "fontSize": "12px"}),
            html.Div([
                html.Label("Max velocity", style={"fontSize": "10px"}),
                dcc.Input(id="vel-threshold", type="number", value=None,
                          placeholder="e.g. 500",
                          style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
            ], style={"marginBottom": "3px"}),
            html.Div([
                html.Label("Min displacement", style={"fontSize": "10px"}),
                dcc.Input(id="min-disp", type="number", value=None, placeholder="e.g. 5",
                          style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
            ], style={"marginBottom": "3px"}),
            html.Div([
                html.Label("Trim samples", style={"fontSize": "10px"}),
                dcc.Input(id="trim-samples", type="number", value=100,
                          style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
            ], style={"marginBottom": "3px"}),
            html.Div([
                html.Label("Jump buffer (s)", style={"fontSize": "10px"}),
                dcc.Input(id="jump-buffer", type="number", value=0.1, step=0.01,
                          style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
            ], style={"marginBottom": "3px"}),

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
                          style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
                html.Label("Max plot points", style={"fontSize": "10px", "marginTop": "3px"}),
                dcc.Input(id="plot-points", type="number", value=None, min=500,
                          placeholder="auto (dynamic)",
                          style={"width": "100%", "fontSize": "11px", "padding": "3px"}),
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
                   "borderRight": "1px solid #ddd", "background": "#fafafa",
                   "flexShrink": "0", "height": "calc(100vh - 46px)"}),

        # ---- Main ----
        html.Div([
            # Summary
            html.Div(id="data-summary",
                     style={"fontSize": "11px", "padding": "3px 8px", "background": "#e9ecef",
                            "borderRadius": "3px", "margin": "0 0 3px 0", "flexShrink": "0"}),

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
                                       "transition": "opacity .2s"}),
                ], id="view-traj", style={**_PANEL_STYLE}),

                # --- Heatmap ---
                html.Div(
                    dcc.Loading(
                        dcc.Graph(id="heatmap-plot", figure=_EMPTY,
                                  config={"scrollZoom": True, "displayModeBar": True},
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s"}),
                    id="view-heat", style={**_PANEL_STYLE, "visibility": "hidden"}),

                # --- Diagnostics ---
                html.Div([
                    html.Div([
                        html.Div([
                            dcc.Graph(id="vel-histogram", figure=_EMPTY,
                                      config={"displayModeBar": False}),
                            html.Div(id="vel-selection-info",
                                     style={"fontSize": "10px", "color": "#666",
                                            "textAlign": "center"}),
                        ], style={"flex": "1", "minWidth": "0"}),
                        html.Div([
                            dcc.Graph(id="disp-histogram", figure=_EMPTY,
                                      config={"displayModeBar": False}),
                            html.Div(id="disp-selection-info",
                                     style={"fontSize": "10px", "color": "#666",
                                            "textAlign": "center"}),
                        ], style={"flex": "1", "minWidth": "0"}),
                    ], style={"display": "flex", "gap": "6px"}),
                    dcc.Loading(
                        dcc.Graph(id="raw-trace-plot", figure=_EMPTY,
                                  config={"scrollZoom": True}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s"}),
                ], id="view-diag", style={**_PANEL_STYLE, "visibility": "hidden"}),

                # --- ROI counts (violins) ---
                html.Div(
                    dcc.Loading(
                        dcc.Graph(id="roi-plot", figure=_EMPTY,
                                  config={"displayModeBar": True},
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s"}),
                    id="view-roi", style={**_PANEL_STYLE, "visibility": "hidden"}),

                # --- Polar ---
                html.Div(
                    dcc.Loading(
                        dcc.Graph(id="polar-plot", figure=_EMPTY, responsive=False,
                                  config={"displayModeBar": True},
                                  style={"width": "100%"}),
                        type="circle", delay_show=250, delay_hide=250,
                        overlay_style={"visibility": "visible", "opacity": 0.55,
                                       "transition": "opacity .2s"}),
                    id="view-polar", style={**_PANEL_STYLE, "visibility": "hidden"}),
            ], style={"position": "relative", "flex": "1", "minHeight": "0"}),
        ], style={"flex": "1", "padding": "4px 8px", "display": "flex",
                   "flexDirection": "column", "height": "calc(100vh - 46px)"}),
    ], style={"display": "flex", "height": "calc(100vh - 46px)"}),

    # Stores
    dcc.Store(id="store-glob"),
    dcc.Store(id="viewport-store"),
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

    def s(k):
        return p[k][0] if k in p else no_update

    def lst(k):
        return p[k][0].split(",") if (k in p and p[k][0]) else no_update

    anim = (["on"] if p["anim"][0] == "1" else []) if "anim" in p else no_update
    rebase = (["on"] if p["rebase"][0] == "1" else []) if "rebase" in p else no_update
    view = p["view"][0] if p.get("view", [""])[0] in ("traj", "heat", "diag") else no_update

    vp = no_update
    if all(k in p for k in ("vbx0", "vbx1", "vby0", "vby1")):
        try:
            vp = {"xaxis": [float(p["vbx0"][0]), float(p["vbx1"][0])],
                  "yaxis": [float(p["vby0"][0]), float(p["vby1"][0])]}
        except Exception:
            vp = no_update

    return (
        s("glob"), num("vel"), num("disp"), num("trim"), num("jb"),
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
    Input("viewport-store", "data"),
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
    prevent_initial_call=True,
)
def update_url(n, vp, g, vel, disp, trim, jb, gb, pm, color, anim, rebase,
               hbin, hscale, hbound, hmetric, hcmin, hcmax, hcrange,
               fcfg, fvr, ffly, fscn, ffld, raw, ncols, pts, view):
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
    Input("btn-load", "n_clicks"),
    State("glob-input", "value"),
    State("btn-plot", "n_clicks"),
    State("heatmap-binsize", "value"),
    prevent_initial_call=True,
)
def load_data_cb(n_clicks, pattern, plot_clicks, cur_binsize):
    empty = go.Figure().update_layout(height=190, template="plotly_white")
    nope = ("No pattern.", None, [], [], [], [], [], [], "", empty, empty,
            no_update, no_update)
    if not pattern:
        return nope

    t0 = time.time()
    df, stats, metas = _load_data(pattern)
    elapsed = time.time() - t0

    if df is None or len(df) == 0:
        return (f"No data for: {pattern}", None, [], [], [], [], [], [], "",
                empty, empty, no_update, no_update)

    n_files = df["SourceFile"].nunique()
    n_segs = df["_seg_id"].nunique()
    status = f"{len(df):,} rows | {n_files} files | {n_segs} segments | {elapsed:.1f}s"

    def opts(col):
        if col not in df.columns:
            return []
        vals = sorted(df[col].unique())
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

    # Smart default bin size on a fresh load; respect any value already set
    # (e.g. restored from the URL).
    binsize_out = no_update if (cur_binsize not in (None, "")) else default_bin_size(df)

    return (
        status, pattern,
        opts("ConfigFile"), opts("VR"), opts("FlyID"), opts("SceneName"),
        opts("SourceFolder"), col_opts,
        "\n".join(meta_parts) or "No metadata",
        vel_fig, disp_fig, binsize_out,
        (plot_clicks or 0) + 1,
    )


_FILTER_CACHE: dict = {}        # signature -> (df_f, df_sub, stats_sub)
_FILTER_CACHE_ORDER: list = []
_FILTER_CACHE_MAX = 4


def _filter_signature(pattern, vel_thresh, min_disp, trim, jump_buf,
                      cfg, vrs, fids, scenes, folders, vel_selection, disp_selection):
    def rng(sel):
        return tuple(sel["range"]["x"]) if sel and sel.get("range") else None
    def lst(v):
        return tuple(sorted(v)) if v else None
    return (pattern, vel_thresh, min_disp, trim, jump_buf,
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

    df_f = apply_filters(df_sub, vel_thresh, min_disp, trim, jump_buf or 0.1)
    result = (df_f, df_sub, stats_sub)

    _FILTER_CACHE[sig] = result
    _FILTER_CACHE_ORDER.append(sig)
    if len(_FILTER_CACHE_ORDER) > _FILTER_CACHE_MAX:
        old = _FILTER_CACHE_ORDER.pop(0)
        _FILTER_CACHE.pop(old, None)
    return result


def _apply_viewport(fig, viewport, df):
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
        return span <= nat * 8.0

    if _ok(viewport.get("xaxis"), rx):
        fig.update_xaxes(range=viewport["xaxis"])
    if _ok(viewport.get("yaxis"), rz):
        fig.update_yaxes(range=viewport["yaxis"])


@app.callback(
    Output("trajectory-plot", "figure"),
    Output("heatmap-plot", "figure"),
    Output("raw-trace-plot", "figure"),
    Output("data-summary", "children"),
    Output("vel-histogram", "figure", allow_duplicate=True),
    Output("disp-histogram", "figure", allow_duplicate=True),
    Output("roi-plot", "figure", allow_duplicate=True),
    Input("btn-plot", "n_clicks"),
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
    prevent_initial_call=True,
)
def update_plots(n, pattern, vel_thresh, min_disp, trim, jump_buf,
                 group_by, pool_mode, color_by, animate, rebase, hm_binsize, hm_scale,
                 hm_bound, hm_metric, hm_cmin, hm_cmax, hm_crange, cfg, vrs, fids,
                 scenes, folders, raw_cols, ncols, max_points, vel_selection,
                 disp_selection, viewport, roi_show, roi_reach):
    empty = go.Figure().update_layout(height=400, template="plotly_white")
    if not pattern:
        return empty, empty, empty, "Load data first.", no_update, no_update, no_update

    df_f, df_sub, stats_sub = _filtered_df(
        pattern, vel_thresh, min_disp, trim, jump_buf,
        cfg, vrs, fids, scenes, folders, vel_selection, disp_selection)

    if df_sub is None:
        return empty, empty, empty, "No data.", no_update, no_update, no_update
    if len(df_sub) == 0:
        return empty, empty, empty, "All filtered out.", no_update, no_update, no_update

    # Histograms reflect the subset before velocity/disp cuts
    df, _, metas = _load_data(pattern)
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
    df_plot = rebase_to_origin(df_f) if do_rebase else df_f

    # ROIs auto-load from the scene configs. Overlay only makes sense in absolute
    # coordinates, so it's suppressed when tracks are rebased to origin; reached
    # counts are always computed on the real (non-rebased) filtered data.
    rois = rois_by_config(metas)
    want_rois = bool(roi_show) and "on" in (roi_show or []) and bool(rois)
    reach = float(roi_reach) if roi_reach else 3.0
    roi_counts = None
    roi_fig = _msg_figure("Enable 'Show target ROIs' and load Choice/BinaryChoice "
                          "data to see reached-fraction violins.")
    if want_rois:
        roi_counts = roi_config_summary(roi_reached_table(df_f, rois, reach))
        roi_fig = build_roi_violin_figure(df_f, rois, reach)

    traj_fig = build_trajectory_figure(df_plot, group_by, pool_mode, ncols=ncols_val,
                                        color_by=color_by or "individual",
                                        animate=do_animate, max_points=max_points,
                                        rois=rois, reach_radius=reach,
                                        show_rois=want_rois and not do_rebase,
                                        roi_counts=roi_counts)
    heat_fig = build_heatmap_figure(df_plot, group_by, pool_mode, ncols=ncols_val,
                                     bin_size=hm_binsize, log_scale=(hm_scale == "log"),
                                     bound_pct=hm_bound if hm_bound else 100,
                                     metric=hm_metric or "time",
                                     cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange)
    raw_fig = build_raw_trace_figure(
        df_f, raw_cols or ["GameObjectPosX", "GameObjectPosZ"], max_points=max_points)
    bt = time.time() - t0

    # Retain / restore the shared viewbox across replots and from the URL.
    for f in (traj_fig, heat_fig):
        _apply_viewport(f, viewport, df_plot)

    # Effective drawn points (post-decimation) for the summary
    n_traces = int(df_f["_seg_id"].nunique()) if len(df_f) else 0
    drawn = sum(len(t.x) for t in traj_fig.data if getattr(t, "x", None) is not None)
    budget_str = (f"{int(max_points):,}" if (max_points and max_points > 0)
                  else (f"anim {BUDGET_SVG//1000}k" if do_animate else f"{BUDGET_GL//1000}k"))

    n_segs_before = df_sub["_seg_id"].nunique()
    summary = (f"{len(df_f):,}/{len(df_sub):,} pts | "
               f"{n_traces}/{n_segs_before} segs | "
               f"drawn ~{drawn:,} ({budget_str}) | "
               f"{len(traj_fig.frames)} frames | "
               f"build {bt:.2f}s | colour: {color_by}")

    return traj_fig, heat_fig, raw_fig, summary, vel_fig, disp_fig, roi_fig


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
    Output("heatmap-plot", "figure", allow_duplicate=True),
    Input("heatmap-binsize", "value"),
    Input("heatmap-scale", "value"),
    Input("heatmap-bound", "value"),
    Input("heatmap-metric", "value"),
    Input("heatmap-cmin", "value"),
    Input("heatmap-cmax", "value"),
    Input("heatmap-crange", "value"),
    Input("view-mode", "value"),
    State("store-glob", "data"),
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
def update_heatmap_only(hm_binsize, hm_scale, hm_bound, hm_metric, hm_cmin, hm_cmax,
                        hm_crange, view, pattern, vel_thresh,
                        min_disp, trim, jump_buf, group_by, pool_mode, ncols, rebase,
                        cfg, vrs, fids, scenes, folders,
                        vel_selection, disp_selection, viewport):
    if not pattern:
        return no_update
    # If this fire came from switching views, only act when heatmap is shown.
    if ctx.triggered_id == "view-mode" and view != "heat":
        return no_update
    # Skip the rebuild on a plain tab switch when nothing relevant changed — this
    # is what stops the heatmap re-initialising (and flashing) every time you
    # open the tab. The clientside paints the existing figure on reveal.
    def _sig_of(v):
        try:
            return json.dumps(v, sort_keys=True, default=str)
        except Exception:
            return repr(v)
    sig = _sig_of([pattern, hm_binsize, hm_scale, hm_bound, hm_metric, hm_cmin,
                   hm_cmax, hm_crange, group_by, pool_mode, ncols, rebase, cfg,
                   vrs, fids, scenes, folders, vel_thresh, min_disp, trim, jump_buf,
                   vel_selection, disp_selection, viewport])
    if ctx.triggered_id == "view-mode" and sig == _LAST_HEAT_SIG["v"]:
        return no_update
    df_f, df_sub, _ = _filtered_df(
        pattern, vel_thresh, min_disp, trim, jump_buf,
        cfg, vrs, fids, scenes, folders, vel_selection, disp_selection)
    if df_sub is None or len(df_sub) == 0:
        return no_update
    ncols_val = int(ncols) if ncols and ncols >= 1 else 2
    if rebase and "on" in rebase:
        df_f = rebase_to_origin(df_f)
    heat = build_heatmap_figure(df_f, group_by, pool_mode, ncols=ncols_val,
                                bin_size=hm_binsize, log_scale=(hm_scale == "log"),
                                bound_pct=hm_bound if hm_bound else 100,
                                metric=hm_metric or "time",
                                cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange)
    _apply_viewport(heat, viewport, df_f)
    _LAST_HEAT_SIG["v"] = sig
    return heat


# Sync zoom/pan between trajectory and heatmap tabs (shared viewport)
def _extract_axis_ranges(relayout):
    """
    Return {"xaxis": [lo,hi], "yaxis": [lo,hi]} from a relayoutData payload.

    Robust to zooming on any subplot: with matched axes Plotly may report
    e.g. 'xaxis3.range[0]'. We collapse any x*/y* axis range onto the master
    xaxis/yaxis (which propagates back to all via `matches`).
    """
    if not relayout:
        return {}
    if any(k.endswith("autorange") for k in relayout) or relayout.get("autosize"):
        return {"reset": True}
    out = {}
    for key, val in relayout.items():
        m = re.match(r"^(x|y)axis\d*\.range\[(0|1)\]$", key)
        if not m:
            continue
        axis = "xaxis" if m.group(1) == "x" else "yaxis"
        idx = int(m.group(2))
        out.setdefault(axis, [None, None])[idx] = val
    # Only keep complete ranges
    return {k: v for k, v in out.items() if None not in v}


def _range_patch(ranges):
    patch = Patch()
    if ranges.get("reset"):
        patch["layout"]["xaxis"]["autorange"] = True
        patch["layout"]["yaxis"]["autorange"] = True
    else:
        if "xaxis" in ranges:
            patch["layout"]["xaxis"]["range"] = ranges["xaxis"]
        if "yaxis" in ranges:
            patch["layout"]["yaxis"]["range"] = ranges["yaxis"]
    return patch


@app.callback(
    Output("viewport-store", "data"),
    Input("trajectory-plot", "relayoutData"),
    Input("heatmap-plot", "relayoutData"),
    prevent_initial_call=True,
)
def sync_viewport(traj_relayout, heat_relayout):
    # Only RECORD the current viewbox. We deliberately do NOT patch the other
    # figure live: only one panel is visible at a time, and patching the hidden
    # heatmap forced a full re-init (newPlot) that produced zoom glitches. The
    # stored viewbox is re-applied to whichever panel is opened next.
    trigger = ctx.triggered_id
    relayout = traj_relayout if trigger == "trajectory-plot" else heat_relayout
    ranges = _extract_axis_ranges(relayout)
    if not ranges:
        return no_update
    return ranges


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
    Input("roi-reach", "value"),
    Input("roi-show", "value"),
    Input("view-mode", "value"),
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
    State("vel-histogram", "selectedData"),
    State("disp-histogram", "selectedData"),
    prevent_initial_call=True,
)
def update_roi_view(reach, roi_show, view, pattern, vel_thresh, min_disp, trim,
                    jump_buf, cfg, vrs, fids, scenes, folders, vel_sel, disp_sel):
    if not pattern:
        return no_update
    if ctx.triggered_id == "view-mode" and view != "roi":
        return no_update
    if not (roi_show and "on" in roi_show):
        return _msg_figure("Enable 'Show target ROIs + reached counts' to see "
                           "reached-fraction violins.")
    df_f, df_sub, _ = _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                                   cfg, vrs, fids, scenes, folders, vel_sel, disp_sel)
    if df_sub is None or len(df_sub) == 0:
        return no_update
    _, _, metas = _load_data(pattern)
    rois = rois_by_config(metas)
    if not rois:
        return _msg_figure("No ROI targets in these configs.")
    return build_roi_violin_figure(df_f, rois, float(reach) if reach else 3.0)


# Polar is built lazily (only when its tab is open or a polar control changes) —
# it's WebGL and heavy, and a WebGL plot created in a hidden panel won't paint,
# so we push the figure while the panel is visible.
@app.callback(
    Output("polar-plot", "figure", allow_duplicate=True),
    Input("view-mode", "value"),
    Input("polar-color", "value"),
    Input("polar-moving", "value"),
    Input("polar-walk", "value"),
    Input("roi-reach", "value"),
    Input("roi-show", "value"),
    State("store-glob", "data"),
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
def update_polar_view(view, polar_color, polar_moving, polar_walk, reach, roi_show,
                      pattern, vel_thresh, min_disp, trim, jump_buf, group_by,
                      pool_mode, ncols, max_points, rebase, cfg, vrs, fids, scenes,
                      folders, vel_sel, disp_sel):
    if not pattern or view != "polar":
        return no_update
    df_f, df_sub, _ = _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                                   cfg, vrs, fids, scenes, folders, vel_sel, disp_sel)
    if df_sub is None or len(df_sub) == 0:
        return _msg_figure("All filtered out.")
    ncols_val = int(ncols) if ncols and ncols >= 1 else 2
    if rebase and "on" in rebase:
        df_f = rebase_to_origin(df_f)
    _, _, metas = _load_data(pattern)
    rois = rois_by_config(metas)
    want_rois = bool(roi_show) and "on" in (roi_show or []) and bool(rois)
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
    "function(hfig, view){setTimeout(function(){"
    "var hc=document.getElementById('heatmap-plot');"
    "var hg=hc&&hc.querySelector('.js-plotly-plot');"
    "var panel=document.getElementById('view-heat');"
    "var vis=panel&&getComputedStyle(panel).visibility!=='hidden';"
    "var fp='';try{var L=(hfig&&hfig.layout)||{};"
    "fp=JSON.stringify((hfig&&hfig.data||[]).map(function(t){"
    "return [t.type,(t.z&&t.z.length)||0,(t.x&&t.x.length)||0,t.zmin,t.zmax];}))"
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
    "}else if(vis){try{window.Plotly.Plots.resize(hg);}catch(e){}}}"
    # Resize ONLY the graph in the currently-visible panel. Resizing a hidden
    # scaleanchor plot (or a global window 'resize') makes Plotly recompute a
    # wildly wrong aspect-locked range and fire a relayout that pollutes the
    # shared viewport -- that was the intermittent zoom-out-to-nothing glitch.
    # polar-plot is newPlot-managed (like the heatmap) and keeps its own tall
    # multi-subplot height — resizing it to the container would flatten it, so it
    # is deliberately NOT in this fit-to-container resize map.
    "var _pan={'trajectory-plot':'view-traj','vel-histogram':'view-diag',"
    "'disp-histogram':'view-diag','raw-trace-plot':'view-diag',"
    "'roi-plot':'view-roi'};"
    "Object.keys(_pan).forEach(function(id){var p=document.getElementById(_pan[id]);"
    "if(!p||getComputedStyle(p).visibility==='hidden')return;"
    "var c=document.getElementById(id);var g=c&&c.querySelector('.js-plotly-plot');"
    "if(g&&window.Plotly&&window.Plotly.Plots){try{window.Plotly.Plots.resize(g);}catch(e){}}});"
    "},90);return '';}",
    Output("anim-dummy", "children", allow_duplicate=True),
    Input("heatmap-plot", "figure"),
    Input("view-mode", "value"),
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


# Selection info for histograms
@app.callback(
    Output("vel-selection-info", "children"),
    Input("vel-histogram", "selectedData"),
    prevent_initial_call=True,
)
def vel_sel_info(sel):
    if not sel or not sel.get("range"):
        return "Drag to select velocity range"
    rng = sel["range"]["x"]
    return f"Selected: {rng[0]:.1f} – {rng[1]:.1f} (click Re-Plot to apply)"


@app.callback(
    Output("disp-selection-info", "children"),
    Input("disp-histogram", "selectedData"),
    prevent_initial_call=True,
)
def disp_sel_info(sel):
    if not sel or not sel.get("range"):
        return "Drag to select displacement range"
    rng = sel["range"]["x"]
    return f"Selected: {rng[0]:.1f} – {rng[1]:.1f} (click Re-Plot to apply)"


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
