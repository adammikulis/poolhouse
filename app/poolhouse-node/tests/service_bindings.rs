mod kit;

use poolhouse_node::fsutil::hex;
use serde_json::{json, Value};
use kit::grants::Spec;
use kit::netkit::{device, Dev};

struct Service { cert: String, key: Vec<u8> }

fn certificate() -> Service {
    let key = rcgen::KeyPair::generate().unwrap();
    let params = rcgen::CertificateParams::new(Vec::<String>::new()).unwrap();
    Service { cert: hex(params.self_signed(&key).unwrap().der()), key: key.serialize_der() }
}

fn proof(service: &Service, draft: &Value) -> String {
    let random = ring::rand::SystemRandom::new();
    let key = ring::signature::EcdsaKeyPair::from_pkcs8(&ring::signature::ECDSA_P256_SHA256_ASN1_SIGNING, &service.key, &random).unwrap();
    hex(key.sign(&random, &poolhouse_node::fsutil::unhex(draft["proof_bytes"].as_str().unwrap()).unwrap()).unwrap().as_ref())
}

fn prepared(service: &Service, draft: &Value) -> Value {
    json!({"cert":service.cert,"endpoint":"https://127.0.0.1:8770", "challenge":draft["challenge"],"proof":proof(service,draft)})
}

fn bind(dev: &Dev, service: &Service, label: &str) -> Value {
    let (name, token) = dev.session("demo", label);
    let mut params = json!({"cert":service.cert,"endpoint":"https://127.0.0.1:8770"});
    let draft = dev.ok("service_draft", "", "", params.clone());
    let resource = draft["resource"].as_str().unwrap();
    let spec = Spec::session(&name,"service_bind").device(&dev.fp()).on(resource).uses(1);
    kit::govnode::inject_approved(dev,"demo",&format!("g-{}", &poolhouse_node::fsutil::sha256_hex(label.as_bytes())[..20]),&spec,&name);
    params["challenge"] = draft["challenge"].clone();
    params["proof"] = json!(proof(service,&draft));
    let bound = dev.ok("service_bind","",&token,params.clone());
    assert_eq!(bound,dev.ok("service_bind","",&token,params));
    bound
}

#[test]
fn signed_service_exchange_and_fresh_authorization_use_the_canonical_member() {
    let (a,b) = (device(),device()); a.know(&b);
    let bound = bind(&b,&certificate(),"service");
    kit::govnode::sync(&a);
    let listed = a.ok("service_members","","",json!({}));
    assert!(listed["services"].as_array().unwrap().contains(&bound));
    let authorized = a.ok("service_authorize","","",json!({"fingerprint":bound["fingerprint"]}));
    assert_eq!(authorized,bound);
    assert_eq!(authorized["device"],b.fp());
    assert_ne!(authorized["device"],authorized["fingerprint"]);
    let mut no_network = a.node.lock().unwrap();
    let result = poolhouse_node::poolapi::dispatch(&mut no_network,"service_authorize","",json!({"fingerprint":bound["fingerprint"]}).as_object().unwrap());
    assert!(result.is_err());
    drop(no_network);
    let own = bind(&a,&certificate(),"own-service");
    let mut open = kit::govnode::link(&b,&a);
    let request = json!({"op":"service_binding","fingerprint":own["fingerprint"],"generation":own["generation"]});
    assert!(open.call(&request).is_ok());
    a.ok("member_remove","",&a.token,json!({"fingerprint":b.fp()}));
    assert!(open.call(&request).is_err());
    assert_eq!(a.call("service_authorize","","",json!({"fingerprint":bound["fingerprint"]}))["ok"],false);
}

