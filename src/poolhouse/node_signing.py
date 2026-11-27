"""How the macOS apps Poolhouse makes are signed, and the one certificate a user may add for them.

An app is signed locally (ad hoc) by default: codesign's own requirement names the code, so macOS may ask for a permission again
after an update changes it. A user may opt in (`enable`, or the app's question, whose answer `answer` keeps) to a self-signed
code-signing certificate and key in their login keychain, trusted for code signing in the user domain only: the apps' requirement
then names the certificate, so a permission should stay across updates. `remove` deletes all of it.

Opted in is what the keychain says: a signing identity of that name that macOS lists as usable. It changes only when the user
asks. Poolhouse's encryption key is another item of that keychain, read only by the code that made it under either
signature, so a new signature gets the key handed to it (`poolhouse.keystore_handoff`).
"""

from __future__ import annotations

import base64
import hashlib
import plistlib
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from poolhouse import caller, real_machine
from poolhouse.files import read_json, write_json
from poolhouse.home import account_roots, home
from poolhouse.lock import Busy, only_one
from poolhouse.log import warn

CERT_NAME = "Poolhouse Local Signing"
CHOICE = "signing-choice.json"
ANSWERS = ("yes", "no")
OPENSSL = "/usr/bin/openssl"  # the system LibreSSL: its .p12 files are the kind `security import` reads
DAYS = 3650
TOOL_TIMEOUT = 120.0
NOT_FOUND = 44  # `security`'s exit status for an item that is not there
NO_TRUST = "No Trust Settings were found"  # what `security trust-settings-export` says, failing, when there is none
CDHASH = r'cdhash H"[0-9a-f]{40}"'
STOPPING = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))
ENABLE = "poolhouse local-network certificate enable"
REMOVE = "poolhouse local-network certificate remove"
KEY_HANDED = ("Poolhouse hands the encryption key it keeps in your login keychain to its app as newly signed, with no prompt, before "
              "it uses that app.")
WHAT_ENABLE_ADDS = f"""This adds a code-signing certificate to your login keychain:
  - a self-signed certificate named "{CERT_NAME}" and its private key, which /usr/bin/codesign may use
  - a setting that it is trusted for code signing, for your user account only
macOS will ask for your password to add that setting.
Poolhouse then signs its apps with the certificate, so a permission you give Poolhouse (such as Local Network) should stay after an update.
Any program running as you could ask codesign to sign with this key, and so sign itself as Poolhouse.
To undo it: {REMOVE}
{KEY_HANDED}"""


class SigningError(OSError):
    """The keychain could not be changed as asked, or a bundle could not be signed."""


def keychain() -> Path:
    """The account's login keychain (the account's own home, whatever HOME says)."""
    return account_roots()[0].parent / "Library" / "Keychains" / "login.keychain-db"


def certificate_requirement(identifier: str, sha1: str) -> str:
    """The designated requirement of a certificate signature: this id, signed by this certificate. It names no cdhash, so rebuilt
    code still satisfies it."""
    return f'designated => identifier "{identifier}" and certificate leaf = H"{sha1.lower()}"'


def signer_of(required: str, identifier: str) -> str:
    """The certificate ``required`` names when it is exactly `certificate_requirement` of ``identifier``, else ''."""
    found = re.fullmatch(rf'designated => identifier "{re.escape(identifier)}" and certificate leaf = H"([0-9a-f]{{40}})"', required)
    return found.group(1) if found else ""


def local_requirement(required: str) -> bool:
    """Whether ``required`` is the one codesign gives a local signature by itself (the leading "#" says nobody wrote it): hashes
    of the code and nothing else."""
    return re.fullmatch(rf"# designated => {CDHASH}(?: or {CDHASH})*", required) is not None


def parse_identities(output: str) -> list[str]:
    """The SHA-1 fingerprints (lower-case hex) of this certificate's identities in `security find-identity` output."""
    found = re.findall(rf'^\s*\d+\)\s+([0-9A-Fa-f]{{40}})\s+"{re.escape(CERT_NAME)}"', output, re.MULTILINE)
    return list(dict.fromkeys(one.lower() for one in found))


