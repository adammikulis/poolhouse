//! Who may put a person key on a node and who may take one off: the first key needs the one-time secret a person's command
//! at a terminal leaves, so a session that registered first enrols nothing, and a key is removed only by an enrolled key.

mod kit;

use ed25519_dalek::SigningKey;
use kit::grants::{agent, enrolled, expect_enrolment, now_ms, person, third, Person, Spec};
use kit::*;
use poolhouse_node::fsutil::sha256_hex;
use poolhouse_node::grants::person::SECRET_FILE;
use poolhouse_node::grants::{check, Scope};
use poolhouse_node::identity::Holder;
use serde_json::{json, Value};
use tempfile::tempdir;

const GID: &str = "g-00000000000000c1";

fn leave(dir: &std::path::Path, secret: &str, at_ms: u64) {
    std::fs::write(dir.join(SECRET_FILE), json!({"sha256": sha256_hex(secret.as_bytes()), "at_ms": at_ms}).to_string()).unwrap();
}

fn with_secret(who: &Person, about: &str, secret: &str) -> Value {
    let mut params = who.enrolment(about, None);
    params["secret"] = json!(secret);
    params
}

#[test]
fn a_session_that_registered_first_cannot_enrol_its_own_key_grant_itself_and_lock_the_person_out() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, lead_token) = session(&mut n, "demo", "lead");
    let (evil, evil_token) = session(&mut n, "demo", "evil");
    let key = agent();

    let own = key.enrolment("the agent", None);
    assert_eq!(code(&req(&mut n, "person_enroll", "", &evil_token, own.clone())), "denied", "no secret, no key");
    assert_eq!(code(&req(&mut n, "person_enroll", "", &evil_token, with_secret(&key, "the agent", &"0".repeat(64)))), "denied", "a guessed secret");
    let mut planted = with_secret(&key, "the agent", "mine");
    planted["sha256"] = json!(sha256_hex(b"mine"));
    assert_eq!(code(&req(&mut n, "person_enroll", "", &evil_token, planted)), "invalid", "no request sets the hash a secret is checked against");
    assert!(!dir.path().join(SECRET_FILE).exists() && held(&mut n).is_empty());

    let want = Spec::session(&evil, "set_join_policy");
    assert_eq!(code(&key.issue(&mut caller(&mut n), "demo", &evil_token, GID, &want)), "denied", "a grant signed by a key nobody enrolled");
    assert_eq!(code(&req(&mut n, "set_join_policy", "", &evil_token, json!({"policy": "open"}))), "denied");

    enrolled(dir.path(), &mut caller(&mut n), &lead_token);
    assert_eq!(held(&mut n), [person().fingerprint()], "the person's own key is the one enrolled");
    assert_eq!(code(&req(&mut n, "person_enroll", "", &evil_token, own)), "denied", "and an agent's key still needs the person's approval");
}

#[test]
fn the_secret_is_single_use_short_lived_and_spent_by_a_wrong_guess() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    let secret = expect_enrolment(dir.path());
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, with_secret(&agent(), "x", "not-it"))), "denied");
    assert!(!dir.path().join(SECRET_FILE).exists(), "a wrong guess spends the secret");
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, with_secret(&person(), "a test person", &secret))), "denied", "so the right one is refused after it");

    leave(dir.path(), "old", now_ms() - 11 * 60_000);
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, with_secret(&person(), "a test person", "old"))), "denied", "ten minutes is as long as it lasts");
    leave(dir.path(), "early", now_ms() + 3_600_000);
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, with_secret(&person(), "a test person", "early"))), "denied", "a hash dated ahead is no secret");
    leave(dir.path(), "", now_ms());
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, with_secret(&person(), "a test person", ""))), "denied", "an empty secret is none");
    assert!(held(&mut n).is_empty());

    let secret = expect_enrolment(dir.path());
    ok(req(&mut n, "person_enroll", "", &token, with_secret(&person(), "a test person", &secret)));
    assert!(!dir.path().join(SECRET_FILE).exists(), "the secret is gone once it enrolled a key");

    expect_enrolment(dir.path());
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, with_secret(&agent(), "x", "anything"))), "denied", "a node that holds a key asks for the key's approval, not a secret");
}

