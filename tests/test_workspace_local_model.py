"""Downloaded local model selection, context fit and identity contracts."""

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse.hub.discover import ModelInfo
from poolhouse.hub.probe import MachineMemory
from poolhouse.workspace import localmodel

GB = 10**9
BIG = MachineMemory(total_ram=64 * GB, available_ram=60 * GB)


def info(name: str, gb: float, arch: str = "qwen3moe") -> ModelInfo:
    return ModelInfo(id=f"hf:x/{name}", name=name, path=Path("/nonexistent") / name, format="gguf",
                     size_bytes=int(gb * GB), source="huggingface", quantization="Q4_K_M",
                     parameters=int(35e9), architecture=arch)

def test_auto_takes_the_best_downloaded_qwen_and_never_flash_next():
    have = [info("Qwen3.8-Flash-Next-Q4_K_M.gguf", 4), info("Llama-70B-Q4.gguf", 40, "llama"),
            info("Qwen3.6-35B-A3B-Q4_K_M.gguf", 20)]
    pick = localmodel.choose(localmodel.AUTO, installed=have,
                             selection=localmodel.Selection(machine=BIG))
    assert pick.ok and pick.name.startswith("Qwen3.6-35B-A3B")
    assert localmodel.agent_name(pick.name) == "local-qwen3.6-35b-a3b"

def test_auto_includes_dense_qwen_and_skips_models_that_do_not_fit_requested_context(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    dense = info("ThinkingCap-Qwen3.8-27B-IQ4_XS.gguf", 14.4, "qwen35")
    moe = info("Qwen3.6-35B-A3B-Q4_K_M.gguf", 20)
    dense_path, moe_path = tmp_path / "dense.gguf", tmp_path / "moe.gguf"
    dense_path.touch()
    moe_path.touch()

    def row(model, path):
        return NS(candidate=NS(name=model.name, ref=model.id, path=path,
                               size_bytes=model.size_bytes), verdict="green", reason="fits")

    rows = [row(dense, dense_path), row(moe, moe_path)]
    monkeypatch.setattr(localmodel, "_ranked", lambda *_: rows)
    monkeypatch.setattr(localmodel.suggest, "largest_fit", lambda path, **_: NS(
        context=65536 if path == str(dense_path) else 131072, verdict="green"))

    pick = localmodel.choose(localmodel.AUTO, installed=[dense, moe],
                             selection=localmodel.Selection(machine=BIG, context=131072))

    assert pick.ok and pick.name == moe.name

def test_auto_selects_a_dense_qwen_when_it_fits_and_no_moe_is_available(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    dense = info("ThinkingCap-Qwen3.8-27B-IQ4_XS.gguf", 14.4, "qwen35")
    model_path = tmp_path / "thinkingcap.gguf"
    model_path.touch()
    row = NS(candidate=NS(name=dense.name, ref=dense.id, path=model_path,
                          size_bytes=dense.size_bytes), verdict="green", reason="fits")
    monkeypatch.setattr(localmodel, "_ranked", lambda *_: [row])
    monkeypatch.setattr(localmodel.suggest, "largest_fit", lambda *_args, **_kwargs:
                        NS(context=131072, verdict="green"))

    pick = localmodel.choose(localmodel.AUTO, installed=[dense],
                             selection=localmodel.Selection(machine=BIG, context=131072))

    assert pick.ok and pick.name == dense.name

def test_with_only_flash_next_downloaded_it_says_what_to_fetch_and_picks_nothing():
    pick = localmodel.choose(localmodel.AUTO, installed=[info("Qwen3.8-Flash-Next-Q4.gguf", 4)],
                             selection=localmodel.Selection(machine=BIG, search=False))
    assert not pick.ok and "poolhouse-models find" in pick.hint and not pick.ref

def test_a_model_that_does_not_fit_gets_one_line_and_the_smaller_choice():
    have = [info("Qwen3.6-35B-A3B-Q4.gguf", 200), info("Qwen3-30B-A3B-Q4.gguf", 18)]
    pick = localmodel.choose("Qwen3.6-35B-A3B-Q4.gguf", installed=have,
                             selection=localmodel.Selection(machine=BIG))
    assert not pick.ok and "red" in pick.problem and "Qwen3-30B-A3B-Q4.gguf" in pick.problem
    assert "\n" not in pick.problem

def test_an_id_that_is_not_downloaded_is_refused():
    assert not localmodel.choose("nope.gguf", installed=[],
                                 selection=localmodel.Selection(machine=BIG)).ok

def test_automatic_and_coding_agents_choose_the_best_qwen_and_never_flash_next():
    have = [info("Qwen3.8-Flash-Next-UD-Q4_K_XL.gguf", 118), info("Qwen3.6-35B-A3B-Q4_K_M.gguf", 22),
            info("Qwen3.8-27B (Q4_K_XL)", 18, "qwen35"), info("Qwen3.8-27B (Q4_K_M)", 17, "qwen35")]
    pick = localmodel.choose(localmodel.AUTO, installed=have,
                             selection=localmodel.Selection(machine=BIG, coding=True))
    assert pick.ok and pick.name == "Qwen3.8-27B (Q4_K_XL)"
    plain = localmodel.choose(localmodel.AUTO, installed=have,
                              selection=localmodel.Selection(machine=BIG))
    assert plain.name == "Qwen3.8-27B (Q4_K_M)"
    only_flash = localmodel.choose(localmodel.AUTO, installed=have[:1],
                                   selection=localmodel.Selection(machine=BIG, coding=True))
    assert not only_flash.ok and "27B" in only_flash.problem
    named = localmodel.choose("Qwen3.8-Flash-Next-UD-Q4_K_XL.gguf", installed=have,
                              selection=localmodel.Selection(machine=BIG))
    assert named.name.startswith("Qwen3.8-Flash-Next")

def test_exact_downloaded_model_path_resolves_cache_symlinks_without_basename_fallback(tmp_path):
    snapshot=tmp_path/'snapshot'
    model=snapshot/'Qwen3.8-27B-Q4_K_M.gguf'
    if os.name == 'nt':
        backing=tmp_path/'backing'
        backing.mkdir()
        blob=backing/model.name
        blob.write_bytes(b'GGUF installed fixture')
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(snapshot), str(backing)],
                       check=True, capture_output=True)
        assert snapshot.resolve() == backing.resolve()
    else:
        blob=tmp_path/'blob'
        blob.write_bytes(b'GGUF installed fixture')
        snapshot.mkdir()
        model.symlink_to(blob)
    candidate=replace(info(model.name,18),path=model)
    for asked in (str(model),str(blob)):
        pick=localmodel.choose(asked,installed=[candidate],
                               selection=localmodel.Selection(machine=BIG))
        assert pick.ok and pick.ref==str(candidate.path)
    missing=tmp_path/'elsewhere'/model.name
    assert not localmodel.choose(str(missing),installed=[candidate],
                                 selection=localmodel.Selection(machine=BIG)).ok
    model.unlink()
    assert not localmodel.choose(str(model),installed=[candidate],
                                 selection=localmodel.Selection(machine=BIG)).ok

