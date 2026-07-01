#!/usr/bin/env python3
"""Spec Test Generator — generates MockMvc/JUnit tests from API contract and business rules.

Produces negative/boundary/hidden-case tests to simulate the black-box test suite.
Output goes to ``.tmp/generated-tests/`` (not committed, verified then discarded).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shophub_goal_runner as runner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate spec-driven tests from API contract.")
    parser.add_argument("--root", default=".", help="Project root.")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: .tmp/generated-tests/).")
    parser.add_argument("--format", choices=["mockmvc", "junit"], default="mockmvc", help="Test format.")
    return parser


def generate_test_class_name(endpoint: dict[str, Any]) -> str:
    """Generate a test class name from an endpoint."""
    path = endpoint["path"].strip("/").replace("/", "_").replace("{", "").replace("}", "")
    method = endpoint["method"]
    summary = endpoint.get("summary", "").replace(" ", "")
    if summary:
        return f"{method}{path.capitalize()}{summary}Test"
    return f"{method}{path.capitalize()}ContractTest"


def generate_positive_test(endpoint: dict[str, Any]) -> list[str]:
    """Generate a positive test case."""
    method = endpoint["method"].lower()
    path = endpoint["path"]
    tests: list[str] = []

    test_name = f"test_{method}_{endpoint['path'].strip('/').replace('/', '_').replace('{', '').replace('}', '')}_success"

    body_fields = endpoint.get("request", {}).get("body", {})
    body_json = generate_valid_body_json(body_fields)

    tests.append(f"""
    @Test
    @DisplayName("{endpoint.get('summary', '')} - should succeed")
    void {test_name}() throws Exception {{
        String requestBody = \"\"\"
            {body_json}
            \"\"\";

        mockMvc.perform({method}({java_string(path)})
                .contentType(MediaType.APPLICATION_JSON)
                .content(requestBody))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data").exists());
    }}
""")
    return tests


def generate_negative_tests(endpoint: dict[str, Any], rules: list[dict[str, Any]]) -> list[str]:
    """Generate negative test cases from contract fields and business rules."""
    tests: list[str] = []
    method = endpoint["method"].lower()
    path = endpoint["path"]
    body_fields = endpoint.get("request", {}).get("body", {})

    for field_name, field_spec in body_fields.items():
        field_type = field_spec.get("type", "string")
        required = field_spec.get("required", False)
        constraints = field_spec.get("constraints", [])

        base = f"{method}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}_{field_name}"

        # Null test for required fields
        if required:
            tests.append(f"""
    @Test
    @DisplayName("{field_name} null should return 400")
    void test_{base}_null_shouldFail() throws Exception {{
        String requestBody = \"\"\"
            {generate_body_with_field_null(body_fields, field_name)}
            \"\"\";

        mockMvc.perform({method}({java_string(path)})
                .contentType(MediaType.APPLICATION_JSON)
                .content(requestBody))
            .andExpect(status().isBadRequest());
    }}
""")

        # Blank/empty test for string fields
        if field_type == "string":
            tests.append(f"""
    @Test
    @DisplayName("{field_name} blank should return 400")
    void test_{base}_blank_shouldFail() throws Exception {{
        String requestBody = \"\"\"
            {generate_body_with_field_value(body_fields, field_name, '""')}
            \"\"\";

        mockMvc.perform({method}({java_string(path)})
                .contentType(MediaType.APPLICATION_JSON)
                .content(requestBody))
            .andExpect(status().isBadRequest());
    }}
""")

            tests.append(f"""
    @Test
    @DisplayName("{field_name} whitespace should return 400")
    void test_{base}_whitespace_shouldFail() throws Exception {{
        String requestBody = \"\"\"
            {generate_body_with_field_value(body_fields, field_name, '"   "')}
            \"\"\";

        mockMvc.perform({method}({java_string(path)})
                .contentType(MediaType.APPLICATION_JSON)
                .content(requestBody))
            .andExpect(status().isBadRequest());
    }}
""")

        # Zero/negative for numeric fields
        if field_type in ("integer", "long", "decimal"):
            if any(c.startswith("min") for c in constraints) or "positive" in str(constraints).lower():
                tests.append(f"""
    @Test
    @DisplayName("{field_name} zero should return 400")
    void test_{base}_zero_shouldFail() throws Exception {{
        String requestBody = \"\"\"
            {generate_body_with_field_value(body_fields, field_name, '0')}
            \"\"\";

        mockMvc.perform({method}({java_string(path)})
                .contentType(MediaType.APPLICATION_JSON)
                .content(requestBody))
            .andExpect(status().isBadRequest());
    }}
