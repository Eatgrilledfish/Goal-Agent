#!/usr/bin/env python3
"""Prepare small, isolated development replays of one agent-loop stage.

This tool is deliberately outside ``goal_runner`` and the submission entrypoint.
It never invokes an LLM.  Model-owned stages produce a frozen, machine-readable
prompt envelope; deterministic schema/gate stages may optionally execute the
existing helpers against the replay copy.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import agent_common as ac
import handoff_merge as hm
import handoff_template as ht
import risk_sweep_plan_validator as rpv


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
SHARED_SKILL = REPO_ROOT / "work" / "skill" / "SKILL.md"

STAGES = (
    "inventory", "claims", "claim-review", "risk", "plan", "investigator", "probe",
    "critic", "judge", "coverage", "gate",
)
ITEM_STAGES = {"risk", "investigator", "probe", "critic", "judge"}
LOCAL_STAGES = {"inventory", "claims", "claim-review", "plan", "coverage", "gate"}

ROLE_SKILLS = {
    "inventory": REPO_ROOT / "work" / "skills" / "spec-analyst.md",
    "claims": REPO_ROOT / "work" / "skills" / "spec-analyst.md",
    "claim-review": REPO_ROOT / "work" / "skills" / "spec-critic.md",
    "risk": REPO_ROOT / "work" / "skills" / "risk-explorer.md",
    "plan": REPO_ROOT / "work" / "skills" / "orchestrator.md",
    "investigator": REPO_ROOT / "work" / "skills" / "code-investigator.md",
    "probe": REPO_ROOT / "work" / "skills" / "code-investigator.md",
    "critic": REPO_ROOT / "work" / "skills" / "evidence-critic.md",
    "judge": REPO_ROOT / "work" / "skills" / "final-judge.md",
    "coverage": REPO_ROOT / "work" / "skills" / "coverage-critic.md",
}

PROMPTS = {
    "inventory": (
        "Re-run only the light design inventory for the supplied frozen session inputs. "
        "Map every document group to its source-grounded scope relation and sections, and write "
        "design_inventory.json only under the replay state root."
    ),
    "claims": (
        "Materialize only the on-demand design obligations requested by the supplied frontier "
        "signals. Preserve the light inventory as the breadth map; do not expand every section "
        "into claims. Write design_coverage.json and design_claims.jsonl only under the replay "
        "state root."
    ),
    "claim-review": (
        "Independently re-review only the frozen scoped design claims against the supplied design "
        "documents. Write design_claim_review.json only under the replay state root."
    ),
    "risk": (
        "Re-run only code-side semantic risk exploration from the frozen architecture map. "
        "Do not read design artifacts; write risk handoffs only under the replay state root."
    ),
    "plan": (
        "Re-run only task-plan construction from accepted, digest-bound claims and code-risk "
        "observations. Create one atomic claim branch and one hypothesis per task. Write the task "
        "plan artifacts only under the replay state root; do not mutate lifecycle state."
    ),
    "investigator": (
        "Re-run exactly the selected investigation task. Use the frozen task and its single design "
        "claim, inspect only the declared review roots, and write only the replay finding handoff."
    ),
    "probe": (
        "Re-run only the selected finding's focused dynamic probe in a replay-owned workspace. "
        "Use the frozen claim oracle and finding evidence, preserve the original review roots, "
        "and write only the nested replay probe handoff."
    ),
    "critic": (
        "Adversarially re-review exactly the selected finding using its frozen claim, finding, "
        "and optional probe evidence. Write only the replay critic handoff."
    ),
    "judge": (
        "Re-judge exactly the selected finding from the frozen claim, investigator handoff, critic "
        "handoff, and optional probe evidence. Do not introduce new source evidence."
    ),
    "coverage": (
        "Re-run only the coverage audit from the frozen architecture, claims, risks, tasks, "
        "findings, probes, early critics, scope, and rounds. Write semantic_coverage.json and "
        "coverage_audit.json only."
    ),
    "gate": (
        "Replay the existing deterministic validation and final gate against the isolated copied "
        "artifacts. This stage has no model-owned semantic action."
    ),
}

CORE_FILES = (
    "workspace_manifest.json",
    "agent_loop_contract.json",
    "agent_loop_state.json",
)

GATE_STATE_FILES = (
    "run_clock.json",
    "architecture_map.json",
    "risk_sweep_plan.json",
    "design_agent_manifest.json",
    "design_inventory.json",
    "design_lookup_requests.jsonl",
    "design_coverage.json",
    "claim_review_scope.json",
    "semantic_coverage.json",
    "design_claims.jsonl",
    "design_claim_review.json",
    "risk_observations.jsonl",
    "investigation_rounds.jsonl",
    "investigation_tasks.jsonl",
    "investigation_findings.jsonl",
    "dynamic_probes.jsonl",
    "critic_reviews.jsonl",
    "critic_review_history.jsonl",
    "agent_review_verdicts.jsonl",
    "coverage_audit.json",
    "coverage_supplement_history.json",
    "validated_issues.json",
    "probable_review_queue.json",
    "agent_run_ledger.jsonl",
    "approval_events.jsonl",
    "investigator_batch_gate.json",
    "design_source_plan.json",
)


class ReplayError(ValueError):
    """A replay cannot be prepared without making an unsafe assumption."""


def _json_digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _prepare_empty_root(
    source_state: Path, replay_root: Path, force: bool,
    protected_roots: list[Path] | None = None,
) -> None:
    source_state = source_state.resolve()
    replay_root = replay_root.resolve()
    if replay_root == Path(replay_root.anchor):
        raise ReplayError("replay root cannot be a filesystem root")
    if _is_relative_to(replay_root, source_state) or _is_relative_to(source_state, replay_root):
        raise ReplayError("replay root and source state must be separate trees")
    for protected in protected_roots or []:
        protected = protected.resolve()
        if replay_root == protected or _is_relative_to(replay_root, protected) or _is_relative_to(
            protected, replay_root,
        ):
            raise ReplayError(
                f"replay root must be disjoint from protected source/session root: {protected}"
            )
    if replay_root.exists() and any(replay_root.iterdir()):
        if not force:
            raise ReplayError("replay root is not empty; pass --force to replace it")
        shutil.rmtree(replay_root)
    replay_root.mkdir(parents=True, exist_ok=True)
    (replay_root / "state").mkdir()
    (replay_root / "logs" / "trace").mkdir(parents=True)
    (replay_root / "result").mkdir()
    (replay_root / "prompt-assets").mkdir()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    values, errors = ac.load_jsonl(path)
    if errors:
        raise ReplayError("; ".join(errors))
    return values


def _one(values: list[dict[str, Any]], key: str, identifier: str, label: str) -> dict[str, Any]:
    matches = [value for value in values if str(value.get(key) or "") == identifier]
    if len(matches) != 1:
        raise ReplayError(
            f"{label} selector {identifier!r} matched {len(matches)} records; expected exactly one"
        )
    return matches[0]


class ArtifactCopier:
    """Copy only explicitly selected artifacts and retain their provenance."""

    def __init__(self, source_state: Path, replay_root: Path) -> None:
        self.source_state = source_state.resolve()
        self.replay_root = replay_root.resolve()
        self.state_root = replay_root / "state"
        self.records: list[dict[str, Any]] = []
        self.copied: set[str] = set()

    def _record(
        self, source: Path, destination: Path, logical_name: str, selection: dict[str, Any] | None,
    ) -> None:
        self.records.append({
            "logical_name": logical_name,
            "source_path": str(source.resolve()),
            "source_sha256": ac.sha256_file(source),
            "replay_path": _relative(self.replay_root, destination),
            "replay_sha256": ac.sha256_file(destination),
            "selection": selection or {"mode": "whole_artifact"},
        })
        self.copied.add(logical_name)

    def copy_file(
        self,
        name: str,
        *,
        required: bool = True,
        destination: Path | None = None,
        transform: Callable[[Path, Path], None] | None = None,
        selection: dict[str, Any] | None = None,
        logical_name: str | None = None,
    ) -> Path | None:
        source = self.source_state / name
        if not source.is_file() or source.is_symlink():
            if required:
                raise ReplayError(f"missing regular source artifact: {source}")
            return None
        destination = destination or (self.state_root / name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if transform:
            transform(source, destination)
        else:
            shutil.copy2(source, destination)
        self._record(source, destination, logical_name or f"state/{name}", selection)
        return destination

    def copy_jsonl_selection(
        self,
        name: str,
        values: list[dict[str, Any]],
        *,
        key: str,
        identifiers: list[str],
        required: bool = True,
    ) -> Path | None:
        source = self.source_state / name
        if not source.is_file() or source.is_symlink():
            if required:
                raise ReplayError(f"missing regular source artifact: {source}")
            return None
        wanted = set(identifiers)
        selected = [value for value in values if str(value.get(key) or "") in wanted]
        if required and {str(value.get(key) or "") for value in selected} != wanted:
            raise ReplayError(f"{name} does not contain every requested {key}: {sorted(wanted)}")
        destination = self.state_root / name
        destination.write_text(
            "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in selected),
            encoding="utf-8",
        )
        self._record(source, destination, f"state/{name}", {
            "mode": "jsonl_filter", "key": key, "identifiers": sorted(wanted),
        })
        return destination


def _rewrite_manifest(
    source: Path, destination: Path, replay_root: Path, stage: str,
) -> None:
    value = ac.load_json(source)
    paths = value.setdefault("paths", {})
    paths["state_root"] = str((replay_root / "state").resolve())
    paths["log_root"] = str((replay_root / "logs").resolve())
    paths["result_root"] = str((replay_root / "result").resolve())
    if stage == "gate":
        paths["review_code_root"] = str(
            (replay_root / "state" / "review-inputs" / "code").resolve()
        )
        paths["review_design_root"] = str(
            (replay_root / "state" / "review-inputs" / "design").resolve()
        )
    ac.save_json(destination, value)


def _rewrite_contract(
    source: Path, destination: Path, replay_root: Path,
) -> None:
    value = ac.load_json(source)
    artifacts = value.get("session", {}).get("artifacts", {})
    if isinstance(artifacts, dict):
        for name, path in list(artifacts.items()):
            artifacts[name] = str((replay_root / "state" / Path(str(path)).name).resolve())
    guardrails = value.get("guardrails", {})
    if isinstance(guardrails, dict):
        guardrails["allowed_writes"] = [
            str((replay_root / "state").resolve()),
            str((replay_root / "result").resolve()),
            str((replay_root / "logs").resolve()),
        ]
    ac.save_json(destination, value)


def _rewrite_state(
    source: Path, destination: Path, replay_root: Path,
) -> None:
    value = ac.load_json(source)
    artifacts = value.get("artifacts", {})
    if isinstance(artifacts, dict):
        for name, path in list(artifacts.items()):
            artifacts[name] = str((replay_root / "state" / Path(str(path)).name).resolve())
    ac.save_json(destination, value)


def _sanitize_design_manifest(source: Path, destination: Path) -> None:
    value = ac.load_json(source)
    design = value.get("design", {}) if isinstance(value.get("design"), dict) else {}
    paths = value.get("paths", {}) if isinstance(value.get("paths"), dict) else {}
    ac.save_json(destination, {
        "session_id": value.get("session_id", ""),
        "prepared_at": value.get("prepared_at", ""),
        "review_design_root": paths.get("review_design_root", ""),
        "design": {
            key: design.get(key)
            for key in (
                "document_count", "document_group_count", "documents",
                "document_groups", "source_manifest",
            )
        },
        "preflight_problems": list(value.get("preflight_problems", [])),
    })


def _rewrite_ledger(
    source: Path, destination: Path, source_trace: Path, replay_trace: Path,
) -> None:
    values = _load_jsonl(source)
    old_prefix = str(source_trace.resolve())
    new_prefix = str(replay_trace.resolve())
    for value in values:
        report = value.get("report")
        if isinstance(report, str) and (report == old_prefix or report.startswith(old_prefix + "/")):
            value["report"] = new_prefix + report[len(old_prefix):]
    destination.write_text(
        "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
        encoding="utf-8",
    )


def _copy_prompt_assets(
    replay_root: Path, stage: str, prompt_file: Path | None,
) -> tuple[str, str, list[dict[str, str]], dict[str, str]]:
    role = ROLE_SKILLS.get(stage)
    skill_sources = [SHARED_SKILL] + ([role] if role else [])
    skill_records: list[dict[str, str]] = []
    for source in skill_sources:
        if not source or not source.is_file():
            raise ReplayError(f"missing replay skill: {source}")
        destination = replay_root / "prompt-assets" / source.name
        if destination.exists():
            destination = replay_root / "prompt-assets" / f"role-{source.name}"
        shutil.copy2(source, destination)
        skill_records.append({
            "path": _relative(replay_root, destination),
            "source_path": str(source.resolve()),
            "sha256": ac.sha256_file(destination),
        })

    # Stage contracts live in the copied role/shared skills.  The repository's
    # publication schema describes result/issues.json, not these stage outputs.
    schema_record = {"path": "", "draft": "", "sha256": "", "applies_to_stage": False}

    if prompt_file:
        prompt_file = prompt_file.resolve()
        if not prompt_file.is_file() or prompt_file.is_symlink():
            raise ReplayError(f"prompt file is not a regular file: {prompt_file}")
        prompt = prompt_file.read_text(encoding="utf-8", errors="strict")
    else:
        prompt = PROMPTS[stage]
    prompt_destination = replay_root / "prompt-assets" / "prompt.txt"
    prompt_destination.write_text(prompt.rstrip() + "\n", encoding="utf-8")
    prompt_digest = ac.sha256_file(prompt_destination)
    skill_digest = _json_digest([
        {"path": item["path"], "sha256": item["sha256"]} for item in skill_records
    ])
    return prompt, prompt_digest, skill_records, schema_record | {"skill_digest": skill_digest}


def _copy_named(copier: ArtifactCopier, names: list[tuple[str, bool]]) -> None:
    for name, required in names:
        if f"state/{name}" not in copier.copied:
            if name == "design_agent_manifest.json" and not (
                copier.source_state / name
            ).is_file():
                copier.copy_file(
                    "workspace_manifest.json", required=required,
                    destination=copier.state_root / name,
                    transform=_sanitize_design_manifest,
                    selection={"mode": "design_only_projection"},
                    logical_name=f"state/{name}",
                )
                continue
            copier.copy_file(name, required=required)


def _source_ref_draft(value: dict[str, Any]) -> dict[str, Any]:
    """Strip fields that the deterministic source materializer must derive."""
    draft = copy.deepcopy(value)
    source_ref = draft.get("source_ref")
    if isinstance(source_ref, dict):
        draft["source_ref"] = {
            key: source_ref[key]
            for key in ("path", "line_start", "line_end")
            if key in source_ref
        }
    for field in (
        "path", "line_start", "line_end", "section", "heading", "quote", "document",
    ):
        draft.pop(field, None)
    return draft


def _inventory_to_draft(source: Path, destination: Path) -> None:
    """Project a frozen materialized inventory back to the model-owned raw shape."""
    value = ac.load_json(source)
    draft = copy.deepcopy(value)
    groups = draft.get("document_groups")
    if not isinstance(groups, list):
        raise ReplayError("design_inventory.json document_groups must be an array")
    draft_groups: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            raise ReplayError(f"design_inventory.json document_groups[{index}] must be an object")
        raw_group = copy.deepcopy(group)
        evidence = raw_group.get("scope_evidence")
        if not isinstance(evidence, dict):
            raise ReplayError(
                f"design_inventory.json document_groups[{index}].scope_evidence must be an object"
            )
        raw_group["scope_evidence"] = _source_ref_draft(evidence)
        sections = raw_group.get("sections")
        if not isinstance(sections, list):
            raise ReplayError(
                f"design_inventory.json document_groups[{index}].sections must be an array"
            )
        if any(not isinstance(section, dict) for section in sections):
            raise ReplayError(
                f"design_inventory.json document_groups[{index}].sections entries must be objects"
            )
        raw_group["sections"] = [_source_ref_draft(section) for section in sections]
        raw_group.pop("group_sha256", None)
        draft_groups.append(raw_group)
    draft["document_groups"] = draft_groups
    ac.save_json(destination, draft)


def _claims_to_draft(source: Path, destination: Path) -> None:
    """Project frozen materialized claims back to source-ref-only raw drafts."""
    values, errors = ac.load_jsonl(source)
    if errors:
        raise ReplayError("; ".join(errors))
    destination.write_text(
        "".join(
            json.dumps(_source_ref_draft(value), ensure_ascii=False) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _copy_trace(copier: ArtifactCopier, name: str, *, required: bool) -> Path | None:
    """Copy a validation trace while preserving its item-level digest bindings."""
    workspace = ac.load_json(copier.source_state / "workspace_manifest.json")
    paths = workspace.get("paths", {}) if isinstance(workspace, dict) else {}
    log_root = paths.get("log_root") if isinstance(paths, dict) else None
    source_trace = (
        Path(str(log_root)).resolve() / "trace"
        if isinstance(log_root, str) and log_root
        else copier.source_state.parent / "trace"
    )
    source = source_trace / name
    if not source.is_file() or source.is_symlink():
        if required:
            raise ReplayError(f"missing regular source trace: {source}")
        return None
    destination = copier.replay_root / "logs" / "trace" / name
    shutil.copy2(source, destination)
    copier._record(source, destination, f"logs/trace/{name}", None)
    return destination


def _risk_ids(task: dict[str, Any]) -> list[str]:
    values = task.get("risk_observation_ids", task.get("risk_observation_id", []))
    if isinstance(values, str):
        values = [values]
    return sorted({str(value) for value in values if value}) if isinstance(values, list) else []


def _prepare_stage_inputs(
    copier: ArtifactCopier, stage: str, item_id: str | None, run_local: bool,
) -> dict[str, Any]:
    state = copier.source_state
    selection: dict[str, Any] = {}
    if stage == "inventory":
        _copy_named(copier, [
            ("design_agent_manifest.json", True), ("design_source_plan.json", False),
        ])
        if run_local:
            copier.copy_file(
                "design_inventory.json",
                destination=copier.state_root / "handoffs" / "design" / "inventory.raw.json",
                transform=_inventory_to_draft,
                selection={"mode": "raw_source_ref_projection"},
                logical_name="state/handoffs/design/inventory.raw.json",
            )
        return selection

    if stage == "claims":
        _copy_named(copier, [
            ("design_agent_manifest.json", True), ("design_source_plan.json", False),
            ("design_inventory.json", True), ("design_lookup_requests.jsonl", False),
        ])
        if run_local:
            _copy_named(copier, [("design_coverage.json", True)])
            copier.copy_file(
                "design_claims.jsonl",
                destination=copier.state_root / "handoffs" / "design" / "claims.raw.jsonl",
                transform=_claims_to_draft,
                selection={"mode": "raw_source_ref_projection"},
                logical_name="state/handoffs/design/claims.raw.jsonl",
            )
        return selection

    if stage == "claim-review":
        _copy_named(copier, [
            ("design_agent_manifest.json", True), ("design_inventory.json", True),
            ("design_coverage.json", True),
            ("design_claims.jsonl", True), ("claim_review_scope.json", True),
            ("design_claim_review.json", False),
        ])
        if run_local and not (copier.state_root / "design_claim_review.json").is_file():
            raise ReplayError("local claim-review replay needs an existing review artifact")
        return selection

    if stage == "risk":
        _copy_named(copier, [
            ("architecture_map.json", True), ("design_inventory.json", True),
            ("risk_sweep_plan.json", True),
        ])
        _plan, index, errors = rpv.load_validated_plan(copier.state_root)
        if errors:
            raise ReplayError(
                "risk replay requires a currently validated risk sweep plan: "
                + "; ".join(errors)
            )
        selected = index.get("slices", {}).get(item_id)
        if not isinstance(selected, dict):
            raise ReplayError(f"risk sweep selector {item_id!r} matched 0 validated slices")
        return dict(selected) | {
            "risk_sweep_plan_sha256": ac.sha256_file(
                copier.state_root / "risk_sweep_plan.json"
            ),
        }

    if stage == "plan":
        _copy_named(copier, [
            ("architecture_map.json", True), ("risk_sweep_plan.json", True),
            ("coverage_supplement_history.json", True),
            ("design_agent_manifest.json", True),
            ("design_inventory.json", True), ("design_lookup_requests.jsonl", False),
            ("design_coverage.json", True), ("design_claims.jsonl", True),
            ("claim_review_scope.json", True), ("design_claim_review.json", True),
            ("risk_observations.jsonl", False),
            ("coverage_audit.json", False), ("semantic_coverage.json", False),
        ])
        _copy_trace(copier, "claim_review_validation.json", required=True)
        if run_local:
            _copy_named(copier, [
                ("investigation_tasks.jsonl", True),
                ("investigation_rounds.jsonl", True),
            ])
        return selection

    if stage == "coverage":
        _copy_named(copier, [
            ("architecture_map.json", True), ("risk_sweep_plan.json", True),
            ("design_agent_manifest.json", True), ("design_inventory.json", True),
            ("design_lookup_requests.jsonl", False),
            ("design_coverage.json", True), ("design_claims.jsonl", True),
            ("claim_review_scope.json", True), ("design_claim_review.json", True),
            ("risk_observations.jsonl", True),
            ("investigation_tasks.jsonl", True),
            ("investigation_findings.jsonl", True),
            ("dynamic_probes.jsonl", True), ("critic_reviews.jsonl", True),
            ("critic_review_history.jsonl", True),
            ("agent_run_ledger.jsonl", True),
            ("investigation_rounds.jsonl", True),
            ("coverage_supplement_history.json", True),
        ])
        _copy_trace(copier, "claim_review_validation.json", required=True)
        if run_local:
            _copy_named(copier, [
                ("semantic_coverage.json", True), ("coverage_audit.json", True),
            ])
        return selection

    if stage == "gate":
        _copy_named(copier, [(name, False) for name in GATE_STATE_FILES])
        return selection

    if not item_id:
        raise ReplayError(f"--item-id is required for stage {stage}")

    tasks = _load_jsonl(state / "investigation_tasks.jsonl")
    findings = _load_jsonl(state / "investigation_findings.jsonl") if stage != "investigator" else []

    if stage == "investigator":
        task = _one(tasks, "task_id", item_id, "task")
        claim_id = str(task.get("claim_id") or "")
        if not claim_id:
            raise ReplayError(f"task {item_id!r} has no claim_id")
        claims = _load_jsonl(state / "design_claims.jsonl")
        claim = _one(claims, "claim_id", claim_id, "claim")
        copier.copy_jsonl_selection(
            "investigation_tasks.jsonl", tasks, key="task_id", identifiers=[item_id],
        )
        copier.copy_jsonl_selection(
            "design_claims.jsonl", claims, key="claim_id", identifiers=[claim_id],
        )
        _copy_named(copier, [
            ("architecture_map.json", True), ("risk_sweep_plan.json", True),
            ("design_coverage.json", False),
        ])
        risk_ids = _risk_ids(task)
        risk_path = state / "risk_observations.jsonl"
        if risk_ids and risk_path.is_file():
            risks = _load_jsonl(risk_path)
            copier.copy_jsonl_selection(
                "risk_observations.jsonl", risks, key="observation_id",
                identifiers=risk_ids, required=True,
            )
        template = state / "handoff-templates" / "investigators" / f"{item_id}.json"
        if template.is_file() and not template.is_symlink():
            destination = copier.state_root / "handoff-templates" / "investigators" / template.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template, destination)
            copier._record(
                template, destination,
                f"state/handoff-templates/investigators/{template.name}",
                {"mode": "item", "task_id": item_id},
            )
        else:
            destination = (
                copier.state_root / "handoff-templates" / "investigators" / f"{item_id}.json"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            generated = ht.finding_template(task, claim)
            ac.save_json(destination, generated)
            logical_name = f"state/handoff-templates/investigators/{item_id}.json"
            copier.records.append({
                "logical_name": logical_name,
                "source_path": "generated from frozen task and claim",
                "source_sha256": _json_digest({"task": task, "claim": claim}),
                "replay_path": _relative(copier.replay_root, destination),
                "replay_sha256": ac.sha256_file(destination),
                "selection": {"mode": "deterministic_template", "task_id": item_id},
            })
            copier.copied.add(logical_name)
        selection = {"task_id": item_id, "claim_id": claim_id, "risk_observation_ids": risk_ids}
        return selection

    finding = _one(findings, "finding_id", item_id, "finding")
    task_id = str(finding.get("task_id") or "")
    claim_id = str(finding.get("claim_id") or "")
    if not task_id or not claim_id:
        raise ReplayError(f"finding {item_id!r} lacks task_id or claim_id")
    _one(tasks, "task_id", task_id, "task")
    claims = _load_jsonl(state / "design_claims.jsonl")
    _one(claims, "claim_id", claim_id, "claim")
    copier.copy_jsonl_selection(
        "investigation_tasks.jsonl", tasks, key="task_id", identifiers=[task_id],
    )
    copier.copy_jsonl_selection(
        "design_claims.jsonl", claims, key="claim_id", identifiers=[claim_id],
    )
    copier.copy_jsonl_selection(
        "investigation_findings.jsonl", findings, key="finding_id", identifiers=[item_id],
    )
    if stage == "probe":
        return {
            "finding_id": item_id, "task_id": task_id, "claim_id": claim_id,
            "probe_ids": [],
        }
    probe_ids: list[str] = []
    probe_path = state / "dynamic_probes.jsonl"
    if probe_path.is_file():
        probes = _load_jsonl(probe_path)
        selected_probes = [probe for probe in probes if str(probe.get("finding_id") or "") == item_id]
        probe_ids = [str(probe.get("probe_id") or "") for probe in selected_probes if probe.get("probe_id")]
        if probe_ids:
            copier.copy_jsonl_selection(
                "dynamic_probes.jsonl", probes, key="probe_id", identifiers=probe_ids,
            )

    if stage == "judge":
        critics = _load_jsonl(state / "critic_reviews.jsonl")
        _one(critics, "finding_id", item_id, "critic")
        copier.copy_jsonl_selection(
            "critic_reviews.jsonl", critics, key="finding_id", identifiers=[item_id],
        )
    selection = {
        "finding_id": item_id, "task_id": task_id, "claim_id": claim_id,
        "probe_ids": probe_ids,
    }
    return selection


def _copy_gate_support(copier: ArtifactCopier, manifest: dict[str, Any]) -> None:
    replay_root = copier.replay_root
    manifest_paths = manifest.get("paths", {}) if isinstance(manifest.get("paths"), dict) else {}
    for name, destination_name in (
        ("review_code_root", "code"), ("review_design_root", "design"),
    ):
        source_value = manifest_paths.get(name)
        source = Path(str(source_value)).resolve() if source_value else None
        if source is None or not source.is_dir() or source.is_symlink():
            raise ReplayError(f"gate replay requires a regular source review directory: {source}")
        destination = replay_root / "state" / "review-inputs" / destination_name
        shutil.copytree(source, destination, symlinks=True)

    materialization = manifest.get("design", {}).get("materialization_source") \
        if isinstance(manifest.get("design"), dict) else None
    if isinstance(materialization, dict):
        source_value = str(materialization.get("source_root") or "")
        plan_value = str(materialization.get("plan_path") or "")
        source_root = Path(source_value).resolve() if source_value else None
        plan_path = Path(plan_value).resolve() if plan_value else None
        if source_root is None or source_root.is_symlink() or not source_root.is_dir():
            raise ReplayError(
                f"gate replay requires the original materialization source: {source_root}"
            )
        if plan_path is None or plan_path.is_symlink() or not plan_path.is_file():
            raise ReplayError(
                f"gate replay requires the original materialization plan: {plan_path}"
            )
        replay_source = replay_root / "state" / "materialization-inputs" / "source"
        shutil.copytree(source_root, replay_source, symlinks=True)
        for source in ac.iter_integrity_files(source_root):
            relative = source.relative_to(source_root)
            destination = replay_source / relative
            logical_name = f"state/materialization-inputs/source/{relative}"
            if source.is_symlink():
                source_digest = ac.stable_id(
                    "symlink", str(source.readlink()), length=64,
                )
                replay_digest = ac.stable_id(
                    "symlink", str(destination.readlink()), length=64,
                )
                copier.records.append({
                    "logical_name": logical_name,
                    "source_path": str(source.absolute()),
                    "source_sha256": source_digest,
                    "replay_path": str(
                        destination.absolute().relative_to(replay_root.resolve())
                    ),
                    "replay_sha256": replay_digest,
                    "selection": {"mode": "materialization_source_tree"},
                })
                copier.copied.add(logical_name)
            elif source.is_file():
                copier._record(
                    source, destination, logical_name,
                    {"mode": "materialization_source_tree"},
                )
        replay_plan = copier.state_root / "design_source_plan.json"
        if not replay_plan.is_file():
            replay_plan.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(plan_path, replay_plan)
            copier._record(
                plan_path, replay_plan, "state/design_source_plan.json",
                {"mode": "materialization_plan"},
            )
        replay_manifest_path = copier.state_root / "workspace_manifest.json"
        replay_manifest = ac.load_json(replay_manifest_path)
        replay_materialization = replay_manifest.get("design", {}).get(
            "materialization_source"
        )
        if not isinstance(replay_materialization, dict):
            raise ReplayError("gate replay materialization snapshot is missing")
        replay_materialization["source_root"] = str(replay_source.resolve())
        replay_materialization["plan_path"] = str(replay_plan.resolve())
        source_manifest = replay_manifest.get("design", {}).get("source_manifest")
        if isinstance(source_manifest, dict):
            source_manifest["source_root"] = str(replay_source.resolve())
            source_manifest["plan_path"] = str(replay_plan.resolve())
        ac.save_json(replay_manifest_path, replay_manifest)
        _refresh_record(
            copier, "state/workspace_manifest.json", replay_manifest_path,
        )
        contract_path = copier.state_root / "agent_loop_contract.json"
        contract = ac.load_json(contract_path)
        target_roots = contract.get("guardrails", {}).get("target_roots_read_only") \
            if isinstance(contract, dict) else None
        if isinstance(target_roots, list):
            contract["guardrails"]["target_roots_read_only"] = [
                str(replay_source.resolve()) if value == source_value else value
                for value in target_roots
            ]
            ac.save_json(contract_path, contract)
            _refresh_record(copier, "state/agent_loop_contract.json", contract_path)

    design_manifest_path = copier.state_root / "design_agent_manifest.json"
    if design_manifest_path.is_file():
        design_manifest = ac.load_json(design_manifest_path)
        current_workspace = ac.load_json(
            copier.state_root / "workspace_manifest.json"
        )
        design_manifest["review_design_root"] = str(
            (replay_root / "state" / "review-inputs" / "design").resolve()
        )
        if isinstance(design_manifest.get("design"), dict) and isinstance(
            current_workspace.get("design"), dict
        ):
            design_manifest["design"]["source_manifest"] = current_workspace[
                "design"
            ].get("source_manifest")
        ac.save_json(design_manifest_path, design_manifest)
        for record in copier.records:
            if record["logical_name"] == "state/design_agent_manifest.json":
                record["replay_sha256"] = ac.sha256_file(design_manifest_path)
    source_log_value = manifest.get("paths", {}).get("log_root")
    source_trace = (
        Path(str(source_log_value)).resolve() / "trace"
        if source_log_value else copier.source_state.parent / "trace"
    )
    replay_trace = replay_root / "logs" / "trace"
    if source_trace.is_dir():
        for source in sorted(source_trace.glob("*.json")):
            if not source.is_file() or source.is_symlink():
                continue
            name = source.name
            if not (
                name in {
                    "run_clock.json", "session_prepared.json",
                    "design_validation.json", "claim_review_validation.json",
                    "architecture_validation.json", "risk_sweep_plan_validation.json",
                    "task_plan_validation.json", "task_lifecycle_validation.json",
                    "coverage_validation.json", "evidence_validation.json",
                }
                or name.endswith("-handoff-merge.json")
                or "-merge-" in name
            ):
                continue
            destination = replay_trace / name
            shutil.copy2(source, destination)
            copier._record(source, destination, f"logs/trace/{name}", None)

    templates = copier.source_state / "handoff-templates"
    if templates.is_dir() and not templates.is_symlink():
        for source in sorted(templates.rglob("*.json")):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(copier.source_state)
            destination = replay_root / "state" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copier._record(source, destination, f"state/{relative}", None)

    source_result_value = manifest.get("paths", {}).get("result_root")
    source_result = Path(str(source_result_value)).resolve() if source_result_value else None
    if source_result and source_result.is_dir():
        allowed = [source_result / name for name in ("issues.json", "issues.jsonl", "00-summary.md")]
        allowed.extend(sorted(source_result.glob("[0-9][0-9]-*.md")))
        for source in allowed:
            if not source.is_file() or source.is_symlink():
                continue
            destination = replay_root / "result" / source.name
            if source.name == "issues.json":
                value = ac.load_json(source)
                for issue in value.get("issues", []) if isinstance(value, dict) else []:
                    if isinstance(issue, dict) and issue.get("report_path"):
                        issue["report_path"] = str(
                            (replay_root / "result" / Path(str(issue["report_path"])).name).resolve()
                        )
                ac.save_json(destination, value)
            elif source.name == "issues.jsonl" and (replay_root / "result" / "issues.json").is_file():
                issues = ac.load_json(replay_root / "result" / "issues.json").get("issues", [])
                destination.write_text(
                    "".join(json.dumps(issue, ensure_ascii=False) + "\n" for issue in issues),
                    encoding="utf-8",
                )
            else:
                shutil.copy2(source, destination)
            copier._record(source, destination, f"result/{source.name}", None)

    ledger = copier.state_root / "agent_run_ledger.jsonl"
    source_ledger = copier.source_state / "agent_run_ledger.jsonl"
    if source_ledger.is_file() and ledger.is_file():
        _rewrite_ledger(source_ledger, ledger, source_trace, replay_trace)
        for record in copier.records:
            if record["logical_name"] == "state/agent_run_ledger.jsonl":
                record["replay_sha256"] = ac.sha256_file(ledger)


def _refresh_record(copier: ArtifactCopier, logical_name: str, path: Path) -> None:
    for record in copier.records:
        if record["logical_name"] == logical_name:
            record["replay_sha256"] = ac.sha256_file(path)
            return


def _copy_probe_support(copier: ArtifactCopier, *, refresh_gate_traces: bool) -> None:
    """Relocate probe workspaces and refresh their deterministic bindings.

    A probe records an absolute session-owned workspace.  A replay therefore
    cannot merely copy its JSONL ledger: it must copy that workspace below the
    replay state and update the path-dependent critic digest.  Semantic fields
    are preserved byte-for-byte.
    """
    probe_path = copier.state_root / "dynamic_probes.jsonl"
    if not probe_path.is_file():
        return
    probes = _load_jsonl(probe_path)
    if not probes:
        return
    source_probe_root = copier.source_state / "probes"
    replay_probe_root = copier.state_root / "probes"
    for probe in probes:
        isolation = probe.get("isolation")
        if not isinstance(isolation, dict):
            raise ReplayError("dynamic probe lacks an isolation object")
        source_value = isolation.get("workspace")
        source_workspace = Path(str(source_value)).resolve() if source_value else None
        if source_workspace is None or not source_workspace.is_dir() or source_workspace.is_symlink():
            raise ReplayError(f"dynamic probe workspace is unavailable: {source_workspace}")
        try:
            relative = source_workspace.relative_to(source_probe_root.resolve())
        except ValueError as exc:
            raise ReplayError(
                f"dynamic probe workspace is outside source state/probes: {source_workspace}"
            ) from exc
        destination_workspace = replay_probe_root / relative
        destination_workspace.mkdir(parents=True, exist_ok=True)
        for source in sorted(source_workspace.rglob("*")):
            if source.is_symlink():
                raise ReplayError(f"dynamic probe workspace contains a symlink: {source}")
            relative_file = source.relative_to(source_workspace)
            destination = destination_workspace / relative_file
            if source.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            if not source.is_file():
                raise ReplayError(f"dynamic probe workspace contains a non-file: {source}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copier._record(
                source, destination,
                f"state/probes/{relative}/{relative_file}",
                {"mode": "probe_workspace", "probe_id": probe.get("probe_id")},
            )
        isolation["workspace"] = str(destination_workspace.resolve())
        isolation["command_cwd"] = str(destination_workspace.resolve())
    probe_path.write_text(
        "".join(json.dumps(probe, ensure_ascii=False) + "\n" for probe in probes),
        encoding="utf-8",
    )
    _refresh_record(copier, "state/dynamic_probes.jsonl", probe_path)

    critic_path = copier.state_root / "critic_reviews.jsonl"
    history_path = copier.state_root / "critic_review_history.jsonl"
    if not critic_path.is_file() or not history_path.is_file():
        raise ReplayError("probe replay requires critic reviews and critic review history")
    critics = _load_jsonl(critic_path)
    rebound_critics: list[dict[str, Any]] = []
    for critic in critics:
        draft = dict(critic)
        draft.pop("input_digests", None)
        draft.pop("evidence_critic_prompt_version", None)
        rebound_critics.append(hm.materialize_critic_bindings(
            draft, copier.state_root,
            f"critic ({draft.get('finding_id') or draft.get('review_id') or '?'})",
        ))
    critic_path.write_text(
        "".join(json.dumps(critic, ensure_ascii=False) + "\n" for critic in rebound_critics),
        encoding="utf-8",
    )
    _refresh_record(copier, "state/critic_reviews.jsonl", critic_path)

    history = _load_jsonl(history_path)
    by_key = {
        str(entry.get("review_key") or ""): entry
        for entry in history if entry.get("review_key")
    }
    for critic in rebound_critics:
        review_key = hm.canonical_digest({
            "finding_id": critic.get("finding_id"),
            "input_digests": critic.get("input_digests"),
            "evidence_critic_prompt_version": critic.get(
                "evidence_critic_prompt_version"
            ),
        })
        critic_sha256 = hm.canonical_digest(critic)
        existing = by_key.get(review_key)
        if existing is not None and existing.get("critic_sha256") != critic_sha256:
            raise ReplayError("critic history conflicts with the relocated probe binding")
        if existing is None:
            entry = {
                "recorded_at": ac.now_iso(),
                "session_id": critic.get("session_id"),
                "finding_id": critic.get("finding_id"),
                "review_key": review_key,
                "input_digests": critic.get("input_digests"),
                "evidence_critic_prompt_version": critic.get(
                    "evidence_critic_prompt_version"
                ),
                "critic_sha256": critic_sha256,
                "replay_relocation": True,
            }
            history.append(entry)
            by_key[review_key] = entry
    history_path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in history),
        encoding="utf-8",
    )
    _refresh_record(copier, "state/critic_review_history.jsonl", history_path)

    if not refresh_gate_traces:
        return
    replay_trace = copier.replay_root / "logs" / "trace"
    ledger_digests = {
        "probe": ac.sha256_file(probe_path),
        "critic": ac.sha256_file(critic_path),
    }
    for report_path in sorted(replay_trace.glob("*merge*.json")):
        if not report_path.is_file() or report_path.is_symlink():
            continue
        report = ac.load_json(report_path)
        artifact_type = report.get("artifact_type") if isinstance(report, dict) else None
        if artifact_type not in ledger_digests:
            continue
        report["ledger_sha256"] = ledger_digests[artifact_type]
        ac.save_json(report_path, report)
        _refresh_record(copier, f"logs/trace/{report_path.name}", report_path)

    ledger_path = copier.state_root / "agent_run_ledger.jsonl"
    ledger = _load_jsonl(ledger_path)
    for event in ledger:
        artifact_type = event.get("artifact_type")
        if event.get("event") != "handoff_merge" or artifact_type not in ledger_digests:
            continue
        event["ledger_sha256"] = ledger_digests[artifact_type]
        report_value = event.get("report")
        report_path = Path(str(report_value)) if report_value else None
        if report_path is not None and report_path.is_file():
            event["report_sha256"] = ac.sha256_file(report_path)
    ledger_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in ledger),
        encoding="utf-8",
    )
    _refresh_record(copier, "state/agent_run_ledger.jsonl", ledger_path)


def _outputs(stage: str, item_id: str | None) -> list[dict[str, str]]:
    if stage == "inventory":
        paths = ("state/design_inventory.json",)
    elif stage == "claims":
        paths = ("state/design_coverage.json", "state/design_claims.jsonl")
    elif stage == "claim-review":
        paths = ("state/design_claim_review.json",)
    elif stage == "risk":
        paths = (f"state/handoffs/risks/{item_id}/{item_id}.json",)
    elif stage == "plan":
        paths = ("state/investigation_tasks.jsonl", "state/investigation_rounds.jsonl")
    elif stage == "investigator":
        paths = (f"state/handoffs/investigators/{item_id}/{item_id}.json",)
    elif stage == "probe":
        paths = (f"state/handoffs/probes/{item_id}/{item_id}.json",)
    elif stage == "critic":
        paths = (f"state/handoffs/critics/{item_id}/{item_id}.json",)
    elif stage == "judge":
        paths = ("state/agent_review_verdicts.jsonl",)
    elif stage == "coverage":
        paths = ("state/semantic_coverage.json", "state/coverage_audit.json")
    else:
        paths = ("logs/trace/final_gate.json",)
    return [{"path": path, "write_scope": "replay_only"} for path in paths]


def _prompt_inputs(stage: str, records: list[dict[str, Any]]) -> list[str]:
    """Expose only role-owned artifacts; copied core files remain validator infrastructure."""
    allowed: dict[str, set[str]] = {
        "inventory": {
            "state/design_agent_manifest.json", "state/design_source_plan.json",
        },
        "claims": {
            "state/design_agent_manifest.json", "state/design_source_plan.json",
            "state/design_inventory.json", "state/design_lookup_requests.jsonl",
        },
        "claim-review": {
            "state/design_agent_manifest.json", "state/design_inventory.json",
            "state/design_coverage.json",
            "state/design_claims.jsonl", "state/claim_review_scope.json",
            "state/design_claim_review.json",
        },
        "risk": {
            "state/agent_loop_contract.json", "state/architecture_map.json",
            "state/design_inventory.json", "state/risk_sweep_plan.json",
            "state/coverage_supplement_history.json",
        },
        "plan": {
            "state/agent_loop_contract.json", "state/architecture_map.json",
            "state/risk_sweep_plan.json",
            "state/design_inventory.json", "state/design_lookup_requests.jsonl",
            "state/design_coverage.json", "state/design_claims.jsonl",
            "state/claim_review_scope.json", "state/design_claim_review.json",
            "state/risk_observations.jsonl", "state/coverage_audit.json",
            "state/semantic_coverage.json",
            "logs/trace/claim_review_validation.json",
        },
        "investigator": {
            "state/architecture_map.json", "state/risk_sweep_plan.json",
            "state/design_coverage.json",
            "state/design_claims.jsonl", "state/investigation_tasks.jsonl",
            "state/risk_observations.jsonl",
        },
        "critic": {
            "state/design_claims.jsonl",
            "state/investigation_findings.jsonl", "state/dynamic_probes.jsonl",
        },
        "probe": {
            "state/design_claims.jsonl", "state/investigation_findings.jsonl",
        },
        "judge": {
            "state/design_claims.jsonl",
            "state/investigation_findings.jsonl", "state/dynamic_probes.jsonl",
            "state/critic_reviews.jsonl",
        },
        "coverage": {
            "state/agent_loop_contract.json", "state/workspace_manifest.json",
            "state/architecture_map.json", "state/risk_sweep_plan.json",
            "state/design_agent_manifest.json", "state/design_inventory.json",
            "state/design_lookup_requests.jsonl",
            "state/design_coverage.json", "state/design_claims.jsonl",
            "state/claim_review_scope.json", "state/design_claim_review.json",
            "state/risk_observations.jsonl",
            "state/investigation_tasks.jsonl", "state/investigation_findings.jsonl",
            "state/dynamic_probes.jsonl", "state/critic_reviews.jsonl",
            "state/investigation_rounds.jsonl",
            "state/coverage_supplement_history.json",
            "logs/trace/claim_review_validation.json",
        },
    }
    if stage == "gate":
        return [record["replay_path"] for record in records]
    exact = allowed.get(stage, set())
    values: list[str] = []
    for record in records:
        logical_name = str(record.get("logical_name") or "")
        if logical_name in exact or (
            stage == "investigator"
            and logical_name.startswith("state/handoff-templates/investigators/")
        ):
            values.append(str(record["replay_path"]))
    return values


def prepare_replay(
    *,
    source_state: Path,
    replay_root: Path,
    stage: str,
    item_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    prompt_file: Path | None = None,
    run_local: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Create an isolated replay and return its manifest."""
    if stage not in STAGES:
        raise ReplayError(f"unknown stage {stage!r}")
    if stage in ITEM_STAGES and not item_id:
        raise ReplayError(f"--item-id is required for stage {stage}")
    if item_id and stage not in ITEM_STAGES:
        raise ReplayError(f"--item-id is not valid for stage {stage}")
    if run_local and stage not in LOCAL_STAGES:
        raise ReplayError(
            "--run-local is supported only for inventory, claims, claim-review, plan, "
            "coverage, and gate"
        )

    source_state = source_state.resolve()
    replay_root = replay_root.resolve()
    for name in CORE_FILES:
        path = source_state / name
        if not path.is_file() or path.is_symlink():
            raise ReplayError(f"missing regular source artifact: {path}")
    source_manifest = ac.load_json(source_state / "workspace_manifest.json")
    source_contract = ac.load_json(source_state / "agent_loop_contract.json")
    source_loop_state = ac.load_json(source_state / "agent_loop_state.json")
    session_ids = {
        str(source_manifest.get("session_id") or ""),
        str(source_contract.get("session", {}).get("session_id") or ""),
        str(source_loop_state.get("session_id") or ""),
    }
    if len(session_ids) != 1 or "" in session_ids:
        raise ReplayError("source core artifacts do not share one non-empty session_id")
    session_id = next(iter(session_ids))

    source_paths = source_manifest.get("paths", {})
    protected_roots = [source_state]
    protected_roots.extend([
        REPO_ROOT / "work",
        REPO_ROOT / "INSTRUCTION.md",
    ])
    if prompt_file is not None:
        protected_roots.append(prompt_file.resolve())
    if isinstance(source_paths, dict):
        protected_roots.extend(
            Path(str(value)) for key, value in source_paths.items()
            if key in {
                "code_root", "design_root", "result_root", "log_root", "state_root",
                "review_code_root", "review_design_root",
            }
            and isinstance(value, str) and value
        )
    materialization = source_manifest.get("design", {}).get(
        "materialization_source"
    ) if isinstance(source_manifest.get("design"), dict) else None
    if isinstance(materialization, dict):
        for key in ("source_root", "plan_path"):
            value = materialization.get(key)
            if isinstance(value, str) and value:
                protected_roots.append(Path(value))
    _prepare_empty_root(source_state, replay_root, force, protected_roots)
    copier = ArtifactCopier(source_state, replay_root)
    copier.copy_file(
        "workspace_manifest.json",
        transform=lambda source, destination: _rewrite_manifest(
            source, destination, replay_root, stage,
        ),
    )
    copier.copy_file(
        "agent_loop_contract.json",
        transform=lambda source, destination: _rewrite_contract(source, destination, replay_root),
    )
    copier.copy_file(
        "agent_loop_state.json",
        transform=lambda source, destination: _rewrite_state(source, destination, replay_root),
    )
    selection = _prepare_stage_inputs(copier, stage, item_id, run_local)
    if stage == "coverage":
        _copy_probe_support(copier, refresh_gate_traces=False)
    if stage == "gate":
        _copy_gate_support(copier, source_manifest)
        _copy_probe_support(copier, refresh_gate_traces=True)

    prompt, prompt_digest, skills, schema = _copy_prompt_assets(
        replay_root, stage, prompt_file,
    )
    source_rows = [
        {
            "logical_name": record["logical_name"],
            "source_sha256": record["source_sha256"],
            "selection": record["selection"],
        }
        for record in sorted(copier.records, key=lambda value: value["logical_name"])
    ]
    replay_rows = [
        {"logical_name": record["logical_name"], "replay_sha256": record["replay_sha256"]}
        for record in sorted(copier.records, key=lambda value: value["logical_name"])
    ]
    source_digest = _json_digest(source_rows)
    replay_input_digest = _json_digest(replay_rows)
    contract_version = source_contract.get("contract_version")
    replay_id = "replay-" + ac.stable_id(
        session_id, stage, item_id or "", source_digest, prompt_digest, schema["skill_digest"],
    )

    replay_manifest = ac.load_json(replay_root / "state" / "workspace_manifest.json")
    paths = replay_manifest.get("paths", {})
    read_roots: dict[str, str] = {}
    if stage in {"risk", "investigator", "probe", "critic"} and paths.get("review_code_root"):
        read_roots["code"] = str(paths["review_code_root"])
    if stage in {"inventory", "claims", "claim-review", "risk", "investigator", "critic"} and paths.get(
        "review_design_root"
    ):
        read_roots["design"] = str(paths["review_design_root"])
    envelope = {
        "envelope_version": 1,
        "replay_id": replay_id,
        "source_session_id": session_id,
        "stage": stage,
        "mode": "deterministic_local" if run_local or stage == "gate" else "external_llm_required",
        "selection": selection,
        "instruction": prompt,
        "prompt_sha256": prompt_digest,
        "skills": skills,
        "skill_digest": schema["skill_digest"],
        "inputs": _prompt_inputs(
            stage, sorted(copier.records, key=lambda value: value["logical_name"]),
        ),
        "read_only_source_roots": read_roots,
        "outputs": _outputs(stage, item_id),
        "guardrails": [
            "Write only beneath the replay root.",
            "Do not modify the source session, target code, or design documents.",
            "Do not read evaluation-only assets or use expected answers as evidence.",
            "Do not invoke or simulate a model from this preparation tool.",
        ],
    }
    envelope_path = replay_root / "prompt_envelope.json"
    ac.save_json(envelope_path, envelope)

    runtime = {key: value for key, value in (("provider", provider), ("model", model)) if value}
    manifest = {
        "manifest_version": 1,
        "replay_id": replay_id,
        "created_at": ac.now_iso(),
        "development_only": True,
        "stage": stage,
        "source_session_id": session_id,
        "source_state_root": str(source_state),
        "replay_root": str(replay_root),
        "selection": selection,
        "source_digest": source_digest,
        "replay_input_digest": replay_input_digest,
        "prompt": {
            "path": "prompt-assets/prompt.txt",
            "sha256": prompt_digest,
            "envelope_path": "prompt_envelope.json",
            "envelope_sha256": ac.sha256_file(envelope_path),
        },
        "skills": {"combined_sha256": schema["skill_digest"], "files": skills},
        "schema": {
            "version": f"contract-{contract_version}",
            "contract_version": contract_version,
            "output_schema_path": schema["path"],
            "output_schema_draft": schema["draft"],
            "output_schema_sha256": schema["sha256"],
            "publication_schema_applies_to_stage": schema["applies_to_stage"],
        },
        "runtime": runtime,
        "artifacts": sorted(copier.records, key=lambda value: value["logical_name"]),
        "outputs": _outputs(stage, item_id),
        "llm_invoked": False,
        "local_execution_requested": run_local,
        "local_execution": None,
    }
    ac.save_json(replay_root / "replay_manifest.json", manifest)
    return manifest


