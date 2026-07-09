import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import c_code_indexer


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _symbols_by_name(record: dict) -> dict[str, dict]:
    return {sym["name"]: sym for sym in record["symbols"]}


def test_indexes_freebsd_knf_return_type_split_from_function_name(tmp_path):
    code_root = tmp_path / "f-stack"
    source = code_root / "freebsd" / "netinet6" / "ip6_forward.c"
    _write(
        source,
        """struct mbuf*
ip6_tryforward(struct mbuf *m)
{
if (m == 0)
    return 0;
return m;
}
""",
    )

    record = c_code_indexer.index_file(source, code_root)
    symbols = _symbols_by_name(record)

    assert "ip6_tryforward" in symbols
    assert symbols["ip6_tryforward"]["kind"] == "function"
    assert symbols["ip6_tryforward"]["line_start"] == 1
    assert symbols["ip6_tryforward"]["line_end"] == 7
    assert symbols["ip6_tryforward"]["signature"] == "struct mbuf* ip6_tryforward(struct mbuf *m)"


def test_indexes_multiline_parameters_and_ignores_multiline_prototype(tmp_path):
    code_root = tmp_path / "f-stack"
    source = code_root / "freebsd" / "netinet6" / "nd6.c"
    _write(
        source,
        """int
not_a_definition(struct mbuf *m);

void
nd6_na_output(struct ifnet *ifp, const struct in6_addr *daddr6_0,
    const struct in6_addr *taddr6, int flags,
    struct sockaddr_dl *tlladdr)
{
    return;
}
""",
    )

    record = c_code_indexer.index_file(source, code_root)
    symbols = _symbols_by_name(record)

    assert "not_a_definition" not in symbols
    assert "nd6_na_output" in symbols
    assert symbols["nd6_na_output"]["line_start"] == 4
    assert symbols["nd6_na_output"]["line_end"] == 10
    assert (
        symbols["nd6_na_output"]["signature"]
        == "void nd6_na_output(struct ifnet *ifp, const struct in6_addr *daddr6_0, "
        "const struct in6_addr *taddr6, int flags, struct sockaddr_dl *tlladdr)"
    )


def test_main_writes_code_index_stats_with_previous_baseline(tmp_path):
    code_root = tmp_path / "f-stack"
    source = code_root / "freebsd" / "netinet6" / "nd6.c"
    log_root = tmp_path / "logs"
    work = tmp_path / ".agent-work"
    _write(
        source,
        """void
nd6_ns_input(struct mbuf *m)
{
    return;
}
""",
    )
    _write(
        work / "code_index.json",
        json.dumps(
            {
                "files": [
                    {"file": "freebsd/netinet6/nd6.c", "symbols": []},
                    {"file": "freebsd/netinet6/old.c", "symbols": [{"name": "old"}]},
                ]
            }
        ),
    )

    rc = c_code_indexer.main(
        [
            "--code-root", str(code_root),
            "--design-root", str(tmp_path / "design"),
            "--benchmark", str(tmp_path / "benchmark.md"),
            "--log-root", str(log_root),
        ]
    )

    assert rc == 0
    stats = json.loads((log_root / "trace" / "code_index_stats.json").read_text(encoding="utf-8"))
    assert stats["file_count"] == 1
    assert stats["file_count_before"] == 2
    assert stats["symbol_count_before"] == 1
    assert stats["symbol_count_after"] == 1
    assert stats["zero_symbol_files_before"] == 1
    assert stats["zero_symbol_files_after"] == 0
    assert stats["priority_dir_symbol_counts"]["freebsd/netinet6"] == 1
    assert stats["known_function_presence"]["nd6_ns_input"] is True
