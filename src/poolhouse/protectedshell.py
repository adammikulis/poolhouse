"""How a shell command line is read for the rules: its quotes, comments, operators, substitutions, expansions and heredocs, found in one
pass as the shell finds them, so that no one of them hides what another holds.

Standard library only: `protectedwrites` loads this file by file.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

OPERATORS = ("&>>", "<<<", "<<-", ";;&", "&&", "||", "|&", ";;", ";&", "&>", ">>", ">|", ">&", "<&", "<>", "<<", ";", "&", "|", "(", ")",
             "<", ">")
"""The shell's operators, the longest first."""
BLANK = " \t\r"
DEEPEST = 64
LONGEST_ARITHMETIC = 4096


class TooDeep(ValueError):
    """Substitutions nested past what is read."""


@dataclass(frozen=True, slots=True)
class Read:
    flat: str
    """The command without comments and heredoc bodies, newlines as `;`, operators apart, each substitution as the word SUB."""
    subs: list[str]
    """The text of each substitution, to be read as a command of its own."""
    bodies: list[str]
    """The heredoc bodies."""
    kept: str
    """The command as written, without its heredoc bodies and the lines that close them."""


class _Reader:
    def __init__(self, text: str) -> None:
        self.text, self.at = text, 0
        self.subs: list[str] = []
        self.bodies: list[str] = []
        self.cut: list[tuple[int, int]] = []

    def commands(self, depth: int = 0, closing: bool = False) -> str:
        """The flattened commands from here to the end, or (``closing``) to the `)` that closes a substitution."""
        if depth > DEEPEST:
            raise TooDeep(f"substitutions nested more than {DEEPEST} deep")
        out: list[str] = []
        waiting: list[tuple[str, bool, bool]] = []
        new_word, open_groups = True, 0
        text = self.text
        while self.at < len(text):
            char = text[self.at]
            starts, new_word = new_word, False
            if char in BLANK:
                out.append(" ")
                self.at, new_word = self.at + 1, True
            elif char == "#" and starts:
                end = text.find("\n", self.at)
                self.at = len(text) if end < 0 else end
            elif char == "\n":
                self.at += 1
                for heredoc in waiting:
                    self.body(heredoc, depth)
                waiting = []
                out.append(" ; ")
                new_word = True
            elif text.startswith(("<(", ">("), self.at):
                self.at += 2
                out.append(self.substitution(depth))
            elif starts and text.startswith("((", self.at) and (end := self.arithmetic(self.at + 2, depth)):
                self.at, new_word = end, True
                out.append(" ARITH ")
            elif char in ";&|()<>":
                operator = next(one for one in OPERATORS if text.startswith(one, self.at))
                self.at += len(operator)
                if operator == ")" and closing and not open_groups:
                    return "".join(out)
                open_groups += (operator == "(") - (operator == ")")
                out.append(f" {operator} ")
                new_word = True
                if operator in ("<<", "<<-"):
                    word, quoted = self.delimiter()
                    if word:
                        waiting.append((word, quoted, operator == "<<-"))
                        out.append(f"{shlex.quote(word)} ")
            else:
                new_word = starts if text.startswith("\\\n", self.at) else False
                out.append(self.piece(depth))
        return "".join(out)

    def piece(self, depth: int) -> str:
        """The next part of a word: an escape, a quoted text, an expansion or one character."""
        text, char = self.text, self.text[self.at]
        if char == "\\":
            escape = text[self.at:self.at + 2]
            self.at += 2
            return "" if escape == "\\\n" else escape
        if char == "'":
            end = text.find("'", self.at + 1)
            end = len(text) if end < 0 else end + 1
            found, self.at = text[self.at:end], end
            return found
        if char == '"':
            self.at += 1
            return '"' + self.quoted('"', depth)
        if char == "$":
            return self.dollar(depth, quoted=False)
        if char == "`":
            return self.backticks()
        self.at += 1
        return char

    def quoted(self, closer: str, depth: int) -> str:
        """Text in which only escapes, `$` forms and backticks mean anything, up to ``closer`` (kept) or the end."""
        out: list[str] = []
        while self.at < len(self.text):
            char = self.text[self.at]
            if char == closer:
                self.at += 1
                out.append(char)
                break
            if char == "\\":
                out.append(self.text[self.at:self.at + 2])
                self.at += 2
            elif char == "$":
                out.append(self.dollar(depth, quoted=True))
            elif char == "`":
                out.append(self.backticks())
            else:
                out.append(char)
                self.at += 1
        return "".join(out)

    def dollar(self, depth: int, quoted: bool) -> str:
        text, at = self.text, self.at
        if text.startswith("$$", at):
            self.at += 2
            return "$$"
        if text.startswith("$((", at) and (end := self.arithmetic(at + 3, depth)):
            self.at = end
            return "ARITH"
        if text.startswith("$(", at):
            self.at += 2
            return self.substitution(depth)
        if text.startswith("${", at):
            found = self.braces(depth + 1)
            return found if quoted else shlex.quote(found)
        if text.startswith("$'", at) and not quoted:
            found = re.compile(r"\$'((?:[^'\\]|\\.)*)'?", re.S).match(text, at)
            self.at = found.end()
            return shlex.quote(re.sub(r"\\(x[0-9a-fA-F]{1,2}|[0-7]{1,3}|.)", _escaped, found.group(1), flags=re.S))
        self.at += 1
        return "$"

    def braces(self, depth: int) -> str:
        """A `${...}` expansion as written, its substitutions read."""
        if depth > DEEPEST:
            raise TooDeep(f"expansions nested more than {DEEPEST} deep")
        out = ["${"]
        self.at += 2
        while self.at < len(self.text):
            if self.text[self.at] == "}":
                self.at += 1
                out.append("}")
                break
            out.append(self.dollar(depth, quoted=True) if self.text[self.at] == "$" else self.piece(depth))
        return "".join(out)

    def substitution(self, depth: int) -> str:
        """Read a substitution whose `(` is behind, keep its text to be read as a command, and stand SUB for it."""
        start, held = self.at, len(self.subs)
        self.commands(depth + 1, closing=True)
        end = self.at - 1 if self.text[self.at - 1:self.at] == ")" else self.at
        self.subs[held:] = [self.text[start:end]]
        return "SUB"

    def backticks(self) -> str:
        found = re.compile(r"`((?:[^`\\]|\\.)*)`?", re.S).match(self.text, self.at)
        self.at = found.end()
        self.subs.append(re.sub(r"\\([`\\$])", r"\1", found.group(1)))
        return "SUB"

    def arithmetic(self, start: int, depth: int) -> int:
        """Where the arithmetic that begins at ``start`` ends, past its `))`; 0 when this is not arithmetic, or is longer than
        `LONGEST_ARITHMETIC` (it is then read as commands). The reader does not move."""
        level, at, held = 0, start, self.at
        end = 0
        while at < min(len(self.text), start + LONGEST_ARITHMETIC):
            if self.text.startswith("$(", at) and not self.text.startswith("$((", at):
                self.at = at + 2
                self.substitution(depth)
                at = self.at
                continue
            if self.text[at] == "(":
                level += 1
            elif self.text[at] == ")":
                if not level:
                    end = at + 2 if self.text[at + 1:at + 2] == ")" else 0
                    break
                level -= 1
            at += 1
        self.at = held
        return end

    def delimiter(self) -> tuple[str, bool]:
        """The word after a heredoc operator without its quotes, and whether it held any."""
        while self.at < len(self.text) and self.text[self.at] in BLANK:
            self.at += 1
        found = re.compile(r"(?:'[^'\n]*'|\"[^\"\n]*\"|\\.|[^\s;&|()<>'\"\\])*").match(self.text, self.at)
        self.at = found.end()
        word = found.group(0)
        return re.sub(r"\\(.)|['\"]", r"\1", word), bool(re.search(r"['\"\\]", word))

    def body(self, heredoc: tuple[str, bool, bool], depth: int) -> None:
        """Take the body of a heredoc, which starts here; the substitutions of one with a bare delimiter run, so they are read."""
        word, quoted, _ = heredoc
        start, lines = self.at, []
        while self.at < len(self.text):
            end = self.text.find("\n", self.at)
            end = len(self.text) if end < 0 else end
            line, self.at = self.text[self.at:end], end + 1
            if line.strip() == word:
                break
            lines.append(line)
        self.cut.append((start, self.at))
        self.bodies.append("\n".join(lines))
        if not quoted:
            inner = _Reader("\n".join(lines))
            inner.quoted("", depth + 1)
            self.subs += inner.subs


def _escaped(found: re.Match) -> str:
    code = found.group(1)
    if code[0] == "x" and len(code) > 1:
        return chr(int(code[1:], 16))
    if code[0] in "01234567":
        return chr(int(code, 8))
    return {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}.get(code, "\\" + code)


def read(command: str) -> Read:
    """``command`` as the shell reads it; `TooDeep` past `DEEPEST` nested substitutions."""
    reader = _Reader(command)
    flat = reader.commands()
    kept, last = [], 0
    for start, end in sorted(reader.cut):
        if start >= last:
            kept.append(command[last:start])
        last = max(last, end)
    return Read(flat, reader.subs, reader.bodies, "".join([*kept, command[last:]]))


def words(flat: str) -> list[str]:
    """The words and operators of a flattened command; split at blanks where its quotes do not close."""
    lexer = shlex.shlex(flat, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # the comments are out already; a `#` left here is part of a word
    try:
        return list(lexer)
    except ValueError:
        return flat.split()
