"""The macOS identity every Poolhouse process of an installed runtime runs under: an executable named Poolhouse in a signed bundle.

macOS names a program in its firewall list, its Local Network prompt and its background-item list by the executable the process runs, and keys
the answer by that executable's path and code signature. A runtime's `bin/python` leads to the interpreter Python was installed
with, so every prompt said python3.13 and one Allow belonged to every program using it. `bind` points the runtime's interpreter
at `~/.poolhouse/apps/runtime/<identity>/Poolhouse.app/Contents/MacOS/Poolhouse` instead: a copy of that interpreter signed by
`node_signing`, so a rebuild, a runtime update or a patch release of Python leaves the path and the name as they were. The signature
is local by default, which names the code, so macOS may ask again when the code changes; with the certificate a user added, its
designated requirement names no hash and stays. A certificate that cannot sign leaves a local signature and stops no update.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from poolhouse import (
    app_bundle,
    keystore,
    keystore_handoff,
    macho,
    node_signing,
    real_machine,
    runtime,
)
from poolhouse.files import promote, read_json, sha256_file, write_json
from poolhouse.home import home
from poolhouse.log import say, warn

BUNDLE_ID = "app.poolhouse.runtime"
NAME = app_bundle.NAME
EXECUTABLE = "Poolhouse"
SOURCE_RECORD = "source.json"
TOOL_TIMEOUT = 20.0
PROBE_TIMEOUT = 60.0
IDENTITY = re.compile(r"[a-z0-9_-]+")
BARE = "import glob, json, os, re, stat, subprocess, sys, time"
RENAME_SWAP = 2
LOOPBACK = ("127.", "::1", "localhost")
PROBE = ("import ctypes,json,os,sys;b=ctypes.create_string_buffer(4096);n=ctypes.c_uint32(4096);"
         "ctypes.CDLL(None)._NSGetExecutablePath(b,ctypes.byref(n));"
         "print(json.dumps([sys.prefix,os.path.realpath(b.value.decode())]))")
Signer = Callable[[Path, str, Callable[[str], str]], str]


class IdentityError(OSError):
    """A runtime could not be made to run as Poolhouse; nothing was left half-changed."""


def _mac() -> bool:
    return sys.platform == "darwin"


def applies() -> bool:
    """Whether this process is the installed software on a Mac, the only place that signs or binds anything."""
    return _mac() and not real_machine.refusal()


def _real(path: Path | str) -> Path:
    return Path(os.path.realpath(path))


def bundle_path(identity: str | None = None) -> Path:
    """The Poolhouse.app that holds the executable for a runtime identity (platform, machine and Python ABI)."""
    named = identity or runtime.identity()
    if not IDENTITY.fullmatch(named):
        raise IdentityError(f"{named!r} is not a runtime identity")
    return app_bundle.apps_directory() / "runtime" / named / f"{NAME}.app"


def executable_path(identity: str | None = None) -> Path:
    """The executable every process of that runtime runs as."""
    return bundle_path(identity) / "Contents" / "MacOS" / EXECUTABLE


def info_plist(version: str, *, icon: bool = True) -> dict:
    """The bundle's Info.plist: the keys every Poolhouse bundle has, under this bundle's own id and executable."""
    return app_bundle.info_plist(BUNDLE_ID, EXECUTABLE, version, icon=icon)


def requirement(certificate_sha1: str) -> str:
    """The designated requirement: this bundle id signed by this certificate, and no hash of the executable's bytes."""
    return node_signing.certificate_requirement(BUNDLE_ID, certificate_sha1)


def stable_requirement(text: str) -> bool:
    """Whether a designated requirement is exactly the certificate signature's of this bundle."""
    return bool(node_signing.signer_of(text, BUNDLE_ID))


