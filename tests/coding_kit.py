"""A native subprocess fixture emitting the harness's documented JSONL contract."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

SESSION = "13d70164-3caa-4e17-9201-d57511fcab62"


def native_cli() -> None:
    request = sys.stdin.read()
    home = Path(os.environ["CODEX_HOME"])
    session = home / "sessions" / "fixture.json"
    if "resume" in sys.argv and not session.exists():
        raise RuntimeError("native session history was not restored")
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(request)
    print(json.dumps({"type": "thread.started", "thread_id": SESSION}), flush=True)
    print(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "inspect project", "status": "completed"}}), flush=True)
    if request == "wait until cancelled":
        import time
        time.sleep(120)
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Inspected: " + request}}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}}), flush=True)


def fixture_worker(root, cid, conversation, prompt, communication) -> None:
    """PLUMBING ONLY: the turn worker with the model leasing and the board stood in for by the visibly marked plumbing server (`CANNED-NOT-A-MODEL`)
    and the fixture program above. The confinement the turn runs in is real: the stage, the bridges, the proof and the Seatbelt are the launcher's own.
    This process is spawned, so the parent's monkeypatch does not reach it."""
    import threading
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace

    from poolhouse import coding, harnessid, harnessing, isolation
    from poolhouse.isolation import confinement as launching
    from poolhouse.workspace import tools
    from poolhouse.workspace.coding_turns import worker
    from tests.isolation_kit import MODEL, Canned

    storage = Path(root).parent / "snapshot-storage"
    storage.mkdir(mode=0o700, exist_ok=True)
    begun = launching.Session.begin

    def begin(launch):
        """A stage is built in private storage that does not hold the test's state root; the proof's sockets keep the short default path."""
        kept, tempfile.tempdir = tempfile.tempdir, str(storage)
        try:
            return begun(launch)
        finally:
            tempfile.tempdir = kept

    launching.Session.begin = staticmethod(begin)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Canned)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    program = Path(__file__).read_text(encoding="utf-8")

    def launch(model, role, project, **options):
        if prompt == "startup rejected":
            raise SystemExit(2)
        session = harnessid.invite(options["name"], Path(project), options["parent"], lambda text: None)
        try:
            args = SimpleNamespace(permissions=role.permissions, execution_mode=role.execution_mode, protect=[], slots=1,
                                   launch=SimpleNamespace(grants=()))
            how = harnessing.Harness("codex", sys.executable, (f"http://127.0.0.1:{server.server_port}", MODEL, 65536), lambda text: None,
                                     workspace=tools.for_helper)
            run = harnessing.Session(session, Path(project).resolve(), "brief")
            render = lambda context: isolation.Rendering((sys.executable, "-I", "-c", program, *options["harness_args"]))  # noqa: E731
            with harnessing.confined(args, run, how, render) as confinement:
                return options["run_codex"](confinement)
        finally:
            session.revoke()
    coding.launch_coding_agent = launch
    worker(root, cid, conversation, prompt, communication)


if __name__ == "__main__":
    native_cli()
