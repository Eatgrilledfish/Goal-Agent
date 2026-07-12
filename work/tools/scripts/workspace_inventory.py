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
    "risk_sweep_plan": "risk_sweep_plan.json",
    "design_agent_manifest": "design_agent_manifest.json",
    "design_inventory": "design_inventory.json",
    "design_coverage": "design_coverage.json",
    "design_lookup_requests": "design_lookup_requests.jsonl",
    "claim_review_scope": "claim_review_scope.json",
    "design_claim_review": "design_claim_review.json",
    "semantic_coverage": "semantic_coverage.json",
    "design_claims": "design_claims.jsonl",
    "risk_observations": "risk_observations.jsonl",
    "rounds": "investigation_rounds.jsonl",
    "investigation_tasks": "investigation_tasks.jsonl",
    "investigation_findings": "investigation_findings.jsonl",
    "dynamic_probes": "dynamic_probes.jsonl",
    "critic_reviews": "critic_reviews.jsonl",
    "critic_review_history": "critic_review_history.jsonl",
    "verdicts": "agent_review_verdicts.jsonl",
    "coverage_audit": "coverage_audit.json",
    "coverage_supplement_history": "coverage_supplement_history.json",
    "validated_issues": "validated_issues.json",
    "ledger": "agent_run_ledger.jsonl",
    "state": "agent_loop_state.json",
    "approval_events": "approval_events.jsonl",
    "investigator_batch_gate": "investigator_batch_gate.json",
    "run_clock": "run_clock.json",
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
    binary_docs: list[Path] = []
    explicitly_rejected_binary: set[Path] = set()
    for value in entries:
        path = ac.contained_path(root, value)
        if not path or not path.is_file():
            problems.append(f"design entry is missing or outside design root: {value}")
            continue
        if path.suffix.lower() in ac.UNSUPPORTED_BINARY_DESIGN_SUFFIXES:
            problems.append(
                f"binary design document requires a text export with stable line provenance: {value}"
            )
            binary_docs.append(path)
            explicitly_rejected_binary.add(path)
            continue
        if path.suffix.lower() not in ac.DESIGN_SUFFIXES:
            problems.append(f"design entry has an unsupported text suffix: {value}")
            continue
        explicit.append(path)

    docs: list[Path] = []
    for path in ac.iter_files(root):
        if path.suffix.lower() in ac.UNSUPPORTED_BINARY_DESIGN_SUFFIXES:
            if path not in binary_docs:
                binary_docs.append(path)
            continue
        if path.suffix.lower() in ac.DESIGN_SUFFIXES:
            docs.append(path)
    if binary_docs and not docs and not explicit:
        problems.append(
            "design root contains only binary PDF/DOCX files; provide a UTF-8 text export "
            "with stable line provenance"
        )
    elif binary_docs:
        text_stems = {(path.parent, path.stem) for path in docs}
        for path in binary_docs:
            if path in explicitly_rejected_binary:
                continue
            if (path.parent, path.stem) not in text_stems:
                problems.append(
                    "binary design document lacks a same-stem UTF-8 text export with stable "
                    f"line provenance: {ac.relative_path(root, path)}"
                )
    ordered = explicit + [path for path in docs if path not in explicit]
    records: list[dict] = []
    groups: dict[str, list[str]] = {}
    for path in ordered:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            problems.append(
                f"design document is not valid UTF-8 text: {ac.relative_path(root, path)}"
            )
            continue
        if "\x00" in text:
            problems.append(
                f"design document contains binary NUL bytes: {ac.relative_path(root, path)}"
            )
            continue
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


