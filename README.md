# Video CLI Toolkit

Local-first video editing toolkit for Apple Silicon Macs. It wraps `ffmpeg`, `whisper-cpp`, and `auto-editor` behind a single CLI, with `rewrite-edit` as the first-class agent workflow and `word-editor` as the main manual fallback for talking-head cleanup.

## Agent Workflow

If you want Codex or Claude Code to take a video, work from a cleaned transcript, make the cuts, and deliver the export, start here:

```bash
toolkit rewrite-edit /path/to/input.mp4 --transcript-file final_draft.txt
```

Also supported:

```bash
toolkit rewrite-edit /path/to/input.mp4 --transcript "clean final draft transcript"
cat final_draft.txt | toolkit rewrite-edit /path/to/input.mp4 --stdin
```

Preview the edit without rendering:

```bash
toolkit plan-edit /path/to/input.mp4 --transcript-file final_draft.txt --preset tight-social-clip
```

`plan-edit` writes `edit_plan.json` and `decision_report.html` in the run folder. The JSON includes proposed word keep/cut decisions, detected silences, boundary scores, raw ranges, and padded clip ranges, so an agent can explain or adjust the edit before spending time on ffmpeg.

That flow will:

- ensure word-level transcription exists
- match the final-draft transcript against the spoken source with the shared Python rewrite matcher
- convert the kept words into exportable ranges
- render a final `transcript_edit.mp4`
- write machine-friendly metadata into `run.json`

Named presets are available for common edit styles:

```bash
toolkit presets
toolkit rewrite-edit /path/to/input.mp4 --transcript-file final_draft.txt --preset sermon-excerpt
```

Presets can still be overridden with explicit flags like `--padding`, `--max-silence`, `--merge-gap`, `--silence-threshold-db`, `--min-silence-duration`, and `--weak-boundary-score`.

For agent handoffs, use a structured JSON request:

```bash
toolkit request-schema
toolkit edit-request request.json
```

Example request:

```json
{
  "source_path": "/path/to/input.mp4",
  "workflow": "rewrite-edit",
  "target_transcript_path": "final_draft.txt",
  "preset": "gentle-talking-head-cleanup",
  "output_style": "plan",
  "notes": "Inspect before rendering."
}
```

Use `rewrite-edit` when:

- Codex or Claude Code is acting as the editor
- you already have a final-draft transcript
- you want a CLI or API-shaped workflow instead of a browser review loop

Use `word-editor` when:

- you want to inspect cuts manually
- you want to click-drag transcript words in the browser
- you want one-click cleanup before export

## Best Workflow for CapCut-like Editing

If you want the closest thing to "open transcript, click words, remove the bad parts, export", start here:

```bash
toolkit word-editor /path/to/input.mp4
```

That flow will:

- extract audio
- transcribe at word level
- open a browser editor over the source video
- let you cut words, sentences, fillers, and long pauses
- export a cleaned `transcript_edit.mp4`

The word editor is the preferred workflow when you want:

- fast talking-head cleanup
- transcript-driven cutting instead of timeline trimming
- a lightweight local alternative to CapCut-style transcript editing
- a manual fallback after an agent-driven `rewrite-edit` pass

## What the Word Editor Does

Inside `word-editor` you can:

- click-drag words to select and cut them
- cut a whole sentence/card in one click
- split cards into smaller chunks
- reorder cards
- search the transcript
- bulk cut obvious filler words
- bulk cut long pauses conservatively
- rewrite the transcript and apply approximate matching

Edits are undoable before export, and every run writes artifacts to a fresh output directory.

## Install

```bash
cd /Users/michaelpierre/Documents/coding-projects/video-cli-toolkit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
toolkit setup
make install-global
```

`toolkit setup` will:

- check for Homebrew
- install `ffmpeg` and `whisper-cpp` if missing
- install Python packages into `.venv`
- warm the `auto-editor` binary inside the local venv
- print model download instructions if no Whisper model file exists yet

## Quick Start

Health check:

```bash
toolkit doctor
```

Open the main editor workflow:

```bash
toolkit word-editor /path/to/input.mp4
```

Run the agent-first transcript rewrite workflow:

```bash
toolkit rewrite-edit /path/to/input.mp4 --transcript-file final_draft.txt
```

Use a larger Whisper model for better word alignment:

```bash
toolkit word-editor /path/to/input.mp4 --model small.en
```

If you are using Claude Code in this repo, see [CLAUDE.md](/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/CLAUDE.md) for project-specific guidance. If you are using Codex, see [AGENTS.md](/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/AGENTS.md).

