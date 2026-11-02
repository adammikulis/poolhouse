# Pools

## Chat and Coding

Open **Chat** in the sidebar. The same conversation list and message composer serve ordinary
model chat and **Coding** mode. Search saved messages, rename a conversation from its menu, or
delete it after confirmation. Reopening a conversation restores its model and mode.

The shared model picker searches names, families, and quantizations, and marks models as
**Loaded** or **Installed**. Installed Chat choices open the Models screen so you can set
context length before loading; loaded models are reused by Chat and compatible Coding turns.

For Coding, select a downloaded model, an existing **Project directory**, **Coding agent**
(Codex or Claude Code), and **Tool permissions**. The model preference comes from the maintained
coding profile; unavailable models and missing command-line agents cannot start a turn.
**Advanced options** holds **Context length**, in tokens, and **Draft model / MTP head**
(`auto`, `none`, or an explicit installed head path). Ordinary Chat keeps Temperature there.

Coding launches through the same broker and permission hooks as the command-line harnesses;
it never grants a human identity to the agent. Responses stream into the shared conversation.
Expand **Tool activity and session details** to inspect commands, usage and the native session identifier. **Stop**
cancels model startup or the running harness and revokes that session's temporary workspace
identity. Closing the tab leaves an accepted turn running; reopening its conversation reattaches.

Conversation messages, model and project relationships live in the local graph store. Native
harness session files are private to each conversation and settings fingerprint under the chat
state root. Subsequent turns resume that session; changing the model, project or permissions starts
fresh native context with the saved conversation. Coding works in the selected project directly;
create a separate checkout when you want isolated edits. See [the harness rails](harnesses.md).

## Driving it from Python

`poolhouse` is pure Python and pulls in nothing heavy, so the machine you drive from
needs no CUDA, no MLX and no training stack.

```python
from poolhouse.fleet import Device, Requires, Unit, run

devices = Member.discover()
report = run(
    [Unit(id=f"shard{i}",
          argv=["python", "-m", "poolhouse.train.run", "--recipe", "text-lm",
                "--data", f"shards/{i}.jsonl", "--out", f"out/{i}"],
          requires=Requires(labels=("train",), min_vram_gb=8))
     for i in range(8)],
    devices, kind="text-lm")

for place in report:
    print(place.unit_id, place.member, place.state, f"{place.elapsed_s:.0f}s")
```

Work waits for capacity rather than failing, is retried on a *different* machine if it
fails, and a machine that fails several in a row is set aside rather than draining the
queue. A unit no machine can run fails at once, naming every machine and why:

```
gpubox: has 23.0 GB VRAM, needs 80.0
pi-rack: does not report 'cuda'; has no backends
radeon: this machine is in use (mon tue wed thu fri 09:00-17:00); work resumes Mon 17:00
```

One machine at a time, when that is what you want:

```python
rtx = Member.find_one(require="cuda")     # refuses to guess between two
rtx.push("data/train.jsonl", "data/train.jsonl")
job = rtx.submit(["python", "-m", "poolhouse.train.run", "--recipe", "text-lm",
                  "--data", "data/train.jsonl", "--out", "out/lm"])
rtx.wait(job["id"], on_metric=print)
rtx.pull("out/lm/step_000010000/model.safetensors", "local/model.safetensors")
```

Uploads and downloads resume and are verified by digest.

