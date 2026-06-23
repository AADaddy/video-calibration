from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MediaReference(BaseModel):
    path: str = ""
    url: str = ""
    kind: str = ""
    original_name: str = ""
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None


class Point(BaseModel):
    x: float
    y: float


class PointPair(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    camera: Point | None = None
    raw_camera: Point | None = None
    undistorted_camera: Point | None = None
    floor_map: Point | None = None
    enabled: bool = True
    role: str = "control"
    status: str = "ready"
    label: str = ""


class ValidationPath(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = "Validation path"
    points: list[Point] = Field(default_factory=list)
    notes: str = ""


class CalibrationSession(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = "Untitled calibration"
    camera_id: str = ""
    floor_id: str = ""
    floor_map_id: str = ""
    lens_profile_id: str = ""
    lens_profile_status: str = "missing"
    lens_profile_warning: str = ""
    camera_coordinate_space: str = "raw"
    allow_raw_fisheye_override: bool = False
    camera_media: MediaReference | None = None
    floorplan_media: MediaReference | None = None
    selected_timestamp_seconds: float | None = None
    bookmarks: list[float] = Field(default_factory=list)
    point_pairs: list[PointPair] = Field(default_factory=list)
    roi_polygon: list[Point] = Field(default_factory=list)
    validation_paths: list[ValidationPath] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def touch(self) -> None:
        self.updated_at = utc_now()


class SessionCreate(BaseModel):
    name: str | None = None
    camera_id: str | None = None
    floor_id: str | None = None
    floor_map_id: str | None = None


class SessionUpdate(BaseModel):
    name: str | None = None
    camera_id: str | None = None
    floor_id: str | None = None
    floor_map_id: str | None = None
    lens_profile_id: str | None = None
    lens_profile_status: str | None = None
    lens_profile_warning: str | None = None
    camera_coordinate_space: str | None = None
    allow_raw_fisheye_override: bool | None = None
    camera_media: MediaReference | None = None
    floorplan_media: MediaReference | None = None
    selected_timestamp_seconds: float | None = None
    bookmarks: list[float] | None = None
    point_pairs: list[PointPair] | None = None
    roi_polygon: list[Point] | None = None
    validation_paths: list[ValidationPath] | None = None
    analysis: dict[str, Any] | None = None


class SessionSummary(BaseModel):
    id: str
    name: str
    camera_id: str
    floor_id: str
    floor_map_id: str
    point_pair_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_session(cls, session: CalibrationSession) -> SessionSummary:
        return cls(
            id=session.id,
            name=session.name,
            camera_id=session.camera_id,
            floor_id=session.floor_id,
            floor_map_id=session.floor_map_id,
            point_pair_count=len(session.point_pairs),
            created_at=session.created_at,
            updated_at=session.updated_at,
        )


def session_path(session_dir: Path, session_id: str) -> Path:
    safe_id = "".join(ch for ch in session_id if ch.isalnum() or ch in ("-", "_"))
    return session_dir / f"{safe_id}.json"