## Agent Skills

This repo also includes local skills written to work cleanly with both Codex and Claude Code:

- [skills/guided-video-editor/SKILL.md](/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/skills/guided-video-editor/SKILL.md): front-door skill for raw video in, transcript conversation, approved final draft, and rendered cut out
- [skills/video-cli-toolkit/SKILL.md](/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/skills/video-cli-toolkit/SKILL.md): general repo skill for setup, health checks, transcription, and core toolkit commands
- [skills/rewrite-edit-video/SKILL.md](/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/skills/rewrite-edit-video/SKILL.md): preferred skill when Claude should turn a final-draft transcript into the finished cut
- [skills/word-editor-fallback/SKILL.md](/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/skills/word-editor-fallback/SKILL.md): manual browser-editor fallback when a human wants to inspect or adjust cuts
- [skills/transcript-video-edit/SKILL.md](/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/skills/transcript-video-edit/SKILL.md): query, fuzzy-query, or manual ranges based transcript editing
- [skills/transcript-review-cut/SKILL.md](/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/skills/transcript-review-cut/SKILL.md): older review-sheet flow for human-in-the-loop transcript review
- [skills/social-clip-cutter/SKILL.md](/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/skills/social-clip-cutter/SKILL.md): short social-ready clip extraction

For agentic editing, the intended order is:

1. `guided-video-editor` for raw-video-to-final-cut collaboration
2. `rewrite-edit` for first-class CLI/API use when the final transcript already exists
3. `word-editor` for manual fallback
4. older review-sheet or query-first flows only when they are specifically the right tool

## Whisper Model

`whisper-cpp` requires GGML model files. Place your chosen model in:

```text
/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/models/
```

Example default filename:

```text
ggml-base.bin
```

## Other Workflows

These still work, but they are now secondary to `rewrite-edit` for agent-driven automation and secondary to `word-editor` for manual transcript editing.

Silence-cut edit:

```bash
toolkit edit /path/to/input.mp4
toolkit edit /path/to/input.mp4 --margin 0.2s,1.0s
```

Transcript query editing:

```bash
toolkit transcript-edit /path/to/input.mp4 --query "chapter 16"
toolkit transcript-edit /path/to/input.mp4 --query "chapter" --query "david" --padding 0.4,0.8
toolkit transcript-edit /path/to/input.mp4 --fuzzy-query "chapter sixteen david" --fuzzy-threshold 0.55
toolkit transcript-edit /path/to/input.mp4 --ranges ranges.json
```

Older review-sheet flow:

```bash
toolkit review-sheet /path/to/input.mp4
toolkit ranges-from-review /path/to/input.mp4 --instructions "keep 3-6, 9, 12-14"
toolkit ranges-from-review /path/to/input.mp4 --instructions "cut 0-2, 5"
```

Captions and transcription:

```bash
toolkit transcribe /path/to/input.mp4
toolkit captions /path/to/input.mp4
toolkit pipeline /path/to/input.mp4
```

Matching notes for `transcript-edit`:

- exact and fuzzy matching normalize punctuation and simple number words like `sixteen -> 16`
- transcript matching can span adjacent transcript segments instead of only matching one segment at a time
- fuzzy matching combines sequence similarity, token overlap, and normalized contains checks

Manual ranges JSON format:

```json
[
  { "start": 12.4, "end": 18.9, "label": "hook" },
  { "start": 44.1, "end": 58.0, "label": "cta" }
]
```

## Outputs

Each run writes to:

```text
outputs/<source-stem>/<run-id>/
```

Typical files:

- `audio.wav`
- `transcript.txt`
- `segments.json`
- `word_segments.json`
- `ranges.json`
- `captions.srt`
- `edited.mp4`
- `transcript_edit.mp4`
- `selected_segments.json`
- `clip_ranges.json`
- `run.json`

Some older flows also write `review_sheet.txt`.

## Global Command

Install a wrapper into `/Users/michaelpierre/.local/bin/toolkit`:

```bash
cd /Users/michaelpierre/Documents/coding-projects/video-cli-toolkit
make install-global
```

After that, `toolkit` will work from anywhere in your shell.

## Project Layout

- `config.toml`: project defaults
- `src/video_cli_toolkit/`: CLI implementation
- `tests/`: unit tests
- `models/`: Whisper model files
- `outputs/`: generated workflow artifacts

## Apple Silicon Note

`toolkit doctor` checks for VideoToolbox encoder support in your local `ffmpeg` build. The default edit output is configured for `h264_videotoolbox`.
