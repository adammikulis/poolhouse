//! Governed pools across real nodes: founding, joining, the acts exchange, removal reaching a third device, a malicious member, two
//! people signing one act on two devices, a delegate, and the plain refusals.

mod kit;

use kit::gov::*;
use kit::govnode::*;
use kit::netkit::*;
use poolhouse_node::cert::{self, Identity};
use poolhouse_node::error::Error;
use poolhouse_node::gov::act::{Act, ActSig, SigKind};
use poolhouse_node::gov::keys::Strength;
use poolhouse_node::gov::signer::RigSigner;
use poolhouse_node::net::{Net, NetConfig};
use poolhouse_node::node::Node;
use poolhouse_node::peer::PeerClient;
use poolhouse_node::poolops::wall_ms;
use poolhouse_node::projectid::project_key;
use serde_json::{json, Value};


fn identity(seed: u8) -> Identity {
    let dir = tempfile::tempdir().unwrap();
    cert::load_or_create(dir.path(), &kit::key(seed)).unwrap()
}


fn status_of(dev: &Dev, id: &str) -> String {
    format!("{:?}", dev.node.lock().unwrap().members.gov().unwrap().status(id))
}


// -- founding and joining ------------------------------------------------------------------------------------------------

#[test]
fn a_device_founds_a_pool_whose_id_is_the_hash_of_its_charter_and_is_its_first_member() {
    let a = device();
    let before = a.pool();
    let id = found(&a, person(ANN, "ann"), "secure");
    assert_eq!(id.len(), 32);
    assert_ne!(id, before);
    let view = a.status();
    assert_eq!((view["pool"].as_str(), view["policy"].as_str()), (Some(id.as_str()), Some("secure")));
    assert_eq!(view["governance"]["mode"], "governed");
    assert_eq!(view["governance"]["epoch"], 1);
    assert_eq!(view["governance"]["admins"].as_array().unwrap().len(), 1);
    assert_eq!(view["governance"]["weak_keys_only"], false);
    assert!(a.is_member(&a.fp()));
    let again = a.call("pool_create", "", &a.token, json!({"title": "other", "policy": "secure"}));
    assert_eq!(again["ok"], false);
    assert!(again["error"]["say"].as_str().unwrap().contains("already runs a pool"), "{again}");
}

#[test]
fn a_device_joins_a_governed_pool_with_a_code_and_the_admin_signs_the_admit() {
    let (a, b) = (device(), device());
    let id = found(&a, person(ANN, "ann"), "secure");
    a.join_with_code(&b);
    assert_eq!(b.pool(), id);
    assert!(a.is_member(&b.fp()) && b.is_member(&a.fp()), "each lists the other");
    assert_eq!(a.member(&b.fp()).unwrap()["by"], "join-code");
    let n = b.node.lock().unwrap();
    assert!(n.members.gov().is_some(), "the joiner holds the pool as a governed pool");
    assert!(!n.dir.join("pool.json").exists(), "the pool of one it had was never written, so nothing is retired");
    assert!(!n.dir.join("pools").join("retired").exists());
}

#[test]
fn a_device_that_cannot_sign_does_not_open_a_window_and_says_so_in_one_sentence() {
    let (a, b) = (device(), device());
    found(&a, person(ANN, "ann"), "secure");
    a.join_with_code(&b);
    let reply = b.call("add_device", "", &b.token, json!({}));
    assert_eq!(reply["ok"], false);
    let say = reply["error"]["say"].as_str().unwrap();
    assert!(say.len() <= 160 && say.contains("OK"), "{say}");
    assert_eq!((reply["error"]["needs"].as_str(), reply["error"]["then"].as_str()), (Some("approval"), Some("ask-owner")));
}

#[test]
fn the_join_policy_has_no_method_that_changes_it_in_place() {
    let a = device();
    found(&a, person(ANN, "ann"), "secure");
    let reply = a.call("set_join_policy", "", &a.token, json!({"policy": "open"}));
    assert_eq!((reply["ok"].clone(), reply["error"]["code"].clone()), (json!(false), json!("invalid")), "{reply}");
    assert_eq!(a.status()["policy"], "secure");
}