#[test]
fn cached_certificate_is_refused_after_source_binding_replacement_or_reset() {
    let (a,b) = (device(),device()); a.know(&b);
    let first = bind(&b,&certificate(),"first-service"); kit::govnode::sync(&a);
    assert_eq!(a.call("service_authorize","","",json!({"fingerprint":first["fingerprint"]}))["ok"],true);
    let second = bind(&b,&certificate(),"second-service");
    assert_ne!(first["generation"],second["generation"]);
    assert_eq!(a.call("service_authorize","","",json!({"fingerprint":first["fingerprint"]}))["ok"],false);
    kit::govnode::sync(&a);
    assert_eq!(a.ok("service_authorize","","",json!({"fingerprint":second["fingerprint"]})),second);
    b.node.lock().unwrap().services = poolhouse_node::services::Services::new().unwrap();
    assert_eq!(a.call("service_authorize","","",json!({"fingerprint":second["fingerprint"]}))["ok"],false);
}

#[test]
fn a_member_cannot_relay_another_devices_service_binding_or_forge_fields() {
    let (a,b) = (device(),device()); a.know(&b);
    bind(&b,&certificate(),"service");
    let signed = b.node.lock().unwrap().own_service_binding();
    let mut peer = kit::govnode::link(&b,&a);
    let mut claimed = signed.clone(); claimed["body"]["device"] = json!(a.fp());
    let pool = b.pool();
    assert!(peer.call(&json!({"op":"members","pool":pool,"service":claimed})).is_err());
    assert!(peer.call(&json!({"op":"service_binding","fingerprint":signed["body"]["fingerprint"],"generation":signed["body"]["generation"],"owner":"person"})).is_err());
    let mut stranger = kit::govnode::link(&device(),&a);
    assert!(stranger.call(&json!({"op":"service_binding","fingerprint":signed["body"]["fingerprint"],"generation":signed["body"]["generation"]})).is_err());
}

#[test]
fn authorized_service_key_renews_after_rotation_and_actual_node_restart_without_a_grant() {
    let dev = device(); dev.found_if_unfounded();
    let service = certificate(); let original = bind(&dev,&service,"service");
    let old = dev.ok("service_draft","","",json!({"cert":service.cert,"endpoint":"https://127.0.0.1:8770"}));
    dev.node.lock().unwrap().host("pool").unwrap().board.rotate().unwrap();
    let Dev { dir, node, net, .. } = dev;
    let stopped = std::sync::Arc::downgrade(&node);
    net.shutdown(); drop(node); drop(net);
    kit::netkit::eventually("the old node released its instance",poolhouse_node::peer::IO_TIMEOUT.as_secs(),||stopped.upgrade().is_none());
    let mut node = poolhouse_node::node::Node::open(dir.path()).unwrap();
    assert_eq!(kit::req(&mut node,"service_bind","","",prepared(&service,&old))["ok"],false);
    let draft = kit::req(&mut node,"service_draft","","",json!({"cert":service.cert,"endpoint":"https://127.0.0.1:8770"}));
    assert_eq!(draft["ok"],true);
    let draft = &draft["result"];
    assert_ne!(draft["binding"]["generation"],original["generation"]);
    let result = kit::req(&mut node,"service_bind","","",prepared(&service,draft));
    assert_eq!(result["ok"],true,"{result}");
    assert_eq!(result["result"]["device"],original["device"]);
}

#[test]
fn superseded_capability_and_wrong_key_challenge_and_process_do_not_renew() {
    let dev = device(); dev.found_if_unfounded();
    let old = certificate(); bind(&dev,&old,"old-service");
    let current = certificate(); bind(&dev,&current,"new-service");
    dev.node.lock().unwrap().host("pool").unwrap().board.rotate().unwrap();
    assert_eq!(dev.node.lock().unwrap().view("pool").unwrap().iter().filter(|entry| !entry.foreign && entry.fields["event"] == "service_capability").count(),1);
    dev.node.lock().unwrap().services = poolhouse_node::services::Services::new().unwrap();
    let draft = dev.ok("service_draft","","",json!({"cert":old.cert,"endpoint":"https://127.0.0.1:8770"}));
    assert_eq!(dev.call("service_bind","","",prepared(&old,&draft))["ok"],false);
    let draft = dev.ok("service_draft","","",json!({"cert":current.cert,"endpoint":"https://127.0.0.1:8770"}));
    let mut wrong = prepared(&current,&draft); wrong["proof"] = json!(proof(&old,&draft));
    assert_eq!(dev.call("service_bind","","",wrong)["ok"],false);
    let mut wrong = prepared(&current,&draft); wrong["challenge"] = json!("0".repeat(32));
    assert_eq!(dev.call("service_bind","","",wrong)["ok"],false);
    let request = json!({"v":1,"id":1,"method":"service_bind","params":prepared(&current,&draft)});
    assert_eq!(dev.net.call(&request,&poolhouse_node::lineage::Peer::none())["ok"],false);
    assert_eq!(dev.call("service_bind","","",prepared(&current,&draft))["ok"],true);
}

