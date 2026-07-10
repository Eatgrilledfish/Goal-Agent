#!/usr/bin/env python3
"""Create a semantic-neutral workspace manifest and resumable agent session.

This helper inventories paths and writes the loop contract. It deliberately
does not extract requirements, map domains, rank code, or detect issues.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import agent_common as ac


ARTIFACT_NAMES = {
    "architecture_map": "architecture_map.json",
    "design_coverage": "design_coverage.json",
    "semantic_coverage": "semantic_coverage.json",
    "design_claims": "design_claims.jsonl",
    "rounds": "investigation_rounds.jsonl",
    "investigation_tasks": "investigation_tasks.jsonl",
    "investigation_findings": "investigation_findings.jsonl",
    "dynamic_probes": "dynamic_probes.jsonl",
    "critic_reviews": "critic_reviews.jsonl",
    "verdicts": "agent_review_verdicts.jsonl",
    "coverage_audit": "coverage_audit.json",
    "validated_issues": "validated_issues.json",
    "ledger": "agent_run_ledger.jsonl",
    "state": "agent_loop_state.json",
    "approval_events": "approval_events.jsonl",
    "investigator_batch_gate": "investigator_batch_gate.json",
}

PORTFOLIO_LENSES = [
    "collection completeness and hidden fixed bounds",
    "timing, delay, retry, and unsolicited behavior",
    "optional, recommended, and conditional behavior",
    "declared capability versus registration, build, and total absence",
    "chains, nested records, and repeated element traversal",
    "routing or ownership changes across subsystem boundaries",
    "parallel implementations, imported code, and fast/slow path parity",
    "error handling, state transitions, invariants, and configuration-dependent behavior",
]

EXPLORATION_MODES = [
    "design-to-code obligation tracing",
    "code-to-design risk backtracking",
    "capability-absence reconciliation",
]


def file_record(root: Path, path: Path, include_hash: bool = False) -> dict[str, Any]:
    stat = path.lstat()
    record = {
        "path": ac.relative_path(root, path),
        "suffix": path.suffix.lower() or "(none)",
        "bytes": stat.st_size,
        "kind": "symlink" if path.is_symlink() else "file",
    }
    if path.is_symlink():
        record["link_target"] = str(path.readlink())
    if include_hash and path.is_file() and not path.is_symlink():
        record["sha256"] = ac.sha256_file(path)
    return record


def design_manifest(root: Path, entries: list[str]) -> tuple[list[dict], list[dict], list[str]]:
    problems: list[str] = []
    explicit: list[Path] = []
    for value in entries:
        path = ac.contained_path(root, value)
        if not path or not path.is_file():
            problems.append(f"design entry is missing or outside design root: {value}")
            continue
        explicit.append(path)

    docs: list[Path] = []
    for path in ac.iter_files(root):
        if path.suffix.lower() in ac.DESIGN_SUFFIXES:
            docs.append(path)
    ordered = explicit + [path for path in docs if path not in explicit]
    records: list[dict] = []
    groups: dict[str, list[str]] = {}
    for path in ordered:
        record = file_record(root, path, include_hash=True) | {"explicit_entry": path in explicit}
        document_key = str(Path(record["path"]).with_suffix("")).lower()
        record["document_key"] = document_key
        records.append(record)
        groups.setdefault(document_key, []).append(record["path"])
    group_records = [
        {
            "document_key": key,
            "members": members,
            "explicit_entry": any(record["explicit_entry"] for record in records if record["document_key"] == key),
        }
        for key, members in groups.items()
    ]
    return records, group_records, problems


def code_manifest(root: Path) -> dict[str, Any]:
    suffixes: Counter[str] = Counter()
    top_level: Counter[str] = Counter()
    files: list[dict] = []
    for path in ac.iter_files(root):
        record = file_record(root, path, include_hash=True)
        files.append(record)
        suffixes[record["suffix"]] += 1
        first = Path(record["path"]).parts[0] if Path(record["path"]).parts else "."
        top_level[first] += 1
    return {
        "file_count": len(files),
        "suffix_counts": dict(suffixes.most_common()),
        "top_level_counts": dict(top_level.most_common()),
        "files": files,
    }


def _review_copy_ignore(directory: str, names: list[str]) -> set[str]:
    return {
        name for name in names
        if name in {".git", ".hg", ".svn"}
        or (
            (Path(directory) / name).is_dir()
            and (name in ac.DEFAULT_IGNORED_DIRS or name.startswith(".cache"))
        )
    }


def _iter_symlinks(root: Path):
    ignored = ac.DEFAULT_IGNORED_DIRS
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            name for name in dirs
            if name not in ignored and not name.startswith(".cache")
        )
        base = Path(current)
        for name in [*dirs, *(name for name in sorted(files) if name not in {".git", ".hg", ".svn"})]:
            path = base / name
            if path.is_symlink():
                yield path


def _review_destination_errors(destination: Path, state_root: Path) -> list[str]:
    return ac.lexical_path_errors(state_root, destination, "review snapshot")


def materialize_review_tree(
    source: Path,
    destination: Path,
    *,
    state_root: Path,
    block_parent_git: bool = False,
) -> list[str]:
    """Copy one supplied root into session state without interpreting its content."""
    source = source.resolve()
    destination = destination.absolute()
    destination_errors = _review_destination_errors(destination, state_root)
    if destination_errors:
        return destination_errors
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        return [f"review snapshot destination is inside its source root: {destination}"]
    try:
        source.relative_to(destination)
    except ValueError:
        pass
    else:
        return [f"review source is inside its snapshot destination: {source}"]

    if destination.exists():
        shutil.rmtree(destination)
    ac.ensure_dir(destination.parent)
    shutil.copytree(source, destination, symlinks=True, ignore=_review_copy_ignore)

    problems: list[str] = []
    for source_link in _iter_symlinks(source):
        relative = source_link.absolute().relative_to(source)
        destination_link = destination / relative
        resolved_source_target = source_link.resolve(strict=False)
        try:
            target_relative = resolved_source_target.relative_to(source)
        except ValueError:
            problems.append(f"source symlink escapes supplied root: {relative}")
            continue

        # Rewrite absolute or non-canonical links so the copied tree never points
        # back through the original external workspace.
        copied_target = destination / target_relative
        safe_target = os.path.relpath(copied_target, destination_link.parent)
        if str(destination_link.readlink()) != safe_target:
            destination_link.unlink()
            destination_link.symlink_to(safe_target, target_is_directory=source_link.is_dir())
    if block_parent_git:
        # An invalid local gitdir marker makes Git stop here instead of discovering
        # the submission repository above the snapshot. VCS history is intentionally
        # excluded from the self-contained review input. iter_files ignores this
        # reserved metadata filename, so it is not source evidence.
        (destination / ".git").write_text(ac.REVIEW_GIT_BARRIER_CONTENT, encoding="utf-8")
    return problems


def review_copy_errors(source_records: list[dict], review_records: list[dict], label: str) -> list[str]:
    """Verify copied regular files byte-for-byte and preserve the entry topology."""
    source = {str(item.get("path")): item for item in source_records if item.get("path")}
    review = {str(item.get("path")): item for item in review_records if item.get("path")}
    errors: list[str] = []
    if set(source) != set(review):
        missing = sorted(set(source) - set(review))
        extra = sorted(set(review) - set(source))
        if missing:
            errors.append(f"{label} review snapshot is missing entries: {missing[:10]}")
        if extra:
            errors.append(f"{label} review snapshot has extra entries: {extra[:10]}")
    for relative in sorted(set(source) & set(review)):
        expected = source[relative]
        actual = review[relative]
        if expected.get("kind") != actual.get("kind"):
            errors.append(f"{label} review snapshot changed entry kind: {relative}")
        elif expected.get("kind") == "file" and expected.get("sha256") != actual.get("sha256"):
            errors.append(f"{label} review snapshot content mismatch: {relative}")
    return errors


def record_integrity_errors(expected_records: list[dict], current_records: list[dict], label: str) -> list[str]:
    """Compare an existing session baseline without allowing prepare to reset it."""
    expected = {str(item.get("path")): item for item in expected_records if item.get("path")}
    current = {str(item.get("path")): item for item in current_records if item.get("path")}
    errors: list[str] = []
    added = sorted(set(current) - set(expected))
    removed = sorted(set(expected) - set(current))
    if added:
        errors.append(f"{label} has entries added since session prepare: {added[:10]}")
    if removed:
        errors.append(f"{label} has entries removed since session prepare: {removed[:10]}")
    for relative in sorted(set(expected) & set(current)):
        before = expected[relative]
        now = current[relative]
        if before.get("kind") != now.get("kind"):
            errors.append(f"{label} entry kind changed since session prepare: {relative}")
        elif before.get("kind") == "symlink":
            if before.get("link_target") != now.get("link_target"):
                errors.append(f"{label} symlink changed since session prepare: {relative}")
        elif before.get("sha256") != now.get("sha256"):
            errors.append(f"{label} file changed since session prepare: {relative}")
    return errors


def loop_contract(paths: dict[str, str], session_id: str) -> dict[str, Any]:
    state_root = Path(paths["state_root"])
    artifacts = {name: str(state_root / filename) for name, filename in ARTIFACT_NAMES.items()}
    return {
        "contract_version": 7,
        "execution_model": "opencode-owned-model-driven-loop",
        "session": {
            "session_id": session_id,
            "resume": "Read state, ledger, and existing JSONL artifacts; append revisions and never discard prior evidence.",
            "artifacts": artifacts,
        },
        "objective": (
            "Find semantic inconsistencies between the supplied design documents and target implementation. "
            "The model chooses what to inspect next from evidence gathered during the run. This is a design/implementation "
            "review, not a vulnerability scan or security audit. Focused design-derived dynamic probes may strengthen or "
            "challenge a semantic finding, but probe outcomes never replace design and code evidence."
        ),
        "phases": [
            {
                "id": "architecture_mapping",
                "owner": "orchestrator",
                "output": artifacts["architecture_map"],
                "done_when": "Core implementation, adapters, fast/slow paths, configuration/capability surfaces, and integration boundaries are mapped from repository evidence.",
            },
            {
                "id": "design_analysis",
                "owner": "spec_analyst",
                "output": [artifacts["design_coverage"], artifacts["design_claims"]],
                "done_when": "Every manifest document group has an evidence-backed disposition and a bounded difference-oriented claim portfolio with exact source locations.",
            },
            {
                "id": "investigation_planning",
                "owner": "orchestrator",
                "output": artifacts["investigation_tasks"],
                "done_when": "Claims are converted into evidence questions without assuming project vocabulary.",
            },
            {
                "id": "code_investigation",
                "owner": "code_investigator",
                "output": artifacts["investigation_findings"],
                "done_when": "Each investigated claim has code evidence, reverse checks, and a real tool trace.",
            },
            {
                "id": "dynamic_probe",
                "owner": "orchestrator_then_code_investigator",
                "output": artifacts["dynamic_probes"],
                "done_when": (
                    "Every candidate finding records a probe selection disposition; selected low-cost probes use a "
                    "design-derived oracle and an isolated session copy, while unavailable environments remain inconclusive."
                ),
            },
            {
                "id": "adversarial_critique",
                "owner": "evidence_critic",
                "output": artifacts["critic_reviews"],
                "done_when": "A fresh-context critic has challenged scope, alternate paths, configuration, and evidence sufficiency.",
            },
            {
                "id": "final_judgement",
                "owner": "final_judge",
                "output": artifacts["verdicts"],
                "done_when": "Every final finding references a design claim, investigator finding, and critic decision.",
            },
            {
                "id": "coverage_audit",
                "owner": "coverage_critic_then_orchestrator",
                "output": [artifacts["semantic_coverage"], artifacts["coverage_audit"]],
                "done_when": "All exploration modes ran; lens, high-risk boundary, parallel-plane, and capability dispositions have referenced evidence or an explicit evidence limitation.",
            },
        ],
        "handoffs": [
            {"from": "orchestrator", "to": "spec_analyst", "artifact": artifacts["architecture_map"]},
            {"from": "spec_analyst", "to": "orchestrator", "artifact": artifacts["design_claims"]},
            {"from": "orchestrator", "to": "code_investigator", "artifact": artifacts["investigation_tasks"]},
            {"from": "code_investigator", "to": "evidence_critic", "artifact": artifacts["investigation_findings"]},
            {"from": "code_investigator", "to": "evidence_critic", "artifact": artifacts["dynamic_probes"]},
            {"from": "evidence_critic", "to": "final_judge", "artifact": artifacts["critic_reviews"]},
            {"from": "final_judge", "to": "helper_validator", "artifact": artifacts["verdicts"]},
        ],
        "handoff_integrity": {
            "max_concurrent_subagent_tasks": 2,
            "parallel_write_rule": "Each investigator/probe/critic task writes one isolated JSON file under state/handoffs; never append to a shared JSONL from parallel tasks.",
            "merge_helper": str(Path(paths["state_root"]).parents[1] / "work" / "tools" / "scripts" / "handoff_merge.py"),
            "merge_semantics": "Syntax, artifact-shape, session, and stable-ID validation plus atomic replacement only; no semantic filtering or ranking.",
        },
        "guardrails": {
            "target_roots_read_only": [paths["code_root"], paths["design_root"]],
            "agent_read_roots": [paths["review_code_root"], paths["review_design_root"]],
            "source_path_rule": (
                "Model agents read/search only the session-local review roots and cite paths relative to them. "
                "Validators re-read the same relative paths under the original supplied roots."
            ),
            "allowed_writes": [paths["state_root"], paths["result_root"], paths["log_root"]],
            "forbidden": [
                "Use project names, known benchmark answers, fixed file paths, fixed symbols, regex hits, or scores as issue decisions.",
                "Read work/tools/eval before final result generation.",
                "Confirm an issue without exact design evidence, exact code evidence, reverse checks, and critic approval.",
                "Treat a generated probe failure, build failure, missing dependency, or environment failure as a confirmed inconsistency by itself.",
                "Modify the target code repository or design documents.",
            ],
            "evidence_truth": "The validator re-reads cited files and rejects quotes or snippets that do not match source lines.",
            "source_integrity": "Prepare hashes every supplied design/code file and its session-local review copy; the final gate rejects changes to either the original target roots or the review snapshots.",
        },
        "approval_flows": {
            "auto_approved": [
                "Read and search the session-local review roots created by prepare.",
                "Use the deterministic prepare helper to inventory and copy supplied roots without semantic interpretation.",
                "Use search, navigation, build metadata, and source configuration inside the session-local review roots.",
                "Run a focused probe only in a session-owned isolated copy, using an oracle derived from the supplied design claim.",
                "Fetch a read-only design URL selected from the supplied catalog and cache it under session state.",
                "Write session artifacts under the allowed output roots.",
            ],
            "skip_without_external_approval": [
                "Source or design edits.",
                "Destructive commands, dependency installation/publication, network side effects, credential access, or unrelated long jobs.",
            ],
            "audit_artifact": artifacts["approval_events"],
            "record_when_considered": (
                "Append actor, action, scope, decision=auto_approved|denied|external_approval_required, rationale, and timestamp. "
                "Never reinterpret silence as approval for a skipped action."
            ),
        },
        "tool_protocol": {
            "principle": "Use tools for just-in-time retrieval; semantic decisions belong to the model, not search syntax.",
            "minimum_confirmed_trace": {
                "required": ["design_read", "code_read", "reverse_check"],
                "one_of": [["code_search", "code_navigation"]],
            },
            "trace_fields": ["seq", "kind", "tool", "target", "purpose", "result"],
        },
        "coverage_contract": {
            "design": [
                "Account for every document_key in workspace_manifest.design.document_groups.",
                "A supplied document is potentially applicable by default. Absence of matching symbols is not evidence of inapplicability; it may indicate a feature gap.",
                "Applicable document groups must reference one or more design claim IDs.",
                "Inapplicable/superseded/supporting dispositions need document or repository evidence, not project reputation.",
            ],
            "architecture": [
                "Map the apparent core implementation and at least the repository's adapters/integration layer, configuration/capability surface, and alternate execution paths when present.",
                "Map every reachable owned, adapter, imported, generated, fast, and slow implementation plane that can realize the same supplied design behavior; do not assume the most obvious implementation is the only one.",
                "Every high-risk integration boundary must be investigated or deferred with a concrete evidence limitation.",
            ],
            "portfolio_lenses": PORTFOLIO_LENSES,
            "exploration_modes": EXPLORATION_MODES,
            "mode_rule": (
                "Before successful completion, rounds must collectively use every exploration mode. Design-to-code traces obligations; "
                "code-to-design starts from risky execution boundaries and maps observations back to supplied claims; capability-absence "
                "reconciles designed capabilities with build, registration, entrypoint, configuration, and adjacent implementation evidence."
            ),
            "semantic_coverage_artifact": artifacts["semantic_coverage"],
            "lens_rule": (
                "Every portfolio lens must be marked investigated or inapplicable with evidence. An investigated lens must reference "
                "real task IDs and finding IDs whose artifacts explicitly name that lens. Inapplicable requires referenced design groups "
                "and architecture boundaries plus a counterfactual explanation. Listing a lens only in a round is insufficient."
            ),
            "claim_rule": (
                "Use a risk-diverse portfolio rather than treating every extracted sentence as mandatory work. A compliant finding is valid coverage but cannot be published. "
                "Optional/recommended behavior and completely absent capabilities remain eligible design claims; normative strength affects "
                "classification and severity, not whether the behavior is inspected. Each behavior family declared for an applicable design "
                "group must be represented by at least one claim before that group is considered covered."
            ),
            "boundary_rule": "A high-risk integration boundary must be investigated, not merely deferred, for a successful gate.",
            "anti_shortcut": "Prior maturity, upstream origin, popularity, and a few compliant samples are not evidence that the supplied implementation is fully consistent.",
            "dynamic_probe": {
                "selection": (
                    "Triage every investigated finding, but execute only high-value, observable, low-cost probes supported by "
                    "the discovered repository environment. Do not require a probe for claims that are structural, absence-based, "
                    "non-deterministic, hardware-bound, or otherwise unsuitable."
                ),
                "oracle_independence": (
                    "The spec analyst defines preconditions, stimulus, and expected observation from design evidence before code mapping. "
                    "The investigator may map that oracle to an interface but must not rewrite it to match current behavior."
                ),
                "isolation": (
                    "Write harnesses and build outputs only below state/probes in a copied workspace. Never run a command that can write "
                    "under the original code or design roots. Reuse already available build/test entrypoints; do not install dependencies."
                ),
                "interpretation": (
                    "A probe can support, disconfirm, or remain inconclusive. Environment/baseline failure and unproven reachability are "
                    "always inconclusive. A failed probe alone cannot confirm an issue; a passing probe is counter-evidence, not proof of full consistency."
                ),
            },
        },
        "iteration_policy": {
            "round_artifact": artifacts["rounds"],
            "on_failed_gate": (
                "If time remains, do not answer the user or declare completion. Start another round using unreviewed document groups, "
                "unvisited architecture boundaries, and a materially different portfolio lens."
            ),
            "zero_finding_response": (
                "Treat zero confirmed findings as a recall warning. Run a coverage-critic pass and broaden to integration/capability boundaries "
                "before considering an evidence-limited stop."
            ),
            "minimum_strategy_change": (
                "A retry must change an exploration mode and at least one of document group, architecture boundary, or portfolio lens."
            ),
        },
        "timing": {
            "hard_limit_seconds": 21600,
            "review_stop_target_seconds": 19800,
            "policy": "Reserve time for validation, report generation, and one repair iteration.",
        },
        "stop_conditions": {
            "success": [
                "Coverage audit explains remaining gaps and completes the required lens, mode, boundary, and execution-plane portfolio.",
                "Every confirmed finding passed independent critique and source verification.",
                "At least four confirmed findings exist for the competition target.",
                "The final gate passes within the time budget.",
            ],
            "never": "Do not fabricate or lower evidence standards to reach the issue-count target.",
            "failed_gate": "A failed gate is an iteration signal, not a completed run, while the hard time limit has not been reached.",
        },
    }


def prepare(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    design_root = Path(args.design_root).resolve()
    result_root = Path(args.result_root).resolve()
    log_root = Path(args.log_root).resolve()
    state_root = ac.state_root(log_root, args.state_root)
    for path in (result_root, log_root / "trace", state_root):
        ac.ensure_dir(path)

    problems: list[str] = []
    if not code_root.is_dir():
        problems.append(f"code root is not a directory: {code_root}")
    if not design_root.is_dir():
        problems.append(f"design root is not a directory: {design_root}")
    if problems:
        ac.save_json(log_root / "trace" / "preflight.json", {"ok": False, "problems": problems})
        for problem in problems:
            print(f"[prepare] {problem}", file=sys.stderr)
        return 2

    design_docs, document_groups, entry_problems = design_manifest(design_root, args.design_entry)
    design_source_files = [file_record(design_root, path, include_hash=True) for path in ac.iter_files(design_root)]
    source_manifest: dict[str, Any] | None = None
    if args.source_manifest:
        source_manifest_path = Path(args.source_manifest).resolve()
        if not source_manifest_path.is_file():
            problems.append(f"design source manifest is missing: {source_manifest_path}")
        else:
            source_manifest = ac.load_json(source_manifest_path)
            if source_manifest.get("passed") is not True:
                problems.append("design source manifest did not pass")
            if source_manifest.get("output_root") != str(design_root):
                problems.append("design source manifest output_root does not match design_root")
    problems.extend(entry_problems)
    if not design_docs:
        problems.append("no supported design documents found")
    code = code_manifest(code_root)
    if not code["file_count"]:
        problems.append("target code repository contains no files")

    prepared_at = ac.now_iso()
    review_root = state_root / "review-inputs"
    review_code_root = review_root / "code"
    review_design_root = review_root / "design"
    paths = {
        "code_root": str(code_root),
        "design_root": str(design_root),
        "result_root": str(result_root),
        "log_root": str(log_root),
        "state_root": str(state_root),
        "review_code_root": str(review_code_root),
        "review_design_root": str(review_design_root),
    }
    existing_state_path = state_root / ARTIFACT_NAMES["state"]
    existing_manifest_path = state_root / "workspace_manifest.json"
    resumed = False
    existing_state: dict[str, Any] = {}
    existing_manifest: dict[str, Any] = {}
    if existing_state_path.exists() != existing_manifest_path.exists():
        problem = "state root has an incomplete prior session; choose a new --state-root"
        ac.save_json(log_root / "trace" / "preflight.json", {"ok": False, "problems": [problem]})
        print(f"[prepare] {problem}", file=sys.stderr)
        return 2
    if existing_state_path.exists():
        existing_state = ac.load_json(existing_state_path)
        existing_manifest = ac.load_json(existing_manifest_path)
        previous_paths = existing_manifest.get("paths", {})
        if any(previous_paths.get(name) != value for name, value in paths.items()):
            problem = "state root belongs to different prepared paths; choose a new --state-root"
            ac.save_json(log_root / "trace" / "preflight.json", {"ok": False, "problems": [problem]})
            print(f"[prepare] {problem}", file=sys.stderr)
            return 2
        session_id = str(existing_state.get("session_id") or "")
        resumed = bool(session_id) and session_id == str(existing_manifest.get("session_id") or "")
        if not resumed:
            problem = "state root has inconsistent session identifiers; choose a new --state-root"
            ac.save_json(log_root / "trace" / "preflight.json", {"ok": False, "problems": [problem]})
            print(f"[prepare] {problem}", file=sys.stderr)
            return 2

    review_code: dict[str, Any] = {"file_count": 0, "files": []}
    review_design_source_files: list[dict] = []
    if resumed:
        previous_review = existing_manifest.get("review_workspace", {})
        review_code = previous_review.get("code", {})
        review_design_source_files = previous_review.get("design_source_files", [])
        problems.extend(record_integrity_errors(
            existing_manifest.get("code", {}).get("files", []), code["files"], "original code root"
        ))
        problems.extend(record_integrity_errors(
            existing_manifest.get("design", {}).get("source_files", []),
            design_source_files,
            "original design root",
        ))
        review_path_errors = [
            *_review_destination_errors(review_code_root, state_root),
            *_review_destination_errors(review_design_root, state_root),
        ]
        problems.extend(review_path_errors)
        if review_path_errors or not review_code_root.is_dir() or not review_design_root.is_dir():
            problems.append("session-local review roots are missing")
        else:
            barrier_errors = ac.review_git_barrier_errors(review_code_root)
            problems.extend(barrier_errors)
            barrier_record = previous_review.get("git_isolation_barrier", {})
            if barrier_record.get("path") != ".git":
                problems.append("review code Git isolation barrier manifest is missing")
            elif not barrier_errors and barrier_record.get("sha256") != ac.sha256_file(review_code_root / ".git"):
                problems.append("review code Git isolation barrier hash changed")
            current_review_code = code_manifest(review_code_root)
            current_review_design = [
                file_record(review_design_root, path, include_hash=True)
                for path in ac.iter_files(review_design_root)
            ]
            problems.extend(record_integrity_errors(
                review_code.get("files", []), current_review_code["files"], "review code snapshot"
            ))
            problems.extend(record_integrity_errors(
                review_design_source_files, current_review_design, "review design snapshot"
            ))
        if existing_manifest.get("preflight_problems"):
            problems.append("prior session did not pass preflight")
        if problems:
            ac.save_json(log_root / "trace" / "preflight.json", {"ok": False, "problems": problems})
            for problem in problems:
                print(f"[prepare] {problem}", file=sys.stderr)
            return 2
    else:
        session_id = "session-" + ac.stable_id(str(code_root), str(design_root), prepared_at)
        try:
            materialization_errors = materialize_review_tree(
                code_root, review_code_root, state_root=state_root, block_parent_git=True
            )
            materialization_errors.extend(materialize_review_tree(
                design_root, review_design_root, state_root=state_root
            ))
            problems.extend(materialization_errors)
            if not materialization_errors:
                review_code = code_manifest(review_code_root)
                review_design_source_files = [
                    file_record(review_design_root, path, include_hash=True)
                    for path in ac.iter_files(review_design_root)
                ]
                problems.extend(review_copy_errors(code["files"], review_code["files"], "code"))
                problems.extend(review_copy_errors(design_source_files, review_design_source_files, "design"))
        except (OSError, shutil.Error) as exc:
            problems.append(f"could not materialize session-local review inputs: {exc}")

    git_barrier = {}
    if not resumed and (review_code_root / ".git").is_file() and not (review_code_root / ".git").is_symlink():
        git_barrier = {"path": ".git", "sha256": ac.sha256_file(review_code_root / ".git")}
    manifest = existing_manifest if resumed else {
        "prepared_at": prepared_at,
        "session_id": session_id,
        "paths": paths,
        "design": {
            "document_count": len(design_docs),
            "document_group_count": len(document_groups),
            "documents": design_docs,
            "document_groups": document_groups,
            "source_files": design_source_files,
            "source_manifest": source_manifest,
        },
        "code": code,
        "review_workspace": {
            "kind": "session_local_semantic_neutral_copy",
            "git_isolation_barrier": git_barrier,
            "code": review_code,
            "design_source_files": review_design_source_files,
        },
        "preflight_problems": problems,
        "semantic_analysis_performed": False,
    }
    contract = loop_contract(paths, session_id)
    state = existing_state if resumed else {
        "session_id": session_id,
        "started_at": prepared_at,
        "status": "ready",
        "current_phase": "architecture_mapping",
        "completed_phases": [],
        "metrics": {"claims": 0, "investigations": 0, "critic_reviews": 0, "confirmed": 0},
        "next_actions": [
            "Read INSTRUCTION.md, the skill, workspace_manifest.json, and agent_loop_contract.json.",
            "Map repository architecture and integration boundaries before extracting design claims.",
            "Account for every design document group, then start claim-driven investigation.",
        ],
        "stop_reason": "",
    }
    state["updated_at"] = prepared_at
    state["artifacts"] = contract["session"]["artifacts"]
    state.setdefault("metrics", {}).update({
        "design_documents": len(design_docs),
        "design_document_groups": len(document_groups),
        "code_files": code["file_count"],
    })

    if not resumed:
        ac.save_json(state_root / "workspace_manifest.json", manifest)
    ac.save_json(state_root / "agent_loop_contract.json", contract)
    ac.save_json(state_root / ARTIFACT_NAMES["state"], state)
    for key in (
        "design_claims", "rounds", "investigation_tasks", "investigation_findings", "dynamic_probes", "critic_reviews",
        "verdicts", "approval_events",
    ):
        path = state_root / ARTIFACT_NAMES[key]
        if not path.exists():
            path.touch()
    ac.append_jsonl(state_root / ARTIFACT_NAMES["ledger"], {
        "recorded_at": prepared_at,
        "session_id": session_id,
        "event": "session_resumed" if resumed else "session_prepared",
        "actor": "helper",
        "phase": "bootstrap",
        "status": "complete" if not problems else "warning",
        "summary": (
            "Refreshed the workspace inventory and resumed the existing model-driven session."
            if resumed else
            "Created a semantic-neutral workspace inventory and model-driven loop contract."
        ),
        "metrics": {"design_documents": len(design_docs), "code_files": code["file_count"]},
        "problems": problems,
        "resumed": resumed,
    })
    ac.save_json(log_root / "trace" / "session_prepared.json", {
        "session_id": session_id,
        "prepared_at": prepared_at,
        "state_root": str(state_root),
        "manifest": str(state_root / "workspace_manifest.json"),
        "contract": str(state_root / "agent_loop_contract.json"),
        "problems": problems,
    })
    print(json.dumps({
        "session_id": session_id,
        "state_root": str(state_root),
        "design_documents": len(design_docs),
        "code_files": code["file_count"],
        "ready": not problems,
        "resumed": resumed,
    }, ensure_ascii=False))
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a generic opencode review session.")
    ac.add_common_arguments(parser)
    return prepare(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
