# -*- coding: utf-8 -*-
"""海宁皮影戏 500 题基准评测。"""
import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import sys
import time
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import chatchat_config as config

QUESTION_BANK = ROOT / "knowledge_base" / "海宁皮影戏题库.md"
ANSWER_BANK = ROOT / "knowledge_base" / "海宁皮影戏题库答案.md"
GEN_QUESTIONS_JSON = ROOT / "eval_questions_200.json"
GEN_QUESTIONS_MD = ROOT / "eval_questions_200.md"
GEN_EVIDENCE_JSON = ROOT / "eval_generated_evidence.json"
BENCHMARK_JSON = ROOT / "eval_benchmark_500.json"
RESULTS_JSONL = ROOT / "eval_results_v4.jsonl"
SOURCE_MANIFEST = ROOT / "knowledge-sources.json"

BANK_SAMPLE_N = 300
GEN_TOTAL = 200
GEN_PER_BATCH = 25
MANIFEST_KNOWLEDGE_COUNT = 9
EVIDENCE_QUESTION_BATCH_SIZE = 4
EVIDENCE_DOCUMENT_BATCH_CHARS = 6000
RANDOM_SEED = 20260802
SCHEMA_VERSION = "4.0"
FACT_WEIGHT = 0.45
ENTITY_WEIGHT = 0.25
JUDGE_WEIGHT = 0.30
CONFLICT_THRESHOLD = 0.4
GEN_EVIDENCE_SCHEMA_VERSION = "4.0"
GEN_EVIDENCE_META_KEY = "_meta"
# 答案-证据落地校验：文本相似度（含二元组加分）与实体覆盖双信号，二者都低才判定无据
ANSWER_EVIDENCE_SIM_MIN = 0.40
ANSWER_EVIDENCE_ENTITY_MIN = 0.50
# 本地候选段落的相关性下限：过低会漏掉关键段落（如“弋阳腔/海盐腔起源”），过高则噪声过多
CANDIDATE_RELEVANCE_MIN = 0.10
# 模型调用网络/服务异常重试退避上限（秒）
MODEL_CALL_BACKOFF = 10.0
RETRIEVAL_WEIGHTS = {"qa": 1.0, "knowledge": 0.7}
CLASSIFICATIONS = (
    "correct",
    "partial",
    "incorrect",
    "correct_rejection",
    "hallucination",
    "manual_review",
    "error",
)
OLLAMA_READY_URL = "http://127.0.0.1:11434/api/tags"
ACTION_KEYWORDS = ["跳舞", "舞蹈", "行礼", "拱手礼", "跳跃", "抓耳挠腮", "向前走", "向后走"]
NO_ANSWER_KEYWORDS = [
    "未找到", "无法回答", "暂无法回答", "暂时无法回答", "找不到", "没有相关",
    "没有找到", "不太清楚", "不知道", "无法根据", "知识范围", "超出", "暂未收录",
]


