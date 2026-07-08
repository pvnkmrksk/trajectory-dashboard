"""Vectorized trajectory filtering and per-segment statistics.

The atomic unit is `_seg_id = SourceFile + CurrentTrial + CurrentStep`, built
after numeric trial/step coercion by `trajectory_dashboard.io`. All functions
assume rows are already sorted by source file, trial, step, and time so segment
rows are contiguous. Do not regroup by `(CurrentTrial, CurrentStep)` alone.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def jump_buffer_seconds(value) -> float:
    """Normalize jump-buffer UI/URL values to seconds.

    Current controls use milliseconds, while old URLs may contain seconds
    (`0.1` for 100 ms). Values above 10 are treated as milliseconds.
    """

    if value is None or value == "":
        return 0.1
    try:
        f = float(value)
    except Exception:
        return 0.1
    return f / 1000.0 if f > 10 else f


def velocity_all(df: pd.DataFrame) -> np.ndarray:
    """Return per-row speed in raw position units per second.

    The first row of each segment is `NaN`, and velocity is never computed across
    segment boundaries.
    """

    if df is None or len(df) == 0:
        return np.array([], dtype=float)
    x = df["GameObjectPosX"].to_numpy()
    z = df["GameObjectPosZ"].to_numpy()
    t = df["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
    dx = np.empty(len(df)); dx[0] = np.nan; dx[1:] = np.diff(x)
    dz = np.empty(len(df)); dz[0] = np.nan; dz[1:] = np.diff(z)
    dt = np.empty(len(df)); dt[0] = np.nan; dt[1:] = np.diff(t)
    with np.errstate(invalid="ignore", divide="ignore"):
        speed = np.hypot(dx, dz) / dt
    seg = df["_seg_id"].to_numpy()
    starts = np.empty(len(df), bool); starts[0] = True
    starts[1:] = seg[1:] != seg[:-1]
    speed[starts] = np.nan
    speed[~np.isfinite(speed)] = np.nan
    return speed


def smoothed_velocity(
    df: pd.DataFrame,
    window: int = 10,
    spike_pct: float = 99.5,
) -> np.ndarray:
    """Speed smoothed within each segment after dropping reset spikes."""

    speed = velocity_all(df)
    finite = speed[np.isfinite(speed)]
    if finite.size:
        threshold = np.percentile(finite, spike_pct)
        speed = np.where(speed > threshold, np.nan, speed)
    series = pd.Series(speed, index=df.index)
    smoothed = (
        series.groupby(df["_seg_id"].to_numpy(), sort=False)
        .rolling(window, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )
    return smoothed.reindex(df.index).to_numpy()


def compute_tortuosity(df: pd.DataFrame, window: int = 15) -> np.ndarray:
    """Return local path tortuosity within each segment.

    A value of 1 is straight; larger values are more winding.
    """

    if df is None or len(df) == 0:
        return np.array([], dtype=float)
    x = df["GameObjectPosX"].to_numpy()
    z = df["GameObjectPosZ"].to_numpy()
    seg = df["_seg_id"].to_numpy()
    dx = np.empty(len(df)); dx[0] = 0.0; dx[1:] = np.diff(x)
    dz = np.empty(len(df)); dz[0] = 0.0; dz[1:] = np.diff(z)
    step = np.hypot(dx, dz)
    starts = np.empty(len(df), bool); starts[0] = True
    starts[1:] = seg[1:] != seg[:-1]
    step[starts] = 0.0
    step_series = pd.Series(step, index=df.index)
    path = (
        step_series.groupby(seg, sort=False)
        .rolling(window, min_periods=2)
        .sum()
        .reset_index(level=0, drop=True)
        .reindex(df.index)
        .to_numpy()
    )
    x0 = pd.Series(x, index=df.index).groupby(seg, sort=False).shift(window - 1).to_numpy()
    z0 = pd.Series(z, index=df.index).groupby(seg, sort=False).shift(window - 1).to_numpy()
    chord = np.hypot(x - x0, z - z0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = path / chord
    out[~np.isfinite(out)] = np.nan
    return np.clip(out, 1.0, None)


def compute_segment_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Compute vectorized per-segment summary statistics."""

    cols = [
        "seg_id", "n_points", "displacement", "peak_velocity",
        "median_velocity", "config", "vr", "fly_id", "scene", "source_folder",
    ]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=cols)
    speed = velocity_all(df)
    work = pd.DataFrame({
        "_seg_id": df["_seg_id"].to_numpy(),
        "x": df["GameObjectPosX"].to_numpy(),
        "z": df["GameObjectPosZ"].to_numpy(),
        "speed": speed,
    })
    grouped = work.groupby("_seg_id", sort=False)
    agg = grouped.agg(
        n_points=("x", "size"),
        x0=("x", "first"),
        z0=("z", "first"),
        x1=("x", "last"),
        z1=("z", "last"),
        peak_velocity=("speed", "max"),
        median_velocity=("speed", "median"),
    )
    agg["displacement"] = np.hypot(agg["x1"] - agg["x0"], agg["z1"] - agg["z0"])
    first = df.groupby("_seg_id", sort=False).first()
    out = pd.DataFrame({
        "seg_id": agg.index,
        "n_points": agg["n_points"].to_numpy(),
        "displacement": agg["displacement"].to_numpy(),
        "peak_velocity": agg["peak_velocity"].fillna(0).to_numpy(),
        "median_velocity": agg["median_velocity"].fillna(0).to_numpy(),
    })
    meta_cols = {
        "config": "ConfigFile",
        "vr": "VR",
        "fly_id": "FlyID",
        "scene": "SceneName",
        "source_folder": "SourceFolder",
    }
    for out_col, src_col in meta_cols.items():
        out[out_col] = first[src_col].to_numpy() if src_col in first.columns else ""
    return out[out["n_points"] >= 2].reset_index(drop=True)


