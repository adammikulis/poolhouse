"""Offline checks for package dependency metadata."""

import tomllib
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

CORE = {
    'cryptography': '>=50', 'ladybug': '>=0.20.4,<0.21', 'packaging': '>=24.2',
    'psutil': '>=7.2.2', 'qrcode': '>=8.2', 'pywin32': '',
}
ALL = {
    'agents', 'train', 'gguf', 'graph', 'store', 'scrape', 'vision', 'pdf',
    'testing', 'telemetry', 'plot', 'fleet-tls', 'fleet-update', 'fleet-onboard',
}


def requirements(values: list[str]) -> dict[str, Requirement]:
    """Parse distinct requirement names."""
    parsed = [Requirement(value) for value in values]
    result = {canonicalize_name(item.name): item for item in parsed}
    if not (len(result) == len(parsed)):
        raise AssertionError('duplicate requirements')
    return result


def verify(root: Path) -> str:
    """Check the checkout's package metadata and return its core dependencies."""
    with (root / 'pyproject.toml').open('rb') as source:
        meta = tomllib.load(source)['project']
    if not (meta['name'] == 'poolhouse'):
        raise AssertionError('package metadata contract')
    if not (SpecifierSet(meta['requires-python']) == SpecifierSet('>=3.12')):
        raise AssertionError('package metadata contract')
    if not (meta['license'] == 'Apache-2.0'):
        raise AssertionError('package metadata contract')
    if not (set(meta['license-files']) == {'LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'}):
        raise AssertionError('package metadata contract')
    if not (all((root / name).is_file() for name in meta['license-files'])):
        raise AssertionError('package metadata contract')
    core = requirements(meta['dependencies'])
    if not (core.keys() == CORE.keys()):
        raise AssertionError('base dependency names')
    for name, item in core.items():
        if not (not item.url and (not item.extras)):
            raise AssertionError(name)
        if not (item.specifier == SpecifierSet(CORE[name])):
            raise AssertionError(name)
        for platform in ('darwin', 'linux', 'win32'):
            for version in ('3.12', '3.13', '3.14', '3.15'):
                env = {**default_environment(), 'sys_platform': platform,
                       'python_version': version, 'python_full_version': version + '.0'}
                enabled = item.marker is None or item.marker.evaluate(env)
                if not (enabled == (name != 'pywin32' or platform == 'win32')):
                    raise AssertionError(name)
    extras = meta['optional-dependencies']
    train = requirements(extras['train'])
    if not (train.keys() == {'numpy', 'safetensors'}):
        raise AssertionError('package metadata contract')
    for name, constraint in [('numpy', '>=1.24'), ('safetensors', '>=0.4')]:
        item = train[name]
        if not (item.specifier == SpecifierSet(constraint)):
            raise AssertionError(name)
        if not (not item.marker and (not item.url) and (not item.extras)):
            raise AssertionError(name)
    selected = requirements(extras['all'])
    if not (selected.keys() == {'poolhouse'}):
        raise AssertionError('package metadata contract')
    bundle = selected['poolhouse']
    if not (bundle.extras == ALL and extras.keys() >= ALL):
        raise AssertionError('package metadata contract')
    if not (not bundle.marker and (not bundle.url) and (not bundle.specifier)):
        raise AssertionError('package metadata contract')
    return ', '.join(sorted(core)) + '; curated all extra'
