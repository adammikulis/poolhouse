# The agent rules in detail

[AGENTS.md](../AGENTS.md) holds the rules every agent follows. This file holds their mechanics and the long
reference text that would make it longer, grouped by the AGENTS.md section each belongs to. A rule stated there
is not restated here except where the detail adds to it; where the two meet, AGENTS.md wins.

## Workspace and coordination (AGENTS.md, section 3)

### Identity and registration

- A subagent's workspace access comes with starting it: no invite, no paste, no token. The SubagentStart hook
  registers it with `spawn`; the board gives it a unique name (family plus six characters of its agent id), records
  the session that started it as its parent and gives it its own token. It runs every workspace command with
  `--agent NAME`, and the Bash guard refuses a workspace command that omits it (docs/workspace.md, "Subagents").
- Every subagent prompt carries: "You are the subagent NAME, spawned by PARENT. Run workspace commands with
  `--agent NAME`." `poolhouse-workspace brief --agent NAME` prints the long form.
- A main session's name is its model family plus the six-character suffix of its session identity (for example
  `claude-6e1a2f`). The SessionStart hook assigns it, registers the session on the node and exports it to its
  commands, so a main session runs workspace commands as itself, with no `--agent`. A shared harness name is refused
  with a message naming the session's own id. The lead is joined to the Poolhouse workspace under its own name
  (docs/workspace.md, "Session names").
- Commands: `announce KIND TEXT`, `inbox`, `send TO KIND TEXT`, `thread SEQ`, `claim KIND KEY`, `who KIND KEY`.
- Agents that are not subagents (Codex, a local model) are registered on the node by their harness hook or
  launcher: the node names the session, records its parent and keeps only the hash of its token.
- Local harness launchers and authenticated workspace commands establish the session automatically under the
  current OS account. The client's token files are internal state: the owner never copies a token or runs a
  command for each session.
- The registration records a subagent's model and `whoami --model` a main session's; the model is a label, never a
  right (docs/workspace.md, "Which model is it").

### Shared ownership

Shared claims are enforced by maintained mutation and launch tooling, not advisory board messages (docs/workspace.md,
"Mutation ownership").

- Before editing a shared file area, using a worktree or port, changing an installed runtime, or reserving
  model/compute resources, acquire the applicable authenticated claim or broker allocation. File areas use
  repository identity plus relative path across worktrees; physical worktree claims stay separate. Claim only the
  required area, not an entire repository that would serialize unrelated changes.
- A clean checkout can fast-forward or switch to an exact independently reviewed commit under its physical worktree
  and branch claims. Do not reserve every incoming source file for that baseline move, and preserve unique branch
  history before switching. New source edits, custom staging and conflict resolutions still require their named
  source-area claims.
- Overlap checks and handoffs are atomic. Record the authenticated owner, actual process identity and birth time,
  source commit and environment where the maintained allocation supports them.
- Claims never grant project permissions, bypass person approval or permit another agent's credentials; tool policy
  and registry/project scope remain required. Broker-controlled resources use that broker's authoritative lease, not
  a parallel informal GPU lock.
- A claim covers a step: heartbeat while working, release when idle, never hold one across a wait. Ask a stale
  claim's owner once, then use the documented recovery. Refresh active claims before expiry. An expired claim or dead worker needs verified recovery or an explicit
  task-scoped handoff before mutation; do not infer permission from an old PID or steal a live claim. Use
  authenticated task-scoped handoffs rather than stealing, preserve checkpoints and exact task/resource identity
  during recovery, and do not let released or expired ownership race a new reservation. Read-only inspection needs
  no mutation claim; ambiguous destructive commands are refused or confined to the explicitly claimed authorized
  project.

### Delegation, accounts and limits

