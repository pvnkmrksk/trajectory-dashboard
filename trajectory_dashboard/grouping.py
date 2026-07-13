"""Subset and grouping helpers for trajectory frames."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .filters import apply_filters, compute_segment_stats, filter_by_stat_range, jump_buffer_seconds


GROUP_BY_COLUMNS = {
    "config": "ConfigFile",
    "scene": "SceneName",
    "vr": "VR",
    "flyid": "FlyID",
    "file": "SourceFolder",
}


def _has_values(values) -> bool:
    if values is None:
        return False
    try:
        return len(values) > 0
    except TypeError:
        return bool(values)


def _normalise_trial_range(trial_range):
    if not trial_range:
        return None
    lo, hi = trial_range
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and float(lo) > float(hi):
        lo, hi = hi, lo
    return lo, hi


def _positive(value) -> bool:
    if value is None or value == "":
        return False
    try:
        return float(value) > 0
    except Exception:
        return False


@dataclass(frozen=True)
class FilterSpec:
    """Serializable filter/subset description for a trajectory frame."""

    vel_threshold: float | None = None
    min_displacement: float | None = None
    edge_trim_samples: int = 0
    jump_buffer_ms: float | None = 100
    configs: tuple[str, ...] | None = None
    vrs: tuple[str, ...] | None = None
    fly_ids: tuple[str, ...] | None = None
    scenes: tuple[str, ...] | None = None
    folders: tuple[str, ...] | None = None
    trial_range: tuple[float | None, float | None] | None = None
    velocity_range: tuple[float, float] | None = None
    displacement_range: tuple[float, float] | None = None


@dataclass(frozen=True)
class FilterResult:
    """The standard dashboard filtering outputs."""

    filtered: pd.DataFrame | None
    subset: pd.DataFrame | None
    stats: pd.DataFrame | None


def ordered_values(values, config_order: dict[str, int] | None = None, labeler=None) -> list:
    """Sort values with optional experiment/config order first."""

    vals = [str(v) for v in values]
    if not config_order:
        return sorted(vals, key=lambda v: (labeler(v).lower() if labeler else v.lower(), v))
    return sorted(
        vals,
        key=lambda v: (
            config_order.get(v, 10**9),
            labeler(v).lower() if labeler else v.lower(),
            v,
        ),
    )


def subset_frame(
    df: pd.DataFrame,
    configs=None,
    vrs=None,
    fly_ids=None,
    scenes=None,
    folders=None,
    trial_range: tuple[float | None, float | None] | None = None,
) -> pd.DataFrame:
    """Return rows matching optional metadata and trial-number subsets.

    `trial_range` is inclusive and uses the dataset's numeric `CurrentTrial`
    column.
    """

    if df is None or len(df) == 0:
        return df
    trng = _normalise_trial_range(trial_range)
    if not any(_has_values(v) for v in (configs, vrs, fly_ids, scenes, folders)) and not trng:
        return df
    mask = pd.Series(True, index=df.index)
    if _has_values(configs):
        mask &= df["ConfigFile"].isin(configs)
    if _has_values(vrs):
        mask &= df["VR"].isin(vrs)
    if _has_values(fly_ids):
        mask &= df["FlyID"].isin(fly_ids)
    if _has_values(scenes):
        mask &= df["SceneName"].isin(scenes)
    if _has_values(folders):
        mask &= df["SourceFolder"].isin(folders)
    if trng:
        lo, hi = trng
        trial = pd.to_numeric(df["CurrentTrial"], errors="coerce")
        if lo is not None:
            mask &= trial >= float(lo)
        if hi is not None:
            mask &= trial <= float(hi)
    return df[mask].copy()


def filter_frame(
    df: pd.DataFrame,
    spec: FilterSpec,
    stats: pd.DataFrame | None = None,
    compute_stats: bool = True,
) -> FilterResult:
    """Apply metadata subsets, histogram ranges, and quality filters."""

    if df is None or len(df) == 0:
        return FilterResult(None, None, None)

    subset = subset_frame(
        df,
        configs=spec.configs,
        vrs=spec.vrs,
        fly_ids=spec.fly_ids,
        scenes=spec.scenes,
        folders=spec.folders,
        trial_range=spec.trial_range,
    )
    if len(subset) == 0:
        return FilterResult(subset, subset, None)

    subset_is_original = subset is df
    subset_stats = stats if (subset_is_original and stats is not None) else None
    has_range = bool(spec.displacement_range or spec.velocity_range)
    if spec.displacement_range:
        subset_stats = subset_stats if subset_stats is not None else compute_segment_stats(subset)
        lo, hi = spec.displacement_range
        subset = filter_by_stat_range(subset, subset_stats, "displacement", lo, hi)
    if spec.velocity_range:
        subset_stats = subset_stats if subset_stats is not None else compute_segment_stats(subset)
        lo, hi = spec.velocity_range
        subset = filter_by_stat_range(subset, subset_stats, "peak_velocity", lo, hi)
    if has_range:
        subset_stats = compute_segment_stats(subset) if len(subset) else subset_stats
    elif compute_stats and subset_stats is None:
        subset_stats = compute_segment_stats(subset)
    elif not compute_stats:
        subset_stats = None

    if _positive(spec.vel_threshold) or _positive(spec.min_displacement) or _positive(spec.edge_trim_samples):
        filtered = apply_filters(
            subset,
            spec.vel_threshold,
            spec.min_displacement,
            spec.edge_trim_samples,
            jump_buffer_seconds(spec.jump_buffer_ms),
        )
    else:
        filtered = subset
    return FilterResult(filtered, subset, subset_stats)


def group_frames(
    df: pd.DataFrame,
    group_by: str = "config",
    pool_mode: str = "separate",
    *,
    config_order: dict[str, int] | None = None,
    labeler=None,
) -> dict[str, pd.DataFrame]:
    """Split a frame into plot/analysis groups.

    `group_by="config"` uses config order when provided; other groupings keep
    input order via `groupby(..., sort=False)`.
    """

    if df is None or len(df) == 0:
        return {"All Data": df}
    if pool_mode == "pooled" or group_by == "all":
        return {"All Data": df}
    column = GROUP_BY_COLUMNS.get(group_by, "ConfigFile")
    if column not in df.columns:
        return {"All Data": df}
    if column == "ConfigFile":
        values = ordered_values(pd.unique(df[column]), config_order=config_order, labeler=labeler)
        return {str(value): df[df[column] == value] for value in values}
    return {str(key): frame for key, frame in df.groupby(column, sort=False)}
