"""``poolhouse-models adopt``: the decider's files, a weights view and the drafters join the hub cache."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import home, http
from poolhouse.hub import derived, hfstore
from poolhouse.serve import models_adopt, models_cli
from poolhouse.testing.fakehub import fake_hub
from tests import adopt_support as sup


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """The owner's machine in small: every model kept outside the hub cache, and the hub cache it joins."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    monkeypatch.setattr(http, "check", lambda url: url)
    repos = sup.hub_files()
    with fake_hub(repos) as server:
        sup.served(repos, server)
        server.point(monkeypatch)
        yield build(server, repos)


def build(server, repos) -> SimpleNamespace:
    rev = {repo: server.commit(repo) for repo in (sup.VIEW, sup.BASE, sup.DECIDER, sup.GEMMA)}
    held = {**repos, sup.GEMMA: sup.gemma_files()}
    plain = {repo: frozenset(r for r in files if not r.endswith(".safetensors")) for repo, files in held.items()}
    view = {rel: body for rel, body in repos[sup.VIEW].items()
            if not any(rel == f"model-{n:05d}-of-00022.safetensors" for n in sup.PLE_SHARDS)}
    view |= {"config.json": b'{"model_type": "qwen4_exp", "text_config": {"ple_storage": {}}}',
             "model.safetensors.index.json": b'{"weight_map": {"t1": "model-00001-of-00022.safetensors"}}',
             "EXTERNAL_PLE.json": b'{"format": "qwen4_exp_external_ple_v1"}', "ple-store.json": b'{"version": 2}'}
    views = sup.write(home.cache("spec", "weights", f"models--mlx-community--Qwen3.8-Flash-Next-4bit-{rev[sup.VIEW]}"), view)
    records = {repo: {rel: sup.record_of(body, etag=sup.sha1(body) if rel in plain[repo] else "f" * 64)
                      for rel, body in files.items()} for repo, files in held.items()}
    decide = home.cache("decide", "snapshots")
    base = sup.write(decide / "Qwen--Qwen3.5-2B-Base" / rev[sup.BASE], repos[sup.BASE], records[sup.BASE])
    decider = sup.write(decide / "StrandsAgents--strands-decider-2B-hobson-v19" / rev[sup.DECIDER], repos[sup.DECIDER],
                        records[sup.DECIDER])
    gemma = sup.write(decide / "google--gemma-4-E2B" / rev[sup.GEMMA], sup.gemma_files(), records[sup.GEMMA])
    sup.lay_in_hub(sup.BASE, rev[sup.BASE], repos[sup.BASE], plain=plain[sup.BASE],
                   shared=frozenset({"model.safetensors-00001-of-00001.safetensors"}))
    sup.lay_in_hub(sup.DECIDER, rev[sup.DECIDER], repos[sup.DECIDER], plain=plain[sup.DECIDER])
    sup.lay_in_hub(sup.DFLASH, sup.DFLASH_COMMIT, {"config.json": b"{}"}, plain=frozenset({"config.json"}))
    for commit in sup.HEADS_COMMITS:
        sup.lay_in_hub(sup.HEADS, commit, {sup.HEAD_FILE: sup.gguf()})
    dflash = home.cache("spec", "dflash") / f"{sup.DFLASH_COMMIT}-4bit.safetensors"
    head = home.cache("spec", "mtp") / "mtp-Qwen3.8-Flash-Next-shared-BF16-4bit.safetensors"
    sup.write(dflash.parent, {dflash.name: sup.safetensors("drafter", 3, 512)})
    sup.write(head.parent, {head.name: sup.safetensors("head", 4, 512)})
    return SimpleNamespace(server=server, rev=rev, repos=repos, views=views, base=base, decider=decider, gemma=gemma,
                           dflash=dflash, head=head, tmp=Path(os.environ["HF_HUB_CACHE"]).parent)


def tree(*roots: Path) -> list[str]:
    return sorted(f"{p}:{p.stat().st_ino}" for root in roots if root.exists() for p in root.rglob("*"))


def row(report, name: str):
    return next(r for r in report.rows if r.path.endswith(name))


