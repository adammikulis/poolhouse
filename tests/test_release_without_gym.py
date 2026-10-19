"""Release code runs when the wheel holds no gym: every console script's module imports, the daemon's page and routes leave the gym out."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GYM_MODULES = ("poolhouse.gym", "poolhouse.fleet.gym_routes", "poolhouse.fleet.gym_recording_routes",
               "poolhouse.fleet.gym_interpreters")

BLOCKED = f"""
import importlib, sys
for name in {GYM_MODULES!r}:
    sys.modules[name] = None
"""


def _run(body: str) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO / "src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    done = subprocess.run([sys.executable, "-c", BLOCKED + body], capture_output=True, text=True, env=env, timeout=300)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_every_console_script_module_imports_without_the_gym() -> None:
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    modules = sorted({target.split(":")[0] for target in scripts.values()})
    failed = _run(f"""
failed = []
for module in {modules!r}:
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if (exc.name or "").startswith("poolhouse"):
            failed.append(f"{{module}}: {{exc}}")
print("\\\\n".join(failed))
""")
    assert not failed.strip(), failed


def test_the_daemon_serves_no_gym_routes_or_page_without_the_gym() -> None:
    _run("""
from poolhouse.fleet import daemon, page, routes, workspace_routes
assert not [c for c in routes.Router.__mro__ if c.__name__.startswith("Gym")]
assert object.__new__(routes.Router).job_commands() == ()
import shutil, tempfile
from pathlib import Path
held = Path(tempfile.mkdtemp()) / "components"
shutil.copytree(page.COMPONENTS_DIR, held, ignore=shutil.ignore_patterns("gym-*.html"))
page.COMPONENTS_DIR = held
html = page.render()
assert "<gym-view" not in html and 'define("gym-view"' not in html and "gym-scene" not in html
""")


def test_the_daemon_serves_the_gym_routes_when_the_build_holds_them() -> None:
    from poolhouse.fleet import routes

    assert {"GymRoutes", "GymRecordingRoutes"} <= {c.__name__ for c in routes.Router.__mro__}
    assert "poolhouse.gym" in object.__new__(routes.Router).job_commands()
