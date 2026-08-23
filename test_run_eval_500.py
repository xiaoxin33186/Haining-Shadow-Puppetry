import json
import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_eval_500 as evaluation


class BenchmarkTests(unittest.TestCase):
    def test_parse_answer_bank_extracts_answer_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answers.md"
            path.write_text(
                "# 答案\n\n### Q001\n**问题：** 什么是海宁皮影戏？\n\n"
                "**答案：** 这是标准答案。\n\n**来源：** [来源](https://example.com/a)\n",
                encoding="utf-8",
            )

            answers = evaluation.parse_answer_bank(path)

        self.assertEqual(
            answers["Q001"],
            {"reference_answer": "这是标准答案。", "source": "https://example.com/a"},
        )

    def test_parse_answer_bank_stops_answer_before_reference_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answers.md"
            path.write_text(
                "### Q511\n**问题：** 海宁皮影戏有什么特点？\n\n"
                "**答案：** 综合特点答案。\n\n"
                "**来源：** 综合题库答案与文档\n\n"
                "[S01]: https://example.com/1\n[S02]: https://example.com/2\n",
                encoding="utf-8",
            )

            answers = evaluation.parse_answer_bank(path)

        self.assertEqual(answers["Q511"]["reference_answer"], "综合特点答案。")
        self.assertNotIn("[S01]", answers["Q511"]["reference_answer"])

    def test_build_benchmark_adds_evidence_baseline_to_generated_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            question_path = root / "questions.md"
            answer_path = root / "answers.md"
            question_path.write_text(
                "- **Q001** 问题一？\n- **Q002** 问题二？\n- **Q003** 问题三？\n",
                encoding="utf-8",
            )
            answer_path.write_text(
                "### Q001\n**答案：** 答案一。\n**来源：** https://example.com/1\n"
                "### Q002\n**答案：** 答案二。\n**来源：** https://example.com/2\n"
                "### Q003\n**答案：** 答案三。\n**来源：** https://example.com/3\n",
                encoding="utf-8",
            )
            evidence = {
                "生成问题一？": {
                    "answerable": True,
                    "reference_answer": "生成题答案一。",
                    "source": ["doc-a.md"],
                    "evidence": ["证据片段一。"],
                },
                "生成问题二？": {
                    "answerable": False,
                    "reference_answer": None,
                    "source": [],
                    "evidence": [],
                    "unanswerable_reason": "知识库无相关证据",
                },
            }

            benchmark = evaluation.build_benchmark(
                question_path,
                answer_path,
                ["生成问题一？", "生成问题二？"],
                bank_sample_n=2,
                random_seed=7,
                generated_evidence=evidence,
            )

        self.assertEqual(benchmark["schema_version"], "4.0")
        self.assertEqual(benchmark["metadata"]["total"], 4)
        self.assertEqual(benchmark["metadata"]["bank_count"], 2)
        self.assertEqual(benchmark["metadata"]["generated_count"], 2)
        self.assertEqual(benchmark["metadata"]["generated_evidence_count"], 1)
        self.assertEqual(benchmark["metadata"]["unanswerable_count"], 1)
        answerable = benchmark["items"][2]
        unanswerable = benchmark["items"][3]
        self.assertEqual(answerable["qid"], "G001")
        self.assertTrue(answerable["answerable"])
        self.assertEqual(answerable["reference_answer"], "生成题答案一。")
        self.assertEqual(answerable["evidence"], ["证据片段一。"])
        self.assertFalse(unanswerable["answerable"])
        self.assertEqual(unanswerable["unanswerable_reason"], "知识库无相关证据")

    def test_build_benchmark_rejects_incomplete_generated_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            question_path = root / "questions.md"
            answer_path = root / "answers.md"
            question_path.write_text("- **Q001** 问题一？\n", encoding="utf-8")
            answer_path.write_text(
                "### Q001\n**答案：** 答案一。\n**来源：** https://example.com/1\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "生成题基准缺失"):
                evaluation.build_benchmark(
                    question_path,
                    answer_path,
                    ["生成问题？"],
                    bank_sample_n=1,
                    generated_evidence={},
                )

    def test_validate_benchmark_requires_exactly_500_complete_unique_items(self):
        benchmark = {
            "schema_version": evaluation.SCHEMA_VERSION,
            "metadata": {"total": 1, "bank_count": 1, "generated_count": 0},
            "items": [{"qid": "Q001", "question": "问题？", "answerable": True}],
        }

        with self.assertRaisesRegex(ValueError, "500"):
            evaluation.validate_benchmark(benchmark)

    def test_load_manifest_documents_reads_ten_md_docx_pdf_knowledge_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = []
            for index in range(8):
                path = root / f"doc-{index}.md"
                path.write_text(f"# 文档{index}\n\n正文{index}", encoding="utf-8")
                sources.append({"path": path.name, "role": "knowledge"})
            docx_path = root / "doc.docx"
            with zipfile.ZipFile(docx_path, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    '<w:body><w:p><w:r><w:t>DOCX正文</w:t></w:r></w:p></w:body></w:document>',
                )
            sources.append({"path": docx_path.name, "role": "knowledge"})
            pdf_path = root / "doc.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 test")
            sources.append({"path": pdf_path.name, "role": "knowledge"})
            sources.append({"path": "ignored.md", "role": "evaluation"})
            manifest = root / "knowledge-sources.json"
            manifest.write_text(json.dumps({"sources": sources}, ensure_ascii=False), encoding="utf-8")

            with patch.object(evaluation, "_read_pdf_text", return_value="PDF正文"):
                documents = evaluation.load_manifest_documents(manifest, expected_count=10)

        self.assertEqual(len(documents), 10)
        self.assertEqual({item["suffix"] for item in documents}, {".md", ".docx", ".pdf"})
        self.assertTrue(any("DOCX正文" in item["text"] for item in documents))
        self.assertTrue(any("PDF正文" in item["text"] for item in documents))

    def test_load_manifest_documents_rejects_wrong_knowledge_document_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "only.md"
            path.write_text("正文", encoding="utf-8")
            manifest = root / "knowledge-sources.json"
            manifest.write_text(
                json.dumps({"sources": [{"path": path.name, "role": "knowledge"}]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "10"):
                evaluation.load_manifest_documents(manifest, expected_count=10)

    def test_build_generated_evidence_selects_local_candidates_and_only_calls_model_to_summarize(self):
        relevant_paragraph = "《童年的金庸》于2024年3月在海宁首演。"
        documents = [
            {
                "path": "a.md",
                "title": "甲",
                "suffix": ".md",
                "text": f"无关介绍。\n\n{relevant_paragraph}\n\n其他内容。",
            },
            {"path": "b.pdf", "title": "乙", "suffix": ".pdf", "text": "皮影人物制作需要雕刻。"},
        ]
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"Q1":{"answerable":true,"reference_answer":"《童年的金庸》于2024年3月在海宁首演。",'
                '"key_facts":["2024年3月在海宁首演"],"source":["a.md"],'
                f'"evidence":["{relevant_paragraph}"],"unanswerable_reason":""}}}}'
            )))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            with patch.object(evaluation, "screen_evidence_batch", side_effect=AssertionError("不应调用模型筛证")):
                result = evaluation.build_generated_evidence(
                    ["《童年的金庸》何时在哪里首演？"],
                    path=path,
                    documents=documents,
                    client=client,
                    model="test-model",
                )
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(len(calls), 1)
        prompt = calls[0]["messages"][-1]["content"]
        self.assertIn(relevant_paragraph, prompt)
        self.assertNotIn("皮影人物制作需要雕刻", prompt)
        self.assertNotIn("请逐题筛选证据", prompt)
        self.assertTrue(result["《童年的金庸》何时在哪里首演？"]["answerable"])
        self.assertEqual(saved, result)

    def test_select_local_evidence_candidates_uses_paragraphs_keywords_entities_and_character_similarity(self):
        exact_paragraph = "海宁皮影戏《童年的金庸》于2024年3月在海宁首演。"
        documents = [
            {
                "path": "a.md",
                "title": "演出资料",
                "text": f"皮影戏历史悠久。\n\n{exact_paragraph}\n\n2024年另有其他文化活动。",
            },
            {"path": "b.md", "title": "制作资料", "text": "皮影人物采用皮革雕刻和彩绘制作。"},
        ]

        selected = evaluation.select_local_evidence_candidates(
            ["《童年的金庸》何时在哪里首演？"],
            documents,
            max_candidates=2,
        )

        self.assertEqual(selected["《童年的金庸》何时在哪里首演？"][0], {
            "source": "a.md",
            "evidence": exact_paragraph,
        })
        self.assertNotIn(
            {"source": "b.md", "evidence": "皮影人物采用皮革雕刻和彩绘制作。"},
            selected["《童年的金庸》何时在哪里首演？"],
        )

    def test_screen_evidence_batch_uses_json_mode_and_retries_invalid_per_question_fields(self):
        responses = iter([
            "不是JSON",
            '{"Q1":[{"source":"a.md","evidence":"证据甲"}],"Q2":{}}',
            '{"Q1":[{"source":"a.md","evidence":"证据甲"}],"Q2":[]}',
        ])
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))]
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = evaluation.screen_evidence_batch(
            client,
            "test-model",
            ["问题一？", "问题二？"],
            [{"path": "a.md", "title": "甲", "text": "证据甲"}],
        )

        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call["response_format"] == {"type": "json_object"} for call in calls))
        self.assertEqual(result["问题一？"], [{"source": "a.md", "evidence": "证据甲"}])
        self.assertEqual(result["问题二？"], [])

    def test_screen_evidence_batch_accepts_flat_single_question_text_or_evidence(self):
        for field in ("text", "evidence"):
            with self.subTest(field=field):
                content = json.dumps({"source": "a.md", field: "证据甲"}, ensure_ascii=False)
                client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                    create=lambda **kwargs: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                    )
                )))

                result = evaluation.screen_evidence_batch(
                    client,
                    "test-model",
                    ["问题一？"],
                    [{"path": "a.md", "title": "甲", "text": "前文证据甲后文"}],
                    max_attempts=1,
                )

                self.assertEqual(result["问题一？"], [{"source": "a.md", "evidence": "证据甲"}])

    def test_screen_evidence_batch_rejects_flat_response_for_multiple_questions(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"source":"a.md","text":"证据甲"}')
            )])
        )))

        with self.assertRaises(ValueError):
            evaluation.screen_evidence_batch(
                client,
                "test-model",
                ["问题一？", "问题二？"],
                [{"path": "a.md", "title": "甲", "text": "证据甲"}],
                max_attempts=1,
            )

    def test_screen_evidence_batch_accepts_q1_string_clue_and_expands_source_paragraph(self):
        content = '{"Q1":"关键线索"}'
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        result = evaluation.screen_evidence_batch(
            client,
            "test-model",
            ["问题一？"],
            [{"path": "a.md", "title": "甲", "text": "无关段落。\n\n包含关键线索的完整原文段落。\n仍属于同一段。"}],
            max_attempts=1,
        )

        self.assertEqual(result["问题一？"], [{
            "source": "a.md",
            "evidence": "包含关键线索的完整原文段落。\n仍属于同一段。",
        }])

    def test_screen_evidence_batch_accepts_q1_string_clue_array(self):
        content = '{"Q1":["线索甲","线索乙"]}'
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        result = evaluation.screen_evidence_batch(
            client,
            "test-model",
            ["问题一？"],
            [{"path": "a.md", "title": "甲", "text": "包含线索甲的原文段落。\n\n包含线索乙的另一原文段落。"}],
            max_attempts=1,
        )

        self.assertEqual(result["问题一？"], [
            {"source": "a.md", "evidence": "包含线索甲的原文段落。"},
            {"source": "a.md", "evidence": "包含线索乙的另一原文段落。"},
        ])

    def test_screen_evidence_batch_normalizes_object_evidence_and_returns_exact_source_paragraph(self):
        content = '{"Q1":[{"source":"a.md","evidence":"海宁 皮影戏 历史悠久"}]}'
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))
        paragraph = "海宁\t皮影戏\n历史悠久，保留真实排版。"

        result = evaluation.screen_evidence_batch(
            client,
            "test-model",
            ["问题一？"],
            [{"path": "a.md", "title": "甲", "text": f"前段。\n\n{paragraph}\n\n后段。"}],
            max_attempts=1,
        )

        self.assertEqual(result["问题一？"], [{"source": "a.md", "evidence": paragraph}])

    def test_screen_evidence_batch_normalizes_whitespace_and_returns_exact_source_paragraph(self):
        content = '{"Q1":"海宁 皮影戏历史悠久"}'
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))
        paragraph = "海宁\t皮影戏\n历史悠久，保留真实排版。"

        result = evaluation.screen_evidence_batch(
            client,
            "test-model",
            ["问题一？"],
            [{"path": "a.md", "title": "甲", "text": f"前段。\n\n{paragraph}\n\n后段。"}],
            max_attempts=1,
        )

        self.assertEqual(result["问题一？"], [{"source": "a.md", "evidence": paragraph}])

    def test_screen_evidence_batch_rejects_fabricated_q1_string_clue(self):
        responses = ('{"Q1":"伪造线索"}', '{"Q1":["伪造线索"]}')
        for content in responses:
            with self.subTest(content=content):
                client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                    create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=content)
                    )])
                )))

                with self.assertRaisesRegex(ValueError, "原文"):
                    evaluation.screen_evidence_batch(
                        client,
                        "test-model",
                        ["问题一？"],
                        [{"path": "a.md", "title": "甲", "text": "真实证据甲"}],
                        max_attempts=1,
                    )

    def test_screen_evidence_batch_rejects_evidence_not_in_source_document(self):
        responses = (
            '{"source":"a.md","text":"伪造证据"}',
            '{"Q1":[{"source":"a.md","evidence":"伪造证据"}]}',
        )
        for content in responses:
            with self.subTest(content=content):
                client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                    create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=content)
                    )])
                )))

                with self.assertRaisesRegex(ValueError, "原文子串"):
                    evaluation.screen_evidence_batch(
                        client,
                        "test-model",
                        ["问题一？"],
                        [{"path": "a.md", "title": "甲", "text": "真实证据甲"}],
                        max_attempts=1,
                    )

    def test_screen_evidence_batch_uses_stable_ids_for_fullwidth_and_halfwidth_question_marks(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"Q1":[{"source":"a.md","evidence":"证据甲"}],"Q2":[]}'
            )))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = evaluation.screen_evidence_batch(
            client,
            "test-model",
            ["问题一？", "问题二?"],
            [{"path": "a.md", "title": "甲", "text": "证据甲"}],
        )

        prompt = calls[0]["messages"][-1]["content"]
        self.assertIn('{"id": "Q1", "question": "问题一？"}', prompt)
        self.assertIn('{"id": "Q2", "question": "问题二?"}', prompt)
        self.assertEqual(result["问题一？"], [{"source": "a.md", "evidence": "证据甲"}])
        self.assertEqual(result["问题二?"], [])

    def test_screen_evidence_batch_reports_truncated_raw_response_after_three_attempts(self):
        raw = "非JSON" + "长" * 1000
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=raw))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with self.assertRaisesRegex(ValueError, "已重试 3 次") as raised:
            evaluation.screen_evidence_batch(
                client,
                "test-model",
                ["问题？"],
                [{"path": "a.md", "title": "甲", "text": "证据甲"}],
            )

        self.assertEqual(len(calls), 3)
        self.assertIn("原始响应：非JSON", str(raised.exception))
        self.assertIn("...<已截断>", str(raised.exception))
        self.assertLess(len(str(raised.exception)), 800)

    def test_evidence_document_batch_defaults_to_6000_chars(self):
        self.assertEqual(evaluation.EVIDENCE_DOCUMENT_BATCH_CHARS, 6000)

    def test_build_generated_evidence_uses_one_summary_call_per_question_batch(self):
        documents = [{"path": "a.md", "title": "甲", "suffix": ".md", "text": "问题一证据。\n\n问题二证据。"}]
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"Q1":{"answerable":true,"reference_answer":"问题一证据。",'
                '"key_facts":["事实一"],"source":["a.md"],"evidence":["问题一证据。"],'
                '"unanswerable_reason":""},'
                '"Q2":{"answerable":true,"reference_answer":"问题二证据。",'
                '"key_facts":["事实二"],"source":["a.md"],"evidence":["问题二证据。"],'
                '"unanswerable_reason":""}}'
            )))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as tmp:
            result = evaluation.build_generated_evidence(
                ["问题一？", "问题二？"],
                path=Path(tmp) / "evidence.json",
                documents=documents,
                client=client,
                model="test-model",
                question_batch_size=2,
            )

        self.assertEqual(len(calls), 1)
        self.assertTrue(result["问题一？"]["answerable"])
        self.assertTrue(result["问题二？"]["answerable"])

    def test_build_generated_evidence_falls_back_to_each_question_when_batch_summary_fails(self):
        documents = [{"path": "a.md", "title": "甲", "suffix": ".md", "text": "问题一证据。\n\n问题二证据。"}]
        responses = iter([
            "不是JSON",
            "仍不是JSON",
            "还是不是JSON",
            '{"answer":"问题一证据。","source":"a.md"}',
            '{"answer":"问题二证据。","source":"a.md"}',
        ])
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as tmp:
            result = evaluation.build_generated_evidence(
                ["问题一？", "问题二？"],
                path=Path(tmp) / "evidence.json",
                documents=documents,
                client=client,
                model="test-model",
                question_batch_size=2,
            )

        self.assertEqual(len(calls), 5)
        self.assertEqual(result["问题一？"]["reference_answer"], "问题一证据。")
        self.assertEqual(result["问题二？"]["reference_answer"], "问题二证据。")

    def test_summarize_baseline_batch_uses_json_mode_retries_and_validates_each_question(self):
        responses = iter([
            '{"Q1":{"answerable":true}}',
            '{"Q1":{"answerable":true,"reference_answer":"证据甲。",'
            '"key_facts":["事实甲"],"source":["a.md"],"evidence":["证据甲"],'
            '"unanswerable_reason":""}}',
        ])
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))]
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["问题一？"],
            {"问题一？": [{"source": "a.md", "evidence": "证据甲"}]},
        )

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call["response_format"] == {"type": "json_object"} for call in calls))
        self.assertEqual(result["问题一？"]["reference_answer"], "证据甲。")

    def test_summarize_baseline_batch_accepts_q1_source_and_evidence_strings(self):
        content = (
            '{"Q1":{"answerable":true,"reference_answer":"海宁皮影戏使用羊皮制作影偶。",'
            '"key_facts":["模型事实"],"source":"a.md","evidence":"模型改写证据",'
            '"unanswerable_reason":""}}'
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))
        candidates = [{"source": "a.md", "evidence": "海宁皮影戏使用羊皮制作影偶。"}]

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["海宁皮影戏使用什么制作影偶？"],
            {"海宁皮影戏使用什么制作影偶？": candidates},
            max_attempts=1,
        )

        item = result["海宁皮影戏使用什么制作影偶？"]
        self.assertEqual(item["source"], ["a.md"])
        self.assertEqual(item["evidence"], [candidates[0]["evidence"]])
        self.assertEqual(item["key_facts"], ["海宁皮影戏使用羊皮制作影偶"])

    def test_summarize_baseline_batch_accepts_flat_single_answer_source_and_derives_evidence(self):
        content = '{"answer":"海宁皮影戏使用羊皮影偶。表演采用海盐腔伴唱。","source":"a.md"}'
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))
        candidates = [{
            "source": "a.md",
            "evidence": "海宁皮影戏使用羊皮制作影偶，演出时采用海盐腔伴唱。",
        }]

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["海宁皮影戏有什么特点？"],
            {"海宁皮影戏有什么特点？": candidates},
            max_attempts=1,
        )

        item = result["海宁皮影戏有什么特点？"]
        self.assertTrue(item["answerable"])
        self.assertEqual(item["reference_answer"], "海宁皮影戏使用羊皮影偶。表演采用海盐腔伴唱。")
        self.assertEqual(item["source"], ["a.md"])
        self.assertEqual(item["evidence"], [candidates[0]["evidence"]])
        self.assertEqual(item["key_facts"], ["海宁皮影戏使用羊皮影偶", "表演采用海盐腔伴唱"])

    def test_summarize_baseline_batch_rejects_flat_single_source_outside_candidates(self):
        content = '{"answer":"海宁皮影戏使用羊皮影偶。","source":"outside.md"}'
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        with self.assertRaisesRegex(ValueError, "候选"):
            evaluation.summarize_baseline_batch(
                client,
                "test-model",
                ["海宁皮影戏使用什么制作影偶？"],
                {"海宁皮影戏使用什么制作影偶？": [{"source": "a.md", "evidence": "影偶使用羊皮制作。"}]},
                max_attempts=1,
            )

    def test_summarize_baseline_batch_accepts_null_unanswerable_reason_on_answerable(self):
        # 模型对可回答题常返回 "unanswerable_reason": null，必须等价于缺失而非报错
        content = (
            '{"Q1":{"answerable":true,"reference_answer":"海宁皮影戏以彩绘和雕刻见长。",'
            '"source":["a.md"],"unanswerable_reason":null}}'
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["海宁皮影戏的造型有什么特点？"],
            {"海宁皮影戏的造型有什么特点？": [
                {"source": "a.md", "evidence": "海宁皮影戏以彩绘和雕刻见长，风格独特。"},
            ]},
            max_attempts=1,
        )

        item = result["海宁皮影戏的造型有什么特点？"]
        self.assertTrue(item["answerable"])
        self.assertEqual(item["unanswerable_reason"], "")
        self.assertIn("彩绘和雕刻", item["reference_answer"])

    def test_summarize_baseline_batch_accepts_null_source_for_unanswerable(self):
        # 模型对不可回答题常返回 "source": null，必须等价于空数组
        content = (
            '{"Q1":{"answerable":false,"reference_answer":null,'
            '"source":null,"unanswerable_reason":"10篇资料无相关证据"}}'
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["知识库没有的问题？"],
            {"知识库没有的问题？": []},
            max_attempts=1,
        )

        item = result["知识库没有的问题？"]
        self.assertFalse(item["answerable"])
        self.assertEqual(item["source"], [])
        self.assertEqual(item["unanswerable_reason"], "10篇资料无相关证据")

    def test_summarize_baseline_batch_accepts_missing_unanswerable_reason_field(self):
        # 第4轮实测：模型对可回答题完全省略 unanswerable_reason 字段，必须等价于缺失而非报错
        content = (
            '{"Q1":{"answerable":true,"reference_answer":"海宁皮影戏约有300个剧目。",'
            '"source":["a.md"]}}'
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["海宁皮影戏存有大约多少个剧目？"],
            {"海宁皮影戏存有大约多少个剧目？": [
                {"source": "a.md", "evidence": "海宁皮影戏约有300个剧目，流传至今。"},
            ]},
            max_attempts=1,
        )

        item = result["海宁皮影戏存有大约多少个剧目？"]
        self.assertTrue(item["answerable"])
        self.assertEqual(item["unanswerable_reason"], "")
        self.assertIn("300个剧目", item["reference_answer"])

    def test_summarize_baseline_batch_falls_back_to_answer_when_reference_answer_null(self):
        # 第4轮实测：可回答题返回 reference_answer:null 但 answer 有值（《鸡斗》案例），应回退 answer
        content = (
            '{"Q1":{"answerable":true,"answer":"《鸡斗》以动物戏展现精准技艺。",'
            '"reference_answer":null,"source":["a.md"]}}'
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["《鸡斗》这部小戏的特色在于什么？"],
            {"《鸡斗》这部小戏的特色在于什么？": [
                {"source": "a.md", "evidence": "《鸡斗》以动物戏展现皮影操作的精准技艺，曾获金奖。"},
            ]},
            max_attempts=1,
        )

        item = result["《鸡斗》这部小戏的特色在于什么？"]
        self.assertTrue(item["answerable"])
        self.assertEqual(item["reference_answer"], "《鸡斗》以动物戏展现精准技艺。")
        self.assertIn("《鸡斗》", item["evidence"][0])

    def test_summarize_baseline_batch_allows_source_in_documents_but_not_candidates(self):
        # 第4轮实测：模型引用的来源只在全文档中、未进候选集，传入 documents 后应放行并按全文重建证据
        documents = [{"path": "b.md", "title": "乙", "text": "海宁皮影戏约有300个剧目。"}]
        candidates = [{"source": "a.md", "evidence": "海宁皮影戏剧目丰富。"}]
        content = (
            '{"Q1":{"answerable":true,"reference_answer":"海宁皮影戏约有300个剧目。",'
            '"source":["b.md"]}}'
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["海宁皮影戏存有大约多少个剧目？"],
            {"海宁皮影戏存有大约多少个剧目？": candidates},
            max_attempts=1,
            documents=documents,
        )

        item = result["海宁皮影戏存有大约多少个剧目？"]
        self.assertEqual(item["source"], ["b.md"])
        self.assertEqual(item["evidence"], ["海宁皮影戏约有300个剧目。"])

    def test_summarize_baseline_batch_expands_evidence_from_source_when_candidates_truncated(self):
        # 第4轮实测：候选截断导致相关段落未进入候选证据（"300个剧目"案例），
        # 落地度不足时应从所引来源全文扩展段落复核后通过
        documents = [{
            "path": "a.md",
            "title": "甲",
            "text": "海宁皮影戏在民国鼎盛期剧目达300余个，历史上曾有近300个剧目。",
        }]
        candidates = [{"source": "a.md", "evidence": "该剧种源远流长。"}]
        content = (
            '{"Q1":{"answerable":true,"reference_answer":"海宁皮影戏存有大约300个剧目。",'
            '"source":["a.md"]}}'
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["海宁皮影戏存有大约多少个剧目？"],
            {"海宁皮影戏存有大约多少个剧目？": candidates},
            max_attempts=1,
            documents=documents,
        )

        item = result["海宁皮影戏存有大约多少个剧目？"]
        self.assertTrue(item["answerable"])
        self.assertEqual(item["reference_answer"], "海宁皮影戏存有大约300个剧目。")
        self.assertIn("300余个", item["evidence"][0])

    def test_summarize_baseline_batch_rejects_answer_with_low_candidate_evidence_overlap(self):
        content = '{"answer":"该剧于1999年在北京首演。","source":"a.md"}'
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))

        with self.assertRaisesRegex(ValueError, "证据重合"):
            evaluation.summarize_baseline_batch(
                client,
                "test-model",
                ["《童年的金庸》何时在哪里首演？"],
                {"《童年的金庸》何时在哪里首演？": [{
                    "source": "a.md",
                    "evidence": "《童年的金庸》于2024年3月在海宁首演。",
                }]},
                max_attempts=1,
            )

    def test_summarize_baseline_batch_uses_stable_ids_for_fullwidth_and_halfwidth_question_marks(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"Q1":{"answerable":true,"reference_answer":"证据甲。",'
                '"key_facts":["事实甲"],"source":["a.md"],"evidence":["证据甲"],'
                '"unanswerable_reason":""},'
                '"Q2":{"answerable":false,"reference_answer":null,"key_facts":[],'
                '"source":[],"evidence":[],"unanswerable_reason":"10篇资料无相关证据"}}'
            )))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["问题一？", "问题二?"],
            {
                "问题一？": [{"source": "a.md", "evidence": "证据甲"}],
                "问题二?": [],
            },
        )

        prompt = calls[0]["messages"][-1]["content"]
        self.assertIn('{"id": "Q1", "question": "问题一？"}', prompt)
        self.assertIn('{"id": "Q2", "question": "问题二?"}', prompt)
        self.assertEqual(result["问题一？"]["reference_answer"], "证据甲。")
        self.assertFalse(result["问题二?"]["answerable"])

    def test_build_generated_evidence_resumes_completed_questions(self):
        documents = [{"path": "a.md", "title": "甲", "suffix": ".md", "text": "新问题证据甲"}]
        completed = {
            evaluation.GEN_EVIDENCE_META_KEY: {
                "fingerprint": evaluation.generated_evidence_fingerprint(documents, "test-model"),
                "model": "test-model",
                "documents": 1,
                "schema_version": evaluation.GEN_EVIDENCE_SCHEMA_VERSION,
            },
            "已完成问题？": {
                "answerable": True,
                "reference_answer": "旧答案。",
                "key_facts": ["旧事实"],
                "source": ["a.md"],
                "evidence": ["旧证据。"],
                "unanswerable_reason": "",
            }
        }
        responses = iter([
            '{"Q1":{"answerable":true,"reference_answer":"新问题证据甲。",'
            '"key_facts":["新事实"],"source":["a.md"],"evidence":["新问题证据甲"],'
            '"unanswerable_reason":""}}',
        ])
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))]
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text(json.dumps(completed, ensure_ascii=False), encoding="utf-8")
            result = evaluation.build_generated_evidence(
                ["已完成问题？", "新问题？"],
                path=path,
                documents=documents,
                client=client,
                model="test-model",
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["已完成问题？"], completed["已完成问题？"])
        self.assertEqual(result["新问题？"]["reference_answer"], "新问题证据甲。")

    def test_summarize_baseline_batch_accepts_paraphrased_answer_grounded_in_evidence(self):
        # 原回归失败点：答案是候选证据的同义概括，字符集合重合低于旧 0.6 阈值，
        # 但语义明显来自证据，必须放行。
        content = (
            '{"answer":"海宁皮影戏的纹样布局体现了严谨、象征和寓意的审美理念。'
            '影偶服饰上的纹样布局严谨，每一笔描画都有其文化含义。龙纹象征至尊，云纹增添灵动，'
            '纹样与角色情感共鸣，传递福气与身份，展现中式美学魅力。","source":"a.md"}'
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))
        candidates = [
            {"source": "a.md", "evidence": "海宁皮影戏的纹样不仅装饰影偶，更承载着丰富的文化寓意和符号价值。影偶服饰上的纹样布局严谨，每一笔描画都有其文化含义。"},
            {"source": "a.md", "evidence": "小状元影偶是海宁皮影的代表，纹样布局严谨，龙纹象征至尊，云纹增添灵动，纹样与角色情感共鸣，传递福气与身份。"},
        ]

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["海宁皮影戏的纹样布局体现了怎样的审美理念？"],
            {"海宁皮影戏的纹样布局体现了怎样的审美理念？": candidates},
            max_attempts=1,
        )

        item = result["海宁皮影戏的纹样布局体现了怎样的审美理念？"]
        self.assertTrue(item["answerable"])
        self.assertIn("纹样布局严谨", item["reference_answer"])
        # 证据只保留与答案实际重合的段落
        self.assertIn(candidates[1]["evidence"], item["evidence"])

    def test_generated_evidence_fingerprint_invalidates_stale_cache(self):
        documents = [{"path": "a.md", "title": "甲", "suffix": ".md", "text": "旧文档证据"}]
        completed = {
            evaluation.GEN_EVIDENCE_META_KEY: {
                "fingerprint": evaluation.generated_evidence_fingerprint(documents, "test-model"),
                "model": "test-model",
                "documents": 1,
                "schema_version": evaluation.GEN_EVIDENCE_SCHEMA_VERSION,
            },
            "已完成问题？": {
                "answerable": True,
                "reference_answer": "旧答案。",
                "key_facts": ["旧事实"],
                "source": ["a.md"],
                "evidence": ["旧证据。"],
                "unanswerable_reason": "",
            },
        }
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"Q1":{"answerable":true,"reference_answer":"新问题证据。",'
                '"source":["a.md"],"unanswerable_reason":""}}'
            )))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text(json.dumps(completed, ensure_ascii=False), encoding="utf-8")
            # 文档内容变化 → 指纹不匹配 → 旧缓存作废，重新汇总
            result = evaluation.build_generated_evidence(
                ["已完成问题？"],
                path=path,
                documents=[{"path": "a.md", "title": "甲", "suffix": ".md", "text": "已完成问题证据。"}],
                client=client,
                model="test-model",
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["已完成问题？"]["reference_answer"], "新问题证据。")

    def test_build_generated_evidence_completes_failed_question_via_local_fallback_and_persists_progress(self):
        # 模型对某题 3 次失败后不再阻塞全局：由本地兜底从候选原文重建接地答案，进度照常落盘
        documents = [{"path": "a.md", "title": "甲", "suffix": ".md", "text": "问题一证据。\n\n问题二证据。\n\n问题三证据。"}]
        responses = iter([
            "不是JSON", "仍不是JSON", "还是不是JSON",  # 批量 3 次重试失败
            '{"answer":"问题一证据。","source":"a.md"}',  # 逐题 Q1 成功
            "坏格式", "坏格式", "坏格式",  # 逐题 Q2 3 次失败 → 本地兜底
            '{"answer":"问题三证据。","source":"a.md"}',  # 下一批 Q3 成功
        ])
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(responses)))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            result = evaluation.build_generated_evidence(
                ["问题一？", "问题二？", "问题三？"],
                path=path,
                documents=documents,
                client=client,
                model="test-model",
                question_batch_size=2,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(result["问题一？"]["reference_answer"], "问题一证据。")
        self.assertEqual(result["问题二？"]["reference_answer"], "问题二证据。")
        self.assertTrue(result["问题二？"]["answerable"])
        self.assertEqual(result["问题二？"]["source"], ["a.md"])
        self.assertEqual(result["问题三？"]["reference_answer"], "问题三证据。")
        self.assertIn(evaluation.GEN_EVIDENCE_META_KEY, saved)
        self.assertIn("问题二？", saved)

    def test_build_local_baseline_fallback_builds_grounded_answer_from_candidates(self):
        # 弋阳腔/海盐腔场景：候选含起源段落时，兜底必须产出可接地的参考答案而非幻觉
        candidates = [
            {
                "source": "a.md",
                "evidence": "### 弋阳腔\n- **起源**: 江西弋阳，为**明代四大声腔**之一\n- **特点**: 声调高亢激昂",
            },
            {
                "source": "a.md",
                "evidence": "### 海盐腔\n- **起源**: 浙江海盐，同为明代重要声腔\n- **特点**: 柔美婉转，抒情性强",
            },
        ]
        result = evaluation.build_local_baseline_fallback(
            "“弋阳腔”和“海盐腔”分别起源于哪个地区，并简述其主要的特点？",
            candidates,
        )
        self.assertTrue(result["answerable"])
        self.assertIn("江西弋阳", result["reference_answer"])
        self.assertIn("浙江海盐", result["reference_answer"])
        self.assertEqual(result["source"], ["a.md"])
        self.assertEqual(
            sorted(result["evidence"]),
            sorted(["弋阳腔 起源: 江西弋阳，为明代四大声腔之一 特点: 声调高亢激昂",
                    "海盐腔 起源: 浙江海盐，同为明代重要声腔 特点: 柔美婉转，抒情性强"]),
        )

    def test_build_local_baseline_fallback_marks_unanswerable_when_no_relevant_paragraph(self):
        # 候选与全文档均不含问题关键信息时，兜底判定不可回答，杜绝死锁
        candidates = [{"source": "a.md", "evidence": "皮影戏历史悠久。"}]
        documents = [{"path": "b.md", "title": "乙", "text": "影偶以彩绘雕刻见长。"}]
        result = evaluation.build_local_baseline_fallback(
            "知识库完全不存在的冷门名词解释？",
            candidates,
            documents,
        )
        self.assertFalse(result["answerable"])
        self.assertTrue(result["unanswerable_reason"])

    def test_summarize_baseline_batch_autofills_source_when_answer_grounded_in_candidates(self):
        # 模型漏填 source（source=[]）但答案可在候选段落接地时，应本地自动补全来源
        content = (
            '{"Q1":{"answerable":true,"reference_answer":"弋阳腔起源于江西弋阳，是明代四大声腔之一。",'
            '"source":[]}}'
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))
        candidates = [{"source": "a.md", "evidence": "弋阳腔起源于江西弋阳，为明代四大声腔之一。"}]

        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            ["弋阳腔起源于哪里？"],
            {"弋阳腔起源于哪里？": candidates},
            max_attempts=1,
        )

        item = result["弋阳腔起源于哪里？"]
        self.assertTrue(item["answerable"])
        self.assertEqual(item["source"], ["a.md"])
        self.assertIn("江西弋阳", item["evidence"][0])

    def test_extract_json_object_handles_multiple_objects_and_trailing_text(self):
        content = (
            "以下是答案：\n"
            '{"Q1":{"answerable":true}}\n'
            "（示例结束）\n"
            '{"Q2":{"answerable":false}}'
        )
        payload = evaluation._extract_json_object(content, "未找到")
        self.assertEqual(payload, {"Q1": {"answerable": True}})

    def test_request_validated_json_retries_network_errors_without_context_mutation(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise TimeoutError("连接超时")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        result = evaluation._request_validated_json(
            client,
            "test-model",
            [{"role": "user", "content": "原始问题"}],
            "测试",
            lambda payload: payload,
            max_attempts=2,
        )
        self.assertEqual(result, {"ok": True})
        # 网络异常不应追加纠正上下文，避免污染对话历史
        self.assertEqual(len(calls[1]["messages"]), 1)

    def test_summarize_baseline_batch_overrides_model_unanswerable_when_local_evidence_exists(self):
        # 模型判 answerable=false（表格行场景）但知识库全文存在相关段落时，
        # 必须拒绝模型误判，翻转可回答并本地抽取接地参考答案
        documents = [{"path": "a.md", "title": "甲", "text": "## 高腔\n《水漫金山》《盗仙草》属高腔声腔系统。"}]
        content = '{"Q1":{"answerable":false}}'
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))
        question = "水漫金山和盗仙草属于哪个声腔系统？"
        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            [question],
            {question: []},
            max_attempts=1,
            documents=documents,
        )
        item = result[question]
        self.assertTrue(item["answerable"])
        self.assertIn("高腔", item["reference_answer"])
        self.assertEqual(item["source"], ["a.md"])
        self.assertTrue(item["evidence"])
        self.assertFalse(item["unanswerable_reason"])

    def test_summarize_baseline_batch_keeps_unanswerable_when_no_local_evidence(self):
        # 全文确无与问题相关的段落时，维持模型不可回答判定
        documents = [{"path": "a.md", "title": "甲", "text": "皮影戏历史悠久，深受群众喜爱。"}]
        content = '{"Q1":{"answerable":false,"unanswerable_reason":"资料无记载"}}'
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content)
            )])
        )))
        question = "知识库完全不存在的内容解释？"
        result = evaluation.summarize_baseline_batch(
            client,
            "test-model",
            [question],
            {question: []},
            max_attempts=1,
            documents=documents,
        )
        item = result[question]
        self.assertFalse(item["answerable"])
        self.assertTrue(item["unanswerable_reason"])
        self.assertEqual(item["source"], [])

    def test_fulltext_evidence_pool_merges_full_documents_despite_candidate_source_overlap(self):
        # 候选已含某来源段落时，全文复核仍须纳入该来源其余相关段落（段落级去重而非来源级）
        documents = [{"path": "a.md", "title": "甲", "text": "### 高腔\n《水漫金山》《盗仙草》属高腔声腔系统。"}]
        candidates = [{"source": "a.md", "evidence": "### 高腔"}]
        pool = evaluation._fulltext_evidence_pool(
            "水漫金山和盗仙草属于哪个声腔系统？", candidates, documents
        )
        self.assertTrue(any("水漫金山" in entry["evidence"] for entry in pool))

    def test_reconcile_unanswerable_evidence_removes_only_groundable_questions(self):
        # 评测口径修正：本地全文存在证据却判不可回答的题从缓存移除，其余保留
        with tempfile.TemporaryDirectory() as tmp:
            evidence_path = Path(tmp) / "evidence.json"
            evidence_path.write_text(json.dumps({
                "水漫金山和盗仙草属于哪个声腔系统？": {"answerable": False, "unanswerable_reason": "10篇资料无相关证据"},
                "知识库完全不存在的内容解释？": {"answerable": False, "unanswerable_reason": "10篇资料无相关证据"},
                "_meta": {"fingerprint": "fp"},
            }, ensure_ascii=False), encoding="utf-8")
            documents = [{"path": "a.md", "title": "甲", "text": "《水漫金山》《盗仙草》属高腔声腔系统。"}]
            with patch.object(evaluation, "GEN_EVIDENCE_JSON", evidence_path), \
                 patch.object(evaluation, "load_gen_questions", return_value=[
                     "水漫金山和盗仙草属于哪个声腔系统？", "知识库完全不存在的内容解释？",
                 ]), \
                 patch.object(evaluation, "load_manifest_documents", return_value=documents):
                removed = evaluation.reconcile_unanswerable_evidence()
            saved = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(removed, ["水漫金山和盗仙草属于哪个声腔系统？"])
        self.assertNotIn("水漫金山和盗仙草属于哪个声腔系统？", saved)
        self.assertIn("知识库完全不存在的内容解释？", saved)
        self.assertEqual(saved["_meta"], {"fingerprint": "fp"})