@pytest.mark.parametrize('name,expected', [
    ('Qwen3.8-27B', 'Qwen3.8-27B'),
    ('Qwen3.8-27B (Q4_K_XL)', 'qwen3.8-27b'),
    ('hf:downloaded-model-namespace/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf',
     'hf:downloaded-model-namespace/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf'),
    ('hf:' + 'downloaded-model-namespace-' * 4 + '/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf',
     'qwen3.8-27b-ud'),
])
def test_model_identity_keeps_valid_claims_and_bounds_display_names(name, expected):
    from poolhouse.workspace.modelid import clean_model
    assert localmodel.model_identity(name) == expected
    assert clean_model(localmodel.model_identity(name)) == expected


def test_a_local_agent_is_served_at_the_largest_context_its_model_and_machine_allow(tmp_path, monkeypatch):
    from conftest import write_gguf

    from poolhouse.hub.probe import GIB
    from poolhouse.serve import largest, suggest

    path = write_gguf(tmp_path / "agent-Q4_K_M.gguf", {
        "general.architecture": "llama", "llama.block_count": 32, "llama.context_length": 131072,
        "llama.attention.head_count": 32, "llama.attention.head_count_kv": 8,
        "llama.attention.key_length": 128, "llama.attention.value_length": 128})
    os.truncate(path, 4 * GIB)
    machine = MachineMemory(16 * GIB, 8 * GIB, True, 8 * GIB, (), "Darwin")
    monkeypatch.setattr(suggest, "machine_memory", lambda: machine)
    pick = localmodel.Pick(ref=str(path), name="agent")
    got = localmodel.context_for(pick)
    assert got == suggest.largest_fit(path, machine).context
    assert largest.PAD <= got < 131072 and got % largest.PAD == 0
    assert localmodel.Selection().context == 0


def test_no_ceiling_is_set_on_how_hard_an_agent_may_think_or_how_much_it_may_say():
    from poolhouse.workspace import localagent, localeffort, localstart

    assert localeffort.DEFAULT_MAX == localeffort.LEVELS[-1] == "high"
    assert localstart.Ask().max_effort == localagent.Agent(name="a", model="m").max_effort == "high"
    assert localstart.Ask().max_output_tokens is None and localagent.Agent(name="a", model="m").ctx == 0
    assert localeffort.clamp("high", localeffort.DEFAULT_MAX) == "high"
