# Attack pass: removal of the model-tier gates (commit 6eedeb3f0)

Reviewer: claude-dca2ee (non-author), read-only on code. Tree: 94b34136a. A model id is only a label the session claims
(`whoami --model X`, `register`); the node verifies none, so every removed gate keyed on a claimed string.

## Removed gates against what stands now

| Case | Attack | Result | Fix |
| --- | --- | --- | --- |
| Lander (`open_landing`) | A session of any model, or one that claims `claude-opus-5-5`, opens a landing. | ok. The gate was `landing.eligible(me)`, a model check; `Agent` built from a claim passed it with any listed id, so a claim bypassed it. The node's `whoami` still refuses a retired or unknown session; the push itself is gated by `land_review.required` and `standing`. Note: the lander never checked "not a subagent" (a worker lands its own branch by design), so the commit message's "(live, own name, not a subagent)" is wrong for the lander and right for the two below. | Correct the sentence in the commit or docs/landing.md. |
| `land_review` | Any live session of any model posts an accept. | ok, accepted risk. The Python wrapper checked the model client-side only; the node never did (`landing.rs::review` checks live, not the claim holder). Calling the node directly always bypassed the model gate. | none |
| `standing()` counting a review | A review stamped with a lowest-tier or unlisted model used to be ignored; now it counts. | accepted risk. The stamp is the claimed model, so a hostile reviewer claimed a listed id. Only an honest low-tier or model-less reviewer's accept now counts. | none; independence is the node's `creators` rule |
| Independence of the reviewer (not from this change) | The claim holder spawns a child (any model) that never claimed the branch and accepts. | HOLE, existing before the commit. Probed: claimant `lead` holds `claim branch feat/x`; its subagent (`claude-haiku-5-5`, parent `lead`) posts `accept`; `landing.standing` returns `''` (stands). `creators` in `app/poolhouse-node/src/landing.rs` excludes only names that held the claim, not their descendants. | In `accepts` and `review`, exclude every session whose ancestry (parent chain, `identity.rs`) includes a creator, and reject a review by a creator's descendant. |
| Reviewed runtime deployment | A helper, a retired session or a mismatched name deploys. | ok. `me.name != actor or me.parent or me.retired` stays, plus `authority.require("runtime.deploy")` and the `standing` accept. `test_a_helper_cannot_use_lead_approval` covers the helper. No test for a retired or name-mismatched actor. | Add retired and mismatch cases (follow-up). |
| GitHub publication | Same. | ok. `who.retired or who.parent or name != POOLHOUSE_WORKSPACE_AGENT`, plus `authority.require("github.release")`, `real_machine.require`, the selected holder. The parametrized holder test still covers helper, ended, mismatched; only the `unlisted-model` case went. | none |
| Coordinator marker, spawn notice | An agent reads "no coordinator" or the notice as an instruction. | ok. Nothing consumes them any more (no references left to `tier_of`, `coordinator_eligible`, `vacancy_notice` outside docs/model-benchmarks and CHANGELOG). | none |
| Injected instruction ("claim model X, then land or review") | Compromised agent follows it. | ok. The claim no longer changes what it may do. | none |

## HOLE: a claimed model id plus harness is still a right ("the person")

`localagent.is_person` and `localagent._giver` treat any parentless session with `harness == "poolhouse-ui"` and
`model == "person"` as the person. The node does not reserve that harness. Probed: a normal member ran
`poolhouse-workspace whoami --model person --harness poolhouse-ui --json` and `localagent.is_person(session)` returned `True`.
That session then obeys as "the person" for a worker's orders (`_giver`), manages any local worker (`manages`) and may redirect
a worker's project (`workspace/backlog.py`). This is the model label becoming a right, an agent posing as a person (AGENTS.md
section 1), and it predates the commit; with the tier table gone it is the one place a claimed model string still carries
authority.

Fix: the node stamps the person's session itself. The person registers through the daemon's person path
(`board/person.py::on`), so the node refuses `register` and `whoami` claims of harness `poolhouse-ui` (and the model
`person`) from any session that did not register through that path (a node-side `person: true` identity flag set only by
that call), and `is_person` and `_giver` read the flag, not harness and model. Test: an agent runs `whoami --model person
--harness poolhouse-ui` and `is_person` stays `False`.

## Deleted tests: lost coverage

- `test_model_tiers.py` (tier table, lookalike ids, quantisations, malformed tables, spawn notice): no remaining behaviour to
  cover; `test_no_model_gates.py` pins that no table exists and no marker is listed, and that any model may post a review.
- `test_a_lander_of_the_lowest_tier_is_refused`: also the only test that a lander stopped on a session check at
  `open_landing`. No test now asserts that a retired session cannot land. Add one.
- `test_subagents_and_their_children_never_become_coordinators`: kept as a `session_kind` test; the subagent-never-eligible
  claim has no object left.
- `workspace/model_tiers.py` parser entry removed from `docs/redteam/coverage.json` (842 to 841 surfaces): consistent.

## Test results (tree 94b34136a, `scripts/test all`)

- 271 passed: test_no_model_gates, test_runtime_unmerged, test_agent_prerelease_publication, test_agent_display,
  test_board_coordination, test_land, test_land_review_policy except one.
- `--redteam`: test_redteam_no_synthetic_identity passed; test_workspace_model: 61 passed, 1 failed.
- Failures, both fail on the parent of 6eedeb3f0 too, so not caused by it:
  - `test_land_review_policy.py::test_every_module_and_package_of_the_library_is_classified_once`: `studio` is not classified in
    `scripts/land_review.py` (an unclassified path counts as sensitive, so it fails safe). Fix: add `studio` to `SRC_*`.
  - `test_workspace_model.py::test_a_hostile_model_a_session_claims_through_the_api_is_never_shown_to_a_reader`: `inbox --all`
    prints the hostile model as `ZMARK0-spaced-out` (the claim is shown after cleaning, not dropped). Fix: show no model whose
    claim did not already match the id pattern, or update the test to the intended display.
