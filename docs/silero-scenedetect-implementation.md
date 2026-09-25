# Implementation spec: Silero VAD + PySceneDetect

Audience: coding agents executing the approved plan
(`docs/silero-scenedetect-plan.md`). This file is prescriptive. Do exactly what each
task says, in order. Each task is independently committable and has acceptance
criteria. Do not expand scope beyond these tasks.

## Ground rules

- **Backward compatibility is the top invariant.** With Silero and PySceneDetect
  unavailable, every output must be byte-for-byte identical to today. Several tests
  pin this — do not weaken them.
- **Detectors never raise into the planner.** Wrap all Silero/PySceneDetect work in
  `try/except Exception`, return `None` (detector unavailable/failed) or `[]`.
- **No new runtime dependency in the core package.** `pyproject.toml`
  `dependencies` stays `[]`. New deps go under an `analysis` optional-extra.
- **Prefer existing abstractions.** No new dataclasses, provider registries, service
  layers, or event buses. Plain dicts/lists/floats only.
- Line numbers are anchors at authoring time; locate by **function name** and verify
  before editing. Files: `src/video_cli_toolkit/{analysis.py(new),workflow.py,rewrite_matcher.py,config.py}`,
  `config.toml`, `pyproject.toml`, `tests/`.
- After Python changes: `.venv/bin/python -m pip install . --force-reinstall --no-deps`
  then `.venv/bin/pytest -q`. Full suite must stay green after every task.

---

## Task 1 — Pure helpers in new module `analysis.py`

Create `src/video_cli_toolkit/analysis.py`. Implement **only the two pure functions**
in this task (detectors come later). No third-party imports here.

```python
from __future__ import annotations
from typing import Any

def speech_regions_to_silences(
    speech_regions: list[dict[str, float]],
    audio_duration: float,
) -> list[dict[str, float]]:
    """Invert speech regions into non-speech regions.

    Input:  [{"start": s, "end": e}, ...] sorted or unsorted, possibly overlapping.
    Output: [{"start","end","duration"}, ...] covering the complement within
            [0, audio_duration], each with duration > 0.
    """

def snap_ranges_to_scene_boundaries(
    ranges: list[dict[str, float]],
    scene_boundaries: list[float],
    *,
    tolerance: float = 0.22,
) -> tuple[list[dict[str, float]], list[dict[str, Any]]]:
    """Snap clip edges onto nearby scene cuts without clipping speech.

    For each adjacent pair, gap = [ranges[i]["end"], ranges[i+1]["start"]].
    A boundary b may move ranges[i]["end"] up to b iff
        ranges[i]["end"] <= b <= ranges[i]["end"] + tolerance  and  b <= gap end.
    Symmetric for ranges[i+1]["start"] (b within [start - tolerance, start], b >= gap start).
    First range start snaps within [0, start]; last range end within [end, +inf) only
    up to tolerance. Never move an edge past its own range's opposite edge; never make
    ranges overlap. Ties -> nearest boundary.
    Returns (snapped_ranges, snap_log) where snap_log entries are
        {"edge":"start"|"end","clip_index":int,"from":float,"to":float,"boundary":float}.
    Input ranges are not mutated.
    """
```

Implementation notes:
- Sort/clamp/merge inverted silences defensively; drop zero/negative-duration spans.
- `snap_ranges_to_scene_boundaries` with `scene_boundaries == []` returns
  `(ranges_copy, [])` — exact identity of values.

**Acceptance (add to `tests/test_analysis.py`):**
- `speech_regions_to_silences([{ "start":1,"end":2}], 4.0) == [{"start":0.0,"end":1.0,"duration":1.0},{"start":2.0,"end":4.0,"duration":2.0}]`.
- Empty speech → single silence `[0, audio_duration]`; full-coverage speech → `[]`.
- Snap: boundary at 2.10 with clip end 2.00, next start 2.60, tol 0.22 → end moves to 2.10, log has one `end` entry.
- Snap: boundary at 2.40 (outside tol) → unchanged, empty log.
- Snap: boundary that would cross into next clip's kept audio → rejected.
- `scene_boundaries=[]` → ranges unchanged, empty log.

---

## Task 2 — `detect_scene_boundaries` in `analysis.py`

Add, guarded on PySceneDetect. Threshold comes from caller (config); no secondary
"strength" logic (decision 4).

```python
def detect_scene_boundaries(
    video_path,               # pathlib.Path
    *,
    threshold: float = 27.0,  # PySceneDetect ContentDetector default; caller overrides from config
) -> list[float] | None:
    """Return scene-cut times (seconds) or None if PySceneDetect is unavailable/fails.
    Uses scenedetect.detect + ContentDetector. Return cut points as sorted floats
    (start of each scene after the first, i.e. the boundary times). Never raises."""
    try:
        from scenedetect import detect, ContentDetector
    except Exception:
        return None
    try:
        scenes = detect(str(video_path), ContentDetector(threshold=threshold))
    except Exception:
        return None
    # scenes: list of (start_timecode, end_timecode). Boundary times = each scene start
    # after the first. Convert to seconds via .get_seconds().
    ...
```

