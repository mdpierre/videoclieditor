"""Optional analysis signal producers: Silero VAD (ONNX) and PySceneDetect.

This module has no hard third-party imports. `detect_scene_boundaries` and
`detect_speech_regions_silero` guard their imports and never raise into the
caller; on any failure (missing dependency, missing model, bad input) they
return `None` so callers can fall back to the existing ffmpeg-based silence
detection and unbiased boundary scoring. `speech_regions_to_silences` and
`snap_ranges_to_scene_boundaries` are pure helpers with no third-party
imports at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def speech_regions_to_silences(
    speech_regions: list[dict[str, float]],
    audio_duration: float,
) -> list[dict[str, float]]:
    """Invert speech regions into non-speech regions.

    Input:  [{"start": s, "end": e}, ...] sorted or unsorted, possibly overlapping.
    Output: [{"start","end","duration"}, ...] covering the complement within
            [0, audio_duration], each with duration > 0.
    """
    if audio_duration <= 0:
        return []

    # Clamp and drop degenerate regions defensively, then sort and merge
    # overlapping/adjacent speech regions before inverting.
    cleaned: list[tuple[float, float]] = []
    for region in speech_regions:
        start = max(0.0, min(float(region["start"]), audio_duration))
        end = max(0.0, min(float(region["end"]), audio_duration))
        if end > start:
            cleaned.append((start, end))
    cleaned.sort(key=lambda pair: pair[0])

    merged: list[list[float]] = []
    for start, end in cleaned:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    silences: list[dict[str, float]] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor:
            silences.append(
                {"start": cursor, "end": start, "duration": start - cursor}
            )
        cursor = max(cursor, end)

    if audio_duration > cursor:
        silences.append(
            {"start": cursor, "end": audio_duration, "duration": audio_duration - cursor}
        )

    return silences


def snap_ranges_to_scene_boundaries(
    ranges: list[dict[str, float]],
    scene_boundaries: list[float],
    *,
    tolerance: float = 0.22,
) -> tuple[list[dict[str, float]], list[dict[str, Any]]]:
    """Snap clip edges onto nearby scene cuts without clipping speech.

    For each adjacent pair, gap = [ranges[i]["end"], ranges[i+1]["start"]].
    A boundary b may move ranges[i]["end"] up to b iff
        ranges[i]["end"] <= b <= ranges[i]["end"] + tolerance  and  b <= gap end.
    Symmetric for ranges[i+1]["start"] (b within [start - tolerance, start], b >= gap start).
    First range start snaps within [0, start]; last range end within [end, +inf) only
    up to tolerance. Never move an edge past its own range's opposite edge; never make
    ranges overlap. Ties -> nearest boundary.
    Returns (snapped_ranges, snap_log) where snap_log entries are
        {"edge":"start"|"end","clip_index":int,"from":float,"to":float,"boundary":float}.
    Input ranges are not mutated.
    """
    snapped = [dict(r) for r in ranges]
    snap_log: list[dict[str, Any]] = []

    if not scene_boundaries:
        return snapped, snap_log

    boundaries = sorted(float(b) for b in scene_boundaries)

    def nearest_boundary_in(low: float, high: float) -> float | None:
        """Nearest boundary within [low, high] (inclusive), ties -> nearest, then
        first found (boundaries are sorted, so this is stable)."""
        best: float | None = None
        best_distance: float | None = None
        for boundary in boundaries:
            if boundary < low or boundary > high:
                continue
            distance = abs(boundary - low) if low == high else min(abs(boundary - low), abs(boundary - high))
            if best is None or distance < best_distance:  # type: ignore[operator]
                best = boundary
                best_distance = distance
        return best

    if not snapped:
        return snapped, snap_log

    # First range start: snap within [0, start], but only up to `tolerance` away.
    first = snapped[0]
    first_start = first["start"]
    low = max(0.0, first_start - tolerance)
    candidate = nearest_boundary_in(low, first_start)
    if candidate is not None and candidate < first_start and candidate <= first["end"]:
        snap_log.append(
            {
                "edge": "start",
                "clip_index": 0,
                "from": first_start,
                "to": candidate,
                "boundary": candidate,
            }
        )
        first["start"] = candidate

    # Adjacent gaps between ranges[i].end and ranges[i+1].start.
    for i in range(len(snapped) - 1):
        left = snapped[i]
        right = snapped[i + 1]
        gap_start = left["end"]
        gap_end = right["start"]
        if gap_end < gap_start:
            # Defensive: nothing to snap into a negative/empty gap.
            continue

        # Candidate for moving left["end"] forward, up to `tolerance`, without
        # crossing into the gap's own end or past the left clip's own start.
        end_low = gap_start
        end_high = min(gap_start + tolerance, gap_end)
        if end_high >= end_low:
            end_candidate = nearest_boundary_in(end_low, end_high)
        else:
            end_candidate = None

        # Candidate for moving right["start"] backward, up to `tolerance`,
        # without crossing before the gap's own start or past the right
        # clip's own end.
        start_low = max(gap_start, right["start"] - tolerance)
        start_high = gap_end
        if start_high >= start_low:
            start_candidate = nearest_boundary_in(start_low, start_high)
        else:
            start_candidate = None

        chosen_edge: str | None = None
        chosen_value: float | None = None
        if end_candidate is not None and end_candidate != left["end"]:
            chosen_edge = "end"
            chosen_value = end_candidate
        if start_candidate is not None and start_candidate != right["start"]:
            # If both edges have a candidate, prefer whichever boundary is
            # closer to its own original edge (ties -> nearest already
            # resolved above; between the two edges, keep the first found —
            # "end" — unless "start" is strictly closer).
            if chosen_edge is None or abs(start_candidate - right["start"]) < abs(chosen_value - left["end"]):  # type: ignore[arg-type]
                chosen_edge = "start"
                chosen_value = start_candidate

        if chosen_edge == "end" and chosen_value is not None:
            snap_log.append(
                {
                    "edge": "end",
                    "clip_index": i,
                    "from": left["end"],
                    "to": chosen_value,
                    "boundary": chosen_value,
                }
            )
            left["end"] = chosen_value
        elif chosen_edge == "start" and chosen_value is not None:
            snap_log.append(
                {
                    "edge": "start",
                    "clip_index": i + 1,
                    "from": right["start"],
                    "to": chosen_value,
                    "boundary": chosen_value,
                }
            )
            right["start"] = chosen_value

    # Last range end: snap within [end, end + tolerance].
    last = snapped[-1]
    last_end = last["end"]
    high = last_end + tolerance
    candidate = nearest_boundary_in(last_end, high)
    if candidate is not None and candidate > last_end and candidate >= last["start"]:
        snap_log.append(
            {
                "edge": "end",
                "clip_index": len(snapped) - 1,
                "from": last_end,
                "to": candidate,
                "boundary": candidate,
            }
        )
        last["end"] = candidate

    return snapped, snap_log


def detect_scene_boundaries(
    video_path: Path,
    *,
    threshold: float = 27.0,
) -> list[float] | None:
    """Return scene-cut times (seconds) or None if PySceneDetect is unavailable/fails.

    Uses scenedetect.detect + ContentDetector. Return cut points as sorted floats
    (start of each scene after the first, i.e. the boundary times). Never raises.
    """
    try:
        from scenedetect import detect, ContentDetector
    except Exception:
        return None

    try:
        scenes = detect(str(video_path), ContentDetector(threshold=threshold))
        boundaries: list[float] = []
        for index, (start_timecode, _end_timecode) in enumerate(scenes):
            if index == 0:
                continue
            boundaries.append(float(start_timecode.get_seconds()))
        return sorted(boundaries)
    except Exception:
        return None


def detect_speech_regions_silero(
    audio_path: Path,
    *,
    model_path: Path,
    threshold: float = 0.5,
    sampling_rate: int = 16000,
) -> list[dict[str, float]] | None:
    """Return speech regions [{"start","end"}] in seconds, or None if unavailable/failed.

    Never raises. Requires onnxruntime and the bundled ONNX model; if either is missing
    return None so the caller falls back to ffmpeg silence detection.
    """
    try:
        import onnxruntime
    except Exception:
        return None

    model_path = Path(model_path)
    if not model_path.exists():
        return None

    try:
        import array
        import wave

        with wave.open(str(audio_path), "rb") as wav_file:
            if wav_file.getnchannels() != 1 or wav_file.getframerate() != sampling_rate:
                return None
            sample_width = wav_file.getsampwidth()
            if sample_width != 2:
                # Silero expects 16-bit PCM; anything else is unsupported here.
                return None
            raw_frames = wav_file.readframes(wav_file.getnframes())

        samples = array.array("h")
        samples.frombytes(raw_frames)
        if not samples:
            return []

        import numpy as np

        audio = np.asarray(samples, dtype=np.float32) / 32768.0

        session = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )

        window_size = 512 if sampling_rate == 16000 else 256
        context_size = 64 if sampling_rate == 16000 else 32

        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, context_size), dtype=np.float32)
        sr_input = np.array(sampling_rate, dtype=np.int64)

        frame_probs: list[float] = []
        total_samples = audio.shape[0]
        offset = 0
        while offset < total_samples:
            chunk = audio[offset : offset + window_size]
            if chunk.shape[0] < window_size:
                padded = np.zeros(window_size, dtype=np.float32)
                padded[: chunk.shape[0]] = chunk
                chunk = padded

            model_input = np.concatenate([context, chunk[np.newaxis, :]], axis=1)
            ort_inputs = {
                "input": model_input.astype(np.float32),
                "state": state,
                "sr": sr_input,
            }
            input_names = {inp.name for inp in session.get_inputs()}
            ort_inputs = {k: v for k, v in ort_inputs.items() if k in input_names}
            outputs = session.run(None, ort_inputs)
            prob = float(np.asarray(outputs[0]).reshape(-1)[0])
            if len(outputs) > 1:
                state = np.asarray(outputs[1], dtype=np.float32)
            frame_probs.append(prob)

            context = chunk[-context_size:][np.newaxis, :]
            offset += window_size

        frame_duration = window_size / float(sampling_rate)

        regions: list[dict[str, float]] = []
        region_start: float | None = None
        for index, prob in enumerate(frame_probs):
            frame_time = index * frame_duration
            is_speech = prob >= threshold
            if is_speech and region_start is None:
                region_start = frame_time
            elif not is_speech and region_start is not None:
                regions.append({"start": region_start, "end": frame_time})
                region_start = None

        if region_start is not None:
            regions.append({"start": region_start, "end": total_samples / float(sampling_rate)})

        return regions
    except Exception:
        return None
