//! The one prompt of the operating system that the first person key and the first phone need.
//!
//! Nothing can sign for the first key, so something else has to say that a person is at this computer and agrees. The node asks the
//! operating system itself, in a prompt of its own (the system's password or fingerprint dialog, which a process that runs as the
//! person cannot click or type into), and only a yes from it makes the one-time secret that adds the first key or the first phone.
//! Everything else around it, which session asked and what happens next, can be done by an agent: the prompt is the one step that
//! cannot. The secret lives in the node's memory, never in a file, so a program that can write the state directory cannot leave one.
//!
//! There is no switch that turns the prompt off. A node started with no `--presence` has no way to ask and refuses the first key and
//! the first phone (`Closed`). `--presence os` asks with the operating system's own prompt, and only for the node of the account's own
//! real state directory (`Os`); the launcher passes it only for the installed software on the real home (`poolhouse.real_machine`), so a
//! test, a checkout or a rig never raises a dialog. `--presence terminal` asks on the person's terminal device that the node itself reads
//! (`terminal::Terminal`). Tests of this crate put their own `Presence` on `Node::firsts` (a library field no binary sets), and the pool
//! check answers the terminal the way a person does.

pub mod card;
pub mod terminal;

use std::process::{Child, Command, Stdio};

use serde_json::{json, Map, Value};

use crate::error::{Error, Result};
use crate::fsutil::sha256_hex;
use crate::gov::keys::Claim;
use crate::node::Node;

pub const METHODS: [&str; 3] = ["presence_ask", "presence_status", "ticket_spend"];
/// How long a prompt waits for the person, and how long what it made is good for.
const ASK_FOR_MS: u64 = 120_000;
const SECRET_FOR_MS: u64 = 10 * 60_000;
/// After a prompt that was refused or not answered, how long before the node asks again: a session cannot pester the person with prompts.
const QUIET_MS: u64 = 10 * 60_000;
/// How many prompts for a card the node raises in ten minutes and in a day: a password prompt every few minutes teaches a person to type the
/// password into anything, so a request over the limit waits.
const MOST_PROMPTS_PER_TEN_MINUTES: usize = 3;
const MOST_PROMPTS_PER_DAY: usize = 20;
const DAY_MS: u64 = 24 * 3_600_000;

pub fn params_for(method: &str) -> Option<&'static [&'static str]> {
    Some(match method {
        "presence_ask" => &["purpose", "public", "terminal", "card"],
        "presence_status" => &[],
        "ticket_spend" => &["ticket", "payload"],
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
    /// A key for a pool, made for one change the node drafted: the yes enrols it and is the ticket for the change.
    PoolKey,
    /// A change the node drafted that keys already enrolled sign: the yes is the ticket for it.
    Card,
}

impl Purpose {
    fn named(name: &str) -> Result<Purpose> {
        match name {
            "key" => Ok(Purpose::Key),
            "phone" => Ok(Purpose::Phone),
            "pool_key" => Ok(Purpose::PoolKey),
            "card" => Ok(Purpose::Card),
            _ => Err(Error::Invalid("purpose is key (the first person key), phone (the first phone), pool_key (a key for a pool) or card (a change to sign)".into())),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Purpose::Key => "key",
            Purpose::Phone => "phone",
            Purpose::PoolKey => "pool_key",
            Purpose::Card => "card",
        }
    }

    fn words(self) -> &'static str {
        match self {
            Purpose::Key => "your first person key",
            Purpose::Phone => "your first phone",
            Purpose::PoolKey => "a key for a pool",
            Purpose::Card => "a change to a pool",
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

/// The operating system's own prompt, run as a child process the node polls. It ends when it is dropped and, whether or not anything polls,
/// when the time to answer has run out: a dialog nobody answered cannot be answered hours later.
struct OsPrompt {
    child: std::sync::Arc<std::sync::Mutex<Child>>,
    stop: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

impl OsPrompt {
    fn start(child: Child, wait: std::time::Duration) -> OsPrompt {
        let (child, stop) = (std::sync::Arc::new(std::sync::Mutex::new(child)), std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)));
        let (reaped, quit) = (child.clone(), stop.clone());
        std::thread::spawn(move || {
            let end = std::time::Instant::now() + wait;
            while !quit.load(std::sync::atomic::Ordering::SeqCst) && std::time::Instant::now() < end {
                std::thread::sleep(std::time::Duration::from_millis(250));
            }
            if let Ok(mut c) = reaped.lock() {
                let _ = c.kill();
            }
        });
        OsPrompt { child, stop }
    }
}

impl Asking for OsPrompt {
    fn poll(&mut self) -> Option<bool> {
        self.child.lock().ok()?.try_wait().ok().flatten().map(|status| status.success())
    }
}

impl Drop for OsPrompt {
    fn drop(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::SeqCst);
        if let Ok(mut c) = self.child.lock() {
            let _ = c.kill();
            let _ = c.wait();
        }
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
        command.args(["/usr/bin/env", "--", "/usr/bin/true"]).arg(plain(what).replace(' ', "_"));
        return Some(command);
    }
    if cfg!(target_os = "windows") {
        return Some(windows_dialog(what));
    }
    None
}

/// What Windows PowerShell runs to ask the person: Windows Hello (a PIN, a fingerprint or a face) through the system's user consent
/// verifier, and where Hello is not set up the system's credential dialog checked against the account's password. It exits 0 only when
/// the person was verified. The sentence is read from the environment, never put into the script.
const WINDOWS_PROMPT: &str = r#"
$ErrorActionPreference = 'Stop'
$say = $env:POOLHOUSE_PROMPT
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null = [Windows.Security.Credentials.UI.UserConsentVerifier, Windows.Security.Credentials.UI, ContentType=WindowsRuntime]
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  $op = [Windows.Security.Credentials.UI.UserConsentVerifier]::RequestVerificationAsync($say)
  $task = $asTask.MakeGenericMethod([Windows.Security.Credentials.UI.UserConsentVerificationResult]).Invoke($null, @($op))
  $task.Wait() | Out-Null
  if ($task.Result -eq 'Verified') { exit 0 }
  if ($task.Result -notin 'DeviceNotPresent', 'NotConfiguredForUser', 'DisabledByPolicy') { exit 1 }
} catch { }
Add-Type -AssemblyName System.DirectoryServices.AccountManagement
$cred = $Host.UI.PromptForCredential('Poolhouse', $say, $env:USERNAME, '')
if ($null -eq $cred) { exit 1 }
$machine = New-Object System.DirectoryServices.AccountManagement.PrincipalContext('Machine')
if ($machine.ValidateCredentials($cred.UserName, $cred.GetNetworkCredential().Password)) { exit 0 }
exit 1
"#;

/// Standard base64 of ``bytes``, which PowerShell's `-EncodedCommand` takes over UTF-16 text.
fn base64(bytes: &[u8]) -> String {
    const SET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let n = chunk.iter().enumerate().fold(0u32, |acc, (i, b)| acc | (u32::from(*b) << (16 - 8 * i)));
        for i in 0..4 {
            out.push(if i <= chunk.len() { SET[((n >> (18 - 6 * i)) & 63) as usize] as char } else { '=' });
        }
    }
    out
}

