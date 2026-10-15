"""Select verified Python runtimes without replacing running processes' import files."""

from __future__ import annotations

import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from poolhouse import windows_private
from poolhouse.files import writing
from poolhouse.home import state
from poolhouse.platform import private_file

COMMIT = re.compile(r"[0-9a-f]{40}")


def directory() -> Path:
    """Return the machine's owned runtime installation directory."""
    return state("runtimes", identity()).absolute()


def identity() -> str:
    """Return this platform, architecture and interpreter ABI's runtime key."""
    value = f"{sys.platform}-{platform.machine().lower()}-{sys.implementation.cache_tag}"
    if not re.fullmatch(r"[a-z0-9_-]+", value):
        raise OSError("runtime interpreter identity is invalid")
    return value


@dataclass(frozen=True, slots=True)
class Runtime:
    prefix: Path
    commit: str
    version: str
    identity: str

    @property
    def python(self) -> Path:
        return self.prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def environment(source=None) -> dict[str, str]:
    """Remove interpreter paths and frozen loader state from a child environment."""
    return {key: value for key, value in (os.environ if source is None else source).items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "_MEIPASS2",
                           "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}
            and not key.startswith("_PYI_")}


def installer_environment() -> dict[str, str]:
    """Remove pip destination selectors and disable inherited pip configuration."""
    values = environment()
    for key in ('PIP_TARGET', 'PIP_PREFIX', 'PIP_USER', 'PIP_ROOT', 'PIP_PYTHON', 'PYTHONUSERBASE'):
        values.pop(key, None)
    values['PIP_CONFIG_FILE'] = os.devnull
    return values


def plain(path: Path) -> None:
    """Refuse symbolic links in a runtime storage path."""
    if any(candidate.is_symlink() for candidate in (path, *path.parents)):
        raise OSError("runtime selection paths cannot be symbolic links")


def _owned(path: Path) -> None:
    plain(path)
    if os.name == "nt" and windows_private.problem(path):
        raise OSError("runtime selection must be private to its Windows account")
    info = path.stat()
    if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise OSError("runtime selection must be private to its owner")


def protect(path: Path) -> None:
    """Apply account-private permissions to new owned runtime storage."""
    plain(path)
    if os.name == "nt":
        windows_private.restrict(path)
    elif path.is_dir():
        path.chmod(0o700)
    else:
        private_file(path)


def verify(runtime: Runtime) -> Runtime:
    """Verify the interpreter, imported package, distribution and source revision."""
    if runtime.identity != identity():
        raise OSError("runtime belongs to a different platform or interpreter")
    if not COMMIT.fullmatch(runtime.commit):
        raise ValueError("runtime requires a full source commit")
    root = directory()
    _owned(root)
    family = root / runtime.commit
    _owned(family)
    expected = runtime.prefix
    if (expected.parent != family or not re.fullmatch(r"[0-9a-f]{32}", expected.name)
            or expected.is_symlink()):
        raise ValueError("runtime must use its owned source revision directory")
    _owned(expected)
    config = expected / "pyvenv.cfg"
    if config.is_symlink() or config.stat().st_size > 16384:
        raise OSError("runtime virtual environment configuration is invalid")
    fields = dict(line.split("=", 1) for line in config.read_text().splitlines() if "=" in line)
    fields = {key.strip().lower(): value.strip().lower() for key, value in fields.items()}
    if fields.get("include-system-site-packages") != "false":
        raise OSError("runtime dependencies must be isolated from system site packages")
    script = (
        "import json,sys,platform;from pathlib import Path;from importlib.metadata import distribution;"
        "import poolhouse;d=distribution('poolhouse');p=Path(poolhouse.__file__).resolve();"
        "print(json.dumps([sys.prefix,sys.base_prefix,sys.executable,str(p),"
        "str(d.locate_file('poolhouse/__init__.py').resolve()),d.version,"
        "p.with_name('fleet').joinpath('built-from').read_text().strip(),"
        "f'{sys.platform}-{platform.machine().lower()}-{sys.implementation.cache_tag}']))"
    )
    done = subprocess.run([str(runtime.python), "-I", "-c", script], capture_output=True,
                          text=True, timeout=30, env=environment())
    if done.returncode:
        raise OSError("runtime interpreter verification failed")
    prefix, base, executable, imported, packaged, version, commit, identity_key = json.loads(done.stdout)
    if (Path(prefix) != runtime.prefix or prefix == base or Path(executable) != runtime.python
            or imported != packaged or not Path(imported).is_relative_to(runtime.prefix)
            or version != runtime.version or commit != runtime.commit or identity_key != runtime.identity):
        raise OSError("runtime imports, metadata and source revision do not agree")
    return runtime


