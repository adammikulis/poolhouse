"""A folder of safetensors files pulled under a `Trust` that left a question open is asked about when it is served or
loaded: wherever its files sit (a copy, a hardlink, a renamed or case-swapped folder, a link to the folder, a link loop),
and by every in-process loader that takes a path."""
# ruff: noqa: F811

from __future__ import annotations

import ast
import os
import shutil
from pathlib import Path

import pytest
from model_approval import approve
from net_site import gguf_bytes, safetensors_bytes
from test_library_trust import load, pinned, pins, stand  # noqa: F401

from poolhouse import hub, requests
from poolhouse.hub import admission
from poolhouse.hub.admission import LIBRARY, PERSON
from poolhouse.serve import guarded

# -- a folder of safetensors files, and the in-process loaders that take a path ------------------------
SHARD = "model-00001-of-00002.safetensors"


def folder_repos(name: str = "maker/thing", seed: int = 1):
    return {name: {"config.json": b'{"model_type": "llama"}',
                   SHARD: safetensors_bytes(data=16 + seed),
                   "model-00002-of-00002.safetensors": safetensors_bytes(data=32 + seed)}}


def pull_folder(repo: str = "maker/thing", trust=LIBRARY, dest=None):
    return hub.pull(f"hf:{repo}/{SHARD}", dest, members=False, trust=trust)


def waiting_pins():
    return [p for p in pins().values() if p.unvetted]


# -- serving a folder ----------------------------------------------------------------------------
def test_a_safetensors_folder_pulled_under_library_is_asked_about_once_when_it_is_served(stand, tmp_path):
    stand(folder_repos())
    folder = pull_folder()
    assert folder.is_dir() and len(waiting_pins()) == 2 and requests.list_requests() == []
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker, who Poolhouse does not know") as raised:
        load(folder, tmp_path)
    assert "requests inbox" in str(raised.value)
    (waiting,) = requests.list_requests(state="pending", kind="model_publisher")
    assert waiting.human_only
    assert approve("model_publisher") == 1
    assert load(folder, tmp_path) == "" and waiting_pins() == []
    assert requests.list_requests(state="pending") == []


def test_the_question_about_a_folder_is_not_asked_again_once_a_person_answered(stand, tmp_path, monkeypatch):
    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(guarded.SentinelRefused):
        load(folder, tmp_path)
    approve("model_publisher")
    load(folder, tmp_path)

    def never(*_args, **_fields):
        raise AssertionError("a person was asked again")

    monkeypatch.setattr(guarded.approvals, "approved", never)
    monkeypatch.setattr(guarded.approvals, "ask", never)
    monkeypatch.setattr(admission, "approved", never)
    monkeypatch.setattr(admission, "ask", never)
    assert load(folder, tmp_path) == "" and load(folder, tmp_path) == ""


def test_a_folder_pulled_into_a_plain_directory_under_library_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    folder = pull_folder(dest=tmp_path / "plain")
    assert folder == tmp_path / "plain" and len(waiting_pins()) == 2
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
        load(folder, tmp_path)


def test_a_link_to_a_folder_pulled_under_library_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    link = tmp_path / "linked"
    link.symlink_to(pull_folder(), target_is_directory=True)
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
        load(link, tmp_path)


def test_a_folder_from_a_shipped_publisher_pulled_under_library_is_served_without_a_question(stand, tmp_path):
    stand(folder_repos("unsloth/thing"))
    folder = pull_folder("unsloth/thing")
    assert waiting_pins() == [] and load(folder, tmp_path) == "" and requests.list_requests() == []


def test_a_folder_the_person_surface_pulled_after_an_approval_is_not_asked_about_again(stand, tmp_path):
    stand(folder_repos())
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        pull_folder(trust=PERSON)
    approve("model_publisher")
    folder = pull_folder(trust=PERSON)
    assert waiting_pins() == [] and load(folder, tmp_path) == ""
    assert requests.list_requests(state="pending") == []


def test_an_approval_at_the_first_serve_of_a_folder_answers_a_later_person_pull(stand, tmp_path):
    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(guarded.SentinelRefused):
        load(folder, tmp_path)
    approve("model_publisher")
    load(folder, tmp_path)
    assert pull_folder(trust=PERSON) == folder and requests.list_requests(state="pending") == []


