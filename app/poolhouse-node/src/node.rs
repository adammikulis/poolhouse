//! The node: every board this device hosts, the tokens, the links, and what a session may do.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex, Weak};
use std::time::Instant;

use ed25519_dalek::SigningKey;
use serde_json::{json, Map, Value};

use crate::board::Board;
use crate::cert::{self, Identity as CertIdentity};
use crate::facts::Facts;
use crate::grants::book::Book;
use crate::grants::person::Persons;
use crate::gov::signer::{KeyBinding, Signer};
use crate::membership::Pool;
use crate::device::{self, fingerprint, Custody, Supplied};
use crate::error::{Error, Result};
use crate::fold::{self, check_local, Context, Entry};
use crate::fsutil::{private_dir, write_atomic};
use crate::identity::{self, Holder, Ident, ModelState, Registry, Tokens, Who};
use crate::lease::Leases;
use crate::limits::Rates;
use crate::lineage::{shares, Anchor, Asking, Peer};
use crate::landtrust::LandTrust;
use crate::links::Links;
use crate::quarantine::Quarantine;
use crate::registry::Projects;
use crate::row::{valid_name, Kind};
use crate::subs::Subs;
use crate::tally::Tally;

pub const READ_DEFAULT: usize = 200;
pub const READ_MAX: usize = 1000;
/// How many boards a node hosts at most.
pub const MAX_BOARDS: usize = 256;
/// How many boards the live sessions of one process tree hold at most.
pub const MOST_BOARDS_PER_LINEAGE: usize = 32;
/// What a request with a token the node does not hold is told.
const UNKNOWN_TOKEN: &str =
    "the token is not known: its session was retired (an idle session is retired after a day, and so is one its parent ends) or never registered; start a new session";

/// One board this node holds and the names registered on it.
pub struct Hosted {
    pub board: Board,
    pub names: Registry,
    pub quarantine: Quarantine,
    pub subs: Subs,
    pub book: Book,
    /// What the write limits count, kept as rows arrive (`tally`).
    pub tally: Tally,
    /// The processes of the lineage that made the board by registering a session on it; None for a board made any other way.
    pub made_by: Option<Vec<Anchor>>,
}

