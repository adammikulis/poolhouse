"""The profile of one confinement: the engine policy a launch runs under, and a digest of everything in it.

`build` turns a `Spec` and the grants in force into one `sandbox.Policy`: the stage and the agent home are the only writable trees (the stage
is read-only for `read-only` and for plan mode, and a session with a writable stage runs the programs in both), the harness's install tree, the
interpreter, the system toolchain, the derived toolchain folders and the granted reads are readable, the control names are frozen in the stage,
the network is the model bridge's port, the egress proxy's and, for an editing session, TCP within its own window of loopback ports, no address
is bound by the session, and the unix sockets are the session bridge's, the window's (`isolation.ports`) and those of a harness's own server. The
same object runs the harness and the probe of the proof, so what the proof shows is what the harness gets (docs/design-isolated-launcher.md, 3.2,
5.2 and 8.1).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from poolhouse import secretpaths
from poolhouse.coding_workspace_state import CONTROL_PREFIXES
from poolhouse.isolation import toolchain
from poolhouse.isolation.grants import Grant
from poolhouse.isolation.spec import Spec
from poolhouse.isolation.toolchain import system_toolchain
from poolhouse.sandbox import Limits, Net, Policy
from poolhouse.sandbox.policies import SYSTEM_EXEC
from poolhouse.sandbox.run import fair_share

__all__ = ["Profile", "build", "interpreter_reads", "search_path"]

GIT_DIRECTORY = ".git"
"""The stage's git directory, which the launcher reads again after the run (its index names what is captured): a session reads it, so `git diff` and
`git status` work, and writes nothing in it, so a commit in the box fails."""
PREFERENCES = {"codex": ("com.openai.codex",)}
"""The preference domains a harness reads on macOS (found by running it under the profile: Codex fails to start without its own domain, which it
reads for the settings an administrator manages), and no other."""

@dataclass(frozen=True, slots=True)
class Profile:
    """The policy, the directories it names, the grants it holds and the digest the probe echoes."""

    policy: Policy
    stage: str
    home: str
    path: tuple[str, ...]
    grants: tuple[Grant, ...]
    digest: str
    harness: str = ""
    interactive: bool = False
    read_only: bool = False

    def refuses_write(self, path: str) -> bool:
        """Whether this profile lets nothing write ``path`` (absolute): it is in no tree a session writes, or in one the profile freezes there.
        Links are followed, as the box follows them; a device is nobody's file."""
        real = os.path.realpath(path)
        if real.startswith("/dev/"):
            return False

        def folded(each: str) -> tuple[str, ...]:
            return tuple(secretpaths.fold(part) for part in Path(each).parts)

        def within(trees: Sequence[str]) -> bool:
            return any(folded(real)[:len(root)] == root for root in (folded(os.path.realpath(tree)) for tree in trees))

        return within(self.policy.deny_write) or not within(self.policy.write)


def interpreter_reads() -> tuple[str, ...]:
    """The trees the interpreter that runs the bridge client and the probe reads: its own prefixes, and no more of Homebrew than the
    system toolchain already grants."""
    return tuple(sorted({os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix)}))


def _git_directory() -> tuple[str, ...]:
    """The directory of a git a confined shell can run: not Apple's `/usr/bin/git`, a shim that asks a service the profile refuses."""
    found = shutil.which("git")
    real = os.path.realpath(found) if found else ""
    return (str(Path(real).parent),) if real and real != "/usr/bin/git" else ()


