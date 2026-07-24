from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator
import xml.etree.ElementTree as ET

from .config import SUPPORTED_SUFFIXES
from .models import ParsedDocument, ParsedSection, QAPair


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
