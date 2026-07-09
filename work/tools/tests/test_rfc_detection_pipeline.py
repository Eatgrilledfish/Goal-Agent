import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
sys.path.insert(0, str(SCRIPT_DIR))

import final_detection_gate
import agent_review_bundle_builder
import evidence_validator
import issue_ranker
import issue_report_writer
import normative_requirement_extractor
import protocol_inconsistency_detector
import public_fstack_gold_evaluator
import requirement_code_mapper
import rfc_goal_runner


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _issue(status: str, seq: int) -> dict:
    return {
        "issue_id": f"ISSUE-{seq}",
        "title": f"Issue {seq}",
        "status": status,
        "confidence": 0.85 if status == "confirmed" else 0.72,
        "normative_level": "MUST",
        "design_evidence": {
            "rfc": "RFC4861",
            "section": "7.2",
            "doc_path": "/tmp/rfc4861.md",
            "quote": "MUST do the thing.",
        },
        "code_evidence": [
            {
                "file": "freebsd/netinet6/nd6.c",
                "line_start": 10,
                "line_end": 20,
                "symbol": "nd6_ns_input",
                "snippet": "void\nnd6_ns_input(void)\n{\n}\n",
            }
        ],
        "inconsistency": "RFC and implementation differ.",
        "impact": "Protocol behavior may differ.",
        "false_positive_controls": ["Concrete RFC and code evidence are present."],
        "related_files": ["freebsd/netinet6/nd6.c"],
        "report_path": f"/result/{seq:02d}-issue.md",
        "agent_review": {
            "source": "opencode",
            "candidate_id": f"ISSUE-{seq}",
            "agent_notes": "opencode reviewed the evidence chain",
            "generalization_rationale": "The finding is based on design/code behavior rather than a project-specific answer.",
            "tool_trace": [],
        },
    }


def _result_doc(issues: list[dict]) -> dict:
    return {
        "generated_at": "2026-01-01T00:00:00Z",
        "tool": "goal-agent-rfc-diff",
        "code_root": "/tmp/f-stack",
        "benchmark": "/tmp/benchmark.md",
        "summary": {
            "total": len(issues),
            "confirmed": sum(1 for i in issues if i["status"] == "confirmed"),
            "probable": sum(1 for i in issues if i["status"] == "probable"),
            "high_confidence": sum(1 for i in issues if i["confidence"] >= 0.80),
        },
        "issues": issues,
    }


def _candidate(dtype: str) -> dict:
    return {
        "candidate_id": f"REQ-1-{dtype}",
        "requirement_id": "REQ-1",
        "title": "Neighbor Discovery behavior",
        "detection_type": dtype,
        "normative_level": "MUST",
        "design_evidence": {
            "rfc": "RFC4861",
            "section": "4.3",
            "doc_path": "/tmp/rfc4861.md",
            "quote": "Nodes MUST process the option chain.",
        },
        "code_evidence": [
            {
                "file": "freebsd/netinet6/nd6.c",
                "symbol": "nd6_options",
                "line_start": 452,
                "line_end": 534,
                "snippet": "int\nnd6_options(void)\n{\n    break;\n}\n",
                "match_reasons": ["early break out of header/option processing loop"],
                "evidence_lines": [
                    {
                        "line": 494,
                        "text": "break;",
                        "reason": "early break out of header/option processing loop",
                    }
                ],
            }
        ],
        "trace_status": "linked",
        "inconsistency": "Concrete implementation evidence differs from the RFC.",
        "detection_reasons": [f"detection_type={dtype}", "code=freebsd/netinet6/nd6.c:494"],
        "protocol_area": "ipv6_nd",
    }


def _verdict(candidate_id: str, status: str = "confirmed") -> dict:
    candidate = _candidate("wrong_control_flow")
    return {
        "candidate_id": candidate_id,
        "status": status,
        "confidence": 0.91 if status == "confirmed" else 0.7,
        "title": "Neighbor Discovery behavior",
        "normative_level": "MUST",
        "design_evidence": candidate["design_evidence"],
        "code_evidence": candidate["code_evidence"],
        "inconsistency": "The design requires processing the option chain, but the implementation exits early.",
        "impact": "Valid protocol options after the early exit may be ignored.",
        "false_positive_controls": ["Checked the implementation path and no alternate branch implements the design behavior."],
        "related_files": ["freebsd/netinet6/nd6.c"],
        "agent_notes": "opencode reviewed design quote and code context",
        "generalization_rationale": "The finding is based on design/code behavior, not a project-specific filename.",
    }


