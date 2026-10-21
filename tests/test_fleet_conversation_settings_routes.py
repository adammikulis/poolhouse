"""Saved conversation settings through the guarded daemon API."""
from __future__ import annotations

import pytest
from launch_support import browser, sign_in, signed
from test_fleet_ui import Serving

from poolhouse.fleet.conversations import Conversations


@pytest.fixture
def conversation_api(tmp_path):
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.root = served.files.parent
    signed(served)
    call = served.call
    def local(path, **options):
        options["headers"] = {**({} if path.startswith("/ui/launch/") else browser(served)), **options.get("headers", {})}
        return call(path, **options)
    served.call = local
    served.ui.conversations = Conversations(tmp_path / "chats")
    try:
        yield served
    finally:
        served.close()


def test_settings_update_preserves_title_and_messages(conversation_api):
    served = conversation_api
    status, made, _ = served.call("/ui/conversations", method="POST", body={"operation_id": "a" * 32, "title": "Code review", "model": "small",
                                 "settings": {"project": "/tmp/example", "effort": "low"}})
    assert status == 201
    served.ui.conversations.append(made["id"], "user", "Review this code")
    status, updated, _ = served.call(f"/ui/conversations/{made['id']}", method="POST",
                                     body={"model": "large", "settings": {"temperature": .4}})
    assert status == 200
    assert updated["title"] == "Code review"
    assert updated["model"] == "large"
    assert updated["settings"]["project"] == "/tmp/example"
    assert updated["settings"]["temperature"] == .4
    assert updated["messages"][0]["content"] == "Review this code"


def test_defaults_endpoint_exposes_the_persisted_settings_source(conversation_api):
    served = conversation_api
    status, response, _ = served.call("/ui/conversations/defaults")
    assert status == 200
    assert response["settings"]["harness"] == ""
    assert response["settings"]["context"] == 0
    assert served.call("/ui/conversations/defaults", ui_header=False)[0] == 403
    assert served.call("/ui/conversations/defaults", headers={"Origin": "https://foreign.invalid"})[0] == 403


@pytest.mark.parametrize("body", [{"title": "Changed", "settings": {"temperature": True}},
                                  {"settings": {"mode": "unknown"}}, {"settings": []},
                                  {"model": 42}, {"model": None}, {"settings": None}, {"unknown": "field"}, []])
def test_invalid_updates_return_400_without_changing_the_chat(conversation_api, body):
    served = conversation_api
    made = served.ui.conversations.start(model="small", title="Kept")
    status, response, _ = served.call(f"/ui/conversations/{made.id}", method="POST", body=body)
    assert status == 400
    assert response["error"]
    kept = served.ui.conversations.get(made.id)
    assert kept.title == "Kept"
    assert kept.model == "small"
    assert kept.settings == made.settings


def test_conversation_mutations_require_ui_authority(conversation_api):
    served = conversation_api
    made = served.ui.conversations.start(title="Protected")
    for method, payload in (("POST", {"title": "Changed"}), ("DELETE", None)):
        path = f"/ui/conversations/{made.id}"
        assert served.call(path, method=method, body=payload, ui_header=False)[0] == 403
        assert served.call(path, method=method, body=payload,
                           headers={"Origin": "https://foreign.invalid"})[0] == 403
    assert served.ui.conversations.get(made.id).title == "Protected"


@pytest.mark.parametrize("limit", [512, None])
def test_new_conversations_use_central_output_defaults_and_keep_snapshots(conversation_api, limit):
    served = conversation_api
    _, first, _ = served.call("/ui/conversations", method="POST", body={"operation_id": "a" * 32, "title": "Original defaults"})
    _, response, _ = served.call("/ui/settings", method="POST", body={"chat_max_output_tokens": limit})
    assert "error" not in response
    _, defaults, _ = served.call("/ui/conversations/defaults")
    assert defaults["settings"]["max_output_tokens"] == limit
    assert defaults["settings"]["effort"] == "off"
    assert defaults["settings"]["context"] == 0
    _, second, _ = served.call("/ui/conversations", method="POST", body={"operation_id": "b" * 32, "title": "Central defaults"})
    assert second["settings"]["max_output_tokens"] == limit
    _, original, _ = served.call(f"/ui/conversations/{first['id']}")
    assert original["settings"]["max_output_tokens"] == first["settings"]["max_output_tokens"]
    _, explicit, _ = served.call("/ui/conversations", method="POST", body={"operation_id": "c" * 32, "settings": {"max_output_tokens": 123, "effort": "high"}})
    assert explicit["settings"]["max_output_tokens"] == 123
    assert explicit["settings"]["effort"] == "high"


@pytest.mark.parametrize("mode", ["chat", "coding"])
def test_removed_mode_is_refused_without_rewriting_saved_state(conversation_api, mode):
    served = conversation_api
    made = served.ui.conversations.start(model="fixed-model")
    status, response, _ = served.call(f"/ui/conversations/{made.id}", method="POST", body={"settings": {"mode": mode}})
    assert status == 400 and response["error"]
    current = served.ui.conversations.get(made.id)
    assert current.model == "fixed-model" and current.settings["harness"] == ""
    assert "mode" not in current.settings and current.messages == []


OPERATION = "a" * 32

def test_guarded_routes_attach_once_reconcile_and_reject_missing_key(conversation_api): 
    app = conversation_api
    payload = {"operation_id": OPERATION, "model": "model-a"}
    status, first, _ = app.call("/ui/conversations", method="POST", body=payload)
    assert status == 201
    assert app.call("/ui/conversations", method="POST", body=payload)[:2] == (201, first)
    assert app.call("/ui/conversations/operations/" + OPERATION)[:2] == (200, first)
    assert app.call("/ui/conversations/operations/" + "b" * 32)[0] == 404
    assert app.call("/ui/conversations", method="POST", body={})[0] == 400
    assert app.call("/ui/conversations", method="POST", body={**payload, "model": "other"})[0] == 409
    foreign = sign_in(app)
    assert app.call("/ui/conversations/operations/" + OPERATION, cookie=foreign)[0] == 404
    assert app.call("/ui/conversations", method="POST", body=payload, cookie=foreign)[0] == 409
    assert len(app.ui.conversations.all()) == 1


def test_uncredentialed_and_expired_sessions_cannot_create_or_lookup(conversation_api): 
    from poolhouse.fleet.session import parse_cookie

    app = conversation_api
    cookie = app.ui.sessions.cookie_header(app.ui.sessions.open("uncredentialed"))
    payload = {"operation_id": OPERATION}
    assert Serving.call(app, "/ui/conversations", method="POST", body=payload,
                        headers=browser(app), cookie=cookie)[0] == 403
    app.ui.sessions.close(parse_cookie(app.cookie))
    assert app.call("/ui/conversations", method="POST", body=payload)[0] == 401
    assert app.call("/ui/conversations/operations/" + OPERATION)[0] == 401
    assert not app.ui.conversations.root.exists()
