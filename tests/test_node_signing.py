"""Poolhouse's macOS signature is local (ad hoc) unless the user opted into a certificate, and only that user's command adds, trusts or
deletes one.

`security` and `openssl` never run here: `Tools` records each command and answers it with canned, plumbing-only text standing for a keychain,
so no keychain is read or written. `codesign` runs for real for local signatures and verification, on bundles under the test's own state root;
signing with a certificate is canned too, because there is none.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import (
    app_bundle,
    caller,
    home,
    keystore,
    node_app,
    node_binary,
    node_permission,
    node_signing,
    real_machine,
    runtime,
    runtime_deploy,
    runtime_identity,
    runtime_store,
    setup,
)
from poolhouse.lock import Busy, only_one

NAME = "Poolhouse Local Signing"


def canned(label: bytes) -> tuple[str, str]:
    """A plumbing-only certificate: its fingerprint and its PEM text."""
    der = b"plumbing-only canned certificate, not a real one: " + label
    pem = f"-----BEGIN CERTIFICATE-----\n{base64.b64encode(der).decode()}\n-----END CERTIFICATE-----\n"
    return hashlib.sha1(der, usedforsecurity=False).hexdigest(), pem


(SHA, CANNED_PEM), (OTHER, OTHER_PEM) = canned(b"one"), canned(b"two")
PEMS = {SHA: CANNED_PEM, OTHER: OTHER_PEM}
RUN = subprocess.run
LOOKS = ("find-identity", "find-certificate", "trust-settings-export")
LOCAL = '# designated => cdhash H"'


@pytest.fixture
def macos():
    """The test runs macOS's own codesign."""
    if sys.platform != "darwin":
        pytest.skip("codesign is macOS's")