def check_ollama() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_READY_URL, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def parse_bank(path: Path = QUESTION_BANK) -> list:
    items = []
    pattern = re.compile(r"^- \*\*(Q\d+)\*\*\s*(.+)$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            items.append((match.group(1), match.group(2).strip()))
    items.sort(key=lambda item: int(item[0][1:]))
    return items


def parse_answer_bank(path: Path = ANSWER_BANK) -> dict:
    answers = {}
    current_qid = None
    answer_lines = []
    source = None

    def save_current():
        if current_qid and answer_lines:
            answers[current_qid] = {
                "reference_answer": "\n".join(answer_lines).strip(),
                "source": source,
            }

    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^###\s+(Q\d+)\s*$", line.strip())
        if heading:
            save_current()
            current_qid = heading.group(1)
            answer_lines = []
            source = None
            continue
        if not current_qid:
            continue
        if line.startswith("**答案：**"):
            answer_lines = [line.split("**答案：**", 1)[1].strip()]
            continue
        if line.startswith("**来源：**"):
            raw_source = line.split("**来源：**", 1)[1].strip()
            links = re.findall(r"\[[^]]*]\((https?://[^)]+)\)", raw_source)
            plain = re.findall(r"https?://\S+", raw_source)
            sources = links or [url.rstrip("。)") for url in plain]
            source = sources if len(sources) > 1 else sources[0] if sources else raw_source or None
            continue
        if source is not None or re.match(r"^\[[^]]+]:\s*\S+", line.strip()):
            continue
        if answer_lines and line.strip() and not line.startswith("**问题：**") and not line.startswith("---"):
            answer_lines.append(line.strip())
    save_current()
    return answers


def sample_bank(bank: list, n: int = BANK_SAMPLE_N, random_seed: int = RANDOM_SEED) -> list:
    if n > len(bank):
        raise ValueError(f"题库仅有 {len(bank)} 题，无法抽样 {n} 题")
    chosen = random.Random(random_seed).sample(bank, n)
    chosen.sort(key=lambda item: int(item[0][1:]))
    return chosen


def split_key_facts(reference_answer: str) -> list:
    facts = []
    for part in re.split(r"[。！？；;\n]+", str(reference_answer or "")):
        fact = part.strip(" ，,：:")
        if fact and fact not in facts:
            facts.append(fact)
    return facts


def build_benchmark(
    question_path: Path = QUESTION_BANK,
    answer_path: Path = ANSWER_BANK,
    generated_questions: list = None,
    bank_sample_n: int = BANK_SAMPLE_N,
    random_seed: int = RANDOM_SEED,
    generated_evidence: dict = None,
) -> dict:
    bank = sample_bank(parse_bank(question_path), bank_sample_n, random_seed)
    answers = parse_answer_bank(answer_path)
    missing = [qid for qid, _ in bank if qid not in answers]
    if missing:
        raise ValueError(f"标准答案缺失：{', '.join(missing)}")
    generated_questions = list(generated_questions or [])
    generated_evidence = generated_evidence or {}
    invalid_generated = []
    for question in generated_questions:
        baseline = generated_evidence.get(question)
        if not baseline or "answerable" not in baseline:
            invalid_generated.append(question)
            continue
        if baseline["answerable"] and (
            not baseline.get("reference_answer")
            or not baseline.get("evidence")
            or not (baseline.get("key_facts") or split_key_facts(baseline.get("reference_answer")))
        ):
            invalid_generated.append(question)
        if not baseline["answerable"] and not baseline.get("unanswerable_reason"):
            invalid_generated.append(question)
    if invalid_generated:
        raise ValueError(f"生成题基准缺失：{', '.join(invalid_generated)}")
    items = [
        {
            "qid": qid,
            "category": "题库",
            "question": question,
            "answerable": True,
            "reference_answer": answers[qid]["reference_answer"],
            "key_facts": split_key_facts(answers[qid]["reference_answer"]),
            "source": answers[qid]["source"],
            "evidence": [answers[qid]["reference_answer"]],
            "unanswerable_reason": "",
        }
        for qid, question in bank
    ]
    items.extend(
        {
            "qid": f"G{index:03d}",
            "category": "生成",
            "question": question,
            "answerable": bool(generated_evidence[question]["answerable"]),
            "reference_answer": generated_evidence[question].get("reference_answer"),
            "key_facts": generated_evidence[question].get("key_facts") or split_key_facts(
                generated_evidence[question].get("reference_answer")
            ),
            "source": generated_evidence[question].get("source") or [],
            "evidence": generated_evidence[question].get("evidence") or [],
            "unanswerable_reason": generated_evidence[question].get("unanswerable_reason") or "",
        }
        for index, question in enumerate(generated_questions, 1)
    )
    generated_evidence_count = sum(
        1 for question in generated_questions if generated_evidence[question]["answerable"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "total": len(items),
            "bank_count": len(bank),
            "generated_count": len(generated_questions),
            "generated_evidence_count": generated_evidence_count,
            "unanswerable_count": len(generated_questions) - generated_evidence_count,
            "random_seed": random_seed,
        },
        "items": items,
    }


def save_benchmark(benchmark: dict, path: Path = BENCHMARK_JSON):
    _atomic_write_text(path, json.dumps(benchmark, ensure_ascii=False, indent=2))


def load_benchmark(path: Path = BENCHMARK_JSON) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def benchmark_fingerprint(benchmark: dict) -> str:
    payload = json.dumps(benchmark, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_benchmark(benchmark: dict, expected_total: int = BANK_SAMPLE_N + GEN_TOTAL) -> None:
    if benchmark.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"基准版本必须为 {SCHEMA_VERSION}")
    items = benchmark.get("items")
    if not isinstance(items, list) or len(items) != expected_total:
        raise ValueError(f"评测基准必须完整包含 {expected_total} 题")
    metadata = benchmark.get("metadata") or {}
    if metadata.get("total") != expected_total:
        raise ValueError(f"评测基准 metadata.total 必须为 {expected_total}")
    if expected_total == BANK_SAMPLE_N + GEN_TOTAL and (
        metadata.get("bank_count") != BANK_SAMPLE_N
        or metadata.get("generated_count") != GEN_TOTAL
    ):
        raise ValueError(f"评测基准必须由 {BANK_SAMPLE_N} 道题库题和 {GEN_TOTAL} 道生成题组成")
    qids = [item.get("qid") for item in items]
    if any(not qid for qid in qids) or len(set(qids)) != expected_total:
        raise ValueError("评测基准 qid 必须完整且唯一")
    for item in items:
        if not item.get("question") or "answerable" not in item:
            raise ValueError(f"评测基准题目不完整：{item.get('qid') or '未知编号'}")
        if item["answerable"] and (
            not item.get("reference_answer")
            or not (item.get("key_facts") or split_key_facts(item.get("reference_answer")))
            or not item.get("evidence")
        ):
            raise ValueError(f"可回答题基准不完整：{item['qid']}")
        if not item["answerable"] and not item.get("unanswerable_reason"):
            raise ValueError(f"不可回答题缺少原因：{item['qid']}")


def _read_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_data = archive.read("word/document.xml")
    root = ET.fromstring(xml_data)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        value = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
        if value:
            paragraphs.append(value)
    return "\n\n".join(paragraphs)


def _read_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except ImportError:
        try:
            import fitz

            with fitz.open(path) as document:
                return "\n\n".join(page.get_text("text").strip() for page in document).strip()
        except ImportError as exc:
            raise RuntimeError("读取 PDF 需要安装 pypdf 或 PyMuPDF") from exc


def load_manifest_documents(
    manifest_path: Path = SOURCE_MANIFEST,
    expected_count: int = MANIFEST_KNOWLEDGE_COUNT,
) -> list:
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    entries = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("manifest.sources 必须是数组")
    documents = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"manifest 第 {index + 1} 项必须是对象")
        if str(entry.get("role") or "").strip().casefold() != "knowledge":
            continue
        raw_path = str(entry.get("path") or "").strip()
        if not raw_path:
            raise ValueError(f"manifest 第 {index + 1} 项缺少 path")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = manifest_path.parent / path
        if not path.is_file():
            raise FileNotFoundError(f"manifest 知识文档不存在：{path}")
        suffix = path.suffix.lower()
        if suffix in {".md", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
        elif suffix == ".docx":
            text = _read_docx_text(path)
        elif suffix == ".pdf":
            text = _read_pdf_text(path)
        else:
            raise ValueError(f"评测脚本不支持的 manifest 知识文档：{path.name}")
        text = text.strip()
        if not text:
            raise ValueError(f"manifest 知识文档内容为空：{path.name}")
        documents.append({
            "path": raw_path.replace("\\", "/"),
            "title": path.stem,
            "suffix": suffix,
            "text": text,
        })
    if expected_count and len(documents) != expected_count:
        raise ValueError(f"manifest 必须恰好包含 {expected_count} 篇 role=knowledge 文档，实际 {len(documents)} 篇")
    return documents


def load_doc_texts() -> list:
    return [(document["title"], document["text"]) for document in load_manifest_documents()]


def gen_questions_batch(client, doc_title, doc_text, n=GEN_PER_BATCH) -> list:
    response = client.chat.completions.create(
        model=config.ACTION_MODEL,
        messages=[
            {
                "role": "system",
                "content": "你是知识评测集制作助手，只能依据给定资料设计海宁皮影戏评测问题。",
            },
            {
                "role": "user",
                "content": (
                    f"资料标题：{doc_title}\n\n资料内容：\n{doc_text[:2000]}\n\n"
                    f"生成 {n} 个完整问句，每行格式为‘1. 问题’，覆盖历史、工艺、表演、声腔、"
                    "剧目、传承、保护、活动、数字化和横向比较；不要引用上文，不要生成动作指令。"
                ),
            },
        ],
        temperature=0.8,
        timeout=300,
    )
    content = response.choices[0].message.content or ""
    questions = []
    pattern = re.compile(r"^\s*(?:\d+[.、）]\s*|\-\s*)(.+)$")
    for line in content.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        question = re.sub(r"^[（(]\d+[)）]\s*", "", match.group(1).strip().strip("。；;，,"))
        if 6 <= len(question) <= 100 and not any(keyword in question for keyword in ACTION_KEYWORDS):
            questions.append(question)
    return questions


def gen_questions() -> list:
    if not check_ollama():
        print("[gen] 错误：Ollama 未在线，请先启动 Ollama。")
        raise SystemExit(1)
    from openai import OpenAI

    client = OpenAI(base_url=config.OLLAMA_BASE_URL, api_key=config.OLLAMA_API_KEY)
    docs = load_doc_texts()
    if not docs:
        print("[gen] 错误：未找到可读取的知识文档。")
        raise SystemExit(1)
    questions = []
    seen = set()
    batch_index = 0
    fail_times = 0
    while len(questions) < GEN_TOTAL and batch_index < 40:
        title, text = docs[batch_index % len(docs)]
        try:
            batch = gen_questions_batch(client, title, text)
        except Exception as exc:
            fail_times += 1
            print(f"[gen] 第 {batch_index + 1} 批失败（{fail_times} 次）：{exc}")
            if fail_times >= 3:
                break
            time.sleep(3)
            continue
        for question in batch:
            if question not in seen:
                seen.add(question)
                questions.append(question)
        batch_index += 1
        print(f"[gen] 第 {batch_index} 批解析 {len(batch)} 条，累计 {len(questions)}/{GEN_TOTAL}")
        time.sleep(1)
    questions = questions[:GEN_TOTAL]
    _atomic_write_text(GEN_QUESTIONS_JSON, json.dumps(questions, ensure_ascii=False, indent=2))
    _atomic_write_text(
        GEN_QUESTIONS_MD,
        "\n".join(["# 评测生成问题（Ollama 生成 200 条）", ""] + [f"{i}. {q}" for i, q in enumerate(questions, 1)]),
    )
    print(f"[gen] 完成：生成 {len(questions)} 条")
    return questions


def load_gen_questions() -> list:
    if not GEN_QUESTIONS_JSON.exists():
        return gen_questions()
    return json.loads(GEN_QUESTIONS_JSON.read_text(encoding="utf-8"))


def load_generated_evidence(path: Path = GEN_EVIDENCE_JSON) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_json_object(content: str, error_message: str) -> dict:
    """从模型输出中提取 JSON 对象。

    优先整段解析；失败则用 raw_decode 逐对象扫描，正确处理多个对象、
    前后解释文本和内部花括号，替代贪婪正则。
    """
    text = str(content or "").strip()
    decoder = json.JSONDecoder()
    try:
        payload, _ = decoder.raw_decode(text)
        if isinstance(payload, dict):
            return payload
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    start = text.find("{")
    while start != -1:
        try:
            payload, _ = decoder.raw_decode(text[start:])
            if isinstance(payload, dict):
                return payload
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        start = text.find("{", start + 1)
    raise ValueError(error_message)


def _atomic_write_text(path: Path, content: str) -> None:
    """临时文件 + os.replace 原子替换，避免进程中断留下截断文件。"""
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    os.replace(temp_path, path)


def _truncate_model_response(content: str, max_chars: int = 500) -> str:
    normalized = str(content or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars] + "...<已截断>"


def _request_validated_json(client, model: str, messages: list, label: str, validate, max_attempts: int) -> dict:
    last_error = None
    last_content = ""
    request_messages = messages
    for attempt in range(max_attempts):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=request_messages,
                response_format={"type": "json_object"},
                temperature=0,
                timeout=300,
            )
            last_content = response.choices[0].message.content or ""
            payload = _extract_json_object(last_content, f"{label}未返回JSON对象")
            return validate(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < max_attempts:
                request_messages = request_messages + [
                    {"role": "assistant", "content": last_content},
                    {"role": "user", "content": f"上一条结果无效：{exc}。请只返回满足逐题字段和类型要求的JSON对象。"},
                ]
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max_attempts:
                time.sleep(min(2.0 * (attempt + 1), MODEL_CALL_BACKOFF))
    raw = _truncate_model_response(last_content)
    raise ValueError(f"{label}JSON校验失败，已重试 {max_attempts} 次：{last_error}；原始响应：{raw}")


def _batched(items: list, size: int):
    if size <= 0:
        raise ValueError("批大小必须大于0")
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _document_batches(documents: list, max_chars: int) -> list:
    if max_chars <= 0:
        raise ValueError("文档批字符数必须大于0")
    fragments = []
    for document in documents:
        text = document["text"]
        for start in range(0, len(text), max_chars):
            fragment = dict(document)
            fragment["text"] = text[start:start + max_chars]
            fragments.append(fragment)
    batches = []
    current = []
    current_chars = 0
    for document in fragments:
        text_chars = len(document["text"])
        if current and current_chars + text_chars > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(document)
        current_chars += text_chars
    if current:
        batches.append(current)
    return batches


def _split_source_paragraphs(text: str) -> list:
    return [
        paragraph.strip()
        for paragraph in re.split(r"(?:\r?\n[ \t]*){2,}", str(text or ""))
        if paragraph.strip()
    ]


def _find_source_paragraphs(clue: str, document_texts_by_source: dict) -> list:
    normalized_clue = re.sub(r"\s+", "", clue)
    if not normalized_clue:
        return []
    matches = []
    for source, texts in document_texts_by_source.items():
        for text in texts:
            for original_paragraph in _split_source_paragraphs(text):
                normalized_paragraph = re.sub(r"\s+", "", original_paragraph)
                if normalized_clue in normalized_paragraph:
                    matches.append({"source": source, "evidence": original_paragraph})
    return matches


def _extract_candidate_keywords(text: str) -> set:
    normalized = re.sub(r"[？?，,。；;：:！!、的了是在于和与及有何什么哪里何时如何]", " ", str(text or ""))
    keywords = set(extract_entities(normalized))
    for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]*", normalized):
        keywords.add(token.lower())
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", token):
            keywords.update(token[index:index + 4] for index in range(len(token) - 3))
    return keywords