def _tool(argv: list[str]) -> subprocess.CompletedProcess:
    """Run a system tool; a tool that cannot run or hangs is a failed check, never an exception for the caller."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=TOOL_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 1, "", f"{argv[0]}: {exc}")


def requirement_of(path: Path) -> str:
    """The designated requirement `codesign` reads off ``path``, or ''; one codesign made itself keeps its leading "# "."""
    lines = (_tool(["codesign", "-d", "-r-", str(path)]).stdout or "").splitlines()
    return next((line for line in lines if line.removeprefix("# ").startswith("designated")), "")


def identifier_of(path: Path) -> str:
    """The signing identifier `codesign` reads off ``path``, or ''."""
    lines = (_tool(["codesign", "-d", "--verbose=2", str(path)]).stderr or "").splitlines()
    return next((line.partition("=")[2] for line in lines if line.startswith("Identifier=")), "")


def signature_form(bundle: Path, identifier: str) -> str:
    """How ``bundle`` is signed as ``identifier``: "certificate" (exactly that requirement, which its code satisfies), "local"
    (codesign's own requirement) or '' for anything else, such as a requirement another program could satisfy."""
    required = requirement_of(bundle)
    if node_signing.signer_of(required, identifier):
        asked = f"-R={required.removeprefix('designated => ')}"
        return "certificate" if _tool(["codesign", "--verify", "--strict", asked, str(bundle)]).returncode == 0 else ""
    return "local" if node_signing.local_requirement(required) and identifier_of(bundle) == identifier else ""


def signature_problem(bundle: Path) -> str:
    """Why ``bundle`` does not carry a Poolhouse signature (local or the certificate's), or '' when it does."""
    checked = _tool(["codesign", "--verify", "--strict", str(bundle)])
    if checked.returncode:
        return f"{bundle} does not verify: {(checked.stderr or checked.stdout).strip()[-200:]}"
    return "" if signature_form(bundle, BUNDLE_ID) else f"{bundle} is not signed as Poolhouse"


def _certificate() -> str:
    """The certificate the user opted in with, as the installed software of a Mac sees it; '' anywhere else."""
    return node_signing.usable_certificate() if applies() else ""


def bundle_problem(identity: str | None = None) -> str:
    """Why the executable is not ready to run anything as Poolhouse, or '' when it is."""
    if not executable_path(identity).is_file():
        return "Poolhouse has no signed executable yet: run `poolhouse runtime ensure`"
    problem = signature_problem(bundle_path(identity))
    if problem:
        return problem
    leaf = _certificate()
    signed_by = node_signing.signer_of(requirement_of(bundle_path(identity)), BUNDLE_ID) if leaf else ""
    if signed_by and signed_by != leaf:
        return "the Poolhouse executable is signed with another certificate than this Mac's"
    return ""


def source_of(bundle: Path) -> dict:
    """What the executable was copied from: ``interpreter`` (its path then) and ``sha256``."""
    row = read_json(bundle / "Contents" / "Resources" / SOURCE_RECORD, {})
    return row if isinstance(row, dict) else {}


def inside(path: Path | str) -> bool:
    """Whether ``path`` leads to an executable of a Poolhouse runtime bundle."""
    return _real(path).is_relative_to(_real(app_bundle.apps_directory() / "runtime"))


def interpreter_of(prefix: Path) -> Path:
    """The file a runtime's ``bin/python`` leads to."""
    return _real(prefix / "bin" / "python")


def bound(prefix: Path, identity: str | None = None) -> bool:
    """Whether a runtime's interpreter is this identity's Poolhouse executable."""
    return interpreter_of(prefix) == _real(executable_path(identity))


def base_of(prefix: Path) -> Path | None:
    """The interpreter a runtime was made from, read from its own ``pyvenv.cfg`` (``executable``); None when it names no file.

    Read from the runtime, never from the bundle, so the executable can always be made again from what the runtime says."""
    try:
        rows = (line.partition("=") for line in (prefix / "pyvenv.cfg").read_text(encoding="utf-8").splitlines())
    except OSError:
        return None
    named = next((value.strip() for key, _, value in rows if key.strip() == "executable"), "")
    found = unbound(Path(named)) if named else None
    return _real(found) if found is not None and found.is_file() else None


def unbound(interpreter: Path) -> Path:
    """The interpreter a Poolhouse executable was copied from, so a runtime built from it points at Python, not at a copy."""
    real = _real(interpreter)
    if not inside(real):
        return interpreter
    recorded = Path(str(source_of(real.parents[2]).get("interpreter", "")))
    return recorded if recorded.is_file() else interpreter


def held_interpreter(identity: str | None = None) -> Path | None:
    """The interpreter the Poolhouse executable was copied from, while it is there unchanged: a runtime built from it leaves the
    executable, and what macOS keeps against its signature, as they are. None otherwise."""
    row = source_of(bundle_path(identity))
    recorded = Path(str(row.get("interpreter", "")))
    if not recorded.is_file():
        return None
    try:
        return recorded if digest(parts(recorded)) == row.get("sha256") else None
    except OSError:
        return None


def parts(interpreter: Path) -> list[tuple[Path, str]]:
    """The files that make the executable: ``interpreter`` (as ``Poolhouse``) and each library it loads from beside itself
    (``@executable_path/`` and ``@loader_path/``, which a Python that is not installed in place, such as a standalone build, uses),
    each with its path under ``Contents/MacOS``. A library that would sit outside ``Contents`` is refused."""
    found, queue, seen = [(interpreter, EXECUTABLE)], [(interpreter, EXECUTABLE)], {interpreter}
    while queue:
        source, at = queue.pop()
        for name in macho.dependencies(source):
            kind, _, relative = name.partition("/")
            if kind not in ("@executable_path", "@loader_path"):
                continue
            beside = (interpreter.parent, Path()) if kind == "@executable_path" else (source.parent, Path(at).parent)
            wanted, where = Path(os.path.normpath(beside[0] / relative)), Path(os.path.normpath(beside[1] / relative))
            if Path(os.path.normpath(Path("Contents", "MacOS") / where)).parts[:1] != ("Contents",):
                raise IdentityError(f"{source} loads {name}, which cannot sit inside the Poolhouse bundle")
            if wanted not in seen and wanted.is_file():
                seen.add(wanted)
                found.append((wanted, str(where)))
                queue.append((wanted, str(where)))
    return found


def digest(files: list[tuple[Path, str]]) -> str:
    """One checksum over the interpreter and its libraries, so a changed library rebuilds the executable."""
    parts_ = "".join(f"{where}:{sha256_file(source)};" for source, where in files)
    return hashlib.sha256(f"{files[0][0].parent}|{parts_}".encode()).hexdigest()


def _assemble(staging: Path, files: list[tuple[Path, str]], sha: str) -> None:
    for source, where in files:
        target = Path(os.path.normpath(staging / "Contents" / "MacOS" / where))
        if target.relative_to(staging / "Contents").parts[0] in ("Resources", "_CodeSignature", "Info.plist") or target.name == "pyvenv.cfg":
            raise IdentityError(f"{source} loads a library that would sit where the bundle keeps its own files")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o755)
    resources = staging / "Contents" / "Resources"
    resources.mkdir()
    icon = app_bundle.ICON_SVG if app_bundle.ICON_SVG.is_file() else None
    has_icon = icon is not None and app_bundle.icns(icon, resources / f"{NAME}.icns")
    (staging / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info_plist(sha[:12], icon=has_icon)))
    write_json(resources / SOURCE_RECORD, {"interpreter": str(files[0][0]), "sha256": sha})
    # run bare (a launchd unit starts the executable with no virtual environment), Python finds its standard library from here
    (resources / "pyvenv.cfg").write_text(f"home = {files[0][0].parent}\ninclude-system-site-packages = false\n", encoding="utf-8")
    (staging / "Contents" / "MacOS" / "pyvenv.cfg").symlink_to("../Resources/pyvenv.cfg")  # a file loose in MacOS would have to be code