class ScoringTests(unittest.TestCase):
    def test_score_result_uses_approved_fact_entity_judge_weights(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "《童年的金庸》何时在哪里首演？",
            "answerable": True,
            "reference_answer": "《童年的金庸》于2024年3月在海宁首演。",
            "source": "https://example.com",
            "evidence": ["《童年的金庸》于2024年3月在海宁首演。"],
        }

        scores = evaluation.score_result(
            item,
            answer="《童年的金庸》于2024年3月在海宁首演。",
            judge_score=0.8,
        )

        self.assertEqual(evaluation.FACT_WEIGHT, 0.45)
        self.assertEqual(evaluation.ENTITY_WEIGHT, 0.25)
        self.assertEqual(evaluation.JUDGE_WEIGHT, 0.30)
        self.assertEqual(scores["fact"], 1.0)
        self.assertEqual(scores["entity"], 1.0)
        self.assertEqual(scores["judge"], 0.8)
        self.assertEqual(scores["total"], 0.94)
        self.assertEqual(scores["classification"], "correct")
        self.assertTrue(scores["answered"])
        self.assertTrue(scores["answerability_correct"])
        self.assertFalse(scores["manual_review"]["required"])

    def test_score_result_uses_explicit_key_facts_for_semantic_coverage(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "海宁皮影戏有什么特点？",
            "answerable": True,
            "reference_answer": "海宁皮影戏采用羊皮影偶，并以海盐腔伴唱。",
            "key_facts": ["影偶以羊皮制作", "表演使用海盐腔伴唱"],
            "source": "https://example.com",
            "evidence": ["海宁皮影戏采用羊皮影偶，并以海盐腔伴唱。"],
        }

        scores = evaluation.score_result(
            item,
            answer="影人是羊皮雕成的，演出时配以海盐腔。",
            judge={
                "score": 1.0,
                "fact_coverage": 1.0,
                "entity_consistency": 1.0,
                "groundedness": 1.0,
                "missing_facts": [],
                "entity_conflicts": [],
                "reason": "语义完整",
            },
        )

        self.assertEqual(scores["fact"], 1.0)
        self.assertEqual(scores["missing_facts"], [])
        self.assertEqual(scores["classification"], "correct")

    def test_score_result_uses_fact_point_coverage_instead_of_whole_answer_similarity(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "海宁皮影戏有什么特点？",
            "answerable": True,
            "reference_answer": "海宁皮影戏采用羊皮影偶，并以海盐腔伴唱。",
            "key_facts": ["影偶以羊皮制作", "表演使用海盐腔伴唱"],
            "source": "https://example.com",
            "evidence": ["海宁皮影戏采用羊皮影偶，并以海盐腔伴唱。"],
        }

        scores = evaluation.score_result(
            item,
            answer="海宁皮影戏使用羊皮影偶。",
            judge={
                "score": 0.65,
                "fact_coverage": 0.5,
                "entity_consistency": 1.0,
                "groundedness": 1.0,
                "missing_facts": ["表演使用海盐腔伴唱"],
                "entity_conflicts": [],
                "reason": "覆盖一个事实点",
            },
        )

        self.assertEqual(scores["fact"], 0.5)
        self.assertEqual(scores["missing_facts"], ["表演使用海盐腔伴唱"])
        self.assertEqual(scores["classification"], "partial")

    def test_score_result_flags_entity_conflict_even_when_text_is_similar(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "何时首演？",
            "answerable": True,
            "reference_answer": "《童年的金庸》于2024年在海宁首演。",
            "key_facts": ["《童年的金庸》于2024年在海宁首演"],
            "source": "https://example.com",
            "evidence": ["《童年的金庸》于2024年在海宁首演。"],
        }

        scores = evaluation.score_result(
            item,
            answer="《童年的金庸》于2023年在海宁首演。",
            judge={
                "score": 0.6,
                "fact_coverage": 1.0,
                "entity_consistency": 0.0,
                "groundedness": 0.8,
                "missing_facts": [],
                "entity_conflicts": ["首演年份：答案为2023年，基准为2024年"],
                "reason": "年份矛盾",
            },
        )

        self.assertEqual(scores["entity"], 0.0)
        self.assertEqual(scores["entity_conflicts"], ["首演年份：答案为2023年，基准为2024年"])
        self.assertTrue(scores["manual_review"]["required"])
        self.assertIn("实体矛盾", scores["manual_review"]["reason"])
        self.assertEqual(scores["classification"], "manual_review")

    def test_score_result_flags_conflict_for_manual_review(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "何时首演？",
            "answerable": True,
            "reference_answer": "2024年在海宁首演。",
            "source": "https://example.com",
            "evidence": ["2024年在海宁首演。"],
        }

        scores = evaluation.score_result(item, answer="2024年在海宁首演。", judge_score=0.1)

        self.assertTrue(scores["manual_review"]["required"])
        self.assertEqual(scores["manual_review"]["reason"], "规则评分与模型裁判分歧过大")
        self.assertEqual(scores["classification"], "manual_review")

    def test_score_result_marks_manual_review_when_judge_failed(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "何时首演？",
            "answerable": True,
            "reference_answer": "2024年在海宁首演。",
            "source": "https://example.com",
            "evidence": ["2024年在海宁首演。"],
        }

        scores = evaluation.score_result(
            item,
            answer="2024年在海宁首演。",
            judge={
                "score": None,
                "groundedness": 0.0,
                "reason": "模型裁判失败：连接超时",
                "judge_error": True,
            },
        )

        # 裁判失败不能静默降级为普通错误分类，必须进入人工复核
        self.assertTrue(scores["manual_review"]["required"])
        self.assertIn("模型裁判失败", scores["manual_review"]["reason"])
        self.assertEqual(scores["classification"], "manual_review")

    def test_score_result_rewards_correct_abstention_for_unanswerable_question(self):
        item = {
            "qid": "G001",
            "category": "生成",
            "question": "知识库没有的问题？",
            "answerable": False,
            "reference_answer": None,
            "source": [],
            "evidence": [],
            "unanswerable_reason": "知识库无相关证据",
        }

        scores = evaluation.score_result(
            item,
            answer="未找到足够信息，无法根据当前知识库回答。",
            judge_score=1.0,
        )

        self.assertFalse(scores["answered"])
        self.assertTrue(scores["answerability_correct"])
        self.assertEqual(scores["fact"], 1.0)
        self.assertEqual(scores["entity"], 1.0)
        self.assertEqual(scores["total"], 1.0)
        self.assertEqual(scores["classification"], "correct_rejection")

    def test_score_result_scores_unanswerable_questions_deterministically_without_judge(self):
        item = {
            "qid": "G001",
            "category": "生成",
            "question": "知识库没有的问题？",
            "answerable": False,
            "reference_answer": None,
            "source": [],
            "evidence": [],
            "unanswerable_reason": "知识库无相关证据",
        }

        scores = evaluation.score_result(
            item,
            answer="未找到足够信息，无法根据当前知识库回答。",
            judge={"groundedness": 0.0},
        )

        self.assertEqual(scores["judge"], 1.0)
        self.assertEqual(scores["total"], 1.0)

    def test_score_result_rejects_hallucinated_answer_for_unanswerable_question(self):
        item = {
            "qid": "G001",
            "category": "生成",
            "question": "知识库没有的问题？",
            "answerable": False,
            "reference_answer": None,
            "source": [],
            "evidence": [],
            "unanswerable_reason": "知识库无相关证据",
        }

        scores = evaluation.score_result(item, answer="答案是2024年。", judge_score=0.0)

        self.assertTrue(scores["answered"])
        self.assertFalse(scores["answerability_correct"])
        self.assertEqual(scores["total"], 0.0)
        self.assertEqual(scores["classification"], "hallucination")
        self.assertTrue(scores["manual_review"]["required"])
        self.assertEqual(scores["manual_review"]["reason"], "不可回答题给出了实质性答案")

    def test_score_result_reviews_refusal_for_answerable_question(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "可回答问题？",
            "answerable": True,
            "reference_answer": "标准答案。",
            "source": "https://example.com",
            "evidence": ["标准答案。"],
        }

        scores = evaluation.score_result(
            item,
            answer="未找到足够信息，无法根据当前知识库回答。",
            judge_score=0.0,
        )

        self.assertFalse(scores["answerability_correct"])
        self.assertTrue(scores["manual_review"]["required"])
        self.assertEqual(scores["manual_review"]["reason"], "可回答题发生拒答")
        self.assertEqual(scores["classification"], "manual_review")

    def test_score_result_requires_complete_answerable_baseline(self):
        item = {
            "qid": "G001",
            "category": "生成",
            "question": "生成问题？",
            "answerable": True,
            "reference_answer": None,
            "source": None,
            "evidence": [],
        }

        scores = evaluation.score_result(item, answer="回答", judge_score=1.0)

        self.assertEqual(scores["classification"], "error")
        self.assertIsNone(scores["total"])
        self.assertTrue(scores["manual_review"]["required"])


