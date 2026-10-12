"""A run keeps the machine it ran on and the state of that machine at its start and at its end."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from poolhouse.bench import keep, system

_REAL_OUT = system._out

THERM_OK = """Note: No thermal warning level has been recorded
Note: No performance warning level has been recorded
Note: No CPU power status has been recorded
"""
THERM_HOT = """Note: A thermal warning level has been recorded
Note: No performance warning level has been recorded
CPU_Scheduler_Limit \t= 100
CPU_Available_CPUs \t= 12
CPU_Speed_Limit \t= 72
"""
BATT_AC = """Now drawing from 'AC Power'
 -InternalBattery-0 (id=1234567)\t100%; charged; 0:00 remaining present: true
"""
BATT_BAT = """Now drawing from 'Battery Power'
 -InternalBattery-0 (id=1234567)\t61%; discharging; 3:10 remaining present: true
"""
PMSET_LOW = """System-wide power settings:
Currently in use:
 standby              1
 lowpowermode         1
 powermode            1
"""
PMSET_NORMAL = PMSET_LOW.replace("lowpowermode         1", "lowpowermode         0")
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               20000.
Pages active:                            400000.
Pages inactive:                          380000.
Pages speculative:                         3000.
Pages wired down:                        150000.
Pages occupied by compressor:             50000.
"""
SWAP = "vm.swapusage: total = 4096.00M  used = 1024.50M  free = 3071.50M  (encrypted)\n"
DISPLAYS = """Graphics/Displays:

    Apple M9 Ultra:

      Chipset Model: Apple M9 Ultra
      Type: GPU
      Bus: Built-In
      Total Number of Cores: 60
      Vendor: Apple (0x106b)
      Metal Support: Metal 4
      Displays:
        Studio Display:
          Resolution: 5120 x 2880 Retina
"""
SW_VERS = "ProductName:\t\tmacOS\nProductVersion:\t\t26.1\nBuildVersion:\t\t25B99\n"
PS = """ 812340 /Applications/Some Editor.app/Contents/MacOS/Some Editor
 700000 /Users/exampleuser/bin/llama-server
  90000 /usr/sbin/mDNSResponder
  80000 /System/Library/CoreServices/Finder.app/Contents/MacOS/Finder
  70000 kernel_task
  60000 /sbin/launchd
  50000 /usr/libexec/trustd
"""
FAKE = {
    ("sysctl", "-n", "machdep.cpu.brand_string"): "Apple M9 Ultra\n",
    ("sysctl", "-n", "hw.model"): "Mac99,1\n",
    ("sysctl", "-n", "hw.memsize"): str(192 * 2**30) + "\n",
    ("sysctl", "-n", "hw.perflevel0.physicalcpu"): "16\n",
    ("sysctl", "-n", "hw.perflevel1.physicalcpu"): "8\n",
    ("system_profiler", "SPDisplaysDataType"): DISPLAYS,
    ("sw_vers",): SW_VERS,
    ("sysctl", "-n", "iogpu.wired_limit_mb"): "0\n",
    ("pmset", "-g", "therm"): THERM_OK,
    ("pmset", "-g", "batt"): BATT_AC,
    ("pmset", "-g"): PMSET_NORMAL,
    ("vm_stat",): VM_STAT,
    ("sysctl", "vm.swapusage"): SWAP,
    ("ps", "-axo", "rss=,comm="): PS,
}


class Boundary:
    """`subprocess.run` as the process boundary: what each argv prints, and how often it was asked."""

    def __init__(self, answers, delay=0.0):
        self.answers = dict(answers)
        self.delay = delay
        self.asked = []

    def __call__(self, argv, **kw):
        self.asked.append(tuple(argv))
        assert kw.get("shell") is not True and isinstance(argv, (list, tuple))
        assert kw.get("timeout"), "every probe has a timeout"
        time.sleep(self.delay)
        answer = self.answers.get(tuple(argv))
        if answer is None:
            return SimpleNamespace(returncode=1, stdout="", stderr="no such thing")
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(returncode=0, stdout=answer, stderr="")


@pytest.fixture
def boundary(monkeypatch):
    fake = Boundary(FAKE)
    monkeypatch.setattr(system, "_out", _REAL_OUT)
    monkeypatch.setattr(system.subprocess, "run", fake)
    monkeypatch.setattr(system, "platform_name", lambda: "darwin")
    monkeypatch.setattr(system.os, "getloadavg", lambda: (1.5, 2.0, 2.5))
    monkeypatch.setattr(system, "memory_ceiling", lambda: 150 * 2**30)
    monkeypatch.setattr(system, "kernel_release", lambda: "25.1.0")
    system.forget()
    yield fake
    system.forget()