def _agent_discovered_verdict(seq: int) -> dict:
    return {
        "candidate_id": f"AGENT-DISCOVERED-{seq:03d}",
        "status": "confirmed",
        "confidence": 0.86,
        "title": f"Agent confirmed design/code mismatch {seq}",
        "normative_level": "design-requirement",
        "design_evidence": {
            "rfc": "design.md",
            "section": f"Requirement {seq}",
            "doc_path": "/tmp/design.md",
            "quote": f"The implementation must satisfy behavior {seq}.",
        },
        "code_evidence": [
            {
                "file": f"src/module_{seq}.c",
                "line_start": 10,
                "line_end": 20,
                "symbol": f"module_{seq}",
                "snippet": f"int module_{seq}(void) {{ return 0; }}",
            }
        ],
        "inconsistency": f"The design requires behavior {seq}, but the implementation omits it.",
        "impact": "The documented behavior is not provided at runtime.",
        "false_positive_controls": ["Checked related files and no alternate implementation exists."],
        "related_files": [f"src/module_{seq}.c"],
        "agent_notes": "opencode inspected design and code evidence.",
        "generalization_rationale": "The issue is based on a design requirement and implementation evidence.",
    }


def _gold_result_issue(seq: int, title: str, rfc: str, section: str, file_rel: str,
                       text: str) -> dict:
    issue = _issue("confirmed", seq)
    issue.update({
        "issue_id": f"GOLD-{seq}",
        "title": title,
        "design_evidence": {
            "rfc": rfc,
            "section": section,
            "doc_path": f"/tmp/{rfc.lower()}.md",
            "quote": text,
        },
        "code_evidence": [
            {
                "file": file_rel,
                "line_start": 10,
                "line_end": 20,
                "symbol": "gold_symbol",
                "snippet": text,
            }
        ],
        "inconsistency": text,
        "impact": text,
        "related_files": [file_rel],
    })
    return issue


def test_protocol_detection_patterns_do_not_include_weak_fallbacks():
    cfg = json.loads((CONFIG_DIR / "protocol_detection_patterns.json").read_text(encoding="utf-8"))
    regexes = [
        pattern.get("regex", "")
        for dtype in cfg["detection_types"].values()
        for pattern in dtype.get("code_signals", {}).get("patterns", [])
    ]

    assert "return" not in regexes
    assert "\\b\\w+\\s*\\(" not in regexes
    assert "ETHERTYPE_IP6" not in regexes


def test_runner_discovers_non_fstack_code_root(tmp_path, monkeypatch):
    asset_root = tmp_path / "assets"
    hidden_project = asset_root / "code" / "internal-stack"
    design_root = asset_root / "design-docs"
    hidden_project.mkdir(parents=True)
    _write(design_root / "spec.md", "# Spec\n")
    monkeypatch.setattr(rfc_goal_runner.rc, "DEFAULT_ASSET_ROOT", str(asset_root))

    assert rfc_goal_runner.discover_code_root() == str(hidden_project)
    assert rfc_goal_runner.discover_design_root() == str(design_root)
    assert rfc_goal_runner.discover_benchmark(str(design_root)) == str(design_root / "spec.md")


def test_prepare_review_phase_refreshes_recall_artifacts_before_bundles():
    scripts = rfc_goal_runner.PHASE_SCRIPTS["prepare-review"]

    assert scripts[:9] == [
        "benchmark_reader.py",
        "rfc_fetch_convert.py",
        "code_inventory_lite.py",
        "rfc_scope_planner.py",
        "rfc_scope_plan_validator.py",
        "normative_requirement_extractor.py",
        "c_code_indexer.py",
        "requirement_code_mapper.py",
        "protocol_inconsistency_detector.py",
    ]
    assert scripts[-1] == "agent_review_bundle_builder.py"