#[test]
fn a_fresh_node_is_the_unfounded_pool_of_one_and_needs_no_signer_to_exist() {
    let a = device();
    let view = a.status();
    assert_eq!(view["governance"]["mode"], "unfounded");
    assert_eq!(view["pool"], "", "no charter, so no id");
    let members = view["members"].as_array().unwrap();
    assert_eq!((members.len(), members[0]["fingerprint"].clone(), members[0]["self"].clone()), (1, json!(a.fp()), json!(true)), "the device is its own only member");
    assert!(!a.dir.path().join("pool.json").exists() && !a.dir.path().join("pools").exists(), "nothing is written until a pool is founded");
    let admit = a.call("add_device", "", &a.token, json!({}));
    assert_eq!(admit["error"]["say"], "This device is not in a pool yet; make one, or join one.", "it admits nobody");
    let propose = a.call("pool_propose", "", &a.token, json!({"kind": "remove", "body": {"fingerprint": "ab".repeat(32)}}));
    assert_eq!(propose["error"]["say"], "This device is not in a pool yet; make one, or join one.");
    let reopened = Node::open(a.dir.path());
    assert!(reopened.is_ok(), "a fresh state directory opens again as it was");
}

#[test]
fn a_pool_json_of_the_old_format_or_of_no_format_is_set_aside_and_the_node_starts_unfounded_with_its_boards() {
    let old_pool = br#"{"v":1,"pool":"0123456789abcdef","policy":"secure","devices":[]}"#;
    for (i, content) in [old_pool.as_slice(), b"\x00\xff not json at all {{{".as_slice()].into_iter().enumerate() {
        let dir = tempfile::tempdir().unwrap();
        let token = {
            let mut n = kit::node(dir.path());
            let (name, token) = kit::session(&mut n, "demo", "a");
            kit::allow(&mut n, "demo", &token, &name, &[]);
            kit::ok(kit::post(&mut n, "demo", &token, "kept across the old pool"));
            token
        };
        std::fs::write(dir.path().join("pool.json"), content).unwrap();
        std::fs::write(dir.path().join("pool.json.old-format"), b"an earlier one").unwrap();
        let mut n = Node::open(dir.path()).expect("the node is up, not refused");
        assert!(!dir.path().join("pool.json").exists());
        assert_eq!(std::fs::read(dir.path().join("pool.json.old-format.1")).unwrap(), content, "case {i}: renamed, never deleted, and the earlier file is untouched");
        assert_eq!(std::fs::read(dir.path().join("pool.json.old-format")).unwrap(), b"an earlier one");
        let entries = kit::ok(kit::req(&mut n, "read", "demo", &token, json!({"limit": 100})));
        assert_eq!(kit::texts(&entries["entries"]), vec!["kept across the old pool"], "the board is served as before");
        let view = kit::ok(kit::req(&mut n, "pool_status", "", "", json!({})));
        assert_eq!((view["governance"]["mode"].as_str(), view["pool"].as_str()), (Some("unfounded"), Some("")));
        assert_eq!(view["governance"]["old_format_file"], "pool.json.old-format.1");
        assert!(view["governance"]["say"].as_str().unwrap().contains("old-format pool: it now starts unfounded; create a new pool"));
        drop(n);
        assert!(Node::open(dir.path()).is_ok(), "and it opens again as it was");
    }
}

// -- convergence and removal ---------------------------------------------------------------------------------------------

