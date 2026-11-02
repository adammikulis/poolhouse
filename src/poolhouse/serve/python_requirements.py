"""App-declared dependencies for Python model serving."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion

from poolhouse.installed import declared


def mlx_requirements() -> list[str]:
    """Return the serving dependencies from this app's speculative engine extra."""
    requirements = declared().get('spec', [])
    if not requirements:
        raise OSError('The installed app has no MLX serving dependency contract')
    return requirements


def missing_mlx(packages: dict[str, str]) -> list[str]:
    """Return absent or incompatible declared MLX serving dependencies."""
    missing = []
    for text in mlx_requirements():
        required = Requirement(text)
        if required.marker and not required.marker.evaluate({'extra': 'spec'}):
            continue
        found = packages.get(canonicalize_name(required.name))
        try:
            ready = bool(found and required.specifier.contains(found, prereleases=True))
        except InvalidVersion:
            ready = False
        if not ready:
            missing.append(str(required))
    return sorted(missing)


def installed_mlx() -> bool:
    """Return whether the current interpreter satisfies the MLX serving contract."""
    packages = {}
    try:
        for text in mlx_requirements():
            required = Requirement(text)
            if required.marker and not required.marker.evaluate({'extra': 'spec'}):
                continue
            try:
                packages[canonicalize_name(required.name)] = version(required.name)
            except PackageNotFoundError:
                continue
        return not missing_mlx(packages)
    except (OSError, ValueError):
        return False