class JudgeTests(unittest.TestCase):
    def test_model_judge_returns_three_dimensions_and_explicit_conflicts(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"fact_coverage": 0.75, "entity_consistency": 0.5, "groundedness": 0.9, '
                '"missing_facts": ["首演地点为海宁"], "entity_conflicts": ["首演年份错误"], '
                '"reason": "部分事实缺失且年份矛盾"}'
            )))]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )
        )

        result = evaluation.model_judge(
            client,
            model="test-model",
            question="问题？",
            reference_answer="标准答案。",
            evidence=["证据。"],
            answer="回答。",
            answerable=True,
        )

        self.assertEqual(result["fact_coverage"], 0.75)
        self.assertEqual(result["entity_consistency"], 0.5)
        self.assertEqual(result["groundedness"], 0.9)
        self.assertEqual(result["missing_facts"], ["首演地点为海宁"])
        self.assertEqual(result["entity_conflicts"], ["首演年份错误"])
        self.assertEqual(result["reason"], "部分事实缺失且年份矛盾")
        self.assertAlmostEqual(result["score"], 0.7325)

    def test_validate_judge_payload_rejects_non_finite_scores(self):
        for value in (math.nan, math.inf, -math.inf):
            payload = {
                "fact_coverage": value,
                "entity_consistency": 1.0,
                "groundedness": 1.0,
                "missing_facts": [],
                "entity_conflicts": [],
                "reason": "invalid",
            }
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "有限数"):
                evaluation.validate_judge_payload(payload)

    def test_model_judge_retries_invalid_json_and_validates_required_fields(self):
        responses = iter([
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="不是JSON"))]),
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"fact_coverage":1,"entity_consistency":1,"groundedness":1,'
                '"missing_facts":[],"entity_conflicts":[],"reason":"有效"}'
            )))]),
        ])
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return next(responses)

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        result = evaluation.model_judge(
            client,
            model="test-model",
            question="问题？",
            reference_answer="答案。",
            evidence=["证据。"],
            answer="回答。",
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["reason"], "有效")

    def test_model_judge_rejects_non_list_detail_fields_after_retries(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
            '{"fact_coverage":1,"entity_consistency":1,"groundedness":1,'
            '"missing_facts":"无","entity_conflicts":[],"reason":"无"}'
        )))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))

        with self.assertRaisesRegex(ValueError, "missing_facts"):
            evaluation.model_judge(
                client,
                model="test-model",
                question="问题？",
                reference_answer="答案。",
                evidence=["证据。"],
                answer="回答。",
            )

    def test_validate_judge_payload_accepts_null_optional_fields(self):
        # 裁判返回 null 的可选字段（列表/原因）必须等价于空值，不能判失败
        payload = {
            "fact_coverage": 1.0,
            "entity_consistency": 1.0,
            "groundedness": 1.0,
            "missing_facts": None,
            "entity_conflicts": None,
            "reason": None,
        }

        normalized = evaluation.validate_judge_payload(payload)

        self.assertEqual(normalized["missing_facts"], [])
        self.assertEqual(normalized["entity_conflicts"], [])
        self.assertEqual(normalized["reason"], "")

    def test_model_judge_prompt_requires_key_facts_missing_facts_and_classification_enum(self):
        captured = {}
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"fact_coverage": 1, "entity_consistency": 1, "groundedness": 1, '
                '"missing_facts": [], "entity_conflicts": [], "reason": "完整"}'
            )))]
        )

        def create(**kwargs):
            captured.update(kwargs)
            return response

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        evaluation.model_judge(
            client,
            model="test-model",
            question="问题？",
            reference_answer="标准答案。",
            evidence=["证据。"],
            answer="回答。",
            answerable=True,
            key_facts=["事实一", "事实二"],
        )

        prompt = "\n".join(message["content"] for message in captured["messages"])
        self.assertIn('"fact_coverage"', prompt)
        self.assertIn('"entity_consistency"', prompt)
        self.assertIn('"groundedness"', prompt)
        self.assertIn('"missing_facts"', prompt)
        self.assertIn('"entity_conflicts"', prompt)
        self.assertIn("事实点语义覆盖", prompt)
        self.assertIn("实体矛盾", prompt)
        self.assertIn("correct、partial、incorrect、correct_rejection、hallucination、manual_review、error", prompt)
        self.assertIn('关键事实：["事实一", "事实二"]', prompt)


