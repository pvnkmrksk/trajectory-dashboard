import math
import inspect
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs

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

    def test_local_direction_field_encodes_cell_strength_and_abundance(self):
        frame = _polar_frame().iloc[:2].copy()
        frame["GameObjectPosX"] = 0.0
        frame["GameObjectPosZ"] = 0.0
        frame["GameObjectRotY"] = [0.0, 180.0]
        bins = app._direction_field_bins(
            frame, "all", "pooled", 1, 10.0, 100.0,
            "orientation", False, None)
        occupied = bins["groups"][0]["count"] > 0
        self.assertEqual(int(bins["groups"][0]["count"][occupied][0]), 2)
        self.assertAlmostEqual(
            float(bins["groups"][0]["R"][occupied][0]), 0.0, places=10)
        scaled = app._direction_field_bins(
            frame, "all", "pooled", 1, 10.0, 100.0,
            "orientation", False, None, metric="count",
            log_scale=False, cmin=0, cmax=10)
        scaled_occupied = scaled["groups"][0]["count"] > 0
        self.assertAlmostEqual(
            float(scaled["groups"][0]["abundance"][scaled_occupied][0]),
            0.2,
        )

        fig = app.build_direction_field_figure(
            frame, group_by="all", pool_mode="pooled", ncols=1,
            bin_size=10.0, bound_pct=100.0, angle_source="orientation",
            metric="count", cmin=0, cmax=10)
        self.assertEqual(len(fig.layout.images), 1)
        self.assertTrue(
            str(fig.layout.images[0].source).startswith("data:image/png;base64,"))
        self.assertEqual(fig.layout.meta["flow_cells"], 1)
        self.assertEqual(fig.layout.meta["heading_source"], "orientation")
        self.assertEqual(fig.layout.meta["max_radius"], 0.49)
        self.assertEqual(fig.layout.meta["abundance_metric"], "count")
        self.assertEqual(fig.layout.meta["abundance_scale"], "linear")
        self.assertEqual(list(fig.layout.meta["abundance_range"]), [0.0, 10.0])
        self.assertEqual(fig.layout.meta["marginals"], "active heatmap metric")
        marginal_names = {
            trace.name for trace in fig.data
            if trace.name in ("X abundance", "Z abundance")
        }
        self.assertEqual(marginal_names, {"X abundance", "Z abundance"})
        self.assertLess(fig.layout.xaxis.domain[1], 1.0)
        self.assertLess(fig.layout.yaxis.domain[1], 1.0)
        self.assertFalse(any(
            bool(getattr(getattr(trace, "marker", None), "showscale", False))
            for trace in fig.data
        ))

    def test_local_direction_strokes_respect_adjustable_cell_radius(self):
        stroke_x, stroke_z = app._flow_arrow_arrays(
            np.array([0.0]), np.array([0.0]), np.array([1.0]),
            np.array([0.0]), 10.0, max_radius=0.3)
        self.assertEqual(len(stroke_x), 3)
        self.assertEqual(len(stroke_z), 3)
        self.assertAlmostEqual(stroke_x[1], 0.0)
        self.assertAlmostEqual(stroke_z[1], 3.0)
        self.assertTrue(np.isnan(stroke_x[2]))
        self.assertTrue(np.isnan(stroke_z[2]))

    def test_local_direction_field_movement_heading_uses_segment_safe_diffs(self):
        frame = _polar_frame().copy()
        bins = app._direction_field_bins(
            frame, "all", "pooled", 1, 100.0, 100.0,
            "movement", False, None)
        result = bins["groups"][0]
        occupied = result["count"] > 0
        # One sample per segment has no within-segment movement heading.
        self.assertEqual(int(result["count"][occupied].sum()),
                         len(frame) - frame["_seg_id"].nunique())
        self.assertTrue(np.allclose(result["R"][occupied], 1.0))
        self.assertTrue(np.allclose(result["theta"][occupied], 90.0))

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
        self.assertTrue(all(len(fig.data[0].x) == 36 for fig in figures))
        self.assertTrue(all(np.allclose(fig.data[0].width, 0.96 / 36)
                            for fig in figures))

    def test_polar_titles_report_retained_over_visible_trials(self):
        fig = app.build_polar_figure(
            _polar_frame(), group_by="all", pool_mode="pooled",
            color_by="none", r_range=[0.0, 0.5], angle_source="orientation")
        titles = [annotation.text for annotation in fig.layout.annotations]
        self.assertTrue(any("0/2 trials shown" in title for title in titles))

    def test_raw_initial_heading_uses_one_first_sample_per_segment(self):
        fig = app.build_initial_heading_distribution(_polar_frame(), bins=36)
        bars = [trace for trace in fig.data if trace.type == "barpolar"]
        self.assertEqual(len(bars), 1)
        self.assertEqual(len(bars[0].r), 36)
        self.assertEqual(sum(bars[0].r), 2)
        self.assertEqual(float(bars[0].theta[0]), 0.0)
        self.assertEqual(float(bars[0].theta[1]), 10.0)
        self.assertTrue(any("2 segment starts" in annotation.text
                            for annotation in fig.layout.annotations))

    def test_trial_metrics_switch_between_swarms_and_violins(self):
        def stats(count):
            return pd.DataFrame({
                "seg_id": [f"s{i}" for i in range(count)],
                "n_points": np.full(count, 100),
                "distance_walked": np.linspace(5, 10, count),
                "displacement": np.linspace(2, 4, count),
                "median_local_tortuosity": np.linspace(1, 1.5, count),
                "median_velocity": np.linspace(0.5, 2, count),
                "config": ["cfg.json"] * count,
                "vr": ["VR1"] * count,
                "fly_id": ["1"] * count,
                "scene": ["scene"] * count,
                "source_folder": ["folder"] * count,
            })

        swarm = app.build_trial_metrics_figure(stats(8))
        boundary = app.build_trial_metrics_figure(stats(200))
        violin = app.build_trial_metrics_figure(stats(201))
        self.assertEqual({trace.type for trace in swarm.data}, {"scatter"})
        self.assertEqual({trace.type for trace in boundary.data}, {"scatter"})
        self.assertEqual({trace.type for trace in violin.data}, {"violin"})
        self.assertEqual(len(swarm.data), 4)
        self.assertEqual(len(swarm.layout.shapes), 8)
        self.assertEqual(len(violin.layout.shapes), 8)
        self.assertGreater(np.ptp(np.asarray(swarm.data[0].x, dtype=float)), 0)
        self.assertTrue(all(
            abs(float(x)) <= 0.36 for x in swarm.data[0].x
        ))
        iqr, median = swarm.layout.shapes[:2]
        self.assertEqual(iqr.type, "rect")
        self.assertEqual(median.type, "line")
        self.assertAlmostEqual(float(iqr.x1) - float(iqr.x0), 0.72)
        self.assertAlmostEqual(float(median.x1) - float(median.x0), 0.72)
        self.assertAlmostEqual(float(median.y0), 7.5)
        self.assertIn("15-sample path/chord", swarm.layout.yaxis3.title.text)

    def test_local_tortuosity_uses_matching_path_and_chord_intervals(self):
        straight = pd.DataFrame({
            "_seg_id": ["straight_T1_S0"] * 15,
            "GameObjectPosX": np.arange(15, dtype=float),
            "GameObjectPosZ": np.zeros(15),
        })
        turn = straight.copy()
        turn["_seg_id"] = "turn_T1_S0"
        turn["GameObjectPosX"] = np.r_[np.arange(8), np.full(7, 7)]
        turn["GameObjectPosZ"] = np.r_[np.zeros(8), np.arange(1, 8)]

        self.assertAlmostEqual(
            float(app.compute_tortuosity(straight, window=15)[-1]),
            1.0,
            places=10,
        )
        self.assertGreater(
            float(app.compute_tortuosity(turn, window=15)[-1]),
            1.3,
        )

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

    def test_inherited_frame_tokens_do_not_alias_different_row_subsets(self):
        frame = _polar_frame().reset_index(drop=True)
        frame.attrs["_frame_token"] = ("data", "same-source")
        subset_a = frame.iloc[[0, 1, 3]].copy()
        subset_b = frame.iloc[[0, 2, 3]].copy()
        self.assertEqual(subset_a.attrs["_frame_token"], frame.attrs["_frame_token"])
        self.assertEqual(subset_b.attrs["_frame_token"], frame.attrs["_frame_token"])
        self.assertNotEqual(
            app._frame_cache_token(frame),
            app._frame_cache_token(subset_a),
        )
        self.assertNotEqual(
            app._frame_cache_token(subset_a),
            app._frame_cache_token(subset_b),
        )

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

    def test_heatmap_colour_controls_never_arm_the_full_renderer(self):
        auto = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..auto-replot-state.data...")
        )
        auto_inputs = {item["id"] for item in auto["inputs"]}
        for control in ("heatmap-color-range", "heatmap-cmin",
                        "heatmap-cmax", "heatmap-crange"):
            self.assertNotIn(control, auto_inputs)

        colour = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..heatmap-color-values.data...")
        )
        colour_inputs = {item["id"] for item in colour["inputs"]}
        colour_state = {item["id"] for item in colour["state"]}
        self.assertIn("heatmap-color-distributions", colour_inputs)
        self.assertNotIn("store-glob", colour_state)
        self.assertNotIn("vel-threshold", colour_state)

    def test_native_diagnostics_are_not_outputs_of_the_master_renderer(self):
        master_key = next(
            output for output in app.app.callback_map
            if output.startswith("..trajectory-plot.figure...")
        )
        self.assertNotIn("vel-histogram.figure", master_key)
        self.assertNotIn("disp-histogram.figure", master_key)

    def test_large_callback_signatures_match_registered_inputs_and_states(self):
        checks = (
            ("trajectory-plot.figure", app.update_plots),
            ("polar-r-hist.figure", app.update_polar_only),
            ("download-html.data", app.export_html),
        )
        for output_fragment, callback in checks:
            meta = next(
                value for output, value in app.app.callback_map.items()
                if output_fragment in output
            )
            registered = len(meta["inputs"]) + len(meta["state"])
            self.assertEqual(
                len(inspect.signature(callback).parameters),
                registered,
                output_fragment,
            )

    def test_sections_follow_analysis_then_diagnostics_order(self):
        ids = [getattr(node, "id", None) for node in _components(app.app.layout)]
        self.assertIn("flow-field-legend", ids)
        positions = [ids.index(component_id) for component_id in (
            "view-traj", "view-heat", "view-flow", "view-polar", "view-roi",
            "view-metrics", "view-diag")]
        self.assertEqual(positions, sorted(positions))

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
        self.assertIn("flow-max-radius", fast_inputs)
        self.assertIn("polar-min-animal-frac", fast_inputs)
        self.assertIn("heatmap-metric", fast_inputs)
        self.assertIn("heatmap-scale", fast_inputs)
        self.assertIn("heatmap-cmin", fast_inputs)
        self.assertIn("heatmap-cmax", fast_inputs)
        self.assertIn("heatmap-crange", fast_inputs)

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
        self.assertIn("status-progress-bar", ids)
        self.assertIn("operation-progress", ids)
        self.assertIn("main-scroll", ids)
        self.assertNotIn("preload-view", ids)
        self.assertNotIn("preload-interval", ids)

    def test_inline_clientside_callbacks_are_valid_javascript(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available")
        document = app.app.server.test_client().get("/").get_data(as_text=True)
        scripts = [
            body for body in re.findall(
                r"<script[^>]*>(.*?)</script>", document, re.DOTALL
            )
            if "_dashprivate_clientside_funcs" in body
        ]
        self.assertTrue(scripts)
        failures = []
        for index, script in enumerate(scripts):
            result = subprocess.run(
                [node, "--check"], input=script, text=True,
                capture_output=True, check=False,
            )
            if result.returncode:
                failures.append(f"script {index}: {result.stderr}")
        self.assertFalse(failures, "\n".join(failures))

    def test_new_controls_and_url_restore_are_synchronised(self):
        self.assertEqual(_component("heatmap-crange").value, "percentile")
        self.assertEqual(_component("heatmap-color-range").value, [0, 99])
        self.assertEqual(_component("flow-max-radius").value, 0.49)
        self.assertIsNotNone(_component("step-range"))
        restored = app.restore_from_url(
            "?smin=2&smax=4&hcrange=percentile&frad=0.31", False)
        self.assertEqual(len(restored), 47)
        self.assertEqual(restored[24:26], (2, 4))
        self.assertEqual(restored[36], [2.0, 4.0])
        self.assertEqual(restored[44], 0.31)
        self.assertEqual(len(app.restore_from_url("", True)), 47)
        value_restore = app.restore_from_url(
            "?hcmin=150&hcmax=200&hcrange=value", False)
        self.assertEqual(value_restore[34], [150.0, 200.0])

        exact_velocity = app.restore_from_url(
            "?vrmin=2.5&vrmax=250&layout=compare", False)
        self.assertEqual(exact_velocity[31:33], (2.5, 250))
        self.assertEqual(exact_velocity[42], "compare")

    def test_long_horizontal_legends_reserve_plot_height(self):
        short_top, short_extra = app._horizontal_legend_layout(["VR1"], 2)
        long_top, long_extra = app._horizontal_legend_layout(
            [f"VR{i} fly with a long label" for i in range(24)], 2)
        self.assertEqual(short_extra, 0)
        self.assertGreater(long_extra, short_extra)
        self.assertGreater(long_top, short_top)

    def test_exact_velocity_bounds_are_unbounded_and_explicit(self):
        self.assertIsNone(getattr(_component("vel-range-min"), "min", None))
        self.assertIsNone(getattr(_component("vel-range-max"), "max", None))
        value = app.effective_velocity_range([0, 6], None, 250)
        self.assertEqual(value["range"], [0.0, 250.0])
        self.assertTrue(value["explicit"])
        self.assertEqual(app._active_stat_range(
            value,
            pd.DataFrame({"peak_velocity": [1.0, 6.0, 300.0]}),
            "peak_velocity",
        ), (0.0, 250.0))

    def test_segment_peak_velocity_matches_smoothed_colour_series(self):
        n = 60
        frame = pd.DataFrame({
            "_seg_id": ["source_T1_S0"] * n,
            "Current Time": pd.date_range("2026-01-01", periods=n, freq="100ms"),
            "GameObjectPosX": np.r_[np.arange(30) * 0.1,
                                    np.arange(30, 60) * 0.1 + 10],
            "GameObjectPosZ": np.zeros(n),
            "ConfigFile": "cfg.json",
            "VR": "VR1",
            "FlyID": "1",
            "SceneName": "scene",
            "SourceFolder": "folder",
        })
        raw_peak = float(np.nanmax(app.velocity_all(frame)))
        smooth = app.smoothed_velocity(frame)
        stats = app.compute_segment_stats(frame, smooth)
        self.assertAlmostEqual(
            float(stats.iloc[0]["peak_velocity"]),
            float(np.nanmax(smooth)),
            places=10,
        )
        self.assertLess(float(stats.iloc[0]["peak_velocity"]), raw_peak)

    def test_scene_grouping_uses_raw_fallback_and_never_splits_a_segment(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as folder:
            path = Path(folder) / "scene_fallback_VR1.csv"
            pd.DataFrame({
                "Current Time": pd.date_range(
                    "2026-01-01", periods=9, freq="100ms"),
                "CurrentTrial": [1] * 9,
                "CurrentStep": [0] * 3 + [1] * 3 + [2] * 3,
                "GameObjectPosX": np.arange(9, dtype=float),
                "GameObjectPosZ": np.zeros(9),
                "ConfigFile": "cfg.json",
                # Sequence scene is the authoritative raw scene when present.
                "CurrentSequenceScene": (
                    [" seq_scene "] * 3 + [None] * 3 + ["wrong_seq"] * 3
                ),
                # Generic Scene is the fallback; one stale transition frame
                # must not split CurrentStep=1 across two scene panels.
                "Scene": (
                    ["wrong_scene"] * 3
                    + ["stale_scene", "scene_b", "scene_b"]
                    + ["wrong_scene"] * 3
                ),
                # A populated SceneName remains higher priority than raw
                # fallback columns, while blanks fall through.
                "SceneName": [None] * 6 + [" metadata_scene "] * 3,
            }).to_csv(path, index=False)

            frame = app.load_csv_fast(str(path))
            self.assertIsNotNone(frame)
            by_step = {
                int(step): set(sub["SceneName"].astype(str))
                for step, sub in frame.groupby(
                    "CurrentStep", sort=False, observed=True)
            }
            self.assertEqual(by_step[0], {"seq_scene"})
            self.assertEqual(by_step[1], {"scene_b"})
            self.assertEqual(by_step[2], {"metadata_scene"})
            self.assertTrue(
                frame.groupby("_seg_id", sort=False, observed=True)[
                    "SceneName"].nunique().eq(1).all()
            )
            groups = app._group_frames(
                frame, "scene", "separate", ncols=2)
            self.assertEqual(
                set(groups), {"seq_scene", "scene_b", "metadata_scene"})

    def test_streaming_loader_retains_a_bounded_frame_but_exact_point_counts(self):
        old_budget = app.LOAD_ROW_BUDGET
        try:
            with tempfile.TemporaryDirectory(dir="/tmp") as folder:
                for file_index in range(2):
                    n = 100
                    pd.DataFrame({
                        "Current Time": pd.date_range(
                            "2026-01-01", periods=n, freq="100ms"),
                        "CurrentTrial": file_index,
                        "CurrentStep": 0,
                        "GameObjectPosX": np.linspace(0, 10, n),
                        "GameObjectPosZ": np.zeros(n),
                        "ConfigFile": "Choice_empty.json",
                        "SceneName": "scene",
                        "FlyID": "1",
                        "GameObjectRotY": np.zeros(n),
                    }).to_csv(
                        Path(folder) / f"source_VR{file_index + 1}.csv",
                        index=False,
                    )
                app.LOAD_ROW_BUDGET = 40
                app._DATA_CACHE.clear()
                app._STATS_CACHE.clear()
                app._META_CACHE.clear()
                app._DATA_TOKEN_BY_PATTERN.clear()
                app._DATA_CACHE_ORDER.clear()
                frame, stats, _ = app._load_data(str(Path(folder) / "*.csv"))
                self.assertEqual(frame.attrs["_raw_rows"], 200)
                self.assertLess(len(frame), 200)
                self.assertLessEqual(len(frame), 44)
                self.assertEqual(int(stats["n_points"].sum()), 200)
                self.assertIn("distance_walked", stats)
                self.assertIn("median_local_tortuosity", stats)
                self.assertIn("_smoothed_velocity", frame)
                progress = app._progress_snapshot()
                self.assertFalse(progress["active"])
                self.assertEqual(
                    [stage["label"] for stage in progress["stages"]],
                    ["Detect files", "Load + preprocess",
                     "Combine retained rows", "Index + cache"],
                )
        finally:
            app.LOAD_ROW_BUDGET = old_budget
            app._DATA_CACHE.clear()
            app._STATS_CACHE.clear()
            app._META_CACHE.clear()
            app._DATA_TOKEN_BY_PATTERN.clear()
            app._DATA_CACHE_ORDER.clear()

    def test_missing_target_config_uses_modal_loaded_geometry(self):
        def config(x):
            return {"objects": [{"type": "tree01",
                                 "position": {"x": x, "z": 10},
                                 "scale": {"x": 1}}]}

        metas = [
            {"sequence_order": ["empty.json"],
             "configs": {"a.json": config(-3), "empty.json": {"objects": []}}},
            {"sequence_order": [], "configs": {"a2.json": config(-3)}},
            {"sequence_order": [], "configs": {"b.json": config(7)}},
        ]
        rois = app.rois_by_config(metas)
        self.assertIn("empty.json", rois)
        self.assertEqual(rois["empty.json"][0]["x"], -3)
        self.assertTrue(rois["empty.json"][0]["inferred"])

    def test_reach_radius_is_unbounded_in_exact_input_and_url(self):
        exact = _component("roi-reach")
        slider = _component("roi-reach-slider")
        self.assertIsNone(getattr(exact, "max", None))
        self.assertEqual(slider.max, 100)

        restored = app.restore_from_url("?reach=250.5", False)
        self.assertEqual(restored[-2], 250.5)
        self.assertIs(app.restore_from_url("?reach=-1", False)[-2], app.no_update)

        args = {name: None for name in inspect.signature(app.update_url).parameters}
        args.update(restored=True, reach=250.5, rrange=[0, 1], anim=[])
        params = parse_qs(app.update_url(**args).lstrip("?"))
        self.assertEqual(params["reach"], ["250.5"])

        original_ctx = app.ctx
        try:
            app.ctx = SimpleNamespace(triggered_id="roi-reach")
            exact_out, slider_out = app.sync_roi_reach_controls(250.5, 3)
            self.assertIs(exact_out, app.no_update)
            self.assertEqual(slider_out, 100)

            app.ctx = SimpleNamespace(triggered_id="roi-reach-slider")
            exact_out, slider_out = app.sync_roi_reach_controls(250.5, 80)
            self.assertEqual(exact_out, 80)
            self.assertIs(slider_out, app.no_update)
        finally:
            app.ctx = original_ctx

    def test_export_is_offline_capable_and_contains_every_section(self):
        fig = app.go.Figure(app.go.Scatter(x=[0, 1], y=[0, 1]))
        document = app._compose_export_html(
            fig, fig, fig, fig, fig, fig, fig, fig, fig, fig,
            include_raw=False, summary="test summary", share_state="?mode=speed",
        )
        self.assertNotIn('src="https://cdn.plot.ly', document)
        self.assertIn("plotly.js", document)
        for heading in ("Trajectories", "Heatmap", "Local direction field",
                        "Target diagnostics", "Polar",
                        "Trial metrics",
                        "Diagnostics: raw starting-heading null distribution",
                        "Velocity / Displacement", "Raw traces"):
            self.assertIn(f"<h3>{heading}</h3>", document)


if __name__ == "__main__":
    unittest.main()
