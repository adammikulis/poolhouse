//! The node: every board this device hosts, the tokens, the links, and what a session may do.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::{Duration, Instant};

use ed25519_dalek::SigningKey;
use serde_json::{json, Map, Value};

use crate::board::Board;
use crate::cert::{self, Identity as CertIdentity};
use crate::facts::Facts;
use crate::grants::book::Book;
use crate::grants::person::Persons;
use crate::membership::Pool;
use crate::device::{self, fingerprint, Custody, Supplied};
use crate::error::{Error, Result};
use crate::fold::{self, check_local, Context, Entry};
use crate::fsutil::private_dir;
use crate::identity::{self, Holder, Ident, ModelState, Registry, Tokens, Who};
use crate::lease::Leases;
use crate::limits::Rates;
use crate::lineage::{limit_key, Anchor, Asking, Peer};
use crate::landtrust::LandTrust;
use crate::links::Links;
use crate::quarantine::Quarantine;
use crate::registry::Projects;
use crate::row::{valid_name, Kind};
use crate::subs::Subs;
use crate::tally::Tally;

pub const READ_DEFAULT: usize = 200;
pub const READ_MAX: usize = 1000;
/// How many boards a node hosts at most, how many sessions a lineage registers in a minute, and how many boards it creates in an hour.
pub const MAX_BOARDS: usize = 256;
const MOST_REGISTRATIONS_PER_MINUTE: usize = 60;
const MOST_NEW_BOARDS_PER_HOUR: usize = 16;
const MINUTE: Duration = Duration::from_secs(60);
const HOUR: Duration = Duration::from_secs(3600);

/// One board this node holds and the names registered on it.
pub struct Hosted {
    pub board: Board,
    pub names: Registry,
    pub quarantine: Quarantine,
    pub subs: Subs,
    pub book: Book,
    /// What the write limits count, kept as rows arrive (`tally`).
    pub tally: Tally,
}

pub struct Node {
    pub dir: PathBuf,
    pub pool: String,
    pub key: SigningKey,
    /// Where the key is kept (`device`).
    pub custody: Custody,
    /// The device certificate: its hash is the identity peers know this device by.
    pub cert: CertIdentity,
    /// The pool this device belongs to and its record of the devices in it.
    pub members: Pool,
    /// The keys of the person, whose signature makes a grant count.
    pub persons: Persons,
    /// What agents asked of the person and what became of it (`approvals`), and the phones the person answers from (`phone`).
    pub approvals: crate::approvals::Approvals,
    /// What keeps a request that is made again and again from wearing the person down, and what the node knows of what the person said.
    pub fatigue: crate::approvals::fatigue::Fatigue,
    pub evidence: Box<dyn crate::approvals::context::Evidence>,
    pub phones: crate::phone::Phones,
    /// The certificate of the endpoint a phone talks to, and the port it listens on (0 while it does not).
    pub phone_cert: crate::phone::cert::Endpoint,
    pub phone_port: u16,
    /// The prompt of the operating system that the first key and the first phone need, and the one-time secret it makes (never a file).
    pub firsts: crate::presence::Firsts,
    /// The one-time code for a further phone that is waiting to be scanned, in memory only.
    pub offer: Option<crate::phone::enrol::Offer>,
    /// Where a sealed notification goes.
    pub pusher: Box<dyn crate::phone::push::Pusher>,
    /// The other devices whose landing entries this device counts; empty by default.
    pub land_trust: LandTrust,
    pub facts: Facts,
    pub boards: BTreeMap<String, Hosted>,
    pub tokens: Tokens,
    pub links: Links,
    pub projects: Projects,
    pub leases: Leases,
    /// How often a lineage did what is limited (`limits`); not kept across a restart.
    pub rates: Rates,
    /// What one session may write (`writes`).
    pub writes: crate::writes::WriteLimits,
    /// Who is asking, while a request is answered (`lineage::Asking`).
    pub asking: Asking,
    pub stop: Arc<AtomicBool>,
    pub started: Instant,
    /// How many times a whole board was folded (`view`).
    pub folds: u64,
    /// The build this device runs and advertises (`build`).
    pub build: crate::build::Build,
    /// How this machine is attached to the network (`netif::network`), told to every peer in the hello so it can say how to reach this device.
    pub network: String,
}

/// A native session as its harness names it: the model it says it runs, the harness and its own session id.
#[derive(Clone, Copy)]
pub struct Native<'a> {
    pub model: &'a str,
    pub harness: &'a str,
    pub session: &'a str,
}

