"""The Rust tests share one staged workspace per set of sources and one cargo target folder per Cargo.lock and compiler."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest
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


def compile_snapshot(build):
    environment = {**testnode.account_environment(), 'CARGO_TARGET_DIR': str(build.target), 'CARGO_BUILD_JOBS': '1'}
    done = subprocess.run([testnode.tool('cargo'), 'test', '--locked', '--offline', '-p', 'poolhouse-node',
                           '--', '--nocapture', '--test-threads=1'], cwd=build.staged,
                          env=environment, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def snapshots(specimen, root):
    manifest = specimen.root / 'app' / 'poolhouse-node' / 'Cargo.toml'
    manifest.write_text(manifest.read_text() + '\n[dependencies]\nitoa = "=1.0.18"\n')
    subprocess.run([testnode.tool('cargo'), 'generate-lockfile', '--offline'], cwd=specimen.root / 'app',
                   env=testnode.account_environment(), check=True, capture_output=True)
    original = 'fn main() {}\n#[test] fn snapshot() { println!("snapshot-a"); assert_eq!(itoa::Buffer::new().format(7), "7"); }\n'
    specimen.source.write_text(original)
    first = testnode.shared_build(specimen.root / 'app', root=root)
    specimen.source.write_text(original.replace('snapshot-a', 'snapshot-b'))
    second = testnode.shared_build(specimen.root / 'app', root=root)
    specimen.source.write_text(original)
    return first, second


def test_returning_to_an_older_snapshot_runs_its_own_artifact_and_reuses_dependencies(site, tmp_path):  # noqa: F811
    first, second = snapshots(site, tmp_path / 'shared')
    assert 'snapshot-a' in compile_snapshot(first)
    dependency = {path.name: path.stat().st_mtime_ns for path in first.target.glob('debug/deps/libitoa-*.rlib')}
    assert dependency
    assert 'snapshot-b' in compile_snapshot(second)
    restored = testnode.shared_build(site.root / 'app', root=tmp_path / 'shared')
    assert restored == first
    assert 'snapshot-a' in compile_snapshot(restored)
    assert {path.name: path.stat().st_mtime_ns for path in first.target.glob('debug/deps/libitoa-*.rlib')} == dependency


def test_concurrent_snapshots_run_their_own_workspace_artifacts(site, tmp_path):  # noqa: F811
    first, second = snapshots(site, tmp_path / 'shared')
    with ThreadPoolExecutor(2) as workers:
        outcomes = list(workers.map(compile_snapshot, (first, second)))
    assert 'snapshot-a' in outcomes[0] and 'snapshot-b' not in outcomes[0]
    assert 'snapshot-b' in outcomes[1] and 'snapshot-a' not in outcomes[1]


@pytest.mark.parametrize('name', ('RUSTC_WORKSPACE_WRAPPER', 'CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER'))
@pytest.mark.parametrize('value', ('', '/untrusted/compiler'))
def test_workspace_wrapper_overrides_are_refused_before_compilation(site, tmp_path, monkeypatch, name, value):  # noqa: F811
    monkeypatch.setenv(name, value)
    with pytest.raises(testnode.BuildError, match='without environment overrides'):
        testnode.shared_build(site.root / 'app', root=tmp_path / 'shared')


def test_source_keyed_compiler_controls_reject_links(site, tmp_path):  # noqa: F811
    build = testnode.shared_build(site.root / 'app', root=tmp_path / 'shared')
    wrapper = build.staged.parent / 'rustc-workspace'
    wrapper.unlink()
    wrapper.symlink_to(site.source)
    with pytest.raises(testnode.BuildError, match='cannot contain links'):
        testnode.shared_build(site.root / 'app', root=tmp_path / 'shared')
