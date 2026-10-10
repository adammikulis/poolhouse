//! What a device is told when a member refuses it at the handshake: the reason a person can act on, not a bare TLS alert.

mod kit;

use kit::netkit::*;
use serde_json::json;

#[test]
fn a_device_put_out_of_the_pool_is_told_so_when_it_next_dials() {
    let (a, b) = (device(), device());
    a.join_with_code(&b);
    b.net.sync_once(true).unwrap();
    a.ok("member_remove", "", &a.token, json!({"fingerprint": b.fp()}));
    b.net.sync_once(true).unwrap();
    let error = b.member(&a.fp()).unwrap()["last_error"].as_str().unwrap().to_string();
    assert!(error.contains("put this device out of the pool"), "{error}");
}
