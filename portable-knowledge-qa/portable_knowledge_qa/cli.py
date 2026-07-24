from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import KnowledgeQAConfig
from .engine import KnowledgeQAEngine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable grounded knowledge QA")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", action="append", default=[])
    common.add_argument("--qa-source", action="append", default=[])
    common.add_argument("--root", default=str(Path.cwd()))

    query_parser = subparsers.add_parser("query", parents=[common])
    query_parser.add_argument("question")
    query_parser.add_argument("--include-sources", action="store_true")

    search_parser = subparsers.add_parser("search", parents=[common])
    search_parser.add_argument("question")

    subparsers.add_parser("inspect", parents=[common])

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = KnowledgeQAConfig.from_paths(
        args.source,
        args.qa_source,
        discovery_root=Path(args.root),
    )
    engine = KnowledgeQAEngine(config)

    if args.command == "query":
        payload = engine.query(args.question).to_dict(
            include_sources=args.include_sources
        )
    elif args.command == "search":
        payload = engine.retrieve(args.question).to_dict(
            include_sources=True,
            include_evidence=True,
        )
    elif args.command == "inspect":
        payload = {**engine.health(), "document_details": engine.list_documents()}
    else:
        raise AssertionError(f"Unhandled command: {args.command}")

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0
