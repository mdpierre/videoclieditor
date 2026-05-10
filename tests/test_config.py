from pathlib import Path

from video_cli_toolkit.cli import build_parser
from video_cli_toolkit.config import load_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_load_config_defaults() -> None:
    config = load_config(PROJECT_ROOT)
    assert config.whisper.model_name == "medium.en"
    assert config.auto_editor.margin == "0.3s,1.5s"
    assert config.captions.format == "srt"
    assert config.ffmpeg.video_codec == "h264_videotoolbox"


def test_model_override_resolution() -> None:
    config = load_config(PROJECT_ROOT)
    assert config.model_path() == PROJECT_ROOT / "models" / "ggml-medium.en.bin"
    assert config.model_path("small.en") == PROJECT_ROOT / "models" / "ggml-small.en.bin"


def test_cli_override_parsing() -> None:
    parser = build_parser()
    args = parser.parse_args(["edit", "clip.mp4", "--margin", "0.2s,1.0s"])
    assert args.command == "edit"
    assert args.input == Path("clip.mp4")
    assert args.margin == "0.2s,1.0s"


def test_review_sheet_cli_parsing() -> None:
    parser = build_parser()
    args = parser.parse_args(["review-sheet", "clip.mp4"])
    assert args.command == "review-sheet"
    assert args.input == Path("clip.mp4")


def test_ranges_from_review_cli_parsing() -> None:
    parser = build_parser()
    args = parser.parse_args(["ranges-from-review", "clip.mp4", "--instructions", "keep 3-6, 9"])
    assert args.command == "ranges-from-review"
    assert args.input == Path("clip.mp4")
    assert args.instructions == "keep 3-6, 9"


def test_transcript_edit_cli_parsing() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["transcript-edit", "clip.mp4", "--query", "chapter", "--query", "David", "--padding", "0.4,0.8"]
    )
    assert args.command == "transcript-edit"
    assert args.input == Path("clip.mp4")
    assert args.query == ["chapter", "David"]
    assert args.padding == "0.4,0.8"
    assert args.fuzzy_query is None
    assert args.ranges is None


def test_transcript_edit_fuzzy_cli_parsing() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["transcript-edit", "clip.mp4", "--fuzzy-query", "chapter 16 david", "--fuzzy-threshold", "0.7"]
    )
    assert args.command == "transcript-edit"
    assert args.fuzzy_query == ["chapter 16 david"]
    assert args.fuzzy_threshold == 0.7


def test_transcript_edit_ranges_cli_parsing() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["transcript-edit", "clip.mp4", "--ranges", "ranges.json"]
    )
    assert args.command == "transcript-edit"
    assert args.ranges == Path("ranges.json")


def test_rewrite_edit_cli_parsing_with_transcript_file() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["rewrite-edit", "clip.mp4", "--transcript-file", "final.txt", "--padding", "0.4,0.8", "--max-silence", "0.25", "--json"]
    )
    assert args.command == "rewrite-edit"
    assert args.input == Path("clip.mp4")
    assert args.transcript_file == Path("final.txt")
    assert args.transcript is None
    assert args.stdin is False
    assert args.padding == "0.4,0.8"
    assert args.max_silence == 0.25
    assert args.json is True


def test_rewrite_edit_cli_parsing_with_inline_transcript() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["rewrite-edit", "clip.mp4", "--transcript", "hello world"]
    )
    assert args.command == "rewrite-edit"
    assert args.transcript == "hello world"
    assert args.transcript_file is None
    assert args.stdin is False


def test_rewrite_edit_cli_parsing_with_stdin() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["rewrite-edit", "clip.mp4", "--stdin"]
    )
    assert args.command == "rewrite-edit"
    assert args.stdin is True
    assert args.transcript is None
    assert args.transcript_file is None
