from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Iterable

from .config import KnowledgeQAConfig
from .generator import AnswerGenerator, ExtractiveGenerator, GroundedGenerationError, trim_to_limit
from .models import ParsedDocument, QueryResult, RetrievalResult, SourceRef
from .parsers import collect_snapshots, discover_source_paths, parse_document
from .retrieval import KnowledgeRetriever, build_chunks


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
