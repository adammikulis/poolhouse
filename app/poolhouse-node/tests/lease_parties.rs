mod kit;

use kit::{code, ok, req, session};
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use std::process::{Child, Command, Stdio};

fn gpu() -> Value {
    json!({"type": "gpu"})
}

fn sleeper() -> Child {
    #[cfg(unix)]
    let mut command = { let mut c = Command::new("sleep"); c.arg("120"); c };
    #[cfg(windows)]
    let mut command = { let mut c = Command::new("ping"); c.args(["-n", "120", "127.0.0.1"]); c };
    command.stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap()
}

fn state(n: &mut Node, token: &str, id: &Value) -> String {
    ok(req(n, "lease_wait", "demo", token, json!({"id": id, "timeout_ms": 0})))["state"].as_str().unwrap().into()
}

fn lease_entries(n: &mut Node, token: &str) -> usize {
    ok(req(n, "read", "demo", token, json!({"kind": "lease"})))["entries"].as_array().map_or(0, Vec::len)
}

#[test]
fn one_session_holds_a_gpu_lease_per_tag_and_the_second_waits_behind_the_first() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let t = session(&mut n, "demo", "broker").1;
    let first = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "turn-a"})));
    let second = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "turn-b"})));
    assert_eq!(first["state"], "held");
    assert_eq!(second["state"], "queued", "a second request of the same session for the same GPU is another request");
    assert_ne!(first["id"], second["id"]);
    assert_eq!(second["position"], 1);
    ok(req(&mut n, "lease_release", "demo", &t, json!({"id": first["id"]})));
    assert_eq!(state(&mut n, &t, &second["id"]), "held");
}

#[test]
fn asking_again_with_the_same_tag_is_the_same_request() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let t = session(&mut n, "demo", "broker").1;
    let first = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "turn-a"})));
    let again = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "turn-a"})));
    assert_eq!(first["id"], again["id"]);
    let untagged = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()]})));
    assert_eq!(untagged["state"], "queued", "no tag is a party of its own");
}

#[test]
fn a_tag_names_the_request_and_nothing_else() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let (a, b) = (session(&mut n, "demo", "a").1, session(&mut n, "demo", "b").1);
    let held = ok(req(&mut n, "lease_acquire", "demo", &a, json!({"resources": [gpu()], "tag": "x"})));
    let theirs = ok(req(&mut n, "lease_acquire", "demo", &b, json!({"resources": [gpu()], "tag": "x"})));
    assert_eq!(theirs["state"], "queued", "the same tag of another session is not the same party");
    assert_eq!(code(&req(&mut n, "lease_release", "demo", &b, json!({"id": held["id"]}))), "denied");
    for bad in ["", "a b", "a\nb", &"x".repeat(65)] {
        let reply = req(&mut n, "lease_acquire", "demo", &a, json!({"resources": [{"type": "claim", "kind": "port", "name": "9000"}], "tag": bad}));
        if bad.is_empty() {
            ok(reply);
        } else {
            assert_eq!(code(&reply), "invalid", "tag {bad:?}");
        }
    }
}

#[test]
fn a_quiet_lease_leaves_nothing_on_the_board_and_only_the_gpu_and_a_server_line_may_be_quiet() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let t = session(&mut n, "demo", "broker").1;
    let before = lease_entries(&mut n, &t);
    let quiet = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "q", "quiet": true})));
    assert_eq!(quiet["state"], "held");
    ok(req(&mut n, "lease_release", "demo", &t, json!({"id": quiet["id"]})));
    assert_eq!(lease_entries(&mut n, &t), before, "a quiet lease and its end are not entries");
    let loud = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "l"})));
    ok(req(&mut n, "lease_release", "demo", &t, json!({"id": loud["id"]})));
    assert_eq!(lease_entries(&mut n, &t), before + 2, "an ordinary lease is acquire and end");
    let line = json!({"type": "claim", "kind": "server", "name": "line:cpu"});
    assert_eq!(ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [line], "quiet": true})))["state"], "held");
    for resource in [json!({"type": "claim", "kind": "branch", "name": "main"}), json!({"type": "cpu_slots", "count": 1}), json!({"type": "model_slot", "model": "m"})] {
        assert_eq!(code(&req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [resource], "quiet": true}))), "invalid", "{resource}");
    }
}

