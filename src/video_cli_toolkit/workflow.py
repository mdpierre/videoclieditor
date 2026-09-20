from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, HTTPServer
import html as _html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from typing import Any
import webbrowser

from .analysis import (
    detect_scene_boundaries,
    detect_speech_regions_silero,
    snap_ranges_to_scene_boundaries,
    speech_regions_to_silences,
)
from .config import AppConfig
from .rewrite_matcher import build_ranges_from_kept_word_ids, match_rewrite_words, score_boundary


class ToolkitError(RuntimeError):
    pass


NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
}

DOCTOR_IMPORT_TIMEOUT_SECONDS = 3.0
DOCTOR_COMMAND_TIMEOUT_SECONDS = 5.0
WORD_EDITOR_FILLER_WORDS = ("um", "uh", "erm", "hmm", "mm")
WORD_EDITOR_LONG_PAUSE_SECONDS = 1.0
REWRITE_AUDIO_SILENCE_DB = -35.0
REWRITE_AUDIO_SILENCE_MIN_DURATION = 0.2
REWRITE_WEAK_BOUNDARY_SCORE = 0

EDIT_PRESETS: dict[str, dict[str, Any]] = {
    "tight-social-clip": {
        "padding": "0.12,0.25",
        "fuzzy_threshold": 0.68,
        "max_silence": 0.12,
        "merge_gap": 0.25,
        "silence_threshold_db": -32.0,
        "min_silence_duration": 0.12,
        "weak_boundary_score": 1,
    },
    "gentle-talking-head-cleanup": {
        "padding": "0.35,0.75",
        "fuzzy_threshold": 0.58,
        "max_silence": 0.35,
        "merge_gap": 0.8,
        "silence_threshold_db": -36.0,
        "min_silence_duration": 0.25,
        "weak_boundary_score": 0,
    },
    "sermon-excerpt": {
        "padding": "0.6,1.2",
        "fuzzy_threshold": 0.55,
        "max_silence": 0.45,
        "merge_gap": 1.2,
        "silence_threshold_db": -38.0,
        "min_silence_duration": 0.3,
        "weak_boundary_score": 0,
    },
    "rough-review-draft": {
        "padding": "0.8,1.5",
        "fuzzy_threshold": 0.5,
        "max_silence": 0.0,
        "merge_gap": 1.5,
        "silence_threshold_db": -35.0,
        "min_silence_duration": 0.2,
        "weak_boundary_score": -1,
    },
}

EDIT_REQUEST_SCHEMA: dict[str, Any] = {
    "schema_version": 1,
    "required": ["source_path", "workflow"],
    "properties": {
        "source_path": "Path to source media.",
        "workflow": "rewrite-edit, transcript-edit, or plan-edit.",
        "model": "Optional Whisper model name.",
        "target_transcript": "Inline final transcript for rewrite workflows.",
        "target_transcript_path": "Path to final transcript text for rewrite workflows.",
        "queries": "Exact transcript queries for transcript-edit.",
        "fuzzy_queries": "Fuzzy transcript queries for transcript-edit.",
        "ranges_path": "Manual ranges JSON path for transcript-edit.",
        "preset": f"One of: {', '.join(sorted(EDIT_PRESETS))}.",
        "padding": "PRE,POST seconds, for example 0.2,0.5.",
        "fuzzy_threshold": "Minimum fuzzy transcript score.",
        "max_silence": "Max allowed silence gap between kept rewrite words before splitting.",
        "merge_gap": "Seconds between ranges that may be merged after padding.",
        "silence_threshold_db": "ffmpeg silencedetect noise threshold in dB.",
        "min_silence_duration": "Minimum silence duration for ffmpeg silencedetect.",
        "weak_boundary_score": "Only split detected silence when boundary score is greater than this value.",
        "output_style": "plan or render.",
        "notes": "Free-form operator notes stored in run metadata.",
    },
}

SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)")


@dataclass(frozen=True)
class RunContext:
    input_path: Path
    run_dir: Path
    audio_path: Path
    transcript_path: Path
    segments_path: Path
    captions_path: Path
    edited_path: Path
    transcript_edit_path: Path
    review_sheet_path: Path
    generated_ranges_path: Path
    selected_segments_path: Path
    clip_ranges_path: Path
    concat_list_path: Path
    clips_dir: Path
    run_metadata_path: Path
    review_ui_path: Path
    word_segments_path: Path
    rewrite_target_path: Path
    edit_plan_path: Path
    decision_report_path: Path


def project_root_from_here() -> Path:
    env_root = os.environ.get("VIDEO_CLI_TOOLKIT_ROOT")
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if (candidate / "config.toml").exists():
            return candidate

    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / "config.toml").exists():
            return candidate

    executable_path = Path(sys.argv[0]).resolve()
    for candidate in executable_path.parents:
        if (candidate / "config.toml").exists():
            return candidate

    for candidate in Path(__file__).resolve().parents:
        if (candidate / "config.toml").exists():
            return candidate

    raise ToolkitError("Could not locate project root. Run the command from the toolkit project directory or set VIDEO_CLI_TOOLKIT_ROOT.")


def create_run_context(config: AppConfig, input_path: Path, run_id: str | None = None) -> RunContext:
    source_stem = input_path.stem.replace(" ", "-")
    actual_run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = config.outputs.root / source_stem / actual_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunContext(
        input_path=input_path,
        run_dir=run_dir,
        audio_path=run_dir / "audio.wav",
        transcript_path=run_dir / "transcript.txt",
        segments_path=run_dir / "segments.json",
        captions_path=run_dir / "captions.srt",
        edited_path=run_dir / "edited.mp4",
        transcript_edit_path=run_dir / "transcript_edit.mp4",
        review_sheet_path=run_dir / "review_sheet.txt",
        generated_ranges_path=run_dir / "ranges.json",
        selected_segments_path=run_dir / "selected_segments.json",
        clip_ranges_path=run_dir / "clip_ranges.json",
        concat_list_path=run_dir / "concat.txt",
        clips_dir=run_dir / "clips",
        run_metadata_path=run_dir / "run.json",
        review_ui_path=run_dir / "review_ui.html",
        word_segments_path=run_dir / "word_segments.json",
        rewrite_target_path=run_dir / "rewrite_target.txt",
        edit_plan_path=run_dir / "edit_plan.json",
        decision_report_path=run_dir / "decision_report.html",
    )


def discover_whisper_binary(config: AppConfig) -> str | None:
    for candidate in config.whisper.binary_candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def venv_bin(project_root: Path, executable_name: str) -> Path:
    return project_root / ".venv" / "bin" / executable_name


def discover_auto_editor_binary(project_root: Path) -> str | None:
    local_binary = venv_bin(project_root, "auto-editor")
    if local_binary.exists():
        return str(local_binary)
    return shutil.which("auto-editor")


def auto_editor_env() -> dict[str, str]:
    env = os.environ.copy()
    for candidate in (
        "/opt/homebrew/etc/ca-certificates/cert.pem",
        "/etc/ssl/cert.pem",
    ):
        if Path(candidate).exists():
            env.setdefault("SSL_CERT_FILE", candidate)
            break
    return env


def build_ffmpeg_extract_command(input_path: Path, output_path: Path, audio_codec: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        audio_codec,
        str(output_path),
    ]


def build_whisper_command(
    binary: str,
    audio_path: Path,
    output_base: Path,
    model_path: Path,
    disable_gpu: bool = False,
) -> list[str]:
    command = [
        binary,
        "-m",
        str(model_path),
        "-f",
        str(audio_path),
        "--output-txt",
        "--output-json",
        "--output-file",
        str(output_base),
    ]
    if disable_gpu:
        command.extend(["--no-gpu", "--no-flash-attn"])
    return command


def build_auto_editor_command(
    binary: str,
    input_path: Path,
    output_path: Path,
    margin: str,
    video_codec: str,
    quality: str,
) -> list[str]:
    return [
        binary,
        str(input_path),
        "--margin",
        margin,
        "--video-codec",
        video_codec,
        "--scale",
        "1",
        "--no-open",
        "--output",
        str(output_path),
    ]


def build_ffmpeg_clip_command(input_path: Path, output_path: Path, start: float, duration: float) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(start, 0.0):.3f}",
        "-t",
        f"{max(duration, 0.1):.3f}",
        "-i",
        str(input_path),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        str(output_path),
    ]


def build_ffmpeg_concat_command(concat_file: Path, output_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output_path),
    ]


def run_command(
    command: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise ToolkitError(
            "Command failed:\n"
            f"{' '.join(command)}\n\n"
            f"stdout:\n{process.stdout}\n\nstderr:\n{process.stderr}"
        )
    return process


def ffmpeg_supports_videotoolbox() -> bool:
    process = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        text=True,
        capture_output=True,
        check=False,
    )
    encoders = f"{process.stdout}\n{process.stderr}"
    return "h264_videotoolbox" in encoders or "hevc_videotoolbox" in encoders