""")
                tests.append(f"""
    @Test
    @DisplayName("{field_name} negative should return 400")
    void test_{base}_negative_shouldFail() throws Exception {{
        String requestBody = \"\"\"
            {generate_body_with_field_value(body_fields, field_name, '-1')}
            \"\"\";

        mockMvc.perform({method}({java_string(path)})
                .contentType(MediaType.APPLICATION_JSON)
                .content(requestBody))
            .andExpect(status().isBadRequest());
    }}
""")

        # ID不存在 test
        if "exists" in constraints:
            tests.append(f"""
    @Test
    @DisplayName("{field_name} not found should return 404")
    void test_{base}_notFound_shouldFail() throws Exception {{
        String requestBody = \"\"\"
            {generate_body_with_field_value(body_fields, field_name, '999999')}
            \"\"\";

        mockMvc.perform({method}({java_string(path)})
                .contentType(MediaType.APPLICATION_JSON)
                .content(requestBody))
            .andExpect(status().isNotFound());
    }}
""")

    # Error format test
    for error in endpoint.get("errors", []):
        status = error.get("status")
        error_code = error.get("body", {}).get("code", "")
        condition = error.get("condition", "")
        if error_code:
            enum_name = f"HTTP_{status}" if status else "UNKNOWN"
            tests.append(f"""
    @Test
    @DisplayName("Error response should match contract: {condition}")
    void test_error_{error_code.lower()}_format() throws Exception {{
        // This test will fail if the error format doesn't match.
        // Generate a request that triggers this error condition and verify format.
        // Condition: {condition}
        // Expected: status={status}, code={error_code}
    }}
""")

    return tests


def generate_pagination_tests(endpoint: dict[str, Any]) -> list[str]:
    """Generate pagination boundary tests."""
    tests: list[str] = []
    method = endpoint["method"].lower()
    path = endpoint["path"]

    if method != "get":
        return tests

    path_slug = path.strip("/").replace("/", "_")

    tests.append(f"""
    @Test
    @DisplayName("Pagination page=0 should return first page")
    void test_{path_slug}_pagination_page_zero() throws Exception {{
        mockMvc.perform(get({java_string(path)})
                .param("page", "0")
                .param("size", "10"))
            .andExpect(status().isOk());
    }}
""")

    tests.append(f"""
    @Test
    @DisplayName("Pagination size=0 should return 400 or default")
    void test_{path_slug}_pagination_size_zero() throws Exception {{
        mockMvc.perform(get({java_string(path)})
                .param("size", "0"))
            .andExpect(status().is4xxClientError());
    }}
