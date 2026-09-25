import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
from unittest.mock import patch

from video_cli_toolkit import cli as cli_module
from video_cli_toolkit import exporters
from video_cli_toolkit.workflow import (
    _run_check_command,
    apply_word_editor_rewrite,
    build_auto_editor_command,
    build_clip_ranges,
    build_ffmpeg_clip_command,
    build_ffmpeg_concat_command,
    build_ffmpeg_extract_command,
    build_segment_windows,
    build_whisper_command,
    build_review_sheet,
    create_run_context,
    build_ranges_from_segment_ids,
    doctor,
    edit_from_review,
    export_run,
    export_word_editor_ranges,
    generate_word_editor_html,
    group_words_for_editor,
    list_edit_presets,
    load_manual_ranges,
    normalize_match_text,
    plan_rewrite_edit,
    parse_kept_segment_ids,
    parse_padding,
    parse_silencedetect_output,
    parse_review_instructions,
    resolve_edit_options,
    resolve_existing_run_context,
    resolve_rewrite_target_text,
    rewrite_edit,
    select_segments_by_fuzzy_queries,
    select_segments_by_queries,
    ToolkitError,
)
from video_cli_toolkit.config import load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ffmpeg_command_construction() -> None:
    command = build_ffmpeg_extract_command(Path("input.mp4"), Path("audio.wav"), "pcm_s16le")
    assert command == [
        "ffmpeg",
        "-y",
        "-i",
        "input.mp4",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "audio.wav",
    ]


def test_parse_silencedetect_output_extracts_silence_ranges() -> None:
    stderr = """
[silencedetect @ 0x0] silence_start: 1.230
[silencedetect @ 0x0] silence_end: 1.700 | silence_duration: 0.470
[silencedetect @ 0x0] silence_start: 3.500
"""

    silences = parse_silencedetect_output(stderr, audio_duration=4.0)

    assert silences == [
        {"start": 1.23, "end": 1.7, "duration": 0.47},
        {"start": 3.5, "end": 4.0, "duration": 0.5},
    ]


def test_whisper_command_construction() -> None:
    command = build_whisper_command("/opt/homebrew/bin/whisper-cli", Path("audio.wav"), Path("whisper"), Path("models/ggml-base.bin"))
    assert command == [
        "/opt/homebrew/bin/whisper-cli",
        "-m",
        "models/ggml-base.bin",
        "-f",
        "audio.wav",
        "--output-txt",
        "--output-json",
        "--output-file",
        "whisper",
    ]


def test_auto_editor_command_construction() -> None:
    command = build_auto_editor_command(
        "/tmp/.venv/bin/auto-editor",
        Path("input.mp4"),
        Path("edited.mp4"),
        "0.3s,1.5s",
        "h264_videotoolbox",
        "50",
    )
    assert command == [
        "/tmp/.venv/bin/auto-editor",
        "input.mp4",
        "--margin",
        "0.3s,1.5s",
        "--video-codec",
        "h264_videotoolbox",
        "--scale",
        "1",
        "--no-open",
        "--output",
        "edited.mp4",
    ]


def test_run_context_paths() -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/My Video.mp4"), run_id="20260324-120000")
    assert run_context.run_dir == PROJECT_ROOT / "outputs" / "My-Video" / "20260324-120000"
    assert run_context.transcript_path.name == "transcript.txt"
    assert run_context.segments_path.name == "segments.json"
    assert run_context.captions_path.name == "captions.srt"
    assert run_context.edited_path.name == "edited.mp4"
    assert run_context.transcript_edit_path.name == "transcript_edit.mp4"
    assert run_context.review_sheet_path.name == "review_sheet.txt"
    assert run_context.fcpxml_path == run_context.run_dir / "export.fcpxml"
    assert run_context.edl_path == run_context.run_dir / "export.edl"


def test_transcript_selection_and_range_merging() -> None:
    segments = [
        {"id": 0, "start": 1.0, "end": 2.0, "text": "Hello world"},
        {"id": 1, "start": 2.4, "end": 3.2, "text": "world again"},
        {"id": 2, "start": 7.0, "end": 8.0, "text": "different phrase"},
    ]
    selected = select_segments_by_queries(segments, ["world"])
    assert [segment["id"] for segment in selected] == [0, 1]

    clip_ranges = build_clip_ranges(selected, input_duration=10.0, padding_before=0.4, padding_after=0.8)
    assert len(clip_ranges) == 1
    assert clip_ranges[0]["start"] == 0.6
    assert clip_ranges[0]["end"] == 4.0
    assert clip_ranges[0]["segment_ids"] == [0, 1]


def test_exact_query_matches_across_adjacent_segments() -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "shared presence of having"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "someone witness you is where"},
        {"id": 2, "start": 2.0, "end": 3.0, "text": "the healing happens"},
    ]
    selected = select_segments_by_queries(segments, ["shared presence someone witness you"])
    assert [segment["id"] for segment in selected] == [0, 1]


def test_fuzzy_transcript_selection() -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "moving on to chapter sixteen"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "story about david"},
        {"id": 2, "start": 2.5, "end": 3.5, "text": "completely unrelated"},
    ]
    selected = select_segments_by_fuzzy_queries(segments, ["chapter 16 david"], 0.55)
    assert [segment["id"] for segment in selected[:2]] == [0, 1]
    assert selected[0]["match_score"] >= 0.55


def test_fuzzy_transcript_selection_prefers_tightest_window() -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "lead in context"},
        {"id": 1, "start": 2.0, "end": 4.0, "text": "human beings not human doings"},
        {"id": 2, "start": 4.0, "end": 6.0, "text": "extra trailing context"},
    ]
    selected = select_segments_by_fuzzy_queries(segments, ["human beings not human doings"], 0.55)
    assert [segment["id"] for segment in selected] == [1]
    assert selected[0]["match_score"] == 1.0


def test_normalization_handles_punctuation_and_number_words() -> None:
    assert normalize_match_text("Chapter Sixteen, David!") == "chapter 16 david"