def _run_check_command(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float,
) -> tuple[bool, bool]:
    try:
        process = subprocess.run(
            command,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, True
    return process.returncode == 0, False


def _coerce_segment(entry: dict[str, Any], index: int) -> dict[str, Any]:
    start = float(
        entry.get("start")
        or entry.get("from")
        or entry.get("offsets", {}).get("from")
        or 0.0
    )
    end = float(
        entry.get("end")
        or entry.get("to")
        or entry.get("offsets", {}).get("to")
        or start
    )
    text = str(entry.get("text") or "").strip()
    if start > 1000 or end > 1000:
        start = start / 1000.0
        end = end / 1000.0
    return {
        "id": index,
        "start": start,
        "end": max(end, start),
        "text": text,
    }


def normalize_whisper_output(json_path: Path, transcript_path: Path, segments_path: Path) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {}
    if json_path.exists():
        payload = json.loads(json_path.read_text())

    raw_segments = payload.get("transcription") or payload.get("segments") or []
    segments = [_coerce_segment(segment, index) for index, segment in enumerate(raw_segments)]

    if not transcript_path.exists() and payload.get("text"):
        transcript_path.write_text(str(payload["text"]).strip() + "\n")

    if not transcript_path.exists():
        joined_text = "\n".join(segment["text"] for segment in segments if segment["text"]).strip()
        transcript_path.write_text((joined_text + "\n") if joined_text else "")

    if not segments:
        transcript_text = transcript_path.read_text().strip() if transcript_path.exists() else ""
        if transcript_text:
            segments = [{"id": 0, "start": 0.0, "end": 0.0, "text": transcript_text}]

    segments_path.write_text(json.dumps(segments, indent=2))
    return segments


def format_srt_timestamp(seconds: float) -> str:
    milliseconds = max(int(round(seconds * 1000)), 0)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_review_timestamp(seconds: float) -> str:
    total_milliseconds = max(int(round(seconds * 1000)), 0)
    minutes, remainder = divmod(total_milliseconds, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


def write_srt(segments: list[dict[str, Any]], output_path: Path) -> None:
    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        start = format_srt_timestamp(float(segment["start"]))
        end_seconds = float(segment["end"])
        if end_seconds <= float(segment["start"]):
            end_seconds = float(segment["start"]) + 2.0
        end = format_srt_timestamp(end_seconds)
        lines.extend([str(index), f"{start} --> {end}", str(segment["text"]).strip(), ""])
    output_path.write_text("\n".join(lines).rstrip() + "\n")


def write_run_metadata(run_context: RunContext, data: dict[str, Any]) -> None:
    run_context.run_metadata_path.write_text(json.dumps(data, indent=2))


def resolve_rewrite_target_text(
    transcript_text: str | None = None,
    transcript_path: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    if transcript_text is not None and transcript_path is not None:
        raise ToolkitError("Provide either transcript text or a transcript file, not both.")
    if transcript_path is not None:
        source_path = transcript_path.expanduser().resolve()
        if not source_path.exists():
            raise ToolkitError(f"Transcript file does not exist: {source_path}")
        resolved_text = source_path.read_text()
        source_metadata = {"type": "file", "path": str(source_path)}
    elif transcript_text is not None:
        resolved_text = transcript_text
        source_metadata = {"type": "inline"}
    else:
        raise ToolkitError("A target transcript is required.")

    normalized_text = resolved_text.strip()
    if not normalized_text:
        raise ToolkitError("Target transcript cannot be empty.")
    return normalized_text, source_metadata


def build_review_sheet(segments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for segment in segments:
        segment_id = int(segment.get("id", 0))
        start = format_review_timestamp(float(segment["start"]))
        end = format_review_timestamp(float(segment["end"]))
        text = str(segment.get("text", "")).strip()
        lines.append(f"[{segment_id:02d}] {start}-{end} {text}")
    return "\n".join(lines).rstrip() + "\n"


def load_segments(run_context: RunContext) -> list[dict[str, Any]]:
    if not run_context.segments_path.exists():
        raise ToolkitError(f"Missing segments file: {run_context.segments_path}")
    return json.loads(run_context.segments_path.read_text())


def parse_padding(padding: str | None, default_before: float = 0.4, default_after: float = 0.8) -> tuple[float, float]:
    if not padding:
        return default_before, default_after
    parts = [part.strip() for part in padding.split(",")]
    if len(parts) != 2:
        raise ToolkitError("Padding must be in the form PRE,POST, for example `0.4,0.8`.")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise ToolkitError("Padding values must be numeric seconds, for example `0.4,0.8`.") from exc


def list_edit_presets() -> dict[str, dict[str, Any]]:
    return {name: dict(values) for name, values in EDIT_PRESETS.items()}


def resolve_edit_options(
    *,
    preset: str | None = None,
    padding: str | None = None,
    fuzzy_threshold: float | None = None,
    max_silence: float | None = None,
    merge_gap: float | None = None,
    silence_threshold_db: float | None = None,
    min_silence_duration: float | None = None,
    weak_boundary_score: int | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "padding": None,
        "fuzzy_threshold": 0.6,
        "max_silence": 0.2,
        "merge_gap": 1.0,
        "silence_threshold_db": REWRITE_AUDIO_SILENCE_DB,
        "min_silence_duration": REWRITE_AUDIO_SILENCE_MIN_DURATION,
        "weak_boundary_score": REWRITE_WEAK_BOUNDARY_SCORE,
    }
    if preset:
        if preset not in EDIT_PRESETS:
            raise ToolkitError(f"Unknown preset `{preset}`. Available presets: {', '.join(sorted(EDIT_PRESETS))}")
        options.update(EDIT_PRESETS[preset])
        options["preset"] = preset

    overrides = {
        "padding": padding,
        "fuzzy_threshold": fuzzy_threshold,
        "max_silence": max_silence,
        "merge_gap": merge_gap,
        "silence_threshold_db": silence_threshold_db,
        "min_silence_duration": min_silence_duration,
        "weak_boundary_score": weak_boundary_score,
    }
    for key, value in overrides.items():
        if value is not None:
            options[key] = value
    return options


def apply_padding_to_ranges(
    ranges: list[dict[str, Any]],
    input_duration: float,
    padding_before: float,
    padding_after: float,
    merge_gap: float = 1.0,
) -> list[dict[str, Any]]:
    padded: list[dict[str, Any]] = []
    for entry in ranges:
        start = max(float(entry["start"]) - padding_before, 0.0)
        end = min(float(entry["end"]) + padding_after, input_duration)
        if end <= start:
            continue
        padded.append(
            {
                "start": start,
                "end": max(end, start + 0.1),
                "text": str(entry.get("text") or entry.get("label") or ""),
                "segment_ids": list(entry.get("segment_ids", [])),
            }
        )

    if not padded:
        return []

    padded.sort(key=lambda item: float(item["start"]))
    merged: list[dict[str, Any]] = [padded[0]]
    for current in padded[1:]:
        previous = merged[-1]
        if float(current["start"]) <= float(previous["end"]) + merge_gap:
            previous["end"] = max(float(previous["end"]), float(current["end"]))
            previous["text"] = " ".join(part for part in [str(previous.get("text", "")).strip(), str(current.get("text", "")).strip()] if part)
            previous["segment_ids"] = list(previous.get("segment_ids", [])) + list(current.get("segment_ids", []))
        else:
            merged.append(current)

    for index, entry in enumerate(merged, start=1):
        entry["id"] = index
        entry["duration"] = round(float(entry["end"]) - float(entry["start"]), 3)
    return merged


def normalize_match_text(text: str) -> str:
    lowered = text.casefold().replace("'", "")
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    tokens = [NUMBER_WORDS.get(token, token) for token in lowered.split()]
    return " ".join(tokens)


def build_segment_windows(segments: list[dict[str, Any]], max_window_size: int = 3) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for start_index in range(len(segments)):
        combined_text_parts: list[str] = []
        combined_ids: list[Any] = []
        for window_size in range(1, max_window_size + 1):
            current_index = start_index + window_size - 1
            if current_index >= len(segments):
                break
            segment = segments[current_index]
            combined_text_parts.append(str(segment.get("text", "")).strip())
            combined_ids.append(segment.get("id"))
            raw_text = " ".join(part for part in combined_text_parts if part).strip()
            windows.append(
                {
                    "start": float(segments[start_index]["start"]),
                    "end": float(segment["end"]),
                    "text": raw_text,
                    "normalized_text": normalize_match_text(raw_text),
                    "segment_ids": list(combined_ids),
                }
            )
    return windows


def _segments_by_id(segments: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    return {segment.get("id"): segment for segment in segments}


def _collect_segments_from_windows(segments: list[dict[str, Any]], windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segment_lookup = _segments_by_id(segments)
    ordered_ids: list[Any] = []
    for window in windows:
        for segment_id in window["segment_ids"]:
            if segment_id not in ordered_ids:
                ordered_ids.append(segment_id)
    return [segment_lookup[segment_id] for segment_id in ordered_ids if segment_id in segment_lookup]


def _token_overlap_score(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _contains_ordered_token_sequence(query: str, candidate: str) -> bool:
    query_tokens = query.split()
    candidate_tokens = candidate.split()
    if not query_tokens:
        return False
    candidate_index = 0
    for query_token in query_tokens:
        while candidate_index < len(candidate_tokens) and candidate_tokens[candidate_index] != query_token:
            candidate_index += 1
        if candidate_index >= len(candidate_tokens):
            return False
        candidate_index += 1
    return True


def _query_matches_window(query: str, normalized_window: str) -> bool:
    return query in normalized_window or _contains_ordered_token_sequence(query, normalized_window)


def _score_fuzzy_window(query: str, normalized_window: str) -> float:
    ratio = SequenceMatcher(None, query, normalized_window).ratio()
    contains_bonus = 1.0 if query in normalized_window or normalized_window in query else 0.0
    overlap_score = _token_overlap_score(query, normalized_window)
    return max(ratio, contains_bonus, overlap_score)


def _fuzzy_window_sort_key(window: dict[str, Any]) -> tuple[float, int, float, int, float]:
    return (
        -float(window["match_score"]),
        len(window["segment_ids"]),
        round(float(window["end"]) - float(window["start"]), 6),
        len(str(window["normalized_text"])),
        float(window["start"]),
    )


def select_segments_by_queries(segments: list[dict[str, Any]], queries: list[str]) -> list[dict[str, Any]]:
    normalized_queries = [normalize_match_text(query) for query in queries if query.strip()]
    if not normalized_queries:
        raise ToolkitError("At least one non-empty `--query` value is required.")

    windows = build_segment_windows(segments)
    matched_windows: list[dict[str, Any]] = []
    for query in normalized_queries:
        query_matches = [window for window in windows if _query_matches_window(query, window["normalized_text"])]
        if not query_matches:
            continue
        minimum_window_size = min(len(window["segment_ids"]) for window in query_matches)
        matched_windows.extend(
            window for window in query_matches if len(window["segment_ids"]) == minimum_window_size
        )
    selected = _collect_segments_from_windows(segments, matched_windows)
    if not selected:
        raise ToolkitError(f"No transcript segments matched the requested query text: {queries}")
    return selected


def select_segments_by_fuzzy_queries(
    segments: list[dict[str, Any]],
    queries: list[str],
    threshold: float,
) -> list[dict[str, Any]]:
    normalized_queries = [normalize_match_text(query) for query in queries if query.strip()]
    if not normalized_queries:
        raise ToolkitError("At least one non-empty `--fuzzy-query` value is required.")

    windows = build_segment_windows(segments)
    matched_windows: list[dict[str, Any]] = []
    for query in normalized_queries:
        query_matches: list[dict[str, Any]] = []
        for window in windows:
            best_score = _score_fuzzy_window(query, window["normalized_text"])
            if best_score < threshold:
                continue
            enriched_window = dict(window)
            enriched_window["match_score"] = round(best_score, 3)
            query_matches.append(enriched_window)
        if query_matches:
            matched_windows.append(sorted(query_matches, key=_fuzzy_window_sort_key)[0])

    selected = _collect_segments_from_windows(segments, matched_windows)
    if not selected:
        raise ToolkitError(
            f"No transcript segments matched the requested fuzzy query text at threshold {threshold}: {queries}"
        )
    best_score_by_segment_id: dict[Any, float] = {}
    for window in matched_windows:
        for segment_id in window["segment_ids"]:
            best_score_by_segment_id[segment_id] = max(best_score_by_segment_id.get(segment_id, 0.0), float(window["match_score"]))
    for segment in selected:
        segment["match_score"] = round(best_score_by_segment_id.get(segment.get("id"), 0.0), 3)
    return selected


def build_clip_ranges(
    selected_segments: list[dict[str, Any]],
    input_duration: float,
    padding_before: float,
    padding_after: float,
    merge_gap: float = 1.0,
) -> list[dict[str, Any]]:
    ranges = [
        {
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "text": str(segment["text"]),
            "segment_ids": [segment.get("id")],
        }
        for segment in selected_segments
    ]
    return apply_padding_to_ranges(ranges, input_duration, padding_before, padding_after, merge_gap=merge_gap)


def load_manual_ranges(ranges_path: Path, input_duration: float) -> list[dict[str, Any]]:
    if not ranges_path.exists():
        raise ToolkitError(f"Ranges file does not exist: {ranges_path}")

    try:
        payload = json.loads(ranges_path.read_text())
    except json.JSONDecodeError as exc:
        raise ToolkitError(f"Ranges file is not valid JSON: {ranges_path}") from exc

    if not isinstance(payload, list) or not payload:
        raise ToolkitError("Ranges file must be a non-empty JSON array.")

    clip_ranges: list[dict[str, Any]] = []
    for index, entry in enumerate(payload, start=1):
        if not isinstance(entry, dict):
            raise ToolkitError("Each ranges entry must be a JSON object with `start` and `end`.")
        if "start" not in entry or "end" not in entry:
            raise ToolkitError("Each ranges entry must include `start` and `end`.")
        try:
            start = max(float(entry["start"]), 0.0)
            end = min(float(entry["end"]), input_duration)
        except (TypeError, ValueError) as exc:
            raise ToolkitError("Range `start` and `end` values must be numeric.") from exc
        if end <= start:
            continue
        clip_ranges.append(
            {
                "id": index,
                "start": start,
                "end": end,
                "duration": round(end - start, 3),
                "text": str(entry.get("label") or entry.get("text") or f"range-{index}"),
                "segment_ids": [],
            }
        )
    return clip_ranges


def probe_duration(input_path: Path) -> float:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        raise ToolkitError(
            "Could not determine media duration.\n"
            f"stdout:\n{process.stdout}\n\nstderr:\n{process.stderr}"
        )
    try:
        return float(process.stdout.strip())
    except ValueError as exc:
        raise ToolkitError(f"Could not parse media duration for {input_path}") from exc


def parse_silencedetect_output(stderr: str, audio_duration: float | None = None) -> list[dict[str, float]]:
    silences: list[dict[str, float]] = []
    current_start: float | None = None
    for line in stderr.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            current_start = float(start_match.group(1))
            continue

        end_match = SILENCE_END_RE.search(line)
        if end_match:
            silence_end = float(end_match.group(1))
            silence_duration = float(end_match.group(2))
            silence_start = current_start if current_start is not None else max(0.0, silence_end - silence_duration)
            if silence_end > silence_start:
                silences.append(
                    {
                        "start": silence_start,
                        "end": silence_end,
                        "duration": silence_end - silence_start,
                    }
                )
            current_start = None

    if current_start is not None and audio_duration is not None and audio_duration > current_start:
        silences.append(
            {
                "start": current_start,
                "end": audio_duration,
                "duration": audio_duration - current_start,
            }
        )

    return silences


def detect_audio_silences(
    audio_path: Path,
    *,
    noise_db: float = REWRITE_AUDIO_SILENCE_DB,
    min_duration: float = REWRITE_AUDIO_SILENCE_MIN_DURATION,
) -> list[dict[str, float]]:
    if not audio_path.exists():
        return []

    process = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(audio_path),
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_duration:.3f}",
            "-f",
            "null",
            "-",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        return []

    audio_duration = probe_duration(audio_path)
    return parse_silencedetect_output(process.stderr, audio_duration)


def resolve_silences(
    config: AppConfig,
    run_context: RunContext,
    *,
    silence_threshold_db: float,
    min_silence_duration: float,
    speech_regions_out: list[dict[str, float]] | None = None,
) -> tuple[list[dict[str, float]], str]:
    """Resolve non-speech regions for rewrite planning.

    Prefers Silero VAD (ONNX) when `config.analysis.vad_backend == "silero"`.
    Falls back to ffmpeg `silencedetect` when Silero is disabled, unavailable,
    or fails to produce a result. Never raises: `detect_speech_regions_silero`
    already guards its own failures and returns `None` on any problem.

    When `speech_regions_out` is provided, the raw Silero speech regions (if
    any were found) are appended to it so callers can surface them separately
    from the derived silences; it is left untouched on the ffmpeg path.
    """
    if config.analysis.vad_backend == "silero":
        speech_regions = detect_speech_regions_silero(
            run_context.audio_path,
            model_path=config.silero_model_path,
        )
        if speech_regions is not None:
            if speech_regions_out is not None:
                speech_regions_out.extend(speech_regions)
            audio_duration = probe_duration(run_context.audio_path)
            silences = speech_regions_to_silences(speech_regions, audio_duration)
            return silences, "silero"

    silences = detect_audio_silences(
        run_context.audio_path,
        noise_db=silence_threshold_db,
        min_duration=min_silence_duration,
    )
    return silences, "ffmpeg"


def transcript_edit(
    config: AppConfig,
    run_context: RunContext,
    queries: list[str] | None = None,
    fuzzy_queries: list[str] | None = None,
    ranges_path: Path | None = None,
    padding: str | None = None,
    fuzzy_threshold: float = 0.6,
    merge_gap: float = 1.0,
    model_name: str | None = None,
) -> dict[str, Any]:
    input_duration = probe_duration(run_context.input_path)
    selection_mode = "ranges" if ranges_path else "fuzzy-query" if fuzzy_queries else "query"

    selected_segments: list[dict[str, Any]] = []
    transcribe_metadata: dict[str, Any] | None = None

    if ranges_path is not None:
        raw_ranges = load_manual_ranges(ranges_path.expanduser().resolve(), input_duration)
        padding_before, padding_after = parse_padding(padding, 0.0, 0.0)
        manual_merge_gap = merge_gap if padding is not None else 0.0
        clip_ranges = apply_padding_to_ranges(raw_ranges, input_duration, padding_before, padding_after, merge_gap=manual_merge_gap)
    else:
        if run_context.segments_path.exists():
            segments = load_segments(run_context)
        else:
            segments, transcribe_metadata = transcribe(config, run_context, model_name=model_name)

        padding_before, padding_after = parse_padding(padding)
        if fuzzy_queries:
            selected_segments = select_segments_by_fuzzy_queries(segments, fuzzy_queries, fuzzy_threshold)
        else:
            selected_segments = select_segments_by_queries(segments, queries or [])
        clip_ranges = build_clip_ranges(selected_segments, input_duration, padding_before, padding_after, merge_gap=merge_gap)
        if not clip_ranges:
            raise ToolkitError("No clip ranges were created from the selected transcript segments.")

    run_context.clips_dir.mkdir(parents=True, exist_ok=True)
    clip_commands: list[list[str]] = []
    concat_lines: list[str] = []
    for clip_range in clip_ranges:
        clip_output = run_context.clips_dir / f"clip-{int(clip_range['id']):03d}.mp4"
        clip_command = build_ffmpeg_clip_command(
            run_context.input_path,
            clip_output,
            float(clip_range["start"]),
            float(clip_range["duration"]),
        )
        run_command(clip_command)
        clip_commands.append(clip_command)
        concat_lines.append(f"file '{clip_output}'")

    run_context.concat_list_path.write_text("\n".join(concat_lines) + "\n")
    concat_command = build_ffmpeg_concat_command(run_context.concat_list_path, run_context.transcript_edit_path)
    run_command(concat_command)

    run_context.clip_ranges_path.write_text(json.dumps(clip_ranges, indent=2))
    if selected_segments:
        run_context.selected_segments_path.write_text(json.dumps(selected_segments, indent=2))

    metadata: dict[str, Any] = {
        "step": "transcript-edit",
        "selection_mode": selection_mode,
        "padding": {
            "before": padding_before,
            "after": padding_after,
        },
        "merge_gap": merge_gap,
        "selected_segment_count": len(selected_segments),
        "clip_count": len(clip_ranges),
        "artifacts": {
            "clip_ranges": str(run_context.clip_ranges_path),
            "transcript_edit": str(run_context.transcript_edit_path),
            "concat_list": str(run_context.concat_list_path),
            "clips_dir": str(run_context.clips_dir),
        },
        "commands": {
            "clips": clip_commands,
            "concat": concat_command,
        },
    }
    if queries:
        metadata["queries"] = queries
    if fuzzy_queries:
        metadata["fuzzy_queries"] = fuzzy_queries
        metadata["fuzzy_threshold"] = fuzzy_threshold
    if ranges_path is not None:
        metadata["ranges_path"] = str(ranges_path.expanduser().resolve())
    if run_context.transcript_path.exists():
        metadata["artifacts"]["transcript"] = str(run_context.transcript_path)
    if run_context.segments_path.exists():
        metadata["artifacts"]["segments"] = str(run_context.segments_path)
    if selected_segments:
        metadata["artifacts"]["selected_segments"] = str(run_context.selected_segments_path)
    if transcribe_metadata is not None:
        metadata["transcribe"] = transcribe_metadata
    return metadata


def generate_review_sheet(
    config: AppConfig,
    run_context: RunContext,
    model_name: str | None = None,
) -> tuple[str, dict[str, Any]]:
    if run_context.segments_path.exists():
        segments = load_segments(run_context)
        transcribe_metadata: dict[str, Any] | None = None
    else:
        segments, transcribe_metadata = transcribe(config, run_context, model_name=model_name)

    review_sheet = build_review_sheet(segments)
    run_context.review_sheet_path.write_text(review_sheet)
    metadata: dict[str, Any] = {
        "step": "review-sheet",
        "segment_count": len(segments),
        "artifacts": {
            "transcript": str(run_context.transcript_path),
            "segments": str(run_context.segments_path),
            "review_sheet": str(run_context.review_sheet_path),
        },
        "preview": review_sheet.splitlines()[:12],
    }
    if transcribe_metadata is not None:
        metadata["transcribe"] = transcribe_metadata
    return review_sheet, metadata


def _parse_id_token(token: str) -> list[int]:
    cleaned = token.strip()
    if not cleaned:
        return []
    if "-" in cleaned:
        start_text, end_text = [part.strip() for part in cleaned.split("-", 1)]
        start = int(start_text)
        end = int(end_text)
        if end < start:
            raise ToolkitError(f"Invalid range `{cleaned}`: end must be >= start.")
        return list(range(start, end + 1))
    return [int(cleaned)]


def parse_review_instructions(instructions: str) -> tuple[str, list[int]]:
    normalized = instructions.strip().lower()
    if not normalized:
        raise ToolkitError("Review instructions cannot be empty.")
    if normalized.startswith("keep "):
        mode = "keep"
        remainder = normalized[5:]
    elif normalized.startswith("cut "):
        mode = "cut"
        remainder = normalized[4:]
    else:
        raise ToolkitError("Review instructions must start with `keep ` or `cut `.")

    items = re.split(r"[,\s]+", remainder)
    segment_ids: list[int] = []
    for item in items:
        if not item:
            continue
        segment_ids.extend(_parse_id_token(item))
    if not segment_ids:
        raise ToolkitError("No segment IDs were found in the review instructions.")
    return mode, sorted(set(segment_ids))


def build_ranges_from_segment_ids(
    segments: list[dict[str, Any]],
    selected_ids: list[int],
    mode: str,
    merge_gap: float = 1.0,
) -> list[dict[str, Any]]:
    segment_lookup = {int(segment["id"]): segment for segment in segments}
    unknown_ids = [segment_id for segment_id in selected_ids if segment_id not in segment_lookup]
    if unknown_ids:
        raise ToolkitError(f"Unknown segment IDs in review instructions: {unknown_ids}")

    if mode == "keep":
        kept_segments = [segment_lookup[segment_id] for segment_id in selected_ids]
    elif mode == "cut":
        kept_segments = [segment for segment in segments if int(segment["id"]) not in set(selected_ids)]
    else:
        raise ToolkitError(f"Unsupported review mode: {mode}")

    if not kept_segments:
        raise ToolkitError("The review instructions removed everything. No kept ranges remain.")

    kept_segments.sort(key=lambda segment: float(segment["start"]))
    clip_ranges: list[dict[str, Any]] = []
    current_range = {
        "start": float(kept_segments[0]["start"]),
        "end": float(kept_segments[0]["end"]),
        "text": str(kept_segments[0]["text"]),
        "segment_ids": [int(kept_segments[0]["id"])],
    }
    for segment in kept_segments[1:]:
        segment_start = float(segment["start"])
        segment_end = float(segment["end"])
        if segment_start <= float(current_range["end"]) + merge_gap:
            current_range["end"] = max(float(current_range["end"]), segment_end)
            current_range["text"] = " ".join(
                part for part in [str(current_range["text"]).strip(), str(segment["text"]).strip()] if part
            ).strip()
            current_range["segment_ids"] = list(current_range["segment_ids"]) + [int(segment["id"])]
        else:
            clip_ranges.append(current_range)
            current_range = {
                "start": segment_start,
                "end": segment_end,
                "text": str(segment["text"]),
                "segment_ids": [int(segment["id"])],
            }
    clip_ranges.append(current_range)

    for index, clip_range in enumerate(clip_ranges, start=1):
        clip_range["id"] = index
        clip_range["duration"] = round(float(clip_range["end"]) - float(clip_range["start"]), 3)
    return clip_ranges


def ranges_from_review(
    config: AppConfig,
    run_context: RunContext,
    instructions: str,
    model_name: str | None = None,
) -> dict[str, Any]:
    if run_context.segments_path.exists():
        segments = load_segments(run_context)
        transcribe_metadata: dict[str, Any] | None = None
    else:
        segments, transcribe_metadata = transcribe(config, run_context, model_name=model_name)

    if not run_context.review_sheet_path.exists():
        review_sheet = build_review_sheet(segments)
        run_context.review_sheet_path.write_text(review_sheet)

    mode, selected_ids = parse_review_instructions(instructions)
    clip_ranges = build_ranges_from_segment_ids(segments, selected_ids, mode=mode)
    run_context.generated_ranges_path.write_text(json.dumps(clip_ranges, indent=2))
    metadata: dict[str, Any] = {
        "step": "ranges-from-review",
        "mode": mode,
        "instructions": instructions,
        "selected_segment_ids": selected_ids,
        "clip_count": len(clip_ranges),
        "artifacts": {
            "review_sheet": str(run_context.review_sheet_path),
            "segments": str(run_context.segments_path),
            "ranges": str(run_context.generated_ranges_path),
        },
        "preview": clip_ranges,
    }
    if run_context.transcript_path.exists():
        metadata["artifacts"]["transcript"] = str(run_context.transcript_path)
    if transcribe_metadata is not None:
        metadata["transcribe"] = transcribe_metadata
    return metadata


def parse_kept_segment_ids(review_sheet_text: str) -> list[int]:
    ids: list[int] = []
    for line in review_sheet_text.splitlines():
        match = re.match(r"^\[(\d+)\]", line.strip())
        if match:
            ids.append(int(match.group(1)))
    return sorted(ids)


def edit_from_review(
    config: AppConfig,
    run_context: RunContext,
    review_path: Path | None = None,
    model_name: str | None = None,
    padding: str | None = None,
) -> dict[str, Any]:
    if run_context.segments_path.exists():
        segments = load_segments(run_context)
        transcribe_metadata: dict[str, Any] | None = None
    else:
        segments, transcribe_metadata = transcribe(config, run_context, model_name=model_name)

    if not run_context.review_sheet_path.exists():
        review_sheet_text = build_review_sheet(segments)
        run_context.review_sheet_path.write_text(review_sheet_text)

    source_path = review_path.expanduser().resolve() if review_path else run_context.review_sheet_path
    if not source_path.exists():
        raise ToolkitError(f"Review sheet does not exist: {source_path}")

    review_sheet_text = source_path.read_text()
    keep_ids = parse_kept_segment_ids(review_sheet_text)
    if not keep_ids:
        raise ToolkitError("No segment IDs found in the review sheet. Nothing to keep.")

    clip_ranges = build_ranges_from_segment_ids(segments, keep_ids, mode="keep")
    run_context.generated_ranges_path.write_text(json.dumps(clip_ranges, indent=2))

    edit_metadata = transcript_edit(
        config,
        run_context,
        ranges_path=run_context.generated_ranges_path,
        padding=padding,
    )

    metadata: dict[str, Any] = {
        "step": "edit-from-review",
        "review_path": str(source_path),
        "kept_segment_ids": keep_ids,
        "clip_count": len(clip_ranges),
        "artifacts": {
            "ranges": str(run_context.generated_ranges_path),
            "transcript_edit": str(run_context.transcript_edit_path),
        },
        "transcript_edit": edit_metadata,
    }
    if run_context.transcript_path.exists():
        metadata["artifacts"]["transcript"] = str(run_context.transcript_path)
    if run_context.segments_path.exists():
        metadata["artifacts"]["segments"] = str(run_context.segments_path)
    if transcribe_metadata is not None:
        metadata["transcribe"] = transcribe_metadata
    return metadata


def ensure_input_exists(input_path: Path) -> None:
    if not input_path.exists():
        raise ToolkitError(f"Input file does not exist: {input_path}")


def extract_audio(config: AppConfig, run_context: RunContext) -> list[str]:
    command = build_ffmpeg_extract_command(
        input_path=run_context.input_path,
        output_path=run_context.audio_path,
        audio_codec=config.ffmpeg.audio_codec,
    )
    run_command(command)
    return command


def transcribe(config: AppConfig, run_context: RunContext, model_name: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    binary = discover_whisper_binary(config)
    if not binary:
        raise ToolkitError("Could not find a whisper-cpp binary. Run `toolkit setup` first.")

    model_path = config.model_path(model_name)
    if not model_path.exists():
        raise ToolkitError(
            f"Missing Whisper model file: {model_path}\n"
            "Place a GGML model file in the models directory before transcribing."
        )

    if not run_context.audio_path.exists():
        extract_audio(config, run_context)

    output_base = run_context.run_dir / "whisper"
    command = build_whisper_command(binary, run_context.audio_path, output_base, model_path)
    gpu_fallback_used = False
    try:
        run_command(command)
    except ToolkitError:
        gpu_fallback_used = True
        command = build_whisper_command(
            binary,
            run_context.audio_path,
            output_base,
            model_path,
            disable_gpu=True,
        )
        run_command(command)

    generated_txt = output_base.with_suffix(".txt")
    generated_json = output_base.with_suffix(".json")
    if generated_txt.exists():
        generated_txt.replace(run_context.transcript_path)

    segments = normalize_whisper_output(
        json_path=generated_json,
        transcript_path=run_context.transcript_path,
        segments_path=run_context.segments_path,
    )
    metadata = {
        "step": "transcribe",
        "binary": binary,
        "command": command,
        "gpu_fallback_used": gpu_fallback_used,
        "model_path": str(model_path),
        "artifacts": {
            "audio": str(run_context.audio_path),
            "transcript": str(run_context.transcript_path),
            "segments": str(run_context.segments_path),
        },
    }
    return segments, metadata


def merge_tokens_to_words(tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for tok in tokens:
        text = tok["text"]
        if text.startswith("["):
            continue
        starts_word = text.startswith(" ") or current is None
        cleaned = text.lstrip(" ")
        if not cleaned:
            continue
        if starts_word:
            if current is not None:
                words.append(current)
            current = {
                "id": len(words),
                "word": cleaned,
                "start": tok["offsets"]["from"] / 1000.0,
                "end": tok["offsets"]["to"] / 1000.0,
            }
        else:
            current["word"] += cleaned
            current["end"] = tok["offsets"]["to"] / 1000.0
    if current is not None:
        words.append(current)
    return words


def _word_ends_sentence(word_text: str) -> bool:
    return word_text.rstrip().endswith((".", "!", "?"))


def _word_ends_soft_break(word_text: str) -> bool:
    return word_text.rstrip().endswith((",", ";", ":"))


def group_words_for_editor(
    words: list[dict[str, Any]],
    sentence_gap: float = 0.7,
    soft_gap: float = 0.35,
    max_words_per_card: int = 24,
) -> list[list[dict[str, Any]]]:
    if not words:
        return []

    cards: list[list[dict[str, Any]]] = [[words[0]]]
    for word in words[1:]:
        current_card = cards[-1]
        previous_word = current_card[-1]
        gap = float(word["start"]) - float(previous_word["end"])
        should_split = False

        if gap >= sentence_gap:
            should_split = True
        elif _word_ends_sentence(str(previous_word["word"])):
            should_split = True
        elif len(current_card) >= max_words_per_card and (
            gap >= soft_gap or _word_ends_soft_break(str(previous_word["word"]))
        ):
            should_split = True

        if should_split:
            cards.append([word])
        else:
            current_card.append(word)
    return cards


def transcribe_words(
    config: AppConfig,
    run_context: RunContext,
    model_name: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if run_context.word_segments_path.exists():
        cached = json.loads(run_context.word_segments_path.read_text())
        segs = load_segments(run_context) if run_context.segments_path.exists() else []
        return cached, segs, {}

    segments, transcribe_metadata = transcribe(config, run_context, model_name=model_name)

    binary = discover_whisper_binary(config)
    if not binary:
        raise ToolkitError("Could not find a whisper-cpp binary.")
    model_path = config.model_path(model_name)

    output_base = run_context.run_dir / "whisper_full"
    command = [binary, "-m", str(model_path), "-f", str(run_context.audio_path), "-ojf", "--output-file", str(output_base)]
    gpu_fallback_used = False
    try:
        run_command(command)
    except ToolkitError:
        gpu_fallback_used = True
        command = [binary, "-m", str(model_path), "-f", str(run_context.audio_path), "-ojf", "--output-file", str(output_base), "--no-gpu", "--no-flash-attn"]
        run_command(command)

    full_json_path = output_base.with_suffix(".json")
    if not full_json_path.exists():
        # Approximate: split segment text evenly across segment duration
        all_words: list[dict[str, Any]] = []
        word_id = 0
        for seg in segments:
            seg_words = seg["text"].split()
            if not seg_words:
                continue
            duration = seg["end"] - seg["start"]
            step = duration / len(seg_words)
            for i, w in enumerate(seg_words):
                all_words.append({
                    "id": word_id,
                    "word": w,
                    "start": round(seg["start"] + i * step, 3),
                    "end": round(seg["start"] + (i + 1) * step, 3),
                })
                word_id += 1
        run_context.word_segments_path.write_text(json.dumps(all_words))
        return all_words, segments, transcribe_metadata

    full_data = json.loads(full_json_path.read_text())
    all_words = []
    word_id = 0
    for seg_data in full_data.get("transcription", []):
        seg_words = merge_tokens_to_words(seg_data.get("tokens", []))
        for w in seg_words:
            w["id"] = word_id
            word_id += 1
            all_words.append(w)

    run_context.word_segments_path.write_text(json.dumps(all_words))
    return all_words, segments, {**transcribe_metadata, "gpu_fallback_used": gpu_fallback_used}


def build_rewrite_plan(
    words: list[dict[str, Any]],
    transcript_text: str,
    *,
    max_silence_gap: float = 0.2,
    audio_silences: list[dict[str, float]] | None = None,
    weak_boundary_score: int = REWRITE_WEAK_BOUNDARY_SCORE,
    scene_boundaries: list[float] | None = None,
    scene_tolerance: float = 0.22,
    scene_bonus: int = 2,
) -> tuple[str, Any, list[dict[str, float]]]:
    target_text, _target_source = resolve_rewrite_target_text(transcript_text=transcript_text)
    match_result = match_rewrite_words(words, target_text)
    clip_ranges = build_ranges_from_kept_word_ids(
        words,
        match_result.kept_word_ids,
        anchored_word_ids=match_result.anchored_word_ids,
        max_silence_gap=max_silence_gap if max_silence_gap > 0 else None,
        audio_silences=audio_silences,
        weak_boundary_score=weak_boundary_score,
        scene_boundaries=scene_boundaries,
        scene_tolerance=scene_tolerance,
        scene_bonus=scene_bonus,
    )
    if not clip_ranges:
        raise ToolkitError("The target transcript did not leave any exportable ranges.")
    return target_text, match_result, clip_ranges


def build_word_match_rows(words: list[dict[str, Any]], match_result: Any) -> list[dict[str, Any]]:
    kept_ids = set(match_result.kept_word_ids)
    anchored_ids = set(match_result.anchored_word_ids)
    return [
        {
            "id": int(word["id"]),
            "word": str(word.get("word", "")),
            "start": float(word["start"]),
            "end": float(word["end"]),
            "decision": "keep" if int(word["id"]) in kept_ids else "cut",
            "anchored": int(word["id"]) in anchored_ids,
        }
        for word in words
    ]


def _gap_overlaps_silence(start: float, end: float, silences: list[dict[str, float]]) -> bool:
    for silence in silences:
        if max(0.0, min(end, float(silence["end"])) - max(start, float(silence["start"]))) > 0:
            return True
    return False


def build_boundary_scores(words: list[dict[str, Any]], audio_silences: list[dict[str, float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(len(words) - 1):
        left = words[index]
        right = words[index + 1]
        gap_start = float(left["end"])
        gap_end = float(right["start"])
        rows.append(
            {
                "after_word_id": int(left["id"]),
                "before_word_id": int(right["id"]),
                "left_word": str(left.get("word", "")),
                "right_word": str(right.get("word", "")),
                "gap_start": gap_start,
                "gap_end": gap_end,
                "gap_duration": round(max(0.0, gap_end - gap_start), 3),
                "score": score_boundary(words, index, index + 1),
                "overlaps_detected_silence": _gap_overlaps_silence(gap_start, gap_end, audio_silences),
            }
        )
    return rows


def build_match_summary(match_result: Any) -> dict[str, int]:
    return {
        "source_word_count": match_result.source_word_count,
        "target_token_count": match_result.target_token_count,
        "kept_word_count": match_result.kept_word_count,
        "cut_word_count": len(match_result.cut_word_ids),
        "matched_target_count": match_result.matched_target_count,
        "unmatched_target_token_count": len(match_result.unmatched_target_tokens),
    }


def generate_decision_report_html(plan: dict[str, Any]) -> str:
    def esc(value: object) -> str:
        return _html.escape(str(value))

    def artifact_href(path: object) -> str:
        return esc(Path(str(path)).name)

    summary = plan.get("match_summary", {})
    options = plan.get("options", {})
    kept = int(summary.get("kept_word_count", 0))
    cut = int(summary.get("cut_word_count", 0))
    total_words = max(kept + cut, 1)
    kept_pct = round((kept / total_words) * 100)
    clip_duration = round(sum(float(item.get("duration", float(item["end"]) - float(item["start"]))) for item in plan.get("clip_ranges", [])), 3)
    silence_duration = round(sum(float(item.get("duration", 0.0)) for item in plan.get("silences", [])), 3)

    summary_cards = "".join(
        f'<div class="metric"><span>{esc(label)}</span><strong>{esc(value)}</strong></div>'
        for label, value in (
            ("Clips", plan.get("clip_count", 0)),
            ("Kept Words", f"{kept} ({kept_pct}%)"),
            ("Cut Words", cut),
            ("Matched Target", f"{summary.get('matched_target_count', 0)}/{summary.get('target_token_count', 0)}"),
            ("Clip Time", f"{clip_duration:.3f}s"),
            ("Detected Silence", f"{silence_duration:.3f}s"),
        )
    )

    option_rows = "".join(
        f"<tr><th>{esc(key)}</th><td><code>{esc(value)}</code></td></tr>"
        for key, value in options.items()
    )

    word_rows = []
    for word in plan.get("word_matches", []):
        klass = "keep" if word.get("decision") == "keep" else "cut"
        anchored = " anchored" if word.get("anchored") else ""
        label = "keep" if klass == "keep" else "cut"
        word_rows.append(
            f'<span class="word {klass}{anchored}" title="{label} {word["start"]:.3f}-{word["end"]:.3f}">{esc(word["word"])}</span>'
        )

    range_rows = []
    for item in plan.get("clip_ranges", []):
        artifact_name = f"clip-{int(item.get('id', 0)):03d}.mp4"
        range_rows.append(
            "<tr>"
            f"<td>{int(item.get('id', 0))}</td>"
            f"<td>{float(item['start']):.3f}</td>"
            f"<td>{float(item['end']):.3f}</td>"
            f"<td>{float(item.get('duration', float(item['end']) - float(item['start']))):.3f}</td>"
            f"<td>{esc(item.get('text', ''))}</td>"
            f'<td><a href="clips/{esc(artifact_name)}">{esc(artifact_name)}</a></td>'
            "</tr>"
        )

    silence_rows = []
    for index, item in enumerate(plan.get("silences", []), start=1):
        silence_rows.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td>{float(item['start']):.3f}</td>"
            f"<td>{float(item['end']):.3f}</td>"
            f"<td>{float(item.get('duration', float(item['end']) - float(item['start']))):.3f}</td>"
            "</tr>"
        )

    boundary_rows = []
    for item in plan.get("boundary_scores", []):
        if item.get("score", 0) <= options.get("weak_boundary_score", 0) and not item.get("overlaps_detected_silence"):
            continue
        signal = "silence" if item.get("overlaps_detected_silence") else "boundary"
        boundary_rows.append(
            "<tr>"
            f"<td>{esc(item['left_word'])} / {esc(item['right_word'])}</td>"
            f"<td>{float(item['gap_duration']):.3f}</td>"
            f"<td>{int(item['score'])}</td>"
            f"<td><span class=\"signal {esc(signal)}\">{esc(signal)}</span></td>"
            "</tr>"
        )

    artifacts = plan.get("artifacts", {})
    artifact_links = "".join(
        f'<a class="artifact" href="{artifact_href(path)}"><span>{esc(name)}</span><code>{esc(Path(str(path)).name)}</code></a>'
        for name, path in artifacts.items()
        if path and Path(str(path)).name
    )
    unmatched_tokens = plan.get("unmatched_target_tokens", [])
    unmatched_html = (
        "<p class=\"warning\"><strong>Unmatched target tokens:</strong> "
        + esc(", ".join(str(token) for token in unmatched_tokens[:24]))
        + (" ..." if len(unmatched_tokens) > 24 else "")
        + "</p>"
        if unmatched_tokens
        else ""
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Edit Decision Report</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: #f7f5f0; color: #202124; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.45; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 28px 22px 44px; }}
header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 22px; }}
h1 {{ margin: 0 0 8px; font-size: 30px; line-height: 1.1; }}
h2 {{ margin: 0 0 12px; font-size: 17px; }}
section {{ margin: 18px 0; background: #fff; border: 1px solid #dedbd2; border-radius: 8px; padding: 16px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border-bottom: 1px solid #e6e2d8; padding: 8px 9px; text-align: left; vertical-align: top; }}
th {{ color: #5f6368; font-weight: 650; }}
tr:last-child td, tr:last-child th {{ border-bottom: 0; }}
a {{ color: #245b8f; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code {{ background: #f0eee7; padding: 2px 4px; border-radius: 4px; overflow-wrap: anywhere; }}
.meta {{ color: #5f6368; font-size: 13px; margin: 0; overflow-wrap: anywhere; }}
.pill {{ display: inline-flex; align-items: center; border: 1px solid #cfc9bc; border-radius: 999px; padding: 5px 9px; font-size: 12px; background: #fff; white-space: nowrap; }}
.metrics {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin-bottom: 18px; }}
.metric {{ background: #fff; border: 1px solid #dedbd2; border-radius: 8px; padding: 12px; min-width: 0; }}
.metric span {{ display: block; color: #6a665d; font-size: 12px; margin-bottom: 4px; }}
.metric strong {{ display: block; font-size: 20px; line-height: 1.1; }}
.grid {{ display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(280px, 0.9fr); gap: 18px; align-items: start; }}
.artifacts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 8px; }}
.artifact {{ display: block; border: 1px solid #e3ded3; border-radius: 6px; padding: 9px 10px; background: #fbfaf7; }}
.artifact span {{ display: block; color: #5f6368; font-size: 12px; margin-bottom: 3px; }}
.artifact code {{ background: transparent; padding: 0; color: #245b8f; }}
.transcript {{ max-height: 52vh; overflow: auto; padding: 12px; border: 1px solid #e6e2d8; border-radius: 6px; background: #fbfaf7; }}
.word {{ display: inline-block; margin: 2px 2px 2px 0; padding: 2px 5px; border-radius: 5px; }}
.word.keep {{ background: #dff0dc; color: #1f4e25; }}
.word.cut {{ background: #f3ded8; color: #7b2b1f; text-decoration: line-through; }}
.word.anchored {{ outline: 2px solid #578d58; outline-offset: 1px; }}
.warning {{ margin: 0 0 12px; padding: 10px 12px; background: #fff5cf; border: 1px solid #e6cf76; border-radius: 6px; color: #5b4a14; }}
.signal {{ display: inline-flex; border-radius: 999px; padding: 2px 7px; font-size: 12px; }}
.signal.silence {{ background: #e4eaf6; color: #29466f; }}
.signal.boundary {{ background: #ece7dc; color: #5b5143; }}
@media (max-width: 860px) {{
  header, .grid {{ display: block; }}
  .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  main {{ padding: 20px 12px 32px; }}
  section {{ padding: 12px; }}
}}
</style>
</head>
<body>
<main>
<header>
<div>
<h1>Edit Decision Report</h1>
<p class="meta">Source: <code>{esc(plan.get("source_path"))}</code></p>
</div>
<span class="pill">{esc(plan.get("workflow"))}</span>
</header>
<div class="metrics">{summary_cards}</div>
{unmatched_html}
<div class="grid">
<section>
<h2>Artifacts</h2>
<div class="artifacts">{artifact_links}</div>
</section>
<section>
<h2>Options</h2>
<table><tbody>{option_rows}</tbody></table>
</section>
</div>
<section>
<h2>Proposed Transcript Decisions</h2>
<div class="transcript">{" ".join(word_rows)}</div>
</section>
<section>
<h2>Clip Ranges</h2>
<table><thead><tr><th>#</th><th>Start</th><th>End</th><th>Duration</th><th>Text</th><th>Clip</th></tr></thead><tbody>{"".join(range_rows)}</tbody></table>
</section>
<section>
<h2>Detected Silences</h2>
<table><thead><tr><th>#</th><th>Start</th><th>End</th><th>Duration</th></tr></thead><tbody>{"".join(silence_rows)}</tbody></table>
</section>
<section>
<h2>Boundary / Silence Signals</h2>
<table><thead><tr><th>Boundary</th><th>Gap</th><th>Score</th><th>Silence</th></tr></thead><tbody>{"".join(boundary_rows)}</tbody></table>
</section>
</main>
</body>
</html>"""


def plan_rewrite_edit(
    config: AppConfig,
    run_context: RunContext,
    *,
    transcript_text: str | None = None,
    transcript_path: Path | None = None,
    model_name: str | None = None,
    padding: str | None = None,
    max_silence_gap: float = 0.2,
    silence_threshold_db: float = REWRITE_AUDIO_SILENCE_DB,
    min_silence_duration: float = REWRITE_AUDIO_SILENCE_MIN_DURATION,
    merge_gap: float = 1.0,
    weak_boundary_score: int = REWRITE_WEAK_BOUNDARY_SCORE,
    preset: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    target_text, target_source = resolve_rewrite_target_text(
        transcript_text=transcript_text,
        transcript_path=transcript_path,
    )
    run_context.rewrite_target_path.write_text(target_text + "\n")

    words, segments, transcribe_metadata = transcribe_words(config, run_context, model_name=model_name)
    speech_regions: list[dict[str, float]] = []
    audio_silences, vad_backend = resolve_silences(
        config,
        run_context,
        silence_threshold_db=silence_threshold_db,
        min_silence_duration=min_silence_duration,
        speech_regions_out=speech_regions,
    )

    scene_boundaries: list[float] = []
    scene_boundaries_path: Path | None = None
    if config.analysis.scene_detection:
        scene_boundaries = detect_scene_boundaries(
            run_context.input_path,
            threshold=config.analysis.scene_threshold,
        ) or []
        scene_boundaries_path = run_context.run_dir / "scene_boundaries.json"
        scene_boundaries_path.write_text(json.dumps(scene_boundaries, indent=2))

    _resolved_target_text, match_result, ranges = build_rewrite_plan(
        words,
        target_text,
        max_silence_gap=max_silence_gap,
        audio_silences=audio_silences,
        weak_boundary_score=weak_boundary_score,
        scene_boundaries=scene_boundaries,
        scene_tolerance=config.analysis.scene_snap_tolerance,
        scene_bonus=config.analysis.scene_score_bonus,
    )
    ranges, scene_snaps = snap_ranges_to_scene_boundaries(
        ranges,
        scene_boundaries,
        tolerance=config.analysis.scene_snap_tolerance,
    )
    padding_before, padding_after = parse_padding(padding, 0.0, 0.0)
    input_duration = probe_duration(run_context.input_path)
    clip_ranges = apply_padding_to_ranges(ranges, input_duration, padding_before, padding_after, merge_gap=merge_gap)

    run_context.generated_ranges_path.write_text(json.dumps(ranges, indent=2))
    run_context.clip_ranges_path.write_text(json.dumps(clip_ranges, indent=2))

    plan: dict[str, Any] = {
        "step": "plan-edit",
        "workflow": "rewrite-edit",
        "source_path": str(run_context.input_path),
        "target_transcript_source": target_source,
        "preset": preset,
        "notes": notes,
        "options": {
            "model": model_name or config.whisper.model_name,
            "padding": {"before": padding_before, "after": padding_after},
            "max_silence_gap": max_silence_gap,
            "merge_gap": merge_gap,
            "silence_threshold_db": silence_threshold_db,
            "min_silence_duration": min_silence_duration,
            "weak_boundary_score": weak_boundary_score,
        },
        "match_summary": build_match_summary(match_result),
        "unmatched_target_tokens": match_result.unmatched_target_tokens,
        "word_matches": build_word_match_rows(words, match_result),
        "silences": audio_silences,
        "vad_backend": vad_backend,
        "speech_regions": speech_regions,
        "scene_boundaries": scene_boundaries,
        "scene_snaps": scene_snaps,
        "boundary_scores": build_boundary_scores(words, audio_silences),
        "ranges": ranges,
        "clip_ranges": clip_ranges,
        "clip_count": len(clip_ranges),
        "artifacts": {
            "target_transcript": str(run_context.rewrite_target_path),
            "word_segments": str(run_context.word_segments_path),
            "ranges": str(run_context.generated_ranges_path),
            "clip_ranges": str(run_context.clip_ranges_path),
            "edit_plan": str(run_context.edit_plan_path),
            "decision_report": str(run_context.decision_report_path),
        },
    }
    if scene_boundaries_path is not None:
        plan["artifacts"]["scene_boundaries"] = str(scene_boundaries_path)
    if run_context.transcript_path.exists():
        plan["artifacts"]["transcript"] = str(run_context.transcript_path)
    if run_context.segments_path.exists():
        plan["artifacts"]["segments"] = str(run_context.segments_path)
        plan["segment_count"] = len(segments)
    if transcribe_metadata:
        plan["transcribe"] = transcribe_metadata

    run_context.edit_plan_path.write_text(json.dumps(plan, indent=2))
    run_context.decision_report_path.write_text(generate_decision_report_html(plan))
    return plan


def rewrite_edit(
    config: AppConfig,
    run_context: RunContext,
    *,
    transcript_text: str | None = None,
    transcript_path: Path | None = None,
    model_name: str | None = None,
    padding: str | None = None,
    max_silence_gap: float = 0.2,
    silence_threshold_db: float = REWRITE_AUDIO_SILENCE_DB,
    min_silence_duration: float = REWRITE_AUDIO_SILENCE_MIN_DURATION,
    merge_gap: float = 1.0,
    weak_boundary_score: int = REWRITE_WEAK_BOUNDARY_SCORE,
    preset: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    plan = plan_rewrite_edit(
        config,
        run_context,
        transcript_text=transcript_text,
        transcript_path=transcript_path,
        model_name=model_name,
        padding=padding,
        max_silence_gap=max_silence_gap,
        silence_threshold_db=silence_threshold_db,
        min_silence_duration=min_silence_duration,
        merge_gap=merge_gap,
        weak_boundary_score=weak_boundary_score,
        preset=preset,
        notes=notes,
    )
    transcript_edit_metadata = transcript_edit(
        config,
        run_context,
        ranges_path=run_context.generated_ranges_path,
        padding=padding if padding is not None or merge_gap == 1.0 else "0,0",
        merge_gap=merge_gap,
    )

    metadata: dict[str, Any] = {
        "step": "rewrite-edit",
        "target_transcript_source": plan["target_transcript_source"],
        "clip_count": int(transcript_edit_metadata.get("clip_count", plan["clip_count"])),
        "match_summary": plan["match_summary"],
        "max_silence_gap": max_silence_gap,
        "merge_gap": merge_gap,
        "audio_silence_detection": {
            "noise_db": silence_threshold_db,
            "min_duration": min_silence_duration,
            "silence_count": len(plan["silences"]),
            "vad_backend": plan["vad_backend"],
        },
        "artifacts": {
            "target_transcript": str(run_context.rewrite_target_path),
            "word_segments": str(run_context.word_segments_path),
            "ranges": str(run_context.generated_ranges_path),
            "clip_ranges": str(run_context.clip_ranges_path),
            "edit_plan": str(run_context.edit_plan_path),
            "decision_report": str(run_context.decision_report_path),
            "transcript_edit": str(run_context.transcript_edit_path),
        },
        "plan": str(run_context.edit_plan_path),
        "transcript_edit": transcript_edit_metadata,
    }
    if preset:
        metadata["preset"] = preset
    if notes:
        metadata["notes"] = notes
    if plan["unmatched_target_tokens"]:
        metadata["unmatched_target_tokens"] = plan["unmatched_target_tokens"]
    if run_context.transcript_path.exists():
        metadata["artifacts"]["transcript"] = str(run_context.transcript_path)
    if run_context.segments_path.exists():
        metadata["artifacts"]["segments"] = str(run_context.segments_path)
        metadata["segment_count"] = plan.get("segment_count", 0)
    if plan.get("transcribe"):
        metadata["transcribe"] = plan["transcribe"]

    write_run_metadata(run_context, metadata)
    return metadata


def generate_word_editor_html(
    words: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    input_filename: str,
) -> str:
    SILENCE_GAP = 0.3

    editor_cards = group_words_for_editor(words)

    card_html_parts: list[str] = []
    for seg_i, sw in enumerate(editor_cards):
        if not sw:
            continue
        body_parts: list[str] = []
        for w_i, word in enumerate(sw):
            if w_i > 0:
                prev = sw[w_i - 1]
                gap = word["start"] - prev["end"]
                body_parts.append(f'<span class="split-pt" data-after="{prev["id"]}"></span>')
                if gap > SILENCE_GAP:
                    body_parts.append(
                        f'<span class="gap" data-start="{prev["end"]:.3f}" data-end="{word["start"]:.3f}">[...{gap:.1f}s]</span>'
                )
            body_parts.append(
                f'<span class="word" data-id="{word["id"]}" data-start="{word["start"]:.3f}" data-end="{word["end"]:.3f}"> {_html.escape(word["word"])}</span>'
            )
        body_html = "".join(body_parts)
        card_start = float(sw[0]["start"])
        card_end = float(sw[-1]["end"])
        card_html_parts.append(
            f'<div class="card" data-seg="{seg_i}">'
            f'<div class="card-grip" draggable="true">\u2807</div>'
            f'<div class="card-body">{body_html}</div>'
            f'<div class="card-time">{format_review_timestamp(card_start)}-{format_review_timestamp(card_end)}</div>'
            f'<div class="card-actions"><button class="cut-card-btn">Cut sentence</button></div>'
            f'</div>'
        )

    editor_inner = "\n".join(card_html_parts)
    words_json = json.dumps(words)
    filler_words_json = json.dumps(list(WORD_EDITOR_FILLER_WORDS))
    escaped_filename = _html.escape(input_filename)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Word Editor \u2014 {escaped_filename}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: #111; color: #ddd; font-family: system-ui, sans-serif; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }}
#video-wrap {{ background: #000; display: flex; justify-content: center; padding: 8px; flex-shrink: 0; }}
#v {{ max-height: 35vh; max-width: 100%; display: block; }}
#toolbar {{ display: flex; align-items: center; gap: 12px; padding: 8px 16px; background: #1a1a1a; border-bottom: 1px solid #333; flex-shrink: 0; }}
#toolbar .filename {{ font-weight: 600; font-size: 13px; color: #aaa; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
#cut-count {{ font-size: 12px; color: #888; white-space: nowrap; }}
#cleanup-status {{ font-size: 12px; color: #6f8fb7; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
#undo-btn, #cleanup-fillers-btn, #cleanup-pauses-btn, #transcript-btn, #copy-ai-btn, #search-prev-btn, #search-next-btn {{ padding: 6px 12px; background: #333; color: #ddd; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; white-space: nowrap; }}
#undo-btn:hover, #cleanup-fillers-btn:hover, #cleanup-pauses-btn:hover, #transcript-btn:hover, #copy-ai-btn:hover, #search-prev-btn:hover, #search-next-btn:hover {{ background: #444; }}
#undo-btn:disabled {{ opacity: 0.4; cursor: default; }}
#cleanup-fillers-btn, #cleanup-pauses-btn {{ background: #243224; color: #b9d6b9; }}
#cleanup-fillers-btn:hover, #cleanup-pauses-btn:hover {{ background: #2d3f2d; }}
#transcript-btn.active {{ background: #2a3f5f; color: #7ab4f5; }}
#search-box {{ width: 200px; padding: 6px 10px; background: #101010; color: #ddd; border: 1px solid #2a2a2a; border-radius: 4px; font-size: 13px; }}
#search-box:focus {{ outline: none; border-color: #4a9fd4; }}
#search-status {{ font-size: 12px; color: #777; min-width: 68px; }}
#export-btn {{ margin-left: auto; padding: 6px 16px; background: #1e4a8a; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; white-space: nowrap; }}
#export-btn:hover {{ background: #2558a8; }}
#export-btn:disabled {{ opacity: 0.5; cursor: default; }}
#main {{ display: flex; flex: 1; overflow: hidden; }}
#editor-wrap {{ flex: 1; overflow-y: auto; padding: 16px 20px; user-select: none; }}
#editor {{ counter-reset: card-num; }}
.card {{ counter-increment: card-num; display: flex; align-items: flex-start; gap: 8px; padding: 10px 12px; margin-bottom: 6px; border: 1px solid #1e1e1e; border-radius: 6px; background: #161616; position: relative; }}
.card:hover {{ border-color: #2a2a2a; background: #191919; }}
.card.all-cut {{ opacity: 0.45; }}
.card.search-hit {{ border-color: #355179; }}
.card.dragging {{ opacity: 0.35; }}
.card.drag-over-top {{ border-top: 2px solid #4a9fd4; }}
.card.drag-over-bottom {{ border-bottom: 2px solid #4a9fd4; }}
.card-grip {{ cursor: grab; color: #3a3a3a; font-size: 18px; line-height: 1; padding-top: 4px; flex-shrink: 0; user-select: none; display: flex; flex-direction: column; align-items: center; gap: 2px; }}
.card-grip::before {{ content: counter(card-num); font-size: 10px; font-weight: 600; color: #444; line-height: 1; cursor: default; }}
.card:hover .card-grip {{ color: #666; }}
.card:hover .card-grip::before {{ color: #666; }}
.card-grip:active {{ cursor: grabbing; }}
.card-body {{ flex: 1; line-height: 2.3; font-size: 16px; }}
.card-time {{ font-size: 10px; color: #5f5f5f; padding-top: 4px; white-space: nowrap; }}
.card-actions {{ display: none; flex-shrink: 0; align-self: center; }}
.card:hover .card-actions {{ display: flex; }}
.cut-card-btn {{ padding: 3px 9px; background: #2a1a1a; color: #c07070; border: 1px solid #3a2020; border-radius: 3px; cursor: pointer; font-size: 11px; white-space: nowrap; }}
.cut-card-btn:hover {{ background: #3a2020; color: #e08080; }}
.word {{ cursor: default; padding: 1px 2px; border-radius: 2px; }}
.word.cut {{ opacity: 0.3; text-decoration: line-through; }}
.word.selected {{ background: #1e4a8a; border-radius: 2px; }}
.word.playing {{ background: #0d4d1a; }}
.word.search-hit {{ background: #3b2d0a; }}
.word.search-hit.search-active {{ background: #a06d00; color: #111; }}
.word.cut.selected {{ background: #3a1a1a; }}
.gap {{ color: #555; font-size: 11px; margin: 0 3px; cursor: pointer; }}
.gap.cut {{ opacity: 0.3; }}
.gap.selected {{ background: #1e3a6a; border-radius: 2px; }}
.split-pt {{ display: inline-block; width: 5px; height: 1.4em; vertical-align: middle; cursor: col-resize; position: relative; flex-shrink: 0; }}
.split-pt::after {{ content: ''; position: absolute; left: 2px; top: 15%; bottom: 15%; width: 1px; background: transparent; border-radius: 1px; transition: background 0.1s; }}
.card:hover .split-pt::after {{ background: #2e2e2e; }}
.split-pt:hover::after {{ background: #4a9fd4 !important; width: 2px; left: 1.5px; }}
#transcript-panel {{ width: 340px; flex-shrink: 0; display: none; flex-direction: column; border-left: 1px solid #222; background: #141414; }}
#transcript-panel.open {{ display: flex; }}
#panel-header {{ display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid #222; flex-shrink: 0; }}
#panel-header span {{ font-size: 11px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: 0.06em; }}
#copy-btn {{ padding: 3px 10px; background: #222; color: #888; border: none; border-radius: 3px; cursor: pointer; font-size: 11px; }}
#copy-btn:hover {{ background: #2e2e2e; color: #bbb; }}
#transcript-ta {{ flex: 1; background: #111; color: #bbb; border: none; resize: none; padding: 14px; font-size: 14px; line-height: 1.75; outline: none; font-family: inherit; }}
#transcript-ta:focus {{ background: #121212; }}
#apply-info {{ font-size: 11px; color: #444; padding: 6px 14px 8px; flex-shrink: 0; line-height: 1.5; }}
#apply-info.active {{ color: #7b94b8; }}
#panel-footer {{ padding: 10px 14px; border-top: 1px solid #222; display: flex; gap: 8px; flex-shrink: 0; }}
#apply-btn {{ flex: 1; padding: 7px 0; background: #1a5c33; color: #eee; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: 600; }}
#apply-btn:hover {{ background: #216b3c; }}
#apply-btn:disabled {{ opacity: 0.5; cursor: default; }}
#reset-ta-btn {{ padding: 7px 12px; background: #222; color: #888; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }}
#reset-ta-btn:hover {{ background: #2e2e2e; color: #bbb; }}
#done-screen {{ display: none; position: fixed; inset: 0; background: #111; align-items: center; justify-content: center; flex-direction: column; gap: 16px; }}
#done-screen h2 {{ color: #ddd; font-size: 18px; }}
#done-screen code {{ background: #1a1a1a; padding: 10px 16px; border-radius: 4px; font-size: 14px; color: #7fc97f; display: block; max-width: 90vw; overflow-x: auto; white-space: pre; }}
#done-screen p {{ color: #888; font-size: 13px; }}
</style>
</head>
<body>
<div id="video-wrap">
  <video id="v" src="/video" controls preload="metadata"></video>
</div>
<div id="toolbar">
  <span class="filename">{escaped_filename}</span>
  <span id="cut-count">0 words cut</span>
  <button id="undo-btn" disabled>Undo</button>
  <button id="cleanup-fillers-btn">Cut fillers</button>
  <button id="cleanup-pauses-btn">Cut long pauses</button>
  <input id="search-box" type="search" placeholder="Search transcript" spellcheck="false">
  <button id="search-prev-btn">Prev</button>
  <button id="search-next-btn">Next</button>
  <span id="search-status">0 / 0</span>
  <span id="cleanup-status"></span>
  <button id="transcript-btn">Transcript</button>
  <button id="copy-ai-btn">Copy for AI</button>
  <button id="export-btn">Export cuts</button>
</div>
<div id="main">
  <div id="editor-wrap">
    <div id="editor">
{editor_inner}
    </div>
  </div>
  <div id="transcript-panel">
    <div id="panel-header">
      <span>Target Transcript</span>
      <button id="copy-btn">Copy</button>
    </div>
    <textarea id="transcript-ta" spellcheck="false"></textarea>
    <div id="apply-info">Edit or paste your desired transcript. Approximate matching keeps close transcript words even when the transcript needs small fixes.</div>
    <div id="panel-footer">
      <button id="reset-ta-btn">Reset</button>
      <button id="apply-btn">Apply \u2192 Cut</button>
    </div>
  </div>
</div>
<div id="done-screen">
  <h2>Done! Video rendered.</h2>
  <code id="done-output"></code>
  <p id="done-meta">Your edited video has been exported.</p>
</div>
<script>
const WORDS = {words_json};
const FILLER_WORDS = new Set({filler_words_json});
const LONG_PAUSE_SECONDS = {WORD_EDITOR_LONG_PAUSE_SECONDS:.1f};

let selecting = false;
let selStart = null;
let undoStack = [];
let searchMatches = [];
let activeSearchIndex = -1;
let cleanupStatusTimer = null;

// ── Helpers ───────────────────────────────────────────────
function allWordGapSpans() {{
  return Array.from(document.querySelectorAll('.word, .gap'));
}}

function selectedSpans() {{
  return Array.from(document.querySelectorAll('.selected'));
}}

function getCurrentTranscript() {{
  const words = [];
  for (const card of document.querySelectorAll('.card')) {{
    for (const w of card.querySelectorAll('.word')) {{
      words.push(w.textContent.trim());
    }}
  }}
  return words.join(' ');
}}

function updateCutCount() {{
  const wordsCut = document.querySelectorAll('.word.cut').length;
  const gapsCut = document.querySelectorAll('.gap.cut').length;
  let label = wordsCut + ' word' + (wordsCut !== 1 ? 's' : '') + ' cut';
  if (gapsCut) {{
    label += ', ' + gapsCut + ' pause' + (gapsCut !== 1 ? 's' : '') + ' cut';
  }}
  document.getElementById('cut-count').textContent = label;
  for (const card of document.querySelectorAll('.card')) {{
    const ws = card.querySelectorAll('.word');
    const allCut = ws.length > 0 && Array.from(ws).every(w => w.classList.contains('cut'));
    card.classList.toggle('all-cut', allCut);
  }}
}}

function setCleanupStatus(message) {{
  const el = document.getElementById('cleanup-status');
  el.textContent = message;
  if (cleanupStatusTimer) clearTimeout(cleanupStatusTimer);
  if (!message) return;
  cleanupStatusTimer = setTimeout(() => {{
    el.textContent = '';
    cleanupStatusTimer = null;
  }}, 2600);
}}

function snapshotCuts() {{
  const snap = new Set();
  document.querySelectorAll('.cut').forEach(el => {{
    snap.add(el.dataset.id !== undefined ? 'w:' + el.dataset.id : 'g:' + el.dataset.start);
  }});
  return snap;
}}

function restoreCuts(snap) {{
  allWordGapSpans().forEach(el => {{
    const key = el.dataset.id !== undefined ? 'w:' + el.dataset.id : 'g:' + el.dataset.start;
    el.classList.toggle('cut', snap.has(key));
  }});
  updateCutCount();
}}

function pushUndo() {{
  undoStack.push(snapshotCuts());
  document.getElementById('undo-btn').disabled = false;
}}

function applyBulkCut(elements) {{
  const toCut = elements.filter(el => !el.classList.contains('cut'));
  if (!toCut.length) return 0;
  pushUndo();
  toCut.forEach(el => {{
    el.classList.add('cut');
    el.classList.remove('selected');
  }});
  updateCutCount();
  return toCut.length;
}}

function formatCardTimestamp(seconds) {{
  const totalMs = Math.max(Math.round(seconds * 1000), 0);
  const minutes = Math.floor(totalMs / 60000);
  const secs = Math.floor((totalMs % 60000) / 1000);
  const millis = totalMs % 1000;
  return String(minutes).padStart(2, '0') + ':' + String(secs).padStart(2, '0') + '.' + String(millis).padStart(3, '0');
}}

function normalizeSearchText(text) {{
  return text.toLowerCase().replace(/[^a-z0-9']/g, ' ').trim();
}}

function updateSearchStatus() {{
  const status = document.getElementById('search-status');
  if (!searchMatches.length) {{
    status.textContent = '0 / 0';
    return;
  }}
  status.textContent = (activeSearchIndex + 1) + ' / ' + searchMatches.length;
}}

function clearSearchHighlights() {{
  document.querySelectorAll('.word.search-hit, .word.search-active').forEach(el => {{
    el.classList.remove('search-hit', 'search-active');
  }});
  document.querySelectorAll('.card.search-hit').forEach(el => el.classList.remove('search-hit'));
  searchMatches = [];
  activeSearchIndex = -1;
  updateSearchStatus();
}}

function applySearchHighlights() {{
  document.querySelectorAll('.word.search-hit, .word.search-active').forEach(el => {{
    el.classList.remove('search-hit', 'search-active');
  }});
  document.querySelectorAll('.card.search-hit').forEach(el => el.classList.remove('search-hit'));
  for (const match of searchMatches) {{
    for (let idx = match.start; idx <= match.end; idx++) {{
      const el = document.querySelector('.word[data-id="' + idx + '"]');
      if (el) {{
        el.classList.add('search-hit');
        const card = el.closest('.card');
        if (card) card.classList.add('search-hit');
      }}
    }}
  }}
  if (activeSearchIndex >= 0 && activeSearchIndex < searchMatches.length) {{
    const active = searchMatches[activeSearchIndex];
    let anchor = null;
    for (let idx = active.start; idx <= active.end; idx++) {{
      const el = document.querySelector('.word[data-id="' + idx + '"]');
      if (!el) continue;
      el.classList.add('search-active');
      if (!anchor) anchor = el;
    }}
    if (anchor) {{
      anchor.scrollIntoView({{ block: 'center', behavior: 'smooth' }});
      video.currentTime = parseFloat(anchor.dataset.start);
    }}
  }}
  updateSearchStatus();
}}

function findMatches(query) {{
  const tokens = normalizeSearchText(query).split(/\\s+/).filter(Boolean);
  if (!tokens.length) return [];
  const normalizedWords = WORDS.map(w => normalizeSearchText(w.word));
  const matches = [];
  for (let start = 0; start <= normalizedWords.length - tokens.length; start++) {{
    let ok = true;
    for (let i = 0; i < tokens.length; i++) {{
      if (normalizedWords[start + i] !== tokens[i]) {{
        ok = false;
        break;
      }}
    }}
    if (ok) matches.push({{ start, end: start + tokens.length - 1 }});
  }}
  return matches;
}}

function runSearch(step) {{
  const query = document.getElementById('search-box').value;
  if (!query.trim()) {{
    clearSearchHighlights();
    return;
  }}
  const matches = findMatches(query);
  if (!matches.length) {{
    clearSearchHighlights();
    return;
  }}
  const sameSearch =
    matches.length === searchMatches.length &&
    matches.every((match, idx) => searchMatches[idx] && searchMatches[idx].start === match.start && searchMatches[idx].end === match.end);
  searchMatches = matches;
  if (!sameSearch || activeSearchIndex < 0) {{
    activeSearchIndex = 0;
  }} else {{
    activeSearchIndex = (activeSearchIndex + step + searchMatches.length) % searchMatches.length;
  }}
  applySearchHighlights();
}}

function normalizeCleanupToken(text) {{
  return text.toLowerCase().replace(/[^a-z0-9']/g, '');
}}

function cutFillerWords() {{
  const fillers = Array.from(document.querySelectorAll('.word')).filter(el => FILLER_WORDS.has(normalizeCleanupToken(el.textContent)));
  const count = applyBulkCut(fillers);
  setCleanupStatus(count ? 'Cut ' + count + ' filler word' + (count !== 1 ? 's' : '') : 'No filler words found');
}}

function cutLongPauses() {{
  const longPauses = Array.from(document.querySelectorAll('.gap')).filter(el => (parseFloat(el.dataset.end) - parseFloat(el.dataset.start)) >= LONG_PAUSE_SECONDS);
  const count = applyBulkCut(longPauses);
  setCleanupStatus(count ? 'Cut ' + count + ' long pause' + (count !== 1 ? 's' : '') : 'No long pauses found');
}}

// ── Card factory (used by split) ──────────────────────────
function makeCard(bodyChildren) {{
  const card = document.createElement('div');
  card.className = 'card';
  const grip = document.createElement('div');
  grip.className = 'card-grip';
  grip.setAttribute('draggable', 'true');
  grip.textContent = '\u2807';
  const body = document.createElement('div');
  body.className = 'card-body';
  for (const el of bodyChildren) body.appendChild(el);
  const time = document.createElement('div');
  time.className = 'card-time';
  const words = bodyChildren.filter(el => el.classList && el.classList.contains('word'));
  if (words.length) {{
    time.textContent = formatCardTimestamp(parseFloat(words[0].dataset.start)) + '-' + formatCardTimestamp(parseFloat(words[words.length - 1].dataset.end));
  }}
  const actions = document.createElement('div');
  actions.className = 'card-actions';
  const cutBtn = document.createElement('button');
  cutBtn.className = 'cut-card-btn';
  cutBtn.textContent = 'Cut sentence';
  actions.appendChild(cutBtn);
  card.appendChild(grip);
  card.appendChild(body);
  card.appendChild(time);
  card.appendChild(actions);
  return card;
}}

// ── Card-level actions ────────────────────────────────────
const editor = document.getElementById('editor');

editor.addEventListener('click', e => {{
  // Cut sentence toggle
  if (e.target.classList.contains('cut-card-btn')) {{
    const card = e.target.closest('.card');
    if (!card) return;
    pushUndo();
    const ws = Array.from(card.querySelectorAll('.word'));
    const gs = Array.from(card.querySelectorAll('.gap'));
    const allCut = ws.every(w => w.classList.contains('cut'));
    [...ws, ...gs].forEach(el => el.classList.toggle('cut', !allCut));
    updateCutCount();
    return;
  }}
  // Split sentence
  if (e.target.classList.contains('split-pt')) {{
    const splitPt = e.target;
    const card = splitPt.closest('.card');
    const body = card.querySelector('.card-body');
    const children = Array.from(body.children);
    const idx = children.indexOf(splitPt);
    if (idx < 0) return;
    const toMove = children.slice(idx + 1);
    if (!toMove.length) return;
    splitPt.remove();
    for (const el of toMove) el.remove();
    card.after(makeCard(toMove));
    return;
  }}
}});

// ── Drag to reorder ───────────────────────────────────────
let dragSrc = null;

editor.addEventListener('dragstart', e => {{
  const grip = e.target.closest('.card-grip');
  if (!grip) {{ e.preventDefault(); return; }}
  dragSrc = grip.closest('.card');
  dragSrc.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', '');
}});

editor.addEventListener('dragend', () => {{
  if (dragSrc) dragSrc.classList.remove('dragging');
  document.querySelectorAll('.drag-over-top, .drag-over-bottom').forEach(c => {{
    c.classList.remove('drag-over-top', 'drag-over-bottom');
  }});
  dragSrc = null;
}});

editor.addEventListener('dragover', e => {{
  e.preventDefault();
  const card = e.target.closest('.card');
  if (!card || card === dragSrc) return;
  document.querySelectorAll('.drag-over-top, .drag-over-bottom').forEach(c => {{
    c.classList.remove('drag-over-top', 'drag-over-bottom');
  }});
  const rect = card.getBoundingClientRect();
  card.classList.add(e.clientY < rect.top + rect.height / 2 ? 'drag-over-top' : 'drag-over-bottom');
}});

editor.addEventListener('drop', e => {{
  e.preventDefault();
  const card = e.target.closest('.card');
  if (!card || card === dragSrc || !dragSrc) return;
  const rect = card.getBoundingClientRect();
  if (e.clientY < rect.top + rect.height / 2) card.before(dragSrc);
  else card.after(dragSrc);
  document.querySelectorAll('.drag-over-top, .drag-over-bottom').forEach(c => {{
    c.classList.remove('drag-over-top', 'drag-over-bottom');
  }});
}});

// ── Word selection ────────────────────────────────────────
editor.addEventListener('mousedown', e => {{
  const target = e.target.closest('.word, .gap');
  if (!target) return;
  e.preventDefault();
  selecting = true;
  selStart = target;
  document.querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
  target.classList.add('selected');
}});

editor.addEventListener('mouseover', e => {{
  if (!selecting || !selStart) return;
  const target = e.target.closest('.word, .gap');
  if (!target) return;
  // Constrain selection to the same card
  const srcCard = selStart.closest('.card');
  const tgtCard = target.closest('.card');
  if (srcCard !== tgtCard) return;
  const spans = Array.from(srcCard.querySelectorAll('.word, .gap'));
  const a = spans.indexOf(selStart), b = spans.indexOf(target);
  const lo = Math.min(a, b), hi = Math.max(a, b);
  document.querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
  spans.forEach((el, i) => {{ if (i >= lo && i <= hi) el.classList.add('selected'); }});
}});

document.addEventListener('mouseup', () => {{
  if (!selecting) return;
  selecting = false;
  const sel = selectedSpans();
  if (sel.length === 1 && sel[0].classList.contains('cut')) {{
    pushUndo();
    sel[0].classList.remove('cut', 'selected');
    updateCutCount();
  }}
}});

document.addEventListener('keydown', e => {{
  if (e.target.id === 'transcript-ta') return;
  if (e.key === 'Delete' || e.key === 'Backspace') {{
    const sel = selectedSpans();
    if (!sel.length) return;
    e.preventDefault();
    pushUndo();
    sel.forEach(el => {{ el.classList.add('cut'); el.classList.remove('selected'); }});
    updateCutCount();
    return;
  }}
  if ((e.ctrlKey || e.metaKey) && e.key === 'z') {{
    e.preventDefault();
    if (!undoStack.length) return;
    restoreCuts(undoStack.pop());
    document.getElementById('undo-btn').disabled = undoStack.length === 0;
  }}
}});

document.getElementById('undo-btn').addEventListener('click', () => {{
  if (!undoStack.length) return;
  restoreCuts(undoStack.pop());
  document.getElementById('undo-btn').disabled = undoStack.length === 0;
}});

document.getElementById('cleanup-fillers-btn').addEventListener('click', cutFillerWords);
document.getElementById('cleanup-pauses-btn').addEventListener('click', cutLongPauses);
document.getElementById('search-box').addEventListener('input', () => runSearch(0));
document.getElementById('search-box').addEventListener('keydown', e => {{
  if (e.key !== 'Enter') return;
  e.preventDefault();
  runSearch(e.shiftKey ? -1 : 1);
}});
document.getElementById('search-prev-btn').addEventListener('click', () => runSearch(-1));
document.getElementById('search-next-btn').addEventListener('click', () => runSearch(1));

// ── Transcript panel ──────────────────────────────────────
const transcriptBtn = document.getElementById('transcript-btn');
const panel = document.getElementById('transcript-panel');
const applyInfo = document.getElementById('apply-info');

function setApplyInfo(message, active = false) {{
  applyInfo.textContent = message;
  applyInfo.classList.toggle('active', active);
}}

transcriptBtn.addEventListener('click', () => {{
  const open = panel.classList.toggle('open');
  transcriptBtn.classList.toggle('active', open);
  if (open) document.getElementById('transcript-ta').value = getCurrentTranscript();
}});

document.getElementById('copy-btn').addEventListener('click', () => {{
  const ta = document.getElementById('transcript-ta');
  navigator.clipboard.writeText(ta.value).then(() => {{
    const btn = document.getElementById('copy-btn');
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 1500);
  }});
}});

document.getElementById('reset-ta-btn').addEventListener('click', () => {{
  document.getElementById('transcript-ta').value = getCurrentTranscript();
  setApplyInfo('Edit or paste your desired transcript. Approximate matching keeps close transcript words even when the transcript needs small fixes.');
}});

document.getElementById('apply-btn').addEventListener('click', async () => {{
  const btn = document.getElementById('apply-btn');
  const transcript = document.getElementById('transcript-ta').value;
  btn.disabled = true;
  btn.textContent = 'Applying...';
  try {{
    const res = await fetch('/rewrite-apply', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{transcript}}),
    }});
    if (!res.ok) {{
      const message = await res.text();
      throw new Error(message || ('HTTP ' + res.status));
    }}
    const data = await res.json();
    const kept = new Set(data.kept_word_ids.map(id => String(id)));
    pushUndo();
    WORDS.forEach((w, idx) => {{
      const el = document.querySelector('.word[data-id="' + idx + '"]');
      if (el) {{
        el.classList.toggle('cut', !kept.has(String(idx)));
        el.classList.remove('selected');
      }}
    }});
    updateCutCount();
    const unmatched = data.unmatched_target_tokens || [];
    const summary = data.match_summary || {{}};
    if (unmatched.length) {{
      const sample = unmatched.slice(0, 4).join(', ');
      setApplyInfo(
        'Applied server rewrite match. ' + (summary.matched_target_count || 0) + '/' + (summary.target_token_count || 0) + ' target tokens matched. Unmatched: ' + sample + (unmatched.length > 4 ? ', ...' : ''),
        true,
      );
    }} else {{
      setApplyInfo(
        'Applied server rewrite match. ' + (summary.matched_target_count || 0) + '/' + (summary.target_token_count || 0) + ' target tokens matched.',
        true,
      );
    }}
  }} catch (err) {{
    setApplyInfo('Rewrite apply failed: ' + err.message, true);
  }} finally {{
    btn.disabled = false;
    btn.textContent = 'Apply → Cut';
  }}
}});

// ── Playback sync ─────────────────────────────────────────
const video = document.getElementById('v');
let playingEl = null;

video.addEventListener('timeupdate', () => {{
  const t = video.currentTime;
  let lo = 0, hi = WORDS.length - 1, found = -1;
  while (lo <= hi) {{
    const mid = (lo + hi) >> 1;
    if (WORDS[mid].end < t) lo = mid + 1;
    else if (WORDS[mid].start > t) hi = mid - 1;
    else {{ found = mid; break; }}
  }}
  const el = found >= 0 ? document.querySelector('.word[data-id="' + found + '"]') : null;
  if (el === playingEl) return;
  if (playingEl) playingEl.classList.remove('playing');
  playingEl = el;
  if (el) el.classList.add('playing');
}});

// ── Export ────────────────────────────────────────────────
function buildRanges() {{
  const ranges = [];
  for (const card of document.querySelectorAll('.card')) {{
    // Split ranges only at CUT spans — consecutive kept words always merge
    let cur = null;
    for (const span of card.querySelectorAll('.word, .gap')) {{
      if (span.classList.contains('cut')) {{
        if (cur) {{ ranges.push(cur); cur = null; }}
      }} else {{
        const s = parseFloat(span.dataset.start), e = parseFloat(span.dataset.end);
        if (!cur) cur = {{start: s, end: e}};
        else cur.end = Math.max(cur.end, e);
      }}
    }}
    if (cur) ranges.push(cur);
  }}
  return ranges.filter(r => r.end - r.start >= 0.1);
}}

function buildAITranscript() {{
  const lines = ['Transcript (numbered sentences — ✓ kept, ✗ cut, [cut words] shown inline):\n'];
  let n = 0;
  for (const card of document.querySelectorAll('.card')) {{
    n++;
    const words = Array.from(card.querySelectorAll('.word'));
    if (!words.length) continue;
    const allCut = words.every(w => w.classList.contains('cut'));
    let text = '';
    for (const w of words) {{
      const wt = w.textContent.trim();
      if (w.classList.contains('cut')) text += ' [' + wt + ']';
      else text += ' ' + wt;
    }}
    text = text.trim();
    lines.push((allCut ? `[${{n}} \u2717] ` : `[${{n}} \u2713] `) + text);
  }}
  return lines.join('\n');
}}

document.getElementById('copy-ai-btn').addEventListener('click', () => {{
  const btn = document.getElementById('copy-ai-btn');
  navigator.clipboard.writeText(buildAITranscript()).then(() => {{
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy for AI', 1500);
  }});
}});

document.getElementById('export-btn').addEventListener('click', async () => {{
  const btn = document.getElementById('export-btn');
  btn.disabled = true;
  btn.textContent = 'Exporting...';
  try {{
    const res = await fetch('/submit', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ranges: buildRanges()}}),
    }});
    if (!res.ok) {{
      const message = await res.text();
      throw new Error(message || ('HTTP ' + res.status));
    }}
    const data = await res.json();
    document.getElementById('done-output').textContent = data.transcript_edit;
    document.getElementById('done-meta').textContent = data.clip_count + ' clip' + (data.clip_count !== 1 ? 's' : '') + ' rendered to ' + data.run_dir;
    document.getElementById('done-screen').style.display = 'flex';
  }} catch (err) {{
    btn.disabled = false;
    btn.textContent = 'Export cuts';
    alert('Export failed: ' + err.message);
  }}
}});
</script>
</body>
</html>"""


def export_word_editor_ranges(
    config: AppConfig,
    run_context: RunContext,
    ranges: list[dict[str, Any]],
) -> dict[str, Any]:
    run_context.generated_ranges_path.write_text(json.dumps(ranges, indent=2))
    transcript_edit_metadata = transcript_edit(config, run_context, ranges_path=run_context.generated_ranges_path)
    return {
        "ranges": str(run_context.generated_ranges_path),
        "clip_ranges": str(run_context.clip_ranges_path),
        "transcript_edit": str(run_context.transcript_edit_path),
        "run_dir": str(run_context.run_dir),
        "clip_count": int(transcript_edit_metadata.get("clip_count", 0)),
        "transcript_edit_metadata": transcript_edit_metadata,
    }


def apply_word_editor_rewrite(
    run_context: RunContext,
    words: list[dict[str, Any]],
    transcript_text: str,
    *,
    max_silence_gap: float = 0.2,
    config: AppConfig | None = None,
) -> dict[str, Any]:
    audio_silence_min_duration = min(max(max_silence_gap, 0.0), REWRITE_AUDIO_SILENCE_MIN_DURATION) or REWRITE_AUDIO_SILENCE_MIN_DURATION
    if config is not None:
        audio_silences, vad_backend = resolve_silences(
            config,
            run_context,
            silence_threshold_db=REWRITE_AUDIO_SILENCE_DB,
            min_silence_duration=audio_silence_min_duration,
        )
    else:
        # No config supplied (e.g. direct/unit-test calls): behave exactly as
        # before and stay on the ffmpeg silence detector.
        audio_silences = detect_audio_silences(
            run_context.audio_path,
            noise_db=REWRITE_AUDIO_SILENCE_DB,
            min_duration=audio_silence_min_duration,
        )
        vad_backend = "ffmpeg"
    target_text, match_result, clip_ranges = build_rewrite_plan(
        words,
        transcript_text,
        max_silence_gap=max_silence_gap,
        audio_silences=audio_silences,
    )
    run_context.rewrite_target_path.write_text(target_text + "\n")
    return {
        "kept_word_ids": match_result.kept_word_ids,
        "cut_word_ids": match_result.cut_word_ids,
        "ranges": clip_ranges,
        "match_summary": {
            "source_word_count": match_result.source_word_count,
            "target_token_count": match_result.target_token_count,
            "kept_word_count": match_result.kept_word_count,
            "cut_word_count": len(match_result.cut_word_ids),
            "matched_target_count": match_result.matched_target_count,
            "unmatched_target_token_count": len(match_result.unmatched_target_tokens),
        },
        "unmatched_target_tokens": match_result.unmatched_target_tokens,
        "max_silence_gap": max_silence_gap,
        "vad_backend": vad_backend,
        "audio_silence_detection": {
            "noise_db": REWRITE_AUDIO_SILENCE_DB,
            "min_duration": audio_silence_min_duration,
            "silence_count": len(audio_silences),
            "vad_backend": vad_backend,
        },
        "target_transcript": str(run_context.rewrite_target_path),
    }


def serve_word_editor(
    config: AppConfig,
    run_context: RunContext,
    model_name: str | None = None,
) -> dict[str, Any]:
    effective_model = model_name or config.whisper.model_name
    if "base" in effective_model:
        print(
            "Tip: for better word-level accuracy, download a larger model:\n"
            "  curl -L -o models/ggml-small.en.bin \\\n"
            "    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin\n"
            f"Then re-run with: toolkit word-editor {run_context.input_path} --model small.en",
            flush=True,
        )

    words, segments, transcribe_metadata = transcribe_words(config, run_context, model_name=model_name)
    input_filename = run_context.input_path.name
    html_content = generate_word_editor_html(words, segments, input_filename)

    done_event = threading.Event()
    result: dict[str, Any] = {}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if self.path == "/":
                body = html_content.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/video":
                self._serve_video()
            else:
                self.send_response(404)
                self.end_headers()

        def _serve_video(self) -> None:
            _stream_video(self, run_context.input_path)

        def do_POST(self) -> None:
            try:
                if self.path not in {"/submit", "/rewrite-apply"}:
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                if self.path == "/submit":
                    payload = export_word_editor_ranges(config, run_context, body.get("ranges", []))
                else:
                    payload = apply_word_editor_rewrite(run_context, words, body.get("transcript", ""), config=config)
            except ToolkitError as exc:
                message = str(exc).encode()
                self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)
                return
            except Exception as exc:
                message = f"Unexpected export error: {exc}".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(message)))
                self.end_headers()
                self.wfile.write(message)
                return

            if self.path == "/submit":
                result.update(payload)
                response_payload = {
                    "ranges": payload["ranges"],
                    "clip_ranges": payload["clip_ranges"],
                    "transcript_edit": payload["transcript_edit"],
                    "run_dir": payload["run_dir"],
                    "clip_count": payload["clip_count"],
                }
            else:
                response_payload = payload
            response = json.dumps(response_payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            threading.Thread(target=done_event.set, daemon=True).start()

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"Word Editor: {url}", flush=True)
    webbrowser.open(url)

    done_event.wait()
    server.shutdown()

    metadata: dict[str, Any] = {
        "step": "word-editor",
        "word_count": len(words),
        "artifacts": {
            "word_segments": str(run_context.word_segments_path),
            "ranges": result.get("ranges", ""),
            "clip_ranges": result.get("clip_ranges", ""),
            "transcript_edit": result.get("transcript_edit", ""),
        },
        "clip_count": result.get("clip_count", 0),
    }
    if result.get("transcript_edit_metadata"):
        metadata["export"] = result["transcript_edit_metadata"]
    if transcribe_metadata:
        metadata["transcribe"] = transcribe_metadata
    return metadata


def edit_media(config: AppConfig, run_context: RunContext, margin: str | None = None) -> dict[str, Any]:
    chosen_margin = margin or config.auto_editor.margin
    auto_editor_binary = discover_auto_editor_binary(config.project_root)
    if not auto_editor_binary:
        raise ToolkitError("Could not find auto-editor. Run `toolkit setup --apply` first.")
    command = build_auto_editor_command(
        binary=auto_editor_binary,
        input_path=run_context.input_path,
        output_path=run_context.edited_path,
        margin=chosen_margin,
        video_codec=config.ffmpeg.video_codec,
        quality=config.ffmpeg.quality,
    )
    fallback_used = False
    try:
        run_command(command, env=auto_editor_env())
    except ToolkitError:
        if config.ffmpeg.video_codec != "h264_videotoolbox":
            raise
        fallback_used = True
        command = build_auto_editor_command(
            binary=auto_editor_binary,
            input_path=run_context.input_path,
            output_path=run_context.edited_path,
            margin=chosen_margin,
            video_codec="libx264",
            quality=config.ffmpeg.quality,
        )
        run_command(command, env=auto_editor_env())
    return {
        "step": "edit",
        "command": command,
        "margin": chosen_margin,
        "fallback_used": fallback_used,
        "artifacts": {
            "edited": str(run_context.edited_path),
        },
    }


def generate_captions(config: AppConfig, run_context: RunContext, model_name: str | None = None) -> dict[str, Any]:
    if run_context.segments_path.exists():
        segments = json.loads(run_context.segments_path.read_text())
    else:
        segments, _ = transcribe(config, run_context, model_name=model_name)

    write_srt(segments, run_context.captions_path)
    return {
        "step": "captions",
        "format": config.captions.format,
        "artifacts": {
            "captions": str(run_context.captions_path),
        },
    }


def python_from_venv(project_root: Path) -> Path:
    return venv_bin(project_root, "python")


def pip_from_venv(project_root: Path) -> Path:
    return venv_bin(project_root, "pip")


def setup_environment(config: AppConfig, apply_changes: bool) -> dict[str, Any]:
    brew = shutil.which("brew")
    if not brew:
        raise ToolkitError("Homebrew is not installed or not on PATH.")

    python_executable = Path(sys.executable)
    venv_python = python_from_venv(config.project_root)

    actions: list[list[str]] = []
    if not shutil.which("ffmpeg"):
        actions.append([brew, "install", "ffmpeg"])
    if not discover_whisper_binary(config):
        actions.append([brew, "install", "whisper-cpp"])
    if not venv_python.exists():
        actions.append([str(python_executable), "-m", "venv", str(config.project_root / ".venv")])

    pip_path = pip_from_venv(config.project_root)
    auto_editor_path = venv_bin(config.project_root, "auto-editor")
    pip_actions = [
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        [str(venv_python), "-m", "pip", "install", "."],
        [str(pip_path), "install", "auto-editor", "moviepy", "pytest"],
        [str(auto_editor_path), "--version"],
    ]

    if apply_changes:
        for action in actions:
            run_command(action, cwd=config.project_root)
        if not venv_python.exists():
            raise ToolkitError("Virtual environment was not created successfully.")
        for action in pip_actions:
            env = auto_editor_env() if action[0] == str(auto_editor_path) else None
            run_command(action, cwd=config.project_root, env=env)

    model_path = config.model_path()
    return {
        "brew_available": True,
        "actions": actions + pip_actions,
        "model_hint": f"Download a GGML model file such as {model_path.name} into {model_path.parent}",
        "applied": apply_changes,
    }


def doctor(config: AppConfig) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    auto_editor_binary = discover_auto_editor_binary(config.project_root)
    checks["brew"] = bool(shutil.which("brew"))
    checks["ffmpeg"] = bool(shutil.which("ffmpeg"))
    checks["videotoolbox"] = ffmpeg_supports_videotoolbox() if checks["ffmpeg"] else False
    whisper_binary = discover_whisper_binary(config)
    checks["whisper_binary"] = whisper_binary
    checks["auto_editor_binary"] = auto_editor_binary
    checks["venv_python"] = str(python_from_venv(config.project_root))
    checks["venv_exists"] = python_from_venv(config.project_root).exists()
    checks["model_path"] = str(config.model_path())
    checks["model_exists"] = config.model_path().exists()

    imports: dict[str, bool] = {}
    import_timeouts: dict[str, bool] = {}
    python_bin = python_from_venv(config.project_root)
    for module_name in ("auto_editor", "moviepy"):
        if python_bin.exists():
            import_ok, timed_out = _run_check_command(
                [str(python_bin), "-c", f"import {module_name}"],
                timeout=DOCTOR_IMPORT_TIMEOUT_SECONDS,
            )
            imports[module_name] = import_ok
            import_timeouts[module_name] = timed_out
        else:
            imports[module_name] = False
            import_timeouts[module_name] = False
    checks["python_imports"] = imports
    checks["python_import_timeouts"] = import_timeouts
    checks["auto_editor_runnable"] = False
    checks["auto_editor_runnable_timed_out"] = False
    if auto_editor_binary:
        runnable_ok, runnable_timed_out = _run_check_command(
            [auto_editor_binary, "--version"],
            env=auto_editor_env(),
            timeout=DOCTOR_COMMAND_TIMEOUT_SECONDS,
        )
        checks["auto_editor_runnable"] = runnable_ok
        checks["auto_editor_runnable_timed_out"] = runnable_timed_out
    checks["ok"] = all(
        [
            checks["brew"],
            checks["ffmpeg"],
            checks["videotoolbox"],
            bool(checks["whisper_binary"]),
            bool(checks["auto_editor_binary"]),
            checks["auto_editor_runnable"],
            checks["venv_exists"],
            checks["model_exists"],
            imports.get("auto_editor", False),
        ]
    )

    # Soft, informational-only probes for the optional analysis extras.
    # Neither key participates in `checks["ok"]`: Silero VAD and PySceneDetect
    # are optional accelerants, and their absence must never fail `doctor`.
    try:
        import onnxruntime  # noqa: F401

        checks["silero_available"] = True
    except Exception:
        checks["silero_available"] = False
    try:
        import scenedetect  # noqa: F401

        checks["scenedetect_available"] = True
    except Exception:
        checks["scenedetect_available"] = False

    return checks


def generate_review_ui_html(segments: list[dict[str, Any]], input_filename: str) -> str:
    segments_json = json.dumps(segments)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Review: {input_filename}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: monospace; background: #0f0f0f; color: #d4d4d4; display: flex; flex-direction: column; height: 100vh; }}
  #player {{ background: #000; width: 100%; max-height: 280px; flex-shrink: 0; }}
  #toolbar {{ display: flex; align-items: center; gap: 12px; padding: 8px 12px; background: #161616; border-bottom: 1px solid #2a2a2a; flex-shrink: 0; }}
  #toolbar h1 {{ font-size: 13px; color: #888; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  #cut-count {{ font-size: 12px; color: #888; }}
  #submit-btn {{ padding: 6px 16px; background: #2563eb; color: #fff; border: none; border-radius: 4px; font-family: monospace; font-size: 13px; cursor: pointer; }}
  #submit-btn:hover {{ background: #1d4ed8; }}
  #submit-btn:disabled {{ background: #333; color: #666; cursor: default; }}
  #segments {{ flex: 1; overflow-y: auto; }}
  .seg {{ display: flex; align-items: baseline; gap: 0; padding: 0; border-bottom: 1px solid #1a1a1a; cursor: pointer; user-select: none; }}
  .seg:hover {{ background: #181818; }}
  .seg.playing {{ background: #0d2010; }}
  .seg.cut {{ opacity: 0.3; }}
  .seg.cut .seg-text {{ text-decoration: line-through; }}
  .seg-toggle {{ width: 36px; text-align: center; padding: 8px 0; font-size: 11px; color: #555; flex-shrink: 0; }}
  .seg.cut .seg-toggle {{ color: #c0392b; }}
  .seg-id {{ width: 36px; text-align: right; padding: 8px 4px; font-size: 11px; color: #444; flex-shrink: 0; }}
  .seg-time {{ width: 160px; padding: 8px 8px; font-size: 11px; color: #666; flex-shrink: 0; }}
  .seg-text {{ flex: 1; padding: 8px 8px 8px 0; font-size: 13px; line-height: 1.4; }}
  #done-screen {{ display: none; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 16px; padding: 32px; }}
  #done-screen h2 {{ color: #4ade80; font-size: 16px; }}
  #done-cmd {{ background: #1a1a1a; border: 1px solid #333; border-radius: 4px; padding: 12px 16px; font-size: 13px; color: #d4d4d4; width: 100%; max-width: 600px; word-break: break-all; }}
  #copy-btn {{ padding: 6px 16px; background: #333; color: #d4d4d4; border: none; border-radius: 4px; font-family: monospace; font-size: 13px; cursor: pointer; }}
  #copy-btn:hover {{ background: #444; }}
</style>
</head>
<body>
<video id="player" src="/video" controls preload="metadata"></video>
<div id="toolbar">
  <h1>{input_filename}</h1>
  <span id="cut-count"></span>
  <button id="submit-btn" onclick="submitCuts()">Export cuts</button>
</div>
<div id="segments"></div>
<div id="done-screen">
  <h2>✓ Review sheet saved</h2>
  <div id="done-cmd"></div>
  <button id="copy-btn" onclick="copyCmd()">Copy command</button>
  <p style="font-size:12px;color:#666">You can close this tab.</p>
</div>
<script>
const SEGMENTS = {segments_json};
const kept = new Set(SEGMENTS.map(s => s.id));
const video = document.getElementById('player');
let currentId = null;

function formatTime(s) {{
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(3).padStart(6, '0');
  return m.toString().padStart(2, '0') + ':' + sec;
}}

function updateCutCount() {{
  const cuts = SEGMENTS.length - kept.size;
  document.getElementById('cut-count').textContent =
    cuts === 0 ? '' : cuts + ' cut' + (cuts === 1 ? '' : 's');
}}

function renderSegments() {{
  const container = document.getElementById('segments');
  container.innerHTML = '';
  for (const seg of SEGMENTS) {{
    const row = document.createElement('div');
    row.className = 'seg' + (kept.has(seg.id) ? '' : ' cut');
    row.dataset.id = seg.id;
    row.dataset.start = seg.start;

    const toggle = document.createElement('div');
    toggle.className = 'seg-toggle';
    toggle.textContent = kept.has(seg.id) ? '●' : '○';
    toggle.title = kept.has(seg.id) ? 'Click to cut' : 'Click to keep';

    const id = document.createElement('div');
    id.className = 'seg-id';
    id.textContent = '[' + String(seg.id).padStart(2, '0') + ']';

    const time = document.createElement('div');
    time.className = 'seg-time';
    time.textContent = formatTime(seg.start) + '-' + formatTime(seg.end);

    const text = document.createElement('div');
    text.className = 'seg-text';
    text.textContent = seg.text;

    row.appendChild(toggle);
    row.appendChild(id);
    row.appendChild(time);
    row.appendChild(text);

    row.addEventListener('click', (e) => {{
      if (e.target === toggle) {{
        toggleSeg(seg.id, row, toggle);
      }} else {{
        video.currentTime = seg.start;
        video.play();
        if (currentId !== null) {{
          const prev = document.querySelector('.seg.playing');
          if (prev) prev.classList.remove('playing');
        }}
        row.classList.add('playing');
        currentId = seg.id;
      }}
    }});

    container.appendChild(row);
  }}
  updateCutCount();
}}

function toggleSeg(id, row, toggle) {{
  if (kept.has(id)) {{
    kept.delete(id);
    row.classList.add('cut');
    toggle.textContent = '○';
    toggle.title = 'Click to keep';
  }} else {{
    kept.add(id);
    row.classList.remove('cut');
    toggle.textContent = '●';
    toggle.title = 'Click to cut';
  }}
  updateCutCount();
}}

async function submitCuts() {{
  const btn = document.getElementById('submit-btn');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  const res = await fetch('/submit', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{ kept_ids: [...kept] }}),
  }});
  const data = await res.json();
  document.getElementById('segments').style.display = 'none';
  document.getElementById('toolbar').style.display = 'none';
  document.getElementById('player').style.display = 'none';
  const done = document.getElementById('done-screen');
  done.style.display = 'flex';
  document.getElementById('done-cmd').textContent = data.command;
}}

function copyCmd() {{
  const cmd = document.getElementById('done-cmd').textContent;
  navigator.clipboard.writeText(cmd).then(() => {{
    document.getElementById('copy-btn').textContent = 'Copied!';
    setTimeout(() => document.getElementById('copy-btn').textContent = 'Copy command', 1500);
  }});
}}

renderSegments();
</script>
</body>
</html>"""


def _stream_video(handler: BaseHTTPRequestHandler, path: Path) -> None:
    file_size = path.stat().st_size
    range_header = handler.headers.get("Range")
    try:
        if range_header:
            byte_range = range_header.strip().replace("bytes=", "")
            parts = byte_range.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1
            handler.send_response(206)
            handler.send_header("Content-Type", "video/mp4")
            handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            handler.send_header("Content-Length", str(length))
            handler.send_header("Accept-Ranges", "bytes")
            handler.end_headers()
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    data = fh.read(min(65536, remaining))
                    if not data:
                        break
                    handler.wfile.write(data)
                    remaining -= len(data)
        else:
            handler.send_response(200)
            handler.send_header("Content-Type", "video/mp4")
            handler.send_header("Content-Length", str(file_size))
            handler.send_header("Accept-Ranges", "bytes")
            handler.end_headers()
            with open(path, "rb") as fh:
                while True:
                    data = fh.read(65536)
                    if not data:
                        break
                    handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        pass


def serve_review_ui(
    config: AppConfig,
    run_context: RunContext,
    model_name: str | None = None,
) -> dict[str, Any]:
    if run_context.segments_path.exists():
        segments = load_segments(run_context)
        transcribe_metadata: dict[str, Any] | None = None
    else:
        segments, transcribe_metadata = transcribe(config, run_context, model_name=model_name)

    input_filename = run_context.input_path.name
    html = generate_review_ui_html(segments, input_filename)
    run_context.review_ui_path.write_text(html)

    done_event = threading.Event()
    result: dict[str, Any] = {}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            if self.path == "/":
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/video":
                self._serve_video()
            else:
                self.send_response(404)
                self.end_headers()

        def _serve_video(self) -> None:
            _stream_video(self, run_context.input_path)

        def do_POST(self) -> None:
            if self.path != "/submit":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            kept_ids: set[int] = set(int(i) for i in body.get("kept_ids", []))
            kept_segments = [s for s in segments if int(s["id"]) in kept_ids]
            review_sheet = build_review_sheet(kept_segments)
            run_context.review_sheet_path.write_text(review_sheet)
            command = f"toolkit edit-from-review {run_context.input_path}"
            result["review_sheet"] = str(run_context.review_sheet_path)
            result["command"] = command
            response = json.dumps({"review_sheet": str(run_context.review_sheet_path), "command": command}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            threading.Thread(target=done_event.set, daemon=True).start()

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"Review UI: {url}", flush=True)
    webbrowser.open(url)

    done_event.wait()
    server.shutdown()

    kept_ids = parse_kept_segment_ids(run_context.review_sheet_path.read_text()) if run_context.review_sheet_path.exists() else []
    metadata: dict[str, Any] = {
        "step": "review-ui",
        "segment_count": len(segments),
        "kept_segment_count": len(kept_ids),
        "artifacts": {
            "review_ui": str(run_context.review_ui_path),
            "review_sheet": result.get("review_sheet", ""),
            "segments": str(run_context.segments_path),
        },
        "next_command": result.get("command", ""),
    }
    if transcribe_metadata is not None:
        metadata["transcribe"] = transcribe_metadata
    return metadata
