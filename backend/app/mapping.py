"""Single camera-to-floorplan mapping engine.

This is the one place transform logic lives. Validation, detection projection,
tracking, and tests must all map points through `MappingEngine` so they share
identical behaviour. The frontend never re-implements any of this maths; it
only interpolates a precomputed field returned by `build_field`.

Pipeline (stages skipped gracefully when not configured):

    raw camera pixel
      -> fisheye undistort (lens profile)
      -> homography (undistorted camera -> floorplan)
      -> TPS (undistorted camera -> floorplan, optional)
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from app.calibration import _tps_solve
from app.lens import LensProfile, validate_profile_shape


def _tps_project_batch(points: np.ndarray, source: np.ndarray, weights: np.ndarray, affine: np.ndarray) -> np.ndarray:
    """Vectorised TPS evaluation. Identical maths to calibration._tps_project."""
    diff = points[:, np.newaxis, :] - source[np.newaxis, :, :]
    r_sq = np.sum(diff ** 2, axis=2)
    kernel = np.zeros_like(r_sq)
    mask = r_sq > 1e-10
    kernel[mask] = r_sq[mask] * np.log(r_sq[mask])
    affine_part = affine[0] + points @ affine[1:]
    return affine_part + kernel @ weights


class MappingEngine:
    """Maps raw camera pixels to floorplan coordinates through the full pipeline."""

    def __init__(
        self,
        *,
        homography: np.ndarray | None = None,
        tps: dict[str, np.ndarray] | None = None,
        lens: dict[str, np.ndarray] | None = None,
    ) -> None:
        self.homography = homography
        self.tps = tps
        self.lens = lens

    # -- construction ------------------------------------------------------

    @classmethod
    def from_calibration(cls, pairs: list, lens_profile: LensProfile | None = None) -> "MappingEngine":
        """Build from enabled+complete point pairs.

        Accepts any object with `undistorted_camera` / `camera` / `floor_map` /
        `enabled` (both PointPair and CalibrationPointPairIn qualify). The source
        point prefers undistorted camera coords, falling back to raw.
        """
        def source_of(p):
            return getattr(p, "undistorted_camera", None) or getattr(p, "camera", None)

        usable = [p for p in pairs if p.enabled and source_of(p) is not None and p.floor_map is not None]

        lens = None
        if lens_profile is not None and not validate_profile_shape(lens_profile):
            K = np.asarray(lens_profile.camera_matrix_K, dtype=np.float64)
            D = np.asarray(lens_profile.distortion_coefficients_D, dtype=np.float64).reshape(4, 1)
            out_w = lens_profile.output_width or lens_profile.source_width
            out_h = lens_profile.output_height or lens_profile.source_height
            new_K = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
                K, D,
                (lens_profile.source_width, lens_profile.source_height),
                np.eye(3),
                balance=float(lens_profile.balance),
                new_size=(out_w, out_h),
            )
            lens = {"K": K, "D": D, "new_K": new_K}

        source = np.asarray([[source_of(p).x, source_of(p).y] for p in usable], dtype=np.float64)
        target = np.asarray([[p.floor_map.x, p.floor_map.y] for p in usable], dtype=np.float64)

        homography = None
        if len(usable) >= 4:
            homography, _ = cv2.findHomography(source, target, method=0)

        tps = None
        if len(usable) >= 3:
            weights, affine = _tps_solve(source, target)
            tps = {"source": source, "weights": weights, "affine": affine}

        return cls(homography=homography, tps=tps, lens=lens)

    # -- transforms --------------------------------------------------------

    def undistort(self, raw: np.ndarray) -> np.ndarray:
        if not self.lens:
            return raw
        src = raw.reshape(-1, 1, 2).astype(np.float64)
        return cv2.fisheye.undistortPoints(src, self.lens["K"], self.lens["D"], P=self.lens["new_K"]).reshape(-1, 2)

    def via_homography(self, undistorted: np.ndarray) -> np.ndarray | None:
        if self.homography is None:
            return None
        return cv2.perspectiveTransform(undistorted.reshape(-1, 1, 2), self.homography).reshape(-1, 2)

    def via_tps(self, undistorted: np.ndarray) -> np.ndarray | None:
        if not self.tps:
            return None
        return _tps_project_batch(undistorted, self.tps["source"], self.tps["weights"], self.tps["affine"])

    def map_points(self, raw: np.ndarray) -> dict[str, np.ndarray | None]:
        """Return every pipeline stage for the given raw camera pixels (N x 2)."""
        raw = np.asarray(raw, dtype=np.float64).reshape(-1, 2)
        undistorted = self.undistort(raw)
        return {
            "raw": raw,
            "undistorted": undistorted,
            "homography": self.via_homography(undistorted),
            "tps": self.via_tps(undistorted),
        }

    # -- field for the validation UI --------------------------------------

    def build_field(self, width: int, height: int, cols: int, rows: int) -> dict[str, Any]:
        """Sample the pipeline over a cols x rows grid of raw pixels (row-major)."""
        xs = np.linspace(0, max(width - 1, 1), cols)
        ys = np.linspace(0, max(height - 1, 1), rows)
        grid_x, grid_y = np.meshgrid(xs, ys)
        raw = np.column_stack([grid_x.ravel(), grid_y.ravel()])
        stages = self.map_points(raw)

        def serialize(arr: np.ndarray | None) -> list[list[float]] | None:
            if arr is None:
                return None
            return np.round(arr, 3).tolist()

        return {
            "cols": cols,
            "rows": rows,
            "width": width,
            "height": height,
            "raw": serialize(stages["raw"]),
            "undistorted": serialize(stages["undistorted"]),
            "homography": serialize(stages["homography"]),
            "tps": serialize(stages["tps"]),
            "has_lens": self.lens is not None,
            "has_homography": self.homography is not None,
            "has_tps": self.tps is not None,
        }