def _digest(policy: Policy, grants: Sequence[Grant], spec: Spec) -> str:
    """A digest of what the policy grants and of the grants in force. The environment contributes the names it sets and never a value,
    so a token the launcher minted is never in a digest."""
    body = {"harness": spec.harness, "name": policy.name, "read": policy.read, "write": policy.write, "exec": policy.exec,
            "net": [policy.net.mode.value, policy.net.ports, policy.net.accept], "env": sorted(policy.env), "strict": policy.strict,
            "preferences": policy.preferences, "user_records": policy.user_records,
            "deny_write": policy.deny_write, "unix_sockets": policy.unix_sockets, "unix_namespaces": policy.unix_namespaces,
            "signal_children": policy.signal_children,
            "limits": [policy.limits.processes, policy.limits.wall_seconds, policy.limits.cpu_seconds, policy.limits.file_bytes,
                       policy.limits.open_files], "gpu": policy.gpu,
            "grants": sorted(f"{grant.kind}:{grant.subject}:{grant.fingerprint}" for grant in grants)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def build(spec: Spec, grants: Sequence[Grant] = (), env: Mapping[str, str] | None = None, limits: Limits | None = None) -> Profile:
    """The profile of ``spec`` with ``grants`` in force and ``env`` the whole environment the harness gets (`isolation.environment`)."""
    reads = [each for grant in grants if grant.kind in ("read", "toolchain") for each in (toolchain.reach(grant.subject) if grant.kind == "toolchain" else (grant.subject,))]
    runs = [each for grant in grants if grant.kind == "toolchain" for each in toolchain.reach(grant.subject)]
    writable = [spec.home] if spec.read_only_stage else [spec.stage, spec.home]
    own = () if spec.read_only_stage else (spec.stage, spec.home)
    """What an editing session runs of its own: the programs it wrote, a project's `scripts/test` among them. It already runs them through a shell
    or an interpreter, in this same box, so this reaches nothing new."""
    readable = [spec.stage, spec.install, *interpreter_reads(), *system_toolchain(), *_git_directory(), *reads, *spec.environments,
                *([spec.patches] if spec.patches else [])]
    ports = (spec.model_port, *([spec.proxy_port] if spec.proxy_port else []))
    policy = Policy(f"coding-{spec.harness}", read=tuple(dict.fromkeys(readable)), write=tuple(writable),
                    exec=tuple(dict.fromkeys([*SYSTEM_EXEC, os.path.realpath(sys.executable), spec.install, *system_toolchain(), *runs,
                                              *_git_directory(), *spec.environments, *own])),
                    net=Net.only(*ports, accept=spec.window), env=dict(env or {}), strict=True, signal_children=True, preferences=PREFERENCES.get(spec.harness, ()), user_records=True,
                    unix_sockets=tuple(each for each in (spec.session_socket, spec.window_socket) if each),
                    unix_namespaces=(spec.server,) if spec.server else (),
                    deny_write=(*(str(Path(spec.stage) / name) for name in (*CONTROL_PREFIXES, GIT_DIRECTORY)), *spec.frozen),
                    limits=limits or Limits(wall_seconds=None, output_bytes=None, processes=fair_share()))
    return Profile(policy, spec.stage, spec.home, search_path(spec, grants), tuple(grants), _digest(policy, grants, spec), spec.harness,
                   spec.interactive, spec.read_only_stage)


def search_path(spec: Spec, grants: Sequence[Grant] = ()) -> tuple[str, ...]:
    """The directories of ``PATH`` a session runs with: the agent home's shims, the programs of the project's environments, the git it can run,
    the programs of Homebrew and `/usr/local`, those of Apple's command-line tools (ahead of `/usr/bin`, whose `python3` and `git` are shims that
    ask a service the profile refuses), the system, and each granted toolchain."""
    own = (Path(each) / (".bin" if Path(each).name == "node_modules" else "bin") for each in spec.environments)
    trees = system_toolchain()
    local = (*(each for each in trees if each.endswith("/bin")), *(f"{each}/usr/bin" for each in trees if each == toolchain.COMMAND_LINE_TOOLS))
    return tuple(dict.fromkeys((str(Path(spec.home) / "bin"), *(str(each) for each in own if each.is_dir()), *_git_directory(), *local, *SYSTEM_EXEC,
                                *(str(Path(grant.subject) / "bin") for grant in grants if grant.kind == "toolchain"))))
