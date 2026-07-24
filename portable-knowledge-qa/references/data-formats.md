# Data Format Reference

## Contents

- Supported formats
- Source manifest and roles
- Knowledge discovery
- QA auto-detection
- Locator metadata
- Unsupported inputs

## Supported Formats

- Markdown: `.md`
- Plain UTF-8 text: `.txt`
- Word Open XML: `.docx`
- Excel: `.xlsx`, `.xlsm`, and legacy `.xls`
- JSON: `.json`

The package does not support PDF or OCR.

## Source Manifest and Roles

Place `knowledge-sources.json` in the workspace root to control production indexing:

```json
{
  "version": 1,
  "sources": [
    {"path": "local_kb", "role": "knowledge"},
    {"path": "question_bank/answers.md", "role": "qa"},
    {"path": "question_bank/questions.md", "role": "evaluation"}
  ]
}
```

Supported roles are `knowledge`, `qa`, `evaluation`, `template`, and `deprecated`. Only `knowledge` and `qa` are loaded. QA-role files produce QA pairs but no ordinary knowledge sections, preventing duplicate indexing.

## Knowledge Discovery

Explicit `--source` and `--qa-source` paths take priority. Otherwise, `knowledge-sources.json` takes priority. Without either form of configuration, discovery checks the current directory and up to three parents for these directory names:

`local_kb`, `local_kb_extended`, `question_bank`, `knowledge`, `knowledge_base`, `kb`, `faq`, `qa`, `知识库`, `问答库`.

When no conventional directory exists, supported files directly inside the discovery root are considered. Hidden directories are ignored.

## QA Auto-Detection

A file is treated as a QA source when one of these conditions is true:

- It is explicitly passed through `--qa-source` or `qa_paths`.
- Its filename contains a marker such as `题库`, `问答`, `答案`, `faq`, `question`, `answer`, or `qa`.
- The parser finds at least two valid question-answer pairs.

### Markdown, Text, and DOCX

Recognized examples:

```markdown
### Q001
**问题：** 什么是示例？

**答案：** 这是标准答案。

**来源：** 示例来源
```

```text
问题：什么是示例？
答案：这是标准答案。
```

### Excel

Use a header row containing question and answer columns. Accepted names include:

- Question: `question`, `q`, `问题`, `题目`
- Answer: `answer`, `a`, `答案`, `回复`
- Optional ID: `id`, `qid`, `编号`, `题号`
- Optional source: `source`, `来源`, `出处`

Each worksheet is inspected independently.

### JSON

Question-answer objects may appear at any nesting level:

```json
{
  "items": [
    {
      "id": "Q001",
      "question": "什么是示例？",
      "answer": "这是标准答案。",
      "source": "示例来源"
    }
  ]
}
```

Chinese key names are also accepted.

## Locator Metadata

The engine retains location information for later source follow-ups:

- Markdown: heading text or QA ID.
- Text and DOCX: generated section number.
- Excel: worksheet and row number.
- JSON: JSON path such as `$.items[0]`.

Source metadata is not shown in normal answers. Request it explicitly with `include_sources`.

## Unsupported Inputs

- PDF files
- Scanned images and OCR
- Password-protected Office documents
- Binary formats other than legacy `.xls`
