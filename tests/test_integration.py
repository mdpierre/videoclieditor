from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from video_cli_toolkit.config import load_config
from video_cli_toolkit.workflow import create_run_context, probe_duration, transcript_edit


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required for media smoke tests")
def test_transcript_edit_with_manual_ranges_renders_real_media(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=640x360:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(input_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    base_config = load_config(PROJECT_ROOT)
    config = replace(base_config, outputs=replace(base_config.outputs, root=tmp_path / "outputs"))
    run_context = create_run_context(config, input_path, run_id="media-smoke")

    ranges_path = tmp_path / "ranges.json"
    ranges_path.write_text(
        json.dumps(
            [
                {"start": 0.5, "end": 1.4, "label": "intro"},
                {"start": 2.0, "end": 2.6, "label": "cta"},
            ]
        )
    )

    metadata = transcript_edit(config, run_context, ranges_path=ranges_path)

    assert metadata["selection_mode"] == "ranges"
    assert metadata["clip_count"] == 2
    assert run_context.transcript_edit_path.exists()
    assert run_context.clip_ranges_path.exists()

    duration = probe_duration(run_context.transcript_edit_path)
    assert 1.3 <= duration <= 1.8
