from collections import Counter
from pathlib import Path, PurePosixPath
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "work" / "tools" / "scripts"))

import agent_common as ac
import risk_sweep_plan_validator as validator
import scout_plan_builder as builder


def _write_builder_state(
    root: Path,
    code: Path,
    *,
    planes: list[dict],
    inventory: dict,
    boundaries: list[dict] | None = None,
) -> None:
    session_id = "session-partition-test"
    ac.save_json(root / "agent_loop_state.json", {"session_id": session_id})
    ac.save_json(root / "architecture_map.json", {
        "implementation_planes": planes,
        "integration_boundaries": boundaries or [],
        "parallel_behavior_paths": [],
        "test_surfaces": [],
    })
    ac.save_json(root / "design_inventory.json", inventory)
    ac.save_json(root / "agent_loop_contract.json", {
        "coverage_contract": {"portfolio_lenses": ["behavior", "timing"]},
    })
    ac.save_json(root / "workspace_manifest.json", {
        "paths": {"review_code_root": str(code)},
    })


def _owned_regular_files(code: Path, anchors: list[str]) -> set[str]:
    owned: set[str] = set()
    for anchor in anchors:
        path = code / PurePosixPath(anchor)
        if path.is_file() and not path.is_symlink():
            owned.add(path.relative_to(code).as_posix())
        elif path.is_dir() and not path.is_symlink():
            owned.update(
                item.relative_to(code).as_posix()
                for item in path.rglob("*")
                if item.is_file() and not item.is_symlink()
            )
    return owned


def test_builder_covers_every_design_group_and_uses_non_overlapping_code_anchors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    code = tmp_path / "code"
    root.mkdir()
    (code / "lib").mkdir(parents=True)
    (code / "dpdk").mkdir()
    (code / "tests").mkdir()
    (code / "lib" / "main.c").write_text("int main(void) { return 0; }\n")
    (code / "dpdk" / "fast.c").write_text("int fast(void) { return 0; }\n")
    (code / "tests" / "test_fast.c").write_text("int test_fast(void) { return 0; }\n")
    session_id = "session-test"
    ac.save_json(root / "agent_loop_state.json", {"session_id": session_id})
    planes = [
        {
            "plane_id": f"PLANE-LIB-{index}",
            "paths": ["lib/main.c"],
        }
        for index in range(8)
    ] + [
        {"plane_id": "PLANE-DPDK", "paths": ["dpdk/fast.c"]},
        {"plane_id": "PLANE-TEST", "kind": "test", "paths": ["tests"]},
    ]
    ac.save_json(root / "architecture_map.json", {
        "implementation_planes": planes,
        "integration_boundaries": [
            {
                "boundary_id": "BOUNDARY-LIB", "paths": ["lib"],
                # Deliberately linked to another plane: local boundary code
                # ownership must win over model-authored navigation metadata.
                "plane_ids": ["PLANE-DPDK"],
            },
            {
                "boundary_id": "BOUNDARY-DPDK", "paths": ["dpdk"],
                "plane_ids": ["PLANE-DPDK"],
            },
        ],
        "parallel_behavior_paths": [],
        "test_surfaces": [{"path": "tests"}],
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
        ] + [{
            "document_key": "doc-large", "scope_relation": "in_scope",
            "sections": [
                {
                    "section_id": f"SECTION-LARGE-{index:02d}",
                    "line_start": index * 300 + 1,
                    "line_end": (index + 1) * 300,
                }
                for index in range(11)
            ],
        }],
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
    } == {*(f"doc-{index}" for index in range(5)), "doc-large"}
    large_slices = [
        item for item in design_slices if "doc-large" in item["document_keys"]
    ]
    assert len(large_slices) == 1
    assert {
        section_id for section_id in large_slices[0]["section_ids"]
        if section_id.startswith("SECTION-LARGE-")
    } == {f"SECTION-LARGE-{index:02d}" for index in range(11)}
    assert {
        section_id for item in design_slices for section_id in item["section_ids"]
    } == {
        *(f"SECTION-{index}" for index in range(5)),
        *(f"SECTION-LARGE-{index:02d}" for index in range(11)),
    }
    assert all(
        len(item["document_keys"]) <= builder.MAX_DOCUMENTS_PER_DESIGN_SCOUT
        for item in design_slices
    )
    assert len(code_slices) == 3
    assert all(not item["anchor_paths"] for item in design_slices)
    owned = [set(item["anchor_paths"]) for item in code_slices]
    assert owned[0].isdisjoint(owned[1])
    assert any("tests" in item["anchor_paths"] for item in code_slices)
    assert "PLANE-TEST" in plan["required_coverage"]["plane_ids"]
    lib_slice = next(
        item for item in code_slices
        if any(
            path == "lib" or path.startswith("lib/")
            for path in item["anchor_paths"]
        )
    )
    assert "BOUNDARY-LIB" in lib_slice["architecture_boundaries"]
    _loaded, _index, errors = validator.load_validated_plan(root)
    assert errors == []


