"""Release code runs when the wheel holds no train, gguf or testing: every console script's module imports, the decider command, the device report, the page and the job routes leave them out."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXPERIMENTAL_MODULES = ("poolhouse.train", "poolhouse.gguf", "poolhouse.testing", "poolhouse.fleet.train_routes")

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


def test_every_console_script_module_imports_without_training_the_gguf_tools_and_the_fakes() -> None:
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


def test_the_decider_command_keeps_ask_score_calibrate_and_compare_without_training() -> None:
    out = _run("""
from poolhouse import decide_cli
names = {getattr(command, "name", "") for command in decide_cli.COMMANDS.commands}
assert {"ask", "eval", "calibrate", "bench", "jevbench", "list", "export-cases", "check-cases"} <= names, names
assert not {"train", "make-cases"} & names, names
print("ok")
""")
    assert out.strip() == "ok"


def test_the_device_report_names_the_card_without_training() -> None:
    out = _run("""
from poolhouse.fleet.device import device_report, registered_reports
assert [fn.__module__ for fn in registered_reports()] == ["poolhouse.fleet.accelerator"]
assert device_report()["cpus"] >= 1
print("ok")
""")
    assert out.strip() == "ok"


def test_the_page_and_the_job_routes_leave_training_out_when_the_build_holds_none() -> None:
    _run("""
from poolhouse.fleet import page, routes
assert not [c for c in routes.Router.__mro__ if c.__name__.startswith("Train")]
assert "poolhouse.train" not in object.__new__(routes.Router).job_commands()
import shutil, tempfile
from pathlib import Path
held = Path(tempfile.mkdtemp()) / "components"
shutil.copytree(page.COMPONENTS_DIR, held, ignore=shutil.ignore_patterns("training-view.html"))
page.COMPONENTS_DIR = held
html = page.render()
assert "<training-view" not in html and 'define("training-view"' not in html
""")


def test_a_build_that_holds_training_runs_its_commands_and_routes() -> None:
    out = _run("""
from poolhouse import decide_cli, optional
from poolhouse.fleet import routes
assert all(optional.held(name) for name in ("poolhouse.train", "poolhouse.gguf", "poolhouse.testing"))
assert {"train", "make-cases"} <= {getattr(command, "name", "") for command in decide_cli.COMMANDS.commands}
assert "poolhouse.train" in object.__new__(routes.Router).job_commands()
print("ok")
""", blocked=False)
    assert out.strip() == "ok"
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    for command in ("run", "tools", "decider", "suite"):
        done = subprocess.run([sys.executable, "-m", "poolhouse.train", command, "--help"], capture_output=True, text=True,
                              env=env, timeout=300)
        assert done.returncode == 0 and "usage" in done.stdout.lower(), (command, done.stdout, done.stderr)
    done = subprocess.run([sys.executable, "-m", "poolhouse.train"], capture_output=True, text=True, env=env, timeout=300)
    assert done.returncode == 2 and "usage: python -m poolhouse.train" in done.stderr
