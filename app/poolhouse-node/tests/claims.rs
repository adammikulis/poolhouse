mod kit;

use kit::*;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use std::process::{Command, Stdio};
use tempfile::tempdir;

fn claim(n: &mut Node, token: &str, kind: &str, key: &str, extra: Value) -> Value {
    let mut params = json!({"kind": kind, "key": key});
    for (k, v) in extra.as_object().unwrap() {
        params[k] = v.clone();
    }
    req(n, "claim", "demo", token, params)
}

fn listed(n: &mut Node, token: &str) -> Vec<Value> {
    ok(req(n, "claims", "demo", token, json!({})))["claims"].as_array().unwrap().clone()
}

#[test]
fn every_kind_is_a_claim_with_its_owner_shown_and_a_wrong_kind_is_refused() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, token) = session(&mut n, "demo", "a");
    for (kind, key) in [("worktree", "/work/a"), ("branch", "feat"), ("area", "/repo/src"), ("port", "8080"), ("server", "broker"), ("install", "/venv")] {
        let got = ok(claim(&mut n, &token, kind, key, json!({"ttl_s": 600})));
        assert_eq!((got["claim"]["kind"].clone(), got["claim"]["key"].clone(), got["claim"]["owner"].clone()), (json!(kind), json!(key), json!(name)), "{kind}");
        assert!(got["claim"]["expires_in_s"].as_u64().unwrap() <= 600);
    }
    assert_eq!(listed(&mut n, &token).len(), 6);
    assert_eq!(ok(req(&mut n, "claims", "demo", &token, json!({"kind": "port"})))["claims"].as_array().unwrap().len(), 1);
    for (kind, key) in [("file", "/x"), ("port", "99999"), ("worktree", "relative/path"), ("branch", "../x")] {
        assert_eq!(code(&claim(&mut n, &token, kind, key, json!({}))), "invalid", "{kind} {key}");
    }
}

#[test]
fn a_second_holder_is_refused_naming_the_first_and_a_nested_path_conflicts() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((first, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    ok(claim(&mut n, &at, "worktree", "/work/repo", json!({})));
    for path in ["/work/repo", "/work/repo/sub", "/work"] {
        let refused = claim(&mut n, &bt, "worktree", path, json!({}));
        assert_eq!(code(&refused), "denied", "{path}");
        assert!(refused["error"]["message"].as_str().unwrap().contains(&first), "{refused}");
    }
    ok(claim(&mut n, &bt, "worktree", "/work/other", json!({})));
    assert_eq!(listed(&mut n, &bt).len(), 2, "claims show every holder");
}

#[test]
fn a_claim_whose_process_died_or_whose_time_ran_out_is_gone_and_a_heartbeat_extends_it() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    let mut child = Command::new("sleep").arg("120").stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap();
    ok(claim(&mut n, &at, "port", "9001", json!({"pid": child.id()})));
    assert_eq!(code(&claim(&mut n, &bt, "port", "9001", json!({}))), "denied");
    child.kill().unwrap();
    child.wait().unwrap();
    assert_eq!(ok(claim(&mut n, &bt, "port", "9001", json!({})))["changed"], true, "the dead holder's claim was dropped");
    ok(claim(&mut n, &bt, "server", "short", json!({"ttl_s": 1})));
    let lease = listed(&mut n, &bt).into_iter().find(|c| c["key"] == "short").unwrap()["lease"].clone();
    let renewed = ok(req(&mut n, "lease_renew", "demo", &bt, json!({"id": lease, "ttl_s": 600})));
    assert!(renewed["expires_ms"].as_u64().unwrap() > renewed["granted_ms"].as_u64().unwrap() + 100_000);
    ok(claim(&mut n, &bt, "server", "gone", json!({"ttl_s": 1})));
    std::thread::sleep(std::time::Duration::from_millis(1200));
    let keys: Vec<Value> = listed(&mut n, &bt).iter().map(|c| c["key"].clone()).collect();
    assert!(!keys.contains(&json!("gone")) && keys.contains(&json!("short")), "{keys:?}");
}

