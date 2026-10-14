"""Review requirements derived from the exact proposed Git tree."""
from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath

import land_git as lg

from poolhouse import protectedwrites

POLICY = {"agents.md", "claude.md", "skill.md", "security.md"}
SENSITIVE_DOCS = ("agent", "security", "keystore", "landing", "node", "phone", "pool", "install", "workspace", "person", "compromis", "sentinel", "service", "gates", "test-execution")


def required(root: Path, target: str, tip: str) -> tuple[bool, str, str]:
    """Whether the pinned diff needs review, its merge base and the first sensitive path."""
    target_ref = f"origin/{target}" if lg.exists(root, f"origin/{target}") else target
    base = lg.out(root, "merge-base", target_ref, tip)
    raw = lg.git(root, "diff", "--raw", "--no-abbrev", "--no-renames", "-z", base, tip).stdout.split("\0")
    for i in range(0, len(raw) - 1, 2):
        modes = raw[i].split()[:2]
        name = raw[i + 1]
        if len(modes) != 2:
            raise ValueError("the pinned diff has malformed file modes")
        before, after = modes[0].removeprefix(":"), modes[1]
        if before in {"120000", "100755"} or after in {"120000", "100755"} or (before != after and "000000" not in (before, after)):
            return True, base, name
        path = PurePosixPath(name)
        if protectedwrites.touches_guards([name]) or path.name.casefold() in POLICY or any(part.startswith(".") for part in path.parts):
            return True, base, name
        if name.startswith("docs/") and path.suffix == ".md" and not path.name.casefold().startswith(SENSITIVE_DOCS):
            continue
        if name in {"HANDOFF.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md", "NOTICE", "LICENSE", "THIRD_PARTY_NOTICES.md"}:
            continue
        if path.suffix == ".py" and unchanged_python(root, base, tip, name):
            continue
        return True, base, name
    return False, base, ""


def unchanged_python(root: Path, base: str, tip: str, path: str) -> bool:
    """Whether both revisions parse to the same Python syntax tree."""
    before = lg.git(root, "show", f"{base}:{path}", check=False)
    after = lg.git(root, "show", f"{tip}:{path}", check=False)
    if before.returncode or after.returncode:
        return False
    try:
        return ast.dump(ast.parse(before.stdout, type_comments=True)) == ast.dump(ast.parse(after.stdout, type_comments=True))
    except SyntaxError:
        return False
