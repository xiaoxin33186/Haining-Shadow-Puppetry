# TRAE Integration Reference

## Contents

- Skill scope
- Command execution
- Retrieval contract
- Failure handling
- Import verification

## Skill Scope

Import the ZIP into the current TRAE project. The skill must use the current workspace as `--root` and must not store an absolute project path from the computer that created the ZIP.

## Command Execution

Resolve commands relative to the directory containing this skill's `SKILL.md`:

```powershell
python scripts/search_knowledge.py "用户原问题" --root "当前工作区绝对路径"
python scripts/inspect_knowledge.py --root "当前工作区绝对路径"
python scripts/check_runtime.py
python scripts/run_regression.py --root "当前工作区绝对路径" --cases evaluation\knowledge-regression.json
```

The scripts add their own skill directory to `sys.path`; editable installation is not required.

When the workspace contains `knowledge-sources.json`, inspect and search commands automatically honor its roles. Evaluation, template, and deprecated sources are excluded from production retrieval.

## Retrieval Contract

`search_knowledge.py` returns JSON with:

- `match_type`: `qa`, `knowledge`, or `none`.
- `confidence`: retrieval confidence.
- `evidence`: complete text supplied to TRAE for grounded synthesis.
- `authoritative_answer`: authoritative QA-bank answer when `match_type` is `qa`.
- `sources`: internal source metadata for explicit source follow-ups.

Use `authoritative_answer` without factual rewriting for QA matches. Use only `evidence` for knowledge matches. Never answer from model memory when `match_type` is `none`.

## Failure Handling

- If Python is unavailable or older than 3.10, report the requirement without installing it.
- If `openpyxl` is missing, `.xlsx` and `.xlsm` files cannot be parsed.
- If `xlrd` is missing, legacy `.xls` files cannot be parsed.
- If some files fail while others parse, answer only from successfully parsed evidence and disclose parser failures only when relevant.
- Do not fall back to PDF, OCR, web search, or model memory.

## Import Verification

After importing, verify these cases:

1. A known QA-bank question returns the authoritative answer.
2. A general project-knowledge question returns grounded evidence.
3. A fabricated fact returns the fixed no-information response.
4. A source follow-up displays source metadata.
5. A code-edit request does not trigger this skill.
