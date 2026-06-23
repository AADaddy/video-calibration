# Lens Correction Technical Specification

## Purpose

Add a fisheye / wide-angle lens correction layer before camera-to-floorplan homography.

The current tool maps raw camera image coordinates directly to the floorplan. For fisheye cameras this creates unstable homography and high outlier errors because straight floor geometry is distorted in the raw image. TPS must not be used to compensate for that lens distortion.

Target pipeline:

```text
Raw camera frame
  -> undistort point/image using lens profile
  -> homography from undistorted camera coordinate to floorplan coordinate
  -> optional TPS residual refinement
  -> projected floorplan coordinate
```

Detection projection uses the same pipeline:

```text
raw bbox foot point
  -> undistort point
  -> apply homography
  -> optional TPS residual correction
  -> floorplan point
```

## Current Codebase Snapshot

Current implementation:

- `backend/app/models.py`
  - `CalibrationSession`
  - `MediaReference`
  - `PointPair` with `camera` and `floor_map`
- `backend/app/calibration.py`
  - `POST /calibration/analyze` uses raw `pair.camera` points for homography and TPS.
  - Homography uses OpenCV `cv2.findHomography`.
  - TPS currently maps camera points directly to floorplan points.
- `backend/app/main.py`
  - Local media upload.
  - Static media serving.
  - Video metadata and extracted-frame fallback endpoints.
- `frontend/src/App.svelte`
  - Session sidebar.
  - Camera/floorplan canvases.
  - Point pair editor.
  - Homography/TPS analysis display.

Minimal safe implementation path:

1. Add lens profile data and services without changing existing point behavior.
2. Add raw/undistorted preview UI.
3. Add undistorted point storage while preserving backward compatibility with existing `camera` points.
4. Refactor homography to use undistorted camera points.
5. Refactor TPS into optional residual refinement.
6. Add detection projection through the same pipeline.

## Data Model Changes

### LensProfile

Initial local JSON/config model:

```json
{
  "id": "profile-id",
  "camera_id": "camera-uuid-or-local-id",
  "profile_name": "BedRUs front camera 2688x1520",
  "lens_model": "opencv_fisheye",
  "source_width": 2688,
  "source_height": 1520,
  "camera_matrix_K": [[1000, 0, 1344], [0, 1000, 760], [0, 0, 1]],
  "distortion_coefficients_D": [-0.01, 0.001, 0.0, 0.0],
  "balance": 0.0,
  "output_width": 2688,
  "output_height": 1520,
  "active": true,
  "created_at": "2026-06-23T00:00:00Z",
  "updated_at": "2026-06-23T00:00:00Z",
  "notes": ""
}
```

Future model/table fields should match this shape. A profile belongs to one camera and one source resolution.

### CalibrationSession

Add fields:

```json
{
  "lens_profile_id": "profile-id",
  "lens_profile_status": "valid | missing | resolution_mismatch | invalid",
  "lens_profile_warning": "",
  "camera_coordinate_space": "undistorted",
  "allow_raw_fisheye_override": false
}
```

### PointPair

Refactor toward:

```json
{
  "id": "point-id",
  "raw_camera": {"x": 110.0, "y": 240.0},
  "undistorted_camera": {"x": 100.0, "y": 220.0},
  "floor_map": {"x": 220.0, "y": 310.0},
  "enabled": true,
  "role": "control",
  "status": "ready",
  "label": ""
}
```

Backward compatibility:

- Existing `camera` should be treated as `undistorted_camera` if no lens profile metadata exists.
- New saves should write `undistorted_camera`.
- Keep `camera` temporarily only as a migration alias until export/import is updated.

## Backend Services

Create a lens service module, for example:

```text
backend/app/lens/
  profiles.py
  correction.py
```

Recommended functions:

```python
get_active_lens_profile(camera_id: str, width: int, height: int) -> LensProfile | None
validate_lens_profile(profile: LensProfile, width: int, height: int) -> LensProfileValidation
undistort_points(points: list[Point], lens_profile: LensProfile) -> list[Point]
generate_undistorted_preview(frame: np.ndarray, lens_profile: LensProfile) -> np.ndarray
```

OpenCV fisheye method:

- Use `cv2.fisheye.undistortPoints` for point undistortion.
- Use `cv2.fisheye.estimateNewCameraMatrixForUndistortRectify` when balance/crop output is configurable.
- Use `cv2.fisheye.initUndistortRectifyMap` and `cv2.remap` for preview generation.

Profile validation rules:

- Source frame width must equal `profile.source_width`.
- Source frame height must equal `profile.source_height`.
- `camera_matrix_K` must be 3x3.
- `distortion_coefficients_D` must have 4 values for OpenCV fisheye.
- Mismatch must be surfaced to UI and must not be silently applied.

## Calibration Math Changes

### Homography

Inputs:

- Source: enabled point pairs with `undistorted_camera`.
- Target: matching `floor_map`.

Error:

```text
projected_floor = H(undistorted_camera)
error_px = distance(projected_floor, manual_floor_map)
```

Outlier thresholds:

- Warning threshold default: 50 px.
- Outlier threshold default: 100 px.
- Keep configurable because floorplan image size changes pixel meaning.

### TPS

TPS must be optional and disabled by default.

TPS should refine residual floorplan error after homography:

```text
base_floor = H(undistorted_camera)
refined_floor = TPS(base_floor)
```

or equivalently:

```text
residual = manual_floor_map - H(undistorted_camera)
refined_floor = H(undistorted_camera) + TPS_residual(base_floor)
```

Do not map raw fisheye camera points directly with TPS.

## API Changes

### Lens Profiles

Add:

```text
GET    /lens-profiles?camera_id=...
GET    /lens-profiles/active?camera_id=...&width=...&height=...
POST   /lens-profiles
PUT    /lens-profiles/{profile_id}
POST   /lens-profiles/validate
```

For local MVP, profiles can be stored as JSON in:

```text
lens_profiles/
```

### Lens Correction

Add:

```text
POST /lens/undistort-points
POST /lens/undistorted-preview
```

Request example:

```json
{
  "lens_profile_id": "profile-id",
  "points": [{"x": 110, "y": 240}]
}
```

Response example:

```json
{
  "status": "valid",
  "undistorted_points": [{"x": 100.2, "y": 219.8}],
  "warnings": []
}
```

### Calibration Analyze

Update request:

```json
{
  "lens_profile_id": "profile-id",
  "point_pairs": [
    {
      "raw_camera": {"x": 110, "y": 240},
      "undistorted_camera": {"x": 100, "y": 220},
      "floor_map": {"x": 220, "y": 310},
      "enabled": true
    }
  ],
  "allow_raw_fisheye_override": false,
  "enable_tps_refinement": false
}
```

Response should include:

```json
{
  "lens_profile_status": "valid",
  "coordinate_space": "undistorted",
  "homography": {},
  "tps_refinement": {},
  "quality": "needs_review",
  "warnings": []
}
```

### Detection Projection

Add:

```text
POST /calibration/project-detections
```

Request:

```json
{
  "lens_profile_id": "profile-id",
  "homography_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
  "tps_refinement": null,
  "detections": [
    {"class_name": "person", "bbox": [100, 120, 180, 360], "score": 0.92}
  ],
  "roi_polygon": []
}
```

Projection:

```text
foot = ((x1 + x2) / 2, y2)
undistorted_foot = undistort_points([foot], lens_profile)[0]
floor = homography(undistorted_foot)
floor = optional_tps_residual(floor)
```

Response:

```json
{
  "projected": [
    {
      "raw_foot": {"x": 140, "y": 360},
      "undistorted_foot": {"x": 132, "y": 342},
      "floor_map": {"x": 520, "y": 810},
      "inside_roi": true
    }
  ],
  "projected_count": 1,
  "rejected_count": 0
}
```

