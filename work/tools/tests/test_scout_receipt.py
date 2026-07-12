from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import scout_receipt  # noqa: E402


@pytest.fixture
def receipt_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    state = tmp_path / "state"
    handoffs = state / "handoffs"
    handoffs.mkdir(parents=True)
    ac.save_json(state / "agent_loop_state.json", {
        "session_id": "session-scout-receipt",
    })
    ac.save_json(state / "risk_sweep_plan.json", {
        "session_id": "session-scout-receipt",
        "slices": [
            {"sweep_id": "SCOUT-A"},
            {"sweep_id": "SCOUT-B"},
        ],
    })
    (state / "scout_receipts.jsonl").write_text("", encoding="utf-8")
    (state / "agent_run_ledger.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        scout_receipt.rpv,
        "load_validated_plan",
        lambda root: ({}, {
            "slices": {
                "SCOUT-A": {"sweep_id": "SCOUT-A"},
                "SCOUT-B": {"sweep_id": "SCOUT-B"},
            },
        }, []),
    )
    return state, handoffs


def _handoff(path: Path, values: list[dict]) -> Path:
    ac.save_json(path, values)
    return path


def _passed_check(path: Path, candidate_ids: list[str]) -> Path:
    ac.save_json(path, {
        "passed": True,
        "validated_ids": candidate_ids,
    })
    return path


def test_empty_candidate_scout_records_a_complete_receipt(
    receipt_state: tuple[Path, Path],
) -> None:
    state, handoffs = receipt_state
    handoff = _handoff(handoffs / "SCOUT-A.json", [])

    receipt = scout_receipt.record(state, "SCOUT-A", handoff)

    assert receipt["status"] == "complete"
    assert receipt["candidate_count"] == 0
    assert receipt["candidate_ids"] == []
    assert receipt["handoff_sha256"] == ac.sha256_file(handoff)
    values, errors = ac.load_jsonl(state / "scout_receipts.jsonl")
    assert errors == []
    assert values == [receipt]


def test_nonempty_scout_requires_a_passed_current_check(
    receipt_state: tuple[Path, Path], tmp_path: Path,
) -> None:
    state, handoffs = receipt_state
    candidate = {"observation_id": "OBS-A", "sweep_id": "SCOUT-A"}
    handoff = _handoff(handoffs / "SCOUT-A.json", [candidate])

    with pytest.raises(ValueError, match="requires a passed check report"):
        scout_receipt.record(state, "SCOUT-A", handoff)

    failed = tmp_path / "failed-check.json"
    ac.save_json(failed, {"passed": False, "validated_ids": ["OBS-A"]})
    with pytest.raises(ValueError, match="did not pass"):
        scout_receipt.record(state, "SCOUT-A", handoff, failed)

    passed = _passed_check(tmp_path / "passed-check.json", ["OBS-A"])
    receipt = scout_receipt.record(state, "SCOUT-A", handoff, passed)
    assert receipt["candidate_count"] == 1
    assert receipt["candidate_ids"] == ["OBS-A"]


def test_foreign_sweep_in_handoff_is_rejected(
    receipt_state: tuple[Path, Path],
) -> None:
    state, handoffs = receipt_state
    handoff = _handoff(handoffs / "SCOUT-A.json", [{
        "observation_id": "OBS-B",
        "sweep_id": "SCOUT-B",
    }])

    with pytest.raises(ValueError, match="foreign or missing sweep_id"):
        scout_receipt.record(state, "SCOUT-A", handoff)


def test_receipt_upsert_replaces_only_the_same_scout(
    receipt_state: tuple[Path, Path], tmp_path: Path,
) -> None:
    state, handoffs = receipt_state
    scout_receipt.record(
        state, "SCOUT-A", _handoff(handoffs / "SCOUT-A.json", []),
    )
    scout_receipt.record(
        state, "SCOUT-B", _handoff(handoffs / "SCOUT-B.json", []),
    )

    updated_handoff = _handoff(handoffs / "SCOUT-A.json", [{
        "observation_id": "OBS-A-NEW",
        "sweep_id": "SCOUT-A",
    }])
    check = _passed_check(tmp_path / "updated-check.json", ["OBS-A-NEW"])
    updated = scout_receipt.record(state, "SCOUT-A", updated_handoff, check)

    values, errors = ac.load_jsonl(state / "scout_receipts.jsonl")
    assert errors == []
    assert len(values) == 2
    by_sweep = {value["sweep_id"]: value for value in values}
    assert set(by_sweep) == {"SCOUT-A", "SCOUT-B"}
    assert by_sweep["SCOUT-A"] == updated
    assert by_sweep["SCOUT-A"]["candidate_ids"] == ["OBS-A-NEW"]
    assert by_sweep["SCOUT-B"]["candidate_ids"] == []

