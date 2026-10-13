"""scripts/agent-identity: the App made from a manifest, its key kept in the keystore, the token minted from it,
and what refuses along the way. GitHub and the browser are fakes (poolhouse.testing.fakegithub)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import logging
import os
import pty
import re
import socket
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import jwt
import pytest

from poolhouse import credentials
from poolhouse.testing.fakegithub import (
    ASKED,
    KEYS,
    NAME,
    REPO,
    FakeBrowser,
    FakeGitHub,
    loopback_get,
    pem,
)
from tests import github_support, memory_keys

ring = memory_keys.ring

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "agent-identity"
TTY = (True, True)
MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE")


@pytest.fixture
def ai(tmp_path, monkeypatch):
    for name in MARKERS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    gh = FakeGitHub()
    loader = importlib.machinery.SourceFileLoader("agent_identity", str(SCRIPT))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    monkeypatch.setitem(sys.modules, loader.name, module)
    loader.exec_module(module)
    browser = FakeBrowser(gh)
    for name, value in (("API", gh.url), ("POLL_S", 0.01), ("CREATE_WAIT_S", 10.0),
                        ("INSTALL_WAIT_S", 10.0), ("OPEN", browser)):
        monkeypatch.setattr(module, name, value)
    module.gh, module.browser = gh, browser
    yield module
    gh.close()


def setup(ai, *extra):
    code = ai.main(["setup", "--repo", REPO, *extra], TTY)
    ai.browser.join()
    return code


def key_chunks(ai) -> list[str]:
    """Pieces of every private key and secret the fake GitHub handed out, as they would read in an output."""
    out = []
    for app in ai.gh.apps.values():
        body = "".join(pem(app["key"]).splitlines()[1:-1])
        out += [body[100:140], body[400:440], "BEGIN RSA PRIVATE KEY", app["secret"]]
    return out


def leaks(ai, tmp_path, *texts: str) -> list[str]:
    """Which key pieces appear in the texts or in any file under the test's directories."""
    found = []
    blobs = [*texts, *(p.read_text(errors="ignore") for p in tmp_path.rglob("*") if p.is_file())]
    for chunk in key_chunks(ai):
        found += [chunk for blob in blobs if chunk in blob]
    return found


def stored_state(ai) -> bool:
    return credentials.status(ai.ITEM)["present"]


def seen(ai, method, tail):
    return [r for r in ai.gh.log if r["method"] == method and r["path"].endswith(tail)]


# -- setup ----------------------------------------------------------------------------------

def test_setup_creates_the_app_installs_it_and_stores_the_key(ai, capsys):
    assert setup(ai) == 0
    who = ai.load()
    app = ai.gh.apps[who.app_id]
    assert who.repo == REPO and who.slug == app["slug"] and who.client_id == app["client_id"]
    assert who.key == pem(app["key"])
    assert who.installation_id in ai.gh.installations
    assert ai.gh.exchanges == 1
    out = capsys.readouterr().out
    assert "ready" in out and who.slug in out and str(who.installation_id) in out


def test_the_manifest_asks_for_exactly_the_documented_permissions_and_nothing_wider(ai):
    setup(ai)
    manifest = ai.gh.manifests[0]
    assert manifest["default_permissions"] == ASKED
    assert not {"administration", "secrets", "environments"} & set(manifest["default_permissions"])
    assert manifest["public"] is False and manifest["default_events"] == []
    assert "hook_attributes" not in manifest
    assert manifest["url"] == f"https://github.com/{REPO}"
    port = re.search(r"127\.0\.0\.1:(\d+)/start", ai.browser.opened[0])[1]
    assert manifest["redirect_url"] == f"http://127.0.0.1:{port}/callback"


def test_the_browser_is_sent_to_the_start_page_then_the_install_page(ai):
    setup(ai)
    who = ai.load()
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/start\?state=[\w-]{40,}", ai.browser.opened[0])
    assert ai.browser.opened[1] == f"https://github.com/apps/{who.slug}/installations/new"


def test_the_listener_is_closed_after_the_one_redirect(ai):
    setup(ai)
    port = int(re.search(r":(\d+)/start", ai.browser.opened[0])[1])
    with pytest.raises(ConnectionRefusedError), socket.create_connection(("127.0.0.1", port), timeout=2):
        pass


