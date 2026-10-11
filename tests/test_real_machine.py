"""Nothing but the installed software on the real home touches the machine's own keystore, password dialogs or `security`.

2026-10-11: a dozen administrator-password dialogs (and, before, Keychain prompts) came up on the owner's Mac while workers ran
tests. The only guard had been an environment variable every process had to remember to set. These tests hold the structural
rule: `poolhouse.real_machine`, with no variable or argument that switches it on.
"""
from __future__ import annotations

import re
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from real_machine_guard import is_the_real_thing

from poolhouse import home, keystore, real_machine

SRC = Path(__file__).resolve().parents[1] / "src" / "poolhouse"


class NotReallyMacOS(KeyringBackend):
    """A fake that carries the real macOS backend's module name and counts what it is asked."""

    __module__ = "keyring.backends.macOS"
    priority = 5  # type: ignore[assignment]
    asked: list[str] = []  # noqa: RUF012

    def get_password(self, service, username):
        self.asked.append("get")

    def set_password(self, service, username, password):
        self.asked.append("set")

    def delete_password(self, service, username):
        self.asked.append("delete")


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A pretend real home, a pretend installed runtime inside it, and a state root that is a temporary directory: every
    condition holds except that the state root is the real home. The tests then move one condition at a time."""
    real = tmp_path / "account" / ".poolhouse"
    (real / "runtimes" / "id").mkdir(parents=True)
    monkeypatch.setattr(home, "account_roots", lambda: (real, tmp_path / "cache"))
    monkeypatch.setattr(real_machine, "_under_test", lambda: False)
    monkeypatch.setattr(real_machine, "_module_file", lambda: real / "runtimes" / "id" / "poolhouse" / "real_machine.py")
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "elsewhere" / ".poolhouse"))
    NotReallyMacOS.asked = []
    keyring.set_keyring(NotReallyMacOS())
    return real


def test_a_real_backend_in_a_temporary_home_is_never_asked(machine, tmp_path):
    ks = keystore.Keystore(directory=tmp_path / "ks")
    assert not ks.available()
    assert ks.backend().startswith("none: this computer's own keystore is not used here")
    with pytest.raises(keystore.KeystoreUnavailable, match=r"not the installed Poolhouse|not your own"):
        ks.provision()
    with pytest.raises(keystore.KeystoreUnavailable):
        ks.subkey("memory", "owner")
    assert NotReallyMacOS.asked == []


def test_the_installed_software_on_the_real_home_does_use_it(machine, tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(machine))
    assert real_machine.allowed()
    assert keystore.Keystore(directory=tmp_path / "ks").available()


@pytest.mark.parametrize("which", ["a test run", "another state root", "not the installed copy"])
def test_each_condition_alone_is_enough_to_refuse(machine, monkeypatch, tmp_path, which):
    monkeypatch.setenv("POOLHOUSE_HOME", str(machine))
    assert real_machine.refusal() == ""
    if which == "a test run":
        monkeypatch.setattr(real_machine, "_under_test", lambda: True)
    elif which == "another state root":
        monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "other" / ".poolhouse"))
    else:
        monkeypatch.setattr(real_machine, "_module_file", lambda: tmp_path / "checkout" / "src" / "poolhouse" / "real_machine.py")
    assert real_machine.refusal()
    with pytest.raises(real_machine.Refused):
        real_machine.require("a thing")
    assert not keystore.Keystore(directory=tmp_path / "ks").available()


@pytest.mark.parametrize("name,value", [("POOLHOUSE_NO_REAL_KEYSTORE", "0"), ("POOLHOUSE_REAL_KEYSTORE", "1"),
                                        ("POOLHOUSE_INSTALLED", "1"), ("POOLHOUSE_RUNTIME", "installed"), ("CI", ""),
                                        ("HOME", "/nonexistent")])
def test_no_variable_turns_it_on_for_a_path_outside_the_real_home(machine, monkeypatch, tmp_path, name, value):
    monkeypatch.setenv(name, value)
    assert real_machine.refusal()
    assert not keystore.Keystore(directory=tmp_path / "ks").available()


def test_a_symlink_into_the_real_home_is_the_real_home_and_a_lookalike_is_not(machine, tmp_path, monkeypatch):
    link = tmp_path / "link"
    link.symlink_to(machine)
    monkeypatch.setenv("POOLHOUSE_HOME", str(link))
    assert real_machine.refusal() == ""
    lookalike = tmp_path / "temp" / "account" / ".poolhouse"
    lookalike.mkdir(parents=True)
    monkeypatch.setenv("POOLHOUSE_HOME", str(lookalike))
    assert real_machine.refusal()


def test_a_test_run_is_refused_without_any_seam():
    assert "test run" in real_machine.refusal()


def test_every_other_backend_keyring_ships_counts_as_real():
    class Future(KeyringBackend):
        __module__ = "keyring.backends.someday"
        priority = 1  # type: ignore[assignment]

        def get_password(self, service, username):
            return None

        def set_password(self, service, username, password):
            return None

    assert keystore.is_real(Future())
    assert not keystore.is_real(keyring.backends.fail.Keyring())


def test_a_test_that_resolves_the_real_keyring_fails_there():
    from keyring.backends import fail

    from tests.conftest import real_keystore_classes

    for cls in real_keystore_classes():
        try:
            ring = cls()
        except Exception:  # noqa: BLE001, S112 - a backend that cannot be built on this host cannot be resolved either
            continue
        keyring.set_keyring(ring)
        with pytest.raises(RuntimeError, match="resolved the real keyring backend"):
            keyring.get_keyring()
        break
    keyring.set_keyring(fail.Keyring())
    assert not is_the_real_thing(keyring.get_keyring())


# What may start `osascript`, `security` or macOS authorization. The first group asks first (`real_machine`); the second only shows
# a dialog or text, which the suite's PATH shims and the notifier shim check; none opens an administrator or Keychain prompt itself.
ASKS_FIRST = {
    "node_signing.py": "security find-certificate / import / add-trusted-cert",
    "serve/wired_apply.py": "osascript ... with administrator privileges",
    "fleet/autostart_backends.py": "osascript ... with administrator privileges",
    "scrape/browser.py": "osascript System Events (Automation prompt)",
}
NO_PROMPT = {
    "serve/wired_cli.py": "names the way (sudo or osascript); wired_apply runs it",
    "fleet/room_routes.py": "names the way (osascript); wired_apply runs it",
    "desktop.py": "osascript display dialog (shimmed in tests)",
    "fleet/onboard/notify.py": "osascript display dialog (shimmed in tests)",
    "fleet/shard_split.py": "a pattern that keeps macOS-only code off other devices",
    "guard/shellcmds.py": "a list of commands the guard refuses",
    "keystore_guard.py": "tells macOS to fail an access that needs a dialog (SecKeychainSetUserInteractionAllowed)",
}
CALL = re.compile(r"""osascript|\[\s*["']security["']\s*,|(?<![-\w])security\s+(?:find|add|import|delete|unlock|list|cms|dump|authorizationdb)\b"""
                  r"""|authtrampoline|administrator privileges|AuthorizationExecute|AuthorizationCreate|SecKeychain|SecItem""")


def sources_that_call() -> set[str]:
    return {path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py")
            if path.name != "real_machine.py" and CALL.search(path.read_text(encoding="utf-8"))}


def test_no_new_osascript_security_or_authorization_call_outside_the_reviewed_list():
    found = sources_that_call()
    assert found - ASKS_FIRST.keys() - NO_PROMPT.keys() == set(), (
        "a new osascript, `security` or macOS authorization call: route it through poolhouse.real_machine.refusal() "
        "(and add it to ASKS_FIRST here) or, if it can show no prompt, list it in NO_PROMPT with the reason")
    assert (ASKS_FIRST.keys() | NO_PROMPT.keys()) - found == set(), "a listed file no longer calls: remove it from the list"


def test_every_file_that_starts_a_prompt_asks_real_machine_first():
    for name in ASKS_FIRST:
        assert "real_machine" in (SRC / name).read_text(encoding="utf-8"), name


def test_the_node_never_touches_the_operating_systems_keystore():
    app = Path(__file__).resolve().parents[1] / "app" / "poolhouse-node"
    crates = (app / "Cargo.toml").read_text(encoding="utf-8")
    assert not re.search(r"keyring|security-framework|keychain|secret-service|libsecret", crates, re.IGNORECASE)
    for source in (app / "src").rglob("*.rs"):
        assert not re.search(r"SecItem|SecKeychain|keyring::|Command::new\(\"(?:security|osascript)\"\)",
                             source.read_text(encoding="utf-8")), source
    assert "--key-stdin" in (app / "src" / "main.rs").read_text(encoding="utf-8")


@pytest.fixture
def nothing_spawns(monkeypatch):
    """Any process started through `subprocess.run` fails the test: the refusal must come before it."""
    def spawn(argv, *_a, **_k):
        raise AssertionError(f"a test started {argv!r}")

    monkeypatch.setattr("subprocess.run", spawn)


def test_the_administrator_prompt_is_not_shown_to_a_test_or_a_checkout(nothing_spawns):
    from poolhouse.fleet import autostart_backends
    from poolhouse.serve import wired_apply

    code, _out, why = wired_apply._run(["osascript", "-e", 'do shell script "true" with administrator privileges'], True)
    assert code == 1 and "not shown here" in why
    done, said = autostart_backends.elevated("true", "why")
    assert done is False and "not shown here" in said


def test_a_signing_certificate_is_never_made_or_read_outside_the_installed_software(nothing_spawns, tmp_path, monkeypatch):
    from poolhouse import node_signing

    signed: list[list[str]] = []
    monkeypatch.setattr(node_signing, "_run", lambda argv: signed.append(argv) or "")
    assert node_signing.sign(tmp_path / "app", "id", lambda _fingerprint: "req", "adhoc-req") == "ad-hoc"
    assert [argv[0] for argv in signed] == ["codesign"] and signed[0][3] == "-"
    with pytest.raises(real_machine.Refused):
        node_signing.certificate_sha1()


def test_the_frontmost_application_is_not_asked_of_system_events_outside_the_installed_software(nothing_spawns):
    import importlib

    browser = importlib.import_module("poolhouse.scrape.browser")

    assert browser._frontmost() == ""
