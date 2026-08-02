"""Framework-neutral binary bridge for the browser-native dashboard.

The current Python loader is intentionally reused as the analytical reference.
This module only dictionary-encodes its retained frame and exact segment table;
it does not construct a Plotly figure or a Dash callback payload.
"""

from __future__ import annotations

from collections import OrderedDict
import concurrent.futures
from dataclasses import dataclass
import hashlib
import json
import math
import os
import struct
import threading
from typing import Any

import numpy as np
import pandas as pd

from trajectory_dashboard import io as td_io
from trajectory_dashboard.filters import (
    compute_segment_stats,
    compute_tortuosity,
    smoothed_velocity,
    velocity_all,
)
from trajectory_dashboard.roi import rois_by_config


FORMAT_NAME = "trajectory-native-columns"
FORMAT_VERSION = 1
_CACHE_MAX = 2
_CACHE: "OrderedDict[str, NativeDataset]" = OrderedDict()
_CACHE_LOOKUP: dict[tuple, str] = {}
_CACHE_LOCK = threading.RLock()
LOAD_ROW_BUDGET = max(0, int(os.environ.get("TRAJ_LOAD_ROW_BUDGET", "2000000")))
LOAD_WORKERS = min(8, max(1, int(os.environ.get("TRAJ_LOAD_WORKERS", "2"))))
_DUPLICATE_DIGEST_BYTES = 128 * 1024


@dataclass
class NativeDataset:
    """One loaded reference frame plus its browser binary representation."""

    dataset_id: str
    pattern: str
    frame: pd.DataFrame
    stats: pd.DataFrame
    metadata: list[dict]
    header: dict[str, Any]
    binary: bytes


class NativeDatasetError(RuntimeError):
    """Raised when a requested source cannot form a trajectory dataset."""


def _sample_file_digest(path: str) -> bytes:
    """Hash bounded start/end samples for same-name, same-size candidates."""

    size = os.path.getsize(path)
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(size).encode("ascii"))
    with open(path, "rb") as handle:
        digest.update(handle.read(_DUPLICATE_DIGEST_BYTES))
        if size > _DUPLICATE_DIGEST_BYTES:
            handle.seek(max(0, size - _DUPLICATE_DIGEST_BYTES))
            digest.update(handle.read(_DUPLICATE_DIGEST_BYTES))
    return digest.digest()


def _metadata_score(path: str) -> tuple[int, int]:
    folder = os.path.dirname(path)
    try:
        json_count = sum(
            name.lower().endswith(".json") for name in os.listdir(folder)
        )
    except OSError:
        json_count = 0
    return json_count, len(os.path.normpath(folder).split(os.sep))


def _deduplicate_source_files(files: list[str]) -> tuple[list[str], list[str]]:
    """Drop confirmed duplicate copies while retaining metadata-rich paths.

    Candidates must share basename, byte size, and nanosecond modification
    time, then match a bounded digest of both file ends. Distinctly named or
    independently written recordings are never merged.
    """

    groups: dict[tuple[str, int, int], list[str]] = {}
    for path in files:
        stat = os.stat(path)
        groups.setdefault(
            (os.path.basename(path), stat.st_size, stat.st_mtime_ns), []
        ).append(path)
    kept: list[str] = []
    skipped: list[str] = []
    for paths in groups.values():
        if len(paths) == 1:
            kept.append(paths[0])
            continue
        by_digest: dict[bytes, list[str]] = {}
        for path in paths:
            by_digest.setdefault(_sample_file_digest(path), []).append(path)
        for copies in by_digest.values():
            preferred = max(copies, key=_metadata_score)
            kept.append(preferred)
            skipped.extend(path for path in copies if path != preferred)
    return sorted(kept), sorted(skipped)


def _finite_float(values, *, fill=np.nan) -> np.ndarray:
    arr = pd.to_numeric(values, errors="coerce").to_numpy(dtype=np.float32)
    if not math.isnan(fill):
        arr[~np.isfinite(arr)] = np.float32(fill)
    return arr


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return value


