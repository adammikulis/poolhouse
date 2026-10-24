"""App-only Swift/SDK cache invalidation metadata, never installation or code proof."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

import testnode


def identity(app: Path) -> str:
    if sys.platform != 'darwin' or not (app / 'native-service/Bridge.swift').is_file():
        return ''
    minimum = json.loads((app / 'src-tauri/tauri.conf.json').read_text())['bundle']['macOS']['minimumSystemVersion']
    if not isinstance(minimum, str) or not minimum or any(c not in '0123456789.' for c in minimum):
        raise testnode.BuildError('The app macOS deployment floor is invalid')
    architecture = {'arm64': 'arm64', 'x86_64': 'x86_64'}.get(platform.machine())
    if architecture is None:
        raise testnode.BuildError('The native app test architecture is unsupported')
    target = f'{architecture}-apple-macosx{minimum}'
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('DYLD_', 'LD_')) and key not in {
                       'SWIFT_EXEC', 'SWIFTFLAGS', 'CODESIGN_ALLOCATE', 'DEVELOPER_DIR', 'SDKROOT',
                       'SWIFT_MODULECACHE_PATH', 'CLANG_MODULE_CACHE_PATH'}}
    values = []
    for command in (['/usr/bin/swiftc', '-target', target, '-print-target-info'],
                    ['/usr/bin/xcrun', '--show-sdk-path'], ['/usr/bin/xcrun', '--show-sdk-build-version']):
        try:
            done = subprocess.run(command, env=environment, capture_output=True, text=True,
                                  timeout=testnode.PROBE_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise testnode.BuildError('The app Swift/SDK cache identity is unavailable') from error
        if done.returncode or not done.stdout.strip():
            raise testnode.BuildError('The app Swift/SDK cache identity is unavailable')
        values.append(done.stdout.strip())
    try:
        compiler = json.loads(values[0])
        if (not isinstance(compiler.get('compilerVersion'), str) or not compiler['compilerVersion']
                or compiler.get('target', {}).get('triple') != target or not Path(values[1]).is_absolute()
                or not re.fullmatch('[0-9][0-9A-Za-z]+', values[2])):
            raise ValueError('unsupported metadata')
    except (ValueError, TypeError, AttributeError) as error:
        raise testnode.BuildError('The app Swift/SDK cache identity is invalid') from error
    # Metadata identifies invalidation inputs only; this is not a hermetic or protected build claim.
    return hashlib.sha256(json.dumps([compiler, *values[1:]], sort_keys=True).encode()).hexdigest()
