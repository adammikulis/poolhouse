"""Which package may import which.

The layers are core, model, graph, machine, tools, and experimental (the red-team harness, the
gym, the bench, the world, the draft tool, training, the GGUF tools, the shared fakes, reading
documents and sites into a graph and the tools that drive the rest, none of which ships in the
wheel; the gym's and the training's files in ``fleet`` count as theirs). Release code reaches an
experimental module by name through ``poolhouse.optional`` and works when it is absent. A package
may import a package in a lower layer, and one in its own layer as long as the other does not
import it back.
``KNOWN`` lists the edges that break that today; it may only shrink.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "src" / "poolhouse"

LAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("core", ("activity", "agent_dependency", "agent_hooks", "agent_hooks_stale", "asking", "authority", "backend", "briefing", "checks", "command", "command_capture", "contracts", "credentials", "data",
              "entities", "errors", "extraction", "files", "files_windows", "gate", "geo", "gitstate",
              "home", "hook_bootstrap", "hook_diagnostics", "hookcheck", "http", "http_cancel", "httpguard", "installed", "interventions", "features", "jobs", "json_objects", "jsonl", "keystore", "keystore_guard", "kvtype", "lan", "leasetable", "legacy", "limits",
              "runtime", "runtime_launchers", "runtime_git", "runtime_bridges",
              "lock", "log", "macauth", "sealing",
              "markup", "sandbox",
              "deciders", "desktop", "devbranch", "media", "messages", "net", "node_health", "node_pool", "optional", "paths", "platform", "safenames", "redact", "sentinel",
              "real_machine", "records", "requests", "person", "private_path", "private_counters", "protectedwrites", "safetext", "secretpaths",
              "serverkeys",
              "subscribers", "taint", "tar_libraries", "telemetry", "tool_schema", "trees", "ui", "units", "win32", "windows_private", "worktreerules")),
    ("model", ("client", "decide", "hub", "spec", "speech", "vision")),
    ("graph", ("board_names", "graph")),
    ("machine", ("doctor", "fleet", "serve", "setup", "studio")),
    ("tools", ("coordination", "agent", "chat", "chatpolicy", "claude", "cli", "codex", "coding", "decide_cli",
               "do", "guard", "harness", "harness_claims", "harnessid", "harnessing", "harnesshook", "harnesspolicy", "mcp", "migrate", "migrate_paths", "migrate_scan", "memory", "reputation", "inbox", "roles", "rules",
               "api", "testfarm", "workspace", "pi", "features_cli", "board", "node_app", "device_host", "device_setup", "node_binary", "node_build", "node_join", "node_join_check", "node_key", "node_launch", "node_permission", "node_signing", "node_supervise", "build_update", "runtime_await", "runtime_board", "runtime_cli", "runtime_coalesce", "runtime_deploy", "runtime_host", "runtime_stale", "runtime_source", "runtime_store", "runtime_trust", "runtime_unmerged")),
    ("experimental", ("bench", "datasheet", "draft", "gguf", "gym", "ingest", "redteam", "scrape", "sources", "surface", "testing",
                      "train", "walk", "web", "world")),
)

EXPERIMENTAL = dict(LAYERS)["experimental"]
EXPERIMENTAL_FILES = ("src/poolhouse/fleet/gym_*.py", "src/poolhouse/fleet/train_*.py",
                      "src/poolhouse/fleet/web/components/gym-*.html",
                      "src/poolhouse/fleet/web/components/benchmarks-view.html",
                      "src/poolhouse/fleet/web/components/fleet-benchmark.html",
                      "src/poolhouse/fleet/web/components/training-view.html")

RANK = {package: height for height, (_, packages) in enumerate(LAYERS)
        for package in packages}

KNOWN = {
    ("fleet", "serve"),
    ("fleet", "setup"),
    ("graph", "serve"),
    ("hub", "serve"),
    ("serve", "fleet"),
    ("serve", "setup"),
    ("setup", "fleet"),
    ("setup", "serve"),
}


def _package_of_file(path: Path) -> str:
    parts = path.relative_to(ROOT).parts
    if parts[:1] == ("fleet",) and path.match("gym_*.py"):
        return "gym"
    if parts[:1] == ("fleet",) and path.match("train_*.py"):
        return "train"
    return parts[0][:-3] if len(parts) == 1 else parts[0]


def _package_of_module(name: str) -> str | None:
    parts = name.split(".")
    return parts[1] if len(parts) > 1 else None


def _imports() -> dict[tuple[str, str], set[str]]:
    """Every ``poolhouse`` import in the tree, keyed by (importer, imported) package."""
    found: dict[tuple[str, str], set[str]] = {}
    for path in sorted(ROOT.rglob("*.py")):
        if path == ROOT / "__init__.py":
            continue
        source = _package_of_file(path)
        where = str(path.relative_to(REPO))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
            named: list[str] = []
            if isinstance(node, ast.Import):
                named = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                module = node.module or ""
                named = [module]
                if module == "poolhouse":
                    named += [f"poolhouse.{alias.name}" for alias in node.names]
            for name in named:
                if name != "poolhouse" and not name.startswith("poolhouse."):
                    continue
                target = _package_of_module(name)
                if target and target != source:
                    found.setdefault((source, target), set()).add(where)
    return found


def _packages() -> set[str]:
    return {_package_of_file(path) for path in ROOT.rglob("*.py") if path != ROOT / "__init__.py"}


def _violations(edges: dict[tuple[str, str], set[str]]) -> set[tuple[str, str]]:
    """Edges that point up a layer, or across one layer in both directions."""
    out: set[tuple[str, str]] = set()
    for source, target in edges:
        if RANK[source] < RANK[target] or (RANK[source] == RANK[target] and (target, source) in edges):
            out.add((source, target))
    return out


def test_every_package_is_placed_in_a_layer() -> None:
    unplaced = sorted(_packages() - set(RANK))
    assert not unplaced, f"packages with no layer: {unplaced}"


def test_no_import_reaches_above_its_layer() -> None:
    edges = _imports()
    new = sorted(_violations(edges) - KNOWN)
    detail = [f"{a} -> {b}  {sorted(edges[(a, b)])}" for a, b in new]
    assert not new, "layer violations not in KNOWN:\n" + "\n".join(detail)


def test_known_holds_nothing_already_fixed() -> None:
    stale = sorted(KNOWN - _violations(_imports()))
    assert not stale, f"no longer violations, remove them from KNOWN: {stale}"


def _loaded(module: str, then: str = "pass") -> set[str]:
    """The ``poolhouse`` modules a fresh interpreter loads importing ``module`` and running
    ``then``."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    code = (f"import sys, {module}; {then}; "
            "print('\\n'.join(m for m in sys.modules if m.startswith('poolhouse')))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    return set(done.stdout.split())


