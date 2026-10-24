//! The node: one per device. It holds the boards this device takes part in as signed
//! append-only logs, assigns every session one unique name per board, stamps every write from
//! the session's token, and answers a local API on a Unix socket, or on a Windows named pipe limited to the current user.

pub mod api;
pub mod approvals;
pub mod beacon;
pub mod board;
pub mod build;
pub mod cert;
pub mod claims;
pub mod contract;
#[allow(dead_code)] // Sealed interface only; no native root/provider admission implementation.
mod contract_authority_admission;
#[allow(dead_code)] // Reference mathematics only; no admitted descriptor or production issuer.
mod contract_transcripts;
#[cfg(test)]
mod contract_private_tests;
pub mod contract_lifecycle;
pub mod contract_operations;
pub mod client;
pub mod device;
pub mod error;
pub mod events;
pub mod facts;
pub mod files;
pub mod fold;
pub mod fsutil;
pub mod gov;
pub mod grants;
pub mod identity;
pub mod idle;
pub mod landing;
pub mod landtrust;
pub mod lease;
pub mod limits;
pub mod lineage;
#[allow(dead_code)] // Dormant until independently reviewed native provenance can issue sealed proofs.
mod lifecycle;
pub mod links;
pub mod log;
pub mod logbounds;
pub mod mail;
pub mod membership;
pub mod net;
pub mod netif;
pub mod netsync;
pub mod node;
pub mod notes;
pub mod notice;
pub mod join_code;
pub mod peer;
pub mod phone;
pub mod poolapi;
pub mod poolops;
pub mod presence;
pub mod probe;
pub mod project;
pub mod projectid;
pub mod quarantine;
pub mod reach;
pub mod reap;
pub mod registry;
pub mod row;
pub mod rebind;
pub mod rooms;
pub mod rules;
pub mod scratch;
pub mod sessions;
pub mod server;
pub mod shard;
pub mod state;
pub mod subs;
pub mod sync;
pub mod tasks;
pub mod sys;
pub mod tally;
pub mod tls;
pub mod wire;
pub mod writes;

#[allow(dead_code)] // Dormant model; no authenticated control or writer-registry witness issuer.
mod update_handoff;
