"""Install a verified compute companion into an isolated managed interpreter."""

from pathlib import Path

from poolhouse.net import packages

from . import compute_artifact


def replace_wheel(python: Path, wheel: Path, *, environment: dict, timeout: float = 3600):
    """Install one app-owned wheel over what is there, offline and writing no bytecode: the compute inventory refuses
    every file its manifest does not list. Pip also removes what an earlier compiling install recorded."""
    return packages.run(python, ['install', '--no-index', '--no-deps', '--no-compile',
                                 '--force-reinstall', str(wheel)], timeout=timeout, env=environment)


def install(python: Path, directory: Path, *, environment: dict):
    """Install only the companion declared by the current app manifest."""
    manifest = compute_artifact.expected()
    wheel = directory / manifest['filename']
    compute_artifact.verify(wheel, manifest, manifest['source'])
    result = replace_wheel(python, wheel, environment=environment)
    if result.returncode == 0:
        compute_artifact.verify_install(python, environment=environment)
    return result