def test_generic_design_doc_extraction_and_mapping_without_rfc_manifest(tmp_path):
    code_root = tmp_path / "internal-service"
    design_root = tmp_path / "design"
    log_root = tmp_path / "logs"
    work = tmp_path / ".agent-work"
    code_root.mkdir()
    _write(
        design_root / "spec.md",
        """# Retry Policy

The payment client must retry transient upstream failures before returning an error.
""",
    )
    _write(
        work / "code_index.json",
        json.dumps({"files": [{
            "file": "src/payment_client.c",
            "topics": ["general"],
            "symbols": [{
                "name": "send_payment_once",
                "kind": "function",
                "line_start": 10,
                "line_end": 20,
                "signature": "int send_payment_once(void)",
                "snippet": "int send_payment_once(void) { return upstream_send(); /* no retry */ }",
            }],
        }]}),
    )

    rc = normative_requirement_extractor.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(design_root),
            "--benchmark", str(design_root / "spec.md"),
            "--log-root", str(log_root),
        ]
    )
    assert rc == 0

    reqs = json.loads((work / "rfc_requirements.json").read_text(encoding="utf-8"))["requirements"]
    assert len(reqs) == 1
    assert reqs[0]["rfc"].startswith("DESIGN-")
    assert reqs[0]["source_kind"] == "design_document"
    assert "retry transient upstream failures" in reqs[0]["requirement_text"]

    rc = requirement_code_mapper.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(design_root),
            "--benchmark", str(design_root / "spec.md"),
            "--log-root", str(log_root),
        ]
    )
    trace = json.loads((work / "rfc_code_trace.json").read_text(encoding="utf-8"))["traces"][0]
    assert rc == 0
    assert trace["mapping_strategy"] == "generic_keyword_snippet"
    assert trace["candidate_code_locations"][0]["file"] == "src/payment_client.c"


def test_detector_ignores_generic_function_call_and_plain_return():
    cfg = json.loads((CONFIG_DIR / "protocol_detection_patterns.json").read_text(encoding="utf-8"))
    req = {
        "requirement_id": "REQ-1",
        "rfc": "RFC4861",
        "section": "7.2",
        "normative_level": "MUST",
        "requirement_text": "The implementation MUST process this behavior.",
        "title": "Generic requirement",
    }
    trace = {
        "requirement_id": "REQ-1",
        "trace_status": "linked",
        "candidate_code_locations": [
            {
                "file": "freebsd/netinet6/nd6.c",
                "symbol": "nd6_ns_input",
                "line_start": 10,
                "line_end": 14,
                "snippet": "void nd6_ns_input(void)\n{\n    helper_call();\n    return;\n}\n",
            }
        ],
    }

    candidates = protocol_inconsistency_detector.detect_for_requirement(
        req, trace, {"files": []}, Path("/missing"), cfg
    )

    assert candidates == []


def test_validator_requires_opencode_verdict_before_confirming(tmp_path):
    code_root = tmp_path / "f-stack"
    work = tmp_path / ".agent-work"
    log_root = tmp_path / "logs"
    code_root.mkdir()
    _write(
        work / "candidate_issues.json",
        json.dumps({"candidates": [_candidate("wrong_control_flow")]}),
    )

    rc = evidence_validator.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
            "--log-root", str(log_root),
        ]
    )

    validated = json.loads((work / "validated_issues.json").read_text(encoding="utf-8"))
    assert rc == 2
    assert validated["agent_review_present"] is False
    assert validated["confirmed"] == 0
    assert validated["rejected"] == 1


def test_validator_consumes_opencode_confirmed_verdict(tmp_path):
    code_root = tmp_path / "f-stack"
    work = tmp_path / ".agent-work"
    log_root = tmp_path / "logs"
    code_root.mkdir()
    candidate = _candidate("wrong_control_flow")
    _write(work / "candidate_issues.json", json.dumps({"candidates": [candidate]}))
    _write(
        work / "agent_review_verdicts.jsonl",
        json.dumps(_verdict(candidate["candidate_id"], "confirmed")) + "\n",
    )

    rc = evidence_validator.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
            "--log-root", str(log_root),
        ]
    )

    validated = json.loads((work / "validated_issues.json").read_text(encoding="utf-8"))
    issue = validated["issues"][0]
    assert rc == 0
    assert validated["confirmed"] == 1
    assert issue["status"] == "confirmed"
    assert issue["confidence"] == 0.91
    assert issue["agent_review"]["source"] == "opencode"