def _exchange(one: Path, other: Path) -> bool:
    """Swap two directories in one step (``renamex_np``); False where the system cannot."""
    try:
        return ctypes.CDLL(None, use_errno=True).renamex_np(os.fsencode(one), os.fsencode(other), RENAME_SWAP) == 0
    except (OSError, AttributeError):
        return False


def _swap(staging: Path, bundle: Path) -> None:
    """Put ``staging`` in ``bundle``'s place; the old bundle is put back when the move fails."""
    if bundle.exists() and _exchange(staging, bundle):
        return  # the old bundle is now where staging was; there was no moment without a bundle
    old = bundle.parent / ".replaced" / bundle.name
    shutil.rmtree(old.parent, ignore_errors=True)
    if bundle.exists():
        old.parent.mkdir()
        promote(bundle, old)
    try:
        promote(staging, bundle)
    except OSError:
        if old.exists():
            promote(old, bundle)
        raise


def _keeps(bundle: Path, identifier: str) -> bool:
    """Whether an app stays as it is when its new signature came out local: it carries a certificate signature that verifies, the
    keychain has a usable identity of that certificate or cannot be asked, and, for a runtime's, its executable still starts."""
    if not (applies() and bundle.exists() and signature_form(bundle, identifier) == "certificate" and node_signing.may_have_identity()):
        return False
    if identifier == BUNDLE_ID and bare_problem(bundle / "Contents" / "MacOS" / EXECUTABLE):
        return False
    warn(f"signing with the certificate failed, so {bundle} keeps the executable and certificate signature it had")
    return True


