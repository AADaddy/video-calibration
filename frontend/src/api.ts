import type {
  CalibrationAnalysis,
  CalibrationSession,
  LensProfile,
  LensProfileValidation,
  PointPair,
  SessionSummary,
  ValidationField,
  VideoMetadata,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8765";

export function mediaUrl(path?: string | null): string {
  if (!path) return "";
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return `${API_BASE}${path}`;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
    ...options,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function listSessions(): Promise<{ items: SessionSummary[] }> {
  return request("/sessions");
}

export function createSession(payload: {
  name?: string;
  camera_id?: string;
  floor_id?: string;
  floor_map_id?: string;
}): Promise<CalibrationSession> {
  return request("/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getSession(sessionId: string): Promise<CalibrationSession> {
  return request(`/sessions/${sessionId}`);
}

export function updateSession(session: CalibrationSession): Promise<CalibrationSession> {
  return request(`/sessions/${session.id}`, {
    method: "PUT",
    body: JSON.stringify({
      name: session.name,
      camera_id: session.camera_id,
      floor_id: session.floor_id,
      floor_map_id: session.floor_map_id,
      lens_profile_id: session.lens_profile_id,
      lens_profile_status: session.lens_profile_status,
      lens_profile_warning: session.lens_profile_warning,
      camera_coordinate_space: session.camera_coordinate_space,
      allow_raw_fisheye_override: session.allow_raw_fisheye_override,
      camera_media: session.camera_media,
      floorplan_media: session.floorplan_media,
      selected_timestamp_seconds: session.selected_timestamp_seconds,
      bookmarks: session.bookmarks,
      point_pairs: session.point_pairs,
      roi_polygon: session.roi_polygon,
      validation_paths: session.validation_paths,
      analysis: session.analysis,
    }),
  });
}

export async function uploadMedia(kind: "camera" | "floorplan", file: File) {
  const body = new FormData();
  body.append("kind", kind);
  body.append("file", file);

  const response = await fetch(`${API_BASE}/media/upload`, {
    method: "POST",
    body,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Upload failed with ${response.status}`);
  }

  return response.json();
}

export function getVideoMetadata(path: string): Promise<VideoMetadata> {
  return request(`/media/video-metadata?path=${encodeURIComponent(path)}`);
}

export function videoFrameUrl(path: string, timestampSeconds: number, version = 0): string {
  const query = new URLSearchParams({
    path,
    t: String(Math.max(0, timestampSeconds)),
    v: String(version),
  });
  return `${API_BASE}/media/video-frame?${query.toString()}`;
}

export function listLensProfiles(cameraId?: string): Promise<{ items: LensProfile[] }> {
  const query = cameraId ? `?camera_id=${encodeURIComponent(cameraId)}` : "";
  return request(`/lens-profiles${query}`);
}

export function validateLensProfile(profileId: string, width: number, height: number): Promise<LensProfileValidation> {
  return request(`/lens-profiles/${profileId}/validate?width=${width}&height=${height}`);
}

export function undistortedPreviewUrl(profileId: string, path: string, timestampSeconds: number, version = 0): string {
  const query = new URLSearchParams({
    lens_profile_id: profileId,
    path,
    t: String(Math.max(0, timestampSeconds)),
    v: String(version),
  });
  return `${API_BASE}/lens/undistorted-preview?${query.toString()}`;
}

export function analyzeCalibration(pointPairs: PointPair[]): Promise<CalibrationAnalysis> {
  return request("/calibration/analyze", {
    method: "POST",
    body: JSON.stringify({
      point_pairs: pointPairs,
    }),
  });
}

export function getValidationField(payload: {
  point_pairs: PointPair[];
  lens_profile_id: string;
  width: number;
  height: number;
  cols?: number;
  rows?: number;
}): Promise<ValidationField> {
  return request("/calibration/validation-field", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
