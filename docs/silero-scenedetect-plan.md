# Plan: Silero VAD + PySceneDetect signal integration

Status: **Approved / locked.** Scope: improve the quality of signals feeding the
existing edit planner. Do **not** expand product scope.

This document is the reasoning-level plan (hand to Codex). The atomic, executable
task list lives in `docs/silero-scenedetect-implementation.md`.

> Line numbers below are anchors at time of writing (`workflow.py` ~3241 lines,
> `rewrite_matcher.py` ~661 lines). Prefer function names; verify offsets before editing.

---

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | VAD default & fallback | **Silero on by default**; silent fallback to existing ffmpeg `detect_audio_silences` on any failure. Plan records `vad_backend`. |
| 2 | Silero runtime | **ONNX** (`onnxruntime`). Model file **bundled in-repo** (offline, reproducible). No torch. |
| 3 | Model provisioning | **Bundled**, not downloaded at runtime. |
| 4 | "Strong" scene boundary | **PySceneDetect's own threshold is the sole authority.** No secondary scoring layer. |
| 5 | Snap tolerance | **0.22 s** (in the 0.20–0.25 s range). |
| 6 | Scene → planner | Scene boundaries **influence boundary scores** (feed into `_boundary_score`) **and** a final snap step aligns the exact frame. Scoring decides *whether* to cut; snapping aligns *where*. |

---

## A. Current architecture (the seams we touch)

The rewrite/plan pipeline is one linear flow in `workflow.py`; matcher math is
isolated in `rewrite_matcher.py`.

| Concern | Function | Location | Shape |
|---|---|---|---|
| Word timestamps | `transcribe_words()` | `workflow.py:1368` | `word_segments.json`; `words=[{id,word,start,end}]` |
| Audio extraction | `extract_audio()` / `build_ffmpeg_extract_command` | `workflow.py:1234, 234` | `audio.wav` — **16 kHz mono PCM** |
| Homegrown silence detection | `detect_audio_silences()` / `parse_silencedetect_output()` | `workflow.py:874, 838` | `[{start,end,duration}]` = **non-speech** |
| Boundary scoring | `score_boundary()` / `_boundary_score()` | `rewrite_matcher.py:389, 362` | `int` per word-gap |
| Boundary-score table | `build_boundary_scores()` | `workflow.py:1477` | rows w/ `score`, `overlaps_detected_silence` |
| Range building | `build_ranges_from_kept_word_ids()` | `rewrite_matcher.py:536` | `[{start,end}]` |
| Split gate (consumes silences) | `_should_split_at_gap()` / `_gap_matches_audio_silence()` | `rewrite_matcher.py:494, 326` | bool |
| Plan orchestration + artifacts | `plan_rewrite_edit()` | `workflow.py:1699` | `edit_plan.json` + `decision_report.html` |
| Final render | `apply_padding_to_ranges()` → `transcript_edit()` | `workflow.py:564, 906` | `clip_ranges.json` → `transcript_edit.mp4` |
| Browser fallback (same seam) | `apply_word_editor_rewrite()` | `workflow.py:2618` | calls `detect_audio_silences` at `:2626` |

**Two facts that make this cheap:**

1. `audio_silences` is already an optional parameter threaded through the matcher
   (`build_ranges_from_kept_word_ids(..., audio_silences=...)`), with **one producer**
   (`detect_audio_silences`) feeding **two call sites** (`plan_rewrite_edit:1722`,
   `apply_word_editor_rewrite:2626`). That is the entire VAD blast radius.
2. `audio.wav` is already 16 kHz mono — exactly Silero's expected input — and is
   extracted before any planning runs. No new ffmpeg work.

---

## B. Recommended minimal integration

**Silero — invert, don't re-architect.** Silero emits *speech* regions; every
existing consumer wants *non-speech* regions in `{start,end,duration}` form. Run
Silero → invert to non-speech → hand to the identical `audio_silences` seam. The
matcher/boundary-scorer/browser-fallback need zero structural change. Silero
becomes authoritative simply by being the selected producer, ffmpeg as fallback.

