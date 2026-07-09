#!/usr/bin/env python3
"""Consume opencode semantic review verdicts and validate evidence shape.

Final issue status is not computed by regex, confidence weights, or protocol
domain rules. Upstream scripts create candidates and review bundles; the
running opencode agent performs the semantic investigation and writes
the ``agent_review_verdicts.jsonl`` path specified by the review queue. This phase only checks that the
agent verdicts carry enough design/code evidence for the output schema and
converts them into ``validated_issues.json`` for ranking/reporting.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import rfc_common as rc

VALID_STATUSES = {"confirmed", "probable", "rejected"}
VERDICT_JSONL = "agent_review_verdicts.jsonl"


def load_candidates(work: Path) -> dict[str, dict]:
    path = work / "candidate_issues.json"
    if not path.exists():
        return {}
    candidates = rc.load_json(path).get("candidates", [])
    return {
        c.get("candidate_id", f"CANDIDATE-{idx:04d}"): c
        for idx, c in enumerate(candidates, start=1)
    }


def load_agent_verdicts(work: Path) -> tuple[list[dict], list[str]]:
    """Load opencode verdicts.

    JSONL is the required handoff format because the opencode loop can append
    incrementally and resume long reviews without rewriting prior verdicts.
    """
    errors: list[str] = []
    jsonl_path = work / VERDICT_JSONL
    if jsonl_path.exists():
        verdicts: list[dict] = []
        for line_no, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{VERDICT_JSONL}:{line_no}: invalid JSON: {exc}")
                continue
            if isinstance(obj, dict):
                verdicts.append(obj)
            else:
                errors.append(f"{VERDICT_JSONL}:{line_no}: verdict must be an object")
        return verdicts, errors

    return [], [f"{VERDICT_JSONL} missing; opencode semantic review has not run"]


def first_present(*values: Any, default: Any = "") -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return default


def status_from_verdict(verdict: dict) -> tuple[str, str | None]:
    raw = first_present(
        verdict.get("status"),
        verdict.get("agent_status"),
        verdict.get("decision"),
        default="",
    )
    status = str(raw).strip().lower()
    if status not in VALID_STATUSES:
        return "rejected", f"Invalid or missing opencode verdict status: {raw!r}"
    return status, None


def confidence_from_verdict(verdict: dict, status: str) -> tuple[float, str | None]:
    raw = first_present(verdict.get("confidence"), verdict.get("agent_confidence"), default=None)
    if raw is None:
        defaults = {"confirmed": 0.8, "probable": 0.6, "rejected": 0.0}
        return defaults[status], "Missing agent confidence; defaulted from status for sorting only."
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0, f"Invalid agent confidence: {raw!r}"
    return round(max(0.0, min(1.0, value)), 3), None


def normalize_design_evidence(source: dict) -> dict:
    de = source.get("design_evidence") or source.get("rfc_evidence") or {}
    return {
        "rfc": first_present(de.get("rfc"), de.get("doc_id"), source.get("rfc"), default=""),
        "section": first_present(de.get("section"), source.get("section"), default=""),
        "doc_path": first_present(de.get("doc_path"), de.get("source_doc"), source.get("source_doc"), default=""),
        "quote": first_present(de.get("quote"), de.get("requirement_text"), source.get("requirement_text"), default=""),
    }


def normalize_code_evidence(source: dict) -> list[dict]:
    out: list[dict] = []
    for ce in source.get("code_evidence", []) or []:
        if not isinstance(ce, dict):
            continue
        start = int(ce.get("line_start") or 0)
        end = int(ce.get("line_end") or start or 0)
        out.append({
            "file": ce.get("file", ""),
            "line_start": start,
            "line_end": end,
            "symbol": ce.get("symbol", ""),
            "snippet": ce.get("snippet", ""),
        })
    return out


def normalize_string_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)]


def evidence_errors(issue: dict) -> list[str]:
    errors: list[str] = []
    de = issue.get("design_evidence", {})
    if not de.get("quote"):
        errors.append("missing design_evidence.quote")
    if not (de.get("rfc") or de.get("doc_path")):
        errors.append("missing design_evidence.rfc/doc_path")
    ce = issue.get("code_evidence", [])
    if not ce:
        errors.append("missing code_evidence")
    for idx, item in enumerate(ce, start=1):
        if not item.get("file"):
            errors.append(f"code_evidence[{idx}] missing file")
        if not item.get("snippet"):
            errors.append(f"code_evidence[{idx}] missing snippet")
        if int(item.get("line_start") or 0) <= 0:
            errors.append(f"code_evidence[{idx}] missing line_start")
        if int(item.get("line_end") or 0) <= 0:
            errors.append(f"code_evidence[{idx}] missing line_end")
    if not issue.get("inconsistency"):
        errors.append("missing inconsistency explanation")
    if issue.get("status") == "confirmed" and not issue.get("impact"):
        errors.append("confirmed issue missing impact")
    if issue.get("status") == "confirmed" and not issue.get("false_positive_controls"):
        errors.append("confirmed issue missing false_positive_controls")
    review = issue.get("agent_review", {})
    if issue.get("status") == "confirmed" and review.get("source") != "opencode":
        errors.append("confirmed issue missing opencode agent review source")
    if issue.get("status") == "confirmed" and not review.get("generalization_rationale"):
        errors.append("confirmed issue missing generalization_rationale")
    return errors


def merge_verdict(candidate: dict | None, verdict: dict) -> dict:
    base = candidate or {}
    status, status_note = status_from_verdict(verdict)
    conf, conf_note = confidence_from_verdict(verdict, status)
    verdict_de = normalize_design_evidence(verdict)
    verdict_ce = normalize_code_evidence(verdict)

    title = first_present(
        verdict.get("title"),
        verdict.get("issue_title"),
        base.get("title"),
        default="Design/code inconsistency",
    )
    if status == "confirmed":
        # Confirmed verdicts must restate the evidence opencode judged. A bare
        # candidate_id plus status would let helper-script evidence become the
        # final issue, which violates the agent-review contract.
        design_evidence = verdict_de
        code_evidence = verdict_ce
    else:
        design_evidence = verdict_de if verdict_de.get("quote") else normalize_design_evidence(base)
        code_evidence = verdict_ce or normalize_code_evidence(base)

    related_files = sorted(set(normalize_string_list(
        first_present(verdict.get("related_files"), base.get("related_files"), default=[])
    )))
    if not related_files:
        related_files = sorted({c.get("file", "") for c in code_evidence if c.get("file")})

    notes = [n for n in [status_note, conf_note] if n]
    issue = {
        "issue_id": first_present(verdict.get("candidate_id"), base.get("candidate_id"), default="AGENT-ISSUE"),
        "title": title,
        "status": status,
        "confidence": conf,
        "normative_level": first_present(
            verdict.get("normative_level"),
            base.get("normative_level"),
            default="unknown",
        ),
        "detection_type": first_present(
            verdict.get("semantic_family"),
            verdict.get("detection_type"),
            base.get("detection_type"),
            default="agent_semantic_review",
        ),
        "design_evidence": design_evidence,
        "code_evidence": code_evidence,
        "inconsistency": first_present(
            verdict.get("inconsistency"),
            verdict.get("contradiction"),
            "" if status == "confirmed" else base.get("inconsistency"),
            default="",
        ),
        "impact": first_present(
            verdict.get("impact"),
            "" if status == "confirmed" else base.get("impact"),
            default="",
        ),
        "false_positive_controls": normalize_string_list(first_present(
            verdict.get("false_positive_controls"),
            verdict.get("false_positive_risks"),
            [] if status == "confirmed" else base.get("false_positive_controls"),
            default=[],
        )),
        "related_files": related_files,
        "requirement_id": first_present(verdict.get("requirement_id"), base.get("requirement_id"), default=""),
        "protocol_area": first_present(verdict.get("protocol_area"), base.get("protocol_area"), default=""),
        "agent_review": {
            "source": "opencode",
            "candidate_id": first_present(verdict.get("candidate_id"), base.get("candidate_id"), default=""),
            "agent_notes": first_present(
                verdict.get("agent_notes"),
                verdict.get("finality_reason"),
                verdict.get("review_notes"),
                default="",
            ),
            "generalization_rationale": verdict.get("generalization_rationale", ""),
            "tool_trace": verdict.get("tool_trace", verdict.get("tools_used", [])),
        },
    }

    errs = evidence_errors(issue)
    if conf_note and status == "confirmed":
        errs.append(conf_note)
    if errs and status == "confirmed":
        issue["status"] = "rejected"
        issue["confidence"] = min(issue["confidence"], 0.2)
        notes.append("Confirmed verdict rejected by schema/evidence validation: " + "; ".join(errs))
    elif errs and status == "probable":
        notes.append("Probable verdict has incomplete evidence: " + "; ".join(errs))
    elif errs:
        notes.append("Rejected verdict evidence issues: " + "; ".join(errs))

    if notes:
        issue["fp_note"] = " ".join(notes)
    return issue


def build_missing_verdict_issues(candidates: dict[str, dict]) -> list[dict]:
    issues: list[dict] = []
    for candidate_id, candidate in candidates.items():
        verdict = {
            "candidate_id": candidate_id,
            "status": "rejected",
            "confidence": 0.0,
            "agent_notes": "Missing opencode semantic review verdict.",
        }
        issue = merge_verdict(candidate, verdict)
        issue["fp_note"] = "Missing opencode semantic review verdict; helper candidates are not final evidence."
        issues.append(issue)
    return issues


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Validate opencode agent review verdicts.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    work = rc.agent_work_dir(Path(args.code_root))
    candidates = load_candidates(work)
    verdicts, load_errors = load_agent_verdicts(work)
    trace_root = rc.ensure_dir(Path(args.log_root) / "trace")

    if load_errors and not verdicts:
        issues = build_missing_verdict_issues(candidates)
        rc.save_json(work / "validated_issues.json", {
            "validated_at": rc.now_iso(),
            "agent_review_required": True,
            "agent_review_present": False,
            "errors": load_errors,
            "total": len(issues),
            "confirmed": 0,
            "probable": 0,
            "rejected": len(issues),
            "issues": issues,
        })
        rc.save_json(trace_root / "agent_review_consumption.json", {
            "consumed_at": rc.now_iso(),
            "agent_review_present": False,
            "errors": load_errors,
            "candidate_count": len(candidates),
        })
        print("[validator] opencode verdicts missing; review cannot promote candidates", file=sys.stderr)
        return 2

    seen: set[str] = set()
    issues: list[dict] = []
    for verdict in verdicts:
        candidate_id = str(first_present(verdict.get("candidate_id"), default=""))
        candidate = candidates.get(candidate_id)
        if candidate_id:
            seen.add(candidate_id)
        issues.append(merge_verdict(candidate, verdict))

    for candidate_id, candidate in candidates.items():
        if candidate_id not in seen:
            issue = merge_verdict(candidate, {
                "candidate_id": candidate_id,
                "status": "rejected",
                "confidence": 0.0,
                "agent_notes": "Candidate was queued but opencode did not write a verdict.",
            })
            issue["fp_note"] = "Candidate was queued but opencode did not write a verdict."
            issues.append(issue)

    rejected = [i for i in issues if i["status"] == "rejected"]
    (trace_root / "rejected_candidates.jsonl").write_text(
        "\n".join(json.dumps(i, ensure_ascii=False) for i in rejected)
        + ("\n" if rejected else ""),
        encoding="utf-8",
    )
    rc.save_json(trace_root / "agent_review_consumption.json", {
        "consumed_at": rc.now_iso(),
        "agent_review_present": True,
        "verdict_count": len(verdicts),
        "candidate_count": len(candidates),
        "missing_candidate_verdicts": sorted(set(candidates) - seen),
        "load_errors": load_errors,
    })
    rc.save_json(work / "validated_issues.json", {
        "validated_at": rc.now_iso(),
        "agent_review_required": True,
        "agent_review_present": True,
        "load_errors": load_errors,
        "total": len(issues),
        "confirmed": sum(1 for i in issues if i["status"] == "confirmed"),
        "probable": sum(1 for i in issues if i["status"] == "probable"),
        "rejected": sum(1 for i in issues if i["status"] == "rejected"),
        "issues": issues,
    })
    print(f"[validator] confirmed={sum(1 for i in issues if i['status']=='confirmed')} "
          f"probable={sum(1 for i in issues if i['status']=='probable')} "
          f"rejected={sum(1 for i in issues if i['status']=='rejected')}")
    return 0 if not load_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
