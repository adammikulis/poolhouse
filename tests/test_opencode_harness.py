"""OpenCode runs through the one door (`isolation.prepare` then `Confinement.popen`), on the bridge's URL and token, with poolhouse's plugin, and its
binary is fetched with no install step."""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import shutil
import subprocess
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from confined_kit import repository

from poolhouse import coding, coding_policy, harnessing, isolation, opencode, opencode_install
from poolhouse.http import Server

pytest_plugins = ["tests.confined_kit"]


@pytest.mark.usefixtures("confinable", "from_a_terminal")
def test_opencode_run_is_confined_offline_and_points_at_the_bridge(monkeypatch, tmp_path):
    seen = {}
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    project = repository(tmp_path / "project")
    run = harnessing.Session(SimpleNamespace(name="coding-test", parent=None, managed_inbox=False), project, "trusted role brief")

    @contextmanager
    def opened(*_args, **_kwargs):
        yield run

    monkeypatch.setattr(opencode.harnessing, "binary_for", lambda _: str(binary))
    monkeypatch.setattr(opencode, "alias_of", lambda *_: "local-model")
    monkeypatch.setattr(opencode.harnessing, "window_of", lambda _: 49152)
    monkeypatch.setattr(opencode.harnessing, "opened", opened)

    def run_opencode(confinement):
        seen["argv"], seen["env"] = list(confinement.argv), dict(confinement.env)
        seen["config"] = json.loads(Path(seen["env"]["OPENCODE_CONFIG"]).read_text())
        seen["plugin"] = Path(seen["env"]["OPENCODE_CONFIG"]).with_name(opencode.PLUGIN).read_text()
        return 0

    said = []
    assert opencode.launch(["--on", "http://127.0.0.1:8080", "--project", str(project), "--", "fix it"], say=said.append,
                           run_opencode=run_opencode) == 0, said
    assert seen["argv"] == [str(binary.resolve()), "run", "--format", "json", "--model", "poolhouse/local-model", "fix it"]
    assert seen["env"]["OPENCODE_DISABLE_AUTOUPDATE"] == "true" and seen["env"]["OPENCODE_DISABLE_MODELS_FETCH"] == "true"
    assert set(seen["env"]) <= isolation.allowed("opencode")
    provider = seen["config"]["provider"]["poolhouse"]
    assert provider["options"]["baseURL"].startswith("http://127.0.0.1:") and provider["options"]["baseURL"].endswith("/v1")
    assert not provider["options"]["baseURL"].startswith("http://127.0.0.1:8080"), "OpenCode reaches the bridge, never the server"
    assert provider["options"]["apiKey"] != "local" and len(provider["options"]["apiKey"]) >= 32, "the bridge's token, minted for the launch"
    assert seen["config"]["autoupdate"] is False and seen["config"]["share"] == "disabled"
    assert "tool.execute.before" in seen["plugin"] and "client.py" in seen["plugin"] and "poolhouse.harnesshook" not in seen["plugin"]


def test_coding_agent_takes_opencode_by_name(monkeypatch, tmp_path):
    got = {}
    monkeypatch.setitem(coding.HARNESSES, "opencode", lambda argv, say, **options: got.update(argv=argv, options=options) or 0)
    project = tmp_path / "p"
    project.mkdir()
    assert coding.launch_coding_agent("m", coding_policy.Policy("guarded", "execute"), project, harness="opencode",
                                      harness_args=["fix it"], run_opencode=lambda confinement: 0) == 0
    assert got["argv"][-2:] == ["--", "fix it"] and "run_opencode" in got["options"]
    assert "opencode" in coding.HARNESSES


