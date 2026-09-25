# Implementation spec: NLE export (FCPXML + EDL)

Audience: coding agents. Prescriptive; execute in order. Companion to
`docs/nle-export-plan.md`. Do not exceed this scope (no CapCut, no OTIO, no transitions,
no captions, no drop-frame).

## Ground rules

- **The renderer and planner are untouched.** Export only *reads* `clip_ranges.json` and
  serializes it. No editing logic changes.
- **Builders are pure.** `build_edl` / `build_fcpxml` take a `VideoFormat` value + the
  clip ranges and return a string. Only `probe_video_format` touches ffprobe.
- **Frame-accurate, rational math.** Never do timecode math on a rounded float fps.
- No new hard runtime dependency (stdlib `xml`, `subprocess`, `urllib` only). Core
  `pyproject` deps stay `[]`.
- This environment has NO `.venv`. Validate with `PYTHONPATH=src pytest -q`. The only
  acceptable pre-existing failure is `test_run_check_command_times_out` (missing
  `.venv/bin/python`); confirm it's the ONLY failure.
- Workers: do NOT run `pip install`, `git add/commit/push`. The orchestrator commits.
- Data shape reminder — `clip_ranges.json` entries:
  `{"id": int, "start": float, "end": float, "duration": float, "text": str, "segment_ids": [...]}`
  in **source seconds**, already padded/merged/sorted.

---

## Task 1 — `exporters.py` (timecode core + builders + probe)

Create `src/video_cli_toolkit/exporters.py`.

### 1a. VideoFormat + timecode helpers (pure)

```python
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class VideoFormat:
    fps_num: int          # e.g. 30000
    fps_den: int          # e.g. 1001  (=> 29.97)
    width: int
    height: int
    duration: float       # source duration, seconds
    src_path: str         # absolute path to the source video
    name: str             # source stem, for asset/reel naming

def seconds_to_frames(seconds: float, fps_num: int, fps_den: int) -> int:
    # round to nearest frame; clamp >= 0
    return max(0, round(seconds * fps_num / fps_den))

def frames_to_timecode(frame: int, fps_num: int, fps_den: int) -> str:
    # NON-DROP HH:MM:SS:FF using the integer nominal rate round(fps_num/fps_den)
    fps_int = max(1, round(fps_num / fps_den))
    ff = frame % fps_int
    total_secs = frame // fps_int
    ss = total_secs % 60
    mm = (total_secs // 60) % 60
    hh = total_secs // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

def frames_to_fcpxml_time(frame: int, fps_num: int, fps_den: int) -> str:
    # F frames == (F*fps_den)/fps_num seconds. Return exact rational, simplified.
    if frame == 0:
        return "0s"
    numerator = frame * fps_den
    denominator = fps_num
    # reduce by gcd; if it divides to an integer number of seconds, emit "<int>s"
    from math import gcd
    g = gcd(numerator, denominator)
    numerator //= g
    denominator //= g
    return f"{numerator}s" if denominator == 1 else f"{numerator}/{denominator}s"

def fcpxml_frame_duration(fps_num: int, fps_den: int) -> str:
    # one frame duration in seconds = fps_den/fps_num, simplified
    from math import gcd
    g = gcd(fps_den, fps_num)
    return f"{fps_den // g}/{fps_num // g}s"
```

### 1b. build_edl (pure)

```python
def build_edl(clip_ranges: list[dict], fmt: VideoFormat, *, title: str | None = None) -> str: ...
```
- Header:
  ```
  TITLE: <title or fmt.name>
  FCM: NON-DROP FRAME
  ```
  then a blank line, then events.
- For each clip (1-indexed event `NNN`):
  - src_in_frame = seconds_to_frames(start), src_out_frame = seconds_to_frames(end)
  - clip_len = src_out_frame - src_in_frame (skip clips with len <= 0)
  - rec_in_frame = running total of prior clip_len (starts 0); rec_out_frame = rec_in + clip_len
  - Event line (columns separated by spaces; standard CMX3600 layout):
    `NNN  AX       AA/V  C        SRC_IN SRC_OUT REC_IN REC_OUT`
    where each timecode is frames_to_timecode(...).
  - Then:
    `* FROM CLIP NAME: <fmt.name>`
    `* COMMENT: <clip text, single line, newlines/control chars stripped>`
  - Blank line between events is fine.