/// The file in a board's folder that holds the lineage that made it.
const MADE_BY: &str = "made_by.json";

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
    /// The exact local server owning this node; absent for library-only nodes.
    pub(crate) local_server: Weak<Mutex<Node>>,
    /// The prompt of the operating system that the first key and the first phone need, and the one-time secret it makes (never a file).
    pub firsts: crate::presence::Firsts,
    /// The one-time code for a further phone that is waiting to be scanned, in memory only.
    pub offer: Option<crate::phone::enrol::Offer>,
    /// Where a sealed notification goes.
    pub pusher: Box<dyn crate::phone::push::Pusher>,
    /// The other devices whose board entries this device counts; empty by default.
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
    /// The bounds a board opened from now on holds its own log to (`logbounds`).
    pub log_bounds: crate::logbounds::Bounds,
    /// Milliseconds a board that holds nothing but the ends of its sessions is left alone before it may be removed.
    pub board_grace_ms: u64,
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
    /// Who signs for a person when an act of the pool needs it (`gov::signer`); None until one is connected.
    pub signer: Option<Arc<dyn Signer>>,
    /// Whether this device's key is held by the operating system's keystore or in a file; only the first may be a delegate.
    pub key_binding: KeyBinding,
    /// When a person last said no to a kind of act: the same kind is not asked again for ten minutes.
    pub declined: BTreeMap<String, Instant>,
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
        let members = Pool::load(dir)?;
        let facts = Facts::open(&dir.join("peers.json"))?;
        let writes = crate::writes::WriteLimits::open(dir)?;
        let mut node = Node {
            dir: dir.into(), pool: String::new(), cert, members, persons: Persons::open(&dir.join("persons.json"))?,
            approvals: crate::approvals::Approvals::open(&dir.join("approvals.json"))?, fatigue: crate::approvals::fatigue::Fatigue::open(&dir.join("fatigue.json"))?,
            evidence: Box::new(crate::approvals::context::Unrecorded), phones: crate::phone::Phones::open(&dir.join("phones.json"))?,
            phone_cert: crate::phone::cert::load_or_create(dir)?, phone_port: 0, local_server: Weak::new(), firsts: crate::presence::Firsts::new(crate::presence::default_presence()), offer: None, pusher: Box::new(crate::phone::push::NoPusher), land_trust: LandTrust::open(&dir.join("land_trust.json"))?, facts, key, custody, boards: BTreeMap::new(),
            tokens: Tokens::open(&dir.join("tokens.json"))?, links: Links::open(&dir.join("links.json"))?,
            projects: Projects::open(&dir.join("projects.json"))?, leases: Leases::open(dir)?, rates: Rates::default(), board_grace_ms: writes.board_grace_s * 1000, writes, log_bounds: crate::logbounds::Bounds::default(), asking: Asking::default(),
            stop: Arc::new(AtomicBool::new(false)), started: Instant::now(), folds: 0,
            build: crate::build::Build::from_env(), network: crate::netif::network().into(), signer: None, key_binding: KeyBinding::File, declined: BTreeMap::new(),
        };
        let root = dir.join("boards");
        if root.is_dir() {
            crate::reap::clear_trash(&root);
            for entry in std::fs::read_dir(&root)? {
                let id = entry?.file_name().to_string_lossy().to_string();
                if valid_name(&id) {
                    node.host(&id)?;
                }
            }
        }
        for lease in node.leases.table.leases.clone() {
            let tree = node.boards.get(&lease.board).map(|host| host.names.tree(&lease.name)).unwrap_or_default();
            node.leases.holders.note(&lease, &tree, node.leases.now())?;
        }
        node.enrol_self();
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
            let mut board = Board::open_bounded(&dir.join("log"), id, &self.pool, self.key.clone(), self.log_bounds)?;
            let names = Registry::open(&dir.join("names.json"))?;
            let quarantine = Quarantine::open(&dir.join("quarantine.json"))?;
            let subs = Subs::open(&dir.join("subs.json"))?;
            let mut tally = Tally::default();
            tally.catch_up(&mut board, &names)?;
            names.all().keys().for_each(|name| board.group(name, &names.tree(name)));
            let made_by = match std::fs::read(dir.join(MADE_BY)) {
                Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| Error::Damaged(format!("{MADE_BY} unreadable: {e}")))?,
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
                Err(e) => return Err(e.into()),
            };
            self.boards.insert(id.into(), Hosted { board, names, quarantine, subs, book: Book::default(), tally, made_by });
            self.apply_log_cuts(id)?;
        }
        Ok(self.boards.get_mut(id).expect("hosted above"))
    }

    fn hosted(&mut self, id: &str) -> Result<&mut Hosted> {
        self.boards.get_mut(id).ok_or_else(|| Error::Denied("this node does not host that board".into()))
    }

    /// Decide what ``token`` may do on ``board``: its own board fully, another only through a link.
    pub fn access(&self, token: &str, board: &str, write: bool) -> Result<Access> {
        let holder = self.tokens.resolve(token).cloned().ok_or_else(|| Error::Denied(UNKNOWN_TOKEN.into()))?;
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

    /// How many boards the process tree ``lineage`` made by registering a session on them.
    fn boards_made_by(&self, lineage: &[Anchor]) -> usize {
        self.boards.values().filter(|h| h.made_by.as_deref().is_some_and(|by| shares(by, lineage))).count()
    }

    /// Room for one more board made by the process tree ``lineage``: boards that hold only the ends of their sessions are removed first
    /// when the node or the tree is at its bound.
    fn make_room_for_a_board(&mut self, lineage: &[Anchor]) -> Result<()> {
        if self.boards.len() >= MAX_BOARDS || self.boards_made_by(lineage) >= MOST_BOARDS_PER_LINEAGE {
            self.reap_idle_boards()?;
        }
        if self.boards.len() >= MAX_BOARDS {
            return Err(crate::limits::refuse("this node hosts as many boards as it may", MAX_BOARDS, "MAX_BOARDS", "node.rs"));
        }
        if self.boards_made_by(lineage) >= MOST_BOARDS_PER_LINEAGE {
            return Err(crate::limits::refuse("the sessions of this process tree have made as many boards as they may", MOST_BOARDS_PER_LINEAGE, "MOST_BOARDS_PER_LINEAGE", "node.rs"));
        }
        Ok(())
    }

    /// Room on ``board`` for one more live main session.
    fn make_room_for_a_main_session(&self, board: &str) -> Result<()> {
        let live = self.boards.get(board).map_or(0, |h| h.names.all().values().filter(|i| i.parent.is_empty() && !i.retired).count());
        if live >= self.writes.live_main_sessions {
            return Err(crate::limits::refuse("this board has as many live main sessions as it may: end one first", self.writes.live_main_sessions, "live_main_sessions", "write-limits.json"));
        }
        Ok(())
    }

    /// How many leases, held or queued, the sessions of the process tree of ``name`` of ``board`` keep, on every board.
    pub fn tree_leases(&self, board: &str, name: &str) -> usize {
        let Some(hosted) = self.boards.get(board) else { return 0 };
        let tree = hosted.names.tree(name);
        self.leases.table.leases.iter().filter(|l| self.boards.get(&l.board).is_some_and(|b| b.names.tree(&l.name) == tree)).count()
    }

    /// Note that the process tree ``lineage`` made ``board`` by registering a session on it.
    fn record_maker(&mut self, board: &str, lineage: &[Anchor]) -> Result<()> {
        self.host(board)?.made_by = Some(lineage.to_vec());
        write_atomic(&self.dir.join("boards").join(board).join(MADE_BY), &serde_json::to_vec(lineage)?)
    }

    /// Register a session on ``board`` (creating the board on first use) from the process ``peer``; a token given makes
    /// the new session a subagent of the token's owner. A main session that is already registered gets a token only from
    /// the lineage it was registered from. A node hosts a bounded number of boards, and one process tree makes a bounded number
    /// of them. The identity entry of a new or returning session is written for it, to its share of the log, and a registration
    /// the log refuses leaves the registry as it was. Returns name, token, whether new. The harness of the person's own session is
    /// not registered here (`register_person`).
    pub fn register(&mut self, board: &str, parent_token: Option<&str>, native: Native, peer: &Peer) -> Result<Value> {
        if identity::is_person_harness(&identity::clean(native.harness)?) {
            return Err(Error::Denied(format!("the harness {} is the person's own session, which only the person's own call registers", identity::PERSON_HARNESS)));
        }
        self.enter(board, parent_token, native, peer, false)
    }

    /// Register the person's own session of the native id ``session`` on ``board``: a main session of the harness and model the person's
    /// UI uses, which the node marks as the person's. A token given must be this session's own; a session of any other is no parent for it.
    pub fn register_person(&mut self, board: &str, own_token: Option<&str>, session: &str, peer: &Peer) -> Result<Value> {
        let native = Native { model: identity::PERSON_MODEL, harness: identity::PERSON_HARNESS, session };
        self.enter(board, own_token, native, peer, true)
    }

    fn enter(&mut self, board: &str, parent_token: Option<&str>, native: Native, peer: &Peer, person: bool) -> Result<Value> {
        let Native { model, harness, session } = native;
        let (model, harness) = (identity::clean_model(model)?, identity::clean(harness)?);
        if !valid_name(board) {
            return Err(Error::Invalid("a board id is a short lower-case word".into()));
        }
        if session.is_empty() || harness.is_empty() {
            return Err(Error::Denied("a session name needs the native harness and session id".into()));
        }
        let lineage = peer.lineage();
        let fresh = !self.boards.contains_key(board);
        if fresh {
            self.make_room_for_a_board(&lineage)?;
        }
        let (parent, proven, bound) = self.parent_of(board, parent_token, (&harness, session), &lineage)?;
        if person && !parent.is_empty() {
            return Err(Error::Denied("the person's session has no parent: it is registered with no token, or with its own".into()));
        }
        if parent.is_empty() {
            crate::idle::sweep(self, board)?;
        }
        if fresh {
            self.record_maker(board, &lineage)?;
        }
        let before = self.boards.get(board).and_then(|h| h.names.find(&harness, session).and_then(|n| h.names.get(n).map(|i| (n.to_string(), i.clone()))));
        let revived = before.as_ref().is_some_and(|(_, i)| i.retired);
        if parent.is_empty() && (before.is_none() || revived) {
            self.make_room_for_a_main_session(board)?;
        }
        self.make_room_for_a_token()?;
        let inherited = model.trim().is_empty().then(|| self.boards.get(board).and_then(|h| h.names.get(&parent)).map(|p| p.model.clone())).flatten().unwrap_or_default();
        let (model, state) = if inherited.is_empty() { (model, ModelState::Claimed) } else { (inherited, ModelState::Inherited) };
        let hosted = self.host(board)?;
        let now = hosted.board.now_ms();
        let (name, created) = hosted.names.assign(Who { harness: &harness, session, parent: &parent }, &model, state, now, &lineage, proven)?;
        hosted.board.group(&name, &hosted.names.tree(&name));
        if person {
            hosted.names.mark_person(&name)?;
        }
        if created || revived {
            if let Err(refused) = self.write_identity(board, &name) {
                self.host(board)?.names.restore(&name, before.map(|(_, i)| i))?;
                return Err(refused);
            }
        }
        let origin = self.host(board)?.board.origin().to_string();
        let token = self.tokens.issue(board, &name, &bound)?;
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
        crate::writes::check_post(self, board, &access.actor, kind, fields)?;
        let row = self.writer(board, &access.actor)?.append(kind, &access.actor, body, idem)?;
        Ok(json!({"id": row.id(), "seq": row.seq, "sender": access.actor, "status": self.hosted(board)?.board.status(row.seq, &[])}))
    }

    /// The log of ``board`` to write a row of ``actor`` to. An actor from another board (`name@home`) is put in the group of its process
    /// tree on `home` first, so what it writes counts toward that tree's share of this log.
    pub fn writer(&mut self, board: &str, actor: &str) -> Result<&mut Board> {
        let tree = actor.split_once('@').and_then(|(name, home)| self.boards.get(home).map(|h| h.names.tree(name)));
        let hosted = self.hosted(board)?;
        if let Some(tree) = tree {
            hosted.board.group(actor, &tree);
        }
        Ok(&mut hosted.board)
    }

    /// Put every session of ``board`` in the group of its process tree, so what each writes counts toward its tree's share of the log.
    pub fn regroup(&mut self, board: &str) -> Result<()> {
        let Hosted { board, names, .. } = self.hosted(board)?;
        names.all().keys().for_each(|name| board.group(name, &names.tree(name)));
        Ok(())
    }

    /// Write the current state of the session ``name`` to its board as an identity entry (registered, or its model changed): held to the
    /// share of the log of the session and of its process tree.
    pub fn write_identity(&mut self, board: &str, name: &str) -> Result<()> {
        let hosted = self.hosted(board)?;
        let ident = hosted.names.get(name).cloned().ok_or_else(|| Error::Denied("no such session".into()))?;
        hosted.board.append(Kind::Identity, name, identity_body(name, &ident)?, "")?;
        Ok(())
    }

    /// Write the state of the session ``name`` that just ended to its board as an identity entry the node writes for itself, carrying who
    /// ended it (``by``) and what that released (``detail``): held to the log less its last stretch, and not to a share, since the
    /// session's registration was.
    pub fn write_ended(&mut self, board: &str, name: &str, (by, detail): (&str, &str)) -> Result<()> {
        let hosted = self.hosted(board)?;
        let ident = hosted.names.get(name).cloned().ok_or_else(|| Error::Denied("no such session".into()))?;
        let mut body = identity_body(name, &ident)?;
        body["retired_by"] = json!(by);
        body["detail"] = json!(detail.chars().filter(|c| !c.is_control()).take(300).collect::<String>());
        hosted.board.append_node(Kind::Identity, name, body, "")?;
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
