//! Governed pools on real nodes: a node that founds a pool for a rig's person, and a pool whose clock is moved by hand.

use std::sync::Arc;

use poolhouse_node::gov::signer::RigSigner;
use serde_json::json;

use super::netkit::Dev;

/// A node on this device that signs for ``signer`` and founds a pool.
pub fn found(dev: &Dev, signer: Arc<RigSigner>, policy: &str) -> String {
    found_for(dev, signer, policy, "")
}

/// Like `found`, for the project ``project`` (a project key, or empty).
pub fn found_for(dev: &Dev, signer: Arc<RigSigner>, policy: &str, project: &str) -> String {
    dev.node.lock().unwrap().set_signer(signer);
    let reply = dev.ok("pool_create", "", &dev.token, json!({"title": "home", "policy": policy, "project": project}));
    reply["pool"].as_str().unwrap().to_string()
}

/// Freeze the awake clock of the device's pool and move it on by ``ms``.
pub fn pass(dev: &Dev, ms: i64) {
    let mut n = dev.node.lock().unwrap();
    n.members.gov().unwrap().clock.advance_ms(ms);
    n.gov_tick().unwrap();
}