## Frontend UI Changes

Add a stepper:

1. Select Data
2. Lens Correction
3. Add Points
4. Calculate
5. Validate
6. Review & Save

Lens Correction screen:

- Raw Camera View.
- Undistorted Preview.
- Lens profile selector.
- Profile resolution and source resolution.
- Match/mismatch indicator.
- Balance/crop control if supported.
- Warning if no valid lens profile exists.

Add Points screen:

- Camera canvas label: `Undistorted Camera View`.
- Optional small raw camera preview or toggle.
- Helper text:

```text
Place points on the same real-world floor location. Prefer floor contact points such as bed feet, carpet corners, doorway floor corners, and wall-floor intersections. Avoid bed tops, signs, walls, furniture tops, and other elevated points.
```

Point-pair table:

- Undistorted camera x/y.
- Floor x/y.
- Homography error.
- TPS refinement error if enabled.
- Status.
- Disable/delete actions.

Warnings:

- Missing lens profile:

```text
This camera appears to be fisheye/wide-angle. Raw image points may produce unstable homography. Apply lens correction first.
```

- Resolution mismatch:

```text
Lens profile resolution does not match the current frame. This profile will not be applied.
```

## Validation Workflow

After homography:

1. Load or run detections from the raw frame.
2. Convert person bbox to raw foot point.
3. Undistort raw foot point.
4. Apply homography.
5. Apply TPS residual only if enabled.
6. Show projected dots on the floorplan.
7. Show projected count and outside-ROI/rejected count.

Validation should also support manual points/paths:

- A validation point/path can be marked `role: validation`.
- Validation points are not used to solve homography unless changed to `control`.
- Report validation error separately from control-point reprojection error.

## Publish / Export Rules

Block publish or require explicit override when:

- No valid lens profile exists for a fisheye camera.
- Lens profile resolution mismatches the frame resolution.
- Homography has many outliers.
- Max error exceeds the configured outlier threshold.
- TPS is enabled but homography itself is unstable.

Export must include:

- Lens profile reference or full lens profile.
- Coordinate space: `undistorted`.
- Raw and undistorted point pairs when available.
- Homography matrix.
- Optional TPS residual configuration.
- ROI polygon.
- Validation summary.
- Created/updated metadata.

## Acceptance Criteria

- A camera can have an active fisheye lens profile.
- The calibration tool can display raw and undistorted camera previews.
- Users can place calibration points on the undistorted camera view.
- Homography is calculated using undistorted camera coordinates.
- Point-pair table shows homography error based on undistorted points.
- Raw detection bbox foot points are undistorted before floorplan projection.
- Projected detection dots appear on the floorplan in validation.
- The system warns when a fisheye camera has no valid lens profile.
- The system warns when lens profile resolution does not match the current frame.
- TPS is optional, disabled by default, and applied only after homography.
- Existing homography/TPS functionality is refactored to sit after lens correction, not removed.
- The UI remains focused on the calibration tool only.

## Risks And Assumptions

- Existing lens calibration profiles may not exist yet. MVP should support importing/entering a profile, not capturing one from checkerboard images.
- Incorrect lens profiles can make calibration worse than raw points. The UI must expose resolution and profile status clearly.
- OpenCV fisheye and standard OpenCV distortion models are different. Start with `opencv_fisheye`; add other lens models later only when needed.
- If output balance/crop changes, undistorted coordinates and homography must be recalculated.
- Floorplan pixel thresholds vary by floorplan resolution, so thresholds must be configurable.
- Production projection must use the same lens profile and output dimensions as the calibration tool.

## Non-Goals

- Do not build a full intrinsic calibration capture workflow yet.
- Do not require live RTSP processing for MVP.
- Do not train or modify the object detection model.
- Do not make TPS the default correction method.
- Do not use TPS to solve fisheye distortion directly.
