"""Whether a shell command records a review verdict, read from the text of what it would run.

A verdict is the landing review of a commit (`scripts/lead-review`, `land-review`), the independent review of a task (`task-review`),
the grade of a delegate's work, a pull request review that approves or asks for changes, or a status or review written through
`gh api`. A program is known by its name wherever it stands in a command, past any wrapper or keyword; only a command that reads or
prints text (`READS_TEXT`) may name one without running it, and quoted text stays one word, so prose about a review is none. The
command is split by the shell reader of the loaded rules (`poolhouse.protectedwrites`).
"""

from __future__ import annotations

import itertools
import re

KEYWORDS = frozenset({"!", "{", "}", "if", "then", "do", "else", "elif", "while", "until"})
"""The words of a shell that stand before the program of a command."""

READS_TEXT = frozenset({"cat", "grep", "egrep", "fgrep", "rg", "ag", "ack", "ls", "head", "tail", "less", "more", "bat", "wc", "sed",
                        "awk", "find", "file", "stat", "diff", "cmp", "git", "cp", "mv", "rm", "touch", "chmod", "vim", "vi", "nano",
                        "code", "open", "echo", "printf", "test", "[", "realpath", "basename", "dirname", "md5", "md5sum", "shasum",
                        "sha256sum", "which", "type", "man", "ruff", "shellcheck", "mypy"})
"""Programs that read, print or move the files they are given and run none of them."""

APPROVES = re.compile(r"--(?:approve|request-changes)(?:=.*)?|-[a-z]*[ar][a-z]*(?:=.*)?")
"""The flags of `gh pr review` that give a verdict, bundled short ones too."""

WRITTEN_BY_API = re.compile(r"/statuses/|/reviews\b")
MUTATIONS = re.compile(r"(?:addPullRequestReview|submitPullRequestReview|dismissPullRequestReview|createCommitStatus)\b")
WITHOUT_RULES = re.compile(r"\b(?:lead|land|task)-review\b|\bgrade\b|\bgh\b[^\n]*(?:\breview|/statuses/)")
"""What is refused when the shell rules cannot be loaded to read the command."""


def name(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def literal(text: str) -> str:
    """``text`` with the marks of a substitution blanked inside single quotes, where a shell expands nothing."""
    out: list[str] = []
    quote, escaped = "", False
    for char in text:
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote and char == quote:
            quote = ""
        elif not quote and char in "'\"":
            quote = char
        elif quote == "'" and char in "$`<>":
            char = "_"
        out.append(char)
    return "".join(out)


def commands_run(command: str, reader, depth: int = 0) -> list[list[str]]:
    """The words of each command ``command`` would run, as far as its text shows: continued lines joined, and substitutions, the
    string given to a shell or to `eval`, and the heredocs and here-strings fed to a shell read as commands too. Quoted text stays
    one word."""
    text, bodies = reader._heredocs(command.replace("\\\n", ""))
    flat, inner = reader._substitutions(literal(reader._flatten(text)))
    found = reader._commands(reader._words(flat))
    for words in found:
        for i, word in enumerate(words):
            if name(word) in reader.SHELLS:
                inner += [reader._shell_code(words[i + 1:]), *bodies, *(b for a, b in itertools.pairwise(words) if a == "<<<")]
            elif name(word) == "eval":
                inner.append(" ".join(words[i + 1:]))
    if depth < reader.DEPTH:
        for one in filter(None, inner):
            found += commands_run(one, reader, depth + 1)
    return found


def runs_what_it_names(words: list[str], reader) -> bool:
    """Whether the program of the command ``words``, past leading keywords, wrappers and assignments, is not one that only reads."""
    while words and words[0] in KEYWORDS:
        words = words[1:]
    program = reader._first_words(words)[0]
    return not (program and name(program[0]) in READS_TEXT)


def reviews_with_gh(args: list[str]) -> bool:
    """Whether `gh` with ``args`` gives a verdict on a pull request or writes a status or review through its API."""
    plain = [a for i, a in enumerate(args) if not a.startswith("-") and args[i - 1:i] not in (["-R"], ["--repo"])]
    if plain[:2] == ["pr", "review"]:
        return any(APPROVES.fullmatch(a) for a in args)
    if plain[:1] != ["api"]:
        return False
    if "graphql" in args:
        return bool(MUTATIONS.search(" ".join(args)))
    method = re.search(r"(?:^| )(?:-X|--method)[ =]?(\w+)", " ".join(args))
    body = any(a.startswith(("-f", "-F", "--field", "--raw-field", "--input")) for a in args)
    writes = body or bool(method and method.group(1).upper() in ("POST", "PUT", "PATCH"))
    return writes and any(WRITTEN_BY_API.search(a) for a in args)


def grades(words: list[str]) -> bool:
    """Whether ``words`` run the delegate program's `grade`."""
    for i, word in enumerate(words):
        delegate = name(word) in ("poolhouse-delegate", "poolhouse.delegate") or (
            word == "delegate" and i > 0 and name(words[i - 1]) == "poolhouse")
        if delegate and [a for a in words[i + 1:] if not a.startswith("-")][:1] == ["grade"]:
            return True
    return False


def gives_one(words: list[str]) -> bool:
    """Whether the one command ``words``, which runs what it names, records a verdict."""
    names = [name(word) for word in words]
    workspace = any(n in ("poolhouse", "poolhouse-workspace") or n.startswith(("poolhouse.workspace", "$")) for n in names)
    return ("lead-review" in names or (workspace and ("land-review" in words or "task-review" in words)) or grades(words)
            or any(reviews_with_gh(words[i + 1:]) for i, n in enumerate(names) if n == "gh"))


def records_a_verdict(command: str, rules) -> bool:
    """Whether ``command`` records a review verdict; ``rules`` are the loaded shell rules, or None when they could not be loaded."""
    if rules is None:
        return bool(WITHOUT_RULES.search(command))
    reader = rules.protected()
    return any(runs_what_it_names(words, reader) and gives_one(words) for words in commands_run(command, reader))
