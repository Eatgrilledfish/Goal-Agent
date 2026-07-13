#!/usr/bin/env python3
"""Record completion of one semantic scout independently of candidate count."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import risk_sweep_plan_validator as rpv


def _read_values(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"scout handoff must be a regular file: {path}")
    if path.suffix == ".jsonl":
        values, errors = ac.load_jsonl(path)
        if errors:
            raise ValueError("; ".join(errors))
        return values
    value = ac.load_json(path)
    if not isinstance(value, list):
        raise ValueError("scout handoff JSON must be an array")
    if any(not isinstance(item, dict) for item in value):
        raise ValueError("scout handoff entries must be objects")
    return value


def record(
    state_root: Path, sweep_id: str, handoff: Path,
    check_report: Path | None = None, coverage_report: Path | None = None,
) -> dict[str, Any]:
    _plan, index, errors = rpv.load_validated_plan(state_root)
    if errors:
        raise ValueError("scout plan is invalid: " + "; ".join(errors))
    sweep = index.get("slices", {}).get(sweep_id)
    if not isinstance(sweep, dict):
        raise ValueError(f"unknown scout sweep_id {sweep_id!r}")
    if handoff.name != f"{sweep_id}.json":
        raise ValueError(f"scout handoff filename must be {sweep_id}.json")
    values = _read_values(handoff)
    if any(item.get("sweep_id") != sweep_id for item in values):
        raise ValueError("scout handoff contains a foreign or missing sweep_id")
    if values:
        if check_report is None or not check_report.is_file():
            raise ValueError("non-empty scout handoff requires a passed check report")
        report = ac.load_json(check_report)
        if report.get("passed") is not True:
            raise ValueError("scout check report did not pass")
        validated = report.get("validated_ids")
        candidate_ids = [str(item.get("observation_id") or "") for item in values]
        if not isinstance(validated, list) or set(validated) != set(candidate_ids):
            raise ValueError("scout check report does not validate the current candidates")
    if coverage_report is None or not coverage_report.is_file() or coverage_report.is_symlink():
        raise ValueError("scout completion requires a regular coverage report")
    coverage = ac.load_json(coverage_report)
    if not isinstance(coverage, dict):
        raise ValueError("scout coverage report must be an object")
    if coverage.get("sweep_id") != sweep_id:
        raise ValueError("scout coverage report sweep_id does not match")
    direction = str(sweep.get("direction") or "")
    assigned_section_ids = list(sweep.get("section_ids", []))
    assigned_anchor_paths = list(sweep.get("anchor_paths", []))
    reviewed_section_ids = coverage.get("reviewed_section_ids")
    reviewed_anchor_paths = coverage.get("reviewed_anchor_paths")
    if reviewed_section_ids != assigned_section_ids:
        raise ValueError(
            "reviewed_section_ids must exactly match the assigned plan sections"
        )
    if reviewed_anchor_paths != assigned_anchor_paths:
        raise ValueError(
            "reviewed_anchor_paths must exactly match the assigned plan anchors"
        )
    if direction == "design_to_code" and assigned_anchor_paths:
        raise ValueError("design_to_code scout cannot own code anchor paths")
    if direction == "code_to_design" and assigned_section_ids:
        raise ValueError("code_to_design scout cannot own design sections")
    state = ac.load_json(state_root / "agent_loop_state.json")
    plan_path = state_root / "risk_sweep_plan.json"
    receipt = {
        "session_id": state.get("session_id"),
        "sweep_id": sweep_id,
        "direction": direction,
        "risk_sweep_plan_sha256": ac.sha256_file(plan_path),
        "handoff_sha256": ac.sha256_file(handoff),
        "coverage_report_sha256": ac.sha256_file(coverage_report),
        "status": "complete",
        "candidate_count": len(values),
        "candidate_ids": [str(item.get("observation_id")) for item in values],
        "assigned_section_ids": assigned_section_ids,
        "reviewed_section_ids": reviewed_section_ids,
        "assigned_anchor_paths": assigned_anchor_paths,
        "reviewed_anchor_paths": reviewed_anchor_paths,
        "completed_at": ac.now_iso(),
    }
    path = state_root / "scout_receipts.jsonl"
    with ac.file_lock(path):
        prior, parse_errors = ac.load_jsonl(path)
        if parse_errors:
            raise ValueError(
                "existing scout receipts are invalid: " + "; ".join(parse_errors)
            )
        retained = [item for item in prior if item.get("sweep_id") != sweep_id]
        retained.append(receipt)
        ac.atomic_write_jsonl(path, retained)
    ac.append_jsonl(state_root / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(), "session_id": state.get("session_id"),
        "event": "semantic_scout_complete", "actor": "scout_receipt_helper",
        "phase": "semantic_scouting", "status": "complete",
        "sweep_id": sweep_id, "candidate_count": len(values),
        "receipt_sha256": ac.sha256_file(path),
    })
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--sweep-id", required=True)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--check-report")
    parser.add_argument("--coverage-report", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = record(
            Path(args.state_root).resolve(), args.sweep_id,
            Path(args.handoff).resolve(),
            Path(args.check_report).resolve() if args.check_report else None,
            Path(args.coverage_report).resolve(),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"passed": True, **receipt}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
