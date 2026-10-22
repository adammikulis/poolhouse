"""Completion data and ordinary metadata import without the optional model SDK."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_actual_conversations_and_completion_import_without_model_runtime(tmp_path):
    source = str(Path(__file__).parents[1] / "src")
    script = f"""
import importlib.abc
import sys
from pathlib import Path
sys.path.insert(0, {source!r})
class MissingRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {{'agents', 'openai', 'httpx2'}}:
            raise ModuleNotFoundError('missing optional runtime', name=fullname)
sys.meta_path.insert(0, MissingRuntime())
from poolhouse.client import completion
from poolhouse.fleet.conversations import Conversations
from poolhouse.fleet import api
record = completion._freeze('fixture', '{{"model":"fixture"}}')
assert completion.checked_completion(record) is record
assert record.arguments == {{'model': 'fixture'}}
store = Conversations(Path.cwd() / 'chat')
created = store.start(model='fixture', title='Inspect source')
assert store.get(created.id).title == 'Inspect source'
assert 'poolhouse.fleet.sdk_chat' not in sys.modules
assert not {{'agents', 'openai', 'httpx2'}}.intersection(sys.modules)
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script], cwd=tmp_path,
                            env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