**PySceneDetect — two touch points (per decision 6).**
1. *Scoring:* pass scene cut times into `_boundary_score` so gaps sitting on a real
   shot change earn a bonus, nudging the planner to prefer cutting there.
2. *Snapping:* after word-level `ranges` are built and **before** padding, snap each
   range edge to a nearby scene cut **only when that cut sits inside the inter-clip
   gap** (never clips a spoken word).

Do **not** turn VAD or scene boundaries into new candidate-cut generators. The
transcript rewrite stays the sole cut driver; the new signals only gate splitting,
bias scoring, and align edges.

---

## C. Files that change

1. **`src/video_cli_toolkit/analysis.py`** *(new, small)* — Silero + PySceneDetect
   wrappers and two pure helpers. Only new module.
2. **`src/video_cli_toolkit/rewrite_matcher.py`** — add optional `scene_boundaries`
   to `_boundary_score` / `score_boundary`; thread through
   `build_ranges_from_kept_word_ids` → `_should_split_at_gap`.
3. **`src/video_cli_toolkit/workflow.py`** — `resolve_silences()` selector; scene
   detect + snap in `plan_rewrite_edit`; new plan keys; route `rewrite_edit` and
   `apply_word_editor_rewrite` through the selector; `doctor` soft checks; optional
   report table.
4. **`src/video_cli_toolkit/config.py` + `config.toml`** — optional `[analysis]`
   block, loaded with `.get(...)` defaults so existing configs still load.
5. **`pyproject.toml`** — `[project.optional-dependencies] analysis = [...]`; core
   `dependencies` stays `[]`.
6. **`models/` (or `assets/`)** — bundled `silero_vad.onnx`.
7. **`tests/`** — pure-function + fallback/contract tests.

---

## D. Data flow

```
audio.wav (16k mono, already produced)
   ├─ Silero VAD (ONNX) ── speech_regions [{start,end}] ──invert──► silences [{start,end,duration}]
   │                                       (fallback: ffmpeg detect_audio_silences)
source video ── PySceneDetect ──► scene_boundaries [float, ...]
                                        │
words + silences + scene_boundaries ──► build_ranges_from_kept_word_ids ──► ranges [{start,end}]
   (scene_boundaries bias _boundary_score)          │
                          snap_ranges_to_scene_boundaries(ranges, scene_boundaries, 0.22)
                                        ▼
                     apply_padding_to_ranges ──► clip_ranges ──► transcript_edit.mp4
                                        │
        edit_plan.json ◄───────────────┘  (+ speech_regions, scene_boundaries, scene_snaps, vad_backend)
```

**Data structures (kept dumb):**
- Speech regions: `[{"start": float, "end": float}]`
- Non-speech (unchanged schema): `[{"start","end","duration"}]`
- Scene boundaries: `[float]` cut times
- Snap log: `[{"edge":"start|end","clip_index":int,"from":float,"to":float,"boundary":float}]`

No new dataclasses, no schema versioning, no provider registry.

---

## E. Cut / boundary decision logic

1. **Silences (same logic, new source):** inverted Silero non-speech flows into
   `_should_split_at_gap` / `_gap_matches_audio_silence` exactly as ffmpeg silences
   do today; `build_boundary_scores`' `overlaps_detected_silence` flag gets more
   accurate for free.
2. **Scene scoring (decision 6):** in `_boundary_score`, if the gap between the two
   words is within `scene_snap_tolerance` of any scene boundary, add a small bonus
   (proposed `+2`, tune during testing). `scene_boundaries=None` → no bonus →
   identical to today.
3. **Scene snap:** for each adjacent clip pair, gap = `[clip[i].end, clip[i+1].start]`.
   If a scene boundary `b` is within tolerance of an edge **and** stays inside the
   gap, move that edge to `b`. First/last edges snap against `[0, duration]`. Ties →
   nearest. Snapping only moves an edge within silence toward a real shot change:
   never removes words, never overlaps clips.
