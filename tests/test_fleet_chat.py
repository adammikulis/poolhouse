"""Talking to a model, including from a machine that installed nothing to run one.

The test that matters here is the last one: a daemon with no model store, nothing
serving locally and no way to run a model still holds a conversation, because another
machine on the network is serving. That is the whole point of the fleet, and it is the
first thing a refactor would quietly break.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
from launch_support import browser
from test_fleet_ui import WORDS, Serving as UIServing

from poolhouse.fleet.api import Daemon, make_handler
from poolhouse.fleet.chat import Target, find, targets
from poolhouse.fleet.daemon import load_or_create_token
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.serving import Serving
from poolhouse.http import Options, Server, ServerError, open_stream
from poolhouse.testing.fakes import FakeLlamaServer, Served
from tests.keystore_support import counting  # noqa: F401
from tests.net_site import gguf_bytes
from tests.pool_support import a_keystore, join_pool_by_passphrase  # noqa: F401

PIECES = ["Hel", "lo", " there"]


def tmp_path_of(ui):
    return ui.files.parent





@pytest.fixture(autouse=True)
def the_passphrase_is_kept(a_keystore):  # noqa: F811
    """Joining stores the passphrase and signing in compares against it."""


@pytest.fixture(autouse=True)
def loopback_is_the_internet(monkeypatch):
    """The download test fetches from a server on this machine, which has to be named."""
    monkeypatch.setenv("POOLHOUSE_FETCH_ALLOW_HOSTS", "127.0.0.1")


@pytest.fixture(autouse=True)
def nobody_else_is_on_the_network(monkeypatch):
    """A join finds no machine to shake hands with, so it makes the pool."""
    from poolhouse.fleet.onboard import joining

    monkeypatch.setattr(joining, "find_joiners", lambda *a, **k: [])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def model_server():
    """A llama.cpp-shaped server that streams a reply a piece at a time."""
    fake = FakeLlamaServer(Served(model="qwen3-4b.gguf", pieces=tuple(PIECES), gap=0.05))
    try:
        yield fake.port
    finally:
        fake.close()


@pytest.fixture
def host(tmp_path, model_server):
    """A second machine: a real daemon proxying to a real model server."""
    root = tmp_path / "host"
    files = root / "files"
    files.mkdir(parents=True)
    key = join_pool_by_passphrase(WORDS, group="home", path=tmp_path / "host.key")
    token = load_or_create_token(root, key)
    serving = Serving(root / "serving.json")
    serving.register(model_server, ["qwen3-4b.gguf"])
    runner = JobRunner(root, files)
    port = _free_port()
    httpd = Server(
        ("127.0.0.1", port),
        make_handler(Daemon(runner, files, token, "host", serving=serving)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield {"name": "host", "base_url": f"http://127.0.0.1:{port}",
               "is_self": False, "device": {"serving": serving.public()}}
    finally:
        runner.shutdown()
        httpd.shutdown()
        httpd.server_close()


class TestPickingWhereToSend:
    def test_known_non_chat_local_and_member_targets_are_excluded(self):
        from poolhouse.fleet.serving import Served as LocalServed

        class Local:
            def live(self):
                return [LocalServed(port=9998, models=['embedding.gguf'], capabilities={'chat': False}),
                        LocalServed(port=9999, models=['future-decoder.gguf'])]

        member = {'base_url': 'http://127.0.0.1:12345', 'name': 'member', 'device': {'serving': [
            {'models': ['speech'], 'capabilities': {'chat': False}},
            {'models': ['remote-decoder'], 'capabilities': {'chat': True}}]}}
        assert [row.model for row in targets([member], Local())] == ['future-decoder.gguf', 'remote-decoder']
        found = targets([member], Local())
        assert found[0].public()['capabilities']['chat'] is None
        assert found[1].public()['capabilities']['chat'] is True

    def test_a_machine_serving_nothing_still_sees_a_members_model(self, host):
        found = targets([host], serving=None, token="t")
        assert [t.model for t in found] == ["qwen3-4b.gguf"]
        assert found[0].member == "host"
        assert found[0].local is False
        assert found[0].url.endswith("/infer/v1/chat/completions")

    def test_this_machine_is_preferred_over_a_member_holding_the_same_model(self, host):
        class Fake:
            def live(self):
                from poolhouse.fleet.serving import Served
                return [Served(port=9999, models=["qwen3-4b.gguf"])]

        found = targets([host], serving=Fake(), token="t")
        assert len(found) == 1
        assert found[0].local is True

    def test_it_never_offers_this_machines_own_beacon(self):
        me = {"name": "me", "base_url": "http://127.0.0.1:1", "is_self": True,
              "device": {"serving": [{"port": 1, "models": ["a.gguf"]}]}}
        assert targets([me], serving=None) == []

    def test_only_an_exact_advertised_model_is_selected(self, host):
        found = targets([host], serving=None, token="t")
        assert find(found, "qwen3-4b.gguf").model == "qwen3-4b.gguf"
        assert find(found, "qwen3") is None
        assert find(found, "") is None
        assert find(found, "nothing-like-this") is None
        assert find([], "anything") is None


class TestChattingThroughTheInterface:
    @pytest.fixture
    def bare(self, tmp_path, host):
        """A machine that installed nothing: no model store, nothing serving."""
        ui = UIServing(tmp_path / "bare", name="laptop")
        join_pool_by_passphrase(WORDS, group="home", path=ui.keyfile)
        ui.call("/ui/setup/join", method="POST",
                body={"passphrase": WORDS, "group": "home"})
        _, _, headers = ui.call("/ui/session", method="POST",
                                body={"passphrase": WORDS})
        assert ui.ui.models is None
        assert ui.ui.serving is None
        ui.ui.root = ui.files.parent
        ui.ui.settings.download_sources = "both"
        ui.ui._members = (time.time(), [host])
        try:
            yield ui, headers["Set-Cookie"].split(";")[0]
        finally:
            ui.close()

    def test_canonical_resource_dispatches_to_its_actual_same_named_server(self, bare):
        from types import SimpleNamespace

        from poolhouse.fleet.serving import Served as LocalServed

        ui, cookie = bare
        first = FakeLlamaServer(Served(model='shared.gguf', pieces=('first-resource',)))
        second = FakeLlamaServer(Served(model='shared.gguf', pieces=('second-resource',)))
        try:
            ui.ui.serving = SimpleNamespace(live=lambda: [
                LocalServed(port=first.port, models=['shared.gguf'], aliases=['/models/one/shared.gguf']),
                LocalServed(port=second.port, models=['shared.gguf'], aliases=['/models/two/shared.gguf'])])
            status, body, _ = ui.call('/ui/chat', method='POST', cookie=cookie,
                body={'model': '/models/two/shared.gguf', 'messages': [{'role': 'user', 'content': 'hello'}]})
            assert status == 200 and 'second-resource' in body['raw']
            assert not first.requests
            assert any(path.endswith('/chat/completions') for _method, path, _body in second.requests)
        finally:
            first.close()
            second.close()

    def test_it_lists_the_model_another_machine_is_serving(self, bare):
        ui, cookie = bare
        status, body, _ = ui.call("/ui/chat", cookie=cookie)
        assert status == 200
        assert [m["model"] for m in body["models"]] == ["qwen3-4b.gguf"]
        assert body["models"][0]["member"] == "host"

    def test_post_cannot_select_a_known_non_chat_server(self, bare):
        ui, cookie = bare
        member = ui.ui._members[1][0]
        member['device']['serving'][0]['capabilities'] = {'chat': False, 'embedding': True}
        status, body, _ = ui.call('/ui/chat', cookie=cookie)
        assert status == 200 and body['models'] == []
        status, body, _ = ui.call('/ui/chat', method='POST', cookie=cookie,
            body={'model': 'qwen3-4b.gguf', 'messages': [{'role': 'user', 'content': 'hello'}]})
        assert status == 503 and 'no machine' in body['error']

    def test_a_machine_with_nothing_installed_holds_a_conversation(self, bare):
        ui, cookie = bare
        status, raw, headers = ui.call(
            "/ui/chat", method="POST", cookie=cookie,
            body={"model": "qwen3-4b.gguf",
                  "messages": [{"role": "user", "content": "hi"}]})
        assert status == 200
        assert headers.get("X-Poolhouse-Member") == "host"
        text = raw["raw"] if "raw" in raw else json.dumps(raw)
        assert "Hel" in text and "there" in text

    @pytest.mark.parametrize('limit',[0,-1,True,1.5,'2048'])
    def test_invalid_output_budget_is_refused_before_generation(self, bare, limit):
        ui,cookie=bare
        status,body,_=ui.call('/ui/chat',method='POST',cookie=cookie,
            body={'model':'qwen3-4b.gguf','messages':[{'role':'user','content':'hi'}],'max_output_tokens':limit})
        assert status == 400 and 'positive integer' in body['error']

    def test_the_reply_arrives_as_it_is_generated(self, bare):
        """read(n) waits for n bytes and delivers the whole reply at once."""
        import urllib.request

        ui, cookie = bare
        req = urllib.request.Request(
            f"http://127.0.0.1:{ui.port}/ui/chat", method="POST",
            data=json.dumps({"model": "qwen3-4b.gguf",
                             "messages": [{"role": "user", "content": "hi"}]}).encode())
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Poolhouse-UI", "1")
        req.add_header("Cookie", cookie)

        began = time.monotonic()
        arrived = []
        with open_stream(req.full_url, data=req.data, method=req.get_method(), options=Options(headers=dict(req.header_items()), timeout=10)) as r:
            while True:
                block = r.read1(4096)
                if not block:
                    break
                arrived.append((time.monotonic() - began, block))

        assert len(arrived) > 1, "the whole reply came in one piece"
        spread = arrived[-1][0] - arrived[0][0]
        assert spread > 0.05, f"every piece arrived at once ({spread:.3f}s apart)"

    def test_what_was_said_is_kept(self, bare, tmp_path):
        from poolhouse.fleet.conversations import Conversations

        ui, cookie = bare
        ui.ui.conversations = Conversations(tmp_path / "bare" / "chats")
        status, made, _ = ui.call("/ui/conversations", method="POST", cookie=cookie, headers=browser(ui),
                                  body={"operation_id": "a" * 32, "model": "qwen3-4b.gguf"})
        assert status == 201

        ui.call("/ui/chat", method="POST", cookie=cookie,
                body={"conversation": made["id"], "model": "qwen3-4b.gguf",
                      "messages": [{"role": "user", "content": "hi"}]})

        # A fresh handle, as a restarted daemon would have.
        kept = Conversations(tmp_path / "bare" / "chats").get(made["id"])
        assert [m.role for m in kept.messages] == ["user", "assistant"]
        assert kept.messages[0].content == "hi"
        assert kept.messages[1].content == "".join(PIECES)
        assert kept.title == "hi"

    def test_chats_are_listed_and_searchable_over_the_interface(self, bare, tmp_path):
        from poolhouse.fleet.conversations import Conversations

        ui, cookie = bare
        ui.ui.conversations = Conversations(tmp_path / "bare" / "chats")
        _, a, _ = ui.call("/ui/conversations", method="POST", cookie=cookie, headers=browser(ui), body={"operation_id": "a" * 32})
        ui.ui.conversations.append(a["id"], "user", "about safetensors")
        _, b, _ = ui.call("/ui/conversations", method="POST", cookie=cookie, headers=browser(ui), body={"operation_id": "b" * 32})
        ui.ui.conversations.append(b["id"], "user", "about quantising")

        _, listed, _ = ui.call("/ui/conversations", cookie=cookie)
        assert len(listed["conversations"]) == 2
        assert "messages" not in listed["conversations"][0]

        _, hit, _ = ui.call("/ui/conversations?q=safetensors", cookie=cookie)
        assert [c["id"] for c in hit["conversations"]] == [a["id"]]

        status, one, _ = ui.call(f"/ui/conversations/{a['id']}", cookie=cookie)
        assert status == 200
        assert one["messages"][0]["content"] == "about safetensors"

        status, _, _ = ui.call(f"/ui/conversations/{a['id']}", method="DELETE",
                               cookie=cookie)
        assert status == 200
        _, left, _ = ui.call("/ui/conversations", cookie=cookie)
        assert [c["id"] for c in left["conversations"]] == [b["id"]]

    def test_asking_for_a_chat_that_is_not_there_is_a_404(self, bare, tmp_path):
        from poolhouse.fleet.conversations import Conversations

        ui, cookie = bare
        ui.ui.conversations = Conversations(tmp_path / "bare" / "chats")
        assert ui.call("/ui/conversations/nope", cookie=cookie)[0] == 404

    def test_a_machine_that_cannot_run_a_model_says_so_rather_than_breaking(
            self, bare, monkeypatch):
        """The install that cannot serve is the common one. It must still answer."""
        from poolhouse.fleet import routes as routes_mod

        ui, cookie = bare
        ui.ui.serving = Serving(tmp_path_of(ui) / "serving.json")
        monkeypatch.setattr(routes_mod, "_can_serve", lambda: False)

        status, body, _ = ui.call("/ui/serving", cookie=cookie)
        assert status == 200
        assert body["can_serve"] is False
        assert body["running"] == []

        status, body, _ = ui.call("/ui/serving", method="POST", cookie=cookie,
                                  body={"name": "anything.gguf"})
        assert status == 501
        assert "network can serve one" in body["error"]

    def test_a_daemon_with_no_serving_at_all_answers_the_route(self, bare):
        ui, cookie = bare
        assert ui.ui.serving is None
        status, body, _ = ui.call("/ui/serving", cookie=cookie)
        assert status == 501
        assert "run a model" in body["error"]

    @pytest.mark.slow

    def test_asking_for_a_model_answers_before_it_has_arrived(self, bare, tmp_path, monkeypatch):
        """A multi-gigabyte fetch must not be held open inside one request."""
        import os
        import time as clock

        from test_fleet_models import TestGettingInTheBackground

        from poolhouse.fleet.models import CHUNK, Downloads, Models

        payload = gguf_bytes() + os.urandom(2 * CHUNK)
        seen, _ = TestGettingInTheBackground().held(monkeypatch, tmp_path, payload)

        ui, cookie = bare
        models_dir = tmp_path / "bare" / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        ui.ui.models = Models([models_dir], models_dir)
        ui.ui.downloads = Downloads(ui.ui.models)
        try:
            began = clock.monotonic()
            status, body, _ = ui.call(
                "/ui/models", method="POST", cookie=cookie,
                body={"name": "big.gguf",
                      "source": "hf:unsloth/big-GGUF/big.gguf"})
            answered = clock.monotonic() - began

            assert status == 202, body
            assert body["state"] == "getting"
            assert answered < 1.0, f"the request waited {answered:.1f}s"

            saw_partial = False
            for _ in range(300):
                _, listing, _ = ui.call("/ui/models", cookie=cookie)
                rows = listing.get("getting") or []
                row = next((g for g in rows if g["id"] == body["id"]), None)
                if row is None:
                    break
                if 0 < row["done"] < row["total"]:
                    saw_partial = True
                    seen.set()
                if row["state"] != "getting":
                    assert row["state"] == "done", row.get("error") or row
                    break
                clock.sleep(0.05)
        finally:
            seen.set()

        assert saw_partial, "the screen could never show how far along it was"
        assert (models_dir / "big.gguf").read_bytes() == payload

    def test_a_model_already_here_answers_at_once(self, bare, tmp_path):
        from poolhouse.fleet.models import Downloads, Models

        ui, cookie = bare
        models_dir = tmp_path / "bare" / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        (models_dir / "here.gguf").write_bytes(b"x" * (2 * 1024 * 1024))
        ui.ui.models = Models([models_dir], models_dir)
        ui.ui.downloads = Downloads(ui.ui.models)

        status, body, _ = ui.call("/ui/models", method="POST", cookie=cookie,
                                  body={"name": "here.gguf"})
        assert status == 200
        assert body["name"] == "here.gguf"

    def test_with_nobody_serving_it_says_so_rather_than_failing(self, tmp_path):
        ui = UIServing(tmp_path / "alone", name="alone")
        try:
            ui.call("/ui/setup/join", method="POST",
                    body={"passphrase": WORDS, "group": "home"})
            _, _, headers = ui.call("/ui/session", method="POST",
                                    body={"passphrase": WORDS})
            cookie = headers["Set-Cookie"].split(";")[0]
            ui.ui._members = (time.time(), [])
            status, body, _ = ui.call(
                "/ui/chat", method="POST", cookie=cookie,
                body={"messages": [{"role": "user", "content": "hi"}]})
            assert status == 503
            assert "serving a model" in body["error"]
        finally:
            ui.close()



class TestAnsweringToSeveralPools:
    """The bearer token is derived from a pool key, so a machine in two pools
    has two tokens and must accept either."""

    def daemon(self, tmp_path, anchor):
        from poolhouse.fleet.api import Daemon, make_handler
        from poolhouse.fleet.daemon import load_or_create_token
        from poolhouse.fleet.discovery import derive_token, memberships
        from poolhouse.fleet.jobs import JobRunner

        root = tmp_path / "traind"
        files = root / "files"
        files.mkdir(parents=True, exist_ok=True)
        rows = memberships(anchor)
        token = load_or_create_token(root, rows[0].key)
        runner = JobRunner(root, files)
        port = _free_port()
        httpd = Server(
            ("127.0.0.1", port),
            make_handler(Daemon(runner, files, token, "box",
                         pool_key_path=anchor,
                         tokens=lambda: {derive_token(m.key)
                                         for m in memberships(anchor)})))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{port}", runner, httpd

    def ask(self, base, token):
        # /health answers anyone with whether the daemon is there. /jobs wants a request
        # signed with the pool's secret, which is what this is about.
        from poolhouse.http import build_request

        req = build_request(f"{base}/jobs", token=token)
        try:
            with open_stream(req.full_url, options=Options(headers=dict(req.header_items()), timeout=5)) as r:
                return r.status
        except ServerError as exc:
            return exc.status

    def test_either_pool_can_reach_it(self, tmp_path):
        from poolhouse.fleet.discovery import derive_token
        from tests.pool_support import join

        anchor = tmp_path / "pool.key"
        join(WORDS, group="home", path=anchor)
        join("a different set of words here", group="work", path=anchor)
        rows = {m.group: m.key for m in __import__(
            "poolhouse.fleet.discovery", fromlist=["memberships"]).memberships(anchor)}

        base, runner, httpd = self.daemon(tmp_path, anchor)
        try:
            assert self.ask(base, derive_token(rows["home"])) == 200
            assert self.ask(base, derive_token(rows["work"])) == 200, (
                "the second pool was refused")
            assert self.ask(base, "not-a-real-token") == 401
        finally:
            runner.shutdown()
            httpd.shutdown()
            httpd.server_close()

    def test_a_pool_it_left_is_refused(self, tmp_path):
        from poolhouse.fleet.discovery import derive_token, leave, memberships
        from tests.pool_support import join

        anchor = tmp_path / "pool.key"
        join(WORDS, group="home", path=anchor)
        join("a different set of words here", group="work", path=anchor)
        gone = derive_token({m.group: m.key
                             for m in memberships(anchor)}["work"])

        base, runner, httpd = self.daemon(tmp_path, anchor)
        try:
            assert self.ask(base, gone) == 200
            leave("work", anchor)
            assert self.ask(base, gone) == 401, "it still answered a pool it left"
        finally:
            runner.shutdown()
            httpd.shutdown()
            httpd.server_close()


def test_exact_local_paths_disambiguate_identical_model_names_without_guessing():
    first = Target('shared.gguf', 'http://127.0.0.1:1/v1/chat/completions', alias='/models/one/shared.gguf')
    second = Target('shared.gguf', 'http://127.0.0.1:2/v1/chat/completions', alias='/models/two/shared.gguf')
    assert find([first, second], second.alias) is second
    assert find([first, second], first.alias) is first
    assert find([first, second], 'shared.gguf') is None
    assert find([first, second], '/models/missing/shared.gguf') is None
    assert find([first, second], 'shared') is None
    assert find([first, second], '') is None


def test_alias_model_collision_and_duplicate_exact_identity_are_refused():
    local = Target('shared.gguf', 'http://127.0.0.1:1/v1/chat/completions', alias='/models/one/shared.gguf')
    remote = Target(local.alias, 'http://127.0.0.1:2/infer/v1/chat/completions', member='member-one')
    assert find([local, remote], local.alias) is None
    assert find([remote, local], local.alias) is None
    duplicate = Target('other.gguf', 'http://127.0.0.1:3/v1/chat/completions', alias=local.alias)
    assert find([local, duplicate], local.alias) is None