def test_validator_rejects_confirmed_verdict_without_agent_evidence(tmp_path):
    code_root = tmp_path / "f-stack"
    work = tmp_path / ".agent-work"
    log_root = tmp_path / "logs"
    code_root.mkdir()
    candidate = _candidate("wrong_control_flow")
    _write(work / "candidate_issues.json", json.dumps({"candidates": [candidate]}))
    _write(
        work / "agent_review_verdicts.jsonl",
        json.dumps({
            "candidate_id": candidate["candidate_id"],
            "status": "confirmed",
            "confidence": 0.91,
            "title": "Bare verdict should not pass",
        }) + "\n",
    )

    rc = evidence_validator.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
            "--log-root", str(log_root),
        ]
    )

    validated = json.loads((work / "validated_issues.json").read_text(encoding="utf-8"))
    issue = validated["issues"][0]
    assert rc == 0
    assert validated["confirmed"] == 0
    assert issue["status"] == "rejected"
    assert "missing design_evidence.quote" in issue["fp_note"]


def test_validator_accepts_agent_discovered_issue(tmp_path):
    code_root = tmp_path / "f-stack"
    work = tmp_path / ".agent-work"
    log_root = tmp_path / "logs"
    code_root.mkdir()
    discovered = {
        "candidate_id": "AGENT-DISCOVERED-001",
        "status": "confirmed",
        "confidence": 0.88,
        "title": "Design behavior missing from implementation",
        "normative_level": "design-requirement",
        "design_evidence": {
            "rfc": "design.md",
            "section": "Retries",
            "doc_path": str(tmp_path / "design" / "design.md"),
            "quote": "The service must retry transient failures.",
        },
        "code_evidence": [
            {
                "file": "src/client.c",
                "line_start": 10,
                "line_end": 20,
                "symbol": "send_request",
                "snippet": "int send_request(void) { return send_once(); }",
            }
        ],
        "inconsistency": "The design requires retrying transient failures, but the implementation sends once and returns.",
        "impact": "Transient failures are exposed to callers instead of being retried.",
        "false_positive_controls": ["Searched for retry helpers and callers; none are used on this path."],
        "related_files": ["src/client.c"],
        "generalization_rationale": "The issue is based on a design-required retry behavior and code path evidence.",
    }
    _write(work / "agent_review_verdicts.jsonl", json.dumps(discovered) + "\n")

    rc = evidence_validator.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
            "--log-root", str(log_root),
        ]
    )

    validated = json.loads((work / "validated_issues.json").read_text(encoding="utf-8"))
    assert rc == 0
    assert validated["confirmed"] == 1
    assert validated["issues"][0]["issue_id"] == "AGENT-DISCOVERED-001"


