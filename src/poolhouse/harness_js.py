"""The JavaScript a node-based harness (Pi, OpenCode) runs inside the confinement to ask the parent's classifier before a tool call.

Each harness's extension is its own small file, written read-only by the launcher; what they share is here: the client command that reaches the
session bridge (`call`, a synchronous round trip that fails closed) and the map from the harness's lowercase tool names to the names the
classifier knows.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["NAMES", "prelude"]

NAMES = {"bash": "Bash", "powershell": "PowerShell", "read": "Read", "edit": "Edit", "write": "Write", "grep": "Grep", "find": "Glob",
         "glob": "Glob", "ls": "LS", "list": "LS", "webfetch": "WebFetch"}


def prelude(settings: dict[str, Any]) -> str:
    """The module head every extension starts with: ``cfg`` (``settings``, whose ``hook`` is the client command), ``names`` and ``call(event, payload)``,
    which returns the parsed answer or ``{failed: true}``."""
    return f'''import {{ spawnSync }} from "node:child_process";
const cfg = {json.dumps(settings)};
const names = {json.dumps(NAMES)};
function call(event, payload) {{
  const result = spawnSync(cfg.hook[0], [...cfg.hook.slice(1), event], {{input:JSON.stringify(payload),encoding:"utf8",timeout:310000}});
  if (result.error || result.status !== 0) return {{failed:true}};
  try {{ return JSON.parse(result.stdout || "{{}}") }} catch {{ return {{failed:true}} }}
}}
'''
