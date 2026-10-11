//! An agent asks, a phone's key answers: the request's text, what a decision is checked against, and what an approval does. The phone
//! is a P-256 key made in the test and the node is the real one; a decision reaches it the way the HTTPS endpoint passes it.

mod kit;

use kit::grants::{person, Person};
use kit::phone::{ask, FakePhone, Rig};
use kit::*;
use poolhouse_node::approvals::challenge;
use poolhouse_node::fsutil::{hex, sha256_hex};
use poolhouse_node::grants::{check, Scope};
use poolhouse_node::identity::Holder;
use serde_json::{json, Value};

fn holder(name: &str) -> Holder {
    Holder { board: "demo".into(), name: name.into() }
}

fn live(rig: &Rig, name: &str, action: &str, resource: &str) -> bool {
    check(&mut rig.node.lock().unwrap(), &holder(name), action, &Scope::on(resource)).unwrap()
}

/// The sentence a refusal carries, without the code the local API puts in front of it.
fn message(reply: &Value) -> String {
    let text = reply["error"]["message"].as_str().unwrap_or("");
    for code in ["denied: ", "invalid: ", "quota: "] {
        if let Some(rest) = text.strip_prefix(code) {
            return rest.to_string();
        }
    }
    text.to_string()
}

/// A rig with one phone added and one agent session, and the agent's request for ``asks``: the phone, the session's name and token, the request.
fn asked(asks: Value) -> (Rig, FakePhone, String, String, Value) {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    let (name, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, asks, json!({"note": "adding the Linux box", "task": ""})));
    (rig, phone, name, token, made)
}

#[test]
fn an_approved_request_writes_grants_that_count_and_a_second_decision_is_refused() {
    let (rig, phone, name, token, made) = asked(json!([ask("add_device", "*"), ask("link_write", "demo>lab")]));
    assert_eq!(made["state"], "pending");
    assert!(!live(&rig, &name, "add_device", "*"), "nothing is granted before a person decides");
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let (status, reply) = rig.phone("POST", "decide", "", phone.decision(&seen, "approve"));
    assert_eq!((status, &reply["ok"]), (200, &json!(true)), "{reply}");
    assert_eq!(reply["result"]["state"], "approved");
    assert!(live(&rig, &name, "add_device", "*") && live(&rig, &name, "link_write", "demo>lab"), "two asks, one tap, two grants");
    assert!(!live(&rig, &name, "add_device", "*"), "a grant of one use is spent by its use");
    let status = ok(rig.call("approval_status", "demo", &token, json!({"id": made["id"]})));
    assert_eq!((status["state"].as_str(), status["grants"].as_array().unwrap().len()), (Some("approved"), 2));
    let again = rig.phone("POST", "decide", "", phone.decision(&seen, "approve")).1;
    assert_eq!(message(&again), "That request was already approved, so it cannot be decided again.", "{again}");
    let listing = ok(rig.call("grants", "demo", &token, json!({})));
    let gids: Vec<&str> = listing["grants"].as_array().unwrap().iter().filter(|g| g["carried_by"] == "node").map(|g| g["status"].as_str().unwrap()).collect();
    assert_eq!(gids.len(), 2, "{listing}");
}

#[test]
fn a_deny_is_signed_and_changes_nothing_but_what_the_agent_is_told() {
    let (rig, phone, name, token, made) = asked(json!([ask("add_device", "*")]));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let mut forged = phone.decision(&seen, "deny");
    forged["sig"] = json!("30".to_string() + &"00".repeat(40));
    assert_eq!(message(&rig.phone("POST", "decide", "", forged).1), "That signature does not match this request, so nothing changed.");
    let reply = rig.phone("POST", "decide", "", phone.decision(&seen, "deny")).1;
    assert_eq!(reply["result"]["state"], "denied", "{reply}");
    assert!(!live(&rig, &name, "add_device", "*"));
    assert_eq!(ok(rig.call("approval_status", "demo", &token, json!({"id": made["id"]})))["state"], "denied");
}

