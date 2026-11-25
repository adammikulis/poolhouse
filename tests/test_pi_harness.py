"""Pi runs with the explicit local endpoint and poolhouse's tool-policy extension."""
from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from confined_kit import repository

from poolhouse import harnessing, isolation, pi

pytest_plugins = ["tests.confined_kit"]


@pytest.mark.usefixtures("confinable", "from_a_terminal")
def test_pi_launch_is_offline_and_uses_the_policy_extension(monkeypatch, tmp_path):
    seen = {}
    binary = tmp_path / "pi"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    project = repository(tmp_path / "project")
    run = harnessing.Session(SimpleNamespace(name="coding-test", parent=None, managed_inbox=False), project, "trusted role brief")

    @contextmanager
    def opened(*_args, **_kwargs):
        yield run

    monkeypatch.setattr(pi.harnessing, "binary_for", lambda _: str(binary))
    monkeypatch.setattr(pi, "alias_of", lambda *_: "local-model")
    monkeypatch.setattr(pi.harnessing, "window_of", lambda _: 49152)
    monkeypatch.setattr(pi.harnessing, "opened", opened)

    def run_pi(confinement):
        seen["command"], seen["env"] = list(confinement.argv), dict(confinement.env)
        seen["extension"] = Path(seen["command"][seen["command"].index("--extension") + 1]).read_text()
        seen["models"] = json.loads((confinement.config / "models.json").read_text())["providers"]["poolhouse"]
        return 0

    assert pi.launch(["--on", "http://127.0.0.1:8080", "--project", str(project)], say=lambda _: None, run_pi=run_pi) == 0
    assert seen["command"][:5] == [str(binary.resolve()), "--provider", "poolhouse", "--model", "local-model"]
    assert seen["command"][seen["command"].index("--thinking") + 1] == "off"
    assert "--no-session" in seen["command"] and "--no-mcp" in seen["command"]
    assert 'pi.on("tool_call"' in seen["extension"] and "client.py" in seen["extension"] and "poolhouse.harnesshook" not in seen["extension"]
    assert "toolConfig" not in seen["extension"], "Pi's own tools run inside the box; no per-tool worker is started"
    assert seen["env"]["PI_OFFLINE"] == "1" and set(seen["env"]) <= isolation.allowed("pi")
    assert seen["models"]["baseUrl"].startswith("http://127.0.0.1:") and not seen["models"]["baseUrl"].endswith(":8080"), "Pi reaches the bridge"
    assert seen["models"]["apiKey"] != "local" and len(seen["models"]["apiKey"]) >= 32, "the bridge's token, not a placeholder"


def test_pi_events_stream_usage_and_fail_closed():
    from poolhouse.workspace.coding_events import event

    assert event({"type": "message_update", "assistantMessageEvent":
        {"type": "text_delta", "delta": "Hello"}}, "pi") == {"delta": "Hello"}
    assert event({"type": "turn_end", "message": {"usage": {"output": 17}}}, "pi") == {"usage": {"output": 17}}
    assert event({"type": "message_end", "message": {"role": "assistant",
        "stopReason": "error", "errorMessage": "local runtime failed"}}, "pi") == {"error": "local runtime failed"}
    assert event({"type": "tool_execution_end", "isError": True, "result":
        {"content": [{"type": "text", "text": "poolhouse: person refused"}]}}, "pi") == {"blocked": "poolhouse: person refused"}


DENY = ["/bin/sh", "-c", "cat > /dev/null; echo '{\"hookSpecificOutput\":{\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"read-only\"}}'", "sh"]


def test_pi_extension_denies_writes_and_reports_exhausted_turns(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required by Pi")
    hook = tmp_path / "policy.mjs"
    hook.write_text(pi.extension({"hook": DENY, "root": str(tmp_path), "maxTurns": 1}))
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        'import extension from "./policy.mjs";\n'
        'const handlers={}; extension({on:(name,fn)=>handlers[name]=fn});\n'
        'const state={}; const ctx={abort:()=>state.aborted=true,shutdown:()=>state.stopped=true};\n'
        'handlers.turn_start({},ctx); handlers.turn_start({},ctx);\n'
        'const result=handlers.tool_call({toolName:"write",input:{path:"output.py",content:"x"}});\n'
        'console.log(JSON.stringify({state,result}));\n')
    done = subprocess.run([node, str(driver)], capture_output=True, text=True, timeout=30, check=True)
    rows = [json.loads(line) for line in done.stdout.splitlines()]
    assert rows[0] == {"type": "error", "message": "Pi turn budget exhausted"}
    assert rows[1]["state"] == {"aborted": True, "stopped": True}
    assert rows[1]["result"]["block"] and "poolhouse: read-only" in rows[1]["result"]["reason"]
    assert not (tmp_path / "output.py").exists()


def test_pi_post_checkpoint_failure_stops_the_session(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required by Pi")
    hook = tmp_path / "policy.mjs"
    hook.write_text(pi.extension({"hook": ["/usr/bin/false"], "root": str(tmp_path)}))
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        'import extension from "./policy.mjs";\n'
        'const handlers={}; extension({on:(name,fn)=>handlers[name]=fn});\n'
        'const state={}; const ctx={abort:()=>state.aborted=true,shutdown:()=>state.stopped=true};\n'
        'handlers.tool_result({},ctx); console.log(JSON.stringify(state));\n')
    done = subprocess.run([node, str(driver)], capture_output=True, text=True, timeout=30, check=True)
    rows = [json.loads(line) for line in done.stdout.splitlines()]
    assert rows[0] == {"type": "error", "message": "Pi mutation checkpoint failed"}
    assert rows[1] == {"aborted": True, "stopped": True}
