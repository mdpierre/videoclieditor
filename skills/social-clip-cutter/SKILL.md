---
name: "social-clip-cutter"
description: "Use the local video toolkit to cut short social-ready clips from transcript phrases or manual ranges, with tighter padding and a bias toward concise hook-sized segments."
---

# Social Clip Cutter

Use this skill when the user wants a short clip for TikTok, Reels, Shorts, or promo snippets.

Project root:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit`

Primary command:

`toolkit transcript-edit INPUT ...`

## Recommended defaults

For short social cuts, prefer tighter padding than long-form edits:

- `--padding 0.2,0.5` for punchier cuts
- exact `--query` when the user knows the phrase
- `--fuzzy-query` when the wording is approximate
- `--ranges` when the user gives timestamps directly

## Good usage patterns

- Known phrase:
  - `toolkit transcript-edit INPUT --query "here's the problem" --padding 0.2,0.5`
- Approximate wording:
  - `toolkit transcript-edit INPUT --fuzzy-query "this is why that matters" --fuzzy-threshold 0.55 --padding 0.2,0.5`
- Timestamp-driven social clip:
  - `toolkit transcript-edit INPUT --ranges social-ranges.json`

## What this skill is for

- extracting one tight highlight from a longer video
- turning a spoken hook into a shareable clip
- cutting CTA moments, quotable lines, or short teaching moments

## What this skill is not for

- automatic virality scoring
- reframing to vertical video
- adding burned-in captions or graphics

Those should be described as follow-up steps unless the toolkit gains those features.

## Output expectations

Return the path to:

- `transcript_edit.mp4`
- `clip_ranges.json`
- `selected_segments.json` when transcript matching was used

If the clip is too long or too loose, rerun with:

- tighter padding
- fewer queries
- a higher fuzzy threshold
- manual ranges

