// Generated contract metadata. Transitions do not authorize operations.
use super::contract::ProcessObservation;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Transition { pub domain: &'static str, pub from: &'static str, pub event: &'static str, pub to: &'static str, pub checks: &'static [&'static str] }

pub const TRANSITIONS: &[Transition] = &[
Transition { domain: "session", from: "opening", event: "admit", to: "live", checks: &["authenticated-producer", "actual-native-birth", "validated-root-and-direct-parent", "current-generations"] },
Transition { domain: "session", from: "opening", event: "retire", to: "retired", checks: &["invalidate-outstanding-descendants-and-invocations", "close-channels-before-reaping", "new-generation-to-reopen"] },
Transition { domain: "session", from: "live", event: "retire", to: "retired", checks: &["invalidate-outstanding-descendants-and-invocations", "close-channels-before-reaping", "new-generation-to-reopen"] },
Transition { domain: "invocation", from: "reserved", event: "bind", to: "bound", checks: &["actual-held-process", "live-exact-process-handle", "reservation-owner", "current-generations", "no-prior-binding"] },
Transition { domain: "invocation", from: "bound", event: "release", to: "released", checks: &["live-session", "current-generations", "unchanged-context-purpose-and-digest", "exact-live-bound-process", "single-use-hold"] },
Transition { domain: "invocation", from: "released", event: "consume", to: "consumed", checks: &["live-session", "current-generations", "exact-live-bound-caller", "matching-event-purpose-and-digest", "required-cleanup-evidence", "atomic-single-consumer"] },
Transition { domain: "invocation", from: "reserved", event: "cancel", to: "cancelled", checks: &["authorized-caller", "atomic-terminal-race", "close-capabilities-before-termination"] },
Transition { domain: "invocation", from: "bound", event: "cancel", to: "cancelled", checks: &["authorized-caller", "atomic-terminal-race", "close-capabilities-before-termination"] },
Transition { domain: "invocation", from: "released", event: "cancel", to: "cancelled", checks: &["authorized-caller", "atomic-terminal-race", "close-capabilities-before-termination"] },
Transition { domain: "invocation", from: "reserved", event: "invalidate", to: "invalidated", checks: &["bind-failure-or-process-exit-or-lost-producer-or-lost-holder-or-restart", "no-process-reassignment"] },
Transition { domain: "invocation", from: "bound", event: "invalidate", to: "invalidated", checks: &["bind-failure-or-process-exit-or-lost-producer-or-lost-holder-or-restart", "no-process-reassignment"] },
Transition { domain: "invocation", from: "released", event: "invalidate", to: "invalidated", checks: &["bind-failure-or-process-exit-or-lost-producer-or-lost-holder-or-restart", "no-process-reassignment"] },
];
#[derive(Debug)]
pub enum NativeAdapterError { InvalidInstallation, MissingChannel, WrongPrincipal, StaleProcess, UnprotectedProcess, ConfinementUnavailable, BindingMismatch, Cancelled, Io }

// Incomplete declarations: no platform implementation, issuer, projection graph or bridge.
pub(crate) mod native_seal {
    pub(crate) trait Platform {}
    pub(crate) trait Binding {}
    pub(crate) trait SpawnPlan {}
}
pub(crate) trait NodeBinding: native_seal::Binding {}
pub(crate) trait NodeSpawnPlan: native_seal::SpawnPlan {
    type Binding: NodeBinding;
    fn binding(&self) -> &Self::Binding;
}
/// Runtime handles and diagnostics cannot be converted into core proofs.
pub(crate) trait NativeCallerPlatform<Binding: NodeBinding, SpawnPlan: NodeSpawnPlan<Binding = Binding>>: native_seal::Platform {
    type VerifiedInstallation;
    type RawChannel;
    type VerifiedProducerLaunch;
    type AdmittedProducer;
    type AuthenticatedProducerMessage;
    type VerifiedBootstrap;
    type HeldProcess;
    type RunningProcess;
    type AuthenticatedCaller;
    type BeforeConsumeObligation;
    type RevokedAuthorization;
    type TerminalCleanupObligation;
    type HolderCleanup;
    type OwnedChannelFailure;
    type OwnedSpawnFailure;
    type OwnedReleaseFailure;
    type OwnedCleanupFailure;
    fn verify_installation(&mut self) -> Result<Self::VerifiedInstallation, NativeAdapterError>;
    fn verify_bootstrap(&mut self, installation: &Self::VerifiedInstallation) -> Result<Self::VerifiedBootstrap, NativeAdapterError>;
    fn authenticate_producer(&mut self, channel: Self::RawChannel, installation: &Self::VerifiedInstallation, launch: &Self::VerifiedProducerLaunch) -> Result<Self::AdmittedProducer, Self::OwnedChannelFailure>;
    fn receive_producer_message(&mut self, producer: &Self::AdmittedProducer) -> Result<Self::AuthenticatedProducerMessage, Self::OwnedChannelFailure>;
    fn hold_spawn(&mut self, producer: &Self::AdmittedProducer, bootstrap: &Self::VerifiedBootstrap, binding: &Binding, plan: &SpawnPlan) -> Result<Self::HeldProcess, Self::OwnedSpawnFailure>;
    fn observe_held(&mut self, held: &Self::HeldProcess) -> Result<ProcessObservation, NativeAdapterError>;
    fn release(&mut self, held: Self::HeldProcess, binding: &Binding) -> Result<Self::RunningProcess, Self::OwnedReleaseFailure>;
    fn inspect_caller(&mut self, channel: Self::RawChannel, installation: &Self::VerifiedInstallation, running: &Self::RunningProcess, binding: &Binding) -> Result<Self::AuthenticatedCaller, Self::OwnedChannelFailure>;
    fn caller_observation(&mut self, caller: &Self::AuthenticatedCaller) -> Result<ProcessObservation, NativeAdapterError>;
    fn is_live(&mut self, running: &Self::RunningProcess) -> Result<bool, NativeAdapterError>;
    fn cleanup_before_consume(&mut self, running: &Self::RunningProcess, obligation: Self::BeforeConsumeObligation) -> Result<Self::HolderCleanup, Self::OwnedCleanupFailure>;
    fn cancel_hold(&mut self, held: Self::HeldProcess, revoked: Self::RevokedAuthorization, obligation: Self::TerminalCleanupObligation) -> Result<Self::HolderCleanup, Self::OwnedCleanupFailure>;
    fn terminate(&mut self, running: Self::RunningProcess, revoked: Self::RevokedAuthorization, obligation: Self::TerminalCleanupObligation) -> Result<Self::HolderCleanup, Self::OwnedCleanupFailure>;
}
