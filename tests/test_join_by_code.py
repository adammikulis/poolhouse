"""What a member learns from a join offer, and how the model store is served to the devices that may have it.

The offer is authenticated by the exchange that carried it; the store is served over pinned TLS and
the downloading side is the real `MemberSession`. Joining a pool is the node's: `poolhouse-pool join`."""

from __future__ import annotations

import base64
import hashlib
import os
import socket
from pathlib import Path

import pytest
from onboard_support import Clock, Recorder, identity as make_identity, requests as make_requests
from test_hub_pull import blob

from poolhouse import sentinel
from poolhouse.fleet.onboard import memberfirst, memberlearn, modelstore, pake
from poolhouse.fleet.onboard.join_code import (
    API,
    Grant,
    Hooks,
    JoinCodeClient,
    JoinCodeServer,
    Offer,
    context_for,
)
from poolhouse.fleet.onboard.manifest import DEFAULT_CHUNK, Entry
from poolhouse.fleet.onboard.requests import Devices, Request
from poolhouse.fleet.onboard.sharing import Access, Licences
from poolhouse.fleet.onboard.transfer import Share, confined, serve_file
from poolhouse.hub import members as hub_members
from poolhouse.hub.memberbook import MemberBook
from poolhouse.safenames import Unsafe
from poolhouse.sentinel.human import mint
from poolhouse.sentinel.store import Holding

KIB = 1024
FP = "a" * 64


@pytest.fixture(autouse=True)
def needs_crypto():
    pytest.importorskip("spake2")
    pytest.importorskip("cryptography")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- the offer is authenticated by the join -------------------------------------------------
@pytest.fixture
def world(tmp_path):
    rec = Recorder()
    acceptor, joiner = make_identity(tmp_path, "acceptor"), make_identity(tmp_path, "joiner")
    rq = make_requests(tmp_path, rec, Clock())
    learned = []
    server = JoinCodeServer(
        rq, acceptor, Hooks(lambda r: Grant(certificate=acceptor.beacon, share_port=9),
                            learned=lambda r, o: learned.append((r, o))),
        address=("127.0.0.1", 0), bus=rec.bus).start()
    yield type("World", (), {"rq": rq, "server": server, "acceptor": acceptor,
                             "joiner": joiner, "learned": learned})
    server.stop()


def the_offer(joiner, port=8773) -> Offer:
    return Offer(port, joiner.beacon, base64.b64encode(b"k" * 32).decode(), "secret-for-them")


def joiner_client(world) -> JoinCodeClient:
    return JoinCodeClient("127.0.0.1", world.server.port, fingerprint=world.joiner.fingerprint)


def asked(world):
    c = joiner_client(world)
    rid = c.ask(name="kitchen-pi", hostname="kitchen.local", model="Linux")
    return c, rid, world.rq.accept(rid, mine=True).code


def test_the_accepting_side_hears_the_offer_the_asker_sealed_under_the_exchange(world):
    c, _rid, code = asked(world)
    grant = c.finish(code, the_offer(world.joiner))
    assert grant.share_port == 9
    [(request, offer)] = world.learned
    assert request.name == "kitchen-pi" and offer == the_offer(world.joiner)


def test_an_offer_whose_tag_is_not_the_exchanges_is_refused_and_nothing_is_learned(world):
    c, rid, code = asked(world)
    session = pake.start_initiator(code, context=context_for(rid, c.nonce),
                                   mine=world.joiner.fingerprint, theirs=world.acceptor.fingerprint)
    _s, body = c._call("POST", f"{API}/{rid}/exchange", {"message": session.message})
    session.receive(body["message"])
    raw = the_offer(world.joiner).encode()
    forged = base64.b64encode(raw).decode()
    status, answer = c._call("POST", f"{API}/{rid}/confirm", {
        "confirmation": session.confirmation(), "offer": forged, "offer_tag": session.seal(raw + b"x")})
    assert status == 403 and "tag" in answer["error"]
    assert world.learned == []


