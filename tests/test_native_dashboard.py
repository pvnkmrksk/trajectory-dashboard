from __future__ import annotations

import json
import struct

import numpy as np
import pandas as pd

from native_dashboard.dataset import FORMAT_NAME, load_native_dataset
from native_dashboard.server import create_native_app
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
    assert "SensorValue" in header["rawColumns"]
    for descriptor in header["arrays"].values():
        item_size = np.dtype(descriptor["dtype"]).itemsize
        assert (body_offset + descriptor["offset"]) % item_size == 0


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
    assert b"plotly" not in shell.data.lower()

    response = client.post("/api/load", json={"source": str(tmp_path)})
    assert response.status_code == 200
    assert response.mimetype == "application/vnd.trajectory-dashboard"
    header, _ = _header(response.data)
    assert header["counts"]["retainedRows"] == 12
    assert header["arrays"]["x"]["length"] == 12


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