#[test]
fn three_nodes_converge_on_the_same_members_and_a_removal_reaches_the_third_through_the_second() {
    let (a, b, c) = pool_of_three();
    assert!(b.is_member(&c.fp()) && c.is_member(&b.fp()), "the admits are acts: b and c learn of one another through a");
    let ids: Vec<String> = [&a, &b, &c].iter().map(|d| d.pool()).collect();
    assert!(ids.iter().all(|i| *i == ids[0]));
    let mut b_to_c = link(&b, &c);
    assert!(b_to_c.call(&json!({"op": "boards"})).is_ok(), "two members that never joined one another talk");
    a.ok("member_remove", "", &a.token, json!({"fingerprint": c.fp()}));
    assert_eq!(a.member(&c.fp()).unwrap()["status"], "revoked");
    sync(&b);
    assert_eq!(b.member(&c.fp()).unwrap()["status"], "revoked", "the removal is an act: b takes it from a, signed by a's admin");
    let refused = PeerClient::connect(&c.net.me, b.addr(), Some(&b.fp())).and_then(|mut l| l.call(&json!({"op": "boards"})));
    assert!(refused.is_err(), "c is refused at b");
    assert!(PeerClient::connect(&c.net.me, a.addr(), Some(&a.fp())).and_then(|mut l| l.call(&json!({"op": "boards"}))).is_err());
}

#[test]
fn a_removal_signed_on_one_node_is_the_acts_of_a_pool_and_survives_a_restart() {
    let (a, b, _) = pool_of_three();
    let victim = identity(9);
    a.node.lock().unwrap().enrol_device(&victim.der, "ghost", "join-code").unwrap();
    a.ok("member_remove", "", &a.token, json!({"fingerprint": victim.fingerprint()}));
    sync(&b);
    let dir = a.dir.path().to_path_buf();
    let reopened = poolhouse_node::node::Node::open(&dir).unwrap();
    assert_eq!(reopened.members.get(&victim.fingerprint()).map(|d| d.status), Some(poolhouse_node::membership::Status::Revoked));
    assert!(reopened.members.gov().is_some());
}

#[test]
fn two_admins_removing_different_devices_while_apart_end_with_both_removed_on_both() {
    let (a, b, c) = pool_of_three();
    let extra = (identity(40), identity(41));
    for (d, n) in [(&extra.0, "x"), (&extra.1, "y")] {
        a.node.lock().unwrap().enrol_device(&d.der, n, "join-code").unwrap();
    }
    sync(&b);
    sync(&c);
    // ben becomes a second admin of the pool and signs on b
    let entry = json!(entry(strong(BEN, "ben"), 2, &a.pool()));
    a.ok("pool_propose", "", &a.token, json!({"kind": "admin_add", "body": {"entry": entry}}));
    for d in [&a, &b, &c] {
        sync(d);
        pass(d, 25 * HOUR);
    }
    b.node.lock().unwrap().set_signer(person(BEN, "ben"));
    a.ok("pool_propose", "", &a.token, json!({"kind": "remove", "body": {"fingerprint": extra.0.fingerprint()}}));
    b.ok("pool_propose", "", &b.token, json!({"kind": "remove", "body": {"fingerprint": extra.1.fingerprint()}}));
    sync(&a);
    sync(&b);
    sync(&a);
    for d in [&a, &b] {
        for e in [&extra.0, &extra.1] {
            assert_eq!(d.member(&e.fingerprint()).unwrap()["status"], "revoked");
        }
    }
}

// -- a malicious member ----------------------------------------------------------------------------------------------------

