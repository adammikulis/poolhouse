//! The plain-words text of a request, made from fields the node checked.
//!
//! One fact a line, each line starting with a label, in this order: `FLAG:` lines, `NOTE:` lines, `POOL:` for anything that connects something,
//! `RISK:`, `SAID:`, `WHO:`, `VERDICT:`, `TAINT:`, `ASK:` and `SCOPE:` for each ask, `HELD:` and `PICK:` when they apply, `CANNOT:`,
//! `WHY:`, `DENY:`, `EXPIRES:`. The agent's own words are only the note, shown on the `WHY:` line as the agent's. Times are written in
//! UTC so the text is the same bytes on every device.

use crate::approvals::context::Context;
use crate::cert::civil;

pub const ROUTINE: &str = "routine";
pub const SENSITIVE: &str = "sensitive";
pub const IRREVERSIBLE: &str = "irreversible";

/// What an action costs if it is misused: its level and why, in a sentence. A request is as risky as its riskiest ask.
pub fn risk_of(action: &str) -> (&'static str, &'static str) {
    match action {
        "sync_now" => (ROUTINE, "It swaps board entries with the pool's devices and changes nothing else."),
        "link_read" => (SENSITIVE, "Another board can read what this one holds: it leaves this board, and a link cannot take it back."),
        "link_remove" => (ROUTINE, "It ends a sharing link, and sharing can be made again."),
        "note_verify" => (ROUTINE, "It records that a note's command was run; it runs nothing."),
        "add_device" => (SENSITIVE, "It makes a code that lets another device join this pool."),
        "join_pool" => (SENSITIVE, "It joins this device to another pool and shares its boards with it."),
        "shard_call" => (SENSITIVE, "It sends tests and their files to run on another device."),
        "shard_consent" => (SENSITIVE, "It lets other devices run code here."),
        "land_trust" => (SENSITIVE, "It makes this device count the tasks, grants and reviews another device wrote."),
        "task_authority" => (SENSITIVE, "It acts for you on tasks, including accepting them and marking them done."),
        "quarantine_release" => (SENSITIVE, "It releases a held message that may carry instructions meant to mislead an agent."),
        "link_write" => (SENSITIVE, "Another board can write into this one."),
        "source_add" => (SENSITIVE, "It makes a folder part of a project, so the project's agents can read it."),
        "phone_add" => (SENSITIVE, "The new phone can approve everything this phone can."),
        "member_remove" => (IRREVERSIBLE, "A removed device is never added back; it has to join as a new device."),
        "file_delete" => (IRREVERSIBLE, "The file's bytes are overwritten and cannot be got back."),
        "phone_remove" => (IRREVERSIBLE, "A removed phone's key is never accepted again."),
        _ => (SENSITIVE, "It is not an action this node knows the cost of."),
    }
}

/// Whether a request for ``action`` names a resource that matters: a star is a whole class of things, and a person is told so. `sync_now` and
/// `add_device` take none, so the star they carry says nothing.
pub fn names_a_resource(action: &str) -> bool {
    !matches!(action, "sync_now" | "add_device")
}

/// The level of the riskiest of ``levels``.
pub fn riskiest<'a>(levels: impl Iterator<Item = &'a str>) -> &'static str {
    levels.fold(ROUTINE, |worst, l| match (worst, l) {
        (IRREVERSIBLE, _) | (_, IRREVERSIBLE) => IRREVERSIBLE,
        (SENSITIVE, _) | (_, SENSITIVE) => SENSITIVE,
        _ => ROUTINE,
    })
}

/// Whether a request for ``action`` connects something, so the text states the pool's join policy.
pub fn connects(action: &str) -> bool {
    matches!(action, "add_device" | "join_pool" | "link_read" | "link_write" | "shard_call" | "shard_consent" | "sync_now" | "phone_add")
}