def kept_note(prefix: Path, identity: str | None = None) -> str:
    """What to say when the runtime at ``prefix`` runs under an executable made from another interpreter than its own, which is
    what a failed signature or key hand-off leaves of the app that was there; else ''."""
    if not applies() or not bound(prefix, identity):
        return ""
    base, row = base_of(prefix), source_of(bundle_path(identity))
    try:
        if base is None or row.get("sha256") == digest(parts(base)):
            return ""
    except OSError:
        return ""
    sooner = f", and `{node_signing.ENABLE}` tries now" if stable_requirement(requirement_of(bundle_path(identity))) else ""
    return (f"Poolhouse runs under the executable made from {row.get('interpreter')}, not from {base}, because signing a new one with "
            f"the certificate, or handing it the encryption key, failed: the next update tries again{sooner}")


def unreadable_note() -> str:
    """What to say when the encryption key could not be handed on and nothing has read it since; else ''."""
    why = keystore.Keystore().unreadable() if applies() else ""
    return f"{keystore.UNREADABLE_ADVICE} ({why})" if why else ""


def notices(prefix: Path, identity: str | None) -> list[str]:
    """What a person should know about the executable the runtime at ``prefix`` was bound to."""
    return [one for one in (kept_note(prefix, identity), unreadable_note()) if one]


def _old_programs(bundle: Path) -> list[Path]:
    """The programs that may read the key now: the interpreter the selected runtime runs as (the executable in ``bundle`` once it
    is bound, the bare Python before) and the executable in ``bundle``."""
    selected = runtime.selection_prefix()
    found = [interpreter_of(selected)] if selected is not None else []
    found.append(bundle / "Contents" / "MacOS" / EXECUTABLE)
    return list({_real(one): one for one in found if one.is_file()}.values())


def _hand_key_over(bundle: Path, new: Path) -> bool:
    """Before ``new`` takes the place of the executable in ``bundle``, give it the key Poolhouse keeps, from a program that reads
    it now. True when it was given, so that it is next read from its place; where no program can give it, that is recorded."""
    store = keystore.Keystore()
    if not (applies() and store.status()["provisioned"]):
        return False
    why = "no program that could read it starts"
    for old in _old_programs(bundle):
        if sha256_file(old) == sha256_file(new):
            return False
        if bare_problem(old):
            continue
        if keystore_handoff.hand_over(old, new):
            return True
        why = f"macOS does not let {old} read it"
    store.mark_unreadable(why)
    return False