def test_the_key_is_in_no_output_log_or_file(ai, tmp_path, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    assert setup(ai) == 0
    assert ai.main(["token"], TTY) == 0
    assert ai.main(["check", "--online"], TTY) == 0
    out = capsys.readouterr()
    assert leaks(ai, tmp_path, out.out, out.err, caplog.text) == []
    assert "PRIVATE KEY" not in repr(ai.load())


def test_the_stored_item_is_wrapped_and_nothing_else_holds_the_key(ai, tmp_path):
    setup(ai)
    wrapped = credentials.wrapped_path()
    assert wrapped.exists() and not wrapped.stat().st_mode & 0o077
    assert leaks(ai, tmp_path) == []
    assert not list((tmp_path).rglob("*.pem"))


def test_a_wrong_missing_or_doubled_state_is_refused_and_nothing_is_exchanged(ai):
    ai.browser.sends_first = ("?code={code}&state=wrong", "?code={code}", "?code={code}&state={state}&state=x",
                              "?state={state}")
    assert setup(ai) == 0
    first = [status for status, _ in ai.browser.sent[:4]]
    assert first == [404, 404, 404, 400]
    assert ai.browser.sent[4][0] == 200
    assert ai.gh.exchanges == 1


def test_a_hostile_code_is_refused_before_it_reaches_the_exchange(ai):
    ai.browser.sends_first = ("?code=../../app&state={state}", "?code=a%2Fb%2Fc%2Fd%2Fe%2Ff&state={state}")
    assert setup(ai) == 0
    assert [s for s, _ in ai.browser.sent[:2]] == [400, 400]
    assert [r["path"] for r in ai.gh.log if "conversions" in r["path"]] == [
        f"/app-manifests/{next(iter(ai.gh.codes))}/conversions"]


def test_the_listener_takes_one_redirect_and_the_start_page_once(ai):
    state = "s" * 40
    with ai.Listener(state, lambda port: b"page") as listening:
        base = f"http://127.0.0.1:{listening.port}"
        assert loopback_get(f"{base}/start?state={state}") == (200, b"page")
        assert loopback_get(f"{base}/start?state={state}")[0] == 410
        assert loopback_get(f"{base}/callback?code=firstcode123&state={state}")[0] == 200
        assert loopback_get(f"{base}/callback?code=secondcode12&state={state}")[0] == 410
        assert loopback_get(f"{base}/callback?code=firstcode123&state={state}")[0] == 410
        assert listening.wait(0) == "firstcode123"


def test_the_listener_refuses_the_wrong_host_method_and_path_without_using_the_state_up(ai):
    state = "s" * 40
    with ai.Listener(state, lambda port: b"page") as listening:
        port = listening.port
        url = f"http://127.0.0.1:{port}/callback?code=thecode1234&state={state}"
        assert listening.server.server_address[0] == "127.0.0.1"
        assert loopback_get(url, host=f"attacker.example:{port}")[0] == 403
        assert loopback_get(url, host="127.0.0.1")[0] == 403
        assert loopback_get(url, host=f"127.0.0.1:{port + 1}")[0] == 403
        assert loopback_get(url, method="POST")[0] == 405
        assert loopback_get(f"http://127.0.0.1:{port}/other?state={state}")[0] == 404
        assert listening.wait(0) == ""
        assert loopback_get(url)[0] == 200
        assert listening.wait(0) == "thecode1234"


def test_only_a_request_from_this_machine_to_this_listener_is_taken(ai):
    assert ai.from_here("127.0.0.1", ["127.0.0.1:5000"], 5000)
    assert not ai.from_here("10.0.0.5", ["127.0.0.1:5000"], 5000)
    assert not ai.from_here("192.168.1.9", ["127.0.0.1:5000"], 5000)
    assert not ai.from_here("127.0.0.1", ["localhost:5000"], 5000)
    assert not ai.from_here("127.0.0.1", ["127.0.0.1:5000", "evil:1"], 5000)
    assert not ai.from_here("127.0.0.1", [], 5000)


def test_a_code_used_twice_is_refused_by_github_and_the_second_try_stores_nothing(ai):
    code = ai.gh.create_app({"name": "x-agents", "default_permissions": ASKED})
    first = ai.exchange(code, REPO, "User")
    held = ai.load()
    with pytest.raises(ai.Failed, match="delete any App"):
        ai.exchange(code, REPO, "User")
    assert ai.load() == held == first
    assert ai.gh.exchanges == 2


def test_an_app_owned_by_someone_else_is_not_stored(ai, tmp_path):
    ai.gh.app_owner = "someoneelse"
    assert setup(ai) == 1
    assert not stored_state(ai)
    assert leaks(ai, tmp_path) == []


def test_an_app_with_wider_permissions_than_asked_is_not_stored(ai, capsys):
    ai.gh.app_permissions = {**ASKED, "administration": "write"}
    assert setup(ai) == 1
    err = capsys.readouterr().err
    assert "not asked for" in err and "delete the App" in err and "settings/apps/" in err
    assert not stored_state(ai)


def test_a_keystore_that_cannot_store_names_the_app_to_delete_and_prints_no_key(ai, monkeypatch, capsys, tmp_path):
    def refuse(*_a, **_k):
        raise credentials.CredentialError("the keystore is locked")
    monkeypatch.setattr(credentials, "set", refuse)
    assert setup(ai) == 1
    out = capsys.readouterr()
    assert "the keystore is locked" in out.err and "delete the App" in out.err
    assert leaks(ai, tmp_path, out.out, out.err) == []


def test_an_installation_on_every_repository_is_refused_and_setup_resumes_without_a_second_app(ai, capsys):
    ai.gh.selection = "all"
    assert setup(ai) == 1
    assert "choose only" in capsys.readouterr().err
    assert ai.load().installation_id is None
    ai.gh.selection = "selected"
    ai.gh.installations.clear()
    assert setup(ai) == 0
    assert len(ai.gh.manifests) == 1 and ai.gh.exchanges == 1
    assert ai.load().installation_id in ai.gh.installations


def test_an_installation_that_reaches_another_repository_is_refused(ai, capsys):
    ai.gh.other_repos = [f"{REPO.split('/')[0]}/another"]
    assert setup(ai) == 1
    assert "also reaches" in capsys.readouterr().err
    assert ai.load().installation_id is None


def test_an_app_that_is_never_installed_says_where_and_how_to_continue(ai, monkeypatch, capsys):
    monkeypatch.setattr(ai, "INSTALL_WAIT_S", 0.2)
    ai.browser.installs = False
    assert setup(ai) == 1
    err = capsys.readouterr().err
    assert "installations/new" in err and "scripts/agent-identity setup again" in err
    assert ai.load().installation_id is None


def test_a_browser_that_cannot_open_gets_the_address_printed(ai, monkeypatch, capsys):
    monkeypatch.setattr(ai, "OPEN", lambda url: ai.browser(url) and False)
    assert setup(ai) == 0
    out = capsys.readouterr().out
    assert "Open this address in your browser: http://127.0.0.1:" in out
    assert "Open this address in your browser: https://github.com/apps/" in out


def test_a_second_setup_changes_nothing(ai, capsys):
    setup(ai)
    held = ai.load()
    capsys.readouterr()
    assert setup(ai) == 0
    assert "already set up" in capsys.readouterr().out
    assert ai.load() == held and len(ai.gh.manifests) == 1


def test_an_identity_for_another_repository_is_not_replaced(ai, capsys):
    setup(ai)
    assert ai.main(["setup", "--repo", "ownerlogin/elsewhere"], TTY) == 1
    assert "unset" in capsys.readouterr().err
    assert ai.load().repo == REPO


def test_the_repository_defaults_from_gh(ai, tmp_path, monkeypatch):
    github_support.serve(tmp_path, monkeypatch, {})
    assert ai.main(["setup"], TTY) == 0
    ai.browser.join()
    assert ai.load().repo == REPO


@pytest.mark.parametrize("bad", ["../x", "a b/c", "owner", "o/..", "o/r/extra", "o/r;ls"])
def test_a_malformed_repository_is_refused_before_anything_happens(ai, bad):
    assert ai.main(["setup", "--repo", bad], TTY) == 1
    assert ai.gh.log == [] and ai.browser.opened == []


# -- the person gate ---------------------------------------------------------------------------

@pytest.mark.parametrize("marker", MARKERS)
def test_an_agent_marker_refuses_every_use_of_the_key_before_any_request(ai, monkeypatch, marker):
    setup(ai)
    before, held = list(ai.gh.log), ai.load()
    monkeypatch.setenv(marker, "1")
    for argv in (["setup", "--repo", REPO], ["token"], ["check", "--online"]):
        assert ai.main(argv, TTY) == 1
    assert ai.gh.log == before and len(ai.browser.opened) == 2
    monkeypatch.delenv(marker)
    assert ai.load() == held


def test_an_agent_marker_stops_a_first_setup_before_anything_is_opened_or_stored(ai, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert ai.main(["setup", "--repo", REPO], TTY) == 1
    assert ai.gh.log == [] and ai.browser.opened == [] and not stored_state(ai)


@pytest.mark.parametrize("terminal", [(False, False), (False, True), (True, False)])
def test_a_missing_terminal_refuses_setup_and_the_online_check(ai, terminal, capsys):
    for argv in (["setup", "--repo", REPO], ["check", "--online"]):
        assert ai.main(argv, terminal) == 1
    assert ai.gh.log == [] and ai.browser.opened == [] and not stored_state(ai)
    assert "terminal" in capsys.readouterr().err


def test_token_needs_a_terminal_on_stdin_and_on_stdout_or_stderr(ai, monkeypatch, capsys):
    setup(ai)
    monkeypatch.setattr(ai.person, "is_terminal", lambda _stream: False)
    capsys.readouterr()
    before = len(ai.gh.log)
    assert ai.main(["token"], (False, True)) == 1
    assert ai.main(["token"], (True, False)) == 1
    assert len(ai.gh.log) == before and capsys.readouterr().out == ""
    assert ai.main(["token"], (True, True)) == 0


def run_child(tmp_path, args, *, agent, terminal):
    env = {**os.environ, "POOLHOUSE_HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT / "src")}
    for name in MARKERS:
        env.pop(name, None)
    env.update({"CLAUDECODE": "1"} if agent else {})
    argv = [sys.executable, str(SCRIPT), *args]
    if not terminal:
        done = subprocess.run(argv, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        return done.returncode, done.stdout + done.stderr
    master, slave = pty.openpty()
    child = subprocess.Popen(argv, env=env, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    heard = b""
    while True:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        heard += chunk
    child.wait(timeout=60)
    os.close(master)
    return child.returncode, heard.decode(errors="replace")


@pytest.mark.skipif(sys.platform == "win32", reason="requires a POSIX pseudoterminal")
@pytest.mark.parametrize("command", ["setup", "token"])
@pytest.mark.parametrize("agent,terminal", [(True, True), (False, False), (True, False)])
def test_the_script_as_a_process_refuses_an_agent_and_a_missing_terminal(tmp_path, command, agent, terminal):
    code, heard = run_child(tmp_path, [command, "--repo", REPO] if command == "setup" else [command],
                            agent=agent, terminal=terminal)
    assert code == 1 and "agent-identity:" in heard, heard
    assert not (tmp_path / "home").exists()


def test_the_script_as_a_process_says_one_line_when_nothing_is_set_up(tmp_path):
    code, heard = run_child(tmp_path, ["check"], agent=False, terminal=False)
    assert code == 1
    assert heard.strip() == "agent identity: not set up; run scripts/agent-identity setup"


# -- token ----------------------------------------------------------------------------------------

def test_token_prints_one_installation_token_and_nothing_else(ai, capsys):
    setup(ai)
    capsys.readouterr()
    assert ai.main(["token"], TTY) == 0
    out = capsys.readouterr()
    token = out.out.strip()
    assert out.out == token + "\n" and token in ai.gh.tokens and out.err == ""
    who = ai.load()
    asked = seen(ai, "POST", f"/app/installations/{who.installation_id}/access_tokens")[-1]
    assert asked["auth"] == "Bearer"
    assert asked["body"] == {"permissions": ASKED, "repositories": [NAME]}


def test_the_app_jwt_is_rs256_for_ten_minutes_at_most_and_names_the_app(ai):
    setup(ai)
    who = ai.load()
    key = ai.gh.apps[who.app_id]["key"]
    signed = ai.app_jwt(who)
    claims = jwt.decode(signed, key.public_key(), algorithms=["RS256"], issuer=who.client_id)
    assert jwt.get_unverified_header(signed)["alg"] == "RS256"
    assert 0 < claims["exp"] - claims["iat"] <= 600
    assert ai.gh.app_of(signed)["id"] == who.app_id
    forged = jwt.encode({"iss": who.client_id, "iat": claims["iat"], "exp": claims["exp"]}, pem(KEYS[1] if key is KEYS[0] else KEYS[0]),
                        algorithm="RS256")
    assert ai.gh.app_of(forged) is None
    assert all(r["auth"] == "Bearer" for r in ai.gh.log if "installation" in r["path"] or "access_tokens" in r["path"])


def test_token_before_setup_says_what_to_run_and_prints_nothing(ai, capsys):
    assert ai.main(["token"], TTY) == 1
    out = capsys.readouterr()
    assert out.out == "" and "scripts/agent-identity setup" in out.err and ai.gh.log == []


def wrong_installation(ai, repos):
    """Point the stored identity at another installation of the same App."""
    who = ai.load()
    other = ai.gh.install(who.app_id)
    ai.gh.installations[other]["repos"] = repos
    who.installation_id = other
    ai.save(who, "")
    return other


@pytest.mark.parametrize("repos", [["ownerlogin/unrelated"], ["intruder/" + NAME]])
def test_a_token_for_the_wrong_installation_is_not_given_out(ai, capsys, repos):
    setup(ai)
    wrong_installation(ai, repos)
    capsys.readouterr()
    assert ai.main(["token"], TTY) == 1
    out = capsys.readouterr()
    assert out.out == "" and "no token was given out" in out.err
    assert not [t for t in ai.gh.tokens if t in out.err]


def test_a_stored_installation_that_does_not_exist_is_refused(ai, capsys):
    setup(ai)
    who = ai.load()
    who.installation_id = 999
    ai.save(who, "")
    capsys.readouterr()
    assert ai.main(["token"], TTY) == 1
    out = capsys.readouterr()
    assert out.out == "" and "404" in out.err and "check --online" in out.err


@pytest.mark.parametrize("knob,value,why", [
    ("token_permissions", {**ASKED, "administration": "write"}, "permissions that were not asked for"),
    ("token_permissions", {**ASKED, "contents": "admin"}, "permissions that were not asked for"),
    ("token_repos", [REPO, "ownerlogin/other"], "not " + REPO),
    ("token_lifetime", timedelta(hours=24), "does not last one hour"),
])
def test_a_token_that_is_wider_or_longer_than_asked_is_not_given_out(ai, capsys, knob, value, why):
    setup(ai)
    setattr(ai.gh, knob, value)
    capsys.readouterr()
    assert ai.main(["token"], TTY) == 1
    out = capsys.readouterr()
    assert out.out == "" and why in out.err


# -- check ----------------------------------------------------------------------------------------

def one_line(capsys):
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("agent identity: ")
    return out.strip()


def test_check_says_not_set_up_when_nothing_is_stored(ai, capsys):
    assert ai.main(["check"], TTY) == 1
    assert one_line(capsys) == "agent identity: not set up; run scripts/agent-identity setup"
    assert ai.gh.log == []


def test_check_says_ready_with_the_ids_and_the_repository(ai, capsys):
    setup(ai)
    capsys.readouterr()
    assert ai.main(["check", "--repo", REPO.upper()], (False, False)) == 0
    who = ai.load()
    assert one_line(capsys) == (f"agent identity: ready: App {who.slug} (id {who.app_id}), "
                                f"installation {who.installation_id} on {REPO}")


def test_check_says_when_the_app_is_made_but_not_installed(ai, capsys):
    ai.gh.selection = "all"
    setup(ai)
    capsys.readouterr()
    assert ai.main(["check"], TTY) == 1
    assert "made but not installed" in one_line(capsys)


def test_check_says_when_the_identity_is_for_another_repository(ai, capsys):
    setup(ai)
    capsys.readouterr()
    assert ai.main(["check", "--repo", "ownerlogin/elsewhere"], TTY) == 1
    assert "stored for " + REPO in one_line(capsys)


def test_check_online_asks_github_and_fails_when_the_installation_is_gone(ai, capsys):
    setup(ai)
    capsys.readouterr()
    assert ai.main(["check", "--online"], TTY) == 0
    assert one_line(capsys).startswith("agent identity: ready")
    ai.gh.installations.clear()
    assert ai.main(["check", "--online"], TTY) == 1
    assert "404" in one_line(capsys)


def test_the_environment_cannot_stand_in_for_the_keychain(ai, monkeypatch, capsys):
    monkeypatch.setenv(ai.ITEM, "not-a-record")
    assert ai.main(["check"], TTY) == 1
    assert "environment" in one_line(capsys)


def test_a_damaged_record_names_the_command_that_removes_it(ai, capsys):
    credentials.set(ai.ITEM, "not-base64!", keychain=True)
    assert ai.main(["check"], TTY) == 1
    line = one_line(capsys)
    assert "damaged" in line and f"credentials unset {ai.ITEM} --keychain" in line


# -- the document -----------------------------------------------------------------------------------

def test_the_document_names_the_permissions_the_script_asks_for_and_the_order(ai):
    text = (ROOT / "docs" / "github-protection.md").read_text(encoding="utf-8")
    section = text.split("## Agent identity")[1].split("## Push credential")[0]
    for words in ("Contents", "Pull requests", "Workflows", "Metadata"):
        assert words in section
    assert set(ai.PERMISSIONS) == {"contents", "metadata", "pull_requests", "workflows"}
    assert "scripts/agent-identity setup" in text.split("## Order of work")[1].split("## Agent identity")[0]
    assert "--identity-ready" not in text
