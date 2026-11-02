"""The shared cargo target folder sheds the products of old source snapshots and keeps everything else."""

from __future__ import annotations

import os
import time

import pytest
import testartifacts

DAY = 24 * 3600
FILES = ["Cargo.toml", "Cargo.lock", "poolhouse-node/Cargo.toml", "poolhouse-node/src/lib.rs", "poolhouse-node/tests/shard.rs",
         "poolhouse-node/tests/pool.rs", "poolhouse-node/tests/kit/mod.rs"]


@pytest.fixture
def target(tmp_path):
    (tmp_path / "app" / "poolhouse-node").mkdir(parents=True)
    (tmp_path / "app" / "poolhouse-node" / "Cargo.toml").write_text('[package]\nname = "poolhouse-node"\nversion = "0.0.0"\n')
    return tmp_path / "target"


def make(target, relative, age_days, directory=False):
    path = target / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir() if directory else path.write_text("x")
    old = time.time() - age_days * DAY
    os.utime(path, (old, old))
    return path


def test_names_are_the_crates_and_the_integration_test_targets_of_the_workspace(target, tmp_path):
    crates, tests = testartifacts.names(tmp_path / "app", FILES)
    assert crates == {"poolhouse_node"} and tests == {"shard", "pool"}


def test_idle_products_of_the_workspace_go_and_recent_ones_stay(target):
    old = [make(target, f"debug/deps/{name}", 9) for name in ("shard-0123456789abcdef", "shard-0123456789abcdef.d", "pool-fedcba9876543210",
                                                                  "libpoolhouse_node-0123456789abcdef.rlib", "poolhouse_node-0123456789abcdef")]
    old.append(make(target, "debug/deps/shard-0123456789abcdef.dSYM", 9, directory=True))
    old.append(make(target, "debug/incremental/shard-1a2b3c4d5e6f7", 9, directory=True))
    recent = [make(target, f"debug/deps/{name}", 1) for name in ("shard-aaaaaaaaaaaaaaaa", "libpoolhouse_node-bbbbbbbbbbbbbbbb.rlib")]
    assert testartifacts.prune(target, {"poolhouse_node"}, {"shard", "pool"}, 3 * DAY) == len(old)
    assert not any(path.exists() for path in old) and all(path.exists() for path in recent)


def test_registry_products_and_unrelated_files_stay_however_old(target):
    kept = [make(target, "debug/deps/libserde-0123456789abcdef.rlib", 90), make(target, "debug/deps/libshard-0123456789abcdef.rlib", 90),
            make(target, "debug/deps/notes.txt", 90), make(target, "debug/build/ring-0123456789abcdef", 90, directory=True),
            make(target, "debug/.fingerprint/shard-0123456789abcdef", 90, directory=True)]
    assert testartifacts.prune(target, {"poolhouse_node"}, {"shard"}, 3 * DAY) == 0
    assert all(path.exists() for path in kept)


def test_the_walk_is_made_once_a_day(target, tmp_path):
    old = make(target, "debug/deps/shard-0123456789abcdef", 9)
    assert testartifacts.prune_when_due(target, tmp_path / "app", FILES, 3 * DAY) == 1 and not old.exists()
    again = make(target, "debug/deps/pool-0123456789abcdef", 9)
    assert testartifacts.prune_when_due(target, tmp_path / "app", FILES, 3 * DAY) == 0 and again.exists()
    stamp = target / testartifacts.STAMP
    os.utime(stamp, (time.time() - 2 * DAY,) * 2)
    assert testartifacts.prune_when_due(target, tmp_path / "app", FILES, 3 * DAY) == 1 and not again.exists()