def _replace(bundle: Path, staging: Path, keep: bool) -> bool:
    """Put the runtime executable made in ``staging`` in the place of ``bundle``'s, the key handed to it before and read from
    there after; a new one that cannot read it is taken out again. With ``keep``, when the key cannot be handed over now, the old
    one stays and False is returned."""
    executable = bundle / "Contents" / "MacOS" / EXECUTABLE
    try:
        handed = _hand_key_over(bundle, staging / "Contents" / "MacOS" / EXECUTABLE)
    except keystore_handoff.HandoverError as exc:
        if not (keep and bundle.exists() and not signature_problem(bundle) and not bare_problem(executable)):
            raise
        warn(f"{bundle} keeps the executable it had, because the key could not be handed to a new one: {exc}")
        return False
    previous = staging.parent / "before" / bundle.name
    if handed and bundle.exists():
        shutil.copytree(bundle, previous, symlinks=True)
    _swap(staging, bundle)
    if handed and not keystore_handoff.reads(executable):
        if previous.exists():
            _swap(previous, bundle)
        else:
            shutil.rmtree(bundle)
        raise keystore_handoff.HandoverError("the new executable does not read the key from its place, so it was taken out again")
    return True


def retire_old_keys(identity: str | None = None) -> None:
    """Once a runtime is selected, have its executable delete the key items of every signature but its own and the one before."""
    executable = executable_path(identity)
    if applies() and executable.is_file() and keystore.Keystore().signatures() > 2 and not keystore_handoff.retire(executable):
        warn("the keychain items of earlier signatures of Poolhouse were not deleted; the next update tries again")


@contextmanager
def restored_on_failure(identity: str | None = None) -> Iterator[None]:
    """Put the Poolhouse executable back as it was when the block fails, so a runtime that was not selected leaves what the
    selected one runs as unchanged."""
    bundle = bundle_path(identity)
    if not applies() or not bundle.exists():
        yield
        return
    backup = bundle.parent / ".previous" / bundle.name
    shutil.rmtree(backup.parent, ignore_errors=True)
    shutil.copytree(bundle, backup, symlinks=True)
    held, succeeded = (source_of(bundle).get("sha256"), requirement_of(bundle)), False
    try:
        yield
        succeeded = True
    finally:
        if not succeeded and (source_of(bundle).get("sha256"), requirement_of(bundle)) != held:
            with app_bundle.locked(bundle):
                _swap(backup, bundle)
        shutil.rmtree(backup.parent, ignore_errors=True)


def _build(bundle: Path, files: list[tuple[Path, str]], sha: str, signer: Signer, keep: bool) -> None:
    """Make the signed bundle beside its place and move it in only once it verifies and starts, so a failure leaves the old one
    standing; with ``keep`` the old one also stays where `_keeps` says so."""
    with app_bundle.staged(bundle) as staging:
        _assemble(staging, files, sha)
        if signer(staging, BUNDLE_ID, requirement) != "certificate" and keep and _keeps(bundle, BUNDLE_ID):
            return
        problem = signature_problem(staging) or bare_problem(staging / "Contents" / "MacOS" / EXECUTABLE)
        if problem:
            raise IdentityError(problem)
        _replace(bundle, staging, keep)


def ensure_bundle(interpreter: Path, identity: str | None = None, *, signer: Signer = node_signing.sign, keep: bool = True) -> Path:
    """The signed executable made from ``interpreter``, built when it is missing, holds another interpreter, no longer verifies, or
    is signed locally although the user has a certificate for it (one attempt each time a runtime is bound). With ``keep`` a
    certificate-signed one may stay when its replacement cannot be signed with the certificate (`_keeps`)."""
    bundle = bundle_path(identity)
    files = parts(interpreter)
    sha = digest(files)
    bundle.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    bundle.parent.chmod(0o700)
    with app_bundle.locked(bundle):
        if source_of(bundle).get("sha256") == sha and not bundle_problem(identity) \
                and (not _certificate() or stable_requirement(requirement_of(bundle))):
            return executable_path(identity)
        say(f"runtime: signing the Poolhouse executable at {bundle} from {interpreter}")
        try:
            _build(bundle, files, sha, signer, keep)
        except (node_signing.SigningError, subprocess.SubprocessError, ValueError) as exc:
            raise IdentityError(f"Poolhouse could not sign its executable ({exc}); the runtime keeps the identity it had") from exc
        except keystore_handoff.HandoverError as exc:
            raise IdentityError(f"Poolhouse could not change its executable ({exc}); the runtime keeps the identity it had") from exc
        if note := unreadable_note():
            warn(note)
    return executable_path(identity)