def test_agent_review_bundle_builder_emits_queue(tmp_path):
    code_root = tmp_path / "f-stack"
    design_root = tmp_path / "Difference"
    work = tmp_path / ".agent-work"
    log_root = tmp_path / "logs"
    code_root.mkdir()
    _write(design_root / "benchmark.md", "RFC 4861 says nodes MUST process options.\n")
    _write(
        code_root / "freebsd" / "netinet6" / "nd6.c",
        "int\nnd6_options(void)\n{\n    break;\n}\n",
    )
    candidate = _candidate("wrong_control_flow")
    _write(work / "candidate_issues.json", json.dumps({"candidates": [candidate]}))
    _write(
        work / "rfc_requirements.json",
        json.dumps({"requirements": [{
            "requirement_id": candidate["requirement_id"],
            "rfc": "RFC4861",
            "section": "4.3",
            "normative_level": "MUST",
            "requirement_text": "Nodes MUST process the option chain.",
        }]}),
    )
    _write(
        work / "code_index.json",
        json.dumps({"files": [{
            "file": "freebsd/netinet6/nd6.c",
            "symbols": [{
                "name": "nd6_options",
                "line_start": 1,
                "line_end": 5,
                "snippet": "int\nnd6_options(void)\n{\n    break;\n}\n",
            }],
        }]}),
    )

    rc = agent_review_bundle_builder.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(design_root),
            "--benchmark", str(design_root / "benchmark.md"),
            "--log-root", str(log_root),
        ]
    )

    queue = json.loads((work / "agent_review_queue.json").read_text(encoding="utf-8"))
    candidate_item = next(item for item in queue["items"] if item["item_type"] == "candidate_review")
    domain_item = next(item for item in queue["items"] if item["item_type"] == "protocol_domain_review")
    bundle_path = tmp_path / candidate_item["bundle_path"]
    domain_bundle = json.loads(Path(domain_item["bundle_abs_path"]).read_text(encoding="utf-8"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert rc == 0
    assert queue["review_required"] is True
    assert queue["candidate_queued_count"] == 1
    assert queue["protocol_domain_queued_count"] == 1
    assert queue["queued_count"] == 2
    assert queue["agent_work"] == str(work)
    assert candidate_item["bundle_abs_path"] == str(bundle_path)
    assert domain_bundle["item_type"] == "protocol_domain_review"
    families = {item["family"] for item in queue["semantic_review_checklist"]}
    assert "protocol_or_feature_gap" in families
    assert "optional_or_recommended_behavior_omitted" in families
    assert "timer_randomization_or_delay_gap" in families
    assert bundle["candidate_id"] == candidate["candidate_id"]
    assert bundle["review_contract"]["verdict_file"] == "queue.verdict_output"
    assert bundle["semantic_review_checklist"]


def test_agent_review_queue_includes_protocol_focus_for_path_and_feature_gap(tmp_path):
    code_root = tmp_path / "f-stack"
    design_root = tmp_path / "Difference"
    work = tmp_path / ".agent-work"
    log_root = tmp_path / "logs"
    code_root.mkdir()
    _write(design_root / "benchmark.md", "RFC 2710 and RFC 8415 are in scope.\n")
    _write(
        code_root / "lib" / "ff_dpdk_if.c",
        "static int\nmld_protocol_filter(const void *data, int len)\n{\n    return FILTER_MULTI;\n}\n",
    )
    _write(
        work / "benchmark_index.json",
        json.dumps({"rfcs": [{"rfc": "RFC2710"}, {"rfc": "RFC8415"}]}),
    )
    _write(
        work / "rfc_requirements.json",
        json.dumps({"requirements": [
            {
                "requirement_id": "RFC2710-4-MAY-001",
                "rfc": "RFC2710",
                "section": "4",
                "normative_level": "MAY",
                "title": "Multicast behavior",
                "requirement_text": "A listener MAY use multicast listener discovery for this behavior.",
            },
            {
                "requirement_id": "RFC8415-18-MUST-001",
                "rfc": "RFC8415",
                "section": "18",
                "normative_level": "MUST",
                "title": "Client behavior",
                "requirement_text": "A DHCPv6 client MUST process configuration replies.",
            },
        ]}),
    )
    _write(work / "candidate_issues.json", json.dumps({"candidates": []}))
    _write(
        work / "code_index.json",
        json.dumps({"files": [{
            "file": "lib/ff_dpdk_if.c",
            "symbols": [{
                "name": "mld_protocol_filter",
                "line_start": 1,
                "line_end": 5,
                "signature": "static int mld_protocol_filter(const void *data, int len)",
                "snippet": "static int\nmld_protocol_filter(const void *data, int len)\n{\n    return FILTER_MULTI;\n}\n",
            }],
        }]}),
    )

    rc = agent_review_bundle_builder.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(design_root),
            "--benchmark", str(design_root / "benchmark.md"),
            "--log-root", str(log_root),
        ]
    )

    queue = json.loads((work / "agent_review_queue.json").read_text(encoding="utf-8"))
    assert queue["protocol_domain_queued_count"] == 2
    assert all(item["item_type"] == "protocol_domain_review" for item in queue["items"][:2])
    assert queue["items"][0]["rfc"] == "RFC8415"
    focus = {item["rfc"]: item for item in queue["protocol_domain_focus"]}
    mld_contexts = focus["RFC2710"]["code_path_contexts"]
    assert rc == 0
    assert any(ctx["file"] == "lib/ff_dpdk_if.c" and "FILTER_MULTI" in ctx["snippet"] for ctx in mld_contexts)
    assert focus["RFC2710"]["notable_requirements"][0]["requirement_id"] == "RFC2710-4-MAY-001"
    assert focus["RFC8415"]["notable_requirements"][0]["requirement_id"] == "RFC8415-18-MUST-001"
    assert focus["RFC8415"]["strong_identifier_hits"] == []
    assert focus["RFC8415"]["feature_gap_probe"].startswith("No strong file/symbol identifier hit")
    assert focus["RFC8415"]["priority_score"] > focus["RFC2710"]["priority_score"]
    assert "possible protocol/feature gap" in focus["RFC8415"]["priority_reasons"]


