#!/usr/bin/env python3
"""Convert rule/checker/matrix evidence into the legacy issue queue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_mode
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build issues.jsonl from rules and checker reports.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--append", action="store_true", help="Append instead of rewriting merged issue queue.")
    return parser


def old_severity(severity: str) -> str:
    return {"P0": "high", "P1": "medium", "P2": "low", "HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(str(severity).upper(), "medium")


def first_code_location(root: Path, files: list[str] | None) -> str:
    for file_path in files or []:
        if file_path and file_path.startswith("code/"):
            return f"{file_path}#deterministic-check"
    return "code/#deterministic-check"


def issue_record(
    issue_id: str,
    severity: str,
    issue_type: str,
    summary: str,
    design_basis: str,
    suspected_files: list[str] | None = None,
    related_tests: list[str] | None = None,
    fix_hint: str = "",
) -> dict[str, Any]:
    return {
        "issue_id": issue_id,
        "severity": old_severity(severity),
        "type": issue_type,
        "module": "",
        "design_basis": design_basis,
        "design_behavior": summary or design_basis,
        "actual_behavior": "Deterministic rule/checker evidence indicates this behavior is not yet verified or is failing.",
        "code_location": first_code_location(Path("."), suspected_files),
        "suspected_files": suspected_files or [],
        "related_tests": related_tests or [],
        "confidence": 0.75,
        "estimated_fix_effort": "medium",
        "fix_suggestion": fix_hint or "Read the referenced rule, inspect suspected files, and repair the design-code mismatch without changing frozen inputs.",
        "api_impact": "must_preserve_frozen_contract",
        "status": "open",
        "source": "rule_issue_builder",
        "created_at": runner.now_iso(),
    }


def load_checker_issues(paths: runner.RunnerPaths) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    report_dir = paths.work / "checker_reports"
    if not report_dir.exists():
        return issues
    for report_path in sorted(report_dir.glob("*.json")):
        report = runner.read_json(report_path, {})
        for item in report.get("issues", []):
            issues.append(
                issue_record(
                    issue_id=item.get("issue_id") or f"ISSUE-{runner.stable_hash(json.dumps(item, ensure_ascii=False)).upper()}",
                    severity=item.get("severity", "P1"),
                    issue_type=item.get("type", report.get("checker", "checker")),
                    summary=item.get("summary") or item.get("detail") or "",
                    design_basis=f"deterministic checker {report.get('checker', report_path.stem)}",
                    suspected_files=item.get("suspected_files", []),
                    related_tests=item.get("related_tests", []),
                    fix_hint=item.get("repair_hint", ""),
                )
            )
    return issues


def build_issues(root: Path, append: bool = False) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    paths.work.mkdir(parents=True, exist_ok=True)
    public_can_feed_requirements = pipeline_mode.allows_public_derived_requirements()
    generated: list[dict[str, Any]] = []
    public_diagnostic_risks: list[dict[str, Any]] = []

    consistency = runner.read_json(paths.work / "consistency_report.json", {})
    for item in consistency.get("issues", []):
        method = item.get("method", "")
        path = item.get("path", "")
        endpoint = f"{method} {path}".strip()
        generated.append(
            issue_record(
                issue_id=item.get("issue_id") or f"ISSUE-CONTRACT-{runner.stable_hash(json.dumps(item, ensure_ascii=False)).upper()}",
                severity=item.get("severity", "P1"),
                issue_type=item.get("type", "api_contract"),
                summary=item.get("detail", ""),
                design_basis=f"frozen API contract {endpoint}",
                suspected_files=item.get("suspected_files", []),
                fix_hint=item.get("repair_hint", item.get("detail", "")),
            )
        )

    feature_list = runner.read_json(paths.work / "feature_list.json", {})
    for feature in feature_list.get("features", []):
        if feature.get("passes") is True:
            continue
        if feature.get("severity") not in ("P0", "P1"):
            continue
        if feature.get("source") == "public-test" and not public_can_feed_requirements:
            public_diagnostic_risks.append(
                {
                    "source": "feature_list",
                    "feature_id": feature.get("id", ""),
                    "public_symptom": feature.get("description", ""),
                    "related_tests": feature.get("related_tests", []),
                    "status": "diagnostic_only_unmapped",
                }
            )
            continue
        generated.append(
            issue_record(
                issue_id=f"ISSUE-{feature.get('id')}",
                severity=feature.get("severity", "P1"),
                issue_type=feature.get("category", "business_rule"),
                summary=feature.get("description", ""),
                design_basis=f"{feature.get('source')} feature {feature.get('id')}",
                suspected_files=feature.get("related_files", []),
                related_tests=feature.get("related_tests", []),
                fix_hint="Satisfy the feature and update verification evidence; do not hardcode public fixtures.",
            )
        )

    generated.extend(load_checker_issues(paths))

    matrix = runner.read_json(paths.test_matrix / "current_test_matrix.json", {})
    for record in matrix.get("matrix", []):
        if record.get("outcome") not in ("FAILURE", "ERROR", "TIMEOUT", "NOT_RUN"):
            continue
        test_id = f"{record.get('class_name', '')}#{record.get('method_name', '')}"
        if not public_can_feed_requirements:
            public_diagnostic_risks.append(
                {
                    "source": "public_matrix",
                    "test_id": test_id,
                    "outcome": record.get("outcome", ""),
                    "public_symptom": record.get("message", ""),
                    "source_file": record.get("source_file", ""),
                    "status": "diagnostic_only_unmapped",
                    "required_next_step": "map to README/design-docs/API contract before creating a P0/P1 issue",
                }
            )
            continue
        generated.append(
            issue_record(
                issue_id=f"ISSUE-MATRIX-{runner.stable_hash(test_id + record.get('outcome', '')).upper()}",
                severity="P0" if record.get("outcome") in ("FAILURE", "ERROR") else "P1",
                issue_type="public_matrix_failure",
                summary=f"{test_id} outcome is {record.get('outcome')}: {record.get('message', '')}",
                design_basis="public black-box matrix symptom, to be traced back to README/design-docs/API contract",
                suspected_files=[record.get("source_file", "")] if record.get("source_file", "").startswith("code/") else [],
                related_tests=[test_id],
                fix_hint="Diagnose the failure from design semantics; public tests are symptoms, not fixtures to hardcode.",
            )
        )

    existing = runner.read_jsonl(paths.issues) if append and paths.issues.exists() else []
    merged: dict[str, dict[str, Any]] = {issue.get("issue_id", ""): issue for issue in existing if issue.get("issue_id")}
    for issue in generated:
        merged.setdefault(issue["issue_id"], issue)

    issues = sorted(merged.values(), key=lambda item: ({"high": 0, "medium": 1, "low": 2}.get(item.get("severity", "low"), 2), item.get("issue_id", "")))
    runner.append_jsonl(paths.issues, issues)
    report = {
        "generated_at": runner.now_iso(),
        "mode": pipeline_mode.current_mode(),
        "public_input_role": pipeline_mode.public_role(),
        "generated_count": len(generated),
        "total_count": len(issues),
        "issues_path": runner.rel(root, paths.issues),
        "public_diagnostic_risk_count": len(public_diagnostic_risks),
    }
    runner.write_json(paths.work / "rule_issue_builder_report.json", report)
    if not public_can_feed_requirements:
        runner.write_json(
            paths.work / "public_diagnostic_risks.json",
            {
                "generated_at": runner.now_iso(),
                "mode": pipeline_mode.current_mode(),
                "risks": public_diagnostic_risks,
                "policy": "public failures are symptoms only until mapped to README/design-docs/API contract",
            },
        )
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    report = build_issues(root, append=args.append)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
