"""What wildcard-binds counts: a listener, flag or published port that opens beyond loopback, outside the LAN code."""

from __future__ import annotations

from pathlib import Path

import pytest
from gates import wildcard_binds

REPO = Path(__file__).resolve().parent.parent


def _found(tmp_path: Path, source: str, name: str = "one.py") -> list[str]:
    where = tmp_path / "src" / "poolhouse" / name
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(source, encoding="utf-8")
    return [f.detail for f in wildcard_binds.find(tmp_path)]


@pytest.mark.parametrize("line", [
    "import socket\ns = socket.socket()\ns.bind(('0.0.0.0', 0))",
    "from http.server import ThreadingHTTPServer as S\nS(('', 8000), None)",
    "import socket\nALL = '0.0.0.0'\nsocket.socket().bind((ALL, 0))",
    "import socket\nsocket.create_server(('::', 9))",
    "import asyncio\nasyncio.start_server(None, host='0.0.0.0', port=9)",
    "import uvicorn\nuvicorn.run(app, host='0.0.0.0')",
    "def address(container):\n    return ('0.0.0.0' if container else '127.0.0.1', 0)",
    "def host(args):\n    return args.host or '0.0.0.0'",
    "ARGV = ['llama-server', '--host', '0.0.0.0', '--port', '8080']",
    "ARGV = ['poolhouse-node', 'run', '--listen', f'0.0.0.0:{PORT}']",
    "ARGV = ['docker', 'run', '-p', '8770:8770', 'image']",
    "ARGV = ['docker', 'run', '--publish', f'{PORT}:{PORT}', 'image']",
    "ap.add_argument('--host', default='0.0.0.0')",
])
def test_a_listener_beyond_loopback_is_found(tmp_path, line: str) -> None:
    assert _found(tmp_path, line + "\n")


@pytest.mark.parametrize("line", [
    "import socket\nsocket.socket().bind(('127.0.0.1', 0))",
    "from http.server import HTTPServer\nHTTPServer(('127.0.0.1', 0), None)",
    "import socket\nsocket.socket().bind(('::1', 0))",
    "ARGV = ['llama-server', '--host', '127.0.0.1', '--port', '8080']",
    "ARGV = ['docker', 'run', '-p', '127.0.0.1:8770:8770', 'image']",
    "ARGV = ['docker', 'run', '-p', f'127.0.0.1:{PORT}:{PORT}', 'image']",
    "ARGV = ['pytest', '-p', 'no:cacheprovider']",
    "empty = ('', 0)",
    "BAD = ['0.0.0.0', '224.0.0.1', '8.8.8.8']",
    "ap.add_argument('--host', default='')",
    "ap.add_argument('--host', default='127.0.0.1')",
])
def test_loopback_and_plain_data_are_left_alone(tmp_path, line: str) -> None:
    assert not _found(tmp_path, line + "\n")


def test_the_code_that_is_the_lan_may_name_the_wildcard(tmp_path) -> None:
    where = tmp_path / "src" / "poolhouse" / "fleet"
    where.mkdir(parents=True)
    (where / "daemon.py").write_text("ALL_INTERFACES = '0.0.0.0'\n", encoding="utf-8")
    assert not wildcard_binds.find(tmp_path)


def test_a_test_may_not_name_it(tmp_path) -> None:
    where = tmp_path / "tests"
    where.mkdir()
    (where / "test_one.py").write_text("ALL = '0.0.0.0'\n", encoding="utf-8")
    assert [f.path for f in wildcard_binds.find(tmp_path)] == ["tests/test_one.py"]


def _rust(tmp_path: Path, text: str, name: str = "src/x.rs") -> list[tuple[str, int]]:
    where = tmp_path / "app" / "node" / name
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(text, encoding="utf-8")
    return [(f.path, f.line) for f in wildcard_binds.find(tmp_path)]


def test_a_wildcard_in_node_source_is_found(tmp_path) -> None:
    assert _rust(tmp_path, 'fn a() {\n    let s = UdpSocket::bind("0.0.0.0:0");\n}\n') == [("app/node/src/x.rs", 2)]
    assert _rust(tmp_path, "let a = SocketAddr::from((Ipv4Addr::UNSPECIFIED, 9));\n")


def test_loopback_in_node_source_and_the_opt_in_tests_are_left_alone(tmp_path) -> None:
    assert not _rust(tmp_path, 'let l = TcpListener::bind("127.0.0.1:0");\n')
    text = '#[test]\nfn a() { bind("127.0.0.1:0"); }\n// ---- OPT-IN BELOW\n#[ignore]\nfn b() { bind("0.0.0.0:9"); }\n'
    assert not _rust(tmp_path, text, name="tests/p.rs")


def test_every_lan_entry_is_still_needed() -> None:
    """A file that no longer names a wildcard leaves the list, so the list is only what the product needs."""
    kept = dict(wildcard_binds.LAN)
    try:
        for where in kept:
            wildcard_binds.LAN.pop(where)
            scan = wildcard_binds.scan_rust if where.endswith(".rs") else wildcard_binds.scan
            assert scan(REPO / where, where), f"{where} no longer names a wildcard address: take it off LAN"
            wildcard_binds.LAN[where] = kept[where]
    finally:
        wildcard_binds.LAN.clear()
        wildcard_binds.LAN.update(kept)


def test_development_and_test_code_binds_loopback_only() -> None:
    assert [(f.path, f.line, f.detail) for f in wildcard_binds.find(REPO)] == []
