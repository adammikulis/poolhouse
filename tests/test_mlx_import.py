"""A failed import of ``mlx.core`` is remembered: a second try at the half-loaded extension ends the process, as a node's accelerator report did in
a sandbox with no Metal device.

The sandbox is real (Seatbelt with no GPU), and so are mlx, its failure and the probes.
"""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path

import poolhouse
from poolhouse import sandbox
from poolhouse.testing.markers import needs_mlx

pytest_plugins = ["tests.sandbox_kit"]

CHILD = """
from poolhouse.backend import determinism, mlx_ops
from poolhouse.fleet import accelerator
try:
    mlx_ops.require_mlx()
except RuntimeError:
    print("refused", flush=True)
else:
    print("loaded", flush=True)
for _ in range(2):
    accelerator.report()
print(determinism.set_seeds(1).mlx, "alive", flush=True)
try:
    import mlx.core
except ImportError:
    print("a later import raises", flush=True)
"""


@needs_mlx
def test_a_probe_that_could_not_import_mlx_leaves_the_process_alive_for_every_later_import(seatbelt, tmp_path):
    work = os.path.realpath(tmp_path)
    source = str(Path(poolhouse.__file__).resolve().parents[1])
    reads = tuple(dict.fromkeys(os.path.realpath(each) for each in (sys.prefix, sys.base_prefix, *site.getsitepackages(), source)))
    policy = sandbox.Policy("no-gpu", read=(*reads, work), write=(work,), exec=("/usr/bin", "/bin", *reads), gpu=False,
                            env={"PATH": "/usr/bin:/bin", "HOME": work, "TMPDIR": work, "PYTHONPATH": source, "PYTHONDONTWRITEBYTECODE": "1"})
    done = sandbox.run([os.path.realpath(sys.executable), "-c", CHILD], policy, cwd=work)
    assert done.ok, f"{done.returncode}\n{done.stdout}\n{done.stderr[-3000:]}"
    assert done.stdout.split("\n")[:3] == ["refused", "False alive", "a later import raises"], done.stdout