def _encode_labels(values: pd.Series | np.ndarray) -> tuple[np.ndarray, list[str]]:
    text = pd.Series(values, copy=False).astype("string").fillna("unknown")
    codes, labels = pd.factorize(text, sort=False)
    labels_out = [str(value) for value in labels.tolist()]
    if len(labels_out) <= np.iinfo(np.uint16).max:
        return codes.astype(np.uint16, copy=False), labels_out
    return codes.astype(np.uint32, copy=False), labels_out


def _segment_boundaries(segment: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    starts = np.concatenate((np.array([0], dtype=np.int64), np.flatnonzero(
        segment[1:] != segment[:-1]
    ).astype(np.int64) + 1))
    ends = np.concatenate((starts[1:], np.array([len(segment)], dtype=np.int64)))
    return starts, ends


def _local_time_seconds(frame: pd.DataFrame, starts: np.ndarray,
                        ends: np.ndarray) -> np.ndarray:
    time_ns = frame["Current Time"].astype(
        "int64", copy=False
    ).to_numpy(dtype=np.int64)
    base = np.repeat(time_ns[starts], ends - starts)
    return ((time_ns - base) / 1e9).astype(np.float32)


def _movement_heading(x: np.ndarray, z: np.ndarray,
                       starts: np.ndarray) -> np.ndarray:
    dx = np.empty(len(x), dtype=np.float32)
    dz = np.empty(len(z), dtype=np.float32)
    dx[0] = np.nan
    dz[0] = np.nan
    dx[1:] = np.diff(x)
    dz[1:] = np.diff(z)
    out = np.degrees(np.arctan2(dx, dz)).astype(np.float32)
    out[starts] = np.nan
    return out


def _stats_by_segment(stats: pd.DataFrame, segment_ids: list[str],
                      column: str, default: float) -> np.ndarray:
    if stats is None or len(stats) == 0 or column not in stats:
        return np.full(len(segment_ids), default, dtype=np.float32)
    lookup = stats.assign(seg_id=stats["seg_id"].astype(str)).drop_duplicates(
        "seg_id", keep="last"
    ).set_index("seg_id")[column]
    values = pd.to_numeric(lookup.reindex(segment_ids), errors="coerce").to_numpy(
        dtype=np.float32
    )
    values[~np.isfinite(values)] = np.float32(default)
    return values


def _range(values: np.ndarray, default=(0.0, 0.0)) -> list[float]:
    finite = np.asarray(values)[np.isfinite(values)]
    if finite.size == 0:
        return [float(default[0]), float(default[1])]
    return [float(np.min(finite)), float(np.max(finite))]


def _pack_arrays(meta: dict[str, Any], arrays: dict[str, np.ndarray]) -> bytes:
    descriptors: dict[str, dict[str, Any]] = {}
    chunks: list[bytes] = []
    offset = 0
    for name, source in arrays.items():
        arr = np.ascontiguousarray(source)
        if arr.dtype.byteorder == ">" or (
            arr.dtype.byteorder == "=" and not np.little_endian
        ):
            arr = arr.byteswap().newbyteorder("<")
        padding = (-offset) % max(1, min(8, arr.dtype.itemsize))
        if padding:
            chunks.append(b"\0" * padding)
            offset += padding
        descriptors[name] = {
            "dtype": arr.dtype.str,
            "offset": offset,
            "length": int(arr.size),
            "bytes": int(arr.nbytes),
        }
        raw = arr.tobytes(order="C")
        chunks.append(raw)
        offset += len(raw)
    header = dict(meta)
    header.update({
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "arrays": descriptors,
        "bodyBytes": offset,
        "bodyPadding": 0,
    })
    # TypedArray byte offsets must be naturally aligned. The JSON header has a
    # variable length, so include a short explicit pad before the aligned body.
    # Re-encode until the declared padding agrees with the final header length.
    encoded = b""
    for _ in range(4):
        encoded = json.dumps(
            _json_safe(header), separators=(",", ":")
        ).encode("utf-8")
        padding = (-(4 + len(encoded))) % 8
        if header["bodyPadding"] == padding:
            break
        header["bodyPadding"] = padding
    encoded = json.dumps(_json_safe(header), separators=(",", ":")).encode("utf-8")
    padding = (-(4 + len(encoded))) % 8
    if padding != header["bodyPadding"]:
        header["bodyPadding"] = padding
        encoded = json.dumps(_json_safe(header), separators=(",", ":")).encode("utf-8")
        padding = (-(4 + len(encoded))) % 8
    return (
        struct.pack("<I", len(encoded)) + encoded + b"\0" * padding
        + b"".join(chunks)
    )


_CONFIG_LABELS = {
    "Choice_00.json": "Blank",
    "Choice_All.json": "All (Push + Pull + Shear)",
    "Choice_Push.json": "Push",
    "Choice_Pull.json": "Pull",
    "Choice_Shear.json": "Shear",
    "Choice_empty.json": "Empty",
    "Choice_Empty_Empty.json": "Empty vs Empty",
}


def _display_config(raw: str) -> str:
    """Return a useful default label; the browser can still edit it live."""

    text = str(raw or "unknown")
    if text in _CONFIG_LABELS:
        return _CONFIG_LABELS[text]
    stem = os.path.splitext(text)[0]
    stem = stem.removeprefix("Choice_").removeprefix("BinaryChoice_")
    words = [part for part in stem.replace("-", "_").split("_") if part]
    if not words:
        return text
    replacements = {
        "noflip": "no flip",
        "subnoflip": "near / no flip",
        "subflip": "near / flip",
        "bigfarnoflip": "far / no flip",
        "bigfarflip": "far / flip",
        "uniBG": "uniform background",
    }
    return " · ".join(
        replacements.get(word, word.replace("noflip", "no flip"))
        for word in words
    )


def _histogram_payload(values: np.ndarray, bins: int = 48) -> dict[str, Any]:
    """Small bounded histogram for data-guided browser range controls."""

    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return {
            "edges": [0.0, 1.0], "counts": [0], "range": [0.0, 1.0],
            "displayRange": [0.0, 1.0], "overflow": 0,
        }
    lo, hi = float(np.min(finite)), float(np.max(finite))
    display_hi = float(np.percentile(finite, 99.5))
    if not math.isfinite(display_hi) or display_hi <= lo:
        display_hi = hi if hi > lo else lo + 1.0
    counts, edges = np.histogram(
        finite, bins=max(8, int(bins)), range=(lo, display_hi)
    )
    return {
        "edges": edges.astype(float).tolist(),
        "counts": counts.astype(int).tolist(),
        "range": [lo, hi],
        "displayRange": [lo, display_hi],
        "overflow": int(np.count_nonzero(finite > display_hi)),
    }


def _segment_endpoint_keep(segment_ids, max_points: int | None) -> np.ndarray:
    segment = np.asarray(segment_ids)
    n = len(segment)
    if n == 0:
        return np.zeros(0, dtype=bool)
    if not max_points or max_points <= 0 or n <= int(max_points):
        return np.ones(n, dtype=bool)
    starts = np.concatenate(([0], np.flatnonzero(segment[1:] != segment[:-1]) + 1))
    lengths = np.diff(np.concatenate((starts, [n])))
    points = max(2, int(max_points) // max(1, len(starts)))
    positions = np.arange(n) - np.repeat(starts, lengths)
    segment_lengths = np.repeat(lengths, lengths)
    steps = np.repeat(
        np.maximum(1, np.ceil((lengths - 1) / max(1, points - 1)).astype(int)),
        lengths,
    )
    return (
        (positions == 0)
        | (positions == segment_lengths - 1)
        | ((positions % steps) == 0)
    )


def _load_reference_dataset(pattern: str):
    """Bounded parallel load using only the reusable analysis package."""

    discovered_files = td_io.find_csv_files(pattern)
    if not discovered_files:
        raise NativeDatasetError("No trajectory CSV files matched this source.")
    files, duplicate_files = _deduplicate_source_files(discovered_files)
    total_bytes = sum(max(1, os.path.getsize(path)) for path in files)

    def load_one(item):
        index, path = item
        frame = td_io.load_csv_fast(path, include_numeric=False)
        if frame is None:
            return index, path, None, None, 0
        raw_rows = len(frame)
        raw_speed = velocity_all(frame)
        speed = smoothed_velocity(frame, window=10, raw_speed=raw_speed)
        tortuosity = compute_tortuosity(frame, window=15)
        stats = compute_segment_stats(
            frame, speed=speed, tortuosity=tortuosity,
        )
        quota = None
        if LOAD_ROW_BUDGET > 0:
            quota = max(
                2,
                int(round(
                    LOAD_ROW_BUDGET * max(1, os.path.getsize(path)) / total_bytes
                )),
            )
        frame["_smoothed_velocity"] = np.asarray(speed, dtype=np.float32)
        frame["_raw_velocity"] = np.asarray(raw_speed, dtype=np.float32)
        frame["_local_tortuosity"] = np.asarray(tortuosity, dtype=np.float32)
        if quota and len(frame) > quota:
            frame = frame.loc[
                _segment_endpoint_keep(frame["_seg_id"].to_numpy(), quota)
            ].copy()
        else:
            frame = frame.copy()
        for column in frame.select_dtypes(include=["float64"]).columns:
            frame[column] = pd.to_numeric(frame[column], downcast="float")
        return index, path, frame, stats, raw_rows

    futures = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(LOAD_WORKERS, len(files)),
        thread_name_prefix="native-load",
    ) as pool:
        futures = [pool.submit(load_one, item) for item in enumerate(files)]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]
    results.sort(key=lambda item: item[0])
    frames = [item[2] for item in results if item[2] is not None]
    stat_parts = [item[3] for item in results if item[3] is not None]
    if not frames:
        raise NativeDatasetError("The matched files contained no valid trajectory rows.")

    frame = pd.concat(frames, ignore_index=True)
    old_ids = frame.drop_duplicates("_seg_id")["_seg_id"].astype(str).tolist()
    td_io.concatenate_restarted_trials(frame)
    new_ids = frame.drop_duplicates("_seg_id")["_seg_id"].astype(str).tolist()
    segment_id_map = dict(zip(old_ids, new_ids))
    td_io.sort_frame_for_segments(frame)
    stats = pd.concat(stat_parts, ignore_index=True)
    if len(stats) and segment_id_map:
        current = stats["seg_id"].astype(str)
        stats["seg_id"] = current.map(segment_id_map).fillna(current)
    metadata = []
    seen = set()
    for path in files:
        folder = os.path.dirname(path)
        if folder not in seen:
            seen.add(folder)
            metadata.append(td_io.load_folder_metadata(folder))
    for column in (
        "ConfigFile", "SceneName", "VR", "FlyID", "Sex",
        "SourceFolder", "SourceFile", "_seg_id",
    ):
        if column in frame:
            frame[column] = frame[column].astype("category")
    frame.attrs["_raw_rows"] = int(sum(item[4] for item in results))
    frame.attrs["_discovered_files"] = len(discovered_files)
    frame.attrs["_duplicate_files_skipped"] = len(duplicate_files)
    frame.attrs["_frame_token"] = (
        pattern,
        tuple(
            (os.path.abspath(path), os.path.getmtime(path), os.path.getsize(path))
            for path in files
        ),
        LOAD_ROW_BUDGET,
    )
    return frame, stats, metadata


