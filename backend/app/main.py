from __future__ import annotations

import json
from pathlib import Path
from shutil import copyfileobj
from uuid import uuid4

import cv2
from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.calibration import (
    CalibrationAnalyzeIn,
    CalibrationAnalyzeOut,
    ValidationFieldIn,
    analyze_calibration as run_calibration_analysis,
)
from app.export import build_calibration_export
from app.mapping import MappingEngine
from app.lens import (
    LensProfile,
    LensProfileCreate,
    LensProfileStore,
    UndistortPointsIn,
    UndistortPointsOut,
    generate_undistorted_preview,
    undistort_points,
    validate_profile_resolution,
)
from app.models import CalibrationSession, MediaReference, SessionCreate, SessionUpdate
from app.storage import SessionStore

BASE_DIR = Path(__file__).resolve().parents[2]
SESSION_DIR = BASE_DIR / "sessions"
MEDIA_DIR = BASE_DIR / "media"
LENS_PROFILE_DIR = BASE_DIR / "lens_profiles"
EXPORT_DIR = BASE_DIR / "calibration_exports"

app = FastAPI(title="Video Calibration", version="0.1.0")
store = SessionStore(SESSION_DIR)
lens_store = LensProfileStore(LENS_PROFILE_DIR)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _safe_filename(filename: str) -> str:
    stem = Path(filename).stem or "media"
    suffix = Path(filename).suffix.lower()
    safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
    return f"{safe_stem[:80]}-{uuid4().hex}{suffix}"


def _safe_media_path(path: str) -> Path:
    candidate = (BASE_DIR / path).resolve()
    media_root = MEDIA_DIR.resolve()
    if not candidate.is_file() or media_root not in candidate.parents:
        raise HTTPException(status_code=404, detail="Media file not found")
    return candidate


def _read_media_metadata(path: Path) -> dict[str, float | int | None]:
    cap = cv2.VideoCapture(str(path))
    if cap.isOpened():
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) or None
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) or None
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if fps > 0 and frame_count > 0 else None
        cap.release()
        return {"width": width, "height": height, "duration_seconds": duration, "fps": fps or None}
    cap.release()

    image = cv2.imread(str(path))
    if image is not None:
        height, width = image.shape[:2]
        return {"width": int(width), "height": int(height), "duration_seconds": None, "fps": None}

    return {"width": None, "height": None, "duration_seconds": None, "fps": None}


@app.post("/media/upload", response_model=MediaReference)
def upload_media(kind: str = Form(...), file: UploadFile = File(...)) -> MediaReference:
    if kind not in {"camera", "floorplan"}:
        raise HTTPException(status_code=400, detail="Media kind must be 'camera' or 'floorplan'")

    target_dir = MEDIA_DIR / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(file.filename or f"{kind}-media")
    target_path = target_dir / filename

    with target_path.open("wb") as output:
        copyfileobj(file.file, output)

    relative_path = target_path.relative_to(BASE_DIR).as_posix()
    metadata = _read_media_metadata(target_path)
    return MediaReference(
        path=relative_path,
        url=f"/media/{kind}/{filename}",
        kind=kind,
        original_name=file.filename or filename,
        width=metadata["width"],
        height=metadata["height"],
        duration_seconds=metadata["duration_seconds"],
    )


@app.get("/media/video-metadata")
def video_metadata(path: str = Query(...)) -> dict[str, float | int | None]:
    return _read_media_metadata(_safe_media_path(path))


@app.get("/media/video-frame")
def video_frame(path: str = Query(...), t: float = Query(0, ge=0)) -> Response:
    frame = _read_video_frame(_safe_media_path(path), t)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    if not ok:
        raise HTTPException(status_code=500, detail="Video frame could not be encoded")
    return Response(content=encoded.tobytes(), media_type="image/jpeg")


def _read_video_frame(video_path: Path, t: float) -> cv2.typing.MatLike:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise HTTPException(status_code=400, detail="Video could not be opened")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if fps > 0 and frame_count > 0:
        frame_index = max(0, min(frame_count - 1, int(round(t * fps))))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    else:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0, t) * 1000)

    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(status_code=404, detail="Video frame not found")
    return frame


@app.get("/lens-profiles")
def list_lens_profiles(camera_id: str | None = Query(None)) -> dict[str, list[LensProfile]]:
    return {"items": lens_store.list(camera_id)}


@app.post("/lens-profiles", response_model=LensProfile, status_code=201)
def create_lens_profile(payload: LensProfileCreate) -> LensProfile:
    return lens_store.create(payload)