def test_builder_partitions_one_broad_plane_by_real_file_count(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    code = tmp_path / "code"
    root.mkdir()
    for directory, count in (("left", 700), ("right", 700), ("shared", 101)):
        target = code / "broad" / directory
        target.mkdir(parents=True)
        for index in range(count):
            (target / f"unit_{index:04d}.c").write_text("int value;\n")
    _write_builder_state(
        root,
        code,
        planes=[
            {"plane_id": "PLANE-BROAD", "paths": ["broad"]},
            {"plane_id": "PLANE-LEFT", "paths": ["broad/left"]},
        ],
        inventory={
            "document_groups": [{
                "document_key": "design",
                "scope_relation": "required",
                "sections": [{
                    "section_id": "SECTION-ONLY",
                    "path": "design.md",
                    "line_start": 1,
                    "line_end": 10,
                }],
            }],
        },
    )

    plan = builder.build(root)
    code_slices = [
        item for item in plan["slices"] if item["direction"] == "code_to_design"
    ]
    broad_slices = [
        item for item in code_slices
        if "PLANE-BROAD" in item["implementation_planes"]
    ]

    assert len(broad_slices) >= 2
    assert sum(
        len(_owned_regular_files(code, item["anchor_paths"]))
        for item in code_slices
    ) == 1501
    assert all(
        len(_owned_regular_files(code, item["anchor_paths"]))
        <= builder.MAX_CODE_SLICE_FILES
        for item in code_slices
    )
    for index, left in enumerate(code_slices):
        for right in code_slices[index + 1:]:
            assert all(
                not builder._path_overlap(left_anchor, right_anchor)
                for left_anchor in left["anchor_paths"]
                for right_anchor in right["anchor_paths"]
            )
    _loaded, index, errors = validator.load_validated_plan(
        root, check_code_file_counts=True,
    )
    assert errors == []
    assert set(index["slices"]) == {
        item["sweep_id"] for item in plan["slices"]
    }


def test_builder_splits_large_design_documents_without_duplicate_sections(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    code = tmp_path / "code"
    root.mkdir()
    code.mkdir()
    (code / "runtime.c").write_text("int runtime(void) { return 0; }\n")
    large_sections = [
        {
            "section_id": f"LARGE-{index:02d}",
            "path": "large.md",
            "line_start": index * 700 + 1,
            "line_end": (index + 1) * 700,
        }
        for index in range(11)
    ]
    small_groups = [
        {
            "document_key": f"small-{index}",
            "scope_relation": "in_scope",
            "sections": [{
                "section_id": f"SMALL-{index}",
                "path": f"small-{index}.md",
                "line_start": 1,
                "line_end": 100,
            }],
        }
        for index in range(3)
    ]
    inventory = {
        "document_groups": [{
            "document_key": "large",
            "scope_relation": "required",
            "sections": large_sections,
        }, *small_groups],
    }
    _write_builder_state(
        root,
        code,
        planes=[{"plane_id": "PLANE-RUNTIME", "paths": ["runtime.c"]}],
        inventory=inventory,
    )

    plan = builder.build(root)
    design_slices = [
        item for item in plan["slices"] if item["direction"] == "design_to_code"
    ]
    section_spans = {
        section["section_id"]: section["line_end"] - section["line_start"] + 1
        for group in inventory["document_groups"]
        for section in group["sections"]
    }
    occurrences = Counter(
        section_id
        for item in design_slices
        for section_id in item["section_ids"]
    )

    assert len([item for item in design_slices if "large" in item["document_keys"]]) == 3
    assert set(occurrences) == set(section_spans)
    assert set(occurrences.values()) == {1}
    assert all(
        sum(section_spans[section_id] for section_id in item["section_ids"])
        <= builder.MAX_DESIGN_SLICE_LINES
        for item in design_slices
    )
    assert all(
        len(item["document_keys"]) <= builder.MAX_DOCUMENTS_PER_DESIGN_SCOUT
        for item in design_slices
    )
    large_order = {section["section_id"]: index for index, section in enumerate(large_sections)}
    for item in design_slices:
        positions = [
            large_order[section_id]
            for section_id in item["section_ids"]
            if section_id in large_order
        ]
        assert not positions or positions == list(range(positions[0], positions[-1] + 1))
    _loaded, _index, errors = validator.load_validated_plan(root)
    assert errors == []


def test_builder_preserves_boundary_only_code_scope(
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    code = tmp_path / "code"
    root.mkdir()
    (code / "entry").mkdir(parents=True)
    (code / "core").mkdir()
    (code / "entry" / "dispatch.c").write_text("int dispatch;\n")
    (code / "core" / "handler.c").write_text("int handler;\n")
    _write_builder_state(
        root,
        code,
        planes=[{"plane_id": "PLANE-CORE", "paths": ["core"]}],
        boundaries=[{
            "boundary_id": "BOUNDARY-ENTRY",
            "paths": ["entry"],
            "plane_ids": ["PLANE-CORE"],
        }],
        inventory={
            "document_groups": [{
                "document_key": "design",
                "scope_relation": "required",
                "sections": [{
                    "section_id": "SECTION-ONLY",
                    "path": "design.md",
                    "line_start": 1,
                    "line_end": 10,
                }],
            }],
        },
    )

    plan = builder.build(root)
    code_slices = [
        item for item in plan["slices"] if item["direction"] == "code_to_design"
    ]
    entry_slice = next(
        item for item in code_slices if item["anchor_paths"] == ["entry"]
    )
    core_slice = next(
        item for item in code_slices if item["anchor_paths"] == ["core"]
    )

    assert entry_slice["architecture_boundaries"] == ["BOUNDARY-ENTRY"]
    assert entry_slice["implementation_planes"] == []
    assert core_slice["implementation_planes"] == ["PLANE-CORE"]
    _loaded, _index, errors = validator.load_validated_plan(
        root, check_code_file_counts=True,
    )
    assert errors == []
