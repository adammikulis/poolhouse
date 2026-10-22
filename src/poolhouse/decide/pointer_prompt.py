"""The text a pointer-head model reads, and where in it each option is scored from."""

from __future__ import annotations

from dataclasses import dataclass

from poolhouse.decide.logprob import closed
from poolhouse.decide.types import Option

HEADER = "Select exactly one option."

MAX_PROMPT_TOKENS = 4096
"""The longest prompt, in tokens, a pointer decider reads and a trainer trains on: the one limit.

It is the window the released Strands decider saves with its checkpoint and its reference recipe
sets (`max_length: 4096`, configs/train.yaml:39 of github.com/strands-labs/strands-decider), and
the base model's own window is far larger (Qwen3.5-2B-Base `max_position_embeddings` 262144)."""


def too_long(length: int, what: str) -> str:
    """The refusal for ``what`` that is ``length`` tokens, naming the limit and the constant."""
    return (f"{what} is {length} tokens; the limit is {MAX_PROMPT_TOKENS} "
            "(poolhouse.decide.pointer_prompt.MAX_PROMPT_TOKENS)")


@dataclass(frozen=True, slots=True)
class Rendered:
    """A prompt and the character span of each option's line within it."""

    text: str
    spans: tuple[tuple[int, int], ...]


def render(question: str, state: str, options: tuple[Option, ...]) -> Rendered:
    """The prompt for a choice question: the state, then the numbered options, then ``<answer>``.

    The state goes through ``closed`` first. Each option is one line, ``1. name - description``, with whitespace in the description
    collapsed so that a line is a span.
    """
    prefix = (f"<state>\n{closed(state)}\n</state>\n"
              f'<question type="choice">\n{HEADER}\n{question}\n<options>\n')
    lines, spans, cursor = [], [], len(prefix)
    for i, option in enumerate(options):
        desc = " ".join(option.description.split())
        line = f"{i + 1}. {option.name}" + (f" — {desc}" if desc else "")
        lines.append(line)
        spans.append((cursor, cursor + len(line)))
        cursor += len(line) + 1
    return Rendered(prefix + "\n".join(lines) + "\n</options>\n</question>\n<answer>",
                    tuple(spans))


def positions(offsets: list[tuple[int, int]], spans: tuple[tuple[int, int], ...]) -> list[int]:
    """For each span, the index of the last token that ends inside it."""
    found = []
    for start, end in spans:
        inside = [i for i, (a, b) in enumerate(offsets) if b > a and a >= start and b <= end]
        if not inside:
            raise ValueError(f"no token covers the option line at characters {start}-{end}")
        found.append(inside[-1])
    return found
