#!/usr/bin/env python3
"""Patch Selector — scores validated candidates and selects the optimal patch.

Reads ``candidate_validation.jsonl``, computes scores, and outputs
``selected_patch.json`` with the best candidate and fallbacks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score and select the best candidate patch.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--validation-file", default=None,
                       help="Path to candidate_validation.jsonl (default: .agent-work/candidate_validation.jsonl).")
    parser.add_argument("--output", default=None, help="Output path for selected patch.")
    return parser


def diff_minimization(diff_files: int, diff_lines: int) -> float:
    """Score based on diff size — smaller is better."""
    return max(0.0, 1.0 - (diff_files * 0.1 + diff_lines * 0.005))


def stability_score(stable_result: str | None) -> float:
    """Score based on stability verification results."""
    if stable_result is None:
        return 0.5  # Default: not yet verified
    if stable_result == "3x_pass":
        return 1.0
    if stable_result == "2x_pass_1x_fail":
        return 0.3
    if stable_result == "intermittent":
        return 0.0
    return 0.5


def hard_filter_reason(candidate: dict[str, Any]) -> str:
    """Return an elimination reason if the candidate fails non-negotiable gates."""
    required_pass = {
        "compile": "PASS",
        "code_tests": "PASS",
        "code_install": "PASS",
        "forbidden_guard": "PASS",
        "hardcoding_guard": "PASS",
        "matrix_gate": "PASS",
    }
    for field, expected in required_pass.items():
        value = candidate.get(field)
        if value != expected:
            return f"{field}={value or 'MISSING'} (required {expected})"
    if str(candidate.get("contract_check", "")).startswith("FAIL") or candidate.get("contract_check") == "ERROR":
        return f"contract_check={candidate.get('contract_check')}"
    if candidate.get("hard_regression") is True:
        return "hard_regression=true"
    if candidate.get("elimination_reason"):
        return str(candidate.get("elimination_reason"))
    return ""


def compute_score(candidate: dict[str, Any]) -> float:
    """Compute composite score for a candidate.

    Weights:
      - 40% public test pass rate
      - 25% generated test pass rate
      - 15% contract checker pass
      - 10% diff minimization (smaller = better)
      - 10% stability score
    """
    score_inputs = candidate.get("score_inputs", {})

    public_delta = float(score_inputs.get("public_test_delta", score_inputs.get("public_test_pass_rate", 0.0)))
    rule_delta = float(score_inputs.get("rule_pass_delta", score_inputs.get("generated_test_pass_rate", 0.0)))
    contract_score = float(score_inputs.get("contract_score", score_inputs.get("contract_checker_pass", 0.0)))
    reviewer = float(score_inputs.get("reviewer_score", candidate.get("reviewer_score", 0.5) or 0.5))
    diff_files = int(score_inputs.get("diff_files", 0))
    diff_lines = int(score_inputs.get("diff_lines", 0))
    stable = score_inputs.get("stable")

    diff_score = diff_minimization(diff_files, diff_lines)
    stab_score = stability_score(stable)

    return round(
        0.35 * public_delta
        + 0.20 * rule_delta
        + 0.15 * contract_score
        + 0.10 * stab_score
        + 0.10 * reviewer
        + 0.10 * diff_score,
        4,
    )


def select_patch(root: Path, validation_file: str | None) -> dict[str, Any]:
    """Select the best candidate patch."""
    paths = runner.RunnerPaths(root)

    if validation_file:
        val_path = Path(validation_file)
    else:
        val_path = paths.work / "candidate_validation.jsonl"

    if not val_path.exists():
        return {
            "status": "skipped",
            "reason": f"Validation file not found: {val_path}",
            "selected": None,
        }

    validations = runner.read_jsonl(val_path)
    if not validations:
        return {
            "status": "skipped",
            "reason": "No validation records found",
            "selected": None,
        }

    hard_filtered: list[dict[str, Any]] = []
    rejected_by_hard_filter: list[dict[str, Any]] = []
    for validation in validations:
        reason = hard_filter_reason(validation)
        if reason:
            rejected = dict(validation)
            rejected["hard_filter_reason"] = reason
            rejected_by_hard_filter.append(rejected)
            continue
        hard_filtered.append(validation)

    eligible = [v for v in hard_filtered if v.get("eligible", False) or not v.get("elimination_reason")]

    if not eligible:
        return {
            "status": "no_eligible",
            "reason": "No candidates passed validation",
            "selected": None,
            "eliminated_count": len(validations),
            "hard_filter_rejections": [
                {
                    "task_id": r.get("task_id", ""),
                    "candidate_id": r.get("candidate_id", ""),
                    "reason": r.get("hard_filter_reason", ""),
                }
                for r in rejected_by_hard_filter
            ],
        }

    # Score all eligible candidates
    scored = []
    for candidate in eligible:
        score = compute_score(candidate)
        scored.append({
            "candidate_id": candidate.get("candidate_id", "unknown"),
            "task_id": candidate.get("task_id", ""),
            "strategy": candidate.get("strategy", ""),
            "score": score,
            "patch_file": candidate.get("patch_file", ""),
            "patch_file_exists": candidate.get("patch_file_exists", False),
            "public_test_pass_rate": candidate.get("score_inputs", {}).get("public_test_pass_rate", 0),
            "generated_test_pass_rate": candidate.get("score_inputs", {}).get("generated_test_pass_rate", 0),
            "contract_checker_pass": candidate.get("score_inputs", {}).get("contract_checker_pass", 0),
            "public_test_delta": candidate.get("score_inputs", {}).get("public_test_delta", candidate.get("score_inputs", {}).get("public_test_pass_rate", 0)),
            "rule_pass_delta": candidate.get("score_inputs", {}).get("rule_pass_delta", candidate.get("score_inputs", {}).get("generated_test_pass_rate", 0)),
            "reviewer_score": candidate.get("score_inputs", {}).get("reviewer_score", candidate.get("reviewer_score", 0.5)),
            "diff_files": candidate.get("diff_files", 0),
            "diff_lines": candidate.get("diff_lines", 0),
            "compile": candidate.get("compile", "UNKNOWN"),
            "code_tests": candidate.get("code_tests", "UNKNOWN"),
            "code_install": candidate.get("code_install", "UNKNOWN"),
            "public_tests": candidate.get("public_tests", "UNKNOWN"),
            "forbidden_guard": candidate.get("forbidden_guard", "UNKNOWN"),
            "hardcoding_guard": candidate.get("hardcoding_guard", "UNKNOWN"),
            "matrix_gate": candidate.get("matrix_gate", "UNKNOWN"),
        })

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    best = scored[0]
    fallbacks = scored[1:4] if len(scored) > 1 else []

    # Use real patch_file from candidate validation, fallback to inferred path
    real_patch = best.get("patch_file", "")
    warnings: list[str] = []
    if real_patch and best.get("patch_file_exists", False):
        patch_file_out = real_patch
        patch_source = "candidate_validation"
    elif real_patch:
        patch_file_out = real_patch
        patch_source = "candidate_validation"
        warnings.append(f"patch_file from validation ({real_patch}) does not exist on disk")
    else:
        inferred = f".agent-work/patches/{best['task_id']}-{best['candidate_id']}.patch"
        patch_file_out = inferred
        patch_source = "inferred"
        warnings.append(f"patch_file not in validation result, inferred: {inferred}")

    selected: dict[str, Any] = {
        "task_id": best["task_id"],
        "selected_candidate": best["candidate_id"],
        "score": best["score"],
        "reason": (
            f"Highest score ({best['score']:.4f}): "
            f"public={best['public_test_pass_rate']:.2f}, "
            f"generated={best['generated_test_pass_rate']:.2f}, "
            f"contract={best['contract_checker_pass']:.2f}, "
            f"diff={best['diff_files']}f/{best['diff_lines']}l, "
            f"compile={best['compile']}"
        ),
        "patch_file": patch_file_out,
        "patch_file_source": patch_source,
        "fallback_candidates": [
            {"candidate_id": fb["candidate_id"], "score": fb["score"]}
            for fb in fallbacks
        ],
        "selection_details": scored,
        "hard_filter_rejections": [
            {
                "task_id": r.get("task_id", ""),
                "candidate_id": r.get("candidate_id", ""),
                "reason": r.get("hard_filter_reason", ""),
            }
            for r in rejected_by_hard_filter
        ],
    }
    if warnings:
        selected["warnings"] = warnings

    # Persist
    runner.write_json(paths.work / "selected_patch.json", selected)

    # Markdown summary
    lines = [
        "# Patch Selection",
        "",
        f"Generated: {runner.now_iso()}",
        "",
        f"## Selected: {best['candidate_id']}",
        "",
        f"- **Task**: {best.get('task_id', '')}",
        f"- **Score**: {best.get('score', 0):.4f}",
        f"- **Strategy**: {best.get('strategy', '')}",
        f"- **Reason**: {selected.get('reason', '')}",
        "",
        "## All Candidates (ranked)",
        "",
        "| Rank | Candidate | Score | Public Delta | Rule Delta | Contract | Diff | Compile | Guards |",
        "|------|-----------|-------|--------------|------------|----------|------|---------|--------|",
    ]
    for rank, c in enumerate(scored, 1):
        lines.append(
            f"| {rank} | {c['candidate_id']} | {c['score']:.4f} | "
            f"{c['public_test_delta']:.2f} | {c['rule_pass_delta']:.2f} | "
            f"{c['contract_checker_pass']:.2f} | {c['diff_files']}/{c['diff_lines']} | "
            f"{c['compile']} | forbidden={c.get('forbidden_guard', 'PASS')} hardcoding={c.get('hardcoding_guard', 'UNKNOWN')} |"
        )
    lines.append("")

    if rejected_by_hard_filter:
        lines.append("## Hard Filter Rejections")
        lines.append("")
        for r in rejected_by_hard_filter:
            lines.append(f"- **{r.get('candidate_id', '')}**: {r.get('hard_filter_reason', '')}")
        lines.append("")

    if fallbacks:
        lines.append("## Fallbacks")
        lines.append("")
        for fb in fallbacks:
            lines.append(f"- **{fb['candidate_id']}**: score={fb['score']:.4f}")
        lines.append("")

    runner.write_text(paths.work / "selected_patch.md", "\n".join(lines).rstrip() + "\n")

    return {
        "status": "ok",
        "selected": selected,
        "total_eligible": len(eligible),
        "total_validated": len(validations),
    }


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    report = select_patch(root, args.validation_file)

    if args.output:
        runner.write_json(Path(args.output), report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
