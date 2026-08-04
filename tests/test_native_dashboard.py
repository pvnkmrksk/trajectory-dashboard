from __future__ import annotations

import json
import shutil
import struct
import warnings

import numpy as np
import pandas as pd

from native_dashboard.dataset import (
    FORMAT_NAME, _config_presentation, load_native_dataset,
)
from native_dashboard.server import create_native_app
from trajectory_dashboard.recipe import load_view_recipe, recipe_from_url
from trajectory_dashboard.roi import roi_xz, rois_by_config, rois_from_config


def _write_csv(path, source_offset=0.0):
    rows = []
    for trial_text in ("0", "0.0"):
        for sample in range(3):
            rows.append({
                "Current Time": f"2026-01-01T00:00:0{sample + int(source_offset)}",
                "CurrentTrial": trial_text,
                "CurrentStep": "0.0",
                "GameObjectPosX": source_offset + sample,
                "GameObjectPosZ": sample * 0.5,
                "GameObjectRotY": sample * 10,
                "ConfigFile": "Choice_Test.json",
                "Scene": "Choice",
                "FlyID": f"fly-{int(source_offset)}",
                "SensorValue": sample * 2,
            })
    pd.DataFrame(rows).to_csv(path, index=False)


def _header(payload: bytes):
    header_length = struct.unpack_from("<I", payload, 0)[0]
    header = json.loads(payload[4:4 + header_length])
    body_offset = 4 + header_length + header["bodyPadding"]
    return header, body_offset


def _array(payload: bytes, header: dict, body_offset: int, name: str):
    descriptor = header["arrays"][name]
    dtype = np.dtype(descriptor["dtype"])
    return np.frombuffer(
        payload, dtype=dtype, count=descriptor["length"],
        offset=body_offset + descriptor["offset"],
    )


def test_native_binary_is_aligned_and_file_scoped(tmp_path):
    _write_csv(tmp_path / "session_VR1_.csv", 0)
    _write_csv(tmp_path / "restart_VR1_.csv", 4)

    dataset = load_native_dataset(str(tmp_path))
    assert load_native_dataset(str(tmp_path)) is dataset
    header, body_offset = _header(dataset.binary)

    assert header["format"] == FORMAT_NAME
    assert body_offset % 8 == 0
    assert header["counts"]["files"] == 2
    # Mixed 0/0.0 text is one segment within each source file, while the two
    # source files remain distinct even though trial and step are reused.
    assert header["counts"]["segments"] == 2
    assert len(set(header["segmentIds"])) == 2
    assert header["playbackMax"] == 2.0
    assert header["playbackQuantiles"] == {
        "median": 2.0, "p95": 2.0, "p99": 2.0, "max": 2.0,
    }
    assert sorted(header["categories"]["animal"]) == ["fly-0@unknown", "fly-4@unknown"]
    assert "rawColumns" not in header
    assert header["arrays"]["segmentDuration"]["length"] == 2
    assert set(header["filterHistograms"]) == {
        "trial", "step", "replicate", "time", "resultant",
        "peak", "displacement", "distance",
    }
    assert header["arrays"]["segmentReplicate"]["length"] == 2
    assert header["arrays"]["segmentOrientationR"]["length"] == 2
    for descriptor in header["arrays"].values():
        item_size = np.dtype(descriptor["dtype"]).itemsize
        assert (body_offset + descriptor["offset"]) % item_size == 0


def test_native_replicate_order_combines_trial_and_step_sequence(tmp_path):
    rows = []
    base = pd.Timestamp("2026-01-01T00:00:00")
    for replicate, (trial, step) in enumerate([(0, 0), (0, 1), (1, 0)], 1):
        for sample in range(2):
            rows.append({
                "Current Time": base + pd.Timedelta(seconds=replicate * 10 + sample),
                "CurrentTrial": trial, "CurrentStep": step,
                "GameObjectPosX": sample, "GameObjectPosZ": replicate,
                "GameObjectRotY": 15 * replicate,
                "ConfigFile": "Choice_Test.json", "Scene": "Choice",
                "FlyID": "fly-1",
            })
    pd.DataFrame(rows).to_csv(tmp_path / "sequence_VR1_.csv", index=False)

    dataset = load_native_dataset(str(tmp_path))
    header, body_offset = _header(dataset.binary)

    assert _array(dataset.binary, header, body_offset, "segmentTrial").tolist() == [0, 0, 1]
    assert _array(dataset.binary, header, body_offset, "segmentStep").tolist() == [0, 1, 0]
    assert _array(dataset.binary, header, body_offset, "segmentReplicate").tolist() == [1, 2, 3]
    assert header["ranges"]["replicate"] == [1.0, 3.0]


