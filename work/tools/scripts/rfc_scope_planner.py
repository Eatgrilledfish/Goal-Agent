#!/usr/bin/env python3
"""Phase 2.5: RFC Scope Planner -- dynamic RFC selection.

Sits between ``load-docs`` and ``extract-spec``. Instead of extracting
requirements for *every* RFC the benchmark mentions, this stage decides which
RFCs deserve first-round high-precision detection, which are secondary
reference only, and which should be excluded.

The rule is the *policy* (FIX-rfc-scope-planner.md): we hardcode selection
preferences (prefer core protocol, prefer MUST-level, exclude operational
guidance, exclude obsoleted-when-successor-present, ...), **never** a fixed
list of RFC numbers. Every RFC discovered in ``benchmark.md`` is scored
against the policy and selected dynamically, so the system adapts to any RFC
set the benchmark throws at it.

Inputs (all under ``.agent-work/``):
  * benchmark_index.json   -- RFCs parsed from benchmark.md (+ protocol area)
  * rfc_manifest.json      -- fetched RFC docs (status ok/blocked, doc_path)
  * code_inventory_lite.json -- lightweight code anchor tables (stem/topic/dir)

Output:
  * .agent-work/rfc_scope_plan.json   -- structured plan consumed downstream
  * /logs/trace/rfc_scope_plan.md     -- human-readable trace

Scoring (per RFC, 0..1) -- see config/rfc_scope_policy.json ``scoring_weights``:

  final = 0.25*normative_strength
        + 0.25*implementation_boundary
        + 0.20*code_anchor
        + 0.15*contradiction_testability
        + 0.10*security_or_protocol_core
        + 0.05*current_rfc_status

A RFC is primary when ``final >= primary_score_threshold`` OR it is in the
top ``max_primary_rfcs``; the result is then capped at ``max_primary_rfcs``
and topped up to ``min_primary_rfcs`` from the highest-scoring remainder.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import rfc_common as rc

# Stronger RFC 2119 keywords carry more normative weight than soft ones.
_MUST_LEVELS = {"MUST", "MUST NOT", "SHALL", "SHALL NOT", "REQUIRED"}
_SOFT_LEVELS = {"SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", "OPTIONAL"}


def count_normative(text: str) -> tuple[int, int]:
    """Return ``(must_level_count, soft_level_count)`` in ``text``."""
    must = 0
    soft = 0
    for kw in rc.RFC2119_PATTERN.findall(text):
        if kw in _MUST_LEVELS:
            must += 1
        elif kw in _SOFT_LEVELS:
            soft += 1
    return must, soft


def normative_strength(must_n: int, soft_n: int, char_count: int) -> float:
    """0..1 score from MUST-level count + density.

    Density (MUST per 1k chars) rewards focused normative prose over long,
    sparse documents; the count term rewards RFCs with many obligations.
    """
    per_1k = must_n / (char_count / 1000.0 + 1.0)
    density_score = min(1.0, per_1k / 2.0)        # 2 MUST/k chars -> 1.0
    count_score = min(1.0, must_n / 20.0)         # 20 MUSTs -> 1.0
    if must_n == 0 and soft_n == 0:
        return 0.1
    return round(0.3 * count_score + 0.7 * density_score, 4)


def implementation_boundary(area: str, policy: dict) -> float:
    table = policy.get("protocol_area_impl_boundary", {})
    return float(table.get(area, table.get("unknown", 0.4)))


def security_score(area: str, policy: dict) -> float:
    table = policy.get("protocol_area_security", {})
    return float(table.get(area, table.get("unknown", 0.4)))


def code_anchor_score(domain: dict, inventory: dict) -> tuple[float, list[str]]:
    """0..1 code-locatability score + the list of anchored code paths.

    Uses the lite inventory's ``stem_index`` / ``topic_index`` to answer
    "can this RFC's expected code actually be found?" without a full parse.
    """
    if not inventory or inventory.get("status") != "ok":
        return 0.3, []

    stem_index = inventory.get("stem_index", {})
    topic_index = inventory.get("topic_index", {})
    code_paths = domain.get("code_paths", []) or []
    topics = domain.get("topics", []) or []

    # Token-level anchors: filename stems the domain cares about (e.g. ``nd6``).
    tokens = rc.domain_path_tokens(domain)
    anchored_stems = {t for t in tokens if t in stem_index}

    anchored: list[str] = []
    for cp in code_paths:
        cpl = cp.lower().rstrip("/")
        base = cpl.rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0] if "." in base else base
        if "." in cpl:
            # File-like path: anchored if its stem resolved in the inventory.
            if stem in stem_index:
                anchored.append(cp)
        else:
            # Directory-like path: anchored if the terminal component is a
            # specific (non-generic) stem present in the inventory.
            if stem and stem not in rc._GENERIC_PATH_TOKENS and stem in stem_index:
                anchored.append(cp)

    topic_overlap = [t for t in topics if t in topic_index]

    if not code_paths:
        # No declared code paths: only weak topic overlap can justify a link.
        score = 0.2 + 0.1 * min(2, len(topic_overlap))
        return round(min(0.5, score), 4), []

    ratio = len(anchored) / len(code_paths)
    score = 0.4 * ratio + 0.3 + 0.1 * min(2, len(topic_overlap))
    score = min(1.0, score)
    # A bare token hit (e.g. ``nd6`` present) rescues an otherwise-unanchored
    # domain -- it signals the code exists even if code_paths are imperfect.
    if not anchored and anchored_stems:
        score = max(score, 0.45)
        anchored = [f"token:{t}" for t in sorted(anchored_stems)]
    return round(score, 4), anchored


def current_status(rfc: str, domain: dict, supersession: dict,
                   loaded_rfcs: set[str], blocked: bool) -> float:
    """0..1 score for current validity / availability.

    * blocked (no doc)              -> 0.1
    * obsoleted by a successor that
      is also loaded in this run    -> 0.2  (exclude path)
    * a successor RFC (obsoletes an
      older loaded RFC)             -> 1.0
    * otherwise current             -> 0.8
    """
    if blocked:
        return 0.1
    obsoleted_by = [newer for newer, older in supersession.items() if rfc in older]
    if any(n in loaded_rfcs for n in obsoleted_by):
        return 0.2
    successors = supersession.get(rfc, [])
    if any(s in loaded_rfcs for s in successors):
        return 1.0
    return 0.8


def is_operational(domain: dict, rfc_title: str, policy: dict) -> bool:
    """True when the RFC reads as operational/deployment guidance, not protocol
    behavior an F-Stack implementation must enforce."""
    haystack = " ".join([rfc_title or ""] + list(domain.get("topics", []))
                        + list(domain.get("keywords", []))).lower()
    return any(kw in haystack for kw in policy.get("operational_keywords", []))


def risk_of(security: float) -> str:
    if security >= 0.8:
        return "high"
    if security >= 0.5:
        return "medium"
    return "low"


def score_rfc(entry: dict, domain: dict, manifest_entry: dict, doc_text: str,
              inventory: dict, policy: dict, supersession: dict,
              loaded_rfcs: set[str]) -> dict:
    rfc = entry["rfc"]
    area = domain.get("protocol_area", "unknown")
    blocked = manifest_entry is None or manifest_entry.get("status") != "ok"

    if blocked:
        norm = impl = anchor = test = sec = status = 0.0
        must_n = soft_n = 0
        anchored: list[str] = []
    else:
        must_n, soft_n = count_normative(doc_text)
        norm = normative_strength(must_n, soft_n, len(doc_text))
        impl = implementation_boundary(area, policy)
        anchor, anchored = code_anchor_score(domain, inventory)
        test = round((0.5 * norm + 0.5 * anchor) * (1.0 if must_n > 0 else 0.5), 4)
        sec = security_score(area, policy)
        status = current_status(rfc, domain, supersession, loaded_rfcs, blocked)

    weights = policy.get("scoring_weights", {})
    final = (
        weights.get("normative_strength", 0.25) * norm
        + weights.get("implementation_boundary", 0.25) * impl
        + weights.get("code_anchor", 0.20) * anchor
        + weights.get("contradiction_testability", 0.15) * test
        + weights.get("security_or_protocol_core", 0.10) * sec
        + weights.get("current_rfc_status", 0.05) * status
    )
    final = round(final, 4)

    return {
        "rfc": rfc,
        "number": entry.get("number"),
        "title": domain.get("title", "") or entry.get("title", ""),
        "protocol_area": area,
        "scores": {
            "normative_strength": norm,
            "implementation_boundary": impl,
            "code_anchor": anchor,
            "contradiction_testability": test,
            "security_or_protocol_core": sec,
            "current_rfc_status": status,
            "final": final,
        },
        "must_level_count": must_n,
        "soft_level_count": soft_n,
        "expected_code_areas": anchored,
        "operational": is_operational(domain, domain.get("title", ""), policy),
        "blocked": blocked,
        "obsoleted_by_successor_present": any(
            n in loaded_rfcs for n in [newer for newer, older in supersession.items() if rfc in older]
        ),
    }


def classify(scored: list[dict], policy: dict) -> dict:
    """Split scored RFCs into primary / secondary / excluded."""
    threshold = policy.get("primary_score_threshold", 0.75)
    max_primary = policy.get("max_primary_rfcs", 8)
    min_primary = policy.get("min_primary_rfcs", 4)
    core_policy = policy.get("core_protocol_primary", {})
    gap_policy = policy.get("feature_gap_primary", {})

    excluded: list[dict] = []
    eligible: list[dict] = []

    for s in scored:
        reasons = []
        if s["blocked"]:
            reasons.append("rfc document unavailable (blocked); cannot extract requirements")
        if s["obsoleted_by_successor_present"]:
            reasons.append("obsoleted by a newer RFC also present in this benchmark; successor preferred")
        if s["operational"]:
            reasons.append("operational/deployment guidance; weak direct implementation obligation")
        if s["protocol_area"] == "unknown" and not s.get("expected_code_areas"):
            reasons.append("no protocol-domain mapping or code anchors; reference-only RFC")
        if reasons:
            excluded.append({"rfc": s["rfc"], "reason": "; ".join(reasons),
                             "final_score": s["scores"]["final"]})
        else:
            eligible.append(s)

    # Highest final score first.
    eligible.sort(key=lambda s: s["scores"]["final"], reverse=True)

    def primary_selection_reason(s: dict) -> str | None:
        scores = s["scores"]
        if scores["final"] >= threshold:
            return "score-threshold"
        if (
            scores["implementation_boundary"] >= core_policy.get("min_implementation_boundary", 1.1)
            and scores["security_or_protocol_core"] >= core_policy.get("min_security_or_protocol_core", 1.1)
            and scores["code_anchor"] >= core_policy.get("min_code_anchor", 1.1)
        ):
            return "core-protocol-anchor"
        if (
            scores["normative_strength"] >= gap_policy.get("min_normative_strength", 1.1)
            and scores["implementation_boundary"] >= gap_policy.get("min_implementation_boundary", 1.1)
            and scores["code_anchor"] <= gap_policy.get("max_code_anchor", -0.1)
        ):
            return "feature-gap-probe"
        return None

    selected_reason: dict[str, str] = {}
    primary = []
    for s in eligible:
        reason = primary_selection_reason(s)
        if reason:
            primary.append(s)
            selected_reason[s["rfc"]] = reason

    # The documented policy says top max_primary RFCs also enter the primary
    # loop. This keeps important near-threshold protocol RFCs from being left
    # out before semantic review has a chance to inspect code evidence.
    for s in eligible[:max_primary]:
        if s not in primary:
            primary.append(s)
            selected_reason[s["rfc"]] = "top-score-within-max-primary"

    # Top-up to min_primary from the best remaining (covers small/threshold-miss sets).
    for s in eligible:
        if len(primary) >= min_primary:
            break
        if s in primary:
            continue
        primary.append(s)
        selected_reason[s["rfc"]] = "min-primary-top-up"
    # Hard cap.
    if len(primary) > max_primary:
        primary = primary[:max_primary]

    primary_set = {s["rfc"] for s in primary}
    secondary = [s for s in eligible if s["rfc"] not in primary_set]

    def to_plan_entry(s: dict, tier: int) -> dict:
        sec = s["scores"]["security_or_protocol_core"]
        anchor = s["scores"]["code_anchor"]
        reasons = []
        if s["scores"]["normative_strength"] >= 0.5:
            reasons.append("MUST-level normative obligations present")
        if anchor >= 0.5:
            reasons.append("code anchors locatable in the F-Stack tree")
        if s["scores"]["implementation_boundary"] >= 0.9:
            reasons.append("core protocol-stack behavior")
        if s["scores"]["security_or_protocol_core"] >= 0.8:
            reasons.append("security-relevant protocol area")
        return {
            "rfc": s["rfc"],
            "tier": tier,
            "score": s["scores"]["final"],
            "reason": "; ".join(reasons) or "selected by policy score",
            "selection_policy": selected_reason.get(s["rfc"], "secondary-reference"),
            "scope": s["protocol_area"],
            "expected_code_areas": s["expected_code_areas"],
            "risk": risk_of(sec),
            "testability": "code_checkable" if anchor >= 0.5 else "spec_only",
            "scores": s["scores"],
        }

    return {
        "selected_primary_rfcs": [to_plan_entry(s, 1) for s in primary],
        "secondary_rfcs": [to_plan_entry(s, 2) for s in secondary],
        "excluded_rfcs": excluded,
    }


def write_trace(plan: dict, scored: list[dict], log_root: Path) -> None:
    trace_dir = rc.ensure_dir(log_root / "trace")
    lines = ["# RFC Scope Plan Trace", ""]
    lines.append(f"- Primary: {len(plan['selected_primary_rfcs'])}")
    lines.append(f"- Secondary: {len(plan['secondary_rfcs'])}")
    lines.append(f"- Excluded: {len(plan['excluded_rfcs'])}")
    lines.append("")
    lines.append("## Scored RFCs (descending final score)")
    lines.append("")
    lines.append("| RFC | area | final | norm | impl | anchor | test | sec | status |")
    lines.append("|-----|------|-------|------|------|--------|------|-----|--------|")
    for s in sorted(scored, key=lambda x: x["scores"]["final"], reverse=True):
        sc = s["scores"]
        lines.append(
            f"| {s['rfc']} | {s['protocol_area']} | {sc['final']} | "
            f"{sc['normative_strength']} | {sc['implementation_boundary']} | "
            f"{sc['code_anchor']} | {sc['contradiction_testability']} | "
            f"{sc['security_or_protocol_core']} | {sc['current_rfc_status']} |"
        )
    lines.append("")
    lines.append("## Primary")
    for e in plan["selected_primary_rfcs"]:
        lines.append(f"- **{e['rfc']}** ({e['scope']}, score={e['score']}): {e['reason']}")
    lines.append("")
    lines.append("## Secondary")
    for e in plan["secondary_rfcs"]:
        lines.append(f"- {e['rfc']} (score={e['score']}): {e['reason']}")
    lines.append("")
    lines.append("## Excluded")
    for e in plan["excluded_rfcs"]:
        lines.append(f"- {e['rfc']}: {e['reason']}")
    (trace_dir / "rfc_scope_plan.md").write_text("\n".join(lines) + "\n",
                                                 encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="RFC Scope Planner (dynamic RFC selection).")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    work = rc.agent_work_dir(Path(args.code_root))
    policy = rc.load_config("rfc_scope_policy.json")

    bench_path = work / "benchmark_index.json"
    manifest_path = work / "rfc_manifest.json"
    inventory_path = work / "code_inventory_lite.json"

    if not bench_path.exists():
        rc.save_json(work / "rfc_scope_plan.json", {
            "planned_at": rc.now_iso(),
            "status": "blocked",
            "reason": "benchmark_index.json missing; run load-docs first",
            "selected_primary_rfcs": [],
            "secondary_rfcs": [],
            "excluded_rfcs": [],
        })
        print("[scope_planner] benchmark_index.json missing; run load-docs first", file=sys.stderr)
        return 0

    bench = rc.load_json(bench_path)
    manifest = rc.load_json(manifest_path) if manifest_path.exists() else {"rfcs": []}
    inventory = rc.load_json(inventory_path) if inventory_path.exists() else {"status": "missing"}
    domain_map = rc.load_config("rfc_domain_map.json")
    supersession = domain_map.get("supersession", {})
    domains = domain_map.get("domains", {})

    manifest_by_rfc = {e["rfc"]: e for e in manifest.get("rfcs", [])}
    loaded_rfcs = {e["rfc"] for e in manifest.get("rfcs", []) if e.get("status") == "ok"}

    scored: list[dict] = []
    for entry in bench.get("rfcs", []):
        rfc = entry["rfc"]
        domain = domains.get(rfc) or {
            "protocol_area": entry.get("protocol_area", "unknown"),
            "topics": entry.get("topics", []),
            "keywords": entry.get("keywords", []),
            "code_paths": entry.get("code_paths", []),
            "title": entry.get("title", ""),
        }
        man = manifest_by_rfc.get(rfc)
        doc_text = ""
        if man and man.get("status") == "ok" and man.get("doc_path"):
            doc_text = rc.read_text(Path(man["doc_path"]))
        scored.append(score_rfc(entry, domain, man, doc_text, inventory, policy,
                                supersession, loaded_rfcs))

    plan = classify(scored, policy)
    plan.update({
        "planned_at": rc.now_iso(),
        "status": "ok",
        "policy_file": "config/rfc_scope_policy.json",
        "primary_score_threshold": policy.get("primary_score_threshold", 0.75),
        "max_primary_rfcs": policy.get("max_primary_rfcs", 8),
        "min_primary_rfcs": policy.get("min_primary_rfcs", 4),
        "rfc_count_scored": len(scored),
    })
    rc.save_json(work / "rfc_scope_plan.json", plan)
    write_trace(plan, scored, Path(args.log_root))

    print(f"[scope_planner] primary={len(plan['selected_primary_rfcs'])} "
          f"secondary={len(plan['secondary_rfcs'])} "
          f"excluded={len(plan['excluded_rfcs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