def test_group_words_for_editor_splits_on_sentence_punctuation_and_long_gaps() -> None:
    words = [
        {"id": 0, "word": "Hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "world.", "start": 0.2, "end": 0.5},
        {"id": 2, "word": "Next", "start": 0.55, "end": 0.8},
        {"id": 3, "word": "thought", "start": 0.8, "end": 1.1},
        {"id": 4, "word": "Later", "start": 2.2, "end": 2.5},
    ]
    cards = group_words_for_editor(words)
    assert [[word["id"] for word in card] for card in cards] == [[0, 1], [2, 3], [4]]


def test_group_words_for_editor_splits_long_cards_on_soft_breaks() -> None:
    words = []
    current = 0.0
    for idx in range(24):
        words.append({"id": idx, "word": f"word{idx},", "start": current, "end": current + 0.1})
        current += 0.12
    words.append({"id": 24, "word": "tail", "start": current + 0.5, "end": current + 0.7})

    cards = group_words_for_editor(words, max_words_per_card=24)
    assert len(cards) == 2
    assert [word["id"] for word in cards[0]] == list(range(24))
    assert [word["id"] for word in cards[1]] == [24]


def test_resolve_rewrite_target_text_accepts_file_input(tmp_path: Path) -> None:
    transcript_file = tmp_path / "final.txt"
    transcript_file.write_text(" hello world \n")

    text, source = resolve_rewrite_target_text(transcript_path=transcript_file)

    assert text == "hello world"
    assert source == {"type": "file", "path": str(transcript_file.resolve())}


def test_resolve_rewrite_target_text_rejects_missing_inputs() -> None:
    try:
        resolve_rewrite_target_text()
    except ToolkitError as exc:
        assert str(exc) == "A target transcript is required."
    else:
        raise AssertionError("Expected resolve_rewrite_target_text() to fail without input")


def test_generate_word_editor_html_uses_approximate_rewrite_matching() -> None:
    words = [
        {"id": 0, "word": "prohuman", "start": 0.0, "end": 0.3},
        {"id": 1, "word": "value", "start": 0.3, "end": 0.6},
    ]
    html = generate_word_editor_html(words, [], "clip.mp4")
    assert "Approximate matching" in html
    assert "small fixes" in html
    assert "fetch('/rewrite-apply'" in html
    assert "data.kept_word_ids" in html
    assert "Applied server rewrite match." in html


def test_generate_word_editor_html_includes_search_and_cleanup_controls() -> None:
    words = [
        {"id": 0, "word": "um", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "hello", "start": 0.2, "end": 0.5},
    ]
    html = generate_word_editor_html(words, [], "clip.mp4")
    assert 'id="search-box"' in html
    assert 'id="search-prev-btn"' in html
    assert 'id="search-next-btn"' in html
    assert 'id="cleanup-fillers-btn"' in html
    assert 'id="cleanup-pauses-btn"' in html
    assert "function cutFillerWords" in html
    assert "function cutLongPauses" in html
    assert "const FILLER_WORDS = new Set" in html
    assert "const LONG_PAUSE_SECONDS = 1.0" in html


def test_generate_word_editor_html_includes_export_response_contract() -> None:
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "world", "start": 0.2, "end": 0.5},
    ]
    html = generate_word_editor_html(words, [], "clip.mp4")
    assert "fetch('/submit'" in html
    assert "JSON.stringify({ranges: buildRanges()})" in html
    assert "data.transcript_edit" in html
    assert "data.clip_count" in html
    assert "data.run_dir" in html


def test_apply_word_editor_rewrite_uses_shared_python_matcher() -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-word-rewrite-apply")
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "remove", "start": 0.2, "end": 0.4},
        {"id": 2, "word": "chapter", "start": 0.5, "end": 0.8},
        {"id": 3, "word": "16", "start": 0.8, "end": 1.1},
    ]

    result = apply_word_editor_rewrite(run_context, words, "hello chapter sixteen")

    assert result["kept_word_ids"] == [0, 2, 3]
    assert result["cut_word_ids"] == [1]
    assert result["ranges"] == [{"start": 0.0, "end": 0.2}, {"start": 0.5, "end": 1.1}]
    assert result["match_summary"] == {
        "source_word_count": 4,
        "target_token_count": 3,
        "kept_word_count": 3,
        "cut_word_count": 1,
        "matched_target_count": 3,
        "unmatched_target_token_count": 0,
    }
    assert result["max_silence_gap"] == 0.2
    assert result["audio_silence_detection"]["silence_count"] == 0
    assert result["vad_backend"] == "ffmpeg"
    assert result["audio_silence_detection"]["vad_backend"] == "ffmpeg"
    assert Path(result["target_transcript"]) == run_context.rewrite_target_path
    assert run_context.rewrite_target_path.read_text() == "hello chapter sixteen\n"


def test_apply_word_editor_rewrite_prefers_later_duplicate_take() -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-word-rewrite-duplicate-take")
    words = [
        {"id": 0, "word": "I", "start": 0.0, "end": 0.1},
        {"id": 1, "word": "really", "start": 0.1, "end": 0.2},
        {"id": 2, "word": "mean", "start": 0.2, "end": 0.3},
        {"id": 3, "word": "it,", "start": 0.3, "end": 0.4},
        {"id": 4, "word": "I", "start": 0.4, "end": 0.5},
        {"id": 5, "word": "really", "start": 0.5, "end": 0.6},
        {"id": 6, "word": "mean", "start": 0.6, "end": 0.7},
        {"id": 7, "word": "it.", "start": 0.7, "end": 0.8},
    ]

    result = apply_word_editor_rewrite(run_context, words, "I really mean it")

    assert result["kept_word_ids"] == [4, 5, 6, 7]
    assert result["cut_word_ids"] == [0, 1, 2, 3]
    assert result["ranges"] == [{"start": 0.4, "end": 0.8}]