class Tools:
    """A plumbing-only keychain: every command is recorded and `security` and `openssl` are answered from ``certificates`` and the
    trust ``settings``. A function in ``on`` runs when its `security` verb (or "sign", for a codesign signature) is asked for."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.certificates: dict[str, dict[str, bool]] = {}
        self.settings: set[str] = set()
        self.on: dict = {}
        self.certificate_signing_fails = False
        self.trust_step: BaseException | int = 0
        self.refuses_deletion = False
        self.keeps_trust = False
        self.refuses_untrust = False
        self.listing_fails = False
        self.export_fails = False

    def trusted(self, sha: str = SHA, *, key: bool = True, trusted: bool = True) -> None:
        self.certificates[sha] = {"key": key, "trusted": trusted}
        if trusted:
            self.settings.add(sha)

    def writes(self) -> list[list[str]]:
        return [argv for argv in self.calls
                if Path(argv[0]).name == "openssl" or (argv[0] == "security" and argv[1] not in LOOKS)]

    def signatures(self) -> list[str]:
        return [argv[argv.index("--sign") + 1] for argv in self.calls if argv[0] == "codesign" and "--sign" in argv]

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        name = Path(argv[0]).name
        if name == "codesign":
            if "--sign" in argv:
                self.on.get("sign", lambda _argv: None)(argv)
                if argv[argv.index("--sign") + 1] != "-":
                    failed = self.certificate_signing_fails
                    return subprocess.CompletedProcess(argv, int(failed), "", "plumbing-only canned: errSecInternalComponent" if failed else "")
            return RUN(argv, **kwargs)
        if name == "openssl":
            for option, text in (("-keyout", "plumbing-only canned key\n"), ("-out", CANNED_PEM)):
                if option in argv:
                    Path(argv[argv.index(option) + 1]).write_text(text)
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[0] == "security", f"plumbing-only keychain: unexpected command {argv}"
        self.on.get(argv[1], lambda _rest: None)(argv[2:])
        code, out = self._security(argv[1], argv[2:])
        none = "No Trust Settings were found." if argv[1] == "trust-settings-export" and not self.export_fails else "SecKeychain error"
        return subprocess.CompletedProcess(argv, code, out, "" if code == 0 else f"plumbing-only canned: {none}")

    def _security(self, verb: str, rest: list[str]) -> tuple[int, str]:
        if verb in ("find-identity", "find-certificate") and self.listing_fails:
            return 1, ""
        if verb == "find-identity":
            held = [sha for sha, row in self.certificates.items() if row["key"] and (row["trusted"] or "-v" not in rest)]
            lines = [f'  {number}) {sha.upper()} "{NAME}"' for number, sha in enumerate(held, 1)]
            return 0, "\n".join([*lines, f"     {len(held)} valid identities found", ""])
        if verb == "find-certificate":
            if "-p" in rest:
                rows = [PEMS[sha] for sha in self.certificates]
            else:
                rows = [f'SHA-256 hash: {"0" * 64}\nSHA-1 hash: {sha.upper()}\nkeychain: "plumbing-only"\nattributes:\n    "labl"<blob>="{NAME}"\n'
                        for sha in self.certificates]
            return (0, "".join(rows)) if rows else (44, "")
        if verb == "trust-settings-export":
            if not self.settings or self.export_fails:
                return 1, ""
            issuer = bytes([len(NAME)]) + NAME.encode()
            Path(rest[0]).write_bytes(plistlib.dumps({"trustVersion": 1, "trustList": {sha.upper(): {"issuerName": issuer} for sha in self.settings}}))
            return 0, ""
        if verb == "import":
            self.certificates[SHA] = {"key": True, "trusted": False}
            return 0, ""
        if verb == "add-trusted-cert":
            if isinstance(self.trust_step, BaseException):
                raise self.trust_step
            if self.trust_step == 0:
                self.trusted(SHA)
            return self.trust_step, ""
        if verb == "remove-trusted-cert":
            body = "".join(Path(rest[-1]).read_text().split("-----")[2].split())
            sha = hashlib.sha1(base64.b64decode(body), usedforsecurity=False).hexdigest()
            if self.refuses_untrust or sha not in self.settings:
                return 1, ""
            self.settings.discard(sha)
            return 0, ""
        assert verb in ("delete-identity", "delete-certificate"), f"plumbing-only keychain: unexpected security {verb}"
        sha = rest[rest.index("-Z") + 1].lower()
        row = self.certificates.get(sha)
        if self.refuses_deletion or row is None or (verb == "delete-identity" and not row["key"]):
            return 1, ""
        del self.certificates[sha]
        if "-t" in rest and not self.keeps_trust:
            self.settings.discard(sha)
        return 0, ""


@pytest.fixture
def tools(monkeypatch):
    """The installed software's view of a recorded, plumbing-only keychain, called by a process no agent marked."""
    made = Tools()
    monkeypatch.setattr(node_signing, "subprocess", SimpleNamespace(run=made.run, SubprocessError=subprocess.SubprocessError,
                                                                    TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(node_signing, "real_machine", SimpleNamespace(require=lambda what: None, Refused=real_machine.Refused))
    for name in caller.AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(caller, "confined", lambda: False)
    monkeypatch.setattr(app_bundle, "ICON_SVG", Path("/nowhere/Poolhouse.svg"))
    monkeypatch.setattr(node_app, "ICON_SVG", Path("/nowhere/Poolhouse.svg"))
    return made


@pytest.fixture
def installed(tools, monkeypatch):
    """`tools` on what counts as the installed software, so the runtime's checks ask the keychain."""
    monkeypatch.setattr(runtime_identity, "applies", lambda: True)
    return tools


def node_program(tmp_path: Path, name: str = "poolhouse-node") -> Path:
    binary = tmp_path / name
    shutil.copyfile("/usr/bin/true", binary)
    binary.chmod(0o755)
    return binary


def interpreter() -> Path:
    return Path(os.path.realpath(sys.executable))


@pytest.mark.usefixtures("macos")
def test_no_build_or_start_makes_imports_or_trusts_a_certificate(tmp_path, installed):
    built = node_app.build(node_program(tmp_path))
    started = node_app.bundled(node_program(tmp_path), "0" * 64)
    assert installed.writes() == [], "starting the node changes no keychain"
    runtime_identity.ensure_bundle(interpreter())
    assert installed.writes() == [], "only the user's enable command changes the keychain"
    assert built["signing"] == "ad-hoc" and started == node_app.executable_path()
    assert set(installed.signatures()) == {"-"}
    for bundle in (node_app.bundle_path(), runtime_identity.bundle_path()):
        assert runtime_identity.requirement_of(bundle).startswith(LOCAL), "codesign's own requirement, which no other program satisfies by name"


@pytest.mark.usefixtures("macos")
def test_a_certificate_that_cannot_sign_gives_a_local_signature_says_so_and_blocks_nothing(tmp_path, installed, capsys):
    installed.trusted()
    installed.certificate_signing_fails = True
    runtime_identity.ensure_bundle(interpreter())
    assert node_app.build(node_program(tmp_path))["signing"] == "ad-hoc"
    assert installed.signatures() == [SHA, "-", SHA, "-"] and installed.writes() == []
    assert runtime_identity.bundle_problem() == "" and runtime_identity.requirement_of(runtime_identity.bundle_path()).startswith(LOCAL)
    said = capsys.readouterr().err
    assert "signing locally instead" in said and "errSecInternalComponent" in said


def test_a_usable_certificate_signs_with_the_requirement_that_names_it(tmp_path, tools, monkeypatch):
    tools.trusted()
    ran, real = [], node_signing._run
    monkeypatch.setattr(node_signing, "_run",  # plumbing-only: there is no certificate to sign or verify with
                        lambda argv: (ran.append(argv) or "") if argv[0] == "codesign" else real(argv))
    assert node_signing.sign(tmp_path / "P.app", node_app.BUNDLE_ID, node_app.requirement) == "certificate"
    assert ran[0] == ["codesign", "--force", "--sign", SHA, "--identifier", node_app.BUNDLE_ID, "--timestamp=none",
                      f'-r=designated => identifier "app.poolhouse.node" and certificate leaf = H"{SHA}"', str(tmp_path / "P.app")]
    assert ran[1:] == [["codesign", "--verify", "--strict", str(tmp_path / "P.app")]] and tools.writes() == []


def test_a_certificate_that_is_not_trusted_for_signing_is_not_opted_in(tools):
    tools.trusted(trusted=False)
    assert node_signing.usable_certificate() == ""
    tools.trusted()
    assert node_signing.usable_certificate() == SHA


@pytest.mark.usefixtures("macos")
def test_a_local_bundle_is_ready_and_is_not_built_again(installed):
    executable = runtime_identity.ensure_bundle(interpreter())
    stamp, signed = executable.stat().st_ino, len(installed.signatures())
    assert runtime_identity.ensure_bundle(interpreter()) == executable and executable.stat().st_ino == stamp
    assert runtime_identity.bundle_problem() == "" and runtime_identity.unit_problem() == ""
    assert len(installed.signatures()) == signed == 1 and installed.writes() == []


@pytest.mark.usefixtures("macos")
def test_an_opted_in_mac_with_a_local_bundle_tries_the_certificate_once_at_the_next_binding(installed):
    runtime_identity.ensure_bundle(interpreter())
    installed.trusted()
    installed.certificate_signing_fails = True
    executable = runtime_identity.ensure_bundle(interpreter())
    assert installed.signatures() == ["-", SHA, "-"] and executable.is_file()
    assert runtime_identity.bundle_problem() == "", "a bundle signed locally after a failure still runs"


@pytest.mark.usefixtures("macos")
def test_only_a_signature_with_codesigns_own_requirement_or_the_certificates_is_poolhouses(tmp_path):
    bundle = runtime_identity.bundle_path()
    runtime_identity.executable_path().parent.mkdir(parents=True)
    shutil.copyfile("/usr/bin/true", runtime_identity.executable_path())
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(runtime_identity.info_plist("0", icon=False)))

    def signed(identifier: str, *requirement: str) -> str:
        subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", identifier, "--timestamp=none", *requirement, str(bundle)],
                       check=True, capture_output=True)
        return runtime_identity.signature_problem(bundle)

    assert signed(runtime_identity.BUNDLE_ID) == ""
    assert "not signed as Poolhouse" in signed(runtime_identity.BUNDLE_ID, f'-r=designated => identifier "{runtime_identity.BUNDLE_ID}"'), \
        "any program that claims the id would satisfy it"
    assert "not signed as Poolhouse" in signed("app.somebody.else")
    named = f'identifier "{runtime_identity.BUNDLE_ID}" or certificate leaf = H"{"0" * 40}"'
    assert "not signed as Poolhouse" in signed(runtime_identity.BUNDLE_ID, f"-r=designated => {named}"), "the id alone satisfies it"
    assert "not signed as Poolhouse" in signed(runtime_identity.BUNDLE_ID, f"-r={runtime_identity.requirement('0' * 40)}"), \
        "the certificate's words on a signature that has no certificate"


