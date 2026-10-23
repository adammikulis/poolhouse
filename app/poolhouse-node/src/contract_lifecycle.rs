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

/// Opaque runtime handles are never wire DTOs or proof supplied by a caller.
pub trait NativeCallerPlatform<Binding, SpawnPlan> {
    type VerifiedInstallation;
    type RawChannel;
    type AdmittedProducer;
    type VerifiedBootstrap;
    type HeldProcess;
    type RunningProcess;
    type KernelCaller;
    fn verify_installation(&mut self) -> Result<Self::VerifiedInstallation, NativeAdapterError>;
    fn verify_bootstrap(&mut self, installation: &Self::VerifiedInstallation) -> Result<Self::VerifiedBootstrap, NativeAdapterError>;
    fn authenticate_producer(&mut self, channel: Self::RawChannel, installation: &Self::VerifiedInstallation) -> Result<Self::AdmittedProducer, NativeAdapterError>;
    fn hold_spawn(&mut self, producer: &Self::AdmittedProducer, bootstrap: &Self::VerifiedBootstrap, plan: &SpawnPlan) -> Result<Self::HeldProcess, NativeAdapterError>;
    fn observe_held(&mut self, held: &Self::HeldProcess) -> Result<ProcessObservation, NativeAdapterError>;
    fn release(&mut self, held: Self::HeldProcess, binding: &Binding) -> Result<Self::RunningProcess, NativeAdapterError>;
    fn inspect_caller(&mut self, channel: Self::RawChannel, installation: &Self::VerifiedInstallation) -> Result<Self::KernelCaller, NativeAdapterError>;
    fn caller_observation(&mut self, caller: &Self::KernelCaller) -> Result<ProcessObservation, NativeAdapterError>;
    fn is_live(&mut self, running: &Self::RunningProcess) -> Result<bool, NativeAdapterError>;
    fn cancel_hold(&mut self, held: Self::HeldProcess) -> Result<(), NativeAdapterError>;
    fn terminate(&mut self, running: Self::RunningProcess) -> Result<(), NativeAdapterError>;
}
