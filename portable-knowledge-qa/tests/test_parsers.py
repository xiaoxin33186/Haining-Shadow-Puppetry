from __future__ import annotations

import json
from pathlib import Path
import zipfile

from openpyxl import Workbook

from portable_knowledge_qa.parsers import FileSnapshot, file_fingerprint, parse_document


def snapshot(path: Path, force_qa: bool = False, role: str = "auto") -> FileSnapshot:
    return FileSnapshot(path, file_fingerprint(path), force_qa=force_qa, role=role)


def test_markdown_qa_parser(tmp_path: Path) -> None:
    path = tmp_path / "题库答案.md"
    path.write_text(
        "### Q001\n**问题：** 示例问题？\n\n**答案：** 示例答案。\n\n**来源：** 示例来源\n",
        encoding="utf-8",
    )
    document = parse_document(snapshot(path))
    assert len(document.qa_pairs) == 1
    assert document.qa_pairs[0].qa_id == "Q001"
    assert document.qa_pairs[0].answer == "示例答案。"


def test_docx_text_parser(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>第一段知识</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>第二段知识</w:t></w:r></w:p></w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    document = parse_document(snapshot(path))
    assert any("第一段知识" in section.text for section in document.sections)


def test_excel_qa_parser(tmp_path: Path) -> None:
    path = tmp_path / "faq.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "FAQ"
    sheet.append(["编号", "问题", "答案", "来源"])
    sheet.append(["Q001", "示例问题？", "示例答案。", "示例来源"])
    workbook.save(path)
    workbook.close()

    document = parse_document(snapshot(path, force_qa=True))
    assert len(document.qa_pairs) == 1
    assert document.qa_pairs[0].locator == "FAQ!row-2"


def test_nested_json_qa_parser(tmp_path: Path) -> None:
    path = tmp_path / "faq.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "Q001", "问题": "示例问题？", "答案": "示例答案。"},
                    {"id": "Q002", "问题": "第二问题？", "答案": "第二答案。"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    document = parse_document(snapshot(path))
    assert [pair.qa_id for pair in document.qa_pairs] == ["Q001", "Q002"]


def test_explicit_qa_role_removes_duplicate_sections(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    path.write_text(
        json.dumps(
            [{"question": "示例问题？", "answer": "示例答案。"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    document = parse_document(snapshot(path, force_qa=True, role="qa"))

    assert document.role == "qa"
    assert document.qa_pairs
    assert document.sections == ()


def test_qa_source_stops_before_next_topic_heading(tmp_path: Path) -> None:
    path = tmp_path / "answers.md"
    path.write_text(
        "### Q001\n**问题：** 示例问题？\n\n**答案：** 示例答案。\n\n"
        "**来源：** [示例](https://example.com)\n\n## 下一主题\n",
        encoding="utf-8",
    )

    document = parse_document(snapshot(path, force_qa=True, role="qa"))

    assert document.qa_pairs[0].source_text == "[示例](https://example.com)"