@pytest.mark.parametrize("marker", ["CLAUDECODE", "POOLHOUSE_AGENT"])
def test_enable_and_remove_are_refused_to_an_agent_and_run_nothing(tools, monkeypatch, marker):
    monkeypatch.setenv(marker, "1")
    said = []
    for change in (node_signing.enable, node_signing.remove):
        with pytest.raises(caller.AgentRefused, match="must not write your keychain"):
            change(said.append)
    assert tools.calls == [] and said == []


def test_enable_says_what_it_adds_before_it_changes_anything(tools):
    node_signing.enable(lambda text: tools.calls.append(["say", text]))
    steps = [argv for argv in tools.calls if argv[0] == "say" or argv in tools.writes()]
    assert steps[0][0] == "say", "the words come first"
    told = steps[0][1]
    for word in (NAME, "login keychain", "private key", "trusted for code signing", "password", "poolhouse local-network certificate remove"):
        assert word in told
    assert [argv[1] for argv in tools.writes()] == ["req", "pkcs12", "import", "add-trusted-cert"]
    assert tools.certificates == {SHA: {"key": True, "trusted": True}}


@pytest.mark.parametrize("how", [1, subprocess.TimeoutExpired(["security"], 1), KeyboardInterrupt()],
                         ids=["declined", "timed-out", "interrupted"])
