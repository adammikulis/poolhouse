//! What a request carries besides its asks that a person weighs before deciding, all of it written by the node and signed with the
//! request: what the person said that the request supposedly follows, how the node judges the two against each other, whether the
//! session read content from outside, whether the act is held for an undo window, how often the same thing was declined before,
//! and the steps the person takes (a number to match, a block that outlasts a deny).
//!
//! Nothing here is taken from the agent. How the instruction, the verdict and the taint are found is not decided yet: an `Evidence`
//! source tells the node, and the one a node starts with (`Unrecorded`) says that nothing is recorded, which the text then says in
//! plain words. A source another part of the node supplies fills the same fields and changes no signed byte but their values.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::approvals::content::{span, utc};
use crate::error::Result;

/// How an instruction of the person reached the node: attested by the agent's harness from the person's prompt, or sent from the app.
pub const ORIGINS: [&str; 3] = ["none", "harness", "app"];
/// How the node judges a request against the instruction: it matches it, goes beyond it, has nothing to do with it, or there is none.
pub const VERDICTS: [&str; 4] = ["matches", "beyond", "unrelated", "none"];
/// Whether the session read content from outside it cannot vouch for: not tracked, no, or yes.
pub const TAINTS: [&str; 3] = ["unknown", "clean", "tainted"];
/// Whether the person recently told the session to do this: yes, no, or the node cannot tell.
pub const PROMPTED: [&str; 3] = ["yes", "no", "unknown"];
/// The longest quote of an instruction the text carries.
pub const MOST_QUOTE: usize = 280;

/// The signed context of a request. Every field is always present, so a challenge that lacks one is not one the node wrote.
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct Context {
    /// What the person said, one plain line, and how the node came to know it (`ORIGINS`); empty and `none` when nothing is recorded.
    pub quote: String,
    pub origin: String,
    pub verdict: String,
    pub taint: String,
    /// When the act would happen once approved, and whether a notification lets the person cancel it first; 0 and false for an act that
    /// happens at once.
    pub held_at_ms: u64,
    pub held_cancel: bool,
    pub prompted: String,
    /// How many times the same scope was declined before this request.
    pub repeat: u64,
    /// The three numbers a person chooses among after the fingerprint, or none when no number has to be matched.
    pub pick: Vec<String>,
    /// How long a block lasts if the person denies and blocks: 0 when the request offers none.
    pub block_ms: u64,
}

impl Default for Context {
    /// Nothing recorded: no instruction, no verdict, taint not tracked, nothing held, nothing asked before, nothing to match.
    fn default() -> Context {
        Context {
            quote: String::new(), origin: "none".into(), verdict: "none".into(), taint: "unknown".into(), held_at_ms: 0, held_cancel: false,
            prompted: "unknown".into(), repeat: 0, pick: Vec::new(), block_ms: 0,
        }
    }
}

fn one_of(value: &Value, allowed: &[&str]) -> Option<String> {
    value.as_str().filter(|s| allowed.contains(s)).map(String::from)
}

impl Context {
    /// The object a challenge carries under `context`.
    pub fn object(&self) -> Value {
        json!({"said": {"quote": self.quote, "origin": self.origin}, "verdict": self.verdict, "taint": self.taint,
               "held": {"act_at_ms": self.held_at_ms, "cancel": self.held_cancel}, "prompted": self.prompted, "repeat": self.repeat,
               "pick": self.pick, "block_ms": self.block_ms})
    }

    /// The context an object holds, or none when it lacks a field, adds one, or names a value the node never writes.
    pub fn from_object(v: &Value) -> Option<Context> {
        let map = v.as_object().filter(|m| m.len() == 8)?;
        let (said, held) = (map.get("said")?.as_object().filter(|m| m.len() == 2)?, map.get("held")?.as_object().filter(|m| m.len() == 2)?);
        let pick: Vec<String> = map.get("pick")?.as_array()?.iter().map(|p| p.as_str().map(String::from)).collect::<Option<_>>()?;
        let quote = said.get("quote")?.as_str()?.to_string();
        let ctx = Context {
            origin: one_of(said.get("origin")?, &ORIGINS)?, verdict: one_of(map.get("verdict")?, &VERDICTS)?, taint: one_of(map.get("taint")?, &TAINTS)?,
            held_at_ms: held.get("act_at_ms")?.as_u64()?, held_cancel: held.get("cancel")?.as_bool()?, prompted: one_of(map.get("prompted")?, &PROMPTED)?,
            repeat: map.get("repeat")?.as_u64()?, pick, block_ms: map.get("block_ms")?.as_u64()?, quote,
        };
        let fits = ctx.quote.len() <= MOST_QUOTE && ctx.quote.bytes().all(|b| b.is_ascii_graphic() || b == b' ') && (ctx.pick.is_empty() || ctx.pick.len() == 3);
        fits.then_some(ctx)
    }

