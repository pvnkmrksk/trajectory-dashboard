"""Reusable data pipeline for the trajectory dashboard.

The package is intentionally Dash-free. Use it to load VR trajectory CSVs,
normalize segment identity, filter/subset rows, and split data into groups for
your own plots.
"""

from .filters import (
    apply_filters,
    compute_segment_stats,
    compute_tortuosity,
    filter_by_stat_range,
    jump_buffer_seconds,
    smoothed_velocity,
    velocity_all,
)
from .grouping import (
    FilterResult,
    FilterSpec,
    group_frames,
    subset_frame,
    filter_frame,
)
from .io import (
    TrajectoryDataset,
    assign_trial_index,
    concatenate_restarted_trials,
    find_csv_files,
    load_csv_fast,
    load_dataset,
    load_folder_metadata,
)

__all__ = [
    "FilterResult",
    "FilterSpec",
    "TrajectoryDataset",
    "assign_trial_index",
    "concatenate_restarted_trials",
    "apply_filters",
    "compute_segment_stats",
    "compute_tortuosity",
    "filter_by_stat_range",
    "filter_frame",
    "find_csv_files",
    "group_frames",
    "jump_buffer_seconds",
    "load_csv_fast",
    "load_dataset",
    "load_folder_metadata",
    "smoothed_velocity",
    "subset_frame",
    "velocity_all",
]
