from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


@dataclass
class DocumentsConfig:
    paths: list[str]


@dataclass
class RetrievalConfig:
    chunk_chars: int
    overlap_chars: int
    top_k: int


@dataclass
class AsrConfig:
    mode: str
    language: str
    on_device: bool = True


@dataclass
class LlmConfig:
    provider: str
    model: str
    reasoning_effort: str
    timeout_seconds: float
    codex_command: str
    system_prompt: str


@dataclass
class UiConfig:
    title: str
    always_on_top: bool
    width: int
    height: int
    x: int
    y: int


@dataclass
class AppConfig:
    documents: DocumentsConfig
    retrieval: RetrievalConfig
    asr: AsrConfig
    llm: LlmConfig
    ui: UiConfig


DEFAULT_CONFIG: dict[str, Any] = {
    "documents": {
        "paths": ["./knowledge"],
    },
    "retrieval": {
        "chunk_chars": 700,
        "overlap_chars": 120,
        "top_k": 3,
    },
    "asr": {
        "mode": "stdin",
        "language": "en-US",
    },
    "llm": {
        "provider": "codex",
        "model": "gpt-5.3-codex-spark",
        "reasoning_effort": "low",
        "timeout_seconds": 30.0,
        "codex_command": "codex",
        "system_prompt": (
            "Generate short speaking cues from the current input and retrieved sources.\n"
            "Return cues, not scripted answers or meeting minutes. Write in English.\n"
            "1) Return one to three concise cues focused on facts, numbers, and constraints.\n"
            "2) Keep each cue under twelve words, followed by one brief line of evidence.\n"
            "3) If the sources are insufficient, say so and suggest a clarifying question.\n"
            "4) Cite source numbers such as [1] or [2] where available. Do not invent facts."
        ),
    },
    "ui": {
        "title": "Sidecue",
        "always_on_top": True,
        "width": 440,
        "height": 340,
        "x": 36,
        "y": 48,
    },
}


def _from_dict(cls: type[Any], values: dict[str, Any]) -> Any:
    allowed = {field.name for field in fields(cls)}
    return cls(**{key: value for key, value in values.items() if key in allowed})


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _normalize_paths(paths: list[str], config_file: Path) -> list[str]:
    normalized: list[str] = []
    base_dir = config_file.parent
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        normalized.append(str(path))
    return normalized


def load_config(config_path: str | Path) -> AppConfig:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    file_path = Path(config_path).resolve()
    with file_path.open("rb") as fh:
        from_file = tomllib.load(fh)

    merged = _deep_merge(DEFAULT_CONFIG, from_file)
    merged["documents"]["paths"] = _normalize_paths(merged["documents"]["paths"], file_path)

    return AppConfig(
        documents=_from_dict(DocumentsConfig, merged["documents"]),
        retrieval=_from_dict(RetrievalConfig, merged["retrieval"]),
        asr=_from_dict(AsrConfig, merged["asr"]),
        llm=_from_dict(LlmConfig, merged["llm"]),
        ui=_from_dict(UiConfig, merged["ui"]),
    )
