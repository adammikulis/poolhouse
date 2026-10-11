//! Who governs a pool: a charter that names the admin keys, and signed acts that change the pool.
//!
//! A pool is its charter (`charter`): the admin keys, each with its own acceptance, the thresholds that count distinct people,
//! the holds, and the join policy. The pool id is the hash of the first charter, so a copy with other admins is another pool.
//! Every change to the pool (a member admitted or removed, an admin added, a threshold moved) is an act (`act`) signed by admin
//! keys, or by a device where the table says so; each member checks every act against the keys it already holds, and a member
//! that relays an act proves nothing about it. The member record is a view folded from the acts (`fold`, `roster`), never an input.
//!
//! | piece | does |
//! |---|---|
//! | `keys` | an admin key, its role and strength, its acceptance |
//! | `charter` | the charter, the pool id, thresholds and holds |
//! | `act`, `body` | the signed act and the body of each kind |
//! | `clock` | the awake time every hold is measured in |
//! | `chain` | the admin-set acts one after the other: holds, cancels, vetoes, forks, disputes |
//! | `roster` | the members, the removals and the delegates, with the rate caps |
//! | `pool` | one pool on disk: acceptance of acts, the fold, paging |
//! | `signer` | who signs for a person (a rig key until the phone approval exists) |
//! | `wire` | the `acts` member op |

pub mod act;
pub mod body;
pub mod chain;
pub mod charter;
pub mod clock;
pub mod keys;
pub mod pool;
pub mod roster;
pub mod signer;

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
pub const MAX_ACTS: usize = 8192;
pub const MAX_REFUSALS: usize = 256;
/// The most keys a charter names.
pub const MAX_ADMINS: usize = 16;
/// The delegate: how long it lasts, and how many admits it may sign an hour and a day; an open pool's member, an hour.
pub const DELEGATE_DAYS: i64 = 30;
pub const DELEGATE_PER_HOUR: usize = 3;
pub const DELEGATE_PER_DAY: usize = 10;
pub const OPEN_PER_HOUR: usize = 5;
pub const HOUR_MS: i64 = 3_600_000;
pub const DAY_MS: i64 = 24 * HOUR_MS;
/// The first-sight time of an act a device took inside a join it made: its holds were over before this device was there.
pub const SETTLED: i64 = i64::MIN / 4;
