"""Canonical MATREX animal metadata remains compatible with legacy recordings."""

import json
import pandas as pd
from trajectory_dashboard.io import (
    load_csv_fast,
    load_folder_metadata,
    load_dataset,
    _normalise_vr,
)


def recording(path, **columns):
    data = {
        "Current Time": ["2026-10-25T12:00:00", "2026-10-25T12:00:01"],
        "CurrentTrial": [0, 0],
        "CurrentStep": [0, 0],
        "GameObjectPosX": [0.0, 1.0],
        "GameObjectPosZ": [0.0, 2.0],
    }
    data.update(columns)
    pd.DataFrame(data).to_csv(path, index=False)


def test_canonical_metadata_and_file_win_conflicts(tmp_path):
    csv = tmp_path / "run_VR1_.csv"
    recording(csv)
    canonical = {
        "Animals": [
            {
                "VR": "VR1",
                "AnimalID": "animal-7",
                "FlyID": "stale",
                "SpeciesName": "Agrotis infusa",
                "Sex": "F",
            }
        ],
        "Flies": [{"VR": "VR1", "FlyID": "stale"}],
    }
    (tmp_path / "run_AnimalMetaData.json").write_text(json.dumps(canonical))
    (tmp_path / "run_FlyMetaData.json").write_text(
        json.dumps({"Flies": [{"VR": "VR1", "FlyID": "older"}]})
    )
    frame = load_csv_fast(str(csv))
    assert set(frame.AnimalID) == set(frame.FlyID) == {"animal-7"}
    assert set(frame.SpeciesName) == {"Agrotis infusa"}
    assert set(frame.Sex) == {"F"}
    assert load_folder_metadata(str(tmp_path))["animal_metadata"] == canonical


def test_explicit_empty_animals_do_not_resurrect_legacy_alias(tmp_path):
    csv = tmp_path / "run_VR2_.csv"
    recording(csv, AnimalID=["csv-animal"] * 2)
    (tmp_path / "run_AnimalMetaData.json").write_text(
        json.dumps({"Animals": [], "Flies": [{"VR": "VR2", "FlyID": "obsolete"}]})
    )
    assert set(load_csv_fast(str(csv)).AnimalID) == {"csv-animal"}


def test_csv_canonical_id_precedes_fly_alias_and_file_segments_stay_separate(tmp_path):
    for name in ("run_VR1_.csv", "restart_VR1_.csv"):
        recording(tmp_path / name, AnimalID=["animal-2"] * 2, FlyID=["old"] * 2)
    dataset = load_dataset(str(tmp_path))
    assert set(dataset.frame.AnimalID) == set(dataset.frame.FlyID) == {"animal-2"}
    assert dataset.frame._seg_id.nunique() == 2
    assert len(dataset.stats) == 2


def test_vr_filename_delimiters_match_unity_recording_names():
    assert _normalise_vr("20261025_120000_VR1_Cube.csv") == "VR1"
    assert _normalise_vr("20261025_120000_VR002 Cube.csv") == "VR2"
    assert _normalise_vr("XVR1X") is None
