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
) -> pd.DataFrame:
    """Return rows matching optional metadata subsets."""

    if df is None or len(df) == 0:
        return df
    mask = pd.Series(True, index=df.index)
    if configs:
        mask &= df["ConfigFile"].isin(configs)
    if vrs:
        mask &= df["VR"].isin(vrs)
    if fly_ids:
        mask &= df["FlyID"].isin(fly_ids)
    if scenes:
        mask &= df["SceneName"].isin(scenes)
    if folders:
        mask &= df["SourceFolder"].isin(folders)
    return df[mask].copy()


def filter_frame(
    df: pd.DataFrame,
    spec: FilterSpec,
    stats: pd.DataFrame | None = None,
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
    )
    if len(subset) == 0:
        return FilterResult(subset, subset, None)

    subset_stats = compute_segment_stats(subset)
    if spec.displacement_range:
        lo, hi = spec.displacement_range
        subset = filter_by_stat_range(subset, subset_stats, "displacement", lo, hi)
    if spec.velocity_range:
        lo, hi = spec.velocity_range
        subset = filter_by_stat_range(subset, subset_stats, "peak_velocity", lo, hi)
    subset_stats = compute_segment_stats(subset) if len(subset) else subset_stats

    filtered = apply_filters(
        subset,
        spec.vel_threshold,
        spec.min_displacement,
        spec.edge_trim_samples,
        jump_buffer_seconds(spec.jump_buffer_ms),
    )
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