def _candidate_relevance(question: str, paragraph: str, title: str = "") -> float:
    searchable = f"{title}\n{paragraph}"
    question_entities = extract_entities(question)
    searchable_entities = extract_entities(searchable)
    entity_score = (
        len(question_entities & searchable_entities) / len(question_entities)
        if question_entities else 0.0
    )
    question_keywords = _extract_candidate_keywords(question)
    normalized_searchable_text = searchable.lower()
    keyword_score = (
        sum(keyword.lower() in normalized_searchable_text for keyword in question_keywords)
        / len(question_keywords)
        if question_keywords else 0.0
    )
    normalized_question = re.sub(r"[^\w\u4e00-\u9fff]", "", question.lower())
    normalized_searchable = re.sub(r"[^\w\u4e00-\u9fff]", "", searchable.lower())
    character_score = SequenceMatcher(None, normalized_question, normalized_searchable).ratio()
    return entity_score * 0.45 + keyword_score * 0.35 + character_score * 0.20


def select_local_evidence_candidates(questions: list, documents: list, max_candidates: int = 5) -> dict:
    if max_candidates <= 0:
        raise ValueError("候选段落数必须大于0")
    paragraphs = []
    for document in documents:
        source = document["path"].strip().replace("\\", "/")
        title = str(document.get("title") or "")
        for paragraph in _split_source_paragraphs(document.get("text", "")):
            paragraphs.append((source, title, paragraph))
    selected = {}
    for question in questions:
        ranked = sorted(
            (
                (_candidate_relevance(question, paragraph, title), source, paragraph)
                for source, title, paragraph in paragraphs
            ),
            key=lambda item: (-item[0], item[1], item[2]),
        )
        selected[question] = [
            {"source": source, "evidence": paragraph}
            for score, source, paragraph in ranked[:max_candidates]
            if score >= CANDIDATE_RELEVANCE_MIN
        ]
    return selected


def screen_evidence_batch(client, model: str, questions: list, documents: list, max_attempts: int = 3) -> dict:
    question_payload = [
        {"id": f"Q{index}", "question": question}
        for index, question in enumerate(questions, 1)
    ]
    document_payload = [
        {"source": document["path"], "title": document["title"], "text": document["text"]}
        for document in documents
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "你是知识评测证据筛选器。只能从给定文档原文筛选直接回答问题的证据。"
                "只返回JSON对象，键必须是问题列表中的稳定ID（如Q1、Q2），值优先为数组；数组项可以是"
                "{\"source\":\"manifest路径\",\"evidence\":\"原文证据\"}，也可以是能在当前文档定位的原文字符串线索。"
                "字符串线索会扩展为线索所在的完整原文段落。问题原文仅用于理解问题，不得作为JSON键。"
                "没有直接证据时返回空数组，不得改写或补充文档之外的信息。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"问题列表：{json.dumps(question_payload, ensure_ascii=False)}\n"
                f"文档批次：{json.dumps(document_payload, ensure_ascii=False)}\n"
                "请逐题筛选证据，并按稳定ID返回。"
            ),
        },
    ]
    document_texts_by_source = {}
    for document in documents:
        source = document["path"].strip().replace("\\", "/")
        document_texts_by_source.setdefault(source, []).append(document["text"])
    allowed_sources = set(document_texts_by_source)

    def validate(payload: dict) -> dict:
        if len(question_payload) == 1 and "source" in payload and (
            "text" in payload or "evidence" in payload
        ):
            evidence = payload.get("evidence", payload.get("text"))
            payload = {question_payload[0]["id"]: [{"source": payload["source"], "evidence": evidence}]}
        result = {}
        for question_item in question_payload:
            question_id = question_item["id"]
            question = question_item["question"]
            if question_id not in payload:
                raise ValueError(f"证据筛选缺少问题：{question_id}（{question}）")
            entries = payload[question_id]
            if isinstance(entries, str):
                entries = _find_source_paragraphs(entries.strip(), document_texts_by_source)
                if not entries:
                    raise ValueError(f"问题证据线索必须能在输入文档原文中定位：{question}")
            if not isinstance(entries, list):
                raise ValueError(f"问题证据必须是数组或原文线索字符串：{question_id}（{question}）")
            result[question] = []
            for entry in entries:
                if isinstance(entry, str):
                    matched_entries = _find_source_paragraphs(entry.strip(), document_texts_by_source)
                    if not matched_entries:
                        raise ValueError(f"问题证据线索必须能在输入文档原文中定位：{question}")
                    result[question].extend(matched_entries)
                    continue
                if not isinstance(entry, dict):
                    raise ValueError(f"问题证据项必须是对象或原文线索字符串：{question}")
                if "source" not in entry or "evidence" not in entry:
                    raise ValueError(f"问题证据项缺少 source 或 evidence：{question}")
                if not isinstance(entry["source"], str) or not isinstance(entry["evidence"], str):
                    raise ValueError(f"问题证据项 source 和 evidence 必须是字符串：{question}")
                source = entry["source"].strip().replace("\\", "/")
                evidence = entry["evidence"].strip()
                if source not in allowed_sources or not evidence:
                    continue
                if not any(evidence in text for text in document_texts_by_source[source]):
                    matched_entries = _find_source_paragraphs(evidence, {source: document_texts_by_source[source]})
                    if not matched_entries:
                        raise ValueError(f"问题证据必须是对应输入文档原文子串：{question}")
                    result[question].extend(matched_entries)
                    continue
                result[question].append({"source": source, "evidence": evidence})
        return result

    return _request_validated_json(client, model, messages, "证据筛选", validate, max_attempts)


def _answer_evidence_grounding(answer: str, evidence: list) -> float:
    """答案-证据落地度：取顺序相似度与共享二元组比例中的较大值。

    只按字符集合判断会同时产生假阳性和假阴性：同义改写被误拒、
    高频字堆砌被误放行。顺序相似度能识别同义改写，二元组比例能
    识别“摘录+概括”的半原文答案，二者取大值兼顾两种形态。
    """
    normalized_answer = normalize_text(answer)
    normalized_evidence = normalize_text(" ".join(evidence))
    if not normalized_answer or not normalized_evidence:
        return 0.0
    similarity = SequenceMatcher(None, normalized_answer, normalized_evidence).ratio()
    answer_bigrams = {
        normalized_answer[index:index + 2]
        for index in range(len(normalized_answer) - 1)
    }
    evidence_bigrams = {
        normalized_evidence[index:index + 2]
        for index in range(len(normalized_evidence) - 1)
    }
    bigram_ratio = (
        len(answer_bigrams & evidence_bigrams) / len(answer_bigrams)
        if answer_bigrams else 0.0
    )
    return max(similarity, bigram_ratio)


def _answer_evidence_entity_coverage(answer: str, evidence: list) -> float:
    """答案中关键实体（年份、书名、引号短语、外文标识）在证据中的覆盖比例。"""
    answer_entities = extract_entities(answer)
    if not answer_entities:
        return 1.0
    evidence_text = " ".join(evidence)
    covered = sum(1 for entity in answer_entities if entity in evidence_text)
    return covered / len(answer_entities)