def test_opencode_plugin_denies_and_fails_closed(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to load the plugin")
    deny = ["/bin/sh", "-c", "cat > /dev/null; echo '{\"hookSpecificOutput\":{\"permissionDecision\":\"deny\",\"permissionDecisionReason\":\"read-only\"}}'", "sh"]
    allow = ["/bin/sh", "-c", "cat > /dev/null; echo '{}'", "sh"]
    driver = tmp_path / "driver.mjs"
    out = []
    for name, hook in (("deny", deny), ("allow", allow), ("broken", ["/usr/bin/false"])):
        (tmp_path / f"{name}.mjs").write_text(opencode.plugin({"hook": hook, "root": str(tmp_path)}))
        out.append(name)
    driver.write_text(
        'const seen = {};\n'
        + "".join(f'''{{ const hooks = await (await import("./{name}.mjs")).Poolhouse();
  const row = {{}};
  try {{ await hooks["tool.execute.before"]({{tool:"write"}}, {{args:{{filePath:"x.py",content:"x"}}}}); row.before = "ok" }} catch (e) {{ row.before = e.message }}
  try {{ await hooks["tool.execute.after"]({{}}, {{}}); row.after = "ok" }} catch (e) {{ row.after = e.message }}
  seen.{name} = row; }}\n''' for name in out)
        + 'console.log(JSON.stringify(seen));\n')
    done = subprocess.run([node, str(driver)], capture_output=True, text=True, timeout=30, check=True)
    seen = json.loads(done.stdout)
    assert seen["deny"]["before"] == "poolhouse: read-only" and seen["deny"]["after"] == "ok"
    assert seen["allow"] == {"before": "ok", "after": "ok"}
    assert seen["broken"]["before"] == "poolhouse: tool policy failed; blocked" and "checkpoint failed" in seen["broken"]["after"]
    assert not (tmp_path / "x.py").exists()


def test_opencode_names_are_in_the_closed_records():
    from poolhouse.guard import harnesses

    assert ".opencode" in harnesses.names() and "opencode.json" in harnesses.names()
    assert harnesses.RECORDS["opencode"] == opencode.INSTRUCTIONS


@pytest.fixture
def releases(monkeypatch, tmp_path):
    """A local release feed that serves one OpenCode release (a real zip with a `opencode` file in it) with the digest the feed reports."""
    monkeypatch.setattr(opencode_install, "asset_name", lambda: "opencode-test-arm64.zip")
    made = io.BytesIO()
    with zipfile.ZipFile(made, "w") as archive:
        archive.writestr("opencode", "#!/bin/sh\necho 2.4.1\n")
    body = made.getvalue()
    state = {"digest": hashlib.sha256(body).hexdigest(), "asks": 0}

    class Feed(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            state["asks"] += 1
            if self.path.startswith("/releases"):
                reply = json.dumps([{"tag_name": "v2.4.1", "draft": False, "prerelease": False, "assets": [
                    {"name": "opencode-test-arm64.zip", "size": len(body), "digest": "sha256:" + state["digest"],
                     "browser_download_url": f"http://127.0.0.1:{self.server.server_address[1]}/asset.zip"}]}]).encode()
            else:
                reply = body
            self.send_response(200)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    server = Server(("127.0.0.1", 0), Feed)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(opencode_install, "RELEASES", f"http://127.0.0.1:{server.server_address[1]}/releases")
    yield state
    server.shutdown()


@pytest.mark.usefixtures("loopback_net")
def test_the_binary_is_fetched_once_checked_against_the_reported_digest_and_kept(releases):
    assert opencode_install.kept() == ""
    binary = Path(opencode_install.ensure())
    assert binary.name == "opencode" and binary.parent.name == "v2.4.1" and binary.stat().st_mode & 0o100
    assert subprocess.run([str(binary)], capture_output=True, text=True, check=True).stdout.strip() == "2.4.1"
    asks = releases["asks"]
    assert opencode_install.ensure() == str(binary) and releases["asks"] == asks, "a kept copy is taken without asking the network"
    assert harnessing.binary_for("opencode") == str(binary)


@pytest.mark.usefixtures("loopback_net")
def test_a_download_that_does_not_match_its_digest_installs_nothing(releases):
    releases["digest"] = "0" * 64
    with pytest.raises(opencode_install.InstallFailed, match="digest"):
        opencode_install.ensure()
    assert opencode_install.kept() == ""
