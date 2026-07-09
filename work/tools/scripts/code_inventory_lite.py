#!/usr/bin/env python3
"""Phase 2 (lite): build a lightweight code inventory of the F-Stack tree.

Unlike ``c_code_indexer.py`` (which does full C parsing into symbols/snippets),
this script only records, per source file:

  * relative path, parent directory, filename stem
  * path/keyword-derived protocol topics
  * a small set of keywords hit in the first chunk of the file (priority dirs only)

It builds two lookup tables the scope planner uses to judge whether an RFC can
be *located* in code -- without a full parse:

  * ``stem_index``   : filename stem -> [relative paths]   (e.g. ``nd6`` -> nd6.c)
  * ``topic_index``  : topic -> [relative paths]
  * ``dir_index``    : directory -> [relative paths]

Outputs ``.agent-work/code_inventory_lite.json``. The full indexer
(``c_code_indexer.py``) still runs later in the pipeline for mapping/detection;
this lite pass exists only to feed the scope-planning stage that precedes
requirement extraction (FIX-rfc-scope-planner.md, new Phase 2).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import rfc_common as rc

# Path/keyword -> topic tags. Deliberately coarse; only enough for the planner
# to tell "is there code for neighbor discovery / fragmentation / icmpv6 ...".
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "ipv6": ["ipv6", "in6", "ip6", "inet6", "netinet6"],
    "neighbor_discovery": ["nd6", "neighbor", "nd_", "nd6_", "icmp6"],
    "icmpv6": ["icmp6", "icmpv6", "icmp6_"],
    "fragment": ["frag", "frag6", "ip_frag", "fragment"],
    "multicast": ["mld", "mld6", "multicast", "mcast"],
    "dhcpv6": ["dhcp6", "dhcpv6", "duid"],
    "addressing": ["in6", "in6_var", "in6_ifattach", "addr"],
    "socket": ["socket", "sockbuf", "in_pcb", "uipc", "getaddrinfo"],
    "flowlabel": ["flow", "flowlabel", "ip6_flow"],
    "dpdk": ["dpdk", "rte_", "ff_dpdk", "kni"],
}

PRIORITY_DIRS = [
    "freebsd/netinet6",
    "freebsd/netinet",
    "dpdk/lib",
    "lib",
    "include",
]

# Read only the first chunk of each priority-dir file for keyword tagging, so
# the lite pass stays fast even on the full F-Stack tree (~13k files).
CONTENT_PEEK_CHARS = 2048
# Hard cap on total files recorded, to bound memory on huge repos.
MAX_FILES = 8000


def path_topics(rel_path: str) -> list[str]:
    haystack = rel_path.lower()
    found = []
    for topic, kws in TOPIC_KEYWORDS.items():
        if any(kw in haystack for kw in kws):
            found.append(topic)
    return found


def content_keywords(text: str) -> list[str]:
    """Return distinctive tokens hit in ``text`` (subset of TOPIC_KEYWORDS)."""
    low = text.lower()
    hit = []
    for topic, kws in TOPIC_KEYWORDS.items():
        if any(kw in low for kw in kws):
            hit.append(topic)
    return hit


def stem_of(rel_path: str) -> str:
    base = rel_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return base.rsplit(".", 1)[0].lower() if "." in base else base.lower()


def parent_dir(rel_path: str) -> str:
    return "/".join(rel_path.split("/")[:-1])


def collect_files(code_root: Path) -> list[Path]:
    """Priority dirs first, then the rest, dedup by relative path."""
    seen: set[str] = set()
    ordered: list[Path] = []
    for sub in PRIORITY_DIRS:
        d = code_root / sub
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file() and p.suffix in rc.C_SOURCE_SUFFIXES:
                    rel = str(p.relative_to(code_root))
                    if rel not in seen:
                        seen.add(rel)
                        ordered.append(p)
    for p in code_root.rglob("*"):
        if p.is_file() and p.suffix in rc.C_SOURCE_SUFFIXES:
            rel = str(p.relative_to(code_root))
            if rel not in seen:
                seen.add(rel)
                ordered.append(p)
    return ordered


def main(argv: list[str] | None = None) -> int:
    rc.add_script_dir_to_path()
    parser = argparse.ArgumentParser(description="Lightweight F-Stack code inventory.")
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--design-root", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--result-root", default="/result")
    parser.add_argument("--log-root", default="/logs")
    args = parser.parse_args(argv)

    code_root = Path(args.code_root)
    work = rc.agent_work_dir(code_root)

    if not code_root.exists():
        rc.save_json(work / "code_inventory_lite.json", {
            "indexed_at": rc.now_iso(),
            "status": "blocked",
            "reason": "code root missing",
            "code_root": str(code_root),
            "file_count": 0,
            "files": [],
            "stem_index": {},
            "topic_index": {},
            "dir_index": {},
        })
        print(f"[code_inventory_lite] code root missing: {code_root}", file=sys.stderr)
        return 0

    priority_set = set(PRIORITY_DIRS)
    files: list[dict] = []
    stem_index: dict[str, list[str]] = {}
    topic_index: dict[str, list[str]] = {}
    dir_index: dict[str, list[str]] = {}

    candidates = collect_files(code_root)
    for path in candidates:
        if len(files) >= MAX_FILES:
            break
        rel = str(path.relative_to(code_root))
        d = parent_dir(rel)
        stem = stem_of(rel)
        topics = path_topics(rel)
        kw_hit: list[str] = []
        # Only peek content for files under priority dirs (the protocol stack),
        # to stay fast; path-derived topics are enough elsewhere.
        if any(rel.startswith(sub + "/") or d == sub for sub in priority_set):
            try:
                peek = rc.read_text(path)[:CONTENT_PEEK_CHARS]
            except OSError:
                peek = ""
            kw_hit = content_keywords(peek)
            for k in kw_hit:
                if k not in topics:
                    topics.append(k)

        files.append({
            "file": rel,
            "dir": d,
            "stem": stem,
            "topics": topics or ["general"],
            "keywords_hit": kw_hit,
        })
        stem_index.setdefault(stem, []).append(rel)
        for t in (topics or ["general"]):
            topic_index.setdefault(t, []).append(rel)
        dir_index.setdefault(d, []).append(rel)

    inventory = {
        "indexed_at": rc.now_iso(),
        "status": "ok",
        "code_root": str(code_root),
        "file_count": len(files),
        "capped": len(candidates) > MAX_FILES,
        "files": files,
        "stem_index": stem_index,
        "topic_index": topic_index,
        "dir_index": dir_index,
    }
    rc.save_json(work / "code_inventory_lite.json", inventory)
    print(f"[code_inventory_lite] {len(files)} files; "
          f"stems={len(stem_index)} topics={sorted(topic_index)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