def summarize_baseline_batch(
    client,
    model: str,
    questions: list,
    screened: dict,
    max_attempts: int = 3,
    documents: list = None,
) -> dict:
    question_payload = [
        {"id": f"Q{index}", "question": question}
        for index, question in enumerate(questions, 1)
    ]
    screened_payload = {
        question_item["id"]: screened.get(question_item["question"]) or []
        for question_item in question_payload
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是知识评测基准汇总器。候选原文已由本地算法完成段落切分和相关性选择，"
                "你只能汇总候选原文，不得筛选新文档、扩展检索或补充外部信息。只返回JSON对象，"
                "键必须是问题列表中的稳定ID（如Q1、Q2），值必须包含 answerable；"
                "reference_answer 与 answer 二选一给出答案；source、unanswerable_reason、key_facts、"
                "evidence 均可省略或为 null（本地脚本会按 source 重新提取）。"
                "问题原文仅用于理解问题，不得作为JSON键。"
                "可回答题必须有完整简洁答案；source 只能选择候选来源。"
                "无证据题 answerable=false，答案为null，source为空数组，并说明10篇资料无相关证据。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"问题列表：{json.dumps(question_payload, ensure_ascii=False)}\n"
                f"本地候选原文：{json.dumps(screened_payload, ensure_ascii=False)}\n"
                "请按稳定ID汇总基准，不得引入候选原文之外的信息。"
            ),
        },
    ]

    def validate(payload: dict) -> dict:
        if len(question_payload) == 1 and "answer" in payload and "source" in payload:
            answer = payload.get("answer")
            source = payload.get("source")
            sources = source if isinstance(source, list) else [source]
            payload = {
                question_payload[0]["id"]: {
                    "answerable": bool(str(answer or "").strip()),
                    "reference_answer": answer,
                    "key_facts": [],
                    "source": sources,
                    "evidence": [],
                    "unanswerable_reason": "",
                }
            }
        result = {}
        for question_item in question_payload:
            question_id = question_item["id"]
            question = question_item["question"]
            item = payload.get(question_id)
            if not isinstance(item, dict):
                raise ValueError(f"基准汇总缺少问题：{question_id}（{question}）")
            if not isinstance(item.get("answerable"), bool):
                raise ValueError(f"基准汇总 answerable 必须是布尔值：{question}")
            raw_reference = item.get("reference_answer")
            if raw_reference is None or not str(raw_reference).strip():
                raw_reference = item.get("answer")
            if raw_reference is not None and not isinstance(raw_reference, str):
                raise ValueError(f"基准汇总 reference_answer 必须是字符串或null：{question}")
            item["reference_answer"] = raw_reference
            raw_key_facts = item.get("key_facts") or []
            if isinstance(raw_key_facts, str):
                raw_key_facts = [raw_key_facts]
            if not isinstance(raw_key_facts, list) or any(not isinstance(value, str) for value in raw_key_facts):
                raise ValueError(f"基准汇总 key_facts 必须是字符串数组：{question}")
            raw_source = item.get("source")
            if raw_source is None:
                item["source"] = []
            elif isinstance(raw_source, str):
                item["source"] = [raw_source]
            if not isinstance(item["source"], list) or any(not isinstance(value, str) for value in item["source"]):
                raise ValueError(f"基准汇总 source 必须是字符串或字符串数组：{question}")
            raw_unanswerable_reason = item.get("unanswerable_reason")
            if raw_unanswerable_reason is not None and not isinstance(raw_unanswerable_reason, str):
                raise ValueError(f"基准汇总 unanswerable_reason 必须是字符串：{question}")
            answerable = item["answerable"]
            reference_answer = (item["reference_answer"] or "").strip() or None
            key_facts = [value.strip() for value in raw_key_facts if value.strip()]
            source = [value.strip().replace("\\", "/") for value in item["source"] if value.strip()]
            unanswerable_reason = (item.get("unanswerable_reason") or "").strip()
            candidate_entries = screened.get(question) or []
            candidate_sources = {entry["source"] for entry in candidate_entries}
            if answerable:
                if not reference_answer:
                    raise ValueError(f"可回答题基准汇总不完整：{question}")
                document_texts_by_source = {}
                for document in documents or []:
                    normalized_source = document["path"].strip().replace("\\", "/")
                    document_texts_by_source[normalized_source] = document.get("text", "")
                allowed_sources = set(document_texts_by_source) or candidate_sources
                if not source:
                    # 模型漏填来源：用候选段落本地接地自动补全，避免把可回答题误判为不完整
                    grounded_entries = [
                        entry for entry in candidate_entries
                        if _answer_evidence_grounding(reference_answer, [entry["evidence"]])
                        >= ANSWER_EVIDENCE_SIM_MIN
                    ]
                    if grounded_entries:
                        source = sorted({entry["source"] for entry in grounded_entries})
                if not source:
                    raise ValueError(f"可回答题基准汇总不完整（来源为空且无法本地接地）：{question}")
                if any(value not in allowed_sources for value in source):
                    raise ValueError(f"基准汇总来源不在本地候选中：{question}")
                derived_evidence = [
                    entry["evidence"]
                    for entry in candidate_entries
                    if entry["source"] in source
                ]
                if (
                    _answer_evidence_grounding(reference_answer, derived_evidence) < ANSWER_EVIDENCE_SIM_MIN
                    and _answer_evidence_entity_coverage(reference_answer, derived_evidence) < ANSWER_EVIDENCE_ENTITY_MIN
                ):
                    # 候选截断导致相关段落未进入证据时，从所引来源全文段落扩展后复核
                    expanded_evidence = [
                        paragraph
                        for value in source
                        for paragraph in _split_source_paragraphs(document_texts_by_source.get(value, ""))
                    ]
                    if (
                        _answer_evidence_grounding(reference_answer, expanded_evidence) >= ANSWER_EVIDENCE_SIM_MIN
                        or _answer_evidence_entity_coverage(reference_answer, expanded_evidence) >= ANSWER_EVIDENCE_ENTITY_MIN
                    ):
                        derived_evidence = expanded_evidence
                    else:
                        raise ValueError(f"基准汇总答案与候选证据重合过低：{question}")
                evidence = [
                    segment for segment in derived_evidence
                    if _answer_evidence_grounding(reference_answer, [segment]) > 0.0
                ] or derived_evidence
                key_facts = split_key_facts(reference_answer)
                unanswerable_reason = ""
            else:
                # 模型判不可回答时必须通过本地证据复核：候选与全文档（段落级）仍存在
                # 与问题关键词相关的段落时，说明答案在资料中，拒绝模型误判并本地接地重建
                grounded = _build_grounded_answerable(
                    question,
                    _fulltext_evidence_pool(question, candidate_entries, documents),
                )
                if grounded:
                    answerable = True
                    reference_answer = grounded["reference_answer"]
                    key_facts = grounded["key_facts"]
                    source = grounded["source"]
                    evidence = grounded["evidence"]
                    unanswerable_reason = ""
                else:
                    reference_answer = None
                    key_facts = []
                    source = []
                    evidence = []
                    unanswerable_reason = unanswerable_reason or "10篇资料无相关证据"
            result[question] = {
                "answerable": answerable,
                "reference_answer": reference_answer,
                "key_facts": key_facts,
                "source": source,
                "evidence": evidence,
                "unanswerable_reason": unanswerable_reason,
            }
        return result

    return _request_validated_json(client, model, messages, "基准汇总", validate, max_attempts)


def _fulltext_evidence_pool(question: str, candidates: list, documents: list = None) -> list:
    """候选段落与全文档段落（段落级去重）合并，按问题关键词过滤后的相关段落池。

    注意按（来源, 段落）去重而非仅按来源，避免候选已含某来源时跳过该来源其余相关段落。
    """
    key_tokens = _extract_candidate_keywords(question)
    pool = []
    seen = set()
    for entry in candidates or []:
        marker = (entry.get("source"), entry.get("evidence"))
        if marker in seen:
            continue
        seen.add(marker)
        pool.append(entry)
    for document in documents or []:
        source = document["path"].strip().replace("\\", "/")
        for paragraph in _split_source_paragraphs(document.get("text", "")):
            marker = (source, paragraph)
            if marker in seen:
                continue
            seen.add(marker)
            pool.append({"source": source, "evidence": paragraph})
    if key_tokens:
        pool = [entry for entry in pool if any(token in entry["evidence"] for token in key_tokens)]
    return pool


def _build_grounded_answerable(question: str, entries: list) -> dict:
    """从相关原文段落本地抽取接地参考答案；找不到相关段落返回 None。

    段落按与问题的相关性降序取前 5 条，清理 markdown 后拼接，天然接地不引入模型幻觉。
    """
    ordered = sorted(entries, key=lambda entry: -_candidate_relevance(question, entry["evidence"]))
    used = []
    used_sources = []
    for entry in ordered[:5]:
        cleaned = re.sub(r"^#{1,6}\s*", "", entry["evidence"])
        cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"^[-*]\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = " ".join(cleaned.split())
        if cleaned and cleaned not in used:
            used.append(cleaned)
            used_sources.append(entry["source"])
    if not used:
        return None
    reference_answer = "；".join(segment.rstrip("。") for segment in used) + "。"
    return {
        "answerable": True,
        "reference_answer": reference_answer,
        "key_facts": split_key_facts(reference_answer),
        "source": sorted(set(used_sources)),
        "evidence": used,
        "unanswerable_reason": "",
    }