def test_a_plan_says_what_each_source_becomes_and_changes_nothing(machine):
    before = tree(home.cache("decide"), home.cache("spec"), hfstore.repo_root(sup.BASE).parent)
    report = models_adopt.run(apply=False)
    assert tree(home.cache("decide"), home.cache("spec"), hfstore.repo_root(sup.BASE).parent) == before
    assert report.mode == "plan" and report.ok
    kinds = sorted((part.kind, part.repo) for part in report.parts)
    assert [k for k, _ in kinds] == ["decide", "decide", "decide", "drafter", "drafter", "view"]
    assert report.total("have")[0] == 9 and report.total("derived")[0] == 4
    assert report.total("link")[0] == 4 + 23 + 2
    text = "\n".join(report.lines())
    assert "extra disk  0 B" in text and "freed by run" in text
    assert "still lacks 7 file(s) the Hub lists" in text
    assert "poolhouse-models pull hf:mlx-community/Qwen3.8-Flash-Next-4bit@" in text


def test_a_run_adopts_the_view_and_keeps_its_names(machine):
    models_adopt.run(apply=True)
    root = hfstore.repo_root(sup.VIEW)
    shard = machine.views / "model-00001-of-00022.safetensors"
    blob = root / "blobs" / hashlib.sha256(shard.read_bytes()).hexdigest()
    assert shard.samefile(blob) and shard.stat().st_nlink == 2
    link = root / "snapshots" / machine.rev[sup.VIEW] / shard.name
    assert link.is_symlink() and link.resolve() == blob.resolve()
    for derived_name in ("config.json", "model.safetensors.index.json", "EXTERNAL_PLE.json", "ple-store.json"):
        assert (machine.views / derived_name).exists()
        assert not (root / "snapshots" / machine.rev[sup.VIEW] / derived_name).exists()
    assert (root / "refs" / "main").read_text() == machine.rev[sup.VIEW]


def test_a_run_removes_the_decide_copies_the_cache_already_holds_and_keeps_their_records(machine):
    inode = (hfstore.repo_root(sup.BASE) / "blobs" / hashlib.sha256(machine.repos[sup.BASE][
        "model.safetensors-00001-of-00001.safetensors"]).hexdigest()).resolve().stat().st_ino
    models_adopt.run(apply=True)
    assert not machine.base.exists() and not machine.decider.exists()
    assert not (home.cache("decide", "snapshots") / "Qwen--Qwen3.5-2B-Base").exists()
    shard = machine.repos[sup.BASE]["model.safetensors-00001-of-00001.safetensors"]
    link = hfstore.repo_root(sup.BASE) / "snapshots" / machine.rev[sup.BASE] / "model.safetensors-00001-of-00001.safetensors"
    assert link.resolve().stat().st_ino == inode and link.read_bytes() == shard
    kept = hfstore.repo_root(sup.BASE) / "blobs" / f"{hashlib.sha256(shard).hexdigest()}.provenance.json"
    assert json.loads(kept.read_text())["sha256"] == hashlib.sha256(shard).hexdigest()


def test_a_run_moves_a_model_the_cache_lacks_in_by_link_and_leaves_no_folder(machine):
    weights = machine.gemma / "model.safetensors"
    inode = weights.stat().st_ino
    models_adopt.run(apply=True)
    root = hfstore.repo_root(sup.GEMMA)
    blob = root / "blobs" / hashlib.sha256(sup.gemma_files()["model.safetensors"]).hexdigest()
    assert blob.stat().st_ino == inode and not machine.gemma.exists()
    small = sup.gemma_files()["config.json"]
    assert (root / "blobs" / sup.sha1(small)).read_bytes() == small
    assert (root / "snapshots" / machine.rev[sup.GEMMA] / "tokenizer.json").read_bytes() == sup.gemma_files()["tokenizer.json"]
    assert (blob.parent / f"{blob.name}.provenance.json").is_file()
    assert not (home.cache("decide", "snapshots") / "google--gemma-4-E2B").exists()


def test_a_run_files_each_drafter_under_its_source_and_the_loader_finds_it(machine):
    body = machine.dflash.read_bytes()
    models_adopt.run(apply=True)
    source = derived.Source(sup.DFLASH, sup.DFLASH_COMMIT, "")
    kept = derived.lookup(source, "mlx-q4-g64")
    assert kept is not None and kept.read_bytes() == body and not machine.dflash.exists()
    record = json.loads((kept.parent / derived.RECORD).read_text())
    assert record["origin"] == f"adopted from {machine.dflash}" and record["source"]["repo"] == sup.DFLASH
    main = derived.Source(sup.HEADS, sup.HEADS_COMMITS[0], sup.HEAD_FILE)
    other = derived.Source(sup.HEADS, sup.HEADS_COMMITS[1], sup.HEAD_FILE)
    assert [derived.lookup(one, "mlx-q4-g32") is not None for one in (main, other)].count(True) == 1
    assert not machine.head.exists()


