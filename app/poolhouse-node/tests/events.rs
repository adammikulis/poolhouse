mod kit;

use kit::*;
use poolhouse_node::node::Node;
use serde_json::{json, Value};
use tempfile::tempdir;

fn tell(n: &mut Node, token: &str, event: &str, subject: &str, detail: &str) -> Value {
    req(n, "audit_event", "demo", token, json!({"event": event, "subject": subject, "detail": detail}))
}

fn audit(n: &mut Node, token: &str) -> Vec<Value> {
    ok(req(n, "read", "demo", token, json!({"kind": "audit"})))["entries"].as_array().unwrap().clone()
}

#[test]
fn a_session_tells_the_board_of_an_event_from_the_list_under_its_own_name() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let ((name, token), (_, other)) = (session(&mut n, "demo", "a"), session(&mut n, "demo", "b"));
    ok(tell(&mut n, &token, "agent.effort", "high", "ceiling high, 4 reason characters"));
    ok(tell(&mut n, &token, "authority.set_denied", "set person ALL", "only a lead or a person changes authority"));
    let rows = audit(&mut n, &other);
    let found: Vec<(&str, &str, &str, &str)> = rows.iter()
        .map(|e| (e["sender"].as_str().unwrap(), e["fields"]["event"].as_str().unwrap(), e["fields"]["subject"].as_str().unwrap(), e["fields"]["detail"].as_str().unwrap()))
        .filter(|(_, event, _, _)| event.starts_with("agent.") || event.starts_with("authority."))
        .collect();
    assert_eq!(found, vec![(name.as_str(), "agent.effort", "high", "ceiling high, 4 reason characters"),
                           (name.as_str(), "authority.set_denied", "set person ALL", "only a lead or a person changes authority")]);
}

#[test]
fn an_event_that_is_not_on_the_list_a_bad_token_or_a_named_sender_writes_nothing() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    let before = audit(&mut n, &token).len();
    for event in ["lease_takeover", "retire", "rebind", "board.create", "", "agent.effort\nretire"] {
        assert_eq!(code(&tell(&mut n, &token, event, "x", "y")), "invalid", "{event:?}");
    }
    assert_eq!(code(&tell(&mut n, "not-a-token", "agent.effort", "x", "y")), "denied");
    let named = req(&mut n, "audit_event", "demo", &token, json!({"event": "agent.effort", "subject": "x", "detail": "y", "sender": "someone-else"}));
    assert_eq!(code(&named), "denied");
    assert_eq!(audit(&mut n, &token).len(), before);
}

#[test]
fn what_a_session_writes_in_an_event_is_one_clean_line_of_at_most_three_hundred_characters() {
    let dir = tempdir().unwrap();
    let mut n = node(dir.path());
    let (_, token) = session(&mut n, "demo", "a");
    ok(tell(&mut n, &token, "agent.effort", "low\nsecond line", &"x".repeat(900)));
    let rows = audit(&mut n, &token);
    let row = rows.iter().find(|e| e["fields"]["event"] == "agent.effort").unwrap();
    assert_eq!(row["fields"]["subject"], "lowsecond line");
    assert_eq!(row["fields"]["detail"].as_str().unwrap().len(), 300);
}
