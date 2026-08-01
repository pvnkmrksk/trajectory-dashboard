"""UI update scopes and responsive panel-layout policy.

This module deliberately has no Dash or Plotly imports.  It is the small,
testable contract between controls and the work they are allowed to trigger.
Keeping that contract outside the callback declarations makes accidental
all-section rebuilds visible in review and keeps layout sizing consistent
across trajectory, heatmap, direction, transition, polar, and export views.
"""

from __future__ import annotations

from enum import Enum


class UpdateScope(str, Enum):
    """The narrowest render path a control is allowed to schedule."""

    FULL = "full"
    TRAJECTORY_POLAR = "trajectory-polar"
    SPATIAL_GRID = "spatial-grid"
    DIRECTION = "direction"
    DISTRIBUTIONS = "distributions"
    DEFERRED = "deferred"
    CLIENT = "client"


# This is intentionally explicit rather than inferred from callback wiring.
# Tests compare high-risk controls with the live Dash callback map.
CONTROL_UPDATE_SCOPES: dict[str, UpdateScope] = {
    # Changes to the selected analytical rows or panel membership affect all
    # mounted sections and therefore belong to the atomic renderer.
    "vel-threshold": UpdateScope.FULL,
    "min-disp": UpdateScope.FULL,
    "trim-samples": UpdateScope.FULL,
    "jump-buffer": UpdateScope.FULL,
    "group-by": UpdateScope.FULL,
    "pool-mode": UpdateScope.FULL,
    "subplot-ncols": UpdateScope.FULL,
    "rebase-origin": UpdateScope.FULL,
    "filter-configs": UpdateScope.FULL,
    "filter-vrs": UpdateScope.FULL,
    "filter-flyids": UpdateScope.FULL,
    "filter-scenes": UpdateScope.FULL,
    "filter-folders": UpdateScope.FULL,
    "vel-range-effective": UpdateScope.FULL,
    "disp-range": UpdateScope.FULL,
    "walk-range": UpdateScope.FULL,
    "trial-range": UpdateScope.FULL,
    "step-range": UpdateScope.FULL,
    "roi-reach": UpdateScope.FULL,
    "roi-entered": UpdateScope.FULL,
    "roi-trim": UpdateScope.FULL,
    "raw-columns": UpdateScope.FULL,

    # These alter drawing payloads, not the selected analytical frame.
    "color-by": UpdateScope.TRAJECTORY_POLAR,
    "render-mode": UpdateScope.TRAJECTORY_POLAR,
    "animate-toggle": UpdateScope.TRAJECTORY_POLAR,
    "plot-points": UpdateScope.TRAJECTORY_POLAR,
    # The movement gate also blanks stationary points in the trajectory layer.
    "polar-moving": UpdateScope.TRAJECTORY_POLAR,
    "polar-walk": UpdateScope.TRAJECTORY_POLAR,

    # These change spatial aggregation geometry only.
    "heatmap-binsize": UpdateScope.SPATIAL_GRID,
    "heatmap-bound": UpdateScope.SPATIAL_GRID,

    # Direction quality and distribution encodings already have focused paths.
    "polar-angle-source": UpdateScope.DIRECTION,
    "polar-r-range": UpdateScope.DIRECTION,
    "polar-min-point-frac": UpdateScope.DIRECTION,
    "polar-min-animal-frac": UpdateScope.DIRECTION,
    # Occupancy colours restyle locally, but their abundance semantics also
    # rebuild the focused direction field from cached rows.
    "heatmap-metric": UpdateScope.DIRECTION,
    "heatmap-scale": UpdateScope.DIRECTION,
    "heatmap-cmin": UpdateScope.DIRECTION,
    "heatmap-cmax": UpdateScope.DIRECTION,
    "heatmap-color-range": UpdateScope.DIRECTION,
    "heatmap-crange": UpdateScope.DIRECTION,
    "distribution-mode": UpdateScope.DISTRIBUTIONS,
    "distribution-show-points": UpdateScope.DISTRIBUTIONS,
    "stats-unit": UpdateScope.DISTRIBUTIONS,
    "spatial-unit-scale": UpdateScope.DISTRIBUTIONS,
    "spatial-unit-label": UpdateScope.DISTRIBUTIONS,
    "custom-regions-store": UpdateScope.DISTRIBUTIONS,

    # Mounted-figure presentation and observer interactions remain local.
    "view-layout": UpdateScope.CLIENT,
    "minimal-layout-store": UpdateScope.CLIENT,
    "panel-order-store": UpdateScope.CLIENT,
    "traj-trial-fraction": UpdateScope.CLIENT,
    "btn-traj-resample": UpdateScope.CLIENT,
    "flow-max-radius": UpdateScope.CLIENT,
    # Optional expensive stages never arm the core trajectory renderer.
    "gandiva-enabled": UpdateScope.DEFERRED,
    "heading-time-enabled": UpdateScope.DEFERRED,
    "heading-time-mode": UpdateScope.DEFERRED,
    "heading-time-representation": UpdateScope.DEFERRED,
    "heading-time-window": UpdateScope.DEFERRED,
    "heading-time-variability": UpdateScope.DEFERRED,
    "heading-time-angle-bin": UpdateScope.DEFERRED,
    "roi-show": UpdateScope.DEFERRED,
    "transition-enabled": UpdateScope.DEFERRED,
    "loop-rings-store": UpdateScope.CLIENT,
}


def controls_for_scope(scope: UpdateScope) -> frozenset[str]:
    """Return the registered component ids for one update path."""

    return frozenset(
        control
        for control, registered_scope in CONTROL_UPDATE_SCOPES.items()
        if registered_scope is scope
    )


def resolve_panel_columns(
    requested: object,
    panel_count: int,
    *,
    maximum: int = 4,
) -> int:
    """Resolve an optional override or choose a compact responsive grid.

    ``requested`` accepts old persisted numeric URL values.  A blank/zero value
    means Auto.  Auto avoids a blank second column for one panel, keeps common
    2–4 panel comparisons at two columns, and prevents high-cardinality fly or
    folder groupings from becoming extremely tall two-column documents.
    """

    try:
        explicit = int(requested) if requested not in (None, "") else 0
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return max(1, min(int(maximum), explicit))

    count = max(1, int(panel_count or 1))
    if count == 1:
        return 1
    if count <= 4:
        return 2
    if count <= 9:
        return min(3, maximum)
    return min(4, maximum)


def subplot_pixel_height(nrows: int, ncols: int) -> int:
    """Return a density-aware per-row height for spatial and polar figures."""

    rows = max(1, int(nrows or 1))
    cols = max(1, int(ncols or 1))
    if cols == 1:
        return 500 if rows == 1 else 455
    if cols == 2:
        return 400 if rows <= 2 else 430
    if cols == 3:
        return 350
    return 300
