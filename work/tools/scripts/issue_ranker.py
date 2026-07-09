#!/usr/bin/env python3
"""Phase 6b: rank validated issues and select the final set.

Drops rejected issues (section 11.1), orders survivors by status then
confidence then normative level (section 13 / confidence_weights.json
``ranking``), and assigns stable sequential issue IDs and report paths.

Output: .agent-work/ranked_issues.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import rfc_common as rc


def rank_key(issue: dict, level_order: list[str]) -> tuple:
    status_rank = {"confirmed": 0, "probable": 1, "rejected": 2}.get(issue["status"], 3)
    level_rank = level_order.index(issue["normative_level"]) if issue["normative_level"] in level_order else 99
    return (status_rank, -issue.get("confidence", 0.0), level_rank)


def short_id(issue: dict, seq: int) -> str:
    rfc = issue["design_evidence"].get("rfc", "RFC").replace("RFC", "")
    topic = issue.get("protocol_area") or issue.get("detection_type") or "DIFF"
    slug = topic.upper().replace("_", "-")[:20]
    return f"RFC{rfc}-{slug}-{seq:03d}" if rfc else f"{slug}-{seq:03d}"


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Rank and select final issues.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    work = rc.agent_work_dir(Path(args.code_root))
    validated_path = work / "validated_issues.json"
    if not validated_path.exists():
        print("[ranker] validated_issues.json missing", file=sys.stderr)
        return 0
    validated = rc.load_json(validated_path)
    weights = rc.load_config("confidence_weights.json")
    level_order = weights.get("ranking", {}).get("normative_level_order", [])

    kept = [i for i in validated.get("issues", []) if i["status"] in ("confirmed", "probable")]
    kept.sort(key=lambda i: rank_key(i, level_order))

    for seq, issue in enumerate(kept, start=1):
        issue["issue_id"] = short_id(issue, seq)
        issue["report_path"] = f"/result/{seq:02d}-{rc.slugify(issue['title'])}.md"

    rc.save_json(work / "ranked_issues.json", {
        "ranked_at": rc.now_iso(),
        "kept": len(kept),
        "rejected_dropped": len(validated.get("issues", [])) - len(kept),
        "issues": kept,
    })
    print(f"[ranker] kept {len(kept)} issues, dropped {len(validated.get('issues', [])) - len(kept)} rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
