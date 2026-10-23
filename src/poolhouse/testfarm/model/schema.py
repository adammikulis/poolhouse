"""The keys of testfarm.toml schema 1, table by table, each with the kind of value it holds (section 4.1 of the design lists the same keys)."""

from __future__ import annotations

from poolhouse.testfarm.model import kinds as k

__all__ = ["KEYS", "RUN_PLACEHOLDERS", "STEP_PLACEHOLDERS", "TABLES", "TOP"]

RUN_PLACEHOLDERS = ("ids", "idfile", "result", "extra", "jobs")
STEP_PLACEHOLDERS = ("cache", "shared", "jobs")
_COMMAND = k.argv(("jobs",))
_TOOLS = k.argvs(("jobs",))

TABLES: dict[str, dict[str, k.Kind]] = {
    "project": {"name": k.text},
    "tree": {"include": k.file_globs, "exclude": k.file_globs},
    "list": {"run": _COMMAND, "format": k.choice("lines", "jsonl"), "starts": k.literal, "ends": k.literal, "group": k.literal,
             "allow_empty": k.boolean, "timeout": k.seconds, "glob": k.file_glob},
    "run": {"argv": k.argv(RUN_PLACEHOLDERS, ("ids", "extra")), "ids_after": k.literal, "refuse_prefix": k.strings,
            "slice": k.argv((*RUN_PLACEHOLDERS, "slice_index", "slice_count"), ("ids", "extra")), "slice_base": k.choice(0, 1),
            "slice_count": k.slice_count, "slices_per_target": k.positive_int, "slice_results": k.choice("exit", "jsonl", "junit"),
            "results": k.choice("exit", "jsonl", "junit"), "junit_id": k.template("classname", "name"), "batch": k.positive_int, "reuse": k.choice("exit"),
            "test_timeout": k.seconds, "timeout": k.seconds, "env": k.envmap, "launcher": k.argv(), "needs": k.names, "sandbox": k.sandbox},
    "run.sandbox": {"deny_read": k.file_globs, "deny_write": k.file_globs},
    "deps": {"globs": k.glob_map, "command": _COMMAND, "self": k.file_globs, "timeout": k.seconds, "everything": k.file_globs},
    "step": {"name": k.name, "run": k.argv(STEP_PLACEHOLDERS), "inputs": k.file_globs, "tools": _TOOLS, "env": k.envmap,
             "slots": k.positive_int, "timeout": k.seconds, "keep": k.seconds, "prune": k.argv(STEP_PLACEHOLDERS)},
    "label": {"match": k.strings, "lease": k.lease, "serial": k.name, "platform": k.names, "local": k.boolean, "net": k.choice("deny", "loopback"),
              "reuse": k.boolean, "slots": k.positive_int, "estimate": k.seconds, "timeout": k.seconds, "needs": k.names},
    "set": {"labels": k.names, "not_labels": k.names, "ids": k.strings, "extra": k.strings, "reuse": k.boolean},
    "reuse": {"key_env": k.env_names, "key_tools": _TOOLS, "canary": k.rate},
    "failfirst": {"tests": k.file_globs, "line": k.template("id", "sha", "error"), "exempt": k.literal, "mode": k.choice("off", "report", "enforce")},
    "schedule": {"seed": k.file_glob, "unrecorded_max": k.seconds, "silence_margin": k.margin, "total_margin": k.margin, "queue_wait": k.seconds,
                 "background_after": k.seconds},
    "requires": {"python": k.version, "module": k.word_list, "exe": k.exe_list},
    "unit": {"name": k.name, "run": _COMMAND, "inputs": k.file_globs, "tools": _TOOLS, "labels": k.names, "timeout": k.seconds,
             "slots": k.positive_int, "env": k.envmap},
    "hooks": {"before_plan": _TOOLS, "before_run": _TOOLS, "after_run": _TOOLS, "timeout": k.seconds},
}
"""Each table's keys and the kind of value each holds."""

TOP = ("schema", "preset", "presets", "project", "tree", "list", "run", "deps", "step", "label", "set", "reuse", "failfirst", "schedule",
       "requires", "unit", "hooks")
"""The keys of the file itself."""

KEYS: frozenset[str] = frozenset(f"{table}.{key}" for table, keys in TABLES.items() for key in keys)
"""Every ``table.key`` the schema holds."""
