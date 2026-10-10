"""The warm manifest: recording, reading, merging, and reading ranges ahead.

The OS calls go through stubs in most tests; two tests make the real calls on a temporary file
(read-ahead on Linux and macOS, page residency on any POSIX).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

import pytest
from plan_fixtures import flash_next

from poolhouse.serve import prefault, warm, warm_cli
from poolhouse.serve.tensors import tensors_by_shard

MIB = 1024 * 1024


@pytest.fixture
def model(tmp_path):
    return flash_next(tmp_path, shards=2, experts=256)


def tensor(model, name):
    return next(t for _p, held in tensors_by_shard(model) for t in held if t.name == name)


def observing(wanted: dict[str, list[tuple[int, int]]], model):
    """An `observe` that says these byte ranges (relative to each named tensor) are resident."""
    starts = {tensor(model, n).offset: (n, runs) for n, runs in wanted.items()}

    def observe(path, start, end):
        found = starts.get(start)
        return [(start + a, start + b) for a, b in found[1]] if found else []
    return observe


@pytest.fixture
def now():
    return lambda: datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


class TestRecording:
    def test_only_expert_and_table_tensors_are_observed(self, model, now):
        seen = []

        def observe(path, start, end):
            seen.append(path.name)
            return []
        manifest = warm.record(model, observe=observe, now=now)
        assert set(manifest.tensors) == set()
        assert len(seen) == 3 * 48 + 1

    def test_resident_ranges_are_kept_per_tensor_with_their_shard_and_layer(self, model, now):
        name = "blk.4.ffn_up_exps.weight"
        table = "per_layer_token_embd.weight"
        manifest = warm.record(model, now=now, observe=observing(
            {name: [(0, MIB), (3 * MIB, 4 * MIB)], table: [(10 * MIB, 11 * MIB)]}, model))
        kept = manifest.tensors[name]
        start = tensor(model, name).offset
        assert kept["ranges"] == [[start, start + MIB], [start + 3 * MIB, start + 4 * MIB]]
        assert (kept["shard"], kept["layer"], kept["role"], kept["resident"]) == (0, 4, "experts", 2 * MIB)
        assert manifest.tensors[table]["shard"] == 1
        assert manifest.runs == 1 and manifest.recorded_at.startswith("2026-10-10T12:00")

    def test_nearby_runs_are_joined_and_the_count_is_bounded(self):
        joined = warm._merge([(0, 10), (10 + warm.GAP, 20 + warm.GAP), (10 * warm.GAP, 10 * warm.GAP + 5)])
        assert joined == [(0, 20 + warm.GAP), (10 * warm.GAP, 10 * warm.GAP + 5)]
        many = [(n * 4 * warm.GAP, n * 4 * warm.GAP + 1) for n in range(warm.MOST_RUNS + 50)]
        assert len(warm._thinned(many)) <= warm.MOST_RUNS

    def test_a_second_recording_merges_with_the_first(self, model, now):
        name = "blk.5.ffn_down_exps.weight"
        first = warm.record(model, now=now, observe=observing({name: [(0, MIB)]}, model))
        warm.write(model, first)
        second = warm.record(model, now=now, observe=observing({name: [(8 * MIB, 9 * MIB)]}, model))
        start = tensor(model, name).offset
        assert second.runs == 2
        assert second.tensors[name]["ranges"] == [[start, start + MIB], [start + 8 * MIB, start + 9 * MIB]]


class TestManifestFile:
    def test_it_sits_beside_the_first_shard_and_round_trips(self, model, now):
        name = "blk.6.ffn_gate_exps.weight"
        manifest = warm.record(model, now=now, observe=observing({name: [(0, MIB)]}, model))
        where = warm.write(model, manifest)
        assert where == model.with_name(model.name + ".warm.json")
        back = warm.read(model)
        assert back is not None and back.tensors == manifest.tensors and back.runs == 1
        saved = json.loads(where.read_text())
        assert saved["version"] == warm.VERSION
        assert set(saved["planned"]) == {"per_expert_counts", "ngram_row_counts"}
        assert "llama.cpp patch" in saved["planned"]["per_expert_counts"]

    def test_a_manifest_for_files_that_have_changed_is_not_read(self, model, now):
        warm.write(model, warm.record(model, now=now, observe=lambda *_: []))
        second = model.with_name(model.name.replace("00001", "00002"))
        second.write_bytes(second.read_bytes() + b"\0")
        assert warm.read(model) is None

    def test_a_missing_or_corrupt_manifest_reads_as_none(self, tmp_path):
        first = flash_next(tmp_path, shards=2, experts=256)
        assert warm.read(first) is None
        warm.path_for(first).write_text("{not json")
        assert warm.read(first) is None

    def test_another_version_is_not_read(self, tmp_path, now):
        first = flash_next(tmp_path, shards=2, experts=256)
        warm.write(first, warm.record(first, now=now, observe=lambda *_: []))
        saved = json.loads(warm.path_for(first).read_text())
        warm.path_for(first).write_text(json.dumps({**saved, "version": warm.VERSION + 1}))
        assert warm.read(first) is None


class TestReadingAhead:
    def manifest(self, model, now):
        hot = "blk.7.ffn_up_exps.weight"
        cold = "blk.8.ffn_up_exps.weight"
        size = tensor(model, hot).bytes
        return warm.record(model, now=now, observe=observing({
            hot: [(0, size // 2)], cold: [(0, MIB)]}, model)), hot, cold

    def test_tensors_with_the_larger_resident_share_come_first_and_the_budget_stops_it(self, model, now):
        manifest, hot, _cold = self.manifest(model, now)
        ranges = warm.hot_ranges(model, manifest, 10 * MIB)
        assert next(r[1] for r in ranges) == tensor(model, hot).offset
        assert sum(end - start for _p, start, end in ranges) == 10 * MIB

    def test_each_range_is_asked_for_in_chunks_through_the_platform_call(self, model, now):
        manifest, _hot, _cold = self.manifest(model, now)
        calls = []
        ranges = warm.hot_ranges(model, manifest, 1 << 40)
        asked = warm.prefault_ranges(ranges, advise=lambda fd, off, n: calls.append((off, n)))
        assert asked == sum(n for _o, n in calls) == sum(e - s for _p, s, e in ranges)
        assert all(n <= warm.CHUNK for _o, n in calls) and len(calls) >= len(ranges)

    def test_a_range_over_the_chunk_size_is_split(self, model):
        calls = []
        span = 2 * warm.CHUNK + 5
        warm.prefault_ranges([(model, 100, 100 + span)], advise=lambda fd, off, n: calls.append((off, n)))
        assert calls == [(100, warm.CHUNK), (100 + warm.CHUNK, warm.CHUNK), (100 + 2 * warm.CHUNK, 5)]

    def test_a_stop_flag_ends_it_early(self, model, now):
        import threading
        manifest, _h, _c = self.manifest(model, now)
        stop = threading.Event()
        stop.set()
        ranges = warm.hot_ranges(model, manifest, 1 << 40)
        assert warm.prefault_ranges(ranges, advise=lambda *_: None, stop=stop) == 0

    def test_start_runs_in_the_background_and_does_nothing_without_a_manifest(self, tmp_path, now):
        first = flash_next(tmp_path, shards=2, experts=256)
        assert warm.start(first, 1 << 30) is None
        manifest, _h, _c = self.manifest(first, now)
        warm.write(first, manifest)
        calls = []
        worker = warm.start(first, 1 << 30, advise=lambda fd, off, n: calls.append(n))
        worker.join(10)
        assert not worker.is_alive() and calls
        assert warm.start(first, 0) is None

    def test_the_command_shows_records_and_reads_ahead(self, tmp_path, now, capsys, monkeypatch):
        import argparse
        first = flash_next(tmp_path, shards=2, experts=256)
        monkeypatch.setattr(prefault, "resident_runs", lambda *_a, **_k: [])
        args = argparse.Namespace(model=str(first), record=False, prefault=False, budget=1.0)
        assert warm_cli.cmd_warm(args) == 0 and "no warm manifest" in capsys.readouterr().out
        args.prefault = True
        assert warm_cli.cmd_warm(args) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX calls")
class TestPlatformCalls:
    def test_the_real_read_ahead_call_accepts_a_range_of_a_real_file(self, tmp_path):
        if sys.platform not in ("darwin",) and not sys.platform.startswith("linux"):
            pytest.skip("no read-ahead call here")
        path = tmp_path / "blob"
        path.write_bytes(os.urandom(256 * 1024))
        fd = os.open(path, os.O_RDONLY)
        try:
            prefault.advise_for_platform()(fd, 0, 128 * 1024)
        finally:
            os.close(fd)

    def test_residency_is_reported_as_whole_page_runs_inside_the_range(self, tmp_path):
        path = tmp_path / "blob"
        page = prefault.PAGE
        path.write_bytes(os.urandom(8 * page))
        runs = prefault.resident_runs(path, page + 7, 5 * page - 3)
        assert runs and all(page + 7 <= a < b <= 5 * page - 3 for a, b in runs)
        assert runs[0][0] == page + 7 and runs[-1][1] == 5 * page - 3
