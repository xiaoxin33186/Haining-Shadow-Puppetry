from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Iterable


SUPPORTED_SUFFIXES = frozenset(
    {".md", ".txt", ".docx", ".xlsx", ".xlsm", ".xls", ".json"}
)

SOURCE_MANIFEST_NAME = "knowledge-sources.json"
ACTIVE_SOURCE_ROLES = frozenset({"knowledge", "qa"})
KNOWN_SOURCE_ROLES = frozenset(
    {"knowledge", "qa", "evaluation", "template", "deprecated"}
)


@dataclass
class KnowledgeQAConfig:
    source_paths: tuple[Path, ...] = field(default_factory=tuple)
    qa_paths: tuple[Path, ...] = field(default_factory=tuple)
    discovery_root: Path = field(default_factory=Path.cwd)
    manifest_path: Path | None = None
    recursive: bool = True
    max_chunk_chars: int = 1400
    chunk_overlap_chars: int = 120
    top_k: int = 4
    qa_match_threshold: float = 0.55
    knowledge_match_threshold: float = 0.30
    knowledge_min_coverage: float = 0.40
    watch_interval_seconds: float = 2.0
    max_answer_chars: int = 100

    def __post_init__(self) -> None:
        self.discovery_root = Path(self.discovery_root).expanduser().resolve()
        self.source_paths = _normalize_paths(self.source_paths)
        self.qa_paths = _normalize_paths(self.qa_paths)
        if not self.source_paths and not self.qa_paths:
            manifest = self.discovery_root / SOURCE_MANIFEST_NAME
            if manifest.is_file():
                self.source_paths, self.qa_paths = _load_source_manifest(
                    manifest,
                    self.discovery_root,
                )
                self.manifest_path = manifest.resolve()
        elif self.manifest_path is not None:
            self.manifest_path = Path(self.manifest_path).expanduser().resolve()

    @classmethod
    def from_paths(
        cls,
        source_paths: Iterable[str | Path] = (),
        qa_paths: Iterable[str | Path] = (),
        **kwargs: object,
    ) -> "KnowledgeQAConfig":
        return cls(
            source_paths=_normalize_paths(source_paths),
            qa_paths=_normalize_paths(qa_paths),
            **kwargs,
        )


def _normalize_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    normalized: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(path)
    return tuple(normalized)


def _load_source_manifest(
    manifest_path: Path,
    project_root: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid source manifest JSON: {manifest_path}") from exc

    entries = payload.get("sources", []) if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        raise ValueError("Source manifest field 'sources' must be a list")

    knowledge_paths: list[Path] = []
    qa_paths: list[Path] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"Source manifest entry {index} must be an object")
        raw_path = str(entry.get("path", "")).strip()
        role = str(entry.get("role", "")).strip().casefold()
        if not raw_path:
            raise ValueError(f"Source manifest entry {index} requires a path")
        if role not in KNOWN_SOURCE_ROLES:
            raise ValueError(
                f"Source manifest entry {index} has unsupported role: {role}"
            )
        if role not in ACTIVE_SOURCE_ROLES:
            continue
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = project_root / path
        if role == "knowledge":
            knowledge_paths.append(path)
        else:
            qa_paths.append(path)

    return _normalize_paths(knowledge_paths), _normalize_paths(qa_paths)