#[test]
fn only_the_holder_releases_and_a_claim_of_one_board_is_not_listed_on_another() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((_, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    let (_, ot) = session(&mut n, "other", "o");
    ok(claim(&mut n, &at, "area", "/repo/a", json!({})));
    assert_eq!(code(&req(&mut n, "release", "demo", &bt, json!({"kind": "area", "key": "/repo/a"}))), "denied");
    assert_eq!(ok(req(&mut n, "release", "demo", &bt, json!({"kind": "area", "key": "/repo/none"})))["released"], false);
    assert!(ok(req(&mut n, "claims", "other", &ot, json!({})))["claims"].as_array().unwrap().is_empty());
    assert_eq!(ok(req(&mut n, "release", "demo", &at, json!({"kind": "area", "key": "/repo/a"})))["released"], true);
    assert!(listed(&mut n, &at).is_empty());
    // the lease table shows the same fact a lease client sees
    ok(claim(&mut n, &at, "server", "s", json!({})));
    let leases = ok(req(&mut n, "lease_list", "demo", &at, json!({})));
    assert_eq!(leases["leases"].as_array().unwrap().len(), 1);
}

fn reserve(n: &mut Node, token: &str, wanted: &[(&str, &str)]) -> Value {
    let resources: Vec<Value> = wanted.iter().map(|(kind, key)| json!({"kind": kind, "key": key})).collect();
    req(n, "reserve", "demo", token, json!({"resources": resources}))
}

/// What ``owner`` holds, as `kind key`.
fn keys(n: &mut Node, token: &str, owner: &str) -> Vec<String> {
    let mut found: Vec<String> = listed(n, token).iter().filter(|c| c["owner"] == owner).map(|c| format!("{} {}", c["kind"].as_str().unwrap(), c["key"].as_str().unwrap())).collect();
    found.sort();
    found
}

#[test]
fn an_area_a_worktree_and_an_install_conflict_when_one_path_contains_the_other_whichever_asks() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((first, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    ok(claim(&mut n, &at, "area", "/repo/x/src/lib.rs", json!({})));
    ok(claim(&mut n, &at, "area", "/repo/x/docs/a.md", json!({})));
    for (kind, path) in [("worktree", "/repo/x"), ("worktree", "/repo/x/src"), ("worktree", "/repo/x/src/lib.rs"), ("install", "/repo/x"), ("area", "/repo/x/src"), ("area", "/repo")] {
        let refused = claim(&mut n, &bt, kind, path, json!({}));
        assert_eq!(code(&refused), "denied", "{kind} {path}");
        assert!(refused["error"]["message"].as_str().unwrap().contains(&first), "{refused}");
    }
    for (kind, path) in [("worktree", "/repo/xy"), ("worktree", "/repo/y"), ("install", "/repo/x/src2"), ("area", "/repo/x/src/lib.rs.bak")] {
        ok(claim(&mut n, &bt, kind, path, json!({})));
    }
    ok(claim(&mut n, &at, "worktree", "/work/tree", json!({})));
    ok(claim(&mut n, &at, "install", "/work/venv", json!({})));
    for (kind, path) in [("area", "/work/tree/src/a.py"), ("install", "/work/tree/.venv"), ("worktree", "/work/venv/lib"), ("area", "/work/venv/bin/python")] {
        assert_eq!(code(&claim(&mut n, &bt, kind, path, json!({}))), "denied", "{kind} {path}");
    }
    ok(claim(&mut n, &bt, "port", "9000", json!({})));
    ok(claim(&mut n, &bt, "server", "/repo/x", json!({})));
    ok(claim(&mut n, &bt, "branch", "/repo/x", json!({})));
}

#[test]
fn a_reserve_takes_every_resource_or_none_and_keeps_what_the_caller_already_held() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((first, at), (second, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    ok(claim(&mut n, &at, "area", "/repo/x/src/lib.rs", json!({})));
    ok(claim(&mut n, &bt, "port", "9100", json!({})));
    let refused = reserve(&mut n, &bt, &[("port", "9100"), ("server", "api"), ("branch", "feat"), ("worktree", "/repo/x")]);
    assert_eq!(code(&refused), "denied");
    assert!(refused["error"]["message"].as_str().unwrap().contains(&first), "{refused}");
    assert_eq!(keys(&mut n, &bt, &second), ["port 9100"], "nothing of the refused set was taken and the port held before stays");
    let got = ok(reserve(&mut n, &bt, &[("port", "9100"), ("server", "api"), ("branch", "feat"), ("worktree", "/repo/other")]));
    let changed: Vec<(String, bool)> = got["claims"].as_array().unwrap().iter().map(|c| (c["claim"]["kind"].as_str().unwrap().into(), c["changed"].as_bool().unwrap())).collect();
    assert_eq!(changed, [("port".to_string(), false), ("server".into(), true), ("branch".into(), true), ("worktree".into(), true)]);
    let again = ok(reserve(&mut n, &bt, &[("worktree", "/repo/other"), ("server", "api")]));
    assert!(again["claims"].as_array().unwrap().iter().all(|c| c["changed"] == false));
    assert_eq!(code(&reserve(&mut n, &at, &[("branch", "feat")])), "denied");
    assert_eq!(code(&reserve(&mut n, &at, &[("port", "7001"), ("worktree", "/repo/other/sub")])), "denied");
    assert_eq!(keys(&mut n, &at, &first), ["area /repo/x/src/lib.rs"], "a refused reserve leaves its caller's holdings as they were");
}

#[test]
fn a_reserve_is_bad_when_empty_or_names_a_bad_key_and_takes_as_many_resources_as_it_names() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, at) = session(&mut n, "demo", "a");
    assert_eq!(code(&reserve(&mut n, &at, &[])), "invalid");
    assert_eq!(code(&reserve(&mut n, &at, &[("port", "9100"), ("worktree", "relative")])), "invalid");
    assert!(keys(&mut n, &at, &name).is_empty(), "a set with a bad key takes none of it");
    let many: Vec<(String, String)> = (0..129).map(|i| ("port".to_string(), (9000 + i).to_string())).collect();
    let wanted: Vec<(&str, &str)> = many.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    ok(reserve(&mut n, &at, &wanted));
    assert_eq!(keys(&mut n, &at, &name).len(), 129, "a reserve of more than a hundred and twenty-eight resources takes them all");
}

#[test]
fn the_defaults_bound_the_leases_of_one_holder_and_of_the_table() {
    let defaults = poolhouse_node::lease::types::Config::default();
    assert_eq!((defaults.max_leases_per_holder, defaults.max_leases), (512, 4096));
}

#[test]
fn a_reserve_above_what_a_session_may_hold_is_refused_whole_and_names_the_limit() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    n.leases.cfg.max_leases_per_holder = 8;
    let (name, at) = session(&mut n, "demo", "a");
    let ports: Vec<(String, String)> = (0..9).map(|i| ("port".to_string(), (9500 + i).to_string())).collect();
    let nine: Vec<(&str, &str)> = ports.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    let refused = reserve(&mut n, &at, &nine);
    assert_eq!(code(&refused), "quota", "{refused}");
    let said = refused["error"]["message"].as_str().unwrap();
    assert!(said.contains("max_leases_per_holder") && said.contains("lease-config.json") && said.contains("the limit is 8"), "{said}");
    assert!(keys(&mut n, &at, &name).is_empty(), "a reserve above the bound takes none of it");
    ok(reserve(&mut n, &at, &nine[..8]));
    let ninth = claim(&mut n, &at, "port", "9600", json!({}));
    assert_eq!(code(&ninth), "quota", "{ninth}");
    assert_eq!(keys(&mut n, &at, &name).len(), 8);
    ok(req(&mut n, "release", "demo", &at, json!({"kind": "port", "key": "9500"})));
    ok(claim(&mut n, &at, "port", "9600", json!({})));
    assert_eq!(code(&claim(&mut n, &at, "port", "9601", json!({}))), "quota", "a released claim made room for one");
}

