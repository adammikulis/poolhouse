"""Release code runs when the wheel holds no bench, world or draft: every console script's module imports, the tool lists, the page, the doctor and the daemon's measuring host leave them out."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXPERIMENTAL_MODULES = ("poolhouse.bench", "poolhouse.world", "poolhouse.draft")

BLOCKED = f"""
import importlib, sys
for name in {EXPERIMENTAL_MODULES!r}:
    sys.modules[name] = None
"""


def _run(body: str, *, blocked: bool = True) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO / "src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    done = subprocess.run([sys.executable, "-c", (BLOCKED if blocked else "") + body], capture_output=True, text=True,
                          env=env, timeout=300)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_every_console_script_module_imports_without_the_bench_the_world_and_the_draft_tool() -> None:
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    modules = sorted({target.split(":")[0] for target in scripts.values()})
    failed = _run(f"""
failed = []
for module in {modules!r}:
    try:
        importlib.import_module(module)
    except ImportError as exc:
        failed.append(f"{{module}}: {{exc}}")
print("\\\\n".join(failed))
""")
    assert not failed.strip(), failed


def test_the_tool_lists_leave_out_the_bench_and_the_world_when_the_build_has_neither() -> None:
    out = _run("""
from poolhouse import do, mcp
names = {tool.name for tool in mcp.TOOLS}
assert "serve_status" in names
assert not [n for n in names if n.startswith("bench_") or n == "world_make"], names
listed = {schema["function"]["name"] for schema, _ in do.command_tools()}
assert not [n for n in listed if n.startswith("bench_") or n == "world_make"], listed
print("ok")
""")
    assert out.strip() == "ok"


def test_the_doctor_checks_the_machine_without_the_bench_store(tmp_path: Path) -> None:
    out = _run(f"""
from pathlib import Path
from poolhouse import doctor
assert doctor.bench_of(Path({str(tmp_path)!r})) == []
print("ok")
""")
    assert out.strip() == "ok"


def test_the_page_the_daemon_serves_has_no_benchmarks_without_their_files() -> None:
    _run("""
import shutil, tempfile
from pathlib import Path
from poolhouse.fleet import page
held = Path(tempfile.mkdtemp()) / "components"
shutil.copytree(page.COMPONENTS_DIR, held, ignore=shutil.ignore_patterns("benchmarks-view.html", "fleet-benchmark.html"))
page.COMPONENTS_DIR = held
html = page.render()
assert "<benchmarks-view" not in html and 'define("benchmarks-view"' not in html
assert 'define("fleet-benchmark"' not in html
""")


def test_the_daemon_answers_the_bench_routes_without_the_bench(tmp_path: Path) -> None:
    _run(f"""
from pathlib import Path
from poolhouse.fleet.ui import UI
ui = UI(name="studio", pool_key_path=Path({str(tmp_path)!r}) / "pool.key")
state = ui.bench_state()
assert state["available"] is False and state["measuring"] is None, state
assert "not installed here" in ui.rates()["error"]
for call in (lambda: ui.bench_history(), lambda: ui.stop_sweep(1), lambda: ui.start_sweep(["sweep"])):
    try:
        call()
    except ImportError:
        continue
    raise AssertionError("a bench call answered without the bench")
""")


def test_a_member_without_the_bench_refuses_a_measurement(tmp_path: Path) -> None:
    _run(f"""
from pathlib import Path
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.measuring import BenchHost, Job, Refused
root = Path({str(tmp_path)!r})
host = BenchHost(JobRunner(root / "traind", root / "files"), home=root / "bench", name="lone")
try:
    host.submit(Job(argv=("sweep",), models=("m.gguf",), commit=host.commit))
except Refused as refused:
    assert refused.kind == "launch" and "no bench" in str(refused), refused
else:
    raise AssertionError("the member took a measurement without the bench")
""")


def test_a_build_that_holds_the_bench_lists_its_tools_and_runs_the_experimental_commands() -> None:
    out = _run("""
from poolhouse import mcp, optional
names = {tool.name for tool in mcp.TOOLS}
assert {"bench_run", "bench_status", "bench_history", "bench_show", "world_make"} <= names
assert all(optional.held(name) for name in ("poolhouse.bench", "poolhouse.world", "poolhouse.draft"))
print("ok")
""", blocked=False)
    assert out.strip() == "ok"
    for name in ("bench", "world", "draft"):
        env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
        done = subprocess.run([sys.executable, "-m", f"poolhouse.{name}", "--help"], capture_output=True, text=True,
                              env=env, timeout=300)
        assert done.returncode == 0 and "usage" in done.stdout.lower(), (name, done.stdout, done.stderr)