/// The command that raises the person's prompt on Windows.
pub(crate) fn windows_dialog(what: &str) -> Command {
    let utf16: Vec<u8> = WINDOWS_PROMPT.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let mut command = Command::new("powershell.exe");
    command.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", &base64(&utf16)]).env("POOLHOUSE_PROMPT", plain(what));
    command
}

/// Whether ``state`` is the device's own node directory of the account this process runs as (`~/.poolhouse/node`, the account's home from
/// the password database and never from `HOME`), the same directory and no other under it: the only place a real prompt is raised.
pub(crate) fn is_the_real_state(state: &std::path::Path, account_home: &std::path::Path) -> bool {
    match (std::fs::canonicalize(account_home.join(".poolhouse").join("node")), std::fs::canonicalize(state)) {
        (Ok(real), Ok(mine)) => mine == real,
        _ => false,
    }
}

#[cfg(unix)]
pub(crate) fn on_real_home(state: &std::path::Path) -> bool {
    let mut entry: libc::passwd = unsafe { std::mem::zeroed() };
    let (mut buf, mut found) = (vec![0 as libc::c_char; 4096], std::ptr::null_mut());
    // SAFETY: entry and buf are valid for the call and buf's length is passed; getpwuid_r sets found to entry or null.
    let ok = unsafe { libc::getpwuid_r(libc::geteuid(), &mut entry, buf.as_mut_ptr(), buf.len(), &mut found) };
    if ok != 0 || found.is_null() || entry.pw_dir.is_null() {
        return false;
    }
    // SAFETY: pw_dir is a NUL-terminated string that lives in buf.
    let home = unsafe { std::ffi::CStr::from_ptr(entry.pw_dir) }.to_string_lossy().into_owned();
    is_the_real_state(state, std::path::Path::new(&home))
}

/// On Windows the account's directory comes from the system; when it can not be read nothing is known, so every directory counts as the real one.
#[cfg(windows)]
pub(crate) fn on_real_home(state: &std::path::Path) -> bool {
    crate::sys::account_home().is_none_or(|home| is_the_real_state(state, &home))
}

#[cfg(not(any(unix, windows)))]
pub(crate) fn on_real_home(_state: &std::path::Path) -> bool {
    true
}

/// Ask through the operating system, for the node of this state directory only.
pub struct Os {
    state: std::path::PathBuf,
}

impl Os {
    pub fn new(state: &std::path::Path) -> Os {
        Os { state: state.into() }
    }
}

/// No way to ask: a node started with no `--presence` refuses the first key and the first phone.
pub struct Closed;

