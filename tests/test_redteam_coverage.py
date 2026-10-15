"""Every surface where outside input reaches a model, a tool, a process, a path or the network
has a row in the coverage map, every test the map names exists, and the uncovered count only
falls. The checks are the ones `scripts/redteam_coverage.py --check` runs."""

from __future__ import annotations

import ast
import importlib.util
import sys
from functools import cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@cache
def coverage():
    spec = importlib.util.spec_from_file_location("redteam_coverage", REPO / "scripts/redteam_coverage.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["redteam_coverage"] = module
    spec.loader.exec_module(module)
    return module


def test_class_method_references_match_the_exact_ast_class(tmp_path, monkeypatch):
    cov = coverage()
    monkeypatch.setattr(cov, 'ROOT', tmp_path)
    (tmp_path / 'test_members.py').write_text(
        'class TestFirst:\n    def test_acceptance(self): pass\n'
        'class TestSecond:\n    def test_other(self): pass\n'
        'async def test_async(): pass\n')
    assert not cov.ref_problems('native', 'test_members.py::TestFirst::test_acceptance')
    assert not cov.ref_problems('native', 'test_members.py::test_async')
    assert cov.ref_problems('native', 'test_members.py::TestSecond::test_acceptance')
    assert cov.ref_problems('native', 'test_members.py::TestFirst::test_missing')
    assert cov.ref_problems('native', 'test_members.py::test_acceptance')


@cache
def state():
    cov = coverage()
    found = cov.discover()
    owner, problems = cov.resolve(found, cov.load_map())
    return found, owner, problems, cov.rows(found, owner)


def test_the_tree_passes_the_gate():
    found, owner, problems, table = state()
    failures = coverage().check(found, table, owner, problems)
    assert failures == [], "\n".join(failures)


def test_every_kind_of_surface_is_found():
    kinds = {row["kind"] for row in state()[3]}
    assert kinds >= {"route", "handler", "mcp-tool", "chat-tool", "slash-command", "cli", "spawn",
                     "listener", "egress", "human", "desktop", "keystore", "context", "parser"}


def test_packaged_simulation_and_desktop_boundaries_are_inventoried():
    found = state()[0]
    assert {
        "desktop:app/src-tauri/capabilities/main.json",
        "spawn:scripts/test-on-linux",
        "spawn:gym/transport.py:Process.start",
        "route:fleet/gym_recording_routes.py:/ui/gym/recordings*",
        "route:workspace/fleet_routes.py:/ui/board/*",
        "route:fleet/routes.py:/ui/session",
        "route:fleet/setup_recovery_routes.py:/ui/setup/recovery",
    } <= found.keys()


def test_a_new_surface_without_a_row_fails_and_the_message_says_what_to_add():
    cov = coverage()
    found, _, _, _ = state()
    fresh = dict(found)
    cov.add(fresh, "route", "fleet/api.py:/brand-new", "fleet/api.py:Handler.do_GET")
    owner, problems = cov.resolve(fresh, {"group": []})
    failures = cov.check(fresh, cov.rows(fresh, owner), owner, problems)
    mine = [f for f in failures if "route:fleet/api.py:/brand-new" in f]
    assert mine and "coverage-map.toml" in mine[0]
    assert '[[group]]\nmatch = ["route:fleet/api.py:/brand-new"]' in mine[0]
    assert "tests = [" in mine[0]


def test_a_test_that_no_longer_exists_fails():
    cov = coverage()
    found, _, _, _ = state()
    table = {"group": [{"match": ["*"], "status": "covered",
                        "tests": ["tests/test_redteam_coverage.py::test_gone_for_good",
                                  "tests/test_not_a_file.py"]}]}
    _, problems = cov.resolve(found, table)
    assert any("test_gone_for_good" in p for p in problems)
    assert any("test_not_a_file.py does not exist" in p for p in problems)


def test_a_group_that_matches_nothing_or_miscounts_fails():
    cov = coverage()
    found, _, _, _ = state()
    table = {"group": [{"match": ["route:nothing/*"], "status": "n/a", "note": "x"},
                       {"match": ["mcp-tool:*"], "status": "n/a", "note": "x", "count": 1}]}
    _, problems = cov.resolve(found, table)
    assert any("matches no surface" in p for p in problems)
    assert any("expected 1 surfaces" in p for p in problems)


@pytest.mark.parametrize("group,needle", [
    ({"match": ["*"], "status": "covered"}, "needs at least one test"),
    ({"match": ["*"], "status": "uncovered"}, "needs a note"),
    ({"match": ["*"], "status": "n/a"}, "needs a note"),
    ({"match": ["*"], "status": "fine", "note": "x"}, "status must be one of"),
])
def test_a_row_must_say_why(group, needle):
    assert any(needle in p for p in coverage().group_problems(group, 1))


def test_the_uncovered_count_may_only_fall(monkeypatch):
    cov = coverage()
    found, owner, problems, table = state()
    held = cov.totals(table)
    monkeypatch.setattr(cov, "ratchet", lambda: {"uncovered": held["uncovered"] - 1,
                                                 "partial": held["partial"]})
    failures = cov.check(found, table, owner, problems)
    assert any("uncovered surfaces rose" in f for f in failures)
    monkeypatch.setattr(cov, "ratchet", lambda: {"uncovered": held["uncovered"] + 1,
                                                 "partial": held["partial"]})
    assert any("uncovered fell" in f for f in cov.check(found, table, owner, problems))


def test_the_written_map_is_the_one_the_tree_produces():
    cov = coverage()
    assert cov.OUT.read_text(encoding="utf-8") == cov.render(state()[3])


@pytest.mark.parametrize("call", ["json.load(handle)", "json.loads(text)"])
def test_json_stream_and_text_parsers_remain_inventoried(call):
    found = {}
    coverage().find_parsers(found, "src/poolhouse/files.py", ast.parse(call))
    assert "parser:files.py" in found


def test_extracted_private_integer_map_parser_remains_inventoried():
    found = {}
    coverage().find_parsers(found, "src/poolhouse/private_counters.py", ast.parse("json.loads(value)"))
    assert "parser:private_counters.py" in found


@pytest.mark.parametrize("source", [
    "from poolhouse.http import Options; value = Options(timeout=2)",
    "from poolhouse.http import Retry; value = Retry(tries=2)",
    "from poolhouse.http import Options as Delivery; value = Delivery(timeout=2)",
    "import poolhouse.http as transport; value = transport.Options(timeout=2)",
])
def test_http_data_constructors_do_not_create_egress_surfaces(source):
    found = {}
    coverage().find_calls(found, "src/poolhouse/fixture.py", ast.parse(source))
    assert found == {}


@pytest.mark.parametrize("call", [
    "http.open_stream(url)", "http.request_bytes(url)", "http.request_json(url)",
    "http.unfamiliar_transport(url)", "urllib.request.urlopen(url)",
    "socket.create_connection(address)",
])
def test_http_data_in_a_function_does_not_hide_its_transport(call):
    source = ("import poolhouse.http as http\nimport urllib.request\nimport socket\n"
              "def send():\n    options = http.Options(timeout=2)\n    " + call)
    found = {}
    coverage().find_calls(found, "src/poolhouse/fixture.py", ast.parse(source))
    assert set(found) == {"egress:fixture.py:send"}


def test_transport_added_to_a_constructor_is_still_inventoried():
    source = ("from poolhouse.http import request_bytes\n"
              "class Options:\n    def __post_init__(self):\n        request_bytes(url)")
    found = {}
    coverage().find_calls(found, "src/poolhouse/fixture.py", ast.parse(source))
    assert set(found) == {"egress:fixture.py:Options.__post_init__"}


def test_http_data_construction_does_not_open_a_connection(monkeypatch):
    from poolhouse import http

    def network(*_args, **_kwargs):
        pytest.fail("request data construction reached the network")
    monkeypatch.setattr(http, "open_stream", network)
    monkeypatch.setattr(http, "_open", network)
    monkeypatch.setattr(http.urllib.request, "urlopen", network)
    settings = http.Options(headers={"X-Fixture": "yes"}, retry=http.Retry(tries=2))
    assert dict(settings.headers) == {"X-Fixture": "yes"}
    assert settings.retry.tries == 2


@pytest.mark.parametrize("source", [
    "from poolhouse.http import Options\nOptions = arbitrary_transport\nOptions(url)",
    "from poolhouse.http import Options\ndef send(Options):\n    Options(url)",
    "import poolhouse.http as delivery\ndelivery = arbitrary_transport\ndelivery.Options(url)",
    "import poolhouse.http as delivery\ndelivery.Options = arbitrary_transport\ndelivery.Options(url)",
    "from elsewhere import Options\nfrom poolhouse.http import Options\nOptions(url)",
    "from poolhouse.http import Options\ntry: pass\nexcept Exception as Options: Options(url)",
    "from poolhouse.http import Options\nmatch value:\n    case {'x': Options}: Options(url)",
    "from poolhouse.http import Options\nmatch value:\n    case [*Options]: Options(url)",
    "from poolhouse.http import Options\nmatch value:\n    case {**Options}: Options(url)",
    "from poolhouse.http import Options\n(Options := arbitrary_transport)\nOptions(url)",
    "from poolhouse.http import Options\n[Options(url) for Options in transports]",
    "from poolhouse.http import Options\ndel Options\nOptions(url)",
    "from poolhouse.http import Options\ndef Options(): pass\nOptions(url)",
    "from poolhouse.http import Options\nclass Options: pass\nOptions(url)",
    "import poolhouse.http as http\nsetattr(http, 'Options', transport)\nhttp.Options(url)",
    "import poolhouse.http as http\ndelattr(http, 'Options')\nhttp.Options(url)",
    "from poolhouse.http import Options\nsetattr(Options, '__new__', transport)\nOptions(url)",
    "from poolhouse.http import Options\ndelattr(Options, '__new__')\nOptions(url)",
    "import poolhouse.http as http\nmonkeypatch.setattr(http, 'Options', transport)\nhttp.Options(url)",
    "from poolhouse.http import Options\nmonkeypatch.setattr('poolhouse.http.Options', transport)\nOptions(url)",
    "from poolhouse.http import Options\nmonkeypatch.delattr('poolhouse.http.Options')\nOptions(url)",
    "from poolhouse.http import Options\nsetattr(target(), 'Options', transport)\nOptions(url)",
    "from poolhouse.http import Options\nobject.__setattr__(Options, '__new__', transport)\nOptions(url)",
    "import poolhouse.http as http\nhttp.__dict__['Options'] = transport\nhttp.Options(url)",
    "import poolhouse.http as http\ndel http.__dict__['Options']\nhttp.Options(url)",
    "from poolhouse.http import Options\nglobals()['Options'] = transport\nOptions(url)",
    "from poolhouse.http import Options\nlocals()['Options'] = transport\nOptions(url)",
    "from poolhouse.http import Options\nfrom builtins import setattr as change\nchange(Options, '__new__', transport)\nOptions(url)",
    "from poolhouse.http import Options\nfrom builtins import delattr as remove\nremove(Options, '__new__')\nOptions(url)",
    "from poolhouse.http import Options\nexec('Options = transport')\nOptions(url)",
    "from poolhouse.http import Options\neval(expression)\nOptions(url)",
    "from poolhouse.http import Options\nglobals().update(Options=transport)\nOptions(url)",
    "import poolhouse.http as http\nhttp.__dict__.update(Options=transport)\nhttp.Options(url)",
    "from poolhouse.http import Options\nvars(Options).update(__new__=transport)\nOptions(url)",
])
def test_rebound_or_shadowed_constructor_names_remain_conservative(source):
    found = {}
    coverage().find_calls(found, "src/poolhouse/fixture.py", ast.parse(source))
    assert found and all(row["kind"] == "egress" for row in found.values())


def test_delegated_relay_spawn_inventory_tracks_actual_canonical_calls():
    cov = coverage()
    source = REPO / 'src/poolhouse/coordination/relay.py'
    found = {}
    cov.find_relay_spawns(found, 'src/poolhouse/coordination/relay.py', ast.parse(source.read_text()))
    assert set(found) == {'spawn:coordination/relay.py:Forwarder.deliver'}
    moved = ast.parse('from poolhouse import platform as owned\ndef moved(argv):\n return owned.start_process(argv)\n')
    found = {}
    cov.find_relay_spawns(found, 'src/poolhouse/coordination/relay.py', moved)
    assert set(found) == {'spawn:coordination/relay.py:moved'}
    missing = ast.parse('def moved(argv):\n return argv\n')
    found = {}
    cov.find_relay_spawns(found, 'src/poolhouse/coordination/relay.py', missing)
    assert not found


def test_configured_system_one_transport_keeps_its_parser_and_egress_inventory():
    assert {"parser:client/systemone.py", "egress:client/systemone.py:SystemOne.ask"} <= set(state()[0])