def parse_certificates(output: str) -> list[str]:
    """The SHA-1 fingerprints of the certificates labelled exactly `CERT_NAME` in `security find-certificate -a -Z` output."""
    found, current = [], ""
    for line in output.splitlines():
        if hashed := re.match(r"SHA-1 hash:\s*([0-9A-Fa-f]{40})", line):
            current = hashed.group(1).lower()
        elif line.strip() == f'"labl"<blob>="{CERT_NAME}"' and current:
            found.append(current)
    return list(dict.fromkeys(found))


def parse_trust(data: bytes) -> list[str]:
    """The fingerprints a `security trust-settings-export` file holds settings under for a certificate issued as exactly `CERT_NAME`."""
    named = bytes([len(CERT_NAME)]) + CERT_NAME.encode()  # a DER string: its length, then its characters
    rows = plistlib.loads(data).get("trustList", {})
    return [sha1.lower() for sha1, row in rows.items() if named in bytes(row.get("issuerName", b""))]


def fingerprint_of(pem: str) -> str:
    """The SHA-1 fingerprint of a PEM certificate, as the keychain names it."""
    body = re.search(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", pem, re.DOTALL)
    if body is None:
        raise SigningError("that is no certificate")
    return hashlib.sha1(base64.b64decode("".join(body.group(1).split())), usedforsecurity=False).hexdigest()


def certificate_request(common_name: str = CERT_NAME) -> list[str]:
    """The openssl arguments that make the self-signed code-signing certificate (key and certificate files are added by the caller)."""
    return ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", str(DAYS), "-subj", f"/CN={common_name}",
            "-addext", "basicConstraints=critical,CA:false", "-addext", "keyUsage=critical,digitalSignature",
            "-addext", "extendedKeyUsage=critical,codeSigning"]


def sign_command(bundle: Path, identifier: str, requirement: str = "", identity: str = "-") -> list[str]:
    """The codesign command line: ``identity`` is a certificate's fingerprint with the ``requirement`` that names it; without them
    the signature is local ("-") and takes codesign's own requirement."""
    return ["codesign", "--force", "--sign", identity, "--identifier", identifier, "--timestamp=none",
            *([f"-r={requirement}"] if requirement else []), str(bundle)]