#[test]
fn a_signature_for_other_text_another_node_or_the_other_decision_is_refused() {
    let (rig, phone, name, _, made) = asked(json!([ask("add_device", "*")]));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let sign = |view: &Value, decision: &str| json!({"id": view["id"], "decision": decision, "key": phone.fingerprint(), "sig": phone.sign(&challenge::bytes(&view["challenge"], decision, &phone.fingerprint()))});

    let mut edited = seen.clone();
    edited["challenge"]["text"] = json!(seen["text"].as_str().unwrap().replace("once", "twenty times"));
    assert_eq!(message(&rig.phone("POST", "decide", "", sign(&edited, "approve")).1), "That signature does not match this request, so nothing was approved.", "text the node did not write");

    let mut elsewhere = seen.clone();
    elsewhere["challenge"]["node"] = json!(sha256_hex(b"another node"));
    assert_eq!(message(&rig.phone("POST", "decide", "", sign(&elsewhere, "approve")).1), "That signature does not match this request, so nothing was approved.", "a challenge for another node");

    let mut other_ask = seen.clone();
    other_ask["challenge"]["action"]["grants"][0]["resource"] = json!("*lab");
    assert_eq!(message(&rig.phone("POST", "decide", "", sign(&other_ask, "approve")).1), "That signature does not match this request, so nothing was approved.", "another ask");

    let mut flipped = sign(&seen, "deny");
    flipped["decision"] = json!("approve");
    assert_eq!(message(&rig.phone("POST", "decide", "", flipped).1), "That signature does not match this request, so nothing was approved.", "a signed deny is not an approval");

    let mut drift = phone.decision(&seen, "approve");
    drift["text_sha256"] = json!(sha256_hex(b"what the screen showed"));
    assert_eq!(message(&rig.phone("POST", "decide", "", drift).1), "The text you were shown is not the text of this request, so nothing was signed.");
    assert!(!live(&rig, &name, "add_device", "*"), "no refusal granted anything");
    assert_eq!(ok(rig.call("approval_pending", "", &rig.session("demo", "agent-1").1, json!({})))["approvals"].as_array().unwrap().len(), 1, "the request stayed pending");
}

#[test]
fn a_request_that_ended_cannot_be_decided_and_the_agent_is_told_expired() {
    let (rig, phone, name, token, made) = asked(json!([ask("add_device", "*")]));
    let id = made["id"].as_str().unwrap().to_string();
    let seen = rig.get(&phone, &id);
    rig.node.lock().unwrap().approvals.update(&id, |a| a.expires_ms = a.created_ms + 1).unwrap();
    std::thread::sleep(std::time::Duration::from_millis(5));
    let reply = rig.phone("POST", "decide", "", phone.decision(&seen, "approve")).1;
    assert_eq!(message(&reply), "That request ended before it was decided; ask again if it is still needed.", "{reply}");
    assert_eq!(ok(rig.call("approval_status", "demo", &token, json!({"id": id})))["state"], "expired");
    assert!(!live(&rig, &name, "add_device", "*"));
}

#[test]
fn a_phone_that_was_removed_can_no_longer_approve_and_its_key_is_not_added_again() {
    let rig = Rig::new();
    let (mut first, mut second) = (FakePhone::new("Pixel"), FakePhone::new("Tablet"));
    rig.add(&mut first, None);
    rig.add(&mut second, Some(&first));
    let (name, token) = rig.session("demo", "agent-1");
    let removal = rig.phone("POST", "remove", &first.token, json!({"id": first.id})).1["result"].clone();
    assert_eq!(removal["risk"], "irreversible");
    let done = rig.phone("POST", "decide", "", second.decision(&removal, "approve")).1;
    assert_eq!(done["result"]["state"], "approved", "{done}");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let seen = rig.get(&second, made["id"].as_str().unwrap());
    let refused = rig.phone("POST", "decide", "", first.decision(&seen, "approve")).1;
    assert_eq!(message(&refused), "That phone was removed from this device and can no longer approve.", "{refused}");
    assert_eq!(rig.phone("POST", "list", &first.token, json!({})).0, 401, "a removed phone's token reads nothing");
    let secret = poolhouse_node::phone::enrol::make_offer(rig.dir.path(), kit::phone::now()).unwrap();
    let (_, reply) = rig.phone("POST", "add", "", first.add_body(&rig.device(), &secret));
    assert!(message(&reply).contains("was removed from this device"), "{reply}");
    assert!(!live(&rig, &name, "add_device", "*"));
}