def test_issue_ranker_outputs_confirmed_and_queues_probable(tmp_path):
    code_root = tmp_path / "f-stack"
    work = tmp_path / ".agent-work"
    code_root.mkdir()
    _write(
        work / "validated_issues.json",
        json.dumps(
            {
                "issues": [
                    _issue("probable", 1),
                    _issue("confirmed", 2),
                    {**_issue("rejected", 3), "status": "rejected", "confidence": 0.3},
                ]
            }
        ),
    )

    rc = issue_ranker.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
        ]
    )

    assert rc == 0
    ranked = json.loads((work / "ranked_issues.json").read_text(encoding="utf-8"))
    review = json.loads((work / "probable_review_queue.json").read_text(encoding="utf-8"))
    assert ranked["kept"] == 1
    assert ranked["probable_queued"] == 1
    assert [issue["status"] for issue in ranked["issues"]] == ["confirmed"]
    assert review["probable"] == 1
    assert review["issues"][0]["status"] == "probable"
    assert review["issues"][0]["review_id"] == "REVIEW-001"


def test_final_gate_rejects_probable_issue_in_main_result(tmp_path):
    result_root = tmp_path / "result"
    log_root = tmp_path / "logs"
    issues = [_issue("confirmed", 1), _issue("confirmed", 2), _issue("confirmed", 3), _issue("probable", 4)]
    _write(
        result_root / "issues.json",
        json.dumps(_result_doc(issues)),
    )
    _write(result_root / "issues.jsonl", "\n".join(json.dumps(i) for i in issues) + "\n")
    _write(result_root / "00-summary.md", "# summary\n")
    _write(result_root / "01-issue.md", "# issue\n")

    rc = final_detection_gate.main(
        [
            "--code-root", str(tmp_path / "f-stack"),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
            "--result-root", str(result_root),
            "--log-root", str(log_root),
        ]
    )

    verdict = json.loads((log_root / "trace" / "final_detection_gate.json").read_text(encoding="utf-8"))
    assert rc == 1
    assert verdict["checks"]["only_confirmed_in_main"] is False
    assert any("non-confirmed issue leaked" in problem for problem in verdict["problems"])


def test_final_gate_rejects_confirmed_issue_without_agent_review(tmp_path):
    result_root = tmp_path / "result"
    log_root = tmp_path / "logs"
    issues = [{k: v for k, v in _issue("confirmed", i).items() if k != "agent_review"} for i in range(1, 5)]
    _write(
        result_root / "issues.json",
        json.dumps(_result_doc(issues)),
    )
    _write(result_root / "issues.jsonl", "\n".join(json.dumps(i) for i in issues) + "\n")
    _write(result_root / "00-summary.md", "# summary\n")
    _write(result_root / "01-issue.md", "# issue\n")

    rc = final_detection_gate.main(
        [
            "--code-root", str(tmp_path / "f-stack"),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
            "--result-root", str(result_root),
            "--log-root", str(log_root),
        ]
    )

    verdict = json.loads((log_root / "trace" / "final_detection_gate.json").read_text(encoding="utf-8"))
    assert rc == 1
    assert any("agent_review" in problem for problem in verdict["problems"])
    assert any("missing opencode agent_review source" in problem for problem in verdict["problems"])


def test_final_gate_rejects_missing_result_schema_fields(tmp_path):
    result_root = tmp_path / "result"
    log_root = tmp_path / "logs"
    issues = [_issue("confirmed", i) for i in range(1, 5)]
    _write(
        result_root / "issues.json",
        json.dumps({"issues": issues}),
    )
    _write(result_root / "issues.jsonl", "\n".join(json.dumps(i) for i in issues) + "\n")
    _write(result_root / "00-summary.md", "# summary\n")
    _write(result_root / "01-issue.md", "# issue\n")

    rc = final_detection_gate.main(
        [
            "--code-root", str(tmp_path / "f-stack"),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
            "--result-root", str(result_root),
            "--log-root", str(log_root),
        ]
    )

    verdict = json.loads((log_root / "trace" / "final_detection_gate.json").read_text(encoding="utf-8"))
    assert rc == 1
    assert any("missing/empty root field 'summary'" in problem for problem in verdict["problems"])


