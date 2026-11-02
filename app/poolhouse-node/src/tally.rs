//! What the write limits count on a board, kept as rows arrive: the direct messages waiting for each session, and the notes of each
//! author. A check reads these instead of folding the board, and catching up costs the rows written since the last check; a restart
//! counts the log again once.

use std::collections::BTreeMap;

use serde_json::Value;

use crate::board::Board;
use crate::error::Result;
use crate::fold::{fold, Context, DIRECT};
use crate::identity::Registry;
use crate::row::Kind;
use crate::rules::held_back;

/// A direct message that may still be unread: where it is in its origin's log, and who wrote it.
struct Waiting {
    origin: String,
    seq: u64,
    sender: String,
}

#[derive(Default)]
pub struct Tally {
    /// How many rows of each origin are counted: the rows before the first one dated too far ahead to be shown.
    done: BTreeMap<String, usize>,
    /// The seq of the first row of each live segment counted: a log that starts a new segment is counted again.
    first: BTreeMap<String, u64>,
    waiting: BTreeMap<String, Vec<Waiting>>,
    notes: BTreeMap<String, usize>,
    /// Rows counted since this was made: what keeping the counters has cost.
    pub counted: u64,
}

impl Tally {
    /// Count the rows ``board`` has gained since the last call, as the fold would show them.
    pub fn catch_up(&mut self, board: &mut Board, names: &Registry) -> Result<()> {
        let (own, now) = (board.origin().to_string(), board.now_ms());
        for origin in board.origins() {
            let rows = board.rows(&origin);
            let from = self.done.get(&origin).copied().unwrap_or(0);
            if from > rows.len() || self.first.get(&origin).is_some_and(|f| *f != board.first_seq(&origin)) {
                *self = Tally::default();
                return self.catch_up(board, names);
            }
            let cut = if origin == own { rows.len() } else { rows[from..].iter().position(|r| held_back(r, now)).map_or(rows.len(), |at| from + at) };
            let fresh = rows[from..cut].to_vec();
            if fresh.is_empty() {
                continue;
            }
            let label = board.label(&origin)?;
            let mut name = |_: &str| label.clone();
            let is_local = |n: &str| names.contains(n);
            let (entries, _) = fold(&fresh, Context { own_origin: &own, label: &mut name, is_local: &is_local });
            self.counted += fresh.len() as u64;
            self.first.insert(origin.clone(), board.first_seq(&origin));
            self.done.insert(origin, cut);
            for e in entries {
                if e.kind == Kind::Note {
                    *self.notes.entry(e.sender.clone()).or_default() += 1;
                } else if e.kind == Kind::Message && e.channel == DIRECT {
                    if let Some(to) = e.fields.get("to").and_then(Value::as_str) {
                        self.waiting.entry(to.to_string()).or_default().push(Waiting { origin: e.origin, seq: e.seq, sender: e.sender });
                    }
                }
            }
        }
        Ok(())
    }

    /// The direct messages waiting unread for ``to`` (those past what its inbox cursor has reached), and those among them from ``from``.
    pub fn unread(&mut self, to: &str, read: &BTreeMap<String, u64>, from: &str) -> (usize, usize) {
        let Some(list) = self.waiting.get_mut(to) else { return (0, 0) };
        list.retain(|w| w.seq > read.get(&w.origin).copied().unwrap_or(0));
        (list.len(), list.iter().filter(|w| w.sender == from).count())
    }

    /// The notes ``author`` has written on the board.
    pub fn notes_by(&self, author: &str) -> usize {
        self.notes.get(author).copied().unwrap_or(0)
    }
}
