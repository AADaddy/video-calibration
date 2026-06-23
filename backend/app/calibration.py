from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel, Field

from app.models import Point


class CalibrationPointPairIn(BaseModel):
    id: str
    camera: Point | None = None
    undistorted_camera: Point | None = None
    floor_map: Point | None = None
    enabled: bool = True

    @property
    def source(self) -> Point | None:
        """Calibration source point: prefer undistorted camera coords, fall back to raw."""
        return self.undistorted_camera or self.camera


class CalibrationAnalyzeIn(BaseModel):
    point_pairs: list[CalibrationPointPairIn] = Field(default_factory=list)
    outlier_threshold_px: float = 100


class ValidationFieldIn(BaseModel):
    point_pairs: list[CalibrationPointPairIn] = Field(default_factory=list)
    lens_profile_id: str = ""
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    cols: int = Field(default=96, ge=2, le=200)
    rows: int = Field(default=54, ge=2, le=200)


class HomographyAnalysis(BaseModel):
    ready: bool
    status: str
    matrix: list[list[float]] | None = None
    projected_points: dict[str, Point] = Field(default_factory=dict)
    errors: dict[str, float] = Field(default_factory=dict)
    mean_error: float | None = None
    median_error: float | None = None
    max_error: float | None = None
    outlier_ids: list[str] = Field(default_factory=list)
    message: str = ""


class TPSAnalysis(BaseModel):
    ready: bool
    status: str
    projected_points: dict[str, Point] = Field(default_factory=dict)
    errors: dict[str, float] = Field(default_factory=dict)
    mean_error: float | None = None
    median_error: float | None = None
    max_error: float | None = None
    outlier_ids: list[str] = Field(default_factory=list)
    coefficients: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class CalibrationAnalyzeOut(BaseModel):
    homography: HomographyAnalysis
    tps: TPSAnalysis
    quality: str


def _enabled_complete_pairs(payload: CalibrationAnalyzeIn) -> list[CalibrationPointPairIn]:
    return [
        pair
        for pair in payload.point_pairs
        if pair.enabled and pair.source is not None and pair.floor_map is not None
    ]


# ---------------------------------------------------------------------------
# TPS helpers
# ---------------------------------------------------------------------------

def _tps_kernel_matrix(source: np.ndarray) -> np.ndarray:
    diff = source[:, np.newaxis, :] - source[np.newaxis, :, :]
    r_sq = np.sum(diff ** 2, axis=2)
    kernel = np.zeros_like(r_sq)
    mask = r_sq > 1e-10
    kernel[mask] = r_sq[mask] * np.log(r_sq[mask])
    return kernel


