"""Verified Python commands for broker-owned model servers."""
from __future__ import annotations

import subprocess
import sys

from poolhouse import maintenance, provision
from poolhouse.fleet.environment import Environment, provide
from poolhouse.fleet.managed_compute import process_environment
from poolhouse.fleet.runtime_paths import default_root
from poolhouse.paths import source_checkout
from poolhouse.serve.backend import ServerFailed

_MODULES = {
    'mlx-tree': ('poolhouse.serve.mlx_tree_server', ('mlx', 'mlx_lm', 'mlx_vlm')),
    'vllm': ('vllm.entrypoints.openai.api_server', ('vllm',)),
    'sglang': ('sglang.launch_server', ('sglang',)),
}
_LIBRARIES = {'mlx-tree': 'serve-mlx', 'strata': 'serve-strata'}
"""The engines with a managed library, installed the first time one starts. vLLM and SGLang have none: they run on
Linux with NVIDIA or AMD cards, which this build does not install for."""
_CHECKOUT = {'strata': ('serve-strata', ('numpy', 'jinja2', 'regex'))}
"""The engines run as scripts of their own installed source: the extra a source checkout's environment is given."""
_PROBE = """import importlib.util,json,sys
assert all(importlib.util.find_spec(n) is not None for n in json.loads(sys.argv[1]))
if sys.argv[2]=='mlx-tree':
 from poolhouse.serve.python_requirements import installed_mlx
 assert installed_mlx(), 'MLX serving dependency versions differ'
"""


@maintenance.reading
def command(engine: str) -> list[str]:
    """Return the fixed engine module under its verified compute interpreter."""
    module, required = _MODULES[engine]
    if source_checkout():
        return [sys.executable, '-m', module]
    return [*_managed(engine, required), '-m', module]


@maintenance.reading
def interpreter(engine: str) -> list[str]:
    """Return the isolated Python that runs the scripts of ``engine``, its libraries installed first when they are missing."""
    extra, required = _CHECKOUT[engine]
    if source_checkout():
        try:
            provision.ensure(extra, *required)
        except ImportError as exc:
            raise ServerFailed(str(exc)) from exc
        return [sys.executable, '-B', '-I']
    return _managed(engine, required)


def _managed(engine: str, required: tuple[str, ...]) -> list[str]:
    """The verified compute interpreter holding ``required``, as the command line every module and script of it starts with."""
    import json

    environment = Environment(default_root())

    def ready() -> None:
        if not environment.exists:
            raise OSError(f'The libraries {engine} runs on have no environment yet')
        environment.require_current_runtime()
        environment.require_compute()
        done = subprocess.run([str(environment.python), '-B', '-I', '-c', _PROBE, json.dumps(required), engine],
                              capture_output=True, text=True, timeout=20, env=environment_for_child())
        if done.returncode:
            raise OSError(f'The {engine} libraries are not in the managed environment')

    try:
        if engine in _LIBRARIES:
            provide(environment, ['core', _LIBRARIES[engine]], ready)
        else:
            ready()
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ServerFailed(str(exc)) from exc
    return [str(environment.python), '-B', '-I']


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