def test_a_trust_step_that_does_not_finish_removes_what_was_imported(tools, how):
    tools.trust_step = how
    with pytest.raises((node_signing.SigningError, subprocess.TimeoutExpired, KeyboardInterrupt)):
        node_signing.enable(lambda _text: None)
    assert tools.certificates == {}, "nothing is left behind"
    deleted = tools.writes()[-1]
    assert deleted[1:4] == ["delete-identity", "-Z", SHA] and "-t" not in deleted, "no trust setting was made, so none is asked about"


def test_enable_where_the_certificate_is_already_usable_changes_nothing(tools):
    tools.trusted()
    said = []
    node_signing.enable(said.append)
    assert tools.writes() == [] and "already" in " ".join(said)


def test_remove_deletes_certificate_key_and_trust_and_a_second_run_changes_nothing(tools):
    tools.trusted()
    tools.trusted(OTHER, key=False, trusted=False)
    node_signing.remove(lambda _text: None)
    assert tools.certificates == {}
    assert [(argv[1], argv[3], "-t" in argv) for argv in tools.writes()] == [("delete-identity", SHA, True), ("delete-certificate", OTHER, True)]
    before = len(tools.writes())
    said = []
    node_signing.remove(said.append)
    assert len(tools.writes()) == before and "nothing to remove" in " ".join(said)


def test_a_removal_macos_refuses_says_what_is_left(tools):
    tools.trusted()
    tools.refuses_deletion = True
    with pytest.raises(node_signing.SigningError, match="still in your login keychain"):
        node_signing.remove(lambda _text: None)
    assert SHA in tools.certificates and all("-t" in argv for argv in tools.writes()), "a trusted certificate is never deleted around its trust"


@pytest.mark.usefixtures("macos")
def test_the_command_signs_the_apps_again_after_enable_and_after_remove(tmp_path, installed):
    runtime_identity.ensure_bundle(interpreter())
    node_app.build(node_program(tmp_path))
    installed.calls.clear()
    assert node_permission.main(["certificate", "enable"]) == 0
    assert installed.signatures() == [SHA, SHA] and installed.certificates[SHA]["trusted"]
    installed.calls.clear()
    assert node_permission.main(["certificate", "remove"]) == 0
    assert installed.signatures() == ["-", "-"] and installed.certificates == {}
    for bundle in (node_app.bundle_path(), runtime_identity.bundle_path()):
        assert runtime_identity.requirement_of(bundle).startswith(LOCAL)
        assert subprocess.run(["codesign", "--verify", "--strict", str(bundle)], check=False).returncode == 0
        assert not (bundle.parent / ".building").exists()


def test_the_command_refused_to_an_agent_fails_and_says_why(tools, monkeypatch, capsys):
    monkeypatch.setattr(node_permission.sys, "platform", "darwin")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert node_permission.main(["certificate", "enable"]) == 1
    assert tools.calls == [] and "refused to an agent" in capsys.readouterr().err