def build_local_baseline_fallback(question: str, candidates: list, documents: list = None) -> dict:
    """模型多次汇总失败时的确定性兜底：从本地候选原文抽取参考答案。

    参考答案由命中问题关键实体的原文段落（去掉 markdown 标记）拼接而成，
    天然接地，不会引入模型幻觉；找不到任何相关段落时判定为不可回答。
    """
    grounded = _build_grounded_answerable(
        question, _fulltext_evidence_pool(question, candidates, documents)
    )
    if grounded:
        return grounded
    return {
        "answerable": False,
        "reference_answer": None,
        "key_facts": [],
        "source": [],
        "evidence": [],
        "unanswerable_reason": "本地兜底：候选原文中未找到与问题相关的段落",
    }


def is_generated_baseline_complete(item: dict) -> bool:
    if not item or "answerable" not in item:
        return False
    if item["answerable"]:
        return bool(item.get("reference_answer") and item.get("key_facts") and item.get("evidence"))
    return bool(item.get("unanswerable_reason"))


def generated_evidence_fingerprint(documents: list, model: str) -> str:
    """生成题证据缓存的来源指纹：文档内容哈希 + 模型名 + 协议版本。"""
    payload = {
        "documents": [
            {
                "path": document["path"],
                "sha256": hashlib.sha256(str(document.get("text") or "").encode("utf-8")).hexdigest(),
            }
            for document in documents
        ],
        "model": str(model),
        "schema_version": GEN_EVIDENCE_SCHEMA_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def build_generated_evidence(
    questions: list,
    path: Path = GEN_EVIDENCE_JSON,
    documents: list = None,
    client=None,
    model: str = None,
    question_batch_size: int = EVIDENCE_QUESTION_BATCH_SIZE,
    document_batch_chars: int = EVIDENCE_DOCUMENT_BATCH_CHARS,
) -> dict:
    if client is None:
        from openai import OpenAI

        client = OpenAI(base_url=config.OLLAMA_BASE_URL, api_key=config.OLLAMA_API_KEY)
    model = model or config.ACTION_MODEL
    documents = list(documents or load_manifest_documents())
    baseline = load_generated_evidence(path)
    meta = baseline.pop(GEN_EVIDENCE_META_KEY, None)
    expected_fingerprint = generated_evidence_fingerprint(documents, model)
    if meta is None or meta.get("fingerprint") != expected_fingerprint:
        baseline = {}
        print(f"[benchmark] 证据缓存来源/模型/协议已变化，重建 {len(questions)} 题基准")
    pending = [question for question in questions if not is_generated_baseline_complete(baseline.get(question))]
    if not pending:
        baseline[GEN_EVIDENCE_META_KEY] = {
            "fingerprint": expected_fingerprint,
            "model": model,
            "documents": len(documents),
            "schema_version": GEN_EVIDENCE_SCHEMA_VERSION,
        }
        return baseline
    max_candidates = max(1, document_batch_chars // 1200)
    screened = select_local_evidence_candidates(pending, documents, max_candidates=max_candidates)

    def persist():
        baseline[GEN_EVIDENCE_META_KEY] = {
            "fingerprint": expected_fingerprint,
            "model": model,
            "documents": len(documents),
            "schema_version": GEN_EVIDENCE_SCHEMA_VERSION,
        }
        _atomic_write_text(path, json.dumps(baseline, ensure_ascii=False, indent=2))

    processed = 0
    failed = []
    for question_batch in _batched(pending, question_batch_size):
        batch_candidates = {question: screened[question] for question in question_batch}
        try:
            summarized = summarize_baseline_batch(
                client, model, question_batch, batch_candidates, documents=documents
            )
            baseline.update(summarized)
            persist()
            processed += len(summarized)
            print(f"[benchmark] 已本地选证并汇总 {processed}/{len(pending)} 题")
        except Exception as exc:
            print(f"[benchmark] 批量汇总失败（{len(question_batch)} 题），降级逐题处理：{exc}")
            for question in question_batch:
                try:
                    single = summarize_baseline_batch(
                        client,
                        model,
                        [question],
                        {question: batch_candidates[question]},
                        max_attempts=3,
                        documents=documents,
                    )
                except Exception as single_exc:
                    print(f"[benchmark] 题「{question}」汇总失败，降级本地兜底：{single_exc}")
                    try:
                        single = build_local_baseline_fallback(
                            question, batch_candidates[question], documents
                        )
                    except Exception as fallback_exc:
                        print(f"[benchmark] 题「{question}」本地兜底失败，已跳过并保存其余进度：{fallback_exc}")
                        failed.append(question)
                        continue
                    single = {question: single}
                baseline.update(single)
                persist()
                processed += 1
                print(f"[benchmark] 已逐题汇总 {processed}/{len(pending)} 题")
    if failed:
        raise ValueError(
            f"生成题基准汇总失败 {len(failed)} 题：{failed}。"
            "已保存成功进度并写入指纹，重试本命令即可只处理未完成题目。"
        )
    return baseline


def build_and_save_benchmark() -> dict:
    questions = load_gen_questions()
    # 经模块属性读取（而非默认参数），与 build_generated_evidence 写入路径保持一致
    evidence = load_generated_evidence(GEN_EVIDENCE_JSON)
    meta = evidence.get(GEN_EVIDENCE_META_KEY) or {}
    fingerprint_valid = meta.get("fingerprint") == generated_evidence_fingerprint(
        load_manifest_documents(),
        config.ACTION_MODEL,
    )
    if not fingerprint_valid or any(not is_generated_baseline_complete(evidence.get(question)) for question in questions):
        evidence = build_generated_evidence(questions)
    benchmark = build_benchmark(generated_questions=questions, generated_evidence=evidence)
    validate_benchmark(benchmark)
    save_benchmark(benchmark)
    print(f"[benchmark] 已生成：{BENCHMARK_JSON.name}，共 {benchmark['metadata']['total']} 题")
    return benchmark


def reconcile_unanswerable_evidence() -> list:
    """评测口径修正：将本地全文存在证据却被判不可回答的生成题从证据缓存移除。

    模型（gemma3:4b）判不可回答时可能漏检知识库原文；本命令用与基准汇总一致的
    全文段落级检索复核，确认资料中存在相关段落的题将被移除缓存，重跑 --benchmark
    时只重建这些题（指纹不变），并保留其余已完成题目进度。
    """
    questions = load_gen_questions()
    # 经模块属性读取（而非默认参数），保证与下方写入路径一致，测试 patch 亦生效
    evidence = load_generated_evidence(GEN_EVIDENCE_JSON)
    meta = evidence.pop(GEN_EVIDENCE_META_KEY, None)
    documents = load_manifest_documents()
    removed = []
    for question in questions:
        item = evidence.get(question)
        if not item or item.get("answerable") is not False:
            continue
        pool = _fulltext_evidence_pool(question, [], documents)
        if _build_grounded_answerable(question, pool):
            evidence.pop(question, None)
            removed.append(question)
    evidence[GEN_EVIDENCE_META_KEY] = meta or {}
    _atomic_write_text(GEN_EVIDENCE_JSON, json.dumps(evidence, ensure_ascii=False, indent=2))
    print(f"[reconcile] 本地存在证据却判不可回答：移除 {len(removed)} 题，重跑 --benchmark 将重建为可回答")
    for question in removed:
        print(f"[reconcile]   {question}")
    return removed


def normalize_text(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(text or "").lower())


def answer_similarity(answer: str, reference_answer: str) -> float:
    normalized_answer = normalize_text(answer)
    normalized_reference = normalize_text(reference_answer)
    if not normalized_answer or not normalized_reference:
        return 0.0
    if normalized_answer == normalized_reference:
        return 1.0
    if normalized_answer in normalized_reference or normalized_reference in normalized_answer:
        return round(min(len(normalized_answer), len(normalized_reference)) / max(len(normalized_answer), len(normalized_reference)), 4)
    sequence_score = SequenceMatcher(None, normalized_answer, normalized_reference).ratio()
    answer_chars = set(normalized_answer)
    reference_chars = set(normalized_reference)
    character_score = len(answer_chars & reference_chars) / len(answer_chars | reference_chars)
    return round(max(sequence_score, character_score), 4)


def extract_entities(text: str) -> set:
    normalized = str(text or "")
    entities = set(re.findall(r"\d{2,4}年(?:\d{1,2}月(?:\d{1,2}日)?)?|\d+(?:\.\d+)?%?", normalized))
    entities.update(re.findall(r"《[^》]+》|“[^”]+”|[A-Za-z][A-Za-z0-9_-]*", normalized))
    return entities


def entity_similarity(answer: str, reference_answer: str) -> float:
    reference_entities = extract_entities(reference_answer)
    if not reference_entities:
        return answer_similarity(answer, reference_answer)
    answer_entities = extract_entities(answer)
    return round(len(reference_entities & answer_entities) / len(reference_entities), 4)


def classify_score(total_score: float) -> str:
    if total_score >= 0.85:
        return "correct"
    if total_score >= 0.60:
        return "partial"
    return "incorrect"


def is_abstention(answer: str) -> bool:
    normalized = str(answer or "").strip()
    if not normalized:
        return True
    return any(keyword in normalized for keyword in NO_ANSWER_KEYWORDS) and len(normalized) < 100


def _finite_unit_score(value, field: str) -> float:
    score = float(value)
    if not math.isfinite(score):
        raise ValueError(f"{field} 必须是有限数")
    return round(max(0.0, min(1.0, score)), 4)


def score_result(item: dict, answer: str, judge_score=None, judge=None) -> dict:
    answerable = item.get("answerable")
    reference_answer = item.get("reference_answer")
    key_facts = item.get("key_facts") or split_key_facts(reference_answer)
    evidence = item.get("evidence") or []
    answered = not is_abstention(answer)
    if answerable is None or (answerable and (not reference_answer or not key_facts or not evidence)):
        return {
            "fact": None,
            "entity": None,
            "judge": None,
            "total": None,
            "missing_facts": key_facts,
            "entity_conflicts": [],
            "answered": answered,
            "answerability_correct": False,
            "classification": "error",
            "manual_review": {"required": True, "reason": "缺少可回答性、参考答案、关键事实或证据基准"},
        }
    judge = dict(judge or {})
    if judge_score is not None and "score" not in judge:
        judge["score"] = judge_score
    normalized_judge_score = _finite_unit_score(
        judge.get("groundedness", judge.get("score", 0.0)),
        "模型裁判分数",
    )
    answerability_correct = answered == bool(answerable)
    missing_facts = list(judge.get("missing_facts") or [])
    entity_conflicts = list(judge.get("entity_conflicts") or [])
    if not answerable:
        fact_score = 1.0 if not answered else 0.0
        entity_score = fact_score
        normalized_judge_score = fact_score
        total_score = round(
            fact_score * FACT_WEIGHT + entity_score * ENTITY_WEIGHT + normalized_judge_score * JUDGE_WEIGHT,
            4,
        )
        review_required = answered
        review_reason = "不可回答题给出了实质性答案" if answered else ""
        classification = "hallucination" if answered else "correct_rejection"
    else:
        fact_score = _finite_unit_score(
            judge.get("fact_coverage", answer_similarity(answer, reference_answer)),
            "事实覆盖分数",
        )
        entity_score = _finite_unit_score(
            judge.get("entity_consistency", entity_similarity(answer, reference_answer)),
            "实体一致性分数",
        )
        rule_score = fact_score * FACT_WEIGHT + entity_score * ENTITY_WEIGHT
        scaled_rule_score = rule_score / (FACT_WEIGHT + ENTITY_WEIGHT)
        score_conflict = abs(scaled_rule_score - normalized_judge_score) >= CONFLICT_THRESHOLD
        judge_error = bool(judge.get("judge_error"))
        review_required = judge_error or not answered or bool(entity_conflicts) or score_conflict
        if judge_error:
            review_reason = "模型裁判失败，仅按规则评分，需人工复核"
        elif not answered:
            review_reason = "可回答题发生拒答"
        elif entity_conflicts:
            review_reason = "存在实体矛盾：" + "；".join(entity_conflicts)
        elif score_conflict:
            review_reason = "规则评分与模型裁判分歧过大"
        else:
            review_reason = ""
        total_score = round(rule_score + normalized_judge_score * JUDGE_WEIGHT, 4)
        classification = "manual_review" if review_required else classify_score(total_score)
    return {
        "fact": fact_score,
        "entity": entity_score,
        "judge": normalized_judge_score,
        "total": total_score,
        "missing_facts": missing_facts,
        "entity_conflicts": entity_conflicts,
        "answered": answered,
        "answerability_correct": answerability_correct,
        "classification": classification,
        "manual_review": {"required": review_required, "reason": review_reason},
    }


def validate_judge_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("模型裁判JSON必须是对象")
    required = ("fact_coverage", "entity_consistency", "groundedness", "missing_facts", "entity_conflicts", "reason")
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError("模型裁判JSON缺少字段：" + ", ".join(missing))
    for field in ("fact_coverage", "entity_consistency", "groundedness"):
        value = float(payload[field])
        if not math.isfinite(value):
            raise ValueError(f"模型裁判字段 {field} 必须是有限数")
        if value < 0.0 or value > 1.0:
            raise ValueError(f"模型裁判字段 {field} 必须在0到1之间")
    for field in ("missing_facts", "entity_conflicts"):
        value = payload.get(field)
        if value is None:
            payload[field] = []
            continue
        if not isinstance(value, list):
            raise ValueError(f"模型裁判字段 {field} 必须是数组")
    if payload.get("reason") is None:
        payload["reason"] = ""
    if not isinstance(payload["reason"], str):
        raise ValueError("模型裁判字段 reason 必须是字符串")
    return payload


def model_judge(
    client,
    model: str,
    question: str,
    reference_answer: str,
    evidence: list,
    answer: str,
    answerable: bool = True,
    key_facts: list = None,
    max_attempts: int = 3,
) -> dict:
    key_facts = list(key_facts or split_key_facts(reference_answer))
    criterion = (
        "题目可回答：按关键事实逐点判断事实点语义覆盖，检查日期、数字、人名、地名、作品名等实体矛盾，并判断回答是否有证据支撑。"
        if answerable
        else "题目不可回答：明确拒答为正确拒答，给出实质性答案视为幻觉。"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是严格的答案裁判。只返回JSON，字段为："
                "{\"fact_coverage\":0到1,\"entity_consistency\":0到1,\"groundedness\":0到1,"
                "\"missing_facts\":[],\"entity_conflicts\":[],\"reason\":\"简短理由\"}。"
                "结果分类仅允许 correct、partial、incorrect、correct_rejection、hallucination、manual_review、error。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"评分标准：{criterion}\n问题：{question}\n参考答案：{reference_answer or '无'}\n"
                f"关键事实：{json.dumps(key_facts, ensure_ascii=False)}\n"
                f"证据：{json.dumps(evidence, ensure_ascii=False)}\n待评答案：{answer}\n"
                "fact_coverage 必须按关键事实中被待评答案语义覆盖的比例评分；"
                "missing_facts 列出未覆盖事实；entity_conflicts 列出实体矛盾。"
            ),
        },
    ]
    last_error = None
    last_content = ""
    for attempt in range(max_attempts):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                timeout=300,
            )
            last_content = response.choices[0].message.content or ""
            payload = validate_judge_payload(_extract_json_object(last_content, "模型裁判未返回JSON"))
            fact_coverage = _finite_unit_score(payload["fact_coverage"], "fact_coverage")
            entity_consistency = _finite_unit_score(payload["entity_consistency"], "entity_consistency")
            groundedness = _finite_unit_score(payload["groundedness"], "groundedness")
            return {
                "score": round(
                    fact_coverage * FACT_WEIGHT + entity_consistency * ENTITY_WEIGHT + groundedness * JUDGE_WEIGHT,
                    4,
                ),
                "fact_coverage": fact_coverage,
                "entity_consistency": entity_consistency,
                "groundedness": groundedness,
                "missing_facts": [str(item) for item in payload["missing_facts"]],
                "entity_conflicts": [str(item) for item in payload["entity_conflicts"]],
                "reason": payload["reason"],
            }
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < max_attempts:
                messages = messages + [
                    {"role": "assistant", "content": last_content},
                    {"role": "user", "content": f"上一条结果无效：{exc}。请只返回满足字段和类型要求的JSON对象。"},
                ]
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max_attempts:
                time.sleep(min(2.0 * (attempt + 1), MODEL_CALL_BACKOFF))
    raise ValueError(f"模型裁判JSON校验失败，已重试 {max_attempts} 次：{last_error}；原始响应：{_truncate_model_response(last_content)}")