- Return the whole string (trailing newline).

### 1c. build_fcpxml (pure)

```python
def build_fcpxml(clip_ranges: list[dict], fmt: VideoFormat, *, project_name: str | None = None,
                 add_markers: bool = True) -> str: ...
```
Build with `xml.etree.ElementTree` (or `xml.sax.saxutils.escape` + string templates —
either is fine, but the OUTPUT MUST PARSE with ElementTree). Structure per plan §C,
version `1.9`:
- `<resources>`: one `<format id="r1" frameDuration=fcpxml_frame_duration(...) width=.. height=..>`
  (a `name` attr is optional; if included use a generic string), and one `<asset id="r2"
  name=fmt.name src=<file url> start="0s" duration=frames_to_fcpxml_time(total_frames)
  hasVideo="1" format="r1" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">`
  where total_frames = seconds_to_frames(fmt.duration).
- `src` = file URL from absolute path: `"file://" + urllib.parse.quote(abs_path)` (ensure
  leading slash; on POSIX paths this yields `file:///Users/...`).
- `<library><event name="video-cli-toolkit"><project name=<project_name or fmt.name>>`
  `<sequence format="r1" tcStart="0s" tcFormat="NDF" audioLayout="stereo"
  duration=frames_to_fcpxml_time(sum of clip lengths)>` `<spine>`.
- For each clip: `src_in = seconds_to_frames(start)`, `clip_len = seconds_to_frames(end) - src_in`
  (skip <=0). `<asset-clip ref="r2" offset=frames_to_fcpxml_time(cumulative)
  name=<text> start=frames_to_fcpxml_time(src_in) duration=frames_to_fcpxml_time(clip_len)
  tcFormat="NDF">`. Accumulate `cumulative += clip_len`.
- If `add_markers` and text is non-empty: child `<marker start=frames_to_fcpxml_time(src_in)
  duration=fcpxml_frame_duration(...) value=<text>/>`.
- Escape all text/attribute values (ElementTree does this automatically).
- Return `'<?xml version="1.0" encoding="UTF-8"?>\n' + serialized` (a DOCTYPE is optional;
  omit it — Resolve/Premiere/FCP import versioned fcpxml without the DTD).

### 1d. probe_video_format (only ffprobe-touching fn)

```python
def probe_video_format(src_path: Path) -> VideoFormat: ...
```
- Run `ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate,width,height
  -show_entries format=duration -of json <path>`.
- Parse `r_frame_rate` "num/den" into ints (den defaults 1). Parse width/height/duration.
- name = src_path.stem; src_path resolved to absolute.
- Raise ToolkitError (import from .workflow or define locally — prefer reusing the
  existing ToolkitError) on ffprobe failure or unparseable rate.

### Task 1 tests — `tests/test_exporters.py`
Pure, no ffmpeg needed. Use a fixed `VideoFormat(fps_num=30000, fps_den=1001, width=1920,
height=1080, duration=..., src_path="/tmp/source.mp4", name="source")` and also an integer
case (fps_num=30, fps_den=1).
- `seconds_to_frames(1.0, 30000, 1001)` == 30; `(1.0, 30, 1)` == 30.
- `frames_to_fcpxml_time(30, 30000, 1001)` == `"1001/1000s"`; `frames_to_fcpxml_time(0,...)`
  == `"0s"`; integer-rate whole second reduces to `"1s"`.
- `fcpxml_frame_duration(30000,1001)` == `"1001/30000s"`.
- `frames_to_timecode(90, 30, 1)` == `"00:00:03:00"`.
- `build_edl`: assert TITLE/FCM header, `001`/`002` events present, `AA/V  C`, the
  `* FROM CLIP NAME:` and `* COMMENT:` lines carry the text, and REC timecodes accumulate
  (event 2's REC_IN equals event 1's REC_OUT).
