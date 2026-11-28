# Session cards

A card is one session of one row of [HANDOFF.md](../../HANDOFF.md), written for the local model (Qwen3.8-Flash-Next, run by `poolhouse-delegate`).
HANDOFF.md stays the only backlog: a card has no priority and no state of its own. Its place is its row's (tier, row, `step`), and the change that
lands it deletes it. Where each run stands is kept under Poolhouse's state, and the app shows it under Delegates.

## A card file, `docs/cards/ID.toml`

| Field | Holds |
|---|---|
| `title`, `task` | What is to be done |
| `row` | The first words of its HANDOFF.md row |
| `read` | Files to read, or `path:first-last` lines of one |
| `write` | Files it may change |
| `check` | The acceptance check: `scripts/test all SELECTORS` or `test [!] -e PATH`, joined by `&&`. It must run inside the delegate's sandbox |
| `creates` | Files and test selectors the work itself makes |
| `step`, `after` | Order within the row, and cards that must land first |
| `closes` | Landing it finishes the row |
| `memory` | Whether its delegate has the project's memory (default true); set false to compare runs |

## Size

The budget is worked out from the tree each time: 30,000 tokens for the harness and its tools, what the card reads (3.5 characters a token, times
1.5 for rereads; a changed file not listed under `read` counts whole), 2,000 a changed file, 20,000 for the check's output and 20,000 for the model's
own work. The most is 110,000: half of the 222,822 tokens at which a 262,144 window is compacted. A run that comes within a tenth of that point is
sent back to be split.

## What is not a card

Security work (attack passes, access and trust rules, keys, guard code, confinement), checks and rules, pull request work (review, accept, merge,
branches other than the delegate's own), anything that needs the graphics device while it serves, a phone, a Windows computer or the Rust toolchain,
work that lives on an unmerged branch, and rows that still need a decision. Those are the lead's. `cards check` refuses a card that changes guard code
or whose check cannot run in the sandbox; the rest is the lead's judgement.

## Working them

```
poolhouse-delegate cards check                      # every card against the tree
poolhouse-delegate cards list                       # state, budget, what waits for the lead
poolhouse-delegate cards release --why TEXT         # the releasing session becomes every card's lead
poolhouse-delegate cards release --why TEXT --tier 5    # also work a paused tier
poolhouse-delegate cards run --detach               # one card at a time, when the model is free
poolhouse-delegate cards wait                       # run in the background: returns when a card needs the lead
poolhouse-delegate grade NAME correct --critique TEXT --check "RUN => SAW"
poolhouse-delegate cards split ID | retry ID | hold --why TEXT
```

The runner is held until a lead releases it, and again when `docs/cards/hold.txt` states a new reason. It starts a card only when the model is
already served with room for it, nothing else holds or claims the device and it has been unused for ten minutes; it never loads a model, and it
stops its delegate when someone else comes for the model. Finished work waits on the delegate's own branch: the lead reviews it, grades it, opens
the pull request and merges. A grade below `correct` rejects it, and the next run is given the reasons.

A delegate's first message carries the card, how to reach its lead and the project's rules, quoted from `AGENTS.md` and `docs/agent-policy.md`.
With memory it also carries what the project remembers about the files, and facts it proposes are saved only when the lead's checked grade accepts the
work.
