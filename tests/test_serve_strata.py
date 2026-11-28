"""Serving with the Strata engine: what it is pinned to, how a start is configured, and the lease around its server.

The server in these tests is a plumbing stand-in, marked CANNED on every reply: it runs no model and answers only health,
identity and the drafting report. Nothing is downloaded."""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from poolhouse import delegate_review, harnessing, ownership, serverkeys
from poolhouse.serve import admission, engines, ops, python_runtime
from poolhouse.serve.backend import ServerFailed, ServerSpec
from poolhouse.serve.fitted import fitted
from poolhouse.serve.manager import ServerManager
from poolhouse.serve.serving import Serving
from poolhouse.serve.strata import backend, config, fetch, install, pins, prepare, proof

pytest_plugins = ["serve_node"]

GIB = 1 << 30
CANNED = "[CANNED RESPONSE, NOT A MODEL]"
SERVER = '''"""CANNED plumbing stand-in for Strata's server: no model, every reply marked."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

argv = sys.argv[1:]
config = json.load(open(argv[argv.index("--config") + 1]))
json.dump(argv, open("argv.json", "w"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        public = self.path == "/health"
        if not public and self.headers.get("Authorization") != "Bearer " + config["api_key"]:
            code, body = 401, {}
        else:
            code = 200
            each = int(config["args"][config["args"].index("--max-context") + 1])
            body = {"/health": {"status": "ok"}, "/metrics": {"engine": {"drafting": config["drafting"], "drafting_note": ""}},
                    "/props": {"default_generation_settings": {"n_ctx": each}, "total_slots": 1},
                    "/v1/models": {"data": [{"id": name} for name in (config["model_name"], *config["aliases"])]}}.get(self.path, {})
        data = json.dumps({"canned": "[CANNED RESPONSE, NOT A MODEL]", **body}).encode()
        self.send_response(code)
        self.send_header("X-Canned", "[CANNED RESPONSE, NOT A MODEL]")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer((argv[argv.index("--host") + 1], int(argv[argv.index("--port") + 1])), Handler).serve_forever()
'''


def small_set(folder: Path, key: str = "iq3_s") -> tuple[pins.ModelSet, tuple[Path, ...]]:
    """A set of two tiny files standing for a model's, with their pins."""
    files = []
    for number in (1, 2):
        path = folder / f"thornfield-{number:05d}-of-00002.gguf"
        path.write_bytes(b"CANNED file " * number)
        files.append(path)
    held = tuple(pins.FilePin(p.name, p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()) for p in files)
    return pins.ModelSet(key, "owner/repo", "0" * 40, "Q", True, True, held, 50.3, 62), tuple(files)


class TestPins:
    def test_a_set_is_recognised_only_whole_and_at_its_sizes(self, tmp_path, monkeypatch):
        chosen, files = small_set(tmp_path)
        monkeypatch.setattr(pins, "model_sets", lambda: (chosen,))
        assert pins.recognised(files[0]) == (chosen, files)
        assert pins.recognised(files[1]) is None
        files[1].write_bytes(b"short")
        assert pins.recognised(files[0]) is None

    def test_a_file_with_other_bytes_is_refused_by_its_digest(self, tmp_path):
        chosen, files = small_set(tmp_path)
        pins.check_files(files, chosen.files)
        files[0].write_bytes(files[0].read_bytes().swapcase())
        with pytest.raises(ValueError, match="not the pinned"):
            pins.check_files(files, chosen.files)

    def test_every_pinned_file_and_tensor_has_a_sha256(self):
        held = pins.load()
        digests = [f.sha256 for one in pins.model_sets() for f in one.files] + list(held["mtp"]["tensors"].values())
        digests += [f["sha256"] for f in held["dflash2"]["files"]]
        digests += [held["engine"]["source_sha256"], held["engine"]["source_content_sha256"], held["windows"]["sha256"],
                    held["metal_cpp"]["sha256"]]
        assert digests and all(len(d) == 64 and int(d, 16) >= 0 for d in digests)
        assert len(held["engine"]["commit"]) == 40 and held["engine"]["commit"] in held["engine"]["source_url"]