def test_run_check_command_times_out() -> None:
    ok, timed_out = _run_check_command(
        [str(PROJECT_ROOT / ".venv" / "bin" / "python"), "-c", "import time; time.sleep(0.2)"],
        timeout=0.05,
    )
    assert ok is False
    assert timed_out is True


def test_segment_window_generation() -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "hello"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "world"},
    ]
    windows = build_segment_windows(segments, max_window_size=2)
    assert windows[0]["segment_ids"] == [0]
    assert windows[1]["segment_ids"] == [0, 1]
    assert windows[1]["normalized_text"] == "hello world"


def test_review_sheet_formatting() -> None:
    review_sheet = build_review_sheet(
        [
            {"id": 3, "start": 12.68, "end": 16.64, "text": "people around you"},
            {"id": 4, "start": 16.64, "end": 20.8, "text": "feel like there's no one"},
        ]
    )
    assert review_sheet == (
        "[03] 00:12.680-00:16.640 people around you\n"
        "[04] 00:16.640-00:20.800 feel like there's no one\n"
    )


def test_parse_review_instructions() -> None:
    assert parse_review_instructions("keep 3-6, 9, 12-14") == ("keep", [3, 4, 5, 6, 9, 12, 13, 14])
    assert parse_review_instructions("cut 0-2 5") == ("cut", [0, 1, 2, 5])


def test_build_ranges_from_segment_ids_keep_and_cut() -> None:
    segments = [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "zero"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "one"},
        {"id": 2, "start": 4.0, "end": 5.0, "text": "two"},
    ]
    keep_ranges = build_ranges_from_segment_ids(segments, [0, 1], mode="keep")
    assert keep_ranges == [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "zero one",
            "segment_ids": [0, 1],
            "id": 1,
            "duration": 2.0,
        }
    ]

    cut_ranges = build_ranges_from_segment_ids(segments, [1], mode="cut")
    assert cut_ranges == [
        {
            "start": 0.0,
            "end": 1.0,
            "text": "zero",
            "segment_ids": [0],
            "id": 1,
            "duration": 1.0,
        },
        {
            "start": 4.0,
            "end": 5.0,
            "text": "two",
            "segment_ids": [2],
            "id": 2,
            "duration": 1.0,
        },
    ]


def test_manual_ranges_loading(tmp_path: Path) -> None:
    ranges_file = tmp_path / "ranges.json"
    ranges_file.write_text('[{"start": 1.0, "end": 3.5, "label": "hook"}]')
    clip_ranges = load_manual_ranges(ranges_file, input_duration=10.0)
    assert clip_ranges == [
        {
            "id": 1,
            "start": 1.0,
            "end": 3.5,
            "duration": 2.5,
            "text": "hook",
            "segment_ids": [],
        }
    ]


def test_padding_and_clip_commands() -> None:
    assert parse_padding("0.5,1.2") == (0.5, 1.2)

    clip_command = build_ffmpeg_clip_command(Path("input.mp4"), Path("clip.mp4"), 1.25, 2.5)
    assert clip_command == [
        "ffmpeg",
        "-y",
        "-ss",
        "1.250",
        "-t",
        "2.500",
        "-i",
        "input.mp4",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "clip.mp4",
    ]

    concat_command = build_ffmpeg_concat_command(Path("concat.txt"), Path("merged.mp4"))
    assert concat_command == [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        "concat.txt",
        "-c",
        "copy",
        "merged.mp4",
    ]


def test_edit_presets_can_be_overridden() -> None:
    presets = list_edit_presets()
    assert "tight-social-clip" in presets

    options = resolve_edit_options(preset="tight-social-clip", padding="0.4,0.8", max_silence=0.5)

    assert options["preset"] == "tight-social-clip"
    assert options["padding"] == "0.4,0.8"
    assert options["max_silence"] == 0.5
    assert options["merge_gap"] == presets["tight-social-clip"]["merge_gap"]


def test_parse_kept_segment_ids_basic() -> None:
    review_sheet = (
        "[00] 00:00.000-00:03.080 The people who are the strongest\n"
        "[01] 00:03.080-00:07.800 loneliest. And if you're someone\n"
        "[02] 00:07.800-00:12.680 very independent, right?\n"
    )
    assert parse_kept_segment_ids(review_sheet) == [0, 1, 2]


def test_parse_kept_segment_ids_deleted_lines() -> None:
    # Segment [01] has been deleted by the user
    review_sheet = (
        "[00] 00:00.000-00:03.080 The people who are the strongest\n"
        "[02] 00:07.800-00:12.680 very independent, right?\n"
    )
    assert parse_kept_segment_ids(review_sheet) == [0, 2]


def test_parse_kept_segment_ids_blank_lines_ignored() -> None:
    review_sheet = (
        "\n"
        "[00] 00:00.000-00:03.080 The people\n"
        "\n"
        "# a comment line\n"
        "[03] 00:12.680-00:16.640 people around you\n"
    )
    assert parse_kept_segment_ids(review_sheet) == [0, 3]


def test_parse_kept_segment_ids_empty_returns_empty() -> None:
    assert parse_kept_segment_ids("") == []
    assert parse_kept_segment_ids("no segment markers here\n") == []


def test_edit_from_review_uses_kept_ids(tmp_path: Path) -> None:
    import json as _json

    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-efr")

    segments = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "zero"},
        {"id": 1, "start": 2.0, "end": 4.0, "text": "one"},
        {"id": 2, "start": 8.0, "end": 10.0, "text": "two"},
    ]

    # Pre-seed segments.json so edit_from_review skips transcription
    run_context.run_dir.mkdir(parents=True, exist_ok=True)
    run_context.segments_path.write_text(_json.dumps(segments))

    # Write a review sheet with segment [1] deleted
    review_sheet_text = (
        "[00] 00:00.000-00:02.000 zero\n"
        "[02] 00:08.000-00:10.000 two\n"
    )
    review_path = tmp_path / "edited_review.txt"
    review_path.write_text(review_sheet_text)

    captured_ranges_path: list[Path] = []

    def fake_transcript_edit(cfg, ctx, ranges_path=None, **kwargs):
        captured_ranges_path.append(ranges_path)
        return {"step": "transcript-edit", "clip_count": 2, "artifacts": {"transcript_edit": str(ctx.transcript_edit_path)}}

    with patch("video_cli_toolkit.workflow.transcript_edit", side_effect=fake_transcript_edit):
        metadata = edit_from_review(config, run_context, review_path=review_path)

    assert metadata["kept_segment_ids"] == [0, 2]
    assert metadata["clip_count"] == 2
    assert captured_ranges_path[0] == run_context.generated_ranges_path