#[test]
fn a_key_is_removed_only_by_an_enrolled_key_and_the_last_key_stays() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    enrolled(dir.path(), &mut caller(&mut n), &token);
    let (first, second) = (person(), agent());
    let device = n.cert.fingerprint();
    ok(req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device)))));
    assert_eq!(held(&mut n).len(), 2);
    ok(second.issue(&mut caller(&mut n), "demo", &token, GID, &Spec::session(&name, "note_verify")));
    let me = Holder { board: "demo".into(), name: name.clone() };
    assert!(check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "the second key's grant counts while the key is enrolled");

    let stranger = Person { key: SigningKey::from_bytes(&[0x33; 32]) };
    assert_eq!(code(&req(&mut n, "person_unenroll", "", &token, stranger.unenroll_params(&second.fingerprint(), &device))), "denied", "a key nobody enrolled removes nothing");
    assert_eq!(code(&req(&mut n, "person_unenroll", "", "", first.unenroll_params(&second.fingerprint(), &device))), "denied", "a request with no session carries nothing");
    let mut redirected = first.unenroll_params(&second.fingerprint(), &device);
    redirected["fingerprint"] = json!(first.fingerprint());
    assert_eq!(code(&req(&mut n, "person_unenroll", "", &token, redirected)), "denied", "a removal signed for one key does not remove another");
    assert_eq!(code(&req(&mut n, "person_unenroll", "", &token, first.unenroll_params_at(&second.fingerprint(), &device, now_ms() - 10 * 60_000))), "invalid", "a stale removal");
    assert_eq!(code(&req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&"ab".repeat(32), &device))), "invalid", "no such key");
    assert_eq!(held(&mut n).len(), 2);

    ok(req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&second.fingerprint(), &device)));
    assert_eq!(held(&mut n), [first.fingerprint()]);
    assert!(!check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "a removed key's grants count no more");
    let shown = ok(req(&mut n, "grants", "demo", &token, json!({})));
    assert_eq!(shown["grants"][0]["status"], "ignored", "{shown}");

    assert_eq!(code(&req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&first.fingerprint(), &device))), "denied", "the last key stays");
    assert_eq!(held(&mut n), [first.fingerprint()]);
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", None))), "denied", "a removed key enrols again only with approval");
}

#[test]
fn a_damaged_secret_file_and_hostile_removals_change_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    let big = "x".repeat(5_000_000);
    for junk in ["", "{", "[]", "null", r#"{"sha256": 5, "at_ms": 1}"#, r#"{"sha256": "zz", "at_ms": "now"}"#, r#"{"sha256": "00", "at_ms": -1}"#, &big] {
        std::fs::write(dir.path().join(SECRET_FILE), junk).unwrap();
        assert_eq!(code(&req(&mut n, "person_enroll", "", &token, with_secret(&person(), "a test person", junk))), "denied", "{}", &junk[..junk.len().min(40)]);
        assert!(!dir.path().join(SECRET_FILE).exists());
    }
    assert!(held(&mut n).is_empty());

    enrolled(dir.path(), &mut caller(&mut n), &token);
    let (first, second) = (person(), agent());
    let device = n.cert.fingerprint();
    ok(req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device)))));
    let good = first.unenroll_params(&second.fingerprint(), &device);
    let hostile = [("fingerprint", json!("ab\ncd")), ("fingerprint", json!("a".repeat(100_000))), ("fingerprint", json!(5)), ("fingerprint", Value::Null),
                   ("by", json!("../persons")), ("by", json!(second.fingerprint())), ("by", json!(5)), ("sig", json!("zz")), ("sig", json!("00".repeat(64))),
                   ("sig", json!(["00"])), ("at_ms", json!(-5)), ("at_ms", json!("now")), ("at_ms", json!(1.5)), ("admin", json!(true)), ("as", json!("person")),
                   ("nonce", json!("short")), ("nonce", json!(7)), ("nonce", Value::Null), ("nonce", json!("x".repeat(100_000))), ("anchor", json!(5)),
                   ("anchor", json!("here")), ("anchor", Value::Null), ("anchor", json!({"pid": 1})), ("anchor", json!({"pid": -1, "start": 1})),
                   ("anchor", json!({"pid": 1, "start": 1, "admin": true})), ("node", json!("ab".repeat(32)))];
    for (key, value) in hostile {
        let mut p = good.clone();
        p[key] = value;
        let reply = req(&mut n, "person_unenroll", "", &token, p);
        assert_eq!(reply["ok"], false, "{key}: {reply}");
    }
    assert_eq!(held(&mut n).len(), 2, "nothing was removed");
    assert_eq!(code(&req(&mut n, "person_unenroll", "", &token, json!(["not", "an", "object"]))), "invalid");
    ok(req(&mut n, "person_unenroll", "", &token, good));
    assert_eq!(held(&mut n), [first.fingerprint()]);
}

