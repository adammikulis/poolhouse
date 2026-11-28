"""How the rules read a shell command line. There are two readings, and a rule refuses what either of them shows, so the second
only ever adds refusals. The plain one (`heredocs`, `flatten`, `substitutions`, `words`) takes heredocs by the line,
substitutions by their parentheses and words by `shlex`. The other (`readings`) follows the shell: quotes, comments, operators,
substitutions, expansions, arithmetic and heredocs, found in one pass that reads each character once. Where that pass is not sure
what the shell would pass over, it passes over nothing: the text is also read line by line with its quotes ignored.

Standard library only: `protectedwrites` loads this file by file, and the hooks may run under an old Python.
"""

from __future__ import annotations

import bisect
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass

SPLIT = frozenset({";", "&&", "||", "|", "&", "|&", ";;", "(", ")"})
FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
WRITES_TO_A_FILE = re.compile(r"^(&?>>?|>\||&>>?)$")
READS_A_FILE = re.compile(r"^(\d*<>?)$")
REDIRECTS = re.compile(r"[<>]&?|<<-?|<<<|>&")


def flatten(command: str) -> str:
    """The command with each newline outside quotes turned into a `;`."""
    out: list[str] = []
    quote = ""
    for char in command:
        if char == "\n" and not quote:
            char = ";"
        elif quote and char == quote:
            quote = ""
        elif not quote and char in "'\"":
            quote = char
        out.append(char)
    return "".join(out)


