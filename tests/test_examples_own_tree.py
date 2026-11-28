"""``python -m examples.rbmk...`` from a checkout runs that checkout's Poolhouse, with nothing installed and no ``PYTHONPATH``."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("module", ["examples.rbmk", "examples.rbmk.run"])
def test_an_example_run_from_a_checkout_imports_that_checkouts_poolhouse(tmp_path, module):
    env = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    ask = f"import importlib; importlib.import_module({module!r}); import poolhouse; print(poolhouse.__file__)"
    done = subprocess.run([sys.executable, "-c", ask], cwd=REPO, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert not done.returncode, done.stderr
    assert Path(done.stdout.strip()).resolve().is_relative_to(REPO / "src"), done.stdout