@app.get("/lens-profiles/active/validate")
def validate_active_lens_profile(camera_id: str, width: int, height: int):
    return lens_store.active_for(camera_id, width, height)


@app.get("/lens-profiles/{profile_id}/validate")
def validate_lens_profile(profile_id: str, width: int, height: int):
    return validate_profile_resolution(lens_store.get(profile_id), width, height)


@app.get("/lens-profiles/{profile_id}", response_model=LensProfile)
def get_lens_profile(profile_id: str) -> LensProfile:
    return lens_store.get(profile_id)


@app.post("/lens/undistort-points", response_model=UndistortPointsOut)
def undistort_lens_points(payload: UndistortPointsIn) -> UndistortPointsOut:
    profile = lens_store.get(payload.lens_profile_id)
    return UndistortPointsOut(
        status="valid",
        undistorted_points=undistort_points(payload.points, profile),
    )


@app.get("/lens/undistorted-preview")
def undistorted_preview(
    lens_profile_id: str,
    path: str = Query(...),
    t: float = Query(0, ge=0),
) -> Response:
    profile = lens_store.get(lens_profile_id)
    media_path = _safe_media_path(path)
    metadata = _read_media_metadata(media_path)
    validation = validate_profile_resolution(profile, metadata["width"], metadata["height"])
    if not validation.valid:
        raise HTTPException(status_code=400, detail=validation.message)

    if media_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        frame = cv2.imread(str(media_path))
        if frame is None:
            raise HTTPException(status_code=400, detail="Image could not be opened")
    else:
        frame = _read_video_frame(media_path, t)

    preview = generate_undistorted_preview(frame, profile)
    ok, encoded = cv2.imencode(".jpg", preview, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    if not ok:
        raise HTTPException(status_code=500, detail="Undistorted preview could not be encoded")
    return Response(content=encoded.tobytes(), media_type="image/jpeg")


app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


@app.post("/calibration/analyze", response_model=CalibrationAnalyzeOut)
def analyze_calibration(payload: CalibrationAnalyzeIn) -> CalibrationAnalyzeOut:
    return run_calibration_analysis(payload)


@app.post("/calibration/validation-field")
def calibration_validation_field(payload: ValidationFieldIn) -> dict:
    """Precompute the raw-pixel -> floorplan vector field for the validation screen.

    The full mapping pipeline runs here once; the frontend only interpolates the
    returned grid, so per-cursor-move mapping stays instant and never re-implements
    the transform maths.
    """
    lens_profile = None
    if payload.lens_profile_id:
        try:
            lens_profile = lens_store.get(payload.lens_profile_id)
        except HTTPException:
            lens_profile = None
    engine = MappingEngine.from_calibration(payload.point_pairs, lens_profile)
    return engine.build_field(payload.width, payload.height, payload.cols, payload.rows)


@app.get("/sessions")
def list_sessions():
    return {"items": store.list()}


@app.post("/sessions", response_model=CalibrationSession, status_code=201)
def create_session(payload: SessionCreate) -> CalibrationSession:
    session = CalibrationSession(
        name=payload.name or "Untitled calibration",
        camera_id=payload.camera_id or "",
        floor_id=payload.floor_id or "",
        floor_map_id=payload.floor_map_id or "",
    )
    return store.create(session)


@app.get("/sessions/{session_id}/export")
def export_session(session_id: str, space: str = Query("undistorted", pattern="^(undistorted|raw)$")) -> dict:
    """Build the production calibration export package.

    space=undistorted: lens-corrected camera_points (stable homography; prod must
      undistort detections first).
    space=raw: camera_points re-distorted to raw fisheye pixels (drop-in for prod
      that maps raw points directly).

    Read-only: assembles from the stored session and writes an audit copy to
    calibration_exports/. Does not mutate any external system.
    """
    session = store.get(session_id)
    lens_profile = None
    if session.lens_profile_id:
        try:
            lens_profile = lens_store.get(session.lens_profile_id)
        except HTTPException:
            lens_profile = None
    package = build_calibration_export(session, lens_profile, space=space)

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch for ch in session_id if ch.isalnum() or ch in ("-", "_"))
    (EXPORT_DIR / f"{safe_id}-{package['coordinate_space']}.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
    return package


@app.get("/sessions/{session_id}", response_model=CalibrationSession)
def get_session(session_id: str) -> CalibrationSession:
    return store.get(session_id)


@app.put("/sessions/{session_id}", response_model=CalibrationSession)
def update_session(session_id: str, payload: SessionUpdate) -> CalibrationSession:
    session = store.get(session_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(session, key, value)
    session.touch()
    return store.save(session)
