"""The two processes the Pi tool runtime starts to learn about Node: hostile text stays data.

`pi_tools.runtime` asks the Node binary which libraries it loads and `pi_tools._inspect` asks the
platform inspector about them. What comes back is written by whatever is installed, so it is
read as untrusted: an argument is one argv item, the environment is the minimal one, and an
answer that is malformed or too large is refused.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import textwrap

import pytest

from poolhouse import coding_actions, pi_tools

HOSTILE = ["x; touch PWNED", "$(touch PWNED)", "`touch PWNED`", "a | b > PWNED", "--exec=/bin/sh", "a\nb"]


def test_inspect_passes_hostile_text_as_one_argument_with_a_minimal_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_TEST_SECRET", "hunter2")
    monkeypatch.chdir(tmp_path)
    echo = [sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])"]
    for text in HOSTILE:
        assert pi_tools._inspect([*echo, text]) == (0, text)
    probe = "import os; print(os.environ.get('POOLHOUSE_TEST_SECRET'), os.environ.get('HOME'), os.getcwd())"
    _, seen = pi_tools._inspect([sys.executable, "-c", probe])
    assert seen.split() == ["None", "/var/empty", os.path.realpath(os.sep)]
    assert not (tmp_path / "PWNED").exists()


def test_inspect_refuses_a_report_past_the_frame_limit():
    with pytest.raises(ValueError, match="exceeds its limit"):
        pi_tools._inspect([sys.executable, "-c", f"print('x' * {coding_actions.MAX_FRAME + 1})"])


def pi_package(root, name="@earendil-works/pi-coding-agent"):
    (root / "pkg" / "dist").mkdir(parents=True)
    (root / "pkg" / "bin" / "x").mkdir(parents=True)
    (root / "pkg" / "package.json").write_text(json.dumps({"name": name}))
    (root / "pkg" / "dist" / "index.js").write_text("")
    binary = root / "pkg" / "bin" / "x" / "pi"
    binary.write_text("")
    return str(binary)


def node_on_path(root, monkeypatch, printed: str | None = None, flood: int = 0):
    """A `node` on PATH that records its argv, then answers the report query with ``printed``
    (or ``flood`` bytes of noise)."""
    bin_dir = root / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    answer = textwrap.dedent(f"""\
        import sys
        open({str(root / "argv")!r}, "w").write("\\n".join(sys.argv[1:]))
        sys.stdout.write({printed!r} if {printed!r} is not None else "x" * {flood})
        """)
    (bin_dir / "answer.py").write_text(answer)
    if sys.platform == "win32":
        (bin_dir / "node.cmd").write_text(f'@"{sys.executable}" "{bin_dir / "answer.py"}" %*\r\n')
    else:
        node = bin_dir / "node"
        node.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bin_dir / "answer.py"}" "$@"\n')
        node.chmod(node.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return root / "argv"


def test_runtime_refuses_a_package_that_is_not_the_pi_tool_library(tmp_path, monkeypatch):
    argv = node_on_path(tmp_path, monkeypatch, "[]")
    with pytest.raises(ValueError, match="not a supported installed package"):
        pi_tools.runtime(pi_package(tmp_path, name="evil"))
    assert not argv.exists()


@pytest.mark.parametrize("report", ['["relative/lib.so"]', '[42]', '{"a": "/abs"}', '["/ok", null]',
                                    '["-L/abs"]', 'not json'])
def test_runtime_refuses_a_node_report_that_is_not_a_list_of_absolute_paths(tmp_path, monkeypatch, report):
    node_on_path(tmp_path, monkeypatch, report)
    with pytest.raises(ValueError):
        pi_tools.runtime(pi_package(tmp_path))


def test_runtime_refuses_an_oversized_node_report(tmp_path, monkeypatch):
    node_on_path(tmp_path, monkeypatch, flood=coding_actions.MAX_FRAME + 1)
    with pytest.raises(ValueError, match="exceeds its limit"):
        pi_tools.runtime(pi_package(tmp_path))


def test_runtime_asks_node_with_fixed_arguments_and_reads_a_hostile_library_name_as_data(tmp_path, monkeypatch):
    hostile = tmp_path / "$(touch PWNED); lib.so"
    hostile.write_text("")
    argv = node_on_path(tmp_path, monkeypatch, json.dumps([str(hostile)]))
    monkeypatch.chdir(tmp_path)
    found = pi_tools.runtime(pi_package(tmp_path))
    asked = argv.read_text().split("\n")
    assert asked[0] == "-e" and "PWNED" not in " ".join(asked)
    assert os.path.realpath(hostile) in found["reads"]
    assert not (tmp_path / "PWNED").exists()
