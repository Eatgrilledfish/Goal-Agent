#!/usr/bin/env python3
"""Evaluate final output against the public F-Stack gold fixture.

This is a local regression oracle, not part of the detector. It reads final
confirmed issues and checks whether they cover the known public fixture issues.
Hidden evaluations must not depend on this file; the main pipeline never imports
or calls it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import rfc_common as rc

DEFAULT_GOLD = rc.TOOLS_DIR / "eval" / "public_fstack_gold.json"


def flatten_strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out: list[str] = []
        for value in obj.values():
            out.extend(flatten_strings(value))
        return out
    if isinstance(obj, list):
        out: list[str] = []
        for value in obj:
            out.extend(flatten_strings(value))
        return out
    return []


def normalize_text(parts: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def issue_files(issue: dict) -> set[str]:
    files = set()
    for ev in issue.get("code_evidence", []) or []:
        if isinstance(ev, dict) and ev.get("file"):
            files.add(str(ev["file"]))
    for rel in issue.get("related_files", []) or []:
        files.add(str(rel))
    return files


def any_file_matches(files: set[str], expected: list[str]) -> bool:
    if not expected:
        return True
    lowered = {f.lower() for f in files}
    for target in expected:
        tl = target.lower()
        if any(f == tl or f.endswith("/" + tl) for f in lowered):
            return True
    return False


def issue_matches_gold(issue: dict, gold: dict) -> bool:
    text = normalize_text(flatten_strings(issue))
    if str(gold.get("rfc", "")).lower() not in text:
        return False
    if not any_file_matches(issue_files(issue), gold.get("files_any", [])):
        return False
    sections = [str(s).lower() for s in gold.get("sections_any", [])]
    if sections and not any(section in text for section in sections):
        return False
    for group in gold.get("text_groups", []):
        if not any(str(term).lower() in text for term in group):
            return False
    return True


def evaluate(result_doc: dict, gold_doc: dict, min_matches: int | None = None,
             max_extra_rate: float | None = None) -> dict:
    confirmed = [i for i in result_doc.get("issues", []) if i.get("status") == "confirmed"]
    gold_issues = gold_doc.get("issues", [])
    required = min_matches if min_matches is not None else int(gold_doc.get("min_required_matches", 4))
    max_rate = (
        max_extra_rate
        if max_extra_rate is not None
        else float(gold_doc.get("max_extra_confirmed_rate", 0.5))
    )

    matches: dict[str, list[str]] = {}
    matched_issue_ids: set[str] = set()
    for gold in gold_issues:
        hit_ids = []
        for idx, issue in enumerate(confirmed, start=1):
            if issue_matches_gold(issue, gold):
                issue_id = issue.get("issue_id") or f"issue-{idx}"
                hit_ids.append(issue_id)
                matched_issue_ids.add(issue_id)
        if hit_ids:
            matches[gold["gold_id"]] = hit_ids

    missing = [g["gold_id"] for g in gold_issues if g["gold_id"] not in matches]
    extra_confirmed = max(0, len(confirmed) - len(matched_issue_ids))
    extra_rate = (extra_confirmed / len(confirmed)) if confirmed else 0.0
    return {
        "gold_name": gold_doc.get("name"),
        "confirmed_count": len(confirmed),
        "matched_gold_count": len(matches),
        "required_gold_matches": required,
        "missing_gold_ids": missing,
        "matches": matches,
        "matched_issue_ids": sorted(matched_issue_ids),
        "extra_confirmed_count": extra_confirmed,
        "extra_confirmed_rate": round(extra_rate, 4),
        "max_extra_confirmed_rate": max_rate,
        "pass": len(matches) >= required and extra_rate <= max_rate,
    }


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Evaluate public F-Stack gold issue coverage.")
    parser.add_argument("--result", default="/result/issues.json")
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    parser.add_argument("--output", default="")
    parser.add_argument("--min-matches", type=int, default=None)
    parser.add_argument("--max-extra-rate", type=float, default=None)
    args = parser.parse_args(argv)

    result_path = Path(args.result)
    gold_path = Path(args.gold)
    if not result_path.exists():
        print(f"[public-gold] result missing: {result_path}", file=sys.stderr)
        return 2
    if not gold_path.exists():
        print(f"[public-gold] gold fixture missing: {gold_path}", file=sys.stderr)
        return 2

    report = evaluate(
        rc.load_json(result_path),
        rc.load_json(gold_path),
        min_matches=args.min_matches,
        max_extra_rate=args.max_extra_rate,
    )
    if args.output:
        rc.save_json(Path(args.output), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
