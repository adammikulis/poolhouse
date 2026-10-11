//! The one prompt of the operating system that the first person key and the first phone need.
//!
//! Nothing can sign for the first key, so something else has to say that a person is at this computer and agrees. The node asks the
//! operating system itself, in a prompt of its own (the system's password or fingerprint dialog, which a process that runs as the
//! person cannot click or type into), and only a yes from it makes the one-time secret that adds the first key or the first phone.
//! Everything else around it, which session asked and what happens next, can be done by an agent: the prompt is the one step that
//! cannot. The secret lives in the node's memory, never in a file, so a program that can write the state directory cannot leave one.
//!
//! There is no switch that turns the prompt off. A node asks with the operating system's own prompt where the system has one (`Os`) or,
//! when it was started with `--presence terminal`, on the person's terminal device (`terminal::Terminal`); tests of this crate put their
//! own `Presence` on `Node::firsts` (a library field no binary sets), and the pool check answers the terminal the way a person does.

pub mod terminal;

use std::process::{Child, Command, Stdio};

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fsutil::sha256_hex;
use crate::node::Node;

pub const METHODS: [&str; 2] = ["presence_ask", "presence_status"];
/// How long a prompt waits for the person, and how long what it made is good for.
const ASK_FOR_MS: u64 = 120_000;
const SECRET_FOR_MS: u64 = 10 * 60_000;
/// After a prompt that was refused or not answered, how long before the node asks again: a session cannot pester the person with prompts.
const QUIET_MS: u64 = 10 * 60_000;
const MOST_ABOUT: usize = 60;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "presence_ask" => &["purpose", "about", "terminal"],
        "presence_status" => &[],
        _ => return None,
    })
}

pub fn boardless(method: &str) -> bool {
    METHODS.contains(&method)
}

/// What the confirmed prompt makes possible.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Purpose {
    /// The first person key, enrolled with `person_enroll`.
    Key,
    /// The first phone, added with the code in the link a phone scans.
    Phone,
}

impl Purpose {
    fn named(name: &str) -> Result<Purpose> {
        match name {
            "key" => Ok(Purpose::Key),
            "phone" => Ok(Purpose::Phone),
            _ => Err(Error::Invalid("purpose is key (the first person key) or phone (the first phone)".into())),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Purpose::Key => "key",
            Purpose::Phone => "phone",
        }
    }

    fn words(self) -> &'static str {
        match self {
            Purpose::Key => "your first person key",
            Purpose::Phone => "your first phone",
        }
    }
}

/// A prompt that is open.
pub trait Asking: Send {
    /// None while the person has not answered, then whether the answer was yes.
    fn poll(&mut self) -> Option<bool>;
}

/// Where the question is put to the person.
pub trait Presence: Send {
    /// `os` for the operating system's own prompt, `terminal` for the person's terminal device.
    fn kind(&self) -> &'static str;
    /// Open a prompt that says ``what``; ``terminal`` is the terminal device the asker named, which only a terminal prompt uses.
    fn begin(&mut self, what: &str, terminal: &str) -> Result<Box<dyn Asking>>;
}

/// The operating system's own prompt, run as a child process the node polls: it ends the prompt when it is dropped.
struct OsPrompt(Child);

impl Asking for OsPrompt {
    fn poll(&mut self) -> Option<bool> {
        self.0.try_wait().ok().flatten().map(|status| status.success())
    }
}

impl Drop for OsPrompt {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

/// The text of a prompt as the system shows it: plain, short, with nothing in it a script could be written through.
pub(crate) fn plain(what: &str) -> String {
    what.chars().filter(|c| c.is_ascii_alphanumeric() || " .,-/()".contains(*c)).take(160).collect()
}

/// The command that asks the person: the system's own dialog for a password or fingerprint, which exits 0 only when it was given.
fn dialog(what: &str) -> Option<Command> {
    if cfg!(target_os = "macos") {
        let mut command = Command::new("/usr/bin/osascript");
        command.args(["-e", "on run argv", "-e", "do shell script \"/usr/bin/true\" with administrator privileges with prompt (item 1 of argv)", "-e", "end run", "--"]).arg(plain(what));
        return Some(command);
    }
    if cfg!(target_os = "linux") && std::path::Path::new("/usr/bin/pkexec").is_file() {
        let mut command = Command::new("/usr/bin/pkexec");
        command.arg("/usr/bin/true");
        return Some(command);
    }
    None
}

/// Ask through the operating system.
pub struct Os;

impl Presence for Os {
    fn kind(&self) -> &'static str {
        "os"
    }

