use crate::error::Result;
use super::{bad, Act, Binding, GovPool};
use super::super::body::{self, Body};
use super::super::charter::{Charter, Class, Mode, Unit};
use super::super::keys::{fingerprint_of, AdminKey, Alg, Role, Strength};

fn quote(text: &str) -> String {
    let mut out = String::from("\u{2068}\"");
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            c if c.is_control() || matches!(c, '\u{061c}' | '\u{200e}' | '\u{200f}' | '\u{2028}'..='\u{202e}' | '\u{2066}'..='\u{2069}') => {
                use std::fmt::Write;
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push_str("\"\u{2069}");
    out
}

fn unit(unit: Unit) -> &'static str {
    match unit { Unit::Person => "person", Unit::Key => "key" }
}

fn key(entry: &AdminKey) -> String {
    format!("{}: {}; person {}; {}; {}; key {}; listed since epoch {}",
        match entry.role { Role::Admin => "Admin", Role::Recovery => "Recovery key" }, quote(&entry.label), quote(&entry.person),
        match entry.alg { Alg::Ed25519 => "Ed25519", Alg::P256 => "P-256" },
        match entry.strength { Strength::Strong => "strong", Strength::Weak => "weak" }, fingerprint_of(&entry.key), entry.since)
}

fn terms(charter: &Charter) -> Result<String> {
    let Some((_, fingerprint)) = charter.first.device() else { return bad("The phone cannot identify the pool's first device.") };
    let mut lines = vec![format!("Current pool terms: {} (epoch {})", quote(&charter.name), charter.epoch),
        format!("Joining: {}", charter.policy.name()),
        format!("Governed by: {}", match charter.mode { Mode::Admins => "admins", Mode::Members => "members" }),
        format!("First device: {}; certificate {fingerprint}", quote(&charter.first.name)),
        format!("Project: {}", if charter.project.is_empty() { "none" } else { &charter.project })];
    lines.extend(charter.admins.iter().map(key));
    lines.push(format!("Routine approvals: {}", charter.thresholds.routine));
    lines.push(format!("Sensitive approvals: {}{}", charter.needed(Class::Sensitive),
        if charter.thresholds.sensitive == 0 { " (automatic: two once two people hold strong admin keys)" } else { "" }));
    lines.push(format!("Count approvals by: {}", unit(charter.thresholds.unit)));
    lines.push(format!("Wait before adding an admin: {} hours", charter.holds.admin_add_h));
    lines.push(format!("Wait before removing an admin: {} hours", charter.holds.admin_remove_h));
    lines.push(format!("Wait for a handoff: {} minutes", charter.holds.handoff_min));
    lines.push(format!("Dispute wait: {} days", charter.holds.dispute_d));
    lines.push(format!("Recovery delay: {} days", charter.recover_delay_days));
    if charter.succession.is_some() || charter.from.is_some() {
        return bad("This phone does not know how to show these pool succession terms.");
    }
    Ok(lines.join("\n"))
}

fn named_device(pool: &GovPool, fingerprint: &str) -> String {
    let name = pool.state().roster.devices.get(fingerprint).map(|d| quote(&d.name)).unwrap_or_else(|| "a device not listed on this phone".into());
    format!("{name}; certificate {fingerprint}")
}

