#!/usr/bin/env python3
"""Phase 8: final detection gate.

Verifies the run produced a complete, machine-readable, evidence-backed result
(section 9.10). Writes a gate verdict to ``/logs/trace/final_detection_gate.json``.
Never fabricates issues when fewer than 4 are found -- it records the shortfall
and the reason instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import rfc_common as rc

REQUIRED_FIELDS = [
    "issue_id", "title", "status", "confidence", "normative_level",
    "design_evidence", "code_evidence", "inconsistency", "impact",
    "false_positive_controls", "related_files", "report_path",
]
MIN_ISSUES = 4


def check_issue(issue: dict) -> list[str]:
    problems = []
    for f in REQUIRED_FIELDS:
        if f not in issue or issue[f] in ("", None, []):
            problems.append(f"{issue.get('issue_id','?')}: missing/empty field '{f}'")
    de = issue.get("design_evidence", {})
    if not de.get("rfc") or not de.get("quote"):
        problems.append(f"{issue.get('issue_id','?')}: incomplete design evidence")
    ce = issue.get("code_evidence", [])
    if not ce and issue.get("status") != "probable":
        # gaps may legitimately lack code evidence; only flag for confirmed
        if issue.get("status") == "confirmed":
            problems.append(f"{issue.get('issue_id','?')}: confirmed issue lacks code evidence")
    if issue.get("status") == "rejected":
        problems.append(f"{issue.get('issue_id','?')}: rejected issue leaked into main result")
    return problems


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Final detection gate.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    result_root = Path(args.result_root)
    log_root = rc.ensure_dir(Path(args.log_root) / "trace")

    checks: dict[str, bool] = {}
    problems: list[str] = []

    issues_path = result_root / "issues.json"
    summary_path = result_root / "00-summary.md"
    checks["issues_json_exists"] = issues_path.exists()
    checks["summary_md_exists"] = summary_path.exists()

    issues: list[dict] = []
    if issues_path.exists():
        try:
            doc = rc.load_json(issues_path)
            issues = doc.get("issues", [])
        except json.JSONDecodeError as exc:
            problems.append(f"issues.json not valid JSON: {exc}")
    else:
        problems.append("issues.json missing")

    checks["issues_jsonl_exists"] = (result_root / "issues.jsonl").exists()
    # 00-summary.md also matches [0-9][0-9]-*.md, so exclude it: we want a
    # real per-issue report (01-*.md, 02-*.md, ...).
    checks["at_least_one_issue_md"] = any(
        p for p in result_root.glob("[0-9][0-9]-*.md")
        if not p.name.startswith("00-")
    )

    for issue in issues:
        problems.extend(check_issue(issue))

    kept = [i for i in issues if i["status"] in ("confirmed", "probable")]
    checks["min_4_confirmed_or_probable"] = len(kept) >= MIN_ISSUES
    if len(kept) < MIN_ISSUES:
        problems.append(
            f"Only {len(kept)} confirmed/probable issues (< {MIN_ISSUES}). "
            "Reason: insufficient evidence / RFC fetch blocked / code paths unconfirmed. "
            "No issues fabricated."
        )

    checks["all_issues_have_design_evidence"] = all(
        i.get("design_evidence", {}).get("quote") for i in issues)
    checks["all_issues_have_code_evidence_or_gap"] = all(
        i.get("code_evidence") or i.get("status") == "probable" for i in issues)
    checks["no_rejected_in_main"] = all(i.get("status") != "rejected" for i in issues)

    passed = all(checks.values()) and not problems
    verdict = {
        "judged_at": rc.now_iso(),
        "tool": "goal-agent-rfc-diff",
        "passed": passed,
        "checks": checks,
        "issues_kept": len(kept),
        "min_required": MIN_ISSUES,
        "problems": problems,
    }
    rc.save_json(log_root / "final_detection_gate.json", verdict)
    print(f"[gate] passed={passed} kept={len(kept)}/{MIN_ISSUES} problems={len(problems)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
