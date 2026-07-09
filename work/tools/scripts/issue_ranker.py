#!/usr/bin/env python3
"""Phase 6b: rank validated issues and select the final set.

Only confirmed issues are promoted into the formal result set. Probable issues
are sorted into a review queue so they remain inspectable without being emitted
as final findings. Rejected issues are dropped from both outputs.

Output: .agent-work/ranked_issues.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import rfc_common as rc

LEVEL_ORDER = [
    "MUST",
    "MUST NOT",
    "SHALL",
    "SHALL NOT",
    "REQUIRED",
    "SHOULD",
    "SHOULD NOT",
    "RECOMMENDED",
    "MAY",
    "OPTIONAL",
    "design-requirement",
    "unknown",
]


def rank_key(issue: dict, level_order: list[str]) -> tuple:
    level_rank = level_order.index(issue["normative_level"]) if issue["normative_level"] in level_order else 99
    return (-issue.get("confidence", 0.0), level_rank)


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
    level_order = LEVEL_ORDER

    all_issues = validated.get("issues", [])
    kept = [i for i in all_issues if i["status"] == "confirmed"]
    probable = [i for i in all_issues if i["status"] == "probable"]
    kept.sort(key=lambda i: rank_key(i, level_order))
    probable.sort(key=lambda i: rank_key(i, level_order))

    for seq, issue in enumerate(kept, start=1):
        issue["issue_id"] = short_id(issue, seq)
        issue["report_path"] = f"/result/{seq:02d}-{rc.slugify(issue['title'])}.md"
    for seq, issue in enumerate(probable, start=1):
        issue["review_id"] = f"REVIEW-{seq:03d}"
        issue["queue_reason"] = "opencode verdict is probable; not eligible for final output"

    rc.save_json(work / "ranked_issues.json", {
        "ranked_at": rc.now_iso(),
        "kept": len(kept),
        "probable_queued": len(probable),
        "rejected_dropped": len(all_issues) - len(kept) - len(probable),
        "issues": kept,
    })
    rc.save_json(work / "probable_review_queue.json", {
        "queued_at": rc.now_iso(),
        "probable": len(probable),
        "issues": probable,
    })
    print(f"[ranker] kept {len(kept)} confirmed issues, queued {len(probable)} probable, "
          f"dropped {len(all_issues) - len(kept) - len(probable)} rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
