from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
import math
import re
import unicodedata
from typing import Iterable, Sequence

from .models import KnowledgeChunk, QAPair, SourceRef


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
            if pair_intents - query_intents and query_intents:
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