impl Presence for Closed {
    fn kind(&self) -> &'static str {
        "closed"
    }

    fn begin(&mut self, _what: &str, _terminal: &str) -> Result<Box<dyn Asking>> {
        Err(Error::Denied("This node was started with no way to ask the person, so the first key or phone cannot be added through it: the installed Poolhouse starts it with the operating system's prompt.".into()))
    }
}

impl Presence for Os {
    fn kind(&self) -> &'static str {
        "os"
    }

    fn begin(&mut self, what: &str, _terminal: &str) -> Result<Box<dyn Asking>> {
        #[cfg(unix)]
        // SAFETY: geteuid takes no arguments and cannot fail.
        if unsafe { libc::geteuid() } == 0 {
            return Err(Error::Denied("The operating system's prompt is not raised for an administrator account: add the first key or phone from your own account.".into()));
        }
        if !on_real_home(&self.state) {
            return Err(Error::Denied("The operating system's prompt is raised only for the node of this account's own state directory, never for a test, a checkout or a copy.".into()));
        }
        let mut command = dialog(what).ok_or_else(|| Error::Denied("This computer has no operating system prompt the node can ask the person with (Linux needs polkit's pkexec and a desktop session), so nothing can be confirmed here; use the installed Poolhouse on a computer that has one, or your phone.".into()))?;
        let child = command.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().map_err(|e| Error::Denied(format!("The operating system prompt did not open: {e}")))?;
        Ok(Box::new(OsPrompt::start(child, std::time::Duration::from_millis(ASK_FOR_MS))))
    }
}

/// How a node asks, chosen when it starts (`--presence os|terminal`).
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
            "os" => Some(Mode::Os),
            "terminal" => Some(Mode::Terminal),
            _ => None,
        }
    }

    /// The presence of a node on ``state``.
    pub fn presence(self, state: &std::path::Path) -> Box<dyn Presence> {
        match self {
            Mode::Os => Box::new(Os::new(state)),
            Mode::Terminal => Box::new(terminal::Terminal::new(state)),
        }
    }
}

/// The presence a node asks with unless it was started with another: none.
pub fn default_presence() -> Box<dyn Presence> {
    Box::new(Closed)
}

struct Pending {
    purpose: Purpose,
    by: String,
    bound: String,
    /// The public key (hex) a `PoolKey` yes enrols.
    public: String,
    started_ms: u64,
    prompt: Box<dyn Asking>,
}

/// The ticket a yes for a card made: whose it is, the card, the hash of its secret, the payloads not yet signed and the key it enrolls.
struct Ticket {
    by: String,
    card: String,
    sha256: String,
    left: std::collections::BTreeSet<String>,
    made_ms: u64,
    enrol: Option<String>,
}

/// A founding card that was answered yes, waiting for the charter it founds.
struct Founded {
    by: String,
    anchor: String,
    made_ms: u64,
}

enum Outcome {
    /// Confirmed: the secret, until the session that asked has read it.
    Yes(Option<String>, u64, Purpose),
    No,
    Late,
}

struct Secret {
    purpose: Purpose,
    bound: String,
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
    /// When prompts for cards were raised in the last day.
    raised: Vec<u64>,
    ticket: Option<Ticket>,
    founded: Option<Founded>,
    /// The changes the node drafted that a person may answer.
    pub cards: card::Cards,
}

impl Firsts {
    pub fn new(presence: Box<dyn Presence>) -> Firsts {
        Firsts { presence, asking: None, outcome: None, secret: None, quiet_until_ms: 0, raised: Vec::new(), ticket: None, founded: None, cards: card::Cards::default() }
    }