def make_result_record(item, match_type, confidence, actions, answer, elapsed, judge, fingerprint=None) -> dict:
    normalized_judge = dict(judge)
    if "fact_coverage" not in normalized_judge:
        normalized_judge["fact_coverage"] = answer_similarity(answer, item.get("reference_answer"))
    if "entity_consistency" not in normalized_judge:
        normalized_judge["entity_consistency"] = entity_similarity(answer, item.get("reference_answer"))
    if "groundedness" not in normalized_judge:
        normalized_judge["groundedness"] = normalized_judge.get("score", 0.0)
    scores = score_result(item, answer, judge=normalized_judge)
    if match_type == "error":
        scores["classification"] = "error"
        scores["manual_review"] = {"required": True, "reason": judge.get("reason") or "评测执行异常"}
    record = {
        "schema_version": SCHEMA_VERSION,
        "qid": item["qid"],
        "category": item["category"],
        "question": item["question"],
        "benchmark": {
            "answerable": item.get("answerable"),
            "reference_answer": item.get("reference_answer"),
            "key_facts": item.get("key_facts") or split_key_facts(item.get("reference_answer")),
            "source": item.get("source"),
            "evidence": item.get("evidence") or [],
            "unanswerable_reason": item.get("unanswerable_reason") or "",
        },
        "output": {
            "match_type": match_type,
            "confidence": round(confidence, 4),
            "actions": actions,
            "answer": answer,
            "answered": scores["answered"],
            "elapsed_seconds": round(elapsed, 2),
        },
        "scores": {
            "fact": scores["fact"],
            "entity": scores["entity"],
            "judge": scores["judge"],
            "total": scores["total"],
            "missing_facts": scores["missing_facts"],
            "entity_conflicts": scores["entity_conflicts"],
        },
        "review": {
            "classification": scores["classification"],
            "answerability_correct": scores["answerability_correct"],
            "manual_review_required": scores["manual_review"]["required"],
            "manual_review_reason": scores["manual_review"]["reason"],
            "judge_reason": judge.get("reason") or "",
        },
    }
    if fingerprint:
        record["benchmark_fingerprint"] = fingerprint
    return record


