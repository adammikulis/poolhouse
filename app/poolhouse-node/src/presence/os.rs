//! The operating system's own prompt, and where a computer has none the person's terminal: how the node puts a question to the person.
//!
//! The prompt is a child process the node polls (`osascript` on macOS, `pkexec` on Linux, PowerShell on Windows). The moment the child ends
//! is noted by a watcher thread, not by whoever polls next, because what a yes makes lives from the yes (`Asking::age_ms`).
//!
//! A dialog ends three ways, and only one of them is the person's word. It exits 0 (a yes), it says the person declined (`Kind::declined`: the
//! system's own cancel), or it ends some other way (it could not be shown, it crashed, the session has no screen): that is a failure, kept with the
//! system's words (`Asking::failed`), announced to the person outside the dialog, and never counted as a no. Time running out gives no answer at all.

use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use super::{announce, terminal, Asking, Presence};
use crate::error::{Error, Result};

/// How long a prompt waits for the person.
pub(crate) const ASK_FOR_MS: u64 = 120_000;

/// How the person is told, apart from the dialog, that the node waits for them or that its dialog failed: a title and a sentence.
pub(super) type Tell = Arc<dyn Fn(&str, &str) + Send + Sync>;

/// Which system's dialog a child is, because each says in its own words that the person declined it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum Kind {
    /// `osascript`: the person's cancel is an error of code -128, "User canceled".
    Mac,
    /// polkit's `pkexec`: exit 126 is a dismissed dialog or a person who is not authorized; 127 is no authorization at all (no agent, no session).
    Pkexec,
    /// The PowerShell script (`WINDOWS_PROMPT`): exit 3 is a person who did not verify; any other failure of the script is exit 1.
    Windows,
}

/// The exit status the Windows script gives when the person did not verify.
const WINDOWS_DECLINED: i32 = 3;

/// How a dialog ended.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) enum End {
    Yes,
    /// The person declined at the dialog.
    No,
    /// The dialog could not be shown or failed: the system's words.
    Failed(String),
}

/// What a dialog that exited with ``code`` and said ``said`` on its standard error meant.
pub(super) fn verdict(kind: Kind, code: Option<i32>, said: &str) -> End {
    if code == Some(0) {
        return End::Yes;
    }
    let declined = match kind {
        Kind::Mac => said.contains("User canceled") || said.contains("(-128)"),
        Kind::Pkexec => code == Some(126),
        Kind::Windows => code == Some(WINDOWS_DECLINED),
    };
    if declined {
        return End::No;
    }
    let how = code.map_or("it was stopped by a signal".to_string(), |c| format!("exit status {c}"));
    let said = said.trim().chars().take(300).collect::<String>();
    let hint = if kind == Kind::Pkexec && code == Some(127) { " (polkit found no authentication agent in this session)" } else { "" };
    End::Failed(format!("The system's password prompt did not open or ended with an error ({how}){hint}: {}", if said.is_empty() { "it said nothing" } else { &said }))
}

/// The operating system's own prompt, run as a child process the node polls. It ends when it is dropped and, whether or not anything polls,
/// when the time to answer has run out: a dialog nobody answered cannot be answered hours later.
pub(super) struct OsPrompt {
    child: Arc<Mutex<Child>>,
    stop: Arc<AtomicBool>,
    /// What the dialog said and when, noted by the watcher as soon as the child ended on its own.
    answer: Arc<Mutex<Option<(End, Instant)>>>,
}

