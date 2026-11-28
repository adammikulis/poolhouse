"""Serving Qwen3.8-Flash-Next with Strata behind the same lease as llama-server.

A spec whose ``engine`` is ``"strata"`` is installed and prepared at first use, then served by Strata's own server on loopback
with the key Poolhouse issues, which spawns the engine as its child. Images are off, no request may name a file or an address,
and no MCP server or command is configured.
"""
from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import psutil

from poolhouse import errors, serverkeys
from poolhouse.files import write_json
from poolhouse.http import Options, request_json
from poolhouse.serve import confined as confinement, grant, kvcache, python_runtime
from poolhouse.serve.backend import (
    Lease,
    ServerBackend,
    ServerFailed,
    ServerInfo,
    ServerSpec,
    claim_port,
    fetched,
    launch,
    server_log,
)
from poolhouse.serve.logs import tail
from poolhouse.serve.ports import DEFAULT_HOST
from poolhouse.serve.strata import config, install, pins, prepare

__all__ = ["FLAGS", "NAME", "StrataBackend", "asked_drafter", "asked_options", "overridden"]

logger = logging.getLogger(__name__)

NAME = "strata"
FLAGS = ("--spec", "--spec-min-p", "--draft-conf", "--expert-cache", "--prefill", "--vram-reserve-mib", "--kv-resident", "--kv",
         "--suffix-draft", "--pool-workers", "--ple-io")
"""The engine options a spec's ``extra_args`` may set, each with one value; none of them names a file or an address."""
_KV = {"q8_0": "int8", "f16": "fp16", "q4_0": "q4_0"}
_UNSERVED = (("mmproj", "reading pictures"), ("embedding", "embeddings"), ("reranking", "reranking"),
             ("slot_save_path", "saving slots to disk"), ("chat_template_file", "another chat template"),
             ("override_tensor", "placing tensors by name"), ("cpu_moe", "keeping experts on the CPU by flag"),
             ("n_cpu_moe", "keeping experts on the CPU by flag"), ("lookup_dynamic", "an n-gram cache on disk"),
             ("lookup_static", "an n-gram cache on disk"), ("spec_tree", "tree decoding"), ("rope_scaling", "RoPE scaling"),
             ("context_per_slot", "a context of its own for each slot"))
"""What a spec may ask of llama-server that a Strata server is not given, with what to call it."""


def asked_drafter(spec: ServerSpec) -> str:
    """The drafter ``spec`` names: ``off`` for one that switches drafting off, ``mtp``, ``dflash2``, or empty for the best one.
    `ServerFailed` for any other draft."""
    if spec.mtp is False:
        return "off"
    named = str(spec.draft or spec.spec_type or "").strip().lower()
    if named in ("", "auto"):
        return ""
    if named not in ("mtp", "dflash2"):
        raise ServerFailed(f"Strata drafts with its own drafters, mtp or dflash2, not {named!r}; "
                           "leave the draft out for the best one")
    return named


def asked_options(spec: ServerSpec) -> dict[str, str]:
    """The engine options ``spec`` names, flag by flag; `ServerFailed` for one outside `FLAGS` or without a value."""
    wanted: dict[str, str] = {}
    if spec.spec_draft_max is not None:
        wanted["--spec"] = str(spec.spec_draft_max)
    if spec.spec_p_min is not None:
        wanted["--spec-min-p"] = str(spec.spec_p_min)
    extra = list(spec.extra_args)
    if len(extra) % 2 or any(flag not in FLAGS or value.startswith("-") for flag, value in zip(extra[::2], extra[1::2], strict=False)):
        raise ServerFailed(f"a Strata server takes these options, each with a value: {', '.join(FLAGS)}; not {' '.join(extra)}")
    wanted.update(zip(extra[::2], extra[1::2], strict=True))
    return wanted


def overridden(args: list[str], spec: ServerSpec) -> list[str]:
    """``args`` with the engine options ``spec`` names in place of the ones chosen for it."""
    out = list(args)
    for flag, value in asked_options(spec).items():
        if flag in out[:-1]:
            out[out.index(flag) + 1] = value
        else:
            out += [flag, value]
    return out


def _cuda_libraries(python: list[str]) -> list[str]:
    """The folders of the NVIDIA libraries installed for the Windows interpreter of ``python``: an environment keeps its program in
    ``Scripts``, a plain installation beside ``Lib``."""
    home = Path(python[0]).parent
    return sorted({str(found.parent) for base in (home, home.parent) for name in ("cublas64_13.dll", "cudart64_13.dll")
                   for found in (base / "Lib" / "site-packages" / "nvidia").rglob(name)})


