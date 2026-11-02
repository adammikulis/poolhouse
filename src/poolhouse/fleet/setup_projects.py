"""Private coding projects prepared by an explicit first-run choice."""
from __future__ import annotations

import hashlib
import os
import secrets
import stat
import tempfile
import threading
from pathlib import Path

from poolhouse import home
from poolhouse.activity import source_snapshot
from poolhouse.files import promote_new
from poolhouse.graph.store import GraphStore
from poolhouse.lock import only_one
from poolhouse.net import git
from poolhouse.private_path import redirected

from .project_source import ProjectError
from .projects import identity

ERROR = 'Choose an available local folder again.'


def directory(path):
    """Reject redirected parents before inspecting or changing an exact directory."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path == Path(path.anchor):
        raise ValueError(ERROR)
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        if (redirected(info) or not stat.S_ISDIR(info.st_mode)
                or (os.name != 'nt' and (info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022))):
            raise ValueError(ERROR)
    info = path.stat()
    return info.st_dev, info.st_ino


class Projects:
    """Keep selected paths private; only explicit creation initializes an empty repository."""

    def __init__(self, root, registry):
        self.root, self.registry = Path(root), registry
        self.lock = threading.RLock()

    def _read(self, ident):
        with only_one(self.root / 'setup-jobs.lock'), GraphStore(self.root / 'setup-jobs.db') as graph:
            return graph.get_doc('setup-project:' + ident)

    def _write(self, ident, row):
        with only_one(self.root / 'setup-jobs.lock'), GraphStore(self.root / 'setup-jobs.db') as graph:
            graph.put_doc('setup-project:' + ident, row)

    @staticmethod
    def _check(current):
        if current() is not True:
            raise PermissionError('This setup session ended. Open setup again.')

    def _register(self, path, current):
        self._check(current)
        stamp = directory(path)
        identifier = identity(path)
        with self.registry.lock:
            try:
                existing = self.registry.get(identifier)
            except ProjectError:
                existing = None
            if existing is not None and Path(existing.root) != path:
                raise ValueError('This project is already registered in another folder.')
            self._check(current)
            if directory(path) != stamp:
                raise ValueError(ERROR)
            result = self.registry.register(path, identifier)
            self._check(current)
            return {'id': result['id'], 'title': result['name']}

    def select(self, path, request_id, *, current):
        """Register an existing exact Git root or retain an inert, inode-bound candidate."""
        path = Path(path)
        with self.lock:
            self._check(current)
            stamp = directory(path)
            source_snapshot.validate_storage(path, source_snapshot.protected_roots(dict(os.environ)))
            try:
                top = Path(git.run(['rev-parse', '--show-toplevel'], cwd=path).stdout.strip())
            except git.GitFailed:
                top = None
            if top is not None:
                if top != path:
                    raise ValueError('Choose the project folder itself, rather than a folder inside it.')
                return {'status': 'complete', 'project': self._register(path, current)}
            prior = self._read(request_id)
            row = {'id': request_id, 'path': str(path), 'device': stamp[0], 'inode': stamp[1],
                   'title': path.name, 'state': 'selected'}
            if prior is not None and any(prior[key] != row[key] for key in ('path', 'device', 'inode')):
                raise ValueError(ERROR)
            self._check(current)
            self._write(request_id, prior or row)
            return {'status': 'needs-project', 'project': {'id': request_id, 'title': path.name}}

    def create(self, candidate, *, current):
        """An explicit Create action, never a picker side effect or a sharing grant."""
        with self.lock:
            self._check(current)
            row = self._read(candidate)
            if row is None:
                raise ValueError(ERROR)
            path = Path(row['path'])
            source_snapshot.validate_storage(path, source_snapshot.protected_roots(dict(os.environ)))
            if directory(path) != (row['device'], row['inode']):
                raise ValueError(ERROR)
            if row['state'] == 'complete':
                return self._register(path, current)
            self._git_directory(candidate, row, path, current)
            self._check(current)
            if row['state'] == 'initializing':
                if not (path / '.git' / 'config').exists():
                    git.run(['init', '--initial-branch=workspace', '--template='], cwd=path, protocols='')
                self._verify_git(path, candidate, committed=False)
                row['state'] = 'committing'
                self._write(candidate, row)
            self._check(current)
            if directory(path) != (row['device'], row['inode']):
                raise ValueError(ERROR)
            if row['state'] == 'committing':
                try:
                    git.run(['rev-parse', '--verify', 'HEAD'], cwd=path, protocols='')
                except git.GitFailed:
                    git.commit_empty(path, ('Poolhouse setup agent', 'setup@agents.invalid'),
                                     self._initial_message(candidate))
                row.update(state='registering', commit=self._verify_git(path, candidate, committed=True))
                self._write(candidate, row)
            if self._verify_git(path, candidate, committed=True) != row['commit']:
                raise ValueError('This folder changed. Choose it again.')
            result = self._register(path, current)
            row.update(state='complete', project=result)
            self._write(candidate, row)
            return result

    def _git_directory(self, candidate, row, path, current):
        gitdir = path / '.git'
        if row['state'] == 'selected':
            if gitdir.exists() or gitdir.is_symlink():
                raise ValueError('This folder changed. Choose it again.')
            # An unrecorded stage is an orphan, never adopted on retry.
            stage = Path(tempfile.mkdtemp(prefix='.poolhouse-setup-', dir=path))
            stamp = directory(stage)
            row.update(state='staging', receipt=secrets.token_hex(32), stage=stage.name,
                       stage_device=stamp[0], stage_inode=stamp[1])
            self._write(candidate, row)
        if row['state'] == 'staging':
            stage = path / row['stage']
            if stage.exists():
                if directory(stage) != (row['stage_device'], row['stage_inode']):
                    raise ValueError(ERROR)
                if any(entry.name != 'poolhouse-setup-receipt' for entry in stage.iterdir()):
                    raise ValueError(ERROR)
                self._receipt(stage, row['receipt'], current, write=True)
                self._check(current)
                promote_new(stage, gitdir)
            if directory(gitdir) != (row['stage_device'], row['stage_inode']):
                raise ValueError(ERROR)
            row['state'] = 'initializing'
            self._write(candidate, row)
        if directory(gitdir) != (row['stage_device'], row['stage_inode']):
            raise ValueError(ERROR)
        self._receipt(gitdir, row['receipt'], current, write=False)

    def _receipt(self, folder, value, current, *, write):
        marker = folder / 'poolhouse-setup-receipt'
        flags = (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open(marker, flags | (os.O_CREAT | os.O_EXCL if write else 0), 0o600)
        except FileExistsError:
            descriptor = os.open(marker, flags)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError(ERROR)
            existing = os.read(descriptor, 65).decode('ascii')
            retained = marker.lstat()
            if (retained.st_dev, retained.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError(ERROR)
            if existing != value:
                if not write or not value.startswith(existing):
                    raise ValueError(ERROR)
                self._check(current)
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, value.encode('ascii'))
                os.ftruncate(descriptor, 64)
            retained = marker.lstat()
            if (retained.st_dev, retained.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError(ERROR)
        finally:
            os.close(descriptor)

    @staticmethod
    def _initial_message(candidate):
        if not isinstance(candidate, str) or len(candidate) != 32 or any(c not in '0123456789abcdef' for c in candidate):
            raise ValueError(ERROR)
        return 'Create private coding workspace\n\nPoolhouse project: ' + candidate

    @staticmethod
    def _verify_git(path, candidate, *, committed):
        """Reconcile only our fixed, empty initial repository; foreign content is refused."""
        def read(*args):
            return git.run(list(args), cwd=path, protocols='').stdout.strip()
        if read('rev-parse', '--show-toplevel') != str(path) or read('symbolic-ref', 'HEAD') != 'refs/heads/workspace':
            raise ValueError('This folder changed. Choose it again.')
        if read('remote') or read('ls-files'):
            raise ValueError('This folder changed. Choose it again.')
        if not committed:
            return ''
        if (read('rev-list', '--count', 'HEAD') != '1'
                or read('show', '-s', '--format=%B', 'HEAD') != Projects._initial_message(candidate)
                or read('ls-tree', '-r', '--name-only', 'HEAD')):
            raise ValueError('This folder changed. Choose it again.')
        return read('rev-parse', 'HEAD')

    def default(self, *, current):
        """Create one app-owned private workspace; repeat calls return the same registration."""
        with self.lock:
            self._check(current)
            directory(self.root)
            parent = home.user_home() / 'Poolhouse Projects'
            parent.mkdir(mode=0o700, exist_ok=True)
            directory(parent)
            path = parent / 'Workspace'
            path.mkdir(mode=0o700, exist_ok=True)
            stamp = directory(path)
            if os.name != 'nt' and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
                raise ValueError('The private coding workspace is not private.')
            candidate = hashlib.sha256(str(path).encode()).hexdigest()[:32]
            row = self._read(candidate)
            if row is not None:
                if (row['device'], row['inode']) != stamp:
                    raise ValueError(ERROR)
                return self.create(candidate, current=current)
            if any(path.iterdir()):
                raise ValueError('The private coding workspace already contains files. Choose another folder.')
            self._write(candidate, {'id': candidate, 'path': str(path), 'device': stamp[0],
                                   'inode': stamp[1], 'title': path.name, 'state': 'selected'})
            return self.create(candidate, current=current)
