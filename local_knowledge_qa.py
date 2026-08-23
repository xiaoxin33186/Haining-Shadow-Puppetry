"""本地知识问答模块（单文件内联版）。

本文件由 portable-knowledge-qa v0.4.0 内联合并而来，
原独立 Skill 包位于 portable-knowledge-qa/portable_knowledge_qa/，
合并顺序：config → models → parsers → retrieval → generator → engine。
原 CLI 能力（cli.py / __main__.py）不保留。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol, Sequence

# =====================================================================
# config.py
# =====================================================================

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


# =====================================================================
# models.py
# =====================================================================

@dataclass(frozen=True)
class ParsedSection:
    path: str
    title: str
    locator: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QAPair:
    question: str
    answer: str
    path: str
    locator: str
    qa_id: str = ""
    source_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    path: str
    title: str
    sections: tuple[ParsedSection, ...]
    qa_pairs: tuple[QAPair, ...]
    fingerprint: str
    role: str = "knowledge"


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    text: str
    path: str
    title: str
    locator: str
    kind: str = "knowledge"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceRef:
    path: str
    title: str
    locator: str
    kind: str
    score: float
    excerpt: str
    qa_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    match_type: str
    confidence: float
    contexts: tuple[str, ...]
    sources: tuple[SourceRef, ...]
    authoritative_answer: str = ""

    @property
    def has_evidence(self) -> bool:
        return bool(self.contexts)

    def to_dict(
        self,
        include_sources: bool = False,
        include_evidence: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": self.query,
            "match_type": self.match_type,
            "confidence": round(self.confidence, 6),
            "context_count": len(self.contexts),
        }
        if include_sources:
            payload["sources"] = [source.to_dict() for source in self.sources]
        if include_evidence:
            payload["evidence"] = list(self.contexts)
            if self.authoritative_answer:
                payload["authoritative_answer"] = self.authoritative_answer
        return payload


@dataclass(frozen=True)
class QueryResult:
    query: str
    answer: str
    intent: str
    styled_text: str
    match_type: str
    confidence: float
    source_count: int
    sources: tuple[SourceRef, ...] = ()
    generation_mode: str = "extractive"
    grounded: bool = True
    generation_status: str = "extractive"
    error: str = ""

    def to_dict(self, include_sources: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": self.query,
            "answer": self.answer,
            "intent": self.intent,
            "styled_text": self.styled_text,
            "match_type": self.match_type,
            "confidence": round(self.confidence, 6),
            "source_count": self.source_count,
            "generation_mode": self.generation_mode,
            "grounded": self.grounded,
            "generation_status": self.generation_status,
        }
        if self.error:
            payload["error"] = self.error
        if include_sources:
            payload["sources"] = [source.to_dict() for source in self.sources]
        return payload


# =====================================================================
# parsers.py
# =====================================================================

KNOWN_SOURCE_DIRS = (
    "local_kb",
    "local_kb_extended",
    "question_bank",
    "knowledge",
    "knowledge_base",
    "kb",
    "faq",
    "qa",
    "知识库",
    "问答库",
)

QA_FILENAME_MARKERS = (
    "题库",
    "问答",
    "答案",
    "faq",
    "question",
    "answer",
    "qa",
)


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    fingerprint: str
    force_qa: bool = False
    role: str = "auto"


def discover_source_paths(start: str | Path) -> tuple[Path, ...]:
    start_path = Path(start).expanduser().resolve()
    roots = (start_path, *tuple(start_path.parents)[:3])
    for root in roots:
        found = [root / name for name in KNOWN_SOURCE_DIRS if (root / name).exists()]
        if found:
            return _deduplicate_paths(found)

    direct_files = [
        path
        for path in start_path.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ] if start_path.is_dir() else []
    return _deduplicate_paths(direct_files)


def collect_snapshots(
    source_paths: Iterable[Path],
    qa_paths: Iterable[Path] = (),
    recursive: bool = True,
    source_role: str = "auto",
) -> tuple[FileSnapshot, ...]:
    qa_file_keys = {
        str(path.resolve()).casefold()
        for qa_path in qa_paths
        for path in _expand_path(qa_path, recursive)
    }
    files: list[Path] = []
    for source_path in (*tuple(source_paths), *tuple(qa_paths)):
        files.extend(_expand_path(source_path, recursive))

    snapshots: list[FileSnapshot] = []
    for path in _deduplicate_paths(files):
        key = str(path.resolve()).casefold()
        snapshots.append(
            FileSnapshot(
                path=path,
                fingerprint=file_fingerprint(path),
                force_qa=key in qa_file_keys,
                role="qa" if key in qa_file_keys else source_role,
            )
        )
    return tuple(snapshots)


def file_fingerprint(path: Path) -> str:
    stat = path.stat()
    payload = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_document(snapshot: FileSnapshot) -> ParsedDocument:
    path = snapshot.path
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="ignore")
        sections = _sections_from_text(path, text, markdown=suffix == ".md")
        qa_pairs = extract_qa_pairs_from_text(path, text, force=snapshot.force_qa)
    elif suffix == ".docx":
        text = _read_docx(path)
        sections = _sections_from_text(path, text, markdown=False)
        qa_pairs = extract_qa_pairs_from_text(path, text, force=snapshot.force_qa)
    elif suffix in {".xlsx", ".xlsm", ".xls"}:
        sections, qa_pairs = _read_excel(path, force_qa=snapshot.force_qa)
    elif suffix == ".json":
        sections, qa_pairs = _read_json(path, force_qa=snapshot.force_qa)
    else:
        raise ValueError(f"Unsupported knowledge file: {path}")

    role = snapshot.role
    if role == "auto":
        role = "qa" if qa_pairs else "knowledge"
    if role == "qa":
        sections = []
    elif role == "knowledge":
        qa_pairs = []

    return ParsedDocument(
        path=str(path),
        title=path.stem,
        sections=tuple(section for section in sections if section.text.strip()),
        qa_pairs=tuple(qa_pairs),
        fingerprint=snapshot.fingerprint,
        role=role,
    )


def extract_qa_pairs_from_text(path: Path, text: str, force: bool = False) -> list[QAPair]:
    pairs: list[QAPair] = []
    block_pattern = re.compile(
        r"(?ms)^#{2,4}\s*(?P<id>Q\d{1,6})\s*$\s*"
        r"(?P<body>.*?)(?=^#{2,4}\s*Q\d{1,6}\s*$|\Z)"
    )
    for match in block_pattern.finditer(text):
        body = match.group("body")
        question = _match_labeled_value(body, ("问题", "question", "q"))
        answer = _match_labeled_value(body, ("答案", "answer", "a"), multiline=True)
        source = _match_labeled_value(body, ("来源", "source"), multiline=True)
        if question and answer:
            pairs.append(
                QAPair(
                    question=question,
                    answer=answer,
                    path=str(path),
                    locator=match.group("id"),
                    qa_id=match.group("id"),
                    source_text=source,
                )
            )

    if not pairs:
        generic_pattern = re.compile(
            r"(?ims)(?:^|\n)\s*(?:\*\*)?(?:问题|question|q)\s*[:：]"
            r"(?:\*\*)?\s*(?P<question>[^\n]+?)\s*\n+"
            r"\s*(?:\*\*)?(?:答案|answer|a)\s*[:：](?:\*\*)?\s*"
            r"(?P<answer>.*?)(?=\n\s*(?:\*\*)?(?:问题|question|q)\s*[:：]|\Z)"
        )
        for index, match in enumerate(generic_pattern.finditer(text), 1):
            question = _clean_inline(match.group("question"))
            answer = _clean_answer(match.group("answer"))
            if question and answer:
                pairs.append(
                    QAPair(
                        question=question,
                        answer=answer,
                        path=str(path),
                        locator=f"QA-{index}",
                    )
                )

    filename_suggests_qa = any(marker in path.stem.casefold() for marker in QA_FILENAME_MARKERS)
    if force or filename_suggests_qa or len(pairs) >= 2:
        return pairs
    return []


def _sections_from_text(path: Path, text: str, markdown: bool) -> list[ParsedSection]:
    if markdown:
        sections: list[ParsedSection] = []
        heading_pattern = re.compile(r"(?m)^(#{1,4})\s+(.+?)\s*$")
        matches = list(heading_pattern.finditer(text))
        if matches:
            for index, match in enumerate(matches):
                start = match.end()
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                body = text[start:end].strip()
                if body:
                    sections.append(
                        ParsedSection(
                            path=str(path),
                            title=path.stem,
                            locator=_clean_inline(match.group(2)),
                            text=body,
                        )
                    )
            return sections

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return [
        ParsedSection(
            path=str(path),
            title=path.stem,
            locator=f"section-{index}",
            text=paragraph,
        )
        for index, paragraph in enumerate(paragraphs, 1)
    ]


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_data = archive.read("word/document.xml")
    root = ET.fromstring(xml_data)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        value = "".join(
            node.text or ""
            for node in paragraph.iter(f"{namespace}t")
        ).strip()
        if value:
            paragraphs.append(value)
    return "\n\n".join(paragraphs)


def _read_excel(path: Path, force_qa: bool) -> tuple[list[ParsedSection], list[QAPair]]:
    if path.suffix.lower() == ".xls":
        rows_by_sheet = _read_xls_rows(path)
    else:
        rows_by_sheet = _read_xlsx_rows(path)

    sections: list[ParsedSection] = []
    qa_pairs: list[QAPair] = []
    for sheet_name, rows in rows_by_sheet:
        if not rows:
            continue
        headers = [_normalize_header(value) for value in rows[0]]
        question_index = _find_header(headers, {"question", "q", "问题", "题目"})
        answer_index = _find_header(headers, {"answer", "a", "答案", "回复"})
        id_index = _find_header(headers, {"id", "qid", "编号", "题号"})
        source_index = _find_header(headers, {"source", "来源", "出处"})

        if question_index is not None and answer_index is not None:
            for row_index, row in enumerate(rows[1:], 2):
                question = _cell_value(row, question_index)
                answer = _cell_value(row, answer_index)
                if not question or not answer:
                    continue
                qa_id = _cell_value(row, id_index) if id_index is not None else ""
                source = _cell_value(row, source_index) if source_index is not None else ""
                qa_pairs.append(
                    QAPair(
                        question=question,
                        answer=answer,
                        path=str(path),
                        locator=f"{sheet_name}!row-{row_index}",
                        qa_id=qa_id,
                        source_text=source,
                        metadata={"sheet": sheet_name, "row": row_index},
                    )
                )

        rendered_rows = ["\t".join(value for value in row if value) for row in rows]
        sheet_text = "\n".join(value for value in rendered_rows if value).strip()
        if sheet_text:
            sections.append(
                ParsedSection(
                    path=str(path),
                    title=path.stem,
                    locator=f"sheet:{sheet_name}",
                    text=sheet_text,
                    metadata={"sheet": sheet_name},
                )
            )

    filename_suggests_qa = any(marker in path.stem.casefold() for marker in QA_FILENAME_MARKERS)
    if not (force_qa or filename_suggests_qa or len(qa_pairs) >= 2):
        qa_pairs = []
    return sections, qa_pairs


def _read_xlsx_rows(path: Path) -> list[tuple[str, list[list[str]]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("Excel support requires openpyxl") from exc

    workbook = load_workbook(path, read_only=True, data_only=True)
    result: list[tuple[str, list[list[str]]]] = []
    try:
        for worksheet in workbook.worksheets:
            rows = [
                ["" if value is None else str(value).strip() for value in row]
                for row in worksheet.iter_rows(values_only=True)
            ]
            result.append((worksheet.title, rows))
    finally:
        workbook.close()
    return result


def _read_xls_rows(path: Path) -> list[tuple[str, list[list[str]]]]:
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError("Legacy .xls support requires xlrd") from exc

    workbook = xlrd.open_workbook(path)
    return [
        (
            sheet.name,
            [
                [str(sheet.cell_value(row, column)).strip() for column in range(sheet.ncols)]
                for row in range(sheet.nrows)
            ],
        )
        for sheet in workbook.sheets()
    ]


def _read_json(path: Path, force_qa: bool) -> tuple[list[ParsedSection], list[QAPair]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    qa_pairs = list(_extract_json_qa(path, data))
    sections: list[ParsedSection] = []
    if isinstance(data, dict):
        for key, value in data.items():
            lines = list(_flatten_json(value, f"$.{key}"))
            if lines:
                sections.append(
                    ParsedSection(
                        path=str(path),
                        title=path.stem,
                        locator=f"$.{key}",
                        text="\n".join(lines),
                    )
                )
    else:
        lines = list(_flatten_json(data, "$"))
        if lines:
            sections.append(
                ParsedSection(
                    path=str(path),
                    title=path.stem,
                    locator="$",
                    text="\n".join(lines),
                )
            )

    filename_suggests_qa = any(marker in path.stem.casefold() for marker in QA_FILENAME_MARKERS)
    if not (force_qa or filename_suggests_qa or len(qa_pairs) >= 2):
        qa_pairs = []
    return sections, qa_pairs


def _extract_json_qa(path: Path, value: Any, locator: str = "$") -> Iterator[QAPair]:
    if isinstance(value, dict):
        normalized = {_normalize_header(key): item for key, item in value.items()}
        question = _first_mapping_value(normalized, ("question", "q", "问题", "题目"))
        answer = _first_mapping_value(normalized, ("answer", "a", "答案", "回复"))
        if question is not None and answer is not None:
            qa_id = _first_mapping_value(normalized, ("id", "qid", "编号", "题号"))
            source = _first_mapping_value(normalized, ("source", "来源", "出处"))
            yield QAPair(
                question=str(question).strip(),
                answer=str(answer).strip(),
                path=str(path),
                locator=locator,
                qa_id="" if qa_id is None else str(qa_id).strip(),
                source_text="" if source is None else str(source).strip(),
            )
        for key, item in value.items():
            yield from _extract_json_qa(path, item, f"{locator}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _extract_json_qa(path, item, f"{locator}[{index}]")


def _flatten_json(value: Any, locator: str) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _flatten_json(item, f"{locator}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flatten_json(item, f"{locator}[{index}]")
    elif value is not None:
        yield f"{locator}: {value}"


def _expand_path(path: Path, recursive: bool) -> list[Path]:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        return []
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_SUFFIXES else []
    iterator = path.rglob("*") if recursive else path.glob("*")
    return [
        candidate
        for candidate in iterator
        if candidate.is_file()
        and candidate.suffix.lower() in SUPPORTED_SUFFIXES
        and not any(part.startswith(".") for part in candidate.relative_to(path).parts)
    ]


def _deduplicate_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    output: list[Path] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path).resolve()
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            output.append(path)
    return tuple(output)


def _match_labeled_value(body: str, labels: tuple[str, ...], multiline: bool = False) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    if multiline:
        pattern = re.compile(
            rf"(?ims)(?:\*\*)?(?:{label_pattern})\s*[:：](?:\*\*)?\s*"
            rf"(?P<value>.*?)(?=\n\s*(?:\*\*)?(?:问题|question|q|答案|answer|a|来源|source)\s*[:：]|\Z)"
        )
    else:
        pattern = re.compile(
            rf"(?im)(?:\*\*)?(?:{label_pattern})\s*[:：](?:\*\*)?\s*(?P<value>[^\n]+)"
        )
    match = pattern.search(body)
    if not match:
        return ""
    return _clean_answer(match.group("value")) if multiline else _clean_inline(match.group("value"))


def _clean_inline(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().strip("*#- ")


def _clean_answer(value: str) -> str:
    value = re.split(r"(?m)^\s*#{1,6}\s+", value, maxsplit=1)[0]
    value = re.split(
        r"(?im)^\s*(?:\*\*)?(?:来源|source)\s*[:：]",
        value,
        maxsplit=1,
    )[0]
    return re.sub(r"\s+", " ", value).strip().strip("*#- ")


def _normalize_header(value: Any) -> str:
    return re.sub(r"[\s_\-:：]+", "", str(value or "").strip().casefold())


def _find_header(headers: list[str], candidates: set[str]) -> int | None:
    normalized_candidates = {_normalize_header(candidate) for candidate in candidates}
    for index, header in enumerate(headers):
        if header in normalized_candidates:
            return index
    return None


def _cell_value(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _first_mapping_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        normalized_key = _normalize_header(key)
        if normalized_key in mapping:
            return mapping[normalized_key]
    return None


# =====================================================================
# retrieval.py
# =====================================================================

QUESTION_FILLERS = (
    "介绍一下",
    "可不可以",
    "能不能",
    "是不是",
    "是否",
    "请问",
    "告诉我",
    "谈谈",
    "为什么",
    "怎么样",
    "怎样",
    "怎么",
    "如何",
    "有什么",
    "是什么",
    "什么",
    "哪些",
    "能否",
    "可以吗",
    "吗",
)

CANONICAL_PHRASES = (
    ("联合国教科文组织", "联合国"),
    ("非物质文化遗产", "非遗"),
    ("入选", "列入"),
    ("什么时候", "何时"),
    ("什么时间", "何时"),
    ("哪一年", "何时"),
    ("哪年", "何时"),
    ("何年", "何时"),
    ("在哪儿", "在哪里"),
    ("什么地方", "哪里"),
    ("位于何处", "在哪里"),
)

GENERIC_QUERY_PHRASES = (
    "时间",
    "年份",
    "日期",
    "特点",
    "特征",
    "特色",
    "历史",
    "介绍",
    "资料",
    "信息",
    "来源",
    "名单",
    "人物",
    "方法",
    "步骤",
    "演出时间",
)


def normalize_for_match(text: str) -> str:
    normalized = _canonicalize(text)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def tokenize(text: str) -> list[str]:
    normalized = _canonicalize(text)
    for filler in QUESTION_FILLERS:
        normalized = normalized.replace(filler, " ")

    output: list[str] = re.findall(r"[a-z0-9]+", normalized)
    for segment in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(segment) == 1:
            output.append(segment)
            continue
        if len(segment) <= 8:
            output.append(segment)
        for width in range(2, min(4, len(segment)) + 1):
            output.extend(segment[index:index + width] for index in range(len(segment) - width + 1))

    seen: set[str] = set()
    return [token for token in output if token and not (token in seen or seen.add(token))]


def _canonicalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    for source, target in CANONICAL_PHRASES:
        normalized = normalized.replace(source, target)
    return normalized


@dataclass(frozen=True)
class RankedItem:
    index: int
    score: float
    confidence: float
    coverage: float


class BM25Index:
    def __init__(self, texts: Sequence[str]):
        self.texts = tuple(texts)
        self.documents = tuple(tuple(tokenize(text)) for text in texts)
        self.term_counts = tuple(Counter(document) for document in self.documents)
        self.document_lengths = tuple(len(document) for document in self.documents)
        self.average_length = (
            sum(self.document_lengths) / len(self.document_lengths)
            if self.document_lengths else 0.0
        )
        self.document_frequency: Counter[str] = Counter()
        for document in self.documents:
            self.document_frequency.update(set(document))

    def idf(self, token: str) -> float:
        count = len(self.documents)
        frequency = self.document_frequency.get(token, 0)
        return math.log(1.0 + (count - frequency + 0.5) / (frequency + 0.5)) if count else 0.0

    def search(self, query: str, limit: int = 5) -> list[RankedItem]:
        query_tokens = tokenize(query)
        if not query_tokens or not self.documents:
            return []

        query_weight = sum(self.idf(token) for token in query_tokens) or 1.0
        ranked: list[RankedItem] = []
        for index, term_count in enumerate(self.term_counts):
            score = 0.0
            matched_weight = 0.0
            document_length = self.document_lengths[index]
            for token in query_tokens:
                frequency = term_count.get(token, 0)
                if not frequency:
                    continue
                token_idf = self.idf(token)
                matched_weight += token_idf
                denominator = frequency + 1.5 * (
                    0.25 + 0.75 * document_length / max(1.0, self.average_length)
                )
                score += token_idf * (frequency * 2.5) / denominator

            if score <= 0:
                continue
            coverage = min(1.0, matched_weight / query_weight)
            confidence = min(1.0, 0.62 * coverage + 0.38 * (score / (score + 6.0)))
            ranked.append(RankedItem(index, score, confidence, coverage))

        ranked.sort(key=lambda item: (item.confidence, item.score), reverse=True)
        return ranked[:limit]


class KnowledgeRetriever:
    def __init__(self, chunks: Sequence[KnowledgeChunk], qa_pairs: Sequence[QAPair]):
        self.chunks = tuple(chunks)
        self.qa_pairs = tuple(qa_pairs)
        self.chunk_index = BM25Index(
            [f"{chunk.title}\n{chunk.locator}\n{chunk.text}" for chunk in self.chunks]
        )
        self.qa_index = BM25Index([pair.question for pair in self.qa_pairs])

    def match_qa(self, query: str, limit: int = 3) -> list[tuple[QAPair, float]]:
        match_query = _prepare_qa_query(query)
        normalized_query = normalize_for_match(match_query)
        if not normalized_query:
            return []

        exact = [
            (pair, 1.0)
            for pair in self.qa_pairs
            if normalize_for_match(pair.question) == normalized_query
        ]
        if exact:
            return exact[:limit]

        bm25_by_index = {
            item.index: item
            for item in self.qa_index.search(match_query, limit=max(20, limit * 5))
        }
        query_tokens = set(tokenize(match_query))
        query_intents = _question_intents(match_query)
        ranked: list[tuple[QAPair, float]] = []
        for index, pair in enumerate(self.qa_pairs):
            if not _supports_explicit_query_phrases(
                query,
                f"{pair.question}\n{pair.answer}",
            ):
                continue
            normalized_question = normalize_for_match(pair.question)
            sequence_ratio = SequenceMatcher(None, normalized_query, normalized_question).ratio()
            pair_tokens = set(tokenize(pair.question))
            union = query_tokens | pair_tokens
            token_similarity = len(query_tokens & pair_tokens) / len(union) if union else 0.0
            bm25_confidence = bm25_by_index.get(index).confidence if index in bm25_by_index else 0.0
            pair_intents = _question_intents(pair.question)
            shared_intents = query_intents & pair_intents
            intent_bonus = 0.0
            if shared_intents:
                intent_bonus = 0.16 if "scope" in shared_intents else 0.12
            intent_penalty = 0.0
            if query_intents and not shared_intents:
                intent_penalty += 0.12
            # “是什么/什么是/定义”“特点/特征/风格”等只是提问措辞或主题词，
            # 不构成额外限定，不应因此扣分（例如“……的特点是什么？”无需扣分）。
            non_limiting_intents = frozenset({"definition", "attribute"})
            extra_intents = (pair_intents - query_intents) - non_limiting_intents
            if extra_intents and query_intents:
                intent_penalty += 0.05
            confidence = (
                0.48 * sequence_ratio
                + 0.24 * token_similarity
                + 0.28 * bm25_confidence
                + intent_bonus
                - intent_penalty
            )
            confidence = max(0.0, min(1.0, confidence))
            if confidence > 0:
                ranked.append((pair, confidence))

        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:limit]

    def search_knowledge(
        self,
        query: str,
        limit: int = 4,
        min_coverage: float = 0.0,
    ) -> list[tuple[KnowledgeChunk, SourceRef]]:
        output: list[tuple[KnowledgeChunk, SourceRef]] = []
        for item in self.chunk_index.search(query, limit=limit):
            if item.coverage < min_coverage:
                continue
            chunk = self.chunks[item.index]
            if not _supports_explicit_query_phrases(
                query,
                f"{chunk.title}\n{chunk.locator}\n{chunk.text}",
            ):
                continue
            output.append(
                (
                    chunk,
                    SourceRef(
                        path=chunk.path,
                        title=chunk.title,
                        locator=chunk.locator,
                        kind=chunk.kind,
                        score=item.confidence,
                        excerpt=_excerpt(chunk.text, query),
                        qa_id=str(chunk.metadata.get("qa_id", "")),
                    ),
                )
            )
        return output


def build_chunks(
    sections: Iterable[object],
    qa_pairs: Iterable[QAPair],
    max_chars: int,
    overlap_chars: int,
) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for section in sections:
        for index, piece in enumerate(_split_text(section.text, max_chars, overlap_chars), 1):
            chunks.append(
                KnowledgeChunk(
                    chunk_id=f"{section.path}|{section.locator}|{index}",
                    text=piece,
                    path=section.path,
                    title=section.title,
                    locator=section.locator,
                    metadata=dict(section.metadata),
                )
            )
    for pair in qa_pairs:
        chunks.append(
            KnowledgeChunk(
                chunk_id=f"{pair.path}|{pair.locator}|qa",
                text=f"问题：{pair.question}\n答案：{pair.answer}",
                path=pair.path,
                title="问答库",
                locator=pair.locator,
                kind="qa",
                metadata={"qa_id": pair.qa_id, **pair.metadata},
            )
        )
    return chunks


def _split_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    clean = re.sub(r"\r\n?", "\n", text).strip()
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", clean) if part.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            start = 0
            step = max(1, max_chars - overlap_chars)
            while start < len(paragraph):
                pieces.append(paragraph[start:start + max_chars])
                start += step
            continue
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > max_chars:
            pieces.append(current)
            prefix = current[-overlap_chars:] if overlap_chars else ""
            current = f"{prefix}\n\n{paragraph}".strip()
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _excerpt(text: str, query: str, max_chars: int = 320) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    matched = [token for token in tokenize(query) if token in clean.casefold()]
    index = min((clean.casefold().find(token) for token in matched), default=0)
    start = max(0, index - max_chars // 3)
    end = min(len(clean), start + max_chars)
    excerpt = clean[start:end]
    if start:
        excerpt = "..." + excerpt
    if end < len(clean):
        excerpt += "..."
    return excerpt


def _question_intents(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    patterns = {
        "time": ("何时", "什么时候", "哪一年", "哪年", "时间", "日期"),
        "list": ("有哪些", "有谁", "哪些人", "名单", "列出", "分别是"),
        "compare": ("区别", "不同", "差异", "相比", "对比"),
        "location": ("哪里", "在哪", "何处", "地点", "位于"),
        "reason": ("为什么", "原因", "为何"),
        "method": ("如何", "怎么", "怎样", "方法", "步骤"),
        "definition": ("是什么", "什么是", "定义", "指什么"),
        "attribute": ("特点", "特征", "特色", "风格", "造型"),
        "verification": ("史实", "真实", "可靠吗", "可靠", "确定", "能当作"),
        "scope": ("单独", "独立", "组成部分", "关系", "还是"),
    }
    return {
        intent
        for intent, markers in patterns.items()
        if any(marker in normalized for marker in markers)
    }


def _supports_explicit_query_phrases(query: str, candidate: str) -> bool:
    phrases = _explicit_query_phrases(query)
    if not phrases:
        return True
    normalized_candidate = normalize_for_match(candidate)
    return all(normalize_for_match(phrase) in normalized_candidate for phrase in phrases)


def _prepare_qa_query(query: str) -> str:
    prepared = unicodedata.normalize("NFKC", query or "").casefold()
    prepared = re.sub(
        r"(?:19|20)\d{2}年\s*(?:还是|或者|或)\s*(?:19|20)\d{2}年",
        "何时",
        prepared,
    )
    prepared = prepared.replace("到底是", "").replace("究竟是", "")
    return prepared


def _explicit_query_phrases(query: str) -> tuple[str, ...]:
    normalized = _canonicalize(query)
    parts = [
        part
        for part in re.split(r"[\s,，;；。！？!?、]+", normalized)
        if len(normalize_for_match(part)) >= 3
    ]
    if len(parts) < 2:
        return ()

    specific: list[str] = []
    for part in parts:
        clean = normalize_for_match(part)
        if any(clean == marker or (len(clean) <= 6 and marker in clean) for marker in GENERIC_QUERY_PHRASES):
            continue
        specific.append(part)
    return tuple(specific)


# =====================================================================
# generator.py
# =====================================================================

class AnswerGenerator(Protocol):
    mode: str

    def generate(self, query: str, retrieval: RetrievalResult) -> tuple[str, str]: ...


class GroundedGenerationError(RuntimeError):
    pass


class ExtractiveGenerator:
    """Generate deterministic answers without a language model."""

    mode = "extractive"

    def __init__(self, max_answer_chars: int = 100):
        self.max_answer_chars = max_answer_chars

    def generate(self, query: str, retrieval: RetrievalResult) -> tuple[str, str]:
        if not retrieval.has_evidence:
            raise GroundedGenerationError("Cannot generate without retrieved evidence")

        if retrieval.authoritative_answer:
            answer = _extract_answer(
                query,
                (retrieval.authoritative_answer,),
                self.max_answer_chars,
            )
        else:
            answer = _extract_answer(query, retrieval.contexts, self.max_answer_chars)
        if not answer:
            raise GroundedGenerationError("Retrieved evidence did not contain usable text")
        return answer, infer_intent(query)


def infer_intent(query: str) -> str:
    clean = re.sub(r"\s+", " ", query).strip().rstrip("？?。！!")
    if not clean:
        return "用户正在查询知识库内容"
    return trim_to_limit(f"用户正在询问：{clean}", 100)


def trim_to_limit(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    quote_pairs = (("\"", "\""), ("“", "”"), ("'", "'"), ("‘", "’"))
    if len(clean) >= 2 and any(
        clean.startswith(opening) and clean.endswith(closing)
        for opening, closing in quote_pairs
    ):
        clean = clean[1:-1].strip()
    if len(clean) <= limit:
        return clean
    trimmed = clean[:limit].rstrip("，,；;：: ")
    if trimmed and trimmed[-1] not in "。！？!?":
        trimmed = trimmed[:-1] + "。" if len(trimmed) == limit else trimmed + "。"
    return trimmed[:limit]


def _extract_answer(query: str, contexts: tuple[str, ...], limit: int) -> str:
    candidates: list[tuple[float, int, int, str]] = []
    query_tokens = set(tokenize(query))
    for context_index, context in enumerate(contexts):
        cleaned = _clean_evidence(context)
        sentences = _split_sentences(cleaned)
        for sentence_index, sentence in enumerate(sentences):
            sentence_tokens = set(tokenize(sentence))
            overlap = len(query_tokens & sentence_tokens)
            coverage = overlap / max(1, len(query_tokens))
            score = coverage + 0.18 / (context_index + 1) - sentence_index * 0.002
            if re.search(r"\d", sentence) and re.search(r"\d", query):
                score += 0.08
            candidates.append((score, context_index, sentence_index, sentence))

    if not candidates:
        return ""
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, context_index, sentence_index, best = candidates[0]
    selected = best

    cleaned_context = _clean_evidence(contexts[context_index])
    context_sentences = _split_sentences(cleaned_context)
    if sentence_index + 1 < len(context_sentences):
        next_sentence = context_sentences[sentence_index + 1]
        should_append = selected.endswith(("：", ":")) or len(selected) < limit // 2
        if should_append and len(selected) + len(next_sentence) <= limit:
            selected = f"{selected}{next_sentence}"

    return trim_to_limit(selected, limit)


def _clean_evidence(text: str) -> str:
    clean = re.sub(r"^.*?\n", "", text, count=1) if "\n" in text else text
    clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean)
    clean = re.sub(r"[*#>`]", "", clean)
    clean = re.sub(r"(?m)^\s*[-+]\s*", "", clean)
    return clean.strip()


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])|\n+", text)
    return [re.sub(r"\s+", " ", part).strip() for part in parts if part.strip()]


# =====================================================================
# engine.py
# =====================================================================

NO_INFORMATION_ANSWER = "未找到足够信息，无法根据当前知识库回答。"
NO_INFORMATION_INTENT = "用户询问的内容超出当前知识库范围"


class KnowledgeQAEngine:
    def __init__(
        self,
        config: KnowledgeQAConfig | None = None,
        generator: AnswerGenerator | None = None,
    ):
        self.config = config or KnowledgeQAConfig()
        self.generator = generator or ExtractiveGenerator(self.config.max_answer_chars)
        self.generation_mode = getattr(self.generator, "mode", "custom")
        self._documents: dict[str, ParsedDocument] = {}
        self._parse_errors: dict[str, str] = {}
        self._retriever = KnowledgeRetriever((), ())
        self._source_paths: tuple[Path, ...] = ()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._watcher: threading.Thread | None = None
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> bool:
        with self._lock:
            source_paths = self.config.source_paths or discover_source_paths(
                self.config.discovery_root
            )
            self._source_paths = tuple((*source_paths, *self.config.qa_paths))
            snapshots = collect_snapshots(
                source_paths,
                self.config.qa_paths,
                recursive=self.config.recursive,
                source_role="knowledge" if self.config.manifest_path else "auto",
            )
            snapshot_by_path = {str(snapshot.path): snapshot for snapshot in snapshots}
            changed = force or set(snapshot_by_path) != set(self._documents)

            for path_key in tuple(self._documents):
                if path_key not in snapshot_by_path:
                    del self._documents[path_key]
                    self._parse_errors.pop(path_key, None)
                    changed = True

            for path_key, snapshot in snapshot_by_path.items():
                existing = self._documents.get(path_key)
                if not force and existing and existing.fingerprint == snapshot.fingerprint:
                    continue
                try:
                    self._documents[path_key] = parse_document(snapshot)
                    self._parse_errors.pop(path_key, None)
                except Exception as exc:
                    self._documents.pop(path_key, None)
                    self._parse_errors[path_key] = str(exc)
                changed = True

            if changed:
                sections = [
                    section
                    for document in self._documents.values()
                    for section in document.sections
                ]
                qa_pairs = [
                    pair
                    for document in self._documents.values()
                    for pair in document.qa_pairs
                ]
                chunks = build_chunks(
                    sections,
                    qa_pairs,
                    max_chars=self.config.max_chunk_chars,
                    overlap_chars=self.config.chunk_overlap_chars,
                )
                self._retriever = KnowledgeRetriever(chunks, qa_pairs)
            return changed

    def start_watching(self) -> None:
        with self._lock:
            if self._watcher and self._watcher.is_alive():
                return
            self._stop_event.clear()
            self._watcher = threading.Thread(
                target=self._watch_loop,
                name="portable-knowledge-qa-watcher",
                daemon=True,
            )
            self._watcher.start()

    def stop_watching(self) -> None:
        self._stop_event.set()
        watcher = self._watcher
        if watcher and watcher.is_alive():
            watcher.join(timeout=max(1.0, self.config.watch_interval_seconds + 0.5))

    def retrieve(self, query: str) -> RetrievalResult:
        clean_query = query.strip()
        if not clean_query:
            return RetrievalResult(clean_query, "none", 0.0, (), ())
        self.refresh()
        with self._lock:
            qa_matches = self._retriever.match_qa(clean_query)
            if qa_matches and qa_matches[0][1] >= self.config.qa_match_threshold:
                pair, confidence = qa_matches[0]
                source = SourceRef(
                    path=pair.path,
                    title=Path(pair.path).stem,
                    locator=pair.locator,
                    kind="qa",
                    score=confidence,
                    excerpt=pair.answer[:320],
                    qa_id=pair.qa_id,
                )
                context = (
                    f"标准问题：{pair.question}\n"
                    f"标准答案：{pair.answer}"
                )
                return RetrievalResult(
                    query=clean_query,
                    match_type="qa",
                    confidence=confidence,
                    contexts=(context,),
                    sources=(source,),
                    authoritative_answer=pair.answer,
                )

            ranked = self._retriever.search_knowledge(
                clean_query,
                self.config.top_k,
                min_coverage=self.config.knowledge_min_coverage,
            )
            if not ranked or ranked[0][1].score < self.config.knowledge_match_threshold:
                return RetrievalResult(clean_query, "none", 0.0, (), ())

            selected = [
                (chunk, source)
                for chunk, source in ranked
                if source.score >= self.config.knowledge_match_threshold * 0.8
            ]
            contexts = tuple(
                f"{chunk.title} / {chunk.locator}\n{chunk.text}"
                for chunk, _ in selected
            )
            sources = tuple(source for _, source in selected)
            return RetrievalResult(
                query=clean_query,
                match_type="knowledge",
                confidence=sources[0].score if sources else 0.0,
                contexts=contexts,
                sources=sources,
            )

    def query(self, query: str) -> QueryResult:
        retrieval = self.retrieve(query)
        if not retrieval.has_evidence:
            return QueryResult(
                query=query.strip(),
                answer=NO_INFORMATION_ANSWER,
                intent=NO_INFORMATION_INTENT,
                styled_text=f"{NO_INFORMATION_ANSWER}</STYLE>{NO_INFORMATION_INTENT}</STYLE>",
                match_type="none",
                confidence=0.0,
                source_count=0,
                sources=(),
                generation_mode=self.generation_mode,
                grounded=True,
                generation_status="no_evidence",
            )

        try:
            answer, intent = self.generator.generate(query.strip(), retrieval)
            status = "extractive" if self.generation_mode == "extractive" else "generated"
            generation_error = ""
        except GroundedGenerationError as exc:
            answer = self._fallback_answer(retrieval)
            intent = trim_to_limit(f"用户正在询问：{query.strip()}", 100)
            status = "evidence_fallback"
            generation_error = str(exc)

        answer = trim_to_limit(answer, self.config.max_answer_chars)
        intent = trim_to_limit(intent, 100)
        return QueryResult(
            query=query.strip(),
            answer=answer,
            intent=intent,
            styled_text=f"{answer}</STYLE>{intent}</STYLE>",
            match_type=retrieval.match_type,
            confidence=retrieval.confidence,
            source_count=len(retrieval.sources),
            sources=retrieval.sources,
            generation_mode=self.generation_mode,
            grounded=True,
            generation_status=status,
            error=generation_error,
        )

    def health(self) -> dict[str, object]:
        self.refresh()
        with self._lock:
            return {
                "status": "ok" if self._documents else "empty",
                "documents": len(self._documents),
                "qa_pairs": len(self._retriever.qa_pairs),
                "chunks": len(self._retriever.chunks),
                "source_paths": [str(path) for path in self._source_paths],
                "source_manifest": (
                    str(self.config.manifest_path) if self.config.manifest_path else ""
                ),
                "parse_errors": dict(self._parse_errors),
                "generation_mode": self.generation_mode,
            }

    def list_documents(self) -> list[dict[str, object]]:
        self.refresh()
        with self._lock:
            return [
                {
                    "path": document.path,
                    "title": document.title,
                    "sections": len(document.sections),
                    "qa_pairs": len(document.qa_pairs),
                    "role": document.role,
                    "fingerprint": document.fingerprint,
                }
                for document in sorted(self._documents.values(), key=lambda item: item.path)
            ]

    def _fallback_answer(self, retrieval: RetrievalResult) -> str:
        if retrieval.authoritative_answer:
            return trim_to_limit(retrieval.authoritative_answer, self.config.max_answer_chars)
        context = retrieval.contexts[0] if retrieval.contexts else NO_INFORMATION_ANSWER
        context = re.sub(r"^.*?\n", "", context, count=1).strip()
        sentence = re.split(r"(?<=[。！？!?])", context, maxsplit=1)[0].strip()
        return trim_to_limit(sentence or context, self.config.max_answer_chars)

    def _watch_loop(self) -> None:
        while not self._stop_event.wait(self.config.watch_interval_seconds):
            try:
                self.refresh()
            except Exception:
                continue

    def __enter__(self) -> "KnowledgeQAEngine":
        self.start_watching()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop_watching()


__all__ = [
    "KnowledgeQAConfig",
    "KnowledgeQAEngine",
    "QueryResult",
    "RetrievalResult",
    "SourceRef",
]