def test_export_word_editor_ranges_renders_via_transcript_edit() -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-word-export")
    ranges = [{"start": 0.5, "end": 1.25, "label": "hook"}]

    def fake_transcript_edit(cfg, ctx, ranges_path=None, **kwargs):
        assert cfg is config
        assert ctx is run_context
        assert ranges_path == run_context.generated_ranges_path
        return {
            "step": "transcript-edit",
            "clip_count": 1,
            "artifacts": {
                "clip_ranges": str(ctx.clip_ranges_path),
                "transcript_edit": str(ctx.transcript_edit_path),
            },
        }

    with patch("video_cli_toolkit.workflow.transcript_edit", side_effect=fake_transcript_edit):
        result = export_word_editor_ranges(config, run_context, ranges)

    assert run_context.generated_ranges_path.exists()
    assert set(result.keys()) == {
        "ranges",
        "clip_ranges",
        "transcript_edit",
        "run_dir",
        "clip_count",
        "transcript_edit_metadata",
    }
    assert result["ranges"] == str(run_context.generated_ranges_path)
    assert result["clip_ranges"] == str(run_context.clip_ranges_path)
    assert result["transcript_edit"] == str(run_context.transcript_edit_path)
    assert result["run_dir"] == str(run_context.run_dir)
    assert result["clip_count"] == 1


def test_plan_rewrite_edit_writes_inspectable_artifacts() -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-plan-edit")
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "remove", "start": 0.2, "end": 0.4},
        {"id": 2, "word": "chapter", "start": 0.7, "end": 1.0},
        {"id": 3, "word": "16.", "start": 1.0, "end": 1.3},
    ]
    segments = [{"id": 0, "start": 0.0, "end": 1.3, "text": "hello remove chapter 16"}]

    def fake_transcribe_words(cfg, ctx, model_name=None):
        assert cfg is config
        assert ctx is run_context
        return words, segments, {}

    with (
        patch("video_cli_toolkit.workflow.transcribe_words", side_effect=fake_transcribe_words),
        patch("video_cli_toolkit.workflow.detect_audio_silences", return_value=[{"start": 0.4, "end": 0.7, "duration": 0.3}]),
        patch("video_cli_toolkit.workflow.probe_duration", return_value=2.0),
    ):
        plan = plan_rewrite_edit(
            config,
            run_context,
            transcript_text="hello chapter sixteen",
            padding="0,0",
            max_silence_gap=0.2,
            silence_threshold_db=-32.0,
            min_silence_duration=0.12,
            merge_gap=0.0,
            weak_boundary_score=0,
            preset="tight-social-clip",
            notes="draft check",
        )

    assert plan["step"] == "plan-edit"
    assert plan["workflow"] == "rewrite-edit"
    assert plan["clip_count"] == 2
    assert plan["match_summary"]["kept_word_count"] == 3
    assert [word["decision"] for word in plan["word_matches"]] == ["keep", "cut", "keep", "keep"]
    assert plan["silences"] == [{"start": 0.4, "end": 0.7, "duration": 0.3}]
    assert plan["options"]["silence_threshold_db"] == -32.0
    assert plan["vad_backend"] == "ffmpeg"
    assert plan["speech_regions"] == []
    assert plan["scene_boundaries"] == []
    assert plan["scene_snaps"] == []
    assert run_context.edit_plan_path.exists()
    assert run_context.decision_report_path.exists()
    report_html = run_context.decision_report_path.read_text()
    assert "Proposed Transcript Decisions" in report_html
    assert "Detected Silences" in report_html
    assert "Matched Target" in report_html
    assert "clip-001.mp4" in report_html
    assert "Scene Snaps" in report_html
    assert "No scene snaps." in report_html
    assert "VAD Backend" in report_html
    assert "<strong>ffmpeg</strong>" in report_html


def test_plan_rewrite_edit_falls_back_to_ffmpeg_when_silero_unavailable() -> None:
    """Silero explicitly unavailable (returns None) must behave exactly like the
    ffmpeg-only baseline: vad_backend stays "ffmpeg" and ranges/clip_ranges are
    unchanged for the same fixed input."""
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-plan-edit-silero-fallback")
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "remove", "start": 0.2, "end": 0.4},
        {"id": 2, "word": "chapter", "start": 0.7, "end": 1.0},
        {"id": 3, "word": "16.", "start": 1.0, "end": 1.3},
    ]
    segments = [{"id": 0, "start": 0.0, "end": 1.3, "text": "hello remove chapter 16"}]

    def fake_transcribe_words(cfg, ctx, model_name=None):
        assert cfg is config
        assert ctx is run_context
        return words, segments, {}

    with (
        patch("video_cli_toolkit.workflow.transcribe_words", side_effect=fake_transcribe_words),
        patch("video_cli_toolkit.workflow.detect_audio_silences", return_value=[{"start": 0.4, "end": 0.7, "duration": 0.3}]),
        patch("video_cli_toolkit.workflow.detect_speech_regions_silero", return_value=None),
        patch("video_cli_toolkit.workflow.detect_scene_boundaries", return_value=[]),
        patch("video_cli_toolkit.workflow.probe_duration", return_value=2.0),
    ):
        plan = plan_rewrite_edit(
            config,
            run_context,
            transcript_text="hello chapter sixteen",
            padding="0,0",
            max_silence_gap=0.2,
            silence_threshold_db=-32.0,
            min_silence_duration=0.12,
            merge_gap=0.0,
            weak_boundary_score=0,
            preset="tight-social-clip",
            notes="draft check",
        )

    # Same baseline as test_plan_rewrite_edit_writes_inspectable_artifacts.
    assert plan["clip_count"] == 2
    assert plan["match_summary"]["kept_word_count"] == 3
    assert plan["silences"] == [{"start": 0.4, "end": 0.7, "duration": 0.3}]
    assert plan["vad_backend"] == "ffmpeg"
    assert plan["speech_regions"] == []
    assert plan["ranges"] == [{"start": 0.0, "end": 0.2}, {"start": 0.7, "end": 1.3}]
    assert plan["clip_ranges"] == [
        {"start": 0.0, "end": 0.2, "text": "", "segment_ids": [], "id": 1, "duration": 0.2},
        {"start": 0.7, "end": 1.3, "text": "", "segment_ids": [], "id": 2, "duration": 0.6},
    ]