#[test]
fn a_plain_member_cannot_remove_the_founder_enrol_a_device_or_move_the_policy_in_a_governed_pool() {
    let (a, b, c) = pool_of_three();
    let mut to_c = link(&b, &c);
    let pool = c.pool();
    // 1. the old rows and the old policy: ignored
    let rows = vec![json!({"fingerprint": a.fp(), "cert": "", "name": "", "status": "revoked", "at": 1, "by": b.fp()})];
    let _ = to_c.call(&json!({"op": "members", "pool": pool, "rows": rows, "policy": {"policy": "open", "at": u64::MAX, "by": "b"}}));
    assert!(c.is_member(&a.fp()));
    assert_eq!(c.status()["policy"], "secure");
    // 2. a removal of the founder signed by b's device key
    let founder_remove = {
        let n = b.node.lock().unwrap();
        let mut act = n.members.gov().unwrap().new_act("remove", json!({"fingerprint": a.fp()}), wall_ms()).unwrap();
        let sig = {
            use ed25519_dalek::Signer;
            poolhouse_node::fsutil::hex(&n.key.sign(&act.sign_bytes()).to_bytes())
        };
        act.add_signature(ActSig { kind: SigKind::Device, key: b.fp(), sig });
        act
    };
    // 3. an admit of a stranger signed by a key made up
    let stranger = identity(31);
    let mut self_admit = Act::new(&pool, 1, "admit", admit_body(&stranger, "stranger", "join-code", ""), wall_ms(), &first_hash(&c)).unwrap();
    sign(&mut self_admit, 77);
    // 4. a second founding act
    let other_charter = charter(&[strong(77, "mallory")], poolhouse_node::membership::Policy::Secure, poolhouse_node::gov::charter::Unit::Person);
    let other_found = found_act(&other_charter);
    let reply = to_c.call(&json!({"op": "acts", "pool": pool, "push": [founder_remove, self_admit, other_found]})).unwrap();
    assert_eq!(reply["refused"].as_array().unwrap().len(), 3, "{reply}");
    assert!(c.is_member(&a.fp()) && c.member(&stranger.fingerprint()).is_none());
    let refusals = c.node.lock().unwrap().members.gov().unwrap().refusals();
    assert!(refusals.iter().any(|r| r.why.contains("device")) && refusals.iter().any(|r| r.why.contains("not an admin")) && refusals.iter().any(|r| r.why.contains("another pool")), "{refusals:?}");
    let denied = PeerClient::connect(&stranger, c.addr(), Some(&c.fp())).and_then(|mut l| l.call(&json!({"op": "boards"})));
    assert!(matches!(denied, Err(Error::Denied(_))));
}

fn first_hash(dev: &Dev) -> String {
    dev.node.lock().unwrap().members.gov().unwrap().context().1
}

#[test]
fn a_member_cannot_replay_a_stale_act_or_flood_the_acts_op() {
    let (a, b, _) = pool_of_three();
    let pool = a.pool();
    let mut to_a = link(&b, &a);
    let tokens = |n: usize| -> Vec<String> { (0..n).map(|i| format!("{i:080x}")).collect() };
    let too_many = to_a.call(&json!({"op": "acts", "pool": pool, "have": tokens(4097)}));
    assert!(matches!(too_many, Err(Error::Invalid(_))), "{too_many:?}");
    assert!(to_a.call(&json!({"op": "acts", "pool": pool, "have": tokens(4096)})).is_ok(), "4,096 is what a request may name");
    assert!(matches!(to_a.call(&json!({"op": "acts", "pool": pool, "have": ["not a token"]})), Err(Error::Invalid(_))));
    let pushes: Vec<Value> = (0..300).map(|n| json!({"v": 1, "n": n})).collect();
    assert!(matches!(to_a.call(&json!({"op": "acts", "pool": pool, "push": pushes})), Err(Error::Invalid(_))));
    assert!(matches!(to_a.call(&json!({"op": "acts", "pool": pool, "page": 64})), Err(Error::Invalid(_))));
    assert!(matches!(to_a.call(&json!({"op": "acts", "pool": "0".repeat(32)})), Err(Error::Denied(_))));
    let first = to_a.call(&json!({"op": "acts", "pool": pool, "have": []})).unwrap();
    let again = to_a.call(&json!({"op": "acts", "pool": pool, "push": first["acts"]})).unwrap();
    assert_eq!(again["accepted"], 0, "every act it replays is already held");
    let stranger = identity(33);
    let denied = PeerClient::connect(&stranger, a.addr(), Some(&a.fp())).and_then(|mut l| l.call(&json!({"op": "acts", "pool": pool})));
    assert!(matches!(denied, Err(Error::Denied(_))), "a device that is not a member is answered no act");
}

