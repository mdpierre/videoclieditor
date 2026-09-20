# Local verification runbook: Silero VAD + PySceneDetect

Audience: an agent (or a human) running on the **Apple Silicon Mac** where this
toolkit actually executes. Purpose: install the optional analysis extras and prove
the Silero VAD + PySceneDetect integration works end-to-end on real media — the part
that could not be validated in the cloud sandbox where it was built.

Branch under test: `claude/ultrareview-ovo55n`. All code (Tasks 1–9) is committed and
pushed; the `silero_vad.onnx` model is bundled in the repo.

Run the steps in order. Stop and report if any **Acceptance** check fails.

---

## 0. Preconditions

- macOS on Apple Silicon.
- `ffmpeg` + `ffprobe`, `whisper-cpp`, and a whisper GGML model already set up
  (this repo's normal requirements — run `toolkit doctor` if unsure).
- A short real video file with speech AND at least one visible shot change, so scene
  detection has something to find. Call its path `$VIDEO` below.

```bash
cd /path/to/videoclieditor
git fetch origin
git checkout claude/ultrareview-ovo55n
git pull --ff-only origin claude/ultrareview-ovo55n
```

**Acceptance:** you are on `claude/ultrareview-ovo55n`, tree clean, and
`ls -l models/silero_vad.onnx` shows a ~2.3 MB file.

---

## 1. Create the venv and install with analysis extras

The repo's convention is a `.venv` in the project root. The core package has zero
runtime deps; the analysis extra pulls in `onnxruntime` and `scenedetect[opencv]`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[analysis]'
```

**Acceptance:**
```bash
.venv/bin/python -c "import onnxruntime, scenedetect; print('onnxruntime', onnxruntime.__version__); print('scenedetect', scenedetect.__version__)"
```
prints both versions with no ImportError.

> If `onnxruntime` has no Apple-Silicon wheel for your Python, use a supported Python
> (3.11 or 3.12 are safe) and recreate the venv. Do **not** switch Silero to torch —
> ONNX is the locked decision.

---

## 2. Run the full test suite (real detectors now present)

In the sandbox, the detector tests were skipped (extras absent) and one unrelated test
failed because there was no `.venv`. On the Mac both conditions are gone.

```bash
.venv/bin/pytest -q
```

**Acceptance:** all tests pass. Specifically:
- The 3 previously-skipped `importorskip` tests in `tests/test_analysis.py` now **run**
  (scenedetect / onnxruntime import-guard + model-missing paths).
- `tests/test_commands.py::test_run_check_command_times_out` now **passes** (it needs
  `.venv/bin/python`, which now exists).
If anything other than those improves-to-green differences fails, stop and report.

---

## 3. Doctor shows the new backends available

```bash
.venv/bin/toolkit doctor
```

**Acceptance:** output includes `silero_available: true` and
`scenedetect_available: true`. (These are informational and never affect the overall
`ok`.)

---

## 4. End-to-end: real Silero + scene snapping on media

Pick a final-transcript file (or inline text). Run a plan first (no render), then the
full edit.

```bash
# Plan only — inspect signals without rendering:
.venv/bin/toolkit plan-edit "$VIDEO" --transcript-file final_draft.txt

# Locate the newest run dir:
RUN=$(ls -dt outputs/*/*/ | head -1); echo "$RUN"

# Inspect the plan:
.venv/bin/python - "$RUN/edit_plan.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
print("vad_backend      :", p.get("vad_backend"))
print("speech_regions   :", len(p.get("speech_regions", [])))
print("silences         :", len(p.get("silences", [])))
print("scene_boundaries :", len(p.get("scene_boundaries", [])), p.get("scene_boundaries", [])[:5])
print("scene_snaps      :", len(p.get("scene_snaps", [])))
for s in p.get("scene_snaps", [])[:5]:
    print("   snap:", s)
PY
```

**Acceptance (the whole point of this runbook):**
- `vad_backend` is **`silero`** (proves Silero ran, not the ffmpeg fallback).
- `speech_regions` is non-empty (Silero produced speech spans).
- `scene_boundaries` is non-empty for a clip that has a real shot change (PySceneDetect
  ran). It may legitimately be empty for a single-shot clip — if so, try a clip with an
  obvious cut before concluding anything is wrong.
- If any proposed cut landed within 0.22 s of a scene boundary, `scene_snaps` has at
  least one entry showing `from → to` moved onto the boundary. Zero snaps is acceptable
  when no cut happened to be near a boundary; the presence of `scene_boundaries` is the
  real proof scene detection is wired in.
- `$RUN/scene_boundaries.json` exists.
- `$RUN/decision_report.html` opens and shows a **"Scene Snaps"** section and a **"VAD
  Backend"** metric reading `silero`.

Then render for real:
```bash
.venv/bin/toolkit rewrite-edit "$VIDEO" --transcript-file final_draft.txt
```
**Acceptance:** `transcript_edit.mp4` is produced in the run dir and plays correctly.

---

## 5. Fallback + override sanity (fast)

Prove the ffmpeg fallback and the new flags behave:

```bash
# Force ffmpeg backend for one run:
.venv/bin/toolkit plan-edit "$VIDEO" --transcript-file final_draft.txt --vad ffmpeg
# -> newest edit_plan.json should show "vad_backend": "ffmpeg"

# Disable scene detection/snapping for one run:
.venv/bin/toolkit plan-edit "$VIDEO" --transcript-file final_draft.txt --no-scene-snap
# -> newest edit_plan.json should show "scene_boundaries": [] and "scene_snaps": []
```

**Acceptance:** both overrides take effect as noted; the config defaults
(`vad_backend = "silero"`, `scene_detection = true`) still apply when the flags are
absent.

---

## 6. Tuning knobs (only if results look off)

All in `config.toml` under `[analysis]` (per-run flags override these):
- `scene_threshold` (PySceneDetect ContentDetector) — **lower** = more scene cuts
  detected, **higher** = fewer. This is the sole authority on what counts as a
  boundary.
- `scene_snap_tolerance` (default `0.22` s) — how close a proposed cut must be to a
  scene boundary to snap onto it.
- `scene_score_bonus` (default `2`) — how strongly a nearby scene boundary biases the
  planner toward cutting there.
- `vad_backend` — `"silero"` or `"ffmpeg"`.
- `scene_detection` — `true`/`false`.

---

## 7. Report back

State: which Acceptance checks passed, the `vad_backend`/`scene_boundaries`/`scene_snaps`
numbers from step 4, and anything that failed with its exact error. If everything
passes, the integration is verified on real hardware and the branch is ready to merge.