def test_plan_rewrite_edit_uses_silero_speech_regions_when_available() -> None:
    """When Silero returns speech regions, the plan reports vad_backend "silero",
    surfaces the raw speech regions, and the inverted non-speech spans show up as
    plan["silences"] (used downstream by the rewrite matcher)."""
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-plan-edit-silero-available")
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "remove", "start": 0.2, "end": 0.4},
        {"id": 2, "word": "chapter", "start": 0.7, "end": 1.0},
        {"id": 3, "word": "16.", "start": 1.0, "end": 1.3},
    ]
    segments = [{"id": 0, "start": 0.0, "end": 1.3, "text": "hello remove chapter 16"}]

    def fake_transcribe_words(cfg, ctx, model_name=None):
        return words, segments, {}

    speech_regions = [{"start": 0.0, "end": 0.4}, {"start": 0.7, "end": 2.0}]

    with (
        patch("video_cli_toolkit.workflow.transcribe_words", side_effect=fake_transcribe_words),
        patch("video_cli_toolkit.workflow.detect_speech_regions_silero", return_value=speech_regions),
        patch("video_cli_toolkit.workflow.detect_scene_boundaries", return_value=[]),
        patch("video_cli_toolkit.workflow.probe_duration", return_value=2.0),
    ):
        plan = plan_rewrite_edit(
            config,
            run_context,
            transcript_text="hello chapter sixteen",
            padding="0,0",
            max_silence_gap=0.2,
            silence_threshold_db=-32.0,
            min_silence_duration=0.12,
            merge_gap=0.0,
            weak_boundary_score=0,
            preset="tight-social-clip",
            notes="draft check",
        )

    assert plan["vad_backend"] == "silero"
    assert plan["speech_regions"] == speech_regions
    # speech_regions_to_silences inverts [0.0,0.4] + [0.7,2.0] within [0, 2.0]
    # into a single non-speech gap [0.4, 0.7].
    assert len(plan["silences"]) == 1
    assert plan["silences"][0]["start"] == pytest.approx(0.4)
    assert plan["silences"][0]["end"] == pytest.approx(0.7)
    assert plan["silences"][0]["duration"] == pytest.approx(0.3)


def test_plan_rewrite_edit_snaps_ranges_to_scene_boundaries() -> None:
    """Scene boundaries near a clip edge produced by the fixture should show up in
    plan["scene_boundaries"] and trigger at least one plan["scene_snaps"] entry."""
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-plan-edit-scene-snap")
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "remove", "start": 0.2, "end": 0.4},
        {"id": 2, "word": "chapter", "start": 0.7, "end": 1.0},
        {"id": 3, "word": "16.", "start": 1.0, "end": 1.3},
    ]
    segments = [{"id": 0, "start": 0.0, "end": 1.3, "text": "hello remove chapter 16"}]

    def fake_transcribe_words(cfg, ctx, model_name=None):
        return words, segments, {}

    # Without scene snapping the fixture yields ranges [0.0, 0.2] and [0.7, 1.3]
    # (see test_plan_rewrite_edit_falls_back_to_ffmpeg_when_silero_unavailable).
    # 0.68 sits within scene_snap_tolerance (0.22) of the second range's start
    # (0.7), so it should pull that edge backward to 0.68. 5.0 is unrelated and
    # only exercises that plan["scene_boundaries"] passes both values through.
    scene_boundaries = [0.68, 5.0]

    with (
        patch("video_cli_toolkit.workflow.transcribe_words", side_effect=fake_transcribe_words),
        patch("video_cli_toolkit.workflow.detect_speech_regions_silero", return_value=None),
        patch("video_cli_toolkit.workflow.detect_audio_silences", return_value=[{"start": 0.4, "end": 0.7, "duration": 0.3}]),
        patch("video_cli_toolkit.workflow.detect_scene_boundaries", return_value=scene_boundaries),
        patch("video_cli_toolkit.workflow.probe_duration", return_value=2.0),
    ):
        plan = plan_rewrite_edit(
            config,
            run_context,
            transcript_text="hello chapter sixteen",
            padding="0,0",
            max_silence_gap=0.2,
            silence_threshold_db=-32.0,
            min_silence_duration=0.12,
            merge_gap=0.0,
            weak_boundary_score=0,
            preset="tight-social-clip",
            notes="draft check",
        )

    assert plan["scene_boundaries"] == [0.68, 5.0]
    assert len(plan["scene_snaps"]) >= 1
    snap = plan["scene_snaps"][0]
    assert snap == {"edge": "start", "clip_index": 1, "from": 0.7, "to": 0.68, "boundary": 0.68}
    assert plan["ranges"][1]["start"] == pytest.approx(0.68)

    report_html = run_context.decision_report_path.read_text()
    assert "Scene Snaps" in report_html
    assert "<td>start</td>" in report_html
    assert "0.700 → 0.680" in report_html