impl OsPrompt {
    pub(super) fn start(child: Child, wait: Duration, kind: Kind, tell: Tell) -> OsPrompt {
        let (child, stop, answer) = (Arc::new(Mutex::new(child)), Arc::new(AtomicBool::new(false)), Arc::new(Mutex::new(None)));
        let (watched, quit, noted) = (child.clone(), stop.clone(), answer.clone());
        std::thread::spawn(move || {
            let end = Instant::now() + wait;
            while !quit.load(Ordering::SeqCst) {
                if let Ok(mut c) = watched.lock() {
                    if let Ok(Some(status)) = c.try_wait() {
                        let mut said = String::new();
                        if let Some(mut err) = c.stderr.take() {
                            let _ = std::io::Read::read_to_string(&mut err, &mut said);
                        }
                        let how = verdict(kind, status.code(), &said);
                        if let End::Failed(why) = &how {
                            // the node's log, and the person: a dialog that cannot be shown is exactly the one they cannot answer
                            eprintln!("presence: {why}");
                            tell("Poolhouse could not ask you", why);
                        }
                        note(&noted, how);
                        return;
                    }
                    if Instant::now() >= end {
                        // nobody answered: the dialog is taken away and nothing is noted, so time running out is neither a yes nor a no
                        let _ = c.kill();
                        let _ = c.wait();
                        return;
                    }
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        });
        OsPrompt { child, stop, answer }
    }
}

/// Note the first word the dialog gave.
fn note(slot: &Mutex<Option<(End, Instant)>>, how: End) {
    if let Ok(mut held) = slot.lock() {
        held.get_or_insert((how, Instant::now()));
    }
}

impl Asking for OsPrompt {
    fn poll(&mut self) -> Option<bool> {
        match self.answer.lock().ok()?.as_ref()?.0 {
            End::Yes => Some(true),
            End::No => Some(false),
            End::Failed(_) => None,
        }
    }

    fn failed(&self) -> Option<String> {
        match &self.answer.lock().ok()?.as_ref()?.0 {
            End::Failed(why) => Some(why.clone()),
            _ => None,
        }
    }

    fn age_ms(&self) -> u64 {
        self.answer.lock().ok().and_then(|held| held.as_ref().map(|(_, at)| at.elapsed().as_millis() as u64)).unwrap_or(0)
    }
}

impl Drop for OsPrompt {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Ok(mut c) = self.child.lock() {
            let _ = c.kill();
            let _ = c.wait();
        }
    }
}

/// The text of a prompt as the system shows it: plain, short, with nothing in it a script could be written through.
pub(crate) fn plain(what: &str) -> String {
    what.chars().filter(|c| c.is_ascii_alphanumeric() || " .,_-/()".contains(*c)).take(super::card::MOST_SENTENCE).collect()
}

/// Whether this computer can raise a prompt of its own that the person can answer: macOS for an administrator's account (the dialog asks for
/// an administrator's password), Linux with polkit's `pkexec` in a graphical session, Windows always. Where it cannot, no key made here can
/// be above a typed yes, and the node asks on the person's terminal.
pub fn can_prompt() -> bool {
    #[cfg(target_os = "macos")]
    return in_admin_group();
    #[cfg(target_os = "linux")]
    return std::path::Path::new("/usr/bin/pkexec").is_file() && ["DISPLAY", "WAYLAND_DISPLAY"].iter().any(|v| std::env::var_os(v).is_some_and(|d| !d.is_empty()));
    #[cfg(windows)]
    return true;
    #[cfg(not(any(target_os = "macos", target_os = "linux", windows)))]
    return false;
}

/// Whether ``groups`` hold the group ``admin``.
#[cfg(any(target_os = "macos", test))]
pub(crate) fn is_member(groups: &[u32], admin: Option<u32>) -> bool {
    admin.is_some_and(|gid| groups.contains(&gid))
}

#[cfg(target_os = "macos")]
fn in_admin_group() -> bool {
    // SAFETY: getgrnam returns null or a pointer into storage that lives until the next call; the gid is read at once.
    let admin = unsafe {
        let group = libc::getgrnam(c"admin".as_ptr());
        (!group.is_null()).then(|| (*group).gr_gid)
    };
    // SAFETY: getgroups with a zero size only counts; the second call fills a buffer of that many entries.
    let held = unsafe { libc::getgroups(0, std::ptr::null_mut()) };
    let mut groups: Vec<libc::gid_t> = vec![0; usize::try_from(held).unwrap_or(0) + 1];
    // SAFETY: the buffer holds at least the number of entries asked for.
    let n = unsafe { libc::getgroups(i32::try_from(groups.len()).unwrap_or(0), groups.as_mut_ptr()) };
    groups.truncate(usize::try_from(n).unwrap_or(0));
    // SAFETY: getegid takes no arguments and cannot fail.
    groups.push(unsafe { libc::getegid() });
    is_member(&groups.iter().map(|g| *g as u32).collect::<Vec<_>>(), admin.map(|g| g as u32))
}

/// The command that asks the person: the system's own dialog for a password or fingerprint, which exits 0 only when it was given.
fn dialog(what: &str) -> Option<(Command, Kind)> {
    if cfg!(target_os = "macos") {
        return Some((mac_dialog(what), Kind::Mac));
    }
    if cfg!(target_os = "linux") && std::path::Path::new("/usr/bin/pkexec").is_file() {
        let mut command = Command::new("/usr/bin/pkexec");
        command.args(["/usr/bin/env", "--", "/usr/bin/true"]).arg(plain(what).replace(' ', "_"));
        return Some((command, Kind::Pkexec));
    }
    if cfg!(target_os = "windows") {
        return Some((windows_dialog(), Kind::Windows));
    }
    None
}

/// The macOS dialog. The node is a background program with no window of its own, so the script makes the process the frontmost application first
/// (`activate`): the system's password window belongs to the process that asks, and one that is never brought forward can open behind the person's
/// windows. The sentence is the last argument and is never part of the script.
pub(crate) fn mac_dialog(what: &str) -> Command {
    let mut command = Command::new("/usr/bin/osascript");
    command.args([
        "-e", "on run argv",
        "-e", "tell current application to activate",
        "-e", "do shell script \"/usr/bin/true\" with administrator privileges with prompt (item 1 of argv)",
        "-e", "end run", "--",
    ]).arg(plain(what));
    command
}

/// What Windows PowerShell runs to ask the person: Windows Hello (a PIN, a fingerprint or a face) through the system's user consent
/// verifier, and where Hello is not set up the system's credential dialog checked against the account's password. It exits 0 only when
/// the person was verified, and 3 only when the person did not verify (cancelled, or a wrong password): any other end, such as an error because the
/// session has no desktop to show the dialog on (a node started as a service), exits 1 and is a failure, never a no. The sentence is read from its
/// standard input, never put into the script or its arguments.
const WINDOWS_PROMPT: &str = r#"
$ErrorActionPreference = 'Stop'
$say = [Console]::In.ReadToEnd().Trim()
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  $null = [Windows.Security.Credentials.UI.UserConsentVerifier, Windows.Security.Credentials.UI, ContentType=WindowsRuntime]
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  $op = [Windows.Security.Credentials.UI.UserConsentVerifier]::RequestVerificationAsync($say)
  $task = $asTask.MakeGenericMethod([Windows.Security.Credentials.UI.UserConsentVerificationResult]).Invoke($null, @($op))
  $task.Wait() | Out-Null
  if ($task.Result -eq 'Verified') { exit 0 }
  if ($task.Result -notin 'DeviceNotPresent', 'NotConfiguredForUser', 'DisabledByPolicy') { exit 3 }
} catch { }
Add-Type -AssemblyName System.DirectoryServices.AccountManagement
$cred = $Host.UI.PromptForCredential('Poolhouse', $say, $env:USERNAME, '')
if ($null -eq $cred) { exit 3 }
$machine = New-Object System.DirectoryServices.AccountManagement.PrincipalContext('Machine')
if ($machine.ValidateCredentials($cred.UserName, $cred.GetNetworkCredential().Password)) { exit 0 }
exit 3
"#;

