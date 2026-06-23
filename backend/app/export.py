"""Calibration export package.

Produces a self-describing JSON bundle that a production system can use to
create/update a calibration. Camera points are exported in UNDISTORTED
coordinate space; the lens profile is included so production can undistort
raw detection foot-points into the same space before applying homography.

This module never mutates anything external — it only reads a session.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from app.calibration import CalibrationAnalyzeIn, CalibrationPointPairIn, analyze_calibration
from app.lens import LensProfile, redistort_points
from app.mapping import MappingEngine
from app.models import CalibrationSession, Point

SCHEMA_VERSION = "1.0"


def _source_point(pair) -> object | None:
    """Undistorted camera coord if present, else raw camera coord."""
    return pair.undistorted_camera or pair.camera


def build_calibration_export(
    session: CalibrationSession,
    lens_profile: LensProfile | None,
    space: str = "undistorted",
) -> dict:
    """Build the export package.

    space="undistorted": camera_points are lens-corrected (stable homography;
      production must undistort detections first).
    space="raw": camera_points are re-distorted to raw fisheye pixels — a drop-in
      for a production pipeline that maps raw points directly (use with TPS).
    """
    complete = [p for p in session.point_pairs if _source_point(p) is not None and p.floor_map is not None]
    use_raw = space == "raw" and lens_profile is not None
    coordinate_space = "raw" if (use_raw or lens_profile is None) else "undistorted"

    # The camera points actually exported, in the chosen space.
    undistorted_xy = np.asarray([[_source_point(p).x, _source_point(p).y] for p in complete], dtype=np.float64)
    # Raw (distorted) pixel for each point, recovered when a lens profile exists.
    if lens_profile is not None and len(complete):
        raw_xy = redistort_points(undistorted_xy, lens_profile)
    else:
        raw_xy = undistorted_xy
    camera_xy = raw_xy if use_raw else undistorted_xy
    camera_points = [[round(float(x), 3), round(float(y), 3)] for x, y in camera_xy] if len(complete) else []
    floor_map_points = [[round(p.floor_map.x, 3), round(p.floor_map.y, 3)] for p in complete]
    disabled_indexes = [i for i, p in enumerate(complete) if not p.enabled]

    # Homography / TPS / errors are all computed in the EXPORTED point space, so the
    # matrix maps camera_points -> floor and the validation reflects what production sees.
    space_pairs = [
        CalibrationPointPairIn(
            id=p.id, camera=Point(x=float(cx), y=float(cy)), undistorted_camera=None,
            floor_map=p.floor_map, enabled=p.enabled,
        )
        for p, (cx, cy) in zip(complete, camera_xy)
    ]

    engine = MappingEngine.from_calibration(space_pairs, None)
    homography_matrix = None
    if engine.homography is not None:
        homography_matrix = np.round(engine.homography, 8).astype(float).tolist()

    analysis = analyze_calibration(CalibrationAnalyzeIn(point_pairs=space_pairs))
    h = analysis.homography
    validation = {
        "quality": analysis.quality,
        "homography_status": h.status,
        "mean_error_px": h.mean_error,
        "median_error_px": h.median_error,
        "max_error_px": h.max_error,
        "outlier_count": len(h.outlier_ids),
        "enabled_pair_count": sum(1 for p in complete if p.enabled),
        "total_pair_count": len(complete),
    }

    floor_dims = None
    if session.floorplan_media and session.floorplan_media.width and session.floorplan_media.height:
        floor_dims = {"width": session.floorplan_media.width, "height": session.floorplan_media.height}

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "video-calibration",
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "coordinate_space": coordinate_space,
        "active": True,
        "camera_id": session.camera_id,
        "floor_id": session.floor_id,
        "floor_map_id": session.floor_map_id,
        # Production-native fields (drop-in for camera.Calibration).
        "camera_points": camera_points,
        "floor_map_points": floor_map_points,
        "disabled_indexes": disabled_indexes,
        "floor_map_dimensions": floor_dims,
        # Newline-delimited convenience strings for copy/paste into existing text fields.
        "camera_points_text": "\n".join(f"{x},{y}" for x, y in camera_points),
        "floor_map_points_text": "\n".join(f"{x},{y}" for x, y in floor_map_points),
        # Full detail so production can undistort raw detections + audit.
        "point_pairs": [
            {
                "id": p.id,
                "raw_camera": {"x": round(float(rx), 3), "y": round(float(ry), 3)},
                "undistorted_camera": {"x": round(float(ux), 3), "y": round(float(uy), 3)},
                "floor_map": p.floor_map.model_dump(),
                "enabled": p.enabled,
                "role": p.role,
                "label": p.label,
            }
            for p, (rx, ry), (ux, uy) in zip(complete, raw_xy, undistorted_xy)
        ],
        "lens_profile": lens_profile.model_dump(mode="json") if lens_profile else None,
        "homography_matrix": homography_matrix,
        "roi_polygon": [[round(pt.x, 3), round(pt.y, 3)] for pt in session.roi_polygon],
        "validation": validation,
        "metadata": {
            "session_id": session.id,
            "session_name": session.name,
            "created_at": session.created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": session.updated_at.isoformat().replace("+00:00", "Z"),
        },
    }