#[test]
fn a_session_can_ask_for_a_phone_to_be_removed_and_only_a_key_removes_it() {
    let rig = Rig::new();
    let (mut first, mut second) = (FakePhone::new("Pixel"), FakePhone::new("Tablet"));
    rig.add(&mut first, None);
    rig.add(&mut second, Some(&first));
    let (_, token) = rig.session("demo", "agent-1");
    assert_eq!(code(&rig.call("phone_remove", "", &token, json!({"phone": "ph-nobody"}))), "invalid");
    let asked = ok(rig.call("phone_remove", "", &token, json!({"phone": second.id})));
    assert!(asked["text"].as_str().unwrap().contains("WHO: The session claude-") && asked["text"].as_str().unwrap().contains("ASK: Remove the phone Tablet"), "{asked}");
    assert_eq!(asked["risk"], "irreversible");
    assert_eq!(held(&mut rig.node.lock().unwrap()).len(), 2, "asking removes nothing");
    let done = rig.phone("POST", "decide", "", first.decision(&asked, "approve")).1;
    assert_eq!(done["result"]["state"], "approved", "{done}");
    assert_eq!(held(&mut rig.node.lock().unwrap()), [first.fingerprint()]);
    assert_eq!(rig.phone("POST", "list", &second.token, json!({})).0, 401);
}

#[test]
fn the_last_key_stays_even_when_it_is_the_phone_that_asks_to_go() {
    let rig = Rig::new();
    let mut only = FakePhone::new("Pixel");
    rig.add(&mut only, None);
    let removal = rig.phone("POST", "remove", &only.token, json!({"id": only.id})).1["result"].clone();
    let reply = rig.phone("POST", "decide", "", only.decision(&removal, "approve")).1;
    assert!(message(&reply).contains("the last person key stays"), "{reply}");
    assert_eq!(held(&mut rig.node.lock().unwrap()), [only.fingerprint()]);
}

#[test]
fn no_session_decides_and_the_one_that_asked_does_not_even_carry_the_answer() {
    let (rig, phone, name, token, made) = asked(json!([ask("add_device", "*")]));
    let id = made["id"].as_str().unwrap();
    let seen = rig.get(&phone, id);
    let (_, other) = rig.session("demo", "agent-2");
    let own_key = Person { key: ed25519_dalek::SigningKey::from_bytes(&[0x33; 32]) };
    let bytes = challenge::bytes(&seen["challenge"], "approve", &own_key.fingerprint());
    let sig = hex(&ed25519_dalek::Signer::sign(&own_key.key, &bytes).to_bytes());
    let attempt = json!({"id": id, "decision": "approve", "key": own_key.fingerprint(), "sig": sig});
    assert_eq!(message(&rig.call("approval_decide", "", &other, attempt.clone())), "That key is not one of your keys on this device, so nothing was approved.", "a key a session made itself");
    assert_eq!(message(&rig.call("approval_decide", "", &token, phone_decision(&phone, &seen))), "The session that asked cannot carry the answer to its own request.");
    for field in ["from", "as", "actor"] {
        let mut sneaky = attempt.clone();
        sneaky[field] = json!("person");
        assert_eq!(code(&rig.call("approval_decide", "", &other, sneaky)), "denied", "{field} names nobody the node would believe");
    }
    assert!(!live(&rig, &name, "add_device", "*"));
    let floor = rig.ask("demo", &token, json!([ask("grant_issue", "*")]), json!({}));
    assert_eq!(message(&floor), "grant_issue is a floor: no grant, from anyone, covers it", "an agent cannot ask for what no one may grant: {floor}");
    assert_eq!(message(&rig.ask("demo", &token, json!([ask("person_enroll", "*")]), json!({}))), "person_enroll is a floor: no grant, from anyone, covers it");
}

fn phone_decision(phone: &FakePhone, seen: &Value) -> Value {
    phone.decision(seen, "approve")
}

#[test]
fn a_person_key_that_signs_with_a_typed_yes_decides_the_same_requests() {
    let rig = Rig::new();
    let (name, token) = rig.session("demo", "agent-1");
    let (_, carrier) = rig.session("demo", "person-terminal");
    kit::grants::enrolled(rig.dir.path(), &mut |m, b, t, p| rig.call(m, b, t, p), &carrier);
    ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let seen = ok(rig.call("approval_pending", "", &carrier, json!({})))["approvals"][0].clone();
    let typed = person();
    let sign = |view: &Value, decision: &str| {
        let bytes = challenge::bytes(&view["challenge"], decision, &typed.fingerprint());
        json!({"id": view["id"], "decision": decision, "key": typed.fingerprint(), "sig": hex(&ed25519_dalek::Signer::sign(&typed.key, &bytes).to_bytes())})
    };
    let mut edited = seen.clone();
    edited["challenge"]["text"] = json!("something else");
    assert_eq!(message(&rig.call("approval_decide", "", &carrier, sign(&edited, "approve"))), "That signature does not match this request, so nothing was approved.");
    assert!(!live(&rig, &name, "add_device", "*"));
    let done = ok(rig.call("approval_decide", "", &carrier, sign(&seen, "approve")));
    assert_eq!(done["state"], "approved");
    assert!(live(&rig, &name, "add_device", "*"), "a grant approved by an Ed25519 key counts like one signed the old way");
}

