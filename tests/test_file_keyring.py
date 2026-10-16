"""The file keyring that stands in for the Keychain in tests."""

from __future__ import annotations

import threading

from onboard_support import FileKeyring


def test_a_reader_never_sees_a_key_missing_while_another_process_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_TEST_KEYRING", str(tmp_path / "keyring.json"))
    ring = FileKeyring()
    ring.set_password("svc", "key", "v1:stable")
    stop = threading.Event()

    def writer():
        n = 0
        while not stop.is_set():
            n += 1
            ring.set_password("svc", f"other{n % 50}", "x" * 4000)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        misses = sum(ring.get_password("svc", "key") != "v1:stable" for _ in range(20000))
    finally:
        stop.set()
        thread.join()
    assert misses == 0
