//! The limits and tickets of asking, each asker by itself: what a refusal, a flood or a late poll of one session may not do to another's, and
//! what survives a restart of the node.

use std::sync::atomic::Ordering;

use super::card::{Draft, Generated, TICKET_FOR_MS};
use super::firsts::SECRET_FOR_MS;
use super::os::{self, Kind, OsPrompt, Tell, ASK_FOR_MS};
use super::tests::{person, person_aged, Age, Said, Scripted};
use super::*;

const KEY: &str = "5866666666666666666666666666666666666666666666666666666666666666";

fn card(firsts: &mut Firsts, by: &str, payloads: &[&[u8]], generated: bool, now: u64) -> String {
    let payloads: Vec<Vec<u8>> = payloads.iter().map(|p| p.to_vec()).collect();
    let generated = generated.then(|| Generated { public: KEY.into(), seed: [7; 32], claim: crate::gov::keys::Claim::keystore_prompt() });
    firsts.cards.add(by, Draft { sentence: "Poolhouse - make the pool home.", payloads: &payloads, founding: None, generated }, now).unwrap()
}

/// Ask ``by`` for ``id`` with the person saying yes, and read the ticket the yes made.
fn yes(firsts: &mut Firsts, answer: &Said, by: &str, id: &str, now: u64) -> String {
    *answer.lock().unwrap() = Some(true);
    firsts.ask_card(Purpose::Card, by, id, "", now).unwrap();
    let said = firsts.status(by, now);
    *answer.lock().unwrap() = None;
    assert_eq!(said["state"], "confirmed", "{said}");
    said["secret"].as_str().unwrap().to_string()
}

fn hash(bytes: &[u8]) -> String {
    crate::fsutil::sha256_hex(bytes)
}

#[test]
fn a_refusal_of_one_asker_holds_back_no_request_of_that_asker_or_another() {
    let (mut firsts, answer) = person();
    firsts.ask(Purpose::Key, "demo/impostor", KEY, "", 0).unwrap();
    *answer.lock().unwrap() = Some(false);
    assert_eq!(firsts.status("demo/impostor", 10)["state"], "refused");
    *answer.lock().unwrap() = None;
    firsts.ask(Purpose::Key, "demo/impostor", KEY, "", 20).expect("the asker that was refused is asked again at once");
    firsts.ask(Purpose::Key, "demo/person", KEY, "", 30).expect("and so is the person's own session");
}

#[test]
fn many_prompts_in_a_row_are_each_raised_and_every_one_is_open_to_the_person() {
    let (mut firsts, answer) = person();
    for n in 0..25u64 {
        let id = card(&mut firsts, "demo/a", &[format!("change {n}").as_bytes()], false, 0);
        yes(&mut firsts, &answer, "demo/a", &id, n);
    }
    let more = card(&mut firsts, "demo/a", &[b"one more"], false, 0);
    firsts.ask_card(Purpose::Card, "demo/a", &more, "", 30).expect("no count of earlier prompts holds the next one back");
}

#[test]
fn an_asker_that_fills_its_cards_evicts_no_card_of_another() {
    let (mut firsts, _) = person();
    let kept = card(&mut firsts, "demo/person", &[b"mine"], false, 0);
    for n in 0..40 {
        card(&mut firsts, "demo/impostor", &[format!("junk {n}").as_bytes()], false, 1 + n);
    }
    assert!(firsts.cards.get("demo/person", &kept, 100).is_some(), "the person's card is still there");
    assert_eq!(firsts.cards.count("demo/impostor"), super::card::MOST_CARDS, "the flood evicted only its own oldest");
    assert!(firsts.cards.get("demo/impostor", &kept, 100).is_none(), "a card is for the session it was drafted for");
}