/// Standard base64 of ``bytes``, which PowerShell's `-EncodedCommand` takes over UTF-16 text.
pub(crate) fn base64(bytes: &[u8]) -> String {
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

/// Windows PowerShell by its place under the system directory ``system``, never by a name the search path could resolve to another program.
pub(crate) fn powershell_under(system: &std::path::Path) -> std::path::PathBuf {
    system.join("WindowsPowerShell").join("v1.0").join("powershell.exe")
}

/// The system directory as Windows reports it (`GetSystemDirectoryW`), never read from an environment variable a process can set.
#[cfg(windows)]
pub(super) fn system_directory() -> std::path::PathBuf {
    use std::os::windows::ffi::OsStringExt;
    let mut buf = [0u16; 260];
    // SAFETY: the buffer is valid for the length passed, and the call writes at most that many UTF-16 units.
    let n = unsafe { windows_sys::Win32::System::SystemInformation::GetSystemDirectoryW(buf.as_mut_ptr(), buf.len() as u32) } as usize;
    if n == 0 || n >= buf.len() {
        return std::path::PathBuf::from(r"C:\Windows\System32");
    }
    std::path::PathBuf::from(std::ffi::OsString::from_wide(&buf[..n]))
}

#[cfg(not(windows))]
pub(super) fn system_directory() -> std::path::PathBuf {
    std::path::PathBuf::from(r"C:\Windows\System32")
}

/// The command that raises the person's prompt on Windows; the sentence is written to its standard input.
pub(crate) fn windows_dialog() -> Command {
    let utf16: Vec<u8> = WINDOWS_PROMPT.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let mut command = Command::new(powershell_under(&system_directory()));
    command.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", &base64(&utf16)]);
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
    tell: Tell,
}

impl Os {
    pub fn new(state: &std::path::Path) -> Os {
        Os { state: state.into(), tell: Arc::new(announce::notify) }
    }
}

/// No way to ask: a node started with no `--presence` refuses the first key and the first phone.
pub struct Closed;

impl Presence for Closed {
    fn kind(&self) -> &'static str {
        "closed"
    }

    fn begin(&mut self, _what: &str, _terminal: &str) -> Result<Box<dyn Asking>> {
        Err(Error::Denied("This node was started with no way to ask the person, so nothing can be confirmed through it: the installed Poolhouse starts it with the operating system's prompt.".into()))
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
            return Err(Error::Denied("The operating system's prompt is not raised for an administrator account: ask from your own account.".into()));
        }
        if !on_real_home(&self.state) {
            return Err(Error::Denied("The operating system's prompt is raised only for the node of this account's own state directory, never for a test, a checkout or a copy.".into()));
        }
        let (mut command, kind) = dialog(what).ok_or_else(|| Error::Denied("This computer has no operating system prompt the node can ask the person with (Linux needs polkit's pkexec and a desktop session): the node asks on your terminal instead.".into()))?;
        let mut child = command.stdin(if cfg!(target_os = "windows") { Stdio::piped() } else { Stdio::null() }).stdout(Stdio::null()).stderr(Stdio::piped()).spawn().map_err(|e| {
            let why = format!("The operating system prompt did not open: {e}");
            (self.tell)("Poolhouse could not ask you", &why);
            Error::Denied(why)
        })?;
        if let Some(mut stdin) = child.stdin.take() {
            // The sentence for Windows' prompt travels on standard input; dropping the pipe ends it.
            let _ = std::io::Write::write_all(&mut stdin, plain(what).as_bytes());
        }
        // The dialog can open behind the person's windows, or on a screen they are not looking at: it is announced as well.
        (self.tell)("Poolhouse is waiting for you", &plain(what));
        Ok(Box::new(OsPrompt::start(child, Duration::from_millis(ASK_FOR_MS), kind, self.tell.clone())))
    }
}

/// How a node asks, chosen when it starts (`--presence os|terminal`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    /// The operating system's own prompt, and on a computer that cannot raise one (`can_prompt`) the person's terminal: a typed yes.
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
        self.presence_where(can_prompt(), state)
    }

    /// The presence of a node on ``state`` on a computer that ``prompts`` or does not. A computer with no prompt of its own is never left with
    /// no way to ask: the person's terminal is the way, on the account's own node directory too, and what it makes counts as a typed yes.
    pub fn presence_where(self, prompts: bool, state: &std::path::Path) -> Box<dyn Presence> {
        match self {
            Mode::Os if prompts => Box::new(Os::new(state)),
            Mode::Os => Box::new(terminal::Terminal::unprompted()),
            Mode::Terminal => Box::new(terminal::Terminal::new(state)),
        }
    }
}

/// The presence a node asks with unless it was started with another: none.
pub fn default_presence() -> Box<dyn Presence> {
    Box::new(Closed)
}