def test_another_system_has_no_certificate_to_change(tools, monkeypatch, capsys):
    monkeypatch.setattr(node_permission.sys, "platform", "linux")
    assert node_permission.main(["certificate", "enable"]) == 1
    assert tools.calls == [] and "macOS" in capsys.readouterr().err


def test_setup_shows_the_command_and_changes_nothing(installed):
    [local] = setup.signature_findings()
    assert local.good and local.fix == [] and local.said.startswith("local")
    assert "poolhouse local-network certificate enable" in local.note and "before macOS asks for your password" in local.note
    installed.trusted()
    [signed] = setup.signature_findings()
    assert signed.good and NAME in signed.said and "poolhouse local-network certificate remove" in signed.note
    assert installed.writes() == [] and installed.signatures() == []


def test_setup_says_nothing_about_signing_where_poolhouse_signs_nothing(tools):
    assert setup.signature_findings() == [] and tools.calls == []


def holds_a_key() -> None:
    """The state file saying Poolhouse keeps its encryption key in the keystore; no keystore is touched."""
    directory = home.state("keystore")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "provisioned.json").write_text(json.dumps({"at": 1.0}))


def reads_as_certificate_signed(monkeypatch, bundle: Path) -> None:
    """Plumbing only: ``bundle`` reads as signed with the certificate, which no test has a key for."""
    asked, formed = runtime_identity.requirement_of, getattr(runtime_identity, "signature_form", None)
    monkeypatch.setattr(runtime_identity, "requirement_of",
                        lambda path: runtime_identity.requirement(SHA) if path == bundle else asked(path))
    monkeypatch.setattr(runtime_identity, "signature_form",
                        lambda path, identifier: "certificate" if path == bundle else formed(path, identifier), raising=False)


@pytest.mark.usefixtures("macos")
@pytest.mark.parametrize(("keychain", "kept"), [("cannot sign", True), ("cannot be listed", True), ("holds no certificate", False),
                                                ("certificate is not trusted", False), ("certificate has no key", False)],
                         ids=["cannot-sign", "cannot-be-listed", "no-certificate", "untrusted", "keyless"])
def test_a_certificate_signed_executable_stays_when_the_next_one_cannot_be_signed_with_it(installed, monkeypatch, capsys, keychain, kept):
    executable = runtime_identity.ensure_bundle(interpreter())
    stamp = executable.stat().st_ino
    reads_as_certificate_signed(monkeypatch, runtime_identity.bundle_path())
    if keychain != "holds no certificate":
        installed.trusted(trusted=keychain != "certificate is not trusted", key=keychain != "certificate has no key")
    installed.certificate_signing_fails, installed.listing_fails = True, keychain == "cannot be listed"
    monkeypatch.setattr(runtime_identity, "digest", lambda files: "f" * 64)  # another interpreter, so a new executable is due
    capsys.readouterr()
    assert runtime_identity.ensure_bundle(interpreter()) == executable and executable.is_file(), "the update goes on"
    assert (executable.stat().st_ino == stamp) is kept
    assert ("keeps the executable" in capsys.readouterr().err) is kept


@pytest.mark.usefixtures("macos")
def test_enable_that_cannot_sign_leaves_a_certificate_signed_app_as_it_is(installed, monkeypatch):
    executable = runtime_identity.ensure_bundle(interpreter())
    stamp = executable.stat().st_ino
    reads_as_certificate_signed(monkeypatch, runtime_identity.bundle_path())
    installed.trusted()
    installed.certificate_signing_fails = True
    assert node_permission.main(["certificate", "enable"]) == 1
    assert executable.stat().st_ino == stamp and not (runtime_identity.bundle_path().parent / ".building").exists()


@pytest.mark.usefixtures("macos")
def test_a_copy_that_does_not_start_never_takes_the_working_ones_place(installed, monkeypatch):
    executable = runtime_identity.ensure_bundle(interpreter())
    stamp = executable.stat().st_ino
    monkeypatch.setattr(runtime_identity, "digest", lambda files: "f" * 64)
    monkeypatch.setattr(runtime_identity, "bare_problem", lambda path: "plumbing-only: it does not start")
    with pytest.raises(runtime_identity.IdentityError, match="does not start"):
        runtime_identity.ensure_bundle(interpreter())
    assert executable.stat().st_ino == stamp