// -- two people --------------------------------------------------------------------------------------------------------

#[test]
fn a_second_person_cosigns_on_another_device_and_the_act_takes_effect_everywhere() {
    let (a, b, c) = pool_of_three();
    let entry = json!(entry(strong(BEN, "ben"), 2, &a.pool()));
    let added = a.ok("pool_propose", "", &a.token, json!({"kind": "admin_add", "body": {"entry": entry}}));
    assert_eq!(added["status"], "pending", "{added}");
    for d in [&a, &b, &c] {
        sync(d);
    }
    assert_eq!(status_of(&b, added["act"].as_str().unwrap()), "Pending", "b holds it and counts its own hold");
    for d in [&a, &b, &c] {
        pass(d, 25 * HOUR);
    }
    assert_eq!(a.status()["governance"]["admins"].as_array().unwrap().len(), 2);
    assert_eq!(b.status()["governance"]["epoch"], 2);
    b.node.lock().unwrap().set_signer(person(BEN, "ben"));
    let set = a.ok("pool_propose", "", &a.token, json!({"kind": "set_holds", "body": {"admin_add_h": 48}}));
    assert_eq!(set["status"], "needs-signers", "two people now govern the pool, and one signed: {set}");
    sync(&b);
    let cosigned = b.ok("pool_cosign", "", &b.token, json!({"act": set["act"]}));
    assert_ne!(cosigned["status"], "needs-signers", "{cosigned}");
    sync(&b);
    assert_eq!(a.status()["governance"]["holds"]["admin_add_h"], 48, "b pushed the signature to a, which does not dial b");
    for d in [&a, &c] {
        sync(d);
    }
    for d in [&a, &b, &c] {
        assert_eq!(d.status()["governance"]["holds"]["admin_add_h"], 48, "the longer hold is a strengthening: it takes effect at once, on every device");
    }
}

#[test]
fn an_act_a_person_signed_with_a_key_of_their_own_is_submitted_without_any_signer_on_the_node() {
    let (a, b, _) = pool_of_three();
    let victim = identity(50);
    a.node.lock().unwrap().enrol_device(&victim.der, "ghost", "join-code").unwrap();
    let act = {
        let n = a.node.lock().unwrap();
        let mut act = n.members.gov().unwrap().new_act("remove", json!({"fingerprint": victim.fingerprint()}), wall_ms()).unwrap();
        let sig = RigSigner::sign_as(ANN, &act.sign_bytes());
        act.add_signature(ActSig { kind: SigKind::Admin, key: public(ANN), sig });
        act
    };
    b.node.lock().unwrap().members.gov().unwrap();
    sync(&b);
    let reply = b.ok("pool_submit", "", &b.token, json!({"act": act}));
    assert_eq!(reply["status"], "effective", "{reply}");
    assert_eq!(b.member(&victim.fingerprint()).map(|m| m["status"].clone()), Some(json!("revoked")));
}

fn beacon_config(beacon_cfg: poolhouse_node::beacon::BeaconConfig, project: Option<&str>) -> NetConfig {
    NetConfig { listen: "127.0.0.1:0".parse().unwrap(), beacon: Some(beacon_cfg), sync_every: None, project: project.map(project_key) }
}

#[test]
fn a_device_in_no_pool_that_hears_the_beacon_of_an_open_governed_pool_of_its_project_joins_it() {
    let (pa, pb) = (udp_port(), udp_port());
    let a = device_with(beacon_config(beacon(pa, &[pb]), None));
    let id = found_for(&a, person(ANN, "ann"), "open", &project_key("poolhouse"));
    let dir = tempfile::tempdir().unwrap();
    let node = std::sync::Arc::new(std::sync::Mutex::new(Node::open(dir.path()).unwrap()));
    let net = Net::start(node.clone(), beacon_config(beacon(pb, &[pa]), Some("poolhouse"))).unwrap();
    let mut b = Dev { dir, node, net, token: String::new() };
    b.token = b.session("demo", "main").1;
    eventually("b to join the open governed pool it hears", 15, || a.is_member(&b.fp()) && b.is_member(&a.fp()));
    assert_eq!(b.pool(), id);
    assert_eq!(a.member(&b.fp()).unwrap()["by"], "open", "admitted by the member's own device signature");
    assert!(b.node.lock().unwrap().members.gov().is_some());
}

