from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class WhisperConfig:
    model_name: str
    model_dir: Path
    binary_candidates: list[str]


@dataclass(frozen=True)
class AutoEditorConfig:
    margin: str


@dataclass(frozen=True)
class OutputConfig:
    root: Path


@dataclass(frozen=True)
class CaptionConfig:
    format: str


@dataclass(frozen=True)
class FfmpegConfig:
    audio_codec: str
    video_codec: str
    quality: str


@dataclass(frozen=True)
class AnalysisConfig:
    vad_backend: str
    scene_detection: bool
    scene_threshold: float
    scene_snap_tolerance: float
    scene_score_bonus: int


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    whisper: WhisperConfig
    auto_editor: AutoEditorConfig
    outputs: OutputConfig
    captions: CaptionConfig
    ffmpeg: FfmpegConfig
    analysis: AnalysisConfig = field(default_factory=lambda: _load_analysis_config({}))

    def model_path(self, model_name: str | None = None) -> Path:
        chosen_model = model_name or self.whisper.model_name
        return self.project_root / self.whisper.model_dir / f"ggml-{chosen_model}.bin"

    @property
    def silero_model_path(self) -> Path:
        return self.project_root / self.whisper.model_dir / "silero_vad.onnx"


def load_config(project_root: Path) -> AppConfig:
    config_path = project_root / "config.toml"
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    return AppConfig(
        project_root=project_root,
        whisper=WhisperConfig(
            model_name=data["whisper"]["model_name"],
            model_dir=Path(data["whisper"]["model_dir"]),
            binary_candidates=list(data["whisper"]["binary_candidates"]),
        ),
        auto_editor=AutoEditorConfig(
            margin=data["auto_editor"]["margin"],
        ),
        outputs=OutputConfig(
            root=project_root / data["outputs"]["root"],
        ),
        captions=CaptionConfig(
            format=data["captions"]["format"],
        ),
        ffmpeg=FfmpegConfig(
            audio_codec=data["ffmpeg"]["audio_codec"],
            video_codec=data["ffmpeg"]["video_codec"],
            quality=data["ffmpeg"]["quality"],
        ),
        analysis=_load_analysis_config(data.get("analysis", {})),
    )


def _load_analysis_config(analysis_data: dict) -> AnalysisConfig:
    return AnalysisConfig(
        vad_backend=analysis_data.get("vad_backend", "silero"),
        scene_detection=bool(analysis_data.get("scene_detection", True)),
        scene_threshold=float(analysis_data.get("scene_threshold", 27.0)),
        scene_snap_tolerance=float(analysis_data.get("scene_snap_tolerance", 0.22)),
        scene_score_bonus=int(analysis_data.get("scene_score_bonus", 2)),
    )