#[test]
fn the_text_carries_every_field_a_person_needs_flags_first_and_the_pool_line_near_the_top() {
    let (rig, phone, _, _, made) = asked(json!([ask("add_device", "*"), {"action": "link_write", "resource": "demo>lab", "lasts_ms": 3_600_000, "review": true}]));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let text = seen["text"].as_str().unwrap();
    let labels: Vec<&str> = text.lines().map(|l| l.split(':').next().unwrap().split(' ').next().unwrap()).collect();
    let order = ["FLAG", "POOL", "RISK", "WHO", "ASK", "SCOPE", "ASK", "SCOPE", "CANNOT", "WHY", "DENY", "EXPIRES"];
    let mut at = 0;
    for label in order {
        at += 1 + labels[at..].iter().position(|l| *l == label).unwrap_or_else(|| panic!("{label} is missing or out of order in:\n{text}"));
    }
    assert_eq!(labels[0], "FLAG", "flags come first");
    assert!(text.contains("POOL: SECURE. Each device needs your code to join."), "{text}");
    assert!(text.contains("RISK: SENSITIVE."), "{text}");
    assert!(text.contains("WHO: claude-") && text.contains("claude-sonnet-5-5 in claude-code, a main session, on device"), "{text}");
    assert!(text.contains("ASK 1: Make a short code that lets one more device join this pool."), "{text}");
    assert!(text.contains("ASK 2: Share the board demo with the board lab, read and write."), "{text}");
    assert!(text.contains("SCOPE 1:") && text.contains("10 minutes from the moment you approve") && text.contains("once;"), "{text}");
    assert!(text.contains("each use needs an independent review"), "{text}");
    assert!(text.contains("CANNOT: Nothing beyond what is listed") && text.contains("It can never approve anything or act as you."), "{text}");
    assert!(text.contains("WHY: The agent says: \"adding the Linux box\" (not checked by anyone)."), "{text}");
    assert!(text.contains("DENY: If you deny, the agent is told no and nothing changes."), "{text}");
    assert!(text.contains("EXPIRES: This request ends at "), "{text}");
    assert!(text.contains("No grant you signed has ever named") && text.contains("first request for add_device"), "{text}");
    assert!(text.is_ascii() && text.len() <= challenge::MAX_TEXT);
    assert_eq!(seen["challenge"]["text"], seen["text"], "what is signed is what is shown");
    assert_eq!(seen["risk"], "sensitive");
    assert!(seen["title"].as_str().unwrap().ends_with("asks for 2 things"), "{seen}");
}

#[test]
fn a_request_that_does_not_connect_anything_states_no_pool_line_and_a_routine_one_says_so() {
    let (rig, phone, _, _, made) = asked(json!([ask("note_verify", "note-1")]));
    let text = rig.get(&phone, made["id"].as_str().unwrap())["text"].as_str().unwrap().to_string();
    assert!(!text.contains("POOL:") && text.contains("RISK: ROUTINE."), "{text}");
}

#[test]
fn a_change_of_join_policy_shows_both_values_and_secure_to_open_is_flagged_sensitive() {
    let (rig, phone, _, _, made) = asked(json!([ask("set_join_policy", "open")]));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    let text = seen["text"].as_str().unwrap();
    assert!(text.contains("POOL: SECURE now, would become OPEN. Devices on the same network join without asking."), "{text}");
    assert!(text.lines().next().unwrap().starts_with("FLAG:") && text.contains("FLAG: This would change the pool from secure to open."), "{text}");
    assert!(text.contains("ASK: Change how devices join the pool, from secure to open."), "{text}");
    assert_eq!(seen["risk"], "sensitive");
    assert_eq!(rig.node.lock().unwrap().members.policy.name(), "secure", "asking changes nothing");
}

