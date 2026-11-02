//! The limits and tickets of asking, each asker by itself: what a refusal, a flood or a late poll of one session may not do to another's, and
//! what survives a restart of the node.

use std::sync::atomic::Ordering;

use super::card::{Draft, Generated, TICKET_FOR_MS};
use super::firsts::SECRET_FOR_MS;
use super::ledger::{MOST_PER_DAY, MOST_PER_TEN_MINUTES, QUIET_MS};
use super::os::{self, OsPrompt, ASK_FOR_MS};
use super::tests::{person, person_aged, Age, Said, Scripted};
use super::*;

const KEY: &str = "5866666666666666666666666666666666666666666666666666666666666666";
const MIN: u64 = 60_000;
const DAY: u64 = 24 * 60 * MIN;

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
fn a_refusal_quiets_only_the_asker_that_was_refused() {
    let (mut firsts, answer) = person();
    firsts.ask(Purpose::Key, "demo/impostor", KEY, "", 0).unwrap();
    *answer.lock().unwrap() = Some(false);
    assert_eq!(firsts.status("demo/impostor", 10)["state"], "refused");
    *answer.lock().unwrap() = None;
    assert!(matches!(firsts.ask(Purpose::Key, "demo/impostor", KEY, "", 20), Err(Error::Quota(_))));
    firsts.ask(Purpose::Key, "demo/person", KEY, "", 30).expect("the person's own session is not quiet because another asker was refused");
}

#[test]
fn an_asker_that_fills_its_allowance_leaves_every_other_asker_its_own() {
    let (mut firsts, answer) = person();
    for n in 0..MOST_PER_TEN_MINUTES {
        let id = card(&mut firsts, "demo/impostor", &[format!("change {n}").as_bytes()], false, 0);
        yes(&mut firsts, &answer, "demo/impostor", &id, n as u64);
    }
    let more = card(&mut firsts, "demo/impostor", &[b"one more"], false, 0);
    assert!(matches!(firsts.ask_card(Purpose::Card, "demo/impostor", &more, "", 10), Err(Error::Quota(_))), "its fourth waits");
    let own = card(&mut firsts, "demo/person", &[b"mine"], false, 0);
    yes(&mut firsts, &answer, "demo/person", &own, 11);
}

#[test]
fn the_first_key_and_the_first_phone_count_against_the_asker_as_a_change_does() {
    let (mut firsts, answer) = person();
    for n in 0..MOST_PER_TEN_MINUTES {
        *answer.lock().unwrap() = Some(true);
        firsts.ask(Purpose::Phone, "demo/impostor", "", "", n as u64).unwrap();
        assert_eq!(firsts.status("demo/impostor", n as u64)["state"], "confirmed");
    }
    *answer.lock().unwrap() = None;
    assert!(matches!(firsts.ask(Purpose::Phone, "demo/impostor", "", "", 10), Err(Error::Quota(_))), "its fourth waits");
    assert!(matches!(firsts.ask(Purpose::Key, "demo/impostor", KEY, "", 11), Err(Error::Quota(_))), "a key counts with the phone it asked for");
    firsts.ask(Purpose::Key, "demo/person", KEY, "", 12).expect("another asker keeps its own allowance");
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
fn the_prompt_limits_and_the_quiet_after_a_no_survive_a_restart_of_the_node() {
    let dir = tempfile::tempdir().unwrap();
    let (answer, age) = (Said::default(), Age::default());
    let mut first = Firsts::kept(Box::new(Scripted(answer.clone(), age.clone())), dir.path());
    for n in 0..MOST_PER_TEN_MINUTES {
        let id = card(&mut first, "demo/a", &[format!("change {n}").as_bytes()], false, 0);
        yes(&mut first, &answer, "demo/a", &id, n as u64);
    }
    first.ask(Purpose::Key, "demo/b", KEY, "", 100).unwrap();
    *answer.lock().unwrap() = Some(false);
    assert_eq!(first.status("demo/b", 110)["state"], "refused");
    drop(first);
    let mut second = Firsts::kept(Box::new(Scripted(answer.clone(), age)), dir.path());
    let more = card(&mut second, "demo/a", &[b"one more"], false, 200);
    assert!(matches!(second.ask_card(Purpose::Card, "demo/a", &more, "", 300), Err(Error::Quota(_))), "three prompts in ten minutes were not forgotten");
    *answer.lock().unwrap() = None;
    assert!(matches!(second.ask(Purpose::Key, "demo/b", KEY, "", 400), Err(Error::Quota(_))), "a no was not forgotten");
    second.ask(Purpose::Key, "demo/b", KEY, "", 110 + QUIET_MS + 1).expect("the quiet ends");
}

#[test]
fn twenty_prompts_a_day_and_the_twenty_first_waits_until_the_first_is_a_day_old() {
    let (mut firsts, answer) = person();
    for n in 0..MOST_PER_DAY as u64 {
        let id = card(&mut firsts, "demo/a", &[format!("change {n}").as_bytes()], false, n * 11 * MIN);
        yes(&mut firsts, &answer, "demo/a", &id, n * 11 * MIN);
    }
    let last = MOST_PER_DAY as u64 * 11 * MIN;
    let more = card(&mut firsts, "demo/a", &[b"the twenty-first"], false, last);
    assert!(matches!(firsts.ask_card(Purpose::Card, "demo/a", &more, "", last), Err(Error::Quota(_))), "the day's twentieth was the last");
    let later = card(&mut firsts, "demo/a", &[b"a day later"], false, DAY + 1);
    firsts.ask_card(Purpose::Card, "demo/a", &later, "", DAY + 1).expect("the first prompt is a day old: there is room again");
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

#[cfg(unix)]
#[test]
fn the_moment_the_dialog_ends_is_noted_by_the_node_and_not_by_whoever_polls_next() {
    let yes = std::process::Command::new("/usr/bin/true").spawn().unwrap();
    let mut prompt = OsPrompt::start(yes, std::time::Duration::from_secs(30));
    std::thread::sleep(std::time::Duration::from_millis(600));
    assert_eq!(prompt.poll(), Some(true));
    assert!(prompt.age_ms() >= 400, "the dialog ended 600 ms ago and nobody polled until now: {}", prompt.age_ms());
    let no = std::process::Command::new("/usr/bin/false").spawn().unwrap();
    let mut prompt = OsPrompt::start(no, std::time::Duration::from_secs(30));
    std::thread::sleep(std::time::Duration::from_millis(300));
    assert_eq!(prompt.poll(), Some(false));
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
