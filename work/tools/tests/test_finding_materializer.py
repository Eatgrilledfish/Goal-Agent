from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "work" / "tools" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_common as ac  # noqa: E402
import finding_materializer as materializer  # noqa: E402
import handoff_merge  # noqa: E402
import handoff_template  # noqa: E402
import stage_artifact_validator  # noqa: E402


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


@pytest.fixture
def finding_workspace(tmp_path: Path) -> dict[str, object]:
    code = tmp_path / "code"
    design = tmp_path / "design"
    state = tmp_path / "state"
    handoff = state / "handoffs" / "investigators" / "TASK-001"
    templates = state / "handoff-templates" / "investigators"
    for path in (code, design, handoff, templates):
        path.mkdir(parents=True)
    (code / "service.py").write_text(
        "def charge(amount):\n"
        "    if amount < 0:\n"
        "        return {'accepted': True}\n"
        "    return {'accepted': True}\n",
        encoding="utf-8",
    )
    (design / "contract.md").write_text(
        "# Contract\nThe service must reject negative amounts.\n",
        encoding="utf-8",
    )
    session_id = "session-finding-materializer"
    claim = {
        "claim_id": "CLAIM-001",
        "session_id": session_id,
        "document": "contract.md",
        "path": "contract.md",
        "section": "Contract",
        "line_start": 2,
        "line_end": 2,
        "quote": "The service must reject negative amounts.",
        "subject": "The charge operation",
        "trigger": "A caller submits a negative amount",
        "obligation": "Reject the negative amount.",
        "observable_result": "The request is rejected.",
    }
    task = {
        "task_id": "TASK-001",
        "session_id": session_id,
        "claim_id": "CLAIM-001",
        "claim_branch": ac.canonical_claim_branch(claim),
        "hypothesis": ac.canonical_claim_hypothesis(claim),
        "obligation_sha256": stage_artifact_validator.claim_obligation_sha256(claim),
        "review_lenses": [
            "error handling, state transitions, invariants, and configuration-dependent behavior",
        ],
        "status": "pending",
    }
    _write_jsonl(state / "design_claims.jsonl", [claim])
    _write_jsonl(state / "investigation_tasks.jsonl", [task])
    template = handoff_template.finding_template(task, claim)
    template_path = templates / "TASK-001.json"
    ac.save_json(template_path, template)
    semantic = {
        "task_id": "TASK-001",
        "assessment": "contradiction_supported",
        "observed_behavior": (
            "The reachable branch returns accepted=True for a negative amount."
        ),
        "code_locations": [{
            "file": "service.py", "line_start": 1, "line_end": 3,
            "symbol": "charge",
        }],
        "false_positive_checks": [
            {
                "question": "Is another branch enforcing rejection?",
                "method": "Review every return in charge",
                "target": "service.py:1-4",
                "result": "No return rejects a negative amount.",
            },
            {
                "question": "Is the cited branch unreachable?",
                "method": "Trace the public function entry",
                "target": "charge",
                "result": "The negative branch is reached directly for amount < 0.",
            },
        ],
        "design_read_result": "The contract requires negative amounts to be rejected.",
        "code_search_result": "The charge implementation is in service.py.",
        "reverse_check_result": "No compensating enforcement path was found.",
    }
    semantic_path = handoff / "semantic.json"
    output_path = handoff / "TASK-001.json"
    trace_path = tmp_path / "finding-materialization.json"
    ac.save_json(semantic_path, semantic)
    return {
        "code": code,
        "design": design,
        "state": state,
        "template": template,
        "template_path": template_path,
        "semantic": semantic,
        "semantic_path": semantic_path,
        "output_path": output_path,
        "trace_path": trace_path,
        "session_id": session_id,
    }


def _run(values: dict[str, object]) -> int:
    return materializer.main([
        "--input", str(values["semantic_path"]),
        "--template", str(values["template_path"]),
        "--code-root", str(values["code"]),
        "--output", str(values["output_path"]),
        "--trace", str(values["trace_path"]),
    ])


