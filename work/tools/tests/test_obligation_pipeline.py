from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import handoff_merge  # noqa: E402
import negative_review  # noqa: E402
import obligation_queue  # noqa: E402
import scout_materializer  # noqa: E402
import scout_receipt  # noqa: E402
import workspace_inventory  # noqa: E402


def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict, dict]:
    state = tmp_path / "state"
    design = tmp_path / "design"
    state.mkdir()
    design.mkdir()
    (design / "spec.txt").write_text(
        "intro\nWhen enabled, the service MUST process every record in order.\n"
        "It SHOULD wait before sending an unsolicited update.\nend\n",
        encoding="utf-8",
    )
    design_sweep = {
        "sweep_id": "SCOUT-DESIGN-01", "direction": "design_to_code",
        "section_ids": ["SECTION-A"], "anchor_paths": [],
        "architecture_boundaries": [], "implementation_planes": [],
        "parallel_path_ids": [],
    }
    code_sweep = {
        "sweep_id": "SCOUT-CODE-01", "direction": "code_to_design",
        "section_ids": [], "anchor_paths": ["src/runtime.c"],
        "architecture_boundaries": ["BOUNDARY-A"],
        "implementation_planes": ["PLANE-A"], "parallel_path_ids": [],
    }
    ac.save_json(state / "agent_loop_state.json", {"session_id": "session-test"})
    ac.save_json(state / "risk_sweep_plan.json", {
        "session_id": "session-test", "slices": [design_sweep, code_sweep],
    })
    ac.save_json(state / "design_inventory.json", {
        "document_groups": [{
            "document_key": "spec", "scope_relation": "required",
            "sections": [{
                "section_id": "SECTION-A", "source_ref": {
                    "path": "spec.txt", "line_start": 1, "line_end": 4,
                },
            }],
        }],
    })
    ac.save_json(state / "workspace_manifest.json", {
        "paths": {"review_design_root": str(design)},
    })
    plan_index = {
        "slices": {
            design_sweep["sweep_id"]: design_sweep,
            code_sweep["sweep_id"]: code_sweep,
        },
    }
    for module in (negative_review, obligation_queue, scout_materializer):
        monkeypatch.setattr(
            module.rpv, "load_validated_plan",
            lambda root, plan_index=plan_index: ({}, plan_index, []),
        )
    return state, design_sweep, code_sweep


def _semantic_obligations(path: Path) -> None:
    ac.save_json(path, {
        "obligations": [{
            "source_ref": {"path": "spec.txt", "line_start": 2, "line_end": 2},
            "subject": "enabled service", "trigger": "records arrive",
            "obligation": "process every record in order",
            "observable_result": "all records are processed in their input order",
            "normative_strength": "mandatory",
            "applicability": "the target implements the enabled service",
            "exceptions": [], "ambiguities": [],
            "review_mode": "contract_mechanics",
        }],
        "no_obligation_sections": [],
    })


def _candidate_payload() -> dict:
    return {
        "candidate_key": "first-record-only",
        "behavior_question": "Are all records processed?",
        "mismatch_signal": "direct_conflict",
        "observed_code_behavior": "Only the first record is processed.",
        "code_evidence": [{
            "file": "src/runtime.c", "line_start": 10, "line_end": 11,
            "symbol": "process", "snippet": "process(records[0]);",
        }],
        "false_positive_checks": [{
            "question": "Is there another loop?", "method": "search",
            "target": "src", "result": "No alternate loop was found.",
        }],
        "tool_trace": [
            {
                "seq": 1, "kind": "design_read", "tool": "read",
                "target": "spec.txt:2", "purpose": "confirm the obligation",
                "result": "the design requires every record",
            },
            {
                "seq": 2, "kind": "code_search", "tool": "rg", "target": "process",
                "purpose": "find record processing", "result": "one direct call",
            },
            {
                "seq": 3, "kind": "code_read", "tool": "read",
                "target": "src/runtime.c:10", "purpose": "inspect behavior",
                "result": "only records[0] is processed",
            },
            {
                "seq": 4, "kind": "reverse_check", "tool": "rg",
                "target": "src", "purpose": "find alternate traversal",
                "result": "no alternate traversal found",
            },
        ],
    }


def _execution_accounting() -> dict[str, str]:
    return {
        "entry": "src/runtime.c:10 enters the implementation.",
        "progress_or_transition": "src/runtime.c:11 advances the current item.",
        "guards_and_bounds": "src/runtime.c:12 checks the configured boundary.",
        "termination_or_exit": "src/runtime.c:13 exits after input exhaustion.",
        "remaining_applicable_work": "No applicable work remains after that exit.",
        "alternate_or_compensating_path": "Checked the only alternate caller.",
    }


