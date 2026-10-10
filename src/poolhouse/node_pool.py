"""The node's pool of devices, for a machine that joined it without a pool key.

The node (``poolhouse-node run --lan``) takes the project of the repository it runs in, listens for an open pool of that
project, joins the one it hears and makes one when it hears none (docs/sentinel-open-join.md, 3.7). Nothing here asks for
a key file: this reads who is in that pool from the running node.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from poolhouse import node_health
from poolhouse.checks import Finding
from poolhouse.home import state as state_root

LAN = "--lan"


def network_args(*, project: str = "", project_dir: str = "") -> list[str]:
    """The arguments that make a node reach its pool on the LAN: ``ensure_node(state, extra=network_args())``.

    The project is that of the repository the node is started in; ``project`` names one instead and ``project_dir``
    takes the repository from another directory.
    """
    return [LAN, *(["--project", project] if project else []), *(["--project-dir", project_dir] if project_dir else [])]


def ask(state: Path | None = None) -> tuple[dict | None, str]:
    """The pool the node of ``state`` belongs to and, when no node answers, why not (the error the connection or the node gave)."""
    try:
        return node_health.call(state or state_root("node"), "pool_status"), ""
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def status(state: Path | None = None) -> dict | None:
    """The pool the node of ``state`` belongs to (id, project, policy, members), or None when no node answers."""
    return ask(state)[0]


def rows(pool: dict) -> list[dict]:
    """One row per active member of a pool status: name, fingerprint, address, whether it is this machine or connected."""
    return [{"name": m.get("name") or m["fingerprint"][:12], "fingerprint": m["fingerprint"], "addr": m.get("addr") or "",
             "state": "this machine" if m.get("self") else "connected" if m.get("connected") else "known",
            "build": str(m.get("build", ""))[:7], "build_state": m.get("build_state", "unknown"), "fix": m.get("fix", "")}
            for m in pool.get("members", []) if m.get("status") == "active"]


def render(pool: dict, *, as_json: bool) -> str:
    """The text `poolhouse-members ls` prints for a pool."""
    if as_json:
        return json.dumps({"pool": pool.get("pool"), "project": pool.get("project"), "policy": pool.get("policy"),
                           "build": pool.get("build"), "members": rows(pool)}, indent=2)
    lines = [f"pool {pool.get('pool')}  project {pool.get('project') or '-'}  join policy {pool.get('policy')}",
             f"{'NAME':<20} {'STATE':<13} {'ADDRESS':<24} {'BUILD':<8} {'BUILD STATE':<13} FINGERPRINT"]
    lines += [f"{r['name']:<20} {r['state']:<13} {r['addr']:<24} {r['build'] or '-':<8} {r['build_state']:<13} {r['fingerprint'][:16]}" for r in rows(pool)]
    lines += [f"{r['name']}: {r['fix']}" for r in rows(pool) if r["fix"]]
    return "\n".join(lines)


def build_line(pool: dict | None) -> str:
    """One line for `runtime status`: this device's build, and whether it is stale, pinned or incompatible with a member."""
    if pool is None:
        return "build        unknown (no node answers)"
    build = pool.get("build") or {}
    state = {"catching up": "STALE (catching up)", "needs update": "NEEDS UPDATE"}.get(str(build.get("state")), str(build.get("state", "unknown")))
    return f"build        {str(build.get('build', ''))[:7] or 'unknown'} (#{build.get('build_seq', 0)}, wire {build.get('wire', 0)}) {state}"


def member_lines(pool: dict) -> list[str]:
    """One line per member that is not on this device's build, with the exact fix for a wire mismatch."""
    return [f"  {m.get('name') or str(m.get('fingerprint', ''))[:12]:<18} {m.get('build_state', 'unknown'):<13} {str(m.get('build', ''))[:7]} (#{m.get('build_seq', 0)})"
            + (f"  fix: {m['fix']}" if m.get("fix") else "")
            for m in pool.get("members", []) if m.get("status") == "active" and not m.get("self") and m.get("build_state") not in ("current",)]


def build_findings(pool: dict | None = None) -> list[Finding]:
    """The doctor's rows: this device's build and each member's, a stale or incompatible one as a failure with its fix."""
    pool = pool if pool is not None else status()
    if pool is None:
        return [Finding(name="build", good=True, said="no node answers, so no member's build is known")]
    state = str((pool.get("build") or {}).get("state", "unknown"))
    out = [Finding(name="build", good=state in ("current", "pinned"), said=build_line(pool).removeprefix("build        "),
                   fix=[] if state in ("current", "pinned") else [sys.executable, "-m", "poolhouse.runtime_cli", "update"])]
    out += [Finding(name=f"build: {m.get('name') or str(m.get('fingerprint', ''))[:12]}", good=m.get("build_state") in ("current", "older", "pinned", "self"),
                    said=f"{m.get('build_state')} {str(m.get('build', ''))[:7]} (#{m.get('build_seq', 0)})", note=str(m.get("fix", "")))
            for m in pool.get("members", []) if m.get("status") == "active" and not m.get("self")]
    return out
