# Commands an agent is refused, and why

Nothing in Poolhouse waits for a person. There is no typed yes, no phone tap, no "needs a terminal on stdin", no inbox answer
that a command parks on. Every action is decided at once by one of three rules, and every decision is audited with who ran it
(`authority-audit.jsonl` under the state root: `poolhouse.caller.audit_rows()`; `who` is the process or session, for example
`agent CLAUDECODE session s-1 pid 4242` or `user alice pid 77`).

1. **Upkeep runs for whoever runs it.** Sessions, tokens, pool founding and joining, phones, the device key, grants an agent may have,
   model downloads (a download that fails its checksum or scan is deleted and fetched again, never released) and the rest of the routine
   commands need no approval. The device's own key is made and enrolled the first time it is needed.
2. **A gate in the authority registry** (`poolhouse-workspace authority show`): `delegated` lets an agent through (audited), `person`
   refuses an agent at once with the reason. Both let a process with no agent marker through. An agent may narrow a gate; loosening one
   is refused to an agent and runs for a process with no agent marker.
3. **A protected action** is refused to an agent at once, with the reason, whatever the gates say. A process with no agent marker runs it
   directly, with no question. A person typing `! cmd` inside Claude Code inherits the agent marker and cannot be told from the agent, so
   the refusal says to run the command from a terminal outside the agent.

Refusal shape: `X is refused to an agent (CLAUDECODE is set): <why>. To do it, run it from a terminal outside the agent (...)`
(`poolhouse.caller.refuse_agent`).

## Protected actions (refused to an agent)

| Command | Enforced | Protects |
| --- | --- | --- |
| `poolhouse-workspace phone add`, `phone remove` | `workspace/phone_cli.py` | who can answer for this device |
| `grant`, `revoke-grant`, `distrust`, `grants-setup`, `person-enroll`, `person-unenroll`, `person-proof`, `approval decide`, `approval pending --all`, `approval blocks`, `lift` | `workspace/grants_cli.py`, `approval_cli.py` | an agent minting or widening its own standing grants (it asks with `approval request`, which policy decides) |
| `poolhouse-workspace chat` | `workspace/cli.py` | speaking on the board as the owner |
| `poolhouse-workspace quarantine-release` | `workspace/mail_cli.py` | an agent releasing the message that was held for it |
| `poolhouse-security keystore-reset`, `baseline --kind model --pin` | `net/cli.py`, `sentinel/cli.py` | the key that wraps every secret; what lets a model file load |
| onboard `signing export/rotate/revoke/accept`, `share --accept-licence` | `fleet/onboard/signing_cli.py`, `share_cli.py` | the signing key; accepting a licence |
| `poolhouse open` | `fleet/launch_open.py` | the sign-in ticket that makes the holder the owner of the device's page |
| `scripts/release-key`, `scripts/github-protection-apply --apply`, `scripts/agent-identity token` / `check --online` | the scripts | the release key, the repository's protection, the GitHub credential |
| `gh pr merge`, a push of `main`, edits to the guards, hook settings, git's hooks and config | `protectedwrites.py`, `worktreerules.py`, `scripts/hooks/*` | releases and the rules that protect them; allowed for `gh pr merge` / `main` only when `workspace/release_check.py` finds a standing independent land-review accept and a clean checkout |
| grants `member_remove`, `admin_manage`, `device_trust`, `quarantine_release` | `board/person_key.py` (`AGENT_REFUSED`) | another device's place in the pool, who governs it, which devices it counts, what a scan failed |
| chat tool calls and text naming the above | `chatpolicy.py` | the same actions through a model's tools |

## Gates (delegated in `dev`, held back from agents in `prod`)

`sentinel.quarantine`, `sentinel.policy`, `memory.admin`, `reputation.admin`, `activity.export`, `serve.wired-limit`,
`autostart.install`, `chat.policy`, `runtime.deploy`, `runtime.deploy-unmerged`, `github.release`, `workspace.setup`, `workspace.model`,
`workspace.agents`. `prod` keeps only `workspace.setup`, `workspace.model` and `workspace.agents` delegated.

A session registered while a live sibling runs it spends no grant until that sibling's lineage ends, which needs nobody (`grants/mod.rs`, `lineage_refusal`); `rebind` takes it over at once and is not a protected action.

## Not bound

A process that removes the agent marker from its own environment (`env -u CLAUDECODE ...`) is not recognised as an agent. The audit log
records who ran each action so this is detected afterwards, not stopped.
