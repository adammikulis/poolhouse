mod kit;

use poolhouse_node::fsutil::hex;
use serde_json::{json, Value};
use kit::grants::Spec;
use kit::netkit::{device, Dev};

fn certificate() -> String {
    let key = rcgen::KeyPair::generate().unwrap();
    let params = rcgen::CertificateParams::new(Vec::<String>::new()).unwrap();
    hex(params.self_signed(&key).unwrap().der())
}

fn bind(dev: &Dev, cert: &str, label: &str) -> Value {
    let (name, token) = dev.session("demo", label);
    let params = json!({"cert":cert,"endpoint":"https://127.0.0.1:8770"});
    let draft = dev.ok("service_draft", "", "", params.clone());
    let resource = draft["resource"].as_str().unwrap();
    let spec = Spec::session(&name,"service_bind").device(&dev.fp()).on(resource).uses(1);
    kit::govnode::inject_approved(dev,"demo",&format!("g-{}", &poolhouse_node::fsutil::sha256_hex(label.as_bytes())[..20]),&spec,&name);
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