def run_local(replay_root: Path) -> int:
    """Execute a permitted deterministic replay and record its exact command/result."""
    replay_root = replay_root.resolve()
    manifest_path = replay_root / "replay_manifest.json"
    if not manifest_path.is_file():
        raise ReplayError(f"missing replay manifest: {manifest_path}")
    manifest = ac.load_json(manifest_path)
    stage = str(manifest.get("stage") or "")
    if stage not in LOCAL_STAGES:
        raise ReplayError(f"stage {stage!r} has no deterministic local replay")
    workspace = ac.load_json(replay_root / "state" / "workspace_manifest.json")
    paths = workspace.get("paths", {})
    base = [
        "--code-root", str(paths.get("code_root") or ""),
        "--design-root", str(paths.get("design_root") or ""),
        "--result-root", str((replay_root / "result").resolve()),
        "--log-root", str((replay_root / "logs").resolve()),
        "--state-root", str((replay_root / "state").resolve()),
    ]
    preflight_command: list[str] | None = None
    if stage == "inventory":
        design_manifest = ac.load_json(replay_root / "state" / "design_agent_manifest.json")
        materializer_design_root = str(
            design_manifest.get("review_design_root")
            or paths.get("review_design_root")
            or paths.get("design_root")
            or ""
        )
        materialized_output = replay_root / "state" / "design_inventory.json"
        materialized_output.unlink(missing_ok=True)
        preflight_command = [
            sys.executable, str(SCRIPT_DIR / "design_source_materializer.py"),
            "--materialize", "inventory",
            "--design-root", materializer_design_root,
            "--input", str(replay_root / "state" / "handoffs" / "design" / "inventory.raw.json"),
            "--output", str(materialized_output),
            "--trace", str(replay_root / "logs" / "trace" / "design_inventory_materialization.json"),
        ]
        command = [
            sys.executable, str(SCRIPT_DIR / "design_artifact_validator.py"),
            *base, "--mode", "inventory",
        ]
        kind = "design_inventory_validation"
    elif stage == "claims":
        design_manifest = ac.load_json(replay_root / "state" / "design_agent_manifest.json")
        materializer_design_root = str(
            design_manifest.get("review_design_root")
            or paths.get("review_design_root")
            or paths.get("design_root")
            or ""
        )
        materialized_output = replay_root / "state" / "design_claims.jsonl"
        materialized_output.unlink(missing_ok=True)
        preflight_command = [
            sys.executable, str(SCRIPT_DIR / "design_source_materializer.py"),
            "--materialize", "claims",
            "--design-root", materializer_design_root,
            "--input", str(replay_root / "state" / "handoffs" / "design" / "claims.raw.jsonl"),
            "--output", str(materialized_output),
            "--trace", str(replay_root / "logs" / "trace" / "design_claim_materialization.json"),
        ]
        command = [
            sys.executable, str(SCRIPT_DIR / "design_artifact_validator.py"),
            *base, "--mode", "claims",
        ]
        kind = "design_schema_validation"
    elif stage == "claim-review":
        command = [sys.executable, str(SCRIPT_DIR / "claim_review_validator.py"), *base]
        kind = "claim_review_validation"
    elif stage == "plan":
        command = [
            sys.executable, str(SCRIPT_DIR / "goal_runner.py"), "task-plan-check", *base,
        ]
        kind = "task_plan_validation"
    elif stage == "coverage":
        preflight_command = [
            sys.executable, str(SCRIPT_DIR / "claim_review_validator.py"), *base,
        ]
        command = [
            sys.executable, str(SCRIPT_DIR / "goal_runner.py"), "coverage-check", *base,
        ]
        kind = "coverage_validation"
    else:
        command = [sys.executable, str(SCRIPT_DIR / "goal_runner.py"), "gate", *base]
        kind = "final_gate"
    preflight_execution = None
    executed_command = command
    if preflight_command is not None:
        preflight = subprocess.run(
            preflight_command, capture_output=True, text=True, check=False,
        )
        preflight_execution = {
            "command": preflight_command,
            "returncode": preflight.returncode,
            "stdout": preflight.stdout,
            "stderr": preflight.stderr,
        }
        completed = (
            subprocess.run(command, capture_output=True, text=True, check=False)
            if preflight.returncode == 0 else preflight
        )
        if preflight.returncode != 0:
            executed_command = preflight_command
    else:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    execution = {
        "executed_at": ac.now_iso(),
        "kind": kind,
        "command": executed_command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "preflight": preflight_execution,
    }
    trace_path = replay_root / "logs" / "trace" / "stage_replay_local.json"
    ac.save_json(trace_path, execution)
    manifest["local_execution"] = execution | {"trace_path": _relative(replay_root, trace_path)}
    ac.save_json(manifest_path, manifest)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a development-only, single-stage replay without invoking an LLM."
    )
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--source-state", required=True)
    parser.add_argument("--replay-root", required=True)
    parser.add_argument("--item-id")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--prompt-file")
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        manifest = prepare_replay(
            source_state=Path(args.source_state),
            replay_root=Path(args.replay_root),
            stage=args.stage,
            item_id=args.item_id,
            provider=args.provider,
            model=args.model,
            prompt_file=Path(args.prompt_file) if args.prompt_file else None,
            run_local=args.run_local,
            force=args.force,
        )
        returncode = run_local(Path(args.replay_root)) if args.run_local else 0
    except (OSError, UnicodeError, json.JSONDecodeError, ReplayError) as exc:
        print(json.dumps({"prepared": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({
        "prepared": True,
        "replay_id": manifest["replay_id"],
        "stage": manifest["stage"],
        "manifest": str((Path(args.replay_root).resolve() / "replay_manifest.json")),
        "prompt_envelope": str((Path(args.replay_root).resolve() / "prompt_envelope.json")),
        "local_returncode": returncode if args.run_local else None,
    }, ensure_ascii=False))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