def _fresh_review(
    state: Path, sweep_id: str, raw_candidates: Path, raw_coverage: Path,
    reviews: list[dict] | None = None,
) -> tuple[Path, Path, dict]:
    raw_root = state / "semantic" / "scouts"
    raw_root.mkdir(parents=True, exist_ok=True)
    fixed_candidates = raw_root / f"{sweep_id}.candidates.json"
    fixed_coverage = raw_root / f"{sweep_id}.coverage.json"
    ac.save_json(fixed_candidates, ac.load_json(raw_candidates))
    ac.save_json(fixed_coverage, ac.load_json(raw_coverage))
    review_root = state / "semantic" / "negative-reviews"
    review_root.mkdir(parents=True, exist_ok=True)
    packet_path = review_root / f"{sweep_id}.packet.json"
    review_path = review_root / f"{sweep_id}.review.json"
    packet = negative_review.prepare(
        state, sweep_id, fixed_candidates, fixed_coverage, packet_path,
    )
    packet_items = [item for batch in packet["batches"] for item in batch["items"]]
    if reviews is None:
        reviews = [{
            "review_item_id": item["review_item_id"],
            "verdict": "upheld",
            "independent_analysis": "Independent comparison supports the implementation.",
            "execution_accounting": _execution_accounting(),
            "falsification_attempt": "Tried a boundary case and found no contradiction.",
            "candidate": None,
        } for item in packet_items]
    reviews_by_id = {item["review_item_id"]: item for item in reviews}
    batch_review_paths = []
    batch_sessions = []
    for number, batch in enumerate(packet["batches"], start=1):
        batch_path = review_root / f"{sweep_id}.{batch['batch_id']}.raw.json"
        ac.save_json(batch_path, {"reviews": [
            reviews_by_id[item["review_item_id"]] for item in batch["items"]
        ]})
        batch_review_paths.append(batch_path)
        batch_sessions.append(f"provider-negative-review-{number}")
    negative_review.assemble(
        packet_path, batch_review_paths, batch_sessions, review_path,
    )
    reconciled_candidates = state / "semantic" / f"{sweep_id}.candidates.json"
    reconciled_coverage = state / "semantic" / f"{sweep_id}.coverage.json"
    negative_review.reconcile(
        state, sweep_id, fixed_candidates, fixed_coverage, packet_path, review_path,
        "provider-scout", reconciled_candidates, reconciled_coverage,
    )
    return reconciled_candidates, reconciled_coverage, packet


def test_obligation_modes_are_the_canonical_coverage_vocabulary() -> None:
    assert set(workspace_inventory.PORTFOLIO_LENSES) == obligation_queue.REVIEW_MODES


def test_materializes_source_bound_obligation_and_projects_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "obligations.semantic.json"
    queue_path = state / "design-obligations" / "SCOUT-DESIGN-01.json"
    _semantic_obligations(semantic)
    queue = obligation_queue.materialize(
        state, design_sweep["sweep_id"], semantic, queue_path,
    )
    obligation = queue["obligations"][0]
    assert obligation["obligation_id"].startswith("OBL-")
    assert obligation["section_ids"] == ["SECTION-A"]
    assert "MUST process every record" in obligation["source_excerpt"]
    assert queue["session_id"] == "session-test"
    assert queue["section_checks"] == [{
        "section_id": "SECTION-A", "disposition": "obligations_extracted",
        "obligation_count": 1, "no_obligation_reason": "",
    }]

    raw_candidates = tmp_path / "candidates.semantic.json"
    raw_coverage = tmp_path / "coverage.semantic.json"
    candidate = {**_candidate_payload(), "obligation_id": obligation["obligation_id"]}
    ac.save_json(raw_candidates, [candidate])
    ac.save_json(raw_coverage, {"obligation_checks": [{
        "obligation_id": obligation["obligation_id"],
        "disposition": "candidate", "candidate_keys": ["first-record-only"],
        "code_search_summary": "Searched the runtime and its callers.",
        "countercheck": "No alternate traversal was found.",
    }]})
    handoff = tmp_path / "handoff.json"
    coverage_path = tmp_path / "coverage.json"
    reviewed_candidates, reviewed_coverage, _packet = _fresh_review(
        state, design_sweep["sweep_id"], raw_candidates, raw_coverage,
    )
    candidates, coverage = scout_materializer.materialize(
        state, design_sweep["sweep_id"], reviewed_candidates, reviewed_coverage,
        handoff, coverage_path,
    )
    projected = candidates[0]
    assert projected["observation_id"].startswith("CANDIDATE-")
    assert "candidate_key" not in projected
    assert projected["session_id"] == "session-test"
    assert projected["risk_sweep_plan_sha256"] == ac.sha256_file(
        state / "risk_sweep_plan.json"
    )
    assert projected["design_requirement"]["obligation"] == (
        "process every record in order"
    )
    assert projected["design_section_ids"] == ["SECTION-A"]
    assert projected["review_lenses"] == ["contract_mechanics"]
    assert handoff_merge.validate_artifact(projected, "risk", "candidate") == []
    scout_receipt.validate_coverage_contract(
        state, design_sweep, candidates, coverage,
    )


