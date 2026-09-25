# Plan: NLE export (FCPXML + EDL)

Status: **Approved / building.** Goal: turn the planner's edit decision into project
files that open in DaVinci Resolve, Premiere Pro, and Final Cut Pro, with each kept
clip carrying its transcript text as the clip name. The CLI renderer stays; export is
additive.

Reasoning-level plan (this file). Atomic tasks: `docs/nle-export-implementation.md`.

> Line numbers are anchors at authoring time; locate by function name and verify.

---

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Primary targets | DaVinci Resolve, Premiere Pro, Final Cut Pro |
| 2 | Formats | **FCPXML** (covers all three, carries names) + **EDL/CMX3600** (universal cuts-only fallback) |
| 3 | Timeline scope | Cuts + clip names (transcript text). Optional per-clip marker. No transitions/effects/captions. |
| 4 | Source media | Project files **reference** the original video and relink; export captures src path + format via ffprobe. |
| 5 | Renderer | Unchanged; export is a new consumer of the same `clip_ranges.json`. |
| 6 | CapCut | **Deferred** to a separate experimental phase — proprietary undocumented draft JSON, not part of this build. |
| 7 | FCPXML version | Target `1.9` (broadly imported by Resolve 17+, Premiere, FCP). |
| 8 | Drop-frame | Default **NDF** (frame-accurate cuts regardless); DF is a later refinement. |

---

## A. What we're serializing

`plan_rewrite_edit` / `transcript_edit` already write `clip_ranges.json`:

```json
[{"id": 1, "start": 0.0, "end": 2.34, "duration": 2.34, "text": "...", "segment_ids": [..]}]
```

Times are **source seconds**. That IS the edit decision. An NLE export is a pure
serializer `clip_ranges + source format → project file`. The planner is untouched.

Everything downstream already flows from these ranges (renderer included), so this fits
the "planner is the intelligence, downstream are clients" architecture exactly. It is
the deferred NLE-export idea, promoted and scoped down to standards-based formats.

---

## B. The real substance: frame-accurate timecode

Formats are easy; correct time is the work.

- Source frame rate comes from `ffprobe` `r_frame_rate`, already a rational like
  `30000/1001`. Keep it rational — never use a rounded float for math.
- seconds → frame index: `round(seconds * fps_num / fps_den)`.
- **FCPXML** expresses time as rational seconds. With `fps = num/den` fps, one frame =
  `den/num` seconds, so `F` frames = `f"{F*den}/{num}s"`. `frameDuration = f"{den}/{num}s"`.
  Integer, frame-exact, drop-frame-safe by construction.
- **EDL** uses SMPTE timecode `HH:MM:SS:FF` at the integer fps (`round(num/den)` for NDF),
  `;` separator + `FCM: DROP FRAME` for DF (deferred).

A time helper module owns all of this so both exporters share one implementation.

---

## C. FCPXML shape (1.9, single asset, single spine)

```
<fcpxml version="1.9">
  <resources>
    <format id="r1" name="FFVideoFormatRateUndefined" frameDuration="1001/30000s" width="1920" height="1080"/>
    <asset id="r2" name="SOURCE" src="file:///abs/path.mp4" start="0s"
           duration="<total frames>s-rational" hasVideo="1" hasAudio="1" format="r1"
           audioSources="1" audioChannels="2" audioRate="48000"/>
  </resources>
  <library>
    <event name="video-cli-toolkit">
      <project name="<stem>">
        <sequence format="r1" duration="<timeline dur>" tcStart="0s" tcFormat="NDF" audioLayout="stereo">
          <spine>
            <!-- one asset-clip per kept range; offset = cumulative timeline position -->
            <asset-clip ref="r2" offset="0s" name="<transcript text>"
                        start="<src in, rational>" duration="<clip len, rational>" tcFormat="NDF">
              <!-- optional -->
              <marker start="<src in>" duration="<1 frame>" value="<transcript text>"/>
            </asset-clip>
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
```

- `start` on the clip = source in-point (frames from source zero).
- `duration` = clip length in frames.
- `offset` = the clip's position on the record timeline = running sum of prior clip
  durations (first clip `0s`).
- `name` = the range's transcript `text` (the "names" deliverable). Sanitize/escape XML.
- `src` = absolute path as a `file://` URL, percent-encoded.

## D. EDL shape (CMX3600)

```
TITLE: <stem>
FCM: NON-DROP FRAME

001  AX       AA/V  C        <src_in> <src_out> <rec_in> <rec_out>
* FROM CLIP NAME: <source filename>
* COMMENT: <transcript text>

002  AX       AA/V  C        ...
```

- Event number: zero-padded 3 digits from 1.
- Reel `AX` (auxiliary — safe, universally accepted).
- Channel `AA/V` (video + 2 audio).
- Edit `C` (cut).
- SRC in/out = source timecode; REC in/out = cumulative record timecode.
- Transcript text rides in a `* COMMENT:` line (EDL has no real clip-name field beyond
  the FROM CLIP NAME comment).

---

## E. Integration

- New `src/video_cli_toolkit/exporters.py` — pure builders + timecode helpers +
  `probe_video_format` (the only ffprobe-touching function).
- `RunContext` gains `fcpxml_path` / `edl_path` (`export.fcpxml`, `export.edl` in the run dir).
- New command `toolkit export INPUT --format fcpxml|edl [--fps N] [--run-id ID] [--from-ranges PATH]`
  reads the run's `clip_ranges.json` (or an explicit ranges file), probes the source,
  writes the project file. Errors clearly if no ranges exist yet.
- New `--export {fcpxml,edl}` (repeatable) flag on `plan-edit` and `rewrite-edit` to emit
  alongside. `plan-edit --export fcpxml` is the headline: a project file **without
  rendering**.

---

## F. Failure / fallback

- No `clip_ranges.json` → actionable error ("run plan-edit/rewrite-edit first").
- `ffprobe` missing or fps unreadable → error, or honor an explicit `--fps`.
- Empty ranges → no file, clear message.
- The renderer path is entirely unaffected; export never runs unless asked.

---

## G. Tests (light, sandbox-runnable)

The builders take a `VideoFormat` value object as input, so they're **pure and fully
testable without ffmpeg**:
- Timecode: seconds→frames rounding; frames→`HH:MM:SS:FF`; frames→FCPXML rational
  (`1001/30000s` family and integer rates).
- `build_edl`: event numbering, reel/channel, src vs rec timecode accumulation,
  FROM CLIP NAME + COMMENT lines, XML-free text.
- `build_fcpxml`: well-formed XML (parse with ElementTree), one asset, N asset-clips,
  offsets accumulate, names/escaping correct, frameDuration matches fps.
- `probe_video_format`: monkeypatch subprocess/ffprobe (or `pytest.importorskip`-style
  guard) — do not require a real ffprobe in CI.

---

## H. Ordered build

1. `exporters.py`: `VideoFormat`, timecode helpers, `build_edl`, `build_fcpxml`,
   `probe_video_format` + `tests/test_exporters.py`.
2. Wire-up: `RunContext` paths, `toolkit export` command, `--export` flag on
   plan-edit/rewrite-edit + tests.

---

## I. Deferred

CapCut (proprietary draft JSON, experimental); OTIO hub; AAF; transitions/effects;
captions/subtitle tracks; drop-frame timecode; multi-track / audio-role mapping.
