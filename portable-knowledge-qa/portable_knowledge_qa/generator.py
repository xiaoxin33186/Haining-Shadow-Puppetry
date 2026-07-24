from __future__ import annotations

import re
from typing import Protocol

from .models import RetrievalResult
from .retrieval import tokenize


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
