"""Model transfer and remote chat release checks."""

from functools import partial
from pathlib import Path


def copy_from_member(TMP, free_port):
    import os
    import threading
    from http.server import ThreadingHTTPServer

    from poolhouse.fleet import Daemon, JobRunner, Models, make_handler
    from poolhouse.fleet.daemon import load_or_create_token
    from poolhouse.fleet.discovery import mint_pool

    key = mint_pool("verify-models", TMP / "models.key").key
    where = TMP / "haver"
    where.mkdir(parents=True, exist_ok=True)
    payload = os.urandom(3 * 1024 * 1024)
    (where / "tiny-test.gguf").write_bytes(payload)

    root = TMP / "haver-daemon"
    files = root / "files"
    files.mkdir(parents=True, exist_ok=True)
    runner = JobRunner(root, files)
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Daemon(
        runner, files, load_or_create_token(root, key), "haver",
        models=Models([where], where), pool_key_path=TMP / "models.key")))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        want = TMP / "wanter"
        want.mkdir(parents=True, exist_ok=True)
        # Several daemons on one box share the discovery port, so the address that
        # discovery would supply is given directly.
        got = Models([want], want)._from_member(
            "tiny-test.gguf", f"http://127.0.0.1:{port}", key, None)
        assert got.path.read_bytes() == payload, "the copy does not match"
        assert got.path.parent == want
    finally:
        runner.shutdown()
        httpd.shutdown()
        httpd.server_close()
    return f"{len(payload) // 1024} KB over the network"



def discard_foreign_partial(TMP, free_port):
    import http.server
    import json as js
    import threading

    from poolhouse.fleet import Models

    store = TMP / "parts"
    store.mkdir(parents=True, exist_ok=True)
    part = store / "m.gguf.part"
    part.write_bytes(b"\x00" * 8192)
    (store.parent / "parts" / "m.gguf.part.from").write_text(
        js.dumps({"url": "http://elsewhere.invalid/other.gguf", "validator": "x"}))
    payload = b"R" * 4096
    asked = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            asked.append(self.headers.get("Range"))
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        got = Models([store], store).ensure(
            "m.gguf", source=f"http://127.0.0.1:{srv.server_address[1]}/m.gguf")
    finally:
        srv.shutdown()
    assert asked == [None], f"asked to resume another file's part: {asked}"
    assert got.path.read_bytes() == payload
    return "the stale part was thrown away"



def discard_stopped_download(TMP, free_port):
    import os
    import time as clock

    from poolhouse.fleet import Models

    store = TMP / "stopped"
    store.mkdir(parents=True, exist_ok=True)
    part = store / "half.gguf.part"
    part.write_bytes(b"x" * 4096)
    stamp = Path(str(part) + ".from")
    stamp.write_text('{"url": "http://x/half.gguf", "validator": "t"}')

    models = Models([store], store)
    assert models.unfinished() == [], "an active download must not be offered"
    old = clock.time() - 7200
    os.utime(part, (old, old))

    listed = models.unfinished()
    assert [r["name"] for r in listed] == ["half.gguf.part"], listed
    assert listed[0]["size"] == 4096
    assert models.discard("half.gguf.part") == ["half.gguf.part"]
    assert not part.exists() and not stamp.exists()
    return "4096 bytes offered, then discarded with what it recorded"



