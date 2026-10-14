"""Every name `docs/verify_release.py` imports from poolhouse still exists; a check that
cannot import its subject reports FAIL for a feature that works."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import CodeType, FunctionType

import pytest

VERIFIER = Path(__file__).resolve().parents[1] / "docs" / "verify_release.py"


def imported() -> list[tuple[int, str, str]]:
    """``(line, module, name)`` for every poolhouse name the verifier imports."""
    out = []
    nodes = (node for path in (VERIFIER, VERIFIER.with_name("verify_services.py"))
             for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))))
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("poolhouse"):
            out.extend((node.lineno, node.module or "", alias.name) for alias in node.names)
        elif isinstance(node, ast.Import):
            out.extend((node.lineno, alias.name, "") for alias in node.names
                       if alias.name.startswith("poolhouse"))
    return out


def test_the_verifier_imports_something_from_the_library():
    assert len(imported()) > 20, "the verifier stopped reaching into poolhouse"


@pytest.mark.parametrize("line,module,name",
                         imported(), ids=lambda v: str(v) if isinstance(v, str) else None)
def test_a_name_the_verifier_imports_still_exists(line, module, name):
    try:
        held = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if (exc.name or "").startswith("poolhouse"):
            pytest.fail(f"verify_release.py:{line} imports {module}, which is gone")
        pytest.skip(f"{module} needs {exc.name}, which is not installed here")
    if name:
        assert hasattr(held, name), (
            f"verify_release.py:{line} imports {name} from {module}, which no longer has it")


def test_offline_setup_checks_use_random_keys_and_group_scoped_join_authentication(tmp_path):
    tree = ast.parse(VERIFIER.read_text(encoding='utf-8'))
    ran = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.decorator_list:
            continue
        decorator = node.decorator_list[0]
        if not isinstance(decorator, ast.Call) or not decorator.args \
                or not isinstance(decorator.args[0], ast.Constant) or decorator.args[0].value != 'Setup':
            continue
        claim = decorator.args[1].value
        if 'two groups' in claim:
            continue  # This check requires live UDP; the offline checks do not.
        node.decorator_list = []
        scope = {'TMP': tmp_path, 'WORDS': 'correct horse battery staple'}
        module = compile(ast.Module(body=[node], type_ignores=[]), str(VERIFIER), 'exec')
        code = next(value for value in module.co_consts if isinstance(value, CodeType))
        assert isinstance(FunctionType(code, scope)(), str)
        ran.append(claim)
    assert len(ran) == 3


def service_checks(tmp_path):
    import importlib.util

    path = VERIFIER.with_name('verify_services.py')
    spec = importlib.util.spec_from_file_location('verify_services', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    checks = []

    def collect(area, claim, *, network=False):
        def keep(fn):
            checks.append((area, claim, network, fn))
            return fn
        return keep

    module.register(collect, tmp_path, lambda: 0)
    return checks


def test_service_check_extraction_preserves_order_and_network_markers(tmp_path):
    checks = service_checks(tmp_path)
    assert [area for area, _, _, _ in checks] == ['Models'] * 6 + ['Chat'] * 3
    assert [network for _, _, network, _ in checks] == [False] * 4 + [True] * 2 + [False] * 3
    source = VERIFIER.read_text()
    assert source.index('register(check, TMP, free_port)') < source.index('@check("Updating"')


@pytest.mark.parametrize('size', [2**30, 0, 3 * 2**30])
def test_catalogue_verification_uses_pinned_public_metadata(tmp_path, monkeypatch, size):
    from poolhouse.fleet import catalogue
    from poolhouse.hub import remote

    calls = []
    ref = remote.Ref('sample/model', 'model.gguf', commit='a' * 40)
    pick = catalogue.Suggestion('model', 'hf:sample/model/model.gguf', 1.0, 'text')
    monkeypatch.setattr(catalogue, 'SUGGESTED', [pick])

    def parse(text):
        calls.append(('parse', text))
        return remote.Ref('sample/model', 'model.gguf')

    def resolve(parsed, auth):
        assert parsed.commit == '' and auth == ''
        calls.append(('resolve', parsed.repo))
        return ref

    def listing(repo, revision, auth):
        assert (repo, revision, auth) == (ref.repo, ref.commit, '')
        calls.append(('listing', revision))
        return [remote.RemoteFile('model.gguf', size)]

    actual_members = remote.members

    def members(files, selected):
        assert selected == ref
        calls.append(('members', selected.commit))
        return actual_members(files, selected)

    for name, fn in [('parse', parse), ('resolve', resolve), ('listing', listing), ('members', members)]:
        monkeypatch.setattr(remote, name, fn)
    fn = next(fn for _, claim, _, fn in service_checks(tmp_path) if 'pinned Hub commit' in claim)
    if size == 2**30:
        assert fn() == '1 available: model'
    else:
        with pytest.raises(AssertionError):
            fn()
    assert calls == [('parse', pick.ref), ('resolve', ref.repo), ('listing', ref.commit), ('members', ref.commit)]
