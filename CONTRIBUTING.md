# Contributing to Poolhouse

Issues and pull requests are welcome. This is a one-person, pre-1.0 project: expect slow reviews and changes to
names and layout. `AGENTS.md` holds the working rules in full; the short version:

- Use Python 3.13 in an isolated virtual environment. From a fresh clone:

  ```sh
  python3.13 -m venv .venv
  . .venv/bin/activate
  python -m pip install '.[test,privacy]' -r scripts/gates/pinned.txt
  python -m spacy download en_core_web_sm
  python scripts/preflight dev commit gate
  python scripts/install-hooks.py
  ```

  On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell. The install is a regular
  wheel install; tests load the checkout's source. The privacy extra and English model power the
  commit name checks. Preflight prints missing dependencies and the command that supplies them.
- Run affected tests with explicit selectors: `python scripts/test all tests/<affected-file>.py -n 1`.
  See [development.md](docs/development.md) for additional toolchains and checks.
- Commit subjects start with `feat:`, `fix:` or `chore:`; release-please builds the changelog from them.
- Comments say what the code does, not why it was written that way.
- Submit a pull request from a topic branch with the affected test command and result. Contributor
  completion means the proposed change is reviewable; maintainers own queue integration, development
  publication, installed-runtime update and hardware verification. Maintainer agents use a claimed
  sibling worktree and the landing rules in `AGENTS.md`.
- The gates in `scripts/gates/` refuse a new violation; run `python scripts/budgets` before submitting your pull request. A number in
  `budgets.json` may fall, never rise.
- Tests build their own fixtures with invented names and never read `~/.poolhouse`. No real names, emails, hostnames
  or home paths in files or commit messages (`scripts/hooks/no-real-names` checks this).
- A new dependency needs a licence compatible with Apache-2.0. `python scripts/notices.py` regenerates
  `THIRD_PARTY_NOTICES.md` and `--check` fails on a disallowed licence. GPL and AGPL packages stay optional extras that
  are never bundled.
- Do not copy code from a project whose licence is incompatible with Apache-2.0.

By contributing you agree that your contribution is licensed under Apache-2.0.

Security issues: see `SECURITY.md`; do not file them as public issues.
