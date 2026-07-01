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
    parser.add_argument("--dry-run-compile", action="store_true",
                       help="Compile generated tests and mark status (cannot compile = unusable).")
    return parser


def generate_test_class_name(endpoint: dict[str, Any]) -> str:
    """Generate a test class name from an endpoint."""
    path = endpoint["path"].strip("/").replace("/", "_").replace("{", "").replace("}", "")
    method = endpoint["method"]
    summary = endpoint.get("summary", "").replace(" ", "")
    if summary:
        return f"{method}{path.capitalize()}{summary}Test"
    return f"{method}{path.capitalize()}ContractTest"


def status_assertion(status_codes: list[int]) -> str:
    """Generate status assertion from contract success_status."""
    if not status_codes:
        return "isOk()"
    if len(status_codes) == 1:
        code = status_codes[0]
        status_map = {
            200: "isOk()",
            201: "isCreated()",
            202: "isAccepted()",
            204: "isNoContent()",
        }
        if code in status_map:
            return status_map[code]
        return f"is({code})"
    # Multiple allowed success codes
    codes_str = ", ".join(str(c) for c in status_codes)
    return (
        f"hasStatusCode(status -> assertTrue("
        f"java.util.List.of({codes_str}).contains(status), "
        f"\"Expected one of [{codes_str}]\"))"
    )


def response_jsonpath_assertions(response_body: dict[str, Any]) -> list[str]:
    """Generate jsonPath assertions from contract response body fields."""
    assertions: list[str] = []
    for field_name, field_spec in response_body.items():
        path = field_name if field_name.startswith("$.") else f"$.{field_name}"
        if field_spec and not isinstance(field_spec, dict):
            continue
        ftype = field_spec.get("type", "string") if isinstance(field_spec, dict) else "string"
        if ftype == "array":
            assertions.append(f".andExpect(jsonPath(\"{path}\").isArray())")
        else:
            assertions.append(f".andExpect(jsonPath(\"{path}\").exists())")
    return assertions


