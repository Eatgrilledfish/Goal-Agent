#!/usr/bin/env python3
"""Phase 5: derive candidate implementation inconsistencies.

For each traced requirement it applies the detection-type heuristics in
``protocol_detection_patterns.json`` against the mapped code evidence. Every
candidate carries its RFC evidence and code evidence from the start
(section 9.7). The detector never reads a list of known issues; candidates are
derived purely from RFC requirement + code evidence.

Output: .agent-work/candidate_issues.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import rfc_common as rc


def load_code_index(work: Path) -> dict:
    path = work / "code_index.json"
    if path.exists():
        return rc.load_json(path)
    return {"files": []}


def read_snippet(code_root: Path, file_rel: str, line_start: int, line_end: int) -> str:
    p = code_root / file_rel
    if not p.exists():
        return ""
    lines = rc.read_text(p).splitlines()
    s = max(0, (line_start or 1) - 1)
    e = max(s + 1, line_end or (s + 1))
    return "\n".join(lines[s:e])


def code_signals_hit(snippet: str, code_signals: dict, base_line: int = 0) -> list[dict]:
    """Return concrete hits, one per matched pattern/macro.

    Each hit carries the absolute 1-based file ``line`` and the matching
    ``line_text`` so candidates can cite a specific code line rather than a
    vague region. ``base_line`` is ``line_start - 1`` of the snippet, so a
    1-based offset into the snippet maps to the real file line.
    """
    hits: list[dict] = []
    lines = snippet.splitlines()
    for pat in code_signals.get("patterns", []):
        regex = pat.get("regex", "")
        if not regex:
            continue
        for idx, line in enumerate(lines, start=1):
            if re.search(regex, line):
                hits.append({
                    "reason": pat.get("reason", regex),
                    "line": base_line + idx,
                    "line_text": line.strip(),
                })
                break  # one concrete line per pattern is enough to cite
    macros = code_signals.get("macros", [])
    if macros:
        macro_re = re.compile(r"\b(" + "|".join(re.escape(m) for m in macros) + r")\w*")
        for idx, line in enumerate(lines, start=1):
            mm = macro_re.search(line)
            if mm:
                hits.append({
                    "reason": f"hardcoded-style macro/constant '{mm.group(1)}' referenced",
                    "line": base_line + idx,
                    "line_text": line.strip(),
                })
                break
    return hits


def rfc_signals_hit(req_text: str, rfc_signals: list[str]) -> bool:
    low = req_text.lower()
    return any(sig.lower() in low for sig in rfc_signals)


def detect_for_requirement(req: dict, trace: dict, code_index: dict,
                           code_root: Path, patterns_cfg: dict) -> list[dict]:
    """Derive candidates that each carry concrete RFC text + a concrete code line.

    No candidate is emitted without concrete evidence: unlinked requirements
    have no code location, and absence-only detection types (no patterns/macros)
    cannot point at a specific line. Both are skipped rather than templated.
    """
    candidates: list[dict] = []
    req_text = req.get("requirement_text", "")
    locations = trace.get("candidate_code_locations", [])

    # Unlinked requirements carry no code evidence -> no candidate.
    if trace.get("trace_status") == "unlinked" or not locations:
        return candidates

    for dtype, dcfg in patterns_cfg.get("detection_types", {}).items():
        rfc_signals = dcfg.get("rfc_signals", [])
        if not rfc_signals_hit(req_text, rfc_signals):
            continue
        code_signals = dcfg.get("code_signals", {})
        # Absence-only types have no patterns/macros -> cannot cite a code line.
        if not code_signals.get("patterns") and not code_signals.get("macros"):
            continue

        for loc in locations:
            line_start = loc.get("line_start", 0)
            # Prefer the snippet captured by the indexer (stored in the trace)
            # so detection does not depend on the source tree being mounted at
            # detect time; fall back to reading the file if absent.
            snippet = loc.get("snippet") or read_snippet(
                code_root, loc["file"], line_start, loc.get("line_end"))
            if not snippet:
                continue
            hits = code_signals_hit(snippet, code_signals, base_line=max(0, line_start - 1))
            if not hits:
                continue
            # Absence-oriented types (rfc_signals describe a capability/behavior
            # that may be missing) flag the mapped code for review rather than
            # asserting a concrete defect: a linked trace only proves a related
            # symbol exists, not that it implements the required behavior.
            review_note = ""
            if code_signals.get("absence"):
                review_note = (
                    "requirement linked but implementation behavior needs review"
                )
            candidates.append(make_candidate(req, dtype, dcfg, loc, hits, snippet,
                                             review_note=review_note))

    return candidates


# A hardcoded MAX/limit-style macro/constant referenced in a matched code line.
_CONST_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]*(?:MAX|LIMIT|ND|RTT)[A-Z0-9_]*)\b"
    r"(?:\s*=\s*([^\s,;)\]]+))?"
)


def _constant_from_line(line_text: str) -> str:
    """Extract a ``NAME=value`` (or bare ``NAME``) limit constant from a code line."""
    if not line_text:
        return ""
    m = _CONST_RE.search(line_text)
    if not m:
        return ""
    name, val = m.group(1), m.group(2)
    return f"{name}={val}" if val else name


def build_inconsistency(req: dict, dtype: str, quote_trim: str, loc: dict,
                        first_hit: dict) -> str:
    """Build a detection-type-specific inconsistency statement.

    The statement always (1) quotes the concrete RFC requirement text and
    (2) cites the concrete code line + symbol, then (3) adds a targeted
    description driven by ``dtype`` and the matched code evidence (the hit
    ``reason`` and matched ``line_text``), rather than a fixed template string.
    """
    rfc = req.get("rfc", "")
    section = req.get("section", "")
    level = req.get("normative_level", "MAY")
    file_rel = loc["file"]
    line = first_hit["line"]
    symbol = loc.get("symbol", "")
    hit_reason = first_hit.get("reason", "")
    line_text = first_hit.get("line_text", "")
    topic = req.get("topic") or "this requirement"
    anchor = f"{file_rel}:{line}"
    sym = f" ({symbol})" if symbol else ""
    head = f'{rfc} §{section} ({level}) requires: "{quote_trim}".'

    if dtype == "hardcoded_limit_mismatch":
        const = _constant_from_line(line_text)
        const_phrase = f"constant {const}" if const else "a hardcoded MAX/limit constant"
        tail = (f'RFC does not specify a fixed limit on {topic}, but code at '
                f'{anchor}{sym} uses {const_phrase} to cap processing '
                f'({hit_reason}).')
    elif dtype == "missing_required_behavior":
        tail = (f'RFC requires this behavior ({topic}), but the linked '
                f'implementation at {anchor}{sym} does not show evidence of '
                f'performing it ({hit_reason}).')
    elif dtype == "silent_drop_error_handling_mismatch":
        tail = (f'RFC requires feedback/ICMP when this condition occurs '
                f'({topic}), but code at {anchor}{sym} silently drops the '
                f'packet ({hit_reason}).')
    elif dtype == "wrong_control_flow":
        tail = (f'RFC describes a chain/traversal pattern ({topic}), but code '
                f'at {anchor}{sym} appears to handle only the first element '
                f'({hit_reason}).')
    elif dtype == "missing_feature_protocol_gap":
        tail = (f'RFC requires this protocol capability ({topic}), but the '
                f'mapped symbol at {anchor}{sym} only partially covers it '
                f'({hit_reason}).')
    elif dtype == "timer_delay_behavior_mismatch":
        tail = (f'RFC requires randomized delay/timer behavior ({topic}), but '
                f'code at {anchor}{sym} lacks it ({hit_reason}).')
    elif dtype == "packet_path_mismatch":
        tail = (f'RFC requires the packet to follow this path ({topic}), but '
                f'code at {anchor}{sym} deviates ({hit_reason}).')
    else:
        tail = (f'Code at {anchor}{sym} diverges from the RFC requirement '
                f'({hit_reason}).')
    return f'{head} {tail}'


def make_candidate(req: dict, dtype: str, dcfg: dict, loc: dict,
                   hits: list[dict], snippet: str, review_note: str = "") -> dict:
    rfc = req.get("rfc", "")
    section = req.get("section", "")
    level = req.get("normative_level", "MAY")
    quote = (req.get("requirement_text", "") or "").strip()
    quote_trim = quote[:220] + ("…" if len(quote) > 220 else "")
    first = hits[0]
    file_rel = loc["file"]
    symbol = loc.get("symbol", "")

    # The inconsistency text is built per candidate from the actual RFC quote
    # and the actual matched code line, with a detection-type-specific
    # description driven by the hit evidence -- never the static template
    # string in the patterns config (which produced identical descriptions).
    inconsistency = build_inconsistency(req, dtype, quote_trim, loc, first)

    code_evidence = [{
        "file": file_rel,
        "symbol": symbol,
        "line_start": loc.get("line_start", 0),
        "line_end": loc.get("line_end", 0),
        "snippet": snippet,
        "match_reasons": [h["reason"] for h in hits],
        "evidence_lines": [
            {"line": h["line"], "text": h["line_text"], "reason": h["reason"]}
            for h in hits
        ],
    }]
    design_evidence = {
        "rfc": rfc,
        "section": section,
        "doc_path": req.get("source_doc", ""),
        "quote": quote,
        "source_anchor": req.get("source_anchor", ""),
    }
    title = req.get("title", "") or req.get("topic", dtype)
    reason_bits = [f"detection_type={dtype}", f"code={file_rel}:{first['line']}"]
    reason_bits.extend(h["reason"] for h in hits)
    candidate = {
        "candidate_id": f"{req.get('requirement_id', 'REQ')}-{dtype}",
        "requirement_id": req.get("requirement_id"),
        "title": f"{title}: {dtype.replace('_', ' ')}",
        "detection_type": dtype,
        "normative_level": level,
        "design_evidence": design_evidence,
        "code_evidence": code_evidence,
        "trace_status": "linked",
        "inconsistency": inconsistency,
        "detection_reasons": reason_bits,
        "min_confidence": dcfg.get("min_confidence", 0.5),
        "protocol_area": req.get("protocol_area"),
        "topic": req.get("topic"),
    }
    if review_note:
        candidate["review_note"] = review_note
    return candidate


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Detect protocol inconsistencies.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    code_root = Path(args.code_root)
    work = rc.agent_work_dir(code_root)
    req_path = work / "rfc_requirements.json"
    trace_path = work / "rfc_code_trace.json"
    if not req_path.exists() or not trace_path.exists():
        print("[detector] missing upstream artifacts", file=sys.stderr)
        return 0

    reqs = {r["requirement_id"]: r for r in rc.load_json(req_path).get("requirements", [])}
    traces = {t["requirement_id"]: t for t in rc.load_json(trace_path).get("traces", [])}
    code_index = load_code_index(work)
    patterns_cfg = rc.load_config("protocol_detection_patterns.json")

    candidates: list[dict] = []
    for req_id, req in reqs.items():
        trace = traces.get(req_id, {"candidate_code_locations": [], "trace_status": "unlinked"})
        candidates.extend(detect_for_requirement(req, trace, code_index, code_root, patterns_cfg))

    rc.save_json(work / "candidate_issues.json", {
        "detected_at": rc.now_iso(),
        "candidate_count": len(candidates),
        "candidates": candidates,
    })
    print(f"[detector] produced {len(candidates)} candidate inconsistencies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