def publish(runtime: Runtime) -> None:
    """Atomically select a verified installed revision."""
    verify(runtime)
    target = directory() / "selected.json"
    if target.is_symlink():
        raise OSError("runtime selection cannot replace a symbolic link")
    with writing(target) as temporary:
        protect(temporary)
        temporary.write_text(json.dumps({"format": 1, "version": runtime.version, "commit": runtime.commit,
                                         "identity": runtime.identity, "prefix": str(runtime.prefix)}), encoding="utf-8")
        protect(temporary)


def selected() -> Runtime | None:
    """Read and verify the selected machine runtime."""
    root = directory()
    target = root / "selected.json"
    if not target.exists() and not target.is_symlink():
        return None
    _owned(root)
    _owned(target)
    if target.stat().st_size > 4096:
        raise OSError("runtime selection exceeds its size limit")
    row = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(row, dict) or set(row) != {"format", "prefix", "commit", "version", "identity"}:
        raise ValueError("invalid runtime selection")
    if type(row["format"]) is not int or row["format"] != 1:
        raise ValueError("unknown runtime selection format")
    if any(not isinstance(row[key], str) for key in ("prefix", "commit", "version", "identity")):
        raise ValueError("invalid runtime selection fields")
    return verify(Runtime(Path(row["prefix"]), row["commit"], row["version"], row["identity"]))


