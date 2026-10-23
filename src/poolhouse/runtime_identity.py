"""The macOS identity every Poolhouse process of an installed runtime runs under: an executable named Poolhouse in a signed bundle.

macOS names a program in its firewall list, its Local Network prompt and its background-item list by the executable the process runs, and keys
the answer by that executable's path and code signature. A runtime's `bin/python` leads to the interpreter Python was installed
with, so every prompt said python3.13 and one Allow belonged to every program using it. `bind` points the runtime's interpreter
at `~/.poolhouse/apps/runtime/<identity>/Poolhouse.app/Contents/MacOS/Poolhouse` instead: a copy of that interpreter signed with
the certificate `node_signing` makes, whose designated requirement names no hash. A rebuild, a runtime update or a patch release of
Python then leaves the path and the requirement as they were. Without the certificate nothing is bound and the update stops there.
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
from collections.abc import Callable, Iterable
from pathlib import Path

from poolhouse import app_bundle, macho, node_signing, real_machine, runtime
from poolhouse.files import promote, read_json, sha256_file, write_json
from poolhouse.home import home
from poolhouse.lock import only_one
from poolhouse.log import say

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
    return f'designated => identifier "{BUNDLE_ID}" and certificate leaf = H"{certificate_sha1.lower()}"'


def stable_requirement(text: str) -> bool:
    """Whether a designated requirement is the Poolhouse one: this id, a certificate, no cdhash."""
    return f'identifier "{BUNDLE_ID}"' in text and 'certificate leaf = H"' in text and "cdhash" not in text


def _tool(argv: list[str]) -> subprocess.CompletedProcess:
    """Run a system tool; a tool that cannot run or hangs is a failed check, never an exception for the caller."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=TOOL_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 1, "", f"{argv[0]}: {exc}")


def requirement_of(path: Path) -> str:
    """The designated requirement `codesign` reads off ``path``, or ''."""
    lines = (_tool(["codesign", "-d", "-r-", str(path)]).stdout or "").splitlines()
    return next((line for line in lines if line.startswith("designated")), "")


def signature_problem(bundle: Path) -> str:
    """Why ``bundle`` does not carry the Poolhouse signature, or '' when it does."""
    checked = _tool(["codesign", "--verify", "--strict", str(bundle)])
    if checked.returncode:
        return f"{bundle} does not verify: {(checked.stderr or checked.stdout).strip()[-200:]}"
    return "" if stable_requirement(requirement_of(bundle)) else f"{bundle} is not signed with the Poolhouse requirement"


def bundle_problem(identity: str | None = None) -> str:
    """Why the executable is not ready to run anything as Poolhouse, or '' when it is."""
    if not executable_path(identity).is_file():
        return "Poolhouse has no signed executable yet: run `poolhouse runtime ensure`"
    problem = signature_problem(bundle_path(identity))
    if problem or not applies():
        return problem
    leaf = node_signing.certificate_sha1()
    if not leaf:
        return f"{node_signing.CERT_NAME} is not in the login keychain, so the Poolhouse executable has nothing to be checked against"
    return "" if f'H"{leaf}"' in requirement_of(bundle_path(identity)) else "the Poolhouse executable is signed with another certificate than this Mac's"


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


def _build(bundle: Path, files: list[tuple[Path, str]], sha: str, signer: Signer) -> None:
    """Make the signed bundle beside its place and move it in only once it verifies, so a failure leaves the old one standing."""
    staging = bundle.parent / ".building" / bundle.name
    shutil.rmtree(staging.parent, ignore_errors=True)
    try:
        _assemble(staging, files, sha)
        signer(staging, BUNDLE_ID, requirement)
        problem = signature_problem(staging)
        if problem:
            raise IdentityError(problem)
        _swap(staging, bundle)
    finally:
        shutil.rmtree(staging.parent, ignore_errors=True)
        shutil.rmtree(bundle.parent / ".replaced", ignore_errors=True)


def ensure_bundle(interpreter: Path, identity: str | None = None, *, signer: Signer = node_signing.sign_with_certificate) -> Path:
    """The signed executable made from ``interpreter``, built when it is missing, holds another interpreter or no longer verifies."""
    bundle = bundle_path(identity)
    files = parts(interpreter)
    sha = digest(files)
    bundle.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    bundle.parent.chmod(0o700)
    with only_one(bundle.parent / "build.lock", announce=lambda _: None):
        if source_of(bundle).get("sha256") == sha and not bundle_problem(identity):
            return executable_path(identity)
        say(f"runtime: signing the Poolhouse executable at {bundle} from {interpreter}")
        try:
            _build(bundle, files, sha, signer)
        except (node_signing.SigningError, subprocess.SubprocessError, ValueError) as exc:
            raise IdentityError(f"Poolhouse could not sign its executable ({exc}); the runtime keeps the identity it had") from exc
    return executable_path(identity)


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


def bind(chosen: runtime.Runtime, *, signer: Signer = node_signing.sign_with_certificate) -> Path:
    """Make a runtime's interpreter the Poolhouse executable (idempotent); returns the executable.

    The executable is a signed copy of the interpreter the runtime was built with. A runtime that already leads to it only has
    the executable checked and rebuilt when it is gone. A start that fails puts the old links back and raises `IdentityError`.
    """
    prefix, identity = chosen.prefix, chosen.identity
    done = bound(prefix, identity)
    base = base_of(prefix) or (None if done else interpreter_of(prefix))
    if base is None or not base.is_file():
        raise IdentityError("the runtime names no interpreter to make the Poolhouse executable from: the next build of the runtime does")
    executable = ensure_bundle(base, identity, signer=signer)
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


def adopt(chosen: runtime.Runtime) -> str:
    """`bind` on the installed software of a Mac, and a sentence when that changed the runtime; nothing anywhere else (a test, a
    checkout, another state root, another system)."""
    if not applies():
        return ""
    was = bound(chosen.prefix, chosen.identity)
    bind(chosen)
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
