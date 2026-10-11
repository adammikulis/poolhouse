//! Who governs a pool: a charter that names the admin keys, and signed acts that change the pool.
//!
//! A pool is its charter (`charter`): the admin keys, each with its own acceptance, the thresholds that count distinct people,
//! the holds, and the join policy. The pool id is the hash of the first charter, so a copy with other admins is another pool.
//! Every change to the pool (a member admitted or removed, an admin added, a threshold moved) is an act (`act`) signed by admin
//! keys, or by a device where the table says so; each member checks every act against the keys it already holds, and a member
//! that relays an act proves nothing about it. The member record is a view folded from the acts (`chain`, `roster`), never an input.
//!
//! | piece | does |
//! |---|---|
//! | `alg` | the algorithm of an admin key: Ed25519 or P-256 ECDSA |
//! | `keys` | an admin key, its algorithm, role and strength, its acceptance |
//! | `charter` | the charter, the pool id, thresholds and holds |
//! | `act`, `body` | the signed act and the body of each kind |
//! | `describe` | the sentence a person is asked to sign an act under |
//! | `clock` | the awake time every hold is measured in |
//! | `chain` | the admin-set acts one after the other: holds, cancels, vetoes, forks, disputes |
//! | `logs` | what a removal does to the logs of the removed device on boards |
//! | `roster` | the members, the removals and the delegates, with the rate caps |
//! | `pool` | one pool on disk: acceptance of acts, the fold, paging |
//! | `room` | how many acts of each kind a pool holds, and which go first |
//! | `signer` | who signs for a person (a rig key until the phone approval exists) |
//! | `wire` | the `acts` member op |
//! | `ops` | the node's part: founding, admitting, removing, leaving, and the acts a person's key signs |

pub mod act;
pub mod alg;
pub mod body;
pub mod chain;
pub mod charter;
pub mod clock;
pub mod describe;
pub mod keys;
pub mod logs;
pub mod ops;
pub mod pool;
pub mod roster;
pub mod room;
pub mod signer;
pub mod wire;

/// The largest act, the most signatures on one, and how many an `acts` page holds and weighs.
pub const MAX_ACT_BYTES: usize = 16 * 1024;
pub const MAX_SIGNATURES: usize = 8;
pub const PAGE_ACTS: usize = 256;
pub const PAGE_BYTES: usize = 2 * 1024 * 1024;
/// How many ids of the acts it holds a device names in an `acts` request, and how many pages one exchange reads.
pub const HAVE_ACTS: usize = 4096;
pub const MAX_PAGES: usize = 64;
/// What a pool keeps: live admits, admin-set acts, every act, and refusals recorded.
pub const MAX_ADMITS: usize = 4096;
pub const MAX_ADMIN_ACTS: usize = 256;
/// How many admin-set acts that have not taken effect the same keys may have signed at once.
pub const ADMIN_ACTS_PER_KEYS: usize = 16;
pub const MAX_ACTS: usize = 8192;
pub const MAX_REFUSALS: usize = 256;
/// The most keys a charter names.
pub const MAX_ADMINS: usize = 16;
/// The delegate: how long it lasts, and how many admits it may sign an hour and a day; an open pool's member, an hour.
pub const DELEGATE_DAYS: i64 = 30;
pub const DELEGATE_PER_HOUR: usize = 3;
pub const DELEGATE_PER_DAY: usize = 10;
pub const OPEN_PER_HOUR: usize = 5;
/// How many admits all of an open pool's members together may sign in an hour: one member's five an hour, and the members the pool
/// has taken in each with five of their own, would otherwise grow without end.
pub const OPEN_POOL_PER_HOUR: usize = 10;
pub const HOUR_MS: i64 = 3_600_000;
pub const DAY_MS: i64 = 24 * HOUR_MS;
/// Whether a pool holding ``live`` admits has room for one more that ``admin_signed`` says an admin signed: an admin's admit is a
/// person's decision and is always taken, so a flood of the devices' own admits can never keep a person from admitting a device.
pub fn admit_fits(admin_signed: bool, live: usize) -> bool {
    admin_signed || live < MAX_ADMITS
}
/// The first-sight time of an act a device took inside a join it made: its holds were over before this device was there.
pub const SETTLED: i64 = i64::MIN / 4;