- `build_fcpxml`: `xml.etree.ElementTree.fromstring(result)` parses; exactly one `asset`,
  N `asset-clip` under one `spine`; clip names equal the range texts; second clip's
  `offset` equals first clip's `duration`; frameDuration attribute matches the fps.
- `probe_video_format`: monkeypatch `subprocess.run` to return canned ffprobe JSON; assert
  the parsed VideoFormat. (No real ffprobe.)

**Acceptance:** `PYTHONPATH=src pytest -q tests/test_exporters.py` all green.

---

## Task 2 — CLI + workflow wiring

### 2a. RunContext (workflow.py)
Add `fcpxml_path` (`run_dir/"export.fcpxml"`) and `edl_path` (`run_dir/"export.edl"`) to
the `RunContext` dataclass and `create_run_context`.

### 2b. workflow helper
Add `export_run(config, run_context, *, formats: list[str], fps_override: tuple[int,int]|None,
ranges_path: Path|None) -> dict` that:
- resolves the ranges file (ranges_path or run_context.clip_ranges_path); error if absent
  ("No clip_ranges found — run plan-edit or rewrite-edit first.").
- loads clip ranges JSON.
- builds VideoFormat via `probe_video_format(run_context.input_path)` unless `fps_override`
  is given, in which case still probe for width/height/duration but replace fps.
- for each requested format, writes the file (fcpxml → fcpxml_path, edl → edl_path) and
  collects the paths.
- returns a metadata dict `{ "step": "export", "formats": [...], "artifacts": {...},
  "clip_count": N }`.

### 2c. CLI (cli.py)
- New subcommand `export`:
  `toolkit export INPUT --format {fcpxml,edl} [--format ...] [--fps N] [--run-id ID]
  [--from-ranges PATH] [--out PATH]`
  - `--format` repeatable (default `["fcpxml"]`).
  - resolves a RunContext for INPUT (reuse existing run-context/run-id resolution used by
    other commands; if `--run-id` omitted, use the newest existing run dir for that source,
    matching how the toolkit locates prior runs).
  - `--from-ranges` overrides the ranges file; `--out` overrides the output path base.
  - `--fps` parses `N` or `N/D` into a rational override.
  - prints the export metadata as JSON (use the existing print_json helper).
- Add `--export {fcpxml,edl}` (repeatable, `action="append"`) to the `plan-edit` and
  `rewrite-edit` parsers. In their handlers, after the plan/edit completes, call
  `export_run(...)` for the requested formats and include the export artifacts in the
  emitted JSON. Absent flag → no export, behavior unchanged.

### Task 2 tests — extend `tests/test_commands.py`
- `test_export_run_writes_fcpxml_and_edl`: monkeypatch `probe_video_format` to a fixed
  VideoFormat, write a small `clip_ranges.json` into a run dir, call `export_run(formats=
  ["fcpxml","edl"])`, assert both files exist, fcpxml parses, edl has the right event count.
- `test_export_run_errors_without_ranges`: no clip_ranges → ToolkitError with a helpful
  message.
- A handler-level test that `plan-edit --export fcpxml` (via the argument-parsing path used
  by existing handler tests) calls export and includes the artifact — mirror the existing
  handler test idiom (fake args, patched workflow fns).

**Acceptance:** `PYTHONPATH=src pytest -q` — all green except the one pre-existing
`.venv` failure. `PYTHONPATH=src python -m video_cli_toolkit.cli export --help` shows the
new flags.

---

## Final verification
```
PYTHONPATH=src pytest -q
PYTHONPATH=src python -m video_cli_toolkit.cli export --help
```
Done when: FCPXML + EDL build from clip_ranges, `toolkit export` works, `--export` rides
plan-edit/rewrite-edit, and the suite is green (minus the known `.venv` failure).
