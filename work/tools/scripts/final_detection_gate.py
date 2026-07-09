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
    "false_positive_controls", "related_files", "report_path", "agent_review",
]
MIN_ISSUES = 4


def check_result_schema(doc: dict) -> list[str]:
    """Validate the parts of output_schema.json that matter for the gate.

    Keep this dependency-free: the competition runtime may not have jsonschema
    installed, and the schema is small enough for direct checks.
    """
    problems: list[str] = []
    required_root = ["generated_at", "tool", "code_root", "benchmark", "summary", "issues"]
    for field in required_root:
        if field not in doc or doc[field] in ("", None):
            problems.append(f"issues.json: missing/empty root field '{field}'")
    if doc.get("tool") not in (None, "goal-agent-rfc-diff"):
        problems.append("issues.json: tool must be 'goal-agent-rfc-diff'")
    if "issues" in doc and not isinstance(doc.get("issues"), list):
        problems.append("issues.json: 'issues' must be an array")

    summary = doc.get("summary")
    if not isinstance(summary, dict):
        problems.append("issues.json: 'summary' must be an object")
    else:
        for field in ["total", "confirmed", "probable", "high_confidence"]:
            value = summary.get(field)
            if not isinstance(value, int) or value < 0:
                problems.append(f"issues.json: summary.{field} must be a non-negative integer")
        if isinstance(doc.get("issues"), list) and summary.get("total") != len(doc["issues"]):
            problems.append("issues.json: summary.total does not match issues length")
    return problems


def check_issue(issue: dict) -> list[str]:
    problems = []
    for f in REQUIRED_FIELDS:
        if f not in issue or issue[f] in ("", None, []):
            problems.append(f"{issue.get('issue_id','?')}: missing/empty field '{f}'")
    de = issue.get("design_evidence", {})
    if not de.get("rfc") or not de.get("quote"):
        problems.append(f"{issue.get('issue_id','?')}: incomplete design evidence")
    ce = issue.get("code_evidence", [])
    if not ce:
        problems.append(f"{issue.get('issue_id','?')}: issue lacks code evidence")
    if issue.get("status") != "confirmed":
        problems.append(f"{issue.get('issue_id','?')}: non-confirmed issue leaked into main result")
    review = issue.get("agent_review", {})
    if not isinstance(review, dict) or review.get("source") != "opencode":
        problems.append(f"{issue.get('issue_id','?')}: missing opencode agent_review source")
    if not isinstance(review, dict) or not review.get("generalization_rationale"):
        problems.append(f"{issue.get('issue_id','?')}: missing agent generalization rationale")
    if not isinstance(review, dict) or not review.get("tool_trace"):
        problems.append(f"{issue.get('issue_id','?')}: missing agent tool_trace")
    return problems


def check_issues_jsonl(path: Path, issues: list[dict]) -> list[str]:
    problems: list[str] = []
    if not path.exists():
        return problems
    try:
        raw_lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        parsed = [json.loads(line) for line in raw_lines]
    except json.JSONDecodeError as exc:
        return [f"issues.jsonl not valid JSONL: {exc}"]
    if len(parsed) != len(issues):
        problems.append("issues.jsonl line count does not match issues.json issues length")
    json_ids = [i.get("issue_id") for i in issues]
    jsonl_ids = [i.get("issue_id") for i in parsed]
    if json_ids != jsonl_ids:
        problems.append("issues.jsonl issue_id order does not match issues.json")
    return problems


def append_run_ledger(work: Path, event: dict) -> None:
    path = work / "agent_run_ledger.jsonl"
    rc.ensure_dir(path.parent)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"recorded_at": rc.now_iso(), **event}, ensure_ascii=False) + "\n")


def update_loop_state(work: Path, passed: bool, kept: int, problems: list[str]) -> None:
    state_path = work / "agent_loop_state.json"
    if not state_path.exists():
        return
    try:
        state = rc.load_json(state_path)
    except json.JSONDecodeError:
        return
    state["updated_at"] = rc.now_iso()
    state["current_phase"] = "complete" if passed else "needs_opencode_iteration"
    state.setdefault("progress_metrics", {})["final_confirmed"] = kept
    state["stop_reason"] = (
        "final_gate_passed"
        if passed else
        "final_gate_failed: " + "; ".join(problems[:5])
    )
    state["next_todos"] = [] if passed else [
        "Resume opencode semantic review from agent_review_queue.json and agent_run_ledger.jsonl.",
        "Fix evidence gaps reported by final_detection_gate.json, then rerun review/report/gate.",
    ]
    rc.save_json(state_path, state)


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
            if not isinstance(doc, dict):
                problems.append("issues.json root must be an object")
                doc = {}
            problems.extend(check_result_schema(doc))
            issues = doc.get("issues", [])
            if not isinstance(issues, list):
                issues = []
        except json.JSONDecodeError as exc:
            problems.append(f"issues.json not valid JSON: {exc}")
    else:
        problems.append("issues.json missing")

    issues_jsonl_path = result_root / "issues.jsonl"
    checks["issues_jsonl_exists"] = issues_jsonl_path.exists()
    problems.extend(check_issues_jsonl(issues_jsonl_path, issues))
    # 00-summary.md also matches [0-9][0-9]-*.md, so exclude it: we want a
    # real per-issue report (01-*.md, 02-*.md, ...).
    checks["at_least_one_issue_md"] = any(
        p for p in result_root.glob("[0-9][0-9]-*.md")
        if not p.name.startswith("00-")
    )

    for issue in issues:
        problems.extend(check_issue(issue))

    kept = [i for i in issues if i.get("status") == "confirmed"]
    checks["min_4_confirmed"] = len(kept) >= MIN_ISSUES
    if len(kept) < MIN_ISSUES:
        problems.append(
            f"Only {len(kept)} confirmed issues (< {MIN_ISSUES}). "
            "Reason: insufficient evidence / RFC fetch blocked / code paths unconfirmed. "
            "No issues fabricated."
        )

    checks["all_issues_have_design_evidence"] = all(
        i.get("design_evidence", {}).get("quote") for i in issues)
    checks["all_issues_have_code_evidence"] = all(i.get("code_evidence") for i in issues)
    checks["only_confirmed_in_main"] = all(i.get("status") == "confirmed" for i in issues)

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
    work = rc.agent_work_dir(Path(args.code_root))
    update_loop_state(work, passed, len(kept), problems)
    append_run_ledger(work, {
        "event": "final_gate",
        "actor": "helper",
        "summary": "Evaluated final result artifacts against detection gate.",
        "status": "passed" if passed else "failed",
        "metrics": {
            "issues_kept": len(kept),
            "min_required": MIN_ISSUES,
            "problem_count": len(problems),
        },
        "stop_reason": "final_gate_passed" if passed else "final_gate_failed",
        "next_todos": [] if passed else [
            "Inspect /logs/trace/final_detection_gate.json.",
            "Resume opencode review for missing evidence, tool_trace, or confirmed count shortfall.",
        ],
    })
    print(f"[gate] passed={passed} kept={len(kept)}/{MIN_ISSUES} problems={len(problems)}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