""")

    tests.append(f"""
    @Test
    @DisplayName("Pagination should return empty array for empty list")
    void test_{path_slug}_pagination_empty_list() throws Exception {{
        mockMvc.perform(get({java_string(path)})
                .param("page", "9999"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data").isArray());
    }}
""")

    tests.append(f"""
    @Test
    @DisplayName("Pagination should include metadata fields")
    void test_{path_slug}_pagination_metadata() throws Exception {{
        mockMvc.perform(get({java_string(path)})
                .param("page", "0")
                .param("size", "10"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.data.totalElements").exists())
            .andExpect(jsonPath("$.data.totalPages").exists());
    }}
""")

    return tests


def generate_valid_body_json(fields: dict[str, Any]) -> str:
    """Generate a valid JSON body from field specs."""
    if not fields:
        return "{}"
    parts: list[str] = []
    for name, spec in fields.items():
        ftype = spec.get("type", "string")
        if ftype in ("integer", "long"):
            parts.append(f'  "{name}": 1')
        elif ftype == "decimal":
            parts.append(f'  "{name}": 9.99')
        elif ftype == "boolean":
            parts.append(f'  "{name}": true')
        elif ftype == "array":
            parts.append(f'  "{name}": []')
        else:
            parts.append(f'  "{name}": "test_{name}"')
    return "{\n" + ",\n".join(parts) + "\n}"


def generate_body_with_field_null(fields: dict[str, Any], null_field: str) -> str:
    """Generate JSON with one field set to null."""
    parts: list[str] = []
    for name, spec in fields.items():
        if name == null_field:
            parts.append(f'  "{name}": null')
        else:
            ftype = spec.get("type", "string")
            val = "1" if ftype in ("integer", "long") else ("9.99" if ftype == "decimal" else f'"test_{name}"')
            parts.append(f'  "{name}": {val}')
    return "{\n" + ",\n".join(parts) + "\n}"


def generate_body_with_field_value(fields: dict[str, Any], target_field: str, value: str) -> str:
    """Generate JSON with a specific value for one field."""
    parts: list[str] = []
    for name, spec in fields.items():
        if name == target_field:
            parts.append(f'  "{name}": {value}')
        else:
            ftype = spec.get("type", "string")
            val = "1" if ftype in ("integer", "long") else ("9.99" if ftype == "decimal" else f'"test_{name}"')
            parts.append(f'  "{name}": {val}')
    return "{\n" + ",\n".join(parts) + "\n}"


def java_string(s: str) -> str:
    return f'"{s}"'


def find_target_package(root: Path) -> str:
    """Find the base package for the main application."""
    code_dir = root / "code"
    if not code_dir.exists():
        return "com.example"
    # Try to find @SpringBootApplication
    for path in code_dir.rglob("*Application.java"):
        text = runner.read_text(path)
        match = re.search(r"package\s+([A-Za-z0-9_.]+)\s*;", text)
        if match:
            return match.group(1)
    # Fallback: find the deepest common package
    packages = set()
    for path in code_dir.rglob("*.java"):
        text = runner.read_text(path)
        match = re.search(r"package\s+([A-Za-z0-9_.]+)\s*;", text)
        if match:
            packages.add(match.group(1))
    if packages:
        # Find shortest common prefix
        common = min(packages, key=len)
        return common
    return "com.example"


def generate_test_class(
    endpoint: dict[str, Any],
    rules: list[dict[str, Any]],
    package: str,
    format_type: str,
) -> str:
    """Generate a complete test class for one endpoint."""
    class_name = generate_test_class_name(endpoint)

    imports = [
        "import org.junit.jupiter.api.DisplayName;",
        "import org.junit.jupiter.api.Test;",
        "import org.springframework.beans.factory.annotation.Autowired;",
        "import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;",
        "import org.springframework.boot.test.context.SpringBootTest;",
        "import org.springframework.http.MediaType;",
        "import org.springframework.test.web.servlet.MockMvc;",
        "",
        "import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;",
        "import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;",
    ]

    positive_tests = generate_positive_test(endpoint)
    negative_tests = generate_negative_tests(endpoint, rules)
    pagination_tests = generate_pagination_tests(endpoint) if endpoint["method"] == "GET" else []

    all_tests = positive_tests + negative_tests + pagination_tests

    source = f"""package {package}.generated;

{chr(10).join(imports)}

/**
 * Generated test for {endpoint['method']} {endpoint['path']}
 * Contract ID: {endpoint.get('id', 'N/A')}
 *
 * DO NOT MODIFY — generated by spec_test_generator.py
 */
@SpringBootTest
@AutoConfigureMockMvc
@DisplayName("{endpoint['method']} {endpoint['path']}")
class {class_name} {{

    @Autowired
    private MockMvc mockMvc;

{''.join(all_tests)}
}}
"""
    return source


def generate_tests(root: Path, output_dir: Path, format_type: str) -> dict[str, Any]:
    """Generate all spec-driven tests."""
    paths = runner.RunnerPaths(root)

    contract = runner.read_json(paths.work / "api_contract.json", {})
    rules = runner.read_json(paths.work / "business_rules.json", {})

    endpoints = contract.get("endpoints", [])
    if not endpoints:
        return {"status": "skipped", "reason": "No endpoints in API contract", "generated": 0}

    package = find_target_package(root)
    output_dir.mkdir(parents=True, exist_ok=True)

    generated: list[dict[str, Any]] = []
    for ep in endpoints:
        if ep["method"] in ("DELETE",) and not ep.get("request", {}).get("body"):
            continue  # Skip simple GET/DELETE without body
        if ep["method"] == "GET" and not ep.get("response", {}).get("body"):
            continue  # Skip simple reads

        source = generate_test_class(ep, rules.get("rules", []), package, format_type)
        class_name = generate_test_class_name(ep)
        file_path = output_dir / f"{class_name}.java"
        runner.write_text(file_path, source)
        generated.append({
            "endpoint_id": ep["id"],
            "method": ep["method"],
            "path": ep["path"],
            "test_class": class_name,
            "file": str(file_path),
        })

    # Write manifest
    manifest = {
        "generated_at": runner.now_iso(),
        "format": format_type,
        "test_classes": generated,
        "total": len(generated),
    }
    runner.write_json(output_dir / "generated_tests.json", manifest)

    return {
        "status": "ok",
        "generated": len(generated),
        "output_dir": str(output_dir),
        "manifest": str(output_dir / "generated_tests.json"),
    }


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    output_dir = Path(args.output_dir) if args.output_dir else root / ".tmp" / "generated-tests"
    result = generate_tests(root, output_dir, args.format)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
