"""Package contract checks run against isolated metadata."""

import ast
import importlib.util
from pathlib import Path
from types import CodeType, FunctionType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('verify_packaging', ROOT / 'docs/verify_packaging.py')
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture(tmp_path, before='', after=''):
    source = (ROOT / 'pyproject.toml').read_text()
    assert not before or before in source
    (tmp_path / 'pyproject.toml').write_text(source.replace(before, after))
    for name in ('LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md'):
        (tmp_path / name).write_text('fixture')
    return tmp_path


def test_current_package_contract():
    assert 'cryptography' in MODULE.verify(ROOT)


@pytest.mark.parametrize('before,after', [
    ('"cryptography>=50",', ''),
    ('"cryptography>=50",', '"cryptography>=49",'),
    ('"ladybug>=0.20.4,<0.21",', '"ladybug>=0.20.4",'),
    ("sys_platform == 'win32'", "sys_platform != 'darwin'"),
    ('"qrcode>=8.2",', '"qrcode>=8.2; python_version < \'3.14\'",'),
    ('"cryptography>=50",', '"cryptography>=50", "cryptography>=50",'),
    ('"cryptography>=50",', '"cryptography @ https://invalid.example/archive",'),
    ('"cryptography>=50",', '"cryptography[extra]>=50",'),
    ('requires-python = ">=3.12"', 'requires-python = ">=3.11"'),
    ('license = "Apache-2.0"', 'license = "MIT"'),
    ('"safetensors>=0.4"', '"safetensors>=0.3"'),
    ('agents,train,gguf,graph,store,scrape,vision,pdf,testing,telemetry,plot,fleet-tls,fleet-update,fleet-onboard',
     'agents,train'),
])
def test_invalid_contract_is_refused(tmp_path, before, after):
    with pytest.raises(AssertionError):
        MODULE.verify(fixture(tmp_path, before, after))


def test_equivalent_requirement_spellings(tmp_path):
    root = fixture(tmp_path, '"cryptography>=50",', '"Cryptography >= 50.0",')
    path = root / 'pyproject.toml'
    path.write_text(path.read_text().replace('ladybug>=0.20.4,<0.21', 'Ladybug <0.21, >=0.20.4'))
    assert 'cryptography' in MODULE.verify(root)


def test_missing_notice_is_refused(tmp_path):
    root = fixture(tmp_path)
    (root / 'NOTICE').unlink()
    with pytest.raises(AssertionError):
        MODULE.verify(root)


def test_malformed_requirement_is_refused(tmp_path):
    from packaging.requirements import InvalidRequirement

    root = fixture(tmp_path, '"cryptography>=50",', '"cryptography => 50",')
    with pytest.raises(InvalidRequirement):
        MODULE.verify(root)


def test_release_check_executes_package_contract(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, 'verify_packaging', MODULE)
    tree = ast.parse((ROOT / 'docs/verify_release.py').read_text())
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and any(isinstance(item, ast.Call) and len(item.args) == 2
                       and isinstance(item.args[1], ast.Constant)
                       and item.args[1].value == 'base requirements and optional extras match the package contract'
                       for item in node.decorator_list)]
    assert len(matches) == 1
    node = matches[0]
    node.decorator_list = []
    compiled = compile(ast.Module(body=[node], type_ignores=[]), 'verifier', 'exec')
    code = next(item for item in compiled.co_consts if isinstance(item, CodeType))
    fn = FunctionType(code, {'REPO': fixture(tmp_path)})
    assert 'cryptography' in fn()
    path = tmp_path / 'pyproject.toml'
    path.write_text(path.read_text().replace('"cryptography>=50",', ''))
    with pytest.raises(AssertionError):
        fn()