    fn begin(&mut self, what: &str, _terminal: &str) -> Result<Box<dyn Asking>> {
        let mut command = dialog(what).ok_or_else(|| Error::Denied("This computer has no operating system prompt the node can ask the person with, so the first key or phone cannot be added here yet.".into()))?;
        let child = command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().map_err(|e| Error::Denied(format!("The operating system prompt did not open: {e}")))?;
        Ok(Box::new(OsPrompt(child)))
    }
}

/// How a node asks, chosen when it starts.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    /// The operating system's own prompt.
    Os,
    /// The person's terminal device, named by the asker and read by the node.
    Terminal,
}

impl Mode {
    pub fn parse(name: &str) -> Option<Mode> {
        match name {
            "auto" => Some(Mode::Os),
            "terminal" => Some(Mode::Terminal),
            _ => None,
        }
    }

    pub fn presence(self) -> Box<dyn Presence> {
        match self {
            Mode::Os => Box::new(Os),
            Mode::Terminal => Box::new(terminal::Terminal),
        }
    }
}

/// The presence a node asks with unless it was started with another.
pub fn default_presence() -> Box<dyn Presence> {
    Mode::Os.presence()
}

struct Pending {
    purpose: Purpose,
    by: String,
    started_ms: u64,
    prompt: Box<dyn Asking>,
}

enum Outcome {
    /// Confirmed: the secret, until the session that asked has read it.
    Yes(Option<String>, u64, Purpose),
    No,
    Late,
}

struct Secret {
    purpose: Purpose,
    sha256: String,
    at_ms: u64,
}

/// The prompt, what became of it, and the one secret it made.
pub struct Firsts {
    presence: Box<dyn Presence>,
    asking: Option<Pending>,
    outcome: Option<(String, Outcome)>,
    secret: Option<Secret>,
    quiet_until_ms: u64,
}

impl Firsts {
    pub fn new(presence: Box<dyn Presence>) -> Firsts {
        Firsts { presence, asking: None, outcome: None, secret: None, quiet_until_ms: 0 }
    }

    pub fn kind(&self) -> &'static str {
        self.presence.kind()
    }

    /// Open a prompt for ``by`` (`board/name`): nothing is made until the person says yes.
    pub fn ask(&mut self, purpose: Purpose, by: &str, about: &str, terminal: &str, now: u64) -> Result<()> {
        if self.asking.as_ref().is_some_and(|p| p.by == by && p.purpose == purpose) {
            return Ok(());
        }
        if self.asking.is_some() {
            return Err(Error::Quota("The person is being asked something already: answer that first.".into()));
        }
        if now < self.quiet_until_ms {
            return Err(Error::Quota("The person was asked a moment ago and did not say yes; wait a few minutes before asking again.".into()));
        }
        let what = format!("poolhouse: allow the session {by} to add {} to this computer?{}", purpose.words(), if about.is_empty() { String::new() } else { format!(" ({about})") });
        let prompt = self.presence.begin(&what, terminal)?;
        self.asking = Some(Pending { purpose, by: by.into(), started_ms: now, prompt });
        self.outcome = None;
        Ok(())
    }

    /// What became of the prompt ``by`` opened, as a reply: the secret comes once, in the reply that first says yes.
    pub fn status(&mut self, by: &str, now: u64) -> Value {
        if let Some(p) = &mut self.asking {
            let answer = p.prompt.poll();
            let late = now.saturating_sub(p.started_ms) > ASK_FOR_MS;
            if answer.is_some() || late {
                let done = self.asking.take().expect("checked above");
                self.outcome = Some((done.by.clone(), match answer {
                    Some(true) => self.make(done.purpose, now),
                    Some(false) => Outcome::No,
                    None => Outcome::Late,
                }));
                if !matches!(self.outcome, Some((_, Outcome::Yes(..)))) {
                    self.quiet_until_ms = now + QUIET_MS;
                }
            }
        }
        if let Some(p) = self.asking.as_ref().filter(|p| p.by == by) {
            return json!({"state": "asking", "purpose": p.purpose.words(), "left_ms": ASK_FOR_MS.saturating_sub(now.saturating_sub(p.started_ms))});
        }
        match &mut self.outcome {
            Some((who, Outcome::Yes(secret, expires, purpose))) if who == by => json!({"state": "confirmed", "purpose": purpose.name(), "secret": secret.take(), "expires_ms": *expires}),
            Some((who, Outcome::No)) if who == by => json!({"state": "refused"}),
            Some((who, Outcome::Late)) if who == by => json!({"state": "expired"}),
            _ => json!({"state": "none"}),
        }
    }

    fn make(&mut self, purpose: Purpose, now: u64) -> Outcome {
        let mut raw = [0u8; 32];
        if getrandom::fill(&mut raw).is_err() {
            return Outcome::No;
        }
        let secret = crate::phone::enrol::b64url(&raw);
        self.secret = Some(Secret { purpose, sha256: sha256_hex(secret.as_bytes()), at_ms: now });
        Outcome::Yes(Some(secret), now + SECRET_FOR_MS, purpose)
    }

    /// Whether a secret of ``purpose`` is waiting to be used.
    pub fn offered(&self, purpose: Purpose, now: u64) -> bool {
        self.secret.as_ref().is_some_and(|s| s.purpose == purpose && now >= s.at_ms && now - s.at_ms <= SECRET_FOR_MS)
    }

    /// Whether ``secret`` is the one waiting for ``purpose``, without spending it: a guess from the network never ends the offer.
    pub fn matches(&self, purpose: Purpose, secret: &str, now: u64) -> bool {
        self.offered(purpose, now) && !secret.is_empty() && self.secret.as_ref().is_some_and(|s| same(&s.sha256, &sha256_hex(secret.as_bytes())))
    }

    /// The secret waiting is used up.
    pub fn spend(&mut self) {
        self.secret = None;
    }

    /// Spend the secret of ``purpose``: it is good once, for ten minutes, and one wrong guess spends it too.
    pub fn take(&mut self, purpose: Purpose, secret: &str, now: u64) -> bool {
        let found = self.secret.take();
        found.is_some_and(|s| s.purpose == purpose && now >= s.at_ms && now - s.at_ms <= SECRET_FOR_MS && !secret.is_empty() && same(&s.sha256, &sha256_hex(secret.as_bytes())))
    }
}

