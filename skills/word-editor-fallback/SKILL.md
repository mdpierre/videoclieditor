---
name: "word-editor-fallback"
description: "Use the local toolkit word-editor browser workflow when transcript-driven cuts need manual review, visual inspection, or human-in-the-loop cleanup."
---

# Word Editor Fallback

Use this skill when the user wants the browser editor, wants to manually inspect cuts, or needs a fallback after an automated rewrite-edit pass.

Project root:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit`

Primary command:

```bash
toolkit word-editor INPUT
```

Higher-accuracy model example:

```bash
toolkit word-editor INPUT --model small.en
```

## What this workflow is for

Use `word-editor` when the user wants to:

- drag-select transcript words and cut them manually
- review a rewrite-driven cut before export
- clean up filler words and long pauses interactively
- split or reorder transcript cards
- search the transcript while checking the source video

## Important architecture note

The transcript rewrite panel in the browser now calls the same Python backend matcher as `rewrite-edit`.

That means:

- browser rewrite behavior should match CLI rewrite behavior
- if rewrite results look wrong, debug the Python matcher/backend first
- the browser is a fallback client, not a separate source of truth

## Operating guidance

- Use `rewrite-edit` first when the agent should do the editing.
- Use `word-editor` when the user wants eyes-on review or manual control.
- If the user wants to manually refine an automated cut, tell them the browser editor is the right next step.
- Return the local browser URL and the run directory after launching the editor.

## Output expectations

The editor run folder usually contains:

- `transcript.txt`
- `segments.json`
- `word_segments.json`
- `rewrite_target.txt` if the transcript panel was used
- `ranges.json`
- `clip_ranges.json`
- `transcript_edit.mp4`
- `run.json`
