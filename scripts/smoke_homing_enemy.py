#!/usr/bin/env python3
"""Smoke-test the reusable preprocessing path on the homing enemy dataset."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trajectory_dashboard import FilterSpec, filter_frame, find_csv_files, load_dataset


DEFAULT_GLOB = "/Users/pavan/Downloads/homing_filt/enemy/**/*_VR*.csv"


def _trial_range(lo, hi):
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return (lo, hi)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load a trajectory CSV glob and report FlyID/CurrentTrial filtering health."
    )
    parser.add_argument("glob", nargs="?", default=DEFAULT_GLOB)
    parser.add_argument("--trial-min", type=float, default=None)
    parser.add_argument("--trial-max", type=float, default=None)
    args = parser.parse_args()

    files = find_csv_files(args.glob)
    print(f"Pattern: {args.glob}")
    print(f"Matched files: {len(files)}")
    if not files:
        return 2

    dataset = load_dataset(args.glob)
    df = dataset.frame
    if df is None or len(df) == 0:
        print("No loadable trajectory rows.")
        return 3

    raw_trial_min = float(df["CurrentTrial"].min())
    raw_trial_max = float(df["CurrentTrial"].max())
    trial_min = float(df["TrialIndex"].min()) if "TrialIndex" in df else raw_trial_min
    trial_max = float(df["TrialIndex"].max()) if "TrialIndex" in df else raw_trial_max
    print(f"Rows: {len(df):,}")
    print(f"Segments: {df['_seg_id'].nunique():,}")
    print(f"CurrentTrial range: {raw_trial_min:g}..{raw_trial_max:g}")
    print(f"TrialIndex range: {trial_min:g}..{trial_max:g}")
    print(f"VRs: {df['VR'].nunique()}")
    print(f"FlyIDs: {df['FlyID'].nunique()}")

    pairs = (
        df[["SourceFile", "VR", "FlyID", "Sex"]]
        .drop_duplicates()
        .sort_values(["SourceFile", "VR", "FlyID"])
    )
    print("\nFirst VR/FlyID mappings:")
    print(pairs.head(20).to_string(index=False))

    fly_values = set(pairs["FlyID"].astype(str))
    if not fly_values or fly_values == {"unknown"}:
        print("\nERROR: FlyID did not resolve from metadata or fallback labels.")
        return 4

    trng = _trial_range(args.trial_min, args.trial_max)
    if trng:
        result = filter_frame(df, FilterSpec(trial_range=trng))
        f = result.filtered
        print("\nCurrentTrial filter:")
        print(f"Requested: {trng[0]}..{trng[1]} inclusive")
        print(f"Rows retained: {len(f):,}/{len(df):,}")
        print(f"Segments retained: {f['_seg_id'].nunique():,}/{df['_seg_id'].nunique():,}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