def test_a_second_run_has_nothing_left_to_move(machine):
    models_adopt.run(apply=True)
    again = models_adopt.run(apply=True)
    assert again.ok and {row.action for row in again.rows} <= {"adopted", "derived"}
    assert [part.kind for part in again.parts] == ["view"]
    assert models_adopt.command("run") == 0


def test_a_decide_copy_with_other_bytes_than_the_cache_holds_is_never_removed(machine):
    shard = machine.base / "model.safetensors-00001-of-00001.safetensors"
    other = bytearray(shard.read_bytes())
    other[-1] ^= 1
    shard.write_bytes(bytes(other))
    report = models_adopt.run(apply=True)
    assert not report.ok and row(report, shard.name).action == "blocked"
    assert shard.read_bytes() == bytes(other) and machine.base.exists()
    assert models_adopt.command("plan") == 1


def test_a_cache_file_that_is_not_the_decide_copy_blocks_its_removal(machine):
    link = hfstore.repo_root(sup.DECIDER) / "snapshots" / machine.rev[sup.DECIDER] / "head.safetensors"
    link.resolve().write_bytes(sup.safetensors("head", 2))
    report = models_adopt.run(apply=True)
    assert row(report, "/head.safetensors").action == "blocked" and "other bytes" in row(report, "/head.safetensors").why
    assert (machine.decider / "head.safetensors").exists()


def test_a_drafter_whose_source_snapshots_differ_is_not_adopted(machine):
    link = hfstore.repo_root(sup.HEADS) / "snapshots" / sup.HEADS_COMMITS[1] / sup.HEAD_FILE
    link.unlink()
    link.write_bytes(sup.gguf() + b"other")
    report = models_adopt.run(apply=True)
    assert row(report, machine.head.name).action == "blocked" and "different bytes" in row(report, machine.head.name).why
    assert machine.head.exists()


def test_a_drafter_nothing_in_the_cache_names_is_not_adopted(machine):
    orphan = machine.dflash.with_name(f"{'a' * 40}-4bit.safetensors")
    orphan.write_bytes(sup.safetensors("x"))
    report = models_adopt.run(apply=True)
    assert row(report, orphan.name).action == "blocked" and "no snapshot" in row(report, orphan.name).why
    assert orphan.exists()


def test_a_drafter_the_cache_already_has_other_bytes_for_is_not_replaced(machine):
    source = derived.Source(sup.DFLASH, sup.DFLASH_COMMIT, "")
    derived.save(source, "mlx-q4-g64", lambda stage: stage.write_bytes(sup.safetensors("kept", 5, 64)))
    report = models_adopt.run(apply=True)
    assert row(report, machine.dflash.name).action == "blocked" and machine.dflash.exists()
    assert derived.lookup(source, "mlx-q4-g64").read_bytes() == sup.safetensors("kept", 5, 64)


def test_offline_the_weights_are_vouched_for_by_their_structure_and_the_rest_stays(machine, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "http://127.0.0.1:9")
    report = models_adopt.run(apply=False)
    view = next(part for part in report.parts if part.kind == "view")
    actions = {r.rel: r.action for r in view.rows}
    assert actions["model-00001-of-00022.safetensors"] == "link" and actions["README.md"] == "derived"
    assert "could not be read" in view.notes[0]
    gemma = next(part for part in report.parts if part.repo == sup.GEMMA)
    assert {r.action for r in gemma.rows} == {"link"} and {r.how for r in gemma.rows} == {"its download record"}


def test_the_plan_option_that_skips_the_hub_never_asks_it(machine):
    machine.server.hub_seen.clear()
    report = models_adopt.run(apply=False, offline=True)
    assert machine.server.hub_seen == [] and "--offline" in report.parts[0].notes[0]


def test_an_unwritable_cache_refuses_before_anything_is_read(machine, monkeypatch, tmp_path):
    (tmp_path / "file").write_text("x")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "file" / "hub"))
    report = models_adopt.run(apply=True)
    assert report.blocked and "cannot be written" in report.blocked[0] and not report.parts


def test_a_machine_with_nothing_outside_the_cache_says_so(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    report = models_adopt.run(apply=False)
    assert report.ok and "nothing to adopt" in report.notes[0]


def test_the_command_prints_json_and_exits_by_the_outcome(machine, capsys):
    assert models_cli.run(["adopt", "plan", "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mode"] == "plan" and printed["extra_disk_bytes"] == 0 and len(printed["parts"]) == 6
    assert models_cli.run(["adopt", "run"]) == 0
    assert "linked" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        models_cli.run(["adopt", "now"])
