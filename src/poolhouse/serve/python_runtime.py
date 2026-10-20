"""Verified Python commands for broker-owned model servers."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import poolhouse
from poolhouse.fleet.environment import Environment
from poolhouse.fleet.managed_compute import process_environment
from poolhouse.fleet.runtime_paths import default_root
from poolhouse.serve.backend import ServerFailed

_MODULES = {
    'mlx-tree': ('poolhouse.serve.mlx_tree_server', ('mlx', 'mlx_lm')),
    'vllm': ('vllm.entrypoints.openai.api_server', ('vllm',)),
    'sglang': ('sglang.launch_server', ('sglang',)),
}
_PROBE = "import importlib.util,json,sys; assert all(importlib.util.find_spec(n) is not None for n in json.loads(sys.argv[1]))"


def _checkout() -> bool:
    package = Path(poolhouse.__file__).resolve().parent
    root = package.parent.parent
    immutable = package / 'fleet'
    return (not getattr(sys, 'frozen', False)
            and package == root / 'src' / 'poolhouse' and (root / '.git').exists()
            and not any((immutable / name).exists() or (immutable / name).is_symlink()
                        for name in ('built-from', 'compute-artifact.json')))


def command(engine: str) -> list[str]:
    """Return the fixed engine module under its verified compute interpreter."""
    import json

    module, required = _MODULES[engine]
    if _checkout():
        return [sys.executable, '-m', module]
    environment = Environment(default_root())
    try:
        if not environment.exists:
            raise OSError('Install Training essentials in Libraries before starting a model')
        environment.require_current_runtime()
        environment.require_compute()
        done = subprocess.run([str(environment.python), '-I', '-c', _PROBE, json.dumps(required)],
                              capture_output=True, text=True, timeout=20, env=environment_for_child())
        if done.returncode:
            raise OSError(f'Install the {engine} libraries in Libraries before starting this model')
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ServerFailed(str(exc)) from exc
    return [str(environment.python), '-I', '-m', module]


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
