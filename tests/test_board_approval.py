"""Asking from a shell, answering at a terminal, adding a phone with a code, and the bytes a second implementation agrees on."""

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
from node_kit import PASSPHRASE, TYPED
from phone_kit import PhoneKeys, add_phone, endpoint_of

from poolhouse.board import approval, grants, phone as board_phone
from poolhouse.board.approval import Approval
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


def test_what_the_person_is_asked_at_a_terminal_is_the_request_itself():
    file = json.loads(VECTORS.read_text(encoding="utf-8"))
    shown = grants.describe(file["approve_bytes"].encode())
    assert shown.startswith("APPROVE the request ap-0123456789abcdef of claude-1a2b3c on board demo, which reads:")
    assert all(line.strip() in shown for line in file["challenge"]["text"].split("\n")), "every line the phone shows is put to the person"
    assert grants.describe(file["deny_bytes"].encode()).startswith("DENY the request")
    assert grants.describe(file["block_bytes"].encode()).startswith("DENY AND BLOCK the request")
    assert grants.describe(file["approve_repeat_bytes"].encode()).startswith("APPROVE AGAIN (it was declined before) the request")


def test_every_state_has_one_sentence_an_agent_can_pass_on():
    said = {s: Approval(None, {"state": s, "reason": "the node said why"}).message() for s in ("pending", *approval.ENDED)}  # type: ignore[arg-type]
    assert len(set(said.values())) == len(said) and all(m.endswith((".", "the node said why")) for m in said.values())
    assert said["expired"] == "The request ended before you answered; ask again if it is still needed."
    assert said["failed"] == "You approved it, but it could not be carried out: the node said why"


def test_the_shell_asks_waits_and_reads_what_became_of_the_request(phone_node):
    team = phone_node
    lead = team.member("lead")
    phone = add_phone(team)
    done = team.cli("approval", "request", "add_device", "link_write:demo>lab", "--for", "10m", "--uses", "1", "--note", "adding the Linux box", "--json", who=lead)
    made = json.loads(done.stdout)
    assert done.returncode == 0 and made["state"] == "pending" and made["risk"] == "sensitive", done
    for label in LABELS:
        assert re.search(rf"^{label}( \d)?:", made["text"], re.M), f"{label} is missing from what the person reads"
    waiting = team.cli("approval", "wait", made["id"], "--timeout", "0.5", "--json", who=lead)
    assert waiting.returncode == 0 and json.loads(waiting.stdout)["state"] == "pending", "waiting is not failing"
    endpoint = endpoint_of(team)
    seen = endpoint.ok("get", {"id": made["id"]}, token=phone.token)
    assert re.fullmatch(r"\d\d", made["code"]), "a request that is not routine prints the number to show the person"
    assert endpoint.ok("decide", phone.decision(seen, "approve", pick=made["code"]))["state"] == "approved"
    after = team.cli("approval", "status", made["id"], "--json", who=lead)
    assert after.returncode == 0 and json.loads(after.stdout)["state"] == "approved" and len(json.loads(after.stdout)["grants"]) == 2
    listed = team.cli("approval", "list", who=lead)
    assert made["id"] in listed.stdout and "approved" in listed.stdout
    again = team.cli("approval", "request", "sync_now", "--wait", "0", who=lead)
    assert again.returncode == 0 and "pending" in again.stdout and "what the person reads" in again.stdout
    refused = team.cli("approval", "request", "grant_issue", who=lead)
    assert refused.returncode == 3 and "grant_issue is a floor" in refused.stderr + refused.stdout


def test_a_deny_from_the_phone_is_a_failed_exit_the_agent_can_relay(phone_node):
    team = phone_node
    lead = team.member("lead")
    phone = add_phone(team)
    made = json.loads(team.cli("approval", "request", "sync_now", "--json", who=lead).stdout)
    endpoint = endpoint_of(team)
    endpoint.ok("decide", phone.decision(endpoint.ok("get", {"id": made["id"]}, token=phone.token), "deny"))
    done = team.cli("approval", "wait", made["id"], "--timeout", "2", "--json", who=lead)
    assert done.returncode == 3 and json.loads(done.stdout)["message"] == "You said no; nothing changed."


def test_a_person_at_a_terminal_answers_with_the_key_the_node_holds_once_they_type_yes(phone_node):
    team = phone_node
    lead = team.member("lead")
    team.keep_person_key(lead)
    made = json.loads(team.cli("approval", "request", "sync_now", "--json", who=lead).stdout)
    assert json.loads(team.cli("approval", "pending", "--json", who=team.member("other")).stdout) == [], "another session reads none of what lead asked"
    assert [a["id"] for a in json.loads(team.cli("approval", "pending", "--json", who=lead).stdout)] == [made["id"]], "a session reads its own"
    code, out, err = team.terminal("approval", "decide", made["id"], "approve", who=lead, typed={"Type yes": "no", "signing key passphrase: ": PASSPHRASE})
    assert code != 0 and "READ every request that waits for you" in out, "a person's key signs to read what waits, and a no ends it there: " + out + err
    assert json.loads(team.cli("approval", "status", made["id"], "--json", who=lead).stdout)["state"] == "pending"
    code, out, err = team.terminal("approval", "decide", made["id"], "approve", who=lead, typed=TYPED)
    assert code == 0 and "approved" in out and "APPROVE the request" in out and "WHO:" in out, (out, err)
    assert json.loads(team.cli("approval", "status", made["id"], "--json", who=lead).stdout)["state"] == "approved"