def append_result(record: dict):
    with open(RESULTS_JSONL, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def compact_results(fingerprint: str) -> None:
    """清理结果文件：仅保留当前基准指纹各 qid 的最新记录并原子重写。

    基准指纹变化（如生成题基准重建）后重跑 --run 会全量追加新记录，
    旧指纹记录若不清理将无限累积；本函数在每次 --run 开始前收敛文件。
    """
    latest = {}
    for record in load_results():
        if record.get("benchmark_fingerprint") != fingerprint:
            continue
        latest[record["qid"]] = record
    compacted = [latest[qid] for qid in sorted(latest)]
    _atomic_write_text(
        RESULTS_JSONL,
        "\n".join(json.dumps(record, ensure_ascii=False) for record in compacted) + "\n",
    )
    print(f"[run] 结果文件已压缩：当前指纹 {len(compacted)} 条记录")


def is_current_result(record: dict) -> bool:
    return (
        record.get("schema_version") == SCHEMA_VERSION
        and "benchmark" in record
        and "output" in record
        and "scores" in record
        and "review" in record
        and "answerable" in record["benchmark"]
        and "key_facts" in record["benchmark"]
        and record.get("review", {}).get("classification") in CLASSIFICATIONS
    )


def load_results() -> list:
    if not RESULTS_JSONL.exists():
        return []
    results = []
    for line in RESULTS_JSONL.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except Exception:
            continue
        if is_current_result(record):
            results.append(record)
    return results


def load_done_qids(fingerprint: str, benchmark_items: dict = None) -> set:
    """断点续跑已完成题集合；基准可回答性判定已变化的旧结果作废重评。"""
    done = set()
    for record in load_results():
        if record.get("benchmark_fingerprint") != fingerprint:
            continue
        if record.get("review", {}).get("classification") == "error":
            continue
        if benchmark_items is not None:
            item = benchmark_items.get(record["qid"])
            if (
                item is not None
                and item.get("answerable") != record.get("benchmark", {}).get("answerable")
            ):
                # 基准可回答性已变化（如生成题不可回答误判被修正），旧结果作废需重评
                continue
        done.add(record["qid"])
    return done


def evaluate_item(system, judge_client, judge_model: str, item: dict, benchmark_fingerprint: str) -> dict:
    started = time.time()
    route_error = None
    match_type, confidence, actions, answer = "error", 0.0, [], ""
    judge = {"score": 0.0, "reason": ""}
    try:
        route = asyncio.run(system.process_input_with_metadata(item["question"]))
        match_type = route.match_type
        confidence = route.confidence
        actions = route.actions
        answer = route.answer
    except Exception as exc:
        route_error = exc
        match_type, confidence, actions, answer = "error", 0.0, [], f"[异常] {exc}"
        judge = {"score": 0.0, "reason": str(exc)}
    if route_error is None:
        try:
            if item.get("answerable", True):
                judge = model_judge(
                    judge_client,
                    judge_model,
                    item["question"],
                    item.get("reference_answer"),
                    item.get("evidence") or [],
                    answer,
                    True,
                    item.get("key_facts") or split_key_facts(item.get("reference_answer")),
                )
            else:
                deterministic = 0.0 if not is_abstention(answer) else 1.0
                judge = {
                    "score": deterministic,
                    "fact_coverage": deterministic,
                    "entity_consistency": deterministic,
                    "groundedness": deterministic,
                    "missing_facts": [],
                    "entity_conflicts": [],
                    "reason": "不可回答题按是否明确拒答进行确定性评分",
                }
        except Exception as exc:
            # 裁判失败不覆盖真实系统回答，降级为规则评分并标记人工复核
            judge = {
                "score": None,
                "groundedness": 0.0,
                "reason": f"模型裁判失败：{exc}",
                "judge_error": True,
            }
    elapsed = time.time() - started
    try:
        return make_result_record(
            item,
            match_type,
            confidence,
            actions,
            answer,
            elapsed,
            judge,
            fingerprint=benchmark_fingerprint,
        )
    except Exception as exc:
        return make_result_record(
            item,
            "error",
            0.0,
            [],
            f"[异常] {exc}",
            elapsed,
            {"score": 0.0, "reason": str(exc)},
            fingerprint=benchmark_fingerprint,
        )


def run_eval(limit: int = 0):
    benchmark = load_benchmark() if BENCHMARK_JSON.exists() else build_and_save_benchmark()
    validate_benchmark(benchmark)
    if not check_ollama():
        print("[run] 错误：Ollama 未在线。")
        raise SystemExit(1)
    import skill_chatchat_hybrid as hybrid

    fingerprint = benchmark_fingerprint(benchmark)
    compact_results(fingerprint)
    questions = benchmark["items"]
    if limit and limit < len(questions):
        questions = questions[:limit]
    done = load_done_qids(fingerprint, {item["qid"]: item for item in questions})
    system = hybrid.HybridAgentSystem({})
    judge_client = system._ollama_client
    print(f"[run] 基准共 {len(questions)} 题，已存在当前指纹成功结果 {len(done)} 条")
    for index, item in enumerate(questions, 1):
        if item["qid"] in done:
            print(f"[{index}/{len(questions)}] {item['qid']} 已完成，跳过")
            continue
        try:
            record = evaluate_item(system, judge_client, config.ACTION_MODEL, item, fingerprint)
        except Exception as exc:
            print(f"[{index}/{len(questions)}] {item['qid']} 评测异常：{exc}")
            try:
                record = make_result_record(
                    item, "error", 0.0, [], f"[异常] {exc}", 0.0,
                    {"score": 0.0, "reason": str(exc)}, fingerprint=fingerprint,
                )
            except Exception as record_exc:
                print(f"[{index}/{len(questions)}] {item['qid']} 无法构造错误记录，跳过：{record_exc}")
                continue
        try:
            append_result(record)
        except Exception as exc:
            print(f"[{index}/{len(questions)}] {item['qid']} 结果写入失败，后续题继续：{exc}")
            continue
        if record["review"]["classification"] != "error":
            done.add(item["qid"])
        total_score = record["scores"]["total"]
        score_text = "N/A" if total_score is None else f"{total_score:.2%}"
        match_type = record["output"]["match_type"]
        elapsed = record["output"]["elapsed_seconds"]
        print(f"[{index}/{len(questions)}] {item['qid']} {match_type:<10s} score={score_text} 耗时={elapsed:.1f}s")


def esc(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def stats_of(records):
    stats = {
        "total": len(records),
        "qa": 0,
        "knowledge": 0,
        "none": 0,
        "action": 0,
        "error": 0,
        "answerable_count": 0,
        "unanswerable_count": 0,
        "answered_count": 0,
        "answerability_correct_count": 0,
        "evidence_count": 0,
        "retrieval_weight_sum": 0.0,
        "fact": 0.0,
        "entity": 0.0,
        "judge": 0.0,
        "quality_score": 0.0,
        "scored_count": 0,
        "manual_review_count": 0,
        "elapsed_sum": 0.0,
    }
    for record in records:
        benchmark = record.get("benchmark", {})
        output = record.get("output", {})
        scores = record.get("scores", {})
        review = record.get("review", {})
        match_type = output.get("match_type", "error")
        if match_type not in stats:
            match_type = "none"
        stats[match_type] += 1
        stats["retrieval_weight_sum"] += RETRIEVAL_WEIGHTS.get(match_type, 0.0)
        if benchmark.get("answerable"):
            stats["answerable_count"] += 1
        else:
            stats["unanswerable_count"] += 1
        if output.get("answered"):
            stats["answered_count"] += 1
        if review.get("answerability_correct"):
            stats["answerability_correct_count"] += 1
        if benchmark.get("evidence"):
            stats["evidence_count"] += 1
        if scores.get("total") is not None:
            score_values = {field: float(scores[field]) for field in ("fact", "entity", "judge", "total")}
            if not all(math.isfinite(value) for value in score_values.values()):
                raise ValueError(f"评测结果包含非有限分数：{record.get('qid') or '未知编号'}")
            stats["fact"] += score_values["fact"]
            stats["entity"] += score_values["entity"]
            stats["judge"] += score_values["judge"]
            stats["quality_score"] += score_values["total"]
            stats["scored_count"] += 1
        if review.get("manual_review_required"):
            stats["manual_review_count"] += 1
        elapsed = float(output.get("elapsed_seconds", 0))
        if not math.isfinite(elapsed):
            raise ValueError(f"评测结果包含非有限耗时：{record.get('qid') or '未知编号'}")
        stats["elapsed_sum"] += elapsed
    scored_count = stats["scored_count"]
    for field in ("fact", "entity", "judge", "quality_score"):
        stats[field] = round(stats[field] / scored_count, 10) if scored_count else 0.0
    total = stats["total"]
    stats["answerability_accuracy"] = stats["answerability_correct_count"] / total if total else 0.0
    stats["answer_rate"] = stats["answered_count"] / total if total else 0.0
    stats["evidence_coverage"] = stats["evidence_count"] / total if total else 0.0
    stats["retrieval_coverage_weighted"] = stats["retrieval_weight_sum"] / total if total else 0.0
    stats["avg_elapsed"] = stats["elapsed_sum"] / total if total else 0.0
    return stats


def detail_section(records, title):
    lines = [
        f"### {title}",
        "",
        "| 编号 | 问题 | 基准类型 | 系统行为 | 命中类型 | 事实 | 实体 | 模型裁判 | 质量得分 | 分类 | 人工复核 | 耗时(s) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for record in records:
        benchmark = record["benchmark"]
        output = record["output"]
        scores = record["scores"]
        review = record["review"]
        values = ["N/A" if scores[field] is None else f"{scores[field]:.2%}" for field in ("fact", "entity", "judge", "total")]
        baseline_type = "可回答" if benchmark.get("answerable") else "不可回答"
        behavior = "作答" if output.get("answered") else "拒答"
        manual_review = review.get("manual_review_reason") if review.get("manual_review_required") else "否"
        lines.append(
            f"| {record['qid']} | {esc(record['question'])} | {baseline_type} | {behavior} | {output['match_type']} | "
            f"{values[0]} | {values[1]} | {values[2]} | {values[3]} | {review['classification']} | "
            f"{esc(manual_review)} | {output['elapsed_seconds']:.1f} |"
        )
    lines.append("")
    for record in records:
        benchmark = record["benchmark"]
        output = record["output"]
        review = record["review"]
        lines.extend([
            f"**{record['qid']}｜{record['question']}**",
            "",
            f"- 基准类型：{'可回答' if benchmark.get('answerable') else '不可回答'}",
            f"- 系统行为：{'作答' if output.get('answered') else '拒答'}",
            f"- 命中类型：`{output['match_type']}`",
            f"- 分类：{review['classification']}",
            f"- 人工复核：{review.get('manual_review_reason') or '否'}",
            f"- 模型裁判理由：{review.get('judge_reason') or '无'}",
            f"- 标准答案：{benchmark.get('reference_answer') or '无'}",
            f"- 关键事实：{'；'.join(esc(item) for item in benchmark.get('key_facts') or []) or '无'}",
            f"- 不可回答原因：{benchmark.get('unanswerable_reason') or '无'}",
            f"- 来源：{', '.join(benchmark['source']) if isinstance(benchmark.get('source'), list) else benchmark.get('source') or '无'}",
            f"- 证据基准：{'<br>'.join(esc(item) for item in benchmark.get('evidence') or []) or '无'}",
            "",
            output.get("answer") or "（空答案）",
            "",
            "---",
            "",
        ])
    return lines


def render_report(records, health, model, generated_questions, generated_at=None) -> str:
    generated_at = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bank_records = [record for record in records if record.get("category") == "题库"]
    generated_records = [record for record in records if record.get("category") == "生成"]
    lines = [
        "# 海宁皮影戏 500 题基准评测报告",
        "",
        f"> 生成时间：{generated_at}",
        "",
        "## 一、评测说明",
        "",
        f"- **结果格式**：v{SCHEMA_VERSION}，基准、输出、评分与人工复核分层保存",
        f"- **质量评分**：事实一致性 {FACT_WEIGHT:.0%}、实体一致性 {ENTITY_WEIGHT:.0%}、模型裁判 {JUDGE_WEIGHT:.0%}",
        "- **检索覆盖加权**：QA 命中按 1.0、知识命中按 0.7、其他按 0 计算",
        "- **不可回答题**：正确拒答按三项满分计，给出实质性答案按事实与实体零分并进入人工复核",
        "- **双指标报告**：同时报告质量得分与可回答性准确率，避免用高拒答率换取表面质量",
        f"- **模型**：`{model}`",
        f"- **知识库状态**：docs={health.get('documents')}、qa={health.get('qa_pairs')}、chunks={health.get('chunks')}、status={health.get('status')}",
        "",
        "## 二、总体统计",
        "",
        "| 类别 | 题数 | 可回答 | 不可回答 | QA命中 | 知识命中 | 未命中 | 异常 | 检索覆盖加权 | 质量得分 | 可回答性准确率 | 回答率 | 证据覆盖 | 人工复核 | 平均耗时(s) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, subset in (("总计", records), ("题库", bank_records), ("生成", generated_records)):
        stats = stats_of(subset)
        lines.append(
            f"| {name} | {stats['total']} | {stats['answerable_count']} | {stats['unanswerable_count']} | "
            f"{stats['qa']} | {stats['knowledge']} | {stats['none']} | {stats['error']} | "
            f"{stats['retrieval_coverage_weighted']:.2%} | {stats['quality_score']:.2%} | "
            f"{stats['answerability_accuracy']:.2%} | "
            f"{stats['answer_rate']:.2%} | {stats['evidence_coverage']:.2%} | "
            f"{stats['manual_review_count']} | {stats['avg_elapsed']:.1f} |"
        )
    lines.extend(["", "## 三、全量明细", ""])
    lines.extend(detail_section(bank_records, "题库题明细"))
    lines.extend(detail_section(generated_records, "生成题明细"))
    lines.extend(["## 附录：生成问题列表", ""])
    lines.extend(f"{index}. {question}" for index, question in enumerate(generated_questions, 1))
    lines.append("")
    return "\n".join(lines)


def make_report():
    benchmark = load_benchmark()
    validate_benchmark(benchmark)
    fingerprint = benchmark_fingerprint(benchmark)
    current_records = {
        record["qid"]: record
        for record in load_results()
        if record.get("benchmark_fingerprint") == fingerprint
        and record.get("review", {}).get("classification") != "error"
    }
    records = [current_records[item["qid"]] for item in benchmark["items"] if item["qid"] in current_records]
    if len(records) != len(benchmark["items"]):
        print(f"[report] 当前基准仅有 {len(records)}/{len(benchmark['items'])} 条成功结果，请先完成 --run。")
        raise SystemExit(1)
    from local_knowledge_qa import KnowledgeQAConfig, KnowledgeQAEngine

    health = KnowledgeQAEngine(KnowledgeQAConfig(discovery_root=ROOT)).health()
    report = render_report(records, health, config.ACTION_MODEL, load_gen_questions())
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    report_path = ROOT / f"评测报告-500题-{timestamp}.md"
    _atomic_write_text(report_path, report)
    print(f"[report] 报告已生成：{report_path.name}")


def main():
    parser = argparse.ArgumentParser(description="海宁皮影戏 500 题基准评测")
    parser.add_argument("--gen", action="store_true", help="生成 200 条相关问题")
    parser.add_argument("--benchmark", action="store_true", help="构建版本化评测基准")
    parser.add_argument("--reconcile", action="store_true", help="修正生成题不可回答误判，移除本地存在证据的题后重跑 --benchmark")
    parser.add_argument("--run", action="store_true", help="运行评测")
    parser.add_argument("--report", action="store_true", help="生成 Markdown 报告")
    parser.add_argument("--limit", type=int, default=0, help="评测题数上限")
    args = parser.parse_args()
    if args.gen:
        gen_questions()
    elif args.benchmark:
        build_and_save_benchmark()
    elif args.reconcile:
        reconcile_unanswerable_evidence()
    elif args.run:
        run_eval(args.limit)
    elif args.report:
        make_report()
    else:
        gen_questions()
        build_and_save_benchmark()
        run_eval(args.limit)
        make_report()


if __name__ == "__main__":
    main()