class TestInstall:
    def archive(self, tmp_path: Path, members: list[tarfile.TarInfo]) -> Path:
        path = tmp_path / "source.tar.gz"
        with tarfile.open(path, "w:gz") as out:
            for member in members:
                out.addfile(member, io.BytesIO(b"x" * member.size) if member.isfile() else None)
        return path

    def member(self, name: str, kind: bytes = tarfile.REGTYPE, target: str = "") -> tarfile.TarInfo:
        info = tarfile.TarInfo(name)
        info.type, info.linkname = kind, target
        info.size = 3 if kind == tarfile.REGTYPE else 0
        return info

    def test_only_the_kept_parts_are_unpacked(self, tmp_path):
        got = self.archive(tmp_path, [self.member("top/engine/a.c"), self.member("top/docs/b.md"), self.member("top/LICENSE")])
        install.extract(got, tmp_path / "out", install.KEPT)
        assert sorted(p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*") if p.is_file()) == [
            "LICENSE", "engine/a.c"]

    @pytest.mark.parametrize("bad", ["top/engine/../../escaped", "top/engine/link"])
    def test_a_name_that_leaves_the_folder_or_an_alias_is_refused(self, tmp_path, bad):
        kind = tarfile.SYMTYPE if bad.endswith("link") else tarfile.REGTYPE
        got = self.archive(tmp_path, [self.member(bad, kind, "/etc/hosts" if kind == tarfile.SYMTYPE else "")])
        with pytest.raises(ServerFailed, match="not a plain file"):
            install.extract(got, tmp_path / "out")
        assert not (tmp_path / "escaped").exists()

    def test_an_installed_engine_that_changed_is_refused(self, tmp_path):
        engine = {"commit": "a" * 40}
        where = install.home_of(engine)
        (where / "source" / "terminal").mkdir(parents=True)
        (where / "source" / "terminal" / "server.py").write_text("one\n")
        (where / "strata").write_text("program\n")
        engine["source_content_sha256"] = install.content_digest(
            [("terminal/server.py", hashlib.sha256(b"one\n").hexdigest())])
        (where / install.RECORD).write_text(json.dumps(
            {"commit": engine["commit"], "binary": "strata", "binary_sha256": hashlib.sha256(b"program\n").hexdigest()}))
        assert install.existing({"engine": engine}).binary == where / "strata"
        cache = where / "source" / "terminal" / "__pycache__"
        cache.mkdir()
        (cache / "server.cpython-313.pyc").write_bytes(b"left by a child interpreter")
        assert install.existing({"engine": engine}) is not None and not cache.exists()
        (where / "source" / "terminal" / "added.py").write_text("two\n")
        with pytest.raises(ServerFailed, match="not the pinned one"):
            install.existing({"engine": engine})


def job_for(tmp_path: Path, model: tuple[pins.ModelSet, tuple[Path, ...]], kind: str, memory: int, context: int = 262144):
    engine = install.Engine(tmp_path / "engine", tmp_path / "engine" / "source", tmp_path / "engine" / "strata")
    return prepare.Job(engine, *model, kind, memory, context)


def made_for(tmp_path: Path, files: tuple[Path, ...], drafter: str = "mtp") -> prepare.Prepared:
    return prepare.Prepared(tmp_path / "data", tmp_path / "data" / "packs" / "set", files[0], files[1], False, drafter, "why")