#[test]
fn a_pool_is_founded_drafted_and_signed_by_a_key_outside_the_node_with_no_signer_on_it() {
    let (a, b) = (device(), device());
    let charter = charter(&[strong(ANN, "ann")], poolhouse_node::membership::Policy::Secure, poolhouse_node::gov::charter::Unit::Person);
    let founded = a.ok("pool_found", "", &a.token, json!({"act": found_act(&charter)}));
    assert_eq!(founded["pool"], charter.pool_id().as_str());
    assert!(!a.is_member(&a.fp()), "the pool exists and the device is not yet in it");
    let draft = a.ok("pool_draft", "", &a.token, json!({"kind": "admit", "body": admit_body(&a.net.me, "this-device", "found", "")}));
    let mut admit: Act = serde_json::from_value(draft["act"].clone()).unwrap();
    assert_eq!(draft["sign_bytes"], poolhouse_node::fsutil::hex(&admit.sign_bytes()).as_str(), "the bytes a person's key signs are the act's");
    assert!(draft["summary"].as_str().unwrap().contains("this-device"));
    sign(&mut admit, ANN);
    assert_eq!(a.ok("pool_submit", "", &a.token, json!({"act": admit}))["status"], "effective");
    assert!(a.is_member(&a.fp()));
    let again = a.call("pool_found", "", &a.token, json!({"act": found_act(&charter)}));
    assert_eq!(again["ok"], false, "a device that runs a pool does not found a second");
    let refused = a.call("pool_found", "", &a.token, json!({"act": {"v": 1}}));
    assert_eq!(refused["ok"], false);
    let _ = b;
}