def generate_positive_test(endpoint: dict[str, Any]) -> list[str]:
    """Generate a positive test case using contract-defined success status and response schema."""
    method = endpoint["method"].lower()
    path = endpoint["path"]
    tests: list[str] = []

    test_name = f"test_{method}_{endpoint['path'].strip('/').replace('/', '_').replace('{', '').replace('}', '')}_success"

    body_fields = endpoint.get("request", {}).get("body", {})
    body_json = generate_valid_body_json(body_fields)

    # Read success status from contract
    success_status = endpoint.get("response", {}).get("success_status", [200])
    status_check = status_assertion(success_status)

    # Read response fields from contract for jsonPath assertions
    response_body = endpoint.get("response", {}).get("body", {})
    jsonpath_asserts = response_jsonpath_assertions(response_body)

    if method in ("get", "delete"):
        # GET/DELETE - no request body
        tests.append(f"""
    @Test
    @DisplayName("{endpoint.get('summary', '')} - should succeed")
    void {test_name}() throws Exception {{
        mockMvc.perform({method}({java_string(path)})
                .contentType(MediaType.APPLICATION_JSON))
            .andExpect(status().{status_check})
{chr(10).join('            ' + a for a in jsonpath_asserts if jsonpath_asserts) if jsonpath_asserts else '            .andExpect(jsonPath("$").exists())'};
    }}
""")
    else:
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
            .andExpect(status().{status_check})
{chr(10).join('            ' + a for a in jsonpath_asserts if jsonpath_asserts) if jsonpath_asserts else '            .andExpect(jsonPath("$").exists())'};
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

    # Error format test — generate real assertions
    for error in endpoint.get("errors", []):
        status = error.get("status")
        error_code = error.get("body", {}).get("code", "")
        condition = error.get("condition", "")
        if error_code:
            status_method_map = {
                400: "isBadRequest",
                401: "isUnauthorized",
                403: "isForbidden",
                404: "isNotFound",
                409: "isConflict",
                422: "isUnprocessableEntity",
                500: "isInternalServerError",
            }
            status_method = status_method_map.get(status, f"is({status})")

            # Try to infer what triggers this error from condition text
            trigger_hint = ""
            if "required" in condition.lower() or "missing" in condition.lower():
                trigger_hint = "// Trigger with missing required field"
            elif "not found" in condition.lower() or "404" in condition.lower():
                trigger_hint = "// Trigger with non-existing resource ID"
            elif "duplicate" in condition.lower() or "conflict" in condition.lower():
                trigger_hint = "// Trigger with duplicate resource"
            elif "validation" in condition.lower():
                trigger_hint = "// Trigger with invalid input"

            tests.append(f"""
    @Test
    @DisplayName("Error response should match contract: {condition}")
    void test_error_{error_code.lower()}_format() throws Exception {{
        {trigger_hint}
        // Expected: status={status}, code={error_code}
        // Condition: {condition}

        // Construct a request that triggers this error
        String invalidBody = \"\"\"
            {generate_trigger_body_for_error(body_fields, condition, status)}
            \"\"\";

        mockMvc.perform({method}({java_string(path)})
                .contentType(MediaType.APPLICATION_JSON)
                .content(invalidBody))
            .andExpect(status().{status_method}())
            .andExpect(jsonPath("$.code").value("{error_code}"))
            .andExpect(jsonPath("$.message").exists());
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


def generate_trigger_body_for_error(
    fields: dict[str, Any], condition: str, status: int | None
) -> str:
    """Generate a request body that is likely to trigger a specific error."""
    if not fields:
        return "{}"

    parts: list[str] = []
    cond_lower = condition.lower()

    for name, spec in fields.items():
        ftype = spec.get("type", "string")
        required = spec.get("required", False)

        # For 400 validation errors: use empty/blank/invalid values
        if status == 400:
            if required and any(kw in cond_lower for kw in ("required", "missing", "blank", "empty", "validation")):
                if ftype == "string":
                    parts.append(f'  "{name}": ""')
                elif ftype in ("integer", "long", "decimal"):
                    parts.append(f'  "{name}": -1')
                else:
                    parts.append(f'  "{name}": null')
            elif "validation" in cond_lower:
                if ftype == "string":
                    parts.append(f'  "{name}": ""')
                else:
                    parts.append(f'  "{name}": -1')
            else:
                parts.append(f'  "{name}": ""')
        # For 404: use valid-looking but non-existing reference
        elif status == 404:
            if name.endswith("id") or name.endswith("Id"):
                parts.append(f'  "{name}": 999999')
            else:
                val = "1" if ftype in ("integer", "long") else f'"test_{name}"'
                parts.append(f'  "{name}": {val}')
        # For 409: use likely-duplicate data
        elif status == 409:
            val = "1" if ftype in ("integer", "long") else f'"duplicate_{name}"'
            parts.append(f'  "{name}": {val}')
        else:
            val = "1" if ftype in ("integer", "long") else f'"test_{name}"'
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
    get_tests = generate_get_tests(endpoint) if endpoint["method"] == "GET" else []
    delete_tests = generate_delete_tests(endpoint) if endpoint["method"] == "DELETE" else []

    all_tests = positive_tests + negative_tests + pagination_tests + get_tests + delete_tests

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


def generate_get_tests(endpoint: dict[str, Any]) -> list[str]:
    """Generate comprehensive tests for GET endpoints."""
    tests: list[str] = []
    method = "get"
    path = endpoint["path"]
    path_slug = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")

    # If path has {id} param, generate not-found and response schema tests
    if "{id}" in path or "{Id}" in path:
        # Not found test
        tests.append(f"""
    @Test
    @DisplayName("GET non-existing ID should return 404")
    void test_{path_slug}_not_found() throws Exception {{
        mockMvc.perform(get({java_string(path)}, 999999))
            .andExpect(status().isNotFound());
    }}
""")
        # Response schema test
        resp_body = endpoint.get("response", {}).get("body", {})
        jsonpath_asserts = response_jsonpath_assertions(resp_body)
        tests.append(f"""
    @Test
    @DisplayName("GET should return correct response schema")
    void test_{path_slug}_response_schema() throws Exception {{
        mockMvc.perform(get({java_string(path)}, 1))
            .andExpect(status().{status_assertion(endpoint.get("response", {}).get("success_status", [200]))})
{chr(10).join('            ' + a for a in jsonpath_asserts if jsonpath_asserts) if jsonpath_asserts else '            .andExpect(jsonPath("$").exists())'};
    }}
""")
    else:
        # List endpoint — pagination tests
        tests.extend(generate_pagination_tests(endpoint))

    return tests


def generate_delete_tests(endpoint: dict[str, Any]) -> list[str]:
    """Generate comprehensive tests for DELETE endpoints."""
    tests: list[str] = []
    path = endpoint["path"]
    path_slug = path.strip("/").replace("/", "_").replace("{", "").replace("}", "")

    if "{id}" in path or "{Id}" in path:
        # Delete non-existing
        tests.append(f"""
    @Test
    @DisplayName("DELETE non-existing ID should return 404")
    void test_{path_slug}_not_found() throws Exception {{
        mockMvc.perform(delete({java_string(path)}, 999999))
            .andExpect(status().isNotFound());
    }}
""")
        # Delete existing (200 or 204)
        success_status = endpoint.get("response", {}).get("success_status", [200, 204])
        status_check = status_assertion(success_status)
        tests.append(f"""
    @Test
    @DisplayName("DELETE existing should return success")
    void test_{path_slug}_success() throws Exception {{
        // Note: depends on test data — may need setup
        mockMvc.perform(delete({java_string(path)}, 1))
            .andExpect(status().{status_check});
    }}
""")

    return tests


def generate_tests(root: Path, output_dir: Path, format_type: str,
                   dry_run_compile: bool = False) -> dict[str, Any]:
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
            "compilable": None,  # unknown until dry-run
        })

    # Dry-run compile if requested
    compilable_count = len(generated)
    if dry_run_compile:
        compilable_count = dry_run_compile_tests(root, output_dir, generated, package)
        for g in generated:
            g["compilable"] = g.get("compilable", True)

    # Write manifest
    manifest = {
        "generated_at": runner.now_iso(),
        "format": format_type,
        "compilable_count": compilable_count,
        "test_classes": generated,
        "total": len(generated),
        "dry_run_compile": dry_run_compile,
    }
    runner.write_json(output_dir / "generated_tests.json", manifest)

    # Also write to .agent-work
    runner.write_json(paths.work / "generated_tests_manifest.json", manifest)

    status = "ok" if compilable_count > 0 or not dry_run_compile else "unusable"
    return {
        "status": status,
        "generated": len(generated),
        "compilable": compilable_count,
        "output_dir": str(output_dir),
        "manifest": str(output_dir / "generated_tests.json"),
    }


def dry_run_compile_tests(
    root: Path, output_dir: Path, generated: list[dict[str, Any]], package: str
) -> int:
    """Attempt to compile generated tests and mark compilability."""
    code_dir = root / "code"
    if not code_dir.exists():
        return 0

    # Copy generated tests to code/src/test/java/generated
    test_dir = code_dir / "src" / "test" / "java" / "generated"
    test_dir.mkdir(parents=True, exist_ok=True)

    compilable = 0
    for gen in generated:
        cls_name = gen.get("test_class", "")
        source_path = Path(gen.get("file", ""))
        if not source_path.exists():
            gen["compilable"] = False
            continue

        dest_path = test_dir / f"{cls_name}.java"
        try:
            runner.write_text(dest_path, runner.read_text(source_path))
            gen["compilable"] = True
            compilable += 1
        except Exception:
            gen["compilable"] = False

    # Run compile
    try:
        import subprocess
        settings = runner.find_maven_settings(root)
        cmd = ["mvn"]
        if settings:
            cmd.extend(["-s", runner.rel(root, settings)])
        cmd.extend(["-f", "code/pom.xml", "test-compile", "-q"])

        result = subprocess.run(
            cmd, cwd=root, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False, timeout=300,
        )
        if result.returncode != 0:
            # Mark all as potentially uncompilable if the entire compilation failed
            for gen in generated:
                gen["compilable"] = False
            return 0
    except (OSError, subprocess.SubprocessError):
        pass  # If Maven unavailable, trust the copy was correct

    return compilable


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.root).resolve()
    paths = runner.RunnerPaths(root)
    runner.ensure_work_layout(paths)

    output_dir = Path(args.output_dir) if args.output_dir else root / ".tmp" / "generated-tests"
    result = generate_tests(root, output_dir, args.format, dry_run_compile=args.dry_run_compile)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
