#!/usr/bin/env python3
"""Static checker for unstable list ordering and pagination boundaries."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check sorting and pagination risk patterns.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def check(root: Path) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    report_dir = paths.work / "checker_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    issues: list[dict[str, Any]] = []
    code_dir = root / "code"
    if code_dir.exists():
        for path in sorted(code_dir.rglob("*.java")):
            if "/src/test/" in path.as_posix():
                continue
            text = runner.strip_java_comments(runner.read_text(path))
            rel_path = runner.rel(root, path)
            lowered_path = rel_path.lower()
            if not any(token in lowered_path + text.lower() for token in ("list", "search", "page", "history", "stat", "findall", "分页", "排序")):
                continue
            if re.search(r"\bfindAll\s*\(\s*\)", text) and not re.search(r"Sort\.|OrderBy|order\s+by|PageRequest\.of\s*\([^)]*Sort", text, re.I):
                issues.append({
                    "issue_id": f"ISSUE-SORT-FINDALL-{runner.stable_hash(rel_path).upper()}",
                    "severity": "P1",
                    "type": "sorting_pagination",
                    "summary": "findAll/list query may return unstable ordering",
                    "suspected_files": [rel_path],
                    "evidence": "findAll() without Sort or order by",
                    "repair_hint": "Add a deterministic Sort/order by using documented ordering or createdAt/id tie-breakers.",
                })
            if re.search(r"PageRequest\.of\s*\(", text) and "Sort." not in text:
                issues.append({
                    "issue_id": f"ISSUE-PAGE-NOSORT-{runner.stable_hash(rel_path).upper()}",
                    "severity": "P1",
                    "type": "sorting_pagination",
                    "summary": "PageRequest may lack stable Sort",
                    "suspected_files": [rel_path],
                    "evidence": "PageRequest.of(...) without Sort",
                    "repair_hint": "Use PageRequest.of(page, size, Sort.by(...)) with stable tie-breakers.",
                })
            if re.search(r"new\s+(?:HashMap|HashSet)\s*<", text) and re.search(r"return\s+\w+", text):
                issues.append({
                    "issue_id": f"ISSUE-SORT-HASH-{runner.stable_hash(rel_path).upper()}",
                    "severity": "P2",
                    "type": "sorting_pagination",
                    "summary": "HashMap/HashSet may leak nondeterministic iteration order",
                    "suspected_files": [rel_path],
                    "evidence": "HashMap/HashSet construction in list-like code",
                    "repair_hint": "Return ordered DTO lists or LinkedHashMap/TreeMap only when ordering is semantically defined.",
                })
    report = {
        "generated_at": runner.now_iso(),
        "checker": "sorting_pagination",
        "issues": issues,
        "summary": {"total": len(issues), "p1": sum(1 for i in issues if i["severity"] == "P1")},
    }
    runner.write_json(report_dir / "sorting_pagination_checker.json", report)
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    report = check(root)
    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
