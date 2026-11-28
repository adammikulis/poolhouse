"""A QR code of one short text, as SVG: byte mode, error correction level M, versions 1 to 20 (ISO/IEC 18004).

Only what the link screen needs: the phone's camera reads the link, and nothing else here reads one.
"""

from __future__ import annotations

from itertools import pairwise

__all__ = ["matrix", "svg"]

# version: (error correction codewords a block, blocks, data codewords a block, longer blocks); a longer block holds one more.
BLOCKS = {1: (10, 1, 16, 0), 2: (16, 1, 28, 0), 3: (26, 1, 44, 0), 4: (18, 2, 32, 0), 5: (24, 2, 43, 0), 6: (16, 4, 27, 0),
          7: (18, 4, 31, 0), 8: (22, 2, 38, 2), 9: (22, 3, 36, 2), 10: (26, 4, 43, 1), 11: (30, 1, 50, 4), 12: (22, 6, 36, 2),
          13: (22, 8, 37, 1), 14: (24, 4, 40, 5), 15: (24, 5, 41, 5), 16: (28, 7, 45, 3), 17: (28, 10, 46, 1), 18: (26, 9, 43, 4),
          19: (26, 3, 44, 11), 20: (26, 3, 41, 13)}
ALIGN = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34), 7: (6, 22, 38), 8: (6, 24, 42), 9: (6, 26, 46),
         10: (6, 28, 50), 11: (6, 30, 54), 12: (6, 32, 58), 13: (6, 34, 62), 14: (6, 26, 46, 66), 15: (6, 26, 48, 70),
         16: (6, 26, 50, 74), 17: (6, 30, 54, 78), 18: (6, 30, 56, 82), 19: (6, 30, 58, 86), 20: (6, 34, 62, 90)}