def available() -> Runtime | None:
    """Return the selected runtime, or None when none is selected or it fails verification."""
    try:
        return selected()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def selection_prefix() -> Path | None:
    """Return the prefix the selection file names, without running the interpreter."""
    try:
        row = json.loads((directory() / "selected.json").read_text(encoding="utf-8"))
        return Path(row["prefix"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def python() -> Path:
    """Return the selected interpreter, or the current nonfrozen interpreter."""
    chosen = available()
    if chosen is not None:
        return chosen.python
    if getattr(sys, "frozen", False):
        raise OSError("prepare a verified standalone runtime before starting workers")
    return Path(sys.executable)


def forward(module: str, argv: list[str]) -> bool:
    """Replace a launcher with the selected runtime when its prefix differs."""
    if not getattr(sys, "frozen", False) and Path(sys.prefix) == selection_prefix():
        return False
    chosen = available()
    if chosen is None or (not getattr(sys, "frozen", False) and Path(sys.prefix) == chosen.prefix):
        return False
    os.execve(str(chosen.python), [str(chosen.python), "-I", "-m", module, *argv], environment())  # noqa: S606
    return True


def rerun(script: Path, argv: list[str]) -> None:
    """Run ``script`` again, isolated, under the selected runtime; return when this is it, or none is selected."""
    chosen = available()
    if sys.flags.isolated or chosen is None or Path(sys.prefix) == chosen.prefix:
        return
    os.execve(str(chosen.python), [str(chosen.python), "-I", str(script), *argv], environment())  # noqa: S606


_LAUNCHER_READER = """def read(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                return dict()
            payload = stream.read(65537)
        if len(payload) > 65536:
            return dict()
        row = json.loads(payload)
        return row if isinstance(row, dict) else dict()
    except (OSError, ValueError, TypeError):
        return dict()
"""


LAUNCHER = """#!/usr/bin/env python3
import glob,json,os,re,stat,subprocess,sys,time
from pathlib import Path
root = {root!r}
python = {python!r}
env = {{k:v for k,v in os.environ.items() if k not in
       {{'PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','_MEIPASS2','LD_LIBRARY_PATH','DYLD_LIBRARY_PATH'}}
       and not k.startswith('_PYI_')}}
reader = __POOLHOUSE_READER_LITERAL__
exec(reader)
def interpreter(prefix):
    return os.path.join(prefix, 'Scripts/python.exe' if os.name == 'nt' else 'bin/python')
def usable(py):
    prefix = os.path.dirname(os.path.dirname(py))
    found = any(glob.glob(os.path.join(prefix, where, 'poolhouse', '__init__.py'))
                for where in ('lib/python*/site-packages', 'Lib/site-packages'))
    return os.path.isfile(py) and found and not os.path.exists(os.path.join(prefix, 'rejected'))
def made(prefix):
    return read(os.path.join(prefix, 'created.json')).get('tool') == 'poolhouse-runtime'
def stamp(prefix):
    try:
        return float(read(os.path.join(prefix, 'verified.json')).get('verified_at'))
    except (TypeError, ValueError):
        return 0.0
def private(path, regular=False):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        return False
    try:
        info = path.stat()
        kind = stat.S_ISREG(info.st_mode) if regular else stat.S_ISDIR(info.st_mode)
        return kind and info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077
    except OSError:
        return False

def proven(prefix):
    try:
        path = Path(prefix)
        family = path.parent
        if (family.parent != Path(root) or not re.fullmatch('[0-9a-f]{{40}}', family.name)
                or not re.fullmatch('[0-9a-f]{{32}}', path.name)
                or not all(private(p) for p in (Path(root), family, path))):
            return False
        marks = [path / name for name in ('created.json', 'verified.json')]
        if any(not private(p, True) or p.stat().st_size > 4096 for p in marks):
            return False
        created, verified = (read(p) for p in marks)
        return (created.get('tool') == 'poolhouse-runtime' and verified.get('commit') == family.name
                and verified.get('identity') == Path(root).name and isinstance(verified.get('version'), str)
                and isinstance(verified.get('verified_at'), (int, float)) and verified['verified_at'] > 0
                and not os.path.lexists(path / 'rejected') and usable(interpreter(prefix)))
    except (OSError, ValueError, TypeError):
        return False

if {follow_selection!r}:
    selector = Path(root) / 'selected.json'
    if not private(selector, True) or selector.stat().st_size > 4096:
        sys.exit({name!r} + ': the runtime selection is unavailable')
    selected = read(selector)
    prefix = selected.get('prefix')
    if (set(selected) != {{'format', 'prefix', 'commit', 'version', 'identity'}}
            or type(selected.get('format')) is not int or selected['format'] != 1
            or not isinstance(prefix, str) or not proven(prefix)
            or selected.get('commit') != Path(prefix).parent.name
            or selected.get('identity') != Path(root).name
            or selected.get('version') != read(Path(prefix) / 'verified.json').get('version')):
        sys.exit({name!r} + ': the selected runtime is not verified')
    if not proven(os.path.dirname(os.path.dirname(python))):
        python = next((interpreter(p) for p in [prefix] if proven(p)), '')
    if not python:
        sys.exit({name!r} + ': the runtime bootstrap is unavailable')
    env['POOLHOUSE_HOME'] = str(Path(root).parent.parent)
    bootstrap = ("import json,os,stat,sys,subprocess;from pathlib import Path;from poolhouse import runtime\\n" + reader + "\\n"
                 "row=" + repr(selected) + "\\n"
                 "try: chosen=runtime.verify(runtime.Runtime(Path(row['prefix']),row['commit'],row['version'],row['identity']))\\n"
                 "except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError): sys.exit('The runtime selection is unavailable.')\\n"
                 "if read(runtime.directory()/'selected.json') != row: sys.exit('The runtime selection changed; retry the command.')\\n"
                 "os.execve(str(chosen.python),[str(chosen.python),'-I',*" + repr({arguments}) + ",*sys.argv[1:]],runtime.environment())")
    os.execve(python, [python, '-I', '-c', bootstrap, *sys.argv[1:]], env)
if not usable(python):
    held = read(os.path.join(root, 'deploy.json')).get('held')
    held = held.get('commit') if isinstance(held, dict) else None
    chosen = read(os.path.join(root, 'selected.json')).get('prefix')
    others = sorted((os.path.dirname(path) for path in glob.glob(os.path.join(root, '*', '*', 'verified.json'))),
                    key=stamp, reverse=True)
    order = ([chosen] if isinstance(chosen, str) else []) + [p for p in others if made(p)]
    python = next((interpreter(p) for p in order if usable(interpreter(p))
                   and read(os.path.join(p, 'verified.json')).get('commit') != held), '')
    if not python:
        source = read(os.path.join(root, 'deploy.json')).get('checkout')
        marker = os.path.join(root, 'recovery.stamp')
        fresh = os.path.isfile(marker) and time.time() - os.path.getmtime(marker) < 60
        if isinstance(source, str) and os.path.isfile(os.path.join(source, 'src', 'poolhouse', '__init__.py')) and not fresh:
            try:
                open(marker, 'w').close()
                with os.fdopen(os.open(os.path.join(root, 'ensure.log'), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), 'ab') as log:
                    subprocess.Popen([sys.executable, '-P', '-m', 'poolhouse.runtime_cli', 'ensure', '--background', '--now'], cwd=source,
                                     env={{**env, 'PYTHONPATH': os.path.join(source, 'src')}},
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            except OSError:
                pass
        sys.exit({name!r} + ': no usable poolhouse runtime; recovery started if a source checkout is recorded')
os.execve(python, [python, '-I', *{arguments}, *sys.argv[1:]], env)
""".replace("__POOLHOUSE_READER_LITERAL__", repr(_LAUNCHER_READER))


def gateway(target: Path, module: str = "poolhouse.fleet.launch", function: str = "",
            chosen: Runtime | None = None) -> None:
    """Atomically write an owned standalone launcher for the selected interpreter."""
    chosen = verify(chosen) if chosen is not None else selected()
    if chosen is None:
        raise OSError("select an installed runtime before creating its launcher")
    write_launcher(target, module, function, chosen)


def confine_tests(path: Path) -> None:
    """Refuse a path outside the state root and the temporary directory while a test runs."""
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return
    if "POOLHOUSE_HOME" not in os.environ:
        raise OSError("a test must set POOLHOUSE_HOME before it touches runtimes or launchers")
    roots = (Path(os.path.realpath(state())), Path(os.path.realpath(tempfile.gettempdir())))
    if not any(Path(os.path.realpath(path)).is_relative_to(root) for root in roots):
        raise OSError("a test may not write launchers outside the state root or the temporary directory")


def write_launcher(target: Path, module: str, function: str, chosen: Runtime, *, follow_selection: bool = False) -> None:
    """Atomically write a launcher for an already verified runtime."""
    confine_tests(target)
    if not re.fullmatch(r"poolhouse(?:\.[a-z_][a-z0-9_]*)+", module):
        raise ValueError("launcher module must belong to poolhouse")
    if function and not re.fullmatch(r"[a-z_][a-z0-9_]*", function):
        raise ValueError("launcher function must be an identifier")
    plain(target)
    if target.exists():
        info = target.lstat()
        if (not stat.S_ISREG(info.st_mode) or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o022))
                or (os.name == "nt" and windows_private.launcher_problem(target))):
            raise OSError("runtime launcher must be an owned regular file")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    arguments = (["-c", f"import sys;sys.argv[0]={target.name!r};from {module} import {function};sys.exit({function}())"]
                 if function else ["-m", module])
    text = LAUNCHER.format(root=str(directory()), python=str(chosen.python),
                           arguments=repr(arguments), name=target.name, follow_selection=follow_selection)
    with writing(target) as temporary:
        temporary.write_text(text, encoding="utf-8")
        protect(temporary)
        if os.name != "nt":
            temporary.chmod(0o700)