class TestConfig:
    def test_a_mac_start_drafts_with_mtp_at_the_measured_window_and_262k(self, tmp_path):
        chosen, files = small_set(tmp_path)
        chosen = pins.ModelSet(**{**{f: getattr(chosen, f) for f in chosen.__slots__}, "files": (
            pins.FilePin("a", 54_817_524_224, "0" * 64), pins.FilePin("b", 28_800_138_432, "0" * 64))})
        assert config.fitted_context(chosen, "metal", 128 * GIB) == 262144
        got = config.shape(job_for(tmp_path, (chosen, files), "metal", 128 * GIB), made_for(tmp_path, files))
        args = list(got.args)
        assert args[args.index("--spec") + 1] == "3" and args[args.index("--spec-min-p") + 1] == "0.5"
        assert "--mtp" in args and args[args.index("--max-context") + 1] == "262144"
        assert got.env == {"STRATA_METAL_STREAM": "0"}
        assert any("32,768" in note for note in got.notes)

    def test_a_64_gb_pc_streams_the_kv_cache_at_262k(self, tmp_path):
        chosen, files = small_set(tmp_path)
        got = config.shape(job_for(tmp_path, (chosen, files), "cuda", 64 * GIB), made_for(tmp_path, files, "auto"))
        args = list(got.args)
        assert args[args.index("--kv-resident") + 1] == "32768" and args[args.index("--kv") + 1] == "int8"
        assert "--mmap-experts" not in args and not got.experts_file

    def test_a_pc_short_of_ram_reads_experts_from_the_model_folder(self, tmp_path):
        chosen, files = small_set(tmp_path)
        got = config.shape(job_for(tmp_path, (chosen, files), "cuda", 32 * GIB, 32768), made_for(tmp_path, files))
        assert "--mmap-experts" in got.args and got.experts_file and "--kv-resident" not in got.args

    @pytest.mark.parametrize("kind", ["metal", "cuda"])
    def test_each_slot_is_started_with_its_share_of_the_context(self, tmp_path, kind):
        chosen, files = small_set(tmp_path)
        job = replace(job_for(tmp_path, (chosen, files), kind, 128 * GIB, 2 * 131072), slots=2)
        args = list(config.shape(job, made_for(tmp_path, files)).args)
        assert args[args.index("--max-context") + 1] == "131072"

    def test_engine_options_replace_chosen_ones_and_name_no_file(self):
        spec = ServerSpec(model="m", engine="strata", spec_draft_max=5, extra_args=("--expert-cache", "96"))
        got = backend.overridden(["--spec", "3", "--expert-cache", "64"], spec)
        assert got == ["--spec", "5", "--expert-cache", "96"]
        for extra in (("--native", "/etc/passwd"), ("--mcp-config", "x.json"), ("--expert-cache",), ("--spec", "--host")):
            with pytest.raises(ServerFailed, match="these options"):
                backend.overridden([], ServerSpec(model="m", engine="strata", extra_args=extra))

    @pytest.mark.parametrize(("spec", "want"), [
        (ServerSpec(model="m"), ""), (ServerSpec(model="m", draft="dflash2"), "dflash2"),
        (ServerSpec(model="m", spec_type="mtp"), "mtp"), (ServerSpec(model="m", mtp=False), "off")])
    def test_drafting_is_on_unless_switched_off(self, spec, want):
        assert backend.asked_drafter(spec) == want

    def test_a_llama_draft_head_is_not_a_strata_drafter(self):
        with pytest.raises(ServerFailed, match="its own drafters"):
            backend.asked_drafter(ServerSpec(model="m", draft="/models/head.gguf"))


class TestDrafter:
    def test_a_drafter_from_an_unknown_publisher_is_not_fetched_for_a_start_and_mtp_drafts(self, tmp_path, monkeypatch):
        chosen, files = small_set(tmp_path, "unsloth-ud-iq2_xs")
        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
        made: list[str] = []
        monkeypatch.setattr(prepare, "_mtp", lambda *_: made.append("mtp"))
        monkeypatch.setattr(fetch.hf_cache, "fetch", lambda *_a, **_k: pytest.fail("fetched from an unknown publisher"))
        job = job_for(tmp_path, (chosen, files), "metal", 128 * GIB)
        monkeypatch.setattr(prepare, "data_dir", lambda: tmp_path / "data")
        drafter, why = prepare.drafter_for(job, "", lambda _line: None, pins.load())
        assert (drafter, made) == ("mtp", ["mtp"])
        assert "incoai" in why and "does not know" in why

    def test_asking_for_that_drafter_fails_rather_than_serving_without_it(self, tmp_path, monkeypatch):
        chosen, files = small_set(tmp_path)
        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
        monkeypatch.setattr(prepare, "data_dir", lambda: tmp_path / "data")
        with pytest.raises(fetch.Refused, match="incoai"):
            prepare.drafter_for(job_for(tmp_path, (chosen, files), "metal", 128 * GIB), "dflash2", lambda _line: None, pins.load())

    def test_iq3_s_on_a_mac_drafts_with_mtp_without_asking_for_dflash2(self, tmp_path, monkeypatch):
        chosen, files = small_set(tmp_path, "iq3_s")
        monkeypatch.setattr(prepare, "_mtp", lambda *_: None)
        monkeypatch.setattr(prepare, "_dflash2", lambda *_: pytest.fail("DFlash2 is not IQ3_S's drafter on a Mac"))
        assert prepare.drafter_for(job_for(tmp_path, (chosen, files), "metal", 128 * GIB), "", print, pins.load())[0] == "mtp"


