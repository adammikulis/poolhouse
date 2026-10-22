//! Moving a main session in a pool: a typed-yes signature moves nothing there, an admin phone's approval does, once, from the terminal
//! the request names, and a refused or repeated request spends no nonce.
#![cfg(unix)]

mod kit;

use kit::grants::{anchor_of, fresh_nonce, now_ms};
use kit::pool::{found, Founded};
use kit::procs::*;
use kit::*;
use poolhouse_node::lineage::Peer;
use serde_json::{json, Value};

fn rebind(pool: &Founded, from: &Peer, params: Value) -> Value {
    req_from(&mut pool.rig.node.lock().unwrap(), from, "rebind", "demo", "", params)
}

fn move_to(anchor: &Value) -> Value {
    json!({"harness": "claude-code", "session": "lead", "anchor": anchor})
}

fn finishing(anchor: &Value, id: &str) -> Value {
    json!({"harness": "claude-code", "session": "lead", "anchor": anchor, "approval": id})
}

fn lead_works(pool: &Founded) -> bool {
    req(&mut pool.rig.node.lock().unwrap(), "whoami", "demo", &pool.token, json!({}))["ok"] == true
}

fn registers_from(pool: &Founded, from: &Peer) -> bool {
    req_from(&mut pool.rig.node.lock().unwrap(), from, "register", "demo", "", json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "lead"}))["ok"] == true
}

fn denial(reply: &Value) -> String {
    Founded::denial(reply)
}

#[test]
fn an_approved_session_move_works_once_from_the_terminal_it_names_and_ends_the_tokens_the_session_held() {
    let pool = found();
    let (there, elsewhere) = (process(true), process(true));
    let anchor = anchor_of(there.id());
    let ask = ok(rebind(&pool, &peer(&there), move_to(&anchor)));
    let (id, code) = (ask["id"].as_str().unwrap().to_string(), ask["code"].as_str().unwrap().to_string());
    assert_eq!(code, pool.rig.shown(&id), "the terminal is told the number the person picks among three");
    assert_eq!(ok(rebind(&pool, &peer(&there), move_to(&anchor)))["id"], id.as_str(), "asking again attaches to the first request");
    let waiting = ok(rebind(&pool, &peer(&there), finishing(&anchor, &id)));
    assert_eq!((waiting["state"].clone(), waiting["moved"].clone()), (json!("pending"), json!(false)));
    let view = pool.rig.get(&pool.phone, &id);
    let text = view["text"].as_str().unwrap();
    assert!(text.contains("Move the session") && text.contains("PICK:"), "{text}");
    assert_eq!(view["challenge"]["action"]["anchor"], anchor, "the terminal is inside what the phone signs");
    assert_eq!(ok(pool.answer(&pool.phone, &id, "approve"))["state"], "approved");
    assert!(lead_works(&pool), "the tap moved nothing: the terminal completes the move");
    assert!(denial(&rebind(&pool, &peer(&elsewhere), finishing(&anchor, &id))).contains("another terminal than the one asking"));
    let moved = ok(rebind(&pool, &peer(&there), finishing(&anchor, &id)));
    assert_eq!((moved["name"].clone(), moved["moved"].clone()), (json!(pool.name), json!(true)));
    assert!(moved["tokens_ended"].as_u64().unwrap() >= 1);
    assert!(!lead_works(&pool), "the tokens it held ended");
    assert!(denial(&rebind(&pool, &peer(&there), finishing(&anchor, &id))).contains("already carried out"), "an approved move is used once");
    assert!(!registers_from(&pool, &me()) && registers_from(&pool, &peer(&there)), "the session registers from the new terminal only");
    done(vec![there, elsewhere]);
}

#[test]
fn a_move_is_refused_for_another_session_a_wrong_number_and_a_key_that_is_no_admin_key() {
    let pool = found();
    let there = process(true);
    let anchor = anchor_of(there.id());
    let (_, carrier) = pool.rig.session("demo", "other");
    let id = ok(rebind(&pool, &peer(&there), move_to(&anchor)))["id"].as_str().unwrap().to_string();
    let other = json!({"harness": "claude-code", "session": "other", "anchor": anchor, "approval": id});
    assert!(denial(&rebind(&pool, &peer(&there), other)).contains("no move of that session"), "an approval for one session moves no other");
    let view = pool.rig.get(&pool.phone, &id);
    let typed = pool.rig.call("approval_decide", "demo", &carrier, json!({"id": id, "decision": "approve", "key": pool.typed.fingerprint(), "sig": "00"}));
    assert!(denial(&typed).contains("not an admin key of this pool"), "{typed}");
    let still = pool.rig.node.lock().unwrap().approvals.get(&id).unwrap().state.clone();
    assert_eq!(still, "pending", "a refused decision moves nothing");
    let mut wrong = pool.phone.decision(&view, "approve");
    let other_number = pool.rig.node.lock().unwrap().approvals.get(&id).map(|a| a.context.pick.iter().find(|n| **n != a.pick_code).cloned().unwrap()).unwrap();
    wrong["pick"] = json!(other_number);
    assert_eq!(pool.rig.phone("POST", "decide", "", wrong).1["ok"], false);
    assert!(denial(&rebind(&pool, &peer(&there), finishing(&anchor, &id))).contains("ended without a tap"), "a wrong number is a decline");
    assert!(lead_works(&pool));
    done(vec![there]);
}

#[test]
fn a_typed_yes_signature_moves_no_session_in_a_pool_and_spends_no_nonce() {
    let pool = found();
    let there = process(true);
    let anchor = anchor_of(there.id());
    let device = pool.rig.device();
    let params = pool.typed.rebind_params("claude-code", "lead", "demo", &pool.name, &device, &anchor);
    let nonce = params["nonce"].as_str().unwrap().to_string();
    let first = ok(rebind(&pool, &peer(&there), params.clone()));
    assert!(first["moved"].is_null() && first["id"].is_string(), "a signature asks for a tap; it moves nothing: {first}");
    for _ in 0..20 {
        let again = ok(rebind(&pool, &peer(&there), pool.typed.rebind_params("claude-code", "lead", "demo", &pool.name, &device, &anchor)));
        assert_eq!(again["id"], first["id"], "repeated requests wait on the one request");
    }
    assert!(lead_works(&pool) && registers_from(&pool, &me()) && !registers_from(&pool, &peer(&there)), "the session stayed where it was");
    assert!(pool.rig.node.lock().unwrap().persons.once(&nonce, now_ms()).unwrap(), "the request spent no nonce");
    assert!(pool.rig.node.lock().unwrap().persons.once(&fresh_nonce(), now_ms()).unwrap(), "the table has room for the lead's own request");
    done(vec![there]);
}

#[test]
fn a_device_with_no_admin_phone_asks_for_no_tap_that_nobody_could_give() {
    let pool = found().joined();
    let there = process(true);
    let refused = rebind(&pool, &peer(&there), move_to(&anchor_of(there.id())));
    assert!(denial(&refused).contains("No admin phone of this pool is added to this device"), "{refused}");
    assert!(pool.rig.node.lock().unwrap().approvals.all().is_empty() && pool.rig.pushed.sent().is_empty());
    done(vec![there]);
}