def test_native_loader_normalizes_mixed_timezone_files_without_warnings(tmp_path):
    naive = pd.DataFrame([{
        "Current Time": "2026-01-01T00:00:00",
        "CurrentTrial": 0, "CurrentStep": 0,
        "GameObjectPosX": 0, "GameObjectPosZ": 0, "GameObjectRotY": 0,
        "ConfigFile": "Choice_Test.json", "Scene": "Choice", "FlyID": "fly-1",
    }, {
        "Current Time": "2026-01-01T00:00:01",
        "CurrentTrial": 0, "CurrentStep": 0,
        "GameObjectPosX": 1, "GameObjectPosZ": 0, "GameObjectRotY": 0,
        "ConfigFile": "Choice_Test.json", "Scene": "Choice", "FlyID": "fly-1",
    }])
    aware = naive.copy()
    aware["Current Time"] = ["2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z"]
    naive.to_csv(tmp_path / "naive_VR1_.csv", index=False)
    aware.to_csv(tmp_path / "aware_VR1_.csv", index=False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dataset = load_native_dataset(str(tmp_path))

    assert dataset.frame["Current Time"].dtype == np.dtype("datetime64[ns]")
    assert dataset.header["counts"]["segments"] == 2
    assert not [warning for warning in caught if "timezone" in str(warning.message).lower()]


def test_native_loader_skips_confirmed_duplicate_copies(tmp_path):
    original = tmp_path / "session_VR1_.csv"
    nested = tmp_path / "recording" / original.name
    nested.parent.mkdir()
    _write_csv(original, 0)
    shutil.copy2(original, nested)
    (nested.parent / "experiment.json").write_text("{}")

    dataset = load_native_dataset(str(tmp_path))

    assert dataset.header["counts"]["discoveredFiles"] == 2
    assert dataset.header["counts"]["duplicateFilesSkipped"] == 1
    assert dataset.header["counts"]["sourceRows"] == 6
    assert dataset.header["counts"]["retainedRows"] == 6
    assert set(dataset.frame["SourceFolder"].astype(str)) == {nested.parent.name}


def test_native_server_serves_plotly_free_shell_and_binary(tmp_path):
    _write_csv(tmp_path / "session_VR1_.csv", 0)
    application = create_native_app()
    client = application.test_client()

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json == {"ok": True, "renderer": "browser-native", "plotly": False}

    shell = client.get("/")
    assert shell.status_code == 200
    assert b"TrajectoryRenderer" not in shell.data
    assert b"plotly.min.js" not in shell.data.lower()
    assert b"vendor/echarts.min.js" in shell.data
    assert b"polar-angle-source" in shell.data

    script = client.get("/static/app.js")
    styles = client.get("/static/dashboard.css")
    assert script.status_code == styles.status_code == 200
    assert b"--panel-grid-height" in script.data
    assert b'panelGrid = "true"' in script.data
    assert b'overflow-y: auto' in styles.data
    assert b'grid-auto-rows: auto' in styles.data

    response = client.post("/api/load", json={"source": str(tmp_path)})
    assert response.status_code == 200
    assert response.mimetype == "application/vnd.trajectory-dashboard"
    header, _ = _header(response.data)
    assert header["counts"]["retainedRows"] == 6
    assert header["arrays"]["x"]["length"] == 6


def test_native_server_resolves_a_dropped_folder(monkeypatch, tmp_path):
    folder = tmp_path / "Dropped Session"
    folder.mkdir()
    _write_csv(folder / "session_VR1_.csv", 0)
    monkeypatch.setenv("TRAJ_DATA_ROOT", str(tmp_path))
    client = create_native_app().test_client()

    response = client.post("/api/resolve-drop", json={
        "folder": folder.name,
        "files": [f"{folder.name}/session_VR1_.csv"],
    })

    assert response.status_code == 200
    assert response.json["source"].endswith("Dropped Session")


def test_playback_uses_duration_p95_instead_of_the_longest_outlier(tmp_path):
    rows = []
    base = pd.Timestamp("2026-01-01T00:00:00")
    for trial in range(101):
        duration = 1000 if trial == 100 else 10
        for seconds in (0, duration):
            rows.append({
                "Current Time": base + pd.Timedelta(seconds=seconds),
                "CurrentTrial": trial,
                "CurrentStep": 0,
                "GameObjectPosX": seconds,
                "GameObjectPosZ": trial,
                "GameObjectRotY": 0,
                "ConfigFile": "Choice_Test.json",
                "Scene": "Choice",
                "FlyID": "fly-1",
            })
    pd.DataFrame(rows).to_csv(tmp_path / "outlier_VR1_.csv", index=False)

    header = load_native_dataset(str(tmp_path)).header

    assert header["ranges"]["time"][1] == 1000
    assert header["playbackMax"] == 10
    assert header["playbackQuantiles"]["p95"] == 10
    assert header["playbackQuantiles"]["max"] == 1000


def test_directory_source_recurses_without_loading_a_sibling(tmp_path):
    selected = tmp_path / "selected"
    sibling = tmp_path / "sibling"
    (selected / "session-a").mkdir(parents=True)
    sibling.mkdir()
    _write_csv(selected / "session-a" / "inside_VR1_.csv", 0)
    _write_csv(selected / "session-a" / "inside_without_vr_suffix.csv", 2)
    _write_csv(sibling / "outside_VR1_.csv", 4)

    dataset = load_native_dataset(str(selected))

    assert dataset.header["counts"]["files"] == 2
    assert dataset.header["categories"]["file"] == [
        "inside_VR1_.csv", "inside_without_vr_suffix.csv",
    ]


def test_drop_resolution_uses_the_complete_manifest(monkeypatch, tmp_path):
    wrong = tmp_path / "first" / "Repeated"
    right = tmp_path / "second" / "Repeated"
    wrong.mkdir(parents=True)
    right.mkdir(parents=True)
    _write_csv(wrong / "session_VR1_.csv", 0)
    _write_csv(right / "session_VR1_.csv", 0)
    _write_csv(right / "session_VR2_.csv", 4)
    monkeypatch.setenv("TRAJ_DATA_ROOT", str(tmp_path))
    client = create_native_app().test_client()

    response = client.post("/api/resolve-drop", json={
        "folder": "Repeated",
        "files": [
            {"path": "Repeated/session_VR1_.csv", "size": (right / "session_VR1_.csv").stat().st_size},
            {"path": "Repeated/session_VR2_.csv", "size": (right / "session_VR2_.csv").stat().st_size},
        ],
    })

    assert response.status_code == 200
    assert response.json["source"].endswith("second/Repeated")


def test_drop_resolution_refuses_an_ambiguous_same_named_folder(monkeypatch, tmp_path):
    first = tmp_path / "first" / "Repeated"
    second = tmp_path / "second" / "Repeated"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    _write_csv(first / "session_VR1_.csv", 0)
    _write_csv(second / "session_VR1_.csv", 0)
    monkeypatch.setenv("TRAJ_DATA_ROOT", str(tmp_path))
    client = create_native_app().test_client()

    response = client.post("/api/resolve-drop", json={
        "folder": "Repeated",
        "files": [{
            "path": "Repeated/session_VR1_.csv",
            "size": (first / "session_VR1_.csv").stat().st_size,
        }],
    })

    assert response.status_code == 404


def test_roi_geometry_keeps_unity_xz_convention():
    x, z = roi_xz(10, 90)
    assert x == 10
    assert abs(z) < 1e-8
    config = {
        "objects": [
            {"type": "tree", "position": {"radius": 10, "angle": -30}},
            {"type": "tree", "position": {"x": 3, "y": 0, "z": 4}},
        ]
    }
    rois = rois_from_config(config)
    assert [roi["side"] for roi in rois] == ["left", "right"]
    metadata = [{
        "sequence_order": ["Choice_Targets.json", "Choice_None.json"],
        "configs": {"prefix_ControlScene_Choice_Targets.json": config},
    }]
    mapped = rois_by_config(metadata)
    assert mapped["Choice_Targets.json"]
    assert mapped["Choice_None.json"][0]["inferred"] is True


def test_config_labels_follow_target_geometry_and_define_mirror_pool():
    first = _config_presentation("Choice_SubFlip_BigFarNoFlip.json", [
        {"x": 4, "z": 8, "r": 9, "side": "right", "type": "tree"},
        {"x": -4, "z": 8, "r": 9, "side": "left", "type": "tree"},
    ])
    reflected = _config_presentation("Choice_BigFarNoFlip_SubFlip.json", [
        {"x": 4, "z": 8, "r": 9, "side": "right", "type": "tree"},
        {"x": -4, "z": 8, "r": 9, "side": "left", "type": "tree"},
    ])

    assert first["label"] == "Left: far / no flip · Right: near / flip"
    assert reflected["label"] == "Left: near / flip · Right: far / no flip"
    assert first["mirrorKey"] == reflected["mirrorKey"]
    assert first["mirrorSign"] == -reflected["mirrorSign"]


def test_config_x_separator_discards_protocol_prefix_and_uses_unity_sides():
    targets = [
        {
            "x": 29.6, "z": 0, "r": 29.6,
            "side": "right", "type": "LocustBand_black",
        },
        {
            "x": -29.6, "z": 0, "r": 29.6,
            "side": "left", "type": "LocustBand",
        },
    ]
    black_first = _config_presentation(
        "bilateral_bandH0_constant_distance_black_animated_x_gregarious_animated.json",
        targets,
    )
    gregarious_first = _config_presentation(
        "bilateral_bandH0_constant_distance_gregarious_animated_x_black_animated.json",
        [
            dict(targets[0], type="LocustBand"),
            dict(targets[1], type="LocustBand_black"),
        ],
    )

    assert black_first["label"] == (
        "Left: gregarious animated · Right: black animated"
    )
    assert gregarious_first["label"] == (
        "Left: black animated · Right: gregarious animated"
    )
    assert black_first["mirrorKey"] == gregarious_first["mirrorKey"]
    assert black_first["mirrorSign"] == -1
    assert gregarious_first["mirrorSign"] == 1


def test_native_dataset_only_pools_geometry_confirmed_mirror_pairs(tmp_path):
    rows = []
    for step, config in enumerate(("Choice_SubFlip_BigFarNoFlip.json", "Choice_BigFarNoFlip_SubFlip.json")):
        for sample in range(3):
            rows.append({
                "Current Time": f"2026-01-01T00:00:0{sample}",
                "CurrentTrial": 0, "CurrentStep": step,
                "GameObjectPosX": sample, "GameObjectPosZ": sample * .5,
                "GameObjectRotY": sample * 10, "ConfigFile": config,
                "Scene": "Choice", "FlyID": "fly-1",
            })
    pd.DataFrame(rows).to_csv(tmp_path / "session_VR1_.csv", index=False)
    left_right = {"objects": [
        {"type": "tree", "position": {"x": -4, "z": 8}},
        {"type": "tree", "position": {"x": 4, "z": 8}},
    ]}
    for name in ("Choice_SubFlip_BigFarNoFlip.json", "Choice_BigFarNoFlip_SubFlip.json"):
        (tmp_path / f"test_ControlScene_{name}").write_text(json.dumps(left_right))

    dataset = load_native_dataset(str(tmp_path))
    header, body_offset = _header(dataset.binary)
    signs = _array(dataset.binary, header, body_offset, "segmentMirrorSign")
    pooled = _array(dataset.binary, header, body_offset, "segmentMirrorConfig")

    assert len(header["categories"]["mirrorConfig"]) == 1
    assert set(signs.tolist()) == {-1, 1}
    assert set(pooled.tolist()) == {0}
    assert all(item["poolable"] for item in header["configPresentation"].values())


def test_native_view_recipe_returns_readable_python_groups(tmp_path):
    _write_csv(tmp_path / "session_VR1_.csv", 0)
    recipe = {
        "schema": "daari-deepa-view/v1",
        "source": str(tmp_path),
        "filtersByLabel": {"config": ["Choice_Test.json"]},
        "state": {
            "groupBy": "config",
            "angleSource": "orientation",
            "filters": {},
            "ranges": {
                "trial": [0, 0], "step": [0, 0],
                "replicate": [1, 1], "time": [0, 1],
                "resultant": [0, 1],
            },
        },
        "visuals": {"trajectory-width": "1.7"},
    }

    view = load_view_recipe(recipe)

    assert view.filter_spec.configs == ("Choice_Test.json",)
    assert view.filter_spec.replicate_range == (1, 1)
    assert view.filter_spec.local_time_range == (0, 1)
    assert view.filter_spec.resultant_range == (0, 1)
    assert len(view.filter_result.filtered) == 4
    assert list(view.groups) == ["Choice_Test.json"]


def test_native_url_state_is_parseable_without_starting_the_dashboard():
    recipe = recipe_from_url(
        "http://127.0.0.1:8060/?source=data%2Fsession&group=scene"
        "&filters=%7B%22config%22%3A%5B0%5D%7D"
    )

    assert recipe.source == "data/session"
    assert recipe.state["groupBy"] == "scene"
    assert recipe.state["filters"] == {"config": [0]}
    assert recipe.state["angleSource"] == "orientation"
