"""Sentinel sealing preserves binary key bytes across file reopens."""

from poolhouse.sentinel.sealed import SealedFile


def test_key_bytes_including_newlines_reopen_the_same_sealed_state(tmp_path, monkeypatch):
    expected = bytes(range(32))
    monkeypatch.setattr("poolhouse.sentinel.sealed.secrets.token_bytes",
                        lambda size: expected[:size])
    path = tmp_path / "state.json"
    sealed = SealedFile(path)
    payload = {"records": [{"id": "fixture-record", "state": "quarantined"}]}
    sealed.save(payload)
    assert sealed.keyfile.read_bytes() == expected
    reopened = SealedFile(path).load()
    assert reopened.status == "ok" and reopened.payload == payload
