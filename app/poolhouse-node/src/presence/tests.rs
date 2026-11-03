use super::firsts::SECRET_FOR_MS;
use super::os::{self, ASK_FOR_MS};
use super::*;

/// A public key (the Ed25519 base point) for the first key to be.
const KEY: &str = "5866666666666666666666666666666666666666666666666666666666666666";
/// Another one (the curve's order-2 point is invalid, so this is a different valid point).
const OTHER: &str = "c9a3f86aae465f0e56513864510f3997561fa2c9e85ea21dc2292309f3cd6022";

/// What the person said, shared between the test and the prompt it plays.
pub(super) type Said = std::sync::Arc<std::sync::Mutex<Option<bool>>>;
/// How many milliseconds ago the person answered, as the prompt tells it.
pub(super) type Age = std::sync::Arc<std::sync::atomic::AtomicU64>;

/// A person who answers when the test says so, and who answered `Age` milliseconds before the node looks.
pub(super) struct Scripted(pub Said, pub Age);

struct Waiting(Said, Age);

impl Asking for Waiting {
    fn poll(&mut self) -> Option<bool> {
        *self.0.lock().unwrap()
    }

    fn age_ms(&self) -> u64 {
        self.1.load(std::sync::atomic::Ordering::SeqCst)
    }
}

impl Presence for Scripted {
    fn kind(&self) -> &'static str {
        "scripted"
    }

    fn begin(&mut self, _: &str, _: &str) -> Result<Box<dyn Asking>> {
        Ok(Box::new(Waiting(self.0.clone(), self.1.clone())))
    }
}

