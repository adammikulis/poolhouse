# Commands an agent is refused

Inventory taken 2026-10-12 (read-only sweep of `src/poolhouse`, `app/poolhouse-node/src`, `scripts/hooks`, the authority registry). Classes: **HARD** is a floor (keystore, secrets, passphrases, the administrator prompt, signing a person's key, `main`, grants of authority) or a hard-coded refusal guarding identity or a trust root; **REGISTRY** is a gate in `poolhouse.authority` that the dev preset already delegates; **ROUTED** is refused to the session but a phone or a person's key can grant it; **CONVENIENCE** is a refusal with no security reason visible in the code. In the dev preset every registry gate is delegated; prod keeps only `workspace.setup`, `workspace.model` and `workspace.agents` delegated.

Refusal shapes: `X is for a person; this process was started by an agent (CLAUDECODE is set)` (`person.py:60`), `X needs a terminal on stdin and stdout` (`person.py:63`), `Only a person can X ... Run this in your own terminal` (`chatpolicy.py:138`).

## Hard

| Command | Enforced | Protects | An agent's route |
| --- | --- | --- | --- |
| `poolhouse-members roster add`, `adopt` | `fleet/members_cli.py:65,84` | a device joining a pool | node grant `add_device`, a phone approves |
| `poolhouse-members roster remove` | `fleet/members_cli.py:109` | removing a member, irreversible | `ph.pool.remove`, grant `member_remove`, a phone approves |
| `poolhouse-workspace phone add` (first phone), `phone remove` | `workspace/phone_cli.py:79,105` | the person key that approves grants | later phones: `phone offer`, an existing phone approves |
| `grant`, `revoke-grant`, `grants-setup`, `person-enroll`, `person-unenroll`, `person-proof` | `workspace/grants_cli.py:99` | grants of authority | `approval request ACTION`, a phone answers |
| `rebind HARNESS SESSION` | `grants_cli.py:258`, node `sessions.rs:81` | a session's tokens moving to another terminal | none |
| `approval decide`, `approval pending --all` | `workspace/approval_cli.py:144,87` | answering with the person key | agents request, status, wait, cancel their own |
| `poolhouse-workspace chat` | `workspace/cli.py:50` | speaking as the person on the board | none (AGENTS.md section 1) |
| `mint`, `delegate`; reads of the person record | `scripts/hooks/claude-bash-guard:114-119` | creating identities; the person's key and record | `poolhouse-workspace invite` |
| `poolhouse-security unlock`, `keystore-reset` | `net/cli.py:159,189` | the keystore | none |
| `poolhouse-node key --migrate` | `node_permission.py:115` | device key custody | none |
| recovery `passphrase` | `fleet/recovery.py:149` | the pool passphrase | none |
| onboard `signing export/rotate/revoke/confirm/accept` | `fleet/onboard/signing_cli.py` | the signing key | `signing show` is open |
| `poolhouse-security review` | `sentinel/review.py:209` | the review screen | none |
| `poolhouse-members open` | `fleet/members.py:212` | a signed-in ticket to the owner's page | none |
| `poolhouse-runtime ensure --allow-unmerged` | `runtime_cli.py:114` | releasing code not on the development branch | none |
| `poolhouse-workspace file delete`, `quarantine-release` | `workspace/filecli.py:292`, `mail_cli.py:123` | irreversible deletion; held messages | none through these |
| chat tool names matching release, approv, grant, mint, sentinel, polic, guard, role, rule, request, answer | `chatpolicy.py:90-155` | the same actions as chat tools | none |
| `git push` naming `main`; `--all`, `--tags`, `--mirror`, `--delete`, `--force` | `claude-bash-guard:102-113` | the owner's release; history | `main`: `person-consume propose`, a person selects the approval |
| `--no-verify`, `commit -n`, `SKIP_BUDGETS=`, `SKIP_NAME_CHECK=1` | `claude-bash-guard:120-127` | the last checks before the repo | fix the finding |
| budget raise (`--allow-increase`, editing `budgets.json`) | `scripts/hooks/budgets-only-fall:124` | the ratchet | report the overage |

## Registry (delegated in dev, a person's in prod)

| Command | Gate |
| --- | --- |
| `poolhouse-security release`, `purge`, `inspect`, `mode`, `approve-host`, `scan-policy` | `sentinel.quarantine`, `sentinel.policy` |
| `poolhouse-requests answer` | `requests.answer` |
| `poolhouse-memory ...` | `memory.admin` |
| `poolhouse-reputation ...` | `reputation.admin` |
| `poolhouse-log export` | `activity.export` |
| `poolhouse-runtime ensure`, `rollback`, `restart-host`, `pin` | `runtime.deploy` |
| recovery `export` (writes a file that runs commands on every pool machine) | `fleet.recovery` |
| `poolhouse-serve memory --apply` (wired limit, through the macOS administrator dialog; the sudo path needs a terminal) | `serve.wired-limit` |
| `poolhouse-workspace install-hooks` | `workspace.setup` |
| `poolhouse-workspace agent start`, `stop`, `supersede-issue`, `resume-issue` | `workspace.agents` |
| chat text: release or purge held content, approve a host, change sentinel or guard policy, plant baselines, `/role`, answer or cancel a request | `chat.policy` |

## Convenience, or refused with a reason that is not a security one

| Command | Enforced | Note |
| --- | --- | --- |
| `node_join join` | `node_join.py:59` | refused unless `--yes`; the flag is the only check |
| `ph.pool.listen()` | `api/pool.py:148` | refused unless `agreed=True`; self-declared |
| `poolhouse-requests list`, `show`, `watch` | `inbox/cli.py:29-33` | read-only; `approval list` and `status` show an agent's own requests |
| `poolhouse-serve up --iq` through MCP | `mcp.py:219` | a model-quality choice; the CLI has no check |
| autostart `install`, `rollback` | `fleet/autostart_apply.py:198,250` | AGENTS.md section 1 keeps these a person's (login persistence); `prepare` is open |

## Refused by environment, not by design

- Any command run in an agent's shell is refused, including one the person types: `person.marked()` reads `CLAUDECODE`, `POOLHOUSE_AGENT` and `POOLHOUSE_NONINTERACTIVE` (`person.py:24,32-35`). A person typing a person-only command after `!` in the Claude Code prompt is refused too.
- `person.require_person()` checks only that stdin and stdout are terminals (`person.py:53-63`), so an agent that unsets the marker and runs under a pseudo-terminal passes every T-AGENT gate that has no typed yes. The marker and the terminal are the only boundary.
- A session registered from another process lineage gets no grants until a person runs `rebind` (`grants/mod.rs:137`).
- Budget raises are refused whenever `CLAUDECODE` is set, even for a person's commit.
- Silent drops: `keystore.py:151` (no prompt when marked) and `fleet/runtime_paths.py:31` (the pool token line is left out).

## For the owner

- A lead agent can run `authority set` and `authority preset`; only subagents are refused (`authority_cli.py:49-54`). No gate protects the registry.
- In dev an agent can answer requests (`requests.answer`) and export the recovery key (`fleet.recovery`).
- The onboarding journey (AGENTS.md section 2) is blocked today by: the network-on step in a terminal, the first phone, `roster add` for devices the beacon does not reach, autostart `install`, the owner's page (`members open`), and the hidden pool token.
