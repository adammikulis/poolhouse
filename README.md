# Poolhouse

![Poolhouse by a neon-lit pool at night](docs/assets/poolhouse-banner.jpg)

Poolhouse is a Python library and a set of tools for pooling the compute of your devices. It brings model serving,
agents, tests and training together with a shared board for each project. A laptop can use a model running on a
larger machine, and agents can exchange work without keeping separate copies of a project's conversation.

The library also works on one machine. You can use its graph tools, document processing and model clients without
setting up a pool. The idea is to keep the machinery in one place: finding models, starting them, tracking work,
and keeping the evidence behind what a model says.

Poolhouse is pre-1.0, and the phone-to-node founding journey is still being built;
[release readiness](docs/release-readiness.md) records the current state.

## What you can use it for

- **Serve and use local models.** Find model files, manage their processes and talk to them from Python or the
  command line. The broker controls compute use so two pieces of work do not compete for the same device's GPU.
  [Model serving](docs/serving.md) covers hardware, memory and model settings.
- **Give agents a project workspace.** Messages, threads, notes, claims and tasks live on the project's board.
  Authenticated sessions have their own identities, and claims coordinate changes to shared files and resources.
  [The workspace](docs/workspace.md) explains the library and command-line interfaces.
- **Build and search knowledge graphs.** Keep concepts and their relationships, search them, follow connections
  and render a page you can explore. Document ingestion keeps source references alongside extracted claims.
  See [graphs](docs/graph.md) and [document ingestion](docs/ingest.md).
- **Ask decision models.** Decision models return probabilities for options you supply; chat models generate text.
  See [decision models](docs/decision-models.md). A source checkout also trains and measures models:
  [training](docs/training.md) and [benchmarks](docs/bench.md).

These tools can use local hardware, and some integrations fetch models or call external services. Where work runs
and which services it contacts depend on the configuration and the API you choose. A provider running your work can
see its inputs and outputs. Pooling compute does not make that work private from the provider.

## Install

The library requires Python 3.12 or later. Its base dependencies include native graph and security packages;
model and training integrations have additional dependencies. Check the [installation guide](docs/install.md)
for the supported runtime requirements and extras before installing.

For development from a source checkout:

```sh
python -m pip install .
```

The desktop application and device runtime have their own installation paths. Windows uses an Ubuntu WSL runtime;
macOS and Linux have separate packaging requirements. [Installing Poolhouse](docs/install.md) and
[Windows setup](docs/windows-runtime.md) cover those paths, including updates, uninstalling and optional dependencies.

## Two small examples

First, find a concept in a small graph. This needs no model or running node:

```python
from poolhouse.graph import look_up

graph = {
    "nodes": [{"id": "topic:gardening", "label": "Gardening", "kind": "topic",
               "attrs": {}, "messages": []}],
    "edges": [],
}

print(look_up(graph, "gardening"))
```

You can also talk to an already-running local model. Use the address shown by `poolhouse-serve status`;
this example assumes the server is at `http://127.0.0.1:8080`:

```python
import poolhouse as ph

model = ph.client.Client("http://127.0.0.1:8080")
reply = model.chat([{"role": "user", "content": "Suggest three herbs for a small garden."}])
print(reply.content)
```

The client sends the conversation to that server and returns its reply. Starting a model, choosing one that fits
and accessing it from another device are covered in the serving guide. Graph storage, HTML views and questions
that combine graph search with a model are covered in the graph guide.

The main library entry point is `import poolhouse as ph`. Its namespaces include `ph.pool`, `ph.board`, `ph.leases`,
`ph.test`, `ph.models`, `ph.serve`, `ph.client` and `ph.hub`. The [API reference](docs/api.md) gives their signatures
and error behavior. Pool actions need a running node and the relevant authorization; they are not prerequisites
for the graph example above.

## Security

Poolhouse separates a device's membership from an agent's permissions and a person's approval. Received messages,
files, model replies and tool results are data, not authority. Network listeners, admission policies, signing and
phone approval have different trust boundaries; their defaults and current limits are described in
[the security policy](SECURITY.md) and [the threat model](docs/security.md).

Governance signs the terms and actions a pool follows. Scoped grants limit what an agent session can do, and
sensitive requests go through approval checks. Model processes start through the compute broker. Where implemented,
operating-system keystore storage protects secrets at rest; it is separate from the permissions to use them.

These protections have limits, including compromised accounts, file-backed keys and hardware behavior that has
not been verified. [Security policy](SECURITY.md) covers those limits, privacy, private vulnerability reporting
and release verification. [The assistant security contract](docs/assistant-security.md) separates the implemented
protections from the remaining design work.

## More information

[Features](docs/FEATURES.md) lists the larger set of tools. [Commands](docs/commands.md) covers the shell interfaces,
and [development](docs/development.md) covers contributing and tests. Detailed guides belong there so this page
can stay a starting point.

Poolhouse is licensed under [Apache License 2.0](LICENSE). Dependency notices are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
