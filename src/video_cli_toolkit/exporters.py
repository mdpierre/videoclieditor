"""Pure NLE export builders (FCPXML + EDL) plus the ffprobe-based format probe.

This module only *reads* clip_ranges data produced elsewhere in the toolkit and
serializes it into NLE-importable project files. It never edits or renders video.

Only ``probe_video_format`` shells out (to ``ffprobe``). Everything else here is
pure and safe to unit test without any external binaries.

Note on ``ToolkitError``: ``workflow.py`` defines the canonical ``ToolkitError`` used
across the toolkit, but ``workflow.py`` is a heavy module (it imports ``.analysis``,
``.config``, ``.rewrite_matcher``, ``http.server``, ``webbrowser``, etc.) and is
expected to import *this* module in a later integration step (to call
``probe_video_format`` from a workflow helper). Importing ``workflow`` from here would
therefore risk a circular import. We define a local ``ToolkitError`` instead, matching
``workflow.ToolkitError``'s shape (a plain ``Exception`` subclass used for
user-actionable failures).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from pathlib import Path
import json
import re
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET


class ToolkitError(Exception):
    """Raised for user-actionable export failures (e.g. ffprobe failure)."""


@dataclass(frozen=True)
class VideoFormat:
    fps_num: int  # e.g. 30000
    fps_den: int  # e.g. 1001  (=> 29.97)
    width: int
    height: int
    duration: float  # source duration, seconds
    src_path: str  # absolute path to the source video
    name: str  # source stem, for asset/reel naming


# ---------------------------------------------------------------------------
# Timecode helpers (pure)
# ---------------------------------------------------------------------------


def seconds_to_frames(seconds: float, fps_num: int, fps_den: int) -> int:
    """Round to the nearest frame; clamp to >= 0."""
    return max(0, round(seconds * fps_num / fps_den))


def frames_to_timecode(frame: int, fps_num: int, fps_den: int) -> str:
    """NON-DROP HH:MM:SS:FF using the integer nominal rate round(fps_num/fps_den)."""
    fps_int = max(1, round(fps_num / fps_den))
    ff = frame % fps_int
    total_secs = frame // fps_int
    ss = total_secs % 60
    mm = (total_secs // 60) % 60
    hh = total_secs // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def frames_to_fcpxml_time(frame: int, fps_num: int, fps_den: int) -> str:
    """F frames == (F*fps_den)/fps_num seconds. Return an exact reduced rational."""
    if frame == 0:
        return "0s"
    numerator = frame * fps_den
    denominator = fps_num
    g = gcd(numerator, denominator)
    numerator //= g
    denominator //= g
    return f"{numerator}s" if denominator == 1 else f"{numerator}/{denominator}s"


def fcpxml_frame_duration(fps_num: int, fps_den: int) -> str:
    """One frame duration in seconds = fps_den/fps_num, simplified."""
    g = gcd(fps_den, fps_num)
    return f"{fps_den // g}/{fps_num // g}s"


# ---------------------------------------------------------------------------
# EDL (CMX3600) builder
# ---------------------------------------------------------------------------

_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x1f\x7f]+")


def _clean_comment_text(text: str) -> str:
    """Strip newlines/control chars so a clip's text stays on one EDL line."""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text or "")
    return cleaned.strip()


