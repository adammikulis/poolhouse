"""Launcher-bound resource ownership for inspectable native harness mutations, kept as claims on the board."""

import os
import shlex
import shutil
import stat
from pathlib import Path

from poolhouse import node_launch, worktreerules
from poolhouse.board import credentials, place
from poolhouse.board.client import Client, Conflict, NodeError
from poolhouse.board.session import Session
from poolhouse.errors import Denied
from poolhouse.guard.destructive import classify
from poolhouse.guard.shellscan import has_expansion, has_glob, segments
from poolhouse.harnesspolicy import CATALOG, SHELL_TOOLS, _shell_line
from poolhouse.interventions import Call
from poolhouse.workspace import limits, worktree_lifecycle
from poolhouse.workspace.claim_keys import normal

FILE_TOOLS = frozenset({'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'apply_patch'})
FILE_COMMANDS = frozenset({'touch', 'mkdir', 'rm', 'rmdir', 'cp', 'mv', 'chmod', 'chown', 'tee'})
CLAIM_S = 900
PATHS = ('file', 'worktree', 'install')


class BoardUnavailable(Denied):
    """The board could not be reached or authenticated: local work carries on with a warning."""


def _path(value, cwd):
    return str((Path(cwd) / value).resolve())


def _target(word, here):
    """A fixed target as a file, and a word the shell expands as the checkout around it."""
    return ('worktree', _path('.', here)) if has_expansion(word) or has_glob(word) else ('file', _path(word, here))


def _null_sink(target):
    if target != os.devnull:
        return False
    if os.name == 'nt':
        return target.lower() == 'nul'
    try:
        return stat.S_ISCHR(Path(target).lstat().st_mode)
    except OSError:
        return False



def resources(name, args, cwd):
    """Extract fixed mutation targets without evaluating a shell command."""
    found = []
    if name in FILE_TOOLS:
        found.extend(('file', _path(value, cwd)) for key, value in args.items()
                     if key in ('file_path', 'notebook_path', 'path') and isinstance(value, str) and value)
        for value in args.values():
            if isinstance(value, str) and '*** Begin Patch' in value:
                found.extend(('file', _path(path, cwd)) for path in worktreerules.patch_paths(value))
    elif name in SHELL_TOOLS:
        commands, findings = segments(_shell_line(name, args))
        if findings:
            found.append(('worktree', _path('.', cwd)))
        here = cwd
        for command in commands:
            words = command.argv
            if not words:
                continue
            verb = Path(words[0]).name
            if verb == 'cd' and len(words) == 2:
                here = _path(words[1], here)
                continue
            found.extend(_target(target, here) for operator, target in command.redirects
                         if '>' in operator and not _null_sink(target))
            if verb in FILE_COMMANDS:
                found.extend(_target(word, here) for word in words[1:] if not word.startswith('-'))
            known = (verb == 'poolhouse' and words[1:] == ['--no-browser']) or verb in FILE_COMMANDS or verb in ('git', 'poolhouse-serve', 'poolhouse-workspace') or ('install' in words and 'pip' in words[:3])
            if not known and classify(Call('Bash', {'command': shlex.join(words)}), roots=(here,)).label != 'safe':
                found.append(('worktree', _path('.', here)))
            if verb == 'git' and classify(Call('Bash', {'command': shlex.join(words)}), roots=(here,)).label != 'safe':
                where = words[words.index('-C') + 1] if '-C' in words else here
                found.append(('worktree', _path(where, here)))
            if 'install' in words and (verb in ('pip', 'pip3', 'uv') or 'pip' in words[:3]):
                explicit = next((word.partition('=')[2] for word in words
                                 if word.startswith(('--prefix=', '--target='))), '') or next(
                                     (words[at + 1] for at, word in enumerate(words[:-1])
                                      if word in ('--prefix', '--target')), '')
                executable = shutil.which(words[0]) or (words[0] if Path(words[0]).is_absolute() else '')
                if not explicit and (not executable or 'shims' in Path(executable).parts or verb == 'uv'):
                    raise Denied('install ownership requires an explicit interpreter or target environment')
                target = _path(explicit, here) if explicit else str(Path(executable).resolve().parent.parent)
                found.append(('install', target))
            if verb == 'poolhouse-serve' and any(word in ('up', 'down', 'restart') for word in words[1:]):
                for at, word in enumerate(words):
                    if word == '--port' and at + 1 < len(words):
                        found.append(('port', words[at + 1]))
                    elif word.startswith('--port='):
                        found.append(('port', word.partition('=')[2]))
    physical = list(found)
    for kind, key in physical:
        if kind == 'file' and any((parent / '.git').exists() for parent in Path(key).parents):
            found.append(('area', normal('area', key)))
    return found


def _nested(a, b):
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def _held(kind, key):
    """The claim a resource is kept as on the board: a file is a claim on its path as an area."""
    return ('area' if kind == 'file' else kind), normal(kind, key)


def session_of(actor, cwd):
    """The session ``actor`` holds on the board of ``cwd``'s project. BoardUnavailable when the node or the token is not there;
    Denied when the node refuses the token or it belongs to another session."""
    try:
        node = Client(node_launch.default_state())
        board = place.resolve(node, Path(cwd))
        token = credentials.load(node.state, board, actor)
    except (NodeError, OSError) as error:
        raise BoardUnavailable(f'the board of {Path(cwd).name} is not reachable as {actor}: {error}') from error
    s = Session(node, board, token)
    try:
        named = s.name
    except NodeError as error:
        raise Denied(f'the board refused the token of {actor}: {error}') from error
    except OSError as error:
        raise BoardUnavailable(f'the board of {Path(cwd).name} is not reachable as {actor}: {error}') from error
    if named != actor:
        raise Denied(f'the token kept for {actor} belongs to {named}; ownership requires the launcher-bound identity')
    return s


def conflict(name, args, cwd, actor, roots=()):
    """Return an existing foreign ownership conflict before approval is requested."""
    if not isinstance(args, dict):
        return ''
    required = resources(name, args, cwd)
    if not required:
        return ''
    s = session_of(actor, roots[0] if roots else cwd)
    try:
        held = s.claims()
    except (NodeError, OSError) as error:
        raise BoardUnavailable(str(error)) from error
    for kind, key in required:
        wanted_kind, wanted = _held(kind, key)
        for claim in held:
            if claim.owner == actor:
                continue
            same = claim.kind == wanted_kind and (claim.key == wanted or (wanted_kind in ('worktree', 'area') and _nested(claim.key, wanted)))
            around = kind in PATHS and claim.kind in ('worktree', 'install', 'area') and _nested(claim.key, normal(kind, key))
            if same or around:
                return f"{kind} {key} belongs to {claim.owner}"
    return ''


def reserve(name, args, cwd, actor, roots):
    """Reserve an inspectable mutation using the launcher's authenticated session."""
    if not isinstance(args, dict) or (name not in SHELL_TOOLS and CATALOG.get(name) != 'reversible'):
        return
    required = resources(name, args, cwd)
    if not required:
        return
    approved = [Path(root).resolve() for root in roots]
    for kind, key in required:
        if kind in ('file', 'worktree') and not any(Path(key).is_relative_to(root) for root in approved):
            raise Denied('mutation target is outside the launcher-approved project')
    s = session_of(actor, approved[0] if approved else cwd)
    try:
        if any(place.resolve(s.client, root) != s.board for root in approved):
            raise Denied('mutation ownership requires the board of the launcher-approved project')
        s.reserve([_held(kind, key) for kind, key in required], ttl_s=CLAIM_S, pid=os.getppid())
    except Conflict:
        raise
    except (NodeError, OSError) as error:
        raise Denied(str(error)) from error
    assigned = worktree_lifecycle.assigned(limits.root(), actor)
    for kind, key in required:
        target = Path(key)
        if kind in ('file', 'worktree') and not (assigned and target.is_relative_to(assigned)):
            worktree_lifecycle.remember(limits.root(), actor, str(target.parent if target.is_file() else target))
