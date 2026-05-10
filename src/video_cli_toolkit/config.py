from __future__ import annotations

from dataclasses import dataclass
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
class AppConfig:
    project_root: Path
    whisper: WhisperConfig
    auto_editor: AutoEditorConfig
    outputs: OutputConfig
    captions: CaptionConfig
    ffmpeg: FfmpegConfig

    def model_path(self, model_name: str | None = None) -> Path:
        chosen_model = model_name or self.whisper.model_name
        return self.project_root / self.whisper.model_dir / f"ggml-{chosen_model}.bin"


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
    )

