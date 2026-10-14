//! What a person is shown when asked to sign an act: a sentence that names the key, the person, the numbers and the device the act
//! is about, so the signature is for what was said and an agent cannot put one act under the name of another.

use serde::Serialize;

use super::act::Act;
use super::body::{self, Body};
use super::charter::Charter;
use super::keys::{fingerprint_of, AdminKey, Role, Strength};
use super::pool::GovPool;

/// ``name`` as one line a person reads: at most 40 characters, no control character, in quotes.
pub fn quoted(name: &str) -> String {
    format!("'{}'", name.chars().filter(|c| !c.is_control()).take(40).collect::<String>())
}

fn short(fingerprint: &str) -> String {
    fingerprint.chars().take(12).collect()
}

fn word<T: Serialize>(value: &T) -> String {
    serde_json::to_value(value).ok().and_then(|v| v.as_str().map(str::to_string)).unwrap_or_default()
}

fn plural(n: u64, unit: &str) -> String {
    format!("{n} {unit}{}", if n == 1 { "" } else { "s" })
}

fn device(g: &GovPool, fingerprint: &str) -> String {
    match g.devices().get(fingerprint) {
        Some(d) => format!("{} ({})", quoted(&d.name), short(fingerprint)),
        None => format!("a device this pool does not know ({})", short(fingerprint)),
    }
}

fn key_line(k: &AdminKey) -> String {
    let strength = if k.strength == Strength::Strong { "strong" } else { "weak, which ends a delegate and nothing else" };
    let role = if k.role == Role::Recovery { "the recovery key" } else { "an admin" };
    format!("{} (person {}, {strength}, {}) as {role}", quoted(&k.label), quoted(&k.person), short(&k.fingerprint()))
}

fn listed_key(g: &GovPool, public: &str) -> String {
    match g.state().chain.latest().charter.admin(public) {
        Some(k) => format!("{} (person {}, {})", quoted(&k.label), quoted(&k.person), short(&k.fingerprint())),
        None => format!("a key this pool does not list ({})", short(&fingerprint_of(public))),
    }
}

fn holds_line(h: &body::Holds) -> String {
    let parts: Vec<String> = [
        h.admin_add_h.map(|n| format!("adding an admin {}", plural(n, "hour"))),
        h.admin_remove_h.map(|n| format!("removing an admin {}", plural(n, "hour"))),
        h.handoff_min.map(|n| format!("a handoff {}", plural(n, "minute"))),
        h.dispute_d.map(|n| format!("a dispute {}", plural(n, "day"))),
    ].into_iter().flatten().collect();
    format!("Change how long a change waits before it takes effect: {}.", parts.join(", "))
}

fn threshold_line(t: &body::Thresholds) -> String {
    let parts: Vec<String> = [
        t.routine.map(|n| format!("routine {n}")),
        t.sensitive.map(|n| format!("sensitive {n}")),
        t.unit.map(|u| format!("counted by {}", word(&u))),
    ].into_iter().flatten().collect();
    format!("Change how many sign: {}.", parts.join(", "))
}

/// The sentence a person is asked to accept a charter under: the pool and the computer it starts with.
pub fn founding_line(c: &Charter) -> String {
    let fingerprint = c.first.device().map(|(_, f)| short(&f)).unwrap_or_default();
    format!("Make the pool {} and put the computer {} (certificate {fingerprint}) in it.", quoted(&c.name), quoted(&c.first.name))
}

/// The sentence a person is asked to sign ``act`` under, as this pool reads it now: at most 300 characters.
pub fn describe(act: &Act, g: &GovPool) -> String {
    sentence(act, g, true).chars().take(300).collect()
}

fn sentence(act: &Act, g: &GovPool, deep: bool) -> String {
    let Ok(parsed) = body::parse(act) else { return format!("Sign a {} for the pool.", act.kind.chars().take(24).collect::<String>()) };
    match parsed {
        Body::Admit(a) => {
            let fingerprint = body::cert_of(&a).map(|(_, f)| short(&f)).unwrap_or_default();
            format!("Let the device {} (certificate {fingerprint}) join the pool.", quoted(&a.name))
        }
        Body::Remove(r) => format!("Remove {} from the pool; it stops working with every device at once.", device(g, &r.fingerprint)),
        Body::Leave(d) => format!("Let {} leave the pool.", device(g, &d.fingerprint)),
        Body::AdminAdd(a) => format!("Add the key {} of the pool.", key_line(&a.entry)),
        Body::AdminRemove(d) => {
            let signed = g.acts().values().filter(|a| a.admin_keys().contains(&d.key.as_str())).count();
            format!("Remove the admin key {} from the pool. It keeps {} of the {} acts this key signed.", listed_key(g, &d.key), d.cut.len(), signed.max(d.cut.len()))
        }
        Body::SetThreshold(t) => threshold_line(&t),
        Body::SetHolds(h) => holds_line(&h),
        Body::SetMode(m) => format!("Change who may name admins to {}.", word(&m.mode)),
        Body::Delegate(d) => format!("Let {} admit devices for 30 days.", device(g, &d.fingerprint)),
        Body::DelegateEnd(d) => format!("End {}'s power to admit devices.", device(g, &d.fingerprint)),
        Body::Cancel(t) | Body::Veto(t) => {
            let held = g.acts().get(&t.act).filter(|h| deep && !matches!(h.kind.as_str(), "cancel" | "veto"));
            let what = held.map_or_else(|| format!("the act {}", short(&t.act)), |h| sentence(h, g, false));
            format!("{} this: {what}", if act.kind == "cancel" { "Cancel" } else { "Veto" })
        }
        Body::Found(c) => founding_line(&c),
    }
}