def test_rewrite_edit_builds_ranges_renders_video_and_writes_run_metadata(tmp_path: Path) -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-rewrite-edit")
    transcript_file = tmp_path / "final-draft.txt"
    transcript_file.write_text("hello chapter sixteen")
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "remove", "start": 0.2, "end": 0.4},
        {"id": 2, "word": "chapter", "start": 0.5, "end": 0.8},
        {"id": 3, "word": "16", "start": 0.8, "end": 1.1},
    ]
    segments = [
        {"id": 0, "start": 0.0, "end": 0.4, "text": "hello remove"},
        {"id": 1, "start": 0.5, "end": 1.1, "text": "chapter 16"},
    ]

    def fake_transcribe_words(cfg, ctx, model_name=None):
        assert cfg is config
        assert ctx is run_context
        return words, segments, {"step": "transcribe", "artifacts": {"word_segments": str(ctx.word_segments_path)}}

    def fake_transcript_edit(cfg, ctx, ranges_path=None, padding=None, **kwargs):
        assert cfg is config
        assert ctx is run_context
        assert ranges_path == run_context.generated_ranges_path
        assert padding == "0.2,0.4"
        clip_ranges = json.loads(ranges_path.read_text())
        assert clip_ranges == [{"start": 0.0, "end": 0.2}, {"start": 0.5, "end": 1.1}]
        ctx.clip_ranges_path.write_text(json.dumps([{"id": 1, "start": 0.0, "end": 0.2, "duration": 0.2, "text": "clip-1", "segment_ids": []}]))
        return {
            "step": "transcript-edit",
            "clip_count": 1,
            "artifacts": {
                "clip_ranges": str(ctx.clip_ranges_path),
                "transcript_edit": str(ctx.transcript_edit_path),
            },
        }

    with (
        patch("video_cli_toolkit.workflow.transcribe_words", side_effect=fake_transcribe_words),
        patch("video_cli_toolkit.workflow.probe_duration", return_value=2.0),
        patch("video_cli_toolkit.workflow.transcript_edit", side_effect=fake_transcript_edit),
    ):
        result = rewrite_edit(
            config,
            run_context,
            transcript_path=transcript_file,
            padding="0.2,0.4",
            max_silence_gap=0.2,
        )

    assert run_context.rewrite_target_path.exists()
    assert run_context.rewrite_target_path.read_text() == "hello chapter sixteen\n"
    assert run_context.generated_ranges_path.exists()
    assert result["step"] == "rewrite-edit"
    assert result["clip_count"] == 1
    assert result["target_transcript_source"] == {"type": "file", "path": str(transcript_file.resolve())}
    assert result["match_summary"] == {
        "source_word_count": 4,
        "target_token_count": 3,
        "kept_word_count": 3,
        "cut_word_count": 1,
        "matched_target_count": 3,
        "unmatched_target_token_count": 0,
    }
    assert result["max_silence_gap"] == 0.2
    assert result["audio_silence_detection"]["silence_count"] == 0
    assert result["artifacts"]["target_transcript"] == str(run_context.rewrite_target_path)
    assert result["artifacts"]["ranges"] == str(run_context.generated_ranges_path)
    assert result["artifacts"]["clip_ranges"] == str(run_context.clip_ranges_path)
    assert result["artifacts"]["transcript_edit"] == str(run_context.transcript_edit_path)
    assert run_context.run_metadata_path.exists()
    run_metadata = json.loads(run_context.run_metadata_path.read_text())
    assert run_metadata["step"] == "rewrite-edit"
    assert run_metadata["match_summary"]["kept_word_count"] == 3


def test_handle_rewrite_edit_reads_stdin_and_prints_json(capsys: object) -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-handle-rewrite")
    fake_args = SimpleNamespace(
        input=Path("/tmp/sample.mp4"),
        transcript_file=None,
        transcript=None,
        stdin=True,
        model="small.en",
        padding="0.3,0.6",
        max_silence=0.2,
        merge_gap=None,
        preset=None,
        silence_threshold_db=None,
        min_silence_duration=None,
        weak_boundary_score=None,
        notes=None,
        json=True,
    )

    with (
        patch.object(cli_module, "_ARGS", fake_args),
        patch("video_cli_toolkit.cli.load_config", return_value=config),
        patch("video_cli_toolkit.cli.ensure_input_exists"),
        patch("video_cli_toolkit.cli.create_run_context", return_value=run_context),
        patch("video_cli_toolkit.cli.sys.stdin.read", return_value="hello final draft"),
        patch(
            "video_cli_toolkit.cli.rewrite_edit",
            return_value={
                "step": "rewrite-edit",
                "clip_count": 2,
                "artifacts": {"transcript_edit": str(run_context.transcript_edit_path)},
            },
        ) as rewrite_edit_mock,
        patch("video_cli_toolkit.cli.write_run_metadata") as write_run_metadata_mock,
    ):
        exit_code = cli_module.handle_rewrite_edit(PROJECT_ROOT)

    assert exit_code == 0
    rewrite_edit_mock.assert_called_once_with(
        config,
        run_context,
        transcript_text="hello final draft",
        transcript_path=None,
        model_name="small.en",
        padding="0.3,0.6",
        max_silence_gap=0.2,
        silence_threshold_db=-35.0,
        min_silence_duration=0.2,
        merge_gap=1.0,
        weak_boundary_score=0,
        preset=None,
        notes=None,
        vad_backend_override=None,
        scene_detection_override=None,
    )
    write_run_metadata_mock.assert_called_once()
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_dir"] == str(run_context.run_dir)
    assert payload["step"] == "rewrite-edit"
    assert payload["clip_count"] == 2


