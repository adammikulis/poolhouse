"""What refusal-stubs counts: a function taking nothing that only raises a fixed message, a feature switched off for good."""

from __future__ import annotations

from pathlib import Path

import pytest
from gates import refusal_stubs


def _found(tmp_path: Path, source: str) -> list[str]:
    where = tmp_path / "src" / "poolhouse" / "one.py"
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(source, encoding="utf-8")
    return [f.detail for f in refusal_stubs.find(tmp_path)]


@pytest.mark.parametrize("source", [
    'def require_isolated():\n    raise ValueError("cannot enforce isolation")\n',
    'def require_isolated():\n    """Refuse."""\n    raise ValueError("no")\n',
    'async def start():\n    raise RuntimeError("not built")\n',
    'class Launcher:\n    def start(self):\n        raise ValueError("off")\n',
])
def test_a_function_that_only_refuses_with_a_fixed_message_is_found(tmp_path, source: str) -> None:
    assert _found(tmp_path, source)


@pytest.mark.parametrize("source", [
    'def fail(why):\n    raise ValueError(why)\n',
    'def fail(*why):\n    raise ValueError("no")\n',
    'def check():\n    if bad():\n        raise ValueError("no")\n',
    'def check():\n    work()\n    raise ValueError("no")\n',
    'def check():\n    raise ValueError(f"{name}")\n',
    'import abc\nclass A(abc.ABC):\n    @abc.abstractmethod\n    def run(self):\n        raise NotImplementedError\n',
    'class A:\n    def run(self):\n        raise NotImplementedError("subclass")\n',
    'class Opaque:\n    def __new__(cls):\n        raise TypeError("do not build")\n',
    'class Secret:\n    def __reduce__(self):\n        raise TypeError("do not pickle")\n',
])
def test_a_helper_a_condition_an_abstract_method_or_a_type_guard_is_not_found(tmp_path, source: str) -> None:
    assert not _found(tmp_path, source)