def test_final_gate_accepts_four_confirmed_issues(tmp_path):
    result_root = tmp_path / "result"
    log_root = tmp_path / "logs"
    issues = [_issue("confirmed", i) for i in range(1, 5)]
    _write(
        result_root / "issues.json",
        json.dumps(_result_doc(issues)),
    )
    _write(result_root / "issues.jsonl", "\n".join(json.dumps(i) for i in issues) + "\n")
    _write(result_root / "00-summary.md", "# summary\n")
    _write(result_root / "01-issue.md", "# issue\n")

    rc = final_detection_gate.main(
        [
            "--code-root", str(tmp_path / "f-stack"),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
            "--result-root", str(result_root),
            "--log-root", str(log_root),
        ]
    )

    verdict = json.loads((log_root / "trace" / "final_detection_gate.json").read_text(encoding="utf-8"))
    assert rc == 0
    assert verdict["passed"] is True
    assert verdict["checks"]["min_4_confirmed"] is True


def test_public_fstack_gold_evaluator_matches_known_fixture_without_pipeline_dependency(tmp_path):
    gold = json.loads((public_fstack_gold_evaluator.DEFAULT_GOLD).read_text(encoding="utf-8"))
    issues = [
        _gold_result_issue(
            1,
            "ND 10 option limit",
            "RFC4861",
            "6.3.4",
            "freebsd/netinet6/nd6.c",
            "RFC4861 section 6.3.4 requires processing options, but nd6_options uses a maxndopt 10 option limit.",
        ),
        _gold_result_issue(
            2,
            "Proxy NA no random delay",
            "RFC4861",
            "7.2.8",
            "freebsd/netinet6/nd6_nbr.c",
            "Proxy Neighbor Advertisement NA is sent without the required random delay.",
        ),
        _gold_result_issue(
            3,
            "Fragment extension chain not walked",
            "RFC8200",
            "4.5",
            "dpdk/lib/ip_frag/rte_ip_frag.h",
            "IPv6 fragment handling only checks the next extension header and does not walk the full chain.",
        ),
        _gold_result_issue(
            4,
            "MLD misrouted via KNI",
            "RFC2710",
            "4",
            "lib/ff_dpdk_if.c",
            "MLD multicast listener packets are routed through KNI because protocol_filter returns FILTER_MULTI.",
        ),
        _issue("confirmed", 5),
        _issue("confirmed", 6),
        _issue("confirmed", 7),
        _issue("confirmed", 8),
    ]

    report = public_fstack_gold_evaluator.evaluate(_result_doc(issues), gold)

    assert report["pass"] is True
    assert report["matched_gold_count"] >= 4
    assert report["extra_confirmed_rate"] == 0.5

    for phase_scripts in rfc_goal_runner.PHASE_SCRIPTS.values():
        assert "public_fstack_gold_evaluator.py" not in phase_scripts
    for script in [
        "protocol_inconsistency_detector.py",
        "requirement_code_mapper.py",
        "evidence_validator.py",
        "issue_ranker.py",
        "issue_report_writer.py",
    ]:
        text = (SCRIPT_DIR / script).read_text(encoding="utf-8")
        assert "public_fstack_gold" not in text


def test_opencode_verdicts_flow_through_review_report_and_gate(tmp_path):
    code_root = tmp_path / "target-code"
    design_root = tmp_path / "design"
    result_root = tmp_path / "result"
    log_root = tmp_path / "logs"
    work = tmp_path / ".agent-work"
    code_root.mkdir()
    _write(design_root / "design.md", "# Design\n")
    _write(work / "candidate_issues.json", json.dumps({"candidates": []}))
    _write(
        work / "agent_review_verdicts.jsonl",
        "\n".join(json.dumps(_agent_discovered_verdict(i)) for i in range(1, 5)) + "\n",
    )
    _write(result_root / "99-stale.md", "# stale\n")

    common_args = [
        "--code-root", str(code_root),
        "--design-root", str(design_root),
        "--benchmark", str(design_root / "design.md"),
        "--result-root", str(result_root),
        "--log-root", str(log_root),
    ]

    assert evidence_validator.main(common_args) == 0
    assert issue_ranker.main(common_args) == 0
    assert issue_report_writer.main(common_args) == 0
    assert final_detection_gate.main(common_args) == 0

    result = json.loads((result_root / "issues.json").read_text(encoding="utf-8"))
    gate = json.loads((log_root / "trace" / "final_detection_gate.json").read_text(encoding="utf-8"))
    assert result["summary"]["confirmed"] == 4
    assert all(issue["agent_review"]["source"] == "opencode" for issue in result["issues"])
    assert gate["passed"] is True
    assert not (result_root / "99-stale.md").exists()