def build_edl(clip_ranges: list[dict], fmt: VideoFormat, *, title: str | None = None) -> str:
    lines: list[str] = []
    lines.append(f"TITLE: {title or fmt.name}")
    lines.append("FCM: NON-DROP FRAME")
    lines.append("")

    event_num = 0
    rec_frame = 0
    for clip in clip_ranges:
        src_in_frame = seconds_to_frames(clip["start"], fmt.fps_num, fmt.fps_den)
        src_out_frame = seconds_to_frames(clip["end"], fmt.fps_num, fmt.fps_den)
        clip_len = src_out_frame - src_in_frame
        if clip_len <= 0:
            continue

        event_num += 1
        rec_in_frame = rec_frame
        rec_out_frame = rec_in_frame + clip_len
        rec_frame = rec_out_frame

        src_in_tc = frames_to_timecode(src_in_frame, fmt.fps_num, fmt.fps_den)
        src_out_tc = frames_to_timecode(src_out_frame, fmt.fps_num, fmt.fps_den)
        rec_in_tc = frames_to_timecode(rec_in_frame, fmt.fps_num, fmt.fps_den)
        rec_out_tc = frames_to_timecode(rec_out_frame, fmt.fps_num, fmt.fps_den)

        event_line = (
            f"{event_num:03d}  AX       AA/V  C        "
            f"{src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}"
        )
        lines.append(event_line)
        lines.append(f"* FROM CLIP NAME: {fmt.name}")
        lines.append(f"* COMMENT: {_clean_comment_text(clip.get('text', ''))}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# FCPXML builder
# ---------------------------------------------------------------------------


def _abs_file_url(src_path: str) -> str:
    abs_path = str(Path(src_path).resolve())
    return "file://" + urllib.parse.quote(abs_path)


def build_fcpxml(
    clip_ranges: list[dict],
    fmt: VideoFormat,
    *,
    project_name: str | None = None,
    add_markers: bool = True,
) -> str:
    fcpxml = ET.Element("fcpxml", {"version": "1.9"})

    resources = ET.SubElement(fcpxml, "resources")
    frame_duration = fcpxml_frame_duration(fmt.fps_num, fmt.fps_den)
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": "FFVideoFormatRateUndefined",
            "frameDuration": frame_duration,
            "width": str(fmt.width),
            "height": str(fmt.height),
        },
    )

    total_frames = seconds_to_frames(fmt.duration, fmt.fps_num, fmt.fps_den)
    ET.SubElement(
        resources,
        "asset",
        {
            "id": "r2",
            "name": fmt.name,
            "src": _abs_file_url(fmt.src_path),
            "start": "0s",
            "duration": frames_to_fcpxml_time(total_frames, fmt.fps_num, fmt.fps_den),
            "hasVideo": "1",
            "hasAudio": "1",
            "format": "r1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": "48000",
        },
    )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": "video-cli-toolkit"})
    project = ET.SubElement(event, "project", {"name": project_name or fmt.name})

    # First pass: compute kept clips + total timeline duration.
    kept_clips = []
    cumulative = 0
    for clip in clip_ranges:
        src_in = seconds_to_frames(clip["start"], fmt.fps_num, fmt.fps_den)
        src_out = seconds_to_frames(clip["end"], fmt.fps_num, fmt.fps_den)
        clip_len = src_out - src_in
        if clip_len <= 0:
            continue
        kept_clips.append((clip, src_in, clip_len, cumulative))
        cumulative += clip_len

    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "duration": frames_to_fcpxml_time(cumulative, fmt.fps_num, fmt.fps_den),
        },
    )
    spine = ET.SubElement(sequence, "spine")

    for clip, src_in, clip_len, offset in kept_clips:
        text = clip.get("text", "")
        asset_clip = ET.SubElement(
            spine,
            "asset-clip",
            {
                "ref": "r2",
                "offset": frames_to_fcpxml_time(offset, fmt.fps_num, fmt.fps_den),
                "name": text,
                "start": frames_to_fcpxml_time(src_in, fmt.fps_num, fmt.fps_den),
                "duration": frames_to_fcpxml_time(clip_len, fmt.fps_num, fmt.fps_den),
                "tcFormat": "NDF",
            },
        )
        if add_markers and text:
            ET.SubElement(
                asset_clip,
                "marker",
                {
                    "start": frames_to_fcpxml_time(src_in, fmt.fps_num, fmt.fps_den),
                    "duration": frame_duration,
                    "value": text,
                },
            )

    body = ET.tostring(fcpxml, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


# ---------------------------------------------------------------------------
# ffprobe-based format probe (the only function that shells out)
# ---------------------------------------------------------------------------


def probe_video_format(src_path: Path) -> VideoFormat:
    src_path = Path(src_path)
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=r_frame_rate,width,height",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(src_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ToolkitError(f"Failed to run ffprobe on {src_path}: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise ToolkitError(f"ffprobe failed for {src_path}: {stderr}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ToolkitError(f"Could not parse ffprobe output for {src_path}: {exc}") from exc

    streams = data.get("streams") or []
    if not streams:
        raise ToolkitError(f"ffprobe returned no video stream for {src_path}")
    stream = streams[0]

    r_frame_rate = stream.get("r_frame_rate")
    if not r_frame_rate:
        raise ToolkitError(f"ffprobe returned no r_frame_rate for {src_path}")
    try:
        parts = r_frame_rate.split("/")
        fps_num = int(parts[0])
        fps_den = int(parts[1]) if len(parts) > 1 else 1
    except (ValueError, IndexError) as exc:
        raise ToolkitError(f"Unparseable r_frame_rate {r_frame_rate!r} for {src_path}") from exc

    try:
        width = int(stream["width"])
        height = int(stream["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolkitError(f"Missing/unparseable width or height for {src_path}") from exc

    fmt_section = data.get("format") or {}
    try:
        duration = float(fmt_section["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolkitError(f"Missing/unparseable duration for {src_path}") from exc

    abs_path = Path(src_path).resolve()
    return VideoFormat(
        fps_num=fps_num,
        fps_den=fps_den,
        width=width,
        height=height,
        duration=duration,
        src_path=str(abs_path),
        name=abs_path.stem,
    )