def _build_native_dataset(pattern: str) -> NativeDataset:
    frame, stats, metadata = _load_reference_dataset(pattern)
    if frame is None or len(frame) == 0:
        raise NativeDatasetError("No valid trajectory rows matched this source.")
    stats = stats if stats is not None else pd.DataFrame()

    seg_text = frame["_seg_id"].astype("string")
    segment, segment_labels = pd.factorize(seg_text, sort=False)
    segment = segment.astype(np.uint32, copy=False)
    starts, ends = _segment_boundaries(segment)
    if len(starts) != len(segment_labels):
        raise NativeDatasetError(
            "Loaded segment rows are not contiguous; refusing an unsafe browser payload."
        )

    x = _finite_float(frame["GameObjectPosX"])
    z = _finite_float(frame["GameObjectPosZ"])
    local_time = _local_time_seconds(frame, starts, ends)
    smooth_speed = _finite_float(frame["_smoothed_velocity"])
    raw_speed = _finite_float(frame["_raw_velocity"])
    movement = _movement_heading(x, z, starts)
    if "GameObjectRotY" in frame:
        orientation = _finite_float(frame["GameObjectRotY"])
        orientation = ((orientation + 180.0) % 360.0 - 180.0).astype(np.float32)
    else:
        orientation = movement.copy()
    local_tortuosity = _finite_float(frame["_local_tortuosity"])

    first = frame.iloc[starts]
    category_columns = {
        "config": "ConfigFile",
        "scene": "SceneName",
        "vr": "VR",
        "fly": "FlyID",
        "folder": "SourceFolder",
        "file": "SourceFile",
    }
    category_labels: dict[str, list[str]] = {}
    category_codes: dict[str, np.ndarray] = {}
    for key, column in category_columns.items():
        values = first[column] if column in first else pd.Series(
            ["unknown"] * len(starts)
        )
        codes, labels = _encode_labels(values)
        category_codes[key] = codes
        category_labels[key] = labels

    # Fly IDs can repeat across VR arenas.  The UI's per-animal visibility
    # control therefore uses the same FlyID@VR identity as the dataset summary,
    # while the existing FlyID filter remains available for parity.
    animal_values = (
        first.get("FlyID", pd.Series(["unknown"] * len(starts))).astype("string")
        + "@"
        + first.get("VR", pd.Series(["unknown"] * len(starts))).astype("string")
    )
    animal_codes, animal_labels = _encode_labels(animal_values)
    category_codes["animal"] = animal_codes
    category_labels["animal"] = animal_labels

    segment_ids = [str(value) for value in segment_labels.tolist()]
    segment_trial = _finite_float(first["CurrentTrial"], fill=0.0)
    segment_step = _finite_float(first["CurrentStep"], fill=0.0)
    arrays: dict[str, np.ndarray] = {
        "x": x,
        "z": z,
        "time": local_time,
        "speed": smooth_speed,
        "rawSpeed": raw_speed,
        "orientation": orientation,
        "movement": movement,
        "tortuosity": local_tortuosity,
        "segment": segment,
        "segmentTrial": segment_trial,
        "segmentStep": segment_step,
        "segmentPoints": _stats_by_segment(stats, segment_ids, "n_points", 0).astype(
            np.uint32
        ),
        "segmentDistance": _stats_by_segment(
            stats, segment_ids, "distance_walked", 0.0
        ),
        "segmentDisplacement": _stats_by_segment(
            stats, segment_ids, "displacement", 0.0
        ),
        "segmentPeakSpeed": _stats_by_segment(
            stats, segment_ids, "peak_velocity", 0.0
        ),
        "segmentMedianSpeed": _stats_by_segment(
            stats, segment_ids, "median_velocity", 0.0
        ),
        "segmentTortuosity": _stats_by_segment(
            stats, segment_ids, "median_local_tortuosity", 1.0
        ),
        "segmentDuration": np.maximum(0, local_time[ends - 1]).astype(
            np.float32, copy=False
        ),
    }
    for key, codes in category_codes.items():
        arrays[f"segment{key.title()}"] = codes

    token = repr(frame.attrs.get("_frame_token", (pattern, len(frame))))
    dataset_id = hashlib.sha1(
        f"{pattern}|{token}|{len(frame)}|{len(starts)}".encode("utf-8")
    ).hexdigest()[:16]
    rois = rois_by_config(metadata)
    segment_durations = arrays["segmentDuration"]
    finite_durations = segment_durations[np.isfinite(segment_durations)]
    duration_quantiles = {
        "median": float(np.percentile(finite_durations, 50)) if finite_durations.size else 0.0,
        "p95": float(np.percentile(finite_durations, 95)) if finite_durations.size else 0.0,
        "p99": float(np.percentile(finite_durations, 99)) if finite_durations.size else 0.0,
        "max": float(np.max(finite_durations)) if finite_durations.size else 0.0,
    }
    playback_max = duration_quantiles["p95"]
    display_categories = {
        key: ([_display_config(value) for value in labels]
              if key == "config" else list(labels))
        for key, labels in category_labels.items()
    }
    meta = {
        "datasetId": dataset_id,
        "pattern": pattern,
        "counts": {
            "files": int(frame["SourceFile"].nunique()),
            "discoveredFiles": int(frame.attrs.get("_discovered_files", frame["SourceFile"].nunique())),
            "duplicateFilesSkipped": int(frame.attrs.get("_duplicate_files_skipped", 0)),
            "sourceRows": int(frame.attrs.get("_raw_rows", len(frame))),
            "retainedRows": int(len(frame)),
            "segments": int(len(starts)),
            "animals": int((
                frame["FlyID"].astype(str) + "@" + frame["VR"].astype(str)
            ).nunique()),
        },
        "ranges": {
            "x": _range(x),
            "z": _range(z),
            "time": _range(local_time),
            "speed": _range(smooth_speed, (0.0, 1.0)),
            "rawSpeed": _range(raw_speed, (0.0, 1.0)),
            "trial": _range(segment_trial),
            "step": _range(segment_step),
            "peakSpeed": _range(arrays["segmentPeakSpeed"], (0.0, 1.0)),
            "displacement": _range(arrays["segmentDisplacement"], (0.0, 1.0)),
            "distance": _range(arrays["segmentDistance"], (0.0, 1.0)),
        },
        "playbackMax": playback_max,
        "playbackQuantiles": duration_quantiles,
        "categories": category_labels,
        "displayCategories": display_categories,
        "segmentIds": segment_ids,
        "filterHistograms": {
            "trial": _histogram_payload(segment_trial),
            "step": _histogram_payload(segment_step),
            "peak": _histogram_payload(arrays["segmentPeakSpeed"]),
            "displacement": _histogram_payload(arrays["segmentDisplacement"]),
            "distance": _histogram_payload(arrays["segmentDistance"]),
        },
        "rois": _json_safe(rois),
    }
    binary = _pack_arrays(meta, arrays)
    return NativeDataset(
        dataset_id=dataset_id,
        pattern=pattern,
        frame=frame,
        stats=stats,
        metadata=metadata,
        header=meta,
        binary=binary,
    )