def report_download_progress(TMP, free_port):
    import http.server
    import os
    import threading
    import time as clock

    from poolhouse.fleet.models import CHUNK, Downloads, Models

    payload = os.urandom(3 * CHUNK)

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            for i in range(0, len(payload), 65536):
                self.wfile.write(payload[i:i + 65536])
                self.wfile.flush()
                clock.sleep(0.005)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    store = TMP / "progress"
    store.mkdir(parents=True, exist_ok=True)
    downloads = Downloads(Models([store], store))
    try:
        began = clock.monotonic()
        row = downloads.start(
            "big.gguf", source=f"http://127.0.0.1:{srv.server_address[1]}/big.gguf")
        answered = clock.monotonic() - began
        assert answered < 1.0, f"starting it waited {answered:.1f}s"

        partial = False
        for _ in range(400):
            now = next(g for g in downloads.active() if g.id == row.id)
            if 0 < now.done < now.total:
                partial = True
            if now.state != "getting":
                break
            clock.sleep(0.02)
    finally:
        srv.shutdown()

    assert now.state == "done", now.error
    assert partial, "it only ever reported nothing, then everything"
    assert (store / "big.gguf").read_bytes() == payload
    return f"{len(payload) // 1024 // 1024} MB, counted as it arrived"



def live_catalogue(TMP, free_port):
    """A list written into the code is out of date the day it ships."""
    from poolhouse.fleet.catalogue import SUGGESTED, popular

    got = popular(free_gb=1024.0, ram_gb=1024.0, limit=12)
    assert len(got) >= 5, f"only {len(got)} came back"
    shipped = {s.name for s in SUGGESTED}
    assert not {p.name for p in got} <= shipped, (
        "the list matches the one written into the code, so it is not live")
    for pick in got:
        assert pick.gb > 0, f"{pick.name} has no size"
        assert pick.ref.startswith("hf:") and pick.file.endswith(".gguf")
        assert pick.family, f"{pick.name} has no family"
        assert "mmproj" not in pick.file.lower(), f"{pick.name} is a vision projector"
        assert "mtp" not in pick.file.lower(), f"{pick.name} is a draft head"
    families = sorted({p.family for p in got})
    return f"{len(got)} models across {len(families)}: {', '.join(families)}"



def catalogue_files(TMP, free_port):
    from poolhouse.fleet.catalogue import SUGGESTED
    from poolhouse.hub.remote import listing, members, parse, resolve
    checked = []
    for pick in SUGGESTED:
        ref = resolve(parse(pick.ref), auth='')
        found = members(listing(ref.repo, ref.commit, auth=''), ref)
        assert found, f'{pick.name}: {pick.ref} is absent'
        size = sum((part.size for part in found))
        assert size > 0, f'{pick.name}: {pick.ref} is empty'
        claimed = pick.gb * 2 ** 30
        assert abs(size - claimed) < max(claimed * 0.25, 64 * 2 ** 20), f'{pick.name} is {size / 2 ** 30:.2f} GB, listed as {pick.gb} GB'
        checked.append(pick.name)
    return f"{len(checked)} available: {', '.join(checked)}"