#[test]
fn a_window_opened_with_no_signer_on_the_node_admits_only_the_device_an_admin_already_signed_for() {
    let (a, b, c) = (device(), device(), device());
    let charter = charter(&[strong(ANN, "ann")], poolhouse_node::membership::Policy::Secure, poolhouse_node::gov::charter::Unit::Person);
    a.ok("pool_found", "", &a.token, json!({"act": found_act(&charter)}));
    for dev in [&a, &b] {
        let draft = a.ok("pool_draft", "", &a.token, json!({"kind": "admit", "body": admit_body(&dev.net.me, "dev", "join-code", "")}));
        let mut act: Act = serde_json::from_value(draft["act"].clone()).unwrap();
        if dev.fp() == a.fp() {
            act.body["how"] = json!("found");
        }
        sign(&mut act, ANN);
        a.ok("pool_submit", "", &a.token, json!({"act": act}));
    }
    let closed = a.call("add_device", "", &a.token, json!({}));
    assert_eq!(closed["ok"], false, "with no signer a window opens only for what was signed before: {closed}");
    let code = a.ok("add_device", "", &a.token, json!({"presigned": true}))["code"].as_str().unwrap().to_string();
    let stranger = c.call("join_pool", "", &c.token, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": code}));
    assert_eq!(stranger["ok"], false, "a device nobody signed an admit for is not let in by an open window: {stranger}");
    assert!(!a.is_member(&c.fp()));
    let code = a.ok("add_device", "", &a.token, json!({"presigned": true}))["code"].as_str().unwrap().to_string();
    b.ok("join_pool", "", &b.token, json!({"host": "127.0.0.1", "port": a.net.port, "passphrase": code}));
    assert!(a.is_member(&b.fp()) && b.is_member(&a.fp()));
}

#[test]
fn devices_that_join_an_open_pool_do_not_use_up_the_asks_the_owner_is_spared() {
    let a = device();
    found(&a, person(ANN, "ann"), "open");
    let victim = identity(60);
    a.node.lock().unwrap().enrol_device(&victim.der, "ghost", "join-code").ok();
    for seed in 70..86 {
        let guest = identity(seed);
        let _ = a.node.lock().unwrap().enrol_device(&guest.der, "guest", "open");
    }
    let removed = a.call("member_remove", "", &a.token, json!({"fingerprint": victim.fingerprint()}));
    assert_eq!(removed["ok"], true, "sixteen devices asked to join and the owner's removal was refused: {removed}");
}

#[test]
fn one_address_and_the_whole_pool_may_only_ask_so_often_to_join_an_open_pool_without_a_code() {
    let a = device();
    found(&a, person(ANN, "ann"), "open");
    let guests: Vec<Dev> = (0..11).map(|_| device()).collect();
    let ask = |g: &Dev| link(g, &a).call(&json!({"op": "join_open", "name": "guest", "port": g.net.port}));
    let outcomes: Vec<bool> = guests.iter().map(|g| ask(g).is_ok()).collect();
    assert!(outcomes[..5].iter().all(|ok| *ok), "{outcomes:?}");
    let refused = ask(&guests[10]).unwrap_err().to_string();
    assert!(refused.contains("as fast as it may"), "{refused}");
    assert!(outcomes[5..].iter().all(|ok| !ok), "{outcomes:?}");
}

// -- the delegate ----------------------------------------------------------------------------------------------------------

#[test]
fn a_delegate_needs_a_keystore_bound_device_key_and_each_of_its_admits_is_noted_on_the_pool_board() {
    let (a, b, _) = pool_of_three();
    assert_eq!(b.call("add_device", "", &b.token, json!({}))["ok"], false, "a member that is not a delegate opens no window");
    a.ok("pool_propose", "", &a.token, json!({"kind": "delegate", "body": {"fingerprint": b.fp()}}));
    sync(&b);
    assert_eq!(b.call("add_device", "", &b.token, json!({}))["ok"], false, "a device key held in a file makes no delegate: a copy of its state signs nothing");
    b.node.lock().unwrap().rig_bind_device_key();
    let guest = device();
    b.join_with_code(&guest);
    assert!(b.is_member(&guest.fp()) && guest.is_member(&b.fp()));
    sync(&a);
    assert!(a.is_member(&guest.fp()), "a takes the admit the delegate signed");
    let board = a.node.lock().unwrap().view("pool").unwrap();
    assert!(board.iter().any(|e| e.fields.get("event").and_then(Value::as_str) == Some("delegate_admit")), "the notice the phone shows with [Remove]");
}

#[test]
fn a_cloned_state_directory_has_no_keystore_bound_key_and_signs_no_delegate_admit() {
    let (a, b, _) = pool_of_three();
    a.ok("pool_propose", "", &a.token, json!({"kind": "delegate", "body": {"fingerprint": b.fp()}}));
    sync(&b);
    let copy = tempfile::tempdir().unwrap();
    copy_dir(b.dir.path(), copy.path());
    let clone = poolhouse_node::node::Node::open(copy.path()).unwrap();
    assert!(clone.can_admit().is_err(), "the copy holds the files, not a keystore");
}

fn copy_dir(from: &std::path::Path, to: &std::path::Path) {
    for entry in std::fs::read_dir(from).unwrap().flatten() {
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            std::fs::create_dir_all(&target).unwrap();
            copy_dir(&entry.path(), &target);
        } else if entry.file_name() != "node.sock" {
            let _ = std::fs::copy(entry.path(), &target);
        }
    }
}

