#!/usr/bin/env python3
"""Prepare evidence bundles for the opencode semantic review loop.

This script is intentionally not an LLM client and not a validator. It turns
pipeline artifacts into compact review tasks that the running opencode agent
can inspect, extend with just-in-time code searches, and adjudicate by writing
the JSONL file named by ``agent_review_queue.json``'s ``verdict_output``.

Final issue status must come from that opencode verdict file. Helper scripts
may provide recall, indexing, snippets, and run ledgers; they must not pretend
that regex or score thresholds are semantic agent review.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

import rfc_common as rc

MAX_REVIEW_CANDIDATES = 200
MAX_CONTEXT_CHARS = 12000
MAX_RELATED_REQUIREMENTS = 8
MAX_DESIGN_DOCS = 80
MAX_PROTOCOL_FOCUS = 30
MAX_FOCUS_FILES = 12
MAX_FOCUS_CONTEXTS = 4

GENERIC_PROTOCOL_ALIASES = {
    "protocol", "internet", "version", "ipv6", "ip6", "host", "configuration",
    "request", "reply", "message", "messages", "option", "options", "header",
    "headers", "address", "addresses", "node", "nodes", "router", "routers",
    "group", "groups", "dynamic", "solicit", "renew", "rebind", "lease",
    "done", "report", "reports", "listener", "listeners",
}


def compact(text: str, limit: int = MAX_CONTEXT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return head + "\n...\n[truncated]\n...\n" + tail


def load_json_if_exists(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return rc.load_json(path)


def read_file_window(code_root: Path, file_rel: str, start: int, end: int,
                     pad: int = 40) -> dict:
    path = code_root / file_rel
    if not path.exists():
        return {
            "file": file_rel,
            "line_start": start,
            "line_end": end,
            "error": "file not found",
        }
    lines = rc.read_text(path).splitlines()
    lo = max(1, int(start or 1) - pad)
    hi = min(len(lines), int(end or start or 1) + pad)
    return {
        "file": file_rel,
        "line_start": lo,
        "line_end": hi,
        "snippet": compact("\n".join(lines[lo - 1:hi]), 8000),
    }


def build_symbol_lookup(code_index: dict) -> dict[tuple[str, str], dict]:
    lookup: dict[tuple[str, str], dict] = {}
    for frec in code_index.get("files", []):
        file_rel = frec.get("file", "")
        for sym in frec.get("symbols", []):
            name = sym.get("name", "")
            if file_rel and name:
                lookup[(file_rel, name)] = sym
    return lookup


def code_contexts(candidate: dict, code_root: Path, symbol_lookup: dict) -> list[dict]:
    contexts: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for ce in candidate.get("code_evidence", [])[:4]:
        file_rel = ce.get("file", "")
        if not file_rel:
            continue
        start = int(ce.get("line_start") or 1)
        end = int(ce.get("line_end") or start)
        symbol = ce.get("symbol", "")
        sym = symbol_lookup.get((file_rel, symbol)) if symbol else None
        if sym:
            start = int(sym.get("line_start") or start)
            end = int(sym.get("line_end") or end)
        key = (file_rel, start, end)
        if key in seen:
            continue
        seen.add(key)
        ctx = read_file_window(code_root, file_rel, start, end)
        ctx.update({
            "symbol": symbol,
            "candidate_line_start": ce.get("line_start", 0),
            "candidate_line_end": ce.get("line_end", 0),
            "candidate_match_reasons": ce.get("match_reasons", []),
            "candidate_evidence_lines": ce.get("evidence_lines", []),
        })
        contexts.append(ctx)
    return contexts


def terms_from_text(text: str, limit: int = 14) -> list[str]:
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "shall", "should",
        "must", "will", "may", "not", "are", "all", "any", "when", "then",
        "into", "only", "each", "such", "have", "has", "was", "were",
    }
    terms: list[str] = []
    for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower()):
        if term in stop:
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def related_requirements(candidate: dict, requirements: list[dict]) -> list[dict]:
    req_id = candidate.get("requirement_id")
    de = candidate.get("design_evidence", {})
    rfc = de.get("rfc") or candidate.get("rfc")
    section = de.get("section", "")
    query_terms = set(terms_from_text(" ".join([
        candidate.get("title", ""),
        candidate.get("inconsistency", ""),
        de.get("quote", ""),
    ])))
    scored: list[tuple[int, dict]] = []
    for req in requirements:
        score = 0
        if req_id and req.get("requirement_id") == req_id:
            score += 20
        if rfc and req.get("rfc") == rfc:
            score += 4
        if section and req.get("section") == section:
            score += 4
        hay = " ".join([
            req.get("title", ""),
            req.get("requirement_text", ""),
            req.get("section", ""),
        ]).lower()
        score += sum(1 for t in query_terms if t in hay)
        if score:
            scored.append((score, req))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "requirement_id": req.get("requirement_id"),
            "rfc": req.get("rfc"),
            "section": req.get("section"),
            "normative_level": req.get("normative_level"),
            "source_doc": req.get("source_doc"),
            "requirement_text": compact(req.get("requirement_text", ""), 1200),
        }
        for _, req in scored[:MAX_RELATED_REQUIREMENTS]
    ]


def design_doc_manifest(design_root: Path, benchmark: Path) -> list[dict]:
    docs: list[Path] = []
    if benchmark.exists():
        docs.append(benchmark)
    if design_root.exists():
        for suffix in ("*.md", "*.txt", "*.rst", "*.adoc"):
            docs.extend(sorted(design_root.rglob(suffix)))
    out: list[dict] = []
    seen: set[Path] = set()
    for path in docs:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            text = rc.read_text(path)
        except OSError:
            continue
        out.append({
            "path": str(path),
            "relative_path": str(path.relative_to(design_root)) if design_root in path.parents or path == design_root else str(path),
            "char_count": len(text),
            "preview": compact(text, 2000),
        })
        if len(out) >= MAX_DESIGN_DOCS:
            break
    return out


def code_inventory_summary(code_index: dict) -> dict:
    files = code_index.get("files", [])
    symbol_count = sum(len(f.get("symbols", [])) for f in files)
    zero_symbol = sum(1 for f in files if not f.get("symbols"))
    by_suffix: dict[str, int] = {}
    for frec in files:
        suffix = Path(frec.get("file", "")).suffix or "(none)"
        by_suffix[suffix] = by_suffix.get(suffix, 0) + 1
    return {
        "file_count": code_index.get("file_count", len(files)),
        "symbol_count": code_index.get("symbol_count", symbol_count),
        "zero_symbol_files": zero_symbol,
        "suffix_counts": dict(sorted(by_suffix.items())),
    }


def protocol_aliases(domain: dict) -> list[str]:
    text = " ".join([
        domain.get("protocol_area", ""),
        domain.get("title", ""),
        " ".join(domain.get("topics", [])),
        " ".join(domain.get("keywords", [])),
    ])
    aliases = terms_from_text(text, 18)
    out: list[str] = []
    for alias in aliases:
        if alias not in out:
            out.append(alias)
        if alias.endswith("v6"):
            compact_alias = alias[:-2] + "6"
            if compact_alias not in out:
                out.append(compact_alias)
    return out[:20]


def specific_protocol_aliases(domain: dict) -> list[str]:
    return [
        alias for alias in protocol_aliases(domain)
        if alias not in GENERIC_PROTOCOL_ALIASES and len(alias) >= 3
    ]


def matching_index_files(code_index: dict, code_path: str) -> list[dict]:
    cp = code_path.strip().lstrip("/")
    if not cp:
        return []
    is_file = "." in Path(cp).name
    out: list[dict] = []
    for frec in code_index.get("files", []):
        file_rel = frec.get("file", "")
        if not file_rel:
            continue
        if is_file and file_rel == cp:
            out.append(frec)
        elif not is_file and file_rel.startswith(cp.rstrip("/") + "/"):
            out.append(frec)
    return out


def code_file_context_for_terms(code_root: Path, file_rel: str, terms: list[str]) -> dict | None:
    path = code_root / file_rel
    if not path.exists():
        return None
    lines = rc.read_text(path).splitlines()
    lowered_terms = [t.lower() for t in terms if t]
    hit_line = 1
    hit_term = ""
    for idx, line in enumerate(lines, start=1):
        low = line.lower()
        for term in lowered_terms:
            if term in low:
                hit_line = idx
                hit_term = term
                break
        if hit_term:
            break
    start = max(1, hit_line - 35)
    end = min(len(lines), hit_line + 85)
    return {
        "file": file_rel,
        "line_start": start,
        "line_end": end,
        "matched_term": hit_term,
        "snippet": compact("\n".join(lines[start - 1:end]), 9000),
    }


def code_file_contexts_for_terms(code_root: Path, file_rel: str, terms: list[str],
                                 max_contexts: int = 2) -> list[dict]:
    path = code_root / file_rel
    if not path.exists():
        return []
    lines = rc.read_text(path).splitlines()
    out: list[dict] = []
    used_ranges: list[tuple[int, int]] = []
    for term in [t.lower() for t in terms if t]:
        hit_line = 0
        for idx, line in enumerate(lines, start=1):
            if term in line.lower():
                hit_line = idx
                break
        if not hit_line:
            continue
        start = max(1, hit_line - 35)
        end = min(len(lines), hit_line + 85)
        if any(not (end < lo or start > hi) for lo, hi in used_ranges):
            continue
        used_ranges.append((start, end))
        out.append({
            "file": file_rel,
            "line_start": start,
            "line_end": end,
            "matched_term": term,
            "snippet": compact("\n".join(lines[start - 1:end]), 9000),
        })
        if len(out) >= max_contexts:
            break
    return out


def keyword_symbol_hits(code_index: dict, aliases: list[str], limit: int = MAX_FOCUS_FILES) -> list[dict]:
    hits: list[dict] = []
    alias_l = [a.lower() for a in aliases if len(a) >= 3]
    for frec in code_index.get("files", []):
        file_rel = frec.get("file", "")
        for sym in frec.get("symbols", [])[:80]:
            hay = " ".join([
                file_rel,
                sym.get("name", ""),
                sym.get("signature", ""),
            ]).lower()
            matched = [a for a in alias_l if a in hay]
            if matched:
                hits.append({
                    "file": file_rel,
                    "symbol": sym.get("name", ""),
                    "line_start": sym.get("line_start", 0),
                    "line_end": sym.get("line_end", 0),
                    "matched_aliases": matched[:5],
                })
                break
    return hits[:limit]


def notable_requirements_for_rfc(requirements: list[dict], rfc: str, limit: int = 14) -> list[dict]:
    review_terms = {
        "unsolicited", "random", "delay", "proxy", "all", "each", "every",
        "fragment", "extension", "header", "chain", "multicast", "listener",
        "client", "server", "relay", "configuration", "feature", "option",
        "timer", "state", "route", "forward", "bypass", "drop",
    }
    level_bonus = {
        "SHOULD": 7,
        "SHOULD NOT": 7,
        "MAY": 6,
        "OPTIONAL": 5,
        "MUST": 4,
        "MUST NOT": 4,
        "REQUIRED": 4,
    }
    scored: list[tuple[int, dict]] = []
    for req in requirements:
        if req.get("rfc") != rfc:
            continue
        text = req.get("requirement_text", "")
        low = text.lower()
        score = level_bonus.get(req.get("normative_level", ""), 1)
        score += sum(2 for term in review_terms if term in low)
        if "proxy" in low and "unsolicited" in low:
            score += 24
        if "random" in low and "delay" in low:
            score += 20
        if "unsolicited" in low:
            score += 8
        if ("all" in low or "every" in low or "each" in low) and "option" in low:
            score += 14
        if "fragment" in low and ("header" in low or "chain" in low):
            score += 14
        if req.get("section"):
            score += 1
        scored.append((score, req))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "requirement_id": req.get("requirement_id"),
            "section": req.get("section"),
            "normative_level": req.get("normative_level"),
            "title": req.get("title", ""),
            "requirement_text": compact(req.get("requirement_text", ""), 1200),
        }
        for _, req in scored[:limit]
    ]


def code_path_summaries(code_index: dict, code_root: Path, domain: dict) -> tuple[list[dict], list[dict]]:
    summaries: list[dict] = []
    contexts: list[dict] = []
    aliases = protocol_aliases(domain)
    # Path/routing terms first: for packet-path files, the interesting logic is
    # often in classifier/filter functions rather than protocol-name mentions.
    path_terms = [
        "filter", "classify", "classifier", "route", "forward", "bypass",
        "offload", "tap", "tun", "kni", "kernel", "multicast", "multi",
        "icmp", "icmp6", "icmpv6",
    ] + aliases
    for code_path in domain.get("code_paths", [])[:12]:
        files = matching_index_files(code_index, code_path)
        is_generic_dir = code_path.rstrip("/") in {"lib", "lib/", "freebsd/lib/libc/net"}
        summaries.append({
            "code_path": code_path,
            "matched_file_count": len(files),
            "sample_files": [
                {
                    "file": f.get("file", ""),
                    "symbol_count": len(f.get("symbols", [])),
                    "sample_symbols": [s.get("name", "") for s in f.get("symbols", [])[:6]],
                }
                for f in files[:MAX_FOCUS_FILES]
            ],
            "generic_path": bool(is_generic_dir),
        })
        if "." in Path(code_path.rstrip("/")).name:
            contexts.extend(code_file_contexts_for_terms(code_root, code_path, path_terms, max_contexts=2))
        elif not is_generic_dir:
            for f in files[:2]:
                contexts.extend(code_file_contexts_for_terms(
                    code_root, f.get("file", ""), path_terms, max_contexts=1
                ))
        if len(contexts) >= MAX_FOCUS_CONTEXTS:
            contexts = contexts[:MAX_FOCUS_CONTEXTS]
            break
    return summaries, contexts


def build_protocol_domain_focus(work: Path, code_root: Path, code_index: dict) -> list[dict]:
    benchmark = load_json_if_exists(work / "benchmark_index.json", {})
    domain_map = rc.load_config("rfc_domain_map.json")
    domains = domain_map.get("domains", {})
    requirements = load_json_if_exists(work / "rfc_requirements.json", {}).get("requirements", [])
    rfcs = [entry.get("rfc") for entry in benchmark.get("rfcs", []) if entry.get("rfc")]
    if not rfcs:
        rfcs = sorted({req.get("rfc") for req in requirements if req.get("rfc")})
    focus: list[dict] = []
    for rfc in rfcs[:MAX_PROTOCOL_FOCUS]:
        domain = domains.get(rfc, {})
        if not domain:
            continue
        aliases = protocol_aliases(domain)
        specific_aliases = specific_protocol_aliases(domain) or aliases
        path_summaries, contexts = code_path_summaries(code_index, code_root, domain)
        strong_hits = keyword_symbol_hits(code_index, specific_aliases)
        pattern = "|".join(re.escape(a) for a in specific_aliases[:8]) or re.escape(rfc.lower())
        feature_gap_note = (
            "No strong file/symbol identifier hit for the protocol aliases; opencode should verify "
            "whether this is a true feature gap, an aliasing issue, generated code, or delegated library support."
            if not strong_hits else
            "Strong file/symbol identifier hits exist; opencode should inspect whether they implement the design behavior."
        )
        focus.append({
            "rfc": rfc,
            "title": domain.get("title", ""),
            "protocol_area": domain.get("protocol_area", ""),
            "topics": domain.get("topics", []),
            "aliases": aliases,
            "specific_aliases": specific_aliases,
            "domain_keywords": domain.get("keywords", []),
            "configured_code_paths": domain.get("code_paths", []),
            "notable_requirements": notable_requirements_for_rfc(requirements, rfc),
            "code_path_summaries": path_summaries,
            "code_path_contexts": contexts,
            "strong_identifier_hits": strong_hits,
            "feature_gap_probe": feature_gap_note,
            "suggested_commands": [
                f"rg -n --hidden {shlex.quote(pattern)} {shlex.quote(str(code_root))}",
                (
                    "rg -n --hidden "
                    + shlex.quote("filter|classify|route|forward|bypass|offload|tap|tun|kernel|multicast|icmpv6|icmp6")
                    + f" {shlex.quote(str(code_root))}"
                ),
            ],
        })
    return focus


def rfc_from_candidate(candidate: dict) -> str:
    direct = candidate.get("rfc") or candidate.get("design_evidence", {}).get("rfc")
    if direct:
        return str(direct)
    req_id = str(candidate.get("requirement_id") or candidate.get("candidate_id") or "")
    m = re.match(r"(RFC\d+|DESIGN-[A-Za-z0-9-]+)", req_id)
    return m.group(1) if m else ""


def candidate_counts_by_rfc(candidates: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        rfc = rfc_from_candidate(candidate)
        if rfc:
            counts[rfc] = counts.get(rfc, 0) + 1
    return counts


def protocol_focus_priority(focus: dict, candidate_count: int = 0) -> tuple[int, list[str]]:
    """Rank protocol domains for opencode review convergence.

    This is a generic triage heuristic, not a finding rule. It promotes domains
    where semantic review tends to be high-value: feature gaps, optional/timer
    behavior, full-chain parsing, all-options processing, and domains with many
    recalled candidates.
    """
    score = candidate_count * 3
    reasons: list[str] = []
    if candidate_count:
        reasons.append(f"{candidate_count} recalled candidate(s)")
    if str(focus.get("feature_gap_probe", "")).startswith("No strong"):
        score += 80
        reasons.append("possible protocol/feature gap")
    if focus.get("code_path_contexts"):
        score += min(20, len(focus["code_path_contexts"]) * 3)
        reasons.append("configured code path context available")
    for req in focus.get("notable_requirements", [])[:8]:
        low = " ".join([
            req.get("title", ""),
            req.get("requirement_text", ""),
            req.get("normative_level", ""),
        ]).lower()
        if "proxy" in low and ("random" in low or "delay" in low):
            score += 55
            reasons.append("proxy random/delay requirement")
        if "proxy" in low and "unsolicited" in low:
            score += 55
            reasons.append("proxy unsolicited behavior")
        if ("all" in low or "every" in low or "each" in low) and "option" in low:
            score += 35
            reasons.append("all/every option processing")
        if "fragment" in low and ("header" in low or "chain" in low):
            score += 45
            reasons.append("fragment extension/header-chain behavior")
        if "random" in low and "delay" in low:
            score += 30
            reasons.append("timer randomization/delay behavior")
        if "multicast" in low and "listener" in low:
            score += 15
            reasons.append("multicast listener behavior")
    # Deduplicate while preserving order.
    seen: set[str] = set()
    uniq = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            uniq.append(reason)
    return score, uniq[:8]


def prioritize_protocol_focus(protocol_focus: list[dict], candidates: list[dict]) -> list[dict]:
    counts = candidate_counts_by_rfc(candidates)
    ranked: list[tuple[int, str, dict]] = []
    for focus in protocol_focus:
        score, reasons = protocol_focus_priority(focus, counts.get(focus.get("rfc", ""), 0))
        enriched = dict(focus)
        enriched["priority_score"] = score
        enriched["priority_reasons"] = reasons
        ranked.append((score, str(focus.get("rfc", "")), enriched))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [focus for _, _, focus in ranked]


def search_recipes(candidate: dict, code_root: Path, design_root: Path) -> list[dict]:
    de = candidate.get("design_evidence", {})
    text = " ".join([
        candidate.get("title", ""),
        candidate.get("inconsistency", ""),
        de.get("quote", ""),
        " ".join(c.get("symbol", "") for c in candidate.get("code_evidence", [])),
    ])
    terms = terms_from_text(text, 10)
    if not terms:
        terms = ["TODO", "unsupported", "required"]
    pattern = "|".join(re.escape(t) for t in terms[:6])
    quoted_pattern = shlex.quote(pattern)
    recipes = [
        {
            "purpose": "Find implementation paths or adjacent code for the design behavior.",
            "command": f"rg -n --hidden {quoted_pattern} {shlex.quote(str(code_root))}",
        },
        {
            "purpose": "Find the same behavior in design documents, including non-RFC specs.",
            "command": f"rg -n --hidden {quoted_pattern} {shlex.quote(str(design_root))}",
        },
    ]
    for ce in candidate.get("code_evidence", [])[:3]:
        if ce.get("symbol"):
            recipes.append({
                "purpose": f"Find callers or alternate definitions of {ce['symbol']}.",
                "command": f"rg -n '\\b{re.escape(ce['symbol'])}\\s*\\(' {shlex.quote(str(code_root))}",
            })
    return recipes


def candidate_priority(candidate: dict) -> tuple:
    level_order = {
        "MUST": 0,
        "MUST NOT": 0,
        "SHALL": 0,
        "SHALL NOT": 0,
        "REQUIRED": 0,
        "SHOULD": 1,
        "SHOULD NOT": 1,
        "RECOMMENDED": 1,
        "MAY": 2,
        "OPTIONAL": 2,
    }
    has_code = 0 if candidate.get("code_evidence") else 1
    return (
        level_order.get(candidate.get("normative_level", "MAY"), 3),
        has_code,
        candidate.get("candidate_id", ""),
    )


def review_contract() -> dict:
    return {
        "verdict_file": "queue.verdict_output",
        "required_status_values": ["confirmed", "probable", "rejected"],
        "confirmed_requires": [
            "design_evidence.quote cites the design/spec requirement",
            "code_evidence has at least one concrete file, line range, and snippet",
            "inconsistency explains the semantic contradiction or missing behavior",
            "false_positive_controls records at least one reverse check",
            "generalization_rationale explains why the issue is not project-name hardcoding",
        ],
        "jsonl_record_shape": {
            "candidate_id": "candidate id from queue, or AGENT-DISCOVERED-<n> for a newly found issue",
            "status": "confirmed|probable|rejected",
            "confidence": "0.0-1.0",
            "title": "short issue title",
            "normative_level": "MUST|SHOULD|MAY|design-requirement|unknown",
            "design_evidence": {
                "rfc": "RFC id or design document id",
                "section": "section/heading if known",
                "doc_path": "path to source design doc",
                "quote": "short design quote",
            },
            "code_evidence": [
                {
                    "file": "repo-relative source file",
                    "line_start": 1,
                    "line_end": 2,
                    "symbol": "function/class/module if known",
                    "snippet": "source excerpt",
                }
            ],
            "inconsistency": "why design and implementation differ",
            "impact": "runtime/protocol/user-visible impact",
            "false_positive_controls": ["reverse checks performed"],
            "related_files": ["repo-relative files"],
            "agent_notes": "concise reasoning and tool trail",
            "generalization_rationale": "why this would be detectable in another project",
        },
    }


def semantic_review_checklist() -> list[dict]:
    """Reusable issue families for opencode investigation.

    These are not detector rules. They are prompts for semantic review that
    work across projects and design formats.
    """
    return [
        {
            "family": "bounded_collection_or_option_limit",
            "question": (
                "Does the implementation impose a numeric limit on options, "
                "headers, records, retries, peers, or entries where the design "
                "requires processing all valid items or specifies a different bound?"
            ),
            "evidence_to_seek": [
                "design statement about all/every/multiple entries or a required bound",
                "loop termination, counter check, fixed array length, or max constant in code",
                "reverse check that the limit is not merely allocation sizing or logging",
            ],
        },
        {
            "family": "incomplete_chain_or_tlv_walk",
            "question": (
                "Does code inspect only the first header/TLV/extension item when the "
                "design requires walking a chain or processing later items?"
            ),
            "evidence_to_seek": [
                "design text requiring next-header/TLV/option traversal",
                "code path that stops after the first item, returns early, or does not follow next pointers",
                "caller context showing the shortcut is on the live parse path",
            ],
        },
        {
            "family": "timer_randomization_or_delay_gap",
            "question": (
                "Does the design require a randomized delay, jitter, suppression, "
                "retransmission timer, or backoff that the implementation omits or makes deterministic?"
            ),
            "evidence_to_seek": [
                "design quote requiring randomization/delay/backoff",
                "code sending immediately, using zero delay, fixed timeout, or lacking random source",
                "reverse search for alternate delayed path or config option",
            ],
        },
        {
            "family": "optional_or_recommended_behavior_omitted",
            "question": (
                "Does the design document define a MAY/SHOULD behavior whose omission "
                "causes interoperability, compatibility, or functional difference?"
            ),
            "evidence_to_seek": [
                "design quote with MAY/SHOULD and behavior context",
                "code comment, absent branch, explicit TODO, unsupported feature note, or adjacent implementation path",
                "explanation of why omission is meaningful rather than harmless optional scope",
            ],
        },
        {
            "family": "protocol_or_feature_gap",
            "question": (
                "Does the design require a protocol capability or feature family that "
                "is absent from the target codebase?"
            ),
            "evidence_to_seek": [
                "design requirement or benchmark scope naming the feature",
                "global code search showing no implementation, plus adjacent code/comment/build evidence",
                "reverse check for aliases, generated code, third-party library delegation, or compile-time exclusion",
            ],
        },
        {
            "family": "packet_path_or_routing_mismatch",
            "question": (
                "Does classification, routing, bypass, offload, or forwarding send data "
                "to the wrong subsystem compared with the design?"
            ),
            "evidence_to_seek": [
                "design statement about which packets/events must be processed where",
                "code branch that classifies, forwards, drops, bypasses, or offloads them differently",
                "caller/path evidence showing the branch is reachable",
            ],
        },
        {
            "family": "missing_error_feedback_or_silent_drop",
            "question": (
                "Does code silently drop or ignore inputs where the design requires "
                "an error response, notification, log, state update, or retry?"
            ),
            "evidence_to_seek": [
                "design quote requiring feedback or state change",
                "drop/free/return path without the required feedback",
                "reverse search for feedback emitted by caller or callee",
            ],
        },
        {
            "family": "state_machine_or_lifecycle_mismatch",
            "question": (
                "Does implementation state transition, cleanup, expiry, locking, or "
                "lifecycle behavior differ from the design?"
            ),
            "evidence_to_seek": [
                "design state diagram, lifecycle rule, timeout, or transition condition",
                "code transition table/branch missing or contradicting that rule",
                "evidence that the state path is not test-only or dead code",
            ],
        },
    ]


def build_bundle(candidate: dict, requirements: list[dict], code_index: dict,
                 code_root: Path, design_root: Path, bundle_path: Path) -> dict:
    symbol_lookup = build_symbol_lookup(code_index)
    bundle = {
        "bundle_version": 1,
        "prepared_at": rc.now_iso(),
        "candidate_id": candidate.get("candidate_id"),
        "review_instruction": (
            "Do not trust detector labels. Read this bundle, then use shell tools "
            "such as rg/sed/nl against CODE_ROOT and DESIGN_ROOT for just-in-time "
            "context. Write the final semantic verdict to the queue.verdict_output "
            "JSONL file."
        ),
        "candidate": candidate,
        "code_contexts": code_contexts(candidate, code_root, symbol_lookup),
        "related_requirements": related_requirements(candidate, requirements),
        "search_recipes": search_recipes(candidate, code_root, design_root),
        "semantic_review_checklist": semantic_review_checklist(),
        "review_contract": review_contract(),
    }
    rc.save_json(bundle_path, bundle)
    return bundle


def build_protocol_focus_bundle(focus: dict, code_root: Path, design_root: Path,
                                bundle_path: Path) -> dict:
    bundle = {
        "bundle_version": 1,
        "item_type": "protocol_domain_review",
        "prepared_at": rc.now_iso(),
        "review_instruction": (
            "Start from the design requirements and protocol-domain code focus, "
            "not from detector labels. Use this bundle to search for confirmed "
            "design/code inconsistencies and write AGENT-DISCOVERED verdicts to "
            "queue.verdict_output when evidence is complete."
        ),
        "code_root": str(code_root),
        "design_root": str(design_root),
        "protocol_focus": focus,
        "semantic_review_checklist": semantic_review_checklist(),
        "review_contract": review_contract(),
        "recommended_workflow": [
            "Read notable_requirements and identify behaviors that need implementation evidence.",
            "Inspect code_path_contexts and configured_code_paths for the implementation path.",
            "Run suggested_commands and targeted rg searches for aliases and behavior terms.",
            "If a feature appears absent, run reverse checks for aliases, generated code, config flags, or delegated libraries.",
            "Confirm only with complete design_evidence, code_evidence, impact, false_positive_controls, and generalization_rationale.",
        ],
    }
    rc.save_json(bundle_path, bundle)
    return bundle


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Prepare opencode semantic review bundles.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    code_root = Path(args.code_root)
    design_root = Path(args.design_root)
    benchmark = Path(args.benchmark)
    work = rc.agent_work_dir(code_root)
    review_dir = rc.ensure_dir(work / "agent-review")
    trace_root = rc.ensure_dir(Path(args.log_root) / "trace")

    candidates = load_json_if_exists(work / "candidate_issues.json", {}).get("candidates", [])
    requirements = load_json_if_exists(work / "rfc_requirements.json", {}).get("requirements", [])
    code_index = load_json_if_exists(work / "code_index.json", {"files": []})
    protocol_focus = prioritize_protocol_focus(
        build_protocol_domain_focus(work, code_root, code_index),
        candidates,
    )

    limit = int(os.getenv("RFC_AGENT_REVIEW_LIMIT", str(MAX_REVIEW_CANDIDATES)))
    ordered = sorted(candidates, key=candidate_priority)[:limit]

    items: list[dict] = []
    domain_items: list[dict] = []
    for focus in protocol_focus:
        domain_id = f"DOMAIN-{focus.get('rfc', 'DESIGN')}-{focus.get('protocol_area') or rc.slugify(focus.get('title', 'domain'))}"
        safe_id = rc.slugify(domain_id, maxlen=80)
        rel_bundle = Path(".agent-work") / "agent-review" / f"{safe_id}.json"
        abs_bundle = review_dir / f"{safe_id}.json"
        build_protocol_focus_bundle(focus, code_root, design_root, abs_bundle)
        domain_items.append({
            "item_type": "protocol_domain_review",
            "review_id": domain_id,
            "rfc": focus.get("rfc", ""),
            "title": focus.get("title", ""),
            "protocol_area": focus.get("protocol_area", ""),
            "priority_score": focus.get("priority_score", 0),
            "priority_reasons": focus.get("priority_reasons", []),
            "bundle_path": str(rel_bundle),
            "bundle_abs_path": str(abs_bundle),
            "status": "pending_opencode_review",
        })
    items.extend(domain_items)
    for idx, candidate in enumerate(ordered, start=1):
        candidate_id = candidate.get("candidate_id") or f"CANDIDATE-{idx:04d}"
        safe_id = rc.slugify(candidate_id, maxlen=80)
        rel_bundle = Path(".agent-work") / "agent-review" / f"{safe_id}.json"
        abs_bundle = review_dir / f"{safe_id}.json"
        build_bundle(candidate, requirements, code_index, code_root, design_root, abs_bundle)
        items.append({
            "item_type": "candidate_review",
            "candidate_id": candidate_id,
            "requirement_id": candidate.get("requirement_id", ""),
            "rfc": rfc_from_candidate(candidate),
            "title": candidate.get("title", ""),
            "normative_level": candidate.get("normative_level", ""),
            "detection_type": candidate.get("detection_type", ""),
            "bundle_path": str(rel_bundle),
            "bundle_abs_path": str(abs_bundle),
            "status": "pending_opencode_review",
        })

    queue = {
        "prepared_at": rc.now_iso(),
        "review_required": True,
        "agent": "opencode",
        "code_root": str(code_root),
        "design_root": str(design_root),
        "benchmark": str(benchmark),
        "agent_work": str(work),
        "instruction_files": [
            "INSTRUCTION.md",
            "work/skills/rfc-implementation-diff-detection/SKILL.md",
            "work/agents/rfc-diff-orchestrator.md",
            "work/agents/rfc-evidence-reviewer.md",
        ],
        "verdict_output": str(work / "agent_review_verdicts.jsonl"),
        "review_contract": review_contract(),
        "semantic_review_checklist": semantic_review_checklist(),
        "protocol_domain_focus": protocol_focus,
        "global_investigation": {
            "purpose": (
                "The queued candidates are recall hints, not the full search space. "
                "If design documents describe behaviors not represented by candidates, "
                "opencode should investigate and may write AGENT-DISCOVERED verdicts "
                "with complete design/code evidence."
            ),
            "design_docs": design_doc_manifest(design_root, benchmark),
            "code_inventory": code_inventory_summary(code_index),
            "suggested_first_commands": [
                f"rg -n --hidden 'must|should|required|shall|MUST|SHOULD|REQUIRED|SHALL' {shlex.quote(str(design_root))}",
                f"rg -n --hidden 'TODO|not implemented|unsupported|FIXME|MUST|SHOULD' {shlex.quote(str(code_root))}",
            ],
        },
        "candidate_count": len(candidates),
        "candidate_queued_count": len(ordered),
        "protocol_domain_queued_count": len(domain_items),
        "queued_count": len(items),
        "items": items,
    }

    rc.save_json(work / "agent_review_queue.json", queue)
    rc.save_json(trace_root / "agent_review_queue_summary.json", {
        "prepared_at": queue["prepared_at"],
        "candidate_count": len(candidates),
        "candidate_queued_count": len(ordered),
        "protocol_domain_queued_count": len(domain_items),
        "queued_count": len(items),
        "review_required": True,
        "verdict_output": queue["verdict_output"],
    })
    print(f"[agent-review-bundles] queued_items={len(items)}; "
          f"verdicts required at {queue['verdict_output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