/// A short phrase for what ``action`` does, for a title.
pub fn label_of(action: &str) -> &'static str {
    match action {
        "add_device" => "add a device",
        "join_pool" => "join a pool",
        "member_remove" => "remove a device",
        "sync_now" => "sync the pool",
        "link_read" | "link_write" => "share a board",
        "link_remove" => "end a sharing link",
        "shard_call" => "run tests on another device",
        "shard_consent" => "accept tests from other devices",
        "land_trust" => "trust another device's entries",
        "task_authority" => "act on tasks",
        "note_verify" => "verify a note",
        "quarantine_release" => "release a held message",
        "file_delete" => "delete a file",
        "source_add" => "add a folder to a project",
        "phone_add" => "add a phone",
        "phone_remove" => "remove a phone",
        _ => "do something",
    }
}

/// The time ``ms`` since the epoch as `YYYY-MM-DD HH:MM` UTC.
pub fn utc(ms: u64) -> String {
    let secs = ms / 1000;
    let (y, m, d) = civil(i64::try_from(secs / 86_400).unwrap_or(0));
    format!("{y:04}-{m:02}-{d:02} {:02}:{:02} UTC", (secs % 86_400) / 3600, (secs % 3600) / 60)
}

/// A span in words: `10 minutes`, `1 hour`, `2 days`.
pub fn span(ms: u64) -> String {
    for (unit, size) in [("day", 86_400_000), ("hour", 3_600_000), ("minute", 60_000), ("second", 1000)] {
        if ms >= size && ms % size == 0 {
            let n = ms / size;
            return format!("{n} {unit}{}", if n == 1 { "" } else { "s" });
        }
    }
    format!("{ms} milliseconds")
}

fn uses_words(uses: Option<u64>) -> String {
    match uses {
        None => "as often as it likes".into(),
        Some(1) => "once".into(),
        Some(n) => format!("{n} times"),
    }
}

/// A resource as a person reads it: `*` is anything, a trailing `*` is a prefix.
pub fn resource_words(resource: &str) -> String {
    match resource.strip_suffix('*') {
        Some("") => "anything".into(),
        Some(prefix) => format!("anything starting with {prefix}"),
        None => resource.to_string(),
    }
}

/// One ask as the plain sentence of what it does; ``policy`` is the pool's join policy now.
pub fn sentence(action: &str, resource: &str) -> String {
    let named = resource_words(resource);
    let (from, to) = resource.split_once('>').unwrap_or((resource, "*"));
    let board = |b: &str| if b == "*" { "any board".to_string() } else { format!("the board {b}") };
    match action {
        "member_remove" => format!("Remove the device {named} from the pool."),
        "add_device" => "Make a short code that lets one more device join this pool.".into(),
        "join_pool" => format!("Join this device to the pool at {named}."),
        "sync_now" => "Swap board entries with the pool's devices now.".into(),
        "shard_call" => format!("Send test runs to the device {named}."),
        "shard_consent" => format!("Let other devices send test runs here ({named})."),
        "land_trust" => format!("Count the board entries of the device {named} as if they were made here."),
        "task_authority" => format!("Act for you on tasks ({named}): assign, review, mark done."),
        "note_verify" => format!("Record that a note's command was run ({named})."),
        "quarantine_release" => format!("Release the held message {named} so agents can read it."),
        "file_delete" => format!("Delete the shared file {named} for good."),
        "link_read" => format!("Share {} with {}, read only.", board(from), board(to)),
        "link_write" => format!("Share {} with {}, read and write.", board(from), board(to)),
        "link_remove" => format!("End the sharing link {named}."),
        "source_add" => format!("Add a folder to the project {named}."),
        _ => format!("Do {action} on {named}."),
    }
}

/// The `POOL:` line: the join policy of this node's pool and what it means for the person.
pub fn pool_line(policy: &str, joining: bool) -> String {
    let means = |p: &str| if p == "open" { "Devices on the same network join without asking." } else { "Each device needs your code to join." };
    match joining {
        true => format!("POOL: this device's pool is {}; the pool it would join states its own policy when this device connects.", policy.to_uppercase()),
        false => format!("POOL: {}. {}", policy.to_uppercase(), means(policy)),
    }
}