def test_obligation_source_must_stay_inside_assigned_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "bad.json"
    _semantic_obligations(semantic)
    value = ac.load_json(semantic)
    value["obligations"][0]["source_ref"]["line_start"] = 9
    value["obligations"][0]["source_ref"]["line_end"] = 9
    ac.save_json(semantic, value)
    with pytest.raises(ValueError, match="exactly one assigned section"):
        obligation_queue.materialize(
            state, design_sweep["sweep_id"], semantic, tmp_path / "queue.json",
        )


def test_empty_design_section_requires_explicit_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "empty.json"
    ac.save_json(semantic, {
        "obligations": [],
        "no_obligation_sections": [{
            "section_id": "SECTION-A",
            "reason": "The assigned range contains only explanatory background.",
        }],
    })
    queue = obligation_queue.materialize(
        state, design_sweep["sweep_id"], semantic, tmp_path / "queue.json",
    )
    assert queue["obligations"] == []
    assert queue["section_checks"][0]["disposition"] == (
        "no_implementable_obligation"
    )


def test_extractor_cannot_silently_skip_assigned_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "silent-skip.json"
    ac.save_json(semantic, {"obligations": [], "no_obligation_sections": []})
    with pytest.raises(ValueError, match="exactly account"):
        obligation_queue.materialize(
            state, design_sweep["sweep_id"], semantic, tmp_path / "queue.json",
        )


def test_model_cannot_supply_mechanical_candidate_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "obligations.json"
    queue_path = state / "design-obligations" / "SCOUT-DESIGN-01.json"
    _semantic_obligations(semantic)
    queue = obligation_queue.materialize(state, design_sweep["sweep_id"], semantic, queue_path)
    candidate = {
        **_candidate_payload(),
        "obligation_id": queue["obligations"][0]["obligation_id"],
        "session_id": "model-authored",
    }
    raw_candidates = tmp_path / "candidates.json"
    raw_coverage = tmp_path / "coverage.json"
    ac.save_json(raw_candidates, [candidate])
    ac.save_json(raw_coverage, {"obligation_checks": [{
        "obligation_id": queue["obligations"][0]["obligation_id"],
        "disposition": "candidate", "candidate_keys": ["first-record-only"],
        "code_search_summary": "Located the implementation.",
        "countercheck": "Checked the alternate path.",
    }]})
    reviewed_candidates, reviewed_coverage, _packet = _fresh_review(
        state, design_sweep["sweep_id"], raw_candidates, raw_coverage,
    )
    with pytest.raises(ValueError, match="tool-owned envelope"):
        scout_materializer.materialize(
            state, design_sweep["sweep_id"], reviewed_candidates, reviewed_coverage,
            tmp_path / "handoff.json", tmp_path / "canonical-coverage.json",
        )


