mod kit;

use kit::{kill_hard, short_dir};

use poolhouse_node::client::{ensure_running, Client, Started};
use poolhouse_node::error::Error;
#[cfg(unix)]
use poolhouse_node::server::socket_path;
use poolhouse_node::server::Server;
use poolhouse_node::sys::{connect, owner_only, Principal};
use serde_json::json;
use std::io::{Read, Write};
use std::path::Path;

const BIN: &str = env!("CARGO_BIN_EXE_poolhouse-node");

fn register(c: &mut Client, board: &str, session: &str) -> (String, String) {
    let r = c.call("register", board, "", json!({"model": "claude-sonnet-5-5", "harness": "claude-code", "session": session})).unwrap();
    (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
}

fn stop(state: &Path, token: &str, board: &str) {
    let _ = Client::connect(state).unwrap().call("shutdown", board, token, json!({}));
    for _ in 0..200 {
        if Client::connect(state).is_err() || connect(state).is_err() {
            return;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    panic!("the node did not stop");
}

#[test]
fn a_round_trip_over_the_socket_with_owner_only_access() {
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap();
    let handle = std::thread::spawn(move || server.serve());
    let mut c = Client::connect(dir.path()).unwrap();
    assert_eq!(c.call("hello", "", "", json!({})).unwrap()["node"], "poolhouse-node");
    let (name, token) = register(&mut c, "demo", "s1");
    c.call("post", "demo", &token, json!({"kind": "message", "fields": {"type": "status", "body": "over the wire"}})).unwrap();
    let read = c.call("read", "demo", &token, json!({"kind": "message"})).unwrap();
    assert_eq!(read["entries"][0]["sender"], name);
    #[cfg(unix)]
    assert!(owner_only(&socket_path(dir.path())), "the socket is owner-only (a pipe's access list is its creation attributes)");
    assert!(owner_only(dir.path()), "the state directory is owner-only");
    c.call("shutdown", "demo", &token, json!({})).unwrap();
    handle.join().unwrap().unwrap();
}

#[test]
fn an_unproved_root_cannot_register_over_the_local_socket() {
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap();
    let node = server.node();
    let handle = std::thread::spawn(move || server.serve());
    let mut raw = connect(dir.path()).unwrap();
    poolhouse_node::wire::write_frame(&mut raw, &json!({"v": 1, "id": 1, "method": "register", "board": "demo",
        "params": {"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "unproved"}})).unwrap();
    let reply = poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap();
    node.lock().unwrap().stop.store(true, std::sync::atomic::Ordering::SeqCst);
    drop(raw);
    handle.join().unwrap().unwrap();
    assert_eq!(reply["ok"], false, "a root without a credential cannot register");
}

#[cfg(unix)]
#[test]
fn credential_creation_refuses_a_redirected_directory_or_key_file() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let dir = short_dir();
    let outside = short_dir();
    let folder = dir.path().join("hook-credentials");
    symlink(outside.path(), &folder).unwrap();
    let result = poolhouse_node::root_auth::ensure(dir.path());
    assert!(result.is_err(), "a credential directory cannot be redirected");
    assert!(!outside.path().join("root.key").exists(), "nothing was created at the target");
    std::fs::remove_file(&folder).unwrap();
    std::fs::create_dir(&folder).unwrap();
    std::fs::set_permissions(&folder, std::fs::Permissions::from_mode(0o700)).unwrap();
    let target = outside.path().join("key");
    std::fs::write(&target, "11".repeat(32)).unwrap();
    symlink(&target, folder.join("root.key")).unwrap();
    assert!(poolhouse_node::root_auth::ensure(dir.path()).is_err(), "a credential file cannot be a symlink");
}

#[test]
fn the_root_and_reporter_credentials_are_independent_and_private() {
    let dir = short_dir();
    poolhouse_node::root_auth::ensure(dir.path()).unwrap();
    let folder = dir.path().join("hook-credentials");
    let root = folder.join("root.key");
    let reporter = folder.join("reporter.key");
    assert!(owner_only(&folder) && owner_only(&root) && owner_only(&reporter));
    assert!(std::fs::read(&root).unwrap() != std::fs::read(&reporter).unwrap(), "credentials are independently generated");
    std::fs::copy(&root, &reporter).unwrap();
    assert!(poolhouse_node::root_auth::ensure(dir.path()).is_err(), "the same bytes cannot serve both roles");
}

#[cfg(unix)]
#[test]
fn credential_initialization_recovers_after_publication_was_interrupted() {
    let dir = short_dir();
    poolhouse_node::root_auth::ensure(dir.path()).unwrap();
    let folder = dir.path().join("hook-credentials");
    std::fs::hard_link(folder.join("root.key"), folder.join("root.key.pending")).unwrap();
    assert!(poolhouse_node::root_auth::ensure(dir.path()).is_ok(), "an interrupted publish can finish without replacing the credential");
    assert!(!folder.join("root.key.pending").exists());
}

#[test]
fn root_proof_is_bound_to_one_connection_request_and_cannot_be_replayed() {
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap();
    let node = server.node();
    let handle = std::thread::spawn(move || server.serve());
    let mut raw = connect(dir.path()).unwrap();
    let ask = json!({"v": 1, "id": 1, "method": "root_challenge", "params": {}});
    let mut register = json!({"v": 1, "id": 2, "method": "register", "board": "demo",
        "params": {"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "verified"}});
    poolhouse_node::wire::write_frame(&mut raw, &ask).unwrap();
    let nonce = poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap()["result"]["nonce"].as_str().unwrap().to_string();
    let proof = poolhouse_node::root_auth::sign(dir.path(), &nonce, std::process::id(), &register).unwrap();
    register["params"]["root_proof"] = json!(proof);
    poolhouse_node::wire::write_frame(&mut raw, &register).unwrap();
    let first = poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap();
    assert_eq!(first["ok"], true, "a valid response registers");
    let token = first["result"]["token"].as_str().unwrap().to_string();
    poolhouse_node::wire::write_frame(&mut raw, &register).unwrap();
    assert_eq!(poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap()["ok"], false, "a nonce is used once");
    let mut other = connect(dir.path()).unwrap();
    poolhouse_node::wire::write_frame(&mut other, &register).unwrap();
    assert_eq!(poolhouse_node::wire::read_frame(&mut other).unwrap().unwrap()["ok"], false, "a proof cannot cross connections");
    let same = json!({"v": 1, "id": 3, "method": "register", "board": "demo", "token": token,
        "params": {"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "verified"}});
    poolhouse_node::wire::write_frame(&mut raw, &same).unwrap();
    assert_eq!(poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap()["ok"], false, "the main session's token does not bypass root proof");
    let mut child = same;
    child["params"]["session"] = json!("child");
    poolhouse_node::wire::write_frame(&mut raw, &child).unwrap();
    let made = poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap();
    assert_eq!(made["ok"], true, "the parent token still registers a child");
    node.lock().unwrap().stop.store(true, std::sync::atomic::Ordering::SeqCst);
    drop((raw, other));
    handle.join().unwrap().unwrap();
}

#[test]
fn a_root_response_does_not_authorize_changed_registration_fields() {
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap();
    let node = server.node();
    let handle = std::thread::spawn(move || server.serve());
    let mut raw = connect(dir.path()).unwrap();
    let ask = json!({"v": 1, "id": 1, "method": "root_challenge", "params": {}});
    for field in ["board", "token", "model", "harness", "session"] {
        let mut request = json!({"v": 1, "id": 2, "method": "register", "board": "demo",
            "params": {"model": "claude-sonnet-5-5", "harness": "claude-code", "session": "original"}});
        poolhouse_node::wire::write_frame(&mut raw, &ask).unwrap();
        let nonce = poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap()["result"]["nonce"].as_str().unwrap().to_string();
        let proof = poolhouse_node::root_auth::sign(dir.path(), &nonce, std::process::id(), &request).unwrap();
        request["params"]["root_proof"] = json!(proof);
        if field == "board" || field == "token" { request[field] = json!("changed"); }
        else { request["params"][field] = json!("changed"); }
        poolhouse_node::wire::write_frame(&mut raw, &request).unwrap();
        let refused = poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap();
        assert_eq!(refused["ok"], false, "changing {field} must be refused: {refused}");
    }
    node.lock().unwrap().stop.store(true, std::sync::atomic::Ordering::SeqCst);
    drop(raw);
    handle.join().unwrap().unwrap();
}

#[test]
fn a_root_response_for_a_different_server_process_is_refused() {
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap();
    let node = server.node();
    let handle = std::thread::spawn(move || server.serve());
    let mut raw = connect(dir.path()).unwrap();
    poolhouse_node::wire::write_frame(&mut raw, &json!({"v":1,"id":1,"method":"root_challenge","params":{}})).unwrap();
    let nonce = poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap()["result"]["nonce"].as_str().unwrap().to_string();
    let mut request = json!({"v":1,"id":2,"method":"register","board":"demo",
        "params":{"model":"","harness":"test","session":"relay"}});
    let proof = poolhouse_node::root_auth::sign(dir.path(), &nonce, std::process::id() ^ 0x4000_0000, &request).unwrap();
    request["params"]["root_proof"] = json!(proof);
    poolhouse_node::wire::write_frame(&mut raw, &request).unwrap();
    assert_eq!(poolhouse_node::wire::read_frame(&mut raw).unwrap().unwrap()["ok"], false, "a different server cannot relay this response");
    node.lock().unwrap().stop.store(true, std::sync::atomic::Ordering::SeqCst);
    drop(raw);
    handle.join().unwrap().unwrap();
}

#[test]
fn repeated_bad_root_responses_are_noticed_without_blocking_another_connection() {
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap();
    let node = server.node();
    let handle = std::thread::spawn(move || server.serve());
    let mut bad = connect(dir.path()).unwrap();
    let request = json!({"v": 1, "id": 1, "method": "register", "board": "demo",
        "params": {"model": "", "harness": "test", "session": "bad"}});
    for _ in 0..3 {
        poolhouse_node::wire::write_frame(&mut bad, &request).unwrap();
        assert_eq!(poolhouse_node::wire::read_frame(&mut bad).unwrap().unwrap()["ok"], false);
    }
    let mut good = Client::connect(dir.path()).unwrap();
    assert!(good.call("register", "demo", "", json!({"model":"", "harness":"test", "session":"good"})).is_ok());
    let entries = node.lock().unwrap().view("pool").unwrap();
    assert!(entries.iter().any(|entry| entry.fields["event"] == "root_auth.rejected"), "the repeated failures are recorded");
    node.lock().unwrap().stop.store(true, std::sync::atomic::Ordering::SeqCst);
    drop((bad, good));
    handle.join().unwrap().unwrap();
}

#[test]
fn a_peer_who_is_another_user_is_refused() {
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap().allow_only(Principal::stranger());
    let node = server.node();
    std::thread::spawn(move || server.serve());
    let mut c = Client::connect(dir.path()).unwrap();
    let result = c.call("hello", "", "", json!({}));
    assert!(matches!(&result, Err(Error::Denied(m)) if m.contains("another user")), "{result:?}");
    node.lock().unwrap().stop.store(true, std::sync::atomic::Ordering::SeqCst);
}

#[test]
fn a_peer_refusal_is_read_even_if_the_server_closes_before_the_request_is_written() {
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap().allow_only(Principal::stranger());
    let node = server.node();
    let handle = std::thread::spawn(move || server.serve());
    let mut client = Client::connect(dir.path()).unwrap();
    std::thread::sleep(std::time::Duration::from_millis(100));
    let reply = client.call("hello", "", "", json!({}));
    node.lock().unwrap().stop.store(true, std::sync::atomic::Ordering::SeqCst);
    handle.join().unwrap().unwrap();
    assert!(matches!(reply, Err(Error::Denied(message)) if message.contains("another user")), "the server's refusal must be returned");
}

#[cfg(unix)]
#[test]
fn a_failed_write_does_not_accept_an_unsolicited_success_or_unrelated_error() {
    use std::os::unix::net::UnixListener;
    for response in [json!({"v":1,"id":null,"ok":true,"result":{}}),
                     json!({"v":1,"id":null,"ok":false,"error":{"code":"quota","message":"unrelated"}})] {
        let dir = short_dir();
        let listener = UnixListener::bind(socket_path(dir.path())).unwrap();
        let sender = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            poolhouse_node::wire::write_frame(&mut stream, &response).unwrap();
        });
        let mut client = Client::connect(dir.path()).unwrap();
        sender.join().unwrap();
        assert!(matches!(client.call("hello", "", "", json!({})), Err(Error::Io(_))), "a request that was not written cannot succeed or return an unrelated response");
    }
}

#[test]
fn only_one_node_runs_per_state_directory() {
    let dir = short_dir();
    let first = Server::bind(dir.path()).unwrap();
    assert!(matches!(Server::bind(dir.path()), Err(Error::Denied(_))));
    drop(first);
    assert!(Server::bind(dir.path()).is_ok(), "the lock is released with the node");
}

#[test]
fn a_bad_frame_closes_that_connection_and_not_the_node() {
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap();
    let node = server.node();
    std::thread::spawn(move || server.serve());
    let mut raw = connect(dir.path()).unwrap();
    raw.write_all(&(u32::MAX).to_be_bytes()).unwrap();
    let mut rest = Vec::new();
    let _ = raw.read_to_end(&mut rest);
    assert!(rest.is_empty());
    let mut garbled = connect(dir.path()).unwrap();
    garbled.write_all(&5u32.to_be_bytes()).unwrap();
    garbled.write_all(b"[not ").unwrap();
    let _ = garbled.read_to_end(&mut rest);
    assert!(Client::connect(dir.path()).unwrap().call("hello", "", "", json!({})).is_ok());
    node.lock().unwrap().stop.store(true, std::sync::atomic::Ordering::SeqCst);
}

#[test]
fn many_clients_starting_the_node_at_once_start_exactly_one() {
    let dir = short_dir();
    let state = dir.path().to_path_buf();
    let threads: Vec<_> = (0..8).map(|_| {
        let state = state.clone();
        std::thread::spawn(move || ensure_running(&state, Path::new(BIN)).unwrap())
    }).collect();
    let started: Vec<Started> = threads.into_iter().map(|t| t.join().unwrap()).collect();
    assert_eq!(started.iter().filter(|s| **s == Started::Spawned).count(), 1, "{started:?}");
    let pid = Client::connect(&state).unwrap().call("hello", "", "", json!({})).unwrap()["pid"].as_u64().unwrap();
    assert_ne!(pid, std::process::id() as u64);
    let (_, token) = register(&mut Client::connect(&state).unwrap(), "demo", "s");
    stop(&state, &token, "demo");
}

#[test]
fn a_killed_node_is_restarted_on_demand_and_loses_nothing() {
    let dir = short_dir();
    let state = dir.path().to_path_buf();
    assert_eq!(ensure_running(&state, Path::new(BIN)).unwrap(), Started::Spawned);
    let mut c = Client::connect(&state).unwrap();
    let (name, token) = register(&mut c, "demo", "s");
    for i in 0..5 {
        c.call("post", "demo", &token, json!({"kind": "message", "fields": {"type": "status", "body": format!("m{i}")}})).unwrap();
    }
    let pid = c.call("hello", "", "", json!({})).unwrap()["pid"].as_u64().unwrap();
    kill_hard(pid as u32);
    for _ in 0..200 {
        if Client::connect(&state).is_err() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(10));
    }
    #[cfg(unix)]
    assert!(socket_path(&state).exists(), "the dead node left its socket behind");
    assert_eq!(ensure_running(&state, Path::new(BIN)).unwrap(), Started::Spawned);
    let mut c = Client::connect(&state).unwrap();
    let read = c.call("read", "demo", &token, json!({"kind": "message"})).unwrap();
    assert_eq!(read["entries"].as_array().unwrap().len(), 5, "committed entries and the token survive kill -9");
    assert_eq!(read["entries"][0]["sender"], name);
    stop(&state, &token, "demo");
}

#[cfg(windows)]
#[test]
fn the_stop_event_ends_a_node_that_has_no_terminal() {
    use poolhouse_node::sys::signal_stop;
    let dir = short_dir();
    let server = Server::bind(dir.path()).unwrap();
    let handle = std::thread::spawn(move || server.serve());
    assert!(signal_stop(dir.path()), "the node holds its stop event");
    handle.join().unwrap().unwrap();
    assert!(Client::connect(dir.path()).is_err(), "the pipe went with the node");
}

#[cfg(windows)]
#[test]
fn a_second_pipe_of_the_same_name_is_refused_even_without_the_lock() {
    let dir = short_dir();
    let _first = poolhouse_node::sys::Listener::bind(dir.path()).unwrap();
    assert!(matches!(poolhouse_node::sys::Listener::bind(dir.path()), Err(Error::Denied(_))));
}