def bundles() -> list[Path]:
    """Every runtime's Poolhouse.app that holds an executable."""
    found = (app_bundle.apps_directory() / "runtime").glob(f"*/{NAME}.app")
    return sorted(one for one in found if (one / "Contents" / "MacOS" / EXECUTABLE).is_file())


def resign(bundle: Path, identifier: str, requirement_for: Callable[[str], str]) -> str:
    """Sign an app that already exists with what the keychain now holds; returns "certificate", "ad-hoc" or, where the app stays
    as it was (`_keeps`, or its key could not be handed on), "kept". A copy is signed beside it and moved into its place, so a
    failure leaves the app as it was."""
    with app_bundle.locked(bundle), app_bundle.staged(bundle) as staging:
        shutil.copytree(bundle, staging, symlinks=True)
        signed = node_signing.sign(staging, identifier, requirement_for)
        if signed != "certificate" and _keeps(bundle, identifier):
            return "kept"
        if identifier != BUNDLE_ID:  # the node's app reads no key
            _swap(staging, bundle)
        elif not _replace(bundle, staging, True):
            return "kept"
    if identifier == BUNDLE_ID:
        retire_old_keys(bundle.parent.name)
        if note := unreadable_note():
            warn(note)
    return signed


def resign_apps() -> list[tuple[Path, str]]:
    """Sign every Poolhouse app on this Mac again with what the keychain now holds; each as (app, what `resign` returned)."""
    apps = [(bundle, BUNDLE_ID, requirement) for bundle in bundles()]
    node = app_bundle.node_bundle()
    if (node / "Contents" / "MacOS" / app_bundle.NODE_EXECUTABLE).is_file():
        apps.append((node, app_bundle.NODE_ID, lambda sha1: node_signing.certificate_requirement(app_bundle.NODE_ID, sha1)))
    return [(bundle, resign(bundle, identifier, requirement_for)) for bundle, identifier, requirement_for in apps]


def _link(link: Path, target: Path | str) -> None:
    fresh = link.with_name(f".{link.name}.new")
    fresh.unlink(missing_ok=True)
    fresh.symlink_to(target)
    promote(fresh, link)


def _repoint(prefix: Path, interpreter: Path, executable: Path) -> list[tuple[Path, str]]:
    """Point each ``python*`` link of the runtime that leads to ``interpreter`` at ``executable``; returns what undoes it."""
    links = [one for one in sorted((prefix / "bin").glob("python*")) if one.is_symlink() and _real(one) == interpreter]
    undo = [(one, one.readlink()) for one in links]
    for one in links:
        _link(one, executable)
    return undo


def _probe(prefix: Path, executable: Path) -> None:
    """Start the runtime's interpreter and require that the kernel ran the Poolhouse executable and Python found its prefix."""
    done = subprocess.run([str(prefix / "bin" / "python"), "-I", "-c", PROBE], capture_output=True, text=True,
                          timeout=PROBE_TIMEOUT, env=runtime.environment(), check=False)
    if done.returncode:
        raise IdentityError(f"the runtime does not start as Poolhouse: {(done.stderr or done.stdout).strip()[-300:]}")
    where, image = json.loads(done.stdout)
    if Path(where) != prefix or _real(image) != _real(executable):
        raise IdentityError(f"the runtime started as {image}, not as {executable}")


def bare_problem(executable: Path) -> str:
    """Why the executable cannot start by itself (a launchd unit starts it with no virtual environment), or ''."""
    done = _tool([str(executable), "-I", "-S", "-c", BARE])
    return "" if done.returncode == 0 else f"{executable} does not start on its own: {(done.stderr or done.stdout).strip()[-300:]}"


