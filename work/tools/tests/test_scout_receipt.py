from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
            {
                "sweep_id": "SCOUT-A", "direction": "design_to_code",
                "section_ids": ["SECTION-A"], "anchor_paths": [],
            },
            {
                "sweep_id": "SCOUT-B", "direction": "code_to_design",
                "section_ids": [], "anchor_paths": ["src"],
            },
        ],
    })
    (state / "scout_receipts.jsonl").write_text("", encoding="utf-8")
    (state / "agent_run_ledger.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        scout_receipt.rpv,
        "load_validated_plan",
        lambda root: ({}, {
            "slices": {
                "SCOUT-A": {
                    "sweep_id": "SCOUT-A", "direction": "design_to_code",
                    "section_ids": ["SECTION-A"], "anchor_paths": [],
                },
                "SCOUT-B": {
                    "sweep_id": "SCOUT-B", "direction": "code_to_design",
                    "section_ids": [], "anchor_paths": ["src"],
                },
            },
        }, []),
    )
    monkeypatch.setattr(
        scout_receipt, "validate_coverage_contract",
        lambda state_root, sweep, candidates, coverage: None,
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


def _coverage(path: Path, sweep_id: str) -> Path:
    values = {
        "SCOUT-A": {
            "reviewed_section_ids": ["SECTION-A"],
            "reviewed_anchor_paths": [],
        },
        "SCOUT-B": {
            "reviewed_section_ids": [],
            "reviewed_anchor_paths": ["src"],
        },
    }
    ac.save_json(path, {"sweep_id": sweep_id, **values[sweep_id]})
    return path


def test_empty_candidate_scout_records_a_complete_receipt(
    receipt_state: tuple[Path, Path],
) -> None:
    state, handoffs = receipt_state
    handoff = _handoff(handoffs / "SCOUT-A.json", [])

    coverage = _coverage(handoffs / "SCOUT-A.coverage.json", "SCOUT-A")
    receipt = scout_receipt.record(state, "SCOUT-A", handoff, None, coverage)

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
    coverage = _coverage(tmp_path / "coverage.json", "SCOUT-A")
    receipt = scout_receipt.record(state, "SCOUT-A", handoff, passed, coverage)
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


def test_receipt_rejects_incomplete_reviewed_scope(
    receipt_state: tuple[Path, Path], tmp_path: Path,
) -> None:
    state, handoffs = receipt_state
    handoff = _handoff(handoffs / "SCOUT-A.json", [])
    coverage = tmp_path / "incomplete-coverage.json"
    ac.save_json(coverage, {
        "sweep_id": "SCOUT-A",
        "reviewed_section_ids": [],
        "reviewed_anchor_paths": [],
    })

    with pytest.raises(ValueError, match="exactly match the assigned plan sections"):
        scout_receipt.record(state, "SCOUT-A", handoff, None, coverage)


def test_receipt_upsert_replaces_only_the_same_scout(
    receipt_state: tuple[Path, Path], tmp_path: Path,
) -> None:
    state, handoffs = receipt_state
    scout_receipt.record(
        state, "SCOUT-A", _handoff(handoffs / "SCOUT-A.json", []),
        coverage_report=_coverage(tmp_path / "coverage-a.json", "SCOUT-A"),
    )
    scout_receipt.record(
        state, "SCOUT-B", _handoff(handoffs / "SCOUT-B.json", []),
        coverage_report=_coverage(tmp_path / "coverage-b.json", "SCOUT-B"),
    )

    updated_handoff = _handoff(handoffs / "SCOUT-A.json", [{
        "observation_id": "OBS-A-NEW",
        "sweep_id": "SCOUT-A",
    }])
    check = _passed_check(tmp_path / "updated-check.json", ["OBS-A-NEW"])
    updated = scout_receipt.record(
        state, "SCOUT-A", updated_handoff, check,
        _coverage(tmp_path / "updated-coverage.json", "SCOUT-A"),
    )

    values, errors = ac.load_jsonl(state / "scout_receipts.jsonl")
    assert errors == []
    assert len(values) == 2
    by_sweep = {value["sweep_id"]: value for value in values}
    assert set(by_sweep) == {"SCOUT-A", "SCOUT-B"}
    assert by_sweep["SCOUT-A"] == updated
    assert by_sweep["SCOUT-A"]["candidate_ids"] == ["OBS-A-NEW"]
    assert by_sweep["SCOUT-B"]["candidate_ids"] == []


def test_concurrent_receipts_preserve_both_scouts_and_ledger_events(
    receipt_state: tuple[Path, Path], tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, handoffs = receipt_state
    handoff_a = _handoff(handoffs / "SCOUT-A.json", [])
    handoff_b = _handoff(handoffs / "SCOUT-B.json", [])
    coverage_a = _coverage(tmp_path / "coverage-a.json", "SCOUT-A")
    coverage_b = _coverage(tmp_path / "coverage-b.json", "SCOUT-B")
    receipt_path = state / "scout_receipts.jsonl"
    original_load_jsonl = ac.load_jsonl
    delay_guard = threading.Lock()
    delay_first = {"value": True}

    def delayed_first_load(path: Path):
        values = original_load_jsonl(path)
        delay = False
        if path.resolve() == receipt_path.resolve():
            with delay_guard:
                if delay_first["value"]:
                    delay_first["value"] = False
                    delay = True
        if delay:
            time.sleep(0.2)
        return values

    monkeypatch.setattr(ac, "load_jsonl", delayed_first_load)
    start = threading.Barrier(2)

    def publish(args: tuple[str, Path, Path]) -> dict:
        sweep_id, handoff, coverage = args
        start.wait()
        return scout_receipt.record(
            state, sweep_id, handoff, coverage_report=coverage,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(
            publish,
            (("SCOUT-A", handoff_a, coverage_a), ("SCOUT-B", handoff_b, coverage_b)),
        ))

    receipts, receipt_errors = original_load_jsonl(receipt_path)
    events, event_errors = original_load_jsonl(state / "agent_run_ledger.jsonl")
    assert receipt_errors == []
    assert event_errors == []
    assert {item["sweep_id"] for item in receipts} == {"SCOUT-A", "SCOUT-B"}
    assert {
        item["sweep_id"] for item in events
        if item.get("event") == "semantic_scout_complete"
    } == {"SCOUT-A", "SCOUT-B"}