def test_code_origin_candidates_are_bound_to_primary_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _design_sweep, code_sweep = _state(tmp_path, monkeypatch)
    candidate = {
        **_candidate_payload(), "primary_anchor_path": "src/runtime.c",
        "design_requirement": {
            "source_ref": {"path": "spec.txt", "line_start": 2, "line_end": 2},
            "subject": "service", "trigger": "records arrive",
            "obligation": "process every record", "observable_result": "all processed",
            "normative_strength": "mandatory", "applicability": "implemented service",
            "exceptions": [], "ambiguities": [],
        },
        "design_section_ids": ["SECTION-A"],
        "review_lenses": ["routing_capability"],
    }
    raw_candidates = tmp_path / "code-candidates.json"
    raw_coverage = tmp_path / "code-coverage.json"
    ac.save_json(raw_candidates, [candidate])
    ac.save_json(raw_coverage, {"anchor_checks": [{
        "anchor_path": "src/runtime.c", "disposition": "candidate",
        "candidate_keys": ["first-record-only"],
        "code_search_summary": "Read the anchor and retrieved the design.",
        "countercheck": "Checked its only caller.",
    }]})
    reviewed_candidates, reviewed_coverage, _packet = _fresh_review(
        state, code_sweep["sweep_id"], raw_candidates, raw_coverage,
    )
    candidates, coverage = scout_materializer.materialize(
        state, code_sweep["sweep_id"], reviewed_candidates, reviewed_coverage,
        tmp_path / "code-handoff.json", tmp_path / "code-canonical-coverage.json",
    )
    assert candidates[0]["origin_anchor_path"] == "src/runtime.c"
    assert candidates[0]["architecture_boundaries"] == ["BOUNDARY-A"]
    scout_receipt.validate_coverage_contract(state, code_sweep, candidates, coverage)


def test_blind_negative_review_hides_prior_reasoning_and_escalates_challenge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "obligations.json"
    queue_path = state / "design-obligations" / "SCOUT-DESIGN-01.json"
    _semantic_obligations(semantic)
    queue = obligation_queue.materialize(state, design_sweep["sweep_id"], semantic, queue_path)
    obligation_id = queue["obligations"][0]["obligation_id"]
    raw_candidates = tmp_path / "raw-candidates.json"
    raw_coverage = tmp_path / "raw-coverage.json"
    ac.save_json(raw_candidates, [])
    ac.save_json(raw_coverage, {"obligation_checks": [{
        "obligation_id": obligation_id, "disposition": "no_mismatch",
        "candidate_keys": [],
        "code_search_summary": "The loop visits records but stops at a configured cap.",
        "countercheck": "The configured cap can end traversal before input exhaustion.",
    }]})
    challenged = {
        **_candidate_payload(), "candidate_key": "fresh-review-challenge",
        "obligation_id": obligation_id,
    }
    reviews = [{
        "review_item_id": obligation_id,
        "verdict": "challenged",
        "independent_analysis": "The implementation can stop before every record is processed.",
        "execution_accounting": _execution_accounting(),
        "falsification_attempt": "Constructed an input longer than the implementation cap.",
        "candidate": challenged,
    }]
    reconciled_candidates, reconciled_coverage, packet = _fresh_review(
        state, design_sweep["sweep_id"], raw_candidates, raw_coverage, reviews,
    )
    packet_text = json.dumps(packet, ensure_ascii=False)
    assert "disposition" not in packet_text
    assert "no_mismatch" not in packet_text
    assert "untrusted_scout_notes" not in packet_text
    assert packet["batches"][0]["items"][0]["design_obligation"]
    assert ac.load_json(reconciled_candidates)[0]["candidate_key"] == (
        "fresh-review-challenge"
    )
    check = ac.load_json(reconciled_coverage)["obligation_checks"][0]
    assert check["disposition"] == "candidate"
    assert check["negative_review_status"] == "challenged"

    candidates, coverage = scout_materializer.materialize(
        state, design_sweep["sweep_id"], reconciled_candidates,
        reconciled_coverage, tmp_path / "handoff.json", tmp_path / "coverage.json",
    )
    assert len(candidates) == 1
    scout_receipt.validate_coverage_contract(state, design_sweep, candidates, coverage)


