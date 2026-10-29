use super::*;
use serde_json::{json, Value};
use std::cell::{Cell, RefCell};

struct Mock {
    events: RefCell<Vec<&'static str>>,
    serial: Cell<u32>,
    policy: Value,
    identity: policy::RuleIdentity,
    fail: Option<&'static str>,
}
impl Mock {
    fn new() -> Self {
        Self {
            events: RefCell::new(vec![]),
            serial: Cell::new(0),
            policy: policy::prepared(&policy::RuleIdentity::fixture()),
            identity: policy::RuleIdentity::fixture(),
            fail: None,
        }
    }
    fn event(&self, name: &'static str) -> Result<(), Error> {
        self.events.borrow_mut().push(name);
        if self.fail == Some(name) {
            return Err(Error::Unavailable);
        }
        Ok(())
    }
}
impl Api for Mock {
    type Reference = u32;
    fn create_fresh(&self) -> Result<u32, Error> {
        self.event("create-fresh")?;
        let value = self.serial.get() + 1;
        self.serial.set(value);
        Ok(value)
    }
    fn externalize(&self, _: &u32) -> Result<ExternalForm, Error> {
        self.event("externalize")?;
        Ok(ExternalForm(Box::new([42; 32])))
    }
    fn import(&self, _: &ExternalForm) -> Result<u32, Error> {
        self.event("import")?;
        Ok(9)
    }
    fn identity(&self) -> &policy::RuleIdentity {
        &self.identity
    }
    fn policy(&self) -> Result<Value, Error> {
        self.event("policy")?;
        Ok(self.policy.clone())
    }
    fn request(&self, _: &u32) -> Result<(), Error> {
        self.event("request-fixed-right")
    }
    fn destroy(&self, _: u32) -> Result<(), Error> {
        self.event("destroy-rights")
    }
}
fn context() -> Context {
    Context {
        digest: [1; 32],
        head: [2; 32],
        generation: 3,
        nonce: [4; 32],
        expires_at: 200,
        owner_uid: 501,
        audit_session: 7,
        gui_hash: [8; 32],
    }
}
fn caller() -> VerifiedCaller {
    VerifiedCaller {
        owner_uid: 501,
        audit_session: 7,
        gui_hash: [8; 32],
    }
}
fn form() -> ExternalForm {
    ExternalForm(Box::new([42; 32]))
}

#[test]
fn person_approval_is_bound_to_exact_phone_effect() {
    let effect = crate::receipt::PhoneBootstrapReceipt { authority: [1;32], installation: [2;32], installation_generation: 3,
        request: [4;32], nonce: [5;32], transcript: [6;32], node: [7;32], tls: [8;32], phone_key: [9;32], offer: [10;32],
        token_hash: [11;32], phone_id: [12;16], offer_expires: 200, scope: crate::receipt::Scope::Unfounded,
        authority_head: [13;32], authority_generation: 14, prepared_envelope_head: [15;32], prepared_envelope_generation: 16, committed_at: 100 };
    let mut bound = context();
    bound.digest = effect.decision_digest().unwrap(); bound.head = effect.prepared_envelope_head;
    bound.generation = effect.prepared_envelope_generation; bound.nonce = effect.nonce; bound.expires_at = effect.offer_expires;
    assert!(PersonApproval(bound.clone()).matches_phone_bootstrap(&effect, 100));
    let mut changed = effect.clone(); changed.phone_id[0] ^= 1;
    assert!(!PersonApproval(bound.clone()).matches_phone_bootstrap(&changed, 100));
    let mut changed = effect.clone(); changed.authority_head[0] ^= 1;
    assert!(!PersonApproval(bound.clone()).matches_phone_bootstrap(&changed, 100));
    assert!(!PersonApproval(bound).matches_phone_bootstrap(&effect, 200));
}

