"""The Android app wears the web UI's colours and icons: each value in the app is read out of the web UI's own files and compared.

The app cannot read CSS at run time, so `Palette.kt` repeats the tokens. This test is what keeps the two the same: a theme change on the
web fails here until the app has it too. It needs no Android tool.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "poolhouse" / "fleet" / "web"
APP = ROOT / "app" / "android"
PALETTE = APP / "core" / "src" / "main" / "kotlin" / "app" / "poolhouse" / "core" / "theme" / "Palette.kt"
ICONS = APP / "app" / "src" / "main" / "kotlin" / "app" / "poolhouse" / "phone" / "ui" / "theme" / "Icons.kt"

TOKENS = {
    "bg": "--poolhouse-ui-bg", "surface": "--poolhouse-ui-surface", "sunken": "--poolhouse-ui-sunken", "line": "--poolhouse-ui-line",
    "lineStrong": "--poolhouse-ui-line-strong", "text": "--poolhouse-ui-text", "muted": "--poolhouse-ui-muted",
    "accent": "--poolhouse-ui-accent", "accentInk": "--poolhouse-ui-accent-ink", "accentDim": "--poolhouse-ui-accent-dim",
    "onAccent": "--poolhouse-ui-on-accent", "green": "--poolhouse-ui-green", "yellow": "--poolhouse-ui-yellow",
    "red": "--poolhouse-ui-red", "warn": "--warn", "busy": "--busy",
}


def block(css: str, selector: str) -> dict[str, str]:
    """The custom properties of the first rule whose selector list is exactly ``selector``."""
    match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{(.*?)^\}", css, re.S)
    assert match, f"no rule {selector!r}"
    return {name: value.strip() for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(1))}


def rgb(value: str) -> tuple[int, int, int]:
    digits = value.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def resolve(value: str, values: dict[str, str]) -> tuple[int, int, int]:
    """A token's colour: a hex value, ``var(--x)``, or ``color-mix(in srgb, a P%, b)``."""
    value = value.strip()
    if value.startswith("var("):
        return resolve(values[value[4:-1].strip()], values)
    mix = re.fullmatch(r"color-mix\(in srgb,\s*(.+?)\s+(\d+)%,\s*(#[0-9a-fA-F]+)\)", value)
    if mix:
        first, share, second = resolve(mix.group(1), values), int(mix.group(2)) / 100, rgb(mix.group(3))
        return tuple(int(a * share + b * (1 - share) + 0.5) for a, b in zip(first, second, strict=True))
    return rgb(value)


def web_themes() -> dict[str, dict[str, tuple[int, int, int]]]:
    css = (WEB / "foundation.css").read_text(encoding="utf-8")
    light = block(css, ':root, :root[data-theme="light"]')
    dark = {**light, **block(css, ':root[data-theme="dark"]')}
    return {theme: {name: resolve(values[token], values) for name, token in TOKENS.items()}
            for theme, values in (("light", light), ("dark", dark))}


def app_palettes() -> dict[str, dict[str, tuple[int, int, int]]]:
    source = PALETTE.read_text(encoding="utf-8")
    brand = {name: rgb(value[2:]) for name, value in re.findall(r"const val (\w+) = 0xFF([0-9A-F]{6})", source) for value in ["FF" + value]}
    out = {}
    for theme in ("light", "dark"):
        body = re.search(rf"val {theme} = Palette\((.*?)\n    \)", source, re.S).group(1)
        fields = {}
        for name, value in re.findall(r"(?m)^\s+(\w+) = (0xFF[0-9A-F]{6}|Brand\.\w+),", body):
            fields[name] = rgb(value[4:]) if value.startswith("0x") else brand[value.split(".")[1]]
        out[theme] = fields
    return out


def test_every_colour_of_both_themes_is_the_web_ui_colour_of_the_same_token():
    web, app = web_themes(), app_palettes()
    for theme in ("light", "dark"):
        assert set(app[theme]) == set(TOKENS), f"{theme}: the palette's fields and the token table differ"
        wrong = {name: (app[theme][name], web[theme][name]) for name in TOKENS if app[theme][name] != web[theme][name]}
        assert not wrong, f"{theme}: (app, web) differ for {wrong}"


def test_the_brand_colours_are_the_web_ui_brand_colours():
    values = block((WEB / "foundation.css").read_text(encoding="utf-8"), ':root, :root[data-theme="light"]')
    source = PALETTE.read_text(encoding="utf-8")
    brand = dict(re.findall(r"const val (\w+) = 0xFF([0-9A-F]{6})", source))
    for name in ("PINK", "ORANGE", "YELLOW", "CYAN", "NAVY"):
        assert rgb(brand[name]) == rgb(values[f"--poolhouse-{name.lower()}"]), name


def test_every_screen_of_the_app_takes_its_colours_from_the_shared_theme():
    """A module that built a colour scheme of its own (or took the phone's) would not look like the rest of the app."""
    own = [str(path.relative_to(APP)) for path in APP.glob("*/src/main/**/*.kt")
           if path.relative_to(APP).parts[0] != "theme" and re.search(r"\b\w*(?:light|dark|Light|Dark)ColorScheme\b", path.read_text(encoding="utf-8"))]
    assert not own, f"these build their own colours: {own}"


def test_the_navigation_icons_are_the_web_rails_own_paths():
    web = (WEB / "components" / "fleet-nav.html").read_text(encoding="utf-8")
    table = re.search(r"const ICONS = \{(.*?)\n  \};", web, re.S).group(1)
    paths = {name: re.findall(r'"([^"]+)"', body) for name, body in re.findall(r"(\w+):\[(.*?)\]", table)}
    app = ICONS.read_text(encoding="utf-8")
    used = re.findall(r'web\("(\w+)",((?:\s*"[^"]+",?)+)\s*\)', app)
    assert used, "no web icon is declared in Icons.kt"
    for name, body in used:
        assert re.findall(r'"([^"]+)"', body) == paths[name], f"{name} differs from the web rail"
