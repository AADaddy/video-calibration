export interface MediaReference {
  path: string;
  url: string;
  kind: string;
  original_name: string;
  width?: number | null;
  height?: number | null;
  duration_seconds?: number | null;
}

export interface VideoMetadata {
  width?: number | null;
  height?: number | null;
  duration_seconds?: number | null;
  fps?: number | null;
}

export interface Point {
  x: number;
  y: number;
}

export interface PointPair {
  id: string;
  camera?: Point | null;
  raw_camera?: Point | null;
  undistorted_camera?: Point | null;
  floor_map?: Point | null;
  enabled: boolean;
  role?: string;
  status?: string;
  label: string;
}

export interface LensProfile {
  id: string;
  camera_id: string;
  profile_name: string;
  lens_model: "opencv_fisheye";
  source_width: number;
  source_height: number;
  camera_matrix_K: number[][];
  distortion_coefficients_D: number[];
  balance: number;
  output_width?: number | null;
  output_height?: number | null;
  active: boolean;
  created_at: string;
  updated_at: string;
  notes: string;
}

export interface LensProfileValidation {
  status: "valid" | "missing" | "resolution_mismatch" | "invalid";
  valid: boolean;
  message: string;
  warnings: string[];
  profile?: LensProfile | null;
}

export interface HomographyAnalysis {
  ready: boolean;
  status: string;
  matrix?: number[][] | null;
  projected_points: Record<string, Point>;
  errors: Record<string, number>;
  mean_error?: number | null;
  median_error?: number | null;
  max_error?: number | null;
  outlier_ids: string[];
  message: string;
}

export interface TPSAnalysis {
  ready: boolean;
  status: string;
  projected_points: Record<string, Point>;
  errors: Record<string, number>;
  mean_error?: number | null;
  median_error?: number | null;
  max_error?: number | null;
  outlier_ids: string[];
  coefficients?: Record<string, unknown>;
  message: string;
}

export interface CalibrationAnalysis {
  homography: HomographyAnalysis;
  tps: TPSAnalysis;
  quality: string;
}

export interface ValidationPath {
  id: string;
  name: string;
  points: Point[];
  notes: string;
}

export interface CalibrationSession {
  id: string;
  name: string;
  camera_id: string;
  floor_id: string;
  floor_map_id: string;
  lens_profile_id: string;
  lens_profile_status: string;
  lens_profile_warning: string;
  camera_coordinate_space: string;
  allow_raw_fisheye_override: boolean;
  camera_media?: MediaReference | null;
  floorplan_media?: MediaReference | null;
  selected_timestamp_seconds?: number | null;
  bookmarks: number[];
  point_pairs: PointPair[];
  roi_polygon: Point[];
  validation_paths: ValidationPath[];
  analysis: Partial<CalibrationAnalysis> & Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ValidationField {
  cols: number;
  rows: number;
  width: number;
  height: number;
  raw: number[][];
  undistorted: number[][] | null;
  homography: number[][] | null;
  tps: number[][] | null;
  has_lens: boolean;
  has_homography: boolean;
  has_tps: boolean;
}

export interface SessionSummary {
  id: string;
  name: string;
  camera_id: string;
  floor_id: string;
  floor_map_id: string;
  point_pair_count: number;
  created_at: string;
  updated_at: string;
}
