"""Release code runs when the wheel holds none of the graph's answering, community, tidying and tensor files, the array backend or the vision tools, and no torch: every console script's module imports, the store check, the page server, the device report and the retained graph modules leave them out."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from test_layers import GRAPH_EXTRAS

REPO = Path(__file__).resolve().parents[1]
EXPERIMENTAL_MODULES = (*(f"poolhouse.graph.{name}" for name in GRAPH_EXTRAS), "poolhouse.backend", "poolhouse.vision", "torch")

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


def test_every_console_script_module_imports_without_the_graph_extras_the_backend_vision_and_torch() -> None:
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


def test_the_retained_graph_modules_load_none_of_the_extras() -> None:
    out = _run(f"""
import importlib
retained = ("access", "cache", "columns", "completions", "concerns", "cypher", "drift", "ground", "guard", "metrics", "page",
            "payloads", "places", "propose", "questions", "rebuild", "requests", "review", "routes", "search", "serve", "store",
            "store_cli", "thread", "topology", "vectors")
for name in retained:
    importlib.import_module("poolhouse.graph." + name)
loaded = sorted(m for m, held in sys.modules.items() if held is not None
                and (m.split(".")[:2] == ["poolhouse", "backend"] or m.split(".")[:2] == ["poolhouse", "vision"]
                     or m.startswith("poolhouse.graph.") and m.split(".")[2] in {GRAPH_EXTRAS!r}))
assert not loaded, loaded
print("ok")
""")
    assert out.strip() == "ok"


def test_the_store_check_runs_and_the_store_offers_no_tidy_or_embed_when_the_build_has_neither(tmp_path: Path) -> None:
    out = _run(f"""
import contextlib, io
from pathlib import Path
from poolhouse import optional
from poolhouse.graph import store_cli
from poolhouse.graph.store import GraphStore
assert not optional.held("poolhouse.graph.tidy") and not optional.held("poolhouse.graph.smoothing")
path = Path({str(tmp_path)!r}) / "g"
nodes = [{{"id": "person:ada", "kind": "person", "label": "Ada Lovelace", "mentions": 2, "attrs": {{}}}}]
with GraphStore(path) as store:
    store.write({{"nodes": nodes, "edges": [], "stats": {{"nodes": 1, "edges": 0}}}})
for command in (["check", str(path)], ["docs", str(path)]):
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        assert store_cli.main(command) == 0, command
for command in ("tidy", "embed"):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        try:
            store_cli.main([command, str(path)])
        except SystemExit as exit:
            assert exit.code == 2, exit.code
        else:
            raise AssertionError(command)
    assert "invalid choice" in err.getvalue(), err.getvalue()
err = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
    try:
        store_cli.main(["--help"])
    except SystemExit:
        pass
print("ok")
""")
    assert out.strip() == "ok"


def test_the_page_server_declines_a_question_in_a_plain_sentence_when_the_build_cannot_answer() -> None:
    out = _run("""
from poolhouse import optional
from poolhouse.graph import serve
assert not optional.held(serve.CONVERSATION)
handler = serve.Handler.configured(graph={"nodes": [], "edges": []}, name="Bare")
reason = handler.ready(object.__new__(handler))
assert reason == "this build does not answer questions about a graph", reason
print("ok")
""")
    assert out.strip() == "ok"


def test_the_device_report_names_the_card_and_the_array_libraries_without_the_backend_or_torch() -> None:
    out = _run("""
from poolhouse.fleet import accelerator
from poolhouse.fleet.device import device_report
found = accelerator.report()
assert "torch" not in found["backends"] and set(found["backends"]) <= {"mlx", "torch"}, found
assert found["vendor"] and found["device"], found
assert device_report()["cpus"] >= 1
print("ok")
""")
    assert out.strip() == "ok"


def test_a_build_that_holds_them_answers_the_store_commands_and_the_page_server() -> None:
    out = _run(f"""
from poolhouse import optional
from poolhouse.graph import serve, store_cli
assert all(optional.held(f"poolhouse.graph.{{name}}") for name in {GRAPH_EXTRAS!r})
assert all(optional.held(name) for name in ("poolhouse.backend", "poolhouse.vision"))
handler = serve.Handler.configured(graph={{"nodes": [], "edges": []}}, name="Held")
assert handler.ready(object.__new__(handler)) == "no model on this server: serve with --model"
print("ok")
""", blocked=False)
    assert out.strip() == "ok"
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    for command in ("tidy", "embed"):
        done = subprocess.run([sys.executable, "-m", "poolhouse.graph.store_cli", command, "--help"], capture_output=True,
                              text=True, env=env, timeout=300)
        assert done.returncode == 0 and "usage" in done.stdout.lower(), (command, done.stdout, done.stderr)