    /// Whether the person said something the node knows of.
    pub fn said(&self) -> bool {
        !self.quote.is_empty() && self.origin != "none"
    }

    /// The lines of the text this context writes, each starting with its label, in the order they appear: `SAID:`, then (after `WHO:`)
    /// `VERDICT:` and `TAINT:`; `HELD:` and `PICK:` after the asks.
    pub fn said_line(&self) -> String {
        if !self.said() {
            return "SAID: No instruction from you is recorded for this session.".into();
        }
        let how = if self.origin == "app" { "sent from this app" } else { "recorded by the agent's harness from your prompt" };
        format!("SAID: You said: \"{}\" ({how}).", self.quote)
    }

    pub fn verdict_line(&self) -> String {
        let found = match self.verdict.as_str() {
            "matches" => "The node found this request matches what you said.",
            "beyond" => "The node found this request goes beyond what you said.",
            "unrelated" => "The node found this request has nothing to do with what you said.",
            _ => "No recent instruction from you was found to compare it with.",
        };
        format!("VERDICT: {found}")
    }

    pub fn taint_line(&self) -> String {
        let said = match self.taint.as_str() {
            "tainted" => "This session read content from outside (a message, a page or a file) that it cannot vouch for since your last instruction.",
            "clean" => "This session has read nothing from outside since your last instruction.",
            _ => "Not tracked: the node cannot say whether this session read content from outside.",
        };
        format!("TAINT: {said}")
    }

    /// The `HELD:` line, when the act waits for an undo window.
    pub fn held_line(&self) -> Option<String> {
        (self.held_at_ms > 0).then(|| {
            let undo = if self.held_cancel { "; a notification lets you cancel it before then" } else { "" };
            format!("HELD: If you approve, this happens at {} and not before{undo}.", utc(self.held_at_ms))
        })
    }

    /// The `PICK:` line, when a number has to be matched.
    pub fn pick_line(&self) -> Option<String> {
        (!self.pick.is_empty()).then(|| "PICK: After your fingerprint you choose the number this agent showed you, out of three. A wrong choice counts as a decline.".to_string())
    }

    /// The sentence of the `DENY:` line: what a deny does, and what a deny that blocks does.
    pub fn deny_line(&self) -> String {
        let mut line = "DENY: If you deny, the agent is told no and nothing changes.".to_string();
        if self.block_ms > 0 {
            line.push_str(&format!(" If you deny and block, it also cannot ask again for {}.", span(self.block_ms)));
        }
        line
    }
}

fn random_u16() -> Result<u16> {
    let mut raw = [0u8; 2];
    getrandom::fill(&mut raw).map_err(|e| std::io::Error::other(e.to_string()))?;
    Ok(u16::from_le_bytes(raw))
}

/// The number a person is to be shown and the three they choose among, the right one in a place nobody can guess.
pub fn pick_options() -> Result<(String, Vec<String>)> {
    let mut numbers: Vec<String> = Vec::new();
    while numbers.len() < 3 {
        let n = (10 + random_u16()? % 90).to_string();
        if !numbers.contains(&n) {
            numbers.push(n);
        }
    }
    let code = numbers[0].clone();
    numbers.swap(0, usize::from(random_u16()? % 3));
    Ok((code, numbers))
}

/// What a source of evidence tells the node about a session's request. Values the node does not write are replaced by what it does.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Facts {
    /// What the person said, and its origin (`harness` or `app`).
    pub instruction: Option<(String, String)>,
    pub verdict: String,
    pub taint: String,
    /// When the act would happen and whether the person can cancel it first.
    pub held: Option<(u64, bool)>,
    /// Whether the person recently told the session to do this; `None` when the node cannot tell.
    pub prompted: Option<bool>,
}

impl Facts {
    /// Nothing is recorded.
    pub fn unrecorded() -> Facts {
        Facts { instruction: None, verdict: "none".into(), taint: "unknown".into(), held: None, prompted: None }
    }
}

/// The asks of a request as a source of evidence sees them.
pub struct AskFacts<'a> {
    pub action: &'a str,
    pub resource: &'a str,
}

