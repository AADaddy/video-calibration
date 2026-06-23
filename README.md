# Video Calibration

Standalone local calibration lab for camera-to-floorplan mapping.

Planning docs:

- `IMPLEMENTATION_PLAN.md` tracks the staged build plan.
- `LENS_CORRECTION_TECHNICAL_SPEC.md` defines the fisheye / wide-angle correction layer that must sit before homography. TPS remains optional residual refinement after lens correction and homography.

Stage 1 provides the app scaffold, local JSON session persistence, and a basic
frontend shell. It does not connect to any external database and does not run
video processing, homography, TPS, or detection yet.

Stage 2 adds local media loading:

- Upload a camera video or still camera image.
- Upload a floorplan image.
- Play, pause, scrub, step, and jump through video.
- Bookmark useful calibration frames.
- Save media references, timestamps, dimensions, durations, and bookmarks in the local session JSON.

Uploaded media is stored locally under `media/` and is ignored by git.

Stage 3 adds manual landmark pair editing:

- Click camera and floorplan views to create matched point pairs.
- Drag numbered markers to adjust coordinates.
- Disable, enable, delete, or clear point pairs.
- Zoom each media panel while keeping saved coordinates in natural image/video pixels.
- Save the session to persist point pairs.

Stage 4 adds homography analysis:

- Calculate a global camera-to-floorplan homography from enabled complete point pairs.
- Requires at least four complete enabled point pairs.
- Shows projected floorplan points, error lines, per-pair reprojection error, mean/max error, and outlier highlights.
- Saves the analysis summary into the local session JSON.

Stage 5 adds TPS analysis and compare mode:

- Calculate a Thin Plate Spline transform from camera points to floorplan points using the `r²·log(r²)` kernel.
- Requires at least three complete enabled point pairs.
- Computes leave-one-out cross-validation errors (4+ pairs) to detect overfitting and inconsistent landmarks.
- TPS coefficients (source points, weights, affine) are stored for export and audit.
- Transform mode toggle: **Homography** (global projection), **TPS** (local correction projection), **Compare** (both projections side by side).
- Per-pair TPS error column and TPS summary stats in the session status panel.

## Backend

```powershell
cd "E:\_Projects\Video Calibration\backend"
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --reload --port 8765
```

## Frontend

```powershell
cd "E:\_Projects\Video Calibration\frontend"
npm install
npm run dev
```

The frontend expects the backend at `http://localhost:8765`.
