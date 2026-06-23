<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { analyzeCalibration, createSession, getSession, getValidationField, getVideoMetadata, listLensProfiles, listSessions, mediaUrl, undistortedPreviewUrl, updateSession, uploadMedia, validateLensProfile, videoFrameUrl } from "./api";
  import type { CalibrationSession, LensProfile, LensProfileValidation, MediaReference, Point, PointPair, SessionSummary, ValidationField } from "./types";

  let sessions: SessionSummary[] = [];
  let lensProfiles: LensProfile[] = [];
  let lensProfileValidation: LensProfileValidation | null = null;
  let currentSession: CalibrationSession | null = null;
  let loading = true;
  let lensProfileLoading = false;
  let saving = false;
  let uploadingCamera = false;
  let uploadingFloorplan = false;
  let analyzing = false;
  let creatingSession = false;
  let errorMessage = "";
  let newSessionName = "New calibration";
  let showCreateSessionDialog = false;
  let videoElement: HTMLVideoElement | null = null;
  let cameraImageElement: HTMLImageElement | null = null;
  let floorplanElement: HTMLImageElement | null = null;
  let cameraOverlayElement: SVGSVGElement | null = null;
  let floorplanOverlayElement: SVGSVGElement | null = null;
  let videoPlaying = false;
  let videoStatusMessage = "";
  let videoLoadError = "";
  let videoFallbackMode = false;
  let fallbackFrameVersion = 0;
  let lensPreviewVersion = 0;
  let fallbackPlaybackTimer: ReturnType<typeof setInterval> | null = null;
  let playbackRate = 1;
  let selectedPairId = "";
  let activeTool: "select" | "add" = "add";
  let cameraZoom = 1;
  let floorplanZoom = 1;
  let dragState: { pairId: string; side: "camera" | "floor_map" } | null = null;
  let transformMode: "homography" | "tps" | "compare" = "homography";
  let useUndistortedView = true;

  // --- Validation screen state ---
  let workspaceMode: "calibrate" | "validate" = "calibrate";
  let validationField: ValidationField | null = null;
  let validationLoading = false;
  let validationStage: "homography" | "tps" = "homography";
  let validationMode: "cursor" | "crosshair" | "trail" | "grid" = "cursor";
  let validationCameraOverlay: SVGSVGElement | null = null;
  let hoverCameraPx: Point | null = null;
  let hoverUndistorted: Point | null = null;
  let hoverHomography: Point | null = null;
  let hoverTps: Point | null = null;
  let validationTrail: { camera: Point; floor: Point }[] = [];
  const TRAIL_MAX = 60;

  $: cameraMediaUrl = mediaUrl(currentSession?.camera_media?.url);
  $: floorplanMediaUrl = mediaUrl(currentSession?.floorplan_media?.url);
  $: isCameraImage = Boolean(currentSession?.camera_media?.original_name?.match(/\.(png|jpe?g|webp|gif)$/i));
  $: selectedTimestamp = currentSession?.selected_timestamp_seconds ?? 0;
  $: fallbackFrameUrl = videoFallbackMode && currentSession?.camera_media?.path
    ? videoFrameUrl(currentSession.camera_media.path, selectedTimestamp, fallbackFrameVersion)
    : "";
  $: selectedLensProfile = lensProfiles.find((profile) => profile.id === currentSession?.lens_profile_id) ?? null;
  $: lensPreviewUrl = lensProfileValidation?.valid && currentSession?.camera_media?.path && currentSession?.lens_profile_id
    ? undistortedPreviewUrl(currentSession.lens_profile_id, currentSession.camera_media.path, selectedTimestamp, lensPreviewVersion)
    : "";
  $: canShowUndistorted = Boolean(lensProfileValidation?.valid && lensPreviewUrl);
  $: showUndistortedCamera = useUndistortedView && canShowUndistorted;
  $: sortedBookmarks = [...(currentSession?.bookmarks ?? [])].sort((a, b) => a - b);
  $: selectedPair = currentSession?.point_pairs.find((pair) => pair.id === selectedPairId) ?? null;
  $: enabledPairCount = currentSession?.point_pairs.filter((pair) => pair.enabled && pair.camera && pair.floor_map).length ?? 0;
  $: cameraNaturalWidth = currentSession?.camera_media?.width ?? 1920;
  $: cameraNaturalHeight = currentSession?.camera_media?.height ?? 1080;
  $: floorplanNaturalWidth = currentSession?.floorplan_media?.width ?? 1200;
  $: floorplanNaturalHeight = currentSession?.floorplan_media?.height ?? 800;
  $: homography = currentSession?.analysis?.homography;
  $: tps = currentSession?.analysis?.tps;
  $: homographyOutlierIds = new Set(homography?.outlier_ids ?? []);
  $: tpsOutlierIds = new Set(tps?.outlier_ids ?? []);
  $: outlierIds = new Set([...homographyOutlierIds, ...tpsOutlierIds]);
  $: validationReady = Boolean(homography?.ready && currentSession?.camera_media?.width && currentSession?.floorplan_media);
  $: validationSnapshotUrl = !currentSession?.camera_media
    ? ""
    : isCameraImage
      ? cameraMediaUrl
      : currentSession.camera_media.path
        ? videoFrameUrl(currentSession.camera_media.path, selectedTimestamp, fallbackFrameVersion)
        : "";
  $: hoverFloor = validationStage === "tps" ? hoverTps : hoverHomography;
  $: validationGridStage = validationField ? (validationStage === "tps" ? validationField.tps : validationField.homography) : null;
  $: validationGridDots = (() => {
    if (validationMode !== "grid" || !validationField || !validationGridStage) return [] as number[][];
    const { cols, rows } = validationField;
    const step = Math.max(1, Math.round(Math.sqrt((cols * rows) / 1200)));
    const dots: number[][] = [];
    for (let r = 0; r < rows; r += step) {
      for (let c = 0; c < cols; c += step) {
        const point = validationGridStage[r * cols + c];
        if (point) dots.push(point);
      }
    }
    return dots;
  })();

  onMount(async () => {
    await refreshSessions();
    await refreshLensProfiles();
    window.addEventListener("pointermove", handleGlobalPointerMove);
    window.addEventListener("pointerup", stopDragging);
  });

  onDestroy(() => {
    window.removeEventListener("pointermove", handleGlobalPointerMove);
    window.removeEventListener("pointerup", stopDragging);
    stopFallbackPlayback();
  });

  async function refreshSessions() {
    loading = true;
    errorMessage = "";
    try {
      const response = await listSessions();
      sessions = response.items;
      if (!currentSession && sessions.length > 0) {
        currentSession = await getSession(sessions[0].id);
      }
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to load sessions";
    } finally {
      loading = false;
    }
  }

  async function refreshLensProfiles() {
    lensProfileLoading = true;
    try {
      const response = await listLensProfiles(currentSession?.camera_id || undefined);
      lensProfiles = response.items;
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to load lens profiles";
    } finally {
      lensProfileLoading = false;
    }
  }

  function openCreateSessionDialog() {
    newSessionName = "New calibration";
    showCreateSessionDialog = true;
  }

  function closeCreateSessionDialog() {
    if (creatingSession) return;
    showCreateSessionDialog = false;
  }

  async function handleCreateSession() {
    errorMessage = "";
    creatingSession = true;
    try {
      currentSession = await createSession({ name: newSessionName.trim() || "New calibration" });
      newSessionName = "New calibration";
      showCreateSessionDialog = false;
      await refreshSessions();
      currentSession = await getSession(currentSession.id);
      await refreshLensProfiles();
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to create session";
    } finally {
      creatingSession = false;
    }
  }

  async function handleSelectSession(sessionId: string) {
    if (!sessionId) return;
    errorMessage = "";
    videoLoadError = "";
    videoStatusMessage = "";
    videoFallbackMode = false;
    stopFallbackPlayback();
    try {
      currentSession = await getSession(sessionId);
      selectedPairId = currentSession.point_pairs[0]?.id ?? "";
      lensProfileValidation = null;
      await refreshLensProfiles();
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to open session";
    }
  }

  function handleSessionSelectChange(event: Event) {
    const select = event.currentTarget as HTMLSelectElement;
    void handleSelectSession(select.value);
  }

  async function handleSaveSession() {
    if (!currentSession) return;
    saving = true;
    errorMessage = "";
    try {
      currentSession = await updateSession(currentSession);
      await refreshSessions();
      currentSession = await getSession(currentSession.id);
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to save session";
    } finally {
      saving = false;
    }
  }

  async function handleLensProfileChange(event: Event) {
    if (!currentSession) return;
    const select = event.currentTarget as HTMLSelectElement;
    currentSession = {
      ...currentSession,
      lens_profile_id: select.value,
      lens_profile_status: select.value ? "unvalidated" : "missing",
      lens_profile_warning: "",
      camera_coordinate_space: select.value ? "undistorted" : "raw",
    };
    lensProfileValidation = null;
    currentSession = await updateSession(currentSession);
  }

  async function validateSelectedLensProfile() {
    if (!currentSession) return;
    if (!currentSession.lens_profile_id) {
      lensProfileValidation = {
        status: "missing",
        valid: false,
        message: "No lens profile selected.",
        warnings: ["Raw fisheye points may produce unstable homography."],
      };
      currentSession = {
        ...currentSession,
        lens_profile_status: "missing",
        lens_profile_warning: lensProfileValidation.message,
        camera_coordinate_space: "raw",
      };
      currentSession = await updateSession(currentSession);
      return;
    }
    if (!currentSession.camera_media?.width || !currentSession.camera_media?.height) {
      lensProfileValidation = {
        status: "invalid",
        valid: false,
        message: "Load camera media before validating the lens profile.",
        warnings: [],
      };
      return;
    }

    try {
      lensProfileValidation = await validateLensProfile(
        currentSession.lens_profile_id,
        currentSession.camera_media.width,
        currentSession.camera_media.height,
      );
      currentSession = {
        ...currentSession,
        lens_profile_status: lensProfileValidation.status,
        lens_profile_warning: lensProfileValidation.valid ? "" : lensProfileValidation.message,
        camera_coordinate_space: lensProfileValidation.valid ? "undistorted" : "raw",
      };
      lensPreviewVersion += 1;
      currentSession = await updateSession(currentSession);
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to validate lens profile";
    }
  }

  async function runCalibrationAnalysis() {
    if (!currentSession) return;
    analyzing = true;
    errorMessage = "";
    try {
      const analysis = await analyzeCalibration(currentSession.point_pairs);
      currentSession = {
        ...currentSession,
        analysis: {
          ...currentSession.analysis,
          ...analysis,
        },
      };
      currentSession = await updateSession(currentSession);
      await refreshSessions();
      currentSession = await getSession(currentSession.id);
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to analyze calibration";
    } finally {
      analyzing = false;
    }
  }

  async function handleMediaUpload(event: Event, kind: "camera" | "floorplan") {
    if (!currentSession) return;
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;

    if (kind === "camera") uploadingCamera = true;
    if (kind === "floorplan") uploadingFloorplan = true;
    errorMessage = "";

    try {
      const uploaded = await uploadMedia(kind, file) as MediaReference;
      if (kind === "camera") {
        videoLoadError = "";
        videoStatusMessage = "Loading video metadata...";
        videoFallbackMode = false;
        lensProfileValidation = null;
        lensPreviewVersion += 1;
        stopFallbackPlayback();
        currentSession.camera_media = uploaded;
        currentSession.selected_timestamp_seconds = 0;
        currentSession.lens_profile_status = currentSession.lens_profile_id ? "unvalidated" : "missing";
        currentSession.lens_profile_warning = "";
      } else {
        currentSession.floorplan_media = uploaded;
      }
      currentSession = await updateSession(currentSession);
      await refreshSessions();
      currentSession = await getSession(currentSession.id);
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : `Failed to upload ${kind} media`;
    } finally {
      uploadingCamera = false;
      uploadingFloorplan = false;
      input.value = "";
    }
  }

  async function persistMediaMetadata(kind: "camera" | "floorplan") {
    if (!currentSession) return;
    if (kind === "camera" && currentSession.camera_media && videoElement) {
      videoLoadError = "";
      videoStatusMessage = "Video ready.";
      currentSession.camera_media = {
        ...currentSession.camera_media,
        width: videoElement.videoWidth || currentSession.camera_media.width,
        height: videoElement.videoHeight || currentSession.camera_media.height,
        duration_seconds: Number.isFinite(videoElement.duration) ? videoElement.duration : null,
      };
    }
    if (kind === "camera" && currentSession.camera_media && cameraImageElement) {
      currentSession.camera_media = {
        ...currentSession.camera_media,
        width: cameraImageElement.naturalWidth || currentSession.camera_media.width,
        height: cameraImageElement.naturalHeight || currentSession.camera_media.height,
      };
    }
    if (kind === "floorplan" && currentSession.floorplan_media && floorplanElement) {
      currentSession.floorplan_media = {
        ...currentSession.floorplan_media,
        width: floorplanElement.naturalWidth || currentSession.floorplan_media.width,
        height: floorplanElement.naturalHeight || currentSession.floorplan_media.height,
      };
    }
    currentSession = await updateSession(currentSession);
  }

  async function ensureFallbackVideoMetadata() {
    if (!currentSession?.camera_media?.path) return;
    try {
      const metadata = await getVideoMetadata(currentSession.camera_media.path);
      currentSession.camera_media = {
        ...currentSession.camera_media,
        width: metadata.width ?? currentSession.camera_media.width,
        height: metadata.height ?? currentSession.camera_media.height,
        duration_seconds: metadata.duration_seconds ?? currentSession.camera_media.duration_seconds,
      };
      currentSession = await updateSession(currentSession);
    } catch (error) {
      videoLoadError = error instanceof Error ? error.message : "Could not read video metadata.";
    }
  }

  function handleVideoError() {
    const code = videoElement?.error?.code;
    const reason = code === MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED
      ? "The browser cannot play this video codec. Try an H.264 MP4, or use a converted snapshot for now."
      : code === MediaError.MEDIA_ERR_NETWORK
        ? "The video could not be loaded from the local backend."
        : code === MediaError.MEDIA_ERR_DECODE
          ? "The browser could not decode this video."
          : "The video could not be loaded.";
    videoPlaying = false;
    videoStatusMessage = "";
    videoFallbackMode = true;
    videoStatusMessage = "Using extracted-frame preview for this camera video.";
    videoLoadError = `${reason} Switched to extracted-frame preview.`;
    fallbackFrameVersion += 1;
    void ensureFallbackVideoMetadata();
  }

  function updateTimestampFromVideo() {
    if (!currentSession || !videoElement) return;
    currentSession.selected_timestamp_seconds = videoElement.currentTime;
  }

  async function commitTimestampFromVideo() {
    updateTimestampFromVideo();
    if (currentSession) currentSession = await updateSession(currentSession);
  }

  async function togglePlayback() {
    if (videoFallbackMode) {
      toggleFallbackPlayback();
      return;
    }
    if (!videoElement) return;
    if (videoElement.paused) {
      try {
        videoLoadError = "";
        await videoElement.play();
      } catch (error) {
        videoPlaying = false;
        videoLoadError = error instanceof Error
          ? `Video playback failed: ${error.message}`
          : "Video playback failed.";
      }
    } else {
      videoElement.pause();
    }
  }

  async function seekBy(seconds: number) {
    if (videoFallbackMode) {
      await seekFallbackBy(seconds);
      return;
    }
    if (!videoElement) return;
    videoElement.currentTime = Math.max(0, Math.min(videoElement.duration || 0, videoElement.currentTime + seconds));
    await commitTimestampFromVideo();
  }

  async function stepFrame(direction: -1 | 1) {
    if (videoFallbackMode) {
      await stepFallbackFrame(direction);
      return;
    }
    if (!videoElement) return;
    videoElement.pause();
    const frameStep = 1 / 30;
    videoElement.currentTime = Math.max(0, Math.min(videoElement.duration || 0, videoElement.currentTime + frameStep * direction));
    await commitTimestampFromVideo();
  }

  function handlePlaybackRateChange(event: Event) {
    playbackRate = Number((event.currentTarget as HTMLSelectElement).value);
    if (videoElement) videoElement.playbackRate = playbackRate;
  }

  function stopFallbackPlayback() {
    if (fallbackPlaybackTimer) {
      clearInterval(fallbackPlaybackTimer);
      fallbackPlaybackTimer = null;
    }
    videoPlaying = false;
  }

  function toggleFallbackPlayback() {
    if (!currentSession?.camera_media) return;
    if (fallbackPlaybackTimer) {
      void commitTimestampFromFallback();
      stopFallbackPlayback();
      return;
    }

    videoPlaying = true;
    const stepSeconds = 0.1 * playbackRate;
    fallbackPlaybackTimer = setInterval(() => {
      if (!currentSession?.camera_media) return;
      const duration = currentSession.camera_media.duration_seconds ?? 0;
      const nextTime = (currentSession.selected_timestamp_seconds ?? 0) + stepSeconds;
      if (duration > 0 && nextTime >= duration) {
        currentSession.selected_timestamp_seconds = duration;
        fallbackFrameVersion += 1;
        void commitTimestampFromFallback();
        stopFallbackPlayback();
        return;
      }
      currentSession.selected_timestamp_seconds = nextTime;
      fallbackFrameVersion += 1;
    }, 100);
  }

  async function commitTimestampFromFallback() {
    if (currentSession) currentSession = await updateSession(currentSession);
  }

  async function seekFallbackBy(seconds: number) {
    if (!currentSession?.camera_media) return;
    const duration = currentSession.camera_media.duration_seconds ?? 0;
    currentSession.selected_timestamp_seconds = Math.max(
      0,
      duration ? Math.min(duration, selectedTimestamp + seconds) : selectedTimestamp + seconds,
    );
    fallbackFrameVersion += 1;
    await commitTimestampFromFallback();
  }

  async function stepFallbackFrame(direction: -1 | 1) {
    if (!currentSession?.camera_media) return;
    stopFallbackPlayback();
    const fps = 20;
    const frameStep = 1 / fps;
    const duration = currentSession.camera_media.duration_seconds ?? 0;
    currentSession.selected_timestamp_seconds = Math.max(
      0,
      duration ? Math.min(duration, selectedTimestamp + frameStep * direction) : selectedTimestamp + frameStep * direction,
    );
    fallbackFrameVersion += 1;
    await commitTimestampFromFallback();
  }

  async function addBookmark() {
    if (!currentSession) return;
    const timestamp = Number((videoFallbackMode ? selectedTimestamp : (videoElement?.currentTime ?? 0)).toFixed(3));
    const exists = currentSession.bookmarks.some((item) => Math.abs(item - timestamp) < 0.05);
    if (!exists) {
      currentSession.bookmarks = [...currentSession.bookmarks, timestamp].sort((a, b) => a - b);
      currentSession.selected_timestamp_seconds = timestamp;
      currentSession = await updateSession(currentSession);
      await refreshSessions();
      currentSession = await getSession(currentSession.id);
    }
  }

  async function removeBookmark(timestamp: number) {
    if (!currentSession) return;
    currentSession.bookmarks = currentSession.bookmarks.filter((item) => item !== timestamp);
    currentSession = await updateSession(currentSession);
    await refreshSessions();
    currentSession = await getSession(currentSession.id);
  }

  async function jumpToBookmark(timestamp: number) {
    if (videoFallbackMode) {
      if (!currentSession) return;
      currentSession.selected_timestamp_seconds = timestamp;
      fallbackFrameVersion += 1;
      await commitTimestampFromFallback();
      return;
    }
    if (!videoElement) return;
    videoElement.currentTime = timestamp;
    await commitTimestampFromVideo();
  }

  function formatDate(value: string) {
    return new Intl.DateTimeFormat("en-NZ", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(value));
  }

  function formatDuration(value?: number | null) {
    if (!value || !Number.isFinite(value)) return "00:00.000";
    const minutes = Math.floor(value / 60);
    const seconds = Math.floor(value % 60);
    const millis = Math.round((value % 1) * 1000);
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
  }

  function makePointPair(camera?: Point | null, floorMap?: Point | null): PointPair {
    return {
      id: crypto.randomUUID(),
      camera: camera ?? null,
      undistorted_camera: camera && showUndistortedCamera ? camera : null,
      floor_map: floorMap ?? null,
      enabled: true,
      label: "",
    };
  }

  function naturalPointFromEvent(event: PointerEvent | MouseEvent, side: "camera" | "floor_map"): Point | null {
    const element = event.currentTarget instanceof SVGSVGElement
      ? event.currentTarget
      : side === "camera"
        ? cameraOverlayElement
        : floorplanOverlayElement;
    if (!(element instanceof SVGSVGElement)) return null;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const width = side === "camera" ? cameraNaturalWidth : floorplanNaturalWidth;
    const height = side === "camera" ? cameraNaturalHeight : floorplanNaturalHeight;
    const x = Math.max(0, Math.min(width, ((event.clientX - rect.left) / rect.width) * width));
    const y = Math.max(0, Math.min(height, ((event.clientY - rect.top) / rect.height) * height));
    return { x: Math.round(x), y: Math.round(y) };
  }

  function setPointOnPair(pairId: string, side: "camera" | "floor_map", point: Point) {
    if (!currentSession) return;
    const patch = side === "camera" && showUndistortedCamera
      ? { camera: point, undistorted_camera: point }
      : { [side]: point };
    currentSession = {
      ...currentSession,
      point_pairs: currentSession.point_pairs.map((pair) =>
        pair.id === pairId ? { ...pair, ...patch } : pair
      ),
    };
  }

  function handleCanvasClick(event: MouseEvent, side: "camera" | "floor_map") {
    if (!currentSession || activeTool !== "add") return;
    if ((event.target as Element).closest(".point-marker")) return;
    const point = naturalPointFromEvent(event, side);
    if (!point) return;

    const existing = currentSession.point_pairs.find((pair) => pair.id === selectedPairId);
    if (existing && !existing[side]) {
      setPointOnPair(existing.id, side, point);
      selectedPairId = existing.id;
      return;
    }

    const pair = makePointPair(side === "camera" ? point : null, side === "floor_map" ? point : null);
    currentSession = {
      ...currentSession,
      point_pairs: [...currentSession.point_pairs, pair],
    };
    selectedPairId = pair.id;
  }

  function handleOverlayKeydown(event: KeyboardEvent, side: "camera" | "floor_map") {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    const width = side === "camera" ? cameraNaturalWidth : floorplanNaturalWidth;
    const height = side === "camera" ? cameraNaturalHeight : floorplanNaturalHeight;
    const point = { x: Math.round(width / 2), y: Math.round(height / 2) };
    const existing = currentSession?.point_pairs.find((pair) => pair.id === selectedPairId);
    if (existing && !existing[side]) {
      setPointOnPair(existing.id, side, point);
      return;
    }
    if (!currentSession) return;
    const pair = makePointPair(side === "camera" ? point : null, side === "floor_map" ? point : null);
    currentSession = { ...currentSession, point_pairs: [...currentSession.point_pairs, pair] };
    selectedPairId = pair.id;
  }

  function selectPair(pairId: string) {
    selectedPairId = pairId;
    activeTool = "select";
  }

  function togglePair(pairId: string) {
    if (!currentSession) return;
    currentSession = {
      ...currentSession,
      point_pairs: currentSession.point_pairs.map((pair) =>
        pair.id === pairId ? { ...pair, enabled: !pair.enabled } : pair
      ),
    };
  }

  function deletePair(pairId: string) {
    if (!currentSession) return;
    const remaining = currentSession.point_pairs.filter((pair) => pair.id !== pairId);
    currentSession = { ...currentSession, point_pairs: remaining };
    if (selectedPairId === pairId) selectedPairId = remaining[0]?.id ?? "";
  }

  function clearPairs() {
    if (!currentSession) return;
    currentSession = { ...currentSession, point_pairs: [] };
    selectedPairId = "";
  }

  function startDragging(event: PointerEvent, pairId: string, side: "camera" | "floor_map") {
    event.stopPropagation();
    event.preventDefault();
    selectedPairId = pairId;
    activeTool = "select";
    dragState = { pairId, side };
  }

  function handleGlobalPointerMove(event: PointerEvent) {
    if (!dragState) return;
    const point = naturalPointFromEvent(event, dragState.side);
    if (point) setPointOnPair(dragState.pairId, dragState.side, point);
  }

  function stopDragging() {
    dragState = null;
  }

  function pairStatus(pair: PointPair): string {
    if (outlierIds.has(pair.id)) return "Outlier";
    if (!pair.camera) return "Missing camera point";
    if (!pair.floor_map) return "Missing floor point";
    if (!pair.enabled) return "Disabled";
    return "Ready";
  }

  function pointLabel(point?: Point | null): string {
    if (!point) return "-";
    return `${Math.round(point.x)}, ${Math.round(point.y)}`;
  }

  function pointIndex(pairId: string): number {
    return (currentSession?.point_pairs.findIndex((pair) => pair.id === pairId) ?? -1) + 1;
  }

  function pointError(pairId: string): string {
    const error = homography?.errors?.[pairId];
    return typeof error === "number" ? `${error.toFixed(1)} px` : "-";
  }

  function tpsError(pairId: string): string {
    const error = tps?.errors?.[pairId];
    return typeof error === "number" ? `${error.toFixed(1)} px` : "-";
  }

  function zoom(side: "camera" | "floor_map", delta: number) {
    if (side === "camera") cameraZoom = Math.max(0.5, Math.min(3, Number((cameraZoom + delta).toFixed(2))));
    if (side === "floor_map") floorplanZoom = Math.max(0.5, Math.min(3, Number((floorplanZoom + delta).toFixed(2))));
  }

  // --- Validation screen ---
  async function enterValidate() {
    if (!currentSession || !validationReady) return;
    workspaceMode = "validate";
    await loadValidationField();
  }

  function exitValidate() {
    workspaceMode = "calibrate";
    clearHover();
    validationTrail = [];
  }

  function clearHover() {
    hoverCameraPx = null;
    hoverUndistorted = null;
    hoverHomography = null;
    hoverTps = null;
  }

  async function loadValidationField() {
    if (!currentSession?.camera_media?.width || !currentSession.camera_media.height) return;
    validationLoading = true;
    clearHover();
    validationTrail = [];
    errorMessage = "";
    try {
      const field = await getValidationField({
        point_pairs: currentSession.point_pairs,
        lens_profile_id: currentSession.lens_profile_id || "",
        width: currentSession.camera_media.width,
        height: currentSession.camera_media.height,
      });
      if (validationStage === "tps" && !field.has_tps) validationStage = "homography";
      validationField = field;
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : "Failed to build validation field";
    } finally {
      validationLoading = false;
    }
  }

  function interpStage(stage: number[][] | null, px: number, py: number): Point | null {
    if (!validationField || !stage) return null;
    const { cols, rows, width, height } = validationField;
    const cf = Math.max(0, Math.min(cols - 1, (px / Math.max(width - 1, 1)) * (cols - 1)));
    const rf = Math.max(0, Math.min(rows - 1, (py / Math.max(height - 1, 1)) * (rows - 1)));
    const c0 = Math.floor(cf);
    const r0 = Math.floor(rf);
    const c1 = Math.min(c0 + 1, cols - 1);
    const r1 = Math.min(r0 + 1, rows - 1);
    const tx = cf - c0;
    const ty = rf - r0;
    const at = (r: number, c: number) => stage[r * cols + c];
    const v00 = at(r0, c0);
    const v10 = at(r0, c1);
    const v01 = at(r1, c0);
    const v11 = at(r1, c1);
    const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
    const topX = lerp(v00[0], v10[0], tx);
    const topY = lerp(v00[1], v10[1], tx);
    const botX = lerp(v01[0], v11[0], tx);
    const botY = lerp(v01[1], v11[1], tx);
    return { x: lerp(topX, botX, ty), y: lerp(topY, botY, ty) };
  }

  function handleValidationMove(event: PointerEvent) {
    if (!validationField || !validationCameraOverlay) return;
    const rect = validationCameraOverlay.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const px = Math.max(0, Math.min(cameraNaturalWidth, ((event.clientX - rect.left) / rect.width) * cameraNaturalWidth));
    const py = Math.max(0, Math.min(cameraNaturalHeight, ((event.clientY - rect.top) / rect.height) * cameraNaturalHeight));
    hoverCameraPx = { x: px, y: py };
    hoverUndistorted = interpStage(validationField.undistorted, px, py);
    hoverHomography = interpStage(validationField.homography, px, py);
    hoverTps = interpStage(validationField.tps, px, py);
    const floor = validationStage === "tps" ? hoverTps : hoverHomography;
    if (validationMode === "trail" && floor) {
      validationTrail = [...validationTrail, { camera: { x: px, y: py }, floor }].slice(-TRAIL_MAX);
    }
  }

  function fmtPoint(point: Point | null): string {
    if (!point) return "—";
    return `${Math.round(point.x)}, ${Math.round(point.y)}`;
  }
