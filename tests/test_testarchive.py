"""scripts/testarchive.py: the runner fetches the pinned release archive once, into a shared folder, and names it for the test that inspects
it. No test here reaches a network: a stand-in writes the file a download would, and the download itself is read at its one call."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
import testarchive

from poolhouse import net
from poolhouse.serve import llamacpp_provider as provider

ROOT = Path(__file__).resolve().parents[1]
BYTES = b"the pinned bytes of a stand-in archive"
READS_IT = "tests/test_bonsai_provider.py"


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    """A folder for the archive, a pin that matches ``BYTES`` and a computer the runtime is supplied for."""
    monkeypatch.setenv(testarchive.FOLDER_ENV, str(tmp_path / "archives"))
    monkeypatch.setenv(testarchive.ENV, "")
    monkeypatch.setattr(provider, "ARCHIVE_SHA256", hashlib.sha256(BYTES).hexdigest())
    monkeypatch.setattr(provider, "supported", lambda: True)
    return tmp_path / "archives"


def writes(content: bytes, asked: list[Path]):
    def fetch(target: Path) -> None:
        asked.append(target)
        target.write_bytes(content)

    return fetch


def test_the_archive_is_fetched_once_and_the_same_file_serves_every_later_run(pinned):
    asked: list[Path] = []
    first = testarchive.ensure(fetch=writes(BYTES, asked))
    second = testarchive.ensure(fetch=writes(b"never asked", asked))
    assert first == second == asked[0] and asked == [first]
    assert first.is_relative_to(pinned) and first.read_bytes() == BYTES


@pytest.mark.parametrize("change", ["altered", "link"])
def test_a_held_file_that_is_not_the_pinned_bytes_is_fetched_again_and_never_served(pinned, tmp_path, change):
    held = testarchive.ensure(fetch=writes(BYTES, []))
    if change == "altered":
        held.write_bytes(b"planted")
    else:
        other = tmp_path / "elsewhere"
        other.write_bytes(BYTES)
        held.unlink()
        held.symlink_to(other)
    asked: list[Path] = []
    assert testarchive.ensure(fetch=writes(BYTES, asked)).read_bytes() == BYTES and asked == [held] and not held.is_symlink()


def test_a_download_that_gives_other_bytes_leaves_nothing_to_be_served(pinned):
    with pytest.raises(testarchive.FetchError, match="did not give the pinned bytes"):
        testarchive.ensure(fetch=writes(b"another file", []))
    assert list(pinned.rglob("release.tar.gz")) == []


def test_a_run_that_reads_the_archive_is_given_it_and_any_other_run_fetches_nothing(pinned, monkeypatch):
    asked: list[Path] = []
    real = testarchive.ensure
    monkeypatch.setattr(testarchive, "ensure", lambda **given: real(fetch=writes(BYTES, asked), **given))
    testarchive.supply(ROOT, ["tests/test_lazy_exports.py"], None, say=lambda _text: None)
    assert asked == [] and not os.environ[testarchive.ENV]
    testarchive.supply(ROOT, [READS_IT], None, say=lambda _text: None)
    assert Path(os.environ[testarchive.ENV]) == asked[0] and asked[0].read_bytes() == BYTES


def test_a_tier_run_names_no_file_and_is_given_the_archive(pinned):
    assert testarchive.needed(ROOT, None) is True and testarchive.needed(ROOT, []) is True


def test_a_computer_the_runtime_is_not_supplied_for_fetches_nothing(pinned, monkeypatch):
    monkeypatch.setattr(provider, "supported", lambda: False)
    monkeypatch.setattr(testarchive, "ensure", lambda **_: pytest.fail("fetched"))
    testarchive.supply(ROOT, [READS_IT], None, say=lambda _text: None)
    testarchive.supply(ROOT, None, None, say=lambda _text: None)
    assert not os.environ[testarchive.ENV]


def test_an_archive_the_caller_already_names_is_kept(pinned, monkeypatch):
    monkeypatch.setenv(testarchive.ENV, "/the/callers/archive")
    monkeypatch.setattr(testarchive, "ensure", lambda **_: pytest.fail("fetched"))
    testarchive.supply(ROOT, [READS_IT], None, say=lambda _text: None)
    assert os.environ[testarchive.ENV] == "/the/callers/archive"


@pytest.mark.parametrize("machine,system,answer", [("arm64", "Darwin", True), ("aarch64", "Darwin", True), ("x86_64", "Darwin", False),
                                                   ("arm64", "Linux", False), ("AMD64", "Windows", False)])
def test_the_runtime_is_supplied_for_apple_silicon_only(monkeypatch, machine, system, answer):
    monkeypatch.setattr(provider.platform, "system", lambda: system)
    monkeypatch.setattr(provider.platform, "machine", lambda: machine)
    assert provider.supported() is answer


def test_the_download_asks_for_the_providers_address_size_and_digest_and_leaves_no_record_beside_it(tmp_path, monkeypatch):
    asked = []

    def download(url, target, want, *, pipeline):
        asked.append((url, want, pipeline))
        target.write_bytes(BYTES)
        target.with_name(target.name + ".fetch.json").write_text("{}")

    monkeypatch.setattr(net, "download", download)
    testarchive.download(tmp_path / "release.tar.gz")
    [(url, want, pipeline)] = asked
    assert url == provider.URL and pipeline is net.default()
    assert (want.sha256, want.size, want.require_digest) == (provider.ARCHIVE_SHA256, provider.ARCHIVE_BYTES, True)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["release.tar.gz"]


def test_the_fetch_process_has_state_and_a_cache_of_its_own_and_its_refusal_is_the_runners(pinned, tmp_path, monkeypatch):
    seen = []

    def run(argv, **given):
        seen.append((argv, given["env"]["POOLHOUSE_HOME"], given["env"]["POOLHOUSE_CACHE"]))
        return subprocess.CompletedProcess(argv, 1, "", "Traceback\nRefused: the host is not admitted\n")

    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "the-accounts-state"))
    monkeypatch.setattr(testarchive.subprocess, "run", run)
    with pytest.raises(testarchive.FetchError, match="the host is not admitted"):
        testarchive.isolated(tmp_path / "release.tar.gz")
    [(argv, state, cache)] = seen
    assert argv[0] == sys.executable and argv[2:] == ["download", str(tmp_path / "release.tar.gz")]
    assert str(tmp_path / "the-accounts-state") not in (state, cache) and state != cache
    assert not Path(state).parent.exists(), "the temporary state is removed with the fetch"
