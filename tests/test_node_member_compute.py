"""One device of a pool using another's daemon, between two real nodes: each daemon's service bound by the keeper the daemon runs, then a
job, files and a chat from the first device on the second, and nothing there that the second's owner asked for.

The model server behind the second daemon is the marked canned one: what is tested is who reaches it and what of it they are shown.
"""

import json
import sys
import threading
from dataclasses import dataclass

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from remote_client_support import RemoteClientServer
from service_kit import node_binary, two_nodes  # noqa: F401  (fixtures)

from poolhouse import node_health
from poolhouse.board.service_binding import Keeper
from poolhouse.fleet import chat, chat_routes, roster, service_pool, tls
from poolhouse.fleet.api import Daemon, make_handler
from poolhouse.fleet.files import Fetcher
from poolhouse.fleet.jobs import JobRunner
from poolhouse.fleet.remote import Member, MemberError
from poolhouse.fleet.serving import Serving
from poolhouse.testing.canned import CannedServer, marked

OWNERS = "owners-own-text"


@dataclass
class Used:
    """The first device's view of the second's daemon, and what that daemon holds."""

    member: Member
    runner: JobRunner
    files: object
    model: CannedServer
    owners_job: str


def refused_without_the_grant(nodes) -> None:
    """A session that is not the device's own binds nothing without the grant, whatever it proves about the key."""
    params = {"cert": nodes.idb.der.hex(), "endpoint": "https://127.0.0.1:9"}
    draft = node_health.call(nodes.b.state, "service_draft", params)
    key = serialization.load_pem_private_key(nodes.idb.keyfile.read_bytes(), password=None)
    proof = key.sign(bytes.fromhex(draft["proof_bytes"]), ec.ECDSA(hashes.SHA256())).hex()
    with pytest.raises(ValueError, match="grant"):
        node_health.call(nodes.b.state, "service_bind", {**params, "challenge": draft["challenge"], "proof": proof}, token=nodes.b.token)
    assert not any(row.fingerprint == nodes.idb.fingerprint for row in service_pool.Pool(nodes.b.state).services())


@pytest.fixture
def used(two_nodes, tmp_path):  # noqa: F811
    nodes = two_nodes
    files = tmp_path / "b" / "files"
    files.mkdir(parents=True)
    runner = JobRunner(tmp_path / "b", files)
    model = CannedServer()
    serving = Serving(tmp_path / "serving.json")
    serving.register(model.port, ["canned.gguf"])
    pool = service_pool.Pool(nodes.b.state)
    daemon = Daemon(runner, files, "owner-token-of-b", name="b", members=pool, serving=serving, report=lambda: {},
                    service_identity=lambda: nodes.idb, command=list, fetcher=Fetcher(files))
    server = RemoteClientServer(make_handler(daemon), tls=tls.member_context(nodes.idb, pool))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"https://127.0.0.1:{server.server_port}"
        # The keeper each daemon runs binds its own service; the second device holds no admin key of the pool.
        refused_without_the_grant(nodes)
        assert Keeper(nodes.a.state, lambda: nodes.ida, lambda: "https://127.0.0.1:9").check() is True
        assert Keeper(nodes.b.state, lambda: nodes.idb, lambda: endpoint).check() is True
        nodes.settle()
        (files / "owner.txt").write_text(OWNERS)
        owners = runner.label(runner.submit("owners", [sys.executable, "-c", f"print({OWNERS!r})"], str(files)), "local")
        [member] = Member.discover(pool=service_pool.Pool(nodes.a.state), identity=nodes.ida)
        yield Used(member, runner, files, model, owners.id)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        runner.shutdown()
        model.close()


def test_a_member_runs_a_job_on_its_own_files_and_chats_through_the_other_device(used, tmp_path):
    sent = tmp_path / "in.txt"
    sent.write_text("from-the-first-device")
    used.member.push(sent, "in.txt")
    job = used.member.submit([sys.executable, "-c", "import pathlib; print(pathlib.Path('in.txt').read_text())"], name="read")
    done = used.member.wait(job["id"], poll_s=0.2, timeout_s=60)
    assert done["state"] == "done", used.member.log(job["id"])
    assert "from-the-first-device" in used.member.log(job["id"])
    assert "from-the-first-device" in used.member.pull(f"jobs/{job['id']}/job.log", tmp_path / "back.log").read_text()
    assert used.member.pull("in.txt", tmp_path / "back.txt").read_text() == "from-the-first-device"

    row = roster.describe("b", used.member.base_url, used.member.beacon.machine, used.member.health())
    target = chat.targets([{**row, **chat_routes._reach(row, tmp_path)}])[0]
    reply = b"".join(chat.stream(target, {"model": target.model, "stream": True,
                                          "messages": [{"role": "user", "content": "hi"}]}, timeout=30))
    assert chat.reply_parts(reply)[0] == marked("hello")


def test_a_member_is_shown_nothing_another_asked_for(used, tmp_path):
    member, owners = used.member, used.owners_job
    assert member.jobs() == []
    assert owners not in member.health()["running"]
    for asking in (lambda: member.job(owners), lambda: member.log(owners), lambda: member.metrics(owners), lambda: member.stop(owners)):
        with pytest.raises(MemberError, match="404"):
            asking()
    for path in ("owner.txt", f"jobs/{owners}/job.log", f"jobs/{owners}/job.json"):
        with pytest.raises(MemberError):
            member.pull(path, tmp_path / "taken")
    assert not (tmp_path / "taken").exists()
    forged = tmp_path / "forged"
    forged.write_text("forged")
    for path in ("owner.txt", f"jobs/{owners}/job.log"):
        with pytest.raises(MemberError):
            member.pull(path, tmp_path / "taken-again")
    member.push(forged, "owner.txt")   # the same name is the member's own file
    assert (used.files / "owner.txt").read_text() == OWNERS
    with pytest.raises(MemberError):
        member.push(forged, f"jobs/{owners}/job.log")
    with pytest.raises(MemberError, match="shared"):
        member._json("POST", "/fetch", {"member": "a", "relpath": "owner.txt"})
    (used.files / "shared").mkdir()
    (used.files / "shared" / "for-all.txt").write_text("for-all")
    assert member.pull("shared/for-all.txt", tmp_path / "for-all.txt").read_text() == "for-all"
    with pytest.raises(MemberError):
        member.push(forged, "shared/for-all.txt")


def test_a_member_reads_how_busy_the_slots_are_and_none_of_their_text(used):
    used.model.slots = [{"id": 0, "n_ctx": 4096, "is_processing": True, "prompt": marked(OWNERS), "generated": marked(OWNERS),
                         "next_token": [{"content": OWNERS}], "params": {"grammar": OWNERS}}]
    _, body, _ = used.member._request("GET", "/infer/slots")
    assert json.loads(body) == [{"id": 0, "n_ctx": 4096, "is_processing": True}]
