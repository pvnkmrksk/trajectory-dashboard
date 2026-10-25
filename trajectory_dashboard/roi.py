"""Dash-free target/ROI geometry extracted from Unity scene configs."""

from __future__ import annotations

import math


def roi_xz(radius: float, angle_deg: float) -> tuple[float, float]:
    """Convert Unity left-handed ground-plane polar coordinates to X/Z."""

    angle = math.radians(angle_deg)
    return radius * math.sin(angle), radius * math.cos(angle)


def rois_from_config(config: dict) -> list[dict]:
    """Extract polar or cartesian target objects from one scene config."""

    result: list[dict] = []
    objects = config.get("objects", []) if isinstance(config, dict) else []
    for item in objects:
        position = item.get("position") or {}
        if position.get("radius") is not None and position.get("angle") is not None:
            radius = float(position["radius"])
            angle = float(position["angle"])
            if radius <= 0:
                continue
            x, z = roi_xz(radius, angle)
        elif position.get("x") is not None and position.get("z") is not None:
            x = float(position["x"])
            z = float(position["z"])
            radius = math.hypot(x, z)
            angle = math.degrees(math.atan2(x, z))
        else:
            continue
        scale = item.get("scale") or {}
        side = "left" if x < -1e-6 else "right" if x > 1e-6 else "centre"
        result.append({
            "x": x,
            "z": z,
            "angle": angle,
            "r": radius,
            "scale": abs(float(scale.get("x", 1) or 1)),
            "type": item.get("type", "object"),
            "side": side,
        })
    return result


def _short_config_name(filename: str) -> str:
    return (
        filename.split("_ControlScene_")[-1]
        if "_ControlScene_" in filename else filename
    )


def _geometry_signature(rois: list[dict]) -> tuple:
    return tuple(sorted(
        (
            round(float(roi.get("x", 0.0)), 4),
            round(float(roi.get("z", 0.0)), 4),
            str(roi.get("side", "")),
            str(roi.get("type", "")),
        )
        for roi in rois
    ))


def rois_by_config(metadata: list[dict], *, strict: bool = False) -> dict[str, list[dict]]:
    """Map short ConfigFile names to target geometry, inferring empty configs.

    Strict mode refuses malformed/nonfinite targets and conflicting actual
    geometries for one short config name. The browser identifies targets by
    that name, so choosing the first folder silently would misclassify reaches.
    Empty configs retain the established modal-target inference policy.
    """

    result: dict[str, list[dict]] = {}
    all_keys: list[str] = []
    geometry_samples: list[list[dict]] = []
    geometry_sources: dict[str, tuple[tuple, str]] = {}
    for folder in metadata or []:
        all_keys.extend(
            _short_config_name(value)
            for value in (folder.get("sequence_order") or [])
        )
        for filename, config in (folder.get("configs") or {}).items():
            key = _short_config_name(filename)
            all_keys.append(key)
            folder_path = str(folder.get("folder") or "<unknown folder>")
            try:
                if strict and isinstance(config, dict):
                    for item in config.get("objects", []):
                        position = item.get("position") or {}
                        for field in ("x", "z", "radius", "angle"):
                            if position.get(field) is not None and not math.isfinite(float(position[field])):
                                raise ValueError(f"Nonfinite target {field}")
                rois = rois_from_config(config)
            except (ValueError, TypeError, OverflowError, AttributeError) as exc:
                if not strict:
                    raise
                raise ValueError(
                    f"Invalid target geometry in config '{key}' in folder "
                    f"'{folder_path}': target positions and scales must be "
                    "finite numbers. Correct the scene config and reload."
                ) from exc
            if strict and any(
                not math.isfinite(float(roi[field]))
                for roi in rois
                for field in ("x", "z", "angle", "r", "scale")
            ):
                raise ValueError(
                    f"Invalid target geometry in config '{key}' in folder "
                    f"'{folder_path}': target positions and scales must be "
                    "finite numbers. Correct the scene config and reload."
                )
            if rois:
                if strict:
                    signature = _geometry_signature(rois)
                    previous = geometry_sources.get(key)
                    if previous is not None and previous[0] != signature:
                        raise ValueError(
                            f"Conflicting target geometry for config '{key}' "
                            f"in folders '{previous[1]}' and '{folder_path}'. "
                            "Use distinct config names in the scene sequences "
                            "when target positions differ, or load these "
                            "folders separately."
                        )
                    geometry_sources.setdefault(key, (signature, folder_path))
                geometry_samples.append(rois)
                result.setdefault(key, rois)
    signatures: dict[tuple, tuple[int, list[dict]]] = {}
    for rois in geometry_samples:
        signature = _geometry_signature(rois)
        count, _ = signatures.get(signature, (0, rois))
        signatures[signature] = (count + 1, rois)
    modal = max(signatures.values(), key=lambda item: item[0])[1] if signatures else []
    if modal:
        for key in dict.fromkeys(all_keys):
            if key and key not in result:
                result[key] = [
                    dict(roi, inferred=True, inferred_from="modal targets")
                    for roi in modal
                ]
    return result


__all__ = ["roi_xz", "rois_from_config", "rois_by_config"]