#[test]
fn renewing_with_a_pid_binds_the_lease_to_that_process() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let (_, t) = session(&mut n, "demo", "broker");
    let other = session(&mut n, "demo", "other").1;
    let mut child = sleeper();
    let lease = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [{"type": "model_slot", "model": "m"}], "ttl_s": 3000})));
    assert_eq!(lease["pid"], 0);
    let bound = ok(req(&mut n, "lease_renew", "demo", &t, json!({"id": lease["id"], "pid": child.id()})));
    assert_eq!(bound["pid"], child.id());
    let mut stranger = sleeper();
    assert_eq!(code(&req(&mut n, "lease_renew", "demo", &other, json!({"id": lease["id"], "pid": stranger.id()}))), "denied");
    let listed = ok(req(&mut n, "lease_list", "demo", &t, json!({})));
    assert_eq!(listed["leases"][0]["pid"], child.id(), "another identity's refused renewal binds nothing");
    stranger.kill().unwrap();
    stranger.wait().unwrap();
    assert_eq!(state(&mut n, &t, &lease["id"]), "held");
    child.kill().unwrap();
    child.wait().unwrap();
    let listed = ok(req(&mut n, "lease_list", "demo", &t, json!({})));
    assert!(listed["leases"].as_array().unwrap().is_empty(), "the process the lease was bound to is gone: {listed}");
    let again = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [{"type": "model_slot", "model": "m"}], "ttl_s": 3000})));
    assert_eq!(code(&req(&mut n, "lease_renew", "demo", &t, json!({"id": again["id"], "pid": child.id()}))), "invalid", "a pid that is not running binds nothing");
}

#[test]
fn the_memory_budget_is_read_again_when_the_config_file_changes() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let (a, b) = (session(&mut n, "demo", "a").1, session(&mut n, "demo", "b").1);
    let mem = |mb: u64| json!({"type": "memory_mb", "mb": mb});
    ok(req(&mut n, "lease_acquire", "demo", &a, json!({"resources": [mem(900)]})));
    std::fs::write(dir.path().join("lease-config.json"), json!({"memory_mb": 1000}).to_string()).unwrap();
    assert_eq!(code(&req(&mut n, "lease_acquire", "demo", &b, json!({"resources": [mem(1500)]}))), "quota");
    let waiting = ok(req(&mut n, "lease_acquire", "demo", &b, json!({"resources": [mem(500)]})));
    assert_eq!(waiting["state"], "queued");
    std::fs::write(dir.path().join("lease-config.json"), json!({"memory_mb": 2000, "cpu_slots": 3}).to_string()).unwrap();
    assert_eq!(state(&mut n, &b, &waiting["id"]), "held", "a larger budget grants what was waiting");
    assert_eq!(ok(req(&mut n, "lease_list", "demo", &b, json!({})))["config"]["cpu_slots"], 3);
    std::fs::write(dir.path().join("lease-config.json"), "not json").unwrap();
    assert_eq!(ok(req(&mut n, "lease_list", "demo", &b, json!({})))["config"]["memory_mb"], 2000, "a file that does not parse changes nothing");
}

#[test]
fn a_gpu_lease_folds_no_board_and_a_pool_wide_claim_still_does() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let t = session(&mut n, "demo", "broker").1;
    let folds = n.folds;
    for i in 0..5 {
        let lease = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": format!("t{i}"), "quiet": true})));
        ok(req(&mut n, "lease_renew", "demo", &t, json!({"id": lease["id"]})));
        ok(req(&mut n, "lease_wait", "demo", &t, json!({"id": lease["id"], "timeout_ms": 0})));
        ok(req(&mut n, "lease_list", "demo", &t, json!({})));
        ok(req(&mut n, "lease_release", "demo", &t, json!({"id": lease["id"]})));
    }
    assert_eq!(n.folds, folds, "a lease of the GPU reads no pool-wide claim, so it folds nothing");
    ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [{"type": "claim", "kind": "branch", "name": "main"}]})));
    assert!(n.folds > folds, "a branch claim waits behind what other devices hold, which the board says");
}

#[test]
fn a_quiet_lease_with_no_process_behind_it_lasts_the_default_between_renewals_however_long_it_asks() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let t = session(&mut n, "demo", "broker").1;
    let default = n.leases.cfg.default_ttl_s;
    let lease = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "q", "quiet": true, "ttl_s": 86400})));
    assert_eq!(lease["ttl_s"], default, "a holder that is gone frees the GPU within the default time");
    let renewed = ok(req(&mut n, "lease_renew", "demo", &t, json!({"id": lease["id"], "ttl_s": 86400})));
    assert_eq!(renewed["ttl_s"], default);
    let loud = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [{"type": "claim", "kind": "port", "name": "9001"}], "ttl_s": 7200})));
    assert_eq!(loud["ttl_s"], 7200, "a lease that is not quiet asks as long as the configuration allows");
}