def test_a_folder_of_shards_of_two_commits_asks_about_each_repository_and_commit(stand, tmp_path):
    held = folder_repos()
    server = stand(held)
    first = pull_folder()
    server.moved["maker/thing"] = 1
    held["maker/thing"][SHARD] = safetensors_bytes(data=99)
    second = pull_folder()
    assert first != second
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
        load(first, tmp_path)
    approve("model_publisher")
    assert load(first, tmp_path) == ""
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker"):
        load(second, tmp_path)


def test_a_directory_that_holds_no_pulled_file_is_not_asked_about(tmp_path):
    (tmp_path / "mlx").mkdir()
    (tmp_path / "mlx" / "weights.safetensors").write_bytes(safetensors_bytes())
    assert load(tmp_path / "mlx", tmp_path) == "" and requests.list_requests() == []


# -- the same bytes under another path are asked about ---------------------------------------------------
def pulled_plain(tmp_path):
    """A folder pulled under LIBRARY into a plain directory, and the number of files waiting on an answer."""
    folder = pull_folder(dest=tmp_path / "pulled")
    assert len(waiting_pins()) == 2
    return folder


def asks(path, tmp_path):
    with pytest.raises(guarded.SentinelRefused, match="This model is from maker, who Poolhouse does not know"):
        load(path, tmp_path)


def test_a_copy_of_a_folder_pulled_under_library_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    copy = shutil.copytree(pulled_plain(tmp_path), tmp_path / "copy")
    asks(copy, tmp_path)
    approve("model_publisher")
    assert load(copy, tmp_path) == "" and waiting_pins() == []


def test_a_folder_of_hardlinks_to_a_pulled_folder_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    folder = pulled_plain(tmp_path)
    linked = tmp_path / "hardlinked"
    linked.mkdir()
    for one in folder.iterdir():
        os.link(one, linked / one.name)
    asks(linked, tmp_path)


def test_a_renamed_folder_pulled_under_library_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    folder = pulled_plain(tmp_path)
    moved = tmp_path / "renamed"
    folder.rename(moved)
    asks(moved, tmp_path)


def test_a_case_swapped_path_of_a_pulled_folder_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    folder = pulled_plain(tmp_path)
    swapped = Path(str(folder).replace("pulled", "PULLED"))
    if not swapped.exists():
        swapped = shutil.copytree(folder, tmp_path / "PULLED-copy")
    asks(swapped, tmp_path)


def test_a_folder_holding_a_link_to_a_pulled_folder_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    folder = pulled_plain(tmp_path)
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "inner").symlink_to(folder, target_is_directory=True)
    asks(outer, tmp_path)


def test_a_folder_holding_a_link_to_a_copy_of_a_pulled_folder_is_asked_about(stand, tmp_path):
    stand(folder_repos())
    copy = shutil.copytree(pulled_plain(tmp_path), tmp_path / "copy")
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "inner").symlink_to(copy, target_is_directory=True)
    asks(outer, tmp_path)


def test_a_directory_of_link_loops_around_a_pulled_folder_is_asked_about_and_ends(stand, tmp_path):
    stand(folder_repos())
    copy = shutil.copytree(pulled_plain(tmp_path), tmp_path / "copy")
    (copy / "again").symlink_to(copy, target_is_directory=True)
    (copy / "up").symlink_to(tmp_path, target_is_directory=True)
    asks(copy, tmp_path)


def test_a_directory_of_link_loops_that_holds_no_pulled_file_is_not_asked_about(stand, tmp_path):
    stand(folder_repos())
    pulled_plain(tmp_path)
    loops = tmp_path / "loops"
    loops.mkdir()
    (loops / "again").symlink_to(loops, target_is_directory=True)
    (loops / "weights.safetensors").write_bytes(safetensors_bytes(data=7))
    assert load(loops, tmp_path) == "" and requests.list_requests() == []


def test_a_copy_of_a_vetted_folder_is_not_asked_about(stand, tmp_path):
    stand(folder_repos())
    folder = pulled_plain(tmp_path)
    asks(folder, tmp_path)
    approve("model_publisher")
    load(folder, tmp_path)
    copy = shutil.copytree(folder, tmp_path / "copy")
    assert load(copy, tmp_path) == "" and requests.list_requests(state="pending") == []


