
# Transcript Review Cut

Use this skill when the workflow should be:

1. The user gives a video file.
2. You generate and show the transcript.
3. The user marks what stays in and what should be removed.
4. You turn those transcript decisions into a cut video.

If the user does not want a human review loop and already has a target final transcript, prefer `toolkit rewrite-edit` instead of this review-based flow.

Project root:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit`

Primary commands:

- `toolkit review-sheet INPUT`
- `toolkit ranges-from-review INPUT --instructions "..."`
- `toolkit transcript-edit INPUT --ranges ranges.json`

## Workflow

### Step 1: Transcribe first

Run:

`toolkit review-sheet INPUT`

Then return:

- the transcript path
- the segments path
- the review sheet path
- a readable transcript excerpt in chat

Prefer showing the generated `review_sheet.txt`, not just a raw paragraph wall.

### Step 2: Ask the user to mark keep vs cut

After transcription, ask the user to specify what they want to keep.

Preferred user response formats:

- Segment IDs:
  - `keep 3-6, 9, 12-14`
- Time ranges:
  - `keep 00:12.4-00:18.9 and 00:44.1-00:58.0`
- Exact quoted lines:
  - `keep "feel lonely all the time"`
  - `cut "I already know the solution"`

When possible, guide the user toward `keep` instructions rather than `cut` instructions. Keeping selected ranges is more deterministic.

### Step 3: Convert the review into ranges

Use:

- `toolkit ranges-from-review INPUT --instructions "keep 3-6, 9, 12-14"`
- `toolkit ranges-from-review INPUT --instructions "cut 0-2, 5"`

This writes a `ranges.json` file into the run folder.

### Step 4: Render the cut

Use:

`toolkit transcript-edit INPUT --ranges /path/to/generated/ranges.json`

## Selection rules

- If the user gives segment IDs, convert them to the corresponding segment start/end times.
- If the user gives quoted transcript text, locate matching transcript segments first, then derive start/end times.
- Merge adjacent kept segments when gaps are small enough to feel like one continuous thought.
- If the user asks to remove sections instead of keep sections, invert the request carefully and confirm the implied kept ranges if there is any ambiguity.

## Output expectations

Return:

- the final `transcript_edit.mp4` path
- the `clip_ranges.json` path
- a short note explaining which transcript sections were kept

## Important guidance

- Do not jump straight to cutting before the user has reviewed the transcript.
- Prefer showing transcript chunks with IDs and timestamps so the user can reply quickly.
- If the transcript is long, summarize the themes first and then offer to show all segments or a selected section.
- If the user’s keep/cut instructions are ambiguous, clarify before cutting.
