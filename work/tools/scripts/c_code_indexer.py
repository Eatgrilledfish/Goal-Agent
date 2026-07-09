#!/usr/bin/env python3
"""Phase 3: index F-Stack C/C++/header source files.

Scans the code root for C-family sources and build files, extracts functions,
macros, enums, typedefs, configuration items and comments, and tags each file
with protocol topics derived from its path and content (section 7.2 / 9.5).

Outputs:
  .agent-work/code_index.json        (per-file index)
  .agent-work/code_symbols.jsonl     (one symbol record per line)
  .agent-work/code_topic_index.json  (topic -> files/symbols map)
  /logs/trace/code_index_stats.json  (coverage diagnostics)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import rfc_common as rc

DEFINE_RE = re.compile(r"^\s*#\s*define\s+(?P<name>[A-Za-z_]\w+)")
ENUM_RE = re.compile(r"^\s*enum\s+(?P<name>[A-Za-z_]\w+)")
TYPEDEF_RE = re.compile(r"^\s*typedef\s+.*?\b(?P<name>[A-Za-z_]\w+)\s*;")
PROTO_RE = re.compile(r"^(?P<ret>[A-Za-z_][\w\s\*]*?)\b(?P<name>[A-Za-z_]\w+)\s*\([^)]*\)\s*;")
FUNC_NAME_RE = re.compile(
    r"^(?P<ret>.+?)\b(?P<name>[A-Za-z_]\w*)\s*\(",
    re.DOTALL,
)

CONTROL_NAMES = {
    "if", "for", "while", "switch", "sizeof", "return", "case", "do", "else",
}
MAX_SIGNATURE_LINES = 20
KNOWN_FUNCTIONS = [
    "ip6_tryforward",
    "nd6_na_output",
    "nd6_ns_input",
    "nd6_na_input",
    "icmp6_input",
    "frag6_input",
]

TOPIC_KEYWORDS = {
    "ipv6": ["ipv6", "in6", "ip6", "inet6", "netinet6"],
    "neighbor_discovery": ["nd6", "neighbor", "nd_", "nd6_"],
    "icmpv6": ["icmp6", "icmpv6", "mld6", "mld"],
    "fragment": ["frag", "ip_frag", "fragment"],
    "dhcpv6": ["dhcp6", "dhcpv6", "duid"],
    "multicast": ["mld", "multicast", "mcast"],
    "socket": ["socket", "sockbuf", "in_pcb", "uipc"],
    "dpdk": ["dpdk", "rte_", "ff_dpdk", "kni"],
    "tcp": ["tcp_", "tcp_", "in_rmx"],
    "udp": ["udp_", "in_udp"],
}

PRIORITY_DIRS = [
    "freebsd/netinet6",
    "freebsd/netinet",
    "dpdk/lib",
    "lib",
    "include",
]


def file_topics(rel_path: str, content: str) -> list[str]:
    haystack = (rel_path + " " + content[:4000]).lower()
    found = []
    for topic, kws in TOPIC_KEYWORDS.items():
        if any(kw in haystack for kw in kws):
            found.append(topic)
    return found or ["general"]


def find_brace_end(lines: list[str], start: int) -> int:
    depth = 0
    started = False
    for i in range(start, len(lines)):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    return i + 1
    return start + 1


def is_probably_toplevel_start(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    if stripped.startswith(("/*", "*", "//")):
        return False
    # FreeBSD/KNF function definitions normally start at column 0. This keeps
    # statements inside function bodies from becoming candidate signatures.
    if line[:1].isspace():
        return False
    return True


def collect_function_header(lines: list[str], start_idx: int) -> tuple[str, int] | None:
    """Return (header_text_before_brace, brace_idx) for a function definition.

    ``start_idx`` is 0-based. The collector is deliberately conservative:
    prototypes/declarations win over definitions when ``;`` appears before a
    body, and collection is capped so malformed input cannot consume a file.
    """
    parts: list[str] = []
    paren_balance = 0
    for j in range(start_idx, min(len(lines), start_idx + MAX_SIGNATURE_LINES)):
        raw = lines[j]
        stripped = raw.strip()
        if not stripped:
            if not parts:
                continue
            return None
        if stripped.startswith("#"):
            return None

        parts.append(raw)
        if ";" in raw and "{" not in raw:
            return None

        paren_balance += raw.count("(") - raw.count(")")
        if "{" in raw:
            header = "\n".join(parts).split("{", 1)[0]
            return header, j
        if paren_balance < 0:
            return None
    return None


def parse_function_name(header: str) -> str | None:
    normalized = re.sub(r"/\*.*?\*/", " ", header, flags=re.DOTALL)
    normalized = re.sub(r"//.*", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if "(" not in normalized:
        return None
    m = FUNC_NAME_RE.match(normalized)
    if not m:
        return None
    name = m.group("name")
    ret = m.group("ret").strip()
    if not ret:
        return None
    if name in CONTROL_NAMES:
        return None
    if "=" in ret or "," in ret:
        return None
    return name


# Cap stored function-body snippets so code_index.json stays bounded even for
# huge repositories (F-Stack has ~13k files). 64 lines / 4 KiB is plenty for a
# detector to reason about a function's control flow.
SNIPPET_MAX_LINES = 64
SNIPPET_MAX_CHARS = 4096


def make_snippet(lines: list[str], line_start: int, line_end: int) -> str:
    """Return the function-body source for [line_start, line_end], bounded."""
    s = max(0, (line_start or 1) - 1)
    e = max(s + 1, line_end or (s + 1))
    chunk = lines[s:e]
    if len(chunk) > SNIPPET_MAX_LINES:
        chunk = chunk[:SNIPPET_MAX_LINES]
    text = "\n".join(chunk)
    if len(text) > SNIPPET_MAX_CHARS:
        text = text[:SNIPPET_MAX_CHARS]
    return text


def index_file(path: Path, code_root: Path) -> dict:
    rel = str(path.relative_to(code_root))
    try:
        text = rc.read_text(path)
    except OSError:
        return {"file": rel, "language": "c", "symbols": [], "macros": [], "topics": []}
    lines = text.splitlines()
    symbols: list[dict] = []
    macros: list[dict] = []
    enums: list[dict] = []
    typedefs: list[dict] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        line_no = i + 1
        m = DEFINE_RE.match(line)
        if m:
            macros.append({"name": m.group("name"), "line": line_no})
            i += 1
            continue
        m = ENUM_RE.match(line)
        if m:
            enums.append({"name": m.group("name"), "line": line_no})
            i += 1
            continue
        m = TYPEDEF_RE.match(line)
        if m:
            typedefs.append({"name": m.group("name"), "line": line_no})
            i += 1
            continue
        m = PROTO_RE.match(line)
        if m and "(" in line and line.rstrip().endswith(";"):
            symbols.append({"name": m.group("name"), "kind": "prototype",
                            "line_start": line_no, "line_end": line_no,
                            "snippet": lines[i].strip()})
            i += 1
            continue

        if is_probably_toplevel_start(line):
            collected = collect_function_header(lines, i)
            if collected:
                header, brace_idx = collected
                name = parse_function_name(header)
                if name:
                    end = find_brace_end(lines, brace_idx)
                    if end > brace_idx + 1:
                        signature = re.sub(r"\s+", " ", header).strip()
                        symbols.append({
                            "name": name,
                            "kind": "function",
                            "line_start": line_no,
                            "line_end": end,
                            "snippet": make_snippet(lines, line_no, end),
                            "signature": signature,
                        })
                        i = end
                        continue

        i += 1

    topics = file_topics(rel, text)
    return {
        "file": rel,
        "language": "c" if path.suffix in (".c", ".h") else "cpp",
        "symbols": symbols,
        "macros": macros,
        "enums": enums,
        "typedefs": typedefs,
        "topics": topics,
    }


def symbol_count(files: list[dict]) -> int:
    return sum(len(f.get("symbols", [])) for f in files)


def zero_symbol_file_count(files: list[dict]) -> int:
    return sum(1 for f in files if not f.get("symbols"))


def priority_dir_symbol_counts(files: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for prefix in ("freebsd/netinet6", "freebsd/netinet", "dpdk/lib"):
        counts[prefix] = sum(
            len(f.get("symbols", []))
            for f in files
            if f.get("file", "") == prefix or f.get("file", "").startswith(prefix + "/")
        )
    return counts


def known_function_presence(files: list[dict]) -> dict[str, bool]:
    names = {sym.get("name") for f in files for sym in f.get("symbols", [])}
    return {name: name in names for name in KNOWN_FUNCTIONS}


def build_code_index_stats(files_index: list[dict], previous_index: dict | None = None) -> dict:
    previous_files = previous_index.get("files", []) if previous_index else []
    file_count = len(files_index)
    zero_after = zero_symbol_file_count(files_index)
    return {
        "generated_at": rc.now_iso(),
        "file_count": file_count,
        "file_count_before": len(previous_files) if previous_index else None,
        "symbol_count_before": symbol_count(previous_files) if previous_index else None,
        "symbol_count_after": symbol_count(files_index),
        "zero_symbol_files_before": zero_symbol_file_count(previous_files) if previous_index else None,
        "zero_symbol_files_after": zero_after,
        "zero_symbol_file_ratio_after": round(zero_after / file_count, 4) if file_count else 0.0,
        "priority_dir_symbol_counts": priority_dir_symbol_counts(files_index),
        "known_function_presence": known_function_presence(files_index),
    }


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Index F-Stack C/C++ source files.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    code_root = Path(args.code_root)
    work = rc.agent_work_dir(code_root)
    index_path = work / "code_index.json"
    previous_index = rc.load_json(index_path) if index_path.exists() else None

    # Verify the code root and the expected protocol directories before
    # scanning. The F-Stack layout is code_root/freebsd/netinet6/nd6.c etc.;
    # a missing root means upstream passed the wrong --code-root, so we report
    # exactly what was checked instead of silently emitting an empty index.
    if not code_root.exists():
        top = list(code_root.parent.glob("*")) if code_root.parent.exists() else []
        siblings = [p.name for p in top if p.is_dir()]
        rc.save_json(index_path, {"indexed_at": rc.now_iso(),
                   "status": "blocked", "reason": "code root missing",
                   "code_root": str(code_root),
                   "siblings_of_code_root": siblings, "files": []})
        stats = build_code_index_stats([], previous_index)
        stats.update({"status": "blocked", "reason": "code root missing"})
        rc.save_json(Path(args.log_root) / "trace" / "code_index_stats.json", stats)
        print(f"[indexer] code root missing: {code_root}", file=sys.stderr)
        print(f"[indexer] siblings of code root: {siblings}", file=sys.stderr)
        return 0

    # ls-style verification of the directories we actually intend to walk.
    top_entries = sorted(p.name for p in code_root.iterdir()) if code_root.is_dir() else []
    print(f"[indexer] code_root OK: {code_root} ({len(top_entries)} top-level entries)")
    for sub in PRIORITY_DIRS:
        d = code_root / sub
        flag = "OK" if d.exists() else "absent"
        print(f"[indexer]   {flag}: {sub}")

    files_index: list[dict] = []
    symbols_lines: list[str] = []
    topic_index: dict[str, list[str]] = {}

    # Walk priority dirs first, then the rest, dedup by path.
    seen: set[str] = set()
    candidates: list[Path] = []
    for sub in PRIORITY_DIRS:
        d = code_root / sub
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file() and p.suffix in rc.C_SOURCE_SUFFIXES:
                    candidates.append(p)
    for p in code_root.rglob("*"):
        if p.is_file() and p.suffix in rc.C_SOURCE_SUFFIXES:
            candidates.append(p)

    for path in candidates:
        rel = str(path.relative_to(code_root))
        if rel in seen:
            continue
        seen.add(rel)
        record = index_file(path, code_root)
        files_index.append(record)
        for sym in record["symbols"]:
            sym_record = {"file": rel, **sym, "topics": record["topics"]}
            symbols_lines.append(json.dumps(sym_record, ensure_ascii=False))
        for topic in record["topics"]:
            topic_index.setdefault(topic, []).append(rel)

    rc.save_json(index_path, {
        "indexed_at": rc.now_iso(),
        "code_root": str(code_root),
        "file_count": len(files_index),
        "files": files_index,
    })
    (work / "code_symbols.jsonl").write_text("\n".join(symbols_lines) + ("\n" if symbols_lines else ""),
                                             encoding="utf-8")
    rc.save_json(work / "code_topic_index.json", {
        "indexed_at": rc.now_iso(),
        "topics": topic_index,
    })
    stats = build_code_index_stats(files_index, previous_index)
    rc.save_json(Path(args.log_root) / "trace" / "code_index_stats.json", stats)
    total_syms = sum(len(f["symbols"]) for f in files_index)
    print(f"[indexer] indexed {len(files_index)} files, {total_syms} symbols")
    print(f"[indexer] zero-symbol files: {stats['zero_symbol_files_after']} "
          f"({stats['zero_symbol_file_ratio_after']:.2%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