def test_a_copy_with_other_bytes_of_the_same_size_is_not_taken_for_a_pulled_file(stand, tmp_path):
    stand(folder_repos())
    folder = pulled_plain(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    for one in folder.iterdir():
        data = bytearray(one.read_bytes())
        data[-1] ^= 1
        (other / one.name).write_bytes(bytes(data))
    assert load(other, tmp_path) == "" and requests.list_requests() == []


def test_a_file_is_read_only_when_its_size_is_that_of_a_pulled_file(stand, tmp_path, monkeypatch):
    stand(folder_repos())
    pulled_plain(tmp_path)
    mine = tmp_path / "mine"
    mine.mkdir()
    for i in range(3):
        (mine / f"w{i}.safetensors").write_bytes(safetensors_bytes(data=100 + i))
    read: list[object] = []
    monkeypatch.setattr(admission, "sha256_file", lambda path: read.append(path) or "")
    assert load(mine, tmp_path) == "" and read == []


# -- the in-process loaders that take a path ----------------------------------------------------------
def test_a_base_folder_pulled_under_library_is_asked_about_before_a_trainer_gets_it(stand):
    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(admission.NotApproved, match="This model is from maker, who Poolhouse does not know"):
        hub.base_folder(str(folder), trust=PERSON)
    assert len(requests.list_requests(state="pending", kind="model_publisher")) == 1
    approve("model_publisher")
    assert hub.base_folder(str(folder), trust=PERSON) == str(folder) and waiting_pins() == []


def test_a_base_folder_that_is_not_a_pulled_one_is_returned_without_a_question(tmp_path):
    (tmp_path / "mine").mkdir()
    assert hub.base_folder(str(tmp_path / "mine"), trust=PERSON) == str(tmp_path / "mine")
    assert requests.list_requests() == []


def test_the_weights_a_speculative_decoder_loads_are_asked_about(stand):
    engine = pytest.importorskip("poolhouse.spec.engine")

    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        engine.weights(folder)
    approve("model_publisher")
    assert engine.weights(folder) == folder


def test_the_base_the_mlx_trainer_loads_is_asked_about(stand):
    from poolhouse.train.recipes import tool_calls_mlx

    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        tool_calls_mlx.local_base(str(folder))
    approve("model_publisher")
    assert tool_calls_mlx.local_base(str(folder)) == folder.resolve()


def test_the_base_a_decider_loads_is_asked_about_before_anything_is_read(stand):
    from poolhouse.decide import pointer

    stand(folder_repos())
    folder = pull_folder()
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        pointer.load_torso(folder, None, "float32", "cpu")


def test_a_file_pulled_under_library_is_asked_about_by_the_shared_check(stand, tmp_path):
    stand({"maker/thing-GGUF": {"thing-Q4_K_M.gguf": gguf_bytes(extra=4096)}})
    got = hub.pull("hf:maker/thing-GGUF/thing-Q4_K_M.gguf", members=False, trust=LIBRARY)
    with pytest.raises(admission.NotApproved, match="This model is from maker"):
        admission.vetted(got)
    approve("model_publisher")
    admission.vetted(got)
    assert pinned(got).unvetted == ""


# -- every in-process loader of a path asks first ---------------------------------------------------------
SRC = Path(__file__).resolve().parent.parent / "src" / "poolhouse"
LOADERS = [("hub/transfer.py", "base_folder", "vetted"), ("spec/engine.py", "weights", "vetted"),
           ("train/recipes/tool_calls_mlx.py", "local_base", "vetted"), ("decide/pointer.py", "load_torso", "vetted"),
           ("train/decider.py", "load_model", "load_torso")]


def called(module: str, function: str) -> set[str]:
    tree = ast.parse((SRC / module).read_text())
    (found,) = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function]
    return {c.func.attr if isinstance(c.func, ast.Attribute) else c.func.id
            for c in ast.walk(found) if isinstance(c, ast.Call) and isinstance(c.func, (ast.Attribute, ast.Name))}


@pytest.mark.parametrize(("module", "function", "calls"), LOADERS)
def test_each_loader_that_takes_a_path_asks_the_shared_check_first(module, function, calls):
    assert calls in called(module, function)


def test_a_loader_that_takes_a_path_calls_the_shared_check_before_it_reads_anything(monkeypatch):
    from poolhouse.decide import pointer

    seen: list[object] = []

    def spy(path):
        seen.append(path)
        raise admission.NotApproved("asked")

    monkeypatch.setattr(admission, "vetted", spy)
    with pytest.raises(admission.NotApproved, match="asked"):
        pointer.load_torso(Path("anywhere"), None, "float32", "cpu")
    assert seen == [Path("anywhere")]
