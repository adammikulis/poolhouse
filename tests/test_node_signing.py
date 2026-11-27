"""Poolhouse's macOS signature is local (ad hoc) unless the user opted into a certificate, and only that user's command touches the keychain.

`security` and `openssl` never run here: `Tools` records each command and answers it with canned, plumbing-only text standing for a keychain,
so no keychain is read or written. `codesign` runs for real for local signatures and verification, on bundles under the test's own state root;
signing with a certificate is canned too, because there is none.
"""

from __future__ import annotations

import base64
import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import (
    app_bundle,
    caller,
    node_app,
    node_permission,
    node_signing,
    real_machine,
    runtime_identity,
    setup,
)

NAME = "Poolhouse Local Signing"
CANNED_DER = b"plumbing-only canned certificate, not a real one"
CANNED_PEM = f"-----BEGIN CERTIFICATE-----\n{base64.b64encode(CANNED_DER).decode()}\n-----END CERTIFICATE-----\n"
SHA = hashlib.sha1(CANNED_DER, usedforsecurity=False).hexdigest()
OTHER = "b" * 40
RUN = subprocess.run
LOOKS = ("find-identity", "find-certificate")
LOCAL = '# designated => cdhash H"'

@pytest.fixture
def macos():
    """The test runs macOS's own codesign."""
    if sys.platform != "darwin":
        pytest.skip("codesign is macOS's")


class Tools:
    """A plumbing-only keychain: every command is recorded and `security` and `openssl` are answered from ``certificates``."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.certificates: dict[str, dict[str, bool]] = {}
        self.certificate_signing_fails = False
        self.trust_step: BaseException | int = 0
        self.refuses_deletion = False

    def trusted(self, sha: str = SHA, *, key: bool = True, trusted: bool = True) -> None:
        self.certificates[sha] = {"key": key, "trusted": trusted}

    def writes(self) -> list[list[str]]:
        return [argv for argv in self.calls
                if Path(argv[0]).name == "openssl" or (argv[0] == "security" and argv[1] not in LOOKS)]

    def signatures(self) -> list[str]:
        return [argv[argv.index("--sign") + 1] for argv in self.calls if argv[0] == "codesign" and "--sign" in argv]

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        name = Path(argv[0]).name
        if name == "codesign":
            if "--sign" in argv and argv[argv.index("--sign") + 1] != "-":
                failed = self.certificate_signing_fails
                return subprocess.CompletedProcess(argv, int(failed), "", "plumbing-only canned: errSecInternalComponent" if failed else "")
            return RUN(argv, **kwargs)
        if name == "openssl":
            for option, text in (("-keyout", "plumbing-only canned key\n"), ("-out", CANNED_PEM)):
                if option in argv:
                    Path(argv[argv.index(option) + 1]).write_text(text)
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[0] == "security", f"plumbing-only keychain: unexpected command {argv}"
        code, out = self._security(argv[1], argv[2:])
        return subprocess.CompletedProcess(argv, code, out, "" if code == 0 else "plumbing-only canned: SecKeychain error")

    def _security(self, verb: str, rest: list[str]) -> tuple[int, str]:
        if verb == "find-identity":
            held = [sha for sha, row in self.certificates.items() if row["key"] and (row["trusted"] or "-v" not in rest)]
            lines = [f'  {number}) {sha.upper()} "{NAME}"' for number, sha in enumerate(held, 1)]
            return 0, "\n".join([*lines, f"     {len(held)} valid identities found", ""])
        if verb == "find-certificate":
            rows = [f'SHA-256 hash: {"0" * 64}\nSHA-1 hash: {sha.upper()}\nkeychain: "plumbing-only"\nattributes:\n    "labl"<blob>="{NAME}"\n'
                    for sha in self.certificates]
            return (0, "".join(rows)) if rows else (44, "")
        if verb == "import":
            self.certificates[SHA] = {"key": True, "trusted": False}
            return 0, ""
        if verb == "add-trusted-cert":
            if isinstance(self.trust_step, BaseException):
                raise self.trust_step
            if self.trust_step == 0:
                self.certificates[SHA]["trusted"] = True
            return self.trust_step, ""
        assert verb in ("delete-identity", "delete-certificate"), f"plumbing-only keychain: unexpected security {verb}"
        sha = rest[rest.index("-Z") + 1].lower()
        row = self.certificates.get(sha)
        if self.refuses_deletion or row is None or (verb == "delete-identity" and not row["key"]):
            return 1, ""
        del self.certificates[sha]
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
