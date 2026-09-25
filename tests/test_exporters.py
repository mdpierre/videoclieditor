from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from video_cli_toolkit.exporters import (
    ToolkitError,
    VideoFormat,
    build_edl,
    build_fcpxml,
    fcpxml_frame_duration,
    frames_to_fcpxml_time,
    frames_to_timecode,
    probe_video_format,
    seconds_to_frames,
)


NTSC_FMT = VideoFormat(
    fps_num=30000,
    fps_den=1001,
    width=1920,
    height=1080,
    duration=10.0,
    src_path="/tmp/source.mp4",
    name="source",
)

INT_FMT = VideoFormat(
    fps_num=30,
    fps_den=1,
    width=1920,
    height=1080,
    duration=10.0,
    src_path="/tmp/source.mp4",
    name="source",
)

CLIP_RANGES = [
    {"id": 1, "start": 0.0, "end": 1.0, "duration": 1.0, "text": "hello world", "segment_ids": [0]},
    {"id": 2, "start": 2.0, "end": 3.0, "duration": 1.0, "text": "second clip", "segment_ids": [1]},
]


# ---------------------------------------------------------------------------
# seconds_to_frames
# ---------------------------------------------------------------------------


def test_seconds_to_frames_ntsc():
    assert seconds_to_frames(1.0, 30000, 1001) == 30


def test_seconds_to_frames_integer():
    assert seconds_to_frames(1.0, 30, 1) == 30


def test_seconds_to_frames_clamps_nonnegative():
    assert seconds_to_frames(-1.0, 30, 1) == 0


# ---------------------------------------------------------------------------
# frames_to_fcpxml_time
# ---------------------------------------------------------------------------


def test_frames_to_fcpxml_time_ntsc_30_frames():
    assert frames_to_fcpxml_time(30, 30000, 1001) == "1001/1000s"


def test_frames_to_fcpxml_time_zero():
    assert frames_to_fcpxml_time(0, 30000, 1001) == "0s"
    assert frames_to_fcpxml_time(0, 30, 1) == "0s"


def test_frames_to_fcpxml_time_integer_rate_whole_second():
    assert frames_to_fcpxml_time(30, 30, 1) == "1s"


# ---------------------------------------------------------------------------
# fcpxml_frame_duration
# ---------------------------------------------------------------------------


def test_fcpxml_frame_duration_ntsc():
    assert fcpxml_frame_duration(30000, 1001) == "1001/30000s"


def test_fcpxml_frame_duration_integer():
    assert fcpxml_frame_duration(30, 1) == "1/30s"


# ---------------------------------------------------------------------------
# frames_to_timecode
# ---------------------------------------------------------------------------


def test_frames_to_timecode_integer_rate():
    assert frames_to_timecode(90, 30, 1) == "00:00:03:00"


def test_frames_to_timecode_ntsc_rate_uses_nominal_int_fps():
    # nominal fps = round(30000/1001) == 30
    assert frames_to_timecode(30, 30000, 1001) == "00:00:01:00"


# ---------------------------------------------------------------------------
# build_edl
# ---------------------------------------------------------------------------


def test_build_edl_header():
    edl = build_edl(CLIP_RANGES, NTSC_FMT)
    lines = edl.splitlines()
    assert lines[0] == "TITLE: source"
    assert lines[1] == "FCM: NON-DROP FRAME"


def test_build_edl_custom_title():
    edl = build_edl(CLIP_RANGES, NTSC_FMT, title="My Project")
    assert edl.splitlines()[0] == "TITLE: My Project"


def test_build_edl_events_present_and_channel():
    edl = build_edl(CLIP_RANGES, NTSC_FMT)
    assert any(line.startswith("001") for line in edl.splitlines())
    assert any(line.startswith("002") for line in edl.splitlines())
    for line in edl.splitlines():
        if line.startswith("001") or line.startswith("002"):
            assert "AA/V" in line
            assert " C " in line or line.rstrip().endswith("C")


def test_build_edl_from_clip_name_and_comment():
    edl = build_edl(CLIP_RANGES, NTSC_FMT)
    assert "* FROM CLIP NAME: source" in edl
    assert "* COMMENT: hello world" in edl
    assert "* COMMENT: second clip" in edl


