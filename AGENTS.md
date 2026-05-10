# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

`video-cli-toolkit` is a local-first Python CLI (`toolkit`) that orchestrates **ffmpeg**, **whisper-cpp**, and **auto-editor** for Apple Silicon video workflows. It has no Python runtime dependencies — all heavy lifting is done via subprocess calls to external tools.

The local skills under `skills/` are intended to be compatible with both Codex and Claude Code. Prefer the same workflow order across both:

1. `guided-video-editor` for raw video -> transcript conversation -> approved draft -> final cut
2. `rewrite-edit-video` when the final transcript already exists
3. `word-editor-fallback` for manual browser review

## Development Commands

```bash
# After modifying any Python source
.venv/bin/python -m pip install . --force-reinstall --no-deps

# Run tests
.venv/bin/pytest -q

# Run a single test
.venv/bin/pytest -q tests/test_commands.py::test_function_name

# Health check
.venv/bin/toolkit doctor

# Install the global `toolkit` wrapper to ~/.local/bin
make install-global
```

## Architecture

### Three-layer structure

1. **`cli.py`** — `argparse`-based entry point. Defines 9 subcommands (`setup`, `doctor`, `transcribe`, `review-sheet`, `ranges-from-review`, `edit`, `captions`, `pipeline`, `transcript-edit`), validates arguments, then delegates to `workflow.py`.

2. **`workflow.py`** (core, ~1,100 lines) — All business logic. Key concepts:
   - `RunContext` dataclass: canonical holder for all artifact paths (`audio.wav`, `transcript.txt`, `segments.json`, `captions.srt`, `edited.mp4`, etc.) under `outputs/<source-stem>/<run-id>/`.
   - External tools are called via `subprocess.run` with commands built by helper functions (`build_ffmpeg_*`, `build_whisper_command`, `build_auto_editor_command`).
   - Apple Silicon codecs are used by default (`h264_videotoolbox`) with automatic fallback to `libx264` on failure.
   - Transcript matching supports exact (`select_segments_by_queries`), fuzzy (`select_segments_by_fuzzy_queries`), and multi-segment window matching for cross-segment phrases.

3. **`config.py`** — Frozen dataclasses (`WhisperConfig`, `FfmpegConfig`, `AutoEditorConfig`, `AppConfig`, etc.) loaded from `config.toml`. Config is immutable after load.

### Run output layout

```
outputs/<source-stem>/<run-id>/
├── audio.wav, transcript.txt, segments.json   # transcription artifacts
├── review_sheet.txt, ranges.json              # human-in-the-loop review
├── captions.srt                               # subtitle output
├── edited.mp4                                 # silence-cut video
├── transcript_edit.mp4, clips/, concat.txt   # segment-matched edit
├── selected_segments.json, clip_ranges.json  # segment selection state
└── run.json                                   # run metadata
```

### Key implementation details

- **No Python deps at runtime**: the package itself has no `install_requires`. External binaries (`ffmpeg`, `whisper-cli`/`whisper-cpp`/`main`, `auto-editor`) must be on PATH or discoverable.
- **Text normalization**: `workflow.py` converts number words ("sixteen" → "16") and strips punctuation before fuzzy matching.
- **GPU fallback**: `transcribe()` tries Metal/GPU first, falls back to CPU. `edit_media()` tries VideoToolbox, falls back to libx264.
- **Project root resolution**: discovered via `VIDEO_CLI_TOOLKIT_ROOT` env var, then by walking up from cwd looking for `config.toml`, then from the executable path.