#[test]
fn exact_prepared_policy_and_known_normalization_only() {
    let mut value = policy::prepared(&policy::RuleIdentity::fixture());
    assert!(policy::verify(&value, &policy::RuleIdentity::fixture()).is_ok());
    for name in [
        "password-only",
        "extract-password",
        "entitled",
        "entitled-group",
        "vpn-entitled-group",
        "require-apple-signed",
        "securetoken-only",
    ] {
        value.as_object_mut().unwrap().remove(name);
    }
    value["created"] = json!(100.5);
    value["modified"] = json!(101);
    value["version"] = json!(1);
    value["comment"] = json!("Native approval");
    assert!(policy::verify(&value, &policy::RuleIdentity::fixture()).is_ok());
}
#[test]
fn missing_or_wrong_typed_core_guards_refuse() {
    for name in [
        "class",
        "authenticate-user",
        "session-owner",
        "allow-root",
        "shared",
        "timeout",
        "tries",
        "mechanisms",
    ] {
        let mut value = policy::prepared(&policy::RuleIdentity::fixture());
        value.as_object_mut().unwrap().remove(name);
        assert!(
            policy::verify(&value, &policy::RuleIdentity::fixture()).is_err(),
            "{name}"
        );
        let mut value = policy::prepared(&policy::RuleIdentity::fixture());
        value[name] = json!("false");
        assert!(
            policy::verify(&value, &policy::RuleIdentity::fixture()).is_err(),
            "{name}"
        );
    }
}
#[test]
fn authorization_policy_substitutions_and_unknown_delegation_refuse() {
    for name in [
        "allow-root",
        "shared",
        "password-only",
        "extract-password",
        "entitled",
        "entitled-group",
        "vpn-entitled-group",
        "require-apple-signed",
        "securetoken-only",
    ] {
        let mut value = policy::prepared(&policy::RuleIdentity::fixture());
        value[name] = json!(true);
        assert!(policy::verify(&value, &policy::RuleIdentity::fixture()).is_err());
    }
    for name in [
        "group",
        "rule",
        "rules",
        "k-of-n",
        "identifier",
        "requirement",
        "hash",
        "unknown",
    ] {
        let mut value = policy::prepared(&policy::RuleIdentity::fixture());
        value[name] = json!("anything");
        assert!(policy::verify(&value, &policy::RuleIdentity::fixture()).is_err());
    }
}
#[test]
fn cached_timeout_retry_or_mechanism_substitution_refuse() {
    for (name, changed) in [
        ("timeout", json!(1)),
        ("timeout", json!(0.0)),
        ("timeout", json!(false)),
        ("timeout", json!("0")),
        ("tries", json!(4)),
        ("tries", json!(3.0)),
        ("mechanisms", json!([])),
        ("mechanisms", json!(["builtin:reset-password,privileged"])),
    ] {
        let mut value = policy::prepared(&policy::RuleIdentity::fixture());
        value[name] = changed;
        assert!(policy::verify(&value, &policy::RuleIdentity::fixture()).is_err());
    }
    let mut value = policy::prepared(&policy::RuleIdentity::fixture());
    value["mechanisms"].as_array_mut().unwrap().reverse();
    assert!(policy::verify(&value, &policy::RuleIdentity::fixture()).is_err());
}
#[test]
fn metadata_is_bounded_and_cannot_hide_policy() {
    for (name, changed) in [
        ("version", json!(2)),
        ("created", json!(-1)),
        ("modified", json!("100")),
        ("comment", json!("x".repeat(1025))),
    ] {
        let mut value = policy::prepared(&policy::RuleIdentity::fixture());
        value[name] = changed;
        assert!(policy::verify(&value, &policy::RuleIdentity::fixture()).is_err());
    }
}
#[test]
fn each_native_card_creates_fresh_reference_and_exports_once() {
    let api = Mock::new();
    for _ in 0..2 {
        let mut session = GuiSession::after_card(&api, VerifiedCard(context())).unwrap();
        assert!(session.export(&context()).is_ok());
        assert!(session.export(&context()).is_err());
        session.finish().unwrap();
    }
    assert_eq!(api.serial.get(), 2);
    assert_eq!(
        &*api.events.borrow(),
        &[
            "create-fresh",
            "externalize",
            "destroy-rights",
            "create-fresh",
            "externalize",
            "destroy-rights"
        ]
    );
}
#[test]
fn wrong_card_binding_and_cancellation_never_export_and_destroy_rights() {
    let api = Mock::new();
    {
        let mut session = GuiSession::after_card(&api, VerifiedCard(context())).unwrap();
        let mut changed = context();
        changed.nonce = [9; 32];
        assert!(session.export(&changed).is_err());
    }
    assert_eq!(&*api.events.borrow(), &["create-fresh", "destroy-rights"]);
}
#[test]
fn wrong_caller_or_expired_request_cannot_import_or_prompt() {
    for kind in 0..4 {
        let api = Mock::new();
        let mut caller = caller();
        match kind {
            0 => caller.owner_uid = 502,
            1 => caller.audit_session = 8,
            2 => caller.gui_hash = [9; 32],
            _ => {}
        };
        assert!(authorize(
            &api,
            form(),
            &context(),
            &caller,
            if kind == 3 { 200 } else { 100 },
            || Ok((context(), 101))
        )
        .is_err());
        assert!(api.events.borrow().is_empty());
    }
}
#[test]
fn missing_or_changed_policy_refuses_before_reference_import() {
    let mut api = Mock::new();
    api.policy["shared"] = json!(true);
    assert!(authorize(&api, form(), &context(), &caller(), 100, || Ok((
        context(),
        101
    )))
    .is_err());
    assert_eq!(&*api.events.borrow(), &["policy"]);
}
#[test]
fn helper_requests_right_itself_then_destroys_before_issuing_bound_proof() {
    let api = Mock::new();
    let proof = authorize(&api, form(), &context(), &caller(), 100, || {
        Ok((context(), 101))
    })
    .unwrap();
    assert_eq!(proof.0, context());
    assert_eq!(
        &*api.events.borrow(),
        &["policy", "import", "request-fixed-right", "destroy-rights"]
    );
}
#[test]
fn denial_or_destroy_failure_never_returns_approval() {
    for name in ["request-fixed-right", "destroy-rights"] {
        let mut api = Mock::new();
        api.fail = Some(name);
        assert!(authorize(&api, form(), &context(), &caller(), 100, || Ok((
            context(),
            101
        )))
        .is_err());
        assert_eq!(api.events.borrow().last(), Some(&"destroy-rights"));
    }
}
#[test]
fn changed_state_cancel_or_clock_expiry_after_auth_refuses() {
    for kind in 0..5 {
        let api = Mock::new();
        let mut changed = context();
        if kind == 0 {
            changed.head = [9; 32];
        }
        let result = authorize(&api, form(), &context(), &caller(), 100, || {
            if kind == 1 {
                return Err(Error::Unauthorized);
            }
            if kind == 2 {
                changed.nonce = [9; 32];
            }
            Ok((
                changed,
                if kind == 3 {
                    200
                } else if kind == 4 {
                    99
                } else {
                    101
                },
            ))
        });
        assert!(result.is_err());
        assert_eq!(api.events.borrow().last(), Some(&"destroy-rights"));
    }
}

#[test]
fn rule_creator_identity_must_match_exact_protected_identity() {
    for kind in 0..5 {
        let mut value = policy::prepared(&policy::RuleIdentity::fixture());
        match kind {
            0 => {
                value.as_object_mut().unwrap().remove("identifier");
            }
            1 => {
                value.as_object_mut().unwrap().remove("requirement");
            }
            2 => {
                let a = value["identifier"].clone();
                value["identifier"] = value["requirement"].clone();
                value["requirement"] = a;
            }
            3 => value["requirement"] = json!("identifier app.poolhouse.fixture-installer"),
            _ => value["identifier"] = json!("app.poolhouse.other-installer"),
        }
        assert!(policy::verify(&value, &policy::RuleIdentity::fixture()).is_err());
    }
}
#[test]
fn exact_creator_metadata_never_substitutes_for_policy_guards() {
    let mut value = policy::prepared(&policy::RuleIdentity::fixture());
    value["shared"] = json!(true);
    assert!(policy::verify(&value, &policy::RuleIdentity::fixture()).is_err());
}
