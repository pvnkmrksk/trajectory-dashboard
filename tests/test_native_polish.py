"""Regressions for robust displays and the browser report lifecycle."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np

from native_dashboard.dataset import _histogram_payload, load_native_dataset
from trajectory_dashboard.roi import rois_by_config


def test_histogram_p99_excludes_outlier_without_changing_exact_range():
    values = np.r_[np.arange(100.0), 1e12, np.nan, np.inf]
    result = _histogram_payload(values)
    assert result["displayRange"][1] == 99
    assert result["range"][1] == 1e12
    assert result["overflow"] == 1
    assert sum(result["counts"]) == 100


def test_histogram_zero_mass_does_not_fall_back_to_tracking_outlier():
    result = _histogram_payload(np.r_[np.zeros(1000), 1e12])
    assert result["displayRange"] == [0, 1]
    assert result["overflow"] == 1
    assert sum(result["counts"]) == 1000


def test_skipped_nonfinite_polar_target_cannot_inherit_modal_geometry():
    import pytest

    for radius, angle in [(-np.inf, 90), (0, np.inf)]:
        with pytest.raises(ValueError, match="Invalid target geometry"):
            rois_by_config([{"configs": {"bad.json": {"objects": [
                {"position": {"radius": radius, "angle": angle}},
            ]}}}], strict=True)


def test_metadata_change_invalidates_native_dataset_cache(tmp_path):
    import pandas as pd

    pd.DataFrame({
        "Current Time": ["2026-01-01T00:00:00", "2026-01-01T00:00:01"],
        "CurrentTrial": [0, 0], "CurrentStep": [0, 0],
        "GameObjectPosX": [0, 1], "GameObjectPosZ": [0, 1],
    }).to_csv(tmp_path / "session_VR1_.csv", index=False)
    first = load_native_dataset(str(tmp_path))
    assert load_native_dataset(str(tmp_path)) is first
    (tmp_path / "experiment.json").write_text('{"corrected": true}')
    second = load_native_dataset(str(tmp_path))
    assert second is not first
    assert second.dataset_id != first.dataset_id
    assert load_native_dataset(str(tmp_path)) is second


def test_report_cancellation_reenables_export_and_discards_pending_request():
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("Node.js is needed for browser scheduler regression")
    source = (Path(__file__).parents[1] / "native_dashboard/static/app.js").read_text()
    # Run the real cancellation function without instantiating browser renderers.
    start = source.index("function cancelPendingReport() {")
    end = source.index("\nfunction flushCompute()", start)
    script = """
      let reportRequestId = 12, pngRequestId = null, pendingCompute = {requestId:12}, datasetHeader = {};
      const button = {disabled:true, textContent:'Preparing report…'};
      const byId = () => button;
    """ + source[start:end] + """
      cancelPendingReport();
      const cancelled = {reportRequestId,pendingCompute,...button};
      reportRequestId=13; pendingCompute={requestId:14};
      cancelPendingReport();
      process.stdout.write(JSON.stringify({cancelled,preserved:pendingCompute.requestId}));
    """
    result = json.loads(subprocess.check_output([node, "-e", script], text=True))
    assert result["cancelled"] == {
        "reportRequestId": None, "pendingCompute": None,
        "disabled": False, "textContent": "Export native report",
    }
    assert result["preserved"] == 14


def test_compact_letters_stays_bounded_for_many_different_panels():
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("Node.js is needed for browser statistics regression")
    source = (Path(__file__).parents[1] / "native_dashboard/static/app.js").read_text()
    start = source.index("function compactLetters(")
    end = source.index("\nfunction renderStatistics(", start)
    script = source[start:end] + """
      const tests=[];
      for(let first=0;first<32;first++) for(let second=first+1;second<32;second++)
        tests.push({first,second,adjustedP:.001});
      process.stdout.write(JSON.stringify(compactLetters(32,tests)));
    """
    labels = json.loads(subprocess.check_output([node, "-e", script], text=True, timeout=5))
    assert len(set(labels)) == 32
    assert max(map(len, labels)) == 2
