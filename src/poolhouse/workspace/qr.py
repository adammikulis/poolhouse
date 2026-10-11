"""A QR code as a person's screen shows it: in the terminal and as an image file a screen can open.

The code carries a link that adds a phone (docs/phone-approval.md); the image holds the link as its description, for a person
who cannot scan it and for a tool that reads the file.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from poolhouse.platform import private_file

__all__ = ["matrix", "svg", "terminal", "write_svg"]

QUIET = 4
"""The empty modules around a code that a scanner needs."""


def matrix(text: str) -> list[list[bool]]:
    """The modules of the code for ``text``, dark ones True, with the quiet zone around them."""
    code = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=0)
    code.add_data(text)
    code.make(fit=True)
    inner = [[bool(m) for m in row] for row in code.get_matrix()]
    blank = [False] * (len(inner) + 2 * QUIET)
    return [list(blank) for _ in range(QUIET)] + [[False] * QUIET + row + [False] * QUIET for row in inner] + [list(blank) for _ in range(QUIET)]


def terminal(text: str) -> str:
    """The code as lines of half blocks, black on white whatever the terminal's colours are."""
    grid = matrix(text)
    if len(grid) % 2:
        grid.append([False] * len(grid[0]))
    glyph = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}
    lines = ["".join(glyph[(top, bottom)] for top, bottom in zip(grid[r], grid[r + 1], strict=True)) for r in range(0, len(grid), 2)]
    return "\n".join(f"\x1b[30;47m{line}\x1b[0m" for line in lines)


def svg(text: str, *, module: int = 8) -> str:
    """The code as an SVG image whose description is ``text``."""
    grid = matrix(text)
    size = len(grid) * module
    squares = "".join(f"M{c * module},{r * module}h{module}v{module}h-{module}z" for r, row in enumerate(grid) for c, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f"<title>poolhouse: add a phone</title><desc>{escape(text)}</desc>"
            f'<rect width="{size}" height="{size}" fill="#fff"/><path d="{squares}" fill="#000"/></svg>\n')


def write_svg(path: Path, text: str) -> Path:
    """Write the image to ``path``, readable by its owner alone (the code is good for ten minutes, once)."""
    path = Path(path)
    path.touch(mode=0o600)
    private_file(path)
    path.write_text(svg(text), encoding="utf-8")
    return path
