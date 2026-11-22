"""A failed Hub download is deleted and refused without counting against the Hub's host: the reputation ledger's only
remedy is a person, and a model pull's own retry and refusal already protect the file. A download of anything else
still reports. Real fake Hub on a loopback socket, the real net pipeline, a recording ledger."""

from __future__ import annotations

import pytest
from net_site import gguf_bytes

from poolhouse import caller, http, hub, net
from poolhouse.hub import transfer
from poolhouse.sentinel import observers
from poolhouse.testing.fakehub import fake_hub

FILE = "thing-Q4_K_M.gguf"
REPO = "maker/thing-GGUF"


class Ledger:
    """What the net layer reports to, as a list of what it was told."""

    def __init__(self) -> None:
        self.bad: list[tuple[str, str, str]] = []

    def observe(self, kind: str, key: str, event: str, *, scale: float = 1.0) -> None:
        self.bad.append((kind, key, event))

    def trait(self, kind: str, key: str, name: str, value: str) -> None:
        pass

    def clean(self, kind: str, key: str) -> None:
        pass

    def gate(self, kind: str, key: str) -> None:
        return None


@pytest.fixture
def stand(monkeypatch, tmp_path):
    for name in caller.AGENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(http, "check", lambda url: url)
    ledger = Ledger()
    observers.install(ledger)
    with fake_hub({REPO: {FILE: gguf_bytes(extra=4097)}}) as server:
        server.point(monkeypatch, known=False)
        yield server, ledger


def test_a_hub_pull_whose_bytes_are_not_the_listed_sha256_is_refused_without_a_mark_against_the_host(stand):
    server, ledger = stand
    server.corrupt.add(f"{REPO}/{FILE}")
    with pytest.raises(transfer.ChecksumMismatch):
        hub.pull(f"hf:{REPO}/{FILE}")
    assert server.downloads[f"{REPO}/{FILE}"] == 2 and ledger.bad == []
    server.corrupt.clear()
    assert hub.pull(f"hf:{REPO}/{FILE}").name == FILE


def test_a_download_of_anything_else_that_is_not_its_pinned_sha256_is_still_a_mark_against_the_host(stand, tmp_path):
    server, ledger = stand
    url = f"{server.url}/{REPO}/resolve/{server.commit(REPO)}/{FILE}"
    with pytest.raises(net.ChecksumMismatch):
        net.download(url, tmp_path / "a.gguf", net.Want(sha256="0" * 64), pipeline=net.default())
    assert ledger.bad == [("host", "127.0.0.1", "hash_change")]
    with pytest.raises(net.ChecksumMismatch):
        net.download(url, tmp_path / "b.gguf", net.Want(sha256="0" * 64, reputation=False), pipeline=net.default())
    assert len(ledger.bad) == 1