def test_a_tag_made_for_the_grant_does_not_authenticate_an_offer(world):
    c, rid, code = asked(world)
    session = pake.start_initiator(code, context=context_for(rid, c.nonce),
                                   mine=world.joiner.fingerprint, theirs=world.acceptor.fingerprint)
    _s, body = c._call("POST", f"{API}/{rid}/exchange", {"message": session.message})
    session.receive(body["message"])
    offer = the_offer(world.joiner).encode()
    status, _ = c._call("POST", f"{API}/{rid}/confirm", {
        "confirmation": session.confirmation(), "offer": base64.b64encode(offer).decode(),
        "offer_tag": session.seal(offer.removeprefix(b"offer:"))})
    assert status == 403 and world.learned == []


def request_from(joiner, **more) -> Request:
    base = {"id": "1" * 32, "name": "kitchen-pi", "hostname": "k", "address": "127.0.0.1",
            "model": "m", "fingerprint": joiner.fingerprint, "nonce": "n", "created": 1.0}
    return Request(**{**base, **more})


def test_an_offer_for_some_other_certificate_than_the_one_the_exchange_bound_is_not_stored(tmp_path):
    real, other = make_identity(tmp_path, "real"), make_identity(tmp_path, "other")
    request = request_from(real)
    good = memberlearn.learn_from_offer(tmp_path, request, the_offer(real))
    assert good and good["certificate"] == real.beacon and good["fingerprint"] == real.fingerprint
    assert memberlearn.learn_from_offer(tmp_path / "b", request, the_offer(other)) is None
    assert MemberBook(tmp_path / "b" / "members.json").rows() == []


def test_an_offer_with_no_share_port_or_a_bad_key_gives_no_row(tmp_path):
    real = make_identity(tmp_path, "real")
    request = request_from(real)
    assert memberlearn.learn_from_offer(tmp_path, request, Offer()) is None
    bad_key = Offer(8773, real.beacon, "not a key", "s")
    assert memberlearn.learn_from_offer(tmp_path, request, bad_key) is None
    assert MemberBook(tmp_path / "members.json").rows() == []


def test_a_grant_whose_certificate_is_not_the_one_presented_adds_no_row(tmp_path):
    real, other = make_identity(tmp_path, "real"), make_identity(tmp_path, "other")
    grant = Grant(certificate=other.beacon, signing_key=base64.b64encode(b"k" * 32).decode(),
                  device_secret="s", share_port=8773, name="owner")
    assert memberlearn.learn_from_grant(tmp_path, grant, host="127.0.0.1",
                                      server_fingerprint=real.fingerprint, my_secret="m") is None
    assert MemberBook(tmp_path / "members.json").rows() == [] and Devices(
        tmp_path / "devices.json").all() == []


def test_two_devices_with_one_name_do_not_overwrite_each_other(tmp_path):
    a, b = make_identity(tmp_path, "a"), make_identity(tmp_path, "b")
    first = memberlearn.learn_from_offer(tmp_path, request_from(a), the_offer(a))
    second = memberlearn.learn_from_offer(tmp_path, request_from(b), the_offer(b))
    assert first["name"] == "kitchen-pi" and second["name"].startswith("kitchen-pi-")
    assert len(MemberBook(tmp_path / "members.json").rows()) == 2


# -- two homes, the real flow ------------------------------------------------------------------


















# -- the model store ---------------------------------------------------------------------------
class Models:
    """A folder of model files with one of each kind of thing a store has to get right."""

    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "models"
        self.outside = tmp / "outside"
        self.outside.mkdir()
        # sizes differ: discovery treats two files of one size and header as one model's copies
        self.data = {"open-Q4.gguf": blob(300 * KIB, 1), "gated-Q4.gguf": blob(310 * KIB, 2),
                     "unaccepted-Q4.gguf": blob(320 * KIB, 3),
                     "deep/er/nested-Q8.gguf": blob(200 * KIB, 4), "held-Q4.gguf": blob(210 * KIB, 5)}
        for name, data in self.data.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_bytes(data)
        self.secret = blob(330 * KIB, 9)
        (self.outside / "secret.gguf").write_bytes(self.secret)
        (self.root / "escape.gguf").symlink_to(self.outside / "secret.gguf")

    def wanted(self, name: str) -> hub_members.Wanted:
        data = self.data[name]
        return hub_members.Wanted("maker/thing-GGUF", name.rsplit("/", 1)[-1], len(data),
                                hashlib.sha256(data).hexdigest())


