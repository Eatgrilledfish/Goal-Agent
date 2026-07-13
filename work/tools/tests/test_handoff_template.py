from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from test_agent_pipeline import SCRIPTS, populate_handoffs, run_runner, workspace  # noqa: F401

import agent_common as ac
import handoff_template


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
        "claim_ids": [claim["claim_id"] for claim in claims],
    })
    review_path = state / "design_claim_review.json"
    review = ac.load_json(review_path)
    review["input_digests"] = {
        name: ac.sha256_file(state / name)
        for name in (
            "design_claims.jsonl", "design_coverage.json", "design_inventory.json",
            "design_agent_manifest.json",
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


def _generate_frontier(state: Path, *, force: bool = False):
    command = [
        sys.executable,
        str(SCRIPTS / "handoff_template.py"),
        "--tasks", str(state / "investigation_tasks.jsonl"),
        "--claims", str(state / "design_claims.jsonl"),
        "--frontier",
        "--output-dir", str(state / "handoff-templates" / "investigators"),
    ]
    if force:
        command.append("--force")
    return subprocess.run(command, text=True, capture_output=True)


def test_template_requires_current_plan_and_lifecycle_validation(workspace):
    state, tasks = _prepare_pending_frontier(workspace)
    trace_path = workspace["logs"] / "trace" / "task_plan_validation.json"
    trace_path.unlink()

    missing = _generate(state, "TASK-001")
    assert missing.returncode == 3
    assert "current task plan and lifecycle validation is required" in missing.stdout

    run_runner(
        "task-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"],
    )
    tasks[0]["hypothesis"] = "A changed hypothesis invalidates the frozen candidate plan."
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)
    stale = _generate(state, "TASK-001")
    assert stale.returncode == 3
    assert "task plan validation is stale for current stable plan inputs" in stale.stdout


def test_template_allows_only_first_two_pending_tasks_in_earliest_open_round(workspace):
    state, _ = _prepare_pending_frontier(workspace)

    skipped = _generate(state, "TASK-003")
    assert skipped.returncode == 1
    assert "outside the ordered two-task frontier" in skipped.stdout
    second = _generate(state, "TASK-002")
    assert second.returncode == 0, second.stdout + second.stderr
    first = _generate(state, "TASK-001")
    assert first.returncode == 0, first.stdout + first.stderr


def test_frontier_mode_mechanically_generates_at_most_two_templates(workspace):
    state, _ = _prepare_pending_frontier(workspace)

    generated = _generate_frontier(state)

    assert generated.returncode == 0, generated.stdout + generated.stderr
    result = json.loads(generated.stdout)
    assert result["passed"] is True
    assert result["mode"] == "frontier"
    assert result["task_ids"] == ["TASK-001", "TASK-002"]
    assert result["count"] == 2
    assert [item["task_id"] for item in result["outputs"]] == [
        "TASK-001", "TASK-002",
    ]
    templates = state / "handoff-templates" / "investigators"
    assert sorted(path.name for path in templates.glob("*.json")) == [
        "TASK-001.json", "TASK-002.json",
    ]


def test_frontier_mode_reports_passed_false_without_partial_templates(workspace):
    state, _ = _prepare_pending_frontier(workspace)
    (workspace["logs"] / "trace" / "task_lifecycle_validation.json").unlink()

    generated = _generate_frontier(state)

    assert generated.returncode == 3
    result = json.loads(generated.stdout)
    assert result["passed"] is False
    assert result["task_ids"] == ["TASK-001", "TASK-002"]
    assert set(result["validation_errors"]) == {"TASK-001", "TASK-002"}
    templates = state / "handoff-templates" / "investigators"
    assert list(templates.glob("*.json")) == []


def test_template_rejects_stale_candidate_lifecycle(workspace):
    state, tasks = _prepare_pending_frontier(workspace)
    tasks[0]["status"] = "in_progress"
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)

    stale = _generate(state, "TASK-001")
    assert stale.returncode == 3
    assert "task lifecycle validation is stale for current lifecycle inputs" in stale.stdout


def test_finding_template_preserves_atomic_task_identity(workspace):
    state, tasks = _prepare_pending_frontier(workspace)
    claims = {
        item["claim_id"]: item
        for item in ac.load_jsonl(state / "design_claims.jsonl")[0]
    }
    task = tasks[0]
    claim = claims[task["claim_id"]]
    template = handoff_template.finding_template(task, claim)
    assert template["claim_branch"] == task["claim_branch"]
    assert template["obligation_sha256"] == task["obligation_sha256"]
    assert template["hypothesis"] == task["hypothesis"]
    assert claim["obligation"] in template["expected_behavior"]
    assert claim["observable_result"] in template["expected_behavior"]


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
    assert "task plan validation is stale for current stable plan inputs" in later.stdout


def test_template_allows_valid_candidate_when_another_plan_candidate_is_invalid(workspace):
    state, tasks = _prepare_pending_frontier(workspace)
    tasks[3]["obligation_sha256"] = "0" * 64
    _rewrite_jsonl(state / "investigation_tasks.jsonl", tasks)
    plan = run_runner(
        "task-plan-check", workspace["code"], workspace["design"],
        workspace["result"], workspace["logs"], check=False,
    )
    assert plan.returncode == 1
    trace = ac.load_json(workspace["logs"] / "trace" / "task_plan_validation.json")
    assert trace["global_passed"] is True
    assert "TASK-001" in trace["valid_task_ids"]
    assert trace["invalid_task_ids"] == ["TASK-004"]
    generated = _generate(state, "TASK-001")
    assert generated.returncode == 0, generated.stdout + generated.stderr
