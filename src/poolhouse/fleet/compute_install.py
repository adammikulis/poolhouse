"""Install a verified compute companion into an isolated managed interpreter."""

from pathlib import Path

from poolhouse.net import packages

from . import compute_artifact


def install(python: Path, directory: Path, *, environment: dict):
    """Install only the companion declared by the current app manifest."""
    manifest = compute_artifact.expected()
    wheel = directory / manifest['filename']
    compute_artifact.verify(wheel, manifest, manifest['source'])
    result = packages.run(python, ['install', '--no-index', '--no-deps', '--no-compile',
                                  '--force-reinstall', str(wheel)], timeout=3600, env=environment)
    if result.returncode == 0:
        compute_artifact.verify_install(python, environment=environment)
    return result