def test_a_person_at_a_terminal_chooses_the_number_after_the_yes_and_types_again_for_a_scope_declined_before(phone_node):
    team = phone_node
    lead = team.member("lead")
    team.keep_person_key(lead)
    first = json.loads(team.cli("approval", "request", "add_device", "link_read:a>b", "--json", who=lead).stdout)
    code, out, err = team.terminal("approval", "pending", "--all", "--json", who=lead, typed=TYPED)
    assert code == 0, (out, err)
    options = next(a for a in json.loads(next(line for line in out.splitlines() if line.startswith("["))) if a["id"] == first["id"])["challenge"]["context"]["pick"]
    assert first["code"] in options and len(options) == 3, "the phone offers three numbers, the agent's among them"
    other = next(n for n in options if n != first["code"])
    code, out, err = team.terminal("approval", "decide", first["id"], "approve", who=lead, typed={**TYPED, "Which number": other})
    assert code != 0 and "Which number did your agent show you?" in out and "not the one the agent showed" in out + err, (out, err)
    assert json.loads(team.cli("approval", "status", first["id"], "--json", who=lead).stdout)["state"] == "denied", "a wrong number is a decline"
    again = json.loads(team.cli("approval", "request", "add_device", "--json", who=lead).stdout)
    assert "declined 1 time(s) before" in again["text"], "the scope that was declined says so"
    code, out, err = team.terminal("approval", "decide", again["id"], "approve", who=lead, typed={"Type again": "no", **TYPED, "Which number": again["code"]})
    assert code != 0 and "Type again to approve it anyway" in out, (out, err)
    assert json.loads(team.cli("approval", "status", again["id"], "--json", who=lead).stdout)["state"] == "pending", "without the extra word nothing was signed"
    code, out, err = team.terminal("approval", "decide", again["id"], "approve", who=lead, typed={"Type again": "again", **TYPED, "Which number": again["code"]})
    assert code == 0 and "approved" in out, (out, err)
    assert "APPROVE AGAIN" in out, "the person is told what is being signed"


def test_every_held_request_says_it_is_held_and_waiting_does_not_pretend_otherwise():
    assert approval.SAYS["held"].startswith("Your requests are being held") and approval.HELD == "held"
    held = Approval(None, {"state": "held", "reason": ""})  # type: ignore[arg-type]
    assert held.message() == approval.SAYS["held"] and not held.deduplicated and held.urgency == "urgent" and held.code == ""


def test_an_agent_or_a_process_with_no_terminal_cannot_answer_or_add_a_phone(phone_node):
    team = phone_node
    lead = team.member("lead")
    team.keep_person_key(lead)
    made = json.loads(team.cli("approval", "request", "sync_now", "--json", who=lead).stdout)
    for argv in (["approval", "decide", made["id"], "approve"], ["phone", "add"], ["phone", "remove", "Pixel"]):
        no_terminal = team.cli(*argv, who=lead)
        assert no_terminal.returncode != 0 and "terminal" in no_terminal.stderr + no_terminal.stdout, argv
        marked, out, err = team.terminal(*argv, who=lead, env={"CLAUDECODE": "1"})
        assert marked != 0 and "agent" in out + err, (argv, out, err)
    assert json.loads(team.cli("approval", "status", made["id"], "--json", who=lead).stdout)["state"] == "pending"
    assert not (team.state / board_phone.OFFER_FILE).exists(), "an agent's `phone add` left no code"


def test_the_first_phone_is_added_from_a_code_drawn_in_the_terminal_and_never_an_image_file(phone_node):
    team = phone_node
    lead = team.member("lead")
    image = team.state.parent / "first-phone.svg"
    code, out, err = team.terminal("phone", "add", "--image", str(image), "--wait", "5", who=lead)
    assert code != 0 and "no image file" in out + err, (out, err)
    assert not image.exists() and not (team.state / board_phone.OFFER_FILE).exists(), "nothing was written that could be read as a code"
    code, out, err = team.terminal("phone", "add", "--wait", "2", who=lead)
    assert code == 3 and "\x1b[30;47m" in out and "No phone scanned the code in time" in out, (out, err)
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
        result["run"] = team.terminal("phone", "add", "--image", str(image), "--wait", "60", who=lead, timeout=90)

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
    assert "key " + reply["words"] in seen["text"] and "made by" not in seen["text"], "a code a person made at the computer carries no warning"
    assert endpoint.ok("decide", first.decision(seen, "approve", pick=reply["number"]))["state"] == "approved"
    assert sorted(p["name"] for p in board_phone.phones(team.session(lead)) if p["state"] == "active") == ["Pixel", "Tablet"]


def test_a_code_that_was_made_for_nobody_else_adds_nothing_twice_and_a_wrong_one_adds_nothing(phone_node):
    team = phone_node
    endpoint = endpoint_of(team)
    secret = board_phone.expect_phone(team.state)
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
