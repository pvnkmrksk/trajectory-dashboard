"""Readable bridge from a native-dashboard view to the Python pipeline.

The browser exports ``daari-deepa-view/v1`` JSON.  This module deliberately
keeps the translation small and explicit so the same subset can be inspected,
modified, and plotted with Matplotlib or another publication workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import pandas as pd

from .grouping import FilterResult, FilterSpec, filter_frame, group_frames
from .io import TrajectoryDataset, load_dataset


_FILTER_COLUMNS = {
    "config": "ConfigFile",
    "scene": "SceneName",
    "vr": "VR",
    "fly": "FlyID",
    "folder": "SourceFolder",
}


@dataclass(frozen=True)
class ViewRecipe:
    """Serializable browser view with a source and analysis state."""

    source: str
    state: Mapping[str, Any]
    filters_by_label: Mapping[str, list[str]]
    visuals: Mapping[str, Any]
    schema: str = "daari-deepa-view/v1"


@dataclass(frozen=True)
class RecipeResult:
    """Loaded data and transparent outputs corresponding to a view recipe."""

    recipe: ViewRecipe
    dataset: TrajectoryDataset
    filter_spec: FilterSpec
    filter_result: FilterResult
    groups: Mapping[str, pd.DataFrame]


def _json_param(query: Mapping[str, list[str]], key: str, default):
    try:
        return json.loads(query.get(key, [""])[0])
    except (json.JSONDecodeError, TypeError, IndexError):
        return default


def recipe_from_url(url: str) -> ViewRecipe:
    """Parse the analysis state persisted in a native-dashboard URL."""

    query = parse_qs(urlparse(str(url)).query)
    quality = _json_param(query, "quality", {})
    state = {
        "filters": _json_param(query, "filters", {}),
        "ranges": _json_param(query, "ranges", {}),
        "panelOrders": _json_param(query, "order", {}),
        "groupBy": query.get("group", ["config"])[0],
        "colorBy": query.get("color", ["categorical"])[0],
        "angleSource": query.get("angle", ["orientation"])[0],
        "mirrorPool": query.get("mirror", ["0"])[0] == "1",
        **quality,
    }
    return ViewRecipe(
        source=query.get("source", [""])[0],
        state=state,
        filters_by_label={},
        visuals={},
    )


def parse_view_recipe(value: str | Path | Mapping[str, Any]) -> ViewRecipe:
    """Read a recipe dictionary, JSON file/text, or native-dashboard URL."""

    if isinstance(value, Mapping):
        payload = dict(value)
    else:
        text = str(value)
        if text.startswith(("http://", "https://")):
            return recipe_from_url(text)
        if text.lstrip().startswith("{"):
            payload = json.loads(text)
        else:
            payload = json.loads(Path(text).read_text())
    schema = payload.get("schema", "")
    if schema != "daari-deepa-view/v1":
        raise ValueError("Expected a daari-deepa-view/v1 recipe")
    return ViewRecipe(
        source=str(payload.get("source", "")),
        state=payload.get("state") or {},
        filters_by_label=payload.get("filtersByLabel") or {},
        visuals=payload.get("visuals") or {},
        schema=schema,
    )


def _category_values(frame: pd.DataFrame, key: str) -> list[str]:
    column = _FILTER_COLUMNS[key]
    first = frame.drop_duplicates("_seg_id", keep="first")
    return [str(value) for value in pd.unique(first[column].astype("string"))]


def _selected_labels(recipe: ViewRecipe, dataset: TrajectoryDataset, key: str):
    explicit = recipe.filters_by_label.get(key)
    if explicit:
        return tuple(map(str, explicit))
    codes = recipe.state.get("filters", {}).get(key) or []
    if not codes or dataset.frame is None:
        return None
    values = _category_values(dataset.frame, key)
    return tuple(values[int(code)] for code in codes if 0 <= int(code) < len(values)) or None


def filter_spec_from_recipe(
    recipe: ViewRecipe,
    dataset: TrajectoryDataset,
) -> FilterSpec:
    """Translate browser state to the public, serializable ``FilterSpec``."""

    state = recipe.state
    ranges = state.get("ranges") or {}
    peak_range = ranges.get("peak")
    return FilterSpec(
        vel_threshold=state.get("jumpThreshold"),
        min_displacement=state.get("minDisplacement"),
        edge_trim_samples=int(state.get("edgeTrim") or 0),
        jump_buffer_ms=state.get("jumpBufferMs", 100),
        configs=_selected_labels(recipe, dataset, "config"),
        scenes=_selected_labels(recipe, dataset, "scene"),
        vrs=_selected_labels(recipe, dataset, "vr"),
        fly_ids=_selected_labels(recipe, dataset, "fly"),
        folders=_selected_labels(recipe, dataset, "folder"),
        trial_range=tuple(ranges["trial"]) if ranges.get("trial") else None,
        step_range=tuple(ranges["step"]) if ranges.get("step") else None,
        replicate_range=tuple(ranges["replicate"]) if ranges.get("replicate") else None,
        local_time_range=tuple(ranges["time"]) if ranges.get("time") else None,
        resultant_range=tuple(ranges["resultant"]) if ranges.get("resultant") else None,
        resultant_source=state.get("angleSource", "orientation"),
        velocity_range=tuple(peak_range) if peak_range else None,
        displacement_range=tuple(ranges["displacement"]) if ranges.get("displacement") else None,
        distance_walked_range=tuple(ranges["distance"]) if ranges.get("distance") else None,
    )


def load_view_recipe(value: str | Path | Mapping[str, Any]) -> RecipeResult:
    """Load and filter the source represented by a browser recipe or URL."""

    recipe = parse_view_recipe(value)
    if not recipe.source:
        raise ValueError("The view recipe does not contain a data source")
    dataset = load_dataset(recipe.source)
    if dataset.frame is None:
        raise ValueError(f"No trajectory rows matched {recipe.source!r}")
    spec = filter_spec_from_recipe(recipe, dataset)
    result = filter_frame(dataset.frame, spec, stats=dataset.stats)
    group_key = {"fly": "flyid", "folder": "file"}.get(
        recipe.state.get("groupBy"), recipe.state.get("groupBy", "config")
    )
    groups = group_frames(result.filtered, group_by=group_key)
    return RecipeResult(recipe, dataset, spec, result, groups)