def test_handle_rewrite_edit_threads_vad_and_no_scene_snap_overrides(capsys: object) -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-handle-rewrite-overrides")
    fake_args = SimpleNamespace(
        input=Path("/tmp/sample.mp4"),
        transcript_file=None,
        transcript="hello final draft",
        stdin=False,
        model=None,
        padding=None,
        max_silence=None,
        merge_gap=None,
        preset=None,
        silence_threshold_db=None,
        min_silence_duration=None,
        weak_boundary_score=None,
        notes=None,
        json=True,
        vad="ffmpeg",
        no_scene_snap=True,
    )

    with (
        patch.object(cli_module, "_ARGS", fake_args),
        patch("video_cli_toolkit.cli.load_config", return_value=config),
        patch("video_cli_toolkit.cli.ensure_input_exists"),
        patch("video_cli_toolkit.cli.create_run_context", return_value=run_context),
        patch(
            "video_cli_toolkit.cli.rewrite_edit",
            return_value={
                "step": "rewrite-edit",
                "clip_count": 1,
                "artifacts": {"transcript_edit": str(run_context.transcript_edit_path)},
            },
        ) as rewrite_edit_mock,
        patch("video_cli_toolkit.cli.write_run_metadata"),
    ):
        exit_code = cli_module.handle_rewrite_edit(PROJECT_ROOT)

    assert exit_code == 0
    _, call_kwargs = rewrite_edit_mock.call_args
    assert call_kwargs["vad_backend_override"] == "ffmpeg"
    assert call_kwargs["scene_detection_override"] is False


def test_doctor_tolerates_optional_import_timeout(tmp_path: Path) -> None:
    from video_cli_toolkit.config import AppConfig, AutoEditorConfig, CaptionConfig, FfmpegConfig, OutputConfig, WhisperConfig

    project_root = tmp_path / "project"
    project_root.mkdir()
    python_bin = project_root / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text("")
    model_dir = project_root / "models"
    model_dir.mkdir()
    (model_dir / "ggml-medium.en.bin").write_text("")

    config = AppConfig(
        project_root=project_root,
        whisper=WhisperConfig(model_name="medium.en", model_dir=Path("models"), binary_candidates=["whisper-cli"]),
        auto_editor=AutoEditorConfig(margin="0.3s,1.5s"),
        outputs=OutputConfig(root=project_root / "outputs"),
        captions=CaptionConfig(format="srt"),
        ffmpeg=FfmpegConfig(audio_codec="pcm_s16le", video_codec="h264_videotoolbox", quality="50"),
    )

    def fake_run_check(command, **kwargs):
        joined = " ".join(command)
        if "import auto_editor" in joined:
            return True, False
        if "import moviepy" in joined:
            return False, True
        if command[-1] == "--version":
            return True, False
        raise AssertionError(f"Unexpected command: {command}")

    with (
        patch("video_cli_toolkit.workflow.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"),
        patch("video_cli_toolkit.workflow.ffmpeg_supports_videotoolbox", return_value=True),
        patch("video_cli_toolkit.workflow.discover_whisper_binary", return_value="/usr/bin/whisper-cli"),
        patch("video_cli_toolkit.workflow.discover_auto_editor_binary", return_value="/tmp/auto-editor"),
        patch("video_cli_toolkit.workflow._run_check_command", side_effect=fake_run_check),
    ):
        result = doctor(config)

    assert result["ok"] is True
    assert result["python_imports"]["auto_editor"] is True
    assert result["python_imports"]["moviepy"] is False
    assert result["python_import_timeouts"]["moviepy"] is True


def _fixed_video_format(src_path: Path) -> exporters.VideoFormat:
    return exporters.VideoFormat(
        fps_num=30,
        fps_den=1,
        width=1920,
        height=1080,
        duration=10.0,
        src_path=str(src_path),
        name=src_path.stem,
    )


def test_export_run_writes_fcpxml_and_edl() -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-export-run")
    clip_ranges = [
        {"id": 1, "start": 0.0, "end": 1.0, "duration": 1.0, "text": "clip one", "segment_ids": [0]},
        {"id": 2, "start": 2.0, "end": 3.5, "duration": 1.5, "text": "clip two", "segment_ids": [1]},
    ]
    run_context.clip_ranges_path.write_text(json.dumps(clip_ranges, indent=2))

    with patch(
        "video_cli_toolkit.workflow.exporters.probe_video_format",
        side_effect=lambda src_path: _fixed_video_format(Path(src_path)),
    ) as probe_mock:
        result = export_run(config, run_context, formats=["fcpxml", "edl"])

    probe_mock.assert_called_once_with(run_context.input_path)
    assert result["step"] == "export"
    assert result["formats"] == ["fcpxml", "edl"]
    assert result["clip_count"] == 2
    assert result["artifacts"]["fcpxml"] == str(run_context.fcpxml_path)
    assert result["artifacts"]["edl"] == str(run_context.edl_path)

    assert run_context.fcpxml_path.exists()
    assert run_context.edl_path.exists()

    parsed = ET.fromstring(run_context.fcpxml_path.read_text())
    asset_clips = parsed.findall(".//spine/asset-clip")
    assert len(asset_clips) == 2

    edl_text = run_context.edl_path.read_text()
    assert edl_text.count("* COMMENT:") == 2
    assert "001  AX       AA/V  C        " in edl_text
    assert "002  AX       AA/V  C        " in edl_text


def test_export_run_errors_without_ranges() -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-export-run-missing-ranges")
    if run_context.clip_ranges_path.exists():
        run_context.clip_ranges_path.unlink()

    with pytest.raises(ToolkitError, match="No clip_ranges found"):
        export_run(config, run_context, formats=["fcpxml"])


