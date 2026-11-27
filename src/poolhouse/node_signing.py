"""How the macOS apps Poolhouse makes are signed, and the one certificate a user may add for them.

An app is signed locally (ad hoc) by default: no certificate, nothing in the keychain, and codesign's own designated requirement,
which names the code itself, so macOS may ask for a permission again after an update changes the code. `enable` is the user's
opt-in (`poolhouse local-network certificate enable`): there is no Apple developer account, so it makes a self-signed
code-signing certificate and its key in the user's login keychain, trusted for code signing in the user domain only. The apps'
requirement then names the certificate instead of the code, so a permission should stay across updates. `remove` deletes all of it.

Whether the user opted in is what the keychain says: a signing identity of that name that macOS lists as usable, or none. Nothing
but `enable` creates, imports or trusts a certificate, and a certificate that cannot sign leaves the app signed locally.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from poolhouse import caller, real_machine
from poolhouse.home import account_roots
from poolhouse.log import warn

CERT_NAME = "Poolhouse Local Signing"
OPENSSL = "/usr/bin/openssl"  # the system LibreSSL: its .p12 files are the kind `security import` reads
DAYS = 3650
TOOL_TIMEOUT = 120.0
NOT_FOUND = 44  # `security`'s exit status for an item that is not there
ENABLE = "poolhouse local-network certificate enable"
REMOVE = "poolhouse local-network certificate remove"
WHY_NOT_AN_AGENT = "an agent must not write your keychain"
WHAT_ENABLE_ADDS = f"""This adds a code-signing certificate to your login keychain:
  - a self-signed certificate named "{CERT_NAME}" and its private key, which /usr/bin/codesign may use
  - a setting that it is trusted for code signing, for your user account only
macOS will ask for your password to add that setting.
Poolhouse then signs its apps with the certificate, so a permission you give Poolhouse (such as Local Network) should stay after an update.
Any program running as you could ask codesign to sign with this key, and so sign itself as Poolhouse.
To undo it: {REMOVE}"""


class SigningError(OSError):
    """The keychain could not be changed as asked, or a bundle could not be signed."""


def keychain() -> Path:
    """The account's login keychain (the account's own home, whatever HOME says)."""
    return account_roots()[0].parent / "Library" / "Keychains" / "login.keychain-db"


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


def fingerprint_of(pem: str) -> str:
    """The SHA-1 fingerprint of a PEM certificate, as the keychain names it."""
    body = re.search(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", pem, re.DOTALL)
    if body is None:
        raise SigningError("openssl made no certificate")
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


def certificates() -> list[str]:
    """The fingerprints of every certificate of this name in the login keychain, usable or not. Changes nothing."""
    real_machine.require("reading the login keychain")
    done = subprocess.run(["security", "find-certificate", "-a", "-c", CERT_NAME, "-Z", str(keychain())], capture_output=True, text=True,
                          timeout=TOOL_TIMEOUT, check=False)
    if done.returncode not in (0, NOT_FOUND):
        raise SigningError(f"security find-certificate failed: {(done.stderr or done.stdout).strip()[-400:]}")
    return parse_certificates(done.stdout)


def usable_certificate() -> str:
    """The fingerprint of the certificate the user opted in with, or '' when there is none that can be used (also where the
    keychain may not or cannot be read), and then an app is signed locally."""
    try:
        return next(iter(identities()), "")
    except (OSError, subprocess.SubprocessError):
        return ""


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
    """Delete one certificate with its private key, and with ``trust`` its user trust setting; returns what went wrong, or ''."""
    try:
        verb = "delete-identity" if sha1 in identities(usable=False) else "delete-certificate"  # the second for one with no key
        _run(["security", verb, "-Z", sha1, *(["-t"] if trust else []), str(keychain())])
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return ""


def enable(say: Callable[[str], None]) -> None:
    """The user's opt-in: say what it adds, make the certificate and key, put them in the login keychain and trust them for code
    signing. A step that fails, is declined or is interrupted removes what was imported. Refused at once to an agent."""
    caller.refuse_agent(ENABLE, WHY_NOT_AN_AGENT)
    real_machine.require("adding a signing certificate to the login keychain")
    if identities():
        say(f"the {CERT_NAME!r} certificate is already in your login keychain and trusted for code signing; nothing was added")
        return
    say(WHAT_ENABLE_ADDS)
    password = secrets.token_hex(16)  # protects the throwaway .p12 for the seconds it exists
    with tempfile.TemporaryDirectory(prefix="poolhouse-cert") as scratch:  # the only place the key is a file
        key, cert, package = (Path(scratch) / name for name in ("key.pem", "cert.pem", "id.p12"))
        _run([OPENSSL, *certificate_request(), "-keyout", str(key), "-out", str(cert)])
        sha1 = fingerprint_of(cert.read_text(encoding="utf-8"))
        _run([OPENSSL, "pkcs12", "-export", "-inkey", str(key), "-in", str(cert), "-out", str(package), "-passout", f"pass:{password}"])
        trusted, added = False, False
        try:
            _run(["security", "import", str(package), "-k", str(keychain()), "-P", password, "-T", "/usr/bin/codesign"])
            _run(["security", "add-trusted-cert", "-p", "codeSign", "-k", str(keychain()), str(cert)])
            trusted = True
            if sha1 not in identities():
                raise SigningError("macOS does not list the certificate as usable for code signing")
            added = True
        finally:
            if not added:
                _discard(sha1, trusted=trusted)
    say(f"added {CERT_NAME!r} ({sha1}) and trusted it for code signing")


def _discard(sha1: str, *, trusted: bool) -> None:
    """Remove what a setup that did not finish imported, and say what is left when that fails too."""
    why = ""
    try:
        if sha1 not in certificates():
            return
        why = _delete(sha1, trust=trusted or sha1 in identities())
        left = sha1 in certificates()
    except (OSError, subprocess.SubprocessError) as exc:
        why, left = str(exc), True
    if left:
        warn(f"the certificate {sha1} may still be in your login keychain ({why}); remove it with `{REMOVE}`")
    else:
        warn("the certificate was not set up, and what had been imported was removed")


def remove(say: Callable[[str], None]) -> None:
    """Delete every certificate of this name with its private key and trust setting; with none there it changes nothing. Refused
    at once to an agent."""
    caller.refuse_agent(REMOVE, WHY_NOT_AN_AGENT)
    real_machine.require("removing the signing certificate from the login keychain")
    found = certificates()
    if not found:
        say(f"there is no {CERT_NAME!r} certificate in your login keychain; nothing to remove")
        return
    say(f"removing the {CERT_NAME!r} certificate, its private key and its trust setting; macOS may ask for your password")
    problems = []
    for sha1 in found:
        usable = sha1 in identities()
        why = _delete(sha1, trust=True)
        if why and not usable:
            why = _delete(sha1, trust=False)  # one that never was trusted has no setting to delete
        problems.append(why)
    left = certificates()
    if left:
        raise SigningError(f"{', '.join(left)} is still in your login keychain: {' '.join(filter(None, problems))}")
    say(f"removed {', '.join(found)}")