def _dilate_keep(seg: np.ndarray, time_s: np.ndarray, is_jump: np.ndarray, buf: float) -> np.ndarray:
    keep = np.ones(len(seg), bool)
    if not is_jump.any():
        return keep
    breaks = np.flatnonzero(seg[1:] != seg[:-1]) + 1
    starts = np.concatenate(([0], breaks))
    ends = np.concatenate((breaks, [len(seg)]))
    for start, end in zip(starts, ends):
        jumps = is_jump[start:end]
        if not jumps.any():
            continue
        tt = time_s[start:end]
        jt = tt[jumps]
        idx = np.searchsorted(jt, tt)
        left_idx = np.clip(idx - 1, 0, len(jt) - 1)
        right_idx = np.clip(idx, 0, len(jt) - 1)
        left = np.where(idx > 0, tt - jt[left_idx], np.inf)
        right = np.where(idx < len(jt), jt[right_idx] - tt, np.inf)
        keep[start:end] = np.minimum(left, right) > buf
    return keep


def apply_filters(
    df: pd.DataFrame,
    vel_threshold,
    min_disp,
    trim_samples,
    jump_buffer=0.1,
) -> pd.DataFrame:
    """Apply the standard vectorized data-quality filters."""

    if df is None or len(df) == 0:
        return df

    if vel_threshold is not None and vel_threshold > 0:
        speed = velocity_all(df)
        jumps = np.nan_to_num(speed, nan=0.0) > float(vel_threshold)
        if jumps.any():
            seg = df["_seg_id"].to_numpy()
            time_s = df["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
            df = df[_dilate_keep(seg, time_s, jumps, float(jump_buffer))]

    if min_disp is not None and min_disp > 0 and len(df):
        grouped = df.groupby("_seg_id", sort=False)
        x0 = grouped["GameObjectPosX"].transform("first")
        z0 = grouped["GameObjectPosZ"].transform("first")
        x1 = grouped["GameObjectPosX"].transform("last")
        z1 = grouped["GameObjectPosZ"].transform("last")
        displacement = np.hypot(x1 - x0, z1 - z0)
        df = df[displacement >= float(min_disp)]

    if trim_samples is not None and trim_samples > 0 and len(df):
        grouped = df.groupby("_seg_id", sort=False)
        pos = grouped.cumcount()
        size = grouped["_seg_id"].transform("size")
        trim = int(trim_samples)
        df = df[(pos >= trim) & (pos < size - trim)]

    return df.reset_index(drop=True)


def filter_by_stat_range(df: pd.DataFrame, stats: pd.DataFrame, stat_col: str, lo, hi) -> pd.DataFrame:
    """Keep rows whose segment-level `stat_col` falls in `[lo, hi]`."""

    if stats is None or len(stats) == 0:
        return df
    keep = stats[(stats[stat_col] >= lo) & (stats[stat_col] <= hi)]["seg_id"]
    return df[df["_seg_id"].isin(keep)]