- **Delegate by capability and difficulty.** Select an available model and harness using the work's required
  capabilities, measured benchmark/task evidence, context needs, latency, resource availability and expected cost.
  The brief names the exact model/runtime and the required outcome. Keep independent review and name exact runtime
  provenance. Read-only lookup, routine implementation and architecture/security diagnosis have different
  requirements; no fixed vendor model name determines which may edit. A model that has not demonstrated the needed
  capability gets a bounded trial with independent review. Escalate after evidence of failure or increased
  difficulty, rather than equating a larger model with correctness. Benchmark claims name the run, environment and
  artifact. Model changes do not change the authenticated worker identity. Economic accounts aggregate
  independently verified contributions by model family across devices; exact workers, devices and model loads
  remain provenance, and a family switch does not rebucket historical awards (docs/workspace.md, "Model-family
  accounts and device provenance").
- **Limits are independent settings:** reasoning effort, response output tokens, context length, tool/model turns,
  wall time, and CPU/GPU or memory admission (docs/workspace.md, "Task limits"; docs/response-limits.md). Do not
  derive an answer cap from a thinking level or silently overwrite an explicit caller budget. Expose intentional
  user-facing limits under clearly labelled controls, with advanced settings collapsed and effective defaults
  inspectable. Preserve parser, authentication and security size bounds as distinct protections. A bounded task that
  exhausts a limit records that outcome, checkpoint and reason; activity or GPU usage alone is not completion.
  User-facing generated responses stream real model deltas by default, with cancellation, rather than splitting a
  completed answer into simulated chunks.

### Agent profiles and names

- Every agent profile contains actual device information; an empty `{}` is incomplete. Capture local hardware
  observations or authoritative enrollment metadata automatically, with their source and confidence. Subagents
  inherit their authenticated parent's device and label that provenance as inherited, never as independent hardware
  verification. Keep unavailable fields unknown and request correction from the owning agent; do not infer device
  facts from model, harness or display name, or add manual credential steps to development mode.
- Use distinct readable model-family names with a stable six-character suffix derived from the complete session
  identity, extended on collision. The suffix is presentation, not authority. Keep the exact model identifier and
  harness separate: ChatGPT is a model family, Codex is a harness, and Qwen may use Codex. Mark subagents explicitly
  and preserve their parent linkage; transient helpers do not become coordinators through naming or model labels.

## Nothing is cemented (AGENTS.md, section 2, "Nothing is cemented until 1.0")

What is refused is the *accidental* version of a change: `tests/test_asking_is_the_same_asking.py` and
`graph/cache.py:fingerprint` catch bytes moving when nobody meant them to. They are detectors, not vetoes. A red
you can explain is a change; a red you cannot is a bug.

## Release phases (AGENTS.md, section 2, "Nothing is cemented until 1.0")

There are no users but the owner until a release phase says otherwise (docs/service.md, section 6). Phase 0 (the
owner's own pools) is current: no migration, compatibility or fallback for state or callers nobody holds. From the
first release that an external person runs (phase 1), three things start and only those: a persisted format or wire
message that leaves the owner's machines changes only with a version and a migration; an interface an outside party
calls is not renamed without a note in the release notes; a security-relevant default stays refused. Everything else
in AGENTS.md still holds: no hypothetical consumer, no wrapper for an old name, no fallback "just in case". The first
external release is named by the owner and recorded in AGENTS.md. Design for the service now without building for it:
review new features against the checklist in docs/service.md, "Assumptions to bake in now". It is a prompt, not a
gate.

## Git, worktrees and landing (AGENTS.md, section 4)

### The runtime and the latest build

- The development Python (plain `python3`, tests, scripts) may be an editable install of the primary checkout, so it
  follows what is merged; pip rewrites the `poolhouse*` launchers when it installs, so restore them from a backup or
  with `poolhouse runtime ensure`. Live runtimes use an immutable built wheel or pinned runtime tree with matching
  distribution metadata. Never point a running worker at a changing checkout. Replace only owned processes at a
  coordinated safe boundary, preserving their identity and setup; do not interrupt another process's active work.
- **Every agent runs the latest build, automatically.** On every device the newest merged development build is
  installed without anyone asking for it: a session start refreshes the runtime, a push or merge that lands refreshes
  it, the `runtime-ensure` login unit refreshes it in the background, and a device that joins a pool takes the pool's
  build before it takes work. A person never runs an install or an update by hand, and an agent never waits for one:
  it reports a stale build as a defect and runs `poolhouse runtime ensure` itself. A device on an older build than
  the pool is out of the pool's protocol (signing labels and wire operations change between builds), so a stale
  device is brought up to date before anything else, and a brief or a doc never says "update first" as a step for
  the owner. The owner's pyenv install, launchers and running node are replaced at the coordinated safe boundary
  above, never mid-run (docs/install.md, "Following the source checkout").

### Landing in detail

- **Commit, merge and sync continuously.** Every agent commits completed, bounded pieces by named files in its
  claimed branch and promptly obtains independent review and affected checks. Authorized main agents integrate and
  push their reviewed batches themselves; no designated publisher or coordinator acknowledgment is required.
  Serialize development mutations with a short-lived checked branch claim, fetch before integration and again
  before push, reconcile remote advancement, and run the required combined gates. Release the claim promptly after
  sync. A busy claim delays only that integration; continue useful work in the claimed leaf tree. Managed subagents
  retain their assigned permissions and send ready commits to their parent; the parent carries integration and
  pushing through without waiting for another publisher. Do not accumulate ready branches or local-only commits until
  the end of a session, wait for every worker to finish, or defer publication behind cleanup, unrelated work or
  background full suites. After publication, promptly complete the merged branch's maintained worktree cleanup.
- **Pull and push as work lands.** Fetch before preparing each integration batch and again immediately before
  pushing. Reconcile upstream advancement, conflicts included, in an isolated integration worktree, review the
  resulting changes and run the incremental affected gates before landing the exact tree; equivalent recovered
  patches do not require repeated tests. Push the development branch normally after its batch gates pass; if another
  device advances the remote before the push succeeds, fetch, reconcile and gate the new changes, then retry a normal
  push. Every agent that starts a task, finishes a task or returns from a wait fetches and fast-forwards the
  development branch first, so no one works on history another device has already moved. Keep local and upstream
  development synchronized throughout landing batches and cleanup; report the resulting commit and final upstream
  state.
- **Keep landing short.** Throughput is limited by unlanded work, not typing speed. Branches are cut from
  current development, stay under ten commits, and land the moment they are ready and verified: rebase, verify,
  fast-forward and push straight away, with no other work in between. A ready branch never waits for a day, an hour
  or another task. Land a stack bottom-up, one layer at a time. Past the branch limits (more than 30 commits ahead or 20
  behind development; `scripts/worktrees` `MAX_AHEAD` = 30; AGENTS.md, "Branch hygiene and landing"), split a branch into
  separately landing batches instead of carrying it. Run `git cherry <dev> <branch>` before porting and
  drop equivalent patches. Record the tree hash a gate ran on; a tree already gated is not gated again. Finish before
  starting: no new branch while your own ready branches await landing; about ten open worktrees at most per lead.
- **`scripts/land BRANCH`** lands one branch: it fetches, rebases when no review is needed, checks the accept, runs the
  checks the diff calls for in a clean environment, pushes exactly the tested commit to the development branch (never
  `main`, never forced), fast-forwards the primary checkout and removes the worktree and branch. One lander runs at a time;
  a stop names its cause on the last line, and running it again repairs an interrupted run (docs/landing.md).
- The primary development checkout may receive reviewed changes through a fast-forward merge or authorized direct
  edits and named-file commits; resolve conflicts in an isolated worktree. The retained primary checkout is never
  removed as temporary-worktree cleanup. Coding tasks own their worktree lifecycle: assignment reserves the path and
  branch, claiming creates the checkout, independent review accepts the proposal, and gated integration lands the
  work and verifies cleanup before completion (docs/tasks.md); a worker report cannot bypass its completion gate.

### Orphans and nested trees

`scripts/worktrees` shows every tree with its owner. An *orphan* is a tree whose owner stopped more than the grace
ago (2 hours by default) and which still holds work nobody landed, bundled or abandoned; before the grace passes it
is "finished, waiting to land". A tree whose owner is running, or whose owner is unknown and younger than the grace,
is never an orphan, and only orphans fail the `orphan-trees` gate. Decide one with `scripts/worktrees close TREE
--landed`, `--bundle` or `--abandon 'reason'` (docs/worktrees.md). Cleanup applies to documentation, investigations
that created a worktree, cancelled tasks and subagents too. If work must remain, name the path, branch, pending work
and responsible agent in the handoff; do not report it as complete. The lead checks cleanup for every branch it
requested. Existing nested worktrees get the same preservation checks before removal; an unregistered directory is
not proof that it is disposable.

## Testing and gates (AGENTS.md, section 5)

### Gate cost, Linux and the broker

- Gate cost is per batch: combine all ready leaves into one run and start it whenever a leaf is ready and no gate is
  in flight. Scale it to the diff: documentation-only or handoff-only batches need a clean diff and `scripts/budgets`.
  A background full-suite failure blocks only landings touching the failing surface and gets one named owner at once.
  `scripts/land` applies this: documentation-only diffs get `scripts/budgets` and a clean diff; code diffs get
  `scripts/test gate` and the affected selectors of the diff, plus one full run only for shared infrastructure, a path no
  selector maps or a large diff. A tree that already passed a check is not checked again (`reused from <id>`), and a
  failure that also fails on the clean target is `baseline`.
- Known failures remain named tasks with evidence and ownership. A scoped pass is not a full-suite pass. Fix a known
  relevant regression before the affected change lands. A missing cold selector cache does not require a full suite
  for a leaf change; use reviewed explicit affected tests.
- **Linux testing is active and is not paused.** Use the available WSL device for Linux verification, under the same
  reviewed-selector and background-suite policy as other platforms. Report the exact device, operating system,
  runtime and tested commit; a Mac result or historical Linux receipt is not verification of the current WSL setup.
  Report platform gaps and known failures honestly. Do not run a second full suite before every merge.
- Use the maintained test broker for every run; shared CPU/GPU admission and ownership apply before work starts. Do
  not bypass a queue, start a competing full run, or extend a temporary preview pause indefinitely to obtain a quiet
  gate. Attribute external writes from actual writer evidence; unknown or test-owned writes still fail isolation
  checks. `scripts/test` and `scripts/test-on-linux` acquire maintained CPU admission;
  `scripts/testslots.py status` shows ownership. Model-backed work also requires the model/GPU broker grant. A CPU
  lease is not a GPU lease, and an inherited lease must not lead to a nested admission deadlock. Do not bypass leases,
  disable platform isolation, or grant container privileges to make a check pass.

### Running the tests

- A run that names test files reuses a passing result of the same file from an earlier run when the file, its
  recorded inputs, the pytest flags and the environment are byte-identical (docs/test-reuse.md); the report says
  `ran` or `reused from <run id>` per file, and `--no-reuse` forces execution. Say "reused" in a report only for
  files the runner printed as reused. Tier runs, `gate`, `quick` and the coordinator's combined-tree checks always
  execute. `scripts/test submit <tier|paths>` returns a job id; `status`, `wait`, `result`, `cancel` and `subscribe`
  follow it, and only one whole-tier job runs at a time. `task-checkpoint` and `task-submit` accept runner entry ids
  (`test_entry`, `test_entries`) that the board verifies. Running tests creates no identity, token or credential:
  board notices go out under the submitting agent's own session, and nothing is posted when no agent is configured.
- **The queue runs shorter suites first.** The test queue orders runs by their estimated length, taken from timings
  it records itself on every run, so many short runs finish before one long one. A long suite is never refused or
  held: it ages up the queue and is always granted after a bounded wait. During normal hours (weekdays 08:00 to
  21:00, machine-local) long runs together hold at most half the budget while shorter ones are waiting; outside them
  they may use all of it (docs/test-execution.md, *Scheduling*).
- The maintained tiers are `fast` (neither slow nor heavy), `full` (not slow), `slow` (only slow) and `all`
  (including slow); `tests/README.md` describes their mechanics and AGENTS.md controls when each is authorized.
  Scoped runs default to an automatic pool of what the broker grants. Use `-n 1` for sequential ordering; explicit
  `-n 0` requests an automatic pool up to broker capacity; explicit worker ceilings remain effective. Never run
  bare `pytest -n N` outside maintained admission.

## Models (AGENTS.md, section 6)

- **KV cache.** The KV cache is stored as `q8_0` (8 bit) for K and V on every serve path, llama.cpp and MLX alike,
  unless a spec, a profile or a person overrides it (`f16` for full precision, `q4_0` for less memory). The default
  lives in `poolhouse.kvtype.DEFAULT_KV_CACHE` and nowhere else; read what a spec is really served with through
  `kvcache.effective`, which also applies the fallbacks (V stays f16 without flash attention) and says why. Quality
  note and fallbacks: `docs/serving.md`.
- **MTP (multi-token prediction) draft heads are on by default** whenever the served model has a matching trusted
  head and the managed llama.cpp build supports it; there is a documented opt-out, and paths that cannot use it
  (one-token decisions, logprob scoring if incompatible) turn it off themselves. See `docs/serving.md`.
- **llama.cpp tracks the bleeding edge** through the managed head builds (`docs/llama-cpp-tracking.md`), not a
  pinned stable release.
- **Decision models should be as easy to call locally as hosted ones.** The target is the shape people know from
  Jev-style decision models: give a state and closed questions, get typed answers with probabilities, no text
  generation. The owner's decider is `StrandsAgents/strands-decider-2B-hobson-v19` (Qwen3.5-2B base). Treat a gap in
  `docs/decision-models.md` against that bar as a defect worth an issue; the gap analysis lives in the issue
  tracker, not in a private note.
- **Default decider: Strands 2B** (`StrandsAgents/strands-decider-2B-hobson-v19`), chosen for its small size;
  revisit only against measured results (JevBench and our own sets). **Fine-tuned deciders are never committed**:
  datasets and weights stay in caches, the repo keeps recipes and metrics only.
- **Persistent, relational state defaults to the graph** (`poolhouse.graph.GraphStore`, `docs/graph.md`): agent
  memory, knowledge about models, builds and tasks, ingested documents. Facts link to the things they are about, so
  recall can follow relations and use the hybrid search. A flat JSON file needs a stated reason (pure configuration,
  a tiny single-purpose cache); integrity sealing and tamper checks sit on top of the graph, not instead of it.
