use serde_json::{json, Value};

use crate::Error;

const MECHANISMS: [&str; 3] = [
    "builtin:authenticate",
    "builtin:reset-password,privileged",
    "builtin:authenticate,privileged",
];
const REQUIRED_FALSE: [&str; 2] = ["allow-root", "shared"];
const OPTIONAL_FALSE: [&str; 7] = [
    "password-only",
    "extract-password",
    "entitled",
    "entitled-group",
    "vpn-entitled-group",
    "require-apple-signed",
    "securetoken-only",
];

pub(super) struct RuleIdentity {
    identifier: String,
    requirement: String,
}
impl RuleIdentity {
    #[cfg(test)]
    pub(super) fn fixture() -> Self {
        Self {
            identifier: "app.poolhouse.fixture-installer".into(),
            requirement: "cdhash H\"1111111111111111111111111111111111111111\"".into(),
        }
    }
}

pub(super) fn prepared(identity: &RuleIdentity) -> Value {
    let mut value = json!({
        "version":1, "identifier":identity.identifier, "requirement":identity.requirement,
        "class":"user", "authenticate-user":true, "session-owner":true,
        "allow-root":false, "shared":false, "timeout":0, "tries":3,
        "mechanisms":MECHANISMS,
    });
    for name in OPTIONAL_FALSE {
        value[name] = Value::Bool(false);
    }
    value
}

pub(super) fn verify(value: &Value, identity: &RuleIdentity) -> Result<(), Error> {
    let fields = value.as_object().ok_or(Error::Unavailable)?;
    if identity.identifier.is_empty()
        || identity.identifier.len() > 128
        || identity.requirement.is_empty()
        || identity.requirement.len() > 4096
        || fields.get("identifier").and_then(Value::as_str) != Some(identity.identifier.as_str())
        || fields.get("requirement").and_then(Value::as_str) != Some(identity.requirement.as_str())
        || fields.len() > 32
        || fields.get("class") != Some(&json!("user"))
        || fields.get("authenticate-user") != Some(&json!(true))
        || fields.get("session-owner") != Some(&json!(true))
        || fields.get("tries") != Some(&json!(3))
        || !fields
            .get("timeout")
            .and_then(Value::as_i64)
            .is_some_and(|v| v == 0)
        || fields.get("mechanisms") != Some(&json!(MECHANISMS))
    {
        return Err(Error::Unavailable);
    }
    for name in REQUIRED_FALSE {
        if fields.get(name) != Some(&Value::Bool(false)) {
            return Err(Error::Unavailable);
        }
    }
    for name in OPTIONAL_FALSE {
        if fields.get(name).is_some_and(|v| v != &Value::Bool(false)) {
            return Err(Error::Unavailable);
        }
    }
    for (name, value) in fields {
        if [
            "class",
            "authenticate-user",
            "session-owner",
            "timeout",
            "tries",
            "mechanisms",
            "identifier",
            "requirement",
        ]
        .contains(&name.as_str())
            || REQUIRED_FALSE.contains(&name.as_str())
            || OPTIONAL_FALSE.contains(&name.as_str())
        {
            continue;
        }
        let safe = match name.as_str() {
            "comment" => value.as_str().is_some_and(|s| s.len() <= 1024),
            "version" => value.as_u64() == Some(1),
            "created" | "modified" => value.as_f64().is_some_and(|n| n.is_finite() && n >= 0.0),
            _ => false,
        };
        if !safe {
            return Err(Error::Unavailable);
        }
    }
    Ok(())
}
