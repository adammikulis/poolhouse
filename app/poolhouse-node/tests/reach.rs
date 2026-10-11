//! How a member is reached, as `pool_status` says it between two real loopback nodes: `direct` (this device dialed it and it answered),
//! `channel` (it dials this device and requests go down its connection), `none` (no address answers, no connection is open) and `self`,
//! each with the exact fix when it is not direct. A device behind NAT is one whose address nothing listens on (the helpers of reverse.rs).

mod kit;

use kit::netkit::*;
use poolhouse_node::netif::network_of;
use poolhouse_node::reach::{CHANNEL_HINT, NONE_HINT, WSL_NAT_HINT};
use serde_json::{json, Value};
use std::net::Ipv4Addr;

/// ``a`` can be dialed, ``b`` cannot (the address ``a`` holds for ``b`` is one nothing listens on); ``b`` says it is on ``network``.
fn behind_nat(network: &str) -> (Dev, Dev) {
    let (a, b) = (device(), device());
    b.node.lock().unwrap().network = network.into();
    a.join_with_code(&b);
    a.node.lock().unwrap().facts.set_addr(&b.fp(), "127.0.0.1:9").unwrap();
    (a, b)
}

fn reach(a: &Dev, b: &Dev) -> (String, String) {
    let m = a.member(&b.fp()).unwrap();
    (m["reach"].as_str().unwrap().into(), m["reach_hint"].as_str().unwrap().into())
}

fn caps(a: &Dev, b: &Dev) -> Value {
    a.call("shard_call", "", &a.token, json!({"device": b.fp(), "op": "shard_caps"}))
}

#[test]
fn this_device_is_self_and_needs_no_hint() {
    let (a, _b) = behind_nat("");
    let me = a.member(&a.fp()).unwrap();
    assert_eq!((me["reach"].as_str(), me["reach_hint"].as_str()), (Some("self"), Some("")), "{me}");
}

#[test]
fn a_member_this_device_dialed_and_that_answered_is_direct() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    assert_eq!(reach(&a, &b).0, "none", "nothing has been dialed yet, so nothing has answered");
    a.net.sync_once(true).unwrap();
    b.net.sync_once(true).unwrap();
    assert_eq!(reach(&a, &b), ("direct".to_string(), String::new()));
    assert_eq!(reach(&b, &a), ("direct".to_string(), String::new()), "the joiner dialed the other device too");
}

#[test]
fn a_member_with_no_address_and_no_connection_is_none_and_says_what_to_do() {
    let (a, b) = behind_nat("");
    a.node.lock().unwrap().facts.peer(&b.fp()).addr = None;
    assert_eq!(reach(&a, &b), ("none".to_string(), NONE_HINT.to_string()));
    assert_eq!(NONE_HINT, "no member can dial it yet: have it dial a member, or give it an address");
}

#[test]
fn an_address_that_does_not_answer_is_none_and_names_the_address() {
    let (a, b) = behind_nat("");
    a.net.sync_once(false).unwrap();
    let (state, hint) = reach(&a, &b);
    assert_eq!(state, "none");
    assert_eq!(hint, format!("it did not answer at 127.0.0.1:9: {NONE_HINT}"));
}

#[test]
fn a_member_that_dials_in_is_reached_over_its_channel_and_the_hint_says_how_to_make_it_direct() {
    let (a, b) = behind_nat("");
    a.net.sync_once(true).unwrap();
    assert_eq!(reach(&a, &b).0, "none");
    b.net.sync_once(true).unwrap();
    eventually("a to see the channel b opened", 10, || reach(&a, &b).0 == "channel");
    assert_eq!(reach(&a, &b).1, CHANNEL_HINT);
    assert_eq!(caps(&a, &b)["ok"], true, "channel means requests do go down it");
}

#[test]
fn a_wsl_device_behind_nat_gets_the_wsl_fix_whether_or_not_its_channel_is_open() {
    let (a, b) = behind_nat("wsl-nat");
    a.net.sync_once(false).unwrap();
    assert_eq!(a.member(&b.fp()).unwrap()["network"], "wsl-nat", "the hello carries how the device is attached");
    assert_eq!(reach(&a, &b), ("none".to_string(), WSL_NAT_HINT.to_string()));
    b.net.sync_once(true).unwrap();
    eventually("a to see the channel b opened", 10, || reach(&a, &b).0 == "channel");
    assert_eq!(reach(&a, &b).1, WSL_NAT_HINT);
    assert!(WSL_NAT_HINT.contains("set networkingMode=mirrored in .wslconfig and run wsl --shutdown") && WSL_NAT_HINT.contains("python -m poolhouse.device_setup --yes"));
}

#[test]
fn a_wsl_device_a_member_can_dial_needs_no_fix() {
    let (a, b) = (device(), device());
    b.node.lock().unwrap().network = "wsl-mirrored".into();
    a.join_with_code(&b);
    a.net.sync_once(true).unwrap();
    assert_eq!((a.member(&b.fp()).unwrap()["network"].as_str(), reach(&a, &b)), (Some("wsl-mirrored"), ("direct".to_string(), String::new())));
}

#[test]
fn a_member_put_out_of_the_pool_is_not_reached_at_all() {
    let (a, b) = behind_nat("wsl-nat");
    b.net.sync_once(true).unwrap();
    eventually("the channel", 10, || reach(&a, &b).0 == "channel");
    a.ok("member_remove", "", &a.token, json!({"fingerprint": b.fp()}));
    assert_eq!(reach(&a, &b), ("none".to_string(), String::new()));
}

#[test]
fn a_node_reports_wsl_nat_from_the_kernel_and_the_route_it_uses() {
    let wsl = "Linux version 5.15.153.1-microsoft-standard-WSL2";
    let ip = |s: &str| Some(s.parse::<Ipv4Addr>().unwrap());
    assert_eq!(network_of(wsl, ip("172.24.5.2")), "wsl-nat");
    assert_eq!(network_of(wsl, ip("192.168.2.40")), "wsl-mirrored");
    assert_eq!(network_of(wsl, ip("172.15.0.1")), "wsl-mirrored", "172.16.0.0/12 only");
    assert_eq!(network_of(wsl, ip("172.32.0.1")), "wsl-mirrored");
    assert_eq!(network_of(wsl, None), "wsl-mirrored");
    assert_eq!(network_of("Linux version 6.8.0-generic (buildd)", ip("172.17.0.2")), "", "a container on Docker's 172.17 is not WSL");
}
