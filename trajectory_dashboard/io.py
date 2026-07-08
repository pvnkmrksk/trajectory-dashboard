"""CSV, config, and metadata loading for trajectory analysis."""

from __future__ import annotations

from dataclasses import dataclass
import glob
import json
import os
import re
from pathlib import Path

import pandas as pd

from .filters import compute_segment_stats


REQUIRED_COLUMNS = [
    "Current Time",
    "CurrentTrial",
    "CurrentStep",
    "GameObjectPosX",
    "GameObjectPosZ",
]


@dataclass(frozen=True)
class TrajectoryDataset:
    """Loaded trajectory data and folder metadata.

    Attributes:
        frame: Row-level samples sorted so each `_seg_id` is contiguous.
        stats: Per-segment summary table from `compute_segment_stats`.
        metadata: One metadata dictionary per source folder.
        pattern: The file, folder, or glob used to load the data.
    """

    frame: pd.DataFrame | None
    stats: pd.DataFrame | None
    metadata: list[dict]
    pattern: str


def find_csv_files(pattern: str) -> list[str]:
    """Resolve a file, folder, or glob into CSV file paths."""

    pattern = str(pattern or "").strip()
    if not pattern:
        return []
    if os.path.isfile(pattern):
        return [pattern]
    if os.path.isdir(pattern):
        found = sorted(glob.glob(os.path.join(pattern, "*_VR*_.csv")))
        return found or sorted(glob.glob(os.path.join(pattern, "*.csv")))
    found = sorted(glob.glob(pattern, recursive=True))
    if not found and not pattern.endswith(".csv"):
        found = sorted(glob.glob(pattern + ".csv", recursive=True))
    return [path for path in found if path.endswith(".csv") and os.path.isfile(path)]


def loads_tolerant(text: str):
    """Parse JSON with a small tolerance for Unity-style trailing commas."""

    try:
        return json.loads(text)
    except Exception:
        try:
            return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
        except Exception:
            return None


def _find_sequence_config(csv_dir: str, csv_basename: str) -> str | None:
    parts = csv_basename.split("_")
    prefixes = ["_".join(parts[:2]), "_".join(parts[:3]), parts[0]] if len(parts) >= 2 else [parts[0]]
    for prefix in prefixes:
        path = os.path.join(csv_dir, f"{prefix}_ControlScene_sequenceConfig.json")
        if os.path.exists(path):
            return path
    return None


def _find_fly_metadata(csv_dir: str) -> str | None:
    for pattern in ("*FlyMetaData.json", "*metadata.json"):
        hits = list(Path(csv_dir).glob(pattern))
        if hits:
            return str(hits[0])
    return None


def load_folder_metadata(folder: str) -> dict:
    """Load sequence order, fly metadata, and Choice config JSONs for a folder."""

    meta = {"folder": folder, "configs": {}, "sequence_order": [], "fly_metadata": None}
    for path in Path(folder).glob("*.json"):
        data = loads_tolerant(path.read_text())
        if data is None:
            continue
        if "FlyMetaData" in path.name or "metadata" in path.name.lower():
            meta["fly_metadata"] = data
        elif "sequenceConfig" in path.name:
            order = []
            for seq in data.get("sequences", []) if isinstance(data, dict) else []:
                cfg = seq.get("parameters", {}).get("configFile") if isinstance(seq, dict) else None
                if cfg and cfg not in order:
                    order.append(cfg)
            meta["sequence_order"].extend(order)
        else:
            meta["configs"][path.name] = data
    return meta


def _sequence_mapping(path: str | None) -> dict[int, str]:
    if not path:
        return {}
    data = loads_tolerant(Path(path).read_text())
    if not isinstance(data, dict):
        return {}
    mapping = {}
    for i, seq in enumerate(data.get("sequences", [])):
        cfg = seq.get("parameters", {}).get("configFile") if isinstance(seq, dict) else None
        if cfg:
            mapping[i] = cfg
    return mapping