def _run(argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=TOOL_TIMEOUT, check=False)
    if done.returncode:
        raise SigningError(f"{Path(argv[0]).name} {argv[1] if len(argv) > 1 else ''} failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return done.stdout + done.stderr


def identities(*, usable: bool = True) -> list[str]:
    """The fingerprints of this certificate's signing identities (certificate and key) in the login keychain: with ``usable`` only
    those macOS lists as valid for code signing, which takes the trust setting too. Changes nothing."""
    real_machine.require("reading the login keychain")
    argv = ["security", "find-identity", *(["-v"] if usable else []), "-p", "codesigning", str(keychain())]
    return parse_identities(_run(argv))


def _find(form: str) -> str:
    """What `security find-certificate` says of every certificate of this name: ``-Z`` for fingerprints, ``-p`` for the PEM text."""
    real_machine.require("reading the login keychain")
    done = subprocess.run(["security", "find-certificate", "-a", "-c", CERT_NAME, form, str(keychain())], capture_output=True, text=True,
                          timeout=TOOL_TIMEOUT, check=False)
    if done.returncode not in (0, NOT_FOUND):
        raise SigningError(f"security find-certificate failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return done.stdout


def certificates() -> list[str]:
    """The fingerprints of every certificate of this name in the login keychain, usable or not. Changes nothing."""
    return parse_certificates(_find("-Z"))


def trusted_certificates() -> list[str]:
    """The fingerprints of this certificate that the user's trust settings name, whether or not the certificate is still there.
    Changes nothing."""
    real_machine.require("reading the trust settings")
    with tempfile.TemporaryDirectory(prefix="poolhouse-trust") as scratch:
        exported = Path(scratch) / "trust.plist"
        done = subprocess.run(["security", "trust-settings-export", str(exported)], capture_output=True, text=True,
                              timeout=TOOL_TIMEOUT, check=False)
        if done.returncode:
            if NO_TRUST in done.stderr:
                return []
            raise SigningError(f"security trust-settings-export failed: {(done.stderr or done.stdout).strip()[-400:]}")
        try:
            return parse_trust(exported.read_bytes())
        except (ValueError, TypeError, AttributeError) as exc:
            raise SigningError(f"the exported trust settings cannot be read: {exc}") from exc


def usable_certificate() -> str:
    """The fingerprint of the certificate the user opted in with, or '' when there is none that can be used (also where the
    keychain may not or cannot be read), and then an app is signed locally."""
    try:
        return next(iter(identities()), "")
    except (OSError, subprocess.SubprocessError):
        return ""


def may_have_identity() -> bool:
    """False only when the keychain answers that it has no identity of this certificate that is usable for code signing."""
    try:
        return bool(identities())
    except (OSError, subprocess.SubprocessError):
        return True


def holds_leftovers() -> bool:
    """Whether the keychain or its trust settings hold anything of this certificate, usable or not; False where that cannot be read."""
    try:
        if certificates():
            return True
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(_settings([]))


def _settings(why: list[str]) -> list[str] | None:
    """`trusted_certificates`, or None with the reason added to ``why`` when the trust settings cannot be read."""
    try:
        return trusted_certificates()
    except (OSError, subprocess.SubprocessError) as exc:
        why.append(str(exc))
        return None


def _copies() -> Path:
    """Where the public certificate is kept until its trust setting is known to be gone: deleting a setting takes its certificate's file."""
    return home() / "certificates"


def _keep_copies() -> None:
    """Keep the public certificate of every certificate of this name now in the keychain."""
    folder = _copies()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    for pem in re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", _find("-p"), re.DOTALL):
        (folder / f"{fingerprint_of(pem)}.pem").write_text(pem + "\n", encoding="utf-8")


def _kept_copies() -> dict[str, Path]:
    return {path.stem: path for path in _copies().glob("*.pem")}


def _forget_copies(present: list[str], settings: list[str] | None) -> None:
    """Drop the kept certificate of each fingerprint that has neither a certificate nor a trust setting any more."""
    if settings is not None:
        for sha1, path in _kept_copies().items():
            if sha1 not in present and sha1 not in settings:
                path.unlink(missing_ok=True)


def sign(bundle: Path, identifier: str, requirement_for: Callable[[str], str]) -> str:
    """Sign ``bundle``: with the user's certificate when they opted in ("certificate"), else, and whenever that fails, locally
    ("ad-hoc"). ``requirement_for`` makes the designated requirement from the certificate's fingerprint."""
    fingerprint = usable_certificate()
    if fingerprint:
        try:
            _run(sign_command(bundle, identifier, requirement_for(fingerprint), fingerprint))
            _run(["codesign", "--verify", "--strict", str(bundle)])
            return "certificate"
        except (OSError, subprocess.SubprocessError) as exc:
            warn(f"signing {bundle} with {CERT_NAME!r} failed ({exc}); signing locally instead, so macOS may ask for permission again")
    _run(sign_command(bundle, identifier))
    return "ad-hoc"


def _delete(sha1: str, *, trust: bool) -> str:
    """Delete one certificate with its private key, and with ``trust`` its user trust setting; returns what went wrong, or ''.
    A certificate macOS does not list as usable is then tried without its setting, which it may never have had."""
    try:
        verb = "delete-identity" if sha1 in identities(usable=False) else "delete-certificate"  # the second for one with no key
        try:
            _run(["security", verb, "-Z", sha1, *(["-t"] if trust else []), str(keychain())])
        except SigningError:
            if not trust or sha1 in identities():
                raise
            _run(["security", verb, "-Z", sha1, str(keychain())])
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return ""


def _untrust(certificate: Path) -> str:
    """Delete the user trust setting of the certificate in the file ``certificate``; returns what went wrong, or ''."""
    try:
        _run(["security", "remove-trusted-cert", str(certificate)])
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return ""


def _left(ours: set[str], why: list[str]) -> list[str]:
    """What is still there that a further run could delete, each as "<what> <fingerprint>": certificates and identities of this
    name, and the trust settings of ``ours``, the certificates whose files are kept."""
    held = [("certificate", certificates()), ("identity", identities(usable=False))]
    settings = _settings(why)
    held.append(("trust setting", [sha1 for sha1 in settings or [] if sha1 in ours]))
    left = [f"{what} {sha1}" for what, found in held for sha1 in found]
    return left if settings is not None else [*left, "trust settings that could not be read"]


@contextmanager
def _changing() -> Iterator[None]:
    """Held while the certificate is added or removed, so that one change runs at a time."""
    try:
        with only_one(home() / "certificate.lock", wait=False, note="certificate"):
            yield
    except Busy as exc:
        raise SigningError(f"another certificate change is running: {exc}") from exc


@contextmanager
def _signals(handler: Any) -> Iterator[None]:
    """``handler`` for the signals that stop a command, inside the block; only the main thread can set one."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    before = {number: signal.signal(number, handler) for number in STOPPING}
    try:
        yield
    finally:
        for number, old in before.items():
            signal.signal(number, signal.SIG_DFL if old is None else old)


def _stopping() -> Callable[[int, object], None]:
    """A handler that raises for the first signal that would stop the command, so what is under way is cleaned up, and ignores the rest."""
    came: list[int] = []

    def stop(number: int, _frame: object) -> None:
        if came:
            return
        came.append(number)
        if number == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + number)

    return stop


def answer() -> str:
    """What the user last chose, "yes" for the certificate or "no", or "" while they have not said."""
    found = read_json(home() / CHOICE, {})
    said = found.get("certificate") if isinstance(found, dict) else None
    return said if said in ANSWERS else ""


def remember(said: str) -> None:
    """Keep the user's choice, so the app does not ask again."""
    if said not in ANSWERS:
        raise ValueError(f"the choice is one of {', '.join(ANSWERS)}")
    write_json(home() / CHOICE, {"schema_version": 1, "certificate": said})


def _asked(action: str, via: str) -> None:
    """Record who is changing the keychain and through what; nothing inside a confinement may."""
    if caller.confined():
        with suppress(OSError):  # a confinement may hold no write to the state root: the refusal still stands
            caller.record("refused", action=action, via=via)
        raise caller.AgentRefused(f"{action} is refused: nothing inside a confinement writes your keychain")
    caller.record("ran", action=action, via=via)


def enable(say: Callable[[str], None], then: Callable[[], None] = lambda: None, *, via: str = "command") -> None:
    """The user's opt-in, run when the user asks for it: by them, by an agent they told, or by their answer in the app. Say what
    it adds, make the certificate and key, put them in the login keychain and trust them for code signing, then run ``then``. A
    step that fails, is declined or is stopped removes what was imported. The audit log names who ran it."""
    _asked(ENABLE, via)
    real_machine.require("adding a signing certificate to the login keychain")
    with _changing():
        remember("yes")
        if identities():
            say(f"the {CERT_NAME!r} certificate is already in your login keychain and trusted for code signing; nothing was added")
        else:
            _add(say)
        then()


def _add(say: Callable[[str], None]) -> None:
    say(WHAT_ENABLE_ADDS)
    password = secrets.token_hex(16)  # protects the throwaway .p12 for the seconds it exists
    with _signals(_stopping()), tempfile.TemporaryDirectory(prefix="poolhouse-cert") as scratch:  # the only place the key is a file
        key, cert, package = (Path(scratch) / name for name in ("key.pem", "cert.pem", "id.p12"))
        _run([OPENSSL, *certificate_request(), "-keyout", str(key), "-out", str(cert)])
        sha1 = fingerprint_of(cert.read_text(encoding="utf-8"))
        _copies().mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(cert, _copies() / f"{sha1}.pem")  # before the keychain holds anything of it
        _run([OPENSSL, "pkcs12", "-export", "-inkey", str(key), "-in", str(cert), "-out", str(package), "-passout", f"pass:{password}"])
        trusted, added = False, False
        try:
            _run(["security", "import", str(package), "-k", str(keychain()), "-P", password, "-T", "/usr/bin/codesign"])
            for spent in (key, package):  # only the certificate is a file while macOS asks
                spent.unlink(missing_ok=True)
            _run(["security", "add-trusted-cert", "-p", "codeSign", "-k", str(keychain()), str(cert)])
            trusted = True
            if sha1 not in identities():
                raise SigningError("macOS does not list the certificate as usable for code signing")
            added = True
        finally:
            if not added:
                with _signals(signal.SIG_IGN):  # the tools that clean up inherit this
                    _discard(sha1, cert, trusted=trusted)
    say(f"added {CERT_NAME!r} ({sha1}) and trusted it for code signing")


def _discard(sha1: str, certificate: Path, *, trusted: bool) -> None:
    """Remove what a setup that did not finish left in the keychain and its trust settings, and say what is left when that fails."""
    why: list[str] = []
    try:
        if sha1 in certificates():
            settings = _settings(why)
            why.append(_delete(sha1, trust=trusted or settings is None or sha1 in settings))
        settings = _settings(why)
        if sha1 not in certificates() and (settings is None or sha1 in settings):
            why.append(_untrust(certificate))
        left = [one for one in _left({sha1}, why) if sha1 in one or "could not be read" in one]
        _forget_copies(certificates(), _settings(why))
    except (OSError, subprocess.SubprocessError) as exc:
        why.append(str(exc))
        left = [f"certificate {sha1}"]
    with suppress(OSError):  # the terminal may be gone
        if left:
            warn(f"the certificate was not set up and may not be gone ({', '.join(left)}) {' '.join(filter(None, why))}; run `{REMOVE}`")
        else:
            warn("the certificate was not set up, and nothing of it is left")


def remove(say: Callable[[str], None], then: Callable[[], None] = lambda: None, *, via: str = "command") -> None:
    """Run when the user asks, as `enable` is: delete every certificate of this name with its private key and trust setting; with
    none there the keychain is not changed. ``then`` runs once no identity of the name is left, whatever else is; raises for what
    a further run could still delete. The audit log names who ran it."""
    _asked(REMOVE, via)
    real_machine.require("removing the signing certificate from the login keychain")
    with _changing():
        remember("no")
        left, why = _remove(say)
        if not identities(usable=False):
            then()
    if left:
        raise SigningError(f"still in your login keychain or its trust settings: {', '.join(left)}. {' '.join(filter(None, why))}")


def _remove(say: Callable[[str], None]) -> tuple[list[str], list[str]]:
    """Delete what there is; returns what a further run could still delete, and what the tools said."""
    why: list[str] = []
    held = certificates()
    _keep_copies()  # before anything is deleted, so a later run can delete a setting this one could not
    settings = _settings(why)
    if not held and settings == []:
        _forget_copies(held, settings)
        say(f"there is no {CERT_NAME!r} certificate or trust setting in your login keychain; nothing to remove")
        return [], []
    say(f"removing the {CERT_NAME!r} certificate, its private key and its trust setting; macOS may ask for your password. "
        f"Poolhouse is then signed locally.\n{KEY_HANDED}")
    why += [_delete(sha1, trust=True) for sha1 in held]
    present, settings, kept = certificates(), _settings(why), _kept_copies()
    why += [_untrust(path) for sha1, path in kept.items() if sha1 not in present and (settings is None or sha1 in settings)]
    left = _left(set(kept), why)
    settings = _settings(why)
    _forget_copies(certificates(), settings)
    for sha1 in [one for one in settings or [] if one not in kept]:
        say(f"a trust setting for {sha1} remains and no file of its certificate is left, so `security` cannot delete it; it can be "
            "deleted in Keychain Access")
    if not left and held:
        say(f"removed {', '.join(held)}")
    return left, why