def load_native_dataset(pattern: str) -> NativeDataset:
    pattern = str(pattern or "").strip()
    if not pattern:
        raise NativeDatasetError("Enter a CSV file, folder, or recursive glob.")
    files, _ = _deduplicate_source_files(td_io.find_csv_files(pattern))
    cache_key = (
        pattern,
        tuple(
            (os.path.abspath(path), os.path.getmtime(path), os.path.getsize(path))
            for path in files
        ),
        LOAD_ROW_BUDGET,
    )
    with _CACHE_LOCK:
        cached_id = _CACHE_LOOKUP.get(cache_key)
        cached = _CACHE.get(cached_id) if cached_id else None
        if cached is not None:
            _CACHE.move_to_end(cached.dataset_id)
            return cached
    dataset = _build_native_dataset(pattern)
    with _CACHE_LOCK:
        _CACHE.pop(dataset.dataset_id, None)
        _CACHE[dataset.dataset_id] = dataset
        _CACHE_LOOKUP[cache_key] = dataset.dataset_id
        while len(_CACHE) > _CACHE_MAX:
            evicted_id, _ = _CACHE.popitem(last=False)
            for key, value in list(_CACHE_LOOKUP.items()):
                if value == evicted_id:
                    _CACHE_LOOKUP.pop(key, None)
    return dataset


def get_native_dataset(dataset_id: str) -> NativeDataset | None:
    with _CACHE_LOCK:
        dataset = _CACHE.get(str(dataset_id))
        if dataset is not None:
            _CACHE.move_to_end(str(dataset_id))
        return dataset