</script>

<main class="app-shell">
  <header class="topbar">
    <div>
      <h1>Video Calibration</h1>
    </div>
    <div class="topbar__actions">
      <div class="mode-toggle">
        <button class:button--primary={workspaceMode === "calibrate"} class="button" on:click={exitValidate}>Calibrate</button>
        <button
          class:button--primary={workspaceMode === "validate"}
          class="button"
          disabled={!validationReady}
          title={validationReady ? "Open validation screen" : "Run a homography first (needs camera, floorplan, and 4+ pairs)"}
          on:click={enterValidate}
        >
          Validate
        </button>
      </div>
      <span class="status-pill">{currentSession ? "Session open" : "No session"}</span>
      <button class="button button--primary" disabled={!currentSession || saving} on:click={handleSaveSession}>
        {saving ? "Saving" : "Save session"}
      </button>
    </div>
  </header>

  {#if errorMessage}
    <section class="notice notice--error">{errorMessage}</section>
  {/if}

  <section class="workspace">
    {#if workspaceMode === "calibrate"}
    <aside class="sidebar">
      <section class="panel">
        <div class="panel__header">
          <h2>Sessions</h2>
          {#if loading}<span>Loading</span>{/if}
        </div>
        <div class="session-picker">
          <select
            aria-label="Select calibration session"
            value={currentSession?.id ?? ""}
            disabled={loading || sessions.length === 0}
            on:change={handleSessionSelectChange}
          >
            {#if sessions.length === 0}
              <option value="">No sessions yet</option>
            {:else}
              {#each sessions as session}
                <option value={session.id}>{session.name} ({session.point_pair_count} pairs)</option>
              {/each}
            {/if}
          </select>
          <button class="button" on:click={openCreateSessionDialog}>New</button>
        </div>
        {#if currentSession}
          <p class="session-summary">{currentSession.point_pairs.length} point pairs</p>
        {:else}
          <p class="empty-copy">Create a session to begin the calibration workflow.</p>
        {/if}
      </section>

      <section class="panel">
        <div class="panel__header">
          <h2>Reference IDs</h2>
        </div>
        {#if currentSession}
          <label>
            Camera ID
            <input bind:value={currentSession.camera_id} placeholder="Paste camera UUID" />
          </label>
          <label>
            Floor ID
            <input bind:value={currentSession.floor_id} placeholder="Paste floor UUID" />
          </label>
          <label>
            Floor map ID
            <input bind:value={currentSession.floor_map_id} placeholder="Paste floor map UUID" />
          </label>
        {:else}
          <p class="empty-copy">Open a session before entering reference IDs.</p>
        {/if}
      </section>

      <section class="panel">
        <div class="panel__header">
          <h2>Media</h2>
        </div>
        {#if currentSession}
          <label>
            Camera video or image
            <input
              type="file"
              accept="video/*,image/*"
              disabled={uploadingCamera}
              on:change={(event) => handleMediaUpload(event, "camera")}
            />
          </label>
          <p class="media-meta">{currentSession.camera_media?.original_name || "No camera media loaded"}</p>
          <label>
            Floorplan image
            <input
              type="file"
              accept="image/*"
              disabled={uploadingFloorplan}
              on:change={(event) => handleMediaUpload(event, "floorplan")}
            />
          </label>
          <p class="media-meta">{currentSession.floorplan_media?.original_name || "No floorplan loaded"}</p>
        {:else}
          <p class="empty-copy">Open a session before loading media.</p>
        {/if}
      </section>

      <section class="panel">
        <div class="panel__header">
          <div>
            <h2>Lens Correction</h2>
            <p>Apply fisheye correction before homography.</p>
          </div>
        </div>
        {#if currentSession}
          <label>
            Lens profile
            <select
              value={currentSession.lens_profile_id ?? ""}
              disabled={lensProfileLoading}
              on:change={handleLensProfileChange}
            >
              <option value="">No profile selected</option>
              {#each lensProfiles as profile}
                <option value={profile.id}>{profile.profile_name} ({profile.source_width}x{profile.source_height})</option>
              {/each}
            </select>
          </label>
          <div class="tool-stack tool-stack--split">
            <button class="button" disabled={lensProfileLoading} on:click={refreshLensProfiles}>
              {lensProfileLoading ? "Loading" : "Refresh profiles"}
            </button>
            <button class="button button--primary" disabled={!currentSession.camera_media} on:click={validateSelectedLensProfile}>
              Validate profile
            </button>
          </div>
          <dl class="lens-status">
            <div>
              <dt>Frame</dt>
              <dd>{currentSession.camera_media?.width ?? "-"} x {currentSession.camera_media?.height ?? "-"}</dd>
            </div>
            <div>
              <dt>Profile</dt>
              <dd>{selectedLensProfile ? `${selectedLensProfile.source_width} x ${selectedLensProfile.source_height}` : "None"}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd class:lens-status--valid={lensProfileValidation?.valid} class:lens-status--warning={lensProfileValidation && !lensProfileValidation.valid}>
                {lensProfileValidation?.message || currentSession.lens_profile_status || "Not validated"}
              </dd>
            </div>
          </dl>
          {#if lensProfileValidation?.warnings?.length}
            <p class="video-message video-message--error">{lensProfileValidation.warnings.join(" ")}</p>
          {/if}
          {#if lensPreviewUrl}
            <img class="lens-preview" src={lensPreviewUrl} alt="Undistorted camera preview" />
          {:else}
            <p class="empty-copy">No undistorted preview yet. Select a matching profile and validate it.</p>
          {/if}
        {:else}
          <p class="empty-copy">Open a session before selecting a lens profile.</p>
        {/if}
      </section>

      <section class="panel">
        <div class="panel__header">
          <h2>Point tools</h2>
        </div>
        <div class="tool-stack">
          <button class:button--primary={activeTool === "add"} class="button" disabled={!currentSession} on:click={() => activeTool = "add"}>
            Add pair
          </button>
          <button class:button--primary={activeTool === "select"} class="button" disabled={!currentSession} on:click={() => activeTool = "select"}>
            Select/move
          </button>
        </div>
        <p class="empty-copy">
          Add pair: click one camera landmark, then the matching floorplan landmark. Select/move: drag existing markers.
        </p>
        <div class="tool-stack tool-stack--split">
          <button class="button button--primary" disabled={!currentSession || enabledPairCount < 3 || analyzing} on:click={runCalibrationAnalysis}>
            {analyzing ? "Analyzing" : "Analyze calibration"}
          </button>
          <div class="transform-toggle">
            <button class:button--primary={transformMode === "homography"} class="button" disabled={!homography?.ready} on:click={() => transformMode = "homography"}>Homography</button>
            <button class:button--primary={transformMode === "tps"} class="button" disabled={!tps?.ready} on:click={() => transformMode = "tps"}>TPS</button>
            <button class:button--primary={transformMode === "compare"} class="button" disabled={!homography?.ready || !tps?.ready} on:click={() => transformMode = "compare"}>Compare</button>
          </div>
          <button class="button" disabled={!selectedPair} on:click={() => selectedPair && togglePair(selectedPair.id)}>
            {selectedPair?.enabled === false ? "Enable selected" : "Disable selected"}
          </button>
          <button class="button" disabled={!selectedPair} on:click={() => selectedPair && deletePair(selectedPair.id)}>
            Delete selected
          </button>
          <button class="button" disabled={!currentSession?.point_pairs.length} on:click={clearPairs}>
            Clear pairs
          </button>
        </div>
      </section>
    </aside>

    <section class="main-grid">
      <section class="panel canvas-panel canvas-panel--camera">
        <div class="panel__header">
          <div>
            <h2>Camera video</h2>
            <p>{currentSession?.camera_media?.original_name || "Load a camera video or still image."}</p>
          </div>
        </div>
        {#if cameraMediaUrl}
          <div class="canvas-toolbar">
            <span>{showUndistortedCamera ? "Undistorted view — points are lens-corrected" : "Raw view"} · {activeTool === "add" ? "click to place camera point" : "drag camera markers"}</span>
            {#if canShowUndistorted}
              <button class:button--primary={useUndistortedView} class="button" on:click={() => useUndistortedView = !useUndistortedView}>
                {useUndistortedView ? "Undistorted" : "Raw"}
              </button>
            {/if}
            <button class="button" on:click={() => zoom("camera", -0.1)}>-</button>
            <button class="button" on:click={() => cameraZoom = 1}>Reset</button>
            <button class="button" on:click={() => zoom("camera", 0.1)}>+</button>
          </div>
          <div class="media-scroll">
            <div class="media-stage" style={`--stage-zoom: ${cameraZoom}; --media-aspect: ${cameraNaturalWidth} / ${cameraNaturalHeight}`}>
              {#if showUndistortedCamera}
                <img
                  class="media-viewer"
                  src={lensPreviewUrl}
                  alt="Undistorted camera frame"
                />
              {:else if isCameraImage}
                <img
                  bind:this={cameraImageElement}
                  class="media-viewer"
                  src={cameraMediaUrl}
                  alt="Camera reference"
                  on:load={() => persistMediaMetadata("camera")}
                />
              {:else if videoFallbackMode}
                <img
                  bind:this={cameraImageElement}
                  class="media-viewer"
                  src={fallbackFrameUrl}
                  alt="Extracted camera video frame"
                />
              {:else}
                <video
                  bind:this={videoElement}
                  class="media-viewer"
                  src={cameraMediaUrl}
                  preload="metadata"
                  on:loadedmetadata={() => persistMediaMetadata("camera")}
                  on:error={handleVideoError}
                  on:timeupdate={updateTimestampFromVideo}
                  on:pause={() => { videoPlaying = false; commitTimestampFromVideo(); }}
                  on:play={() => { videoPlaying = true; }}
                >
                  <track kind="captions" />
                </video>
              {/if}
              <svg
                bind:this={cameraOverlayElement}
                aria-label="Camera point overlay"
                class:point-overlay--adding={activeTool === "add"}
                class="point-overlay"
                role="button"
                tabindex="0"
                viewBox={`0 0 ${cameraNaturalWidth} ${cameraNaturalHeight}`}
                preserveAspectRatio="none"
                on:click={(event) => handleCanvasClick(event, "camera")}
                on:keydown={(event) => handleOverlayKeydown(event, "camera")}
              >
                {#each currentSession?.point_pairs ?? [] as pair}
                  {#if pair.camera}
                    <g
                      class:point-marker--selected={pair.id === selectedPairId}
                      class:point-marker--disabled={!pair.enabled}
                      class="point-marker"
                      transform={`translate(${pair.camera.x} ${pair.camera.y})`}
                      on:pointerdown={(event) => startDragging(event, pair.id, "camera")}
                    >
                      <circle r="13" />
                      <text y="5">{pointIndex(pair.id)}</text>
                    </g>
                  {/if}
                {/each}
              </svg>
            </div>
          </div>
          {#if videoLoadError}
            <p class="video-message video-message--error">{videoLoadError}</p>
          {:else if videoStatusMessage}
            <p class="video-message">{videoStatusMessage}</p>
          {/if}
          {#if !isCameraImage}
            <div class="video-controls">
              <button class="button" on:click={togglePlayback}>{videoPlaying ? "Pause" : "Play"}</button>
              <button class="button" on:click={() => stepFrame(-1)}>Prev frame</button>
              <button class="button" on:click={() => stepFrame(1)}>Next frame</button>
              <button class="button" on:click={() => seekBy(-5)}>-5s</button>
              <button class="button" on:click={() => seekBy(-1)}>-1s</button>
              <button class="button" on:click={() => seekBy(1)}>+1s</button>
              <button class="button" on:click={() => seekBy(5)}>+5s</button>
              <label class="inline-label">
                Speed
                <select bind:value={playbackRate} on:change={handlePlaybackRateChange}>
                  <option value={0.25}>0.25x</option>
                  <option value={0.5}>0.5x</option>
                  <option value={1}>1x</option>
                  <option value={1.5}>1.5x</option>
                  <option value={2}>2x</option>
                </select>
              </label>
              <button class="button button--primary" on:click={addBookmark}>Bookmark frame</button>
            </div>
            <div class="timestamp-row">
              <span>Current: {formatDuration(selectedTimestamp)}</span>
              <span>Duration: {formatDuration(currentSession?.camera_media?.duration_seconds)}</span>
            </div>
          {/if}
        {:else}
          <div class="placeholder-frame">
            <strong>No camera media loaded</strong>
            <span>Video-assisted calibration will live here.</span>
          </div>
        {/if}
      </section>

      <section class="panel canvas-panel canvas-panel--floorplan">
        <div class="panel__header">
          <div>
            <h2>Floorplan</h2>
            <p>{currentSession?.floorplan_media?.original_name || "Load the floorplan image for this camera."}</p>
          </div>
        </div>
        {#if floorplanMediaUrl}
          <div class="canvas-toolbar">
            <span>{activeTool === "add" ? "Click to place floorplan point" : "Drag floorplan markers"}</span>
            <button class="button" on:click={() => zoom("floor_map", -0.1)}>-</button>
            <button class="button" on:click={() => floorplanZoom = 1}>Reset</button>
            <button class="button" on:click={() => zoom("floor_map", 0.1)}>+</button>
          </div>
          <div class="media-scroll">
            <div class="media-stage" style={`--stage-zoom: ${floorplanZoom}; --media-aspect: ${floorplanNaturalWidth} / ${floorplanNaturalHeight}`}>
              <img
                bind:this={floorplanElement}
                class="media-viewer media-viewer--floorplan"
                src={floorplanMediaUrl}
                alt="Floorplan"
                on:load={() => persistMediaMetadata("floorplan")}
              />
              <svg
                bind:this={floorplanOverlayElement}
                aria-label="Floorplan point overlay"
                class:point-overlay--adding={activeTool === "add"}
                class="point-overlay"
                role="button"
                tabindex="0"
                viewBox={`0 0 ${floorplanNaturalWidth} ${floorplanNaturalHeight}`}
                preserveAspectRatio="none"
                on:click={(event) => handleCanvasClick(event, "floor_map")}
                on:keydown={(event) => handleOverlayKeydown(event, "floor_map")}
              >
                {#each currentSession?.point_pairs ?? [] as pair}
                  {#if pair.floor_map}
                    <g
                      class:point-marker--outlier={outlierIds.has(pair.id)}
                      class:point-marker--selected={pair.id === selectedPairId}
                      class:point-marker--disabled={!pair.enabled}
                      class="point-marker point-marker--floor"
                      transform={`translate(${pair.floor_map.x} ${pair.floor_map.y})`}
                      on:pointerdown={(event) => startDragging(event, pair.id, "floor_map")}
                    >
                      <circle r="13" />
                      <text y="5">{pointIndex(pair.id)}</text>
                    </g>
                  {/if}
                {/each}
                {#if transformMode === "homography" || transformMode === "compare"}
                  {#each currentSession?.point_pairs ?? [] as pair}
                    {@const projected = homography?.projected_points?.[pair.id]}
                    {#if pair.floor_map && projected}
                      <line
                        class:projection-line--outlier={homographyOutlierIds.has(pair.id)}
                        class="projection-line"
                        x1={pair.floor_map.x}
                        y1={pair.floor_map.y}
                        x2={projected.x}
                        y2={projected.y}
                      />
                      <g class:projection-point--outlier={homographyOutlierIds.has(pair.id)} class="projection-point" transform={`translate(${projected.x} ${projected.y})`}>
                        <circle r="8" />
                        <text y="-12">{pointIndex(pair.id)}</text>
                      </g>
                    {/if}
                  {/each}
                {/if}
                {#if transformMode === "tps" || transformMode === "compare"}
                  {#each currentSession?.point_pairs ?? [] as pair}
                    {@const tpsProjected = tps?.projected_points?.[pair.id]}
                    {#if pair.floor_map && tpsProjected}
                      <line
                        class:tps-projection-line--outlier={tpsOutlierIds.has(pair.id)}
                        class="tps-projection-line"
                        x1={pair.floor_map.x}
                        y1={pair.floor_map.y}
                        x2={tpsProjected.x}
                        y2={tpsProjected.y}
                      />
                      <g class:tps-projection-point--outlier={tpsOutlierIds.has(pair.id)} class="tps-projection-point" transform={`translate(${tpsProjected.x} ${tpsProjected.y})`}>
                        <circle r="8" />
                        <text y="-12">{pointIndex(pair.id)}</text>
                      </g>
                    {/if}
                  {/each}
                {/if}
              </svg>
            </div>
          </div>
        {:else}
          <div class="placeholder-frame placeholder-frame--floor">
            <strong>No floorplan loaded</strong>
            <span>Mapped points and projection previews will appear here.</span>
          </div>
        {/if}
      </section>

      <section class="panel table-panel">
        <div class="panel__header">
          <div>
            <h2>Point pairs</h2>
            <p>{enabledPairCount} complete enabled pairs. Homography needs 4+, TPS needs 3+.</p>
          </div>
          <span>{currentSession?.point_pairs.length ?? 0} pairs</span>
        </div>
        <div class="point-table">
          <div class="point-table__head">#</div>
          <div class="point-table__head">Camera X/Y</div>
          <div class="point-table__head">Floor X/Y</div>
          <div class="point-table__head">H error</div>
          <div class="point-table__head">TPS error</div>
          <div class="point-table__head">Status</div>
          <div class="point-table__head">Actions</div>
          {#each currentSession?.point_pairs ?? [] as pair, index}
            <button
              class:point-table__cell--selected={pair.id === selectedPairId}
              class:point-table__cell--outlier={outlierIds.has(pair.id)}
              class="point-table__cell point-table__cell--index"
              on:click={() => selectPair(pair.id)}
            >
              {index + 1}
            </button>
            <button
              class:point-table__cell--selected={pair.id === selectedPairId}
              class:point-table__cell--outlier={outlierIds.has(pair.id)}
              class="point-table__cell"
              on:click={() => selectPair(pair.id)}
            >
              {pointLabel(pair.camera)}
            </button>
            <button
              class:point-table__cell--selected={pair.id === selectedPairId}
              class:point-table__cell--outlier={outlierIds.has(pair.id)}
              class="point-table__cell"
              on:click={() => selectPair(pair.id)}
            >
              {pointLabel(pair.floor_map)}
            </button>
            <button
              class:point-table__cell--selected={pair.id === selectedPairId}
              class:point-table__cell--outlier={outlierIds.has(pair.id)}
              class="point-table__cell"
              on:click={() => selectPair(pair.id)}
            >
              {pointError(pair.id)}
            </button>
            <button
              class:point-table__cell--selected={pair.id === selectedPairId}
              class:point-table__cell--outlier={tpsOutlierIds.has(pair.id)}
              class="point-table__cell"
              on:click={() => selectPair(pair.id)}
            >
              {tpsError(pair.id)}
            </button>
            <button
              class:point-table__cell--selected={pair.id === selectedPairId}
              class:point-table__cell--outlier={outlierIds.has(pair.id)}
              class="point-table__cell"
              on:click={() => selectPair(pair.id)}
            >
              {pairStatus(pair)}
            </button>
            <div
              class:point-table__cell--selected={pair.id === selectedPairId}
              class:point-table__cell--outlier={outlierIds.has(pair.id)}
              class="point-table__cell point-table__actions"
            >
              <button class="button" on:click={() => togglePair(pair.id)}>{pair.enabled ? "Disable" : "Enable"}</button>
              <button class="button" on:click={() => deletePair(pair.id)}>Delete</button>
            </div>
          {:else}
            <div class="point-table__empty">No point pairs yet. Use Add pair, then click the camera and floorplan landmarks.</div>
          {/each}
        </div>
      </section>

      <section class="panel summary-panel">
        <div class="panel__header">
          <div>
            <h2>Session status</h2>
            <p>{homography?.message || "Add at least 4 complete enabled point pairs to calculate homography and TPS."}</p>
          </div>
        </div>
        {#if currentSession}
          <dl class="meta-grid">
            <div>
              <dt>Name</dt>
              <dd><input bind:value={currentSession.name} /></dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{formatDate(currentSession.created_at)}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatDate(currentSession.updated_at)}</dd>
            </div>
            <div>
              <dt>Bookmarks</dt>
              <dd>{currentSession.bookmarks.length}</dd>
            </div>
            <div>
              <dt>Homography</dt>
              <dd>{homography?.ready ? homography.status : "Not ready"}</dd>
            </div>
            <div>
              <dt>H mean error</dt>
              <dd>{typeof homography?.mean_error === "number" ? `${homography.mean_error.toFixed(1)} px` : "-"}</dd>
            </div>
            <div>
              <dt>H max error</dt>
              <dd>{typeof homography?.max_error === "number" ? `${homography.max_error.toFixed(1)} px` : "-"}</dd>
            </div>
            <div>
              <dt>H outliers</dt>
              <dd>{homography?.outlier_ids?.length ?? 0}</dd>
            </div>
            <div>
              <dt>TPS</dt>
              <dd>{tps?.ready ? tps.status : "Not ready"}</dd>
            </div>
            <div>
              <dt>TPS mean error</dt>
              <dd>{typeof tps?.mean_error === "number" ? `${tps.mean_error.toFixed(1)} px` : "-"}</dd>
            </div>
            <div>
              <dt>TPS max error</dt>
              <dd>{typeof tps?.max_error === "number" ? `${tps.max_error.toFixed(1)} px` : "-"}</dd>
            </div>
            <div>
              <dt>TPS outliers</dt>
              <dd>{tps?.outlier_ids?.length ?? 0}</dd>
            </div>
          </dl>
          {#if homography?.matrix}
            <pre class="matrix-preview">{JSON.stringify(homography.matrix, null, 2)}</pre>
          {/if}
          <div class="bookmark-list">
            {#each sortedBookmarks as bookmark}
              <div class="bookmark-chip">
                <button type="button" on:click={() => jumpToBookmark(bookmark)}>{formatDuration(bookmark)}</button>
                <button type="button" aria-label={"Remove bookmark " + formatDuration(bookmark)} on:click={() => removeBookmark(bookmark)}>x</button>
              </div>
            {:else}
              <p class="empty-copy">No bookmarked frames yet.</p>
            {/each}
          </div>
        {:else}
          <p class="empty-copy">No session selected.</p>
        {/if}
      </section>
    </section>
    {:else}
    <section class="validation-screen">
      <div class="validation-toolbar">
        <button class="button" on:click={exitValidate}>← Back to calibrate</button>
        <div class="seg">
          <button class:button--primary={validationStage === "homography"} class="button" on:click={() => validationStage = "homography"}>Homography</button>
          <button class:button--primary={validationStage === "tps"} class="button" disabled={!validationField?.has_tps} on:click={() => validationStage = "tps"}>TPS</button>
        </div>
        <div class="seg">
          <button class:button--primary={validationMode === "cursor"} class="button" on:click={() => validationMode = "cursor"}>Cursor</button>
          <button class:button--primary={validationMode === "crosshair"} class="button" on:click={() => validationMode = "crosshair"}>Crosshair</button>
          <button class:button--primary={validationMode === "trail"} class="button" on:click={() => validationMode = "trail"}>Trail</button>
          <button class:button--primary={validationMode === "grid"} class="button" on:click={() => validationMode = "grid"}>Grid</button>
        </div>
        {#if validationMode === "trail"}
          <button class="button" on:click={() => validationTrail = []}>Clear trail</button>
        {/if}
        <button class="button" disabled={validationLoading} on:click={loadValidationField}>{validationLoading ? "Building…" : "Refresh field"}</button>
        <span class="validation-hint">
          {validationField?.has_lens ? "Lens correction active" : "No lens correction"} · hover the camera, watch the floorplan
        </span>
      </div>

      <div class="validation-grid">
        <section class="panel canvas-panel">
          <div class="panel__header">
            <div><h2>Camera snapshot (raw)</h2><p>Move your cursor here.</p></div>
            <div class="canvas-toolbar">
              <button class="button" on:click={() => zoom("camera", -0.1)}>-</button>
              <button class="button" on:click={() => cameraZoom = 1}>Reset</button>
              <button class="button" on:click={() => zoom("camera", 0.1)}>+</button>
            </div>
          </div>
          <div class="media-scroll">
            <div class="media-stage" style={`--stage-zoom: ${cameraZoom}; --media-aspect: ${cameraNaturalWidth} / ${cameraNaturalHeight}`}>
              {#if validationSnapshotUrl}
                <img class="media-viewer" src={validationSnapshotUrl} alt="Raw camera snapshot" />
              {/if}
              <svg
                bind:this={validationCameraOverlay}
                class="point-overlay validation-overlay"
                aria-label="Camera validation overlay"
                viewBox={`0 0 ${cameraNaturalWidth} ${cameraNaturalHeight}`}
                preserveAspectRatio="none"
                on:pointermove={handleValidationMove}
                on:pointerleave={clearHover}
              >
                {#if validationMode === "trail"}
                  {#each validationTrail as point, i}
                    <circle class="trail-dot" cx={point.camera.x} cy={point.camera.y} r="6" style={`opacity:${(i + 1) / validationTrail.length}`} />
                  {/each}
                {/if}
                {#if hoverCameraPx}
                  <line class="xhair" x1={hoverCameraPx.x} y1="0" x2={hoverCameraPx.x} y2={cameraNaturalHeight} vector-effect="non-scaling-stroke" />
                  <line class="xhair" x1="0" y1={hoverCameraPx.y} x2={cameraNaturalWidth} y2={hoverCameraPx.y} vector-effect="non-scaling-stroke" />
                  <circle class="xhair-dot" cx={hoverCameraPx.x} cy={hoverCameraPx.y} r="9" vector-effect="non-scaling-stroke" />
                {/if}
              </svg>
            </div>
          </div>
        </section>

        <section class="panel canvas-panel">
          <div class="panel__header">
            <div><h2>Floorplan ({validationStage})</h2><p>Mapped position updates live.</p></div>
            <div class="canvas-toolbar">
              <button class="button" on:click={() => zoom("floor_map", -0.1)}>-</button>
              <button class="button" on:click={() => floorplanZoom = 1}>Reset</button>
              <button class="button" on:click={() => zoom("floor_map", 0.1)}>+</button>
            </div>
          </div>
          <div class="media-scroll">
            <div class="media-stage" style={`--stage-zoom: ${floorplanZoom}; --media-aspect: ${floorplanNaturalWidth} / ${floorplanNaturalHeight}`}>
              {#if floorplanMediaUrl}
                <img class="media-viewer media-viewer--floorplan" src={floorplanMediaUrl} alt="Floorplan" />
              {/if}
              <svg
                class="point-overlay validation-overlay validation-overlay--floor"
                aria-label="Floorplan validation overlay"
                viewBox={`0 0 ${floorplanNaturalWidth} ${floorplanNaturalHeight}`}
                preserveAspectRatio="none"
              >
                {#each currentSession?.point_pairs ?? [] as pair}
                  {#if pair.floor_map}
                    <circle class="control-dot" cx={pair.floor_map.x} cy={pair.floor_map.y} r="5" vector-effect="non-scaling-stroke" />
                  {/if}
                {/each}
                {#if validationMode === "grid"}
                  {#each validationGridDots as dot}
                    <circle class="grid-dot" cx={dot[0]} cy={dot[1]} r="4" vector-effect="non-scaling-stroke" />
                  {/each}
                {/if}
                {#if validationMode === "trail"}
                  {#each validationTrail as point, i}
                    <circle class="trail-dot" cx={point.floor.x} cy={point.floor.y} r="6" style={`opacity:${(i + 1) / validationTrail.length}`} />
                  {/each}
                {/if}
                {#if hoverFloor}
                  {#if validationMode === "crosshair"}
                    <line class="xhair" x1={hoverFloor.x} y1="0" x2={hoverFloor.x} y2={floorplanNaturalHeight} vector-effect="non-scaling-stroke" />
                    <line class="xhair" x1="0" y1={hoverFloor.y} x2={floorplanNaturalWidth} y2={hoverFloor.y} vector-effect="non-scaling-stroke" />
                  {/if}
                  <circle class="floor-marker" cx={hoverFloor.x} cy={hoverFloor.y} r="11" vector-effect="non-scaling-stroke" />
                {/if}
              </svg>
            </div>
          </div>
        </section>
      </div>

      <div class="validation-statusbar">
        <div><span>Camera px</span><strong>{fmtPoint(hoverCameraPx)}</strong></div>
        <div><span>Undistorted</span><strong>{fmtPoint(hoverUndistorted)}</strong></div>
        <div><span>Homography</span><strong>{fmtPoint(hoverHomography)}</strong></div>
        <div><span>TPS</span><strong>{fmtPoint(hoverTps)}</strong></div>
        <div><span>Floorplan</span><strong>{fmtPoint(hoverFloor)}</strong></div>
        <div><span>Algorithm</span><strong>{validationStage}{validationField?.has_lens ? " + lens" : ""}</strong></div>
      </div>
    </section>
    {/if}
  </section>

  {#if showCreateSessionDialog}
    <div class="modal-backdrop">
      <form class="modal" role="dialog" aria-modal="true" aria-labelledby="new-session-title" on:submit|preventDefault={handleCreateSession}>
        <div class="panel__header">
          <div>
            <h2 id="new-session-title">New Session</h2>
            <p>Name this calibration session before creating it.</p>
          </div>
        </div>
        <label>
          Session name
          <input bind:value={newSessionName} aria-label="Session name" />
        </label>
        <div class="modal__actions">
          <button class="button" type="button" disabled={creatingSession} on:click={closeCreateSessionDialog}>Cancel</button>
          <button class="button button--primary" type="submit" disabled={creatingSession}>
            {creatingSession ? "Saving" : "Save"}
          </button>
        </div>
      </form>
    </div>
  {/if}
</main>