def remote_chat(TMP, free_port):
    """The install with no model server is the common one. If this fails, the
    product does not work for the people it is for."""
    import json as js
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from poolhouse.fleet import Daemon, JobRunner, Serving, make_handler
    from poolhouse.fleet.chat import find, reply_text, stream, targets
    from poolhouse.fleet.daemon import load_or_create_token
    from poolhouse.fleet.discovery import derive_token, mint_pool

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in ("It ", "works"):
                self.wfile.write(
                    f"data: {js.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n"
                    .encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")

    model = ThreadingHTTPServer(("127.0.0.1", free_port()), Model)
    threading.Thread(target=model.serve_forever, daemon=True).start()

    key = mint_pool("verify-chat", TMP / "chat.key").key
    root = TMP / "host-daemon"
    files = root / "files"
    files.mkdir(parents=True, exist_ok=True)
    serving = Serving(root / "serving.json")
    serving.register(model.server_address[1], ["tiny-test.gguf"])
    runner = JobRunner(root, files)
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Daemon(
        runner, files, load_or_create_token(root, key), "host", serving=serving)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        # This machine: no model store, nothing serving, no way to run one.
        beacon = {"name": "host", "base_url": f"http://127.0.0.1:{port}",
                  "is_self": False, "device": {"serving": serving.public()}}
        reachable = targets([beacon], serving=None, token=derive_token(key))
        assert reachable, "a member is serving and this machine cannot see it"
        target = find(reachable, "tiny-test.gguf")
        assert target is not None and not target.local
        said = b"".join(stream(target, {"model": target.model, "stream": True,
                                        "messages": [{"role": "user", "content": "hi"}]}))
    finally:
        runner.shutdown()
        httpd.shutdown()
        httpd.server_close()
        model.shutdown()

    assert reply_text(said) == "It works", reply_text(said)
    return "no model store, nothing serving, still answered"



def remote_speech(TMP, free_port):
    """The engine is a fake: what is verified is that a recording crosses the wire under
    the fleet's own credential and comes back as text with times."""
    import threading
    from http.server import ThreadingHTTPServer

    from poolhouse import speech as package
    from poolhouse.fleet import Daemon, JobRunner, make_handler
    from poolhouse.fleet.daemon import load_or_create_token
    from poolhouse.fleet.remote import Member
    from poolhouse.media import wav
    from poolhouse.speech import ProviderHealth, Registry, Segment, Transcript

    class Ears:
        name = "fake"

        def probe(self):
            return ProviderHealth.ok("fake")

        def start(self):
            return None

        def stop(self):
            return None

        def transcribe(self, audio, *, language=None):
            return Transcript(text="the fleet is up", language=language or "en",
                              duration_s=0.1, model="fake",
                              segments=(Segment("the fleet is up", 0.0, 0.1),))

    registry: Registry = Registry(kind="asr")
    registry.register("fake", Ears)
    was, package.ASR = package.ASR, registry

    root = TMP / "speech-daemon"
    files = root / "files"
    files.mkdir(parents=True, exist_ok=True)
    token = load_or_create_token(root)
    runner = JobRunner(root)
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Daemon(runner, files, token)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        member = Member(f"http://127.0.0.1:{port}", token)
        assert member.speech()["asr"]["auto"] == "fake"
        heard = member.transcribe(wav.encode(b"\x00\x00" * 1600, sample_rate=16000))
        assert heard.text == "the fleet is up"
        assert heard.segments[0].end_s == 0.1
    finally:
        package.ASR = was
        runner.shutdown()
        httpd.shutdown()
        httpd.server_close()
    return "a recording posted to a member came back as text with segment times"



def stored_conversation(TMP, free_port):
    from poolhouse.fleet import Conversations

    where = TMP / "chats"
    first = Conversations(where)
    made = first.start(model="tiny-test.gguf")
    first.append(made.id, "user", "how tall is everest")
    first.append(made.id, "assistant", "8849 metres")

    again = Conversations(where).get(made.id)
    assert [m.content for m in again.messages] == ["how tall is everest",
                                                   "8849 metres"]
    assert again.title == "how tall is everest"
    assert [c.id for c in Conversations(where).search("everest")] == [made.id]
    return "kept and found again by what was said in it"



CHECKS = (
    ('Models', 'a model is copied from another machine rather than downloaded again', False, copy_from_member),
    ('Models', 'a part-file left by a different download is discarded, not resumed', False, discard_foreign_partial),
    ('Models', 'a download that stopped is listed, and can be discarded', False, discard_stopped_download),
    ('Models', 'getting a model reports how far along it is', False, report_download_progress),
    ('Models', 'the model list is what Hugging Face says is popular now', True, live_catalogue),
    ('Models', 'the offline model list names available files at a pinned Hub commit', True, catalogue_files),
    ('Chat', "a machine that can run nothing itself still talks to a member's model", False, remote_chat),
    ('Chat', 'a machine with no speech model sends audio to one that has it', False, remote_speech),
    ('Chat', 'a conversation is still there after a restart', False, stored_conversation),
)


def register(check, tmp: Path, free_port):
    """Register model and chat checks using the release verifier context."""
    for area, claim, network, fn in CHECKS:
        check(area, claim, network=network)(partial(fn, tmp, free_port))
