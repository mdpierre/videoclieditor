
# Transcript Video Edit

Use this skill when the user wants to cut video based on spoken content rather than silence detection alone.

If the user already has a cleaned final-draft transcript and wants the agent to make the edit directly, prefer the `rewrite-edit-video` skill and `toolkit rewrite-edit` instead of this lower-level selection workflow.

Primary command:

`toolkit transcript-edit INPUT ...`

Project root:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit`

## Selection modes

Use exactly one mode per command:

- Exact transcript matching:
  - `toolkit transcript-edit INPUT --query "chapter 16"`
  - Repeat `--query` to match multiple phrases.
- Fuzzy transcript matching:
  - `toolkit transcript-edit INPUT --fuzzy-query "chapter sixteen david" --fuzzy-threshold 0.55`
  - Lower threshold means looser matching.
- Manual time ranges:
  - `toolkit transcript-edit INPUT --ranges ranges.json`

Optional padding:

- `--padding 0.4,0.8`
  Adds seconds before and after each transcript-derived match.

## Manual ranges format

The ranges file must be a JSON array of objects with `start` and `end`:

```json
[
  { "start": 12.4, "end": 18.9, "label": "hook" },
  { "start": 44.1, "end": 58.0, "label": "cta" }
]
```

## Output contract

Transcript-edit runs write these artifacts when applicable:

- `transcript_edit.mp4`
- `clip_ranges.json`
- `selected_segments.json`
- `concat.txt`
- `clips/clip-001.mp4`
- `run.json`

## Operating guidance

- Prefer exact `--query` when the user knows the phrase closely.
- Prefer `--fuzzy-query` when wording may differ from the spoken transcript.
- Prefer `--ranges` when the user already knows timestamps or wants deterministic cuts.
- Tell the user which selection mode you used and return the final `transcript_edit.mp4` path.
- If no transcript match is found, switch to a lower fuzzy threshold or ask for manual ranges.