class ResultAndReportTests(unittest.TestCase):
    def test_v4_results_use_a_separate_output_file(self):
        self.assertEqual(evaluation.RESULTS_JSONL.name, "eval_results_v4.jsonl")
        self.assertNotEqual(evaluation.RESULTS_JSONL, evaluation.ROOT / "eval_results.jsonl")

    def test_result_io_only_reads_and_writes_the_v4_path(self):
        record = {
            "schema_version": "4.0",
            "qid": "Q001",
            "benchmark": {"answerable": True, "key_facts": ["事实"]},
            "output": {},
            "scores": {},
            "review": {"classification": "correct"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v4_path = root / "eval_results_v4.jsonl"
            legacy_path = root / "eval_results.jsonl"
            legacy_path.write_text("legacy\n", encoding="utf-8")
            original_path = evaluation.RESULTS_JSONL
            try:
                evaluation.RESULTS_JSONL = v4_path
                evaluation.append_result(record)
                loaded = evaluation.load_results()
            finally:
                evaluation.RESULTS_JSONL = original_path

            self.assertEqual(legacy_path.read_text(encoding="utf-8"), "legacy\n")
            self.assertEqual(loaded, [record])

    def test_compact_results_keeps_only_current_fingerprint_latest_records(self):
        # 基准指纹变化后重跑 --run 会全量追加，旧指纹与同 qid 旧版本记录应被清理
        def record(qid, fingerprint, version):
            return {
                "schema_version": "4.0",
                "qid": qid,
                "benchmark": {"answerable": True, "key_facts": ["事实"]},
                "output": {},
                "scores": {},
                "review": {"classification": "correct"},
                "benchmark_fingerprint": fingerprint,
                "_version": version,
            }

        records = [
            record("Q001", "old_fp", 1),
            record("G001", "old_fp", 1),
            record("Q001", "current_fp", 2),
            record("Q001", "current_fp", 3),
            record("G002", "current_fp", 1),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "eval_results_v4.jsonl"
            results_path.write_text(
                "\n".join(json.dumps(entry, ensure_ascii=False) for entry in records) + "\n",
                encoding="utf-8",
            )
            original_path = evaluation.RESULTS_JSONL
            try:
                evaluation.RESULTS_JSONL = results_path
                evaluation.compact_results("current_fp")
                loaded = evaluation.load_results()
            finally:
                evaluation.RESULTS_JSONL = original_path

        self.assertEqual(len(loaded), 2)
        by_qid = {entry["qid"]: entry for entry in loaded}
        self.assertEqual(by_qid["Q001"]["_version"], 3)
        self.assertEqual(by_qid["G002"]["_version"], 1)
        self.assertTrue(all(entry["benchmark_fingerprint"] == "current_fp" for entry in loaded))

    def test_is_current_result_rejects_legacy_record_without_scoring_layers(self):
        legacy = {
            "qid": "Q001",
            "category": "题库",
            "question": "问题？",
            "match_type": "qa",
            "answer": "答案。",
        }

        self.assertFalse(evaluation.is_current_result(legacy))

    def test_load_done_qids_requires_matching_benchmark_fingerprint_and_excludes_errors(self):
        valid = {
            "schema_version": evaluation.SCHEMA_VERSION,
            "benchmark_fingerprint": "current",
            "qid": "Q001",
            "benchmark": {"answerable": True, "key_facts": ["事实"]},
            "output": {},
            "scores": {},
            "review": {"classification": "correct"},
        }
        stale = {**valid, "qid": "Q002", "benchmark_fingerprint": "old"}
        error = {**valid, "qid": "Q003", "review": {"classification": "error"}}
        with patch.object(evaluation, "load_results", return_value=[valid, stale, error]):
            done = evaluation.load_done_qids("current")

        self.assertEqual(done, {"Q001"})

    def test_load_done_qids_excludes_records_when_benchmark_answerable_changed(self):
        # 生成题不可回答误判被修正为可回答后，旧记录（answerable=false）不得视为已完成
        correct = {
            "benchmark_fingerprint": "current",
            "qid": "Q001",
            "benchmark": {"answerable": True},
            "review": {"classification": "correct"},
        }
        hallucinated = {
            "benchmark_fingerprint": "current",
            "qid": "G001",
            "benchmark": {"answerable": False},
            "review": {"classification": "hallucination"},
        }
        benchmark_items = {"Q001": {"answerable": True}, "G001": {"answerable": True}}
        with patch.object(evaluation, "load_results", return_value=[correct, hallucinated]):
            done = evaluation.load_done_qids("current", benchmark_items)

        self.assertEqual(done, {"Q001"})
        with patch.object(evaluation, "load_results", return_value=[hallucinated]):
            done = evaluation.load_done_qids("current", {"G001": {"answerable": False}})
        self.assertEqual(done, {"G001"})

    def test_make_result_record_uses_approved_scoring_format(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "问题？",
            "answerable": True,
            "reference_answer": "标准答案。",
            "key_facts": ["事实一", "事实二"],
            "source": "https://example.com",
            "evidence": ["标准答案。"],
        }

        record = evaluation.make_result_record(
            item,
            match_type="qa",
            confidence=0.95,
            actions=[],
            answer="标准答案。",
            elapsed=1.234,
            judge={"score": 1.0, "reason": "完全一致"},
        )

        self.assertEqual(record["schema_version"], "4.0")
        self.assertTrue(record["benchmark"]["answerable"])
        self.assertEqual(record["benchmark"]["key_facts"], ["事实一", "事实二"])
        self.assertEqual(record["benchmark"]["evidence"], ["标准答案。"])
        self.assertEqual(record["output"]["elapsed_seconds"], 1.23)
        self.assertTrue(record["output"]["answered"])
        self.assertEqual(record["scores"]["total"], 1.0)
        self.assertEqual(record["review"]["classification"], "correct")
        self.assertTrue(record["review"]["answerability_correct"])
        self.assertFalse(record["review"]["manual_review_required"])

    def test_make_result_record_classifies_runtime_failure_as_error(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "问题？",
            "answerable": True,
            "reference_answer": "标准答案。",
            "key_facts": ["标准答案"],
            "source": "https://example.com",
            "evidence": ["标准答案。"],
        }

        record = evaluation.make_result_record(
            item,
            match_type="error",
            confidence=0.0,
            actions=[],
            answer="[异常] timeout",
            elapsed=1.0,
            judge={"score": 0.0, "reason": "timeout"},
        )

        self.assertEqual(record["review"]["classification"], "error")
        self.assertTrue(record["review"]["manual_review_required"])

    def test_evaluate_item_uses_real_route_metadata_and_skips_judge_for_unanswerable(self):
        item = {
            "qid": "G001",
            "category": "生成",
            "question": "不存在的问题？",
            "answerable": False,
            "reference_answer": None,
            "key_facts": [],
            "source": [],
            "evidence": [],
            "unanswerable_reason": "知识库无相关证据",
        }
        route = SimpleNamespace(
            actions=["actions/idle.json"],
            answer="未找到足够信息，无法根据当前知识库回答。",
            match_type="direct",
            confidence=0.0,
        )
        async def process_input_with_metadata(question):
            return route

        system = SimpleNamespace(process_input_with_metadata=process_input_with_metadata)

        record = evaluation.evaluate_item(
            system,
            judge_client=None,
            judge_model="test-model",
            item=item,
            benchmark_fingerprint="fingerprint",
        )

        self.assertEqual(record["benchmark_fingerprint"], "fingerprint")
        self.assertEqual(record["output"]["match_type"], "direct")
        self.assertEqual(record["output"]["actions"], ["actions/idle.json"])
        self.assertEqual(record["review"]["classification"], "correct_rejection")
        self.assertEqual(record["scores"]["total"], 1.0)

    def test_evaluate_item_preserves_real_answer_when_judge_fails(self):
        item = {
            "qid": "Q001",
            "category": "题库",
            "question": "问题？",
            "answerable": True,
            "reference_answer": "标准答案。",
            "key_facts": ["标准答案"],
            "source": "https://example.com",
            "evidence": ["标准答案。"],
        }
        route = SimpleNamespace(
            actions=[],
            answer="标准答案。",
            match_type="qa",
            confidence=0.9,
        )
        async def process_input_with_metadata(question):
            return route

        system = SimpleNamespace(process_input_with_metadata=process_input_with_metadata)
        judge_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: (_ for _ in ()).throw(TimeoutError("裁判超时"))
        )))

        record = evaluation.evaluate_item(
            system,
            judge_client,
            judge_model="test-model",
            item=item,
            benchmark_fingerprint="fingerprint",
        )

        # 裁判失败不覆盖真实系统回答和路由元数据
        self.assertEqual(record["output"]["match_type"], "qa")
        self.assertEqual(record["output"]["answer"], "标准答案。")
        self.assertIn("模型裁判失败", record["review"]["judge_reason"])
        self.assertTrue(record["review"]["manual_review_required"])

    def test_run_eval_validates_benchmark_before_constructing_system(self):
        invalid = {
            "schema_version": evaluation.SCHEMA_VERSION,
            "metadata": {"total": 1, "bank_count": 1, "generated_count": 0},
            "items": [{"qid": "Q001", "question": "问题？", "answerable": True}],
        }
        fake_hybrid = SimpleNamespace(HybridAgentSystem=lambda mapping: self.fail("不应构造系统"))

        with patch.object(evaluation, "load_benchmark", return_value=invalid), patch.object(
            evaluation, "BENCHMARK_JSON", SimpleNamespace(exists=lambda: True)
        ), patch.dict(sys.modules, {"skill_chatchat_hybrid": fake_hybrid}):
            with self.assertRaisesRegex(ValueError, "500"):
                evaluation.run_eval()

    def test_stats_rejects_non_finite_scores(self):
        record = {
            "qid": "Q001",
            "benchmark": {"answerable": True, "evidence": ["证据"]},
            "output": {"match_type": "qa", "answered": True, "elapsed_seconds": 1.0},
            "scores": {"fact": 1.0, "entity": 1.0, "judge": math.nan, "total": 1.0},
            "review": {"answerability_correct": True, "manual_review_required": False},
        }

        with self.assertRaisesRegex(ValueError, "非有限分数"):
            evaluation.stats_of([record])

    def test_stats_and_report_follow_approved_dimensions(self):
        records = [
            {
                "schema_version": "3.0",
                "qid": "Q001",
                "category": "题库",
                "question": "问题一？",
                "benchmark": {"answerable": True, "reference_answer": "答案一。", "source": "https://example.com/1", "evidence": ["答案一。"]},
                "output": {"match_type": "qa", "confidence": 0.9, "actions": [], "answer": "答案一。", "answered": True, "elapsed_seconds": 1.0},
                "scores": {"fact": 1.0, "entity": 1.0, "judge": 1.0, "total": 1.0},
                "review": {"classification": "优秀", "answerability_correct": True, "manual_review_required": False, "manual_review_reason": ""},
            },
            {
                "schema_version": "3.0",
                "qid": "G001",
                "category": "生成",
                "question": "问题二？",
                "benchmark": {"answerable": False, "reference_answer": None, "source": [], "evidence": [], "unanswerable_reason": "知识库无相关证据"},
                "output": {"match_type": "none", "confidence": 0.0, "actions": [], "answer": "未找到足够信息，无法根据当前知识库回答。", "answered": False, "elapsed_seconds": 3.0},
                "scores": {"fact": 1.0, "entity": 1.0, "judge": 1.0, "total": 1.0},
                "review": {"classification": "优秀", "answerability_correct": True, "manual_review_required": False, "manual_review_reason": ""},
            },
            {
                "schema_version": "3.0",
                "qid": "G002",
                "category": "生成",
                "question": "问题三？",
                "benchmark": {"answerable": True, "reference_answer": "答案三。", "source": ["doc.md"], "evidence": ["答案三。"]},
                "output": {"match_type": "knowledge", "confidence": 0.7, "actions": [], "answer": "错误回答", "answered": True, "elapsed_seconds": 2.0},
                "scores": {"fact": 0.0, "entity": 0.0, "judge": 0.1, "total": 0.03},
                "review": {"classification": "不合格", "answerability_correct": True, "manual_review_required": False, "manual_review_reason": ""},
            },
        ]

        stats = evaluation.stats_of(records)
        report = evaluation.render_report(
            records,
            health={"documents": 8, "qa_pairs": 510, "chunks": 100, "status": "ok"},
            model="test-model",
            generated_questions=["问题二？", "问题三？"],
            generated_at="2026-08-10 12:00:00",
        )

        self.assertAlmostEqual(stats["fact"], 2 / 3)
        self.assertAlmostEqual(stats["entity"], 2 / 3)
        self.assertEqual(stats["judge"], 0.7)
        self.assertAlmostEqual(stats["quality_score"], 2.03 / 3)
        self.assertEqual(stats["answerability_accuracy"], 1.0)
        self.assertEqual(stats["answer_rate"], 2 / 3)
        self.assertAlmostEqual(stats["retrieval_coverage_weighted"], 1.7 / 3)
        self.assertIn("事实一致性 45%", report)
        self.assertIn("实体一致性 25%", report)
        self.assertIn("模型裁判 30%", report)
        self.assertIn("质量得分", report)
        self.assertIn("可回答性准确率", report)
        self.assertIn("不可回答", report)
        self.assertIn("分类", report)
        self.assertIn("人工复核", report)
        self.assertIn("证据基准", report)
        self.assertIn("检索覆盖加权", report)


if __name__ == "__main__":
    unittest.main()