/// What a request is allowed to touch, decided from its token and the board it names.
pub struct Access {
    pub holder: Holder,
    /// The actor name rows are written under on the target board.
    pub actor: String,
    /// Channels of the target usable by this session; `None` means all of the board.
    pub channels: Option<Vec<String>>,
}

impl Access {
    /// Refuse a session that reaches ``board`` through a link: ``what`` is only done by a session registered on ``board``.
    pub fn of_board(&self, board: &str, what: &str) -> Result<()> {
        if self.holder.board == board {
            Ok(())
        } else {
            Err(Error::Denied(format!("only a session of this board {what}")))
        }
    }
}

impl Node {
    /// Open the node whose state lives in ``dir`` (a directory only its owner can enter).
    pub fn open(dir: &Path) -> Result<Node> {
        Node::open_with(dir, None)
    }

    /// Open the node of ``dir`` as the device ``supplied`` is the key of, or as the one its key file names when nothing is supplied.
    pub fn open_with(dir: &Path, supplied: Option<&Supplied>) -> Result<Node> {
        private_dir(dir)?;
        let (key, custody) = device::load(dir, supplied)?;
        let cert = cert::load_or_create(dir, &key)?;
        let members = Pool::open(&dir.join("pool.json"))?;
        let facts = Facts::open(&dir.join("peers.json"))?;
        let mut node = Node {
            dir: dir.into(), pool: String::new(), cert, members, persons: Persons::open(&dir.join("persons.json"))?,
            approvals: crate::approvals::Approvals::open(&dir.join("approvals.json"))?, fatigue: crate::approvals::fatigue::Fatigue::open(&dir.join("fatigue.json"))?,
            evidence: Box::new(crate::approvals::context::Unrecorded), phones: crate::phone::Phones::open(&dir.join("phones.json"))?,
            phone_cert: crate::phone::cert::load_or_create(dir)?, phone_port: 0, firsts: crate::presence::Firsts::new(crate::presence::default_presence()), offer: None, pusher: Box::new(crate::phone::push::NoPusher), land_trust: LandTrust::open(&dir.join("land_trust.json"))?, facts, key, custody, boards: BTreeMap::new(),
            tokens: Tokens::open(&dir.join("tokens.json"))?, links: Links::open(&dir.join("links.json"))?,
            projects: Projects::open(&dir.join("projects.json"))?, leases: Leases::open(dir)?, rates: Rates::default(), writes: crate::writes::WriteLimits::open(dir)?, asking: Asking::default(),
            stop: Arc::new(AtomicBool::new(false)), started: Instant::now(), folds: 0,
            build: crate::build::Build::from_env(), network: crate::netif::network().into(),
        };
        let root = dir.join("boards");
        if root.is_dir() {
            for entry in std::fs::read_dir(&root)? {
                let id = entry?.file_name().to_string_lossy().to_string();
                if valid_name(&id) {
                    node.host(&id)?;
                }
            }
        }
        node.enrol_self()?;
        Ok(node)
    }

    pub fn fingerprint(&self) -> String {
        fingerprint(&self.key)
    }

    /// The board ``id``, opened (and created) under this node's directory.
    pub fn host(&mut self, id: &str) -> Result<&mut Hosted> {
        if !self.boards.contains_key(id) {
            if !valid_name(id) {
                return Err(Error::Invalid("a board id is a short lower-case word".into()));
            }
            let dir = self.dir.join("boards").join(id);
            private_dir(&dir)?;
            let mut board = Board::open(&dir.join("log"), id, &self.pool, self.key.clone())?;
            let names = Registry::open(&dir.join("names.json"))?;
            let quarantine = Quarantine::open(&dir.join("quarantine.json"))?;
            let subs = Subs::open(&dir.join("subs.json"))?;
            let mut tally = Tally::default();
            tally.catch_up(&mut board, &names)?;
            self.boards.insert(id.into(), Hosted { board, names, quarantine, subs, book: Book::default(), tally });
        }
        Ok(self.boards.get_mut(id).expect("hosted above"))
    }

    fn hosted(&mut self, id: &str) -> Result<&mut Hosted> {
        self.boards.get_mut(id).ok_or_else(|| Error::Denied("this node does not host that board".into()))
    }