fn change(act: &Act, pool: &GovPool, depth: usize) -> Result<String> {
    Ok(match body::parse(act)? {
        Body::Admit(admit) => {
            let (_, fingerprint) = body::cert_of(&admit)?;
            format!("Admit device {}\nCertificate: {fingerprint}\nJoining method: {}\nLocal network checked: {}", quote(&admit.name), admit.how, if admit.via == "segment" { "yes" } else { "no" })
        }
        Body::Remove(remove) => {
            let mut lines = vec![format!("Remove {}", named_device(pool, &remove.fingerprint)), "Retain only these recorded board entries:".into()];
            if remove.cut.is_empty() { lines.push("No recorded board entries are retained.".into()); }
            for cut in remove.cut {
                lines.push(format!("Board {}; origin {}; through entry {}; last hash {}", quote(&cut.board), quote(&cut.origin), cut.seq, if cut.hash.is_empty() { "none" } else { &cut.hash }));
            }
            lines.join("\n")
        }
        Body::AdminAdd(add) => format!("Add an admin key. Requested role, strength and identity:\n{}", key(&add.entry)),
        Body::AdminRemove(remove) => {
            let mut lines = vec![format!("Remove admin key {}", fingerprint_of(&remove.key))];
            if remove.cut.is_empty() { lines.push("None of this key's signed changes are retained.".into()); }
            else { lines.push("Retain these signed changes:".into()); lines.extend(remove.cut); }
            lines.join("\n")
        }
        Body::SetThreshold(thresholds) => {
            let mut lines = vec!["Change approval thresholds:".into()];
            if let Some(n) = thresholds.routine { lines.push(format!("Routine approvals: {n}")); }
            if let Some(n) = thresholds.sensitive { lines.push(if n == 0 { "Sensitive approvals: automatic (two once two people hold strong admin keys)".into() } else { format!("Sensitive approvals: {n}") }); }
            if let Some(u) = thresholds.unit { lines.push(format!("Count approvals by: {}", unit(u))); }
            lines.join("\n")
        }
        Body::SetHolds(holds) => {
            let mut lines = vec!["Change waits:".into()];
            if let Some(n) = holds.admin_add_h { lines.push(format!("Add admin: {n} hours")); }
            if let Some(n) = holds.admin_remove_h { lines.push(format!("Remove admin: {n} hours")); }
            if let Some(n) = holds.handoff_min { lines.push(format!("Handoff: {n} minutes")); }
            if let Some(n) = holds.dispute_d { lines.push(format!("Dispute: {n} days")); }
            lines.join("\n")
        }
        Body::SetMode(mode) => format!("Governed by: {}", match mode.mode { Mode::Admins => "admins", Mode::Members => "members" }),
        Body::Delegate(device) => format!("Allow {} to admit devices for 30 days.", named_device(pool, &device.fingerprint)),
        Body::DelegateEnd(device) => format!("End permission for {} to admit devices.", named_device(pool, &device.fingerprint)),
        Body::Cancel(target) | Body::Veto(target) => {
            if depth != 0 { return bad("A cancellation request cannot contain another cancellation.") }
            let Some(held) = pool.acts().get(&target.act) else { return bad("The phone needs the signed change this request cancels.") };
            format!("{} signed change {}:\n{}", if act.kind == "cancel" { "Cancel" } else { "Veto" }, target.act, change(held, pool, depth + 1)?)
        }
        Body::Found(_) | Body::Leave(_) => return bad("This request requires a different kind of signature."),
    })
}

pub(super) fn render(act: &Act, pool: &GovPool, binding: &Binding) -> Result<String> {
    let charter = &pool.state().chain.latest().charter;
    let Some(selected) = charter.admin(&binding.public).filter(|key| key.role == Role::Admin && key.strength == Strength::Strong) else {
        return bad("This phone key is not a strong admin under these pool terms.");
    };
    if pool.genesis.anchor() != binding.anchor { return bad("The pool does not match this phone's founding pin.") }
    let (epoch, head) = pool.context();
    let context = format!("Pool ID: {}\nPinned founding terms: {}\nCurrent terms: epoch {epoch}; fingerprint {head}\nSigning with: {}; {}; strong admin",
        pool.id, binding.anchor, fingerprint_of(&selected.key), match selected.alg { Alg::Ed25519 => "Ed25519", Alg::P256 => "P-256" });
    let card = format!("{context}\n\nChange to approve:\n{}\n\n{}", change(act, pool, 0)?, terms(charter)?);
    if card.len() > 64 * 1024 { return bad("This change is too large for the phone to display safely.") }
    Ok(card)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_cannot_reorder_or_inject_trusted_fields() {
        let escaped = quote("label\u{202e}\u{2066}\n\u{0085}\\u202e\"مرحبا");
        assert!(escaped.contains("\\u202e\\u2066\\u000a\\u0085\\\\u202e\\\"مرحبا"));
        assert_eq!(escaped.matches('\u{2068}').count(), 1);
        assert_eq!(escaped.matches('\u{2069}').count(), 1);
        assert!(!escaped.contains('\n'));
        let card = format!("Person: {}\nRoutine approvals: 1", quote("مرحبا"));
        assert!(card.contains("\"\u{2069}\nRoutine approvals: 1"));
    }
}
