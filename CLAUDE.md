# CLAUDE.md

This file gives Claude Code project-specific guidance for working in `video-cli-toolkit`.

The local skills under `skills/` are written to be agent-neutral so the same workflows can be used from Claude Code and Codex.

## What This Project Is

`video-cli-toolkit` is a local-first Python CLI for transcript-driven and silence-driven video editing on Apple Silicon Macs.

It wraps external tools instead of Python media libraries:

- `ffmpeg`
- `whisper-cpp`
- `auto-editor`

The package itself has no runtime Python dependencies. Most failures are subprocess, model-path, codec, or file-contract issues rather than framework/runtime issues.

## Preferred User Workflows

If a skill entry point is appropriate, prefer:

1. `guided-video-editor` for raw-video-to-final-cut collaboration
2. `rewrite-edit-video` when the final transcript already exists
3. `word-editor-fallback` for manual browser review
4. lower-level skills only when the user specifically wants them

### 1. First-class Claude workflow: `rewrite-edit`

This is the preferred automation path when the user wants:

- "take this video and my cleaned transcript"
- "rewrite the spoken draft into a final draft"
- "make the cuts and deliver the video"

Primary command:

```bash
toolkit rewrite-edit INPUT --transcript-file final_draft.txt
```

Also supported:

```bash
toolkit rewrite-edit INPUT --transcript "inline transcript"
cat final_draft.txt | toolkit rewrite-edit INPUT --stdin
```

What it does:

1. Ensures word-level transcription exists.
2. Uses the shared Python rewrite matcher.
3. Builds `ranges.json` from kept word ids.
4. Renders the final edit through the existing `transcript_edit` pipeline.
5. Writes machine-friendly metadata to `run.json`.

When the agent is acting as the editor, prefer `rewrite-edit` over `word-editor`.

### 2. Manual fallback workflow: `word-editor`

Use `toolkit word-editor INPUT` when:

- the user wants to manually inspect or tweak cuts
- the user wants to drag/select transcript words in the browser
- the user wants one-click cleanup plus human review

Important: the browser transcript rewrite panel now uses the same Python rewrite matcher/backend as `rewrite-edit`. The browser is a fallback client, not a separate source of truth.

### 3. Legacy/secondary flows

These still exist, but they are not the preferred first-class agent path:

- `toolkit transcript-edit ... --query/--fuzzy-query/--ranges`
- `toolkit review-sheet`
- `toolkit ranges-from-review`
- `toolkit edit-from-review`
- `toolkit edit`

Use them only when the user clearly wants one of those workflows.

## Architecture

### Main files

- `src/video_cli_toolkit/cli.py`
  CLI parser and command handlers.
- `src/video_cli_toolkit/workflow.py`
  Core orchestration and browser UI generation.
- `src/video_cli_toolkit/rewrite_matcher.py`
  Canonical transcript rewrite matcher used by both CLI and browser fallback.
- `src/video_cli_toolkit/config.py`
  Immutable configuration dataclasses from `config.toml`.

### Current important commands

- `setup`
- `doctor`
- `transcribe`
- `review-sheet`
- `ranges-from-review`
- `edit`
- `captions`
- `pipeline`
- `transcript-edit`
- `rewrite-edit`
- `word-editor`
- `review-ui`
- `edit-from-review`

### Rewrite architecture

There are now three layers for transcript rewrite editing:

1. `rewrite_matcher.py`
   Canonical normalization, approximate token similarity, dynamic-programming alignment, and kept-word selection.
2. `workflow.py`
   Workflow helpers that:
   - resolve target transcript input
   - generate word-level transcript data
   - build ranges from kept word ids
   - render the final video
3. `cli.py` and `word-editor`
   Two clients of the same rewrite backend:
   - CLI/API via `rewrite-edit`
   - browser fallback via `/rewrite-apply`

If rewrite behavior changes, update Python first. Do not reintroduce separate browser-only matching logic.

## Output Layout

Runs write into:

```text
outputs/<source-stem>/<run-id>/
```

Important artifacts:

- `audio.wav`
- `transcript.txt`
- `segments.json`
- `word_segments.json`
- `rewrite_target.txt`
- `ranges.json`
- `clip_ranges.json`
- `transcript_edit.mp4`
- `edited.mp4`
- `captions.srt`
- `run.json`

Older/manual workflows may also write:

- `review_sheet.txt`
- `review_ui.html`
- `selected_segments.json`

## Working Rules For Claude

### Prefer these commands when debugging

```bash
.venv/bin/toolkit doctor
.venv/bin/pytest -q
.venv/bin/pytest -q tests/test_commands.py
.venv/bin/pytest -q tests/test_rewrite_matcher.py
```

After modifying Python source:

```bash
.venv/bin/python -m pip install . --force-reinstall --no-deps
```

### When changing rewrite behavior

Always touch these in order:

1. `rewrite_matcher.py`
2. workflow helpers in `workflow.py`
3. CLI/browser clients
4. tests

Minimum tests to consider:

- `tests/test_rewrite_matcher.py`
- `tests/test_commands.py`
- full suite with `.venv/bin/pytest -q`

### When changing browser editor behavior

Remember that the browser HTML is generated as a Python string in `workflow.py`.

That means:

- UI changes usually need HTML contract tests, not just Python unit logic
- avoid duplicating logic in JS if Python already owns it
- prefer backend endpoints and JSON contracts for stateful logic

### When changing exports

`transcript_edit()` is the canonical render path for transcript-derived cuts.

Prefer reusing:

- `ranges.json`
- `clip_ranges.json`
- `transcript_edit.mp4`

Do not create a second export pipeline unless absolutely necessary.

## Common Failure Modes

- Missing Whisper model file in `models/`
- Missing `whisper-cpp` binary on `PATH`
- `ffmpeg` or `ffprobe` unavailable
- VideoToolbox encode failure, which should fall back automatically
- Browser UI drift if a frontend behavior is changed without updating tests
- Packaging confusion if tests are run against an old installed package before reinstall completes

## Good Default Behavior For Claude

When a user says:

- "Here is a video, clean it up from this transcript"
  Use `rewrite-edit`.
- "Open the browser editor"
  Use `word-editor`.
- "Help me debug why this cut is wrong"
  Inspect `rewrite_matcher.py`, `workflow.py`, and the run artifacts in the latest output folder.
- "What should we test?"
  Start with matcher tests, then workflow contract tests, then full suite.
