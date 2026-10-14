"""Device onboarding inputs and trusted orchestration callbacks."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poolhouse.fleet.discovery import Beacon, discover
from poolhouse.fleet.launch import HTTP_PORT
from poolhouse.fleet.onboard.joining import JoinOptions
from poolhouse.log import say

DEFAULT_ADMISSION = JoinOptions(timeout_s=2.0)


@dataclass(frozen=True, slots=True)
class JoinRequest:
    name: str = ''
    passphrase: str = field(default='', repr=False)
    group: str = ''
    persist: bool = False
    track: str = ''
    port: int = HTTP_PORT
    root: Path | str | None = None
    pool_key_path: Path | str | None = None
    wait_s: float = 20.0
    admission: JoinOptions = DEFAULT_ADMISSION

    def __post_init__(self) -> None:
        if self.admission.action is not None or self.admission.existing:
            raise ValueError('Device onboarding does not accept explicit pool admission actions.')


@dataclass(frozen=True, slots=True)
class JoinHooks:
    say: Callable[[str], None] = say
    start: Callable[[int, Path, str], int] | None = None
    enrol: Callable[[str, str], None] | None = None
    ensure: Callable[[Path], Path] | None = None
    persist_with: Callable[..., Any] | None = None
    finder: Callable[..., list[Beacon]] = discover


@dataclass(frozen=True, slots=True)
class LeaveRequest:
    group: str = ''
    root: Path | str | None = None
    pool_key_path: Path | str | None = None
    stop: bool = True


DEFAULT_JOIN = JoinRequest()
DEFAULT_HOOKS = JoinHooks()
DEFAULT_LEAVE = LeaveRequest()
