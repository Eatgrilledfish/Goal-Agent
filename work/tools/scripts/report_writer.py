#!/usr/bin/env python3
"""Write generic design/implementation inconsistency result artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import stage_artifact_validator as sav
import verdict_validator as vv


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def sorted_confirmed(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (issue for issue in issues if issue.get("status") == "confirmed"),
        key=lambda issue: (
            SEVERITY_ORDER.get(str(issue.get("severity")), 9),
            -float(issue.get("confidence") or 0),
            str(issue.get("title") or ""),
        ),
    )


def render_issue(issue: dict[str, Any]) -> str:
    lines = [f"# {issue['title']}", "", "## Summary", "", str(issue.get("inconsistency") or ""), ""]
    lines.extend([
        "## Semantic Delta", "",
        f"- Expected: {issue.get('expected_behavior', '')}",
        f"- Actual: {issue.get('actual_behavior', '')}",
        f"- Normative strength: {issue.get('normative_strength', '')}",
        "",
    ])
    lines.extend(["## Design Evidence", ""])
    for evidence in issue.get("design_evidence", []):
        lines.extend([
            f"- Document: `{evidence.get('document') or evidence.get('path', '')}`",
            f"- Section: {evidence.get('section', '')}",
            f"- Location: `{evidence.get('path', '')}:{evidence.get('line_start', 0)}-{evidence.get('line_end', 0)}`",
            "",
            f"> {ac.normalize_text(str(evidence.get('quote') or ''))}",
            "",
        ])
    lines.extend(["## Code Evidence", ""])
    for evidence in issue.get("code_evidence", []):
        lines.extend([
            f"- File: `{evidence.get('file') or evidence.get('path', '')}`",
            f"- Symbol: `{evidence.get('symbol', '')}`",
            f"- Lines: `{evidence.get('line_start', 0)}-{evidence.get('line_end', 0)}`",
            "",
            "```text",
            str(evidence.get("snippet") or "").rstrip(),
            "```",
            "",
        ])
    lines.extend([
        "## Inconsistency Reason", "", str(issue.get("inconsistency") or ""), "",
        "## Functional Impact", "", str(issue.get("impact") or ""), "",
        "## Scope", "", str(issue.get("scope_applicability") or ""), "",
        "## False-positive Exclusion", "",
    ])
    for check in issue.get("false_positive_checks", []):
        lines.append(f"- {check.get('question', '')}: {check.get('result', '')} (`{check.get('target', '')}` via {check.get('method', '')})")
    dynamic = issue.get("dynamic_validation") if isinstance(issue.get("dynamic_validation"), dict) else {}
    lines.extend([
        "", "## Dynamic Validation", "",
        f"- Status: `{dynamic.get('status', '')}`",
        f"- Probe: `{dynamic.get('probe_id', '')}`",
        f"- Interpretation: {dynamic.get('reason', '')}",
    ])
    review = issue.get("agent_review", {})
    critic = review.get("critic_review", {}) if isinstance(review, dict) else {}
    lines.extend([
        "", "## Agent Review", "",
        f"- Session: `{review.get('session_id', '')}`",
        f"- Critic: `{critic.get('review_id', '')}` ({critic.get('decision', '')})",
        f"- Confidence: {issue.get('confidence', 0)}",
        f"- Severity: {issue.get('severity', '')}",
        f"- Generalization: {review.get('generalization_rationale', '')}",
        "",
    ])
    return "\n".join(lines)


def render_summary(result: dict[str, Any], probable_count: int) -> str:
    lines = [
        "# Design / Implementation Inconsistency Review", "",
        "## Run", "",
        f"- Session: `{result['session_id']}`",
        f"- Code root: `{result['code_root']}`",
        f"- Design root: `{result['design_root']}`",
        f"- Confirmed issues: {result['summary']['confirmed']}",
        f"- Probable issues retained for review: {probable_count}",
        "", "## Confirmed Issues", "",
        "| ID | Severity | Confidence | Dynamic validation | Title | Primary code location |",
        "|---|---|---:|---|---|---|",
    ]
    for issue in result["issues"]:
        evidence = issue.get("code_evidence", [{}])[0]
        location = evidence.get("file") or evidence.get("path") or ""
        lines.append(
            f"| {issue['issue_id']} | {issue.get('severity', '')} | {issue.get('confidence', 0)} | "
            f"{issue.get('dynamic_validation', {}).get('status', '')} | "
            f"{issue.get('title', '')} | `{location}` |"
        )
    lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    root = ac.state_root(log_root, args.state_root)
    path_errors = ac.session_path_errors(
        root, code_root=code_root, design_root=design_root, result_root=result_root, log_root=log_root,
    )
    if path_errors:
        print(json.dumps({"reported": False, "errors": path_errors}))
        return 2
    state = ac.load_json(root / "agent_loop_state.json")
    session_id = str(state.get("session_id") or "")
    coverage_trace_path = log_root / "trace" / "coverage_validation.json"
    coverage_trace = (
        ac.load_json(coverage_trace_path) if coverage_trace_path.is_file() else {}
    )
    expected_inputs, expected_combined = sav._input_digests(
        root, sav._stage_inputs(root, "coverage"),
    )
    coverage_errors: list[str] = []
    if coverage_trace.get("passed") is not True or coverage_trace.get("closed") is not True:
        coverage_errors.append("coverage validation is not closed")
    if coverage_trace.get("session_id") != session_id:
        coverage_errors.append("coverage validation belongs to a different session")
    if coverage_trace.get("input_digests") != expected_inputs:
        coverage_errors.append("coverage validation input digests are stale")
    if coverage_trace.get("combined_input_sha256") != expected_combined:
        coverage_errors.append("coverage validation combined digest is stale")
    if coverage_trace.get("coverage_provenance_sha256") != \
            sav.coverage_provenance_sha256(root):
        coverage_errors.append("coverage validation merge provenance is stale")
    if coverage_trace.get("claim_review_provenance_sha256") != \
            sav.claim_review_provenance_sha256(root):
        coverage_errors.append("coverage validation claim-review provenance is stale")
    if coverage_errors:
        print(json.dumps({"reported": False, "errors": coverage_errors}))
        return 2
    evidence_trace_path = log_root / "trace" / "evidence_validation.json"
    evidence_trace = (
        ac.load_json(evidence_trace_path) if evidence_trace_path.is_file() else {}
    )
    validated_path = root / "validated_issues.json"
    evidence_errors: list[str] = []
    if evidence_trace.get("passed") is not True:
        evidence_errors.append("evidence validation has not passed")
    if evidence_trace.get("session_id") != session_id:
        evidence_errors.append("evidence validation belongs to a different session")
    if evidence_trace.get("input_digests") != vv.evidence_input_digests(root):
        evidence_errors.append("evidence validation input digests are stale")
    validated_digest = ac.sha256_file(validated_path) if validated_path.is_file() else ""
    if evidence_trace.get("validated_issues_sha256") != validated_digest:
        evidence_errors.append("validated_issues.json is stale or changed after review")
    if evidence_errors:
        print(json.dumps({"reported": False, "errors": evidence_errors}))
        return 2
    ac.ensure_dir(result_root)
    validated = ac.load_json(validated_path)
    confirmed = sorted_confirmed(validated.get("issues", []))
    probable = [issue for issue in validated.get("issues", []) if issue.get("status") == "probable"]
    for index, issue in enumerate(confirmed, start=1):
        issue["issue_id"] = f"ISSUE-{index:03d}"
        issue["report_path"] = str(result_root / f"{index:02d}-{ac.slugify(str(issue.get('title') or 'issue'))}.md")

    for old in result_root.glob("[0-9][0-9]-*.md"):
        old.unlink()
    result = {
        "generated_at": ac.now_iso(),
        "tool": "goal-agent-design-code-diff",
        "session_id": state.get("session_id", ""),
        "code_root": str(code_root),
        "design_root": str(design_root),
        "summary": {
            "total": len(confirmed),
            "confirmed": len(confirmed),
            "probable": len(probable),
            "high_confidence": sum(float(issue.get("confidence") or 0) >= 0.8 for issue in confirmed),
        },
        "issues": confirmed,
    }
    ac.save_json(result_root / "issues.json", result)
    (result_root / "issues.jsonl").write_text(
        "\n".join(json.dumps(issue, ensure_ascii=False) for issue in confirmed) + ("\n" if confirmed else ""),
        encoding="utf-8",
    )
    ac.save_json(root / "probable_review_queue.json", {
        "generated_at": ac.now_iso(), "session_id": state.get("session_id", ""), "issues": probable,
    })
    (result_root / "00-summary.md").write_text(render_summary(result, len(probable)), encoding="utf-8")
    for issue in confirmed:
        Path(issue["report_path"]).write_text(render_issue(issue), encoding="utf-8")

    state["updated_at"] = ac.now_iso()
    state["status"] = "reported"
    state["current_phase"] = "final_gate"
    state.setdefault("metrics", {}).update({"confirmed": len(confirmed), "probable": len(probable)})
    state["next_actions"] = ["Run the final gate; resume semantic investigation if it reports a gap."]
    ac.save_json(root / "agent_loop_state.json", state)
    ac.append_jsonl(root / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": state.get("session_id", ""),
        "event": "report_handoff", "actor": "report_helper", "phase": "reporting", "status": "complete",
        "summary": "Wrote confirmed-only result artifacts.",
        "metrics": {"confirmed": len(confirmed), "probable": len(probable)},
    })
    print(json.dumps(result["summary"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write confirmed Goal-Agent reports.")
    ac.add_common_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