def test_handle_plan_edit_export_flag_triggers_export_and_merges_artifacts(capsys: object) -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-plan-edit-export")
    fake_args = SimpleNamespace(
        input=Path("/tmp/sample.mp4"),
        transcript_file=None,
        transcript="hello final draft",
        stdin=False,
        model=None,
        padding=None,
        max_silence=None,
        merge_gap=None,
        preset=None,
        silence_threshold_db=None,
        min_silence_duration=None,
        weak_boundary_score=None,
        notes=None,
        vad=None,
        no_scene_snap=False,
        export=["fcpxml"],
    )

    with (
        patch.object(cli_module, "_ARGS", fake_args),
        patch("video_cli_toolkit.cli.load_config", return_value=config),
        patch("video_cli_toolkit.cli.ensure_input_exists"),
        patch("video_cli_toolkit.cli.create_run_context", return_value=run_context),
        patch(
            "video_cli_toolkit.cli.plan_rewrite_edit",
            return_value={
                "step": "plan-edit",
                "clip_count": 1,
                "artifacts": {"clip_ranges": str(run_context.clip_ranges_path)},
            },
        ) as plan_rewrite_edit_mock,
        patch("video_cli_toolkit.cli.write_run_metadata") as write_run_metadata_mock,
        patch(
            "video_cli_toolkit.cli.export_run",
            return_value={
                "step": "export",
                "formats": ["fcpxml"],
                "clip_count": 1,
                "artifacts": {"fcpxml": str(run_context.fcpxml_path)},
            },
        ) as export_run_mock,
    ):
        exit_code = cli_module.handle_plan_edit(PROJECT_ROOT)

    assert exit_code == 0
    plan_rewrite_edit_mock.assert_called_once()
    export_run_mock.assert_called_once_with(config, run_context, formats=["fcpxml"])
    write_run_metadata_mock.assert_called_once()
    payload = json.loads(capsys.readouterr().out)
    assert payload["artifacts"]["clip_ranges"] == str(run_context.clip_ranges_path)
    assert payload["artifacts"]["fcpxml"] == str(run_context.fcpxml_path)
    assert payload["export"]["formats"] == ["fcpxml"]


def test_handle_rewrite_edit_without_export_flag_does_not_call_export_run(capsys: object) -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-rewrite-edit-no-export")
    fake_args = SimpleNamespace(
        input=Path("/tmp/sample.mp4"),
        transcript_file=None,
        transcript="hello final draft",
        stdin=False,
        model=None,
        padding=None,
        max_silence=None,
        merge_gap=None,
        preset=None,
        silence_threshold_db=None,
        min_silence_duration=None,
        weak_boundary_score=None,
        notes=None,
        json=True,
        vad=None,
        no_scene_snap=False,
        export=None,
    )

    with (
        patch.object(cli_module, "_ARGS", fake_args),
        patch("video_cli_toolkit.cli.load_config", return_value=config),
        patch("video_cli_toolkit.cli.ensure_input_exists"),
        patch("video_cli_toolkit.cli.create_run_context", return_value=run_context),
        patch(
            "video_cli_toolkit.cli.rewrite_edit",
            return_value={
                "step": "rewrite-edit",
                "clip_count": 1,
                "artifacts": {"transcript_edit": str(run_context.transcript_edit_path)},
            },
        ),
        patch("video_cli_toolkit.cli.write_run_metadata"),
        patch("video_cli_toolkit.cli.export_run") as export_run_mock,
    ):
        exit_code = cli_module.handle_rewrite_edit(PROJECT_ROOT)

    assert exit_code == 0
    export_run_mock.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert "export" not in payload
    assert payload["artifacts"] == {"transcript_edit": str(run_context.transcript_edit_path)}


def test_handle_export_resolves_newest_run_and_prints_metadata(capsys: object) -> None:
    config = load_config(PROJECT_ROOT)
    run_context = create_run_context(config, Path("/tmp/sample.mp4"), run_id="test-export-cli")
    fake_args = SimpleNamespace(
        input=Path("/tmp/sample.mp4"),
        formats=["fcpxml"],
        fps=None,
        run_id=None,
        from_ranges=None,
        out=None,
    )

    with (
        patch.object(cli_module, "_ARGS", fake_args),
        patch("video_cli_toolkit.cli.load_config", return_value=config),
        patch("video_cli_toolkit.cli.ensure_input_exists"),
        patch("video_cli_toolkit.cli.resolve_existing_run_context", return_value=run_context) as resolve_mock,
        patch(
            "video_cli_toolkit.cli.export_run",
            return_value={
                "step": "export",
                "formats": ["fcpxml"],
                "clip_count": 3,
                "artifacts": {"fcpxml": str(run_context.fcpxml_path)},
            },
        ) as export_run_mock,
    ):
        exit_code = cli_module.handle_export(PROJECT_ROOT)

    assert exit_code == 0
    resolve_mock.assert_called_once_with(config, Path("/tmp/sample.mp4"), run_id=None)
    export_run_mock.assert_called_once_with(
        config,
        run_context,
        formats=["fcpxml"],
        fps_override=None,
        ranges_path=None,
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_dir"] == str(run_context.run_dir)
    assert payload["clip_count"] == 3
    assert payload["artifacts"]["fcpxml"] == str(run_context.fcpxml_path)


def test_resolve_existing_run_context_picks_newest_run_dir(tmp_path: Path) -> None:
    import os as _os
    from dataclasses import replace as _replace

    from video_cli_toolkit.config import OutputConfig

    config = _replace(load_config(PROJECT_ROOT), outputs=OutputConfig(root=tmp_path / "outputs"))
    input_path = Path("/tmp/sample.mp4")
    older = create_run_context(config, input_path, run_id="test-newest-older")
    newer = create_run_context(config, input_path, run_id="test-newest-newer")
    # Force a distinguishable mtime ordering regardless of directory creation order
    # or filesystem mtime resolution.
    now = older.run_dir.stat().st_mtime
    _os.utime(older.run_dir, (now, now - 100))
    _os.utime(newer.run_dir, (now, now))

    resolved = resolve_existing_run_context(config, input_path)
    assert resolved.run_dir == newer.run_dir


def test_resolve_existing_run_context_errors_when_no_run_exists(tmp_path: Path) -> None:
    from dataclasses import replace as _replace

    from video_cli_toolkit.config import OutputConfig

    config = _replace(load_config(PROJECT_ROOT), outputs=OutputConfig(root=tmp_path / "outputs"))
    with pytest.raises(ToolkitError, match="No existing run found"):
        resolve_existing_run_context(config, Path("/tmp/never-run-this-source.mp4"))