def _subshell(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        if depth == 0:
            return i
    return len(text) - 1


def substitutions(text: str) -> tuple[str, list[str]]:
    """``text`` with each `$(...)`, `<(...)`, `>(...)` and backtick body replaced by a word, and the bodies."""
    bodies: list[str] = []
    while found := re.search(r"[$<>]\(", text):
        end = _subshell(text, found.end() - 1)
        bodies.append(text[found.end():end])
        text = text[:found.start()] + "SUB" + text[end + 1:]
    ticks = text.split("`")
    if len(ticks) > 2:
        bodies += ticks[1::2]
        text = "SUB".join(ticks[0::2])
    return text, bodies


def heredocs(command: str) -> tuple[str, list[str]]:
    """``command`` without heredoc bodies, and the bodies."""
    kept: list[str] = []
    bodies: list[str] = []
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        kept.append(lines[i])
        opened = re.search(r"<<-?\s*['\"]?([A-Za-z_]\w*)['\"]?", lines[i])
        if opened:
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != opened.group(1):
                body.append(lines[i])
                i += 1
            bodies.append("\n".join(body))
        i += 1
    return "\n".join(kept), bodies


def words(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return text.split()


def commands(found: list[str]) -> list[list[str]]:
    """The words split into commands; a `;` that ends a `find -exec` clause and the parentheses that group its tests stay in it."""
    out: list[list[str]] = [[]]
    for i, word in enumerate(found):
        ends_exec = word == ";" and FIND_EXEC.intersection(out[-1]) and found[i + 1:i + 2] and found[i + 1].startswith("-")
        groups = word in ("(", ")") and any(w.rsplit("/", 1)[-1] == "find" for w in out[-1])
        if ends_exec or groups:
            out[-1].append(word)
        elif word in SPLIT:
            out.append([])
        else:
            out[-1].append(word)
    return [one for one in out if one]


def split_redirects(found: list[str]) -> tuple[list[str], list[str], list[str]]:
    """The words without redirections, the files written by `>`, `>>`, `>|` and `&>`, and the files read by `<`."""
    kept: list[str] = []
    written: list[str] = []
    read: list[str] = []
    i = 0
    while i < len(found):
        follows = i + 1 < len(found)
        if follows and WRITES_TO_A_FILE.match(found[i]):
            written.append(found[i + 1])
            i += 2
        elif follows and READS_A_FILE.match(found[i]):
            read.append(found[i + 1])
            i += 2
        elif follows and REDIRECTS.fullmatch(found[i]):
            i += 2
        else:
            kept.append(found[i])
            i += 1
    return kept, written, read


CONTROL = (";;&", "&&", "||", "|&", ";;", ";&", ";|", "&|", "&!", ";", "&", "|")
"""The operators that end a command, the longest first."""
REDIRECT = re.compile(r"&?[<>](?:[&|!]|[<>](?!\())*")
"""A redirect operator as any shell spells it (`>>&`, `>!`, `&>|`, `<>`), stopping before a process substitution."""
COMMENT = re.compile(r"[^\n]*")
TICKED_COMMENT = re.compile(r"[^\n`]*")
"""A comment inside backticks ends where they close."""
PLAIN = re.compile(r"[^ \t\n'\"\\$`;&|()<>#\[\]=]+")
QUOTED_PLAIN = re.compile(r"[^\"\\$`\n]+")
BRACE_PLAIN = re.compile(r"[^}'\"\\$`<>= \t\n]+")
BLANKS = re.compile(r"(?:[ \t]|\\\n)*")
DELIMITER = re.compile(r"""(?:'[^'\n]+'|"[^"\n$`\\]+"|\\[A-Za-z0-9_.-]|[A-Za-z0-9_.-])+(?=[ \t\n;&|<>)]|\Z)""")
"""A heredoc delimiter whose meaning is certain; any other one opens no body that is passed over."""
ANSI = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.S)
ANSI_ESCAPE = re.compile(r"\\(x[0-9a-fA-F]{1,2}|u[0-9a-fA-F]{1,4}|U[0-9a-fA-F]{1,8}|[0-7]{1,3}|c.|.)", re.S)
OPERATED = re.compile(r"\$\{!?(?:[A-Za-z_]\w*|\d+|[@*#?]):?[-=+]")
"""The start of an expansion that may stand its own word in the command (`${name:-word}`), which an unquoted one splits."""
BEFORE_A_COMMAND = frozenset({"if", "then", "else", "elif", "fi", "while", "until", "do", "done", "esac", "!", "{", "}", "coproc",
                              "time", "always", "noglob", "nocorrect", "-"})
"""Words of bash and zsh that may stand before the command they run."""
VERSIONED = re.compile(r"(python|pypy|nodejs|node|bun|ruby|perl|php|lua|osascript)w?(\d+(\.\d+)*[a-z]{0,2})?")
SIMPLE_WORD = re.compile(r"[^\s;&|()'\"`$<>\\]+")
SUBSTITUTED = "SUB"
"""The word, or the part of one, that stands where a substitution was."""


class _Redirect(str):
    """A redirect operator among the words of a command, told apart from a quoted word that spells one."""


@dataclass(frozen=True)
class Command:
    """One command: its words, the files its redirections write and read, and the words of its here-strings. ``fed``: its output
    goes into another command (it is in a substitution). ``loose``: read with the quotes ignored, where the reading was not sure."""

    words: list[str]
    written: list[str]
    read: list[str]
    strings: list[str]
    fed: bool
    loose: bool


@dataclass(frozen=True)
class Reading:
    commands: list[Command]
    """Every command at every level, a substitution's before the command that holds it."""
    bodies: list[str]
    """The heredoc bodies passed over."""
    kept: str
    """The text without its comments and the heredoc bodies passed over."""
    text: str
    sure: bool
    """Whether the pass knew everywhere what the shell passes over."""


class _Level:
    """The command being read in one list of commands: the whole line, or one substitution."""

    def __init__(self, fed: bool) -> None:
        self.fed = fed
        self.tokens: list[str] = []
        self.parts: list[str] = []
        self.word = False
        self.brackets = 0
        self.cases = 0


@dataclass
class _Frame:
    kind: str
    """`top`, `sub`, `tick`, `paren`, `dq`, `brace` or `body` (an unquoted heredoc's)."""
    level: _Level
    odd: bool = False
    """In arithmetic or an expansion, where `<<` is not surely a heredoc and `#` not surely a comment."""
    quoted: bool = False
    splits: bool = False
    nested: bool = False
    limit: int = 0
    resume: int = 0
    queue: list | None = None


@dataclass(frozen=True)
class _Heredoc:
    depth: int
    level: _Level
    word: str
    quoted: bool
    tabs: bool


def _joined(lines: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """The lines of an unquoted heredoc: one that ends in an odd number of backslashes goes on in the next."""
    out: list[tuple[str, int, int]] = []
    held: list[str] = []
    begun = -1
    for text, start, after in lines:
        begun = start if begun < 0 else begun
        if (len(text) - len(text.rstrip("\\"))) % 2:
            held.append(text[:-1])
        else:
            out.append(("".join([*held, text]), begun, after))
            held, begun = [], -1
    return out


def _escaped(found: re.Match) -> str:
    code = found.group(1)
    if len(code) > 1 and code[0] in "xuU":
        number = int(code[1:], 16)
    elif code[0] in "01234567":
        number = int(code, 8)
    elif len(code) == 2 and code[0] == "c":
        number = ord(code[1]) & 31
    else:
        return {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}.get(code, "\\" + code)
    return chr(number) if number <= 0x10FFFF else "\\" + code


class _Pass:
    """One pass over a text. With ``held``, the text is a word to be evaluated later: only its substitutions run."""

    def __init__(self, text: str, held: bool = False) -> None:
        self.text, self.at = text, 0
        self.stack = [_Frame("top", _Level(False))]
        self.commands: list[Command] = []
        self.cuts: list[tuple[int, int]] = []
        self.bodies: list[str] = []
        self.sure = True
        self.cutting = True
        self.breaks = True
        self.ticks: list[int] = []
        self.limits: list[int] = []
        self.pending: list[_Heredoc] = []
        self.lines: dict[tuple[bool, bool], dict[str, tuple[list[int], list[int]]]] = {}
        if held:
            self.stack.append(_Frame("body", _Level(False), limit=len(text), resume=len(text), queue=[]))
            self.limits.append(1)

    def run(self) -> None:
        while True:
            if self.limits and self.at >= self.stack[self.limits[-1]].limit:
                self.end_body()
            elif self.at >= len(self.text):
                break
            elif (kind := self.stack[-1].kind) in ("dq", "body"):
                self.in_quotes(kind)
            elif kind == "brace":
                self.in_braces()
            else:
                self.in_commands()
        while len(self.stack) > 1:
            self.sure = False
            self.pop()
        self.flush(self.stack[0].level)
        self.end(self.stack[0].level)
        self.sure = self.sure and not self.pending

    def limit(self) -> int:
        return self.stack[self.limits[-1]].limit if self.limits else len(self.text)

    def add(self, part: str) -> None:
        level = self.stack[-1].level
        level.parts.append(part)
        level.word = True

    def flush(self, level: _Level) -> None:
        """End the word being read."""
        if level.word:
            word = "".join(level.parts)
            if word in ("case", "esac") and (not level.tokens or level.tokens[-1] in BEFORE_A_COMMAND):
                level.cases = max(0, level.cases + (1 if word == "case" else -1))
            level.tokens.append(word)
        level.parts, level.word = [], False

    def end(self, level: _Level) -> None:
        """End the command being read."""
        if level.tokens:
            self.commands += _commands_of(level.tokens, level.fed, False)
            level.tokens = []

    def push(self, frame: _Frame) -> None:
        self.stack.append(frame)
        if frame.kind == "tick":
            self.ticks.append(len(self.stack) - 1)

    def pop(self) -> None:
        frame = self.stack.pop()
        if frame.kind in ("sub", "tick"):
            self.flush(frame.level)
            self.end(frame.level)
            self.add(SUBSTITUTED)
        if frame.kind == "tick":
            self.ticks.pop()
        elif frame.kind == "body":
            self.limits.pop()
        self.breaks = False

    def close(self, index: int) -> None:
        """Close the frame at ``index`` and any left open inside it."""
        while len(self.stack) > index + 1:
            self.sure = False
            self.pop()
        self.pop()

    def substitution(self, odd: bool) -> None:
        """A `$(`, `<(`, `>(` or `=(` just passed."""
        self.stack[-1].level.word = True
        self.push(_Frame("sub", _Level(True), odd=self.stack[-1].odd or odd))
        self.breaks = False

    def newline_inside(self) -> None:
        """A newline passed inside quotes or an expansion while a heredoc waits for its body: where that begins is not sure."""
        if self.pending:
            self.pending, self.sure = [], False

    def in_commands(self) -> None:
        text, at, frame = self.text, self.at, self.stack[-1]
        char, level = text[at], frame.level
        breaks, self.breaks = self.breaks, False
        if char in " \t":
            self.flush(level)
            self.at, self.breaks = at + 1, True
        elif char == "\n":
            self.at, self.breaks = at + 1, True
            self.flush(level)
            self.end(level)
            level.brackets = 0
            self.start_bodies(level)
        elif char == "#":
            self.hash(breaks)
        elif char == "\\":
            self.backslash(breaks)
        elif char == "'":
            self.single()
        elif char == '"':
            level.word = True
            self.push(_Frame("dq", level, odd=frame.odd))
            self.at += 1
        elif char == "$":
            self.dollar(False)
        elif char == "`":
            self.tick()
        elif char in "<>=" and text.startswith("(", at + 1) and (char != "=" or not level.word):
            self.at += 2
            self.substitution(False)
        elif char in "<>" or text.startswith("&>", at):
            self.redirect()
        elif char in ";&|":
            self.operator()
        elif char in "()":
            self.parenthesis(char)
        elif char in "[]":
            level.brackets = max(0, level.brackets + (1 if char == "[" else -1))
            self.add(char)
            self.at += 1
        else:
            found = PLAIN.match(text, at, self.limit())
            self.add(found.group(0) if found else char)
            self.at = found.end() if found else at + 1

    def hash(self, breaks: bool) -> None:
        """A `#`: the start of a comment only where every shell takes it for one; else a part of a word."""
        frame, level = self.stack[-1], self.stack[-1].level
        if not level.word and breaks and not frame.odd and not level.brackets:
            found = (TICKED_COMMENT if self.ticking() else COMMENT).match(self.text, self.at, self.limit())
            self.cuts.append((self.at, found.end()))
            self.at = found.end()
            return
        self.sure = self.sure and level.word  # one that begins a word may be a comment to one shell and not another
        self.add("#")
        self.at += 1

    def operator(self) -> None:
        level = self.stack[-1].level
        found = next(one for one in CONTROL if self.text.startswith(one, self.at))
        self.at, self.breaks = self.at + len(found), True
        self.flush(level)
        self.end(level)
        if ";" in found:
            level.brackets = 0

    def parenthesis(self, char: str) -> None:
        frame = self.stack[-1]
        self.flush(frame.level)
        self.end(frame.level)
        self.at += 1
        if char == "(":
            self.push(_Frame("paren", frame.level, odd=frame.odd or self.text.startswith("(", self.at)))
        elif frame.kind == "paren" or (frame.kind == "sub" and not frame.level.cases):
            self.pop()

    def backslash(self, breaks: bool) -> None:
        after = self.text[self.at + 1:self.at + 2]
        if after == "\n":
            self.breaks = breaks
        elif after == "`" and self.ticking():
            self.nested_tick()
            return
        else:
            self.add(after or "\\")
        self.at += 2

    def past_a_tick(self, end: int) -> bool:
        """Whether a quote from here to ``end`` would pass over the backtick that closes the substitution it stands in."""
        return self.ticking() and self.text.find("`", self.at, end) >= 0

    def single(self) -> None:
        end = self.text.find("'", self.at + 1, self.limit())
        if end < 0 or self.past_a_tick(end):  # such a quote hides nothing here: what follows is read
            self.sure = False
            self.add("'")
            self.at += 1
            return
        held = self.text[self.at + 1:end]
        if "\n" in held:
            self.newline_inside()
        self.add(held)
        self.at = end + 1

    def ticking(self) -> bool:
        """Whether a backtick is open that this text may close: one opened outside the heredoc body being read is not."""
        return bool(self.ticks) and not (self.limits and self.ticks[-1] < self.limits[-1])

    def tick(self) -> None:
        self.at += 1
        if self.ticking():
            self.close(self.ticks[-1])
        else:
            self.stack[-1].level.word = True
            self.push(_Frame("tick", _Level(True), odd=self.stack[-1].odd))

    def nested_tick(self) -> None:
        """An escaped backtick inside backticks opens or closes a substitution inside that one."""
        self.at += 2
        if self.stack[self.ticks[-1]].nested:
            self.close(self.ticks[-1])
        else:
            self.stack[-1].level.word = True
            self.push(_Frame("tick", _Level(True), odd=self.stack[-1].odd, nested=True))

    def dollar(self, quoted: bool) -> None:
        text, at, frame = self.text, self.at, self.stack[-1]
        after = text[at + 1:at + 2]
        if after == "(":
            self.at += 2
            self.substitution(text.startswith("(", at + 2))
        elif after == "{":
            self.braces(quoted)
        elif after == "'" and not quoted and (found := ANSI.match(text, at, self.limit())) and not self.past_a_tick(found.end()):
            held = ANSI_ESCAPE.sub(_escaped, found.group(1))
            if "\n" in found.group(1):
                self.newline_inside()
            self.add(held.split("\0", 1)[0])  # the shell ends the quoted part at a NUL
            self.at = found.end()
        elif after == '"' and not quoted:
            frame.level.word = True
            self.at += 1
        elif after and after in "$#?!@*-":
            self.add("$" + after)
            self.at += 2
        else:
            self.sure = self.sure and not (after == "'" and not quoted)
            self.add("$")
            self.at += 1

    def braces(self, quoted: bool) -> None:
        text, at, level = self.text, self.at, self.stack[-1].level
        after = text[at + 2:at + 3]
        if after in ("", " ", "\t", "\n", "|"):  # `${ command; }`: what follows is read as commands
            self.sure = False
            self.add("${")
            self.at += 2
            return
        self.sure = self.sure and after != "("  # zsh's flags may hold any quote or brace
        found = None if quoted else OPERATED.match(text, at, self.limit())
        if found:
            self.at = found.end()
        else:
            self.add("${")
            self.at += 2
        self.push(_Frame("brace", level, odd=True, quoted=quoted, splits=bool(found)))

    def in_braces(self) -> None:
        text, at, frame = self.text, self.at, self.stack[-1]
        char, level = text[at], frame.level
        if char == "}":
            if not frame.splits:
                self.add("}")
            self.at += 1
            self.pop()
        elif char == "'" and not frame.quoted:
            self.single()
        elif char == '"':
            level.word = True
            self.push(_Frame("dq", level, odd=True))
            self.at += 1
        elif char == "\\":
            self.backslash(False)
        elif char == "$":
            self.dollar(frame.quoted)
        elif char == "`":
            self.tick()
        elif char in "<>=" and not frame.quoted and text.startswith("(", at + 1) and (char != "=" or not level.word):
            self.at += 2
            self.substitution(False)
        elif char in " \t\n":
            if frame.splits:
                self.flush(level)
            else:
                self.add(char)
            if char == "\n":
                self.newline_inside()
            self.at += 1
        else:
            self.sure = self.sure and char != "'"  # a quote inside a quoted expansion: shells differ
            found = BRACE_PLAIN.match(text, at, self.limit())
            self.add(found.group(0) if found else char)
            self.at = found.end() if found else at + 1

    def in_quotes(self, kind: str) -> None:
        text, at = self.text, self.at
        char = text[at]
        if char == '"' and kind == "dq":
            self.at += 1
            self.pop()
        elif char == "\\":
            after = text[at + 1:at + 2]
            if after == "`" and self.ticking():
                self.nested_tick()
                return
            if after != "\n":
                self.add(after if after and after in '$`"\\' else "\\" + after)
            self.at += 2
        elif char == "$":
            self.dollar(True)
        elif char == "`":
            self.tick()
        elif char == "\n":
            self.newline_inside()
            self.add(char)
            self.at += 1
        else:
            found = QUOTED_PLAIN.match(text, at, self.limit())
            self.add(found.group(0) if found else char)
            self.at = found.end() if found else at + 1

    def redirect(self) -> None:
        level = self.stack[-1].level
        if level.word and re.fullmatch(r"\d+|\{\w+\}", "".join(level.parts)):
            level.parts, level.word = [], False  # the descriptor the redirect is for
        self.flush(level)
        found = REDIRECT.match(self.text, self.at, self.limit())
        operator, self.at = found.group(0), found.end()
        if (operator == "<<" or operator.endswith("&")) and self.text.startswith("-", self.at):
            operator, self.at = operator + "-", self.at + 1  # `<<-`, and `>&-`, which closes and takes no word
        level.tokens.append(_Redirect(operator))
        if operator in ("<<", "<<-"):
            self.heredoc(operator)

    def heredoc(self, operator: str) -> None:
        frame = self.stack[-1]
        self.at = BLANKS.match(self.text, self.at, self.limit()).end()
        found = DELIMITER.match(self.text, self.at, self.limit())
        raw = found.group(0) if found else ""
        quoted = any(c in raw for c in "'\"\\")
        if not found or frame.odd or frame.level.brackets or not (quoted or re.match(r"[A-Za-z_]", raw)):
            self.sure = False
            return
        word = re.sub(r"""\\(.)|'([^']*)'|"([^"]*)\"""", lambda m: next(g for g in m.groups() if g is not None), raw)
        self.pending.append(_Heredoc(len(self.stack), frame.level, word, quoted, operator == "<<-"))
        frame.level.tokens.pop()
        self.at = found.end()

    def start_bodies(self, level: _Level) -> None:
        """The newline just passed starts the bodies of the heredocs opened on its line, in the same list of commands."""
        if not self.pending:
            return
        mine = [h for h in self.pending if h.level is level and h.depth == len(self.stack)]
        self.sure = self.sure and len(mine) == len(self.pending)
        self.pending = []
        self.take(mine)

    def take(self, queue: list[_Heredoc]) -> None:
        """Pass over the bodies of ``queue``, which begin here. A body whose closing line is not found is not passed over."""
        while queue and self.cutting:
            heredoc = queue.pop(0)
            found = self.closing(heredoc, self.at)
            if found is None or found[0] >= self.limit():
                self.sure = False
                return
            start, (end, after) = self.at, found
            if self.closes_early(heredoc, self.text[start:end]):
                self.sure = self.cutting = False  # nothing more is passed over, so no body is looked through twice
                return
            self.cuts.append((start, after))
            self.bodies.append(self.text[start:end])
            if heredoc.quoted:
                self.at = after
                continue
            self.stack.append(_Frame("body", _Level(False), limit=end, resume=after, queue=queue))
            self.limits.append(len(self.stack) - 1)
            return

    def closes_early(self, heredoc: _Heredoc, body: str) -> bool:
        """Whether a shell may end the substitution around ``heredoc`` inside ``body``: at the backtick that closes it, or at a
        line that begins with the delimiter and the `)` that closes it (bash)."""
        if self.ticking() and "`" in body:
            return True
        inside = heredoc.level is not self.stack[0].level
        return inside and any(line.lstrip("\t").startswith(heredoc.word) and ")" in line for line in body.split("\n"))

    def end_body(self) -> None:
        index = self.limits[-1]
        frame = self.stack[index]
        self.close(index)
        self.at, self.breaks = frame.resume, True
        self.take(frame.queue)

    def closing(self, heredoc: _Heredoc, start: int) -> tuple[int, int] | None:
        """Where the line that closes ``heredoc`` starts and ends, at or after ``start``: the delimiter and nothing else. The lines
        of the text are indexed once, so that many heredocs cost no more than one."""
        key = (heredoc.quoted, heredoc.tabs)
        if key not in self.lines:
            lines, at = [], 0
            for line in self.text.split("\n"):
                lines.append((line, at, min(at + len(line) + 1, len(self.text))))
                at += len(line) + 1
            index: dict[str, tuple[list[int], list[int]]] = {}
            for line, begun, after in lines if heredoc.quoted else _joined(lines):
                held = index.setdefault(line.lstrip("\t") if heredoc.tabs else line, ([], []))
                held[0].append(begun)
                held[1].append(after)
            self.lines[key] = index
        starts, ends = self.lines[key].get(heredoc.word, ([], []))
        i = bisect.bisect_left(starts, start)
        return (starts[i], ends[i]) if i < len(starts) else None


def _stripped(found: list[str]) -> list[str]:
    """The words without what may stand before the command."""
    i = 0
    while i < len(found):
        if found[i] in BEFORE_A_COMMAND or (found[i] == "-p" and found[i - 1:i] == ["time"]):
            i += 1
        elif found[i] in ("function", "repeat"):
            i += 2
        else:
            break
    out = found[i:]
    while len(out) > 1 and out[0] == SUBSTITUTED:  # a substitution that prints nothing leaves the next word as the command
        out = out[1:]
    if out and out[0].startswith(SUBSTITUTED) and out[0][len(SUBSTITUTED):][:1] not in ("", "/"):
        out[0] = out[0][len(SUBSTITUTED):]
    if out and out[0].startswith("=") and len(out[0]) > 1:  # zsh's `=name`, the path of a program
        out[0] = out[0][1:]
    return out


def interpreter(name: str) -> str:
    """The name the rules know an interpreter by, when the program ``name`` is it with a version, a build letter or `w`
    (`python3.14t`, `pythonw`, `pypy3`); ``name`` otherwise."""
    found = VERSIONED.fullmatch(name)
    if found is None:
        return name
    return "python" if found.group(1) == "pypy" else found.group(1)


def _commands_of(tokens: list[str], fed: bool, loose: bool) -> list[Command]:
    """The commands the words and redirects of one stretch between operators make."""
    plain: list[str] = []
    written: list[str] = []
    read: list[str] = []
    strings: list[str] = []
    i = 0
    while i < len(tokens):
        found = tokens[i]
        if not isinstance(found, _Redirect):
            plain.append(found)
            i += 1
            continue
        target = tokens[i + 1] if i + 1 < len(tokens) and not isinstance(tokens[i + 1], _Redirect) else None
        if target is None or found.endswith("-") or (found.startswith("<<") and found != "<<<"):
            i += 1
            continue
        descriptor = found.endswith("&") and re.fullmatch(r"\d+-?|-", target)
        if found == "<<<":
            strings.append(target)
        elif ">" in found and not descriptor:
            written.append(target)
        if "<" in found and found != "<<<" and not descriptor:
            read.append(target)
        i += 2
    parts: list[list[str]] = [[]]
    for i, word in enumerate(plain):
        if word == "always" and plain[i - 1:i] == ["}"]:  # zsh's `{ ... } always { ... }`
            parts.append([])
        parts[-1].append(word)
        if word == "]]" and i + 1 < len(plain):  # zsh's `if [[ ... ]] command`
            parts.append([])
    out = [Command(_stripped(part), [], [], [], fed, loose) for part in parts[1:]]
    first = Command(_stripped(parts[0]), written, read, strings, fed, loose)
    return [one for one in (first, *out) if one.words or one.written or one.read or one.strings]


def _loosely(text: str) -> list[Command]:
    """Every line and every stretch between operators read as a command with its quotes ignored, for a text the pass was not sure
    of: nothing is passed over."""
    out: list[Command] = []
    for segment in re.split(r"[\n;&|()`]|\$\{(?=[ \t|])", text.replace("\\\n", "")):
        segment = re.sub(r"['\"\\]", "", OPERATED.sub(" ", segment))
        found = [_Redirect(t) if t[0] in "<>" else t.rstrip("}") if t.count("}") > t.count("{") else t
                 for t in re.findall(r"[<>]+!?|[^ \t<>]+", segment)]
        out += _commands_of([t for t in found if t], False, True)
    return out


def read(text: str) -> Reading:
    """``text`` read as the shell reads it, each character once."""
    one = _Pass(text)
    one.run()
    kept, last = [], 0
    for start, end in sorted(one.cuts):
        if start >= last:
            kept.append(text[last:start])
        last = max(last, end)
    kept.append(text[last:])
    found = one.commands if one.sure else [*one.commands, *_loosely(text)]
    return Reading(found, one.bodies, "".join(kept), text, one.sure)


def readings(text: str) -> list[Reading]:
    """``text`` as it stands and, when it holds a backslash at the end of a line, with those lines joined as the shell joins them
    even inside an operator."""
    return [read(one) for one in dict.fromkeys((text, text.replace("\\\n", "")))]


def held(word: str) -> list[Command]:
    """The commands in the substitutions of a quoted word, which a shell runs when the word is evaluated later (`trap`, `eval`,
    `let`, `printf -v`, `sh -c`)."""
    if "$(" not in word and "`" not in word:
        return []
    one = _Pass(word, held=True)
    one.run()
    return one.commands


def blanked(word: str) -> str:
    """``word`` for a rule that matches a pattern in text: one that holds quoted text stands as `_`, so no pattern is found in it."""
    return word if SIMPLE_WORD.fullmatch(word) else "_" if word else "''"


def other_texts(command: str, stripped: str, spell: Callable[[str], str] | None = None) -> list[str]:
    """The ways ``command`` reads besides ``stripped``, for a rule that reads text: without its comments and the heredoc bodies
    passed over, and as written where the reading is not sure. With ``spell``, also one line for each command, from its first word
    on, each word as ``spell`` writes it; a `cd` inside a substitution moves no later line, so it is left out."""
    found: list[str] = []
    for one in readings(command):
        found.append(one.kept)
        if not one.sure:
            found.append(one.text)
        if spell:
            found.append("\n".join(" ".join(map(spell, c.words)) for c in one.commands
                                   if not (c.loose or (c.fed and c.words[:1] in (["cd"], ["pushd"])))))
    return [text for text in dict.fromkeys(found) if text.strip() and text != stripped]
