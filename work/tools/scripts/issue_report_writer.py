#!/usr/bin/env python3
"""Phase 7: write final result artifacts.

Generates, under ``/result``:
  issues.json      machine-readable main result (section 7.4 schema)
  issues.jsonl     one issue per line
  00-summary.md    overview table (section 13)
  01-*.md ...      one markdown report per issue (section 9.9 template)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import rfc_common as rc


def render_issue_md(issue: dict) -> str:
    de = issue["design_evidence"]
    ce_list = issue.get("code_evidence", [])
    lines = [f"# {issue['title']}", ""]
    lines.append("## 1. Summary")
    lines.append(issue.get("inconsistency", "") or "—")
    lines.append("")
    lines.append("## 2. Design / RFC Evidence")
    lines.append(f"- RFC: {de.get('rfc', '')}")
    lines.append(f"- Section: {de.get('section', '')}")
    lines.append(f"- Normative Level: {issue.get('normative_level', '')}")
    lines.append("- Requirement:")
    lines.append(f"> {de.get('quote', '')}")
    lines.append("")
    lines.append("## 3. Code Evidence")
    if ce_list:
        for ce in ce_list:
            lines.append(f"- File: `{ce.get('file', '')}`")
            lines.append(f"- Function / Symbol: `{ce.get('symbol', '')}`")
            lines.append(f"- Lines: `{ce.get('line_start', 0)}-{ce.get('line_end', 0)}`")
            lines.append("")
            lines.append("```c")
            snippet = ce.get("snippet", "").rstrip()
            lines.append(snippet if snippet else "(no snippet available)")
            lines.append("```")
            lines.append("")
    else:
        lines.append("_No code location found; reported as a protocol/feature gap._")
        lines.append("")
    lines.append("## 4. Inconsistency Explanation")
    lines.append(issue.get("inconsistency", "") or "—")
    lines.append("")
    lines.append("## 5. Impact")
    lines.append(issue.get("impact", "") or "—")
    lines.append("")
    lines.append("## 6. False Positive Control")
    for ctrl in issue.get("false_positive_controls", []):
        lines.append(f"* {ctrl}")
    if not issue.get("false_positive_controls"):
        lines.append("* (none)")
    lines.append("")
    if issue.get("agent_review"):
        review = issue["agent_review"]
        lines.append("## 7. Agent Review")
        lines.append(f"- Source: {review.get('source', '')}")
        if review.get("agent_notes"):
            lines.append(f"- Notes: {review.get('agent_notes', '')}")
        if review.get("generalization_rationale"):
            lines.append(f"- Generalization: {review.get('generalization_rationale', '')}")
        lines.append("")
    lines.append("## 8. Confidence")
    lines.append(f"{issue.get('status', '')}, score={issue.get('confidence', 0.0)}")
    if issue.get("fp_note"):
        lines.append("")
        lines.append(f"> Note: {issue['fp_note']}")
    lines.append("")
    return "\n".join(lines)


def build_summary(issues: list[dict], code_root: str, benchmark: str,
                  stats: dict) -> str:
    confirmed = sum(1 for i in issues if i["status"] == "confirmed")
    high_conf = sum(1 for i in issues if i["confidence"] >= 0.80)
    review_queue = stats.get("probable_review_queue", 0)
    lines = [
        "# RFC / Implementation Difference Detection Summary",
        "",
        "## Overview",
        "",
        f"- Code root: `{code_root}`",
        f"- Design source: `{benchmark}`",
        f"- RFC count loaded: {stats.get('rfc_count', 0)}",
        f"- Normative requirements extracted: {stats.get('requirement_count', 0)}",
        f"- Code files indexed: {stats.get('file_count', 0)}",
        f"- Candidate inconsistencies: {stats.get('candidate_count', 0)}",
        f"- Confirmed issues: {confirmed}",
        f"- Probable issues queued for review: {review_queue}",
        "",
        "## Issues",
        "",
        "| ID | Title | RFC | Section | Level | Code Location | Confidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for i in issues:
        loc = i["code_evidence"][0]["file"] if i.get("code_evidence") else "(gap)"
        lines.append(
            f"| {i['issue_id']} | {i['title']} | {i['design_evidence'].get('rfc','')} | "
            f"{i['design_evidence'].get('section','')} | {i.get('normative_level','')} | "
            f"{loc} | {i.get('confidence', 0.0)} |"
        )
    lines.append("")
    if confirmed < 4:
        lines.append("## Note")
        lines.append("")
        lines.append(
            f"Fewer than 4 confirmed issues found ({confirmed}). "
            "Reason: insufficient evidence / RFC fetch blocked / code paths unconfirmed. "
            "No issues were fabricated."
        )
        lines.append("")
    return "\n".join(lines)


def collect_stats(work: Path) -> dict:
    stats = {}
    for name, key in [("benchmark_index.json", "rfc_count"),
                      ("rfc_requirements.json", "requirement_count"),
                      ("code_index.json", "file_count"),
                      ("candidate_issues.json", "candidate_count")]:
        p = work / name
        if p.exists():
            doc = rc.load_json(p)
            stats[key] = doc.get(key, doc.get("file_count", 0) if key == "file_count" else 0)
    review_path = work / "probable_review_queue.json"
    if review_path.exists():
        stats["probable_review_queue"] = rc.load_json(review_path).get("probable", 0)
    return stats


def clean_old_issue_reports(result_root: Path) -> None:
    for path in result_root.glob("[0-9][0-9]-*.md"):
        if path.name == "00-summary.md":
            continue
        path.unlink()


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Write result reports.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    result_root = rc.ensure_dir(Path(args.result_root))
    work = rc.agent_work_dir(Path(args.code_root))
    ranked_path = work / "ranked_issues.json"
    if not ranked_path.exists():
        print("[reporter] ranked_issues.json missing", file=sys.stderr)
        return 0
    issues = [
        i for i in rc.load_json(ranked_path).get("issues", [])
        if i.get("status") == "confirmed"
    ]
    clean_old_issue_reports(result_root)

    confirmed = sum(1 for i in issues if i["status"] == "confirmed")
    probable = sum(1 for i in issues if i["status"] == "probable")
    high_conf = sum(1 for i in issues if i["confidence"] >= 0.80)
    result_obj = {
        "generated_at": rc.now_iso(),
        "tool": "goal-agent-rfc-diff",
        "code_root": args.code_root,
        "benchmark": args.benchmark,
        "summary": {
            "total": len(issues),
            "confirmed": confirmed,
            "probable": probable,
            "high_confidence": high_conf,
        },
        "issues": issues,
    }
    rc.save_json(result_root / "issues.json", result_obj)
    (result_root / "issues.jsonl").write_text(
        "\n".join(json.dumps(i, ensure_ascii=False) for i in issues) + ("\n" if issues else ""),
        encoding="utf-8",
    )
    stats = collect_stats(work)
    (result_root / "00-summary.md").write_text(
        build_summary(issues, args.code_root, args.benchmark, stats), encoding="utf-8")

    for issue in issues:
        md = render_issue_md(issue)
        out = result_root / Path(issue["report_path"]).name
        out.write_text(md, encoding="utf-8")

    print(f"[reporter] wrote issues.json + {len(issues)} markdown reports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