**Acceptance:** unit test skips with `pytest.importorskip("scenedetect")` when absent;
when present, returns a `list[float]` (may be empty) and never raises on a tiny fixture
clip. If no fixture is practical, at minimum assert the import-guard path returns `None`
by monkeypatching the import to fail.

---

## Task 3 — `detect_speech_regions_silero` in `analysis.py` (ONNX)

Add, guarded on `onnxruntime` + the bundled model. **ONNX only — do not import torch.**

```python
def detect_speech_regions_silero(
    audio_path,               # pathlib.Path to 16 kHz mono WAV (audio.wav)
    *,
    model_path,               # pathlib.Path to bundled silero_vad.onnx
    threshold: float = 0.5,
    sampling_rate: int = 16000,
) -> list[dict[str, float]] | None:
    """Return speech regions [{"start","end"}] in seconds, or None if unavailable/failed.
    Never raises. Requires onnxruntime and the bundled ONNX model; if either is missing
    return None so the caller falls back to ffmpeg silence detection."""
```

Implementation notes:
- Use the Silero ONNX VAD inference loop (frame the 16 kHz mono samples, run
  `onnxruntime.InferenceSession`, collect contiguous speech frames into regions).
  Read the WAV with the stdlib `wave` module — do **not** add numpy/soundfile to core
  deps (numpy is acceptable only if it is already pulled in by `onnxruntime`; prefer
  stdlib + `array` where feasible).
- `audio.wav` is guaranteed 16 kHz mono PCM (`build_ffmpeg_extract_command`), so no
  resampling path is required; if sample rate differs, return `None`.
- Missing model file → return `None`.

**Acceptance:** `pytest.importorskip("onnxruntime")`-guarded test; with the model
absent returns `None`. Do not require a real audio fixture in CI.

---

## Task 4 — Config, packaging, bundled model

1. `config.toml`: append
   ```toml
   [analysis]
   vad_backend = "silero"        # "silero" | "ffmpeg"
   scene_detection = true
   scene_threshold = 27.0        # PySceneDetect ContentDetector threshold (sole authority)
   scene_snap_tolerance = 0.22
   scene_score_bonus = 2         # boundary-score bonus when a gap sits on a scene cut
   ```
2. `config.py`: add a frozen `AnalysisConfig` dataclass and load it with **defaults**
   so existing `config.toml` files without `[analysis]` still load:
   ```python
   analysis_data = data.get("analysis", {})
   AnalysisConfig(
       vad_backend=analysis_data.get("vad_backend", "silero"),
       scene_detection=bool(analysis_data.get("scene_detection", True)),
       scene_threshold=float(analysis_data.get("scene_threshold", 27.0)),
       scene_snap_tolerance=float(analysis_data.get("scene_snap_tolerance", 0.22)),
       scene_score_bonus=int(analysis_data.get("scene_score_bonus", 2)),
   )
   ```
   Add `analysis: AnalysisConfig` to `AppConfig`. Add a `model_path`-style helper for
   the bundled ONNX model, e.g. `silero_model_path` under the existing `models/` dir.
3. `pyproject.toml`: add
   ```toml
   [project.optional-dependencies]
   analysis = ["onnxruntime", "scenedetect[opencv]"]
   ```
   Core `dependencies` stays `[]`.
4. Bundle `models/silero_vad.onnx` from the official `snakers4/silero-vad` repo
   (`silero_vad.onnx`, ~1–2 MB). Reference it via the config helper, not a hardcoded path.

**Acceptance:** `test_config.py` still passes; add a test that a config dict lacking
`[analysis]` loads with the documented defaults.

---

## Task 5 — Matcher: scene boundaries influence boundary scores (decision 6)

Edit `src/video_cli_toolkit/rewrite_matcher.py`. **Backward compatible**:
`scene_boundaries=None` must reproduce current scores exactly.

1. `_boundary_score(words, left_index, right_index, *, scene_boundaries=None, scene_tolerance=0.22, scene_bonus=2)`:
   after computing the current score, if `scene_boundaries` and the gap
   `[words[left_index]["end"], words[right_index]["start"]]` is within
   `scene_tolerance` of any boundary (or a boundary lies inside the gap), add
   `scene_bonus`.
2. `score_boundary(...)`: forward the same new kwargs.
3. Thread `scene_boundaries`, `scene_tolerance`, `scene_bonus` through
   `build_ranges_from_kept_word_ids` → `_should_split_at_gap` → `_boundary_score`.
   Add them as keyword-only params with defaults (`None`/`0.22`/`2`). Do not change
   positional signatures.
4. `_is_weak_cut` / `_duplicate_take_score` call `_boundary_score` without scene args —
   leave them as-is (they pass `None` implicitly), so duplicate-take behavior is
   unchanged.

**Acceptance:**
- Existing `test_rewrite_matcher.py` passes unchanged.
- New test: a gap that scored `N` scores `N + scene_bonus` when a scene boundary sits
  on it; scores `N` when `scene_boundaries=None` or the nearest boundary is outside
  tolerance.