    pub fn kind(&self) -> &'static str {
        self.presence.kind()
    }

    /// The claim a key is recorded with when the person says yes for it at this node's prompt: `keystore-prompt` for the operating
    /// system's own prompt, `keystore-typed` for every other way of asking (a typed yes counts for nothing in a pool).
    pub fn pool_key_claim(&self) -> Claim {
        if self.presence.kind() == "os" { Claim::keystore_prompt() } else { Claim::keystore_typed() }
    }

    /// Open a prompt for ``by`` (`board/name`): nothing is made until the person says yes.
    /// ``bound`` is the public key (hex) the first key will be: the yes is for that key only, and the dialog shows its words (empty for a phone,
    /// whose key is made by the phone after the scan and shown on both screens then).
    pub fn ask(&mut self, purpose: Purpose, by: &str, bound: &str, terminal: &str, now: u64) -> Result<()> {
        if self.asking.as_ref().is_some_and(|p| p.by == by && p.purpose == purpose) {
            return Ok(());
        }
        if self.asking.is_some() {
            return Err(crate::limits::floor("The person is being asked something already: answer that first."));
        }
        if now < self.quiet_until_ms {
            return Err(crate::limits::floor("The person was asked a moment ago and did not say yes; wait a few minutes before asking again."));
        }
        let key = match (purpose, bound) {
            (Purpose::Key, "") => return Err(Error::Invalid("The first key is asked for with its public key, so the yes is for that key.".into())),
            (Purpose::Key, public) => format!(" with the key {}", crate::phone::words(&crate::grants::person::fingerprint_of("ed25519", public)?)),
            (Purpose::Phone, _) => String::new(),
            (Purpose::PoolKey | Purpose::Card, _) => return Err(Error::Invalid("A change is asked for by its card.".into())),
        };
        let what = format!("poolhouse: allow the session {by} to add {}{key} to this computer?", purpose.words());
        let prompt = self.presence.begin(&what, terminal)?;
        self.asking = Some(Pending { purpose, by: by.into(), bound: if purpose == Purpose::Key { bound.into() } else { String::new() }, public: String::new(), started_ms: now, prompt });
        self.outcome = None;
        Ok(())
    }

    /// Open a prompt for ``by`` (`board/name`) that says the sentence the node wrote for the card ``card``: a yes makes the ticket for that
    /// card, and for `PoolKey` also enrols ``public``, which the card names. Asking again for the card that is being asked is the same ask.
    pub fn ask_card(&mut self, purpose: Purpose, by: &str, card: &str, public: &str, terminal: &str, now: u64) -> Result<()> {
        if self.asking.as_ref().is_some_and(|p| p.by == by && p.purpose == purpose && p.bound == card) {
            return Ok(());
        }
        if self.asking.is_some() {
            return Err(crate::limits::floor("The person is being asked something already: answer that first."));
        }
        if now < self.quiet_until_ms {
            return Err(crate::limits::floor("The person was asked a moment ago and did not say yes; wait a few minutes before asking again."));
        }
        let held = self.cards.get(card, now).cloned().ok_or_else(|| Error::Invalid("There is no such change waiting: draft it again.".into()))?;
        match purpose {
            Purpose::PoolKey if !held.keys.iter().any(|k| k == public) => return Err(Error::Invalid("The change does not name that key.".into())),
            Purpose::PoolKey | Purpose::Card => {}
            _ => return Err(Error::Invalid("Only a card is asked for by its id.".into())),
        }
        self.raised.retain(|t| now.saturating_sub(*t) < DAY_MS);
        if self.raised.len() >= MOST_PROMPTS_PER_DAY || self.raised.iter().filter(|t| now.saturating_sub(**t) < 10 * 60_000).count() >= MOST_PROMPTS_PER_TEN_MINUTES {
            return Err(crate::limits::floor("The person was asked a few times already; this change waits until they can be asked again."));
        }
        let prompt = self.presence.begin(&held.sentence, terminal)?;
        self.raised.push(now);
        self.asking = Some(Pending { purpose, by: by.into(), bound: held.id, public: public.into(), started_ms: now, prompt });
        self.outcome = None;
        Ok(())
    }

    /// Show the node a payload a signer is about to sign: whether the ticket ``secret`` that ``by`` was given names it, and it is the first
    /// time. Each payload is shown once, a ticket lasts two minutes, and it is good only for the session it was given to.
    pub fn spend_payload(&mut self, by: &str, secret: &str, payload: &str, now: u64) -> std::result::Result<(), &'static str> {
        let Some(t) = self.ticket.as_mut() else { return Err("No yes was given for that, or it was used up: ask the person again.") };
        if t.by != by || secret.is_empty() || !same(&t.sha256, &sha256_hex(secret.as_bytes())) {
            return Err("That ticket is not the one the person's yes made for this session.");
        }
        if now.saturating_sub(t.made_ms) > card::TICKET_FOR_MS {
            self.ticket = None;
            return Err("The person's yes is more than two minutes old: ask again.");
        }
        if !t.left.remove(payload) {
            return Err("The person's yes was not for those bytes, or they were signed already.");
        }
        Ok(())
    }

    /// The key ``by``'s yes enrolled and the node has not yet entered in the person keys, once.
    pub fn take_enrolment(&mut self, by: &str) -> Option<String> {
        self.ticket.as_mut().filter(|t| t.by == by).and_then(|t| t.enrol.take())
    }

    /// Whether ``by`` answered yes for the card that founds the charter of ``anchor`` less than ten minutes ago; true once.
    pub fn founding_confirmed(&mut self, by: &str, anchor: &str, now: u64) -> bool {
        let ok = self.founded.as_ref().is_some_and(|f| f.by == by && f.anchor == anchor && now.saturating_sub(f.made_ms) <= SECRET_FOR_MS);
        if ok {
            self.founded = None;
        }
        ok
    }

    fn poll(&mut self, now: u64) {
        if let Some(p) = &mut self.asking {
            let late = now.saturating_sub(p.started_ms) > ASK_FOR_MS;
            let answer = if late { None } else { p.prompt.poll() };
            if answer.is_some() || late {
                let done = self.asking.take().expect("checked above");
                self.outcome = Some((done.by.clone(), match answer {
                    Some(true) if matches!(done.purpose, Purpose::PoolKey | Purpose::Card) => self.make_ticket(&done, now),
                    Some(true) => self.make(done.purpose, &done.bound, now),
                    Some(false) => Outcome::No,
                    None => Outcome::Late,
                }));
                if !matches!(self.outcome, Some((_, Outcome::Yes(..)))) {
                    self.quiet_until_ms = now + QUIET_MS;
                }
            }
        }
    }

    /// A live phone yes belonging to this requester, before its one-time secret is read.
    pub(crate) fn phone_ready(&mut self, by: &str, now: u64) -> bool {
        self.poll(now);
        self.offered(Purpose::Phone, now)
            && matches!(&self.outcome, Some((who, Outcome::Yes(Some(_), expires, Purpose::Phone))) if who == by && now <= *expires)
    }

    /// What became of the prompt ``by`` opened, as a reply: the secret comes once, in the reply that first says yes.
    pub fn status(&mut self, by: &str, now: u64) -> Value {
        self.poll(now);
        if let Some(p) = self.asking.as_ref().filter(|p| p.by == by) {
            return json!({"state": "asking", "purpose": p.purpose.words(), "left_ms": ASK_FOR_MS.saturating_sub(now.saturating_sub(p.started_ms))});
        }
        match &mut self.outcome {
            Some((who, Outcome::Yes(_, expires, _))) if who == by && now > *expires => json!({"state": "expired"}),
            Some((who, Outcome::Yes(secret, expires, purpose))) if who == by => json!({"state": "confirmed", "purpose": purpose.name(), "secret": secret.take(), "expires_ms": *expires}),
            Some((who, Outcome::No)) if who == by => json!({"state": "refused"}),
            Some((who, Outcome::Late)) if who == by => json!({"state": "expired"}),
            _ => json!({"state": "none"}),
        }
    }

    /// The ticket for the card ``done`` was asked for: the secret is handed once, to the asker; the node keeps only its hash.
    fn make_ticket(&mut self, done: &Pending, now: u64) -> Outcome {
        let Some(held) = self.cards.get(&done.bound, now).cloned() else { return Outcome::No };
        let mut raw = [0u8; 32];
        if getrandom::fill(&mut raw).is_err() {
            return Outcome::No;
        }
        let secret = crate::phone::enrol::b64url(&raw);
        let enrol = (done.purpose == Purpose::PoolKey).then(|| done.public.clone());
        self.ticket = Some(Ticket { by: done.by.clone(), card: held.id.clone(), sha256: sha256_hex(secret.as_bytes()), left: held.payloads.iter().cloned().collect(), made_ms: now, enrol });
        if let Some(anchor) = held.founding {
            self.founded = Some(Founded { by: done.by.clone(), anchor, made_ms: now });
        }
        Outcome::Yes(Some(secret), now + card::TICKET_FOR_MS, done.purpose)
    }

    fn make(&mut self, purpose: Purpose, bound: &str, now: u64) -> Outcome {
        let mut raw = [0u8; 32];
        if getrandom::fill(&mut raw).is_err() {
            return Outcome::No;
        }
        let secret = crate::phone::enrol::b64url(&raw);
        self.secret = Some(Secret { purpose, bound: bound.into(), sha256: sha256_hex(secret.as_bytes()), at_ms: now });
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

    /// Spend the secret of ``purpose`` for the key ``bound`` (the one the yes was for; empty for a phone): it is good once, for ten minutes, and one wrong guess spends it too.
    pub fn take(&mut self, purpose: Purpose, secret: &str, bound: &str, now: u64) -> bool {
        let found = self.secret.take();
        found.is_some_and(|s| s.purpose == purpose && same(&s.bound, bound) && now >= s.at_ms && now - s.at_ms <= SECRET_FOR_MS && !secret.is_empty() && same(&s.sha256, &sha256_hex(secret.as_bytes())))
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
    if method == "ticket_spend" {
        return node.firsts.spend_payload(&by, text(p, "ticket")?, text(p, "payload")?, now).map(|()| json!({"spent": true})).map_err(|why| Error::Denied(why.into()));
    }
    if method == "presence_ask" {
        let purpose = Purpose::named(text(p, "purpose")?)?;
        if matches!(purpose, Purpose::PoolKey | Purpose::Card) {
            if !person_session(node, &holder) {
                return Err(Error::Denied("Only the person's own session asks for a change to be answered at the computer's prompt; an agent asks the person to run the command.".into()));
            }
            let card = text(p, "card")?;
            let public = text(p, "public")?;
            let enrolled = !public.is_empty() && node.persons.claim_of(public).is_some();
            if purpose == Purpose::PoolKey && enrolled {
                return Err(Error::Invalid("That key is enrolled already: a change it signs is a card.".into()));
            }
            node.firsts.ask_card(purpose, &by, card, public, text(p, "terminal")?, now)?;
        } else {
            if !node.persons.is_empty() {
                return Err(Error::Denied("A person key is enrolled already: a further key or phone is approved by one that is, not by this prompt.".into()));
            }
            node.firsts.ask(purpose, &by, text(p, "public")?, text(p, "terminal")?, now)?;
        }
    }
    let out = phone_status(node, &by, now, "0.0.0.0:7449".parse().expect("fixed phone address"))?;
    if out["state"] == "confirmed" && out["purpose"] == "pool_key" {
        if let Some(public) = node.firsts.take_enrolment(&by) {
            let claim = node.firsts.pool_key_claim();
            node.persons.add_claimed_key(crate::grants::person::ED25519, &public, "this computer's key for a pool", now, claim)?;
        }
    }
    Ok(out)
}

/// Whether ``holder`` is the person's own session, which the node marks and no claim sets.
pub(crate) fn person_session(node: &mut Node, holder: &crate::identity::Holder) -> bool {
    node.host(&holder.board).ok().and_then(|hosted| hosted.names.get(&holder.name).map(|i| i.person)).unwrap_or(false)
}

fn phone_status(node: &mut Node, by: &str, now: u64, listen: std::net::SocketAddr) -> Result<Value> {
    if node.firsts.phone_ready(by, now) && node.phone_port == 0 {
        if let Some(shared) = node.local_server.upgrade() {
            crate::phone::http::start_locked(node, shared, listen)?;
            node.pusher = Box::new(crate::phone::push::Web::start(&node.dir));
        }
    }
    let mut out = node.firsts.status(by, now);
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

    /// A public key (the Ed25519 base point) for the first key to be.
    const KEY: &str = "5866666666666666666666666666666666666666666666666666666666666666";
    /// Another one (the curve's order-2 point is invalid, so this is a different valid point).
    const OTHER: &str = "c9a3f86aae465f0e56513864510f3997561fa2c9e85ea21dc2292309f3cd6022";

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
    fn the_windows_prompt_is_a_base64_of_a_utf16_script_and_the_sentence_travels_in_the_environment() {
        assert_eq!((super::base64(b"M"), super::base64(b"Ma"), super::base64(b"Man"), super::base64(b"")), ("TQ==".into(), "TWE=".into(), "TWFu".into(), String::new()));
        let command = super::windows_dialog("Poolhouse - make the pool home. Key ab12 cd34.");
        let args: Vec<String> = command.get_args().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(&args[..4], ["-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand"]);
        assert!(!args[4].contains("make the pool"), "the sentence is not in the script");
        assert!(command.get_envs().any(|(k, v)| k == "POOLHOUSE_PROMPT" && v.is_some_and(|v| v.to_string_lossy().contains("make the pool home"))));
    }

    #[test]
    fn expired_phone_yes_exposes_no_secret_link_or_listener() {
        let dir = tempfile::tempdir().unwrap();
        let shared = std::sync::Arc::new(std::sync::Mutex::new(Node::open(dir.path()).unwrap()));
        let reserved = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = reserved.local_addr().unwrap();
        let (firsts, answer) = person();
        let mut node = shared.lock().unwrap();
        node.firsts = firsts;
        node.local_server = std::sync::Arc::downgrade(&shared);
        *answer.lock().unwrap() = Some(true);
        node.firsts.ask(Purpose::Phone, "demo/a", "", "", 0).unwrap();
        assert!(node.firsts.phone_ready("demo/a", 1));
        let expired = phone_status(&mut node, "demo/a", SECRET_FOR_MS + 2, address).unwrap();
        assert_eq!(expired["state"], "expired");
        assert!(expired["secret"].is_null() && expired["link"].is_null());
        assert_eq!(node.phone_port, 0, "an expired yes opens no listener");
        let again = phone_status(&mut node, "demo/a", SECRET_FOR_MS + 3, address).unwrap();
        assert_eq!(again["state"], "expired");
        assert!(again["secret"].is_null() && again["link"].is_null(), "polling cannot renew or expose it");
    }

    #[test]
    fn activation_failure_keeps_the_same_person_yes_for_its_own_requesters_retry() {
        let dir = tempfile::tempdir().unwrap();
        let shared = std::sync::Arc::new(std::sync::Mutex::new(Node::open(dir.path()).unwrap()));
        let occupied = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = occupied.local_addr().unwrap();
        let (firsts, answer) = person();
        {
            let mut node = shared.lock().unwrap();
            node.firsts = firsts;
            node.local_server = std::sync::Arc::downgrade(&shared);
            node.firsts.ask(Purpose::Phone, "demo/a", "", "", 10).unwrap();
            assert_eq!(phone_status(&mut node, "demo/a", 11, address).unwrap()["state"], "asking");
            assert_eq!(node.phone_port, 0);
            *answer.lock().unwrap() = Some(true);
            assert_eq!(phone_status(&mut node, "demo/b", 12, address).unwrap()["state"], "none");
            assert_eq!(node.phone_port, 0, "another session cannot open it");
            assert!(phone_status(&mut node, "demo/a", 13, address).is_err());
            assert_eq!(node.phone_port, 0, "no false listener after failed bind");
            assert!(node.firsts.phone_ready("demo/a", 14), "secret was not consumed or exposed");
        }
        drop(occupied);
        {
            let mut node = shared.lock().unwrap();
            let result = phone_status(&mut node, "demo/a", 15, address).unwrap();
            assert_eq!(result["state"], "confirmed");
            assert_eq!(result["expires_ms"], 12 + SECRET_FOR_MS, "retry never renews person authorization");
            assert!(result["link"].is_string() && result["secret"].is_string());
            assert_eq!(node.phone_port, address.port());
            let replay = phone_status(&mut node, "demo/a", 16, address).unwrap();
            assert!(replay["link"].is_null() && replay["secret"].is_null(), "one read only");
            node.stop.store(true, std::sync::atomic::Ordering::SeqCst);
        }
        let until = std::time::Instant::now() + std::time::Duration::from_secs(2);
        while shared.lock().unwrap().phone_port != 0 && std::time::Instant::now() < until {
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert_eq!(shared.lock().unwrap().phone_port, 0);
    }

    #[test]
    fn listener_readiness_requires_the_original_requester_and_a_live_unread_phone_yes() {
        let (mut firsts, answer) = person();
        firsts.ask(Purpose::Phone, "demo/a", "", "", 100).unwrap();
        assert!(!firsts.phone_ready("demo/a", 101), "waiting is not permission");
        *answer.lock().unwrap() = Some(true);
        assert!(!firsts.phone_ready("demo/b", 102), "another requester cannot activate");
        assert!(firsts.phone_ready("demo/a", 103));
        assert!(firsts.phone_ready("demo/a", 104), "retry does not make another secret");
        let confirmed = firsts.status("demo/a", 105);
        assert_eq!(confirmed["expires_ms"], 102 + SECRET_FOR_MS);
        assert!(!firsts.phone_ready("demo/a", 106), "a read yes cannot activate again");
        let (mut late, answer) = person();
        *answer.lock().unwrap() = Some(true);
        late.ask(Purpose::Phone, "demo/a", "", "", 0).unwrap();
        assert!(late.phone_ready("demo/a", 1));
        assert!(!late.phone_ready("demo/a", SECRET_FOR_MS + 2), "expired yes is not renewed");
        let (mut key, answer) = person();
        *answer.lock().unwrap() = Some(true);
        key.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
        assert!(!key.phone_ready("demo/a", 1), "a key yes cannot open the phone endpoint");
        let (mut refused, answer) = person();
        *answer.lock().unwrap() = Some(false);
        refused.ask(Purpose::Phone, "demo/a", "", "", 0).unwrap();
        assert!(!refused.phone_ready("demo/a", 1), "a refusal leaves the endpoint closed");
    }

    #[test]
    fn nothing_is_made_until_the_person_says_yes_and_only_the_asker_reads_it() {
        let (mut firsts, answer) = person();
        firsts.ask(Purpose::Key, "demo/a", KEY, "", 1000).unwrap();
        assert_eq!(firsts.status("demo/a", 2000)["state"], "asking");
        assert_eq!(firsts.status("demo/b", 2000)["state"], "none", "another session does not even see the prompt");
        assert!(!firsts.take(Purpose::Key, "guess", KEY, 2000));
        *answer.lock().unwrap() = Some(true);
        assert_eq!(firsts.status("demo/b", 3000)["state"], "none", "nor the secret");
        let said = firsts.status("demo/a", 3000);
        assert_eq!(said["state"], "confirmed");
        let secret = said["secret"].as_str().unwrap().to_string();
        assert!(firsts.status("demo/a", 3500)["secret"].is_null(), "the secret is read once");
        assert!(!firsts.take(Purpose::Phone, &secret, "", 4000), "a key's secret adds no phone, and one try spends it");
        assert!(!firsts.take(Purpose::Key, &secret, KEY, 4000), "spent");
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
        assert!(firsts.take(Purpose::Phone, &secret, "", 20));
        assert!(!firsts.take(Purpose::Phone, &secret, "", 30), "once");
        firsts.ask(Purpose::Phone, "demo/a", "", "", 40).unwrap();
        let late = firsts.status("demo/a", 50)["secret"].as_str().unwrap().to_string();
        assert!(!firsts.take(Purpose::Phone, &late, "", 50 + SECRET_FOR_MS + 1), "ten minutes");
    }

    #[test]
    fn a_no_or_no_answer_makes_nothing_and_keeps_the_node_from_asking_again_for_a_while() {
        let (mut firsts, answer) = person();
        firsts.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
        *answer.lock().unwrap() = Some(false);
        assert_eq!(firsts.status("demo/a", 10)["state"], "refused");
        assert!(matches!(firsts.ask(Purpose::Key, "demo/a", KEY, "", 20), Err(Error::Quota(_))), "a session cannot pester the person with prompts");
        *answer.lock().unwrap() = None;
        firsts.ask(Purpose::Key, "demo/a", KEY, "", QUIET_MS + 100).unwrap();
        assert_eq!(firsts.status("demo/a", QUIET_MS + ASK_FOR_MS + 200)["state"], "expired", "no answer in two minutes is a no");
    }

    #[test]
    fn a_node_started_with_no_presence_refuses_and_no_name_turns_the_question_off() {
        let mut firsts = Firsts::new(default_presence());
        assert!(matches!(firsts.ask(Purpose::Key, "demo/a", KEY, "", 0), Err(Error::Denied(_))), "no way to ask is a no, never a yes");
        assert_eq!(firsts.kind(), "closed");
        for name in ["auto", "off", "none", "unattended", "yes", "test", ""] {
            assert!(Mode::parse(name).is_none(), "{name:?} is not a way to ask");
        }
        assert!(Mode::parse("os").is_some() && Mode::parse("terminal").is_some());
    }

    #[test]
    fn the_operating_systems_prompt_is_raised_only_for_the_account_real_state_directory() {
        let dir = std::env::temp_dir().join(format!("presence-os-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let mut os = Os::new(&dir);
        let refused = os.begin("poolhouse: allow the session demo/a to add the first person key to this computer?", "");
        let _ = std::fs::remove_dir_all(&dir);
        assert!(matches!(refused, Err(Error::Denied(_))), "a temporary state directory is not the account's own, so nothing is spawned");
    }

    #[test]
    fn a_yes_for_one_key_enrols_no_other_key_and_the_dialog_names_the_key() {
        let (mut firsts, answer) = person();
        *answer.lock().unwrap() = Some(true);
        firsts.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
        let secret = firsts.status("demo/a", 10)["secret"].as_str().unwrap().to_string();
        assert!(!firsts.take(Purpose::Key, &secret, OTHER, 20), "the yes was for another key");
        let (mut firsts, answer) = person();
        *answer.lock().unwrap() = Some(true);
        firsts.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
        let secret = firsts.status("demo/a", 10)["secret"].as_str().unwrap().to_string();
        assert!(!firsts.take(Purpose::Key, &secret, "", 20), "a key must be named");
        assert!(matches!(firsts.ask(Purpose::Key, "demo/b", "", "", 30), Err(Error::Invalid(_)) | Err(Error::Quota(_))), "a first key is asked for with its key");
        let (mut firsts, _) = person();
        assert!(matches!(firsts.ask(Purpose::Key, "demo/a", "", "", 0), Err(Error::Invalid(_))));
        assert!(matches!(firsts.ask(Purpose::Key, "demo/a", "not a key", "", 0), Err(Error::Invalid(_))));
    }

    #[test]
    fn a_yes_that_comes_after_the_time_to_answer_makes_nothing_whether_or_not_anyone_polled() {
        let (mut firsts, answer) = person();
        firsts.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
        *answer.lock().unwrap() = Some(true);
        assert_eq!(firsts.status("demo/a", ASK_FOR_MS + 1)["state"], "expired", "answered hours later is no answer");
        assert!(!firsts.offered(Purpose::Key, ASK_FOR_MS + 2));
    }

    #[test]
    fn only_the_accounts_own_node_directory_is_the_real_one() {
        let home = std::env::temp_dir().join(format!("presence-home-{}", std::process::id()));
        let node = home.join(".poolhouse").join("node");
        std::fs::create_dir_all(node.join("x")).unwrap();
        std::fs::create_dir_all(home.join(".poolhouse").join("y")).unwrap();
        let ok = is_the_real_state(&node, &home);
        let decoy_in_node = is_the_real_state(&node.join("x"), &home);
        let decoy_beside = is_the_real_state(&home.join(".poolhouse").join("y"), &home);
        let root = is_the_real_state(&home.join(".poolhouse"), &home);
        let elsewhere = is_the_real_state(&home, &home);
        let _ = std::fs::remove_dir_all(&home);
        assert!(ok && !decoy_in_node && !decoy_beside && !root && !elsewhere, "{ok} {decoy_in_node} {decoy_beside} {root} {elsewhere}");
    }

    #[test]
    fn one_prompt_is_open_at_a_time() {
        let (mut firsts, _) = person();
        firsts.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
        firsts.ask(Purpose::Key, "demo/a", KEY, "", 5).unwrap();
        assert!(matches!(firsts.ask(Purpose::Phone, "demo/b", "", "", 10), Err(Error::Quota(_))));
    }

    #[test]
    fn the_words_of_a_prompt_are_plain() {
        assert_eq!(plain("a \"quoted\"\nline; rm -rf /"), "a quotedline rm -rf /");
    }
}