/// One ask as it is shown.
pub struct Line<'a> {
    pub action: &'a str,
    pub resource: &'a str,
    pub lasts_ms: u64,
    pub uses: Option<u64>,
    pub review: bool,
}

/// Who asks, as the `WHO:` line gives it.
pub struct Who<'a> {
    pub name: &'a str,
    pub model: &'a str,
    pub harness: &'a str,
    /// Whether the node checked the model, or only heard the session say it.
    pub verified: bool,
    pub parent: &'a str,
    pub board: &'a str,
    pub device: &'a str,
}

/// What the text of a request is made from.
pub struct Draft<'a> {
    pub who: Who<'a>,
    pub lines: Vec<Line<'a>>,
    /// The join policy of the node's pool now.
    pub policy: &'a str,
    pub flags: Vec<String>,
    /// What is only news, written as `NOTE:` lines that do not open the request first.
    pub notes: Vec<String>,
    /// What else the person weighs; its lines are part of the text.
    pub ctx: &'a Context,
    pub note: &'a str,
    /// The task the request came from and whether the board has it.
    pub task: &'a str,
    pub task_known: bool,
    pub created_ms: u64,
    pub expires_ms: u64,
}

fn scope(d: &Draft, l: &Line) -> String {
    let who = if d.who.parent.is_empty() { format!("only {} (not its subagents)", d.who.name) } else { format!("only {} (a subagent)", d.who.name) };
    format!("{who}, on this device ({}), on {}, for {} from the moment you approve (at the latest until {}), {}; {}.",
            d.who.device, resource_words(l.resource), span(l.lasts_ms), utc(d.expires_ms.saturating_add(l.lasts_ms)), uses_words(l.uses),
            if l.review { "each use needs an independent review" } else { "no review is needed" })
}

/// The title, the text and the risk level of a request of grants.
pub fn grants_text(d: &Draft) -> (String, String, &'static str) {
    let risk = riskiest(d.lines.iter().map(|l| risk_of(l.action).0));
    let worst = d.lines.iter().map(|l| risk_of(l.action)).find(|r| r.0 == risk).map_or("", |r| r.1);
    let mut out: Vec<String> = d.flags.iter().map(|f| format!("FLAG: {f}")).collect();
    out.extend(d.notes.iter().map(|n| format!("NOTE: {n}")));
    if d.lines.iter().any(|l| connects(l.action)) {
        out.push(pool_line(d.policy, d.lines.iter().any(|l| l.action == "join_pool")));
    }
    out.push(format!("RISK: {}. {worst}", risk.to_uppercase()));
    out.push(d.ctx.said_line());
    let kind = if d.who.parent.is_empty() { "a main session".to_string() } else { format!("a subagent of {}", d.who.parent) };
    let claim = if d.who.verified { format!("verified as {} in {}", d.who.model, d.who.harness) } else if d.who.model.is_empty() { format!("which names no model in {} (unverified)", d.who.harness) } else { format!("which says it is {} in {} (unverified)", d.who.model, d.who.harness) };
    out.push(format!("WHO: {}, {claim}, {kind}, on device {}, board {}.", d.who.name, d.who.device, d.who.board));
    out.push(d.ctx.verdict_line());
    out.push(d.ctx.taint_line());
    let many = d.lines.len() > 1;
    for (i, l) in d.lines.iter().enumerate() {
        let n = if many { format!(" {}", i + 1) } else { String::new() };
        out.push(format!("ASK{n}: {}", sentence(l.action, l.resource)));
        out.push(format!("SCOPE{n}: {}", scope(d, l)));
    }
    out.extend(d.ctx.held_line());
    out.extend(d.ctx.pick_line());
    out.push("CANNOT: Nothing beyond what is listed: no other action, board or device, nothing after the end shown, no more uses than shown. It can never approve anything or act as you.".into());
    let task = match (d.task.is_empty(), d.task_known) {
        (true, _) => String::new(),
        (false, true) => format!(" From {} on board {}.", d.task, d.who.board),
        (false, false) => format!(" It names {}, which is not on board {}.", d.task, d.who.board),
    };
    out.push(if d.note.is_empty() && task.is_empty() { "WHY: The agent gave no reason.".into() } else if d.note.is_empty() { format!("WHY:{task}") } else { format!("WHY: The agent says: \"{}\" (not checked by anyone).{task}", d.note) });
    out.push(d.ctx.deny_line());
    out.push(format!("EXPIRES: This request ends at {}.", utc(d.expires_ms)));
    let title = match d.lines.as_slice() {
        [one] => format!("{} asks to {}", d.who.name, label_of(one.action)),
        many => format!("{} asks for {} things", d.who.name, many.len()),
    };
    (title.chars().take(80).collect(), out.join("\n"), risk)
}