def test_blind_review_assemble_rejects_provider_reuse_across_batches(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet.json"
    ac.save_json(packet, {
        "version": negative_review.PACKET_VERSION,
        "batches": [
            {"batch_id": "BATCH-001", "items": []},
            {"batch_id": "BATCH-002", "items": []},
        ],
    })
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    ac.save_json(first, {"reviews": []})
    ac.save_json(second, {"reviews": []})
    with pytest.raises(ValueError, match="distinct provider sessions"):
        negative_review.assemble(
            packet, [first, second], ["provider-one", "provider-one"],
            tmp_path / "assembled.json",
        )


def test_blind_review_rejects_missing_execution_accounting(
    tmp_path: Path,
) -> None:
    packet = tmp_path / "packet.json"
    ac.save_json(packet, {
        "version": negative_review.PACKET_VERSION,
        "batches": [{
            "batch_id": "BATCH-001",
            "items": [{"review_item_id": "item-one"}],
        }],
    })
    raw_review = tmp_path / "raw-review.json"
    ac.save_json(raw_review, {"reviews": [{
        "review_item_id": "item-one", "verdict": "upheld",
        "independent_analysis": "A conclusion without execution accounting.",
        "falsification_attempt": "Attempted a counterexample.",
        "candidate": None,
    }]})
    with pytest.raises(ValueError, match="execution_accounting"):
        negative_review.assemble(
            packet, [raw_review], ["provider-review"], tmp_path / "review.json",
        )


def test_blind_review_batch_and_assemble_cli_do_not_require_state_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    packet = tmp_path / "packet.json"
    ac.save_json(packet, {
        "version": negative_review.PACKET_VERSION,
        "batches": [{
            "batch_id": "BATCH-001",
            "items": [{"review_item_id": "item-one"}],
        }],
    })
    extracted = tmp_path / "batch.json"
    monkeypatch.setattr(sys, "argv", [
        "negative_review.py", "batch", "--packet", str(packet),
        "--batch-id", "BATCH-001", "--output", str(extracted),
    ])
    assert negative_review.main() == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    assert ac.load_json(extracted)["items"][0]["review_item_id"] == "item-one"

    raw_review = tmp_path / "raw-review.json"
    ac.save_json(raw_review, {"reviews": [{
        "review_item_id": "item-one", "verdict": "upheld",
        "independent_analysis": "Independent evidence supports consistency.",
        "execution_accounting": _execution_accounting(),
        "falsification_attempt": "Tried to construct a counterexample.",
        "candidate": None,
    }]})
    assembled = tmp_path / "assembled.json"
    monkeypatch.setattr(sys, "argv", [
        "negative_review.py", "assemble", "--packet", str(packet),
        "--batch-review", str(raw_review),
        "--reviewer-provider-session-id", "provider-review-one",
        "--output", str(assembled),
    ])
    assert negative_review.main() == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    assert ac.load_json(assembled)["batches"][0][
        "reviewer_provider_session_id"
    ] == "provider-review-one"


def test_blind_review_partitions_scope_and_never_discloses_scout_reasoning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _design_sweep, code_sweep = _state(tmp_path, monkeypatch)
    anchors = [f"src/part-{number}.c" for number in range(1, 6)]
    code_sweep["anchor_paths"] = anchors
    raw_candidates = tmp_path / "raw-candidates.json"
    raw_coverage = tmp_path / "raw-coverage.json"
    ac.save_json(raw_candidates, [])
    ac.save_json(raw_coverage, {"anchor_checks": [{
        "anchor_path": anchor, "disposition": "no_mismatch",
        "candidate_keys": [],
        "code_search_summary": f"SECRET scout conclusion for {anchor}",
        "countercheck": f"SECRET scout countercheck for {anchor}",
    } for anchor in anchors]})
    packet_path = tmp_path / "packet.json"
    packet = negative_review.prepare(
        state, code_sweep["sweep_id"], raw_candidates, raw_coverage, packet_path,
    )
    assert [len(batch["items"]) for batch in packet["batches"]] == [4, 1]
    packet_text = packet_path.read_text(encoding="utf-8")
    assert "SECRET" not in packet_text
    assert "code_search_summary" not in packet_text
    assert "countercheck" not in packet_text
    extracted = negative_review.extract_batch(
        packet_path, "BATCH-002", tmp_path / "batch.json",
    )
    assert [item["review_item_id"] for item in extracted["items"]] == anchors[4:]


def test_blind_challenge_is_not_dropped_when_raw_scout_already_reached_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _design_sweep, code_sweep = _state(tmp_path, monkeypatch)
    anchors = [f"src/part-{number}.c" for number in range(1, 14)]
    code_sweep["anchor_paths"] = anchors
    raw_candidates = tmp_path / "raw-candidates.json"
    raw_coverage = tmp_path / "raw-coverage.json"
    ac.save_json(raw_candidates, [{
        "candidate_key": f"raw-{number}", "primary_anchor_path": anchor,
    } for number, anchor in enumerate(anchors[:12], start=1)])
    ac.save_json(raw_coverage, {"anchor_checks": [*({
        "anchor_path": anchor, "disposition": "candidate",
        "candidate_keys": [f"raw-{number}"],
        "code_search_summary": "Found a candidate.",
        "countercheck": "Checked the alternate path.",
    } for number, anchor in enumerate(anchors[:12], start=1)), {
        "anchor_path": anchors[-1], "disposition": "no_mismatch",
        "candidate_keys": [], "code_search_summary": "Initial close.",
        "countercheck": "Initial countercheck.",
    }]})
    packet_path = tmp_path / "packet.json"
    packet = negative_review.prepare(
        state, code_sweep["sweep_id"], raw_candidates, raw_coverage, packet_path,
    )
    raw_review = tmp_path / "raw-review.json"
    ac.save_json(raw_review, {"reviews": [{
        "review_item_id": anchors[-1], "verdict": "challenged",
        "independent_analysis": "The blind comparison found a mismatch.",
        "execution_accounting": _execution_accounting(),
        "falsification_attempt": "A reachable counterexample was confirmed.",
        "candidate": {
            "candidate_key": "blind-thirteenth",
            "primary_anchor_path": anchors[-1],
        },
    }]})
    review = tmp_path / "review.json"
    negative_review.assemble(
        packet_path, [raw_review], ["provider-blind"], review,
    )
    candidates_out = tmp_path / "candidates-out.json"
    coverage_out = tmp_path / "coverage-out.json"
    candidates, coverage = negative_review.reconcile(
        state, code_sweep["sweep_id"], raw_candidates, raw_coverage,
        packet_path, review, "provider-scout", candidates_out, coverage_out,
    )
    assert len(candidates) == 13
    assert candidates[-1]["candidate_key"] == "blind-thirteenth"
    assert coverage["anchor_checks"][-1]["negative_review_status"] == "challenged"


def test_blind_prepare_still_rejects_scout_initial_output_above_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _design_sweep, code_sweep = _state(tmp_path, monkeypatch)
    anchors = [f"src/part-{number}.c" for number in range(1, 14)]
    code_sweep["anchor_paths"] = anchors
    raw_candidates = tmp_path / "raw-candidates.json"
    raw_coverage = tmp_path / "raw-coverage.json"
    ac.save_json(raw_candidates, [{
        "candidate_key": f"raw-{number}", "primary_anchor_path": anchor,
    } for number, anchor in enumerate(anchors, start=1)])
    ac.save_json(raw_coverage, {"anchor_checks": [{
        "anchor_path": anchor, "disposition": "candidate",
        "candidate_keys": [f"raw-{number}"],
        "code_search_summary": "Found a candidate.",
        "countercheck": "Checked the alternate path.",
    } for number, anchor in enumerate(anchors, start=1)]})
    with pytest.raises(ValueError, match="raw scout exceeded"):
        negative_review.prepare(
            state, code_sweep["sweep_id"], raw_candidates, raw_coverage,
            tmp_path / "packet.json",
        )


def test_negative_review_rejects_reused_provider_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, design_sweep, _code_sweep = _state(tmp_path, monkeypatch)
    semantic = tmp_path / "obligations.json"
    queue_path = state / "design-obligations" / "SCOUT-DESIGN-01.json"
    _semantic_obligations(semantic)
    queue = obligation_queue.materialize(state, design_sweep["sweep_id"], semantic, queue_path)
    obligation_id = queue["obligations"][0]["obligation_id"]
    raw_candidates = tmp_path / "raw-candidates.json"
    raw_coverage = tmp_path / "raw-coverage.json"
    ac.save_json(raw_candidates, [])
    ac.save_json(raw_coverage, {"obligation_checks": [{
        "obligation_id": obligation_id, "disposition": "no_mismatch",
        "candidate_keys": [], "code_search_summary": "Read the implementation.",
        "countercheck": "Checked an alternate path.",
    }]})
    review_root = state / "semantic" / "negative-reviews"
    review_root.mkdir(parents=True)
    packet = review_root / "SCOUT-DESIGN-01.packet.json"
    review = review_root / "SCOUT-DESIGN-01.review.json"
    packet_value = negative_review.prepare(
        state, design_sweep["sweep_id"], raw_candidates, raw_coverage, packet,
    )
    raw_review = review_root / "SCOUT-DESIGN-01.BATCH-001.raw.json"
    ac.save_json(raw_review, {"reviews": [{
        "review_item_id": obligation_id, "verdict": "upheld",
        "independent_analysis": "The implementation satisfies the obligation.",
        "execution_accounting": _execution_accounting(),
        "falsification_attempt": "No counterexample was found.", "candidate": None,
    }]})
    negative_review.assemble(packet, [raw_review], ["same-session"], review)
    assert packet_value["review_item_count"] == 1
    with pytest.raises(ValueError, match="fresh provider session"):
        negative_review.reconcile(
            state, design_sweep["sweep_id"], raw_candidates, raw_coverage,
            packet, review, "same-session",
            tmp_path / "out-candidates.json", tmp_path / "out-coverage.json",
        )
