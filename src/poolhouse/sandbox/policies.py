"""The policies tools run under: a shell, an MCP server, a model server."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from poolhouse.sandbox.policy import Limits, Net, Policy
from poolhouse.sandbox.run import fair_share

if os.name == "nt":
    import win32api

__all__ = ["SYSTEM_EXEC", "bash", "local_net", "mcp_server", "model_server", "runtime_reads", "scratch",
           "system_env"]

SYSTEM_EXEC = ((os.path.realpath(win32api.GetSystemDirectory()),) if os.name == "nt" else
               tuple(dict.fromkeys(os.path.realpath(path)
                                   for path in ("/bin", "/usr/bin", "/sbin", "/usr/sbin"))))
"""The directories of system programs a shell may start."""


def system_env(**extra: str) -> dict[str, str]:
    """A minimal environment: a PATH of system directories, a locale, and ``extra``."""
    return {"PATH": os.pathsep.join(SYSTEM_EXEC), "LANG": "en_US.UTF-8", **extra}


def local_net() -> Net:
    """The network a tool gets when its caller names none: the host's loopback where the platform can scope it (macOS), none where it
    cannot (Linux gives a process the host's whole network or none of it, so a service on the loopback is named with `Net.only`)."""
    return Net.loopback() if sys.platform == "darwin" else Net.deny()


def runtime_reads() -> tuple[str, ...]:
    """The trees this Python interpreter reads: its prefixes, and Homebrew's libraries."""
    found = {os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix)}
    if Path("/opt/homebrew").is_dir():
        found.add("/opt/homebrew")
    return tuple(sorted(found))


@contextmanager
def scratch(prefix: str = "poolhouse-sandbox-") -> Iterator[str]:
    """A fresh private directory (resolved), removed afterwards."""
    with tempfile.TemporaryDirectory(prefix=prefix) as made:
        yield os.path.realpath(made)


def bash(project: str | os.PathLike[str], scratch_dir: str | os.PathLike[str], *,
         wall_seconds: float = 120.0) -> Policy:
    """A shell that reads the project and the scratch directory, writes only the scratch
    directory and has no network. HOME and TMPDIR are the scratch directory."""
    scratch_path = os.path.realpath(scratch_dir)
    return Policy("bash", read=(os.path.realpath(project), scratch_path), write=(scratch_path,),
                  exec=SYSTEM_EXEC, net=Net.deny(),
                  env=system_env(HOME=scratch_path, TMPDIR=scratch_path),
                  limits=Limits(wall_seconds=wall_seconds, cpu_seconds=int(wall_seconds) + 5,
                                file_bytes=256 * 1024 * 1024, open_files=512, processes=fair_share()))


def mcp_server(command: str, project: str | os.PathLike[str],  # noqa: PLR0913
               scratch_dir: str | os.PathLike[str], *, env: Mapping[str, str] | None = None,
               reads: Sequence[str] = (), net: Net | None = None) -> Policy:
    """An MCP server started from ``command``: it reads the interpreter, the project and
    ``reads``, writes the scratch directory, and reaches ``net`` (default `local_net`). ``env`` is the whole
    environment it sees besides PATH, HOME and TMPDIR."""
    scratch_path = os.path.realpath(scratch_dir)
    program = os.path.realpath(command)
    return Policy("mcp-server", read=(*runtime_reads(), os.path.realpath(project), *reads),
                  write=(scratch_path,), exec=(program, *SYSTEM_EXEC),
                  net=net or local_net(),
                  env=system_env(HOME=scratch_path, TMPDIR=scratch_path, **(env or {})),
                  limits=Limits(wall_seconds=None, output_bytes=None, processes=fair_share()))


def model_server(binary: str | os.PathLike[str], model_dir: str | os.PathLike[str], *,
                 cache: str | os.PathLike[str] | None = None, gpu: bool = True) -> Policy:
    """A model server: reads its binary's install tree (its directory, or the prefix above a ``bin``) and the model directory, the
    loopback where the platform can scope it (`local_net`), the GPU when ``gpu``."""
    real = os.path.realpath(binary)
    home = Path(real).parent
    install = home.parent if home.name == "bin" else home
    libraries = "/opt/homebrew" if real.startswith("/opt/homebrew/") else str(install)
    return Policy("model-server", read=(libraries, os.path.realpath(model_dir)),
                  exec=(real,), net=local_net(), gpu=gpu,
                  cache=os.path.realpath(cache) if cache else "",
                  env=system_env(HOME=str(cache or "/var/empty")),
                  limits=Limits(wall_seconds=None, output_bytes=None, processes=fair_share()))