#[test]
fn what_an_agent_believes_about_the_pool_is_checked_against_the_node() {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    let (_, token) = rig.session("demo", "agent-1");
    let refused = rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"expect_policy": "open"}));
    assert_eq!(message(&refused), "The pool is secure, not open as you wrote; ask again with what is true.", "{refused}");
    ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"expect_policy": "secure"})));
}

#[test]
fn a_request_asking_for_more_than_the_last_grant_is_flagged() {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    let (name, token) = rig.session("demo", "agent-1");
    let first = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let seen = rig.get(&phone, first["id"].as_str().unwrap());
    ok(rig.phone("POST", "decide", "", phone.decision(&seen, "approve")).1);
    assert!(live(&rig, &name, "add_device", "*"));
    let greedy = ok(rig.ask("demo", &token, json!([{"action": "add_device", "resource": "*", "lasts_ms": 7 * 24 * 3_600_000u64}]), json!({})));
    let text = rig.get(&phone, greedy["id"].as_str().unwrap())["text"].as_str().unwrap().to_string();
    assert!(text.contains("It asks for more than its last add_device grant: a longer time, more uses."), "{text}");
    assert!(text.contains("The add_device grant would last more than a day."), "{text}");
    assert!(!text.contains("No grant you signed has ever named"), "it was named by the first approval: {text}");
    assert!(!text.contains("first request for add_device"), "{text}");
}

#[test]
fn a_subagent_asking_is_flagged_and_the_grant_names_only_that_session() {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    let (lead, lead_token) = rig.session("demo", "lead");
    let child = ok(rig.call("register", "demo", &lead_token, json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "child"})));
    let made = ok(rig.ask("demo", child["token"].as_str().unwrap(), json!([ask("sync_now", "*")]), json!({})));
    let seen = rig.get(&phone, made["id"].as_str().unwrap());
    assert!(seen["text"].as_str().unwrap().contains(&format!("A subagent of {lead} is asking")), "{seen}");
    assert!(seen["text"].as_str().unwrap().contains(&format!("a subagent of {lead}")), "{seen}");
    ok(rig.phone("POST", "decide", "", phone.decision(&seen, "approve")).1);
    let name = child["name"].as_str().unwrap();
    assert!(live(&rig, name, "sync_now", "*") && !live(&rig, &lead, "sync_now", "*"), "the grant is for the one that asked");
}

#[test]
fn limits_bound_what_an_agent_may_ask() {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    let (_, token) = rig.session("demo", "agent-1");
    assert_eq!(code(&rig.ask("demo", &token, json!([]), json!({}))), "invalid");
    assert_eq!(code(&rig.ask("demo", &token, json!((0..9).map(|i| ask("link_read", &format!("a>b{i}"))).collect::<Vec<_>>()), json!({}))), "invalid", "nine asks");
    assert_eq!(code(&rig.ask("demo", &token, json!([ask("add_device", "*"), ask("add_device", "*")]), json!({}))), "invalid", "one ask twice");
    assert_eq!(code(&rig.ask("demo", &token, json!([{"action": "add_device", "lasts_ms": 1000}]), json!({}))), "invalid", "under a minute");
    assert_eq!(code(&rig.ask("demo", &token, json!([{"action": "add_device", "lasts_ms": 31 * 24 * 3_600_000u64}]), json!({}))), "invalid", "over thirty days");
    assert_eq!(code(&rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"note": "caf\u{e9}"}))), "invalid", "text that is not plain");
    assert_eq!(code(&rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"ttl_s": 5}))), "invalid", "a request that lives under a minute");
    assert_eq!(code(&rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"note": "x".repeat(161)}))), "invalid", "a note over 160 characters");
    for i in 0..6 {
        ok(rig.ask("demo", &token, json!([ask("link_read", &format!("a>b{i}"))]), json!({})));
    }
    assert_eq!(code(&rig.ask("demo", &token, json!([ask("link_read", "a>z")]), json!({}))), "quota", "six requests in ten minutes");
}

#[test]
fn a_session_reads_and_withdraws_only_its_own_requests() {
    let (rig, phone, _, token, made) = asked(json!([ask("add_device", "*")]));
    let id = made["id"].as_str().unwrap();
    let (_, other) = rig.session("demo", "agent-2");
    assert_eq!(code(&rig.call("approval_status", "demo", &other, json!({"id": id}))), "denied");
    assert_eq!(code(&rig.call("approval_cancel", "demo", &other, json!({"id": id}))), "denied");
    assert_eq!(ok(rig.call("approval_cancel", "demo", &token, json!({"id": id})))["state"], "cancelled");
    let seen = rig.get(&phone, id);
    assert_eq!(message(&rig.phone("POST", "decide", "", phone.decision(&seen, "approve")).1), "That request was withdrawn by the session that made it.");
    assert_eq!(ok(rig.call("approvals", "demo", &token, json!({})))["approvals"].as_array().unwrap().len(), 1);
}