/// What a phone's own request shows: a title, the text and the risk, for adding or removing a phone. ``phone`` is its name and ``key``
/// the first twenty-four characters of its key in groups of four.
pub fn phone_text(adding: bool, phone: &str, key: &str, by: &str, policy: &str, flags: &[String], ctx: &Context, expires_ms: u64) -> (String, String, &'static str) {
    let action = if adding { "phone_add" } else { "phone_remove" };
    let (risk, why) = risk_of(action);
    let mut out: Vec<String> = flags.iter().map(|f| format!("FLAG: {f}")).collect();
    if adding {
        out.push(pool_line(policy, false));
    }
    out.push(format!("RISK: {}. {why}", risk.to_uppercase()));
    out.push(format!("WHO: {by}."));
    out.push(format!("ASK: {} the phone {phone} (key {key}).", if adding { "Add" } else { "Remove" }));
    out.push(if adding {
        "SCOPE: From the moment you approve, that phone can approve or deny anything for this device, until you remove it.".into()
    } else {
        "SCOPE: That phone stops being able to approve at once, and what it already approved stays as signed.".into()
    });
    if !ctx.pick.is_empty() {
        out.push("PICK: After your fingerprint you choose the number the new phone shows on its own screen, out of three. A wrong choice counts as a decline.".into());
    }
    out.push(if adding {
        "CANNOT: Nothing else changes. Check that the key above is the one the new phone shows.".into()
    } else {
        "CANNOT: Nothing else changes. The last key on a device is never removed.".into()
    });
    out.push("WHY: A phone asked for this on its own screen.".into());
    out.push("DENY: If you deny, nothing changes.".into());
    out.push(format!("EXPIRES: This request ends at {}.", utc(expires_ms)));
    (format!("{} the phone {phone}", if adding { "Add" } else { "Remove" }), out.join("\n"), risk)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_time_is_written_in_utc_and_a_span_in_words() {
        assert_eq!(utc(1_790_000_000_000), "2026-09-21 14:13 UTC");
        assert_eq!(span(600_000), "10 minutes");
        assert_eq!(span(3_600_000), "1 hour");
        assert_eq!(span(90_000), "90 seconds");
    }

    #[test]
    fn the_riskiest_ask_sets_the_level() {
        assert_eq!(riskiest(["sync_now", "add_device"].into_iter().map(|a| risk_of(a).0)), SENSITIVE);
        assert_eq!(riskiest(["add_device", "member_remove"].into_iter().map(|a| risk_of(a).0)), IRREVERSIBLE);
        assert_eq!(riskiest(["sync_now"].into_iter().map(|a| risk_of(a).0)), ROUTINE);
    }

    #[test]
    fn the_pool_line_says_what_the_policy_means_and_what_a_change_would_do() {
        assert!(pool_line("secure", false).starts_with("POOL: SECURE. Each device needs your code"));
        assert!(pool_line("open", false).contains("without asking"));
    }
}