    /// Decide what ``token`` may do on ``board``: its own board fully, another only through a link.
    pub fn access(&self, token: &str, board: &str, write: bool) -> Result<Access> {
        let holder = self.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied("the token is not known".into()))?;
        if !self.boards.contains_key(board) {
            return Err(Error::Denied("this node does not host that board".into()));
        }
        if holder.board == board {
            return Ok(Access { actor: holder.name.clone(), holder, channels: None });
        }
        match self.links.grants(&holder.board, board, write) {
            Some(channels) => Ok(Access {
                actor: format!("{}@{}", holder.name, holder.board), holder,
                channels: if channels.is_empty() { None } else { Some(channels) },
            }),
            None => Err(Error::Denied("the session is not registered on that board and no link shares it".into())),
        }
    }

    /// The parent a registration names and the lineage its token is bound to. A token given makes the new session a subagent of the
    /// token's owner, and the new token carries the owner's lineage; a token of the very session being registered proves the
    /// registrant holds it (``true``) and registers it again as what it is.
    fn parent_of(&self, board: &str, token: Option<&str>, who: (&str, &str), from: &[Anchor]) -> Result<(String, bool, Vec<Anchor>)> {
        let Some(token) = token else { return Ok((String::new(), false, from.to_vec())) };
        let Some(holder) = self.tokens.resolve(token).filter(|h| h.board == board) else {
            return Err(Error::Denied("a parent must be a session of this board".into()));
        };
        let held = self.tokens.lineage_of(token).unwrap_or_default().to_vec();
        let names = self.boards.get(board).map(|h| &h.names);
        if names.and_then(|n| n.find(who.0, who.1)) != Some(holder.name.as_str()) {
            return Ok((holder.name.clone(), false, held));
        }
        let parent = names.and_then(|n| n.get(&holder.name)).map(|i| i.parent.clone()).unwrap_or_default();
        let bound = if parent.is_empty() { from.to_vec() } else { held };
        Ok((parent, true, bound))
    }

    /// Drop the tokens of sessions idle for `identity::IDLE_MS` when the node holds as many as it will.
    fn make_room_for_a_token(&mut self) -> Result<()> {
        if self.tokens.len() < identity::MOST_TOKENS {
            return Ok(());
        }
        let now = self.now_ms();
        let idle: Vec<(String, String)> = self.boards.iter()
            .flat_map(|(b, h)| h.names.all().iter().filter(|(_, i)| now.saturating_sub(i.seen_ms) > identity::IDLE_MS).map(move |(n, _)| (b.clone(), n.clone())))
            .collect();
        self.tokens.revoke_where(&|h| idle.iter().any(|(b, n)| *b == h.board && *n == h.name)).map(|_| ())
    }

    /// Register a session on ``board`` (creating the board on first use) from the process ``peer``; a token given makes
    /// the new session a subagent of the token's owner. A main session that is already registered gets a token only from
    /// the lineage it was registered from. A lineage registers sessions and creates boards a few times a minute and an hour,
    /// and a node hosts a bounded number of boards. Returns name, token, whether new.
    pub fn register(&mut self, board: &str, parent_token: Option<&str>, native: Native, peer: &Peer) -> Result<Value> {
        let Native { model, harness, session } = native;
        let (model, harness) = (identity::clean(model)?, identity::clean(harness)?);
        if !valid_name(board) {
            return Err(Error::Invalid("a board id is a short lower-case word".into()));
        }
        if session.is_empty() || harness.is_empty() {
            return Err(Error::Denied("a session name needs the native harness and session id".into()));
        }
        let lineage = peer.lineage();
        let (made, boards) = (format!("register {}", limit_key(&lineage)), format!("board {}", limit_key(&lineage)));
        let new_board = !self.boards.contains_key(board);
        if !self.rates.permits(&made, MOST_REGISTRATIONS_PER_MINUTE, MINUTE) {
            return Err(Error::Quota("this process tree registers sessions as fast as it may: try again in a minute".into()));
        }
        if new_board && (self.boards.len() >= MAX_BOARDS || !self.rates.permits(&boards, MOST_NEW_BOARDS_PER_HOUR, HOUR)) {
            return Err(Error::Quota("this process tree creates boards as fast as it may, or this node hosts as many as it will".into()));
        }
        let (parent, proven, bound) = self.parent_of(board, parent_token, (&harness, session), &lineage)?;
        crate::writes::check_children(self, board, &parent, &harness, session)?;
        self.make_room_for_a_token()?;
        let inherited = model.trim().is_empty().then(|| self.boards.get(board).and_then(|h| h.names.get(&parent)).map(|p| p.model.clone())).flatten().unwrap_or_default();
        let (model, state) = if inherited.is_empty() { (model, ModelState::Claimed) } else { (inherited, ModelState::Inherited) };
        let hosted = self.host(board)?;
        let now = hosted.board.now_ms();
        let (name, created) = hosted.names.assign(Who { harness: &harness, session, parent: &parent }, &model, state, now, &lineage, proven)?;
        if created {
            self.write_identity(board, &name)?;
        }
        let origin = self.host(board)?.board.origin().to_string();
        let token = self.tokens.issue(board, &name, &bound)?;
        self.rates.record(&made, MINUTE);
        if new_board {
            self.rates.record(&boards, HOUR);
        }
        Ok(json!({"name": name, "token": token, "created": created, "parent": parent, "board": board, "origin": origin}))
    }

    /// The counters of ``board`` for the write limits, brought up to the rows it has now.
    pub fn tally(&mut self, board: &str) -> Result<&mut Tally> {
        let Hosted { board, names, tally, .. } = self.hosted(board)?;
        tally.catch_up(board, names)?;
        Ok(tally)
    }

    /// The folded entries of ``board``, in merged order, with the foreign rows that were refused recorded.
    pub fn view(&mut self, board: &str) -> Result<Vec<Entry>> {
        self.folds += 1;
        let Hosted { board, names, .. } = self.hosted(board)?;
        let rows = board.merged();
        let own = board.origin().to_string();
        let mut labels: BTreeMap<String, String> = BTreeMap::new();
        for r in rows.iter().filter(|r| r.origin != own) {
            if !labels.contains_key(&r.origin) {
                let l = board.label(&r.origin)?;
                labels.insert(r.origin.clone(), l);
            }
        }
        let mut label = |o: &str| labels.get(o).cloned().unwrap_or_default();
        let is_local = |n: &str| names.contains(n);
        let (entries, rejected) = fold::fold(&rows, Context { own_origin: &own, label: &mut label, is_local: &is_local });
        for (id, why) in rejected {
            board.reject(&id, &why)?;
        }
        Ok(entries)
    }

    /// Write a message or note as the session behind ``token``.
    pub fn post(&mut self, token: &str, board: &str, kind: Kind, fields: &Map<String, Value>, idem: &str) -> Result<Value> {
        let channel = fold::channel_of(kind, fields);
        let access = self.access(token, board, true)?;
        if access.channels.as_ref().is_some_and(|c| !c.contains(&channel)) {
            return Err(Error::Denied("that channel is not shared with this session".into()));
        }
        if let Some(to) = fields.get("to").and_then(Value::as_str).filter(|t| kind == Kind::Message && t.starts_with(|c: char| c != '#')) {
            if !self.can_receive(board, to)? && !crate::mail::answers_sender(self, board, fields, to)? {
                return Err(Error::Denied(format!("{to} is not a session of this board")));
            }
        }
        if kind == Kind::Message {
            crate::rooms::check_post(self, board, &access.actor, fields)?;
            crate::files::check_post(self, board, fields)?;
        }
        let mut body = fields.clone();
        body.insert(if kind == Kind::Note { "author" } else { "from" }.into(), Value::String(access.actor.clone()));
        let body = Value::Object(body);
        check_local(kind, &access.actor, &body, "")?;
        let admitted = crate::writes::check_post(self, board, &access.holder, &access.actor, kind, fields)?;
        let hosted = self.hosted(board)?;
        let row = hosted.board.append(kind, &access.actor, body, idem)?;
        let reply = json!({"id": row.id(), "seq": row.seq, "sender": access.actor, "status": hosted.board.status(row.seq, &[])});
        crate::writes::wrote(self, board, &access.holder, &access.actor, admitted)?;
        Ok(reply)
    }

    /// Write the current state of the session ``name`` to its board as an identity entry.
    pub fn write_identity(&mut self, board: &str, name: &str) -> Result<()> {
        let hosted = self.hosted(board)?;
        let ident = hosted.names.get(name).cloned().ok_or_else(|| Error::Denied("no such session".into()))?;
        hosted.board.append(Kind::Identity, name, identity_body(name, &ident)?, "")?;
        Ok(())
    }

    /// Whether ``name`` is a session of ``board`` that can be written to: a live local one, or
    /// one another device announced with an identity entry that no later entry retired.
    pub fn can_receive(&mut self, board: &str, name: &str) -> Result<bool> {
        if let Some(i) = self.hosted(board)?.names.get(name) {
            return Ok(!i.retired);
        }
        let mut live = false;
        for e in self.view(board)?.iter().filter(|e| e.kind == Kind::Identity && e.fields.get("name").and_then(Value::as_str) == Some(name)) {
            live = e.fields.get("retired") != Some(&Value::Bool(true));
        }
        Ok(live)
    }
}

/// The body of an identity entry.
pub fn identity_body(name: &str, i: &Ident) -> Result<Value> {
    Ok(json!({"name": name, "parent": i.parent, "family": i.family, "model": i.model, "model_state": serde_json::to_value(i.model_state)?,
              "harness": i.harness, "retired": i.retired, "retired_reason": i.retired_reason}))
}