def quarantine(home: Path, data: bytes, monkeypatch, tmp: Path) -> None:
    here = os.environ["POOLHOUSE_HOME"]
    monkeypatch.setenv("POOLHOUSE_HOME", str(home))
    digest = hashlib.sha256(data).hexdigest()
    held = tmp / "held-copy" / "x.gguf"
    held.parent.mkdir(parents=True, exist_ok=True)
    held.write_bytes(b"the copy")
    sentinel.default().store.quarantine(("artifact", f"download:{digest}"), "flagged",
                                        {"sha256": digest}, Holding(path=held), actor="test")
    monkeypatch.setenv("POOLHOUSE_HOME", here)


def accept_licence(owner, name: str, who: str = "adam") -> None:
    entry = Entry(name.rsplit("/", 1)[-1], 1, "0" * 64, 65536, ("0" * 64,), licence=name.rsplit("/", 1)[-1])
    Licences(owner.state / "licences.json").record(
        mint("accept-licence", entry.licence, typed=lambda _p: entry.licence,
             terminal=(True, True), env={}), entry, who=who)








def test_the_manifest_is_signed_and_its_serial_rises(tmp_path):
    first = modelstore.next_serial(tmp_path / "s.json", now=lambda: 1_000.0)
    second = modelstore.next_serial(tmp_path / "s.json", now=lambda: 1_000.0)
    earlier_clock = modelstore.next_serial(tmp_path / "s.json", now=lambda: 5.0)
    assert first == 1_000 and second == 1_001 and earlier_clock == 1_002












# -- store-root confinement, quarantine and levels at the serving function -----------------------
def share_of(models: Models, names: list[str], *, withhold=None, licences=None, level="open"):
    entries = [Entry(n.rsplit("/", 1)[-1], len(models.data[n]), hashlib.sha256(models.data[n]).hexdigest(),
                     DEFAULT_CHUNK, (hashlib.sha256(models.data[n]).hexdigest(),), kind="model",
                     sharing=level) for n in names]
    paths = {e.name: models.root / n for e, n in zip(entries, names, strict=True)}
    from poolhouse.fleet.onboard.manifest import Manifest
    manifest = Manifest(1, 0.0, 0.0, "", tuple(entries))
    return Share(models.root, b"", manifest, licences, withhold, paths, (models.root,)), entries


class Bus:
    def emit(self, *_a, **_k):
        return None


MINE = Access("f" * 64, True)


def serve(share, entry, access=MINE):
    return serve_file(share, entry.name, "bytes=0-9", Bus(), access)


def test_serving_follows_only_paths_that_stay_inside_the_model_roots(tmp_path):
    models = Models(tmp_path)
    share, [entry] = share_of(models, ["open-Q4.gguf"])
    assert serve(share, entry).status == 206
    # swapped for a link out of the roots after the manifest was made, same size: refused
    target = models.outside / "same-size.gguf"
    target.write_bytes(blob(len(models.data["open-Q4.gguf"]), 77))
    (models.root / "open-Q4.gguf").unlink()
    (models.root / "open-Q4.gguf").symlink_to(target)
    refused = serve(share, entry)
    assert refused.status == 404 and b"here" in refused.body
    # a link that stays inside is fine (a Hugging Face cache is links into blobs/)
    (models.root / "blobs").mkdir()
    (models.root / "blobs" / "b").write_bytes(models.data["open-Q4.gguf"])
    (models.root / "open-Q4.gguf").unlink()
    (models.root / "open-Q4.gguf").symlink_to(models.root / "blobs" / "b")
    assert serve(share, entry).status == 206


