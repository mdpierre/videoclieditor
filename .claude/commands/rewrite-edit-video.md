
# Rewrite Edit Video

Use this skill when the user wants the agent to be the editor, not just a reviewer.

This is the front-door skill for:

- raw video in
- transcript out
- a short back-and-forth editorial conversation
- a final cut delivered from an approved transcript

This is the preferred workflow for requests like:

- "Here is the video, make the cut from this transcript."
- "Rewrite the spoken draft into a tighter final draft and export the video."
- "I want to point the video at my coding agent and get back the finished cut."
- "Help me figure out what this video should lead with."
- "Interview me about the transcript and then make the edit."
- "Ask me what lines have to stay in and shape the video around that."

Project root:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit`

Primary command:

```bash
toolkit rewrite-edit INPUT --transcript-file final_draft.txt
```

Alternative input modes:

```bash
toolkit rewrite-edit INPUT --transcript "inline transcript"
cat final_draft.txt | toolkit rewrite-edit INPUT --stdin
```

Optional padding:

```bash
toolkit rewrite-edit INPUT --transcript-file final_draft.txt --padding 0.3,0.6
```

## Recommended workflow

Do not treat this as "run one command immediately." The intended use is a guided editorial loop.

1. Confirm the source video path.
2. Generate the source transcript first.
3. Show the user a transcript excerpt or concise summary.
4. Ask targeted editorial questions.
5. Draft a cleaned final transcript.
6. Confirm or revise that final transcript with the user.
7. Run `toolkit rewrite-edit`.
8. Return the finished video path and run directory.

## Step-by-step operating pattern

### Step 1: Transcribe first

Start by generating transcript artifacts:

```bash
toolkit transcribe INPUT
```

Return:

- the transcript path
- the run directory
- a short readable excerpt or sectioned summary in chat

Do not dump the entire transcript into chat unless the user asks for it.

### Step 2: Run an editorial conversation

After transcription, ask a small set of concrete editorial questions that help shape the final transcript.

Good questions include:

- "What do you want the video to lead with?"
- "What do you want the video to be about?"
- "What framework should I use?"
- "What lines absolutely have to stay in the video?"
- "What should be cut even if it is spoken clearly?"
- "Do you want this to feel punchy, reflective, educational, or promotional?"

Prefer 2-4 focused questions at a time, not a giant questionnaire.

If the user is unsure, offer a few editorial directions based on the transcript:

- hook-first
- story-first
- lesson-first
- framework-first
- CTA-first

### Step 3: Draft the final transcript

Once you have enough direction:

- rewrite the transcript into the intended final spoken draft
- keep it close enough to the spoken audio that `rewrite-edit` can match it
- preserve must-keep lines exactly when the user says they are non-negotiable

If the rewrite is materially more polished than the spoken source, warn the user that matching may become less reliable.

### Step 4: Confirm before cutting

Before rendering, show the proposed final transcript or a concise "opening / body / close" version and let the user adjust it.

Do not cut immediately if the editorial intent is still unclear.

### Step 5: Render the cut

Save the approved transcript to a text file when it is more than a short sentence, then run:

```bash
toolkit rewrite-edit INPUT --transcript-file final_draft.txt
```

Use `--transcript` only for short inline drafts. Use `--stdin` when another tool already produced the final text.

## Good defaults

- Prefer `toolkit transcribe INPUT` before editorial discussion.
- Prefer `--transcript-file` when the final draft is more than a short sentence.
- Prefer `--stdin` when another tool already emitted the cleaned transcript text.
- Use `--padding` only when the user wants slightly looser cuts around transcript matches.
- Keep the rewrite conservative by default so the matcher stays reliable.

## Conversation guidance

Use this skill like an editor, not like a batch processor.

Good behavior:

- summarize what the speaker is already saying before proposing a rewrite
- surface possible hooks and angles
- ask what must stay in
- ask what should lead
- ask what frame or structure the user wants
- iterate once or twice on the transcript before rendering

Avoid:

- cutting immediately from a vague prompt
- rewriting so aggressively that the spoken words no longer match
- asking a long unfocused interview when two good questions would do
- forcing the browser editor when the user wants an agent-driven workflow

## Output contract

Successful runs return JSON and write:

- `rewrite_target.txt`
- `ranges.json`
- `clip_ranges.json`
- `transcript_edit.mp4`
- `run.json`

Useful response fields:

- `run_dir`
- `clip_count`
- `artifacts.transcript_edit`
- `match_summary`

## Important guidance

- `rewrite-edit` is the first-class agent-facing rendering flow, but this skill begins with transcription and conversation before rendering.
- Prefer this skill over `word-editor` unless the user explicitly wants manual review.
- If the match summary shows many unmatched target tokens, tell the user and consider whether the transcript is too different from the actual spoken audio.
- If the user wants manual inspection after an automated cut, switch to `toolkit word-editor INPUT` as the fallback.
- The approved final transcript is the key handoff artifact. Make sure the user has had a real chance to shape it before rendering.