class TestEngine:
    def test_admission_and_the_context_fit_are_the_engines_own(self, tmp_path, monkeypatch):
        chosen, files = small_set(tmp_path)
        monkeypatch.setattr(pins, "model_sets", lambda: (chosen,))
        monkeypatch.setattr(install, "platform_kind", lambda: "metal")
        spec = ServerSpec(model=files[0], engine="strata")
        answered, said = fitted(spec)
        assert answered.context in config.CONTEXTS and "Strata" in said
        assert admission.estimate_bytes(spec) >= config.MAC_OVERHEAD

    def test_an_unknown_engine_is_refused_by_name(self):
        with pytest.raises(ServerFailed, match="no serving engine 'nope'"):
            engines.named("nope")

    def test_status_reports_the_drafter_the_engine_said_it_loaded(self):
        got = ops._drafting("http://127.0.0.1:9", {"backend": "strata", "mtp": "mtp", "mtp_note": "drafting with mtp: why"},
                            None, None)
        assert got.loaded and got.own == "mtp" and "why" in got.note
        none = ops._drafting("http://127.0.0.1:9", {"backend": "strata", "mtp": "", "mtp_note": "no drafting: why"}, None, None)
        assert not none.loaded and "no drafting" in none.note


class TestProof:
    def test_the_filler_is_the_same_for_the_same_seed_and_sized_in_tokens(self):
        assert proof.filler(500, seed=3) == proof.filler(500, seed=3) != proof.filler(500, seed=4)
        assert 500 * proof.CHARS_PER_TOKEN <= len("\n".join(proof.filler(500, seed=3))) <= 500 * proof.CHARS_PER_TOKEN + 200

    def test_a_server_that_runs_no_model_fails_the_proof(self, canned):
        manager, files = canned
        info = manager.lease(ServerSpec(model=files[0], engine="strata", port=18433), timeout=60)
        held = proof.Proof(info.base_url, serverkeys.for_url(info.base_url))
        assert proof.run(held) is False
        assert held.rows and not all(row["passed"] for row in held.rows)


@pytest.fixture
def canned(tmp_path, monkeypatch, serve_leases):
    """A pinned set, an installed engine whose server is the CANNED stand-in, and nothing prepared for real."""
    chosen, files = small_set(tmp_path)
    source = tmp_path / "engine" / "source"
    (source / "terminal" / "serve").mkdir(parents=True)
    (source / "terminal" / "serve" / "server.py").write_text(SERVER, encoding="utf-8")
    engine = install.Engine(tmp_path / "engine", source, tmp_path / "engine" / "strata")
    monkeypatch.setattr(pins, "model_sets", lambda: (chosen,))
    monkeypatch.setattr(install, "platform_kind", lambda: "metal")
    monkeypatch.setattr(install, "ensure", lambda **_: engine)
    monkeypatch.setattr(prepare, "prepare", lambda job, asked, say: made_for(tmp_path, job.files, asked or "mtp"))
    monkeypatch.setattr(config, "estimate_bytes", lambda *_: 1 << 20)
    monkeypatch.setattr(python_runtime, "interpreter", lambda _name: [sys.executable, "-B", "-I"])
    monkeypatch.delenv("POOLHOUSE_SANDBOX_SERVE", raising=False)
    manager = ServerManager(state_file=tmp_path / "servers.json")
    yield manager, files
    manager.stop_all()