@pytest.mark.usefixtures("macos")
def test_a_changed_signature_says_how_to_let_poolhouse_read_its_key_again(installed, monkeypatch, capsys):
    runtime_identity.ensure_bundle(interpreter())
    assert keystore.UNLOCK_COMMAND not in capsys.readouterr().err, "there is no key yet"
    holds_a_key()
    runtime_identity.ensure_bundle(interpreter())
    assert keystore.UNLOCK_COMMAND not in capsys.readouterr().err, "nothing changed"
    monkeypatch.setattr(runtime_identity, "digest", lambda files: "f" * 64)
    runtime_identity.ensure_bundle(interpreter())
    said = capsys.readouterr().err
    assert "signature on this Mac changed" in said and keystore.UNLOCK_COMMAND in said and "always allow" in said


@pytest.mark.usefixtures("macos")
def test_enable_and_remove_say_how_to_let_poolhouse_read_its_key_again(installed, capsys):
    runtime_identity.ensure_bundle(interpreter())
    holds_a_key()
    for change in ("enable", "remove"):
        if change == "remove":  # another signature stands in for the certificate's, so removing it changes the signature
            subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", "plumbing.only", "--timestamp=none",
                            str(runtime_identity.bundle_path())], check=True, capture_output=True)
        capsys.readouterr()
        assert node_permission.main(["certificate", change]) == 0
        said = capsys.readouterr()
        assert keystore.UNLOCK_COMMAND in said.out and "always allow" in said.out, f"{change} says it in its own words"
        if change == "remove":
            assert "signature on this Mac changed" in said.err


def test_setup_says_how_to_let_poolhouse_read_its_key_again(installed):
    for opted_in in (False, True):
        if opted_in:
            installed.trusted()
        [finding] = setup.signature_findings()
        assert keystore.UNLOCK_COMMAND in finding.note and "always allow" in finding.note


def test_setup_offers_to_remove_a_certificate_that_cannot_be_used(installed):
    installed.trusted(trusted=False)
    [finding] = setup.signature_findings()
    assert finding.good and "cannot be used" in finding.said and "poolhouse local-network certificate remove" in finding.note
    assert installed.writes() == []


def test_the_key_files_are_gone_before_macos_asks(tools):
    seen = []
    tools.on["add-trusted-cert"] = lambda rest: seen.append(sorted(one.name for one in Path(rest[-1]).parent.iterdir()))
    node_signing.enable(lambda _text: None)
    assert seen == [["cert.pem"]]


@pytest.mark.usefixtures("macos")
@pytest.mark.parametrize("name", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_a_command_stopped_at_the_dialog_leaves_nothing(tools, name):
    number, heard, scratch = getattr(signal, name), [], []

    def quiet(*_):
        heard.append(number)  # a handler that stops nothing: a command that leaves it in place runs to its end

    def stop(rest):
        scratch.append(Path(rest[-1]).parent)
        os.kill(os.getpid(), number)
        time.sleep(0.05)

    tools.on["add-trusted-cert"] = stop
    before = signal.signal(number, quiet)
    try:
        with pytest.raises((SystemExit, KeyboardInterrupt)):
            node_signing.enable(lambda _text: None)
        assert signal.getsignal(number) is quiet, "the handler there was is put back"
    finally:
        signal.signal(number, before)
    assert tools.certificates == {} and tools.settings == set() and not scratch[0].exists() and heard == []


@pytest.mark.usefixtures("macos")
def test_a_second_ctrl_c_does_not_stop_the_cleanup(tools):
    during = []

    def again(_rest):
        during.append(signal.getsignal(signal.SIGINT))
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.05)

    tools.trust_step = KeyboardInterrupt()
    tools.on["delete-identity"] = again
    with pytest.raises(KeyboardInterrupt):
        node_signing.enable(lambda _text: None)
    assert tools.certificates == {} and during == [signal.SIG_IGN], "the tool that cleans up ignores it too"


@pytest.mark.parametrize("untrust_works", [True, False], ids=["removed", "named"])
def test_a_trust_setting_that_outlives_its_certificate_is_removed_or_named(tools, untrust_works):
    tools.trusted()
    tools.keeps_trust, tools.refuses_untrust = True, not untrust_works
    if untrust_works:
        node_signing.remove(lambda _text: None)
        assert tools.certificates == {} and tools.settings == set()
    else:
        with pytest.raises(node_signing.SigningError, match=f"trust setting {SHA}"):
            node_signing.remove(lambda _text: None)