#[test]
fn an_enrolled_key_survives_a_restart_and_a_removed_one_stays_removed() {
    let dir = tempdir().unwrap();
    let (first, second) = (person(), agent());
    {
        let mut n = node(dir.path());
        let (_, token) = session(&mut n, "demo", "a");
        enrolled(dir.path(), &mut caller(&mut n), &token);
        let device = n.cert.fingerprint();
    ok(req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device)))));
        ok(req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&second.fingerprint(), &device)));
    }
    let mut n = node(dir.path());
    assert_eq!(held(&mut n), [first.fingerprint()]);
    drop(n);
    std::fs::remove_file(dir.path().join("persons.json")).unwrap();
    let mut n = node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    assert!(held(&mut n).is_empty(), "with persons.json deleted while the node was stopped the node holds no key");
    enrolled(dir.path(), &mut caller(&mut n), &token);
    assert_eq!(held(&mut n), [first.fingerprint()], "and the person enrols again with `grants-setup`");
}

#[test]
fn what_a_removed_key_signed_stays_void_and_the_key_is_not_enrolled_again() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    enrolled(dir.path(), &mut caller(&mut n), &token);
    let (first, second) = (person(), agent());
    let device = n.cert.fingerprint();
    ok(req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device)))));
    ok(second.issue(&mut caller(&mut n), "demo", &token, GID, &Spec::session(&name, "note_verify")));
    let ahead = Spec::session(&name, "land_control").window(120_000, 3_600_000);
    ok(second.issue(&mut caller(&mut n), "demo", &token, "g-00000000000000c3", &ahead));
    let me = Holder { board: "demo".into(), name: name.clone() };
    assert!(check(&mut n, &me, "note_verify", &Scope::any()).unwrap());

    let kept_back = second.issue_params("demo", "g-00000000000000c4", &Spec::session(&name, "task_authority"));
    ok(req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&second.fingerprint(), &device)));
    assert!(!check(&mut n, &me, "note_verify", &Scope::any()).unwrap());
    std::thread::sleep(std::time::Duration::from_millis(5));
    let again = req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device))));
    assert_eq!(code(&again), "denied", "{again}");
    assert!(again["error"]["message"].as_str().unwrap().contains("removed"), "{again}");
    assert_eq!(code(&req(&mut n, "grant_issue", "demo", &token, kept_back)), "denied", "a grant signed before the removal and sent after it is not taken");
    assert!(!check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "what it signed before it was removed counts for nothing");
    let shown = ok(req(&mut n, "grants", "demo", &token, json!({})));
    assert_eq!(shown["grants"].as_array().unwrap().len(), 2, "the one signed for now and the one signed for later");
    for grant in shown["grants"].as_array().unwrap() {
        assert_eq!((grant["status"].clone(), grant["why"].as_str().unwrap().contains("removed")), (json!("ignored"), true), "{shown}");
    }

    let other = third();
    ok(req(&mut n, "person_enroll", "", &token, other.enrolment("a new laptop", Some((&first, &device)))));
    ok(other.issue(&mut caller(&mut n), "demo", &token, "g-00000000000000c2", &Spec::session(&name, "sync_now")));
    assert!(check(&mut n, &me, "sync_now", &Scope::any()).unwrap(), "a new key's grants count");
    drop(n);
    let mut n = node(dir.path());
    assert!(!check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "the removal outlasts a restart");
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device))))), "denied", "and so does the refusal to enrol the key again");
    assert!(check(&mut n, &me, "sync_now", &Scope::any()).unwrap());
}

