import math
import inspect
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode

import numpy as np
import pandas as pd

import app
import dashboard_callbacks


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
    def test_server_callback_signatures_match_registered_dependencies(self):
        for output, metadata in app.app.callback_map.items():
            callback = metadata.get("callback")
            if callback is None:
                continue
            function = getattr(callback, "__wrapped__", callback)
            signature = inspect.signature(function)
            if any(
                    parameter.kind in {
                        inspect.Parameter.VAR_POSITIONAL,
                        inspect.Parameter.VAR_KEYWORD,
                    }
                    for parameter in signature.parameters.values()):
                continue
            expected = len(metadata["inputs"]) + len(metadata["state"])
            self.assertEqual(
                len(signature.parameters), expected,
                f"callback dependency arity mismatch for {output}",
            )

    def test_speed_is_the_default_render_mode(self):
        self.assertEqual(app._render_mode(None), "speed")
        self.assertEqual(app._render_mode("speed"), "speed")
        self.assertEqual(app._render_mode("accuracy"), "accuracy")

    def test_expensive_optional_sections_are_off_by_default(self):
        self.assertEqual(_component("gandiva-enabled").value, [])
        self.assertEqual(_component("transition-enabled").value, [])
        self.assertEqual(_component("roi-show").value, [])
        ids = {getattr(node, "id", None) for node in _components(app.app.layout)}
        self.assertNotIn("show-raw-config", ids)

    def test_spatial_lattices_put_zero_at_a_cell_centre(self):
        frame = _polar_frame()
        xedges, yedges, _ = app._heatmap_edges(
            frame, bin_size=1.0, bound_pct=100)
        self.assertTrue(np.any(np.isclose(
            0.5 * (xedges[:-1] + xedges[1:]), 0.0)))
        self.assertTrue(np.any(np.isclose(
            0.5 * (yedges[:-1] + yedges[1:]), 0.0)))
        self.assertTrue(np.any(np.isclose(xedges, -0.5)))
        self.assertTrue(np.any(np.isclose(xedges, 0.5)))
        direction = app._direction_field_bins(
            frame, "all", "pooled", 1, 1.0, 100,
            "orientation", False, 0.0)
        self.assertTrue(np.any(np.isclose(direction["xc"], 0.0)))
        self.assertTrue(np.any(np.isclose(direction["yc"], 0.0)))

    def test_moving_only_blanks_trajectory_points_without_dropping_rows(self):
        frame = _polar_frame()
        masked = app.mask_stationary_trajectory_points(
            frame, moving_only=True, walk_thresh=1e9)
        self.assertEqual(len(masked), len(frame))
        self.assertTrue(masked["GameObjectPosX"].isna().all())
        self.assertTrue(masked["GameObjectPosZ"].isna().all())
        self.assertIs(
            app.mask_stationary_trajectory_points(frame, moving_only=False),
            frame,
        )

    def test_heading_time_panel_merges_trials_and_uses_circular_animal_means(self):
        rows = []
        for trial, angle in ((1, 170.0), (2, -170.0)):
            for sample in range(3):
                rows.append({
                    "_seg_id": f"file_T{trial}_S0",
                    "ConfigFile": "cfg.json", "SceneName": "scene",
                    "SourceFolder": "folder", "animal": "1@VR1",
                    "VR": "VR1", "FlyID": "1",
                    "CurrentTrial": trial, "CurrentStep": 0,
                    "SourceFile": "file.csv",
                    "Current Time": (
                        pd.Timestamp("2026-01-01")
                        + pd.Timedelta(sample, "s")),
                    "GameObjectPosX": float(sample),
                    "GameObjectPosZ": 0.0,
                    "GameObjectRotY": angle,
                })
        frame = pd.DataFrame(rows)
        frame.attrs["_frame_token"] = ("test", "heading-time")

        trials = app.build_heading_time_figure(
            frame, group_by="all", pool_mode="pooled", ncols=1,
            mode="trial", angle_source="orientation")
        self.assertEqual(len(trials.data), 1)
        self.assertIsNone(trials.layout.yaxis.range)
        self.assertEqual(
            tuple(trials.layout.yaxis.tickvals), (-180, -90, 0, 90, 180))
        self.assertEqual(
            set(row[6] for row in trials.data[0].customdata if row[6]),
            {"file_T1_S0", "file_T2_S0"},
        )

        animal = app.build_heading_time_figure(
            frame, group_by="all", pool_mode="pooled", ncols=1,
            mode="animal", angle_source="orientation",
            window_seconds=1, show_variability=True)
        mean_trace = next(
            trace for trace in animal.data
            if (trace.meta or {}).get("td_heading_animal_mean"))
        means = np.asarray(mean_trace.y, dtype=float)
        means = means[np.isfinite(means)]
        self.assertTrue(np.all(np.isclose(np.abs(means), 180.0)))
        spreads = np.asarray(mean_trace.customdata, dtype=object)[:, 1].astype(float)
        self.assertTrue(np.all(np.isfinite(spreads)))
        self.assertTrue(any(
            (trace.meta or {}).get("td_heading_variability")
            for trace in animal.data))
        self.assertEqual(animal.layout.meta["heading_time_mode"], "animal")
        self.assertEqual(animal.layout.meta["window_mode"], "custom")
        self.assertEqual(animal.layout.meta["time_bin_seconds"], 1)

        second_animal = frame.copy()
        second_animal["animal"] = "2@VR2"
        second_animal["FlyID"] = "2"
        second_animal["VR"] = "VR2"
        second_animal["_seg_id"] = "animal2_" + second_animal["_seg_id"]
        density_frame = pd.concat([frame, second_animal], ignore_index=True)
        density_frame.attrs["_frame_token"] = ("test", "heading-density")
        density = app.build_heading_time_figure(
            density_frame, group_by="all", pool_mode="pooled", ncols=1,
            representation="density", angle_bin_degrees=10)
        self.assertEqual(len(density.data), 2)
        self.assertTrue(all(trace.type == "heatmap" for trace in density.data))
        self.assertTrue(all(trace.showlegend for trace in density.data))
        self.assertEqual(
            {trace.name for trace in density.data}, {"1@VR1", "2@VR2"})
        self.assertTrue(all(
            trace.meta["td_heading_density"] for trace in density.data))
        self.assertEqual(density.layout.meta["heading_angle_bin_degrees"], 10)

    def test_distance_walked_range_filters_complete_segments(self):
        rows = []
        paths = {
            "short": [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)],
            "long": [(0.0, 0.0), (0.0, 8.0), (0.0, 16.0)],
        }
        for trial, (segment, points) in enumerate(paths.items(), start=1):
            for sample, (x, z) in enumerate(points):
                rows.append({
                    "_seg_id": segment, "ConfigFile": "cfg.json",
                    "SceneName": "scene", "SourceFolder": "folder",
                    "VR": "VR1", "FlyID": "1", "CurrentTrial": trial,
                    "CurrentStep": 0, "GameObjectPosX": x,
                    "GameObjectPosZ": z,
                    "Current Time": (
                        pd.Timestamp("2026-01-01")
                        + pd.Timedelta(sample, "s")),
                })
        frame = pd.DataFrame(rows)
        stats = app.compute_segment_stats(frame)
        result = dashboard_callbacks.td_grouping.filter_frame(
            frame,
            dashboard_callbacks.td_grouping.FilterSpec(
                distance_walked_range=(10.0, 20.0)),
            stats=stats,
        )
        self.assertEqual(set(result.filtered["_seg_id"]), {"long"})
        self.assertEqual(set(result.stats["seg_id"]), {"long"})

    def test_staged_core_keeps_completed_figures_mounted_until_replacement(self):
        source = inspect.getsource(app.update_plots)
        self.assertIn("Downstream figures intentionally remain mounted", source)
        self.assertNotIn("Queued — occupancy", source)
        self.assertIn('generation.get("reset_controls")', source)
        self.assertIn("walk_selection = None", source)

    def test_explicit_target_request_bypasses_a_settling_gandiva_store(self):
        frame = _polar_frame()
        segments = list(pd.unique(frame["_seg_id"]))
        target_table = pd.DataFrame({
            "_seg_id": segments,
            "reached_left": [True, False],
            "reached_right": [False, True],
        })
        rois = {"cfg.json": [
            {"x": -1.0, "z": 2.0, "side": "left"},
            {"x": 1.0, "z": 2.0, "side": "right"},
        ]}
        trajectory = app.go.Figure()
        trajectory.update_layout(
            shapes=[dict(type="line", x0=0, x1=1, y0=0, y1=1,
                         name="keep-me")],
            annotations=[dict(text="panel", x=0, y=0, name="keep-me")],
        )
        heatmap = app.go.Figure(trajectory).to_plotly_json()

        with (
            patch.object(app, "ctx", SimpleNamespace(triggered_id="roi-show")),
            patch.object(dashboard_callbacks, "_load_data",
                         return_value=(frame, None, [{}])),
            patch.object(
                dashboard_callbacks.td_grouping, "filter_frame",
                return_value=SimpleNamespace(
                    filtered=frame, subset=frame, stats=None,
                ),
            ),
            patch.object(dashboard_callbacks, "rois_by_config",
                         return_value=rois),
            patch.object(dashboard_callbacks, "_roi_apply",
                         return_value=(frame, target_table)),
            patch.object(dashboard_callbacks, "build_roi_swarm_figure",
                         return_value=app.go.Figure()),
        ):
            result = app.update_targets_stage(
                {}, ["on"], 3, "synthetic", None, 0, 0, 0,
                None, None, None, None, None, None, None, None, None,
                None, None, [], [], None, "config", "separate", 1, [],
                "categorical", "time", trajectory.to_plotly_json(),
                heatmap, None,
            )

        trajectory_out = result[0].to_plotly_json()
        shape_names = [
            shape.get("name")
            for shape in trajectory_out["layout"].get("shapes", [])
        ]
        self.assertIn("keep-me", shape_names)
        self.assertTrue(any(
            str(name).startswith("td-target-overlay")
            for name in shape_names
        ))
        self.assertTrue(result[4]["enabled"])
        self.assertIn("Targets ready", result[-1])

    def test_panel_order_save_is_delayed_and_does_not_rebuild_its_list(self):
        source = Path("assets/config_order.js").read_text()
        self.assertIn("}, 7000);", source)
        order_callback = next(
            meta for output, meta in app.app.callback_map.items()
            if "panel-order-list.children" in output
        )
        self.assertNotIn(
            "panel-order-store",
            {item["id"] for item in order_callback["inputs"]},
        )

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

    def test_animal_polar_pooling_gives_each_animal_one_population_vote(self):
        ray = pd.DataFrame({
            "_seg_id": ["a1", "a2", "b1"],
            "group": ["cfg"] * 3,
            "animal": ["fly-a", "fly-a", "fly-b"],
            "R": [1.0, 1.0, 1.0],
            "theta_deg": [0.0, 0.0, 180.0],
            "valid_points": [100, 100, 5],
            "n_points": [100, 100, 5],
            "cval": [1.0, 1.0, 2.0],
            "CurrentTrial": [1, 2, 1],
        })
        animals = app._polar_by_animal(ray)
        self.assertEqual(len(animals), 2)
        self.assertEqual(animals["unit_trials"].tolist(), [2, 1])
        rbar, _theta, support = app._population_polar_vector(
            animals, equal_units=True)
        self.assertEqual(support, 2)
        self.assertAlmostEqual(rbar, 0.0, places=10)

    def test_animal_polar_draws_one_radial_vector_per_animal(self):
        trial = app.build_polar_figure(
            _polar_frame(), group_by="all", pool_mode="pooled",
            color_by="none", stats_unit="trial")
        animal = app.build_polar_figure(
            _polar_frame(), group_by="all", pool_mode="pooled",
            color_by="none", stats_unit="animal")

        def independent_vectors(figure):
            traces = [
                trace for trace in figure.data
                if isinstance(getattr(trace, "meta", None), dict)
                and trace.meta.get("td_trial_source")
            ]
            return sum(
                int(np.isfinite(np.asarray(trace.r, dtype=float)).sum() / 2)
                for trace in traces
            )

        self.assertEqual(independent_vectors(trial), 2)
        self.assertEqual(independent_vectors(animal), 1)
        self.assertIn(
            "1/1 animals shown",
            " ".join(annotation.text for annotation in animal.layout.annotations),
        )

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
        self.assertEqual(fig.layout.meta["marginal_resolution_multiplier"], 4)
        self.assertEqual(len(fig.layout.meta["quadrant_cut"]), 2)
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
        sparse_grid = app.build_direction_field_figure(
            frame, group_by="all", pool_mode="pooled", ncols=2,
            bin_size=10.0, bound_pct=100.0, angle_source="orientation",
            metric="count", cmin=0, cmax=10)
        self.assertEqual(sparse_grid.layout.meta["spatial_axis_count"], 1)

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

    def test_transition_cells_count_each_trial_once_and_distinguish_outcomes(self):
        rows = []
        paths = {
            # Crosses the split, then returns to the original half.
            "returns": [-2.0, -1.5, 1.0, -1.0],
            # Crosses and ends in the opposite half.
            "ends": [-2.0, -1.5, 1.0, 2.0],
            # Revisits the source cell but never crosses.
            "stays": [-2.0, -1.5, -2.0, -1.5],
        }
        for segment, values in paths.items():
            for z in values:
                rows.append({
                    "_seg_id": segment,
                    "GameObjectPosX": 0.25,
                    "GameObjectPosZ": z,
                })
        frame = pd.DataFrame(rows)
        result = app._transition_group_probabilities(
            frame,
            np.array([-1.0, 0.0, 1.0]),
            np.array([-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]),
            split_z=0.0,
        )
        # Cell X=[0,1], Z=[-2,-1]: all three trials entered it, despite
        # repeated samples/revisits. Two ever crossed; only one ended across.
        self.assertEqual(int(result["entrants"][1, 1]), 3)
        self.assertEqual(int(result["crossed"][1, 1]), 2)
        self.assertEqual(int(result["ended"][1, 1]), 1)
        crossed = app._transition_variant([result], "crossed", min_trials=1)
        ended = app._transition_variant([result], "ended", min_trials=1)
        self.assertAlmostEqual(
            crossed["displays"]["fraction"]["z"][0][1][1], 200 / 3)
        self.assertAlmostEqual(
            ended["displays"]["fraction"]["z"][0][1][1], 100 / 3)
        self.assertEqual(
            crossed["displays"]["count"]["z"][0][1][1], 2)
        self.assertEqual(
            ended["displays"]["count"]["z"][0][1][1], 1)

    def test_transition_bundle_anchors_manual_split_and_has_local_displays(self):
        bundle = app.build_transition_probability_bundle(
            _polar_frame(), group_by="all", pool_mode="pooled", ncols=1,
            bin_size=1.0, bound_pct=100, split_z=0.0, min_trials=1)
        self.assertTrue(bundle["enabled"])
        self.assertEqual(set(bundle["variants"]), {"crossed", "ended"})
        self.assertEqual(bundle["split_z"], 0.0)
        self.assertEqual(bundle["split_source"], "manual split line")
        yedges = np.asarray(bundle["yedges"])
        ycentres = 0.5 * (yedges[:-1] + yedges[1:])
        self.assertTrue(np.any(np.isclose(ycentres, 0.0)))
        self.assertTrue(np.any(np.isclose(yedges, -0.5)))
        self.assertTrue(np.any(np.isclose(yedges, 0.5)))
        self.assertTrue(np.allclose(np.diff(yedges), 1.0))
        for outcome in ("crossed", "ended"):
            self.assertEqual(
                set(bundle["variants"][outcome]["displays"]),
                {"fraction", "count"},
            )
        figure = bundle["figure"]
        self.assertIsInstance(figure["data"][0]["z"], list)
        self.assertIsInstance(figure["data"][0]["customdata"], list)
        marginal_traces = [
            trace for trace in figure["data"]
            if (trace.get("meta") or {}).get("td_transition_marginal")
        ]
        self.assertEqual(len(marginal_traces), 2)
        self.assertEqual(
            {trace["meta"]["td_transition_marginal"]
             for trace in marginal_traces},
            {"x", "z"},
        )
        self.assertTrue(figure["layout"]["meta"]["transition_marginals"])
        for selected_outcome in bundle["variants"].values():
            for display in selected_outcome["displays"].values():
                self.assertEqual(len(display["x_marginal"]), 1)
                self.assertEqual(len(display["z_marginal"]), 1)
        self.assertEqual(
            figure["layout"]["meta"]["panel_order_values"], ["All Data"])
        narrower = app.build_transition_probability_bundle(
            _polar_frame(), group_by="all", pool_mode="pooled", ncols=1,
            bin_size=1.0, bound_pct=80, split_z=0.0, min_trials=1)
        self.assertNotEqual(bundle["signature"], narrower["signature"])

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
        forced_violin = app.build_trial_metrics_figure(
            stats(8), distribution_mode="violin")
        forced_swarm = app.build_trial_metrics_figure(
            stats(201), distribution_mode="swarm")
        self.assertEqual({trace.type for trace in forced_violin.data}, {"violin"})
        self.assertEqual({trace.type for trace in forced_swarm.data}, {"scatter"})

    def test_distribution_controls_are_global_and_animal_pooling_is_explicit(self):
        rows = []
        for config in ("a.json", "b.json"):
            for fly in ("1", "2"):
                for trial in range(3):
                    rows.append({
                        "seg_id": f"{config}-{fly}-{trial}",
                        "n_points": 10,
                        "distance_walked": trial + (10 if config == "b.json" else 0),
                        "displacement": trial + 1,
                        "median_local_tortuosity": 1 + trial / 10,
                        "median_velocity": 2 + trial,
                        "config": config,
                        "vr": "VR1",
                        "fly_id": fly,
                        "scene": "scene",
                        "source_folder": "folder",
                    })
        stats = pd.DataFrame(rows)
        animal = app._metric_stats_for_unit(
            stats, "config", "separate", "animal")
        self.assertEqual(len(animal), 4)
        fig = app.build_trial_metrics_figure(
            stats, group_by="config", distribution_mode="violin",
            stats_unit="animal")
        self.assertEqual({trace.type for trace in fig.data}, {"violin"})
        self.assertTrue(all(len(trace.y) == 2 for trace in fig.data))

    def test_delayed_statistics_payload_covers_all_three_views(self):
        rows = []
        for group_index, config in enumerate(("cfg_a.json", "cfg_b.json")):
            for trial in range(12):
                base = float(trial + group_index * 50)
                rows.append({
                    "seg_id": f"{config}_{trial}",
                    "config": config,
                    "n_points": 30,
                    "distance_walked": base + 1,
                    "displacement": base + 2,
                    "median_local_tortuosity": base + 3,
                    "median_velocity": base + 4,
                })
        metric_stats = pd.DataFrame(rows)
        first = _polar_frame().copy()
        first["ConfigFile"] = "cfg_a.json"
        second = _polar_frame().copy()
        second["_seg_id"] = second["_seg_id"].astype(str) + "_b"
        second["SourceFile"] = second["SourceFile"].astype(str) + "_b"
        second["ConfigFile"] = "cfg_b.json"
        second["GameObjectRotY"] = (
            pd.to_numeric(second["GameObjectRotY"]) + 150.0) % 360.0
        circular = pd.concat([first, second], ignore_index=True)
        circular.attrs["_frame_token"] = ("test", "delayed-stats")
        payload = app._statistics_payload(
            metric_stats, circular, circular, "config", "separate",
            [], 1, "orientation", [0, 1], 0, 0)
        self.assertFalse(payload["pending"])
        self.assertEqual(len(payload["metric_labels"]), 4)
        self.assertEqual(len(payload["metric_marks"]), 4)
        self.assertEqual(
            [mark["group"] for mark in payload["metric_marks"][0]],
            ["cfg_a.json", "cfg_b.json"],
        )
        self.assertTrue(all(
            mark["letters"] for mark in payload["metric_marks"][0]))
        self.assertTrue(all("p=" in label for label in payload["metric_labels"]))
        self.assertIn("circular", payload["polar_label"])
        self.assertEqual(
            [mark["group"] for mark in payload["polar_marks"]],
            ["cfg_a.json", "cfg_b.json"],
        )
        self.assertTrue(all(
            "rayleigh_stars" in mark for mark in payload["polar_marks"]))
        self.assertIn("Rayleigh uniformity", payload["start_label"])

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

    def test_trajectory_trial_sampling_keeps_complete_segments(self):
        frame = pd.concat([
            _polar_frame().assign(
                _seg_id=lambda d, i=i: d["_seg_id"] + f"_copy{i}")
            for i in range(5)
        ], ignore_index=True)
        original_sizes = frame.groupby("_seg_id", sort=False).size()

        sampled = app._sample_trajectory_segments(frame, 30, seed=7)
        repeated = app._sample_trajectory_segments(frame, 30, seed=7)
        alternate = app._sample_trajectory_segments(frame, 30, seed=8)

        # Ten source segments × 30% = three complete displayed segments.
        self.assertEqual(sampled["_seg_id"].nunique(), 3)
        pd.testing.assert_series_equal(
            sampled.groupby("_seg_id", sort=False).size(),
            original_sizes.loc[pd.unique(sampled["_seg_id"])],
        )
        self.assertEqual(
            pd.unique(sampled["_seg_id"]).tolist(),
            pd.unique(repeated["_seg_id"]).tolist(),
        )
        self.assertNotEqual(
            set(pd.unique(sampled["_seg_id"])),
            set(pd.unique(alternate["_seg_id"])),
        )
        self.assertIs(app._sample_trajectory_segments(frame, 100, seed=99), frame)

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
        self.assertNotIn("roi-show", auto_inputs)
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
            ("spatial-render-state.data", app.update_spatial_grid),
            ("transition-data-store.data",
             app.update_transition_probability),
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

    def test_transition_controls_do_not_arm_or_enter_the_master_renderer(self):
        master = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..trajectory-plot.figure...")
        )
        master_inputs = {item["id"] for item in master["inputs"]}
        auto = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..auto-replot-state.data...")
        )
        auto_inputs = {item["id"] for item in auto["inputs"]}
        transition = app.app.callback_map["transition-data-store.data"]
        transition_inputs = {item["id"] for item in transition["inputs"]}

        for control in (
                "transition-enabled", "transition-outcome",
                "transition-metric", "transition-split-z",
                "transition-min-trials"):
            self.assertNotIn(control, master_inputs)
            self.assertNotIn(control, auto_inputs)
        self.assertEqual(
            transition_inputs,
            {
                "targets-render-state", "transition-enabled",
                "transition-split-z", "transition-min-trials",
            },
        )
        self.assertNotIn("transition-outcome", transition_inputs)
        self.assertNotIn("transition-metric", transition_inputs)
        update_url = app.app.callback_map["url.search"]
        update_ids = [item["id"] for item in update_url["inputs"]]
        self.assertEqual(len(update_ids), len(set(update_ids)))

    def test_expensive_controls_follow_the_declared_update_scope(self):
        auto = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..auto-replot-state.data...")
        )
        auto_inputs = {item["id"] for item in auto["inputs"]}
        for control in (
                "color-by", "render-mode", "animate-toggle", "plot-points",
                "heatmap-binsize", "heatmap-bound"):
            self.assertNotIn(control, auto_inputs)

        drawing = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..trajectory-plot.figure@")
            and {"color-by", "render-mode", "animate-toggle", "plot-points"}
            <= {item["id"] for item in meta["inputs"]}
        )
        self.assertEqual(
            {item["id"] for item in drawing["inputs"]},
            app.td_ui.controls_for_scope(
                app.td_ui.UpdateScope.TRAJECTORY_POLAR),
        )

        spatial = next(
            meta for output, meta in app.app.callback_map.items()
            if "spatial-render-state.data" in output
        )
        self.assertEqual(
            {item["id"] for item in spatial["inputs"]}
            - {"view-render-state"},
            app.td_ui.controls_for_scope(app.td_ui.UpdateScope.SPATIAL_GRID),
        )

    def test_load_initialisation_cannot_arm_a_duplicate_full_render(self):
        result = app.arm_auto_replot(
            {"loaded": 100.0}, "dataset", 1, {"completed": 99.0}
        )
        self.assertIs(result[0], app.no_update)
        self.assertTrue(result[1])
        self.assertEqual(result[2], 0)
        self.assertIs(result[3], app.no_update)

        auto = next(
            meta for output, meta in app.app.callback_map.items()
            if output.startswith("..auto-replot-state.data...")
        )
        self.assertIn("view-render-state", {item["id"] for item in auto["state"]})

    def test_progress_interval_has_one_enabler_and_one_poller(self):
        interval = next(
            child for child in app.app.layout.children
            if getattr(child, "id", None) == "load-progress-interval"
        )
        self.assertTrue(interval.disabled)
        self.assertEqual(interval.interval, 2000)
        self.assertEqual(interval.max_intervals, 10)

        owners = [
            meta for output, meta in app.app.callback_map.items()
            if "load-progress-interval.disabled" in output
        ]
        self.assertEqual(len(owners), 2)
        input_sets = [{item["id"] for item in meta["inputs"]} for meta in owners]
        self.assertIn({"load-progress-interval"}, input_sets)
        enabler = next(inputs for inputs in input_sets
                       if "load-progress-interval" not in inputs)
        self.assertIn("btn-load", enabler)
        self.assertIn("btn-plot", enabler)
        self.assertIn("heatmap-binsize", enabler)
        self.assertIn("color-by", enabler)

    def test_auto_panel_layout_handles_sparse_and_dense_group_counts(self):
        resolve = app.td_ui.resolve_panel_columns
        self.assertEqual(resolve(0, 1), 1)
        self.assertEqual(resolve(None, 4), 2)
        self.assertEqual(resolve(0, 5), 3)
        self.assertEqual(resolve(0, 26), 4)
        self.assertEqual(resolve(2, 26), 2)
        self.assertLess(
            app.td_ui.subplot_pixel_height(7, 4),
            app.td_ui.subplot_pixel_height(13, 2),
        )

    def test_legacy_name_editor_patches_without_clicking_master_renderer(self):
        lut = next(
            meta for output, meta in app.app.callback_map.items()
            if "lut-status.children" in output
        )
        output_key = next(
            output for output, meta in app.app.callback_map.items()
            if meta is lut
        )
        self.assertIn("visual-style-diff-store.data", output_key)
        self.assertNotIn("btn-plot.n_clicks", output_key)

    def test_region_geometry_arms_idle_request_not_analysis_directly(self):
        region = next(
            meta for _output, meta in app.app.callback_map.items()
            if any(
                item["id"] == "custom-region-analysis-request"
                for item in meta["inputs"]
            )
        )
        region_inputs = {item["id"] for item in region["inputs"]}
        self.assertIn("custom-region-analysis-request", region_inputs)
        self.assertNotIn("custom-regions-store", region_inputs)
        self.assertNotIn("custom-region-analysis-interval", region_inputs)
        self.assertIsNotNone(_component("custom-region-debounce-state"))

    def test_sections_follow_analysis_then_diagnostics_order(self):
        ids = [getattr(node, "id", None) for node in _components(app.app.layout)]
        self.assertIn("flow-field-legend", ids)
        positions = [ids.index(component_id) for component_id in (
            "view-traj", "view-heat", "view-transition", "view-flow",
            "view-polar", "view-heading", "view-roi", "view-metrics",
            "view-diag")]
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
        self.assertIn("spatial-render-state", fast_inputs)
        self.assertIn("polar-moving", fast_inputs)
        self.assertNotIn("flow-max-radius", fast_inputs)
        fast_states = {item["id"] for item in fast["state"]}
        self.assertIn("flow-max-radius", fast_states)
        self.assertIn("polar-min-animal-frac", fast_inputs)
        for control in ("heatmap-metric", "heatmap-scale", "heatmap-cmin",
                        "heatmap-cmax", "heatmap-crange"):
            self.assertNotIn(control, fast_inputs)
        gandiva = next(
            meta for output, meta in app.app.callback_map.items()
            if "gandiva-render-state.data" in output
        )
        gandiva_inputs = {item["id"] for item in gandiva["inputs"]}
        for control in ("heatmap-metric", "heatmap-scale", "heatmap-cmin",
                        "heatmap-cmax", "heatmap-crange"):
            self.assertIn(control, gandiva_inputs)

        heading = next(
            meta for output, meta in app.app.callback_map.items()
            if "heading-time-render-state.data" in output
        )
        self.assertEqual(
            {item["id"] for item in heading["inputs"]},
            {
                "polar-render-state", "heading-time-enabled",
                "heading-time-mode", "heading-time-representation",
                "heading-time-window", "heading-time-variability",
                "heading-time-angle-bin",
            },
        )
        self.assertNotIn("heading-time-enabled", master_inputs)

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

    def test_clean_layout_is_css_only_and_cannot_emit_relayout_events(self):
        source = Path("assets/clean_layout.js").read_text()
        self.assertIn('classList.toggle("td-clean-layout"', source)
        self.assertIn("td-clean-scale-overlay", source)
        self.assertNotIn("window.Plotly", source)
        self.assertNotIn('.on("plotly_relayout"', source)
        self.assertNotIn(".relayout(", source)
        self.assertNotIn(".restyle(", source)

    def test_trial_subset_keeps_an_immutable_source_for_fraction_increases(self):
        source = Path("assets/trial_subset.js").read_text()
        self.assertIn("var baseFigures = {}", source)
        self.assertIn("sourceFigure(", source)
        self.assertIn("finiteCoordinateCount", source)
        self.assertNotIn("finite >= cached.finite", source)
        self.assertIn("renderVersion", source)
        self.assertIn("coordinateGroups", source)

    def test_observation_statistics_only_compare_paired_regions_within_panel(self):
        def summary(values):
            return {
                "segment_values": {
                    "seg_id": ["u1", "u2", "u3"],
                    "n_points": [10, 10, 10],
                    "distance_walked": values,
                    "fly_id": ["f1", "f2", "f3"],
                    "vr": ["VR1", "VR1", "VR1"],
                    "source_folder": ["folder"] * 3,
                },
            }

        payload = {
            "regions": [
                {"id": "r1", "name": "Region 1"},
                {"id": "r2", "name": "Region 2"},
            ],
            "panels": [
                {
                    "raw": "a", "name": "Panel A",
                    "regions": [
                        {"id": "r1", **summary([1, 2, 3])},
                        {"id": "r2", **summary([3, 4, 5])},
                    ],
                },
                {
                    "raw": "b", "name": "Panel B",
                    "regions": [
                        {"id": "r1", **summary([10, 11, 12])},
                        {"id": "r2", **summary([8, 9, 10])},
                    ],
                },
            ],
        }
        comparisons = app._paired_custom_region_tests(
            payload, "distance_walked", "trial")
        self.assertEqual(len(comparisons), 2)
        self.assertEqual({item["group"] for item in comparisons}, {"a", "b"})
        self.assertTrue(all(
            (item["left"], item["right"]) == ("r1", "r2")
            for item in comparisons
        ))
        self.assertTrue(all(item["n"] == 3 for item in comparisons))

    def test_observation_pairing_lines_never_bridge_separate_region_pairs(self):
        def summary(values):
            return {
                "segment_values": {
                    "seg_id": ["u1", "u2"],
                    "n_points": [10, 10],
                    "distance_walked": values,
                    "fly_id": ["f1", "f2"],
                    "vr": ["VR1", "VR1"],
                    "source_folder": ["folder", "folder"],
                },
            }

        regions = [
            {"id": f"r{index}", "name": f"Region {index}"}
            for index in range(1, 5)
        ]
        payload = {
            "regions": regions,
            "panels": [{
                "raw": "a", "name": "Panel A",
                "regions": [
                    {"id": region["id"], **summary([index, index + 1])}
                    for index, region in enumerate(regions, start=1)
                ],
            }],
        }
        figure = app.build_custom_region_diagnostics_figure(
            payload, distribution_mode="swarm")
        pairing = [
            trace for trace in figure.data
            if isinstance(trace.meta, dict) and trace.meta.get("td_pairing")
        ]
        self.assertEqual(len(pairing), 1)
        runs, current = [], []
        for value in pairing[0].x:
            if value is None:
                if current:
                    runs.append(current)
                    current = []
            else:
                current.append(value)
        if current:
            runs.append(current)
        self.assertEqual(len(runs), 4)
        self.assertTrue(all(len(run) == 2 for run in runs))

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
        self.assertEqual(_component("traj-trial-fraction").value, 100)
        self.assertEqual(_component("loop-radius").value, 3)
        self.assertEqual(_component("loop-match-mode").value, "any")
        self.assertEqual(len(_component("loop-rings-store").data), 1)
        color_control = _component("color-by")
        self.assertEqual(color_control.value, "categorical")
        self.assertEqual(
            [option["value"] for option in color_control.options],
            [
                "categorical", "none", "individual", "config", "scene", "vr",
                "folder", "roi", "trial", "local_time", "velocity",
                "tortuosity",
            ],
        )
        self.assertFalse(_component("minimal-layout-store").data)
        self.assertEqual(_component("distribution-mode").value, "auto")
        self.assertEqual(_component("distribution-show-points").value, ["on"])
        self.assertEqual(_component("stats-unit").value, "trial")
        self.assertEqual(_component("observation-paired-lines").value, ["on"])
        self.assertEqual(_component("spatial-unit-scale").value, 1)
        self.assertEqual(_component("spatial-unit-label").value, "cm")
        self.assertEqual(_component("transition-enabled").value, [])
        self.assertEqual(_component("transition-outcome").value, "crossed")
        self.assertEqual(_component("transition-metric").value, "fraction")
        self.assertIsNone(_component("transition-count-min").value)
        self.assertIsNone(_component("transition-count-max").value)
        self.assertIsNone(_component("transition-split-z").value)
        self.assertEqual(_component("transition-min-trials").value, 3)
        self.assertEqual(_component("heading-time-enabled").value, [])
        self.assertEqual(_component("heading-time-mode").value, "trial")
        self.assertEqual(_component("heading-time-representation").value, "traces")
        self.assertIsNone(_component("heading-time-window").value)
        self.assertEqual(_component("heading-time-window-slider").value, -1)
        self.assertEqual(_component("heading-time-variability").value, [])
        self.assertEqual(_component("heading-time-angle-bin").value, 5)
        self.assertIsNotNone(_component("heading-time-plot"))
        self.assertIsNotNone(_component("disp-range-min"))
        self.assertIsNotNone(_component("disp-range-max"))
        self.assertIsNotNone(_component("walk-range-min"))
        self.assertIsNotNone(_component("walk-range-max"))
        self.assertEqual(len(_component("custom-regions-store").data), 1)
        self.assertIsNotNone(_component("loop-observer-plot"))
        self.assertIsNotNone(_component("custom-region-diagnostics-plot"))
        self.assertIsNotNone(_component("step-range"))
        restored = app.restore_from_url(
            "?smin=2&smax=4&hcrange=percentile&frad=0.31"
            "&tf=37&loop=1&lx=-4.5&lz=2&lr=7"
            "&uscale=0.1&ulabel=mm&trans=1&tmode=ended"
            "&tmetric=count&tcmin=2&tcmax=18&tsplit=-2.5&trnmin=7"
            "&view=transition&minimal=1&pairs=0"
            "&heading=1&htmode=animal&htview=density&htwin=2.5"
            "&htvar=1&htbin=10&wrmin=4&wrmax=40", False)
        self.assertEqual(len(restored), 79)
        self.assertEqual(restored[24:26], (2, 4))
        self.assertEqual(restored[34], [4.0, 40.0])
        self.assertEqual(restored[37], [2.0, 4.0])
        self.assertEqual(
            restored[41:47],
            (["on"], "animal", "density", 2.5, ["on"], 10))
        self.assertEqual(restored[51], 0.31)
        self.assertEqual(restored[53:58], (37.0, ["on"], -4.5, 2, 7))
        self.assertEqual(restored[67:70], ([], 0.1, "mm"))
        self.assertEqual(restored[48], "transition")
        self.assertEqual(
            restored[70:77],
            (["on"], "ended", "count", 2, 18, -2.5, 7),
        )
        self.assertTrue(restored[77])
        self.assertEqual(len(app.restore_from_url("", True)), 79)
        legacy_color = app.restore_from_url("?color=one", False)
        self.assertEqual(legacy_color[7], "categorical")
        rings = [
            {"id": "ring-1", "name": "Gate A",
             "x": 0, "z": 1, "radius": 2},
            {"id": "ring-2", "name": "Gate B",
             "x": 4, "z": 5, "radius": 6},
        ]
        ring_restore = app.restore_from_url(
            "?" + urlencode({
                "loops": app.json.dumps(rings),
                "lactive": "ring-2",
                "lmode": "all",
            }),
            False,
        )
        self.assertEqual(ring_restore[58], rings)
        self.assertEqual(ring_restore[59:61], ("ring-2", "all"))
        regions = [
            {"id": "region-1", "name": "Near",
             "x0": -2, "x1": 2, "z0": -1, "z1": 4},
            {"id": "region-2", "name": "Far",
             "x0": 5, "x1": 9, "z0": 6, "z1": 11},
        ]
        region_restore = app.restore_from_url(
            "?" + urlencode({
                "region": "1",
                "regions": app.json.dumps(regions),
                "ractive": "region-2",
            }),
            False,
        )
        self.assertEqual(region_restore[61], ["on"])
        self.assertEqual(region_restore[62], regions)
        self.assertEqual(region_restore[63], "region-2")
        distribution_restore = app.restore_from_url(
            "?dist=violin&dpts=0&sunit=animal", False)
        self.assertEqual(
            distribution_restore[64:67], ("violin", [], "animal"))
        value_restore = app.restore_from_url(
            "?hcmin=150&hcmax=200&hcrange=value", False)
        self.assertEqual(value_restore[35], [150.0, 200.0])

        exact_velocity = app.restore_from_url(
            "?vrmin=2.5&vrmax=250&layout=compare", False)
        self.assertEqual(exact_velocity[31:33], (2.5, 250))
        self.assertEqual(exact_velocity[49], "compare")

        url_args = {
            name: None for name in inspect.signature(app.update_url).parameters
        }
        url_args.update(
            restored=True, rrange=[0, 1], anim=[],
            heading_time_enabled=["on"], heading_time_mode="animal",
            heading_time_representation="density", heading_time_window=2.5,
            heading_time_variability=["on"], heading_time_angle_bin=10,
            wrange=[4, 40],
        )
        heading_params = parse_qs(app.update_url(**url_args).lstrip("?"))
        self.assertEqual(heading_params["heading"], ["1"])
        self.assertEqual(heading_params["htmode"], ["animal"])
        self.assertEqual(heading_params["htview"], ["density"])
        self.assertEqual(heading_params["htwin"], ["2.5"])
        self.assertEqual(heading_params["htvar"], ["1"])
        self.assertEqual(heading_params["htbin"], ["10"])
        self.assertEqual(heading_params["wrmin"], ["4.0"])
        self.assertEqual(heading_params["wrmax"], ["40.0"])

    def test_server_preload_preserves_shared_url_settings(self):
        source = Path("app.py").read_text()
        main = source[source.index('if __name__ == "__main__":'):]
        self.assertNotIn("child.search =", main)
        self.assertIn("component.value = args.glob", main)

    def test_long_horizontal_legends_reserve_plot_height(self):
        short_top, short_extra = app._horizontal_legend_layout(["VR1"], 2)
        long_top, long_extra = app._horizontal_legend_layout(
            [f"VR{i} fly with a long label" for i in range(24)], 2)
        self.assertEqual(short_extra, 0)
        self.assertGreater(long_extra, short_extra)
        self.assertGreater(long_top, short_top)

    def test_visual_style_prefills_every_panel_group_and_muted_defaults(self):
        frame = _polar_frame().copy()
        frame["SceneName"] = "forest"
        frame["SourceFolder"] = "session-a"
        payload = app._visual_style_payload(frame)
        labels = payload["group_labels"]
        self.assertIn("cfg.json", labels["config"])
        self.assertIn("forest", labels["scene"])
        self.assertIn("VR1", labels["vr"])
        self.assertIn("1", labels["flyid"])
        self.assertIn("session-a", labels["file"])
        self.assertLess(payload["trajectory"]["opacity"], 0.7)
        self.assertLess(payload["trajectory"]["gray_opacity"],
                        payload["trajectory"]["opacity"])
        self.assertEqual(payload["spatial_layout"]["unit_label"], "cm")

    def test_custom_regions_subset_points_and_report_panel_percentages(self):
        frame = _polar_frame().copy()
        frame["SceneName"] = "forest"
        frame["SourceFolder"] = "session-a"
        region = [{
            "id": "region-1", "name": "Start",
            "x0": -0.1, "x1": 1.1, "z0": -1, "z1": 1,
        }]
        subset = app._custom_region_subset(frame, region)
        self.assertEqual(len(subset), 4)
        self.assertTrue(set(subset["_seg_id"]) == set(frame["_seg_id"]))
        payload = app._custom_region_stats(
            frame, region, group_by="vr", pool_mode="separate", ncols=2)
        self.assertEqual(payload["regions"][0]["samples"], 4)
        self.assertEqual(payload["regions"][0]["trials"], 2)
        self.assertAlmostEqual(
            payload["panels"][0]["regions"][0]["percent"],
            100 * 4 / len(frame),
        )
        values = payload["panels"][0]["regions"][0]["segment_values"]
        self.assertEqual(len(values["sample_percent"]), 2)
        self.assertTrue(all(0 <= value <= 100
                            for value in values["sample_percent"]))
        diagnostic = app.build_custom_region_diagnostics_figure(
            payload, distribution_mode="violin")
        self.assertEqual({trace.type for trace in diagnostic.data}, {"violin"})

    def test_observation_time_pooling_and_entry_counts_use_animal_denominators(self):
        summary = {
            "segment_values": {
                "seg_id": ["a1", "a2", "b1"],
                "n_points": [10, 30, 5],
                "fly_id": ["a", "a", "b"],
                "vr": ["VR1", "VR1", "VR1"],
                "source_folder": ["folder"] * 3,
                "inside_seconds": [1.0, 3.0, 2.0],
                "total_seconds": [10.0, 30.0, 5.0],
                "time_percent": [10.0, 10.0, 40.0],
                "entered": [1, 0, 1],
            },
        }
        time_values, time_ids, time_denominators = (
            app._observation_distribution_values(
                summary, "time_percent", "animal")
        )
        self.assertEqual(time_ids.tolist(), ["a@VR1", "b@VR1"])
        np.testing.assert_allclose(time_values, [10.0, 40.0])
        np.testing.assert_allclose(time_denominators, [40.0, 5.0])

        entries, animal_ids, trial_effort = (
            app._observation_distribution_values(
                summary, "trial_count", "trial")
        )
        self.assertEqual(animal_ids.tolist(), ["a@VR1", "b@VR1"])
        np.testing.assert_allclose(entries, [1.0, 1.0])
        np.testing.assert_allclose(trial_effort, [2.0, 1.0])

    def test_metric_axes_and_values_follow_spatial_unit_configuration(self):
        stats = pd.DataFrame({
            "seg_id": ["s1"],
            "n_points": [10],
            "distance_walked": [20.0],
            "displacement": [5.0],
            "median_local_tortuosity": [1.2],
            "median_velocity": [4.0],
            "config": ["cfg.json"],
            "vr": ["VR1"],
            "fly_id": ["1"],
            "scene": ["scene"],
            "source_folder": ["folder"],
        })
        figure = app.build_trial_metrics_figure(
            stats, spatial_unit_scale=0.1, spatial_unit_label="m")
        self.assertAlmostEqual(float(figure.data[0].y[0]), 2.0)
        self.assertAlmostEqual(float(figure.data[1].y[0]), 0.5)
        self.assertAlmostEqual(float(figure.data[3].y[0]), 0.4)
        self.assertEqual(figure.layout.yaxis.title.text, "Path length (m)")
        self.assertEqual(figure.layout.yaxis4.title.text, "Smoothed speed (m/s)")

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

    def test_panel_order_follows_the_active_grouping(self):
        frame = _polar_frame().copy()
        frame["SceneName"] = np.where(
            frame["_seg_id"].str.contains("file_a"), "scene_a", "scene_b")
        previous = {
            key: dict(value)
            for key, value in app._USER_GROUP_ORDERS.items()
        }
        try:
            app._USER_GROUP_ORDERS["scene"] = {
                "scene_b": 0,
                "scene_a": 1,
            }
            groups = app._group_frames(
                frame, "scene", "separate", ncols=2)
            self.assertEqual(list(groups), ["scene_b", "scene_a"])
            summary, children = app.render_panel_order_list(
                "scene", "separate",
                [], [], [],
                [
                    {"label": "scene_a", "value": "scene_a"},
                    {"label": "scene_b", "value": "scene_b"},
                ],
                [],
                [], [], [], [], [],
                {},
            )
            self.assertEqual(summary, "Plot order · Scene")
            self.assertEqual(
                [item.to_plotly_json()["props"]["data-order-value"]
                 for item in children],
                ["scene_b", "scene_a"],
            )
        finally:
            app._USER_GROUP_ORDERS.clear()
            app._USER_GROUP_ORDERS.update(previous)

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
        self.assertEqual(restored[52], 250.5)
        self.assertIs(app.restore_from_url("?reach=-1", False)[52], app.no_update)

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
        transition = app.build_transition_probability_bundle(
            _polar_frame(), group_by="all", pool_mode="pooled", ncols=1,
            bin_size=1, bound_pct=100, split_z=0.5, min_trials=1)
        document = app._compose_export_html(
            fig, fig, fig, fig, fig, fig, fig, fig, fig, fig,
            include_raw=False, summary="test summary", share_state="?mode=speed",
            transition_bundle=transition,
            transition_metric="count",
        )
        self.assertNotIn('src="https://cdn.plot.ly', document)
        self.assertIn("plotly.js", document)
        self.assertIn("TrajectoryLoopObserver", document)
        self.assertIn("TransitionProbabilityObserver", document)
        self.assertIn("Heading over time", document)
        self.assertIn('id="export-loop-radius"', document)
        self.assertIn('id="export-transition-metric"', document)
        self.assertNotIn('id="export-transition-observer"', document)
        node = shutil.which("node")
        if node:
            loop_scripts = [
                body for body in re.findall(
                    r"<script[^>]*>(.*?)</script>", document, re.DOTALL
                )
                if "TrajectoryLoopObserver" in body
            ]
            self.assertEqual(len(loop_scripts), 2)
            for script in loop_scripts:
                result = subprocess.run(
                    [node, "--check"], input=script, text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        for heading in ("Trajectories", "Loop observer", "Heatmap",
                        "Transition probability", "Gandiva plot",
                        "Target diagnostics", "Polar",
                        "Trial metrics",
                        "Diagnostics: raw starting-heading null distribution",
                        "Velocity / Displacement", "Raw traces"):
            self.assertIn(f"<h3>{heading}</h3>", document)


if __name__ == "__main__":
    unittest.main()
