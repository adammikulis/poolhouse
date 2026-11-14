"""Start, adopt and tear down local model servers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from poolhouse import lazy

# Names resolve on first use: importing one submodule of this package (`poolhouse.serve.backend`,
# `poolhouse.serve.grant`) must not load the manager, the broker's clients and everything they import.
if TYPE_CHECKING:  # what the names below resolve to, for readers and type checkers; `_HOME` is what runs
    from poolhouse.serve.backend import (
        LlamaServerBackend,
        ServerBackend,
        ServerFailed,
        ServerInfo,
        ServerSpec,
        parse_context,
        tail,
        trained_context,
    )
    from poolhouse.serve.binary import BinaryNotFound, child_env, find_binary, require_binary
    from poolhouse.serve.escalation import EscalationRefused
    from poolhouse.serve.leases import merge_state, recorded_servers
    from poolhouse.serve.manager import Measuring, ServerManager, serve, stop_all_servers
    from poolhouse.serve.matching import model_matches, serving_mismatch
    from poolhouse.serve.ports import (
        DEFAULT_HOST,
        free_port,
        port_is_free,
        reclaim_port,
        server_pids_on_port,
        wait_until_free,
    )
    from poolhouse.serve.process import kill_pid, kill_process_tree, pid_exists
    from poolhouse.serve.profile import Profile, profile_for, profiles
    from poolhouse.serve.public import Served, down, status, up
    from poolhouse.serve.serving import Config, Serving, Talking, draft_for, projector_for, slot
    from poolhouse.serve.slotdump import (
        SlotDump,
        SlotGuardRefused,
        restore_all,
        restore_slot,
        save_all,
        save_slot,
    )

_HOME = {
    "BinaryNotFound": ("poolhouse.serve.binary", "BinaryNotFound"),
    "Config": ("poolhouse.serve.serving", "Config"),
    "DEFAULT_HOST": ("poolhouse.serve.ports", "DEFAULT_HOST"),
    "EscalationRefused": ("poolhouse.serve.escalation", "EscalationRefused"),
    "LlamaServerBackend": ("poolhouse.serve.backend", "LlamaServerBackend"),
    "Measuring": ("poolhouse.serve.manager", "Measuring"),
    "Profile": ("poolhouse.serve.profile", "Profile"),
    "Served": ("poolhouse.serve.public", "Served"),
    "ServerBackend": ("poolhouse.serve.backend", "ServerBackend"),
    "ServerFailed": ("poolhouse.serve.backend", "ServerFailed"),
    "ServerInfo": ("poolhouse.serve.backend", "ServerInfo"),
    "ServerManager": ("poolhouse.serve.manager", "ServerManager"),
    "ServerSpec": ("poolhouse.serve.backend", "ServerSpec"),
    "Serving": ("poolhouse.serve.serving", "Serving"),
    "SlotDump": ("poolhouse.serve.slotdump", "SlotDump"),
    "SlotGuardRefused": ("poolhouse.serve.slotdump", "SlotGuardRefused"),
    "Talking": ("poolhouse.serve.serving", "Talking"),
    "child_env": ("poolhouse.serve.binary", "child_env"),
    "down": ("poolhouse.serve.public", "down"),
    "draft_for": ("poolhouse.serve.serving", "draft_for"),
    "find_binary": ("poolhouse.serve.binary", "find_binary"),
    "free_port": ("poolhouse.serve.ports", "free_port"),
    "kill_pid": ("poolhouse.serve.process", "kill_pid"),
    "kill_process_tree": ("poolhouse.serve.process", "kill_process_tree"),
    "merge_state": ("poolhouse.serve.leases", "merge_state"),
    "model_matches": ("poolhouse.serve.matching", "model_matches"),
    "parse_context": ("poolhouse.serve.backend", "parse_context"),
    "pid_exists": ("poolhouse.serve.process", "pid_exists"),
    "port_is_free": ("poolhouse.serve.ports", "port_is_free"),
    "profile_for": ("poolhouse.serve.profile", "profile_for"),
    "profiles": ("poolhouse.serve.profile", "profiles"),
    "projector_for": ("poolhouse.serve.serving", "projector_for"),
    "reclaim_port": ("poolhouse.serve.ports", "reclaim_port"),
    "recorded_servers": ("poolhouse.serve.leases", "recorded_servers"),
    "require_binary": ("poolhouse.serve.binary", "require_binary"),
    "restore_all": ("poolhouse.serve.slotdump", "restore_all"),
    "restore_slot": ("poolhouse.serve.slotdump", "restore_slot"),
    "save_all": ("poolhouse.serve.slotdump", "save_all"),
    "save_slot": ("poolhouse.serve.slotdump", "save_slot"),
    "serve": ("poolhouse.serve.manager", "serve"),
    "server_pids_on_port": ("poolhouse.serve.ports", "server_pids_on_port"),
    "serving_mismatch": ("poolhouse.serve.matching", "serving_mismatch"),
    "slot": ("poolhouse.serve.serving", "slot"),
    "status": ("poolhouse.serve.public", "status"),
    "stop_all_servers": ("poolhouse.serve.manager", "stop_all_servers"),
    "tail": ("poolhouse.serve.backend", "tail"),
    "trained_context": ("poolhouse.serve.backend", "trained_context"),
    "up": ("poolhouse.serve.public", "up"),
    "wait_until_free": ("poolhouse.serve.ports", "wait_until_free"),
}

__all__ = [
    "DEFAULT_HOST",
    "BinaryNotFound",
    "Config",
    "EscalationRefused",
    "LlamaServerBackend",
    "Measuring",
    "Profile",
    "Served",
    "ServerBackend",
    "ServerFailed",
    "ServerInfo",
    "ServerManager",
    "ServerSpec",
    "Serving",
    "SlotDump",
    "SlotGuardRefused",
    "Talking",
    "child_env",
    "down",
    "draft_for",
    "find_binary",
    "free_port",
    "kill_pid",
    "kill_process_tree",
    "merge_state",
    "model_matches",
    "parse_context",
    "pid_exists",
    "port_is_free",
    "profile_for",
    "profiles",
    "projector_for",
    "reclaim_port",
    "recorded_servers",
    "require_binary",
    "restore_all",
    "restore_slot",
    "save_all",
    "save_slot",
    "serve",
    "server_pids_on_port",
    "serving_mismatch",
    "slot",
    "status",
    "stop_all_servers",
    "tail",
    "trained_context",
    "up",
    "wait_until_free",
]

__getattr__, __dir__ = lazy.exports(__name__, globals(), _HOME)
