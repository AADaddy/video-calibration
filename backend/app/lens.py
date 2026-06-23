from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

import cv2
import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.models import Point


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LensProfile(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    camera_id: str = ""
    profile_name: str = "Untitled lens profile"
    lens_model: Literal["opencv_fisheye"] = "opencv_fisheye"
    source_width: int
    source_height: int
    camera_matrix_K: list[list[float]]
    distortion_coefficients_D: list[float]
    balance: float = 0.0
    output_width: int | None = None
    output_height: int | None = None
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    notes: str = ""

    def touch(self) -> None:
        self.updated_at = utc_now()


class LensProfileCreate(BaseModel):
    camera_id: str = ""
    profile_name: str = "Untitled lens profile"
    lens_model: Literal["opencv_fisheye"] = "opencv_fisheye"
    source_width: int
    source_height: int
    camera_matrix_K: list[list[float]]
    distortion_coefficients_D: list[float]
    balance: float = 0.0
    output_width: int | None = None
    output_height: int | None = None
    active: bool = True
    notes: str = ""


class LensProfileValidation(BaseModel):
    status: Literal["valid", "missing", "resolution_mismatch", "invalid"]
    valid: bool
    message: str
    warnings: list[str] = Field(default_factory=list)
    profile: LensProfile | None = None


class UndistortPointsIn(BaseModel):
    lens_profile_id: str
    points: list[Point] = Field(default_factory=list)


class UndistortPointsOut(BaseModel):
    status: str
    undistorted_points: list[Point]
    warnings: list[str] = Field(default_factory=list)


class LensProfileStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self, camera_id: str | None = None) -> list[LensProfile]:
        profiles: list[LensProfile] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                profile = LensProfile.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if camera_id and profile.camera_id != camera_id:
                continue
            profiles.append(profile)
        profiles.sort(key=lambda item: item.updated_at, reverse=True)
        return profiles

    def create(self, payload: LensProfileCreate) -> LensProfile:
        profile = LensProfile(**payload.model_dump())
        return self.save(profile)

    def get(self, profile_id: str) -> LensProfile:
        path = self._path(profile_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Lens profile not found")
        return LensProfile.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, profile: LensProfile) -> LensProfile:
        profile.touch()
        self._path(profile.id).write_text(json.dumps(profile.model_dump(mode="json"), indent=2), encoding="utf-8")
        return profile

    def active_for(self, camera_id: str, width: int, height: int) -> LensProfileValidation:
        active_profiles = [profile for profile in self.list(camera_id) if profile.active]
        if not active_profiles:
            return LensProfileValidation(
                status="missing",
                valid=False,
                message="No active lens profile exists for this camera.",
            )

        matching = [profile for profile in active_profiles if profile.source_width == width and profile.source_height == height]
        if not matching:
            return LensProfileValidation(
                status="resolution_mismatch",
                valid=False,
                message="Active lens profile resolution does not match the current frame.",
                warnings=[f"Current frame is {width}x{height}; active profile is {active_profiles[0].source_width}x{active_profiles[0].source_height}."],
                profile=active_profiles[0],
            )

        profile = matching[0]
        invalid = validate_profile_shape(profile)
        if invalid:
            return LensProfileValidation(
                status="invalid",
                valid=False,
                message="Lens profile has invalid OpenCV fisheye parameters.",
                warnings=invalid,
                profile=profile,
            )
        return LensProfileValidation(status="valid", valid=True, message="Lens profile matches this frame.", profile=profile)

    def _path(self, profile_id: str) -> Path:
        safe_id = "".join(ch for ch in profile_id if ch.isalnum() or ch in ("-", "_"))
        return self.root / f"{safe_id}.json"


def validate_profile_shape(profile: LensProfile) -> list[str]:
    warnings: list[str] = []
    K = np.asarray(profile.camera_matrix_K, dtype=np.float64)
    D = np.asarray(profile.distortion_coefficients_D, dtype=np.float64)
    if K.shape != (3, 3):
        warnings.append("camera_matrix_K must be a 3x3 matrix.")
    if D.size != 4:
        warnings.append("distortion_coefficients_D must contain 4 values for opencv_fisheye.")
    if profile.source_width <= 0 or profile.source_height <= 0:
        warnings.append("source_width and source_height must be positive.")
    return warnings


def validate_profile_resolution(profile: LensProfile | None, width: int | None, height: int | None) -> LensProfileValidation:
    if profile is None:
        return LensProfileValidation(status="missing", valid=False, message="No lens profile selected.")
    if not width or not height:
        return LensProfileValidation(status="invalid", valid=False, message="Current frame resolution is unknown.", profile=profile)
    invalid = validate_profile_shape(profile)
    if invalid:
        return LensProfileValidation(status="invalid", valid=False, message="Lens profile has invalid parameters.", warnings=invalid, profile=profile)
    if profile.source_width != width or profile.source_height != height:
        return LensProfileValidation(
            status="resolution_mismatch",
            valid=False,
            message="Lens profile resolution does not match the current frame.",
            warnings=[f"Current frame is {width}x{height}; selected profile is {profile.source_width}x{profile.source_height}."],
            profile=profile,
        )
    return LensProfileValidation(status="valid", valid=True, message="Lens profile matches this frame.", profile=profile)


def undistort_points(points: list[Point], profile: LensProfile) -> list[Point]:
    invalid = validate_profile_shape(profile)
    if invalid:
        raise HTTPException(status_code=400, detail=" ".join(invalid))
    if not points:
        return []

    K = np.asarray(profile.camera_matrix_K, dtype=np.float64)
    D = np.asarray(profile.distortion_coefficients_D, dtype=np.float64).reshape(4, 1)
    output_width = profile.output_width or profile.source_width
    output_height = profile.output_height or profile.source_height
    new_K = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
        K,
        D,
        (profile.source_width, profile.source_height),
        np.eye(3),
        balance=float(profile.balance),
        new_size=(output_width, output_height),
    )
    source = np.asarray([[[point.x, point.y]] for point in points], dtype=np.float64)
    undistorted = cv2.fisheye.undistortPoints(source, K, D, P=new_K).reshape(-1, 2)
    return [Point(x=round(float(x), 3), y=round(float(y), 3)) for x, y in undistorted]


def generate_undistorted_preview(frame: np.ndarray, profile: LensProfile) -> np.ndarray:
    invalid = validate_profile_shape(profile)
    if invalid:
        raise HTTPException(status_code=400, detail=" ".join(invalid))

    K = np.asarray(profile.camera_matrix_K, dtype=np.float64)
    D = np.asarray(profile.distortion_coefficients_D, dtype=np.float64).reshape(4, 1)
    source_size = (profile.source_width, profile.source_height)
    output_size = (profile.output_width or profile.source_width, profile.output_height or profile.source_height)
    new_K = cv2.fisheye.estimateNewCameraMatrixForUndistortRectify(
        K,
        D,
        source_size,
        np.eye(3),
        balance=float(profile.balance),
        new_size=output_size,
    )
    map1, map2 = cv2.fisheye.initUndistortRectifyMap(K, D, np.eye(3), new_K, output_size, cv2.CV_16SC2)
    return cv2.remap(frame, map1, map2, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