#[test]
fn concurrent_renewal_attaches_to_one_publication_and_session_audit_cannot_approve() {
    let dev = device(); dev.found_if_unfounded();
    let service = certificate(); bind(&dev,&service,"service");
    assert_eq!(dev.call("post","demo",&dev.token,json!({"kind":"audit","fields":{"event":"service_capability","subject":"forged"}}))["ok"],false);
    let foreign = device(); dev.know(&foreign);
    foreign.node.lock().unwrap().record_event("service_capability",&format!("service:{}","f".repeat(64)),"foreign marker").unwrap();
    kit::govnode::sync(&dev);
    dev.node.lock().unwrap().services = poolhouse_node::services::Services::new().unwrap();
    let draft = dev.ok("service_draft","","",json!({"cert":service.cert,"endpoint":"https://127.0.0.1:8770"}));
    let params = prepared(&service,&draft);
    let (one,two) = std::thread::scope(|scope| {
        let one = scope.spawn(||dev.call("service_bind","","",params.clone()));
        let two = scope.spawn(||dev.call("service_bind","","",params.clone()));
        (one.join().unwrap(),two.join().unwrap())
    });
    assert_eq!(one["ok"],true); assert_eq!(two,one);
    let mut node = dev.node.lock().unwrap();
    let count = node.view("pool").unwrap().iter().filter(|entry| !entry.foreign && entry.fields["event"] == "service_bind").count();
    assert_eq!(count,2);
}

#[test]
fn revoked_initial_grant_cannot_establish_renewal_authority() {
    let dev = device(); dev.found_if_unfounded(); let service = certificate();
    let (name,token) = dev.session("demo","revoked-service");
    let draft = dev.ok("service_draft","","",json!({"cert":service.cert,"endpoint":"https://127.0.0.1:8770"}));
    let spec = Spec::session(&name,"service_bind").device(&dev.fp()).on(draft["resource"].as_str().unwrap()).uses(1);
    let gid = "g-1234567890123456";
    kit::govnode::inject_approved(&dev,"demo",gid,&spec,&name);
    let person = kit::govnode::authority_person(&dev);
    dev.ok("grant_revoke","demo",&token,person.revoke_params("demo",gid,"cancelled"));
    assert_eq!(dev.call("service_bind","",&token,prepared(&service,&draft))["ok"],false);
    assert!(dev.node.lock().unwrap().own_service_binding().is_null());
}

#[test]
fn failed_authoritative_marker_append_leaves_no_live_binding() {
    let dev = device(); dev.found_if_unfounded(); let service = certificate();
    let (name,token) = dev.session("demo","full-service");
    let draft = dev.ok("service_draft","","",json!({"cert":service.cert,"endpoint":"https://127.0.0.1:8770"}));
    let spec = Spec::session(&name,"service_bind").device(&dev.fp()).on(draft["resource"].as_str().unwrap()).uses(1);
    kit::govnode::inject_approved(&dev,"demo","g-2234567890123456",&spec,&name);
    {
        let mut node = dev.node.lock().unwrap();
        let board = poolhouse_node::board::Board::open_bounded(&node.dir.join("boards/pool/log"),"pool",&node.pool,node.key.clone(),
            poolhouse_node::logbounds::Bounds { rows:0, ..Default::default() }).unwrap();
        node.host("pool").unwrap().board = board;
    }
    assert_eq!(dev.call("service_bind","",&token,prepared(&service,&draft))["ok"],false);
    assert!(dev.node.lock().unwrap().own_service_binding().is_null());
    assert_eq!(dev.ok("service_members","","",json!({}))["services"],json!([]));
}