#[cfg(unix)]
#[test]
fn a_quiet_lease_bound_to_a_process_lasts_as_long_as_it_asks_because_it_ends_with_the_process() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let t = session(&mut n, "demo", "broker").1;
    let mut holder = std::process::Command::new("sleep").arg("120").spawn().unwrap();
    let lease = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "q", "quiet": true, "ttl_s": 86400, "pid": holder.id()})));
    assert_eq!(lease["ttl_s"], 86400);
    let free = req(&mut n, "lease_renew", "demo", &t, json!({"id": lease["id"], "ttl_s": 7200}));
    assert_eq!(ok(free)["ttl_s"], 7200);
    holder.kill().unwrap();
    holder.wait().unwrap();
    let again = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "other", "quiet": true})));
    assert_eq!(again["state"], "held", "the lease ended with its process");
}

#[test]
fn a_quiet_lease_given_a_process_later_may_then_be_renewed_for_longer() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let t = session(&mut n, "demo", "broker").1;
    let lease = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "q", "quiet": true})));
    let renewed = ok(req(&mut n, "lease_renew", "demo", &t, json!({"id": lease["id"], "ttl_s": 7200, "pid": std::process::id()})));
    assert_eq!(renewed["ttl_s"], 7200);
}

#[test]
fn a_config_the_table_cannot_run_on_changes_nothing_and_none_at_start_is_the_defaults() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let t = session(&mut n, "demo", "a").1;
    std::fs::write(dir.path().join("lease-config.json"), json!({"memory_mb": 500}).to_string()).unwrap();
    assert_eq!(ok(req(&mut n, "lease_list", "demo", &t, json!({})))["config"]["memory_mb"], 500);
    for bad in [json!({"max_ttl_s": 0}), json!({"background_share_percent": 101}), json!({"no_such_field": 1}), json!([1])] {
        std::fs::write(dir.path().join("lease-config.json"), bad.to_string()).unwrap();
        let listed = ok(req(&mut n, "lease_list", "demo", &t, json!({})));
        assert_eq!(listed["config"]["memory_mb"], 500, "{bad} changed the settings");
        let lease = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "x"})));
        ok(req(&mut n, "lease_release", "demo", &t, json!({"id": lease["id"]})));
    }
    std::fs::remove_file(dir.path().join("lease-config.json")).unwrap();
    assert_eq!(ok(req(&mut n, "lease_list", "demo", &t, json!({})))["config"]["memory_mb"], 0, "a file that is gone sets the defaults");
    drop(n);
    std::fs::write(dir.path().join("lease-config.json"), json!({"max_ttl_s": 0}).to_string()).unwrap();
    let mut again = Node::open(dir.path()).unwrap();
    let t = session(&mut again, "demo", "b").1;
    assert_eq!(ok(req(&mut again, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "ttl_s": 5})))["state"], "held");
}

#[test]
fn a_table_that_changed_only_by_a_request_in_flight_is_not_written() {
    let dir = tempfile::tempdir().unwrap();
    let mut n = Node::open(dir.path()).unwrap();
    let t = session(&mut n, "demo", "broker").1;
    let file = dir.path().join("leases.json");
    let kept = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [{"type": "claim", "kind": "port", "name": "9001"}], "tag": "p"})));
    let before = std::fs::read(&file).unwrap();
    for i in 0..3 {
        let turn = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": format!("t{i}"), "quiet": true})));
        ok(req(&mut n, "lease_renew", "demo", &t, json!({"id": turn["id"]})));
        ok(req(&mut n, "lease_release", "demo", &t, json!({"id": turn["id"]})));
    }
    assert_eq!(std::fs::read(&file).unwrap(), before, "turns wrote nothing");
    ok(req(&mut n, "lease_release", "demo", &t, json!({"id": kept["id"]})));
    assert_ne!(std::fs::read(&file).unwrap(), before, "an ordinary lease's end is written");
    let holding = ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [gpu()], "tag": "held", "quiet": true})));
    ok(req(&mut n, "lease_acquire", "demo", &t, json!({"resources": [{"type": "claim", "kind": "port", "name": "9002"}], "tag": "p2"})));
    drop(n);
    let mut restarted = Node::open(dir.path()).unwrap();
    let after = session(&mut restarted, "demo", "after").1;
    let listed = ok(req(&mut restarted, "lease_list", "demo", &after, json!({})));
    let ids: Vec<&Value> = listed["leases"].as_array().unwrap().iter().map(|l| &l["id"]).collect();
    assert!(!ids.contains(&&holding["id"]), "a request in flight does not outlive the node");
    assert_eq!(ids.len(), 1);
}
