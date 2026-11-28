---
name: prose-to-code
description: Find agent guidance that code should carry instead of prose (rules in AGENTS.md, policy and agent documents, skills, briefs and hook messages), then judge each one and offer to implement it. Use when the user asks which rules should become scripts, hooks, generated facts or checks, or wants less agent prose.
argument-hint: "[path ...]"
allowed-tools: Bash(python3 scripts/prose_to_code.py:*)
---

# Prose to code

The scan of `$ARGUMENTS` (every guidance file when empty), made just now:

!`python3 scripts/prose_to_code.py $ARGUMENTS`

The script found the sentences, their status, their ids and what changed since the last run; do not redo that by
hand. `scripts/prose_to_code.py --help` lists `--top`, `--json` and the record commands; add `--no-save` when you
run it again to read more, so the owner's next run still compares with this one.

## Judge the open sentences

Take `prose` first, then `unknown`, highest score first. Read the sentence in place and the code that would enforce
it, then do one of:

- **Already enforced**: record it, so no run asks again:
  `python3 scripts/prose_to_code.py --link ID --verdict enforced|partly --evidence "PATH::text in that file" --note "what is not covered"`
- **Encode**: pick the cheapest form that makes the right thing happen by itself, in this order: code does it
  automatically; a program generates the fact where the agent needs it; a check inside a command the agent already
  runs; a hook or gate that refuses. Name the harm before proposing a refusal.
- **Delete**: obsolete, duplicated, contradicted by the code, or no named harm.
- **Keep**: only judgment can follow it.

Record the last three with `--decide ID --decision encode|delete|keep --note "why"`.

## Report and offer

Rank candidates by harm, then by how often the rule is broken or costs time. Say how often only from something you
read (git history, refusal records, repeated copies the scan counted); otherwise say "not measured". For each give
file and line, the form, and what code would change. Include documents that claim an enforcement the code lacks.
Ask which to implement, then implement the chosen ones with fail-first tests, and delete or shorten the prose each
one replaces in the same change.
