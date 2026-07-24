from __future__ import annotations

import json
from pathlib import Path

from portable_knowledge_qa import KnowledgeQAConfig, KnowledgeQAEngine


class StubGenerator:
    mode = "stub"

    def generate(self, query, retrieval):
        assert retrieval.has_evidence
        return "这是依据知识库生成的回答。", "用户正在询问示例知识"


def make_engine(tmp_path: Path) -> KnowledgeQAEngine:
    qa_path = tmp_path / "faq.json"
    qa_path.write_text(
        json.dumps(
            [
                {"id": "Q001", "question": "示例项目何时建立？", "answer": "示例项目于2020年建立。"},
                {"id": "Q002", "question": "示例项目在哪里？", "answer": "示例项目位于测试城市。"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    knowledge_path = tmp_path / "knowledge.md"
    knowledge_path.write_text(
        "# 示例项目\n\n## 技术特点\n示例项目采用离线检索，并且不使用向量模型。\n",
        encoding="utf-8",
    )
    config = KnowledgeQAConfig.from_paths([tmp_path], discovery_root=tmp_path)
    return KnowledgeQAEngine(config, generator=StubGenerator())


def test_qa_has_priority(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    retrieval = engine.retrieve("示例项目是哪一年建立的？")
    assert retrieval.match_type == "qa"
    assert "2020年" in retrieval.authoritative_answer


def test_query_contract(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    result = engine.query("示例项目有什么技术特点？")
    assert result.answer == "这是依据知识库生成的回答。"
    assert result.intent == "用户正在询问示例知识"
    assert result.styled_text.endswith("</STYLE>")
    assert len(result.answer) <= 100
    assert result.source_count > 0
    assert result.generation_mode == "stub"
    assert "sources" not in result.to_dict(include_sources=False)
    assert result.to_dict(include_sources=True)["sources"]


def test_no_evidence_refuses_model_knowledge(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    result = engine.query("月球背面的天气如何？")
    assert result.match_type == "none"
    assert result.generation_status == "no_evidence"
    assert "未找到足够信息" in result.answer


def test_file_change_refreshes_index(tmp_path: Path) -> None:
    knowledge_path = tmp_path / "knowledge.txt"
    knowledge_path.write_text("旧知识只包含甲主题。", encoding="utf-8")
    config = KnowledgeQAConfig.from_paths([tmp_path], discovery_root=tmp_path)
    engine = KnowledgeQAEngine(config, generator=StubGenerator())
    assert engine.retrieve("甲主题").has_evidence

    knowledge_path.write_text("新知识只包含乙主题，并且已经更新。", encoding="utf-8")
    assert engine.refresh()
    assert engine.retrieve("乙主题").has_evidence


def test_default_engine_requires_no_model(tmp_path: Path) -> None:
    qa_path = tmp_path / "faq.json"
    qa_path.write_text(
        json.dumps(
            [
                {"question": "项目何时建立？", "answer": "项目于2020年建立。"},
                {"question": "项目在哪里？", "answer": "项目位于测试城市。"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    engine = KnowledgeQAEngine(
        KnowledgeQAConfig.from_paths([tmp_path], discovery_root=tmp_path)
    )
    result = engine.query("项目是哪一年建立的？")
    assert "2020年" in result.answer
    assert result.generation_mode == "extractive"
    assert result.generation_status == "extractive"


def test_search_payload_contains_host_evidence(tmp_path: Path) -> None:
    engine = make_engine(tmp_path)
    payload = engine.retrieve("示例项目何时建立？").to_dict(
        include_sources=True,
        include_evidence=True,
    )
    assert payload["evidence"]
    assert "2020年" in payload["authoritative_answer"]


def test_rewritten_query_requires_specific_entity_coverage(tmp_path: Path) -> None:
    qa_path = tmp_path / "faq.json"
    qa_path.write_text(
        json.dumps(
            [
                {
                    "question": "如何查询示例项目当日的演出时间和票价？",
                    "answer": "请查询项目官方公告。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    engine = KnowledgeQAEngine(
        KnowledgeQAConfig.from_paths([tmp_path], discovery_root=tmp_path)
    )

    retrieval = engine.retrieve("示例项目 火星基地 演出时间")

    assert retrieval.match_type == "none"


def test_source_manifest_excludes_evaluation_and_keeps_qa_out_of_knowledge(tmp_path: Path) -> None:
    knowledge_path = tmp_path / "knowledge.md"
    knowledge_path.write_text("# 项目知识\n\n这是生产知识。", encoding="utf-8")
    qa_path = tmp_path / "answers.json"
    qa_path.write_text(
        json.dumps(
            [{"question": "项目何时建立？", "answer": "项目于2020年建立。"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    evaluation_path = tmp_path / "evaluation.md"
    evaluation_path.write_text("# 评测问题\n\n项目什么时候建立？", encoding="utf-8")
    (tmp_path / "knowledge-sources.json").write_text(
        json.dumps(
            {
                "sources": [
                    {"path": "knowledge.md", "role": "knowledge"},
                    {"path": "answers.json", "role": "qa"},
                    {"path": "evaluation.md", "role": "evaluation"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    engine = KnowledgeQAEngine(KnowledgeQAConfig(discovery_root=tmp_path))
    documents = engine.list_documents()

    assert {item["role"] for item in documents} == {"knowledge", "qa"}
    assert not any(item["path"] == str(evaluation_path) for item in documents)
    qa_document = next(item for item in documents if item["role"] == "qa")
    assert qa_document["sections"] == 0
    assert qa_document["qa_pairs"] == 1
    assert engine.retrieve("项目何时建立？").match_type == "qa"
