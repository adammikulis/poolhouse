mod kit;

use kit::*;
use serde_json::{json, Value};
use std::path::Path;
use tempfile::tempdir;

fn make(n: &mut poolhouse_node::node::Node, token: &str, name: &str, extra: Value) -> Value {
    let mut p = json!({"folder": name});
    for (k, v) in extra.as_object().unwrap() {
        p[k] = v.clone();
    }
    req(n, "scratch_new", "demo", token, p)
}

fn listed(n: &mut poolhouse_node::node::Node, token: &str, owner: &str) -> Value {
    req(n, "scratch_ls", "demo", token, json!({"owner": owner}))
}

fn kid(n: &mut poolhouse_node::node::Node, parent: &str, id: &str) -> (String, String) {
    let r = ok(req(n, "register", "demo", parent, json!({"model": "claude-haiku-5-5", "harness": "claude-code", "session": id})));
    (r["name"].as_str().unwrap().into(), r["token"].as_str().unwrap().into())
}

#[test]
fn a_session_makes_lists_resolves_and_removes_its_own_folders() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (alice, at) = session(&mut n, "demo", "a");
    let path = ok(make(&mut n, &at, "work", json!({})))["path"].as_str().unwrap().to_string();
    assert!(Path::new(&path).is_dir() && path.ends_with(&format!("scratch/demo/{alice}/work")));
    std::fs::write(Path::new(&path).join("a.txt"), b"12345").unwrap();
    let rows = ok(listed(&mut n, &at, ""))["folders"].clone();
    assert_eq!((rows[0]["name"].clone(), rows[0]["bytes"].clone(), rows[0]["owner"].clone(), rows[0]["over_limit"].clone(), rows[0]["expired"].clone()),
               (json!("work"), json!(5 + std::fs::metadata(Path::new(&path).join(".scratch.json")).unwrap().len()), json!(alice), json!(false), json!(false)));
    let inside = ok(req(&mut n, "scratch_path", "demo", &at, json!({"folder": "work", "relative": "sub/../a.txt"})))["path"].as_str().unwrap().to_string();
    assert_eq!(inside, format!("{path}/a.txt"));
    assert_eq!(ok(req(&mut n, "scratch_rm", "demo", &at, json!({"folder": "work"})))["removed"], true);
    assert_eq!(ok(req(&mut n, "scratch_rm", "demo", &at, json!({"folder": "work"})))["removed"], false);
    assert!(!Path::new(&path).exists());
}

#[test]
fn a_path_never_leaves_its_folder_and_a_name_is_a_plain_word() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, at) = session(&mut n, "demo", "a");
    let path = ok(make(&mut n, &at, "work", json!({})))["path"].as_str().unwrap().to_string();
    for bad in ["../x", "a/../../x", "/etc/passwd", "..", "sub/../.."] {
        assert_eq!(code(&req(&mut n, "scratch_path", "demo", &at, json!({"folder": "work", "relative": bad}))), "denied", "{bad}");
    }
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink("/tmp", Path::new(&path).join("out")).unwrap();
        assert_eq!(code(&req(&mut n, "scratch_path", "demo", &at, json!({"folder": "work", "relative": "out/x"}))), "denied", "a link out of the folder");
    }
    for bad in ["", "A", "../up", "a/b", ".hidden..x", &"x".repeat(65)] {
        assert_eq!(code(&make(&mut n, &at, bad, json!({}))), "invalid", "{bad:?}");
    }
    assert_eq!(code(&req(&mut n, "scratch_path", "demo", &at, json!({"folder": "../work"}))), "denied");
}

#[test]
fn only_the_owner_or_a_session_above_it_reaches_a_folder_and_a_subagent_has_none() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((lead_name, lead), (_, other)) = (session(&mut n, "demo", "lead"), session(&mut n, "demo", "other"));
    let (kid_name, kt) = kid(&mut n, &lead, "kid");
    assert_eq!(code(&make(&mut n, &kt, "work", json!({}))), "denied", "a subagent has no scratch space");
    ok(make(&mut n, &lead, "mine", json!({})));
    assert_eq!(code(&listed(&mut n, &other, &lead_name)), "denied");
    assert_eq!(code(&req(&mut n, "scratch_path", "demo", &other, json!({"folder": "mine", "owner": lead_name}))), "denied");
    assert_eq!(code(&req(&mut n, "scratch_rm", "demo", &other, json!({"folder": "mine", "owner": lead_name}))), "denied");
    assert_eq!(listed(&mut n, &lead, &kid_name)["ok"], true, "a parent may list its subagent's folders, which are none");
    assert_eq!(ok(listed(&mut n, &lead, &lead_name))["folders"].as_array().unwrap().len(), 1);
}

#[test]
fn folders_are_limited_in_number_and_expired_ones_are_removed() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, at) = session(&mut n, "demo", "a");
    for i in 0..16 {
        ok(make(&mut n, &at, &format!("f{i}"), json!({})));
    }
    assert_eq!(code(&make(&mut n, &at, "one-more", json!({}))), "quota");
    ok(make(&mut n, &at, "f0", json!({})));
    let path = ok(req(&mut n, "scratch_path", "demo", &at, json!({"folder": "f1"})))["path"].as_str().unwrap().to_string();
    std::fs::write(Path::new(&path).join(".scratch.json"), br#"{"version":1,"created_ms":1,"expires_ms":2,"max_bytes":268435456}"#).unwrap();
    let rows = ok(listed(&mut n, &at, ""))["folders"].clone();
    assert_eq!(rows.as_array().unwrap().len(), 15, "the expired folder was removed");
    assert!(!Path::new(&path).exists());
}