def test_a_trust_setting_whose_certificate_file_is_nowhere_is_named_and_stops_nothing(tools):
    tools.settings.add(OTHER)
    said, ran = [], []
    node_signing.remove(said.append, lambda: ran.append(True))
    told = " ".join(said)
    assert ran == [True] and OTHER in told and "can be deleted in" in told


def test_a_setting_one_removal_leaves_is_deleted_by_the_next(tools):
    tools.trusted()
    tools.keeps_trust = tools.refuses_untrust = True
    with pytest.raises(node_signing.SigningError, match=f"trust setting {SHA}"):
        node_signing.remove(lambda _text: None)
    tools.refuses_untrust = False
    node_signing.remove(lambda _text: None)
    assert tools.settings == set() and not list((home.home() / "certificates").glob("*.pem")), "the copy goes with the setting"


@pytest.mark.usefixtures("macos")
def test_the_apps_are_signed_locally_once_the_identity_is_gone_though_a_setting_stays(tmp_path, installed):
    node_app.build(node_program(tmp_path))
    installed.trusted()
    installed.keeps_trust = installed.refuses_untrust = True
    installed.calls.clear()
    assert node_permission.main(["certificate", "remove"]) == 1
    assert installed.certificates == {} and installed.signatures() == ["-"]


def test_trust_settings_that_cannot_be_read_stop_no_deletion(tools):
    tools.trusted()
    tools.export_fails = True
    ran = []
    with pytest.raises(node_signing.SigningError, match="could not be read"):
        node_signing.remove(lambda _text: None, lambda: ran.append(True))
    assert tools.certificates == {} and ran == [True]


def test_an_unfinished_setup_removes_what_it_imported_though_trust_cannot_be_read(tools):
    tools.trust_step, tools.export_fails = 1, True
    with pytest.raises(node_signing.SigningError):
        node_signing.enable(lambda _text: None)
    assert tools.certificates == {}


def test_the_key_advice_names_all_that_stays_locked_and_what_is_not_known():
    for words in ("device key", "memory vault", "stored credentials", "signing key", "screen", "has not been verified"):
        assert words in node_signing.KEY_ADVICE


def made_runtime(tmp_path: Path, name: str) -> runtime.Runtime:
    prefix = tmp_path / "runtimes" / name
    subprocess.run([runtime_identity.unbound(Path(sys.executable)), "-m", "venv", "--without-pip", str(prefix)], check=True,
                   capture_output=True)
    return runtime.Runtime(prefix, "a" * 40, "0", runtime.identity())


@pytest.mark.usefixtures("macos")
def test_a_certificate_signed_executable_that_no_longer_starts_is_replaced(installed, monkeypatch):
    executable = runtime_identity.ensure_bundle(interpreter())
    stamp, starts = executable.stat().st_ino, runtime_identity.bare_problem
    reads_as_certificate_signed(monkeypatch, runtime_identity.bundle_path())
    installed.trusted()
    installed.certificate_signing_fails = True
    monkeypatch.setattr(runtime_identity, "bare_problem",
                        lambda path: "plumbing-only: its Python is gone" if path == executable else starts(path))
    monkeypatch.setattr(runtime_identity, "digest", lambda files: "f" * 64)
    assert runtime_identity.ensure_bundle(interpreter()) == executable and executable.stat().st_ino != stamp


@pytest.mark.usefixtures("macos")
def test_a_kept_executable_that_cannot_start_the_new_runtime_is_replaced(tmp_path, installed, monkeypatch):
    old = runtime_identity.bind(made_runtime(tmp_path, "one"))
    stamp, probe = old.stat().st_ino, runtime_identity._probe
    reads_as_certificate_signed(monkeypatch, runtime_identity.bundle_path())
    installed.trusted()
    installed.certificate_signing_fails = True
    monkeypatch.setattr(runtime_identity, "digest", lambda files: "f" * 64)

    def probed(prefix, executable):
        if executable.stat().st_ino == stamp:
            raise runtime_identity.IdentityError("plumbing-only: the old executable cannot start this runtime")
        probe(prefix, executable)

    monkeypatch.setattr(runtime_identity, "_probe", probed)
    new = made_runtime(tmp_path, "two")
    assert runtime_identity.bind(new) == old and runtime_identity.bound(new.prefix) and old.stat().st_ino != stamp