def test_pmset_therm_reads_throttling():
    assert system.parse_therm(THERM_OK) == {"throttled": False}
    hot = system.parse_therm(THERM_HOT)
    assert hot == {"throttled": True, "thermal_warning": True, "cpu_speed_limit": 72,
                   "cpu_scheduler_limit": 100, "cpus_available": 12}


def test_pmset_batt_reads_the_source_and_never_the_battery_id():
    ac = system.parse_batt(BATT_AC)
    assert ac == {"power_source": "AC Power", "battery_percent": 100, "charging": False}
    bat = system.parse_batt(BATT_BAT)
    assert bat["power_source"] == "Battery Power" and bat["battery_percent"] == 61
    assert "1234567" not in json.dumps([ac, bat])


def test_low_power_mode_is_read_from_pmset():
    assert system.parse_low_power(PMSET_LOW) is True
    assert system.parse_low_power(PMSET_NORMAL) is False
    assert system.parse_low_power("nothing here") is None


def test_vm_stat_and_swap_are_bytes():
    got = system.parse_vm_stat(VM_STAT)
    assert got == {"free_bytes": 20000 * 16384, "wired_bytes": 150000 * 16384,
                   "compressed_bytes": 50000 * 16384, "inactive_bytes": 380000 * 16384}
    assert system.parse_swap(SWAP) == {"swap_used_bytes": int(1024.5 * 2**20),
                                       "swap_total_bytes": 4096 * 2**20}
    assert system.parse_vm_stat("garbage") == {}


def test_system_profiler_gives_the_chip_and_gpu_cores_not_the_displays():
    got = system.parse_displays(DISPLAYS)
    assert got == {"gpu": "Apple M9 Ultra", "gpu_cores": 60}
    assert "Studio" not in json.dumps(got)


def test_sw_vers_parses_name_version_build():
    assert system.parse_sw_vers(SW_VERS) == {"name": "macOS", "version": "26.1", "build": "25B99"}


def test_hardware_has_every_field_and_no_identifier(boundary):
    got = system.hardware()
    assert got == {"chip": "Apple M9 Ultra", "model": "Mac99,1", "memory_bytes": 192 * 2**30,
                   "performance_cores": 16, "efficiency_cores": 8, "gpu_cores": 60}
    for argv in boundary.asked:
        assert "SPHardwareDataType" not in argv, "that report holds the serial and the hardware id"


def test_hardware_is_asked_once_per_process(boundary):
    system.hardware()
    first = len(boundary.asked)
    system.hardware()
    assert len(boundary.asked) == first


def test_a_failing_probe_leaves_only_its_own_field_out(boundary):
    boundary.answers[("sysctl", "-n", "hw.model")] = OSError("gone")
    boundary.answers[("pmset", "-g", "therm")] = None
    boundary.answers[("system_profiler", "SPDisplaysDataType")] = None
    got = system.record()
    assert "model" not in got["hardware"] and "gpu_cores" not in got["hardware"]
    assert got["hardware"]["chip"] == "Apple M9 Ultra"
    assert "throttled" not in got["end"] and got["end"]["power_source"] == "AC Power"


def test_a_machine_with_no_tools_gives_a_record_and_not_an_error(boundary):
    boundary.answers.clear()
    got = system.record()
    assert isinstance(got, dict)
    assert "load_average" in got["end"]


def test_the_five_largest_processes_by_name_and_size_only(boundary):
    top = system.snapshot()["top_processes"]
    assert [p["name"] for p in top] == ["Some Editor", "llama-server", "mDNSResponder",
                                         "Finder", "kernel_task"]
    assert top[0]["rss_bytes"] == 812340 * 1024
    assert all(set(p) == {"name", "rss_bytes"} for p in top)


def test_no_path_or_user_survives_the_process_list(boundary):
    boundary.answers[("ps", "-axo", "rss=,comm=")] = (
        "exampleuser 900000 /Users/exampleuser/Documents/secret/tool --token abc\n"
        " 800000 /Users/exampleuser/Documents/secret/tool\n"
        " 700000 /opt/" + "x" * 80 + "\n")
    text = json.dumps(system.record())
    assert "exampleuser" not in text and "secret" not in text and "/Users" not in text
    assert "--token" not in text
    names = [p["name"] for p in system.snapshot()["top_processes"]]
    assert names[0] == "tool" and len(names[1]) == 40