def bind(chosen: runtime.Runtime, *, signer: Signer = node_signing.sign, keep: bool = True) -> Path:
    """Make a runtime's interpreter the Poolhouse executable (idempotent); returns the executable.

    The executable is a signed copy of the interpreter the runtime was built with. A runtime that already leads to it only has
    the executable checked and rebuilt when it is gone. A kept executable (`_keeps`) that cannot start the runtime is replaced.
    A start that fails puts the old links back and raises `IdentityError`.
    """
    prefix, identity = chosen.prefix, chosen.identity
    done = bound(prefix, identity)
    base = base_of(prefix) or (None if done else interpreter_of(prefix))
    if base is None or not base.is_file():
        raise IdentityError("the runtime names no interpreter to make the Poolhouse executable from: the next build of the runtime does")
    def lead_to(keeping: bool) -> Path:
        executable = ensure_bundle(base, identity, signer=signer, keep=keeping)
        problem = bare_problem(executable)
        if problem:
            raise IdentityError(problem)
        if done:
            return executable
        undo = _repoint(prefix, interpreter_of(prefix), executable)
        try:
            _probe(prefix, executable)
        except (IdentityError, OSError, ValueError, subprocess.SubprocessError) as exc:
            for link, target in undo:
                _link(link, target)
            raise IdentityError(str(exc)) from exc
        return executable

    try:
        return lead_to(keep)
    except IdentityError:
        if not keep or source_of(bundle_path(identity)).get("sha256") == digest(parts(base)):
            raise
        return lead_to(False)  # the executable from before was kept, and it cannot run this runtime


def adopt(chosen: runtime.Runtime, *, keep: bool = True) -> str:
    """`bind` on the installed software of a Mac, and a sentence when that changed the runtime; nothing anywhere else (a test, a
    checkout, another state root, another system)."""
    if not applies():
        return ""
    was = bound(chosen.prefix, chosen.identity)
    bind(chosen, keep=keep)
    return "" if was else f"{chosen.commit[:7]} now runs as Poolhouse"


def unbound_note(chosen: runtime.Runtime, command: str) -> str:
    """A sentence saying a Mac runtime was left running as its bare Python and why, with the one ``command`` that binds it; '' when
    nothing is left undone (bound already, a system with nothing to bind, or this process is the installed software, where `adopt`
    binds it or raises)."""
    if not _mac() or applies() or bound(chosen.prefix, chosen.identity):
        return ""
    return (f"NOT bound to the Poolhouse executable: {real_machine.refusal()}. This process can sign and bind only as the installed "
            f"Poolhouse, so the host keeps running as {interpreter_of(chosen.prefix).name}. To bind it run `{command}`")


def unit_problem() -> str:
    """Why a macOS unit cannot start under the Poolhouse executable on this machine, or ''; nothing outside the installed software."""
    return (bundle_problem() or bare_problem(executable_path())) if applies() else ""


def launch_argv(argv: list[str]) -> list[str]:
    """``argv`` run by the Poolhouse executable, for a unit that starts a launcher script, which has no program macOS could name.

    A machine that has no such executable (a checkout, another state root) runs its units as it always did."""
    return [str(executable_path()), "-I", "-S", *argv] if executable_path().is_file() else argv


def kernel_executable() -> str:
    """The file this process was started from, as the kernel has it (a link in ``sys.executable`` is followed)."""
    if sys.platform != "darwin":
        return os.path.realpath(sys.executable)
    size = ctypes.c_uint32(4096)
    buffer = ctypes.create_string_buffer(size.value)
    ctypes.CDLL(None)._NSGetExecutablePath(buffer, ctypes.byref(size))
    return os.path.realpath(buffer.value.decode())


def process_report() -> dict[str, str]:
    """What this process runs as, for the device report: its executable, its name and the bundle id when it is Poolhouse's.

    The executable is given relative to the state root when it is inside it, and by name alone elsewhere: a device report names no
    directory of the account.
    """
    image, state = Path(kernel_executable()), _real(home())
    return {"executable": str(image.relative_to(state)) if image.is_relative_to(state) else image.name, "name": image.name,
            "bundle_id": BUNDLE_ID if inside(image) else ""}


def exposed_listeners(rows: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Each (executable, address) listening beyond this machine under anything but a Poolhouse bundle."""
    apps = _real(app_bundle.apps_directory())
    return [(exe, address) for exe, address in rows
            if not address.startswith(LOOPBACK) and not _real(exe).is_relative_to(apps)]