def load_csv_fast(filepath: str) -> pd.DataFrame | None:
    """Load and normalize one trajectory CSV.

    Trial and step values are coerced before both `ConfigFile` mapping and
    `_seg_id` construction, so mixed raw text such as `0` and `0.0` remains one
    segment. The returned frame keeps required columns, metadata columns, and
    additional numeric sensor columns.
    """

    try:
        df = pd.read_csv(filepath, parse_dates=["Current Time"])
    except Exception:
        return None
    if not all(col in df.columns for col in REQUIRED_COLUMNS):
        return None

    for col in ["CurrentTrial", "CurrentStep", "GameObjectPosX", "GameObjectPosZ"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=REQUIRED_COLUMNS, inplace=True)
    if len(df) == 0:
        return None

    csv_dir = os.path.dirname(filepath)
    csv_base = os.path.basename(filepath)

    if "ConfigFile" not in df.columns:
        mapping = _sequence_mapping(_find_sequence_config(csv_dir, csv_base))
        if mapping:
            step_key = df["CurrentStep"].astype("int64")
            df["ConfigFile"] = step_key.map(mapping).fillna("unknown")
        else:
            df["ConfigFile"] = "unknown"

    if "SceneName" not in df.columns:
        df["SceneName"] = df.get("Scene", "unknown")

    vr_number = None
    if "_VR" in csv_base:
        try:
            vr_number = f"VR{csv_base.split('_VR')[1].split('_')[0].rstrip('.')}"
        except Exception:
            pass
    df["VR"] = vr_number or (df["VR"] if "VR" in df.columns else "unknown")

    meta_path = _find_fly_metadata(csv_dir)
    fly_id = "unknown"
    if meta_path and vr_number:
        meta = loads_tolerant(Path(meta_path).read_text())
        if isinstance(meta, dict):
            fly = next((f for f in meta.get("Flies", []) if f.get("VR") == vr_number), None)
            if fly:
                fly_id = fly.get("FlyID", "unknown")
                df["Sex"] = fly.get("Sex", "unknown")
    if "Sex" not in df.columns:
        df["Sex"] = "unknown"
    df["FlyID"] = str(fly_id)
    df["SourceFolder"] = os.path.basename(csv_dir)
    df["SourceFile"] = csv_base

    trial_key = df["CurrentTrial"].astype("int64").astype(str)
    step_key = df["CurrentStep"].astype("int64").astype(str)
    df["_seg_id"] = df["SourceFile"] + "_T" + trial_key + "_S" + step_key

    base_keep = {
        "Current Time", "CurrentTrial", "CurrentStep", "GameObjectPosX",
        "GameObjectPosZ", "ConfigFile", "SceneName", "VR", "FlyID", "Sex",
        "SourceFolder", "SourceFile", "_seg_id",
    }
    numeric_keep = {
        col for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col])
        and col not in base_keep
        and not df[col].isna().all()
    }
    df = df[[col for col in df.columns if col in (base_keep | numeric_keep)]]
    for col in ("ConfigFile", "SceneName", "VR", "FlyID", "Sex", "SourceFolder", "SourceFile"):
        if col in df.columns:
            df[col] = df[col].astype("category")
    return df


def load_dataset(pattern: str) -> TrajectoryDataset:
    """Load all CSVs matching `pattern` into a sorted `TrajectoryDataset`."""

    files = find_csv_files(pattern)
    if not files:
        return TrajectoryDataset(None, None, [], pattern)

    frames = []
    metadata = []
    seen_folders = set()
    for path in files:
        frame = load_csv_fast(path)
        if frame is not None:
            frames.append(frame)
        folder = os.path.dirname(path)
        if folder not in seen_folders:
            seen_folders.add(folder)
            metadata.append(load_folder_metadata(folder))

    if not frames:
        return TrajectoryDataset(None, None, metadata, pattern)

    df = pd.concat(frames, ignore_index=True)
    df.sort_values(
        ["SourceFolder", "SourceFile", "CurrentTrial", "CurrentStep", "Current Time"],
        inplace=True,
    )
    df.reset_index(drop=True, inplace=True)
    for col in ("ConfigFile", "SceneName", "VR", "FlyID", "Sex", "SourceFolder", "SourceFile"):
        if col in df.columns:
            df[col] = df[col].astype("category")
    return TrajectoryDataset(df, compute_segment_stats(df), metadata, pattern)
