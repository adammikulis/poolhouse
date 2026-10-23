"""A project's testfarm.toml as frozen values: what `parse` returns and nothing in it is a repository's own default."""

from __future__ import annotations

from dataclasses import dataclass, field

from poolhouse.testfarm.model.exitcodes import REFUSED

__all__ = ["CommandUnit", "Config", "ConfigError", "DepsSpec", "FailfirstSpec", "HooksSpec", "LabelSpec", "ListSpec", "RequiresSpec",
           "ReuseSpec", "RunSpec", "ScheduleSpec", "SetSpec", "Step"]

Argv = tuple[str, ...]


class ConfigError(ValueError):
    """A file, a LIST or a selection the runner refuses, in one sentence; the status is 4."""

    status = REFUSED


@dataclass(frozen=True, slots=True)
class ListSpec:
    """``[list]``: the command that prints the test ids."""

    run: Argv | None = None
    format: str = "lines"
    starts: str | None = None
    ends: str | None = None
    group: str | None = None
    allow_empty: bool = False
    timeout: float | None = None
    glob: str | None = None


@dataclass(frozen=True, slots=True)
class RunSpec:
    """``[run]``: the command template that runs a subset of ids."""

    argv: Argv | None = None
    ids_after: str | None = None
    refuse_prefix: tuple[str, ...] = ()
    slice: Argv | None = None
    slice_base: int = 1
    slice_count: int | str = "auto"
    slices_per_target: int = 1
    slice_results: str = "exit"
    results: str = "exit"
    junit_id: str | None = None
    batch: int | None = None
    reuse: str | None = None
    test_timeout: float | None = None
    timeout: float | None = None
    env: tuple[tuple[str, str], ...] = ()
    launcher: Argv | None = None
    needs: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class DepsSpec:
    """``[deps]``: which files a unit depends on."""

    globs: tuple[tuple[str, tuple[str, ...]], ...] = ()
    command: Argv | None = None
    self_files: tuple[str, ...] = ()
    """``deps.self``: the files that are the analyzer itself."""
    timeout: float | None = None
    everything: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Step:
    """``[[step]]``: a build or environment command run once per device and cached by a key."""

    name: str
    run: Argv
    inputs: tuple[str, ...] = ()
    tools: tuple[Argv, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    slots: int | None = None
    timeout: float | None = None
    keep: float | None = None
    prune: Argv | None = None


@dataclass(frozen=True, slots=True)
class LabelSpec:
    """``[label.NAME]``: what a word on a unit means."""

    name: str
    match: tuple[str, ...] = ()
    lease: tuple[tuple[str, int | None], ...] = ()
    serial: str | None = None
    platform: tuple[str, ...] = ()
    local: bool = False
    net: str = "deny"
    reuse: bool = True
    slots: int | None = None
    estimate: float | None = None
    timeout: float | None = None
    needs: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SetSpec:
    """``[set.NAME]``: a named selection."""

    name: str
    labels: tuple[str, ...] = ()
    not_labels: tuple[str, ...] = ()
    ids: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    reuse: bool | None = None


@dataclass(frozen=True, slots=True)
class ReuseSpec:
    """``[reuse]``: extra key inputs and the canary sampling rate."""

    key_env: tuple[str, ...] = ()
    key_tools: tuple[Argv, ...] = ()
    canary: float | None = None


@dataclass(frozen=True, slots=True)
class FailfirstSpec:
    """``[failfirst]``: how the project's new tests are checked to have failed first."""

    tests: tuple[str, ...] = ()
    line: str | None = None
    exempt: str | None = None
    mode: str = "report"


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    """``[schedule]``: the project's own numbers for planning and hang guards."""

    seed: str | None = None
    unrecorded_max: float | None = None
    silence_margin: float | None = None
    total_margin: float | None = None
    queue_wait: float | None = None
    background_after: float | None = None


@dataclass(frozen=True, slots=True)
class RequiresSpec:
    """``[requires]``: what a device must have."""

    python: str | None = None
    module: tuple[str, ...] = ()
    exe: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandUnit:
    """``[[unit]]``: a command that is a unit with no LIST."""

    name: str
    run: Argv
    inputs: tuple[str, ...] = ()
    tools: tuple[Argv, ...] = ()
    labels: tuple[str, ...] = ()
    timeout: float | None = None
    slots: int | None = None
    env: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class HooksSpec:
    """``[hooks]``: commands run on the sender around a run."""

    before_plan: tuple[Argv, ...] = ()
    before_run: tuple[Argv, ...] = ()
    after_run: tuple[Argv, ...] = ()
    timeout: float | None = None


@dataclass(frozen=True, slots=True)
class Config:
    """A validated testfarm.toml, resolved through its preset; ``sha256`` is the digest of the bytes it was parsed from."""

    sha256: str
    preset: str | None = None
    project_name: str | None = None
    tree_include: tuple[str, ...] = ()
    tree_exclude: tuple[str, ...] = ()
    list: ListSpec | None = None
    run: RunSpec | None = None
    deps: DepsSpec = field(default_factory=DepsSpec)
    steps: tuple[Step, ...] = ()
    labels: tuple[LabelSpec, ...] = ()
    sets: tuple[SetSpec, ...] = ()
    reuse: ReuseSpec = field(default_factory=ReuseSpec)
    failfirst: FailfirstSpec = field(default_factory=FailfirstSpec)
    schedule: ScheduleSpec = field(default_factory=ScheduleSpec)
    requires: RequiresSpec = field(default_factory=RequiresSpec)
    units: tuple[CommandUnit, ...] = ()
    hooks: HooksSpec = field(default_factory=HooksSpec)
    notes: tuple[str, ...] = ()
    """Sentences about keys the file holds that the runner ignores."""

    @property
    def split_mode(self) -> str:
        """How the plan divides this project: by name (LIST), by slice (``run.slice``) or not at all."""
        if self.list is not None:
            return "by name"
        if self.run is not None and self.run.slice is not None:
            return "by slice"
        return "none"
