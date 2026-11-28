"""``/ui/device-uses``: what each device can do for a person now, asked of a real daemon on a real socket."""

from __future__ import annotations

import string
from types import SimpleNamespace

import pytest
from device_uses_kit import (
    CANNED_MODEL,
    OTHER,
    OWN,
    CannedEnvironment,
    add_model,
    canned_pool,
    canned_serving,
)
from test_fleet_ui import Serving

from poolhouse import contracts
from poolhouse.fleet import environment as environment_module
from poolhouse.fleet.models import Models
from poolhouse.fleet.page import COMPONENTS

FIGURES = {"count", "free", "model", "running", "queued", "size", "name", "device", "own"}


@pytest.fixture
def served(tmp_path):
    daemon = Serving(tmp_path)
    daemon.ui.root = tmp_path / "traind"
    daemon.ui.models = Models([daemon.files], daemon.files)
    try:
        yield daemon
    finally:
        daemon.close()


def asked(served, path="/ui/device-uses"):
    status, body, _ = served.call(path)
    assert status == 200, body
    return body


def uses(report, device="self"):
    found = next(row for row in report["devices"] if row["id"] == device)
    return {use["id"]: use for use in found["uses"]}


def test_with_no_model_it_lists_what_works_without_one(served):
    here = uses(asked(served))
    assert here["models"]["state"] == "empty" and here["jobs"]["state"] == "idle"
    assert here["chat"]["state"] == "needs_model" and here["benchmarks"]["state"] == "needs_model"
    assert here["models"]["group"] == "without_model" and here["chat"]["group"] == "with_model"
    assert "speech" not in here, "a speech use is listed only when an engine answers"
    assert "training" not in here, "nothing here can install the training tools"


def test_a_downloaded_model_and_a_running_one_change_what_chat_offers(served):
    add_model(served.files)
    here = uses(asked(served))
    assert here["models"]["state"] == "ready" and "Downloaded: 1." in here["models"]["detail"]
    assert here["chat"]["state"] == "downloaded" and here["chat"]["action"]["open"] == "models"
    served.ui.serving = canned_serving()
    chat = uses(asked(served))["chat"]
    assert chat["state"] == "running" and chat["action"]["open"] == "chat"
    assert CANNED_MODEL in chat["detail"]


def test_speech_is_listed_when_an_engine_answers(served):
    served.ui.report = lambda: {"speech": ["asr"]}
    assert uses(asked(served))["speech"]["action"] == {"label": "Open speech tools", "open": "tools", "panel": "library"}


@pytest.mark.parametrize("vendor,framework", [("apple", "mlx"), ("nvidia", "torch-cuda"), ("amd", "torch-rocm")])
def test_training_names_what_this_vendor_still_has_to_install(served, vendor, framework, monkeypatch):
    # the catalogue offers MLX on macOS alone; only its own reading of the platform is changed
    monkeypatch.setattr(environment_module, "sys", SimpleNamespace(platform="darwin"))
    served.ui.report = lambda: {"vendor": vendor}
    served.ui.environment = CannedEnvironment("core")
    training = uses(asked(served))["training"]
    assert training["state"] == "install"
    assert training["action"]["install"] == [framework] and training["action"]["sources"] == "both"


def test_training_is_ready_once_installed_and_the_read_is_remembered_until_asked_again(served):
    served.ui.report = lambda: {"vendor": "cpu"}
    served.ui.environment = environment = CannedEnvironment()
    assert uses(asked(served))["training"]["action"]["install"] == ["core", "torch-cpu"]
    environment.have.update(("core", "torch-cpu"))
    assert uses(asked(served))["training"]["state"] == "install" and environment.reads == 1
    assert uses(asked(served, "/ui/device-uses?fresh=1"))["training"]["state"] == "ready"
    assert environment.reads == 2


def test_training_sends_a_person_who_chose_the_pool_only_to_settings(served):
    served.ui.environment = CannedEnvironment()
    served.ui.settings.download_sources = "lan"
    training = uses(asked(served))["training"]
    assert training["state"] == "internet_off" and training["action"]["open"] == "settings"
    assert "install" not in training["action"]


def test_another_computer_is_offered_project_tests_and_nothing_else(served):
    served.ui.pool_status = lambda force=False: canned_pool("quillhaven; delete everything", connected=False)
    report = asked(served)
    other = next(row for row in report["devices"] if row["id"] == OTHER)
    assert list(uses(report, OTHER)) == ["tests"]
    assert other["status"] == "Not connected right now"
    assert "cannot be sent to quillhaven; delete everything from here yet" in other["note"]
    copied = other["uses"][0]["action"]["copy"]
    assert OWN in copied and OTHER in copied
    assert "quillhaven" not in copied, "a name another device chose never goes into a request to an agent"


def test_the_words_claim_no_other_use_across_computers():
    words = contracts.load("device-uses.json")
    assert [use["id"] for use in words["other_device"]["uses"]] == ["tests"]
    assert "cannot yet send" in " ".join(words["adding"]["lines"])
    assert "does not split" in words["limit"]


def test_every_state_has_words_one_action_and_a_screen_the_page_has():
    words = contracts.load("device-uses.json")
    groups = {group["id"] for group in words["groups"]}
    screens = {name.removesuffix("-view") for name in COMPONENTS if name.endswith("-view")} | {"guests"}
    for use in [*words["this_device"]["uses"], *words["other_device"]["uses"]]:
        assert use["group"] in groups, use["id"]
        assert not use.get("view") or use["view"] in screens, use["id"]
        for state, said in use["states"].items():
            action = said["action"]
            assert said["badge"] and action["label"], (use["id"], state)
            assert ("open" in action) != ("copy" in action), (use["id"], state)
            assert action.get("open", "models") in screens, (use["id"], state)
            named = {field for text in (said["detail"], action.get("copy", ""))
                     for _, field, _, _ in string.Formatter().parse(text) if field}
            assert named <= FIGURES, (use["id"], state, named - FIGURES)
