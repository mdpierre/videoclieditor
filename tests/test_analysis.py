from pathlib import Path

import pytest

from video_cli_toolkit.analysis import (
    detect_scene_boundaries,
    detect_speech_regions_silero,
    snap_ranges_to_scene_boundaries,
    speech_regions_to_silences,
)


def test_speech_regions_to_silences_inverts_single_region() -> None:
    assert speech_regions_to_silences([{"start": 1, "end": 2}], 4.0) == [
        {"start": 0.0, "end": 1.0, "duration": 1.0},
        {"start": 2.0, "end": 4.0, "duration": 2.0},
    ]


def test_speech_regions_to_silences_empty_speech_is_whole_clip_silent() -> None:
    assert speech_regions_to_silences([], 4.0) == [
        {"start": 0.0, "end": 4.0, "duration": 4.0}
    ]


def test_speech_regions_to_silences_full_coverage_speech_has_no_silences() -> None:
    assert speech_regions_to_silences([{"start": 0.0, "end": 4.0}], 4.0) == []


def test_speech_regions_to_silences_merges_overlapping_regions() -> None:
    result = speech_regions_to_silences(
        [{"start": 1.0, "end": 2.5}, {"start": 2.0, "end": 3.0}], 5.0
    )
    assert result == [
        {"start": 0.0, "end": 1.0, "duration": 1.0},
        {"start": 3.0, "end": 5.0, "duration": 2.0},
    ]


def test_snap_ranges_to_scene_boundaries_snaps_within_tolerance() -> None:
    ranges = [{"start": 0.0, "end": 2.0}, {"start": 2.6, "end": 4.0}]

    snapped, snap_log = snap_ranges_to_scene_boundaries(
        ranges, [2.10], tolerance=0.22
    )

    assert snapped[0]["end"] == pytest.approx(2.10)
    assert snapped[1]["start"] == pytest.approx(2.6)
    assert snap_log == [
        {
            "edge": "end",
            "clip_index": 0,
            "from": 2.0,
            "to": pytest.approx(2.10),
            "boundary": pytest.approx(2.10),
        }
    ]
    # Input ranges are not mutated.
    assert ranges == [{"start": 0.0, "end": 2.0}, {"start": 2.6, "end": 4.0}]


def test_snap_ranges_to_scene_boundaries_outside_tolerance_is_unchanged() -> None:
    ranges = [{"start": 0.0, "end": 2.0}, {"start": 3.0, "end": 4.0}]

    snapped, snap_log = snap_ranges_to_scene_boundaries(
        ranges, [2.40], tolerance=0.22
    )

    assert snapped == ranges
    assert snapped is not ranges
    assert snap_log == []


def test_snap_ranges_to_scene_boundaries_rejects_cross_clip_boundary() -> None:
    # Gap is only [2.0, 2.05]; a boundary at 2.15 is within simple tolerance of
    # the left clip's end but would land inside the next clip's kept audio, so
    # it must be rejected rather than clipping speech.
    ranges = [{"start": 0.0, "end": 2.0}, {"start": 2.05, "end": 4.0}]

    snapped, snap_log = snap_ranges_to_scene_boundaries(
        ranges, [2.15], tolerance=0.22
    )

    assert snapped == ranges
    assert snap_log == []


def test_snap_ranges_to_scene_boundaries_empty_boundaries_is_identity() -> None:
    ranges = [{"start": 0.0, "end": 2.0}, {"start": 2.6, "end": 4.0}]

    snapped, snap_log = snap_ranges_to_scene_boundaries(ranges, [], tolerance=0.22)

    assert snapped == ranges
    assert snapped is not ranges
    assert snap_log == []


def test_snap_ranges_to_scene_boundaries_logs_entry_shape() -> None:
    ranges = [{"start": 0.0, "end": 2.0}, {"start": 2.6, "end": 4.0}]

    _snapped, snap_log = snap_ranges_to_scene_boundaries(
        ranges, [2.10], tolerance=0.22
    )

    assert len(snap_log) == 1
    entry = snap_log[0]
    assert set(entry.keys()) == {"edge", "clip_index", "from", "to", "boundary"}
    assert entry["edge"] in {"start", "end"}
    assert isinstance(entry["clip_index"], int)


def test_detect_scene_boundaries_returns_none_when_scenedetect_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "scenedetect":
            raise ImportError("no scenedetect")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert detect_scene_boundaries(Path("does-not-matter.mp4")) is None


def test_detect_scene_boundaries_with_real_library_never_raises() -> None:
    pytest.importorskip("scenedetect")

    result = detect_scene_boundaries(Path("this-file-does-not-exist.mp4"))

    assert result is None or isinstance(result, list)


def test_detect_speech_regions_silero_returns_none_without_onnxruntime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "onnxruntime":
            raise ImportError("no onnxruntime")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = detect_speech_regions_silero(
        Path("does-not-matter.wav"), model_path=Path("does-not-matter.onnx")
    )

    assert result is None


def test_detect_speech_regions_silero_returns_none_when_model_missing() -> None:
    pytest.importorskip("onnxruntime")

    result = detect_speech_regions_silero(
        Path("does-not-matter.wav"),
        model_path=Path("definitely-not-a-real-model.onnx"),
    )

    assert result is None