def test_nothing_about_a_person_or_account_is_recorded(boundary, monkeypatch):
    monkeypatch.setenv("USER", "exampleuser")
    monkeypatch.setenv("HOME", "/Users/exampleuser")
    text = json.dumps(system.record())
    for leaked in ("exampleuser", "/Users", "serial", "uuid", "UUID", "Serial"):
        assert leaked not in text
    assert system.record()["hardware"].keys() <= {
        "chip", "model", "memory_bytes", "performance_cores", "efficiency_cores", "gpu_cores",
        "gpu", "gpus"}


def test_the_start_and_the_end_are_both_kept(boundary):
    system.note_start()
    boundary.answers[("pmset", "-g", "therm")] = THERM_HOT
    got = system.record()
    assert got["start"]["throttled"] is False
    assert got["end"]["throttled"] is True and got["end"]["cpu_speed_limit"] == 72
    assert got["limits"] == {"wired_limit_mb": 0, "wired_limit_default": True,
                             "memory_ceiling_bytes": 150 * 2**30}
    assert got["os"] == {"name": "macOS", "version": "26.1", "build": "25B99", "kernel": "25.1.0"}


def test_a_run_without_a_noted_start_keeps_only_the_end(boundary):
    got = system.record()
    assert "start" not in got and "end" in got


def test_the_next_run_of_a_sweep_starts_where_the_last_ended(boundary):
    system.note_start()
    boundary.answers[("pmset", "-g", "batt")] = BATT_BAT
    first = system.record()
    boundary.answers[("pmset", "-g", "batt")] = BATT_AC
    second = system.record()
    assert first["end"]["power_source"] == "Battery Power"
    assert second["start"]["power_source"] == "Battery Power"
    assert second["end"]["power_source"] == "AC Power"


def test_stamped_carries_the_system_of_this_machine_only(boundary):
    assert keep.stamped({})["system"]["hardware"]["chip"] == "Apple M9 Ultra"
    said = {"host": "elsewhere", "system": {"hardware": {"chip": "Other"}}}
    assert keep.stamped(said)["system"] == {"hardware": {"chip": "Other"}}
    assert "system" not in keep.stamped({"host": "elsewhere"})


def test_a_snapshot_costs_under_a_second_and_asks_each_probe_once(monkeypatch):
    fake = Boundary(FAKE, delay=0.04)
    monkeypatch.setattr(system, "_out", _REAL_OUT)
    monkeypatch.setattr(system.subprocess, "run", fake)
    monkeypatch.setattr(system, "platform_name", lambda: "darwin")
    system.forget()
    system.record()
    began = time.perf_counter()
    system.record()
    system.record()
    spent = (time.perf_counter() - began) / 2
    assert spent < 1.0
    assert max(fake.asked.count(a) for a in set(fake.asked)) <= 3, "once per snapshot at most"
    assert fake.asked.count(("system_profiler", "SPDisplaysDataType")) == 1, "hardware is cached"
    system.forget()


def _of(boundary_answers=None, **over):
    base = {"hardware": {"chip": "Apple M9 Ultra", "memory_bytes": 192 * 2**30},
            "os": {"name": "macOS", "version": "26.1"},
            "limits": {"wired_limit_mb": 0, "wired_limit_default": True},
            "end": {"throttled": False, "power_source": "AC Power", "low_power_mode": False}}
    base.update(over)
    return {"server": {"system": base}, "label": "a"}


def test_one_line_per_distinct_system_and_not_recorded_for_an_old_run():
    runs = [_of(), _of(), {"server": {}, "label": "old"}]
    lines = system.lines(runs)
    assert lines[0] == "system: Apple M9 Ultra, 192 GB, macOS 26.1, wired limit default  [2 runs]"
    assert "system: not recorded  [1 run]" in lines
    assert len(lines) == 2


def test_identical_systems_say_nothing_about_comparability():
    assert system.differences([_of(), _of()]) == ""
    assert not any("not directly comparable" in line for line in system.lines([_of(), _of()]))