def test_build_edl_comment_strips_newlines_and_control_chars():
    ranges = [
        {"id": 1, "start": 0.0, "end": 1.0, "duration": 1.0, "text": "line one\nline two\ttabbed"},
    ]
    edl = build_edl(ranges, NTSC_FMT)
    comment_lines = [l for l in edl.splitlines() if l.startswith("* COMMENT:")]
    assert len(comment_lines) == 1
    assert "\n" not in comment_lines[0]
    assert "\t" not in comment_lines[0]
    assert comment_lines[0] == "* COMMENT: line one line two tabbed"


def test_build_edl_rec_timecodes_accumulate():
    edl = build_edl(CLIP_RANGES, NTSC_FMT)
    event_lines = [
        line for line in edl.splitlines() if line.startswith("001") or line.startswith("002")
    ]
    assert len(event_lines) == 2
    event_1_fields = event_lines[0].split()
    event_2_fields = event_lines[1].split()
    # columns: NNN AX AA/V C SRC_IN SRC_OUT REC_IN REC_OUT
    event_1_rec_out = event_1_fields[-1]
    event_2_rec_in = event_2_fields[-2]
    assert event_1_rec_out == event_2_rec_in


def test_build_edl_skips_zero_or_negative_length_clips():
    ranges = [
        {"id": 1, "start": 1.0, "end": 1.0, "duration": 0.0, "text": "zero length"},
        {"id": 2, "start": 2.0, "end": 1.0, "duration": -1.0, "text": "negative length"},
        {"id": 3, "start": 0.0, "end": 1.0, "duration": 1.0, "text": "kept"},
    ]
    edl = build_edl(ranges, NTSC_FMT)
    assert "* COMMENT: kept" in edl
    assert "* COMMENT: zero length" not in edl
    assert "* COMMENT: negative length" not in edl
    # only one event should have been emitted, numbered 001
    assert "001" in edl
    assert "002" not in edl


# ---------------------------------------------------------------------------
# build_fcpxml
# ---------------------------------------------------------------------------


def test_build_fcpxml_parses_and_starts_with_xml_declaration():
    fcpxml = build_fcpxml(CLIP_RANGES, NTSC_FMT)
    assert fcpxml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(fcpxml)
    assert root.tag == "fcpxml"
    assert root.attrib["version"] == "1.9"


def test_build_fcpxml_single_asset_and_n_clips_under_one_spine():
    fcpxml = build_fcpxml(CLIP_RANGES, NTSC_FMT)
    root = ET.fromstring(fcpxml)
    assets = root.findall("./resources/asset")
    assert len(assets) == 1

    spines = root.findall(".//spine")
    assert len(spines) == 1
    clips = spines[0].findall("asset-clip")
    assert len(clips) == len(CLIP_RANGES)


def test_build_fcpxml_clip_names_match_range_texts():
    fcpxml = build_fcpxml(CLIP_RANGES, NTSC_FMT)
    root = ET.fromstring(fcpxml)
    clips = root.findall(".//spine/asset-clip")
    names = [clip.attrib["name"] for clip in clips]
    assert names == [clip["text"] for clip in CLIP_RANGES]


def test_build_fcpxml_second_clip_offset_equals_first_clip_duration():
    fcpxml = build_fcpxml(CLIP_RANGES, NTSC_FMT)
    root = ET.fromstring(fcpxml)
    clips = root.findall(".//spine/asset-clip")
    first_duration = clips[0].attrib["duration"]
    second_offset = clips[1].attrib["offset"]
    assert first_duration == second_offset


def test_build_fcpxml_frame_duration_matches_fps():
    fcpxml = build_fcpxml(CLIP_RANGES, NTSC_FMT)
    root = ET.fromstring(fcpxml)
    fmt_el = root.find("./resources/format")
    assert fmt_el.attrib["frameDuration"] == fcpxml_frame_duration(
        NTSC_FMT.fps_num, NTSC_FMT.fps_den
    )


def test_build_fcpxml_skips_zero_or_negative_length_clips():
    ranges = [
        {"id": 1, "start": 1.0, "end": 1.0, "duration": 0.0, "text": "zero length"},
        {"id": 2, "start": 0.0, "end": 1.0, "duration": 1.0, "text": "kept"},
    ]
    fcpxml = build_fcpxml(ranges, NTSC_FMT)
    root = ET.fromstring(fcpxml)
    clips = root.findall(".//spine/asset-clip")
    assert len(clips) == 1
    assert clips[0].attrib["name"] == "kept"