4. **Ordering:** snap runs on pre-padding `ranges`; `apply_padding_to_ranges` then
   re-merges/clamps, so snapped edges stay valid.

---

## F. Failure / fallback behavior

- Silero import/model/runtime failure → `detect_speech_regions_silero` returns `None`
  → `resolve_silences` falls back to `detect_audio_silences`. Plan records
  `vad_backend: "ffmpeg"`.
- PySceneDetect failure → `detect_scene_boundaries` returns `None` → `scene_boundaries=[]`
  → scoring bonus and snap both no-op → identical to today.
- Both unavailable → byte-for-byte current behavior. **This is the invariant tests pin.**
- Detectors must **never raise into the planner** — catch and return `None`/`[]`.
- `doctor` gains soft `silero_available` / `scenedetect_available` checks (informational,
  not required).

---

## G. Tests (light; real detectors guarded by `pytest.importorskip`)

Pure (no torch/opencv):
1. `speech_regions_to_silences` — inversion; empty speech → whole clip silent;
   full-coverage speech → `[]`.
2. `snap_ranges_to_scene_boundaries` — in-tolerance snaps, out-of-tolerance identity,
   cross-clip rejection, correct snap log, empty boundaries → identity.
3. `_boundary_score(scene_boundaries=...)` — bonus near a cut; unchanged when `None`.

Contract / fallback (reuse monkeypatch idiom at `tests/test_commands.py:591`):
4. Patch `detect_speech_regions_silero → None`; assert `vad_backend=="ffmpeg"` and
   output equals baseline.
5. Patch Silero to synthetic speech; assert inverted silences reach the plan and
   `plan["speech_regions"]` populated.
6. Patch `detect_scene_boundaries` to return times; assert `plan["scene_boundaries"]`
   / `scene_snaps` present and a clip edge moved.
7. `edit_plan.json` contract: new keys exist; `decision_report.html` still renders
   (extend `test_plan_rewrite_edit_writes_inspectable_artifacts`).

---

## H. Ordered steps

1. `analysis.py` pure functions (`speech_regions_to_silences`,
   `snap_ranges_to_scene_boundaries`) + unit tests → green.
2. `detect_scene_boundaries` (guarded) + cache to `run_dir/scene_boundaries.json`.
3. `detect_speech_regions_silero` (ONNX, guarded, bundled model).
4. `[analysis]` config + defaulted loader + `pyproject.toml` extra + bundle
   `silero_vad.onnx`.
5. Matcher: add `scene_boundaries` to `_boundary_score` + thread through; run full
   suite (must stay green).
6. `plan_rewrite_edit`: `resolve_silences()`, scene detect, snap step, new plan keys.
7. Route `rewrite_edit` + `apply_word_editor_rewrite` through `resolve_silences()`.
8. Fallback/contract tests + `doctor` checks.
9. Optional: report table + CLI flags (`--vad`, `--no-scene-snap`).
10. `.venv/bin/python -m pip install . --force-reinstall --no-deps` → `.venv/bin/pytest -q`.

---

## I. Deferred (not this phase)

auto-editor's internal silence detection; VAD/scene as new candidate-cut generators;
new editing modes; semantic CV / face-object tracking / B-roll / reframing /
thumbnails; new UI / Remotion; OTIO / FCPXML / Resolve / Premiere export; large
architecture rewrites.

---

## ONNX vs torch (why ONNX)

Silero ships the same model as PyTorch and ONNX files — identical weights, identical
accuracy. Torch drags in a very heavy dependency (hundreds of MB to >1 GB); ONNX runs
through the lightweight `onnxruntime`. For a currently zero-dependency, local-first,
bundle-the-model toolkit on Apple Silicon, ONNX is the clear fit. Bundle
`silero_vad.onnx` (~1–2 MB) from the official `snakers4/silero-vad` repo so the
checked-in file matches upstream.
