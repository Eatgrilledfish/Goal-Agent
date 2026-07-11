from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from test_agent_pipeline import SCRIPTS, populate_handoffs, run_runner, workspace  # noqa: F401

import agent_common as ac


def _rewrite_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def _prepare_pending_frontier(workspace) -> tuple[Path, list[dict]]:
    populate_handoffs(workspace)
    state = workspace["state"]
    assert isinstance(state, Path)
    claims, errors = ac.load_jsonl(state / "design_claims.jsonl")
    assert errors == []
    ac.save_json(state / "claim_review_scope.json", {
        "session_id": workspace["session_id"],
        "round_id": "ROUND-001",
        "design_claims_sha256": ac.sha256_file(state / "design_claims.jsonl"),
        "claim_ids": [claim["claim_id"] for claim in claims],
    })
    review_path = state / "design_claim_review.json"
    review = ac.load_json(review_path)
    review["input_digests"] = {
        name: ac.sha256_file(state / name)
        for name in (
            "design_claims.jsonl", "design_coverage.json", "design_agent_manifest.json",
            "claim_review_scope.json",
        )
    }
    ac.save_json(review_path, review)
    run_runner(
        "claim-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )
    tasks, errors = ac.load_jsonl(state / "investigation_tasks.jsonl")
    assert errors == []
    for task in tasks:
        task["status"] = "pending"
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)
    (state / "investigation_findings.jsonl").write_text("", encoding="utf-8")
    rounds, errors = ac.load_jsonl(state / "investigation_rounds.jsonl")
    assert errors == []
    rounds[0]["finding_ids"] = []
    _rewrite_jsonl(state / "investigation_rounds.jsonl", rounds)
    template_root = state / "handoff-templates" / "investigators"
    for path in template_root.glob("*.json"):
        path.unlink()
    run_runner(
        "task-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )
    return state, tasks


def _generate(state: Path, task_id: str, *, force: bool = False):
    command = [
        sys.executable,
        str(SCRIPTS / "handoff_template.py"),
        "--tasks", str(state / "investigation_tasks.jsonl"),
        "--claims", str(state / "design_claims.jsonl"),
        "--task-id", task_id,
        "--output", str(
            state / "handoff-templates" / "investigators" / f"{task_id}.json"
        ),
    ]
    if force:
        command.append("--force")
    return subprocess.run(command, text=True, capture_output=True)


def test_template_requires_current_passed_task_validation(workspace):
    state, tasks = _prepare_pending_frontier(workspace)
    trace_path = workspace["logs"] / "trace" / "task_validation.json"
    trace_path.unlink()

    missing = _generate(state, "TASK-001")
    assert missing.returncode == 3
    assert "current passed task validation is required" in missing.stdout

    run_runner(
        "task-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )
    tasks[0]["question"] = "A changed question invalidates the frozen frontier."
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)
    stale = _generate(state, "TASK-001")
    assert stale.returncode == 3
    assert "stale for current frontier inputs" in stale.stdout


def test_template_allows_only_first_two_pending_tasks_in_earliest_open_round(workspace):
    state, _ = _prepare_pending_frontier(workspace)

    skipped = _generate(state, "TASK-003")
    assert skipped.returncode == 1
    assert "outside the ordered two-task frontier" in skipped.stdout
    second = _generate(state, "TASK-002")
    assert second.returncode == 0, second.stdout + second.stderr
    first = _generate(state, "TASK-001")
    assert first.returncode == 0, first.stdout + first.stderr


def test_template_rejects_precreated_later_round_as_stale_frontier(workspace):
    state, _ = _prepare_pending_frontier(workspace)
    rounds, errors = ac.load_jsonl(state / "investigation_rounds.jsonl")
    assert errors == []
    first = dict(rounds[0])
    first.update({
        "task_ids": ["TASK-001", "TASK-002", "TASK-003"],
        "claim_ids": ["CLAIM-001", "CLAIM-002", "CLAIM-003"],
        "finding_ids": [],
    })
    second = dict(rounds[0])
    second.update({
        "round_id": "ROUND-002",
        "task_ids": ["TASK-004"],
        "claim_ids": ["CLAIM-004"],
        "finding_ids": [],
    })
    _rewrite_jsonl(state / "investigation_rounds.jsonl", [first, second])

    later = _generate(state, "TASK-004")
    assert later.returncode == 3
    assert "stale for current frontier inputs" in later.stdout
