use super::{flow, identity, public, response, token, Mode};
use serde_json::json;
use std::path::PathBuf;

const NONCE: &str = "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn";
const CLAIM: &str = "cccccccccccccccccccccccccccccccc";

#[test]
fn only_fixed_request_ids_and_urlsafe_nonces_are_accepted() {
    assert!(identity(&"a".repeat(32)));
    assert!(token(NONCE));
    for invalid in ["../outside", "aa/bb", "?mode=folder", "", &"A".repeat(32)] { assert!(!identity(invalid)); }
    for invalid in ["a", &"x".repeat(129), &"/".repeat(32), &"\r".repeat(32)] { assert!(!token(invalid)); }
}

#[test]
fn only_daemon_mode_and_actual_os_selection_reach_private_completion() {
    for mode in ["file", "folder"] {
        let mut calls = Vec::new();
        let answer = flow(|operation, payload| {
            calls.push((operation.to_string(), payload.clone()));
            Ok(if operation == "begin" { json!({"status":"choosing","mode":mode,"claim":CLAIM}) }
               else { json!({"status":"complete","source":{"id":"watch:opaque","title":"events.jsonl"}}) })
        }, |asked| {
            assert_eq!(asked, if mode == "file" { Mode::File } else { Mode::Folder });
            Ok(Some(PathBuf::from("/private/original/events.jsonl")))
        }, NONCE).unwrap();
        assert_eq!(calls[0], ("begin".to_string(), json!({"nonce":NONCE})));
        assert_eq!(calls[1], ("complete".to_string(), json!({"nonce":NONCE,"claim":CLAIM,"path":"/private/original/events.jsonl"})));
        assert_eq!(answer, json!({"status":"complete","source":{"id":"watch:opaque","title":"events.jsonl"}}));
        assert!(!answer.to_string().contains("private"));
        assert!(!answer.to_string().contains(CLAIM));
    }
}

#[test]
fn cancellation_is_terminal_and_never_submits_a_path() {
    let mut operations = Vec::new();
    let result = flow(|operation, payload| {
        operations.push(operation.to_string());
        if operation == "begin" { Ok(json!({"status":"choosing","mode":"file","claim":CLAIM})) }
        else { assert_eq!(payload, json!({"nonce":NONCE,"claim":CLAIM})); Ok(json!({"status":"cancelled"})) }
    }, |_| Ok(None), NONCE).unwrap();
    assert_eq!(operations, ["begin", "cancel"]);
    assert_eq!(result, json!({"status":"cancelled"}));
}

#[test]
fn duplicate_begin_and_terminal_results_never_reopen_a_picker() {
    for state in [json!({"status":"choosing"}), json!({"status":"cancelled"}),
                  json!({"status":"complete","source":{"id":"watch:opaque","title":"events.csv"}})] {
        let answer = flow(|operation, _| { assert_eq!(operation, "begin"); Ok(state.clone()) },
                          |_| panic!("no repeated native dialog"), NONCE).unwrap();
        assert_eq!(answer, state);
    }
}

#[test]
fn injected_modes_or_extra_fields_never_open_a_picker() {
    for begin in [json!({"status":"choosing","mode":"program","claim":CLAIM}),
                  json!({"status":"choosing","mode":"file","claim":CLAIM,"path":"/private"}),
                  json!({"status":"choosing","mode":"file","claim":"weak"}),
                  json!({"status":"choosing","mode":"file"})] {
        assert!(flow(|_, _| Ok(begin.clone()), |_| panic!("malformed challenge"), NONCE).is_err());
    }
}

#[test]
fn public_replies_reject_secrets_paths_and_unknown_fields() {
    for value in [json!({"status":"complete","source":{"id":"watch:opaque","title":"/private/data"}}),
                  json!({"status":"complete","source":{"id":"watch:opaque","title":"file","path":"/private"}}),
                  json!({"status":"complete","source":{"id":"watch:opaque","title":"file"},"claim":CLAIM}),
                  json!({"status":"complete","source":{"id":"../private","title":"file"}}),
                  json!({"status":"choosing","claim":CLAIM}), json!({"status":"unknown"})] {
        assert!(public(&value).is_err());
    }
}

#[test]
fn native_http_rejects_redirects_truncation_oversized_and_ambiguous_bodies() {
    let data = "{\"status\":\"cancelled\"}";
    let valid = format!("HTTP/1.0 200 OK\r\nContent-Length: {}\r\n\r\n{data}", data.len());
    assert_eq!(response(valid.as_bytes()).unwrap(), json!({"status":"cancelled"}));
    for invalid in [valid.replace("200 OK", "302 Found"), valid.replace("200 OK", "403 Forbidden"),
                    valid.replace("Content-Length:", "Transfer-Encoding:"), valid[..valid.len()-1].to_string(),
                    valid.replace("\r\n\r\n", "\r\nContent-Length: 1\r\n\r\n"), "x".repeat(65537)] {
        assert!(response(invalid.as_bytes()).is_err());
    }
}

#[cfg(unix)]
#[test]
fn native_credential_stays_bound_to_private_file_exact_port_and_safe_token() {
    use std::os::unix::fs::PermissionsExt;
    let mut random = [0_u8; 16];
    getrandom::fill(&mut random).unwrap();
    let root = std::env::temp_dir().join(format!("studio-native-{:x}", u128::from_be_bytes(random)));
    std::fs::create_dir_all(root.join("launch")).unwrap();
    let path = root.join("launch/secret.json");
    std::fs::write(&path, json!({"version":1,"port":9123,"secret":NONCE}).to_string()).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
    assert_eq!(crate::launch::native_secret(&root, 9123).as_deref(), Some(NONCE));
    assert!(crate::launch::native_secret(&root, 9124).is_none());
    std::fs::write(&path, json!({"version":1,"port":9123,"secret":"injected\r\nheader"}).to_string()).unwrap();
    assert!(crate::launch::native_secret(&root, 9123).is_none());
    std::fs::write(&path, json!({"version":1,"port":9123,"secret":NONCE}).to_string()).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
    assert!(crate::launch::native_secret(&root, 9123).is_none());
    std::fs::remove_dir_all(root).unwrap();
}