#[test]
fn a_request_waits_across_a_restart_and_is_still_decided_once() {
    let (rig, phone, name, _, made) = asked(json!([ask("add_device", "*")]));
    let id = made["id"].as_str().unwrap().to_string();
    let seen = rig.get(&phone, &id);
    drop(rig.node);
    let node = poolhouse_node::node::Node::open(rig.dir.path()).unwrap();
    let again = std::sync::Mutex::new(node);
    let reply = poolhouse_node::phone::http::handle(&again, kit::phone::LOCAL, "POST", "/phone/v1/decide", "", phone.decision(&seen, "approve").to_string().as_bytes()).1;
    assert_eq!(reply["result"]["state"], "approved", "{reply}");
    assert!(check(&mut again.lock().unwrap(), &holder(&name), "add_device", &Scope::on("*")).unwrap(), "the grant is on the board and the key is held after a restart");
}

#[test]
fn a_push_is_ciphertext_only_and_opens_to_the_phone_with_the_whole_request() {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    rig.push_to(&phone, "https://push.example.org/UPabc123");
    let (name, token) = rig.session("demo", "agent-1");
    let made = ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({"note": "adding the Linux box"})));
    let sent = rig.pushed.sent();
    assert_eq!(sent.len(), 1);
    assert_eq!(sent[0].0, "https://push.example.org/UPabc123");
    for secret in [name.as_bytes(), b"add_device", b"Linux", b"approval", made["id"].as_str().unwrap().as_bytes()] {
        assert!(!sent[0].1.windows(secret.len()).any(|w| w == secret), "the push service sees nothing of {:?}", String::from_utf8_lossy(secret));
    }
    let opened = phone.open_push(&sent[0].1);
    assert_eq!(opened["id"], made["id"]);
    assert_eq!(opened["agent"], json!(name));
    assert_eq!(opened["risk"], "sensitive");
    assert_eq!(opened["challenge"]["text"], made["text"], "the notification holds the whole text, and it is the text that is signed");
    let signed = json!({"challenge": opened["challenge"], "id": opened["id"], "text": made["text"]});
    let reply = rig.phone("POST", "decide", "", phone.decision(&signed, "approve")).1;
    assert_eq!(reply["result"]["state"], "approved", "a phone can decide from the push alone: {reply}");
}

#[test]
fn a_request_too_large_for_a_push_sends_only_its_title_and_the_phone_fetches_the_rest() {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    rig.push_to(&phone, "https://push.example.org/UPabc123");
    let (_, token) = rig.session("demo", "agent-1");
    let asks: Vec<Value> = (0..8).map(|i| ask("link_write", &format!("{}>{}", "a".repeat(40 + i), "b".repeat(60)))).collect();
    let made = ok(rig.ask("demo", &token, json!(asks), json!({"note": "x".repeat(150)})));
    let opened = phone.open_push(&rig.pushed.sent()[0].1);
    assert_eq!(opened["more"], true, "{opened}");
    assert!(opened.get("challenge").is_none() && opened["id"] == made["id"] && opened["title"].is_string());
    assert_eq!(rig.get(&phone, made["id"].as_str().unwrap())["text"], made["text"]);
}