#[test]
fn a_node_that_starts_again_carries_no_suppression_of_the_node_before_it() {
    let dir = tempfile::tempdir().unwrap();
    // a state directory that an earlier build filled with its quiet and its counts: nothing reads it, so nothing in it holds a request back
    std::fs::write(dir.path().join("prompts.json"), br#"{"demo/b":{"quiet_until_ms":9999999999999,"raised":[1,2,3,4,5,6,7,8,9,10]},"agents":{"quiet_until_ms":9999999999999,"raised":[1,2,3]}}"#).unwrap();
    let (answer, age) = (Said::default(), Age::default());
    let mut first = Firsts::new(Box::new(Scripted(answer.clone(), age.clone())));
    first.ask(Purpose::Key, "demo/b", KEY, "", 100).unwrap();
    *answer.lock().unwrap() = Some(false);
    assert_eq!(first.status("demo/b", 110)["state"], "refused");
    drop(first);
    *answer.lock().unwrap() = None;
    let mut second = Firsts::new(Box::new(Scripted(answer, age)));
    second.ask(Purpose::Key, "demo/b", KEY, "", 120).expect("a no is not remembered across a restart, and neither is a count");
}

#[test]
fn a_ticket_lasts_two_minutes_from_the_yes_and_not_a_moment_longer() {
    let (mut firsts, answer) = person();
    let id = card(&mut firsts, "demo/a", &[b"first", b"second"], false, 0);
    let ticket = yes(&mut firsts, &answer, "demo/a", &id, 1000);
    firsts.spend_payload("demo/a", &ticket, &hash(b"first"), 1000 + TICKET_FOR_MS).expect("at the last moment it still holds");
    assert!(firsts.spend_payload("demo/a", &ticket, &hash(b"second"), 1000 + TICKET_FOR_MS + 1).is_err(), "a moment later it is over");
    assert!(firsts.spend_payload("demo/a", &ticket, &hash(b"second"), 1000 + 10).is_err(), "and an over ticket is gone, not asleep");
}

#[test]
fn a_ticket_is_made_at_the_yes_and_not_at_the_poll_that_found_it() {
    let (mut firsts, answer, age) = person_aged();
    let id = card(&mut firsts, "demo/a", &[b"payload"], false, 0);
    firsts.ask_card(Purpose::Card, "demo/a", &id, "", 0).unwrap();
    *answer.lock().unwrap() = Some(true);
    age.store(100_000, Ordering::SeqCst);
    let said = firsts.status("demo/a", 110_000);
    assert_eq!(said["state"], "confirmed", "{said}");
    let ticket = said["secret"].as_str().unwrap();
    assert!(firsts.spend_payload("demo/a", ticket, &hash(b"payload"), 135_000).is_err(), "125 seconds after the yes, though 25 after the poll");
}

#[test]
fn a_yes_found_by_a_late_poll_still_counts_when_it_was_given_in_time() {
    let (mut firsts, answer, age) = person_aged();
    let id = card(&mut firsts, "demo/a", &[b"payload"], false, 0);
    firsts.ask_card(Purpose::Card, "demo/a", &id, "", 0).unwrap();
    *answer.lock().unwrap() = Some(true);
    age.store(11_000, Ordering::SeqCst);
    let said = firsts.status("demo/a", ASK_FOR_MS + 10_000);
    assert_eq!(said["state"], "confirmed", "the yes came a second before the wait ended, and was found eleven seconds later: {said}");
}

#[test]
fn a_key_is_enrolled_only_through_the_card_that_made_it_and_only_for_the_asker_of_that_card() {
    let (mut firsts, answer) = person();
    let plain = card(&mut firsts, "demo/a", &[b"no key"], false, 0);
    let made = card(&mut firsts, "demo/a", &[b"a key"], true, 0);
    assert!(matches!(firsts.ask_card(Purpose::PoolKey, "demo/a", &plain, "", 1), Err(Error::Invalid(_))), "a card that made no key enrols none");
    assert!(matches!(firsts.ask_card(Purpose::Card, "demo/a", &made, "", 2), Err(Error::Invalid(_))), "a card that made a key is asked for as one");
    assert!(matches!(firsts.ask_card(Purpose::PoolKey, "demo/b", &made, "", 3), Err(Error::Invalid(_))), "another session has no such card");
    assert!(matches!(firsts.ask_card(Purpose::PoolKey, "demo/a", "no-such-card", "", 4), Err(Error::Invalid(_))));
    *answer.lock().unwrap() = Some(true);
    firsts.ask_card(Purpose::PoolKey, "demo/a", &made, "", 5).unwrap();
    assert_eq!(firsts.status("demo/a", 6)["state"], "confirmed");
    assert!(firsts.take_key("demo/b").is_none(), "another session is given nothing");
    let key = firsts.take_key("demo/a").expect("the asker is given the key the card made");
    assert_eq!((key.public.as_str(), key.seed), (KEY, [7; 32]));
    assert!(firsts.take_key("demo/a").is_none(), "once");
    assert!(firsts.founding_yes("demo/a", "no-such-charter", 8).is_none(), "the yes is for a charter, and this one is not it");
    assert!(matches!(firsts.ask_card(Purpose::PoolKey, "demo/a", &made, "", 7), Err(Error::Invalid(_))), "the card keeps no copy of the key: a second yes hands it to nobody");
}

#[test]
fn a_ticket_is_spent_only_by_the_session_it_was_given_to() {
    let (mut firsts, answer) = person();
    let id = card(&mut firsts, "demo/a", &[b"payload"], false, 0);
    let ticket = yes(&mut firsts, &answer, "demo/a", &id, 0);
    assert!(firsts.spend_payload("demo/b", &ticket, &hash(b"payload"), 1).is_err());
    firsts.spend_payload("demo/a", &ticket, &hash(b"payload"), 1).unwrap();
}

/// The notifications a prompt made, in place of the system's: what the person would have been told outside the dialog.
type Told = std::sync::Arc<std::sync::Mutex<Vec<String>>>;

fn recording() -> (Told, Tell) {
    let told = Told::default();
    let record = told.clone();
    (told, std::sync::Arc::new(move |title: &str, body: &str| record.lock().unwrap().push(format!("{title}: {body}"))))
}

/// A stand-in for the system's dialog: a shell that says ``said`` on its standard error and exits with ``code``, the way `osascript` does when the person
/// cancels (`User canceled`) or when the dialog could not be shown at all. It raises no dialog.
#[cfg(unix)]
fn stand_in_dialog(said: &str, code: i32) -> std::process::Child {
    std::process::Command::new("/bin/sh").args(["-c", "printf '%s\\n' \"$0\" >&2; exit $1", said, &code.to_string()]).stderr(std::process::Stdio::piped()).spawn().unwrap()
}

#[cfg(unix)]
#[test]
fn the_moment_the_dialog_ends_is_noted_by_the_node_and_not_by_whoever_polls_next() {
    let (_, tell) = recording();
    let mut prompt = OsPrompt::start(stand_in_dialog("", 0), std::time::Duration::from_secs(30), Kind::Mac, tell.clone());
    std::thread::sleep(std::time::Duration::from_millis(600));
    assert_eq!(prompt.poll(), Some(true));
    assert!(prompt.age_ms() >= 400, "the dialog ended 600 ms ago and nobody polled until now: {}", prompt.age_ms());
    let mut prompt = OsPrompt::start(stand_in_dialog("", 126), std::time::Duration::from_secs(30), Kind::Pkexec, tell);
    std::thread::sleep(std::time::Duration::from_millis(300));
    assert_eq!(prompt.poll(), Some(false), "pkexec's 126 is a dismissed dialog");
}

#[cfg(unix)]
#[test]
fn a_dialog_that_could_not_be_shown_is_not_a_no_says_why_and_is_announced() {
    let (told, tell) = recording();
    let mut prompt = OsPrompt::start(stand_in_dialog("execution error: Can't open default scripting component. (-1750)", 1), std::time::Duration::from_secs(30), Kind::Mac, tell);
    std::thread::sleep(std::time::Duration::from_millis(400));
    assert_eq!(prompt.poll(), None, "a dialog that failed gave no answer, least of all the person's no");
    let why = prompt.failed().expect("the failure is kept for the person and the asker");
    assert!(why.contains("-1750") && why.contains("scripting component"), "the system's own words are said: {why}");
    let told = told.lock().unwrap().clone();
    assert!(told.len() == 1 && told[0].starts_with("Poolhouse could not ask you") && told[0].contains("-1750"), "the person is told outside the dialog: {told:?}");
}

#[cfg(unix)]
#[test]
fn only_the_dialogs_own_cancel_is_the_persons_no_and_nothing_is_announced_for_it() {
    let (told, tell) = recording();
    let mut prompt = OsPrompt::start(stand_in_dialog("execution error: User canceled. (-128)", 1), std::time::Duration::from_secs(30), Kind::Mac, tell);
    std::thread::sleep(std::time::Duration::from_millis(400));
    assert_eq!(prompt.poll(), Some(false), "the person pressed Cancel");
    assert!(prompt.failed().is_none());
    assert!(told.lock().unwrap().is_empty(), "the person knows they declined");
}

#[cfg(unix)]
#[test]
fn a_dialog_nobody_answered_is_ended_and_gives_no_answer_at_all() {
    let (told, tell) = recording();
    let slow = std::process::Command::new("/bin/sleep").arg("30").spawn().unwrap();
    let mut prompt = OsPrompt::start(slow, std::time::Duration::from_millis(150), Kind::Mac, tell);
    std::thread::sleep(std::time::Duration::from_millis(700));
    assert_eq!(prompt.poll(), None, "time running out is not a no");
    assert!(prompt.failed().is_none(), "and not a failure either: the node expires it itself");
    assert!(told.lock().unwrap().is_empty());
}

#[test]
fn each_systems_own_words_for_the_persons_no_are_the_only_no_and_every_other_end_is_a_failure() {
    use os::{verdict, End::{Failed, No, Yes}};
    assert_eq!(verdict(Kind::Mac, Some(0), ""), Yes);
    assert_eq!(verdict(Kind::Mac, Some(1), "execution error: User canceled. (-128)"), No);
    assert_eq!(verdict(Kind::Pkexec, Some(126), ""), No);
    assert_eq!(verdict(Kind::Windows, Some(3), ""), No);
    for (kind, code, said) in [
        (Kind::Mac, Some(1), "execution error: User interaction is not allowed. (-60007)"),
        (Kind::Mac, Some(1), ""),
        (Kind::Mac, None, "killed"),
        (Kind::Pkexec, Some(127), "Error getting authority: Error initializing authority"),
        (Kind::Pkexec, Some(1), ""),
        (Kind::Windows, Some(1), "Exception calling PromptForCredential"),
        (Kind::Windows, Some(126), ""),
    ] {
        assert!(matches!(verdict(kind, code, said), Failed(_)), "{kind:?} {code:?} {said:?} is a dialog that failed and not a person who said no");
    }
    let Failed(why) = verdict(Kind::Pkexec, Some(127), "") else { panic!("127 fails") };
    assert!(why.contains("no authentication agent") && why.contains("it said nothing"), "{why}");
}

#[test]
fn the_dialog_makes_the_node_the_frontmost_application_and_carries_the_sentence_only_as_its_last_argument() {
    let command = os::mac_dialog("poolhouse: allow \"x\"; rm -rf /");
    let args: Vec<String> = command.get_args().map(|a| a.to_string_lossy().into_owned()).collect();
    assert_eq!(command.get_program(), "/usr/bin/osascript");
    assert!(args.iter().any(|a| a == "tell current application to activate"), "a faceless program brings its dialog forward first: {args:?}");
    let script = args[..args.len() - 2].join("\n");
    assert!(script.contains("with administrator privileges with prompt (item 1 of argv)"), "{script}");
    assert_eq!((args[args.len() - 2].as_str(), args[args.len() - 1].as_str()), ("--", "poolhouse allow x rm -rf /"), "the sentence is plain and never part of the script");
}

#[test]
fn a_notification_carries_its_words_as_arguments_and_never_as_script() {
    let command = super::announce::mac_notification("Poolhouse is waiting for you", "make the pool home");
    let args: Vec<String> = command.get_args().map(|a| a.to_string_lossy().into_owned()).collect();
    assert_eq!(command.get_program(), "/usr/bin/osascript");
    assert_eq!(args[args.len() - 3..], ["--", "make the pool home", "Poolhouse is waiting for you"]);
    assert!(args.iter().any(|a| a.starts_with("display notification (item 1 of argv)")));
    let windows = super::announce::windows_notification();
    assert!(windows.get_args().all(|a| !a.to_string_lossy().contains("waiting")), "the words travel on standard input");
}

#[test]
fn what_waits_for_the_person_is_reported_in_their_words_until_it_is_answered() {
    let (mut firsts, answer) = person();
    assert!(firsts.waiting(0).is_empty());
    let id = card(&mut firsts, "demo/a", &[b"payload"], false, 0);
    firsts.ask_card(Purpose::Card, "demo/a", &id, "", 10).unwrap();
    firsts.ask(Purpose::Phone, "demo/b", "", "", 20).unwrap();
    let waiting = firsts.waiting(30);
    assert_eq!(waiting.len(), 2, "{waiting:?}");
    assert_eq!(waiting[0]["say"], "waiting for you: Poolhouse - make the pool home.");
    assert_eq!((waiting[0]["by"].as_str(), waiting[0]["via"].as_str()), (Some("demo/a"), Some("scripted")));
    assert!(waiting[1]["say"].as_str().unwrap().starts_with("waiting for you: poolhouse allow the session demo/b to add your first phone"), "{waiting:?}");
    assert!(firsts.waiting(30 + ASK_FOR_MS).is_empty(), "a request whose time is past is not reported as waiting");
    *answer.lock().unwrap() = Some(true);
    firsts.settle(40);
    assert!(firsts.waiting(40).is_empty(), "answered, so nothing waits");
}

#[test]
fn a_computer_with_no_prompt_asks_on_the_terminal_and_one_with_a_prompt_asks_with_it() {
    let dir = tempfile::tempdir().unwrap();
    assert_eq!(Mode::Os.presence_where(true, dir.path()).kind(), "os");
    assert_eq!(Mode::Os.presence_where(false, dir.path()).kind(), "terminal", "never left with no way to ask");
    assert!(Firsts::new(Mode::Os.presence_where(false, dir.path())).pool_key_claim() == crate::gov::keys::Claim::keystore_typed(), "what it makes counts as a typed yes");
    assert!(Firsts::new(Mode::Os.presence_where(true, dir.path())).pool_key_claim() == crate::gov::keys::Claim::keystore_prompt());
}

#[test]
fn an_administrators_group_is_found_among_the_groups_of_the_account() {
    assert!(os::is_member(&[20, 80, 501], Some(80)));
    assert!(!os::is_member(&[20, 501], Some(80)), "a standard account");
    assert!(!os::is_member(&[80], None), "no such group");
}

#[test]
fn a_card_is_asked_for_only_while_it_waits_and_not_a_moment_after() {
    let (mut firsts, _) = person();
    let id = card(&mut firsts, "demo/a", &[b"payload"], false, 1000);
    assert!(firsts.cards.get("demo/a", &id, 1000 + super::card::CARD_FOR_MS).is_some(), "at the last moment it still waits");
    assert!(firsts.cards.get("demo/a", &id, 1000 + super::card::CARD_FOR_MS + 1).is_none(), "a moment later it is gone");
    assert!(matches!(firsts.ask_card(Purpose::Card, "demo/a", &id, "", 1000 + super::card::CARD_FOR_MS + 1), Err(Error::Invalid(_))), "and it is not asked for: draft it again");
}

#[test]
fn the_yes_for_a_founding_is_good_for_ten_minutes_and_for_the_charter_it_was_given_for() {
    let (mut firsts, answer) = person();
    let payloads = vec![b"acceptance".to_vec()];
    let id = firsts.cards.add("demo/a", Draft { sentence: "Poolhouse - make the pool home.", payloads: &payloads, founding: Some("anchor-a".into()), generated: None }, 0).unwrap();
    *answer.lock().unwrap() = Some(true);
    firsts.ask_card(Purpose::Card, "demo/a", &id, "", 1000).unwrap();
    assert_eq!(firsts.status("demo/a", 1000)["state"], "confirmed");
    assert!(firsts.founding_yes("demo/a", "anchor-b", 1000).is_none(), "another charter is not the one");
    assert!(firsts.founding_yes("demo/b", "anchor-a", 1000).is_none(), "nor is another session");
    assert!(firsts.founding_yes("demo/a", "anchor-a", 1000 + SECRET_FOR_MS).is_some(), "at the last moment it holds");
    assert!(firsts.founding_yes("demo/a", "anchor-a", 1000 + SECRET_FOR_MS + 1).is_none(), "a moment later it does not");
    assert!(firsts.founding_yes("demo/a", "anchor-a", 2000).is_some(), "asking is not spending");
    firsts.founded("demo/a");
    assert!(firsts.founding_yes("demo/a", "anchor-a", 2000).is_none(), "the pool it was for is founded, so it is spent");
}
