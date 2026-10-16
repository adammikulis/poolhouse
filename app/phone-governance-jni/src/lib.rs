//! Android's private bridge to the shared governance verifier.

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::{Mutex, OnceLock};

use jni::objects::{JClass, JString};
use jni::sys::{jlong, jstring};
use jni::JNIEnv;
use poolhouse_node::error::{Error, Result};
use poolhouse_node::fsutil::{hex, random_hex};
use poolhouse_node::gov::phone_verify::{Binding, Engine, Proof, Request, Verified};
use serde::Deserialize;
use serde_json::{json, Value};

const MAX_INPUT: usize = 16 * 1024 * 1024;
const MAX_ENGINES: usize = 4;

struct Held {
    binding: Binding,
    engine: Engine,
    active: Option<(String, Verified)>,
    boot_count: Option<u64>,
}

static ENGINES: OnceLock<Mutex<BTreeMap<String, Held>>> = OnceLock::new();

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Review {
    binding: Binding,
    proof: Proof,
    request: Request,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Token { token: String }

fn wall_ms() -> Result<u64> {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).ok().and_then(|d| u64::try_from(d.as_millis()).ok())
        .ok_or_else(|| Error::Invalid("The phone's clock is unavailable.".into()))
}

fn open_engine(root: &Path, binding: &Binding, boot_count: Option<u64>) -> Result<Engine> {
    #[cfg(target_os = "android")]
    {
        Engine::open_on_boot(root, binding, boot_count.ok_or_else(|| Error::Invalid("The phone's boot counter is unavailable.".into()))?)
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = boot_count;
        Engine::open(root, binding)
    }
}

fn dispatch_on_boot(root: &str, operation: &str, payload: &str, boot_count: Option<u64>) -> Result<Value> {
    if payload.len() > MAX_INPUT || root.len() > 4096 || !Path::new(root).is_absolute() {
        return Err(Error::Invalid("The phone cannot verify this governance request.".into()));
    }
    let mut engines = ENGINES.get_or_init(|| Mutex::new(BTreeMap::new())).lock().map_err(|_| Error::Invalid("The phone verifier must restart.".into()))?;
    match operation {
        "pin" => {
            let binding: Binding = serde_json::from_str(payload)?;
            Engine::pin(Path::new(root), binding)?;
            Ok(json!({}))
        }
        "require-pin" => {
            let binding: Binding = serde_json::from_str(payload)?;
            open_engine(Path::new(root), &binding, boot_count)?;
            Ok(json!({}))
        }
        "review" => {
            let review: Review = serde_json::from_str(payload)?;
            if !engines.contains_key(root) {
                if engines.len() >= MAX_ENGINES {
                    return Err(Error::Invalid("This phone already has four active pool keys.".into()));
                }
                let engine = open_engine(Path::new(root), &review.binding, boot_count)?;
                engines.insert(root.into(), Held { binding: review.binding.clone(), engine, active: None, boot_count });
            }
            let held = engines.get_mut(root).expect("the engine was opened");
            if held.binding != review.binding || held.active.is_some() || held.boot_count != boot_count {
                return Err(Error::Invalid("Finish the phone's active pool request first.".into()));
            }
            let verified = held.engine.review(review.proof, review.request, wall_ms()?)?;
            let token = random_hex(16)?;
            let result = json!({"token": token, "bytes": hex(verified.bytes()), "card": verified.card(), "id": verified.request().id,
                "expires_ms": verified.request().expires_ms.min(verified.request().act.not_after)});
            held.active = Some((token, verified));
            Ok(result)
        }
        "recheck" | "cancel" => {
            let token: Token = serde_json::from_str(payload)?;
            let held = engines.get_mut(root).ok_or_else(|| Error::Invalid("This phone request is no longer active.".into()))?;
            if held.boot_count != boot_count { return Err(Error::Invalid("The phone's boot counter changed while signing.".into())) }
            let (_, verified) = held.active.as_ref().filter(|(t, _)| t == &token.token).ok_or_else(|| Error::Invalid("This phone request is no longer active.".into()))?;
            if operation == "recheck" {
                held.engine.recheck(verified, wall_ms()?)?;
            } else {
                held.active = None;
            }
            Ok(json!({}))
        }
        _ => Err(Error::Invalid("The phone does not support this governance operation.".into())),
    }
}

#[no_mangle]
pub extern "system" fn Java_app_poolhouse_phone_NativeGovernance_call(mut env: JNIEnv, _: JClass, root: JString, operation: JString, payload: JString, boot_count: jlong) -> jstring {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let root: String = env.get_string(&root).map_err(|e| e.to_string())?.into();
        let operation: String = env.get_string(&operation).map_err(|e| e.to_string())?.into();
        let payload: String = env.get_string(&payload).map_err(|e| e.to_string())?.into();
        let boot_count = u64::try_from(boot_count).map_err(|_| "The phone's boot counter is unavailable.".to_string())?;
        dispatch_on_boot(&root, &operation, &payload, Some(boot_count)).map_err(|e| e.to_string())
    }));
    let response = match result {
        Ok(Ok(value)) => value,
        Ok(Err(error)) => json!({"error": error}),
        Err(_) => json!({"error": "The phone verifier could not finish this request."}),
    };
    env.new_string(response.to_string()).map(|s| s.into_raw()).unwrap_or(std::ptr::null_mut())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dispatch(root: &str, operation: &str, payload: &str) -> Result<Value> {
        dispatch_on_boot(root, operation, payload, None)
    }

    #[test]
    fn native_boundary_rejects_arbitrary_operations_and_oversized_evidence() {
        assert!(dispatch("/scratch/phone", "sign", "{}").is_err());
        assert!(dispatch("/scratch/phone", "review", &" ".repeat(MAX_INPUT + 1)).is_err());
        assert!(dispatch("relative/phone", "pin", "{}").is_err());
    }

    #[test]
    fn native_tokens_cannot_be_forged_or_revived() {
        assert!(dispatch("/scratch/phone", "recheck", &json!({"token": "a".repeat(32)}).to_string()).is_err());
        assert!(dispatch("/scratch/phone", "cancel", &json!({"token": "a".repeat(32)}).to_string()).is_err());
    }
}
