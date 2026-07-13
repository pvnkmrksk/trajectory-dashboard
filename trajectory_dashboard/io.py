"""CSV, config, and metadata loading for trajectory analysis."""

from __future__ import annotations

from dataclasses import dataclass
import glob
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .filters import compute_segment_stats


REQUIRED_COLUMNS = [
    "Current Time",
    "CurrentTrial",
    "CurrentStep",
    "GameObjectPosX",
    "GameObjectPosZ",
]


_VR_RE = re.compile(r"\bVR\s*0*([0-9]+)\b", re.IGNORECASE)
SORT_COLUMNS = ["SourceFolder", "SourceFile", "CurrentTrial", "CurrentStep", "Current Time"]


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
    """Find the nearest fly metadata file for a CSV directory."""

    root = Path(csv_dir)
    for folder in [root, *root.parents[:2]]:
        for pattern in ("*FlyMetaData.json", "*metadata.json"):
            hits = sorted(folder.glob(pattern))
            if hits:
                return str(hits[0])
    return None


def _normalise_vr(value) -> str | None:
    """Return a canonical VR label such as ``VR2`` from filenames or CSV text."""

    if value is None:
        return None
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return None
    match = _VR_RE.search(value)
    if not match:
        return None
    return f"VR{int(match.group(1))}"


def _vr_from_frame(df: pd.DataFrame) -> str | None:
    if "VR" not in df.columns:
        return None
    for value in df["VR"].dropna().astype(str).unique()[:10]:
        vr = _normalise_vr(value)
        if vr:
            return vr
    return None


def _session_label(csv_base: str) -> str:
    stem = Path(csv_base).stem.strip("_ ")
    match = re.match(r"^(\d{8}_\d{6})", stem)
    return match.group(1) if match else stem


def _fallback_fly_id(csv_base: str, vr_number: str | None) -> str:
    session = _session_label(csv_base)
    return f"{session}:{vr_number}" if vr_number else session


def _clean_id_value(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "unknown"}:
        return None
    return text


def _fly_id_from_frame(df: pd.DataFrame) -> pd.Series | None:
    """Return a FlyID-like column already present in a CSV, if any."""

    candidates = (
        "FlyID", "FlyId", "fly_id", "flyid", "Fly ID",
        "AnimalID", "AnimalId", "animal_id", "Animal ID",
    )
    for col in candidates:
        if col not in df.columns:
            continue
        ids = df[col].map(_clean_id_value)
        if ids.notna().any():
            return ids.fillna("unknown").astype(str)
    return None


def _metadata_flies(meta) -> list[dict]:
    if not isinstance(meta, dict):
        return []
    for key in ("Flies", "flies", "FlyMetaData", "fly_metadata"):
        flies = meta.get(key)
        if isinstance(flies, list):
            return [fly for fly in flies if isinstance(fly, dict)]
    return []


def _lookup_fly(meta, vr_number: str | None) -> dict | None:
    if not vr_number:
        return None
    for fly in _metadata_flies(meta):
        if _normalise_vr(fly.get("VR")) == vr_number:
            return fly
    return None