A model sweep goes across the pool the same way. `poolhouse.fleet.sweeps` is the pool side
of `python -m poolhouse.bench sweep --fleet`: `plan(models, devices)` sends each model, largest first,
to the idle device with the most room for it -- `room_bytes` is what the daemon announces,
`hub.room()` rather than free memory -- spreading models over machines rather than
stacking them, and names every model that fits nowhere and why on each member. `jobs_from`
turns the plan into one `Job` per device (the sweep's line with only that device's `--serve`s),
`dispatch` posts them, `wait` polls until each is `done` or `failed` and prints the log's
tail, and `gather` brings every device's runs home into one store as `bench:` docs with
`server["host"]` and `server["commit"]` set, never overwriting and skipping what is already
there; `import_runs(FILE.json, into, host=...)` does the same by hand from a
`show --export` file for a device with no daemon. A daemon refuses a bench job (409, with
`refused` saying which) when its checkout's commit is not the dispatcher's, when its
`measuring.lock` is held or a bench of its own is still running, or when a model's
estimated bytes exceed its room; otherwise it runs `python -m poolhouse.bench ... --detach` on itself
and adopts the pid into its job list, so `poolhouse-pool status` shows it `measuring` and no
training job starts beside it. The dispatcher counts as a device through `here()`.

## Restarting the daemon

`poolhouse --restart` replaces the running daemon with the code now installed without stopping
work. The owned daemon refuses only while setup, a download or a benchmark measurement is under
way; it then freezes its job runner, writes each job's process ownership to a private record and
exits. Queued jobs stay queued, a running job keeps running as an independent process, and models
served by their own workers stay loaded. The replacement restores the records, holds the slots the
surviving processes use, and never starts a job again that may already have run. A job that
finishes while the daemon is down keeps its exit code, which the job's own launcher recorded beside
its log. A process whose identity cannot be verified is reported `interrupted` and keeps holding its slot. `poolhouse` without the option still replaces a daemon only at its idle
boundary when its commit differs.

A replacement always ends with a daemon answering on the port. The launcher that asks the old daemon to stop does not become the new
one: it starts the new daemon as a process of its own and stays until it answers (its exit status is 0 only then). A new daemon that
ends first is answered by starting the old one again from the command its control record kept (`launch`), and the launcher says so in
its log and exits 1; one that is still starting after two minutes is left running and reported. One launcher at a time replaces or
starts the daemon on a port (`launcher-control/<port>.handover.lock`), so a second restart waits for the first to be proved. A daemon
told to stop is waited for however long it takes, because giving up then would leave nothing. A process started to answer for itself
on macOS (`poolhouse runtime restart-host`) starts in the state root, not where it was run from, because a process that answers for itself
waits for its own consent to read Documents, Desktop and Downloads.

## What the daemon is called

A firewall list, a Local Network prompt and the list of background items name a program by its executable, so the daemon runs
under the same name on every system, and the name is Poolhouse:

| | the process | the firewall |
|---|---|---|
| macOS | `Poolhouse`, the executable in `~/.poolhouse/apps/runtime/<platform>-<machine>-<python>/Poolhouse.app`, bundle id `app.poolhouse.runtime`, signed with the local certificate (docs/node.md, "The runtime's identity on macOS") | one entry, "Poolhouse", asked once; the launchd unit and the Login Items list say Poolhouse too |
| Linux and WSL | `poolhouse` (what `ps` and `ss -p` print; the daemon names itself with `prctl`) | the rules are by port, never by program: TCP 8770, comment it "Poolhouse" |
| Windows | `python.exe` of the runtime (the file name and the version resource are Python's, and the path changes with every runtime, so a rule that names the program would be asked again at every update) | an inbound rule by port named "Poolhouse" (TCP 8770), added with one administrator prompt |

`/health` says which program answered: `process` holds its `executable` (relative to the state root when it is inside it, else its
name alone), its `name` and its `bundle_id` (empty when it is not Poolhouse's own). `poolhouse-setup` shows "fleet: daemon runs as",
and `poolhouse runtime restart-host` replaces a host on the selected commit that is not yet running as Poolhouse.

## Joining a pool

A pool is founded and approved by a person's admin key on the Rust node (docs/node.md). Each device runs
`poolhouse-traind`; a device joins with a short join code:

```
owner$   poolhouse-pool add-device                     # prints a short join code
newbox$  poolhouse-pool join CODE --host 192.168.2.44  # joins the owner's pool
owner$   poolhouse-pool members                        # the new device is a member
poolhouse-pool status                                  # the devices that answered
```

`status` is that listing on its own:

```
NAME             URL                          ROOM             STATE        COMMIT       UPDATES      SERVING
studio           http://192.168.2.44:8770     96.0G            idle         0ce5bc5 3h   main 4m      quince-2b.gguf:8099
larch            http://192.168.2.27:8770     20.5/24.0 GB     measuring    0ce5bc5 3h   releases 2h  -
harrowgate       http://192.168.2.31:8770     20.5/24.0 GB     idle         9f2c1ab 6d   off          -
```

COMMIT is what each device is running and how old that commit is; UPDATES is how it keeps
current and when it last looked. A pool half on one commit and half on another is the
thing those two columns exist to make visible -- `harrowgate` above is six days behind and
following nothing, which is a machine somebody has to visit.

### Two devices on a LAN

1. Update Poolhouse on the second device (`poolhouse runtime ensure`; a device that follows releases or main does it by itself).
2. Open Poolhouse there. On Windows, open it from Windows: it adds the inbound rule the first time (TCP 8770 for the daemon, on
   private networks) after one Windows prompt that says why, and says in one sentence what to do when the
   prompt was declined or the network is classed Public. Opened inside WSL on Windows' private network, it says that no other device
   can reach it and the one command that fixes it (`python -m poolhouse.device_setup --yes`).
3. Join it with the code from the first device, as above. Then, from the first device:

```
poolhouse-pool members                                    the second device is a member
poolhouse-test-devices                                    each member, whether it takes tests, its platform, Python and last result
scripts/test all tests/test_x.py --on NAME                those files there; its output arrives as it is written, prefixed [NAME]
scripts/test all tests/test_x.py tests/test_y.py --on NAME --split     divide the files between this machine and NAME
```

A member takes tests from the pool's other members once the node authorizes the compute service for it (docs/test-farm.md). It runs them on
its own Python 3.13 that imports pytest: the one `scripts/setup-wsl-dev` makes (`~/.venvs/poolhouse-dev`), else
the Python the daemon runs, else the one `POOLHOUSE_SHARD_PYTHON` names; with none it says so and what to run. A job on a member records the
device that asked and the agent behind the request (`GET /jobs`, the `by` field), and the reuse record, the exit status and the printed
report are those of a run on the node's pool. A member that stops answering while its tests run ends the run with a message that says its
tests did not finish and were not run anywhere else; the other parts of a split run go on and the exit status is 70.

A member can run tests, which is code, on every other member as that member's user. `poolhouse features
disable remote-tests` on a device stops it taking tests and sending them.

A machine with **nothing installed** cannot be pushed to; `poolhouse pool bootstrap --share DIR` offers the
wheel on the LAN for ten minutes to somebody who opens the address on it, and `bootstrap --ssh
user@host --share DIR` (try `--dry-run` first) installs it over your own ssh keys. The design, the
threat model and what is not built are in `docs/onboarding.md`.

### Placing users

`plan` says which model each device should serve, and with how many slots, for a number of
conversations at once:

```
poolhouse-pool plan --users 36 --context 16384
DEVICE             MODEL                                            SLOTS  CONTEXT     USED     ROOM
studio           Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf    31    16384   109.9G   110.0G
larch            gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf                   5    16384     5.7G    24.0G
36 of 36 user(s) placed at 16384 tokens each
--prefer quality: the best measured model that fits each device
```

Models are taken in the order `docs/model-ranking.md` ranks them, and each goes to every
device with room for its loaded weights and at least one slot's cache at `--context`, taking
as many slots as fit or as are still wanted; so the best model reaches the most users, and
a smaller machine serves a smaller model to the rest. A device serves one model.

`--prefer slots` reads the other way: each device, roomiest first, serves whichever model
slots the most of the users still waiting, and a tie goes to the better-ranked model. A
machine with room for the best model at one slot and a smaller one at six gives six people
a smaller model:

```
poolhouse-pool plan --users 36 --context 16384 --prefer slots
```

A user who gets no slot is counted, with every device's reason; so is a model with no memory
measurement and a memory measurement for a model nobody has scored. `--apply` serves the
plan: each placed device's daemon runs its model with those slots (`POST /serve`), and what
each is serving afterwards is printed. `--json` is the same as data.

### Following main

A machine that is a git checkout with an editable install can follow a branch instead of
waiting for a release:

```
poolhouse-traind --persist --track main
```

Every five minutes it asks `git ls-remote` for the head of `main`, and when it has moved it
fetches it and checks the tip commit's signature against the release key (`docs/release.md`);
an unsigned commit, or one signed by another key, is not pulled. Then `git merge --ff-only
FETCH_HEAD` (never any other merge -- a checkout holding commits `main` does not have is
reported and left alone, because resetting somebody's work in progress at three in the
morning is unforgivable), `pip install -e .` only if `pyproject.toml` or a lock file moved,
and then a restart -- `launchctl kickstart` or `systemctl restart` where a login service is
installed, a re-exec where there is not. A pull that fails changes nothing, so the daemon
keeps running the code it started with and says so on the next `poolhouse-pool status`.

Neither this nor the release update ever interrupts work: both wait for no job running, no
benchmark measuring (the same lock `python -m poolhouse.bench status` reads, so a run started at the
keyboard counts) and no model loaded. A machine part way through a sweep is left alone
until it is not, however new the code is.

What this is: **it runs a signed commit minutes after it is pushed**, with no review between
the signature and the machine. `--track off` goes back to releases, and it is off unless asked
for. It is remembered in the daemon's settings, so it is asked for once and
survives a reboot.

**Who can reach it.** The daemon listens on TCP 8770 and nothing else. It listens on this machine alone
until `--lan`, `--host ADDRESS` or `--setup-from-lan` says otherwise. A request from another machine
arrives over TLS 1.3 and is authorized by the node for the pool it belongs to
(docs/pool-node-services.md); a device the pool has put out is refused. An unsigned `/health` from another
machine says only that a daemon is there. File downloads, model downloads and the streamed output of the
`/infer` proxy travel inside the daemon's TLS. The web interface answers other machines (`--ui-from-lan`)
over TLS only and signs in with a launch ticket from the Poolhouse window (`poolhouse open`).

**What a device may run.** `POST /jobs` accepts `python -m poolhouse.bench ...` and
`python -m poolhouse.fleet.calibration ...`, run in the daemon's own folder with its own
environment, and refuses any other program, any `cwd` and any `env` from the wire. A member
also takes test shards (`/shards/v1/...`): a tier, test files and a time limit
in a typed request, run by the same executor as the node's shards, never a command line. A pool
sweep (`python -m poolhouse.bench sweep --fleet`) dispatches measurements through `POST /bench`, which
takes a typed job and no command line.

The app's Pool view has a "Run across the pool" form that
builds `python -m poolhouse.bench sweep --fleet --serve MODEL ...` from the models the devices hold,
starts it detached, and shows `status` and `history` beside it.

**For an agent**, `poolhouse-mcp` serves the same functions as MCP tools over stdio. In
Claude Code:

```
claude mcp add poolhouse -- poolhouse-mcp
```

or in a project's `.mcp.json`: `{"mcpServers": {"poolhouse": {"command": "poolhouse-mcp"}}}`.
The tools are `serve_status`, `serve_up`, `serve_down`, `models_find`, `models_files`,
`models_fetch`, `bench_run`, `bench_status`, `bench_history`, `bench_show`, `fleet_members`,
`world_make`, `setup_look` and `doctor`; a model load, a download and a
measurement never block the call -- each returns a log path and a pid, and `bench_status`
follows it. Joining a pool is a command a person runs (`poolhouse-pool join`), never an MCP tool. With `pip install 'poolhouse[mcp]'` the SDK's server is used; without it the
command speaks the protocol itself.
