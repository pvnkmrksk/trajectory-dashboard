"""Only compatible trajectory sources may consume the retained-row quota."""

from __future__ import annotations

import csv
import gzip

import pandas as pd
import pytest

import native_dashboard.dataset as native


def _write_trajectory(path, rows=101):
    pd.DataFrame({
        "Current Time": pd.date_range("2026-01-01", periods=rows, freq="10ms"),
        "CurrentTrial": 0,
        "CurrentStep": 0,
        "GameObjectPosX": range(rows),
        "GameObjectPosZ": 0,
    }).to_csv(path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_ALL)


@pytest.mark.parametrize("budget", [150, 40])
@pytest.mark.parametrize("unsupported_suffix", [".csv", ".csv.gz"])
def test_unsupported_sources_do_not_consume_trajectory_quota(
    tmp_path, monkeypatch, budget, unsupported_suffix,
):
    valid = tmp_path / "session_VR1_.csv"
    _write_trajectory(valid)
    sensor_folder = tmp_path / "sensors"
    sensor_folder.mkdir()
    unsupported = sensor_folder / f"SimulatedData{unsupported_suffix}"
    content = "Timestamp,Name,Layer,X,Y,Z\n" + "".join(
        f"{index},{index * 7919:08x},3,1,2,3\n" for index in range(20000)
    )
    opener = gzip.open if unsupported_suffix.endswith(".gz") else open
    with opener(unsupported, "wt", encoding="utf-8", newline="") as handle:
        handle.write(content)
    assert unsupported.stat().st_size > valid.stat().st_size * 10
    # Metadata beside an unsupported sensor log must not become target input.
    (sensor_folder / "invalid-targets.json").write_text(
        '{"objects":[{"position":{"x":"invalid","z":0}}]}'
    )
    monkeypatch.setattr(native, "LOAD_ROW_BUDGET", budget)
    monkeypatch.setattr(native, "LOAD_WORKERS", 1)
    original_load = native.td_io.load_csv_fast
    parsed_paths = []

    def checked_load(path, **kwargs):
        parsed_paths.append(path)
        assert path != str(unsupported), "Unsupported bodies must not enter the CSV loader"
        return original_load(path, **kwargs)

    monkeypatch.setattr(native.td_io, "load_csv_fast", checked_load)
    baseline, _, _ = native._load_reference_dataset(str(valid))
    mixed, stats, metadata = native._load_reference_dataset(str(tmp_path))

    assert len(mixed) == len(baseline)
    assert mixed["GameObjectPosX"].tolist() == baseline["GameObjectPosX"].tolist()
    assert mixed["GameObjectPosX"].iloc[[0, -1]].tolist() == [0, 100]
    assert stats["n_points"].tolist() == [101]
    assert stats["distance_walked"].tolist() == [100]
    assert mixed.attrs["_raw_rows"] == 101
    assert mixed.attrs["_discovered_files"] == 2
    assert mixed.attrs["_unsupported_files_skipped"] == 1
    assert mixed.attrs["_duplicate_files_skipped"] == 0
    assert [item["folder"] for item in metadata] == [str(tmp_path)]
    assert parsed_paths == [str(valid), str(valid)]
    if budget > 101:
        assert len(mixed) == 101
    else:
        assert 2 < len(mixed) <= budget

    dataset = native._build_native_dataset(str(tmp_path))
    assert dataset.header["counts"]["unsupportedFilesSkipped"] == 1
    assert dataset.header["counts"]["discoveredFiles"] == 2
    assert dataset.header["counts"]["retainedRows"] == len(baseline)


def test_preflight_accepts_compressed_bom_quoted_trajectory_header(tmp_path, monkeypatch):
    source = tmp_path / "session_VR1_.csv.gz"
    _write_trajectory(source)
    monkeypatch.setattr(native, "LOAD_ROW_BUDGET", 150)

    frame, _, _ = native._load_reference_dataset(str(source))

    assert len(frame) == 101
    assert frame.attrs["_unsupported_files_skipped"] == 0


def test_only_unsupported_sources_report_required_columns(tmp_path):
    (tmp_path / "SimulatedData.csv").write_text("Timestamp,Name,Layer,X,Y,Z\n0,a,1,0,0,0\n")

    with pytest.raises(native.NativeDatasetError, match="Skipped 1 unsupported CSV files") as error:
        native._load_reference_dataset(str(tmp_path))

    assert "Current Time" in str(error.value)
    assert "GameObjectPosX" in str(error.value)


def test_distinct_recordings_with_same_basename_keep_file_scoped_segments(tmp_path, monkeypatch):
    first = tmp_path / "session-A" / "session_VR1_.csv"
    second = tmp_path / "session-B" / first.name
    first.parent.mkdir()
    second.parent.mkdir()
    _write_trajectory(first, rows=21)
    _write_trajectory(second, rows=31)
    monkeypatch.setattr(native, "LOAD_ROW_BUDGET", 1000)

    dataset = native._build_native_dataset(str(tmp_path))

    assert dataset.header["counts"]["files"] == 2
    assert dataset.header["counts"]["segments"] == 2
    assert dataset.header["counts"]["retainedRows"] == 52
    assert set(dataset.frame["SourceFile"].astype(str)) == {
        "session-A/session_VR1_.csv", "session-B/session_VR1_.csv",
    }
    assert len(set(dataset.header["segmentIds"])) == 2
    assert sorted(dataset.stats["n_points"].tolist()) == [21, 31]
    assert sorted(dataset.stats["distance_walked"].tolist()) == [20, 30]
    # Every file-qualified segment is one contiguous block, with its own
    # beginning and endpoint; overlapping timestamps cannot interleave them.
    ids = dataset.frame["_seg_id"].astype(str)
    assert int(ids.ne(ids.shift()).sum()) == 2
    assert sorted(dataset.frame.groupby("_seg_id", observed=True, sort=False).size()) == [21, 31]
