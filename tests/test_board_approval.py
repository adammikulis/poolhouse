"""Asking from a shell (decided at once by policy), answering with this device's key, adding a phone with a code, a phone's own answer, and the bytes
a second implementation agrees on. Nobody types: a request is approved when policy allows it and refused with the reason when it does not."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from phone_kit import PhoneKeys, add_phone, code_secret, endpoint_of

from poolhouse import caller
from poolhouse.board import approval, grants, phone as board_phone
from poolhouse.board.approval import Approval, Ask
from poolhouse.workspace import qr

pytest_plugins = ["node_kit", "phone_kit"]
VECTORS = Path(__file__).parent / "fixtures" / "phone-approval-vectors.json"
LABELS = ("FLAG", "POOL", "RISK", "WHO", "ASK", "SCOPE", "CANNOT", "WHY", "DENY", "EXPIRES")


def test_the_bytes_and_signature_another_implementation_made_are_the_ones_this_one_builds_and_checks():
    file = json.loads(VECTORS.read_text(encoding="utf-8"))
    key = file["key"]["fingerprint"]
    assert grants.approval_bytes(file["challenge"], "approve", key).decode() == file["approve_bytes"]
    assert grants.approval_bytes(file["challenge"], "deny", key).decode() == file["deny_bytes"]
    for decision in ("block", "approve_repeat"):
        built = grants.approval_bytes(file["challenge"], decision, key)
        assert built.decode() == file[f"{decision}_bytes"], decision
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(file["key"]["public"])).verify(
            bytes.fromhex(file[f"{decision}_signature"]), built, ec.ECDSA(hashes.SHA256()))
    assert set(file["challenge"]["context"]) == {"said", "verdict", "taint", "held", "prompted", "repeat", "pick", "block_ms"}
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(file["key"]["public"]))
    public.verify(bytes.fromhex(file["approve_signature"]), file["approve_bytes"].encode(), ec.ECDSA(hashes.SHA256()))
    assert hashlib.sha256(bytes.fromhex(file["key"]["public"])).hexdigest() == key
    assert "ASK 2: Share the board demo with the board lab, read and write." in file["challenge"]["text"]
    add = file["add"]["body"]
    proof = {"op": "phone_add", "node": file["add"]["node"], "name": add["name"], "key": add["key"], "push": add["p256dh"], "auth": add["auth"], "at_ms": add["at_ms"]}
    assert ("poolhouse/phone-add/v1\n" + grants.canonical(proof)) == file["add"]["proof_bytes"]
    public.verify(bytes.fromhex(file["add"]["proof_signature"]), file["add"]["proof_bytes"].encode(), ec.ECDSA(hashes.SHA256()))


def test_a_request_reads_back_in_words_from_the_bytes_that_are_signed_and_every_line_a_phone_shows_is_in_them():
    file = json.loads(VECTORS.read_text(encoding="utf-8"))
    shown = grants.describe(file["approve_bytes"].encode())
    assert shown.startswith("APPROVE the request ap-0123456789abcdef of claude-1a2b3c on board demo, which reads:")
    assert all(line.strip() in shown for line in file["challenge"]["text"].split("\n")), "every line the phone shows is in the words"
    assert grants.describe(file["deny_bytes"].encode()).startswith("DENY the request")
    assert grants.describe(file["block_bytes"].encode()).startswith("DENY AND BLOCK the request")
    assert grants.describe(file["approve_repeat_bytes"].encode()).startswith("APPROVE AGAIN (it was declined before) the request")


def test_every_state_has_one_sentence_an_agent_can_pass_on():
    said = {s: Approval(None, {"state": s, "reason": "the node said why"}).message() for s in ("pending", *approval.ENDED)}  # type: ignore[arg-type]
    assert len(set(said.values())) == len(said) and all(m.endswith((".", "the node said why")) for m in said.values())
    assert said["expired"] == "The request ended before it was answered; ask again if it is still needed."
    assert said["failed"] == "It was approved, but it could not be carried out: the node said why"
    assert "phone" not in " ".join(said.values()).lower() and "you" not in " ".join(said.values()).lower().split(), "no sentence is for a person who is not there"


def ask_phone(team, lead, *actions: str, **flags) -> Approval:
    """A request raised the way a session raises it and left for a key to answer (what a linked phone's notification carries); nothing answers it here."""
    asks = [Ask(action, resource or "*", "10m", 1) for action, _, resource in (a.partition(":") for a in actions)]
    return approval.request(team.session(lead), approval.wire(asks, **flags))


def test_the_shell_asks_and_has_the_answer_at_once_and_reads_what_became_of_the_request(workspace_node):
    team = workspace_node
    lead = team.member("lead")
    done = team.cli("approval", "request", "add_device", "link_write:demo>lab", "--for", "10m", "--uses", "1", "--note", "adding the Linux box", "--json", who=lead)
    made = json.loads(done.stdout)
    assert done.returncode == 0 and made["state"] == "approved" and made["risk"] == "sensitive" and len(made["grants"]) == 2, done
    for label in LABELS:
        assert re.search(rf"^{label}( \d)?:", made["text"], re.M), f"{label} is missing from what the request says"
    after = team.cli("approval", "status", made["id"], "--json", who=lead)
    assert after.returncode == 0 and json.loads(after.stdout)["state"] == "approved" and len(json.loads(after.stdout)["grants"]) == 2
    listed = team.cli("approval", "list", who=lead)
    assert made["id"] in listed.stdout and "approved" in listed.stdout
    waiting = team.cli("approval", "wait", made["id"], "--timeout", "0.5", "--json", who=lead)
    assert waiting.returncode == 0 and json.loads(waiting.stdout)["state"] == "approved", "waiting for what is answered already is no wait"
    assert team.cli("approval", "pending", "--json", who=lead).stdout.strip() == "[]", "nothing waits"
    assert sorted(g.action for g in grants.listing(team.session(lead)).grants if g.status == "live") == ["add_device", "link_write"]
    refused = team.cli("approval", "request", "grant_issue", who=lead)
    assert refused.returncode == 3 and "grant_issue is a floor" in refused.stderr + refused.stdout
    assert team.cli("approval", "request", "sync_now", "--wait", "0", who=lead).returncode == 2, "there is no wait to ask for: it is answered"


def test_a_request_raised_for_a_key_to_answer_waits_and_a_phone_answers_it_with_the_number_the_asker_was_shown(phone_node):
    team = phone_node
    lead = team.member("lead")
    phone = add_phone(team)
    made = ask_phone(team, lead, "add_device", "link_write:demo>lab", note="adding the Linux box")
    assert made.state == approval.PENDING and made.risk == "sensitive" and re.fullmatch(r"\d\d", made.code)
    waiting = team.cli("approval", "wait", made.id, "--timeout", "0.5", "--json", who=lead)
    assert waiting.returncode == 0 and json.loads(waiting.stdout)["state"] == "pending", "waiting is not failing"
    endpoint = endpoint_of(team)
    seen = endpoint.ok("get", {"id": made.id}, token=phone.token)
    assert endpoint.ok("decide", phone.decision(seen, "approve", pick=made.code))["state"] == "approved"
    after = team.cli("approval", "status", made.id, "--json", who=lead)
    assert after.returncode == 0 and json.loads(after.stdout)["state"] == "approved" and len(json.loads(after.stdout)["grants"]) == 2


def test_on_a_node_whose_only_key_is_a_phones_the_shell_enrols_this_devices_key_and_answers_at_once_and_the_phone_still_can(phone_node):
    team = phone_node
    lead = team.member("lead")
    phone = add_phone(team)
    assert len(grants.listing(team.session(lead)).persons) == 1, "the phone's key is the node's only key"
    done = team.cli("approval", "request", "sync_now", "--json", who=lead)
    assert done.returncode == 0 and json.loads(done.stdout)["state"] == "approved", done.stdout
    assert len(grants.listing(team.session(lead)).persons) == 2, "this device's own key was enrolled beside the phone's, and no phone had to tap"
    assert team.cli("approval", "pending", "--json", who=lead).stdout.strip() == "[]", "nothing waits for the phone"
    made = ask_phone(team, lead, "add_device", "link_write:demo>lab", note="adding the Linux box")
    assert made.state == approval.PENDING, "a request raised for a key to answer still waits for the phone"
    endpoint = endpoint_of(team)
    seen = endpoint.ok("get", {"id": made.id}, token=phone.token)
    assert endpoint.ok("decide", phone.decision(seen, "approve", pick=made.code))["state"] == "approved"
    assert {"sync_now", "add_device", "link_write"} <= {g.action for g in grants.listing(team.session(lead)).grants if g.status == "live"}


def test_a_deny_from_the_phone_is_a_failed_exit_the_agent_can_relay(phone_node):
    team = phone_node
    lead = team.member("lead")
    phone = add_phone(team)
    made = ask_phone(team, lead, "sync_now")
    endpoint = endpoint_of(team)
    endpoint.ok("decide", phone.decision(endpoint.ok("get", {"id": made.id}, token=phone.token), "deny"))
    done = team.cli("approval", "wait", made.id, "--timeout", "2", "--json", who=lead)
    assert done.returncode == 3 and json.loads(done.stdout)["message"] == "It was declined; nothing changed."


def test_the_device_lifts_the_block_a_phone_put_on_a_session_with_no_terminal_and_an_agent_cannot(phone_node):
    team = phone_node
    lead = team.member("lead")
    phone = add_phone(team)
    made = ask_phone(team, lead, "sync_now")
    endpoint = endpoint_of(team)
    endpoint.ok("decide", phone.decision(endpoint.ok("get", {"id": made.id}, token=phone.token), "block"))
    who = f"{team.board}/{lead.name}"
    blocks = team.cli("approval", "blocks", "--json")
    assert blocks.returncode == 0 and who in json.loads(blocks.stdout), (blocks.stdout, blocks.stderr)
    marked = team.cli("approval", "lift", who, env={"CLAUDECODE": "1"})
    assert marked.returncode != 0 and "refused to an agent" in marked.stdout + marked.stderr
    assert who in json.loads(team.cli("approval", "blocks", "--json").stdout), "the refused lift changed nothing"
    lifted = team.cli("approval", "lift", who)
    assert lifted.returncode == 0 and "The block was lifted" in lifted.stdout + lifted.stderr, (lifted.stdout, lifted.stderr)
    assert ask_phone(team, lead, "sync_now").state == approval.PENDING, "the session may ask again"


def test_the_device_answers_a_request_with_its_own_key_at_once_and_what_waits_is_read_with_that_key(workspace_node):
    team = workspace_node
    lead = team.member("lead")
    team.keep_person_key(lead)
    made = ask_phone(team, lead, "sync_now")
    assert json.loads(team.cli("approval", "pending", "--json", who=team.member("other")).stdout) == [], "another session reads none of what lead asked"
    assert [a["id"] for a in json.loads(team.cli("approval", "pending", "--json", who=lead).stdout)] == [made.id], "a session reads its own"
    everything = team.cli("approval", "pending", "--all", "--json", who=lead)
    assert everything.returncode == 0 and [a["id"] for a in json.loads(everything.stdout)] == [made.id], "the device's key signs to read every request that waits"
    done = team.cli("approval", "decide", made.id, "approve", "--json")
    assert done.returncode == 0 and json.loads(done.stdout) == {"id": made.id, "state": "approved"}, (done.stdout, done.stderr)
    assert json.loads(team.cli("approval", "status", made.id, "--json", who=lead).stdout)["state"] == "approved"
    assert [g.action for g in grants.listing(team.session(lead)).grants if g.status == "live"] == ["sync_now"]


def test_a_request_that_is_not_routine_is_answered_with_the_number_the_asker_was_shown_and_a_wrong_number_is_a_decline(workspace_node):
    team = workspace_node
    lead = team.member("lead")
    team.keep_person_key(lead)
    first = ask_phone(team, lead, "add_device", "link_read:a>b")
    seen = json.loads(team.cli("approval", "pending", "--all", "--json", who=lead).stdout)
    options = next(a for a in seen if a["id"] == first.id)["challenge"]["context"]["pick"]
    assert first.code in options and len(options) == 3, "the node offers three numbers, the asker's among them"
    unlisted = team.cli("approval", "decide", first.id, "approve", "--pick", "00" if "00" not in options else "01")
    assert unlisted.returncode != 0 and "--pick" in unlisted.stdout + unlisted.stderr, "a number that is not offered is refused before anything is signed"
    unpicked = team.cli("approval", "decide", first.id, "approve")
    assert unpicked.returncode != 0 and "--pick" in unpicked.stdout + unpicked.stderr
    assert first.refresh() == approval.PENDING
    other = next(n for n in options if n != first.code)
    wrong = team.cli("approval", "decide", first.id, "approve", "--pick", other)
    assert wrong.returncode != 0 and "not the one the agent showed" in wrong.stdout + wrong.stderr, (wrong.stdout, wrong.stderr)
    assert first.refresh() == "denied", "a wrong number is a decline"
    again = ask_phone(team, lead, "add_device")
    assert "declined 1 time(s) before" in again.text, "the scope that was declined says so"
    done = team.cli("approval", "decide", again.id, "approve", "--pick", again.code)
    assert done.returncode == 0 and again.refresh() == "approved", (done.stdout, done.stderr)


def test_an_agent_cannot_answer_a_request_read_what_waits_or_add_or_remove_a_phone_and_the_refusal_says_why_at_once(phone_node):
    team = phone_node
    lead = team.member("lead")
    made = ask_phone(team, lead, "sync_now")
    for argv in (["approval", "decide", made.id, "approve"], ["approval", "pending", "--all"], ["approval", "blocks"], ["phone", "add"], ["phone", "remove", "Pixel"]):
        marked = team.cli(*argv, who=lead, env={"CLAUDECODE": "1"})
        out = marked.stdout + marked.stderr
        assert marked.returncode != 0 and "refused to an agent" in out and "terminal outside the agent" in out, (argv, out)
    assert made.refresh() == approval.PENDING
    assert not (team.state / "phone_offer.json").exists(), "an agent's `phone add` left no code"
    refused = [r for r in caller.audit_rows() if r["event"] == "refused"]
    assert {r["action"] for r in refused} >= {"answering an approval", "reading every request that waits", "managing approval blocks", "adding a phone", "removing a phone"}
    assert all("agent CLAUDECODE" in r["who"] and r["why"] for r in refused), "each refusal is on the log with who asked and why"


def test_the_first_phone_is_added_from_a_code_drawn_at_once_and_never_an_image_file(phone_node):
    team = phone_node
    lead = team.member("lead")
    image = team.state.parent / "first-phone.svg"
    done = team.cli("phone", "add", "--image", str(image), "--wait", "5", who=lead)
    assert done.returncode != 0 and "no image file" in done.stdout + done.stderr, (done.stdout, done.stderr)
    assert not image.exists() and not (team.state / "phone_offer.json").exists(), "nothing was written that could be read as a code"
    done = team.cli("phone", "add", "--wait", "2", who=lead)
    assert done.returncode == 3 and "\x1b[30;47m" in done.stdout + done.stderr and "No phone scanned the code in time" in done.stdout + done.stderr, (done.stdout, done.stderr)
    phone = add_phone(team)
    listing = team.cli("phone", "list", who=lead)
    words = " ".join(phone.fingerprint[i:i + 4] for i in range(0, 24, 4))
    assert "Pixel" in listing.stdout and words in listing.stdout, listing.stdout


def test_a_further_phone_is_added_by_an_image_and_waits_for_a_phone_you_hold_to_approve_it(phone_node):
    team = phone_node
    lead = team.member("lead")
    first = add_phone(team)
    image = team.state.parent / "phone-add.svg"
    result: dict[str, object] = {}

    def person_runs_the_command():
        done = team.cli("phone", "add", "--image", str(image), "--wait", "60", who=lead, timeout=90)
        result["run"] = (done.returncode, done.stdout + done.stderr, "")

    thread = threading.Thread(target=person_runs_the_command)
    thread.start()
    end = time.monotonic() + 30
    while not (image.exists() and "</svg>" in image.read_text(encoding="utf-8")) and time.monotonic() < end:
        time.sleep(0.1)
    link = re.search(r"<desc>(poolhouse://add-phone\?data=[A-Za-z0-9_-]+)</desc>", image.read_text(encoding="utf-8"))
    assert link, "the image holds the link it encodes"
    assert (image.stat().st_mode & 0o077) == 0, "the code is the person's alone"
    data = json.loads(base64.urlsafe_b64decode(parse_qs(urlparse(link[1]).query)["data"][0] + "=="))
    assert data["v"] == 1 and data["pin"] == hashlib.sha256((team.state / "phone.cert").read_bytes()).hexdigest()
    assert data["policy"] == "secure" and data["expires"] > time.time() and len(data["secret"]) == 43
    tablet = PhoneKeys("Tablet")
    endpoint = endpoint_of(team)
    reply = endpoint.ok("add", tablet.add_body(team.client.call("hello")["device"], data["secret"]))
    assert reply["state"] == "pending" and reply["words"] == " ".join(tablet.fingerprint[i:i + 4] for i in range(0, 24, 4))
    thread.join(timeout=60)
    code, out, err = result["run"]  # type: ignore[misc]
    assert code == 0 and 'The phone "Tablet" (key ' + reply["words"] + ") scanned the code and waits for one of your phones to approve it." in out, (out, err)
    assert "\x1b[30;47m" in out, "the code was drawn in the terminal"
    seen = endpoint.ok("get", {"id": reply["approval"]}, token=first.token)
    assert "key " + reply["words"] in seen["text"] and "made by the session" in seen["text"], "the node cannot tell the person's command from a session, so the text names who asked for the code"
    assert endpoint.ok("decide", first.decision(seen, "approve", pick=reply["number"]))["state"] == "approved"
    assert sorted(p["name"] for p in board_phone.phones(team.session(lead)) if p["state"] == "active") == ["Pixel", "Tablet"]


def test_a_code_that_was_made_for_nobody_else_adds_nothing_twice_and_a_wrong_one_adds_nothing(phone_node):
    team = phone_node
    endpoint = endpoint_of(team)
    secret = code_secret(team, None)
    device = team.client.call("hello")["device"]
    wrong = endpoint.request("add", PhoneKeys("Thief").add_body(device, "not-the-code"))[1]
    assert wrong["error"]["message"] == "That code was not made for this device."
    assert team.client.call("person_keys")["keys"] == []
    ok = endpoint.ok("add", PhoneKeys("Pixel").add_body(device, secret))
    assert ok["state"] == "active"
    again = endpoint.request("add", PhoneKeys("Thief").add_body(device, secret))[1]
    assert again["error"]["message"] == "No phone is being added right now: ask for a new code."


def test_a_qr_code_has_a_quiet_zone_and_its_image_holds_the_link():
    link = "poolhouse://add-phone?data=" + "A" * 400
    grid = qr.matrix(link)
    assert len(grid) == len(grid[0]) and not any(grid[0]) and not any(row[0] for row in grid), "the quiet zone is empty"
    assert len(grid) >= 4 * 2 + 21 and any(any(row) for row in grid)
    assert f"<desc>{link}</desc>" in qr.svg(link)
    shown = qr.terminal(link).split("\n")
    assert len(shown) == (len(grid) + 1) // 2 and all(line.startswith("\x1b[30;47m") and line.endswith("\x1b[0m") for line in shown)


def test_a_phone_is_found_by_name_id_or_key_and_never_by_a_partial_match():
    held = [{"id": "ph-1", "name": "Pixel", "fingerprint": "ab" * 32, "state": "active"}, {"id": "ph-2", "name": "Pixel", "fingerprint": "cd" * 32, "state": "removed"},
            {"id": "ph-3", "name": "Tablet", "fingerprint": "ef" * 32, "state": "active"}]
    assert board_phone.find(held, "Pixel")["id"] == "ph-1", "a removed phone of the same name is not a second match"
    assert board_phone.find(held, "ph-3")["name"] == "Tablet" and board_phone.find(held, "ef" * 32)["id"] == "ph-3"
    for bad in ("Pix", "Phone"):
        with pytest.raises(ValueError, match="phone is called"):
            board_phone.find(held, bad)
    with pytest.raises(ValueError, match="more than one"):
        board_phone.find([*held, {"id": "ph-4", "name": "Pixel", "fingerprint": "01" * 32, "state": "active"}], "Pixel")