def materialization_source_snapshot(
    source_manifest: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Freeze the supplied catalog/source tree used to build a design bundle."""
    if source_manifest is None:
        return None, []
    errors: list[str] = []
    source_root_value = str(source_manifest.get("source_root") or "")
    plan_path_value = str(source_manifest.get("plan_path") or "")
    plan_sha256_value = str(source_manifest.get("plan_sha256") or "")
    if not source_root_value:
        errors.append("design source manifest is missing source_root")
    if not plan_path_value:
        errors.append("design source manifest is missing plan_path")
    if not plan_sha256_value:
        errors.append("design source manifest is missing plan_sha256")
    if errors:
        return None, errors

    source_root = Path(source_root_value).resolve()
    plan_path = Path(plan_path_value).resolve()
    if not source_root.is_dir():
        errors.append(f"design materialization source root is missing: {source_root}")
    if not plan_path.is_file():
        errors.append(f"design source plan is missing: {plan_path}")
    elif plan_sha256_value != ac.sha256_file(plan_path):
        errors.append("design source manifest plan_sha256 does not match plan_path")
    if errors:
        return None, errors
    try:
        plan = ac.load_json(plan_path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"design source plan is invalid: {exc}"]
    if not isinstance(plan, dict):
        return None, ["design source plan must be an object"]
    catalog_path = str(plan.get("catalog_path") or "")
    catalog = ac.contained_path(source_root, catalog_path)
    if not catalog_path or catalog is None or not catalog.is_file():
        return None, ["design source plan catalog_path is missing or outside source_root"]

    files = [
        file_record(source_root, path, include_hash=True)
        for path in ac.iter_integrity_files(source_root)
    ]
    tree_sha256 = ac.stable_id(
        json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        length=64,
    )
    return {
        "source_root": str(source_root),
        "catalog_path": catalog_path,
        "plan_path": str(plan_path),
        "plan_sha256": plan_sha256_value,
        "file_count": len(files),
        "tree_sha256": tree_sha256,
        "files": files,
    }, []


def loop_contract(
    paths: dict[str, str], session_id: str,
    materialization_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_root = Path(paths["state_root"])
    artifacts = {name: str(state_root / filename) for name, filename in ARTIFACT_NAMES.items()}
    return {
        "contract_version": 17,
        "execution_model": "opencode-owned-model-driven-loop",
        "session": {
            "session_id": session_id,
            "resume": (
                "Read state, ledger, and current stable-ID artifacts. Replace a current revision only through "
                "validated handoff merge, and preserve prior provenance in ledger/trace; never append duplicate "
                "typed IDs or discard evidence silently."
            ),
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
                "id": "design_inventory",
                "owner": "spec-analyst",
                "output": artifacts["design_inventory"],
                "done_when": (
                    "Every manifest document group has an evidence-backed scope relation and a light section/behavior map; "
                    "each independent behavior family remains a design-origin frontier seed, while no full claim portfolio "
                    "or implementation verdict is produced."
                ),
            },
            {
                "id": "code_risk_backtracking",
                "owner": "risk-explorer",
                "output": [artifacts["risk_sweep_plan"], artifacts["risk_observations"]],
                "done_when": (
                    "A digest-bound plan covers every required architecture ID and every portfolio lens through focused, "
                    "non-overlapping primary code scopes of at most six implementation planes; explorers emit only concrete high-information semantic leads, "
                    "and the scheduler runs at most two mutually exclusive tasks at once while design inventory may occupy one slot."
                ),
            },
            {
                "id": "design_claim_resolution",
                "owner": "spec-analyst",
                "output": [
                    artifacts["design_lookup_requests"], artifacts["design_coverage"],
                    artifacts["design_claims"],
                ],
                "done_when": (
                    "Design-origin and code-origin evidence-pair frontier obligations are materialized as atomic claims from source_ref spans; "
                    "every required/in_scope design group has at least one claim, the reviewed portfolio is capped at 24, "
                    "and exact quotes/source hashes are derived deterministically."
                ),
            },
            {
                "id": "design_claim_review",
                "owner": "spec-critic",
                "output": [artifacts["claim_review_scope"], artifacts["design_claim_review"]],
                "done_when": (
                    "A fresh design-only critic accepted each executable claim for quote entailment, normative strength, "
                    "atomicity, and applicability using per-claim/source digests; unrelated group gaps remain expansion signals."
                ),
            },
            {
                "id": "investigation_planning",
                "owner": "orchestrator",
                "output": artifacts["investigation_tasks"],
                "done_when": (
                    "Claims are converted into evidence questions using vocabulary dynamically discovered from the current input; "
                    "the initial frontier starts from both design behavior seeds and code risk observations."
                ),
            },
            {
                "id": "investigation",
                "owner": "code-investigator",
                "output": artifacts["investigation_findings"],
                "done_when": "Each investigated claim has code evidence, reverse checks, and a real tool trace.",
            },
            {
                "id": "dynamic_probe",
                "owner": "code-investigator",
                "output": artifacts["dynamic_probes"],
                "done_when": (
                    "Selected low-cost candidate probes use a design-derived oracle and an independent control/reference "
                    "when feasible, entirely inside a session-owned isolated copy."
                ),
            },
            {
                "id": "critic_review",
                "owner": "evidence-critic",
                "output": artifacts["critic_reviews"],
                "done_when": (
                    "Every finding, including design_satisfied, is challenged promptly by one fresh-context critic whose structured "
                    "normative assessment permits confirmation only for an applicable binding/adopted obligation in direct conflict."
                ),
            },
            {
                "id": "coverage_audit",
                "owner": "coverage-critic",
                "output": [artifacts["semantic_coverage"], artifacts["coverage_audit"]],
                "done_when": (
                    "Every accepted claim has a complete finding/critic or structured defer, then one supplement decision accounts for every "
                    "applicable design section/behavior family plus architecture boundaries, parallel planes, modes, lenses, "
                    "unmapped risks, and critic evidence requests."
                ),
            },
            {
                "id": "final_judgement",
                "owner": "final-judge",
                "output": artifacts["verdicts"],
                "done_when": "The frontier and optional single supplement are drained and every finding has one evidence-bound final verdict.",
            },
        ],
        "handoffs": [
            {
                "from": "orchestrator", "to": "risk-explorer",
                "inputs": [artifacts["architecture_map"], artifacts["risk_sweep_plan"]],
                "read_roots": [paths["review_code_root"]],
            },
            {
                "from": "orchestrator", "to": "spec-analyst",
                "inputs": [
                    artifacts["design_agent_manifest"], artifacts["design_inventory"],
                    artifacts["design_lookup_requests"],
                ],
                "read_roots": [paths["review_design_root"]],
            },
            {
                "from": "spec-analyst", "to": "spec-critic",
                "inputs": [
                    artifacts["design_agent_manifest"], artifacts["design_coverage"],
                    artifacts["design_inventory"], artifacts["design_claims"],
                    artifacts["claim_review_scope"],
                ],
                "read_roots": [paths["review_design_root"]],
            },
            {
                "from": "orchestrator", "to": "code-investigator",
                "inputs": [
                    artifacts["architecture_map"], artifacts["design_claims"],
                    artifacts["risk_observations"], artifacts["investigation_tasks"],
                ],
                "read_roots": [paths["review_code_root"], paths["review_design_root"]],
            },
            {
                "from": "code-investigator", "to": "evidence-critic",
                "inputs": [
                    artifacts["design_claims"], artifacts["investigation_findings"],
                    artifacts["dynamic_probes"],
                ],
                "read_roots": [paths["review_code_root"], paths["review_design_root"]],
            },
            {
                "from": "evidence-critic", "to": "final-judge",
                "inputs": [
                    artifacts["design_claims"], artifacts["investigation_findings"],
                    artifacts["critic_reviews"], artifacts["dynamic_probes"],
                ],
                "read_roots": [paths["review_code_root"], paths["review_design_root"]],
            },
            {
                "from": "orchestrator", "to": "coverage-critic",
                "inputs": [
                    str(state_root / "workspace_manifest.json"),
                    str(state_root / "agent_loop_contract.json"),
                    artifacts["architecture_map"], artifacts["design_inventory"], artifacts["design_coverage"],
                    artifacts["design_claims"], artifacts["claim_review_scope"],
                    artifacts["risk_observations"],
                    artifacts["investigation_tasks"], artifacts["investigation_findings"],
                    artifacts["dynamic_probes"], artifacts["critic_reviews"],
                    artifacts["rounds"], artifacts["coverage_supplement_history"],
                ],
                "read_roots": [],
            },
            {
                "from": "final-judge", "to": "helper-validator",
                "inputs": [artifacts["verdicts"]], "read_roots": [],
            },
        ],
        "handoff_integrity": {
            "max_concurrent_subagent_tasks": 2,
            "risk_discovery_batch": (
                "After risk-plan-check, keep at most two tasks active. Start one design-inventory task and one risk sweep; "
                "as either completes, fill the free slot with the next disjoint risk slice or bounded design-resolution task. "
                "Every sweep owns a disjoint primary anchor scope and one isolated handoff; architecture IDs may repeat only when they have local paths in each scope. Observations are sparse semantic leads, "
                "not an exact restatement of every assigned ID or lens."
            ),
            "parallel_write_rule": "Each risk/investigator/probe/critic task writes one isolated JSON file under state/handoffs; never append to a shared JSONL from parallel tasks.",
            "merge_helper": str(Path(paths["state_root"]).parents[1] / "work" / "tools" / "scripts" / "handoff_merge.py"),
            "merge_semantics": "Syntax, artifact-shape, session, and stable-ID validation plus atomic replacement only; no semantic filtering or ranking.",
        },
        "guardrails": {
            "target_roots_read_only": list(dict.fromkeys(filter(None, [
                paths["code_root"], paths["design_root"],
                str((materialization_source or {}).get("source_root") or ""),
            ]))),
            "agent_read_roots": [paths["review_code_root"], paths["review_design_root"]],
            "source_path_rule": (
                "Model agents read/search only the session-local review roots and cite paths relative to them. "
                "Validators re-read the same relative paths under the original supplied roots."
            ),
            "allowed_writes": [paths["state_root"], paths["result_root"], paths["log_root"]],
            "forbidden": [
                "Use prewritten project names, known benchmark answers, fixed file paths, fixed symbols, regex hits, or scores as issue decisions; dynamically discovered current-input vocabulary is required evidence, not a forbidden shortcut.",
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
            "agent_event_contract": {
                "required_fields": [
                    "event", "role", "phase", "scope_id", "scope",
                    "input_artifacts", "input_sha256", "artifacts",
                    "artifact_snapshots", "artifact_sha256",
                    "started_at", "ended_at", "wall_time_seconds",
                    "provider_attempt", "provider_session_id", "output_count",
                    "repair_count", "outcome", "stop_reason",
                ],
                "validation_errors": "Aggregate repeated validator failures as ERROR_CODE=count.",
                "input_digest": (
                    "session_event.py must hash at least one real regular input file, "
                    "record each path/digest, and derive input_sha256; the model never supplies the digest."
                ),
                "output_digest": (
                    "session_event.py canonicalizes and hashes every declared output artifact; "
                    "retry progress cannot be created by changing free-form scope or outcome text."
                ),
                "required_phase_roles": [
                    {"phase": "architecture_mapping", "role": "orchestrator"},
                    {"phase": "design_inventory", "role": "spec-analyst"},
                    {"phase": "code_risk_backtracking", "role": "risk-explorer"},
                    {"phase": "design_claim_resolution", "role": "spec-analyst"},
                    {"phase": "design_claim_review", "role": "spec-critic"},
                    {"phase": "investigation_planning", "role": "orchestrator"},
                    {"phase": "investigation", "role": "code-investigator"},
                    {"phase": "critic_review", "role": "evidence-critic"},
                    {"phase": "coverage_audit", "role": "coverage-critic"},
                    {"phase": "final_judgement", "role": "final-judge"},
                ],
                "candidate_checkpoint_ids": {
                    "code_risk_backtracking/risk-explorer": "sweep_id",
                    "investigation/code-investigator": "task_id",
                    "dynamic_probe/code-investigator": "finding_id",
                    "critic_review/evidence-critic": "finding_id",
                },
                "portfolio_checkpoint_scope_ids": {
                    "architecture_mapping/orchestrator": "ARCHITECTURE-MAP",
                    "design_inventory/spec-analyst": "DESIGN-INVENTORY",
                    "design_claim_resolution/spec-analyst": "current ROUND-*",
                    "design_claim_review/spec-critic": "current ROUND-*",
                    "investigation_planning/orchestrator": "current ROUND-*",
                    "coverage_audit/coverage-critic": (
                        "COVERAGE-AUDIT-INITIAL or COVERAGE-AUDIT-FINAL; final is required"
                    ),
                    "final_judgement/final-judge": "FINAL-JUDGEMENT",
                },
            },
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
                "Every document group needs a source-grounded scope_relation and section map in design_inventory; every required/in_scope group contributes at least one atomic claim, with no more than 24 reviewed claims total.",
                "A catalog link proves provenance, not a product capability commitment. required/in_scope capability claims need positive supplied-design scope evidence.",
                "Informational/superseded/ambiguous scope relations need supplied-design evidence, not project reputation or missing code symbols.",
                "A superseded document's compatibility, migration, or legacy-mode behavior must map to a replacement obligation or remain an explicit ambiguity seed; it cannot silently disappear.",
            ],
            "architecture": [
                "Map the apparent core implementation and at least the repository's adapters/integration layer, configuration/capability surface, and alternate execution paths when present.",
                "Map every reachable owned, adapter, imported, generated, fast, and slow implementation plane that can realize the same supplied design behavior; do not assume the most obvious implementation is the only one.",
                "Every high-risk integration boundary must be investigated or deferred with a concrete evidence limitation.",
            ],
            "portfolio_lenses": PORTFOLIO_LENSES,
            "exploration_modes": EXPLORATION_MODES,
            "mode_rule": (
                "Use the exploration mode that best tests each evidence-pair hypothesis. Design-to-code traces obligations; "
                "code-to-design starts from risky execution boundaries and maps observations back to supplied claims; capability-absence "
                "reconciles designed capabilities with build, registration, entrypoint, configuration, and adjacent implementation evidence. "
                "Risk observations are not the sole frontier entry: when applicable design exists, the initial frontier must include a "
                "design-to-code or capability-absence task. Modes not exercised by the drained frontier are recorded as coverage gaps, not fabricated work."
            ),
            "semantic_coverage_artifact": artifacts["semantic_coverage"],
            "lens_rule": (
                "Every portfolio lens must be marked investigated, inapplicable, or gap_recorded with evidence. An investigated lens must reference "
                "real task IDs and finding IDs whose artifacts explicitly name that lens. Inapplicable requires referenced design groups "
                "and architecture boundaries plus a counterfactual explanation. gap_recorded names the missing evidence and may motivate the "
                "single coverage supplement. Listing a lens only in a round is insufficient."
            ),
            "claim_rule": (
                "Design inventory is the searchable breadth map; only on-demand claims with accepted per-claim reviews form the executable frontier. "
                "Start the initial frontier from both design-origin behavior seeds and code-origin risk observations, first diversifying across "
                "document groups, behavior families, execution planes, and modes. Every completed risk sweep with observations contributes at least one code-to-design task. Use a risk-diverse evidence-pair portfolio rather than materializing every sentence or treating every high label as mandatory work. A compliant finding is valid evidence but cannot be published. "
                "Optional/recommended behavior and completely absent capabilities remain eligible design claims; normative strength affects "
                "classification and severity. Every applicable inventory section/behavior family is investigated or explicitly recorded as a concrete gap; unmaterialized sections are gaps, not invalid claims."
            ),
            "boundary_rule": (
                "Code-only risk sweeps account for every high-risk integration boundary. Candidate investigation either links a completed "
                "task/finding or records the concrete unmapped boundary as a coverage gap; a gap does not invalidate an already confirmed candidate."
            ),
            "risk_backtracking_rule": (
                "Fresh code-only risk explorers inspect every assigned high-risk boundary and plane but emit only concrete, code-evidenced semantic leads; "
                "observations need not exactly restate every planned ID or lens. "
                "A code-to-design task must reference at least one validated risk observation sharing a boundary and plane; "
                "every completed sweep that produced observations must seed at least one such initial task. Writing the exploration-mode label alone is not evidence that the mode ran."
            ),
            "anti_shortcut": "Prior maturity, upstream origin, popularity, and a few compliant samples are not evidence that the supplied implementation is fully consistent.",
            "dynamic_probe": {
                "selection": (
                    "Triage every investigated finding, but execute only high-value, observable, low-cost probes supported by "
                    "the discovered repository environment. Do not require a probe for claims that are structural, absence-based, "
                    "non-deterministic, hardware-bound, or otherwise unsuitable."
                ),
                "oracle_independence": (
                    "The spec analyst defines preconditions, stimulus, and expected observation from design evidence before code mapping. "
                    "The investigator may map that oracle to an interface but must not rewrite it to match current behavior. "
                    "When feasible, a reference model, known-good path, or negative control must show the generated probe is non-trivial."
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
            "max_tasks_per_round": 4,
            "maximum_initial_frontier_rounds": 6,
            "maximum_coverage_supplement_rounds": 1,
            "initial_frontier_policy": (
                "Review every accepted claim before coverage, using at most six rounds of four tasks (24 accepted claims total). "
                "Include design-to-code and capability-absence work where applicable, and diversify document groups before deepening one domain."
            ),
            "coverage_supplement_history": (
                "prepare and coverage-check exclusively write the history artifact. Agents read it but never create, reset, or edit it; "
                "the first valid non-empty next-task snapshot is immutable and any different second request is rejected."
            ),
            "on_failed_gate": (
                "If time remains, start another frozen round only from a concrete uncovered design behavior, risk observation, "
                "architecture boundary, execution plane, or critic evidence request; never choose work from a count target."
            ),
            "zero_finding_response": (
                "Finding counts are not a coverage input. Critique completed candidates promptly, then run coverage after the initial frontier and allow at most one evidence-backed supplement."
            ),
            "minimum_strategy_change": (
                "A retry must change an exploration mode and at least one of document group, architecture boundary, or portfolio lens."
            ),
        },
        "timing": {
            "hard_limit_seconds": 21600,
            "review_stop_target_seconds": 19800,
            "first_confirmed_target_seconds": 5400,
            "policy": "Use progressive candidate validation and reserve 30-45 minutes for final judgement, report generation, and deterministic gate repair.",
        },
        "stop_conditions": {
            "success": [
                "Coverage audit explains remaining gaps after the initial frontier and at most one evidence-driven supplement.",
                "Every confirmed finding passed independent critique and source verification.",
                "The final gate passes within the time budget.",
            ],
            "never": "Do not use a candidate or issue count to select tasks, stop coverage, or lower evidence standards.",
            "failed_gate": "Repair only the earliest invalid candidate/artifact once; do not reopen a drained semantic portfolio merely to satisfy formatting or a count target.",
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
    materialization_source: dict[str, Any] | None = None
    source_problem_start = len(problems)
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
            materialization_source, source_snapshot_errors = materialization_source_snapshot(
                source_manifest
            )
            problems.extend(source_snapshot_errors)
    else:
        implicit_manifest_path = log_root / "trace" / "design_source_materialization.json"
        implicit_manifest: dict[str, Any] = {}
        if implicit_manifest_path.is_file() and not implicit_manifest_path.is_symlink():
            try:
                loaded_implicit = ac.load_json(implicit_manifest_path)
                implicit_manifest = loaded_implicit if isinstance(loaded_implicit, dict) else {}
            except (OSError, json.JSONDecodeError):
                implicit_manifest = {}
        materialized_below_state = False
        try:
            design_root.relative_to(state_root)
            materialized_below_state = design_root != state_root
        except ValueError:
            pass
        if materialized_below_state or (
            implicit_manifest.get("passed") is True
            and implicit_manifest.get("output_root") == str(design_root)
        ):
            problems.append(
                "materialized design root requires --source-manifest; refusing to omit original catalog/source provenance"
            )
    source_problems = problems[source_problem_start:]
    if source_problems:
        ac.save_json(
            log_root / "trace" / "preflight.json",
            {"ok": False, "problems": source_problems},
        )
        for problem in source_problems:
            print(f"[prepare] {problem}", file=sys.stderr)
        return 2
    problems.extend(entry_problems)
    if not design_docs:
        problems.append("no supported design documents found")
    code = code_manifest(code_root)
    if not code["file_count"]:
        problems.append("target code repository contains no files")

    prepared_at = ac.now_iso()
    run_clock_path = state_root / ARTIFACT_NAMES["run_clock"]
    run_clock: dict[str, Any] = {}
    if run_clock_path.is_symlink() or not run_clock_path.is_file():
        problems.append("run_clock.json is missing or not a regular file")
    else:
        try:
            loaded_clock = ac.load_json(run_clock_path)
            run_clock = loaded_clock if isinstance(loaded_clock, dict) else {}
            run_started_at = str(run_clock.get("started_at") or "")
            run_deadline_at = str(run_clock.get("deadline_at") or "")
            if (
                not run_started_at or not run_deadline_at
                or int((ac.parse_iso(run_deadline_at) - ac.parse_iso(run_started_at)).total_seconds())
                != 21600
            ):
                problems.append("run_clock.json does not contain a valid six-hour interval")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            problems.append(f"run_clock.json is invalid: {exc}")
    run_started_at = str(run_clock.get("started_at") or prepared_at)
    run_deadline_at = str(run_clock.get("deadline_at") or "")
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
        if (
            existing_state.get("started_at") != run_started_at
            or existing_state.get("deadline_at") != run_deadline_at
        ):
            problems.append("prepared session timing does not match immutable run_clock.json")

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
        previous_materialization_source = existing_manifest.get("design", {}).get(
            "materialization_source"
        )
        if isinstance(previous_materialization_source, dict):
            previous_source_root_value = str(
                previous_materialization_source.get("source_root") or ""
            )
            previous_source_root = (
                Path(os.path.abspath(previous_source_root_value))
                if previous_source_root_value else None
            )
            if previous_source_root is None or previous_source_root.is_symlink() \
                    or not previous_source_root.is_dir():
                problems.append("original design materialization source root is missing or a symlink")
            else:
                current_source_files = [
                    file_record(previous_source_root, path, include_hash=True)
                    for path in ac.iter_integrity_files(previous_source_root)
                ]
                problems.extend(record_integrity_errors(
                    previous_materialization_source.get("files", []),
                    current_source_files,
                    "original design materialization source",
                ))
            previous_plan_value = str(
                previous_materialization_source.get("plan_path") or ""
            )
            previous_plan = (
                Path(os.path.abspath(previous_plan_value))
                if previous_plan_value else None
            )
            if previous_plan is None or previous_plan.is_symlink() \
                    or not previous_plan.is_file():
                problems.append("design materialization plan is missing or a symlink")
            elif previous_materialization_source.get("plan_sha256") \
                    != ac.sha256_file(previous_plan):
                problems.append("design materialization plan changed since session prepare")
            if materialization_source is not None and (
                materialization_source.get("source_root")
                != previous_materialization_source.get("source_root")
                or materialization_source.get("catalog_path")
                != previous_materialization_source.get("catalog_path")
                or materialization_source.get("plan_path")
                != previous_materialization_source.get("plan_path")
                or materialization_source.get("plan_sha256")
                != previous_materialization_source.get("plan_sha256")
            ):
                problems.append("design materialization source changed since session prepare")
        elif materialization_source is not None:
            problems.append("design materialization source was not part of the prepared session")
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
            "materialization_source": materialization_source,
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
    effective_materialization_source = manifest.get("design", {}).get(
        "materialization_source"
    ) if isinstance(manifest, dict) else materialization_source
    contract = loop_contract(
        paths, session_id,
        effective_materialization_source
        if isinstance(effective_materialization_source, dict) else None,
    )
    state = existing_state if resumed else {
        "session_id": session_id,
        "started_at": run_started_at,
        "status": "ready",
        "current_phase": "architecture_mapping",
        "completed_phases": [],
        "metrics": {"claims": 0, "investigations": 0, "critic_reviews": 0, "confirmed": 0},
        "next_actions": [
            "Read INSTRUCTION.md, the skill, workspace_manifest.json, and agent_loop_contract.json.",
            "Map repository architecture and integration boundaries.",
            "After architecture-check, validate a focused multi-slice risk plan and schedule one design-inventory task alongside disjoint code-risk sweeps under the global concurrency limit of two.",
        ],
        "stop_reason": "",
    }
    started_at = str(state.get("started_at") or run_started_at)
    if not state.get("deadline_at"):
        state["deadline_at"] = run_deadline_at
    state["updated_at"] = prepared_at
    state["artifacts"] = contract["session"]["artifacts"]
    state.setdefault("metrics", {}).update({
        "design_documents": len(design_docs),
        "design_document_groups": len(document_groups),
        "code_files": code["file_count"],
    })

    if not resumed:
        ac.save_json(state_root / "workspace_manifest.json", manifest)
    design_only_manifest = {
        "session_id": session_id,
        "prepared_at": manifest.get("prepared_at", prepared_at),
        "review_design_root": paths["review_design_root"],
        "design": {
            key: manifest.get("design", {}).get(key)
            for key in (
                "document_count", "document_group_count", "documents",
                "document_groups", "source_manifest",
            )
        },
        "preflight_problems": list(manifest.get("preflight_problems", [])),
    }
    ac.save_json(state_root / ARTIFACT_NAMES["design_agent_manifest"], design_only_manifest)
    ac.save_json(state_root / "agent_loop_contract.json", contract)
    ac.save_json(state_root / ARTIFACT_NAMES["state"], state)
    supplement_history_path = state_root / ARTIFACT_NAMES["coverage_supplement_history"]
    if resumed and not supplement_history_path.is_file():
        problems.append(
            "resumed session is missing coverage_supplement_history.json; "
            "refusing to reset the one-supplement ledger"
        )
    elif not resumed:
        ac.save_json(supplement_history_path, {
            "session_id": session_id,
            "requests": [],
        })
    critic_history_path = state_root / ARTIFACT_NAMES["critic_review_history"]
    if resumed and not critic_history_path.is_file():
        problems.append(
            "resumed session is missing critic_review_history.jsonl; "
            "refusing to reset critic evidence history"
        )
    elif not resumed:
        critic_history_path.touch()
    for key in (
        "design_lookup_requests", "design_claims", "risk_observations", "rounds", "investigation_tasks", "investigation_findings", "dynamic_probes", "critic_reviews",
        "verdicts", "approval_events",
    ):
        path = state_root / ARTIFACT_NAMES[key]
        if not path.exists():
            path.touch()
    approval_path = state_root / ARTIFACT_NAMES["approval_events"]
    existing_approvals, _ = ac.load_jsonl(approval_path)
    existing_decisions = {
        (str(item.get("action") or ""), str(item.get("decision") or ""))
        for item in existing_approvals if item.get("session_id") == session_id
    }
    approval_baseline = [
            (
                "review_snapshot_read", str(review_root), "auto_approved",
                "Model agents may read/search only the session-local review copies.",
            ),
            (
                "session_artifact_write", str(state_root), "auto_approved",
                "The run may write machine artifacts only under its session result/log/state roots.",
            ),
            (
                "target_source_write", " | ".join(filter(None, [
                    str(code_root), str(design_root),
                    str((materialization_source or {}).get("source_root") or ""),
                ])), "denied",
                "The supplied code and design roots are immutable review inputs.",
            ),
            (
                "external_side_effect", "outside supplied inputs and session outputs",
                "external_approval_required",
                "Destructive, publishing, credential, dependency-install, and mutable external actions are out of scope.",
            ),
    ]
    for action, scope, decision, rationale in approval_baseline:
        if (action, decision) in existing_decisions:
            continue
        ac.append_jsonl(approval_path, {
            "recorded_at": prepared_at, "session_id": session_id,
            "actor": "prepare_policy", "action": action, "scope": scope,
            "decision": decision, "rationale": rationale,
        })
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