MASKS = (lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
         lambda x, y: (x // 3 + y // 2) % 2 == 0, lambda x, y: x * y % 2 + x * y % 3 == 0,
         lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0)


def _times(a: int, b: int) -> int:
    """The product in the field of 256 elements the code uses (polynomial 0x11D)."""
    out = 0
    for bit in range(7, -1, -1):
        out = (out << 1) ^ ((out >> 7) * 0x11D)
        out ^= ((b >> bit) & 1) * a
    return out


def _correction(data: list[int], degree: int) -> list[int]:
    """The Reed-Solomon codewords of one block."""
    divisor, root = [0] * (degree - 1) + [1], 1
    for _ in range(degree):
        for i in range(degree):
            divisor[i] = _times(divisor[i], root) ^ (divisor[i + 1] if i + 1 < degree else 0)
        root = _times(root, 2)
    rest = [0] * degree
    for byte in data:
        factor = byte ^ rest.pop(0)
        rest.append(0)
        rest = [r ^ _times(d, factor) for r, d in zip(rest, divisor, strict=True)]
    return rest


def _counted(version: int) -> int:
    """The bits that count the bytes of a text."""
    return 16 if version >= 10 else 8


def _codewords(text: bytes) -> tuple[int, list[int]]:
    """The smallest version that holds ``text`` and its codewords, data and correction interleaved."""
    version = next((v for v, (_check, blocks, short, longer) in BLOCKS.items()
                    if 4 + _counted(v) + 8 * len(text) <= (blocks * short + longer * (short + 1)) * 8), 0)
    if not version:
        raise ValueError("That text is too long for a QR code here.")
    check, blocks, short, longer = BLOCKS[version]
    count, capacity = _counted(version), (blocks * short + longer * (short + 1)) * 8
    bits = [0, 1, 0, 0] + [(len(text) >> i) & 1 for i in range(count - 1, -1, -1)]
    bits += [(byte >> i) & 1 for byte in text for i in range(7, -1, -1)]
    bits += [0] * min(4, capacity - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    data += [(0xEC, 0x11)[i % 2] for i in range(capacity // 8 - len(data))]
    parts, at = [], 0
    for number in range(blocks + longer):
        size = short + (number >= blocks)
        parts.append(data[at:at + size])
        at += size
    checks = [_correction(part, check) for part in parts]
    out = [part[i] for i in range(short + 1) for part in parts if i < len(part)]
    out += [each[i] for i in range(check) for each in checks]
    return version, out


class _Grid:
    """The modules of one symbol; ``fixed`` marks those that carry no data."""

    def __init__(self, version: int) -> None:
        self.version, self.size = version, 17 + 4 * version
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def mark(self, x: int, y: int, dark: bool) -> None:
        if 0 <= x < self.size and 0 <= y < self.size:
            self.dark[y][x], self.fixed[y][x] = dark, True

    def patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.mark(6, i, i % 2 == 0)
            self.mark(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    self.mark(cx + dx, cy + dy, max(abs(dx), abs(dy)) not in (2, 4))
        spots = ALIGN[self.version]
        last = len(spots) - 1
        for i, cx in enumerate(spots):
            for j, cy in enumerate(spots):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.mark(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
        self.format(0)
        if self.version >= 7:
            rest = self.version
            for _ in range(12):
                rest = (rest << 1) ^ ((rest >> 11) * 0x1F25)
            bits = self.version << 12 | rest
            for i in range(18):
                dark = (bits >> i) & 1 == 1
                self.mark(size - 11 + i % 3, i // 3, dark)
                self.mark(i // 3, size - 11 + i % 3, dark)

    def format(self, mask: int) -> None:
        """The two copies of the format bits: level M and ``mask``, protected by their own code."""
        data = mask
        rest = data
        for _ in range(10):
            rest = (rest << 1) ^ ((rest >> 9) * 0x537)
        bits = (data << 10 | rest) ^ 0x5412
        size = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        for i in range(6):
            self.mark(8, i, bit(i))
        self.mark(8, 7, bit(6))
        self.mark(8, 8, bit(7))
        self.mark(7, 8, bit(8))
        for i in range(9, 15):
            self.mark(14 - i, 8, bit(i))
        for i in range(8):
            self.mark(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.mark(8, size - 15 + i, bit(i))
        self.mark(8, size - 8, True)

    def place(self, words: list[int]) -> None:
        at, right = 0, self.size - 1
        while right >= 1:
            right = 5 if right == 6 else right
            upward = (right + 1) & 2 == 0
            for step in range(self.size):
                y = self.size - 1 - step if upward else step
                for x in (right, right - 1):
                    if not self.fixed[y][x] and at < len(words) * 8:
                        self.dark[y][x] = (words[at >> 3] >> (7 - (at & 7))) & 1 == 1
                        at += 1
            right -= 2

    def flip(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if not self.fixed[y][x] and MASKS[mask](x, y):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self) -> int:
        """How hard the symbol is to read: long runs, solid squares, finder look-alikes and an uneven share of dark."""
        lines = self.dark + [list(column) for column in zip(*self.dark, strict=True)]
        score = 0
        for line in lines:
            text = "".join("1" if each else "0" for each in line)
            run = 1
            for before, after in pairwise(text):
                run = run + 1 if before == after else 1
                score += 3 if run == 5 else 1 if run > 5 else 0
            score += 40 * (text.count("10111010000") + text.count("00001011101"))
        for y in range(self.size - 1):
            for x in range(self.size - 1):
                if self.dark[y][x] == self.dark[y][x + 1] == self.dark[y + 1][x] == self.dark[y + 1][x + 1]:
                    score += 3
        dark = sum(map(sum, self.dark))
        return score + 10 * (abs(dark * 20 - self.size ** 2 * 10) // self.size ** 2)


def matrix(text: str) -> list[list[bool]]:
    """The rows of modules of ``text``'s symbol, True for dark, without the quiet zone."""
    version, words = _codewords(text.encode())
    grid = _Grid(version)
    grid.patterns()
    grid.place(words)
    scored = []
    for mask in range(8):
        grid.format(mask)
        grid.flip(mask)
        scored.append((grid.penalty(), mask))
        grid.flip(mask)
    best = min(scored)[1]
    grid.format(best)
    grid.flip(best)
    return grid.dark


def svg(text: str) -> str:
    """An SVG of ``text``'s symbol with its four-module quiet zone: dark on white in every theme, as cameras expect."""
    rows = matrix(text)
    side = len(rows) + 8
    path = "".join(f"M{x + 4},{y + 4}h1v1h-1z" for y, row in enumerate(rows) for x, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}" shape-rendering="crispEdges" role="img" '
            f'aria-label="QR code to link a phone"><rect width="{side}" height="{side}" fill="#ffffff"/>'
            f'<path d="{path}" fill="#000000"/></svg>')