#[test]
fn a_phone_added_by_a_code_is_one_time_and_a_later_phone_waits_for_approval() {
    let rig = Rig::new();
    let (mut first, mut second) = (FakePhone::new("Pixel"), FakePhone::new("Tablet"));
    let secret = poolhouse_node::phone::enrol::make_offer(rig.dir.path(), kit::phone::now()).unwrap();
    let wrong = rig.phone("POST", "add", "", first.add_body(&rig.device(), "not-the-code")).1;
    assert_eq!(message(&wrong), "That code was not made for this device.");
    assert_eq!(held(&mut rig.node.lock().unwrap()).len(), 0);
    let (_, added) = rig.phone("POST", "add", "", first.add_body(&rig.device(), &secret));
    assert_eq!(added["result"]["state"], "active", "{added}");
    assert_eq!(held(&mut rig.node.lock().unwrap()), [first.fingerprint()], "the phone's key is a person key");
    let used = rig.phone("POST", "add", "", second.add_body(&rig.device(), &secret)).1;
    assert_eq!(message(&used), "No phone is being added right now: ask for a new code.", "a code is good once");
    first.token = added["result"]["token"].as_str().unwrap().into();
    first.id = added["result"]["phone"].as_str().unwrap().into();

    rig.add(&mut second, Some(&first));
    assert_eq!(held(&mut rig.node.lock().unwrap()).len(), 2);
    let listing = rig.phone("POST", "phones", &second.token, json!({})).1["result"]["phones"].clone();
    assert_eq!(listing.as_array().unwrap().len(), 2);
    assert!(listing.to_string().find("token").is_none(), "no phone is told another's token");
}

#[test]
fn a_phone_waiting_for_approval_reads_nothing_and_a_denied_one_is_dropped() {
    let rig = Rig::new();
    let (mut first, second) = (FakePhone::new("Pixel"), FakePhone::new("Tablet"));
    rig.add(&mut first, None);
    let secret = poolhouse_node::phone::enrol::make_offer(rig.dir.path(), kit::phone::now()).unwrap();
    let added = rig.phone("POST", "add", "", second.add_body(&rig.device(), &secret)).1["result"].clone();
    let token = added["token"].as_str().unwrap();
    assert_eq!(rig.phone("POST", "status", token, json!({})).1["result"]["state"], "pending");
    assert_eq!(code(&rig.phone("POST", "list", token, json!({})).1), "denied");
    assert!(held(&mut rig.node.lock().unwrap()).len() == 1, "its key is not a person key yet");
    let seen = rig.get(&first, added["approval"].as_str().unwrap());
    assert!(seen["text"].as_str().unwrap().contains("POOL: SECURE."), "adding a phone connects something");
    assert!(seen["text"].as_str().unwrap().contains(&format!("key {}", added["words"].as_str().unwrap())), "the person compares the key on both screens");
    ok(rig.phone("POST", "decide", "", first.decision(&seen, "deny")).1);
    assert_eq!(rig.phone("POST", "status", token, json!({})).0, 401, "a denied phone's token is dead");
    assert_eq!(held(&mut rig.node.lock().unwrap()).len(), 1);
}

#[test]
fn an_agent_can_ask_for_a_code_for_a_phone_only_once_a_key_exists() {
    let rig = Rig::new();
    let (_, token) = rig.session("demo", "agent-1");
    assert!(message(&rig.call("phone_offer", "", &token, json!({}))).contains("The first phone is added by a person at the computer"));
    let mut first = FakePhone::new("Pixel");
    rig.add(&mut first, None);
    assert!(message(&rig.call("phone_offer", "", &token, json!({}))).contains("not listening for phones"), "nothing listens until a person's command turned it on");
    rig.node.lock().unwrap().phone_port = 7449;
    let offer = ok(rig.call("phone_offer", "", &token, json!({})));
    assert!(offer["link"].as_str().unwrap().starts_with("poolhouse://add-phone?data="));
    for _ in 0..2 {
        ok(rig.call("phone_offer", "", &token, json!({})));
    }
    assert_eq!(code(&rig.call("phone_offer", "", &token, json!({}))), "quota", "three codes an hour");
}

#[test]
fn a_phone_sees_what_changes_while_it_waits() {
    let rig = Rig::new();
    let mut phone = FakePhone::new("Pixel");
    rig.add(&mut phone, None);
    let (_, token) = rig.session("demo", "agent-1");
    let before = rig.phone("POST", "list", &phone.token, json!({})).1["result"]["version"].as_u64().unwrap();
    let quick = std::time::Instant::now();
    let same = rig.phone("POST", "wait", &phone.token, json!({"after": before, "timeout_ms": 400})).1["result"].clone();
    assert!(quick.elapsed() >= std::time::Duration::from_millis(350) && same["version"].as_u64().unwrap() == before, "nothing new: it waits the time it was given");
    ok(rig.ask("demo", &token, json!([ask("add_device", "*")]), json!({})));
    let woke = rig.phone("POST", "wait", &phone.token, json!({"after": before, "timeout_ms": 5000})).1["result"].clone();
    assert!(woke["version"].as_u64().unwrap() > before && woke["approvals"].as_array().unwrap().len() == 1);
}

