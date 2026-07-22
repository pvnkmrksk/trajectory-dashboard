import math
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

import app


def _polar_frame():
    rows = []
    specs = [
        ("file_a_T1_S0", 1, [0.0, 0.0]),
        ("file_b_T1_S0", 2, [90.0, 90.0, 90.0, 90.0]),
    ]
    for seg_id, trial, angles in specs:
        for i, angle in enumerate(angles):
            rows.append({
                "_seg_id": seg_id,
                "ConfigFile": "cfg.json",
                "animal": "1@VR1",
                "VR": "VR1",
                "FlyID": "1",
                "CurrentTrial": trial,
                "CurrentStep": 0,
                "SourceFile": seg_id.split("_T", 1)[0] + ".csv",
                "Current Time": pd.Timestamp("2026-01-01") + pd.Timedelta(i, "s"),
                "GameObjectPosX": float(i),
                "GameObjectPosZ": 0.0,
                "GameObjectRotY": angle,
            })
    frame = pd.DataFrame(rows)
    frame.attrs["_frame_token"] = ("test", "polar")
    return frame


class DashboardRegressionTests(unittest.TestCase):
    def test_speed_is_the_default_render_mode(self):
        self.assertEqual(app._render_mode(None), "speed")
        self.assertEqual(app._render_mode("speed"), "speed")
        self.assertEqual(app._render_mode("accuracy"), "accuracy")

    def test_orientation_uses_unity_forward_clockwise_convention(self):
        ray = app.rayleigh_by_segment(
            _polar_frame(), color_by="none", use_cache=False,
            angle_source="orientation")
        self.assertEqual(len(ray), 2)
        self.assertAlmostEqual(float(ray.iloc[0]["theta_deg"]), 0.0, places=8)
        self.assertAlmostEqual(float(ray.iloc[1]["theta_deg"]), 90.0, places=8)
        self.assertTrue(np.allclose(ray["R"].to_numpy(), 1.0))

    def test_population_vector_reconstructs_all_valid_samples(self):
        frame = _polar_frame()
        ray = app.rayleigh_by_segment(
            frame, color_by="none", use_cache=False,
            angle_source="orientation")
        rbar, theta, n = app._population_polar_vector(ray)

        angles = np.radians(frame["GameObjectRotY"].to_numpy(dtype=float))
        expected_x = float(np.mean(np.sin(angles)))
        expected_z = float(np.mean(np.cos(angles)))
        self.assertEqual(n, len(frame))
        self.assertAlmostEqual(rbar, math.hypot(expected_x, expected_z), places=10)
        self.assertAlmostEqual(theta, math.degrees(math.atan2(expected_x, expected_z)), places=10)

    def test_population_vector_is_not_changed_by_display_thinning(self):
        frame = _polar_frame()
        ray = app.rayleigh_by_segment(
            frame, color_by="none", use_cache=False,
            angle_source="orientation")
        expected = app._population_polar_vector(ray)
        fig = app.build_polar_figure(
            frame, group_by="all", pool_mode="pooled", max_points=3,
            color_by="none", angle_source="orientation")
        means = [trace for trace in fig.data
                 if getattr(getattr(trace, "line", None), "width", None) == 3]
        self.assertEqual(len(means), 1)
        self.assertAlmostEqual(float(means[0].r[-1]), expected[0], places=10)
        self.assertAlmostEqual(float(means[0].theta[-1]), expected[1], places=10)

    def test_inline_histogram_uses_explicit_bar_bins(self):
        fig = app.build_mini_histogram(np.arange(10), [2, 7], x_range=(0, 9))
        self.assertEqual(len(fig.data), 1)
        self.assertEqual(fig.data[0].type, "bar")
        self.assertEqual(len(fig.data[0].x), 10)
        self.assertEqual(len(fig.data[0].width), 10)

    def test_dataset_generation_waits_for_range_controls(self):
        master = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..trajectory-plot.figure...")
        )
        input_ids = {item["id"] for item in master["inputs"]}
        state_ids = {item["id"] for item in master["state"]}
        self.assertNotIn("data-generation", input_ids)
        self.assertIn("data-generation", state_ids)

    def test_auto_threshold_refresh_does_not_duplicate_initial_render(self):
        original_ctx = app.ctx
        app.ctx = SimpleNamespace(triggered_id="auto-thresholds")
        try:
            disabled = app.apply_auto_thresholds(
                [], [], {"vel": 12.0, "disp": 0.5}, 0, "/data/*.csv")
            enabled = app.apply_auto_thresholds(
                ["on"], [], {"vel": 12.0, "disp": 0.5}, 0, "/data/*.csv")
        finally:
            app.ctx = original_ctx
        self.assertIs(disabled[-1], app.no_update)
        self.assertEqual(enabled[-1], 1)

    def test_status_dock_is_persistent_and_retired_preload_nodes_are_gone(self):
        def component_ids(node):
            found = set()
            node_id = getattr(node, "id", None)
            if node_id:
                found.add(node_id)
            children = getattr(node, "children", None)
            if children is None:
                return found
            if not isinstance(children, (list, tuple)):
                children = [children]
            for child in children:
                found.update(component_ids(child))
            return found

        ids = component_ids(app.app.layout)
        self.assertIn("status-dock", ids)
        self.assertIn("status-message", ids)
        self.assertIn("main-scroll", ids)
        self.assertNotIn("preload-view", ids)
        self.assertNotIn("preload-interval", ids)

    def test_export_is_offline_capable_and_contains_every_section(self):
        fig = app.go.Figure(app.go.Scatter(x=[0, 1], y=[0, 1]))
        document = app._compose_export_html(
            fig, fig, fig, fig, fig, fig, fig,
            include_raw=False, summary="test summary", share_state="?mode=speed",
        )
        self.assertNotIn('src="https://cdn.plot.ly', document)
        self.assertIn("plotly.js", document)
        for heading in ("Trajectories", "Heatmap", "Target diagnostics", "Polar",
                        "Velocity / Displacement", "Raw traces"):
            self.assertIn(f"<h3>{heading}</h3>", document)


if __name__ == "__main__":
    unittest.main()