def asked(port: int, path: str) -> tuple[int, dict]:
    """``(status, body)`` of a request without a key to the server on loopback ``port``."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path)
        answer = connection.getresponse()
        return answer.status, json.loads(answer.read())
    finally:
        connection.close()


class TestSessions:
    def up(self, canned, monkeypatch, port: int):
        """The CANNED server leased on ``port``, and the harness reading that lease file."""
        manager, files = canned
        info = manager.lease(ServerSpec(model=files[0], engine="strata", port=port, context=131072), timeout=60)
        monkeypatch.setattr(harnessing, "recorded_servers", lambda: ownership.recorded_servers(manager.state_file))
        return manager, files, info

    def test_a_session_takes_the_strata_server_already_up_and_gives_it_no_chat_template(self, canned, tmp_path, monkeypatch):
        manager, files, first = self.up(canned, monkeypatch, 18435)
        template = tmp_path / "late-system.jinja"
        template.write_text("CANNED template\n", encoding="utf-8")
        said: list[str] = []
        taken = harnessing.adopted(str(files[0]), harnessing.Want(port=first.port), template, said.append)
        assert taken is not None, said
        assert (taken.port, taken.serving.engine, taken.serving.slot_context) == (first.port, "strata", 131072)
        again = manager.lease(ServerSpec(model=files[0], chat_template_file=template, **taken.lease()), timeout=60)
        assert (again.port, again.pid) == (first.port, first.pid)

    def test_a_session_asking_for_an_engine_does_not_take_another_ones_server(self, canned, monkeypatch):
        _manager, files, first = self.up(canned, monkeypatch, 18436)
        held = harnessing.recorded_servers()
        monkeypatch.setattr(harnessing, "recorded_servers", lambda: {first.port: {**held[first.port], "backend": "llama.cpp"}})
        said: list[str] = []
        assert harnessing.adopted(str(files[0]), harnessing.Want(port=first.port, engine="strata"), None, said.append) is None
        assert any("not served by the strata engine" in line for line in said)

    def test_a_session_that_starts_strata_asks_the_lease_for_that_engine(self, tmp_path):
        want = harnessing.Want(engine="strata", ctx=131072, slots=2)
        got = harnessing.config_for(str(tmp_path / "set.gguf"), want, lambda _line: None).lease()
        assert (got["engine"], got["context"], got["parallel"]) == ("strata", 131072, 2)
        assert not {"draft", "spec_type", "mtp"} & set(got)

    def test_the_coding_launchers_take_the_engine(self):
        ap = harnessing.parser("claude", "Claude Code", 8080, 1)
        assert ap.parse_args(["--engine", "strata"]).engine == "strata" and ap.parse_args([]).engine == ""

    def test_a_delegates_record_names_the_engine(self):
        got = delegate_review.served("/models/set.gguf", "set", Serving(model="/models/set.gguf", engine="strata"))
        assert got["settings"]["engine"] == "strata"


class TestLease:
    def test_the_server_starts_on_loopback_with_the_issued_key_offline_and_stops_with_its_lease(self, canned):
        manager, files = canned
        info = manager.lease(ServerSpec(model=files[0], engine="strata", port=18431), timeout=60)
        run = install.root() / "runs" / str(info.port)
        argv = json.loads((run / "argv.json").read_text())
        held = json.loads((run / "config.json").read_text())
        assert argv[argv.index("--host") + 1] == "127.0.0.1" and "--offline" in argv
        key = serverkeys.for_url(info.base_url)
        assert key and held["api_key"] == key and all(key not in word for word in argv)
        if os.name == "posix":
            assert (run / "config.json").stat().st_mode & 0o077 == 0
        assert not {"vision", "mcp_servers", "mcpServers", "before_load", "slot_save_path", "cors_origins"} & set(held)
        assert "--vision" not in held["args"]
        status, body = asked(info.port, "/metrics")
        assert status == 401 and body["canned"] == CANNED
        assert asked(info.port, "/health")[1]["canned"] == CANNED
        assert (info.backend, info.mtp) == ("strata", "mtp") and "drafting with mtp" in info.mtp_note
        assert held["model_name"] == "qwen3.8-flash-next-iq3_s" and files[0].name in held["aliases"]
        record = json.loads(manager.state_file.read_text())[str(info.port)]
        assert record["backend"] == "strata" and record["mtp"] == "mtp"
        manager.stop_all()
        with pytest.raises(OSError):
            asked(info.port, "/health")

    def test_a_lease_that_may_escalate_and_brings_a_chat_template_still_starts(self, canned, tmp_path):
        manager, files = canned
        template = tmp_path / "late-system.jinja"
        template.write_text("CANNED template\n", encoding="utf-8")
        info = manager.lease(ServerSpec(model=files[0], engine="strata", port=18434, context=131072, chat_template_file=template),
                             timeout=60, escalate=True)
        record = json.loads(manager.state_file.read_text())[str(info.port)]
        assert record["backend"] == "strata" and not record["chat_template_file"]

    @pytest.mark.parametrize("asking", [{"mmproj": "/etc/hosts"}, {"mmproj": "http://example.invalid/x"},
                                        {"slot_save_path": "/tmp/slots"}, {"embedding": True}])
    def test_images_and_paths_a_spec_names_are_refused_before_anything_starts(self, canned, asking):
        manager, files = canned
        with pytest.raises(ServerFailed, match="not offered"):
            manager.lease(ServerSpec(model=files[0], engine="strata", port=18432, **asking), timeout=30)
        assert not (install.root() / "runs" / "18432" / "argv.json").exists()