def _tps_solve(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = len(source)
    K = _tps_kernel_matrix(source)
    P = np.hstack([np.ones((n, 1)), source])

    L = np.zeros((n + 3, n + 3))
    L[:n, :n] = K
    L[:n, n:] = P
    L[n:, :n] = P.T

    B = np.vstack([target, np.zeros((3, 2))])

    try:
        Y = np.linalg.solve(L, B)
    except np.linalg.LinAlgError:
        Y, _, _, _ = np.linalg.lstsq(L, B, rcond=None)

    return Y[:n], Y[n:]


def _tps_project(point: np.ndarray, source: np.ndarray, weights: np.ndarray, affine: np.ndarray) -> np.ndarray:
    diff = source - point[np.newaxis, :]
    r_sq = np.sum(diff ** 2, axis=1)
    kernel = np.zeros_like(r_sq)
    mask = r_sq > 1e-10
    kernel[mask] = r_sq[mask] * np.log(r_sq[mask])
    return affine[0] + affine[1] * point[0] + affine[2] * point[1] + kernel @ weights


# ---------------------------------------------------------------------------
# Homography
# ---------------------------------------------------------------------------

def _compute_homography(pairs: list[CalibrationPointPairIn], outlier_threshold_px: float) -> HomographyAnalysis:
    if len(pairs) < 4:
        return HomographyAnalysis(
            ready=False,
            status="need_points",
            message=f"Add {4 - len(pairs)} more complete enabled point pair(s) to calculate homography.",
        )

    source = np.asarray([[pair.source.x, pair.source.y] for pair in pairs], dtype=np.float64)
    target = np.asarray([[pair.floor_map.x, pair.floor_map.y] for pair in pairs], dtype=np.float64)
    # Least-squares fit over all curated points. RANSAC's default 3px inlier
    # threshold is far tighter than honest floorplan-scale error (tens of px),
    # so it degenerates to a minimal 4-point subset and throws the rest out.
    # Bad points are surfaced afterwards by the outlier-flagging logic below.
    matrix, _ = cv2.findHomography(source, target, method=0)

    if matrix is None:
        return HomographyAnalysis(
            ready=False,
            status="failed",
            message="Homography could not be calculated from the current point pairs.",
        )

    projected = cv2.perspectiveTransform(source.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    distances = np.linalg.norm(projected - target, axis=1)
    median_error = float(np.median(distances))
    mean_error = float(np.mean(distances))
    max_error = float(np.max(distances))
    threshold = max(float(outlier_threshold_px), median_error * 2)

    projected_points: dict[str, Point] = {}
    errors: dict[str, float] = {}
    outlier_ids: list[str] = []

    for index, pair in enumerate(pairs):
        px, py = projected[index]
        error = float(distances[index])
        projected_points[pair.id] = Point(x=round(float(px), 3), y=round(float(py), 3))
        errors[pair.id] = round(error, 3)
        if error > threshold and not math.isclose(error, 0):
            outlier_ids.append(pair.id)

    if outlier_ids or max_error > 40:
        status = "needs_review"
    elif len(pairs) >= 8 and mean_error <= 15 and max_error <= 30:
        status = "good"
    else:
        status = "usable"

    return HomographyAnalysis(
        ready=True,
        status=status,
        matrix=np.round(matrix, 8).astype(float).tolist(),
        projected_points=projected_points,
        errors=errors,
        mean_error=round(mean_error, 3),
        median_error=round(median_error, 3),
        max_error=round(max_error, 3),
        outlier_ids=outlier_ids,
        message="Homography calculated.",
    )


# ---------------------------------------------------------------------------
# TPS
# ---------------------------------------------------------------------------

def _compute_tps(pairs: list[CalibrationPointPairIn], outlier_threshold_px: float) -> TPSAnalysis:
    if len(pairs) < 3:
        return TPSAnalysis(
            ready=False,
            status="need_points",
            message=f"Add {3 - len(pairs)} more complete enabled point pair(s) to calculate TPS.",
        )

    source = np.asarray([[pair.source.x, pair.source.y] for pair in pairs], dtype=np.float64)
    target = np.asarray([[pair.floor_map.x, pair.floor_map.y] for pair in pairs], dtype=np.float64)

    weights, affine = _tps_solve(source, target)

    projected = np.array([_tps_project(source[i], source, weights, affine) for i in range(len(pairs))])

    if len(pairs) >= 4:
        loo_errors = np.zeros(len(pairs))
        for i in range(len(pairs)):
            mask = np.ones(len(pairs), dtype=bool)
            mask[i] = False
            loo_weights, loo_affine = _tps_solve(source[mask], target[mask])
            loo_projected = _tps_project(source[i], source[mask], loo_weights, loo_affine)
            loo_errors[i] = np.linalg.norm(loo_projected - target[i])
        errors = loo_errors
    else:
        errors = np.zeros(len(pairs))

    median_error = float(np.median(errors))
    mean_error = float(np.mean(errors))
    max_error = float(np.max(errors))
    threshold = max(float(outlier_threshold_px), median_error * 2)

    projected_points: dict[str, Point] = {}
    error_map: dict[str, float] = {}
    outlier_ids: list[str] = []

    for index, pair in enumerate(pairs):
        px, py = projected[index]
        error = float(errors[index])
        projected_points[pair.id] = Point(x=round(float(px), 3), y=round(float(py), 3))
        error_map[pair.id] = round(error, 3)
        if error > threshold and not math.isclose(error, 0):
            outlier_ids.append(pair.id)

    if outlier_ids or max_error > 40:
        status = "needs_review"
    elif len(pairs) >= 8 and mean_error <= 15 and max_error <= 30:
        status = "good"
    else:
        status = "usable"

    return TPSAnalysis(
        ready=True,
        status=status,
        projected_points=projected_points,
        errors=error_map,
        mean_error=round(mean_error, 3),
        median_error=round(median_error, 3),
        max_error=round(max_error, 3),
        outlier_ids=outlier_ids,
        coefficients={
            "source_points": source.tolist(),
            "weights": weights.tolist(),
            "affine": affine.tolist(),
        },
        message="TPS calculated." if len(pairs) >= 4 else "TPS calculated (leave-one-out errors require 4+ pairs).",
    )


# ---------------------------------------------------------------------------
# Combined analysis
# ---------------------------------------------------------------------------

def analyze_calibration(payload: CalibrationAnalyzeIn) -> CalibrationAnalyzeOut:
    pairs = _enabled_complete_pairs(payload)

    homography = _compute_homography(pairs, payload.outlier_threshold_px)
    tps = _compute_tps(pairs, payload.outlier_threshold_px)

    if len(pairs) < 4:
        quality = "incomplete"
    elif homography.outlier_ids or (homography.max_error or 0) > 40:
        quality = "needs_review"
    elif len(pairs) >= 8 and (homography.mean_error or 999) <= 15 and (homography.max_error or 999) <= 30:
        quality = "good"
    else:
        quality = "usable"

    return CalibrationAnalyzeOut(homography=homography, tps=tps, quality=quality)