def test_a_changed_wired_limit_is_named_as_not_comparable():
    other = _of(limits={"wired_limit_mb": 120000, "wired_limit_default": False})
    said = system.differences([_of(), other])
    assert "wired limit" in said and "not directly comparable" in said
    assert "0" in said and "120000" in said
    assert said in system.lines([_of(), other])


def test_throttling_during_one_run_is_named_as_not_comparable():
    hot = _of(end={"throttled": True, "power_source": "AC Power", "low_power_mode": False})
    said = system.differences([_of(), hot])
    assert "throttling" in said and "not directly comparable" in said


def test_chip_ram_os_major_power_and_low_power_each_fire():
    assert "chip" in system.differences([_of(), _of(hardware={"chip": "Apple M1",
                                                              "memory_bytes": 192 * 2**30})])
    assert "RAM" in system.differences([_of(), _of(hardware={"chip": "Apple M9 Ultra",
                                                             "memory_bytes": 96 * 2**30})])
    assert "OS" in system.differences([_of(), _of(os={"name": "macOS", "version": "27.0"})])
    assert "power source" in system.differences([_of(), _of(end={"power_source": "Battery Power"})])
    assert "Low Power Mode" in system.differences([_of(), _of(end={"low_power_mode": True})])


def test_a_minor_os_update_alone_is_not_a_difference():
    assert system.differences([_of(), _of(os={"name": "macOS", "version": "26.4"})]) == ""


def test_runs_with_no_record_never_raise_a_difference():
    assert system.differences([_of(), {"server": {}}]) == ""
    assert system.differences([]) == ""


def test_detail_lines_carry_hardware_start_and_end_state():
    one = _of()
    one["server"]["system"]["start"] = {"load_average": [1.0, 1.5, 2.0], "throttled": False}
    text = "\n".join(system.detail_lines(one["server"]))
    assert "Apple M9 Ultra" in text and "start" in text and "end" in text
    assert system.detail_lines({}) == ["  system: not recorded"]


def test_export_and_import_carry_the_system():
    from poolhouse.bench.member_runs import SERVER_KEYS
    from poolhouse.bench.score import _flat

    run = {"label": "a", "at": "t", "rows": [],
           "server": {"model": "m.gguf", "system": {"hardware": {"chip": "Apple M9 Ultra"}}}}
    assert _flat(run)["system"] == {"hardware": {"chip": "Apple M9 Ultra"}}
    assert "system" in SERVER_KEYS


LSCPU = "Architecture: x86_64\nModel name: Example Cores 9000\nSocket(s): 2\nCore(s) per socket: 12\n"


def test_linux_parsers_read_lscpu_and_nvidia_smi():
    assert system.parse_lscpu(LSCPU) == {"chip": "Example Cores 9000", "performance_cores": 24}
    assert system.parse_nvidia("Example GPU 99, 24576 MiB\n") == [
        {"name": "Example GPU 99", "memory_bytes": 24576 * 2**20}]
    assert system.parse_lscpu("") == {} and system.parse_nvidia("") == []


def test_a_linux_machine_without_nvidia_smi_has_hardware_and_no_gpu(boundary, monkeypatch):
    monkeypatch.setattr(system, "platform_name", lambda: "linux")
    monkeypatch.setattr(system, "_proc", lambda path: "MemTotal:  1048576 kB\n")
    boundary.answers[("lscpu",)] = LSCPU
    got = system.hardware()
    assert got == {"chip": "Example Cores 9000", "performance_cores": 24, "memory_bytes": 2**30}


def test_a_saved_run_keeps_the_system_at_both_ends_and_show_prints_it(
        boundary, tmp_path, capsys):
    from conftest import a_row

    import poolhouse.bench as bench

    store = tmp_path / "runs.ladybug"
    system.note_start()
    boundary.answers[("pmset", "-g", "therm")] = THERM_HOT
    bench.save(store, [a_row("who?", expected=["person:iris"], shown=["person:iris"])])
    kept = bench.runs(store)[0]["server"]["system"]
    assert kept["start"]["throttled"] is False and kept["end"]["throttled"] is True
    capsys.readouterr()
    assert bench._main(["show", "--kept", str(store)]) == 0
    assert "system: Apple M9 Ultra, 192 GB, macOS 26.1, wired limit default" in \
        capsys.readouterr().out
    label = bench.runs(store)[0]["label"]
    assert bench._main(["show", "--detail", label, "--kept", str(store)]) == 0
    said = capsys.readouterr().out
    assert "start:" in said and "end:" in said and "throttled (speed limit 72%)" in said
