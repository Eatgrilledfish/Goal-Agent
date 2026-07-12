from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import pipeline_controller as pc  # noqa: E402


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    (tmp_path / "logs" / "trace").mkdir(parents=True)
    ac.save_json(root / "workspace_manifest.json", {
        "paths": {"log_root": str(tmp_path / "logs")},
    })
    return root


def _jsonl(path: Path, values: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values),
        encoding="utf-8",
    )


def _plan(root: Path) -> None:
    ac.save_json(root / "scout_plan.json", {
        "slices": [{"scout_id": "SCOUT-01"}, {"scout_id": "SCOUT-02"}],
    })


def _complete_scouts(root: Path) -> None:
    _jsonl(root / "scout_receipts.jsonl", [
        {"scout_id": "SCOUT-01", "status": "complete"},
        {"scout_id": "SCOUT-02", "passed": True},
    ])


def _select_candidates(root: Path, candidate_ids: list[str]) -> None:
    ac.save_json(root / "candidate_selection.json", {
        "candidate_ids": candidate_ids,
    })
    ac.save_json(root / "claim_review_scope.json", {
        "claim_ids": [f"CLAIM-{index}" for index, _value in enumerate(candidate_ids, 1)],
    })
    _jsonl(root / "design_claims.jsonl", [
        {"claim_id": f"CLAIM-{index}", "candidate_id": candidate_id}
        for index, candidate_id in enumerate(candidate_ids, 1)
    ])
    _jsonl(root / "design_lookup_requests.jsonl", [
        {"request_id": f"LOOKUP-{index}", "candidate_id": candidate_id}
        for index, candidate_id in enumerate(candidate_ids, 1)
    ])


def _accept_claims(root: Path, count: int) -> None:
    ac.save_json(root / "design_claim_review.json", {
        "claim_reviews": [
            {"claim_id": f"CLAIM-{index}", "decision": "accept"}
            for index in range(1, count + 1)
        ],
    })
    log_root = Path(ac.load_json(root / "workspace_manifest.json")["paths"]["log_root"])
    ac.save_json(log_root / "trace" / "claim_review_validation.json", {
        "passed": True,
        "accepted_claim_ids": [f"CLAIM-{index}" for index in range(1, count + 1)],
    })


def test_status_short_circuits_at_earliest_unmet_precondition(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _plan(root)

    assert pc.next_action(root) == "finish_scouts"

    _jsonl(root / "scout_receipts.jsonl", [
        {"scout_id": "SCOUT-01", "status": "complete"},
    ])
    _jsonl(root / "investigation_tasks.jsonl", [{"task_id": "TASK-01"}])
    _jsonl(root / "investigation_findings.jsonl", [
        {"finding_id": "FINDING-01", "task_id": "TASK-01"},
    ])
    _jsonl(root / "critic_reviews.jsonl", [{"finding_id": "FINDING-01"}])
    assert pc.next_action(root) == "finish_scouts"

    _complete_scouts(root)
    (root / "investigation_findings.jsonl").unlink()
    (root / "critic_reviews.jsonl").unlink()
    assert pc.next_action(root) == "select_candidates"
    _select_candidates(root, ["CANDIDATE-01"])
    assert pc.next_action(root) == "review_claims"
    _accept_claims(root, 1)
    assert pc.next_action(root) == "finish_investigations"


def test_status_walks_selection_investigation_critic_and_final(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)

    assert pc.next_action(root) == "select_candidates"

    _select_candidates(root, ["CANDIDATE-01", "CANDIDATE-02"])
    assert pc.next_action(root) == "review_claims"

    _accept_claims(root, 2)
    assert pc.next_action(root) == "plan_investigations"

    _jsonl(root / "investigation_tasks.jsonl", [
        {"task_id": "TASK-01"},
        {"task_id": "TASK-02"},
    ])
    assert pc.next_action(root) == "finish_investigations"

    _jsonl(root / "investigation_findings.jsonl", [
        {"finding_id": "FINDING-01", "task_id": "TASK-01"},
        {"finding_id": "FINDING-02", "task_id": "TASK-02"},
    ])
    assert pc.next_action(root) == "finish_critics"

    _jsonl(root / "critic_reviews.jsonl", [
        {"finding_id": "FINDING-01"},
    ])
    assert pc.next_action(root) == "finish_critics"

    _jsonl(root / "critic_reviews.jsonl", [
        {"finding_id": "FINDING-01"},
        {"finding_id": "FINDING-02"},
    ])
    assert pc.next_action(root) == "run_final"


def test_valid_empty_selection_runs_final(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    _select_candidates(root, [])
    _accept_claims(root, 0)
    (root / "investigation_tasks.jsonl").write_text("", encoding="utf-8")

    assert pc.next_action(root) == "run_final"


def test_deferred_task_does_not_require_finding(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _plan(root)
    _complete_scouts(root)
    _select_candidates(root, ["CANDIDATE-01"])
    _accept_claims(root, 1)
    _jsonl(root / "investigation_tasks.jsonl", [
        {"task_id": "TASK-01", "status": "deferred"},
    ])

    assert pc.next_action(root) == "run_final"


def test_cli_outputs_only_one_next_action(
    tmp_path: Path, capsys,
) -> None:
    root = _root(tmp_path)

    assert pc.main(["status", "--state-root", str(root)]) == 0

    assert json.loads(capsys.readouterr().out) == {"next_action": "finish_scouts"}
