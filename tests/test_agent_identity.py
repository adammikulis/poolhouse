"""scripts/agent-identity: the App made from a manifest, its key kept in the keystore, the token minted from it,
and what refuses along the way. GitHub and the browser are fakes (poolhouse.testing.fakegithub)."""

from __future__ import annotations

import dataclasses
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

from poolhouse import credentials, httpguard
from poolhouse.fleet import updates
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
from poolhouse.workspace import github_release
from tests import github_support, memory_keys

ring = memory_keys.ring

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "agent-identity"
pytest_plugins = ["node_kit"]

TTY = (True, True)
MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE")


@pytest.fixture
def ai(tmp_path, monkeypatch):
    for name in MARKERS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    gh = FakeGitHub()
    loader = importlib.machinery.SourceFileLoader("agent_identity", str(ROOT / "src/poolhouse/workspace/github_identity.py"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    monkeypatch.setitem(sys.modules, loader.name, module)
    loader.exec_module(module)
    from poolhouse.workspace import github_app
    monkeypatch.setattr(github_app, "api", module.api)
    fetch = httpguard.fetch

    def scratch_api(url, **options):
        if url.startswith(gh.url + '/'):
            options['limits'] = dataclasses.replace(options['limits'], allow_hosts=frozenset({'127.0.0.1'}))
        return fetch(url, **options)
    monkeypatch.setattr(httpguard, 'fetch', scratch_api)
    browser = FakeBrowser(gh)
    for name, value in (("API", gh.url), ("POLL_S", 0.01), ("CREATE_WAIT_S", 10.0),
                        ("INSTALL_WAIT_S", 10.0), ("OPEN", browser)):
        monkeypatch.setattr(module, name, value)
    module.gh, module.browser = gh, browser
    yield module
    gh.close()


def setup(ai, *extra):
    code = ai.command(["setup", "--repo", REPO, *extra], TTY)
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
    assert ai.command(["token"], TTY) == 0
    assert ai.command(["check", "--online"], TTY) == 0
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
    assert ai.command(["setup", "--repo", "ownerlogin/elsewhere"], TTY) == 1
    assert "unset" in capsys.readouterr().err
    assert ai.load().repo == REPO


def test_the_repository_defaults_from_gh(ai, tmp_path, monkeypatch):
    github_support.serve(tmp_path, monkeypatch, {})
    assert ai.command(["setup"], TTY) == 0
    ai.browser.join()
    assert ai.load().repo == REPO


@pytest.mark.parametrize("bad", ["../x", "a b/c", "owner", "o/..", "o/r/extra", "o/r;ls"])
def test_a_malformed_repository_is_refused_before_anything_happens(ai, bad):
    assert ai.command(["setup", "--repo", bad], TTY) == 1
    assert ai.gh.log == [] and ai.browser.opened == []


# -- the person gate ---------------------------------------------------------------------------

@pytest.mark.parametrize("marker", MARKERS)
def test_an_agent_marker_refuses_every_use_of_the_key_before_any_request(ai, monkeypatch, marker):
    setup(ai)
    before, held = list(ai.gh.log), ai.load()
    monkeypatch.setenv(marker, "1")
    for argv in (["setup", "--repo", REPO], ["token"], ["check", "--online"]):
        assert ai.command(argv, TTY) == 1
    assert ai.gh.log == before and len(ai.browser.opened) == 2
    monkeypatch.delenv(marker)
    assert ai.load() == held


def test_an_agent_marker_stops_a_first_setup_before_anything_is_opened_or_stored(ai, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert ai.command(["setup", "--repo", REPO], TTY) == 1
    assert ai.gh.log == [] and ai.browser.opened == [] and not stored_state(ai)


@pytest.mark.parametrize("terminal", [(False, False), (False, True), (True, False)])
def test_a_missing_terminal_refuses_setup_and_the_online_check(ai, terminal, capsys):
    for argv in (["setup", "--repo", REPO], ["check", "--online"]):
        assert ai.command(argv, terminal) == 1
    assert ai.gh.log == [] and ai.browser.opened == [] and not stored_state(ai)
    assert "terminal" in capsys.readouterr().err


def test_token_needs_a_terminal_on_stdin_and_on_stdout_or_stderr(ai, monkeypatch, capsys):
    setup(ai)
    monkeypatch.setattr(ai.person, "is_terminal", lambda _stream: False)
    capsys.readouterr()
    before = len(ai.gh.log)
    assert ai.command(["token"], (False, True)) == 1
    assert ai.command(["token"], (True, False)) == 1
    assert len(ai.gh.log) == before and capsys.readouterr().out == ""
    assert ai.command(["token"], (True, True)) == 0


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
    assert ai.command(["token"], TTY) == 0
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
    assert ai.command(["token"], TTY) == 1
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
    assert ai.command(["token"], TTY) == 1
    out = capsys.readouterr()
    assert out.out == "" and "no token was given out" in out.err
    assert not [t for t in ai.gh.tokens if t in out.err]


def test_a_stored_installation_that_does_not_exist_is_refused(ai, capsys):
    setup(ai)
    who = ai.load()
    who.installation_id = 999
    ai.save(who, "")
    capsys.readouterr()
    assert ai.command(["token"], TTY) == 1
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
    assert ai.command(["token"], TTY) == 1
    out = capsys.readouterr()
    assert out.out == "" and why in out.err


# -- check ----------------------------------------------------------------------------------------

def one_line(capsys):
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("agent identity: ")
    return out.strip()


def test_check_says_not_set_up_when_nothing_is_stored(ai, capsys):
    assert ai.command(["check"], TTY) == 1
    assert one_line(capsys) == "agent identity: not set up; run scripts/agent-identity setup"
    assert ai.gh.log == []


def test_check_says_ready_with_the_ids_and_the_repository(ai, capsys):
    setup(ai)
    capsys.readouterr()
    assert ai.command(["check", "--repo", REPO.upper()], (False, False)) == 0
    who = ai.load()
    assert one_line(capsys) == (f"agent identity: ready: App {who.slug} (id {who.app_id}), "
                                f"installation {who.installation_id} on {REPO}")


def test_check_says_when_the_app_is_made_but_not_installed(ai, capsys):
    ai.gh.selection = "all"
    setup(ai)
    capsys.readouterr()
    assert ai.command(["check"], TTY) == 1
    assert "made but not installed" in one_line(capsys)


def test_check_says_when_the_identity_is_for_another_repository(ai, capsys):
    setup(ai)
    capsys.readouterr()
    assert ai.command(["check", "--repo", "ownerlogin/elsewhere"], TTY) == 1
    assert "stored for " + REPO in one_line(capsys)


def test_check_online_asks_github_and_fails_when_the_installation_is_gone(ai, capsys):
    setup(ai)
    capsys.readouterr()
    assert ai.command(["check", "--online"], TTY) == 0
    assert one_line(capsys).startswith("agent identity: ready")
    ai.gh.installations.clear()
    assert ai.command(["check", "--online"], TTY) == 1
    assert "404" in one_line(capsys)


def test_the_environment_cannot_stand_in_for_the_keychain(ai, monkeypatch, capsys):
    monkeypatch.setenv(ai.ITEM, "not-a-record")
    assert ai.command(["check"], TTY) == 1
    assert "environment" in one_line(capsys)


def test_a_damaged_record_names_the_command_that_removes_it(ai, capsys):
    credentials.set(ai.ITEM, "not-base64!", keychain=True)
    assert ai.command(["check"], TTY) == 1
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


def test_setup_api_uses_fixed_origin_bounded_nonredirect_transport(ai, monkeypatch):
    asked = []
    path = '/app-manifests/scratch-code/conversions'

    def fetch(url, **options):
        asked.append((url, options))
        limits = options['limits']
        assert limits.follow_redirects is False and limits.max_bytes == 8 << 20 and limits.timeout == 30
        with pytest.raises(ai.Failed):
            limits.vet('https://host.invalid/escape')
        return httpguard.Fetched(url, 302, {'location': 'https://host.invalid/escape'}, b'', ())

    monkeypatch.setattr(httpguard, 'fetch', fetch)
    with pytest.raises(ai.Failed, match='302') as refused:
        ai.api('POST', path, hint='retry', bearer='scratch-bearer', payload={})
    assert len(asked) == 1 and asked[0][0] == ai.API + path
    assert asked[0][1]['headers']['Authorization'] == 'Bearer scratch-bearer'
    assert 'scratch-bearer' not in str(refused.value)


@pytest.mark.parametrize('problem', ['oversized', 'malformed'])
def test_setup_api_response_refusals_never_expose_secret_bytes(ai, monkeypatch, problem):
    def fetch(url, **options):
        if problem == 'oversized':
            raise httpguard.Refused('scratch-bearer')
        return httpguard.Fetched(url, 200, {}, b'scratch-bearer', ())
    monkeypatch.setattr(httpguard, 'fetch', fetch)
    with pytest.raises(ai.Failed) as refused:
        ai.api('POST', '/app-manifests/scratch-code/conversions', hint='retry', bearer='scratch-bearer', payload={})
    assert 'scratch-bearer' not in str(refused.value)



@pytest.mark.parametrize('command', ['push-development', 'publish-release'])
def test_publication_sigterm_restores_previous_handler(ai, monkeypatch, capsys, command):
    import signal

    from poolhouse.workspace import github_release

    previous = signal.getsignal(signal.SIGTERM)

    def canceled(source):
        signal.raise_signal(signal.SIGTERM)

    operation = 'push_development' if command == 'push-development' else 'publish_release'
    monkeypatch.setattr(github_release, operation, canceled)
    assert ai.command([command], (False, False)) == 130
    assert signal.getsignal(signal.SIGTERM) == previous
    assert 'stopped' in capsys.readouterr().err


@pytest.fixture
def installed_setup(ai, tmp_path, monkeypatch, workspace_node):
    prefix = tmp_path / 'installed'
    package = prefix / 'poolhouse'
    (package / 'fleet').mkdir(parents=True)
    commit = 'a' * 40
    (package / 'fleet/built-from').write_text(commit)
    selected = ai.runtime.Runtime(prefix, commit, '0.3.0-dev.1', 'fixture')
    monkeypatch.setattr(ai.runtime, 'selected', lambda: selected)
    monkeypatch.setattr(github_release.sys, 'prefix', str(prefix))
    monkeypatch.setattr(github_release, '__file__', str(package / 'workspace/github_release.py'))
    monkeypatch.setattr(ai, '__file__', str(package / 'workspace/github_identity.py'))
    monkeypatch.setattr(github_release.real_machine, 'require', lambda action: None)
    actor = workspace_node.member('bootstrap-lead')
    monkeypatch.setattr(github_release, 'connect', lambda: workspace_node.session(actor))
    monkeypatch.setenv('POOLHOUSE_WORKSPACE_AGENT', actor.name)
    monkeypatch.setenv('POOLHOUSE_AGENT', 'codex')
    gates = []
    monkeypatch.setattr(github_release.authority, 'require', lambda gate, action: gates.append(gate))
    checkout = github_support.git_checkout(tmp_path / 'source', f'https://github.com/{REPO}.git',
        ('-c', 'user.name=Codex', '-c', 'user.email=codex@agents.invalid', 'commit', '--allow-empty', '-qm', 'chore: fixture'))
    monkeypatch.setattr(ai.runtime_wheel, 'source_checkout', lambda: checkout)
    monkeypatch.setattr(updates, 'REPO', REPO)
    monkeypatch.setattr(ai, 'default_repo', lambda: pytest.fail('personal gh/source helper must not run'))
    return selected, actor, gates


def test_installed_lead_setup_uses_only_own_app_holder_and_no_terminal(ai, installed_setup, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(credentials, 'status', lambda *a: pytest.fail('public credential status'))
    monkeypatch.setattr(credentials, 'get', lambda *a: pytest.fail('public credential resolver'))
    assert ai.command(['setup'], (False, False)) == 0
    ai.browser.join()
    held = ai.decode(credentials._open(ai.ITEM))
    assert held.repo == REPO and held.installation_id
    assert installed_setup[2] and set(installed_setup[2]) == {'github.release'}
    before = len(ai.browser.opened)
    assert ai.command(['setup'], (False, False)) == 0
    assert len(ai.browser.opened) == before
    output = capsys.readouterr()
    assert not leaks(ai, tmp_path, output.out, output.err)
    assert all(token not in output.out + output.err for token in ai.gh.tokens)


@pytest.mark.parametrize('refusal', ['child', 'actor', 'selection', 'module', 'repo', 'gate'])
def test_delegated_setup_refuses_before_app_load_api_or_browser(ai, installed_setup, monkeypatch, workspace_node, refusal):
    selected, actor, _ = installed_setup
    monkeypatch.setattr(credentials, '_open', lambda *a, **k: pytest.fail('credential access before admission'))
    if refusal == 'child':
        child = workspace_node.member('bootstrap-child', parent=actor)
        monkeypatch.setattr(github_release, 'connect', lambda: workspace_node.session(child))
        monkeypatch.setenv('POOLHOUSE_WORKSPACE_AGENT', child.name)
    elif refusal == 'actor':
        monkeypatch.setenv('POOLHOUSE_WORKSPACE_AGENT', 'other-agent')
    elif refusal == 'selection':
        monkeypatch.setattr(ai.runtime, 'selected', lambda: None)
    elif refusal == 'module':
        monkeypatch.setattr(ai, '__file__', str(selected.prefix.parent / 'source.py'))
    elif refusal == 'gate':
        monkeypatch.setattr(github_release.authority, 'require', lambda *a: ai.person.require_person('fixture gate', (False, False)))
    args = ['setup', '--repo', 'other/project'] if refusal == 'repo' else ['setup']
    assert ai.command(args, (False, False)) == 1
    assert ai.gh.log == [] and ai.browser.opened == []


@pytest.mark.parametrize('change', ['actor', 'selection', 'source'])
def test_delegated_setup_revalidates_after_browser_before_exchange(ai, installed_setup, monkeypatch, change):
    exchange = ai.exchange

    def changed(code, repo, kind, check=None):
        if change == 'actor':
            monkeypatch.setenv('POOLHOUSE_WORKSPACE_AGENT', 'other-agent')
        elif change == 'selection':
            monkeypatch.setattr(ai.runtime, 'selected', lambda: None)
        else:
            subprocess.run(['git', '-C', str(ai.runtime_wheel.source_checkout()), '-c', 'user.name=Codex',
                            '-c', 'user.email=codex@agents.invalid', 'commit', '--allow-empty', '-qm',
                            'chore: changed fixture'], check=True, capture_output=True)
        return exchange(code, repo, kind, check)
    monkeypatch.setattr(ai, 'exchange', changed)
    assert ai.command(['setup'], (False, False)) == 1
    ai.browser.join()
    assert not any('conversions' in row['path'] for row in ai.gh.log)
    assert credentials._open(ai.ITEM) is None


def test_delegated_setup_preserves_an_existing_other_repo_identity(ai, installed_setup):
    held = ai.Identity('other/project', 7, 'client', 'app', pem(KEYS[0]), 9)
    credentials.set(ai.ITEM, ai.encode(held), keychain=True)
    assert ai.command(['setup'], (False, False)) == 1
    assert ai.decode(credentials._open(ai.ITEM)) == held
    assert ai.gh.log == [] and ai.browser.opened == []


@pytest.mark.parametrize('change', ['actor', 'record'])
def test_delegated_setup_revalidates_before_saving_and_preserves_other_records(ai, installed_setup, monkeypatch, change):
    save = ai.save
    other = ai.Identity('other/project', 7, 'client', 'app', pem(KEYS[0]), 9)

    def changed(who, undo, check=None, expected=None):
        if change == 'actor':
            monkeypatch.setenv('POOLHOUSE_WORKSPACE_AGENT', 'other-agent')
        else:
            credentials.set(ai.ITEM, ai.encode(other), keychain=True)
        return save(who, undo, check, expected)
    monkeypatch.setattr(ai, 'save', changed)
    assert ai.command(['setup'], (False, False)) == 1
    ai.browser.join()
    held = credentials._open(ai.ITEM)
    assert (ai.decode(held) == other) if change == 'record' else held is None


def test_delegated_pending_installation_resumes_without_creating_another_app(ai, installed_setup):
    ai.gh.selection = 'all'
    assert ai.command(['setup'], (False, False)) == 1
    ai.browser.join()
    assert len(ai.gh.apps) == 1 and ai.decode(credentials._open(ai.ITEM)).installation_id is None
    ai.gh.selection = 'selected'
    ai.gh.installations.clear()
    assert ai.command(['setup'], (False, False)) == 0
    ai.browser.join()
    assert len(ai.gh.apps) == 1 and ai.decode(credentials._open(ai.ITEM)).installation_id


def test_isolated_script_dispatches_package_without_source_path_injection(tmp_path):
    installed = tmp_path / 'installed'
    installed.mkdir()
    (installed / 'poolhouse').symlink_to(ROOT / 'src' / 'poolhouse', target_is_directory=True)
    code = ("import runpy,sys,types;sys.path.insert(0," + repr(str(installed)) + ");from poolhouse import runtime;before=list(sys.path);"
            "module=types.ModuleType('poolhouse.workspace.github_identity');"
            "module.command=lambda: print('installed isolated dispatch',sys.flags.isolated,sys.path==before) or 0;"
            "sys.modules[module.__name__]=module;runpy.run_path(" + repr(str(SCRIPT)) + ",run_name='__main__')")
    done = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True,
                          env={**os.environ, 'PYTHONPATH': str(tmp_path / 'hostile-source')}, timeout=30)
    assert done.returncode == 0 and done.stdout.strip() == 'installed isolated dispatch 1 True'


def test_delegated_setup_refuses_expired_preparation_and_locked_app(ai, installed_setup, monkeypatch):
    prepared = ai.Preparation.capture(None)
    with pytest.raises(ai.Failed, match='expired'):
        dataclasses.replace(prepared, deadline=0).check()
    monkeypatch.setattr(credentials, '_open', lambda *a, **k: None)
    monkeypatch.setattr(credentials, '_wrapped', lambda **options: {ai.ITEM: 'wrapped-ciphertext'})
    assert ai.command(['setup'], (False, False)) == 1
    assert ai.gh.log == [] and ai.browser.opened == []


def test_wrapped_app_authentication_failure_is_not_an_unlock_or_initial_setup_request(ai, installed_setup, ring, capsys):
    credentials.set(ai.ITEM, "existing-app-record", keychain=True)
    original = credentials.wrapped_path().read_bytes()
    store = credentials.keystore.default()
    store.lock()
    ring.held.clear()
    store.provision()
    assert ai.command(["check"], (False, False)) == 1
    assert ai.command(["setup"], (False, False)) == 1
    output = capsys.readouterr()
    for line in (output.out, output.err):
        assert "current encryption key or credential context" in line
        assert "preserved" in line
        assert all(word not in line for word in ("unlock", "unset", "initial setup", "existing-app-record"))
    assert ai.gh.log == [] and ai.browser.opened == []
    assert credentials.wrapped_path().read_bytes() == original


@pytest.mark.parametrize("record", [b"not-json", b"[]", b"null", b'{"schema_version":2,"values":{}}',
                                   b'{"schema_version":true,"values":{}}', b'{"schema_version":1,"values":null}',
                                   b'{"schema_version":1,"schema_version":1,"values":{}}',
                                   b'{"schema_version":1,"values":{"POOLHOUSE_AGENT_APP":"held"},"values":{}}',
                                   b'{"schema_version":1,"values":{"POOLHOUSE_AGENT_APP":"held","POOLHOUSE_AGENT_APP":null}}'])
def test_damaged_wrapped_app_container_never_becomes_initial_setup(ai, installed_setup, capsys, record):
    path = credentials.wrapped_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(record)
    assert ai.command(["check"], (False, False)) == 1
    assert ai.command(["setup"], (False, False)) == 1
    output = capsys.readouterr()
    assert "valid encrypted credential record" in output.out + output.err
    assert "preserved" in output.out + output.err
    assert ai.gh.log == [] and ai.browser.opened == []
    assert path.read_bytes() == record


@pytest.mark.parametrize('bearer', ['', ' fixture-token '])
@pytest.mark.parametrize('status', [200, 401])
def test_api_authorization_headers_do_not_send_empty_bearers(ai, monkeypatch, bearer, status):
    calls = []

    def fetch(url, **options):
        headers = options['headers']
        assert headers.get('Authorization') != 'Bearer '
        if bearer == '':
            assert 'Authorization' not in headers
        else:
            assert headers['Authorization'] == f'Bearer {bearer}'
        calls.append(url)
        return httpguard.Fetched(url, status, {}, b'{"id":7}', ())

    monkeypatch.setattr(httpguard, 'fetch', fetch)
    if status == 401:
        with pytest.raises(ai.Failed, match='401'):
            ai.api("GET", "/users/fixture", hint="setup", bearer=bearer)
    else:
        assert ai.api("GET", "/users/fixture", hint="setup", bearer=bearer) == {'id': 7}
    assert len(calls) == 1
