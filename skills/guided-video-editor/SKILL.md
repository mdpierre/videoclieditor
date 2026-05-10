---
name: "guided-video-editor"
description: "Use this as the front-door skill for raw-video-to-final-cut editing: transcribe the video, talk with the user about the transcript and editorial goals, draft an approved final transcript, then render the finished cut with rewrite-edit."
---

# Guided Video Editor

Use this skill as the default front door when the user wants:

- raw video in
- transcript out
- a back-and-forth conversation about what the video should become
- a final cut rendered from an approved transcript

This is the right skill for requests like:

- "Here is the video. Help me shape it and make the cut."
- "Transcribe this, ask me what matters, then edit it."
- "I want a conversation about the transcript before you render the video."
- "Figure out the hook, the structure, and the must-keep lines with me."

Project root:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit`

## Core workflow

This skill is not "run one command immediately." It is a guided editorial loop.

1. Confirm the source video path.
2. Transcribe the source video.
3. Show the user a concise excerpt or summary of the transcript.
4. Ask focused editorial questions.
5. Draft a final transcript that stays close enough to spoken audio to match well.
6. Revise with the user until the transcript is approved.
7. Render with `toolkit rewrite-edit`.
8. Return the finished video path and run directory.

## Commands

Transcribe first:

```bash
toolkit transcribe INPUT
```

Render from the approved final transcript:

```bash
toolkit rewrite-edit INPUT --transcript-file final_draft.txt
```

Alternative render inputs:

```bash
toolkit rewrite-edit INPUT --transcript "inline final draft"
cat final_draft.txt | toolkit rewrite-edit INPUT --stdin
```

Manual fallback:

```bash
toolkit word-editor INPUT
```

## Conversation pattern

After transcription, do not dump the whole transcript by default. Give the user a compact summary or excerpt, then ask 2-4 focused questions.

Good questions include:

- "What do you want the video to lead with?"
- "What do you want the video to be about?"
- "What framework should I use?"
- "What lines absolutely have to stay in the video?"
- "What should definitely be cut?"
- "Do you want this to feel punchy, story-driven, educational, reflective, or promotional?"

If the user is unsure, offer a few directions based on the transcript:

- hook-first
- story-first
- lesson-first
- framework-first
- CTA-first

## Editing rules

- Act like an editor, not a batch processor.
- Keep rewrites conservative enough for `rewrite-edit` to match the spoken source.
- Preserve must-keep lines exactly when the user says they are non-negotiable.
- Summarize and reflect back the apparent angle before rewriting.
- Let the user react to the draft before rendering.

Avoid:

- cutting immediately from a vague prompt
- asking a giant questionnaire
- rewriting so aggressively that the spoken audio can no longer be matched
- forcing `word-editor` when the user wants an agent-driven flow

## Output expectations

Successful runs should produce:

- `transcript.txt`
- `rewrite_target.txt`
- `ranges.json`
- `clip_ranges.json`
- `transcript_edit.mp4`
- `run.json`

Useful response fields after `rewrite-edit`:

- `run_dir`
- `clip_count`
- `artifacts.transcript_edit`
- `match_summary`

## Escalation path

- Use `toolkit word-editor INPUT` when the user wants manual inspection or hand-tuned cleanup after the automated pass.
- If the final transcript drifts too far from the spoken audio, tell the user and tighten the rewrite before rendering.
- If the user already has an approved final-draft transcript, the narrower `rewrite-edit-video` skill is also appropriate.