class StrataBackend(ServerBackend):
    """Strata's server and the engine it runs."""

    name = NAME
    drafts_itself = True
    escalates = False
    renders_late_system = True

    def __init__(self, *, sandboxed: bool | None = None) -> None:
        self.sandboxed = sandboxed

    def sandboxing(self, sandboxed: bool | None) -> StrataBackend:
        """A backend that knows confinement was asked for, so that its start refuses rather than run the server unconfined."""
        return StrataBackend(sandboxed=True) if sandboxed else self

    @staticmethod
    def model_of(spec: ServerSpec) -> tuple[pins.ModelSet, tuple[Path, ...]]:
        """The pinned set ``spec.model`` is the first file of, and its files; `ServerFailed` for any other file."""
        found = pins.recognised(spec.model)
        if found is None:
            raise ServerFailed(f"{spec.model} is not a model Strata is pinned to serve: give the first file of one of these, with "
                               "every file of it beside it: " + ", ".join(one.reference for one in pins.model_sets()))
        return found

    def estimate_bytes(self, spec: ServerSpec) -> int:
        """What serving ``spec`` takes of this device's memory; 0 for a model not on disk."""
        found = pins.recognised(spec.model)
        if found is None:
            return 0
        kind, memory = install.platform_kind(), psutil.virtual_memory().total
        slots = max(1, int(spec.parallel or 1))
        context = int(spec.context) or config.fitted_context(found[0], kind, memory, slots) * slots
        return config.estimate_bytes(found[0], context, kind, memory)

    def fit(self, spec: ServerSpec) -> tuple[ServerSpec, str]:
        """``spec`` with the context this device holds when it names none."""
        if int(spec.context) > 0:
            return spec, ""
        chosen, _files = self.model_of(spec)
        kind, memory = install.platform_kind(), psutil.virtual_memory().total
        slots = max(1, int(spec.parallel or 1))
        context = config.fitted_context(chosen, kind, memory, slots)
        return replace(spec, context=context * slots), f"context {context:,} tokens a slot, what Strata's rules hold on this device"

    def command(self, spec: ServerSpec) -> list[str]:
        """The server's argv, apart from the interpreter and the source the installed engine gives it."""
        return ["--engine", "strata", "--config", str(self._config_path(spec.port)), "--host", DEFAULT_HOST,
                "--port", str(spec.port), "--offline", "--parallel", str(max(1, int(spec.parallel or 1)))]

    @staticmethod
    def _config_path(port: int) -> Path:
        return serverkeys.run_folder(port) / "config.json"

    def _refuse_unserved(self, spec: ServerSpec) -> None:
        for field, what in _UNSERVED:
            if getattr(spec, field):
                raise ServerFailed(f"a Strata server is not offered {what} ({field}); serve this with llama.cpp")
        if confinement.wanted(self.sandboxed):
            raise ServerFailed("the model_server sandbox cannot hold Strata's server yet (it listens on a TCP port); "
                               "serve with llama.cpp, or without the sandbox")

    def written(self, spec: ServerSpec, job: prepare.Job, made: prepare.Prepared, shaped: config.Shape) -> dict[str, Any]:
        """The run config of one start, without its key and library folders."""
        run = self._config_path(spec.port).parent
        args = list(shaped.args)
        kept = kvcache.effective(spec)
        if "--kv" in args and kept.k in _KV:
            args[args.index("--kv") + 1] = _KV[kept.k]
        held: dict[str, Any] = {
            "exe": str(job.engine.binary), "args": overridden(args, spec), "cwd": str(run),
            "tokenizer": str(made.pack / "tokenizer"), "model_name": job.chosen.name, "aliases": [Path(str(spec.model)).name],
            "log": str(run / "engine.log"), "lib_dirs": [], "env": dict(shaped.env), "drafting": made.drafter,
            "open_browser": False, "api_monitor": False, **({"backend": "metal"} if job.kind == "metal" else {}),
        }
        # Strata puts the drafter's own window over ``args`` at every start, except for the options named as the person's
        if mine := asked_options(spec):
            held["user_args"] = [piece for pair in mine.items() for piece in pair]
        if spec.reasoning_budget is not None:
            if spec.reasoning_budget > 0:
                held["reasoning_budget_tokens"] = int(spec.reasoning_budget)
            elif spec.reasoning_budget == 0:
                held["reasoning_effort"] = "none"
        return held

    def start(self, spec: ServerSpec, *, lease: Lease, timeout: float = 600.0, **starting: bool) -> ServerInfo:
        """Install and prepare what is missing, then launch and wait until the model has loaded and answers."""
        grant.require(lease, Lease)
        self._refuse_unserved(spec)
        asked = asked_drafter(spec)
        spec = replace(spec, model=fetched(spec.model, "model"))
        chosen, files = self.model_of(spec)
        spec, _said = self.fit(spec)
        job = prepare.Job(install.ensure(say=logger.info), chosen, files, install.platform_kind(),
                          psutil.virtual_memory().total, spec.context, max(1, int(spec.parallel or 1)))
        made = prepare.prepare(job, asked, say=logger.info)
        shaped = config.shape(job, made)
        if shaped.experts_file:
            prepare.experts_file(job.engine, chosen, made, say=logger.info)
        python = python_runtime.interpreter(NAME)
        grant.require(lease, Lease)
        claim_port(spec, lease)
        path = self._config_path(spec.port)
        # every start begins with an empty folder: Strata keeps what a client set (its shared settings) there and would serve it again
        if path.parent.exists():
            shutil.rmtree(path.parent)
        path.parent.mkdir(parents=True, mode=0o700)
        path.parent.parent.chmod(0o700)
        key = serverkeys.issue(spec.port)
        path.touch(mode=0o600)
        libraries = _cuda_libraries(python) if job.kind == "cuda" else []
        write_json(path, {**self.written(spec, job, made, shaped), "api_key": key, "lib_dirs": libraries})
        path.chmod(0o600)
        argv = [*python, str(job.engine.source / "terminal" / "serve" / "server.py"), *self.command(spec)]
        log_path = server_log(NAME, spec.port)
        # Strata keeps its own per-user records under the home folder it sees; here that is this run's folder
        private = {name: str(path.parent) for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "XDG_STATE_HOME", "XDG_DATA_HOME")}
        environment = python_runtime.environment_for_child({**private, "HF_HUB_OFFLINE": "1"})
        logger.info("starting: %s", " ".join(argv))
        try:
            process, base_url, load_s = launch(argv, lease, log_path=log_path, timeout=timeout, env=environment,
                                               cwd=str(path.parent))
        except ServerFailed as failure:
            raise ServerFailed(f"{failure}\n--- {path.parent / 'engine.log'} ---\n{tail(path.parent / 'engine.log')}") from failure
        serverkeys.bind(spec.port, process.pid)
        drafting, note = self._drafting(base_url, key, made)
        for line in (*shaped.notes, note):
            logger.info("port %s: %s", spec.port, line)
        return ServerInfo(base_url=base_url, port=spec.port, pid=process.pid, backend=NAME, log_path=log_path, load_s=load_s,
                          process=process, mtp=drafting, mtp_note="; ".join((note, *shaped.notes)))

    @staticmethod
    def _drafting(base_url: str, key: str, made: prepare.Prepared) -> tuple[str, str]:
        """``(the drafter the engine says it loaded, a line saying which and why)``; "" for none."""
        try:
            said = request_json(f"{base_url}/metrics", method="GET",
                                options=Options(timeout=30.0, headers={"Authorization": f"Bearer {key}"}))
        except (errors.Error, OSError, ValueError) as exc:   # a server that will not say is reported as not known
            return "", f"drafting asked for with {made.drafter}; the server did not say what it loaded ({exc})"
        return drafting_said(said, made)


def drafting_said(metrics: Any, made: prepare.Prepared) -> tuple[str, str]:
    """What the server's metrics say drafts, beside what was asked and why."""
    found = _find(metrics, "drafting")
    if found is None:
        return "", f"drafting asked for with {made.drafter}; the server did not say what it loaded"
    why = _find(metrics, "drafting_note") or made.why
    if found in ("off", ""):
        return "", f"no drafting: {why}"
    return str(found), f"drafting with {found}: {why}"


def _find(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
        for inner in value.values():
            if (found := _find(inner, name)) is not None:
                return found
    return None


def read_config(port: int) -> dict[str, Any] | None:
    """The run config of the server on ``port``, without its key."""
    try:
        held = json.loads(StrataBackend._config_path(port).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {name: value for name, value in held.items() if name != "api_key"}