#[test]
fn a_reserve_of_tens_of_thousands_of_resources_is_refused_before_any_is_looked_at() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, at) = session(&mut n, "demo", "a");
    let resources: Vec<Value> = (0..30_000).map(|i| json!({"kind": "server", "key": format!("s{i}")})).collect();
    let started = std::time::Instant::now();
    let refused = req(&mut n, "reserve", "demo", &at, json!({"resources": resources}));
    assert_eq!(code(&refused), "quota", "{refused}");
    assert!(refused["error"]["message"].as_str().unwrap().contains("the limit is 512"), "{refused}");
    assert!(started.elapsed() < std::time::Duration::from_secs(2), "{:?}", started.elapsed());
    assert!(keys(&mut n, &at, &name).is_empty());
}

#[test]
fn the_table_keeps_no_more_leases_than_max_leases_and_names_the_limit() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    n.leases.cfg.max_leases = 4;
    let ((_, at), (_, bt)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    for port in ["9701", "9702", "9703"] {
        ok(claim(&mut n, &at, "port", port, json!({})));
    }
    ok(claim(&mut n, &bt, "port", "9704", json!({})));
    let full = claim(&mut n, &bt, "port", "9705", json!({}));
    assert_eq!(code(&full), "quota", "{full}");
    let said = full["error"]["message"].as_str().unwrap();
    assert!(said.contains("max_leases") && said.contains("the limit is 4"), "{said}");
    ok(req(&mut n, "release", "demo", &at, json!({"kind": "port", "key": "9701"})));
    ok(claim(&mut n, &bt, "port", "9705", json!({})));
}

#[test]
fn a_reserve_of_as_many_resources_as_a_session_may_hold_takes_them_all() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (name, at) = session(&mut n, "demo", "a");
    let most = n.leases.cfg.max_leases_per_holder;
    let ports: Vec<(String, String)> = (0..most).map(|i| ("port".to_string(), (10_000 + i).to_string())).collect();
    let all: Vec<(&str, &str)> = ports.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    ok(reserve(&mut n, &at, &all));
    assert_eq!(keys(&mut n, &at, &name).len(), most);
    assert_eq!(code(&claim(&mut n, &at, "server", "one-more", json!({}))), "quota");
}
