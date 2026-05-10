# Video CLI Toolkit Cheat Sheet

Project root:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit`

Global command:

`toolkit`

## Core commands

Check environment:

```bash
toolkit doctor
```

Preview setup actions:

```bash
toolkit setup
```

Run setup actions:

```bash
toolkit setup --apply
```

Transcribe a video:

```bash
toolkit transcribe /path/to/video.mp4
```

Generate a transcript review sheet:

```bash
toolkit review-sheet /path/to/video.mp4
```

Convert keep/cut transcript instructions into ranges:

```bash
toolkit ranges-from-review /path/to/video.mp4 --instructions "keep 3-6, 9, 12-14"
toolkit ranges-from-review /path/to/video.mp4 --instructions "cut 0-2, 5"
```

Generate captions:

```bash
toolkit captions /path/to/video.mp4
```

Create a silence-cut edit:

```bash
toolkit edit /path/to/video.mp4
```

Run the full workflow:

```bash
toolkit pipeline /path/to/video.mp4
```

## Transcript edit commands

Exact transcript match:

```bash
toolkit transcript-edit /path/to/video.mp4 --query "important phrase"
```

Multiple exact matches:

```bash
toolkit transcript-edit /path/to/video.mp4 --query "first phrase" --query "second phrase"
```

Fuzzy transcript match:

```bash
toolkit transcript-edit /path/to/video.mp4 --fuzzy-query "approximate spoken wording" --fuzzy-threshold 0.55
```

Manual time ranges:

```bash
toolkit transcript-edit /path/to/video.mp4 --ranges ranges.json
```

Add padding around matches:

```bash
toolkit transcript-edit /path/to/video.mp4 --query "important phrase" --padding 0.4,0.8
```

Social-style tighter cut:

```bash
toolkit transcript-edit /path/to/video.mp4 --query "hook line" --padding 0.2,0.5
```

## Manual ranges format

```json
[
  { "start": 12.4, "end": 18.9, "label": "hook" },
  { "start": 44.1, "end": 58.0, "label": "cta" }
]
```

## Human-in-the-loop review workflow

1. Transcribe:

```bash
toolkit review-sheet /path/to/video.mp4
```

2. Review transcript and decide what to keep:

- `keep 3-6, 9, 12-14`
- `keep 00:12.4-00:18.9 and 00:44.1-00:58.0`
- `keep "feel lonely all the time"`

3. Convert those keeps into `ranges.json`

```bash
toolkit ranges-from-review /path/to/video.mp4 --instructions "keep 3-6, 9, 12-14"
```

4. Render the final cut:

```bash
toolkit transcript-edit /path/to/video.mp4 --ranges ranges.json
```

## Output folder

All runs are written under:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/outputs/<source-stem>/<run-id>/`

Common outputs:

- `audio.wav`
- `transcript.txt`
- `segments.json`
- `review_sheet.txt`
- `ranges.json`
- `captions.srt`
- `edited.mp4`
- `transcript_edit.mp4`
- `clip_ranges.json`
- `selected_segments.json`
- `run.json`

## Skills

Codex skills:

- `$video-cli-toolkit`
- `$transcript-video-edit`
- `$social-clip-cutter`
- `$transcript-review-cut`

Skill files:

- `/Users/michaelpierre/.codex/skills/video-cli-toolkit/SKILL.md`
- `/Users/michaelpierre/.codex/skills/transcript-video-edit/SKILL.md`
- `/Users/michaelpierre/.codex/skills/social-clip-cutter/SKILL.md`
- `/Users/michaelpierre/.codex/skills/transcript-review-cut/SKILL.md`

Claude Code mirror:

`/Users/michaelpierre/Documents/coding-projects/video-cli-toolkit/skills`

## Local project maintenance

Reinstall the package after code changes:

```bash
cd /Users/michaelpierre/Documents/coding-projects/video-cli-toolkit
.venv/bin/python -m pip install . --force-reinstall --no-deps
```

Run tests:

```bash
cd /Users/michaelpierre/Documents/coding-projects/video-cli-toolkit
.venv/bin/pytest -q
```