def test_a_client_does_not_load_the_fleet() -> None:
    unwanted = {"poolhouse.fleet.daemon", "poolhouse.fleet.discovery"}
    for module in ("poolhouse.client", "poolhouse.hub", "poolhouse.serve.cli"):
        pulled = _loaded(module) & unwanted
        assert not pulled, f"{module} loads {sorted(pulled)}"


def test_the_beacon_s_memory_number_does_not_load_the_graph(tmp_path: Path) -> None:
    pulled = _loaded(
        "poolhouse.fleet.measuring",
        "poolhouse.fleet.measuring.BenchHost(None, home="
        f"{str(tmp_path)!r}).report()")
    graph = sorted(m for m in pulled if m.startswith("poolhouse.graph"))
    assert not graph, f"the beacon's room number loads {graph}"


def _pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def test_the_wheel_leaves_out_exactly_the_experimental_packages() -> None:
    excluded = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]["exclude"]
    packages = [f"src/poolhouse/{name}" + ("" if (ROOT / name).is_dir() else ".py") for name in EXPERIMENTAL]
    assert sorted(excluded) == sorted([*packages, *EXPERIMENTAL_FILES])


def test_the_experimental_files_are_the_ones_the_wheel_leaves_out() -> None:
    for pattern in EXPERIMENTAL_FILES:
        assert list(REPO.glob(pattern)), f"{pattern} matches no file"


def test_no_console_script_runs_an_experimental_package() -> None:
    targets = _pyproject()["project"]["scripts"].values()
    runs = sorted(t for t in targets if t.split(":")[0].split(".")[1] in EXPERIMENTAL)
    assert not runs, f"experimental packages run as python -m poolhouse.<name>, not as commands: {runs}"