fn same(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

fn text<'a>(p: &'a Map<String, Value>, key: &str) -> Result<&'a str> {
    match p.get(key) {
        None => Ok(""),
        Some(Value::String(s)) => Ok(s),
        _ => Err(Error::Invalid(format!("{key} is text."))),
    }
}

pub fn call(node: &mut Node, method: &str, token: &str, p: &Map<String, Value>) -> Result<Value> {
    let holder = node.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("A registered session asks for the first key or phone.".into()))?;
    let (by, now) = (format!("{}/{}", holder.board, holder.name), node.now_ms());
    if method == "presence_ask" {
        let purpose = Purpose::named(text(p, "purpose")?)?;
        if !node.persons.is_empty() {
            return Err(Error::Denied("A person key is enrolled already: a further key or phone is approved by one that is, not by this prompt.".into()));
        }
        let about: String = text(p, "about")?.chars().filter(|c| c.is_ascii_alphanumeric() || " .,-".contains(*c)).take(MOST_ABOUT).collect();
        node.firsts.ask(purpose, &by, &about, text(p, "terminal")?, now)?;
    }
    let mut out = node.firsts.status(&by, now);
    if out["state"] == "confirmed" && out["purpose"] == "phone" && out["secret"].is_string() {
        let secret = out["secret"].as_str().unwrap_or_default().to_string();
        out["link"] = json!(crate::phone::calls::link(node, &secret, out["expires_ms"].as_u64().unwrap_or(0)));
    }
    out["presence"] = json!(node.firsts.kind());
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A person who answers when the test says so.
    struct Scripted(std::sync::Arc<std::sync::Mutex<Option<bool>>>);

    struct Waiting(std::sync::Arc<std::sync::Mutex<Option<bool>>>);

    impl Asking for Waiting {
        fn poll(&mut self) -> Option<bool> {
            *self.0.lock().unwrap()
        }
    }

    impl Presence for Scripted {
        fn kind(&self) -> &'static str {
            "scripted"
        }

        fn begin(&mut self, _: &str, _: &str) -> Result<Box<dyn Asking>> {
            Ok(Box::new(Waiting(self.0.clone())))
        }
    }

    fn person() -> (Firsts, std::sync::Arc<std::sync::Mutex<Option<bool>>>) {
        let answer = std::sync::Arc::new(std::sync::Mutex::new(None));
        (Firsts::new(Box::new(Scripted(answer.clone()))), answer)
    }

    #[test]
    fn nothing_is_made_until_the_person_says_yes_and_only_the_asker_reads_it() {
        let (mut firsts, answer) = person();
        firsts.ask(Purpose::Key, "demo/a", "", "", 1000).unwrap();
        assert_eq!(firsts.status("demo/a", 2000)["state"], "asking");
        assert_eq!(firsts.status("demo/b", 2000)["state"], "none", "another session does not even see the prompt");
        assert!(!firsts.take(Purpose::Key, "guess", 2000));
        *answer.lock().unwrap() = Some(true);
        assert_eq!(firsts.status("demo/b", 3000)["state"], "none", "nor the secret");
        let said = firsts.status("demo/a", 3000);
        assert_eq!(said["state"], "confirmed");
        let secret = said["secret"].as_str().unwrap().to_string();
        assert!(firsts.status("demo/a", 3500)["secret"].is_null(), "the secret is read once");
        assert!(!firsts.take(Purpose::Phone, &secret, 4000), "a key's secret adds no phone, and one try spends it");
        assert!(!firsts.take(Purpose::Key, &secret, 4000), "spent");
    }

    #[test]
    fn a_guess_that_only_checks_never_spends_the_secret() {
        let (mut firsts, answer) = person();
        *answer.lock().unwrap() = Some(true);
        firsts.ask(Purpose::Phone, "demo/a", "", "", 0).unwrap();
        let secret = firsts.status("demo/a", 10)["secret"].as_str().unwrap().to_string();
        assert!(firsts.offered(Purpose::Phone, 20) && !firsts.offered(Purpose::Key, 20));
        assert!(!firsts.matches(Purpose::Phone, "guess", 20) && !firsts.matches(Purpose::Phone, "", 20) && !firsts.matches(Purpose::Key, &secret, 20));
        assert!(firsts.matches(Purpose::Phone, &secret, 30), "a wrong guess did not end it");
        firsts.spend();
        assert!(!firsts.matches(Purpose::Phone, &secret, 40), "once spent it is gone");
    }

    #[test]
    fn a_secret_adds_once_for_its_own_purpose_within_ten_minutes() {
        let (mut firsts, answer) = person();
        *answer.lock().unwrap() = Some(true);
        firsts.ask(Purpose::Phone, "demo/a", "", "", 0).unwrap();
        let secret = firsts.status("demo/a", 10)["secret"].as_str().unwrap().to_string();
        assert!(firsts.take(Purpose::Phone, &secret, 20));
        assert!(!firsts.take(Purpose::Phone, &secret, 30), "once");
        firsts.ask(Purpose::Phone, "demo/a", "", "", 40).unwrap();
        let late = firsts.status("demo/a", 50)["secret"].as_str().unwrap().to_string();
        assert!(!firsts.take(Purpose::Phone, &late, 50 + SECRET_FOR_MS + 1), "ten minutes");
    }

    #[test]
    fn a_no_or_no_answer_makes_nothing_and_keeps_the_node_from_asking_again_for_a_while() {
        let (mut firsts, answer) = person();
        firsts.ask(Purpose::Key, "demo/a", "", "", 0).unwrap();
        *answer.lock().unwrap() = Some(false);
        assert_eq!(firsts.status("demo/a", 10)["state"], "refused");
        assert!(matches!(firsts.ask(Purpose::Key, "demo/a", "", "", 20), Err(Error::Quota(_))), "a session cannot pester the person with prompts");
        *answer.lock().unwrap() = None;
        firsts.ask(Purpose::Key, "demo/a", "", "", QUIET_MS + 100).unwrap();
        assert_eq!(firsts.status("demo/a", QUIET_MS + ASK_FOR_MS + 200)["state"], "expired", "no answer in two minutes is a no");
    }

    #[test]
    fn one_prompt_is_open_at_a_time() {
        let (mut firsts, _) = person();
        firsts.ask(Purpose::Key, "demo/a", "", "", 0).unwrap();
        firsts.ask(Purpose::Key, "demo/a", "", "", 5).unwrap();
        assert!(matches!(firsts.ask(Purpose::Phone, "demo/b", "", "", 10), Err(Error::Quota(_))));
    }

    #[test]
    fn the_words_of_a_prompt_are_plain() {
        assert_eq!(plain("a \"quoted\"\nline; rm -rf /"), "a quotedline rm -rf /");
    }
}