def test_build_fcpxml_markers_present_when_enabled():
    fcpxml = build_fcpxml(CLIP_RANGES, NTSC_FMT, add_markers=True)
    root = ET.fromstring(fcpxml)
    markers = root.findall(".//spine/asset-clip/marker")
    assert len(markers) == len(CLIP_RANGES)


def test_build_fcpxml_no_markers_when_disabled():
    fcpxml = build_fcpxml(CLIP_RANGES, NTSC_FMT, add_markers=False)
    root = ET.fromstring(fcpxml)
    markers = root.findall(".//spine/asset-clip/marker")
    assert len(markers) == 0


def test_build_fcpxml_src_is_file_url():
    fcpxml = build_fcpxml(CLIP_RANGES, NTSC_FMT)
    root = ET.fromstring(fcpxml)
    asset = root.find("./resources/asset")
    assert asset.attrib["src"].startswith("file:///")


# ---------------------------------------------------------------------------
# probe_video_format
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_probe_video_format_parses_canned_ffprobe_json(monkeypatch, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake")

    canned_json = json.dumps(
        {
            "streams": [
                {"r_frame_rate": "30000/1001", "width": 1920, "height": 1080},
            ],
            "format": {"duration": "12.5"},
        }
    )

    def fake_run(cmd, capture_output, text, check):
        assert cmd[0] == "ffprobe"
        assert str(src) in cmd
        return _FakeCompletedProcess(returncode=0, stdout=canned_json, stderr="")

    monkeypatch.setattr("video_cli_toolkit.exporters.subprocess.run", fake_run)

    fmt = probe_video_format(src)
    assert fmt.fps_num == 30000
    assert fmt.fps_den == 1001
    assert fmt.width == 1920
    assert fmt.height == 1080
    assert fmt.duration == 12.5
    assert fmt.name == "clip"
    assert fmt.src_path == str(src.resolve())


def test_probe_video_format_integer_frame_rate_defaults_den_to_one(monkeypatch, tmp_path):
    src = tmp_path / "clip2.mp4"
    src.write_bytes(b"fake")

    canned_json = json.dumps(
        {
            "streams": [{"r_frame_rate": "25", "width": 1280, "height": 720}],
            "format": {"duration": "5.0"},
        }
    )

    def fake_run(cmd, capture_output, text, check):
        return _FakeCompletedProcess(returncode=0, stdout=canned_json, stderr="")

    monkeypatch.setattr("video_cli_toolkit.exporters.subprocess.run", fake_run)

    fmt = probe_video_format(src)
    assert fmt.fps_num == 25
    assert fmt.fps_den == 1


def test_probe_video_format_raises_toolkit_error_on_nonzero_exit(monkeypatch, tmp_path):
    src = tmp_path / "clip3.mp4"
    src.write_bytes(b"fake")

    def fake_run(cmd, capture_output, text, check):
        return _FakeCompletedProcess(returncode=1, stdout="", stderr="no such file")

    monkeypatch.setattr("video_cli_toolkit.exporters.subprocess.run", fake_run)

    with pytest.raises(ToolkitError):
        probe_video_format(src)


def test_probe_video_format_raises_toolkit_error_on_bad_json(monkeypatch, tmp_path):
    src = tmp_path / "clip4.mp4"
    src.write_bytes(b"fake")

    def fake_run(cmd, capture_output, text, check):
        return _FakeCompletedProcess(returncode=0, stdout="not json", stderr="")

    monkeypatch.setattr("video_cli_toolkit.exporters.subprocess.run", fake_run)

    with pytest.raises(ToolkitError):
        probe_video_format(src)


def test_probe_video_format_raises_toolkit_error_on_unparseable_rate(monkeypatch, tmp_path):
    src = tmp_path / "clip5.mp4"
    src.write_bytes(b"fake")

    canned_json = json.dumps(
        {
            "streams": [{"r_frame_rate": "not-a-rate", "width": 1920, "height": 1080}],
            "format": {"duration": "5.0"},
        }
    )

    def fake_run(cmd, capture_output, text, check):
        return _FakeCompletedProcess(returncode=0, stdout=canned_json, stderr="")

    monkeypatch.setattr("video_cli_toolkit.exporters.subprocess.run", fake_run)

    with pytest.raises(ToolkitError):
        probe_video_format(src)
