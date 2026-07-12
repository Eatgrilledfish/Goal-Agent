from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "work" / "tools" / "scripts"))

import agent_common as ac
import risk_sweep_plan_validator as validator
import scout_plan_builder as builder


def test_builder_covers_every_design_group_and_uses_non_overlapping_code_anchors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    code = tmp_path / "code"
    root.mkdir()
    (code / "lib").mkdir(parents=True)
    (code / "dpdk").mkdir()
    (code / "lib" / "main.c").write_text("int main(void) { return 0; }\n")
    (code / "dpdk" / "fast.c").write_text("int fast(void) { return 0; }\n")
    session_id = "session-test"
    ac.save_json(root / "agent_loop_state.json", {"session_id": session_id})
    planes = [
        {
            "plane_id": f"PLANE-LIB-{index}",
            "paths": ["lib/main.c"],
        }
        for index in range(8)
    ] + [{"plane_id": "PLANE-DPDK", "paths": ["dpdk/fast.c"]}]
    ac.save_json(root / "architecture_map.json", {
        "implementation_planes": planes,
        "integration_boundaries": [
            {
                "boundary_id": "BOUNDARY-LIB", "paths": ["lib"],
                "plane_ids": ["PLANE-LIB-0"],
            },
            {
                "boundary_id": "BOUNDARY-DPDK", "paths": ["dpdk"],
                "plane_ids": ["PLANE-DPDK"],
            },
        ],
        "parallel_behavior_paths": [],
    })
    ac.save_json(root / "design_inventory.json", {
        "document_groups": [
            {
                "document_key": f"doc-{index}", "scope_relation": "in_scope",
                "sections": [{
                    "section_id": f"SECTION-{index}",
                    "line_start": 1, "line_end": index + 1,
                }],
            }
            for index in range(5)
        ],
    })
    ac.save_json(root / "agent_loop_contract.json", {
        "coverage_contract": {"portfolio_lenses": ["behavior", "timing"]},
    })
    ac.save_json(root / "workspace_manifest.json", {
        "paths": {"review_code_root": str(code)},
    })

    plan = builder.build(root)

    design_slices = [
        item for item in plan["slices"] if item["direction"] == "design_to_code"
    ]
    code_slices = [
        item for item in plan["slices"] if item["direction"] == "code_to_design"
    ]
    assert {
        key for item in design_slices for key in item["document_keys"]
    } == {f"doc-{index}" for index in range(5)}
    assert len(code_slices) == 2
    assert all(not item["anchor_paths"] for item in design_slices)
    owned = [set(item["anchor_paths"]) for item in code_slices]
    assert owned[0].isdisjoint(owned[1])
    _loaded, _index, errors = validator.load_validated_plan(root)
    assert errors == []
