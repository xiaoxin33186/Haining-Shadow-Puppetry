from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from portable_knowledge_qa import KnowledgeQAConfig, KnowledgeQAEngine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run grounded knowledge regression cases")
    parser.add_argument("--root", default=str(Path.cwd()))
    parser.add_argument("--cases", default="evaluation/knowledge-regression.json")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    cases_path = Path(args.cases).expanduser()
    if not cases_path.is_absolute():
        cases_path = root / cases_path
    payload = json.loads(cases_path.read_text(encoding="utf-8-sig"))
    cases = payload.get("cases", [])
    engine = KnowledgeQAEngine(KnowledgeQAConfig(discovery_root=root))

    results = []
    durations = []
    for case in cases:
        started = time.perf_counter()
        retrieval = engine.retrieve(str(case["query"]))
        duration_ms = (time.perf_counter() - started) * 1000
        durations.append(duration_ms)

        expected_types = case.get("expected_match_type", "")
        if isinstance(expected_types, str):
            expected_types = [expected_types]
        evidence_text = "\n".join(
            (retrieval.authoritative_answer, *retrieval.contexts)
        )
        qa_ids = {source.qa_id for source in retrieval.sources if source.qa_id}
        checks = {
            "match_type": retrieval.match_type in expected_types,
            "qa_id": (
                not case.get("expected_qa_id")
                or case["expected_qa_id"] in qa_ids
            ),
            "must_contain": all(
                value in evidence_text for value in case.get("must_contain", [])
            ),
            "must_not_contain": all(
                value not in evidence_text for value in case.get("must_not_contain", [])
            ),
        }
        results.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "actual_match_type": retrieval.match_type,
                "actual_qa_ids": sorted(qa_ids),
                "duration_ms": round(duration_ms, 3),
                "checks": checks,
                "passed": all(checks.values()),
            }
        )

    passed = sum(1 for result in results if result["passed"])
    output = {
        "suite": payload.get("suite", cases_path.stem),
        "cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "latency_ms": {
            "median": round(statistics.median(durations), 3) if durations else 0.0,
            "p95": round(_percentile(durations, 0.95), 3) if durations else 0.0,
        },
        "health": engine.health(),
        "failed_cases": [result for result in results if not result["passed"]],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["failed"] == 0 else 1


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


if __name__ == "__main__":
    raise SystemExit(main())