/// Where the node learns what the person said and whether the session can be trusted to relay it. How a source finds out is not this
/// module's business; a request is made from what it answers.
pub trait Evidence: Send {
    fn facts(&self, board: &str, session: &str, asks: &[AskFacts]) -> Facts;
}

/// The source a node starts with: nothing is recorded, so the text says so and no request counts as unprompted.
pub struct Unrecorded;

impl Evidence for Unrecorded {
    fn facts(&self, _board: &str, _session: &str, _asks: &[AskFacts]) -> Facts {
        Facts::unrecorded()
    }
}

impl Context {
    /// The context a source's facts make, with the node's own parts: how often the scope was declined before, the numbers to choose
    /// among, and what a block lasts. A fact the node does not write (an unknown verdict, a quote with a line break) is dropped.
    pub fn from_facts(facts: &Facts, repeat: u64, pick: Vec<String>, block_ms: u64) -> Context {
        let mut ctx = Context { repeat, pick, block_ms, ..Context::default() };
        if let Some((quote, origin)) = &facts.instruction {
            let plain = quote.len() <= MOST_QUOTE && !quote.is_empty() && quote.bytes().all(|b| b.is_ascii_graphic() || b == b' ');
            if plain && (origin == "harness" || origin == "app") {
                ctx.quote = quote.replace('"', "'");
                ctx.origin = origin.clone();
            }
        }
        ctx.verdict = VERDICTS.iter().find(|v| **v == facts.verdict).map_or("none", |v| v).into();
        if !ctx.said() {
            ctx.verdict = "none".into();
        }
        ctx.taint = TAINTS.iter().find(|t| **t == facts.taint).map_or("unknown", |t| t).into();
        if let Some((at, cancel)) = facts.held {
            (ctx.held_at_ms, ctx.held_cancel) = (at, cancel);
        }
        ctx.prompted = match facts.prompted {
            Some(true) => "yes",
            Some(false) => "no",
            None => "unknown",
        }
        .into();
        ctx
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_context_is_read_back_only_in_the_shape_the_node_writes() {
        let ctx = Context { quote: "add the Linux box".into(), origin: "harness".into(), verdict: "matches".into(), pick: vec!["12".into(), "47".into(), "83".into()], ..Context::default() };
        assert_eq!(Context::from_object(&ctx.object()), Some(ctx.clone()));
        let mut dropped = ctx.object();
        dropped.as_object_mut().unwrap().remove("taint");
        assert_eq!(Context::from_object(&dropped), None, "a field dropped");
        let mut added = ctx.object();
        added["extra"] = json!(1);
        assert_eq!(Context::from_object(&added), None, "a field added");
        let mut odd = ctx.object();
        odd["verdict"] = json!("fine");
        assert_eq!(Context::from_object(&odd), None, "a value the node never writes");
        let mut two = ctx.object();
        two["pick"] = json!(["1", "2"]);
        assert_eq!(Context::from_object(&two), None, "a number to match is one of three");
    }

    #[test]
    fn a_fact_the_node_does_not_write_is_dropped_and_what_is_unknown_says_so() {
        let odd = Facts { instruction: Some(("line\nbreak".into(), "harness".into())), verdict: "matches".into(), taint: "maybe".into(), held: None, prompted: Some(true) };
        let ctx = Context::from_facts(&odd, 0, Vec::new(), 0);
        assert_eq!((ctx.quote.as_str(), ctx.origin.as_str(), ctx.verdict.as_str(), ctx.taint.as_str()), ("", "none", "none", "unknown"));
        assert!(ctx.said_line().contains("No instruction from you is recorded") && ctx.verdict_line().contains("No recent instruction") && ctx.taint_line().contains("Not tracked"));
        let said = Facts { instruction: Some(("add \"the\" box".into(), "app".into())), verdict: "beyond".into(), taint: "tainted".into(), held: Some((1_790_000_000_000, true)), prompted: Some(false) };
        let ctx = Context::from_facts(&said, 2, Vec::new(), 86_400_000);
        assert_eq!(ctx.said_line(), "SAID: You said: \"add 'the' box\" (sent from this app).");
        assert!(ctx.verdict_line().contains("goes beyond") && ctx.taint_line().contains("cannot vouch for"));
        assert!(ctx.held_line().unwrap().contains("2026-09-21 14:13 UTC") && ctx.held_line().unwrap().contains("cancel"));
        assert!(ctx.deny_line().contains("cannot ask again for 1 day"), "{}", ctx.deny_line());
        assert_eq!(ctx.prompted, "no");
    }
}
