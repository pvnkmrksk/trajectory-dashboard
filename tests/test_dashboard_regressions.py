import math
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

import app


def _components(node):
    yield node
    children = getattr(node, "children", None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        yield from _components(child)


def _component(component_id):
    return next(node for node in _components(app.app.layout)
                if getattr(node, "id", None) == component_id)


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

    def test_percentile_histogram_preserves_shape_on_zero_to_hundred_axis(self):
        values = np.array([1, 1, 1, 2, 3, 5, 8, 13, 21], dtype=float)
        raw_range = app._range_bounds(values, floor_zero=True,
                                      upper_pct=app.MINI_HIST_UPPER_PCT)
        raw = app.build_mini_histogram(values, raw_range, bins=8,
                                       x_range=raw_range)
        pct = app.build_percentile_mini_histogram(values, [10, 90], bins=8)
        self.assertTrue(np.array_equal(np.asarray(raw.data[0].y),
                                       np.asarray(pct.data[0].y)))
        self.assertEqual(tuple(pct.layout.xaxis.range), (0, 100))
        self.assertEqual(tuple(pct.layout.xaxis.tickvals), (0, 50, 100))

    def test_polar_quality_histograms_are_always_populated_from_ray_table(self):
        ray = app.rayleigh_by_segment(
            _polar_frame(), color_by="none", use_cache=False,
            angle_source="orientation")
        figures = app.build_polar_quality_histograms(ray, [0.2, 1], 0.25, 0.5)
        self.assertEqual(len(figures), 3)
        self.assertTrue(all(len(fig.data) == 1 for fig in figures))
        self.assertTrue(all(len(fig.data[0].x) > 0 for fig in figures))

    def test_step_range_keeps_complete_segment_ids(self):
        frame = pd.concat([
            _polar_frame().assign(CurrentStep=0),
            _polar_frame().assign(
                _seg_id=lambda d: d["_seg_id"] + "_S1", CurrentStep=1),
        ], ignore_index=True)
        selected = app.td_grouping.subset_frame(frame, step_range=(1, 1))
        self.assertTrue((selected["CurrentStep"] == 1).all())
        expected = frame.loc[frame["CurrentStep"] == 1].groupby("_seg_id").size()
        actual = selected.groupby("_seg_id").size()
        pd.testing.assert_series_equal(actual, expected)

    def test_trajectory_accepts_shared_robust_view_range(self):
        view = ((-5.0, 5.0), (-7.0, 7.0))
        fig = app.build_trajectory_figure(
            _polar_frame(), group_by="all", pool_mode="pooled",
            color_by="none", view_range=view)
        self.assertEqual(tuple(fig.layout.xaxis.range), view[0])
        self.assertEqual(tuple(fig.layout.yaxis.range), view[1])

    def test_dataset_generation_waits_for_range_controls(self):
        master = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..trajectory-plot.figure...")
        )
        input_ids = {item["id"] for item in master["inputs"]}
        state_ids = {item["id"] for item in master["state"]}
        self.assertNotIn("data-generation", input_ids)
        self.assertIn("data-generation", state_ids)
        self.assertIn("step-min", state_ids)
        self.assertIn("step-max", state_ids)

    def test_polar_controls_use_the_polar_only_callback(self):
        master = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..trajectory-plot.figure...")
        )
        master_inputs = {item["id"] for item in master["inputs"]}
        for control in ("polar-moving", "polar-walk", "polar-angle-source",
                        "polar-r-range", "polar-min-point-frac",
                        "polar-min-animal-frac"):
            self.assertNotIn(control, master_inputs)

        fast = next(
            meta for output, meta in app.app.callback_map.items()
            if "polar-r-hist.figure" in output
        )
        fast_inputs = {item["id"] for item in fast["inputs"]}
        self.assertIn("view-render-state", fast_inputs)
        self.assertIn("polar-moving", fast_inputs)
        self.assertIn("polar-min-animal-frac", fast_inputs)

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
        ids = {getattr(node, "id", None) for node in _components(app.app.layout)}
        self.assertIn("status-dock", ids)
        self.assertIn("status-message", ids)
        self.assertIn("main-scroll", ids)
        self.assertNotIn("preload-view", ids)
        self.assertNotIn("preload-interval", ids)

    def test_new_controls_and_url_restore_are_synchronised(self):
        self.assertEqual(_component("heatmap-crange").value, "percentile")
        self.assertIsNotNone(_component("step-range"))
        restored = app.restore_from_url("?smin=2&smax=4&hcrange=percentile", False)
        self.assertEqual(len(restored), 42)
        self.assertEqual(restored[24:26], (2, 4))
        self.assertEqual(restored[34], [2.0, 4.0])
        self.assertEqual(len(app.restore_from_url("", True)), 42)
        value_restore = app.restore_from_url(
            "?hcmin=150&hcmax=200&hcrange=value", False)
        self.assertEqual(value_restore[32], [150.0, 200.0])

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
