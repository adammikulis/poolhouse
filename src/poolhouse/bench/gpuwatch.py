"""How much of the GPU other processes used while a request ran: Apple's per-process counters, NVIDIA's process list."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable, Collection

__all__ = ["FOREIGN_SHARE", "Span", "apple_times", "nvidia_apps", "watch"]

#: Share of a request's wall time other processes may spend on the GPU before the sample is contended. A Mac's window
#: server draws the screen on the same GPU: an unused desktop reads between 0.01 and 0.05 over 15 s windows on an M4 Max.
FOREIGN_SHARE = 0.10
#: GPU memory another compute process must hold on an NVIDIA card to count.
FOREIGN_MIB = 500
#: NVIDIA utilisation, percent, read before a request is sent, above which something else is computing.
IDLE_PERCENT = 15

_CLIENT = re.compile(r'"IOUserClientCreator" = "pid (\d+), ([^"]*)"')
_TIME = re.compile(r'"accumulatedGPUTime"=(\d+)')


def _out(*argv: str) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def apple_times(text: str) -> dict[tuple[int, str], int]:
    """Nanoseconds of GPU time by ``(pid, name)``, from ``ioreg -r -c AGXDeviceUserClient -w 0``."""
    out: dict[tuple[int, str], int] = {}
    for block in text.split("+-o ")[1:]:
        who = _CLIENT.search(block)
        if who:
            key = (int(who[1]), who[2])
            out[key] = out.get(key, 0) + sum(int(n) for n in _TIME.findall(block))
    return out


def nvidia_apps(text: str) -> dict[int, int]:
    """MiB of GPU memory by pid, from ``nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits``."""
    out: dict[int, int] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if parts and parts[0].isdigit():
            held = parts[1] if len(parts) > 1 else ""
            out[int(parts[0])] = int(held) if held.isdigit() else 0
    return out


class Span:
    """One watched span; `close` says what other processes did on the GPU during it."""

    def __init__(self, own: Collection[int], read: Callable[[], dict] | None, kind: str) -> None:
        self.own, self.read, self.kind = set(own), read, kind
        self.began = time.monotonic()
        self.before = read() if read else {}
        self.idle: int | None = None

    def close(self) -> dict[str, object]:
        """``foreign_share``, the ``foreign`` process names, ``own_gpu_s`` and ``contended``; ``gpu_watched`` False
        when this machine has no counter, in which case nothing is claimed about contention."""
        wall = max(time.monotonic() - self.began, 1e-9)
        if not self.read or (self.kind == "apple" and not self.before):
            return {"gpu_watched": False, "contended": None}
        after = self.read()
        if self.kind == "apple":
            deltas = {key: after[key] - self.before.get(key, 0) for key in after}
            others = {key: d for key, d in deltas.items() if key[0] not in self.own and d > 0}
            share = sum(others.values()) / 1e9 / wall
            top = sorted(others, key=others.get, reverse=True)[:3]
            return {"gpu_watched": True, "foreign_share": round(share, 4), "foreign": [name for _, name in top],
                    "own_gpu_s": round(sum(d for key, d in deltas.items() if key[0] in self.own) / 1e9, 3),
                    "contended": share > FOREIGN_SHARE}
        busy = [pid for pid, mib in {**self.before, **after}.items() if pid not in self.own and mib >= FOREIGN_MIB]
        return {"gpu_watched": True, "foreign": busy, "idle_percent": self.idle,
                "contended": bool(busy) or (self.idle or 0) > IDLE_PERCENT}


def watch(own: Collection[int], *, platform: str) -> Span:
    """A `Span` opened now over every process but ``own``; ``platform`` is ``sys.platform``."""
    if platform == "darwin":
        return Span(own, lambda: apple_times(_out("ioreg", "-r", "-c", "AGXDeviceUserClient", "-w", "0")), "apple")
    smi = shutil.which("nvidia-smi")
    if smi:
        used = _out(smi, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits").split()
        span = Span(own, lambda: nvidia_apps(_out(
            smi, "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits")), "nvidia")
        span.idle = int(used[0]) if used and used[0].isdigit() else None
        return span
    return Span(own, None, "none")
