"""Appearance persistence, complete presets, and atomic validation."""

from copy import deepcopy

import pytest

from poolhouse.fleet.settings import Settings
from poolhouse.fleet.themes import BRAND, default_appearance, registry, resolve, validate_appearance
from poolhouse.fleet.ui import UI


def custom():
    theme = registry()[0]
    theme.update(id="custom-1", name="Quiet water", font_size=16, density="compact")
    theme["colors"]["pink"] = "#AA3377"
    return {"active_theme": "custom-1", "saved_themes": [theme]}


def test_both_presets_include_owner_palette_and_distinct_neutrals():
    themes = registry()
    assert {row["base"] for row in themes} == {"light", "dark"}
    for row in themes:
        assert {key: row["colors"][key] for key in BRAND} == BRAND
    assert themes[0]["colors"]["panel"] != themes[1]["colors"]["panel"]
    themes[0]["colors"]["pink"] = "#000000"
    assert registry()[0]["colors"]["pink"] == "#ff5fa2"


def test_custom_theme_persists_without_changing_advanced_preference(tmp_path):
    ui = UI(name="theme-device")
    ui.settings = Settings(always_show_advanced=True)
    ui.settings_path = tmp_path / "settings.json"
    assert "error" not in ui.apply_prefs({"appearance": custom()})
    loaded = Settings.load(ui.settings_path)
    assert loaded.always_show_advanced is True
    assert resolve(loaded.appearance)["colors"]["pink"] == "#aa3377"
    assert resolve(loaded.appearance)["font_size"] == 16
    assert resolve(loaded.appearance)["density"] == "compact"
    assert "error" not in ui.apply_prefs({"appearance": default_appearance()})
    assert resolve(Settings.load(ui.settings_path).appearance)["id"] == "dark"


@pytest.mark.parametrize("change", [
    lambda a: a.update(active_theme="missing"),
    lambda a: a.update(saved_themes="bad"),
    lambda a: a.update(extra=True),
    lambda a: a["saved_themes"][0].update(id="dark"),
    lambda a: a["saved_themes"][0].update(id="<script>"),
    lambda a: a["saved_themes"][0].update(name="\n"),
    lambda a: a["saved_themes"][0].update(name="a" * 49),
    lambda a: a["saved_themes"][0].update(base=[]),
    lambda a: a["saved_themes"][0].update(font_size=True),
    lambda a: a["saved_themes"][0].update(font_size=21),
    lambda a: a["saved_themes"][0].update(density="dense"),
    lambda a: a["saved_themes"][0]["colors"].update(pink="url(https://example.invalid)"),
    lambda a: a["saved_themes"][0]["colors"].update(panel="red;display:none"),
    lambda a: a["saved_themes"][0]["colors"].update(unknown="#ffffff"),
    lambda a: a["saved_themes"][0]["colors"].pop("text"),
    lambda a: a["saved_themes"].append(deepcopy(a["saved_themes"][0])),
])
def test_invalid_theme_rejects_entire_preference_request(tmp_path, change):
    ui = UI(name="theme-device")
    ui.settings = Settings()
    ui.settings_path = tmp_path / "settings.json"
    before = ui.settings.public()
    appearance = custom()
    change(appearance)
    result = ui.apply_prefs({"appearance": appearance, "always_show_advanced": True, "context": 16384})
    assert result["error"]
    assert ui.settings.public() == before
    assert not ui.settings_path.exists()


def test_custom_theme_count_is_bounded():
    appearance = custom()
    appearance["saved_themes"] *= 17
    with pytest.raises(ValueError, match="16"):
        validate_appearance(appearance)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_setup_system_theme_initializes_once_and_manual_choice_wins(theme, tmp_path):
    ui = UI(name="theme-device")
    ui.settings, ui.settings_path = Settings(), tmp_path / "settings.json"
    assert ui.state()["needs_setup_theme"] is True
    assert "error" not in ui.apply_prefs({"system_theme":theme})
    assert Settings.load(ui.settings_path).appearance["active_theme"] == theme
    assert ui.state()["needs_setup_theme"] is False
    other = "light" if theme == "dark" else "dark"
    assert "error" not in ui.apply_prefs({"system_theme":other})
    assert ui.settings.appearance["active_theme"] == theme
    assert "error" not in ui.apply_prefs({"appearance":custom()})
    assert "error" not in ui.apply_prefs({"system_theme":other, "appearance_initialized":False})
    assert ui.settings.appearance == validate_appearance(custom())
    assert Settings.load(ui.settings_path).appearance_initialized is True


@pytest.mark.parametrize("value", [None, True, 1, [], {}, "system", "LIGHT", ""])
def test_invalid_system_theme_never_mutates_other_preferences(value, tmp_path):
    ui = UI(name="theme-device")
    ui.settings, ui.settings_path = Settings(), tmp_path / "settings.json"
    before = ui.settings.public()
    assert ui.apply_prefs({"system_theme":value, "context":16384})["error"]
    assert ui.settings.public() == before
    assert not ui.settings_path.exists()


def test_finished_setup_and_invalid_initialization_flag_cannot_change_theme(tmp_path):
    ui = UI(name="theme-device")
    ui.settings, ui.settings_path = Settings(setup_done=True), tmp_path / "settings.json"
    assert "error" not in ui.apply_prefs({"system_theme":"light"})
    assert ui.settings.appearance == default_appearance()
    ui.settings.appearance_initialized = "no"
    before = ui.settings.public()
    assert ui.apply_prefs({"system_theme":"light", "context":16384})["error"]
    assert ui.settings.public() == before


def test_unavailable_appearance_validation_is_plain_and_atomic(tmp_path):
    ui = UI(name="theme-device")
    ui.settings, ui.settings_path = Settings(appearance={}), tmp_path / "settings.json"
    before = ui.settings.public()
    assert ui.apply_prefs({"system_theme":"light", "context":16384})["error"]
    assert ui.settings.public() == before
    assert not ui.settings_path.exists()
