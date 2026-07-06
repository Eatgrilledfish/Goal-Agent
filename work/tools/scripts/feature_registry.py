#!/usr/bin/env python3
"""Build and update the external feature registry for the repair loop."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_mode
import shophub_goal_runner as runner


SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build .agent-work/feature_list.json.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output", default=None, help="Output path.")
    return parser


def normalize_severity(value: str | None, default: str = "P2") -> str:
    value = str(value or default).upper()
    if value in {"HIGH", "CRITICAL"}:
        return "P0"
    if value in {"MEDIUM", "IMPORTANT"}:
        return "P1"
    if value in {"LOW", "MINOR"}:
        return "P2"
    return value if value in SEVERITY_ORDER else default


def feature_id(prefix: str, *parts: str) -> str:
    raw = "-".join(part for part in parts if part)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").upper()[:72]
    return f"{prefix}-{slug or runner.stable_hash(raw or prefix).upper()}"


def matrix_outcome_index(matrix: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    for record in matrix.get("matrix", []):
        class_name = record.get("class_name", "")
        method_name = record.get("method_name", "")
        if class_name and method_name:
            index[f"{class_name}#{method_name}"] = record.get("outcome", "")
    return index


def endpoint_key(ep: dict[str, Any]) -> tuple[str, str]:
    return str(ep.get("method", "")), str(ep.get("path") or ep.get("url") or "")


def build_feature_registry(root: Path) -> dict[str, Any]:
    paths = runner.RunnerPaths(root)
    mode = pipeline_mode.current_mode()
    public_can_feed_requirements = pipeline_mode.allows_public_derived_requirements()
    consistency = runner.read_json(paths.work / "consistency_report.json", {})
    contract = runner.read_json(paths.work / "api_contract.json", {})
    business_rules = runner.read_json(paths.work / "business_rules.json", {})
    public_rules = runner.read_json(paths.work / "public_case_rules.json", {}) if public_can_feed_requirements else {}
    matrix = runner.read_json(paths.test_matrix / "current_test_matrix.json", {})

    issue_by_endpoint = {
        (issue.get("method", ""), issue.get("path", ""))
        for issue in consistency.get("issues", [])
        if issue.get("severity") in ("P0", "P1")
    }
    outcomes = matrix_outcome_index(matrix)
    matrix_summary = matrix.get("summary", {})
    matrix_all_green = bool(matrix_summary.get("all_green")) and not any(
        int(matrix_summary.get(key, 0) or 0) > 0
        for key in ("failure", "error", "timeout", "not_run", "skipped")
    )
    checker_categories_with_issues: set[str] = set()
    report_dir = paths.work / "checker_reports"
    if report_dir.exists():
        for report_path in sorted(report_dir.glob("*.json")):
            report = runner.read_json(report_path, {})
            if report.get("issues"):
                checker_categories_with_issues.add(str(report.get("checker", report_path.stem)))
                checker_categories_with_issues.update(str(issue.get("type", "")) for issue in report.get("issues", []))

    features: list[dict[str, Any]] = []

    for ep in contract.get("endpoints", []):
        method, path = endpoint_key(ep)
        if not method or not path:
            continue
        passes = (method, path) not in issue_by_endpoint
        features.append(
            {
                "id": feature_id("RULE-API", method, path),
                "source": "api-contract",
                "category": "api_contract",
                "severity": "P0",
                "description": f"{method} {path} must preserve frozen API contract",
                "related_modules": [],
                "related_files": [],
                "related_tests": [],
                "passes": passes,
                "evidence": [{"kind": "contract", "endpoint": f"{method} {path}"}],
                "last_checked_at": runner.now_iso(),
            }
        )

    for rule in business_rules.get("rules", []):
        severity = normalize_severity(rule.get("priority"), "P2")
        category = rule.get("type", "business_rule")
        if public_can_feed_requirements:
            design_passes = severity == "P2" or (
                matrix_all_green
                and category not in checker_categories_with_issues
                and not issue_by_endpoint
            )
        else:
            design_passes = severity == "P2" or (
                category not in checker_categories_with_issues
                and not issue_by_endpoint
            )
        features.append(
            {
                "id": feature_id("RULE-SPEC", rule.get("id", ""), rule.get("target_domain", "")),
                "source": "design-doc",
                "category": category,
                "severity": severity,
                "description": rule.get("description", ""),
                "related_modules": [rule.get("target_domain", "")] if rule.get("target_domain") else [],
                "related_files": [rule.get("source_file", "")] if rule.get("source_file") else [],
                "related_tests": [],
                "passes": design_passes,
                "evidence": [{"kind": "design", "source": rule.get("source_file", ""), "line": rule.get("source_line")}],
                "last_checked_at": runner.now_iso(),
            }
        )

    if public_can_feed_requirements:
        for rule in public_rules.get("rules", []):
            related_tests = [
                test_id for test_id in outcomes
                if any(token.lower() in test_id.lower() for token in rule.get("matched_keywords", [])[:6])
            ]
            if related_tests:
                passes = all(outcomes[test_id] in ("PASS", "EXPECTED_SKIPPED") for test_id in related_tests)
            else:
                passes = matrix_all_green and rule.get("category") not in checker_categories_with_issues
            features.append(
                {
                    "id": rule.get("id") or feature_id("PUBRULE", rule.get("category", "")),
                    "source": "public-test",
                    "category": rule.get("category", "business_rule"),
                    "severity": normalize_severity(rule.get("severity"), "P1"),
                    "description": rule.get("description", ""),
                    "related_modules": [],
                    "related_files": [],
                    "related_tests": related_tests,
                    "passes": passes,
                    "evidence": rule.get("evidence", []),
                    "last_checked_at": runner.now_iso(),
                }
            )

    for report_path in sorted((paths.work / "checker_reports").glob("*.json")) if (paths.work / "checker_reports").exists() else []:
        report = runner.read_json(report_path, {})
        for issue in report.get("issues", []):
            features.append(
                {
                    "id": feature_id("RULE-CHECKER", issue.get("issue_id", ""), issue.get("type", "")),
                    "source": "checker",
                    "category": issue.get("type", report.get("checker", "checker")),
                    "severity": normalize_severity(issue.get("severity"), "P1"),
                    "description": issue.get("summary") or issue.get("detail") or "",
                    "related_modules": issue.get("related_modules", []),
                    "related_files": issue.get("suspected_files", []),
                    "related_tests": issue.get("related_tests", []),
                    "passes": False,
                    "evidence": [{"kind": "checker", "report": runner.rel(root, report_path), "issue_id": issue.get("issue_id")}],
                    "last_checked_at": runner.now_iso(),
                }
            )

    if public_can_feed_requirements:
        for record in matrix.get("matrix", []):
            if record.get("outcome") not in ("FAILURE", "ERROR", "TIMEOUT", "NOT_RUN"):
                continue
            test_id = f"{record.get('class_name', '')}#{record.get('method_name', '')}"
            features.append(
                {
                    "id": feature_id("RULE-TEST", test_id),
                    "source": "public-test",
                    "category": "public_matrix",
                    "severity": "P0" if record.get("outcome") in ("FAILURE", "ERROR") else "P1",
                    "description": f"Public matrix item {test_id} must pass without masking",
                    "related_modules": [record.get("related_module", "")] if record.get("related_module") else [],
                    "related_files": [record.get("source_file", "")] if record.get("source_file") else [],
                    "related_tests": [test_id],
                    "passes": False,
                    "evidence": [{"kind": "matrix", "outcome": record.get("outcome"), "message": record.get("message", "")}],
                    "last_checked_at": runner.now_iso(),
                }
            )

    deduped: dict[str, dict[str, Any]] = {}
    for feature in features:
        fid = feature["id"]
        if fid not in deduped:
            deduped[fid] = feature
            continue
        existing = deduped[fid]
        existing["passes"] = bool(existing.get("passes")) and bool(feature.get("passes"))
        existing["evidence"].extend(feature.get("evidence", []))
        existing["related_tests"] = sorted(set(existing.get("related_tests", []) + feature.get("related_tests", [])))
        if SEVERITY_ORDER.get(feature["severity"], 2) < SEVERITY_ORDER.get(existing["severity"], 2):
            existing["severity"] = feature["severity"]

    ordered = sorted(deduped.values(), key=lambda item: (SEVERITY_ORDER.get(item["severity"], 2), item["id"]))
    summary = {
        "total": len(ordered),
        "p0_total": sum(1 for f in ordered if f["severity"] == "P0"),
        "p0_passed": sum(1 for f in ordered if f["severity"] == "P0" and f.get("passes") is True),
        "p1_total": sum(1 for f in ordered if f["severity"] == "P1"),
        "p1_passed": sum(1 for f in ordered if f["severity"] == "P1" and f.get("passes") is True),
        "p2_total": sum(1 for f in ordered if f["severity"] == "P2"),
        "p2_passed": sum(1 for f in ordered if f["severity"] == "P2" and f.get("passes") is True),
    }
    report = {
        "generated_at": runner.now_iso(),
        "mode": mode,
        "public_input_role": pipeline_mode.public_role(),
        "features": ordered,
        "summary": summary,
        "ignored_sources": [] if public_can_feed_requirements else ["public_case_rules", "public_matrix_failures"],
    }
    runner.write_json(paths.work / "feature_list.json", report)
    return report


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    report = build_feature_registry(root)
    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
