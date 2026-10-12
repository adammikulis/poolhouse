"""The machine a run was measured on and the state it was in: `record` for a run's ``server``.

Hardware and the operating system are read once per process; the state (thermal, power,
load, memory, the biggest processes) at the start of a run and again at its end. Every probe
is a fixed-argument subprocess with a timeout, and a probe that fails leaves its field out.
Nothing that names a person or an account is read: no serial, no hardware id, no user name,
no path, no address. `lines`, `differences` and `detail_lines` are what `show` prints.
"""

from __future__ import annotations

import contextlib
import os
import platform
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from poolhouse.hub import memory

__all__ = ["detail_lines", "differences", "forget", "hardware", "lines", "note_start", "record",
           "snapshot"]

_QUICK, _SLOW = 3.0, 8.0
_NAME = 40
_TOP = 5
_hardware: dict[str, Any] | None = None
_start: dict[str, Any] | None = None


def platform_name() -> str:
    """``sys.platform``."""
    return sys.platform


def kernel_release() -> str:
    """The kernel release, ``25.1.0``."""
    return platform.release()


def memory_ceiling() -> int:
    """The memory in bytes the broker admits a model against, 0 when unknown."""
    try:
        return int(memory.room())
    except (OSError, ValueError):
        return 0


def _out(*argv: str, timeout: float = _QUICK) -> str:
    """What a fixed command prints, "" when it is missing, fails or is slow."""
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout,
                              check=False, stdin=subprocess.DEVNULL,
                              env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.lstrip("-").isdigit() else None


