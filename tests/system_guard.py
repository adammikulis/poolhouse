"""No test reads the machine it runs on into a bench run's ``system`` record.

`bench.keep.stamped` records the hardware and state of the machine every run was measured on,
which is several subprocesses (`pmset`, `system_profiler`, `ps`). Under a test every probe
answers nothing; the tests of the record itself put the real probe back and fake the process
boundary instead. The fixture is imported by ``conftest.py`` and applies to every test.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_system_probes(monkeypatch):
    """Every probe in `poolhouse.bench.system` answers nothing, and no hardware is held over."""
    from poolhouse.bench import system

    monkeypatch.setattr(system, "_out", lambda *argv, **kw: "")
    monkeypatch.setattr(system, "memory_ceiling", lambda: 0)
    system.forget()
    yield
    system.forget()
