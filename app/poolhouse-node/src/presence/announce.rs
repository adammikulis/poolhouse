//! Telling the person, outside the dialog, that the node is waiting for them or that its dialog failed: the system's own notification.
//!
//! The dialog can open behind other windows, on a screen the person is not looking at, or not at all; a request they have not seen is a request
//! nobody can answer. So every prompt the node raises is announced here as well, and so is every dialog that fails. It is best effort by
//! nature (the person's Focus settings or a session with no desktop can swallow a notification), so it never replaces the dialog or the
//! sentence `pool_status` reports for each open request ("waiting for you: ..."), and a notification that cannot be shown is written to the
//! node's log. Like the dialog, it is raised only for the node of the account's own state directory (`Os::begin` checks that first). The text
//! is passed as arguments after `--` (or on standard input on Windows), never spliced into a script.

use std::process::{Command, Stdio};

use super::plain;

/// Show the system notification ``title`` and ``body``, where this system has one.
pub(super) fn notify(title: &str, body: &str) {
    let (title, body) = (plain(title), plain(body));
    if cfg!(target_os = "macos") {
        run(mac_notification(&title, &body), None);
    } else if cfg!(target_os = "linux") {
        if let Some(command) = linux_notification(&title, &body) {
            run(command, None);
        }
    } else if cfg!(target_os = "windows") {
        run(windows_notification(), Some(format!("{title}\n{body}")));
    }
}

/// Run ``command`` without waiting for it, say in the node's log when it fails, and reap it.
fn run(mut command: Command, stdin: Option<String>) {
    command.stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() }).stdout(Stdio::null()).stderr(Stdio::piped());
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(e) => return eprintln!("presence: the notification did not start: {e}"),
    };
    if let (Some(mut pipe), Some(text)) = (child.stdin.take(), stdin) {
        let _ = std::io::Write::write_all(&mut pipe, text.as_bytes());
    }
    std::thread::spawn(move || match child.wait_with_output() {
        Ok(done) if !done.status.success() => eprintln!("presence: the notification ended with {}: {}", done.status, String::from_utf8_lossy(&done.stderr).trim().chars().take(300).collect::<String>()),
        Err(e) => eprintln!("presence: the notification could not be waited for: {e}"),
        Ok(_) => {}
    });
}

/// macOS: `display notification`, with the title and the sentence as arguments of the script.
pub(crate) fn mac_notification(title: &str, body: &str) -> Command {
    let mut command = Command::new("/usr/bin/osascript");
    command.args(["-e", "on run argv", "-e", "display notification (item 1 of argv) with title (item 2 of argv) sound name \"Submarine\"", "-e", "end run", "--"]).arg(body).arg(title);
    command
}

/// Linux: `notify-send` at its place under `/usr/bin`, as an urgent notification; none where it is not installed.
pub(crate) fn linux_notification(title: &str, body: &str) -> Option<Command> {
    let program = std::path::Path::new("/usr/bin/notify-send");
    program.is_file().then(|| {
        let mut command = Command::new(program);
        command.args(["--app-name=Poolhouse", "--urgency=critical", "--"]).arg(title).arg(body);
        command
    })
}

/// What Windows PowerShell runs to show a toast: the title and the sentence are the two lines of standard input, put in as text nodes.
const WINDOWS_TOAST: &str = r#"
$ErrorActionPreference = 'Stop'
$lines = [Console]::In.ReadToEnd() -split "`n", 2
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$text = $xml.GetElementsByTagName('text')
[void]$text.Item(0).AppendChild($xml.CreateTextNode($lines[0].Trim()))
[void]$text.Item(1).AppendChild($xml.CreateTextNode($lines[1].Trim()))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($toast)
"#;

/// Windows: the toast script, run by Windows PowerShell from the directory the system reports (as the dialog is), with the text on standard input.
pub(crate) fn windows_notification() -> Command {
    let utf16: Vec<u8> = WINDOWS_TOAST.encode_utf16().flat_map(u16::to_le_bytes).collect();
    let mut command = Command::new(super::os::powershell_under(&super::os::system_directory()));
    command.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", &super::os::base64(&utf16)]);
    command
}