---

## Task 6 — Workflow: selector + scene detect + snap + plan keys

Edit `src/video_cli_toolkit/workflow.py`.

1. Add `resolve_silences(config, run_context, *, silence_threshold_db, min_silence_duration) -> tuple[list[dict], str]`
   returning `(silences, vad_backend)`:
   - If `config.analysis.vad_backend == "silero"`: try
     `detect_speech_regions_silero(run_context.audio_path, model_path=config.silero_model_path)`.
     On non-`None`, invert via `speech_regions_to_silences(speech, probe_duration(audio_path))`
     → `(silences, "silero")`.
   - Otherwise / on `None`: `(detect_audio_silences(run_context.audio_path,
     noise_db=silence_threshold_db, min_duration=min_silence_duration), "ffmpeg")`.
2. In `plan_rewrite_edit` (~`:1722`): replace the direct `detect_audio_silences(...)`
   call with `silences, vad_backend = resolve_silences(...)`.
3. Detect scene boundaries once (if `config.analysis.scene_detection`):
   `scene_boundaries = detect_scene_boundaries(run_context.input_path,
   threshold=config.analysis.scene_threshold) or []`. Cache to
   `run_context.run_dir / "scene_boundaries.json"`.
4. Pass `scene_boundaries` + tolerance + bonus into `build_rewrite_plan` →
   `build_ranges_from_kept_word_ids` (extend `build_rewrite_plan`'s signature with
   keyword-only `scene_boundaries=None`, `scene_tolerance`, `scene_bonus`).
5. After ranges are built and **before** `apply_padding_to_ranges` (~`:1736`):
   `ranges, scene_snaps = snap_ranges_to_scene_boundaries(ranges, scene_boundaries,
   tolerance=config.analysis.scene_snap_tolerance)`.
6. Add to the `plan` dict (~`:1741`): `"vad_backend": vad_backend`,
   `"speech_regions": speech_regions_or_[]`, `"scene_boundaries": scene_boundaries`,
   `"scene_snaps": scene_snaps`. Keep `"silences"` as-is (now possibly Silero-derived).
7. Add `scene_boundaries` artifact path to `plan["artifacts"]` if the cache file was
   written.

**Acceptance:** `test_plan_rewrite_edit_writes_inspectable_artifacts` extended to
assert the four new keys exist; existing assertions still pass.

---

## Task 7 — Route the other two consumers through the selector

1. `rewrite_edit` (~`:1787`): it delegates to `plan_rewrite_edit`, so it inherits the
   selector. Ensure its returned `metadata["audio_silence_detection"]` reports
   `vad_backend` from the plan.
2. `apply_word_editor_rewrite` (~`:2618`, call at `:2626`): replace direct
   `detect_audio_silences(...)` with `resolve_silences(...)`; include `vad_backend`
   in its returned metadata dict alongside the existing `audio_silence_detection`
   block.

**Acceptance:** `test_apply_word_editor_rewrite_*` tests pass; add an assertion that a
`vad_backend` field is present.

---

## Task 8 — Fallback/contract tests + doctor checks

1. Tests in `tests/test_commands.py` (reuse the monkeypatch style at `:591`):
   - Patch `video_cli_toolkit.workflow.detect_speech_regions_silero` → `None`; assert
     plan `vad_backend == "ffmpeg"` and ranges equal the current baseline for a fixed
     input.
   - Patch it to return synthetic speech; assert `plan["speech_regions"]` populated and
     inverted silences appear in `plan["silences"]`.
   - Patch `detect_scene_boundaries` → `[t0, t1]`; assert `plan["scene_boundaries"]`
     and that at least one `scene_snaps` entry appears for a range edge near `t0/t1`.
2. `doctor` (~`:2890`): add soft, informational `silero_available` and
   `scenedetect_available` booleans (import-guarded probes). Do **not** make them
   required for the overall pass/fail.

**Acceptance:** full suite green; `doctor` output includes the two new keys.

---

## Task 9 — Optional (do only if Tasks 1–8 are green)

- `generate_decision_report_html`: add a "Scene snaps" table (edge, clip, from→to,
  boundary) and show `vad_backend`. Add an HTML contract assertion.
- `cli.py`: `--vad {silero,ffmpeg}` and `--no-scene-snap` flags on `plan-edit` /
  `rewrite-edit`, overriding config. Wire through `resolve_edit_options`-style plumbing.

---

## Final verification

```bash
.venv/bin/python -m pip install . --force-reinstall --no-deps
.venv/bin/toolkit doctor
.venv/bin/pytest -q
.venv/bin/pytest -q tests/test_rewrite_matcher.py
.venv/bin/pytest -q tests/test_commands.py
.venv/bin/pytest -q tests/test_analysis.py
```

Done when: all tests green; with `analysis` extras uninstalled the pipeline output is
unchanged from `main`; with them installed, `edit_plan.json` shows `vad_backend`,
`speech_regions`, `scene_boundaries`, and `scene_snaps`.