#[test]
fn an_open_pool_admits_a_device_on_the_segment_with_the_members_own_signature() {
    let (a, b) = (device(), device());
    found(&a, person(ANN, "ann"), "open");
    let reply = b.call("join_pool", "", &b.token, json!({"host": "127.0.0.1", "port": a.net.port, "fingerprint": a.fp()}));
    assert_eq!(reply["ok"], true, "{reply}");
    assert!(a.is_member(&b.fp()) && b.is_member(&a.fp()));
    assert_eq!(a.member(&b.fp()).unwrap()["by"], "open");
    assert_eq!(b.pool(), a.pool());
    let view = a.node.lock().unwrap();
    let by = view.members.gov().unwrap().state().roster.signed_by.get(&b.fp()).cloned().unwrap();
    assert!(by.starts_with("member "), "the admit is signed by the member's own device key: {by}");
}

#[test]
fn a_no_from_the_person_is_respected_for_ten_minutes_and_each_refusal_is_one_plain_sentence() {
    let (a, b) = (device(), device());
    let ann = person(ANN, "ann");
    found(&a, ann.clone(), "secure");
    a.join_with_code(&b);
    ann.decline(true);
    let no = a.call("member_remove", "", &a.token, json!({"fingerprint": b.fp()}));
    assert_eq!(no["ok"], false);
    assert_eq!((no["error"]["needs"].as_str(), no["error"]["then"].as_str()), (Some("approval"), Some("stop")), "{no}");
    ann.decline(false);
    let again = a.call("member_remove", "", &a.token, json!({"fingerprint": b.fp()}));
    assert_eq!((again["error"]["needs"].as_str(), again["error"]["then"].as_str()), (Some("time"), Some("wait")), "{again}");
    for reply in [&no, &again] {
        assert!(reply["error"]["say"].as_str().unwrap().len() <= 160);
    }
    assert!(a.is_member(&b.fp()), "nothing was signed");
}

#[test]
fn a_person_is_asked_at_most_twelve_times_in_ten_minutes() {
    let a = device();
    found(&a, person(ANN, "ann"), "secure");
    let remove = |n: usize| a.call("member_remove", "", &a.token, json!({"fingerprint": format!("{n:064x}")}));
    for n in 0..11 {
        assert_eq!(remove(n)["ok"], true, "ask {n}");
    }
    let refused = remove(11);
    assert_eq!(refused["ok"], false, "{refused}");
    assert_eq!((refused["error"]["needs"].as_str(), refused["error"]["then"].as_str()), (Some("time"), Some("wait")));
    assert!(refused["error"]["say"].as_str().unwrap().len() <= 160);
}

#[test]
fn a_pool_is_not_founded_on_weak_keys_alone_and_nothing_is_left_half_made() {
    let a = device();
    let before = a.pool();
    a.node.lock().unwrap().set_signer(std::sync::Arc::new(RigSigner::new().with_key(ANN, "ann", Strength::Weak)));
    let denied = a.call("pool_create", "", &a.token, json!({"title": "home"}));
    assert_eq!(denied["ok"], false);
    assert!(denied["error"]["say"].as_str().unwrap().contains("asks for your presence"), "{denied}");
    assert_eq!(a.pool(), before, "it is still in no pool");
    assert!(!a.dir.path().join("pools").exists() || std::fs::read_dir(a.dir.path().join("pools")).unwrap().count() == 0);
}

#[test]
fn a_member_that_leaves_by_its_own_signature_is_no_longer_a_member_anywhere() {
    let (a, b, c) = pool_of_three();
    let left = b.ok("pool_leave", "", &b.token, json!({}));
    assert_eq!(left["status"], "effective", "{left}");
    sync(&a);
    sync(&c);
    for d in [&a, &c] {
        assert_eq!(d.member(&b.fp()).unwrap()["status"], "left");
    }
    let refused = PeerClient::connect(&b.net.me, a.addr(), Some(&a.fp())).and_then(|mut l| l.call(&json!({"op": "boards"})));
    assert!(matches!(refused, Err(Error::Denied(_))), "a device that left asks for nothing");
}