def assign_trial_index(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Add a 1-based per-file segment ordinal used for early/late trial windows.

    The frame should already be sorted so each `_seg_id` is contiguous. The
    ordinal resets for every `SourceFile`; it does not replace `_seg_id`, which
    remains the atomic segment key.
    """

    if df is None or len(df) == 0 or "_seg_id" not in df.columns:
        return df
    seg_start = df["_seg_id"].ne(df["_seg_id"].shift()).cumsum()
    if "SourceFile" in df.columns:
        first_in_file = seg_start.groupby(
            df["SourceFile"], sort=False, observed=True
        ).transform("first")
        df["TrialIndex"] = (seg_start - first_in_file + 1).astype("int32")
    else:
        df["TrialIndex"] = seg_start.astype("int32")
    return df


def _rebuild_segment_ids(df: pd.DataFrame) -> None:
    """Refresh `_seg_id` after any trial/step normalization."""

    trial_key = pd.to_numeric(df["CurrentTrial"], errors="coerce").astype("int64").astype(str)
    step_key = pd.to_numeric(df["CurrentStep"], errors="coerce").astype("int64").astype(str)
    df["_seg_id"] = df["SourceFile"].astype(str) + "_T" + trial_key + "_S" + step_key


def concatenate_restarted_trials(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Make restarted files for the same FlyID@VR continue trial numbering.

    Some sessions produce multiple CSVs for the same animal/VR with
    `CurrentTrial` restarting at 0 or 1. Segment ids stay file-qualified, but the
    visible trial number should read chronologically for colour, filtering, and
    hover. Files whose trial numbers are already increasing are left unchanged.
    """

    needed = {"FlyID", "VR", "SourceFile", "CurrentTrial", "Current Time"}
    if df is None or len(df) == 0 or not needed.issubset(df.columns):
        return df

    animal = df["FlyID"].astype(str) + "@" + df["VR"].astype(str)
    files = (
        pd.DataFrame({
            "animal": animal,
            "SourceFile": df["SourceFile"].astype(str),
            "start_time": df["Current Time"],
            "trial": pd.to_numeric(df["CurrentTrial"], errors="coerce"),
        })
        .groupby(["animal", "SourceFile"], sort=False, observed=True)
        .agg(start_time=("start_time", "min"),
             trial_min=("trial", "min"),
             trial_max=("trial", "max"))
        .reset_index()
        .sort_values(["animal", "start_time", "SourceFile"], kind="mergesort")
    )
    if files.empty:
        return df

    offsets: dict[tuple[str, str], float] = {}
    changed = False
    for _, group in files.groupby("animal", sort=False):
        running_max = None
        for row in group.itertuples(index=False):
            lo = float(row.trial_min)
            hi = float(row.trial_max)
            offset = 0.0
            if running_max is not None and lo <= running_max:
                offset = running_max - lo + 1.0
            if offset:
                offsets[(str(row.animal), str(row.SourceFile))] = offset
                changed = True
            adjusted_hi = hi + offset
            running_max = adjusted_hi if running_max is None else max(running_max, adjusted_hi)

    if not changed:
        return df

    if "OriginalCurrentTrial" not in df.columns:
        df["OriginalCurrentTrial"] = df["CurrentTrial"]
    file_key = list(zip(animal, df["SourceFile"].astype(str)))
    offset_arr = np.fromiter((offsets.get(k, 0.0) for k in file_key),
                             dtype=float, count=len(df))
    df["CurrentTrial"] = pd.to_numeric(df["CurrentTrial"], errors="coerce") + offset_arr
    _rebuild_segment_ids(df)
    return df


def is_segment_time_sorted(df: pd.DataFrame | None) -> bool:
    """Return True when each `_seg_id` is one contiguous time-sorted block."""

    if df is None or len(df) < 2 or "_seg_id" not in df.columns:
        return True
    seg = df["_seg_id"].to_numpy()
    starts = np.empty(len(df), dtype=bool)
    starts[0] = True
    starts[1:] = seg[1:] != seg[:-1]
    if int(starts.sum()) != int(df["_seg_id"].nunique()):
        return False
    t = df["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64")
    same_segment = ~starts[1:]
    return not bool(np.any(np.diff(t)[same_segment] < 0))


def sort_frame_for_segments(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure segment rows are contiguous and time-sorted, then assign TrialIndex."""

    if not is_segment_time_sorted(df):
        df.sort_values(SORT_COLUMNS, inplace=True)
    df.reset_index(drop=True, inplace=True)
    assign_trial_index(df)
    return df


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

    vr_number = _normalise_vr(csv_base) or _vr_from_frame(df)
    df["VR"] = vr_number or "unknown"

    meta_path = _find_fly_metadata(csv_dir)
    csv_fly_id = _fly_id_from_frame(df)
    fly_id = _fallback_fly_id(csv_base, vr_number)
    metadata_matched = False
    if meta_path:
        meta = loads_tolerant(Path(meta_path).read_text())
        fly = _lookup_fly(meta, vr_number)
        if fly:
            metadata_matched = True
            fly_id = str(fly.get("FlyID") or fly_id)
            df["Sex"] = fly.get("Sex", "unknown")
    if "Sex" not in df.columns:
        df["Sex"] = "unknown"
    if metadata_matched:
        df["FlyID"] = str(fly_id)
    elif csv_fly_id is not None:
        df["FlyID"] = csv_fly_id
    else:
        df["FlyID"] = str(fly_id)
    df["SourceFolder"] = os.path.basename(csv_dir)
    df["SourceFile"] = csv_base

    _rebuild_segment_ids(df)
    sort_frame_for_segments(df)

    base_keep = {
        "Current Time", "CurrentTrial", "CurrentStep", "GameObjectPosX",
        "GameObjectPosZ", "ConfigFile", "SceneName", "VR", "FlyID", "Sex",
        "SourceFolder", "SourceFile", "_seg_id", "TrialIndex",
        "OriginalCurrentTrial",
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
    concatenate_restarted_trials(df)
    sort_frame_for_segments(df)
    for col in ("ConfigFile", "SceneName", "VR", "FlyID", "Sex", "SourceFolder", "SourceFile"):
        if col in df.columns:
            df[col] = df[col].astype("category")
    return TrajectoryDataset(df, compute_segment_stats(df), metadata, pattern)