@pytest.mark.parametrize(
    ("assessment", "recommendation"),
    [
        ("contradiction_supported", "critic_review"),
        ("uncertain", "probable"),
        ("design_satisfied", "reject"),
    ],
)
def test_materializer_builds_valid_finding_and_maps_assessment(
    finding_workspace, assessment, recommendation,
):
    semantic = dict(finding_workspace["semantic"])
    semantic["assessment"] = assessment
    ac.save_json(Path(finding_workspace["semantic_path"]), semantic)

    assert _run(finding_workspace) == 0

    finding = ac.load_json(Path(finding_workspace["output_path"]))
    template = finding_workspace["template"]
    assert isinstance(template, dict)
    for field in handoff_merge.FINDING_TEMPLATE_FIELDS:
        assert finding[field] == template[field]
    assert finding["assessment"] == assessment
    assert finding["recommendation"] == recommendation
    assert finding["code_evidence"] == [{
        "file": "service.py",
        "line_start": 1,
        "line_end": 3,
        "symbol": "charge",
        "snippet": (
            "def charge(amount):\n"
            "    if amount < 0:\n"
            "        return {'accepted': True}"
        ),
    }]
    assert finding["supporting_evidence"] == [semantic["observed_behavior"]]
    assert [step["kind"] for step in finding["tool_trace"]] == [
        "design_read", "code_search", "code_read", "reverse_check",
    ]
    assert handoff_merge.validate_item(
        finding,
        artifact_type="finding",
        identifier=finding["finding_id"],
        session_id=str(finding_workspace["session_id"]),
        code_root=Path(finding_workspace["code"]),
        design_root=Path(finding_workspace["design"]),
        template=template,
    ) == []


def test_materializer_rejects_attempted_task_claim_or_evidence_override(
    finding_workspace,
):
    semantic = dict(finding_workspace["semantic"])
    semantic.update({
        "claim_id": "CLAIM-ATTACKER",
        "design_evidence": [{"quote": "invented"}],
        "code_evidence": [{"snippet": "invented"}],
        "recommendation": "critic_review",
    })
    ac.save_json(Path(finding_workspace["semantic_path"]), semantic)

    assert _run(finding_workspace) == 1
    assert not Path(finding_workspace["output_path"]).exists()
    trace = ac.load_json(Path(finding_workspace["trace_path"]))
    assert trace["passed"] is False
    assert "unsupported fields" in trace["errors"][0]


def test_materializer_rejects_stale_or_modified_pristine_template(
    finding_workspace,
):
    template = dict(finding_workspace["template"])
    template["expected_behavior"] = "An ungrounded replacement."
    ac.save_json(Path(finding_workspace["template_path"]), template)

    assert _run(finding_workspace) == 1
    assert not Path(finding_workspace["output_path"]).exists()
    trace = ac.load_json(Path(finding_workspace["trace_path"]))
    assert any("differs from current task and claim ledgers" in error for error in trace["errors"])


def test_materializer_rejects_out_of_range_code_location(finding_workspace):
    semantic = dict(finding_workspace["semantic"])
    semantic["code_locations"] = [{
        "file": "service.py", "line_start": 1, "line_end": 999,
    }]
    ac.save_json(Path(finding_workspace["semantic_path"]), semantic)

    assert _run(finding_workspace) == 1
    assert not Path(finding_workspace["output_path"]).exists()
    trace = ac.load_json(Path(finding_workspace["trace_path"]))
    assert any("exceeds 4 lines" in error for error in trace["errors"])


def test_materializer_cannot_overwrite_pristine_template(finding_workspace):
    original = Path(finding_workspace["template_path"]).read_bytes()
    finding_workspace["output_path"] = finding_workspace["template_path"]

    assert _run(finding_workspace) == 1
    assert Path(finding_workspace["template_path"]).read_bytes() == original
    trace = ac.load_json(Path(finding_workspace["trace_path"]))
    assert any("pristine template directory" in error for error in trace["errors"])
