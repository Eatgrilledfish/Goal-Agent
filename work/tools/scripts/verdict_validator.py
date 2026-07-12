#!/usr/bin/env python3
"""Validate opencode verdicts against the cited design and source files.

This helper checks provenance and evidence truth. It never infers whether an
implementation is semantically correct; that judgement must already exist in
the opencode investigator, critic, and final-judge artifacts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import agent_common as ac
import handoff_merge as hm


EVIDENCE_INPUT_NAMES = (
    "design_claims.jsonl",
    "investigation_findings.jsonl",
    "critic_reviews.jsonl",
    "critic_review_history.jsonl",
    "dynamic_probes.jsonl",
    "agent_review_verdicts.jsonl",
)


def evidence_input_digests(root: Path) -> dict[str, str]:
    return {
        name: ac.sha256_file(root / name) if (root / name).is_file() else ""
        for name in EVIDENCE_INPUT_NAMES
    }


VALID_STATUSES = {"confirmed", "probable", "rejected"}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}
TRACE_KINDS = {
    "design_read", "code_search", "code_navigation", "code_read", "reverse_check",
    "test", "config_read", "history_read", "build_read", "analysis",
}
PROBE_INTERPRETATIONS = {
    "supports_contradiction", "disconfirms_contradiction", "inconclusive",
}


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _artifact_index(path: Path, key: str) -> tuple[dict[str, dict], list[str]]:
    values, errors = ac.load_jsonl(path)
    index: dict[str, dict] = {}
    for line_number, value in enumerate(values, start=1):
        identifier = str(value.get(key) or "")
        if not identifier:
            errors.append(f"{path.name}:{line_number}: missing {key}")
            continue
        if identifier in index:
            errors.append(f"{path.name}:{line_number}: duplicate {key} {identifier}")
        index[identifier] = value
    return index, errors


def _design_evidence_key(item: dict[str, Any]) -> tuple[str, int, int, str]:
    return (
        str(item.get("path") or ""),
        int(item.get("line_start") or 0),
        int(item.get("line_end") or item.get("line_start") or 0),
        ac.normalize_text(str(item.get("quote") or "")),
    )


def _code_evidence_key(item: dict[str, Any]) -> tuple[str, int, int, str]:
    return (
        str(item.get("file") or item.get("path") or ""),
        int(item.get("line_start") or 0),
        int(item.get("line_end") or item.get("line_start") or 0),
        ac.normalize_text(str(item.get("snippet") or "")),
    )


def _validate_dynamic_probe(
    probe: dict[str, Any],
    *,
    root: Path,
    session_id: str,
    finding_id: str,
    claim_id: str,
    claim: dict[str, Any],
) -> list[str]:
    prefix = f"{finding_id}: dynamic probe"
    errors = hm.validate_probe_contract(probe, prefix)
    if probe.get("session_id") != session_id:
        errors.append(f"{prefix} session does not match current session")
    if str(probe.get("finding_id") or "") != finding_id:
        errors.append(f"{prefix} finding_id does not match verdict")
    if str(probe.get("claim_id") or "") != claim_id:
        errors.append(f"{prefix} claim_id does not match verdict")

    oracle = probe.get("oracle") if isinstance(probe.get("oracle"), dict) else {}
    claim_oracle = claim.get("probe_oracle") if isinstance(claim.get("probe_oracle"), dict) else {}
    source_ref = claim.get("source_ref") if isinstance(claim.get("source_ref"), dict) else {}
    if oracle.get("claim_id") != claim_id:
        errors.append(f"{prefix} oracle.claim_id does not match the design claim")
    if oracle.get("claim_sha256") != hm.canonical_digest(claim):
        errors.append(f"{prefix} oracle.claim_sha256 does not match the current design claim")
    if oracle.get("source_sha256") != source_ref.get("source_sha256"):
        errors.append(f"{prefix} oracle.source_sha256 does not match the design source")
    for field in ("preconditions", "stimulus", "expected_observation"):
        if oracle.get(field) != claim_oracle.get(field):
            errors.append(f"{prefix} oracle.{field} does not match the design claim")

    isolation = probe.get("isolation") if isinstance(probe.get("isolation"), dict) else {}
    if isolation.get("kind") != "session_copy":
        errors.append(f"{prefix} isolation.kind must be session_copy")
    if isolation.get("original_target_unchanged") is not True:
        errors.append(f"{prefix} must attest original_target_unchanged=true")
    errors.extend(hm.validate_probe_workspace(probe, root, prefix))
    return errors


def validate_verdict(
    verdict: dict,
    session_id: str,
    code_root: Path,
    design_root: Path,
    claims: dict[str, dict],
    findings: dict[str, dict],
    critiques: dict[str, dict],
    probes: dict[str, dict],
    root: Path,
) -> list[str]:
    finding_id = str(verdict.get("finding_id") or "?")
    prefix = f"{finding_id}:"
    errors: list[str] = []
    status = str(verdict.get("status") or "").lower()
    if status not in VALID_STATUSES:
        return [f"{prefix} invalid status {status!r}"]
    if not _nonempty(verdict.get("finding_id")):
        errors.append(f"{prefix} missing finding_id")
    if verdict.get("session_id") != session_id:
        errors.append(f"{prefix} verdict session does not match current session")
    if status == "rejected":
        if not _nonempty(verdict.get("rejection_reason")):
            errors.append(f"{prefix} rejected verdict needs rejection_reason")
        finding = findings.get(finding_id)
        if not finding:
            errors.append(f"{prefix} finding_id not present in investigation_findings.jsonl")
            return errors
        assessment = finding.get("assessment")
        critic = critiques.get(finding_id)
        if assessment not in {
            "contradiction_supported", "uncertain", "design_satisfied",
        }:
            errors.append(f"{prefix} rejected verdict has invalid investigator assessment")
        if not critic:
            errors.append(f"{prefix} rejected finding lacks a critic artifact")
        elif critic.get("decision") != "reject_issue":
            errors.append(
                f"{prefix} rejected finding requires critic decision reject_issue"
            )
        return errors

    required = [
        "claim_id", "title", "confidence", "severity", "issue_type",
        "design_evidence", "code_evidence", "expected_behavior", "actual_behavior",
        "inconsistency", "impact",
        "scope_applicability", "false_positive_checks", "dynamic_validation", "critic_review",
        "tool_trace", "generalization_rationale",
    ]
    for field in required:
        if not _nonempty(verdict.get(field)):
            errors.append(f"{prefix} missing/empty {field}")

    claim_id = str(verdict.get("claim_id") or "")
    if claim_id and claim_id not in claims:
        errors.append(f"{prefix} claim_id not present in design_claims.jsonl")
    if finding_id not in findings:
        errors.append(f"{prefix} finding_id not present in investigation_findings.jsonl")
    critic_artifact = critiques.get(finding_id)
    if not critic_artifact:
        errors.append(f"{prefix} finding_id not present in critic_reviews.jsonl")

    try:
        confidence = float(verdict.get("confidence"))
        if not 0.0 <= confidence <= 1.0:
            errors.append(f"{prefix} confidence must be within 0..1")
    except (TypeError, ValueError):
        errors.append(f"{prefix} confidence must be numeric")
    if verdict.get("severity") not in VALID_SEVERITIES:
        errors.append(f"{prefix} severity must be one of {sorted(VALID_SEVERITIES)}")

    design_evidence = verdict.get("design_evidence")
    if not isinstance(design_evidence, list) or not design_evidence:
        errors.append(f"{prefix} design_evidence must be a non-empty array")
    else:
        for index, item in enumerate(design_evidence, start=1):
            errors.extend(ac.validate_source_evidence(item, design_root, f"{prefix} design_evidence[{index}]", "quote"))
        claim = claims.get(claim_id)
        if claim and _design_evidence_key(claim) not in {
            _design_evidence_key(item) for item in design_evidence if isinstance(item, dict)
        }:
            errors.append(f"{prefix} design_evidence does not include the associated design claim")

    code_evidence = verdict.get("code_evidence")
    if not isinstance(code_evidence, list) or not code_evidence:
        errors.append(f"{prefix} code_evidence must be a non-empty array")
    else:
        for index, item in enumerate(code_evidence, start=1):
            errors.extend(ac.validate_source_evidence(item, code_root, f"{prefix} code_evidence[{index}]", "snippet"))

    checks = verdict.get("false_positive_checks")
    if not isinstance(checks, list) or len(checks) < 2:
        errors.append(f"{prefix} at least two false_positive_checks are required")
    else:
        for index, check in enumerate(checks, start=1):
            if not isinstance(check, dict) or any(not _nonempty(check.get(k)) for k in ("question", "method", "target", "result")):
                errors.append(f"{prefix} false_positive_checks[{index}] needs question/method/target/result")

    trace = verdict.get("tool_trace")
    trace_kinds: set[str] = set()
    if not isinstance(trace, list) or len(trace) < 4:
        errors.append(f"{prefix} tool_trace must contain at least four real steps")
    else:
        for index, step in enumerate(trace, start=1):
            if not isinstance(step, dict) or any(not _nonempty(step.get(k)) for k in ("kind", "tool", "target", "purpose", "result")):
                errors.append(f"{prefix} tool_trace[{index}] needs kind/tool/target/purpose/result")
                continue
            if step.get("seq") != index:
                errors.append(f"{prefix} tool_trace[{index}] seq must equal {index}")
            kind = str(step.get("kind"))
            if kind not in TRACE_KINDS:
                errors.append(f"{prefix} tool_trace[{index}] has unsupported kind {kind!r}")
            trace_kinds.add(kind)
        if "design_read" not in trace_kinds:
            errors.append(f"{prefix} tool_trace lacks design_read")
        if not trace_kinds.intersection({"code_search", "code_navigation"}):
            errors.append(f"{prefix} tool_trace lacks code search/navigation")
        if "code_read" not in trace_kinds:
            errors.append(f"{prefix} tool_trace lacks code_read")
        if "reverse_check" not in trace_kinds:
            errors.append(f"{prefix} tool_trace lacks reverse_check")

    critic = verdict.get("critic_review")
    expected_critic_decisions = {
        "confirmed": {"confirm_contradiction", "confirm_optional_gap"},
        "probable": {"needs_more_evidence"},
    }[status]
    if not isinstance(critic, dict) or critic.get("decision") not in expected_critic_decisions:
        errors.append(
            f"{prefix} critic_review.decision must be one of {sorted(expected_critic_decisions)} for {status}"
        )
    elif any(
        not _nonempty(critic.get(k))
        for k in ("review_id", "challenges", "resolution", "normative_assessment")
    ):
        errors.append(
            f"{prefix} critic_review needs review_id/challenges/resolution/"
            "normative_assessment"
        )
    if critic_artifact and critic_artifact.get("decision") not in expected_critic_decisions:
        errors.append(f"{prefix} critic artifact decision is incompatible with {status}")
    if critic_artifact and isinstance(critic, dict) and critic.get("review_id") != critic_artifact.get("review_id"):
        errors.append(f"{prefix} critic review_id does not match critic artifact")
    if critic_artifact and isinstance(critic, dict):
        for field in (
            "decision", "challenges", "resolution", "review_context",
            "normative_assessment",
        ):
            if critic.get(field) != critic_artifact.get(field):
                errors.append(f"{prefix} critic_review.{field} does not match critic artifact")
    if status == "confirmed" and isinstance(critic_artifact, dict):
        normative = critic_artifact.get("normative_assessment")
        decision = critic_artifact.get("decision")
        binding_conflict = (
            decision == "confirm_contradiction"
            and isinstance(normative, dict)
            and normative.get("applicability") == "supported"
            and normative.get("actual_conflict") == "yes"
            and normative.get("obligation_status")
            not in {"optional_not_adopted", "informational"}
        )
        optional_gap = (
            decision == "confirm_optional_gap"
            and isinstance(normative, dict)
            and normative.get("applicability") == "supported"
            and normative.get("actual_conflict") == "no"
            and normative.get("obligation_status") == "optional_not_adopted"
        )
        if not binding_conflict and not optional_gap:
            errors.append(
                f"{prefix} confirmed verdict requires either a supported binding "
                "conflict or a supported, explicitly labeled optional design gap"
            )
    finding = findings.get(finding_id, {})
    selection = finding.get("dynamic_probe_selection") if isinstance(finding.get("dynamic_probe_selection"), dict) else {}
    if selection.get("disposition") not in {
        "selected", "not_selected", "not_suitable", "environment_limited",
    } or not _nonempty(selection.get("reason")):
        errors.append(f"{prefix} finding needs a valid dynamic_probe_selection disposition and reason")

    dynamic = verdict.get("dynamic_validation") if isinstance(verdict.get("dynamic_validation"), dict) else {}
    dynamic_status = dynamic.get("status")
    if dynamic_status not in PROBE_INTERPRETATIONS | {"not_run"}:
        errors.append(f"{prefix} dynamic_validation has invalid status {dynamic_status!r}")
    if not _nonempty(dynamic.get("reason")):
        errors.append(f"{prefix} dynamic_validation needs a reason")
    probe_id = str(dynamic.get("probe_id") or "")
    probe: dict[str, Any] | None = None
    if dynamic_status == "not_run":
        if probe_id:
            errors.append(f"{prefix} not_run dynamic_validation must not reference a probe")
        if selection.get("disposition") == "selected":
            errors.append(f"{prefix} selected probe cannot disappear as not_run")
    else:
        if not probe_id:
            errors.append(f"{prefix} dynamic_validation status {dynamic_status!r} needs probe_id")
        else:
            probe = probes.get(probe_id)
            if not probe:
                errors.append(f"{prefix} references unknown dynamic probe {probe_id}")
            else:
                errors.extend(_validate_dynamic_probe(
                    probe, root=root, session_id=session_id, finding_id=finding_id,
                    claim_id=claim_id, claim=claims.get(claim_id, {}),
                ))
                if probe.get("interpretation") != dynamic_status:
                    errors.append(f"{prefix} dynamic_validation status does not match probe interpretation")

    probe_review = critic_artifact.get("dynamic_probe_review") if isinstance(critic_artifact, dict) else None
    if not isinstance(probe_review, dict):
        errors.append(f"{prefix} critic artifact needs dynamic_probe_review")
    else:
        for field in ("status", "oracle_validity", "environment_validity", "reachability", "effect_on_decision"):
            if not _nonempty(probe_review.get(field)):
                errors.append(f"{prefix} critic dynamic_probe_review missing/empty {field}")
        if probe_review.get("status") != dynamic_status:
            errors.append(f"{prefix} critic dynamic probe status does not match verdict")
        if str(probe_review.get("probe_id") or "") != probe_id:
            errors.append(f"{prefix} critic dynamic probe_id does not match verdict")
    expected_assessments = {
        "confirmed": {"contradiction_supported"},
        "probable": {"contradiction_supported", "uncertain"},
    }[status]
    if finding.get("assessment") not in expected_assessments:
        errors.append(
            f"{prefix} investigator assessment must be one of {sorted(expected_assessments)} for {status}"
        )
    if finding and str(finding.get("claim_id") or "") != claim_id:
        errors.append(f"{prefix} finding claim_id does not match verdict claim_id")
    if critic_artifact and str(critic_artifact.get("claim_id") or "") != claim_id:
        errors.append(f"{prefix} critic claim_id does not match verdict claim_id")
    if finding and finding.get("expected_behavior") != verdict.get("expected_behavior"):
        errors.append(f"{prefix} expected_behavior does not match investigator finding")
    if finding and finding.get("observed_behavior") != verdict.get("actual_behavior"):
        errors.append(f"{prefix} actual_behavior does not match investigator finding")
    if finding and isinstance(design_evidence, list):
        if finding.get("design_evidence") != design_evidence:
            errors.append(f"{prefix} design_evidence must exactly match the investigator handoff")
        finding_design = {
            _design_evidence_key(item)
            for item in finding.get("design_evidence", [])
            if isinstance(item, dict)
        }
        verdict_design = {_design_evidence_key(item) for item in design_evidence if isinstance(item, dict)}
        if not finding_design.intersection(verdict_design):
            errors.append(f"{prefix} final design evidence was not handed off by the investigator")
    if finding and isinstance(code_evidence, list):
        if finding.get("code_evidence") != code_evidence:
            errors.append(f"{prefix} code_evidence must exactly match the investigator handoff")
        finding_code = {
            _code_evidence_key(item)
            for item in finding.get("code_evidence", [])
            if isinstance(item, dict)
        }
        verdict_code = {_code_evidence_key(item) for item in code_evidence if isinstance(item, dict)}
        if not finding_code.intersection(verdict_code):
            errors.append(f"{prefix} final code evidence was not handed off by the investigator")
    if finding and finding.get("false_positive_checks") != verdict.get("false_positive_checks"):
        errors.append(f"{prefix} false_positive_checks must exactly match the investigator handoff")
    if finding and finding.get("tool_trace") != verdict.get("tool_trace"):
        errors.append(f"{prefix} tool_trace must exactly match the investigator handoff")
    return errors


def normalized_issue(verdict: dict, session_id: str, claim: dict[str, Any]) -> dict:
    return {
        key: verdict.get(key)
        for key in (
            "finding_id", "claim_id", "status", "title", "confidence", "severity",
            "issue_type", "design_evidence", "code_evidence", "expected_behavior", "actual_behavior", "inconsistency",
            "impact", "scope_applicability", "false_positive_checks", "dynamic_validation",
        )
    } | {
        "normative_strength": claim.get("normative_strength", ""),
        "agent_review": {
            "source": "opencode",
            "session_id": session_id,
            "critic_review": verdict.get("critic_review"),
            "tool_trace": verdict.get("tool_trace"),
            "generalization_rationale": verdict.get("generalization_rationale"),
            "agent_notes": verdict.get("agent_notes", ""),
        }
    }


def run(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    root = ac.state_root(log_root, args.state_root)
    path_errors = ac.session_path_errors(
        root, code_root=code_root, design_root=design_root, result_root=result_root, log_root=log_root,
    )
    if path_errors:
        ac.save_json(log_root / "trace" / "evidence_validation.json", {
            "session_id": "", "passed": False, "input_digests": {},
            "validated_issues_sha256": "", "metrics": {}, "errors": path_errors,
        })
        print(json.dumps({"confirmed": 0, "probable": 0, "invalid": 0, "errors": len(path_errors)}))
        return 2
    state = ac.load_json(root / "agent_loop_state.json")
    session_id = str(state.get("session_id") or "")
    input_digests = evidence_input_digests(root)

    claims, artifact_errors = _artifact_index(root / "design_claims.jsonl", "claim_id")
    findings, finding_errors = _artifact_index(root / "investigation_findings.jsonl", "finding_id")
    critiques, critique_errors = _artifact_index(root / "critic_reviews.jsonl", "finding_id")
    probes, probe_errors = _artifact_index(root / "dynamic_probes.jsonl", "probe_id")
    artifact_errors.extend(finding_errors)
    artifact_errors.extend(critique_errors)
    artifact_errors.extend(probe_errors)
    for finding_id, critique in critiques.items():
        artifact_errors.extend(hm.validate_item(
            critique,
            artifact_type="critic",
            identifier=finding_id,
            session_id=session_id,
        ))
        artifact_errors.extend(hm._context_errors(
            critique,
            "critic",
            root,
            f"critic ({finding_id})",
        ))
    verdicts, verdict_parse_errors = ac.load_jsonl(root / "agent_review_verdicts.jsonl")
    artifact_errors.extend(verdict_parse_errors)

    latest: dict[str, dict] = {}
    for verdict in verdicts:
        finding_id = str(verdict.get("finding_id") or "")
        if finding_id:
            latest[finding_id] = verdict
    missing_critics = sorted(set(findings) - set(critiques))
    extra_critics = sorted(set(critiques) - set(findings))
    if missing_critics:
        artifact_errors.append(
            f"findings lack critic handoffs: {missing_critics}"
        )
    if extra_critics:
        artifact_errors.append(
            f"critic handoffs reference unknown findings: {extra_critics}"
        )
    artifact_errors.extend(hm.validate_probe_chain(findings, probes, critiques))
    artifact_errors.extend(hm.validate_critic_review_history(root, critiques))
    missing_verdicts = sorted(set(findings) - set(latest))
    extra_verdicts = sorted(set(latest) - set(findings))
    if missing_verdicts:
        artifact_errors.append(f"findings lack final-judge verdicts: {missing_verdicts}")
    if extra_verdicts:
        artifact_errors.append(f"verdicts reference unknown findings: {extra_verdicts}")
    issues: list[dict] = []
    rejected: list[dict] = []
    validation_errors = list(artifact_errors)
    for verdict in latest.values():
        errors = validate_verdict(verdict, session_id, code_root, design_root, claims, findings, critiques, probes, root)
        if errors:
            validation_errors.extend(errors)
            rejected.append({"finding_id": verdict.get("finding_id"), "status": verdict.get("status"), "errors": errors})
            continue
        if verdict.get("status") in {"confirmed", "probable"}:
            issues.append(normalized_issue(verdict, session_id, claims.get(str(verdict.get("claim_id") or ""), {})))

    confirmed = sum(issue["status"] == "confirmed" for issue in issues)
    probable = sum(issue["status"] == "probable" for issue in issues)
    output = {
        "validated_at": ac.now_iso(),
        "session_id": session_id,
        "verdict_count": len(latest),
        "confirmed": confirmed,
        "probable": probable,
        "invalid": len(rejected),
        "issues": issues,
        "rejected_verdicts": rejected,
    }
    ac.save_json(root / "validated_issues.json", output)
    validated_issues_sha256 = ac.sha256_file(root / "validated_issues.json")
    ac.save_json(log_root / "trace" / "evidence_validation.json", {
        "session_id": session_id,
        "passed": not validation_errors,
        "input_digests": input_digests,
        "validated_issues_sha256": validated_issues_sha256,
        "metrics": {"verdicts": len(latest), "confirmed": confirmed, "probable": probable, "invalid": len(rejected)},
        "errors": validation_errors,
    })
    state["updated_at"] = ac.now_iso()
    state["status"] = "validation_failed" if validation_errors else "validated"
    state["current_phase"] = "evidence_repair" if validation_errors else "reporting"
    state.setdefault("metrics", {}).update({"confirmed": confirmed, "probable": probable, "invalid_verdicts": len(rejected)})
    state["next_actions"] = (
        ["Repair verdict evidence using /logs/trace/evidence_validation.json, then rerun review."]
        if validation_errors else
        ["Run report and gate."]
    )
    ac.save_json(root / "agent_loop_state.json", state)
    ac.append_jsonl(root / "agent_run_ledger.jsonl", {
        "recorded_at": ac.now_iso(),
        "session_id": session_id,
        "event": "evidence_validation",
        "actor": "helper_validator",
        "phase": "validation_handoff",
        "status": "failed" if validation_errors else "complete",
        "summary": "Re-read cited source lines and validated agent handoff provenance.",
        "metrics": output | {"issues": None, "rejected_verdicts": None},
        "errors": validation_errors,
    })
    print(json.dumps({"confirmed": confirmed, "probable": probable, "invalid": len(rejected), "errors": len(validation_errors)}))
    return 0 if not validation_errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate opencode semantic verdict evidence.")
    ac.add_common_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