def _proc(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


_UNITS = {"K": 2**10, "M": 2**20, "G": 2**30}


def parse_therm(text: str) -> dict[str, Any]:
    """``pmset -g therm``: whether the machine is limiting itself, and by how much."""
    if not text.strip():
        return {}
    out: dict[str, Any] = {}
    for kind in ("thermal", "performance"):
        for line in text.splitlines():
            if f"{kind} warning level" in line and "No " not in line:
                out[f"{kind}_warning"] = True
    for key, name in (("cpu_speed_limit", "CPU_Speed_Limit"),
                      ("cpu_scheduler_limit", "CPU_Scheduler_Limit"),
                      ("cpus_available", "CPU_Available_CPUs")):
        if found := re.search(rf"{name}\s*=\s*(\d+)", text):
            out[key] = int(found.group(1))
    throttled = bool(out.get("thermal_warning") or out.get("performance_warning")
                     or out.get("cpu_speed_limit", 100) < 100
                     or out.get("cpu_scheduler_limit", 100) < 100)
    return {"throttled": throttled, **out}


def parse_batt(text: str) -> dict[str, Any]:
    """``pmset -g batt``: the power source, and the charge when there is a battery."""
    out: dict[str, Any] = {}
    if found := re.search(r"drawing from '([^']+)'", text):
        out["power_source"] = found.group(1)
    if found := re.search(r"(\d+)%;\s*([a-z ]+)", text):
        out["battery_percent"] = int(found.group(1))
        out["charging"] = found.group(2).strip() == "charging"
    return out


def parse_low_power(text: str) -> bool | None:
    """Whether ``pmset -g`` says Low Power Mode is on, None when it does not say."""
    found = re.search(r"^\s*lowpowermode\s+(\d)", text, re.MULTILINE)
    return bool(int(found.group(1))) if found else None


def parse_vm_stat(text: str) -> dict[str, int]:
    """``vm_stat`` as bytes: free, wired, compressed and inactive."""
    size = re.search(r"page size of (\d+) bytes", text)
    if not size:
        return {}
    page = int(size.group(1))
    out: dict[str, int] = {}
    for key, label in (("free_bytes", "free"), ("wired_bytes", "wired down"),
                       ("compressed_bytes", "occupied by compressor"),
                       ("inactive_bytes", "inactive")):
        if found := re.search(rf"Pages {label}:\s+(\d+)", text):
            out[key] = int(found.group(1)) * page
    return out


def parse_swap(text: str) -> dict[str, int]:
    """``sysctl vm.swapusage`` as bytes."""
    out: dict[str, int] = {}
    for key, label in (("swap_used_bytes", "used"), ("swap_total_bytes", "total")):
        if found := re.search(rf"{label} = ([\d.]+)([KMG])", text):
            out[key] = int(float(found.group(1)) * _UNITS[found.group(2)])
    return out


def parse_displays(text: str) -> dict[str, Any]:
    """The graphics chip's name and core count out of ``system_profiler SPDisplaysDataType``;
    the displays are not read."""
    out: dict[str, Any] = {}
    if found := re.search(r"Chipset Model:\s*(.+)", text):
        out["gpu"] = found.group(1).strip()
    if found := re.search(r"Total Number of Cores:\s*(\d+)", text):
        out["gpu_cores"] = int(found.group(1))
    return out


def parse_sw_vers(text: str) -> dict[str, str]:
    """``sw_vers``: the operating system's name, version and build."""
    out: dict[str, str] = {}
    for key, label in (("name", "ProductName"), ("version", "ProductVersion"),
                       ("build", "BuildVersion")):
        if found := re.search(rf"{label}:\s*(\S.*)", text):
            out[key] = found.group(1).strip()
    return out


def parse_ps(text: str, count: int = _TOP) -> list[dict[str, Any]]:
    """The ``count`` largest processes out of ``ps -axo rss=,comm=``, by name and resident
    bytes. A line that does not start with a number is dropped; the name is the last path
    part of the command, cut to 40 characters."""
    found = []
    for line in text.splitlines():
        size, _, command = line.strip().partition(" ")
        if not size.isdigit() or not command.strip():
            continue
        name = command.strip().rsplit("/", 1)[-1].strip()[:_NAME]
        if name:
            found.append({"name": name, "rss_bytes": int(size) * 1024})
    return sorted(found, key=lambda one: -one["rss_bytes"])[:count]


def parse_lscpu(text: str) -> dict[str, Any]:
    """``lscpu``: the processor's name and its core count."""
    out: dict[str, Any] = {}
    if found := re.search(r"^Model name:\s*(.+)$", text, re.MULTILINE):
        out["chip"] = found.group(1).strip()
    cores = re.search(r"^Core\(s\) per socket:\s*(\d+)", text, re.MULTILINE)
    sockets = re.search(r"^Socket\(s\):\s*(\d+)", text, re.MULTILINE)
    if cores:
        out["performance_cores"] = int(cores.group(1)) * (int(sockets.group(1)) if sockets else 1)
    return out


def parse_nvidia(text: str) -> list[dict[str, Any]]:
    """``nvidia-smi --query-gpu=name,memory.total``: each card's name and memory."""
    cards = []
    for line in text.splitlines():
        name, _, memory = line.partition(",")
        if name.strip():
            card: dict[str, Any] = {"name": name.strip()[:_NAME * 2]}
            if (mib := _int(memory.replace("MiB", ""))) is not None:
                card["memory_bytes"] = mib * 2**20
            cards.append(card)
    return cards


def _mac_hardware() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, argv in (("chip", ("sysctl", "-n", "machdep.cpu.brand_string")),
                      ("model", ("sysctl", "-n", "hw.model"))):
        if said := _out(*argv).strip():
            out[key] = said
    for key, name in (("memory_bytes", "hw.memsize"), ("performance_cores", "hw.perflevel0.physicalcpu"),
                      ("efficiency_cores", "hw.perflevel1.physicalcpu")):
        if (number := _int(_out("sysctl", "-n", name))) is not None:
            out[key] = number
    if (gpu := parse_displays(_out("system_profiler", "SPDisplaysDataType", timeout=_SLOW))
            ).get("gpu_cores"):
        out["gpu_cores"] = gpu["gpu_cores"]
    return out


def _linux_hardware() -> dict[str, Any]:
    out = parse_lscpu(_out("lscpu"))
    if found := re.search(r"MemTotal:\s+(\d+) kB", _proc("/proc/meminfo")):
        out["memory_bytes"] = int(found.group(1)) * 1024
    if cards := parse_nvidia(_out("nvidia-smi", "--query-gpu=name,memory.total",
                                  "--format=csv,noheader", timeout=_SLOW)):
        out["gpus"] = cards
    return out


def hardware() -> dict[str, Any]:
    """The chip, model identifier, memory and core counts, read once per process."""
    global _hardware
    if _hardware is None:
        _hardware = _mac_hardware() if platform_name() == "darwin" else _linux_hardware()
    return dict(_hardware)


def operating_system() -> dict[str, str]:
    """Name, version, build and kernel of the operating system."""
    if platform_name() == "darwin":
        out = parse_sw_vers(_out("sw_vers"))
    else:
        out = {"name": platform.system(), "version": platform.release()}
        if found := re.search(r'^PRETTY_NAME="?([^"\n]+)', _proc("/etc/os-release"),
                              re.MULTILINE):
            out = {"name": found.group(1)}
    if kernel := kernel_release():
        out["kernel"] = kernel
    return out


def limits() -> dict[str, Any]:
    """The wired memory limit (0 is the system's default) and the broker's memory ceiling."""
    out: dict[str, Any] = {}
    if platform_name() == "darwin" and (
            wired := _int(_out("sysctl", "-n", "iogpu.wired_limit_mb"))) is not None:
        out["wired_limit_mb"] = wired
        out["wired_limit_default"] = wired == 0
    if ceiling := memory_ceiling():
        out["memory_ceiling_bytes"] = ceiling
    return out


def _mac_state() -> dict[str, Any]:
    out: dict[str, Any] = parse_therm(_out("pmset", "-g", "therm"))
    if (level := _int(_out("sysctl", "-n", "machdep.xcpm.cpu_thermal_level"))) is not None:
        out["cpu_thermal_level"] = level
        out["throttled"] = bool(out.get("throttled")) or level > 0
    out.update(parse_batt(_out("pmset", "-g", "batt")))
    if (low := parse_low_power(_out("pmset", "-g"))) is not None:
        out["low_power_mode"] = low
    out.update(parse_vm_stat(_out("vm_stat")))
    out.update(parse_swap(_out("sysctl", "vm.swapusage")))
    return out


def _linux_state() -> dict[str, Any]:
    meminfo = _proc("/proc/meminfo")
    out: dict[str, Any] = {}
    for key, label in (("free_bytes", "MemFree"), ("inactive_bytes", "Inactive")):
        if found := re.search(rf"^{label}:\s+(\d+) kB", meminfo, re.MULTILINE):
            out[key] = int(found.group(1)) * 1024
    total = re.search(r"^SwapTotal:\s+(\d+) kB", meminfo, re.MULTILINE)
    free = re.search(r"^SwapFree:\s+(\d+) kB", meminfo, re.MULTILINE)
    if total and free:
        out["swap_total_bytes"] = int(total.group(1)) * 1024
        out["swap_used_bytes"] = (int(total.group(1)) - int(free.group(1))) * 1024
    return out


def snapshot() -> dict[str, Any]:
    """The state of the machine now: thermal, power, load, memory and the biggest processes."""
    out = _mac_state() if platform_name() == "darwin" else _linux_state()
    with contextlib.suppress(OSError, AttributeError):
        out["load_average"] = [round(v, 2) for v in os.getloadavg()]
    if top := parse_ps(_out("ps", "-axo", "rss=,comm=")):
        out["top_processes"] = top
    return out


def note_start() -> None:
    """Take the state a run starts in; the run's `record` carries it."""
    global _start
    hardware()
    _start = snapshot()


def record() -> dict[str, Any]:
    """The ``system`` record for a run ending now: hardware, operating system, limits, the
    state noted at its start (when there is one) and the state now. The end becomes the
    start of the next run of the same process."""
    global _start
    end = snapshot()
    out: dict[str, Any] = {"hardware": hardware(), "os": operating_system(), "limits": limits()}
    if _start:
        out["start"] = _start
    out["end"] = end
    _start = end
    return {k: v for k, v in out.items() if v}


def forget() -> None:
    """Drop what is held for the process: the hardware and the noted start."""
    global _hardware, _start
    _hardware = _start = None


def _gb(size: int | float) -> str:
    return f"{size / 2**30:.0f} GB"


def _of(one: Mapping[str, Any]) -> Mapping[str, Any]:
    found = (one.get("server") or one).get("system") if isinstance(one, Mapping) else None
    return found if isinstance(found, Mapping) and found else {}


def _states(system: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [s for s in (system.get("start"), system.get("end")) if isinstance(s, Mapping)]


def _any(system: Mapping[str, Any], key: str, want: Any = True) -> bool | None:
    """Whether ``key`` is ``want`` in either state, False when seen otherwise, None when unseen."""
    seen = [s[key] for s in _states(system) if key in s]
    return None if not seen else any(v == want for v in seen)


def _wired(system: Mapping[str, Any]) -> str | None:
    wired = (system.get("limits") or {}).get("wired_limit_mb")
    if wired is None:
        return None
    return "default" if wired == 0 else f"{wired} MB"


def summary(system: Mapping[str, Any]) -> str:
    """``chip, RAM, OS, wired limit`` for one system, "" when it holds none of them."""
    hard, os_ = system.get("hardware") or {}, system.get("os") or {}
    chip = hard.get("chip") or ""
    parts = [chip, _gb(hard["memory_bytes"]) if hard.get("memory_bytes") else "",
             " ".join(str(os_[k]) for k in ("name", "version") if os_.get(k)),
             f"wired limit {wired}" if (wired := _wired(system)) else ""]
    return ", ".join(p for p in parts if p)


def lines(kept: Sequence[Mapping[str, Any]]) -> list[str]:
    """One ``system:`` line per distinct system among the runs, ``not recorded`` for those
    that predate it, and a line naming what differs when the runs are not comparable."""
    groups: dict[str, int] = {}
    for one in kept:
        name = summary(_of(one)) or "not recorded"
        groups[name] = groups.get(name, 0) + 1
    out = [f"system: {name}" + (f"  [{n} run{'' if n == 1 else 's'}]"
                                if len(groups) > 1 or n > 1 else "")
           for name, n in groups.items()]
    if said := differences(kept):
        out.append(said)
    return out


def _traits(system: Mapping[str, Any]) -> dict[str, Any]:
    hard, os_ = system.get("hardware") or {}, system.get("os") or {}
    power = _any(system, "power_source", "Battery Power")
    traits = {
        "chip": hard.get("chip"),
        "RAM": _gb(hard["memory_bytes"]) if hard.get("memory_bytes") else None,
        "OS major version": (str(os_["version"]).split(".")[0] or None) if os_.get("version")
        else None,
        "wired limit": _wired(system),
        "power source": None if power is None else ("battery" if power else "mains"),
        "Low Power Mode": None if (low := _any(system, "low_power_mode")) is None
        else ("on" if low else "off"),
        "thermal throttling": None if (hot := _any(system, "throttled")) is None
        else ("yes" if hot else "no"),
    }
    return {k: v for k, v in traits.items() if v is not None}


def differences(kept: Sequence[Mapping[str, Any]]) -> str:
    """One plain line naming what differs among the runs' systems (chip, RAM, OS major
    version, wired limit, power source, Low Power Mode, thermal throttling), "" when
    nothing does; a run with no record takes no part."""
    seen = [_traits(s) for one in kept if (s := _of(one))]
    named = []
    for key in _traits_order():
        values = list(dict.fromkeys(t[key] for t in seen if key in t))
        if len(values) > 1:
            named.append(f"{key} ({' vs '.join(values)})")
    if not named:
        return ""
    return ("these runs were measured on systems that differ in " + ", ".join(named)
            + ": the numbers are not directly comparable")


def _traits_order() -> tuple[str, ...]:
    return ("chip", "RAM", "OS major version", "wired limit", "power source", "Low Power Mode",
            "thermal throttling")


def _state_line(name: str, state: Mapping[str, Any]) -> str:
    bits = []
    if "throttled" in state:
        bits.append("throttled" + (f" (speed limit {state['cpu_speed_limit']}%)"
                                   if state.get("cpu_speed_limit", 100) < 100 else "")
                    if state["throttled"] else "not throttled")
    if state.get("power_source"):
        bits.append(str(state["power_source"]) + (
            f" {state['battery_percent']}%" if "battery_percent" in state else ""))
    if state.get("low_power_mode") is not None:
        bits.append("Low Power Mode " + ("on" if state["low_power_mode"] else "off"))
    if state.get("load_average"):
        bits.append("load " + " ".join(f"{v:.2f}" for v in state["load_average"]))
    for key, label in (("free_bytes", "free"), ("wired_bytes", "wired"),
                       ("compressed_bytes", "compressed"), ("swap_used_bytes", "swap used")):
        if key in state:
            bits.append(f"{label} {state[key] / 2**30:.1f} GB")
    top = ", ".join(f"{p['name']} {p['rss_bytes'] / 2**30:.1f} GB"
                    for p in state.get("top_processes") or ())
    return f"    {name}: " + "; ".join(bits) + (f"\n      largest: {top}" if top else "")


def detail_lines(server: Mapping[str, Any]) -> list[str]:
    """The whole record of one run, for ``show --detail``."""
    found = _of(server)
    if not found:
        return ["  system: not recorded"]
    hard, os_, lim = found.get("hardware") or {}, found.get("os") or {}, found.get("limits") or {}
    cores = "/".join(str(hard[k]) for k in ("performance_cores", "efficiency_cores") if k in hard)
    out = [f"  system: {summary(found) or '-'}"]
    out.append("    hardware: " + ", ".join(p for p in (
        hard.get("model", ""), f"{cores} cores (P/E)" if cores else "",
        f"{hard['gpu_cores']} GPU cores" if hard.get("gpu_cores") else "") if p))
    out.append("    os: " + " ".join(str(os_[k]) for k in ("name", "version", "build") if k in os_)
               + (f", kernel {os_['kernel']}" if os_.get("kernel") else ""))
    if lim:
        ceiling = lim.get("memory_ceiling_bytes")
        out.append("    limits: " + ", ".join(p for p in (
            f"wired limit {_wired(found)}" if _wired(found) else "",
            f"broker memory ceiling {_gb(ceiling)}" if ceiling else "") if p))
    out.extend(_state_line(name, state) for name in ("start", "end")
               if isinstance(state := found.get(name), Mapping))
    return out
