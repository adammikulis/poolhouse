//! What a person is shown when asked to sign an act: a sentence that names the key, the person, the numbers and the device the act
//! is about, so the signature is for what was said and an agent cannot put one act under the name of another.

use serde::Serialize;

use super::act::Act;
use super::body::{self, Body};
use super::charter::{floor_class, Charter, Class};
use super::keys::{fingerprint_of, AdminKey, Level, Role};
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

fn level_words(level: Level) -> &'static str {
    match level {
        Level::Weak => "weak, which ends a delegate and nothing else",
        other => other.name(),
    }
}

fn key_line(k: &AdminKey) -> String {
    let role = if k.role == Role::Recovery { "the recovery key" } else { "an admin" };
    format!("{} (person {}, {}, {}) as {role}", quoted(&k.label), quoted(&k.person), level_words(k.assurance), short(&k.fingerprint()))
}

fn floors_line(f: &body::Floors) -> String {
    let parts: Vec<String> = [("routine", f.routine), ("sensitive", f.sensitive), ("governance", f.governance)].into_iter().filter_map(|(name, level)| level.map(|l| format!("{name} acts from {}", l.shown()))).collect();
    format!("Change which keys count: {}.", parts.join(", "))
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
    format!("Make the pool {} and put the computer {} (certificate {fingerprint}) in it. It is approved from {}.", quoted(&c.name), quoted(&c.first.name), c.floors.sensitive.shown())
}

/// The `LEVEL:` line of ``act``: where this pool's floor for the kind of act puts the approval, and what that does not stop.
fn level_line(act: &Act, g: &GovPool) -> String {
    let c = &g.state().chain.latest().charter;
    let class = floor_class(&act.kind).unwrap_or(Class::Routine);
    let floor = c.floors.of(class);
    let how = match floor {
        Level::Phone => "from your phone.",
        Level::Native => "from this computer, protected. Only root on this computer could approve it without asking.",
        Level::Computer | Level::Weak => "from this computer. A program running as you could approve it without asking; a phone would stop that.",
    };
    let wait = if class == Class::Governance && floor <= Level::Computer { " Signed by computer keys alone it waits 7 days, and a key can cancel it." } else { "" };
    format!("LEVEL: This change is approved {how}{wait}")
}

/// What a person is asked to sign ``act`` under, as this pool reads it now: a sentence of at most 300 characters, then the `LEVEL:` line,
/// which the cut never reaches.
pub fn describe(act: &Act, g: &GovPool) -> String {
    compose(&sentence(act, g, true), &level_line(act, g))
}

/// ``said`` cut at 300 characters, then ``level`` on a line of its own: the cut never reaches the line.
pub fn compose(said: &str, level: &str) -> String {
    format!("{}\n{level}", said.chars().take(300).collect::<String>())
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
        Body::SetFloors(f) => floors_line(&f),
        Body::RecoveryReplace(a) => format!("Replace the recovery key with {}. The old recovery key can cancel this for 7 days.", key_line(&a.entry)),
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
