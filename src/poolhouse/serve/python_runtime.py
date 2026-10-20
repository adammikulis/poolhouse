"""Verified Python commands for broker-owned model servers."""
from __future__ import annotations

import subprocess
import sys

from poolhouse import maintenance
from poolhouse.fleet.environment import Environment
from poolhouse.fleet.managed_compute import process_environment
from poolhouse.fleet.runtime_paths import default_root
from poolhouse.paths import source_checkout
from poolhouse.serve.backend import ServerFailed

_MODULES = {
    'mlx-tree': ('poolhouse.serve.mlx_tree_server', ('mlx', 'mlx_lm', 'mlx_vlm')),
    'vllm': ('vllm.entrypoints.openai.api_server', ('vllm',)),
    'sglang': ('sglang.launch_server', ('sglang',)),
}
_PROBE = """import importlib.util,json,sys
assert all(importlib.util.find_spec(n) is not None for n in json.loads(sys.argv[1]))
if sys.argv[2]=='mlx-tree':
 from poolhouse.serve.python_requirements import installed_mlx
 assert installed_mlx(), 'MLX serving dependency versions differ'
"""


@maintenance.reading
def command(engine: str) -> list[str]:
    """Return the fixed engine module under its verified compute interpreter."""
    import json

    module, required = _MODULES[engine]
    if source_checkout():
        return [sys.executable, '-m', module]
    environment = Environment(default_root())
    try:
        if not environment.exists:
            raise OSError('Install Training essentials in Libraries before starting a model')
        environment.require_current_runtime()
        environment.require_compute()
        done = subprocess.run([str(environment.python), '-B', '-I', '-c', _PROBE, json.dumps(required), engine],
                              capture_output=True, text=True, timeout=20, env=environment_for_child())
        if done.returncode:
            raise OSError('Install MLX model serving in Libraries before starting this model' if engine == 'mlx-tree'
                          else f'Install the {engine} libraries in Libraries before starting this model')
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ServerFailed(str(exc)) from exc
    return [str(environment.python), '-B', '-I', '-m', module]


def environment_for_child(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a Python child environment with explicit server credentials preserved."""
    result = {name: value for name, value in process_environment().items()
              if not name.startswith(('PYTHON', 'DYLD_'))
              and name not in {'LD_PRELOAD', 'LD_AUDIT'}}
    if extra:
        result.update({name: value for name, value in extra.items()
                       if not name.startswith(('PYTHON', 'DYLD_'))
                       and name not in {'LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH',
                                        'LD_LIBRARY_PATH_ORIG'}})
    return result