#[test]
fn a_grant_entry_copied_from_an_approval_and_changed_counts_for_nothing() {
    use poolhouse_node::grants::spec::{parse_issue, signing_bytes};
    let (rig, phone, _, _, made) = asked(json!([ask("add_device", "*")]));
    let id = made["id"].as_str().unwrap();
    let decision = phone.decision(&rig.get(&phone, id), "approve");
    ok(rig.phone("POST", "decide", "", decision.clone()).1);
    let (other, _) = rig.session("demo", "agent-2");
    let node = rig.node.lock().unwrap();
    let approved = node.approvals.get(id).cloned().unwrap();
    let (device, key, sig) = (node.cert.fingerprint(), phone.fingerprint(), decision["sig"].as_str().unwrap().to_string());
    let entry = || poolhouse_node::approvals::challenge::grant_entries(&approved, &device, approved.decided_ms, &key, &sig).remove(0);
    let whole = entry();
    assert!(parse_issue(&whole).is_ok() && node.persons.signed(&key, &signing_bytes(&whole), &sig), "the entry the node wrote is read and verifies");

    let mut other_session = entry();
    other_session.insert("to".into(), json!({"kind": "session", "name": other}));
    assert!(parse_issue(&other_session).is_ok() && !node.persons.signed(&key, &signing_bytes(&other_session), &sig), "a signature for one session is not good for another");
    let mut other_text = entry();
    other_text["approval"]["text"] = json!(format!("{}\nWHY: nothing to see", approved.text));
    assert!(!node.persons.signed(&key, &signing_bytes(&other_text), &sig), "nor for other words");
    let mut other_board = entry();
    other_board.insert("board".into(), json!("lab"));
    other_board["scope"]["board"] = json!("lab");
    assert!(!node.persons.signed(&key, &signing_bytes(&other_board), &sig), "nor for another board");
    let mut other_key = entry();
    other_key.insert("person".into(), json!(sha256_hex(b"another key")));
    assert!(!node.persons.signed(&key, &signing_bytes(&other_key), &sig), "nor for another key");

    let tweaks: [(&str, Box<dyn Fn(&mut serde_json::Map<String, Value>)>); 5] = [
        ("a gid the approval does not name", Box::new(|b| { b.insert("gid".into(), json!("g-0000000000000001")); })),
        ("a resource the approval does not name", Box::new(|b| { b["scope"]["resource"] = json!("*lab"); })),
        ("no limit on uses", Box::new(|b| { b.remove("uses"); })),
        ("a longer time", Box::new(|b| { let end = b["until_ms"].as_u64().unwrap(); b.insert("until_ms".into(), json!(end + 1)); })),
        ("a start after the request ended", Box::new(|b| { let late = approved.expires_ms + 10 * 60 * 1000; b.insert("at_ms".into(), json!(late)); b.insert("until_ms".into(), json!(late + 600_000)); })),
    ];
    for (what, change) in &tweaks {
        let mut body = entry();
        change(&mut body);
        assert!(parse_issue(&body).is_err(), "an entry with {what} is not one of the asks that were approved");
    }
}

#[test]
fn the_vectors_a_second_implementation_made_are_the_bytes_and_the_signature_this_node_checks() {
    let file: Value = serde_json::from_str(include_str!("../../../tests/fixtures/phone-approval-vectors.json")).unwrap();
    let key = file["key"]["fingerprint"].as_str().unwrap();
    let bytes = challenge::bytes(&file["challenge"], "approve", key);
    assert_eq!(String::from_utf8(bytes.clone()).unwrap(), file["approve_bytes"].as_str().unwrap(), "the same canonical bytes as Python's");
    assert!(poolhouse_node::grants::person::verifies_p256(file["key"]["public"].as_str().unwrap(), &bytes, file["approve_signature"].as_str().unwrap()));
    let deny = challenge::bytes(&file["challenge"], "deny", key);
    assert_eq!(String::from_utf8(deny.clone()).unwrap(), file["deny_bytes"].as_str().unwrap());
    assert!(poolhouse_node::grants::person::verifies_p256(file["key"]["public"].as_str().unwrap(), &deny, file["deny_signature"].as_str().unwrap()));
    assert!(!poolhouse_node::grants::person::verifies_p256(file["key"]["public"].as_str().unwrap(), &deny, file["approve_signature"].as_str().unwrap()), "an approval is not a deny");
}
