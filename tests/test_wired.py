"""The wiring limit: the plan for a model and the state it is read in. Models are real GGUF headers over a sparse file, so
the estimator reads real bytes. Nothing changes the limit: that needs administrator rights."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import write_gguf

from poolhouse.serve import wired, wired_state as state

GIB = 1024**3
SRC = str(Path(__file__).resolve().parent.parent / "src")


def dense(layers=32, train=262144) -> dict:
    return {"general.architecture": "llama", "llama.block_count": layers,
            "llama.context_length": train, "llama.embedding_length": 4096,
            "llama.attention.head_count": 32, "llama.attention.head_count_kv": 8,
            "llama.attention.key_length": 128, "llama.attention.value_length": 128}


def model(tmp_path: Path, size: int, name="m-Q4_K_M.gguf") -> Path:
    path = write_gguf(tmp_path / name, dense())
    with path.open("ab") as f:
        f.truncate(size)
    return path


def facts(total_gib, current=0, **more) -> wired.Hooks:
    return wired.Hooks(total=total_gib * GIB, current=current, others=0, **more)


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))


# -- the plan --------------------------------------------------------------------------------
def test_the_plan_counts_weights_cache_and_a_margin_and_proposes_a_whole_number(tmp_path):
    path = model(tmp_path, 16 * GIB)
    plan = wired.plan(str(path), wired.Ask(context=65536), facts(32))
    sizes = dict(plan.counted)
    assert sizes["Weights"] == 16 * GIB
    assert sizes["KV cache"] > 0
    assert plan.need_bytes >= 16 * GIB + sizes["KV cache"]
    assert plan.needed_mb * 1024**2 >= plan.need_bytes + plan.margin_bytes
    assert plan.needed_mb % 256 == 0
    assert plan.fits and plan.proposed_mb == plan.needed_mb


def test_a_longer_context_and_a_wider_cache_need_more(tmp_path):
    path = str(model(tmp_path, 8 * GIB))
    short = wired.plan(path, wired.Ask(context=8192), facts(64))
    long = wired.plan(path, wired.Ask(context=262144), facts(64))
    f16 = wired.plan(path, wired.Ask(context=262144, kv="f16"), facts(64))
    assert short.need_bytes < long.need_bytes < f16.need_bytes


def test_the_rest_of_the_machine_keeps_8_gib_and_a_plan_past_it_does_not_fit(tmp_path):
    path = str(model(tmp_path, 28 * GIB))
    plan = wired.plan(path, wired.Ask(context=262144), facts(32))
    assert plan.max_mb == (32 - 8) * 1024
    assert not plan.fits and plan.proposed_mb == 0
    assert plan.largest_context < 262144
    fitting = [r for r in plan.table if r.fits]
    assert all(r.left_bytes >= 8 * GIB for r in fitting)
    assert any(not r.fits for r in plan.table)


def test_under_12_gib_left_is_allowed_with_a_swap_warning(tmp_path):
    path = str(model(tmp_path, 19 * GIB))
    plan = wired.plan(path, wired.Ask(context=8192), facts(32))
    assert plan.fits and 8 * GIB <= plan.left_bytes < 12 * GIB
    assert "swap" in plan.warn
    roomy = wired.plan(str(model(tmp_path, 8 * GIB, "b.gguf")), wired.Ask(context=8192),
                       facts(32))
    assert roomy.warn == ""


def test_a_limit_already_high_enough_proposes_nothing_to_change(tmp_path):
    path = str(model(tmp_path, 4 * GIB))
    plan = wired.plan(path, wired.Ask(context=8192), facts(32, current=24 * 1024))
    assert plan.enough_now


def test_the_growing_parts_are_host_ram_with_their_caps_and_the_uncapped_one_says_so(tmp_path):
    path = str(model(tmp_path, 4 * GIB))
    plan = wired.plan(path, wired.Ask(cache_ram_mb=4096), facts(64))
    by = {g.label.split(" (")[0]: g for g in plan.grows}
    assert by["prompt cache"].bytes == 4096 * 1024**2 and "4096" in by["prompt cache"].cap
    assert all(g.where == "host RAM" for g in plan.grows)
    nocap = next(g for g in plan.grows if "dynamic" in g.label)
    assert nocap.bytes == 0 and "no cap" in nocap.cap
    assert plan.need_bytes == sum(v for _, v in plan.counted)


def test_the_mtp_head_adds_only_its_own_layer_when_it_lives_in_the_weights(tmp_path, monkeypatch):
    from poolhouse.serve import mtp

    path = str(model(tmp_path, 4 * GIB))
    monkeypatch.setattr(mtp, "embeds_head", lambda _p: True)
    on = wired.plan(path, wired.Ask(mtp=True), facts(64))
    off = wired.plan(path, wired.Ask(mtp=False), facts(64))
    extra = dict(on.counted)
    assert "MTP head weights" not in extra
    assert 0 < extra["MTP head KV cache"] < dict(on.counted)["KV cache"]
    assert on.need_bytes > off.need_bytes
    assert any("shares the main model's weights" in n for n in on.notes)


@pytest.mark.parametrize("bad", [{"kv": "q5_0"}, {"context": -1}, {"context": 100}, {"context": True}])
def test_an_unknown_cache_type_or_context_is_refused(tmp_path, bad):
    with pytest.raises(ValueError):
        wired.plan(str(model(tmp_path, GIB)), wired.Ask(**bad), facts(64))


def test_no_context_asked_is_the_models_whole_trained_window(tmp_path):
    from poolhouse.serve import estimate as est

    path = model(tmp_path, GIB)
    got = wired.plan(str(path), wired.Ask(), facts(64))
    assert got.context == est.read_meta(path)["llama.context_length"]


def test_a_model_that_is_not_installed_is_an_error_not_a_command(tmp_path):
    with pytest.raises(FileNotFoundError):
        wired.plan("x; rm -rf ~ $(id)", None, facts(64))


# -- the state, and what is never done ------------------------------------------------------
@pytest.fixture
def daemon(tmp_path):
    return tmp_path / "LaunchDaemons" / "stack.ml.wired-limit.plist"


def test_the_state_reads_the_kept_value_and_flags_drift(daemon):
    daemon.parent.mkdir()
    assert not state.state(facts(128, current=1, daemon=daemon)).kept
    daemon.write_bytes(plistlib.dumps({"ProgramArguments": ["/usr/sbin/sysctl", "-w",
                                                           "iogpu.wired_limit_mb=65536"]}))
    same = state.state(facts(128, current=65536, daemon=daemon))
    assert same.kept and same.kept_mb == 65536 and not same.drift
    off = state.state(facts(128, current=98304, daemon=daemon))
    assert off.drift and off.live_mb == 98304


@pytest.mark.parametrize("flag", ["--apply", "--persist", "--unpersist", "--reset", "--write", "--print"])
def test_the_command_line_has_no_way_to_change_the_limit(flag, tmp_path):
    """Changing the limit needs administrator rights; nothing in Poolhouse asks for a password, so no flag does."""
    env = {**os.environ, "PYTHONPATH": SRC, "POOLHOUSE_HOME": str(tmp_path / "home")}
    code = ("import sys; from poolhouse.serve import cli; "
            f"sys.exit(cli.COMMANDS.run(['memory', {flag!r}]))")
    got = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=60)
    assert got.returncode != 0 and "unrecognized arguments" in got.stderr


def test_nothing_in_the_source_asks_the_operating_system_for_an_administrator_password():
    root = Path(SRC) / "poolhouse"
    asking = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                    if p.relative_to(root).parts[0] != "guard"   # the guard's lists of what an agent may not run name them
                    and re.search(r"with administrator privileges|\"pkexec\"|\[\"sudo\"", p.read_text(encoding="utf-8")))
    assert asking == []