#[test]
fn an_approval_is_for_one_node_and_is_used_once() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    enrolled(dir.path(), &mut caller(&mut n), &token);
    let (first, second, other) = (person(), agent(), third());
    let device = n.cert.fingerprint();
    let approved = second.enrolment("a laptop", Some((&first, &device)));
    ok(req(&mut n, "person_enroll", "", &token, approved.clone()));
    ok(req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&second.fingerprint(), &device)));
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, approved.clone())), "denied", "a captured approval does not enrol the removed key again");
    assert_eq!(held(&mut n), [first.fingerprint()]);

    let other_node = other.enrolment("a tablet", Some((&first, &"ab".repeat(32))));
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, other_node)), "denied", "an approval for another node enrols nothing here");
    let mut stale = other.enrolment("a tablet", Some((&first, &device)));
    stale["approved_ms"] = json!(now_ms() - 10 * 60_000);
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, stale)), "invalid", "a stale approval");
    for nonce in ["short", "has spaces in it 0123456789", "../../../../../../../../../../etc"] {
        let mut p = other.enrolment("a tablet", Some((&first, &device)));
        p["nonce"] = json!(nonce);
        assert_eq!(code(&req(&mut n, "person_enroll", "", &token, p)), "invalid", "{nonce}");
    }
    let mut unsigned = other.enrolment("a tablet", Some((&first, &device)));
    unsigned.as_object_mut().unwrap().remove("nonce");
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, unsigned)), "invalid", "an approval without a nonce");
    assert_eq!(held(&mut n), [first.fingerprint()]);
    let fresh = other.enrolment("a tablet", Some((&first, &device)));
    ok(req(&mut n, "person_enroll", "", &token, fresh.clone()));
    assert_eq!(held(&mut n).len(), 2, "a fresh approval of a new key enrols it");
    ok(req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&other.fingerprint(), &device)));
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, fresh)), "denied", "and is used once");
}

#[test]
fn a_revocation_a_removed_key_signed_stays_void_and_a_new_key_ends_a_grant_as_before() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    enrolled(dir.path(), &mut caller(&mut n), &token);
    let (first, second, other) = (person(), agent(), third());
    let device = n.cert.fingerprint();
    ok(req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device)))));
    ok(first.issue(&mut caller(&mut n), "demo", &token, GID, &Spec::session(&name, "note_verify")));
    let me = Holder { board: "demo".into(), name: name.clone() };
    assert!(check(&mut n, &me, "note_verify", &Scope::any()).unwrap());
    ok(second.revoke(&mut caller(&mut n), "demo", &token, GID));
    assert!(!check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "the second key ended the grant");

    ok(req(&mut n, "person_unenroll", "", &token, first.unenroll_params(&second.fingerprint(), &device)));
    assert!(check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "a key that is not enrolled ends nothing");
    std::thread::sleep(std::time::Duration::from_millis(5));
    assert_eq!(code(&req(&mut n, "person_enroll", "", &token, second.enrolment("a laptop", Some((&first, &device))))), "denied");
    assert!(check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "so what it ended before it was removed stays unended");
    ok(req(&mut n, "person_enroll", "", &token, other.enrolment("a new laptop", Some((&first, &device)))));
    ok(other.revoke(&mut caller(&mut n), "demo", &token, GID));
    assert!(!check(&mut n, &me, "note_verify", &Scope::any()).unwrap(), "and a new key ends it");
}