@pytest.mark.usefixtures("macos")
def test_a_kept_executable_is_in_the_outcome_the_deploy_record_and_setup(tmp_path, installed, monkeypatch):
    chosen = made_runtime(tmp_path, "one")
    runtime_identity.bind(chosen)
    reads_as_certificate_signed(monkeypatch, runtime_identity.bundle_path())
    installed.trusted()
    installed.certificate_signing_fails = True
    monkeypatch.setattr(runtime_identity, "digest", lambda files: "f" * 64)
    told = runtime_deploy._identity(chosen, runtime_deploy.Plan(tmp_path, chosen.commit, None))
    assert any("made from" in one and "certificate enable" in one for one in told)
    assert any("made from" in one for one in runtime_store.read_state()["identity_notices"])
    monkeypatch.setattr(setup.runtime, "selection_prefix", lambda: chosen.prefix)
    kept = [finding for finding in setup.signature_findings() if finding.name == "macOS executable"]
    assert kept and not kept[0].good and kept[0].fix == ["poolhouse", "local-network", "certificate", "enable"]


@pytest.mark.usefixtures("macos")
def test_a_changed_signature_is_in_the_updates_outcome_and_record(tmp_path, installed, monkeypatch):
    chosen = made_runtime(tmp_path, "one")
    runtime_identity.bind(chosen)
    holds_a_key()
    monkeypatch.setattr(runtime_identity, "digest", lambda files: "f" * 64)
    told = runtime_deploy._identity(chosen, runtime_deploy.Plan(tmp_path, chosen.commit, None))
    assert node_signing.KEY_NOTE in told and node_signing.KEY_NOTE in runtime_store.read_state()["identity_notices"]


def test_a_declined_step_that_still_made_a_trust_setting_leaves_none(tools):
    tools.on["add-trusted-cert"] = lambda _rest: tools.settings.add(SHA)
    tools.trust_step, tools.keeps_trust = 1, True
    with pytest.raises(node_signing.SigningError):
        node_signing.enable(lambda _text: None)
    assert tools.certificates == {} and tools.settings == set()


def test_one_certificate_change_runs_at_a_time(tools):
    refused = []

    def meanwhile(_rest):
        with pytest.raises(node_signing.SigningError, match="another certificate change"):
            node_signing.remove(lambda _text: None)
        refused.append(True)

    tools.on["add-trusted-cert"] = meanwhile
    node_signing.enable(lambda _text: None)
    assert refused == [True] and tools.certificates[SHA]["trusted"]


@pytest.mark.usefixtures("macos")
def test_the_node_app_is_built_and_signed_again_under_one_lock(tmp_path, tools):
    held = []

    def probe(_argv):
        try:
            with only_one(app_bundle.apps_directory() / "build.lock", wait=False):
                held.append(False)
        except Busy:
            held.append(True)

    tools.on["sign"] = probe
    node_app.build(node_program(tmp_path))
    runtime_identity.resign(node_app.bundle_path(), node_app.BUNDLE_ID, node_app.requirement)
    assert held == [True, True]


@pytest.mark.usefixtures("macos")
@pytest.mark.parametrize("named", ['identifier "app.poolhouse.node"',
                                   f'identifier "app.poolhouse.node" or certificate leaf = H"{"0" * 40}"'],
                         ids=["identifier-only", "identifier-or-certificate"])
def test_a_node_app_signed_so_that_any_program_satisfies_it_is_built_again(tmp_path, tools, named):
    binary = node_program(tmp_path)
    sha = node_binary.sha256(binary)
    executable = node_app.bundled(binary, sha)
    stamp = executable.stat().st_ino
    assert node_app.bundled(binary, sha).stat().st_ino == stamp, "an app signed locally is kept"
    subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", node_app.BUNDLE_ID, "--timestamp=none",
                    f"-r=designated => {named}", str(node_app.bundle_path())], check=True, capture_output=True)
    assert node_app.bundled(binary, sha) == executable
    assert runtime_identity.requirement_of(node_app.bundle_path()).startswith(LOCAL)
