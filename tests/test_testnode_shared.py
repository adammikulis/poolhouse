"""The Rust tests share one staged workspace per set of sources and one cargo target folder per Cargo.lock and compiler."""

from __future__ import annotations

import os

import testnode
import testreuse_plugin as plugin
from test_testnode import site  # noqa: F401  (fixture)


def test_the_same_sources_are_staged_once_and_share_the_target(site, tmp_path):  # noqa: F811
    root = tmp_path / "shared"
    first = testnode.shared_build(site.root / "app", root=root)
    staged_stamp = (first.staged / "poolhouse-node" / "src" / "main.rs").stat().st_mtime_ns
    second = testnode.shared_build(site.root / "app", root=root)
    assert first == second and first.target.is_dir()
    assert (second.staged / "poolhouse-node" / "src" / "main.rs").stat().st_mtime_ns == staged_stamp
    assert (first.staged / "Cargo.toml").is_file() and first.staged.is_relative_to(root) and first.target.is_relative_to(root)


def test_an_edited_crate_is_staged_beside_the_old_copy_and_keeps_the_target(site, tmp_path):  # noqa: F811
    root = tmp_path / "shared"
    before = testnode.shared_build(site.root / "app", root=root)
    site.source.write_text(site.source.read_text() + "// edited\n")
    after = testnode.shared_build(site.root / "app", root=root)
    assert after.staged != before.staged and after.target == before.target
    assert (after.staged / "poolhouse-node" / "src" / "main.rs").read_text().endswith("// edited\n")
    assert not (before.staged / "poolhouse-node" / "src" / "main.rs").read_text().endswith("// edited\n")


def test_a_changed_lock_file_gets_its_own_target(site, tmp_path):  # noqa: F811
    root = tmp_path / "shared"
    before = testnode.shared_build(site.root / "app", root=root)
    lock = site.root / "app" / "Cargo.lock"
    lock.write_text(lock.read_text() + "\n# another resolution\n")
    assert testnode.shared_build(site.root / "app", root=root).target != before.target


def test_the_folder_is_outside_the_checkout_and_the_poolhouse_roots_and_the_reuse_plugin_allows_it(monkeypatch, tmp_path):
    monkeypatch.delenv(testnode.CARGO_ENV, raising=False)
    folder = testnode.cargo_root()
    assert folder == testnode.account() / ".cache" / "test-cargo"
    assert not folder.is_relative_to(testnode.account() / ".poolhouse") and not folder.is_relative_to(testnode.account() / ".cache" / "poolhouse")
    assert plugin._write_ok(str(plugin.CARGO_CACHE / "targets" / "k" / "debug" / "x"))
    assert not plugin._write_ok(str(plugin.CARGO_CACHE.parent / "poolhouse" / "x"))


def test_a_named_folder_replaces_the_account_cache(monkeypatch, tmp_path):
    monkeypatch.setenv(testnode.CARGO_ENV, str(tmp_path / "named"))
    assert testnode.cargo_root() == tmp_path / "named" and os.fspath(testnode.cargo_root()).startswith(str(tmp_path))
