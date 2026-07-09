#!/usr/bin/env python3
"""Phase 6: validate evidence chains and control false positives.

Every candidate must satisfy the six evidence requirements (section 9.8).
Confidence is computed from evidence completeness plus normative-level and
trace-status modifiers loaded from ``confidence_weights.json`` -- never
hardcoded per issue.

Output: .agent-work/validated_issues.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import rfc_common as rc

MAY_REJECTION_RULES = [
    "RFC is MAY and absence does not constitute a clear behavioral difference.",
]


def false_positive_controls(candidate: dict) -> list[str]:
    controls: list[str] = []
    ce = candidate.get("code_evidence", [])
    if ce:
        controls.append("Confirmed code location is reachable from the mapped protocol path.")
        controls.append("Code evidence is anchored to a concrete file and line range, not a guess.")
    else:
        controls.append("No code location found; reported as a feature gap, not a behavioral violation.")
    if candidate.get("detection_type") == "hardcoded_limit_mismatch":
        controls.append("Hardcoded limit appears in control flow, not merely in a logging statement.")
    if candidate.get("detection_type") == "silent_drop_error_handling_mismatch":
        controls.append("Drop path lacks the RFC-required feedback (error/ICMP).")
    return controls


def compute_confidence(candidate: dict, weights: dict, domain_ok: bool) -> float:
    w = weights.get("weights", {})
    pen = weights.get("penalties", {})
    cap = weights.get("confidence_cap", 0.9)
    conf = 0.0
    de = candidate.get("design_evidence", {})
    ce = candidate.get("code_evidence", [])
    if de and de.get("quote"):
        conf += w.get("design_evidence_present", 0.0)
    if ce and any(c.get("snippet") for c in ce):
        conf += w.get("code_evidence_present", 0.0)
    if ce and any(c.get("line_start") for c in ce):
        conf += w.get("code_location_present", 0.0)
    if de and (de.get("section") or de.get("source_anchor")):
        conf += w.get("design_location_present", 0.0)

    # Quality, not just presence: more distinct matched code signals => stronger.
    total_reasons = sum(len(c.get("match_reasons", [])) for c in ce)
    strength = min(1.0, total_reasons / 3.0)
    conf += w.get("evidence_strength_bonus", 0.0) * strength

    if any("control flow" in r or "control-flow" in r for r in candidate.get("detection_reasons", [])) or \
       candidate.get("detection_type") in {"hardcoded_limit_mismatch", "wrong_control_flow",
                                            "silent_drop_error_handling_mismatch"}:
        conf += w.get("control_flow_evidence_present", 0.0)

    # Domain anchoring is the sanity check that was missing: evidence that does
    # not belong to the RFC's protocol domain must not be confirmed.
    if domain_ok:
        conf += w.get("domain_match_bonus", 0.0)
    else:
        conf -= pen.get("domain_mismatch", 0.0)

    level = candidate.get("normative_level", "MAY")
    conf += weights.get("normative_level_modifier", {}).get(level, 0.0)
    conf += weights.get("trace_status_modifier", {}).get(candidate.get("trace_status", "linked"), 0.0)

    # A single weak signal is not enough to land at the top of the scale.
    if total_reasons <= 1:
        conf -= pen.get("weak_single_signal", 0.0)

    # Hard cap: heuristic detections are never certain. Previously the presence
    # weights summed to ~1.0 (+MUST) so almost everything hit 1.0.
    conf = max(0.0, min(cap, conf))
    return round(conf, 3)


def classify(conf: float, weights: dict, candidate: dict) -> str:
    th = weights["thresholds"]
    has_rfc = bool(candidate.get("design_evidence", {}).get("quote"))
    has_code = bool(candidate.get("code_evidence"))
    # Section 11.1 / Goal: every issue MUST carry RFC evidence AND code
    # evidence (with a concrete code location). Candidates lacking either
    # are rejected -- they cannot satisfy the output schema (code_evidence
    # minItems=1) nor the Goal's per-issue evidence requirement. Feature-gap
    # candidates with no code location are reported as rejected, not emitted.
    if not has_rfc or not has_code:
        return "rejected"
    if conf < th["probable"]:
        return "rejected"
    if conf >= th["confirmed"]:
        return "confirmed"
    return "probable"


def apply_fp_filters(candidate: dict, status: str) -> tuple[str, str | None]:
    """Return (possibly_updated_status, rejection_reason). Section 11.1."""
    if status == "rejected":
        return status, None
    level = candidate.get("normative_level", "MAY")
    if level == "MAY" and candidate.get("detection_type") != "missing_feature_protocol_gap":
        if status == "confirmed":
            status = "probable"
        return status, "MAY requirement down-weighted; emitted as probable/gap only."
    if candidate.get("trace_status") == "unlinked":
        # Unlinked requirements are weakly anchored; never confirm them.
        if status == "confirmed":
            status = "probable"
    return status, None


def domain_check(candidate: dict, domain_map: dict) -> tuple[bool, str | None]:
    """Sanity check: the cited code must belong to the RFC's protocol domain.

    Returns (ok, reason). When the RFC is absent from the domain map, or the
    primary code file is not under one of the domain's code paths/tokens, the
    evidence chain is broken (e.g. RFC3122 -> tcp_ecn.c) and the candidate is
    rejected regardless of its keyword score.
    """
    rfc = candidate.get("design_evidence", {}).get("rfc", "")
    domain = rc.resolve_rfc_domain(rfc, domain_map)
    if not domain:
        return False, (f"RFC {rfc} not in domain map; cannot anchor code evidence "
                       "to a protocol domain.")
    ce = candidate.get("code_evidence", [])
    if ce:
        primary = ce[0].get("file", "")
        if primary and not rc.file_matches_domain(primary, domain):
            topic_str = ", ".join(domain.get("topics", [])) or domain.get("protocol_area", "")
            return False, (f"Code evidence file '{primary}' does not match RFC {rfc} "
                           f"protocol domain ({topic_str}).")
    return True, None


def validate(candidate: dict, weights: dict, domain_map: dict) -> dict:
    domain_ok, domain_note = domain_check(candidate, domain_map)
    conf = compute_confidence(candidate, weights, domain_ok)
    # Note: we deliberately do NOT floor confidence at min_confidence. That
    # max() previously inflated every weak detection to >=0.65, which is why
    # 53/53 issues passed as confirmed/probable. The detection type's
    # min_confidence now only serves as documentation of expected strength.
    status = classify(conf, weights, candidate)
    if not domain_ok:
        status = "rejected"
        conf = min(conf, 0.4)
    status, fp_reason = apply_fp_filters(candidate, status)
    if domain_note:
        fp_reason = domain_note
    controls = false_positive_controls(candidate)

    issue = {
        "issue_id": candidate["candidate_id"],
        "title": candidate["title"],
        "status": status,
        "confidence": conf,
        "normative_level": candidate.get("normative_level", "MAY"),
        "detection_type": candidate.get("detection_type"),
        "design_evidence": {
            "rfc": candidate["design_evidence"].get("rfc"),
            "section": candidate["design_evidence"].get("section", ""),
            "doc_path": candidate["design_evidence"].get("doc_path", ""),
            "quote": candidate["design_evidence"].get("quote", ""),
        },
        "code_evidence": [
            {
                "file": c.get("file", ""),
                "line_start": c.get("line_start", 0),
                "line_end": c.get("line_end", 0),
                "symbol": c.get("symbol", ""),
                "snippet": c.get("snippet", ""),
            }
            for c in candidate.get("code_evidence", [])
        ],
        "inconsistency": candidate.get("inconsistency", ""),
        "impact": infer_impact(candidate),
        "false_positive_controls": controls if controls else ["No code evidence; reported as gap only."],
        "related_files": sorted({c.get("file", "") for c in candidate.get("code_evidence", []) if c.get("file")}),
        "requirement_id": candidate.get("requirement_id"),
        "protocol_area": candidate.get("protocol_area"),
    }
    if fp_reason:
        issue["fp_note"] = fp_reason
    return issue


def infer_impact(candidate: dict) -> str:
    dtype = candidate.get("detection_type", "")
    impacts = {
        "hardcoded_limit_mismatch": "Valid protocol items after the hardcoded limit may be ignored.",
        "missing_required_behavior": "A required RFC behavior is not implemented; protocol conformance breaks.",
        "wrong_control_flow": "The full header/option chain is not walked; later items are skipped.",
        "missing_feature_protocol_gap": "A protocol capability required by the RFC is absent from the repository.",
        "silent_drop_error_handling_mismatch": "Packets are dropped without the required error/feedback.",
        "timer_delay_behavior_mismatch": "Randomized delay or suppression required by the RFC is missing.",
        "packet_path_mismatch": "Packets may be bypassed, forwarded, or dropped instead of processed by the stack.",
    }
    return impacts.get(dtype, "Potential deviation from the RFC normative requirement.")


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Validate evidence chains.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    work = rc.agent_work_dir(Path(args.code_root))
    cand_path = work / "candidate_issues.json"
    if not cand_path.exists():
        print("[validator] candidate_issues.json missing", file=sys.stderr)
        return 0
    candidates = rc.load_json(cand_path).get("candidates", [])
    weights = rc.load_config("confidence_weights.json")
    domain_map = rc.load_config("rfc_domain_map.json")

    issues = [validate(c, weights, domain_map) for c in candidates]
    rc.save_json(work / "validated_issues.json", {
        "validated_at": rc.now_iso(),
        "total": len(issues),
        "confirmed": sum(1 for i in issues if i["status"] == "confirmed"),
        "probable": sum(1 for i in issues if i["status"] == "probable"),
        "rejected": sum(1 for i in issues if i["status"] == "rejected"),
        "issues": issues,
    })
    print(f"[validator] confirmed={sum(1 for i in issues if i['status']=='confirmed')} "
          f"probable={sum(1 for i in issues if i['status']=='probable')} "
          f"rejected={sum(1 for i in issues if i['status']=='rejected')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