/// A system dialog that could not be shown: it gives no answer and says why in the system's words.
struct Broken(&'static str);

impl Asking for Broken {
    fn poll(&mut self) -> Option<bool> {
        None
    }

    fn failed(&self) -> Option<String> {
        Some(self.0.into())
    }
}

struct Dud(&'static str);

impl Presence for Dud {
    fn kind(&self) -> &'static str {
        "scripted"
    }

    fn begin(&mut self, _: &str, _: &str) -> Result<Box<dyn Asking>> {
        Ok(Box::new(Broken(self.0)))
    }
}

pub(super) fn person() -> (Firsts, Said) {
    let (firsts, answer, _) = person_aged();
    (firsts, answer)
}

pub(super) fn person_aged() -> (Firsts, Said, Age) {
    let (answer, age) = (Said::default(), Age::default());
    (Firsts::new(Box::new(Scripted(answer.clone(), age.clone()))), answer, age)
}

#[test]
fn the_windows_prompt_is_a_base64_of_a_utf16_script_and_the_sentence_is_not_in_its_arguments() {
    assert_eq!((os::base64(b"M"), os::base64(b"Ma"), os::base64(b"Man"), os::base64(b"")), ("TQ==".into(), "TWE=".into(), "TWFu".into(), String::new()));
    let command = os::windows_dialog();
    let args: Vec<String> = command.get_args().map(|a| a.to_string_lossy().into_owned()).collect();
    assert_eq!(&args[..4], ["-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand"]);
    assert!(command.get_envs().next().is_none(), "no environment carries the sentence");
    let program = std::path::Path::new(command.get_program());
    assert!(program.components().count() > 1 && program.ends_with("powershell.exe"), "the program is a place and not a name the search path resolves: {program:?}");
}

#[test]
fn powershell_is_found_under_the_system_directory_the_system_reports_and_never_by_the_environment() {
    let system = std::env::temp_dir();
    assert_eq!(os::powershell_under(&system), system.join("WindowsPowerShell").join("v1.0").join("powershell.exe"));
    // an environment variable names no place: a process that sets `SystemRoot` to a directory of its own does not change the program that is started
    let before = os::windows_dialog().get_program().to_owned();
    std::env::set_var("SystemRoot", std::env::temp_dir());
    let after = os::windows_dialog().get_program().to_owned();
    std::env::remove_var("SystemRoot");
    assert_eq!(before, after, "the system directory is the one the system reports");
    assert!(!std::path::Path::new(&after).starts_with(std::env::temp_dir()), "{after:?}");
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
fn a_no_makes_nothing_and_the_person_is_asked_again_at_once() {
    let (mut firsts, answer) = person();
    firsts.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
    *answer.lock().unwrap() = Some(false);
    assert_eq!(firsts.status("demo/a", 10)["state"], "refused");
    *answer.lock().unwrap() = None;
    firsts.ask(Purpose::Key, "demo/a", KEY, "", 20).expect("there is no quiet after a no: a request the person has not seen is never held back");
    assert_eq!(firsts.status("demo/a", 30)["state"], "asking");
}

#[test]
fn nobody_answering_is_not_a_no_and_counts_for_nothing() {
    let (mut firsts, _) = person();
    firsts.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
    assert_eq!(firsts.status("demo/a", ASK_FOR_MS + 200)["state"], "expired", "no answer in two minutes makes nothing");
    firsts.ask(Purpose::Key, "demo/a", KEY, "", ASK_FOR_MS + 300).expect("a dialog nobody saw is raised again at once");
    assert_eq!(firsts.status("demo/a", ASK_FOR_MS + 400)["state"], "asking");
}

#[test]
fn a_dialog_that_failed_is_reported_as_the_failure_it_was_and_is_raised_again_at_once() {
    let mut firsts = Firsts::new(Box::new(Dud("The system's password prompt did not open or ended with an error (exit status 1): execution error (-1750)")));
    for n in 0..30u64 {
        firsts.ask(Purpose::Key, "demo/a", KEY, "", n * 10).unwrap_or_else(|e| panic!("request {n} was held back after a dialog that did not open: {e}"));
        let said = firsts.status("demo/a", n * 10 + 1);
        assert_eq!(said["state"], "failed", "a dialog that did not open is not a no: {said}");
        assert!(said["error"].as_str().unwrap().contains("-1750"), "the asker is told the system's words: {said}");
    }
    assert!(firsts.waiting(1000).is_empty(), "a request whose dialog failed is not waiting for an answer that cannot be given");
}

#[test]
fn how_often_the_person_answered_never_refuses_a_request() {
    let (mut firsts, answer) = person();
    for n in 0..30u64 {
        *answer.lock().unwrap() = Some(n % 2 == 0);
        firsts.ask(Purpose::Phone, "demo/a", "", "", n).unwrap_or_else(|e| panic!("request {n} was held back: {e}"));
        firsts.status("demo/a", n);
    }
}

#[test]
fn a_second_request_while_one_is_open_is_told_what_is_open_and_what_to_do() {
    let (mut firsts, _) = person();
    firsts.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
    let Err(Error::Quota(said)) = firsts.ask(Purpose::Phone, "demo/a", "", "", 6) else { panic!("a second question of the same asker is refused while one is open") };
    assert!(said.contains("your first person key") && !said.contains("minutes"), "it says what is waiting for the person, not for how long to wait: {said}");
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
    let ok = os::is_the_real_state(&node, &home);
    let decoy_in_node = os::is_the_real_state(&node.join("x"), &home);
    let decoy_beside = os::is_the_real_state(&home.join(".poolhouse").join("y"), &home);
    let root = os::is_the_real_state(&home.join(".poolhouse"), &home);
    let elsewhere = os::is_the_real_state(&home, &home);
    let _ = std::fs::remove_dir_all(&home);
    assert!(ok && !decoy_in_node && !decoy_beside && !root && !elsewhere, "{ok} {decoy_in_node} {decoy_beside} {root} {elsewhere}");
}

#[test]
fn one_prompt_is_open_for_each_asker_and_one_asker_cannot_block_another() {
    let (mut firsts, _) = person();
    firsts.ask(Purpose::Key, "demo/a", KEY, "", 0).unwrap();
    firsts.ask(Purpose::Key, "demo/a", KEY, "", 5).unwrap();
    assert!(matches!(firsts.ask(Purpose::Phone, "demo/a", "", "", 6), Err(Error::Quota(_))), "a second question of the same asker waits");
    firsts.ask(Purpose::Phone, "demo/b", "", "", 10).expect("another asker's question is not blocked by the first asker's open one");
}

#[test]
fn the_words_of_a_prompt_are_plain() {
    assert_eq!(plain("a \"quoted\"\nline; rm -rf /"), "a quotedline rm -rf /");
}
