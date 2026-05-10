
# Video CLI Toolkit

Use this skill when the user wants to process video locally with the toolkit project at:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit`

Prefer the global `toolkit` command when available. If it fails, use:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/.venv/bin/toolkit`

## Preferred workflow order

When an agent is the operator, prefer these workflows in this order:

1. guided transcribe -> transcript conversation -> `toolkit rewrite-edit`
2. `toolkit word-editor`
3. legacy transcript-edit/review-sheet flows only when clearly requested

## Primary workflow for agent-driven edits

Use this sequence when the user gives:

- a raw video and wants Claude to help shape the edit
- a request to rewrite spoken content into a final-draft cut
- a request like "make the cuts and deliver the video"
- a request to discuss the transcript before rendering

Recommended sequence:

```bash
toolkit transcribe INPUT
toolkit rewrite-edit INPUT --transcript-file final_draft.txt
```

Alternative render inputs:

```bash
toolkit rewrite-edit INPUT --transcript-file final_draft.txt
toolkit rewrite-edit INPUT --transcript "inline final draft"
cat final_draft.txt | toolkit rewrite-edit INPUT --stdin
```

Optional:

```bash
toolkit rewrite-edit INPUT --transcript-file final_draft.txt --padding 0.3,0.6
```

The command returns machine-friendly JSON and writes:

- `rewrite_target.txt`
- `ranges.json`
- `clip_ranges.json`
- `transcript_edit.mp4`
- `run.json`

## Manual fallback

Use `word-editor` when the user wants to review or manually adjust transcript cuts in the browser:

```bash
toolkit word-editor INPUT
toolkit word-editor INPUT --model small.en
```

Inside the browser editor, the transcript rewrite panel now uses the same Python matcher as `rewrite-edit`. Treat it as a fallback client of the same backend, not a separate workflow.

## Other useful commands

- `toolkit doctor`
  Checks environment readiness.
- `toolkit transcribe INPUT`
  Writes `transcript.txt` and `segments.json`.
- `toolkit captions INPUT`
  Generates `captions.srt`.
- `toolkit edit INPUT`
  Creates a silence-cut rough cut as `edited.mp4`.
- `toolkit transcript-edit INPUT ...`
  Use exact query, fuzzy query, or manual ranges when the user specifically wants that style.
- `toolkit review-sheet INPUT`
  Generates a human review sheet.
- `toolkit edit-from-review INPUT`
  Renders from a manually edited review sheet.

## Operating guidance

- Run `toolkit doctor` before chasing environment issues.
- For agent-driven editing, do not skip straight to `rewrite-edit` when the user has not approved the transcript direction yet.
- Start by transcribing, summarize what is there, ask focused editorial questions, then render from the approved draft.
- If editing code in this repo, reinstall after Python changes:
  - `.venv/bin/python -m pip install . --force-reinstall --no-deps`
- Run tests after code changes:
  - `.venv/bin/pytest -q`
- If rewrite behavior is wrong, inspect:
  - `src/video_cli_toolkit/rewrite_matcher.py`
  - `src/video_cli_toolkit/workflow.py`
  - the latest output run directory

## Output contract

Each run writes to:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/outputs/<source-stem>/<run-id>/`

Common artifacts:

- `audio.wav`
- `transcript.txt`
- `segments.json`
- `word_segments.json`
- `rewrite_target.txt`
- `ranges.json`
- `clip_ranges.json`
- `transcript_edit.mp4`
- `run.json`