def test_a_name_with_dots_or_a_path_is_no_way_out_of_the_roots(tmp_path):
    models = Models(tmp_path)
    share, _entries = share_of(models, ["open-Q4.gguf"])
    for name in ("../outside/secret.gguf", "..%2foutside%2fsecret.gguf", "/etc/hosts",
                 "deep/er/nested-Q8.gguf", "escape.gguf", "secret.gguf"):
        assert serve_file(share, name, "bytes=0-9", Bus(), Access("f" * 64, True)).status == 404
    with pytest.raises(Unsafe):
        confined(models.root / ".." / "outside" / "secret.gguf", (models.root,))
    with pytest.raises(Unsafe):
        confined(models.root / "escape.gguf", (models.root,))
    assert confined(models.root / "open-Q4.gguf", (models.root,)).name == "open-Q4.gguf"


def test_a_file_that_is_not_listed_cannot_be_asked_for_by_giving_it_an_entry_name(tmp_path):
    models = Models(tmp_path)
    share, [entry] = share_of(models, ["open-Q4.gguf"])
    other = Entry("escape.gguf", entry.size, entry.sha256, entry.chunk_size, entry.chunks)
    assert serve_file(share, other.name, "bytes=0-9", Bus(), Access("f" * 64, True)).status == 404


def test_a_copy_sentinel_holds_is_not_served_even_after_the_manifest_was_made(
        tmp_path, monkeypatch):
    models = Models(tmp_path)
    share, [entry] = share_of(models, ["open-Q4.gguf"], withhold=memberfirst.quarantine_veto)
    assert serve(share, entry).status == 206
    quarantine(tmp_path / "home", models.data["open-Q4.gguf"], monkeypatch, tmp_path)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    held = serve(share, entry)
    assert held.status == 403 and b"quarantine" in held.body


def test_building_the_store_skips_a_held_copy_a_link_out_and_a_second_file_of_one_name(
        tmp_path, monkeypatch):
    models = Models(tmp_path)
    twin = models.root / "other" / "open-Q4.gguf"
    twin.parent.mkdir()
    twin.write_bytes(blob(10 * KIB, 5))
    quarantine(tmp_path / "home", models.data["held-Q4.gguf"], monkeypatch, tmp_path)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    found, roots = modelstore.candidates([models.root])
    found.append(modelstore.Candidate("escape.gguf", models.root / "escape.gguf",
                                      len(models.secret), ""))          # whatever found it
    built = modelstore.build(found, roots, digests=modelstore.Digests(None),
                             veto=memberfirst.quarantine_veto)
    names = [e.name for e in built.entries]
    assert "held-Q4.gguf" not in names and "escape.gguf" not in names
    assert names.count("open-Q4.gguf") == 1
    why = " ".join(built.skipped)
    assert "quarantine" in why and "outside" in why and "already listed" in why
    assert set(built.paths) == set(names)
    assert all(e.sharing == "owner" and e.licence for e in built.entries)     # the default level


def test_a_gated_file_is_served_to_the_owners_devices_only_with_the_acceptance_on_record(tmp_path):
    models = Models(tmp_path)
    licences = Licences(tmp_path / "licences.json")
    share, [entry] = share_of(models, ["gated-Q4.gguf"], licences=licences, level="owner")
    entry = Entry(**{**{f: getattr(entry, f) for f in entry.__slots__}, "licence": "llama"})
    share = Share(share.root, b"", share.manifest.__class__(1, 0.0, 0.0, "", (entry,)), licences,
                  None, share.paths, share.roots)
    mine, other = Access("f" * 64, True), Access("e" * 64, False)
    assert serve(share, entry, mine).status == 403                   # no acceptance yet
    licences.record(mint("accept-licence", "llama", typed=lambda _p: "llama",
                         terminal=(True, True), env={}), entry, who="adam")
    assert serve(share, entry, other).status == 403                  # another person's device
    assert serve(share, entry, Access()).status == 403                # the pool key alone
    ok = serve(share, entry, mine)
    assert ok.status == 206 and ok.headers["X-Licence-Accepted-By"] == "adam"


