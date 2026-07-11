from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import session_event  # noqa: E402


def _state_root(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    ac.save_json(root / "agent_loop_state.json", {
        "session_id": "session-trace-test",
        "status": "ready",
        "current_phase": "prepare",
        "completed_phases": [],
        "metrics": {},
        "next_actions": [],
        "stop_reason": "",
    })
    return root


def _input_file(tmp_path: Path, name: str = "input.json", text: str = "{}\n") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _required_args(
    root: Path,
    inputs: list[Path],
    output_artifact: Path | None = None,
    **overrides: str,
) -> list[str]:
    values = {
        "actor": "orchestrator",
        "role": "orchestrator",
        "phase": "candidate_review",
        "status": "complete",
        "summary": "Candidate evidence review completed.",
        "event": "critic.completed",
        "scope_id": "FINDING-017",
        "scope": "claim=CLAIM-009;finding=FINDING-017",
        "started_at": "2026-07-11T02:03:04.250Z",
        "ended_at": "2026-07-11T02:04:34.750Z",
        "provider_attempt": "1",
        "provider_session_id": "provider-session-abc",
        "output_count": "1",
        "repair_count": "0",
        "outcome": "accepted",
        "stop_reason": "candidate_evidence_closed",
    }
    values.update(overrides)
    args = ["--state-root", str(root)]
    for name, value in values.items():
        args.extend([f"--{name.replace('_', '-')}", value])
    for path in inputs:
        args.extend(["--input-artifact", str(path)])
    args.extend(["--artifact", str(output_artifact or inputs[0])])
    return args


def _without(args: list[str], flag: str) -> list[str]:
    result = list(args)
    index = result.index(flag)
    del result[index:index + 2]
    return result


def _expected_combined_digest(records: list[dict[str, object]]) -> str:
    digest_payload = [
        {"path": item["path"], "sha256": item["sha256"]}
        for item in records
    ]
    canonical = json.dumps(
        digest_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_trace_event_records_required_fields_and_real_input_digests(tmp_path: Path) -> None:
    root = _state_root(tmp_path)
    first = _input_file(tmp_path, "zeta.json", '{"z": 1}\n')
    second = _input_file(tmp_path, "alpha.md", "# actual input\n")
    output = _input_file(tmp_path, "critic_reviews.jsonl", '{"decision":"accept"}\n')

    assert session_event.main(_required_args(
        root,
        [first, second],
        output_artifact=output,
        role="evidence_critic",
        provider_attempt="2",
        output_count="3",
        repair_count="1",
    ) + [
        "--task-id", "TASK-017",
        "--error-category", "QUOTE_RANGE_MISMATCH=2",
        "--error-category", "QUOTE_RANGE_MISMATCH=1",
        "--error-category", "CLAIM_GROUP_GAP=4",
        "--completed-phase", "candidate_review",
        "--metric", "reviewed=2",
        "--next", "Run coverage supplement.",
    ]) == 0

    state = ac.load_json(root / "agent_loop_state.json")
    events, errors = ac.load_jsonl(root / "agent_run_ledger.jsonl")
    assert errors == []
    assert state["status"] == "complete"
    assert state["metrics"]["reviewed"] == 2
    assert state["completed_phases"] == ["candidate_review"]
    assert state["stop_reason"] == "candidate_evidence_closed"
    assert len(events) == 1

    event = events[0]
    assert event["event"] == "critic.completed"
    assert event["actor"] == "orchestrator"
    assert event["role"] == "evidence_critic"
    assert event["task_id"] == "TASK-017"
    assert event["scope_id"] == "FINDING-017"
    assert event["scope"] == "claim=CLAIM-009;finding=FINDING-017"
    assert event["started_at"] == "2026-07-11T02:03:04.250Z"
    assert event["ended_at"] == "2026-07-11T02:04:34.750Z"
    assert event["wall_time_seconds"] == 90.5
    assert event["provider_attempt"] == 2
    assert event["provider_session_id"] == "provider-session-abc"
    assert event["output_count"] == 3
    assert event["repair_count"] == 1
    assert event["outcome"] == "accepted"
    assert event["stop_reason"] == "candidate_evidence_closed"
    assert event["artifacts"] == [str(output)]
    assert event["artifact_snapshots"] == [{
        "path": str(output),
        "sha256": ac.sha256_file(output),
        "size_bytes": output.stat().st_size,
    }]
    assert event["artifact_sha256"] == _expected_combined_digest(
        event["artifact_snapshots"]
    )

    expected_records = [
        {
            "path": str(path),
            "sha256": ac.sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted([first, second], key=lambda item: str(item))
    ]
    assert event["input_artifacts"] == expected_records
    assert event["input_sha256"] == _expected_combined_digest(expected_records)
    assert event["validation_error_categories"] == {
        "QUOTE_RANGE_MISMATCH": 3,
        "CLAIM_GROUP_GAP": 4,
    }
    assert event["validation_error_count"] == 7


def test_input_digest_is_independent_of_repeated_argument_order(tmp_path: Path) -> None:
    root = _state_root(tmp_path)
    first = _input_file(tmp_path, "one.txt", "one\n")
    second = _input_file(tmp_path, "two.txt", "two\n")

    assert session_event.main(_required_args(root, [second, first])) == 0
    assert session_event.main(_required_args(root, [first, second])) == 0

    events, errors = ac.load_jsonl(root / "agent_run_ledger.jsonl")
    assert errors == []
    assert events[0]["input_artifacts"] == events[1]["input_artifacts"]
    assert events[0]["input_sha256"] == events[1]["input_sha256"]


@pytest.mark.parametrize(
    "flag",
    [
        "--event",
        "--role",
        "--scope-id",
        "--scope",
        "--input-artifact",
        "--artifact",
        "--started-at",
        "--ended-at",
        "--provider-attempt",
        "--provider-session-id",
        "--output-count",
        "--repair-count",
        "--outcome",
        "--stop-reason",
    ],
)
def test_declared_trace_fields_are_cli_required(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flag: str,
) -> None:
    root = _state_root(tmp_path)
    artifact = _input_file(tmp_path)

    with pytest.raises(SystemExit) as exc:
        session_event.main(_without(_required_args(root, [artifact]), flag))

    assert exc.value.code == 2
    assert flag in capsys.readouterr().err
    assert not (root / "agent_run_ledger.jsonl").exists()
    assert ac.load_json(root / "agent_loop_state.json")["status"] == "ready"


def test_nonexistent_input_artifact_is_rejected_before_state_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _state_root(tmp_path)
    missing = tmp_path / "invented-input.json"

    with pytest.raises(SystemExit) as exc:
        session_event.main(_required_args(root, [missing]))

    assert exc.value.code == 2
    assert "input-artifact does not exist" in capsys.readouterr().err
    assert not (root / "agent_run_ledger.jsonl").exists()
    assert ac.load_json(root / "agent_loop_state.json")["status"] == "ready"


def test_directory_input_artifact_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _state_root(tmp_path)
    directory = tmp_path / "not-a-file"
    directory.mkdir()

    with pytest.raises(SystemExit) as exc:
        session_event.main(_required_args(root, [directory]))

    assert exc.value.code == 2
    assert "input-artifact must be a regular file" in capsys.readouterr().err
    assert not (root / "agent_run_ledger.jsonl").exists()


def test_symlink_input_artifact_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _state_root(tmp_path)
    target = _input_file(tmp_path, "real-input.json")
    symlink = tmp_path / "input-link.json"
    symlink.symlink_to(target)

    with pytest.raises(SystemExit) as exc:
        session_event.main(_required_args(root, [symlink]))

    assert exc.value.code == 2
    assert "input-artifact path contains a symlink" in capsys.readouterr().err
    assert not (root / "agent_run_ledger.jsonl").exists()


def test_legacy_model_supplied_input_digest_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _state_root(tmp_path)
    artifact = _input_file(tmp_path)

    with pytest.raises(SystemExit) as exc:
        session_event.main(
            _required_args(root, [artifact]) + ["--input-sha256", "a" * 64]
        )

    assert exc.value.code == 2
    assert "unrecognized arguments: --input-sha256" in capsys.readouterr().err
    assert not (root / "agent_run_ledger.jsonl").exists()


@pytest.mark.parametrize(
    ("overrides", "extra_args", "message"),
    [
        ({"event": "not an id"}, [], "event must match"),
        ({"scope_id": "not an id"}, [], "scope-id must match"),
        ({"scope": ""}, [], "must not be empty"),
        ({"started_at": "2026-07-11T02:03:04"}, [], "must include a timezone"),
        (
            {
                "started_at": "2026-07-11T02:04:04Z",
                "ended_at": "2026-07-11T02:03:04Z",
            },
            [],
            "must not be earlier",
        ),
        ({"provider_attempt": "0"}, [], "must be greater than zero"),
        ({"provider_attempt": "3"}, [], "must not exceed two provider attempts"),
        ({"output_count": "-1"}, [], "greater than or equal to zero"),
        ({"output_count": "0"}, [], "complete checkpoint output-count"),
        ({}, ["--error-category", "quote_mismatch=2"], "error category code"),
        ({}, ["--error-category", "QUOTE_RANGE_MISMATCH=0"], "greater than zero"),
        ({"repair_count": "not-a-number"}, [], "must be an integer"),
        ({"repair_count": "2"}, [], "must not exceed one semantic repair"),
    ],
)
def test_trace_contract_rejects_malformed_mechanical_fields(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, str],
    extra_args: list[str],
    message: str,
) -> None:
    root = _state_root(tmp_path)
    artifact = _input_file(tmp_path)

    with pytest.raises(SystemExit) as exc:
        session_event.main(_required_args(root, [artifact], **overrides) + extra_args)

    assert exc.value.code == 2
    assert message in capsys.readouterr().err
    assert not (root / "agent_run_ledger.jsonl").exists()
    assert ac.load_json(root / "agent_loop_state.json")["status"] == "ready"
