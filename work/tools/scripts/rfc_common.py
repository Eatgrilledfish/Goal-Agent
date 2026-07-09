#!/usr/bin/env python3
"""Shared helpers for the RFC implementation-difference-detection pipeline.

These tools are part of the submitted Goal-Agent package and run against an
arbitrary F-Stack code repository and a design/RFC document root. They must not
assume Maven/Spring Boot or any Java tooling.

Inter-stage data flows through JSON files under ``.agent-work/`` relative to the
code root, as specified in section 7 of FIX-rfc-migration.md.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = SCRIPT_DIR.parent
WORK_DIR = TOOLS_DIR.parent
SUBMISSION_ROOT = WORK_DIR.parent
CONFIG_DIR = TOOLS_DIR / "config"

# RFC 2119 normative keywords (section 7.1 / 9.4).
RFC2119_LEVELS = [
    "MUST NOT",
    "SHALL NOT",
    "SHOULD NOT",
    "MUST",
    "SHALL",
    "SHOULD",
    "REQUIRED",
    "RECOMMENDED",
    "MAY",
    "OPTIONAL",
]

# Ordered longest-first so "MUST NOT" wins over "MUST".
RFC2119_PATTERN = re.compile(
    r"\b(MUST NOT|SHALL NOT|SHOULD NOT|MUST|SHALL|SHOULD|REQUIRED|RECOMMENDED|MAY|OPTIONAL)\b"
)

# Default fixed competition paths (section 6.1).
DEFAULT_ASSET_ROOT = (
    "/app/code/judge-assets/01_03_ai_implementation_design_difference_detection"
)


def now_iso() -> str:
    """Return a UTC timestamp string. Deterministic across the pipeline."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def agent_work_dir(code_root: Path) -> Path:
    """Workspace for inter-stage JSON, sibling to the code root's parent.

    The pipeline writes intermediate artifacts here; final results go to
    ``/result`` and ``/logs``.
    """
    return ensure_dir(code_root.parent / ".agent-work")


def result_root_default() -> Path:
    return Path("/result")


def log_root_default() -> Path:
    return Path("/logs")


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def load_config(name: str) -> Any:
    return load_json(CONFIG_DIR / name)


def read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def add_script_dir_to_path() -> None:
    """Allow sibling-module imports when invoked as a plain script."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)


def slugify(text: str, maxlen: int = 60) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return slug[:maxlen] or "issue"


def normative_level_of(text: str) -> str | None:
    """Return the strongest RFC 2119 level found in ``text``."""
    for level in RFC2119_PATTERN.findall(text):
        return level
    return None


def resolve_supersession(rfc: str, supersession: dict[str, list[str]]) -> str:
    """If ``rfc`` is obsoleted by a newer RFC present in the map, return the newer one."""
    for newer, older in supersession.items():
        if rfc in older:
            return newer
    return rfc


# Path components that are too generic to anchor an RFC to a code file on
# their own (every F-Stack tree has these). Excluded when deriving domain
# tokens so that, e.g., DHCPv6's ``freebsd/lib/libc/net`` does not match the
# whole repository.
_GENERIC_PATH_TOKENS = {
    "freebsd", "lib", "libc", "net", "netinet", "netinet6", "sys", "dpdk",
    "include", "kern", "kernel", "conf", "dev", "fs", "cam", "geom", "crypto",
    "security", "compat", "contrib", "gnu", "tests", "tools", "netpfil",
    "amd64", "x86", "rpm", "app", "example",
}


def resolve_rfc_domain(rfc: str, domain_map: dict) -> dict | None:
    """Return the domain entry for ``rfc``, applying supersession.

    Falls back to the literal RFC key if no supersession resolves. Returns
    ``None`` when the RFC is absent from the domain map -- callers must treat
    that as "cannot anchor code evidence to a protocol domain".
    """
    if not rfc:
        return None
    supersession = domain_map.get("supersession", {}) if domain_map else {}
    domains = domain_map.get("domains", {}) if domain_map else {}
    resolved = resolve_supersession(rfc, supersession)
    return domains.get(resolved) or domains.get(rfc)


def domain_path_tokens(domain: dict) -> set[str]:
    """Specific path tokens a domain cares about (e.g. ``nd6`` from ``nd6.c``).

    File-like code_paths contribute their filename stem; directory-like
    code_paths contribute their terminal component only when it is specific
    (not a generic name like ``lib`` or ``net``). This stops a bare ``lib/``
    code_path from anchoring every file under ``lib/`` (which is how DHCPv6
    once matched ``ff_memory.c``).
    """
    tokens: set[str] = set()
    for cp in domain.get("code_paths", []):
        base = cp.rstrip("/").split("/")[-1].lower()
        if not base:
            continue
        if "." in base:
            stem = base.rsplit(".", 1)[0]
            if stem and stem not in _GENERIC_PATH_TOKENS:
                tokens.add(stem)
        elif base not in _GENERIC_PATH_TOKENS:
            tokens.add(base)
    return tokens


def file_matches_domain(file_rel: str, domain: dict) -> bool:
    """True if ``file_rel`` plausibly belongs to ``domain``.

    Token-based on purpose: a file matches when it equals one of the domain's
    file code_paths, or when one of its path-component stems equals a domain
    path token (e.g. ``nd6`` from ``nd6.c``). Generic directory prefixes such
    as ``lib/`` are deliberately NOT trusted as anchors -- the indexer's
    content-derived topics are too loose (they once tagged ``tcp_ecn.c`` as
    ``neighbor_discovery``), and so are bare top-level directory prefixes.
    """
    if not file_rel or not domain:
        return False
    fl = file_rel.lower()
    # Exact file match against file-like code_paths.
    for cp in domain.get("code_paths", []):
        cpl = cp.lower().rstrip("/")
        if "." in cpl and fl == cpl:
            return True
    tokens = domain_path_tokens(domain)
    if not tokens:
        # Domain only has generic directory paths -> cannot anchor specifically.
        return False
    for comp in fl.split("/"):
        stem = comp.rsplit(".", 1)[0] if "." in comp else comp
        if stem in tokens:
            return True
    return False


C_SOURCE_SUFFIXES = (".c", ".h", ".cc", ".cpp", ".hpp", ".cxx", ".hxx")
BUILD_FILES = ("Makefile", "makefile", "GNUmakefile", "meson.build", "CMakeLists.txt")
