"""Guard target identities before pooling recordings from multiple folders."""

from __future__ import annotations

import math

import pytest

from trajectory_dashboard.roi import rois_by_config


def _metadata(folder, *, left=-10, right=10, name="Choice_A_B.json"):
    return {
        "folder": folder,
        "sequence_order": [name],
        "configs": {
            name: {"objects": [
                {"type": "target", "position": {"x": left, "z": 2}},
                {"type": "target", "position": {"x": right, "z": 2}},
            ]},
        },
    }


def test_strict_geometry_accepts_identical_targets_across_folders():
    first = _metadata("/recordings/session-A")
    # Source order and tiny serialization differences do not alter the frame.
    second = _metadata("/recordings/session-B", left=-10.000001, right=10.000001)
    second["configs"]["Choice_A_B.json"]["objects"].reverse()

    result = rois_by_config([first, second], strict=True)

    assert [target["x"] for target in result["Choice_A_B.json"]] == [-10, 10]
    assert [target["side"] for target in result["Choice_A_B.json"]] == ["left", "right"]


def test_strict_geometry_rejects_conflicting_short_config_names():
    first = _metadata("/recordings/session-A", name="A_ControlScene_Choice_A_B.json")
    second = _metadata("/recordings/session-B", left=-20, right=20,
                       name="B_ControlScene_Choice_A_B.json")

    with pytest.raises(ValueError) as error:
        rois_by_config([first, second], strict=True)

    message = str(error.value)
    assert "Choice_A_B.json" in message
    assert "/recordings/session-A" in message
    assert "/recordings/session-B" in message
    assert "load these folders separately" in message


def test_strict_geometry_allows_different_configs_to_have_different_targets():
    result = rois_by_config([
        _metadata("/recordings/session-A"),
        _metadata("/recordings/session-B", name="Choice_C_D.json", left=-20, right=20),
    ], strict=True)

    assert result["Choice_A_B.json"][0]["x"] == -10
    assert result["Choice_C_D.json"][0]["x"] == -20


@pytest.mark.parametrize("position", [
    {"x": float("nan"), "z": 2},
    {"x": -10, "z": float("inf")},
    {"x": "bad coordinate", "z": 2},
    {"radius": float("inf"), "angle": -45},
    {"radius": 10, "angle": float("inf")},
])
def test_strict_geometry_refuses_invalid_targets_before_modal_inference(position):
    valid = _metadata("/recordings/good")
    invalid = _metadata("/recordings/broken", name="Choice_Broken.json")
    invalid["configs"]["Choice_Broken.json"]["objects"][0]["position"] = position

    # Reject before malformed geometry can inherit valid targets or enter a
    # reach calculation as a plausible left/right target.
    with pytest.raises(ValueError) as error:
        rois_by_config([valid, invalid], strict=True)

    assert "Choice_Broken.json" in str(error.value)
    assert "/recordings/broken" in str(error.value)
    assert "finite numbers" in str(error.value)


def test_strict_geometry_preserves_empty_config_inference():
    source = _metadata("/recordings/session-A")
    source["configs"]["Choice_Empty.json"] = {"objects": []}

    result = rois_by_config([source], strict=True)

    inferred = result["Choice_Empty.json"]
    assert [target["x"] for target in inferred] == [-10, 10]
    assert all(target["inferred"] for target in inferred)
    assert all(math.isfinite(target["x"]) and math.isfinite(target["z"]) for target in inferred)


def test_default_geometry_mapping_keeps_legacy_first_folder_behavior():
    result = rois_by_config([
        _metadata("/recordings/session-A"),
        _metadata("/recordings/session-B", left=-20, right=20),
    ])

    assert [target["x"] for target in result["Choice_A_B.json"]] == [-10, 10]
