from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import load_config
from .workflow import (
    ToolkitError,
    create_run_context,
    doctor,
    edit_from_review,
    edit_media,
    ensure_input_exists,
    generate_captions,
    generate_review_sheet,
    project_root_from_here,
    ranges_from_review,
    rewrite_edit,
    serve_review_ui,
    serve_word_editor,
    setup_environment,
    transcript_edit,
    transcribe,
    write_run_metadata,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toolkit", description="Local Apple Silicon video workflow toolkit.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Install dependencies and create the local Python environment.")
    setup_parser.add_argument("--apply", action="store_true", help="Run the install commands instead of printing them only.")

    subparsers.add_parser("doctor", help="Check local dependencies and project readiness.")

    for command_name, help_text in (
        ("ranges-from-review", "Convert keep/cut review instructions into a ranges.json file."),
        ("review-sheet", "Generate a timestamped transcript review sheet for keep/cut decisions."),
        ("transcribe", "Extract audio and transcribe a media file."),
        ("edit", "Create a silence-cut talking-head edit."),
        ("captions", "Generate SRT captions from a media file."),
        ("pipeline", "Run the full talking-head workflow."),
        ("transcript-edit", "Create a video made from transcript-matched ranges."),
        ("rewrite-edit", "Create a video from a target final-draft transcript."),
    ):
        cmd_parser = subparsers.add_parser(command_name, help=help_text)
        cmd_parser.add_argument("input", type=Path, help="Path to the source media file.")
        cmd_parser.add_argument("--model", help="Override the configured Whisper model name.")
        if command_name == "ranges-from-review":
            cmd_parser.add_argument(
                "--instructions",
                required=True,
                help='Review instructions like `keep 3-6, 9, 12-14` or `cut 0-2`.',
            )
        if command_name == "edit":
            cmd_parser.add_argument("--margin", help="Override the configured auto-editor margin.")
        if command_name == "transcript-edit":
            selection_group = cmd_parser.add_mutually_exclusive_group(required=True)
            selection_group.add_argument(
                "--query",
                action="append",
                help="Exact substring transcript text to match. Repeat for multiple phrases.",
            )
            selection_group.add_argument(
                "--fuzzy-query",
                action="append",
                help="Fuzzy transcript text to match. Repeat for multiple phrases.",
            )
            selection_group.add_argument(
                "--ranges",
                type=Path,
                help="Path to a JSON file containing manual clip ranges.",
            )
            cmd_parser.add_argument(
                "--padding",
                help="Padding before and after each match in seconds, for example `0.4,0.8`.",
            )
            cmd_parser.add_argument(
                "--fuzzy-threshold",
                type=float,
                default=0.6,
                help="Minimum similarity score for `--fuzzy-query` matches. Default: 0.6",
            )
        if command_name == "rewrite-edit":
            transcript_group = cmd_parser.add_mutually_exclusive_group(required=True)
            transcript_group.add_argument(
                "--transcript-file",
                type=Path,
                help="Path to a final-draft transcript text file.",
            )
            transcript_group.add_argument(
                "--transcript",
                help="Inline final-draft transcript text.",
            )
            transcript_group.add_argument(
                "--stdin",
                action="store_true",
                help="Read the final-draft transcript from standard input.",
            )
            cmd_parser.add_argument(
                "--padding",
                help="Padding before and after each clip in seconds, for example `0.4,0.8`.",
            )
            cmd_parser.add_argument(
                "--max-silence",
                type=float,
                default=0.2,
                help="Split rewrite ranges when silence between kept words exceeds this many seconds. Use 0 to disable. Default: 0.2",
            )
            cmd_parser.add_argument(
                "--json",
                action="store_true",
                help="Print machine-friendly JSON output. This is already the default output format.",
            )

    we_parser = subparsers.add_parser("word-editor", help="Open browser word-level editor — select and delete words to cut them.")
    we_parser.add_argument("input", type=Path, help="Path to the source media file.")
    we_parser.add_argument("--model", help="Override the configured Whisper model name.")

    rui_parser = subparsers.add_parser("review-ui", help="Open a browser UI to keep/cut transcript segments, then write a review sheet.")
    rui_parser.add_argument("input", type=Path, help="Path to the source media file.")
    rui_parser.add_argument("--model", help="Override the configured Whisper model name.")

    efr_parser = subparsers.add_parser(
        "edit-from-review",
        help="Build a video from an edited review sheet (deleted lines = cut segments).",
    )
    efr_parser.add_argument("input", type=Path, help="Path to the source media file.")
    efr_parser.add_argument(
        "--review",
        type=Path,
        default=None,
        help="Path to an edited review_sheet.txt. Defaults to the one in the run directory.",
    )
    efr_parser.add_argument(
        "--padding",
        default=None,
        help="Padding before and after each clip in seconds, for example `0.2,0.5`.",
    )
    efr_parser.add_argument("--model", help="Override the configured Whisper model name.")

    return parser


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def handle_setup(project_root: Path) -> int:
    config = load_config(project_root)
    result = setup_environment(config, apply_changes=args().apply)
    print_json(result)
    return 0


def handle_doctor(project_root: Path) -> int:
    config = load_config(project_root)
    print_json(doctor(config))
    return 0


def handle_transcribe(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    segments, metadata = transcribe(config, run_context, model_name=args().model)
    metadata["segment_count"] = len(segments)
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_review_sheet(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    _review_sheet, metadata = generate_review_sheet(config, run_context, model_name=args().model)
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_ranges_from_review(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    metadata = ranges_from_review(
        config,
        run_context,
        instructions=args().instructions,
        model_name=args().model,
    )
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_edit(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    metadata = edit_media(config, run_context, margin=getattr(args(), "margin", None))
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_captions(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    metadata = generate_captions(config, run_context, model_name=args().model)
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_pipeline(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)

    segments, transcribe_meta = transcribe(config, run_context, model_name=args().model)
    edit_meta = edit_media(config, run_context)
    captions_meta = generate_captions(config, run_context, model_name=args().model)
    metadata = {
        "step": "pipeline",
        "segment_count": len(segments),
        "artifacts": {
            "audio": str(run_context.audio_path),
            "transcript": str(run_context.transcript_path),
            "segments": str(run_context.segments_path),
            "captions": str(run_context.captions_path),
            "edited": str(run_context.edited_path),
        },
        "steps": [transcribe_meta, edit_meta, captions_meta],
    }
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_word_editor(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    metadata = serve_word_editor(config, run_context, model_name=args().model)
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_review_ui(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    metadata = serve_review_ui(config, run_context, model_name=args().model)
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_edit_from_review(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    metadata = edit_from_review(
        config,
        run_context,
        review_path=args().review,
        model_name=args().model,
        padding=args().padding,
    )
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_transcript_edit(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    metadata = transcript_edit(
        config,
        run_context,
        queries=args().query,
        fuzzy_queries=args().fuzzy_query,
        ranges_path=args().ranges,
        padding=args().padding,
        fuzzy_threshold=args().fuzzy_threshold,
        model_name=args().model,
    )
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


def handle_rewrite_edit(project_root: Path) -> int:
    config = load_config(project_root)
    input_path = args().input.expanduser().resolve()
    ensure_input_exists(input_path)
    run_context = create_run_context(config, input_path)
    transcript_text = sys.stdin.read() if args().stdin else getattr(args(), "transcript", None)
    metadata = rewrite_edit(
        config,
        run_context,
        transcript_text=transcript_text,
        transcript_path=getattr(args(), "transcript_file", None),
        model_name=args().model,
        padding=args().padding,
        max_silence_gap=args().max_silence,
    )
    write_run_metadata(run_context, metadata)
    print_json({"run_dir": str(run_context.run_dir), **metadata})
    return 0


_ARGS: argparse.Namespace | None = None


def args() -> argparse.Namespace:
    if _ARGS is None:
        raise RuntimeError("CLI arguments have not been initialized.")
    return _ARGS


def main(argv: list[str] | None = None) -> int:
    global _ARGS
    parser = build_parser()
    _ARGS = parser.parse_args(argv)
    project_root = project_root_from_here()

    try:
        if _ARGS.command == "setup":
            return handle_setup(project_root)
        if _ARGS.command == "doctor":
            return handle_doctor(project_root)
        if _ARGS.command == "transcribe":
            return handle_transcribe(project_root)
        if _ARGS.command == "ranges-from-review":
            return handle_ranges_from_review(project_root)
        if _ARGS.command == "review-sheet":
            return handle_review_sheet(project_root)
        if _ARGS.command == "edit":
            return handle_edit(project_root)
        if _ARGS.command == "captions":
            return handle_captions(project_root)
        if _ARGS.command == "pipeline":
            return handle_pipeline(project_root)
        if _ARGS.command == "transcript-edit":
            return handle_transcript_edit(project_root)
        if _ARGS.command == "rewrite-edit":
            return handle_rewrite_edit(project_root)
        if _ARGS.command == "word-editor":
            return handle_word_editor(project_root)
        if _ARGS.command == "review-ui":
            return handle_review_ui(project_root)
        if _ARGS.command == "edit-from-review":
            return handle_edit_from_review(project_root)
    except ToolkitError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
