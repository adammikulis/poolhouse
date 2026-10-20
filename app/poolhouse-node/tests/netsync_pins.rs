//! A device is tied to a board (and the board kept from being reaped) by rows that moved between the two, not by a call that asked.

mod kit;

use kit::netkit::*;
use poolhouse_node::netsync::sync_all;
use poolhouse_node::peer::PeerClient;
use serde_json::json;

fn reach(from: &Dev, to: &Dev) -> PeerClient {
    PeerClient::connect(&from.net.me, to.addr(), Some(&to.fp())).unwrap()
}

/// Two members of one pool; each has made `board`, started and ended a session on it, and nothing else.
fn pair(board: &str) -> (Dev, Dev) {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    for d in [&a, &b] {
        let (_, token) = d.session(board, "short");
        d.ok("retire", board, &token, json!({"target": "", "reason": "done"}));
        d.node.lock().unwrap().board_grace_ms = 0;
    }
    (a, b)
}

fn tied(d: &Dev, board: &str) -> usize {
    d.node.lock().unwrap().boards[board].board.peer_fingerprints().len()
}

fn origin_of(d: &Dev, board: &str) -> String {
    d.node.lock().unwrap().boards[board].board.origin().to_string()
}

#[test]
fn a_member_that_only_asks_for_the_vector_does_not_tie_itself_to_a_board_or_keep_it_from_being_reaped() {
    let (a, b) = pair("asked");
    let reply = reach(&b, &a).call(&json!({"op": "vector", "board": "asked", "origin": origin_of(&b, "asked")})).unwrap();
    assert!(reply["vector"].is_object(), "{reply}");
    assert_eq!(tied(&a, "asked"), 0, "a vector asked for is not a sync");
    assert_eq!(a.node.lock().unwrap().reap_idle_boards().unwrap(), 1, "the board that held only the ends of its sessions is removed");
    assert!(!a.node.lock().unwrap().boards.contains_key("asked"));
}

#[test]
fn a_pull_and_a_push_that_move_no_row_tie_nothing() {
    let (a, b) = pair("empty");
    let mut client = reach(&b, &a);
    let origin = origin_of(&b, "empty");
    let current = {
        let mut n = a.node.lock().unwrap();
        let board = &mut n.boards.get_mut("empty").unwrap().board;
        board.seal().unwrap();
        board.vector()
    };
    let pulled = client.call(&json!({"op": "pull", "board": "empty", "origin": origin, "vector": current})).unwrap();
    assert!(pulled["logs"].as_object().unwrap().values().all(|rows| rows.as_array().unwrap().is_empty()), "{pulled}");
    client.call(&json!({"op": "push", "board": "empty", "origin": origin, "logs": {}, "trusted_vector": {}})).unwrap();
    assert_eq!(tied(&a, "empty"), 0, "nothing moved, so nothing is tied");
    assert_eq!(a.node.lock().unwrap().reap_idle_boards().unwrap(), 1);
}

#[test]
fn a_push_of_rows_the_board_refuses_for_a_gap_ties_nothing() {
    let (a, b) = pair("refused");
    let origin = origin_of(&b, "refused");
    let rows = b.node.lock().unwrap().boards["refused"].board.rows(&origin).to_vec();
    let gapped = serde_json::to_value(&rows[1..]).unwrap();
    let reply = reach(&b, &a).call(&json!({"op": "push", "board": "refused", "origin": origin, "logs": {origin.clone(): gapped}, "trusted_vector": {}})).unwrap();
    assert_eq!(reply["stored"], 0, "{reply}");
    assert_eq!(tied(&a, "refused"), 0, "a push that stored no row is not a sync");
}

#[test]
fn a_sync_that_moves_rows_ties_both_devices_and_the_board_is_never_reaped() {
    let (a, b) = pair("synced");
    sync_all(&b.node, &mut reach(&b, &a)).unwrap();
    assert_eq!((tied(&a, "synced"), tied(&b, "synced")), (1, 1), "rows moved both ways, so each device knows the other");
    assert_eq!(a.node.lock().unwrap().reap_idle_boards().unwrap(), 0, "a board another device synced is kept");
    assert_eq!(b.node.lock().unwrap().reap_idle_boards().unwrap(), 0);
}
