"""Release code runs when the wheel holds no ingest, sources, scrape, web or datasheet: every console script's module imports, the tool lists, the page, the store check and the tidy report leave them out."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXPERIMENTAL_MODULES = ("poolhouse.ingest", "poolhouse.sources", "poolhouse.scrape", "poolhouse.web", "poolhouse.datasheet")

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


def test_every_console_script_module_imports_without_reading_documents_or_sites() -> None:
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


def test_the_tool_lists_hold_no_web_or_datasheet_tool_when_the_build_has_neither() -> None:
    out = _run("""
from poolhouse import do, mcp
from poolhouse.graph import prompts
names = {tool.name for tool in mcp.TOOLS}
assert "serve_status" in names
assert not [n for n in names if n.startswith(("web_", "datasheet_", "ingest_"))], names
listed = {schema["function"]["name"] for schema, _ in do.command_tools()}
assert not [n for n in listed if n.startswith(("web_", "datasheet_", "ingest_"))], listed
assert prompts.TOOLS
print("ok")
""")
    assert out.strip() == "ok"


def test_the_page_and_the_command_library_name_no_ingest_command() -> None:
    out = _run("""
from poolhouse.fleet import page, workspace_routes
html = page.render()
assert "Search graph, speech, security, workspace" in html
assert "ingest" not in workspace_routes.PURPOSES
print("ok")
""")
    assert out.strip() == "ok"


def test_the_store_check_and_the_tidy_report_name_no_ingest_command_without_it(tmp_path: Path) -> None:
    out = _run(f"""
from pathlib import Path
from poolhouse import optional
from poolhouse.graph.hygiene import Report
from poolhouse.graph.store import GraphStore
from poolhouse.graph import store_cli
assert not optional.held("poolhouse.ingest") and optional.command("poolhouse.ingest", "fold") == ""
said = Report(findings=["node x: found by scan, not by id"]).said()
assert said.startswith("NOT SOUND") and "ingest" not in said and "Rebuild from the reads and report" in said, said
path = Path({str(tmp_path)!r}) / "g"
nodes = [{{"id": "person:ada", "kind": "person", "label": "Ada Lovelace", "mentions": 2, "attrs": {{}}}},
         {{"id": "topic:compilers", "kind": "topic", "label": "compilers", "mentions": 1, "attrs": {{}}}}]
with GraphStore(path) as store:
    store.write({{"nodes": nodes, "edges": [], "stats": {{"nodes": 2, "edges": 0}}}})
    store.set_embedding("person:ada", [1.0, 0.0], model="gemma")
import contextlib, io
err = io.StringIO()
with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
    assert store_cli.main(["check", str(path)]) == 0
assert "1 node(s) short of a vector" in err.getvalue() and "ingest" not in err.getvalue(), err.getvalue()
print("ok")
""")
    assert out.strip() == "ok"


def test_a_build_that_holds_them_names_the_commands_and_runs_them() -> None:
    out = _run("""
from poolhouse import optional
from poolhouse.graph.hygiene import Report
assert all(optional.held(name) for name in ("poolhouse.ingest", "poolhouse.sources", "poolhouse.scrape", "poolhouse.web", "poolhouse.datasheet"))
assert "python -m poolhouse.ingest fold" in Report(findings=["x"]).said()
print("ok")
""", blocked=False)
    assert out.strip() == "ok"
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    for name in ("ingest", "datasheet"):
        done = subprocess.run([sys.executable, "-m", f"poolhouse.{name}", "--help"], capture_output=True, text=True,
                              env=env, timeout=300)
        assert done.returncode == 0 and "usage" in done.stdout.lower(), (name, done.stdout, done.stderr)
