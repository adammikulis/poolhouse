// Generated contract metadata. Transitions do not authorize operations.
use super::contract::ProcessObservation;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Transition { pub domain: &'static str, pub from: &'static str, pub event: &'static str, pub to: &'static str, pub checks: &'static [&'static str] }

pub const TRANSITIONS: &[Transition] = &[
Transition { domain: "session", from: "opening", event: "admit", to: "live", checks: &["authenticated-producer", "authenticated-native-birth-or-same-producer-resume", "stable-original-occurrence-identity", "producer-stream-global-event-deduplication", "current-producer-boot-and-admission", "validated-root-and-direct-parent", "current-generations", "source-occurrence-namespace-independent-of-node-boot-and-admission", "native-source-view-has-no-node-issued-session-keys", "durable-source-disposition-and-custody-fence-before-right", "missing-or-rolled-back-occurrence-ledger-refuses-prior-source"] },
Transition { domain: "session", from: "opening", event: "retire", to: "retired", checks: &["invalidate-outstanding-descendants-and-invocations", "atomically-revoke-authorization-before-returning-physical-cleanup-obligations", "new-generation-to-reopen", "unresolved-cleanup-retains-original-holder-custody"] },
Transition { domain: "session", from: "live", event: "retire", to: "retired", checks: &["invalidate-outstanding-descendants-and-invocations", "atomically-revoke-authorization-before-returning-physical-cleanup-obligations", "new-generation-to-reopen", "unresolved-cleanup-retains-original-holder-custody"] },
Transition { domain: "invocation", from: "reserved", event: "bind", to: "bound", checks: &["actual-held-process", "live-exact-process-handle", "reservation-owner", "current-generations", "no-prior-binding", "durable-custody-fence-before-spawn"] },
Transition { domain: "invocation", from: "bound", event: "release", to: "released", checks: &["live-session", "current-generations", "unchanged-context-purpose-and-digest", "exact-live-bound-process", "single-use-hold", "durable-custody-fence-before-release"] },
Transition { domain: "invocation", from: "released", event: "consume", to: "consumed", checks: &["live-session", "current-generations", "exact-live-bound-caller", "matching-event-purpose-and-digest", "required-cleanup-evidence", "atomic-single-consumer", "one-effect-permit-pending-on-loss-or-crash-no-replacement", "durable-consumption-fence-before-effect", "unique-phase-obligation-and-full-resource-accounting", "separate-sealed-domain-effect-owner", "unknown-effect-retains-permit-operation-and-owner"] },
Transition { domain: "invocation", from: "reserved", event: "cancel", to: "cancelled", checks: &["authorized-caller", "atomic-terminal-race", "atomically-revoke-authorization-before-returning-physical-cleanup-obligations", "unresolved-cleanup-retains-original-holder-custody"] },
Transition { domain: "invocation", from: "bound", event: "cancel", to: "cancelled", checks: &["authorized-caller", "atomic-terminal-race", "atomically-revoke-authorization-before-returning-physical-cleanup-obligations", "unresolved-cleanup-retains-original-holder-custody"] },
Transition { domain: "invocation", from: "released", event: "cancel", to: "cancelled", checks: &["authorized-caller", "atomic-terminal-race", "atomically-revoke-authorization-before-returning-physical-cleanup-obligations", "unresolved-cleanup-retains-original-holder-custody"] },
Transition { domain: "invocation", from: "reserved", event: "invalidate", to: "invalidated", checks: &["bind-failure-or-process-exit-or-lost-producer-or-lost-holder-or-restart", "no-process-reassignment"] },
Transition { domain: "invocation", from: "bound", event: "invalidate", to: "invalidated", checks: &["bind-failure-or-process-exit-or-lost-producer-or-lost-holder-or-restart", "no-process-reassignment"] },
Transition { domain: "invocation", from: "released", event: "invalidate", to: "invalidated", checks: &["bind-failure-or-process-exit-or-lost-producer-or-lost-holder-or-restart", "no-process-reassignment"] },
];
#[derive(Debug)]
pub enum NativeAdapterError { InvalidInstallation, MissingChannel, WrongPrincipal, StaleProcess, UnprotectedProcess, ConfinementUnavailable, BindingMismatch, Cancelled, Io }

// Dormant private declarations: no issuer, bridge, platform implementation or durable ledger.
mod native_projection {
use std::sync::Arc;
use super::{NativeAdapterError, ProcessObservation};
use super::super::contract::{AuditToken, InstallationId, ResourceGeneration, WindowsSid};
mod native_seal {
pub trait BeforeConsume {}
pub trait Binding {}
pub trait Bootstrap {}
pub trait Caller {}
pub trait CallerMessage {}
pub trait Channel {}
pub trait ChannelFailure {}
pub trait Child {}
pub trait Cleanup {}
pub trait CleanupFailure {}
pub trait Graph {}
pub trait Held {}
pub trait Holder {}
pub trait Installation {}
pub trait InstallationGraph {}
pub trait Launch {}
pub trait LaunchPlan {}
pub trait MeasuredAsset {}
pub trait Plan {}
pub trait Platform {}
pub trait Process {}
pub trait Producer {}
pub trait ProducerMessage {}
pub trait RawChannel {}
pub trait ReleaseFailure {}
pub trait Revoked {}
pub trait Running {}
pub trait SpawnFailure {}
pub trait Terminal {}
}
mod effect_seal {
pub trait Completion {}
pub trait Graph {}
pub trait Identity {}
pub trait Operation {}
pub trait Owner {}
pub trait Permit {}
pub trait Unknown {}
}
#[derive(Debug)] pub(crate) struct Digest([u8; 32]);
impl Digest { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct ProducerLaunchEpoch([u8; 32]);
impl ProducerLaunchEpoch { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct RegistryBootEpoch([u8; 32]);
impl RegistryBootEpoch { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct KernelBootIdentity([u8; 32]);
impl KernelBootIdentity { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct ReaderEpoch([u8; 32]);
impl ReaderEpoch { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct ObligationId([u8; 32]);
impl ObligationId { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct CompletionRecordId([u8; 32]);
impl CompletionRecordId { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct HolderIdentity([u8; 32]);
impl HolderIdentity { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct ChannelIdentity([u8; 32]);
impl ChannelIdentity { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct NodeEffectOwnerKey([u8; 32]);
impl NodeEffectOwnerKey { pub(crate) fn bytes(&self) -> &[u8; 32] { &self.0 } }
#[derive(Debug)] pub(crate) struct ProducerAdmissionKey { boot: RegistryBootEpoch, generation: u64 }
impl ProducerAdmissionKey {
pub(crate) fn boot(&self) -> &RegistryBootEpoch { &self.boot }
pub(crate) fn generation(&self) -> u64 { self.generation }
}
#[derive(Debug)] pub(crate) struct NodeSessionKey { boot: RegistryBootEpoch, generation: u64 }
impl NodeSessionKey {
pub(crate) fn boot(&self) -> &RegistryBootEpoch { &self.boot }
pub(crate) fn generation(&self) -> u64 { self.generation }
}
#[derive(Debug)] pub(crate) struct NodeInvocationKey { boot: RegistryBootEpoch, generation: u64 }
impl NodeInvocationKey {
pub(crate) fn boot(&self) -> &RegistryBootEpoch { &self.boot }
pub(crate) fn generation(&self) -> u64 { self.generation }
}
#[derive(Debug)] pub(crate) struct NodeRevocationKey { boot: RegistryBootEpoch, generation: u64 }
impl NodeRevocationKey {
pub(crate) fn boot(&self) -> &RegistryBootEpoch { &self.boot }
pub(crate) fn generation(&self) -> u64 { self.generation }
}
#[derive(Debug)] pub(crate) struct EffectPermitId { boot: RegistryBootEpoch, generation: u64 }
impl EffectPermitId {
pub(crate) fn boot(&self) -> &RegistryBootEpoch { &self.boot }
pub(crate) fn generation(&self) -> u64 { self.generation }
}
#[derive(Debug)] pub(crate) struct NativeActorId(Vec<u8>);
impl NativeActorId { pub(crate) fn bytes(&self) -> &[u8] { &self.0 } }
#[derive(Debug)] pub(crate) struct NativeEventKind(Vec<u8>);
impl NativeEventKind { pub(crate) fn bytes(&self) -> &[u8] { &self.0 } }
#[derive(Debug)] pub(crate) struct OperationPurpose(Vec<u8>);
impl OperationPurpose { pub(crate) fn bytes(&self) -> &[u8] { &self.0 } }
#[derive(Debug)] pub(crate) struct ImmutableArgument(Vec<u8>);
impl ImmutableArgument { pub(crate) fn bytes(&self) -> &[u8] { &self.0 } }
#[derive(Debug)] pub(crate) struct InstallationIdentity { id: InstallationId, generation: ResourceGeneration }
impl InstallationIdentity {
pub(crate) fn id(&self) -> &InstallationId { &self.id }
pub(crate) fn generation(&self) -> &ResourceGeneration { &self.generation }
}
#[derive(Debug)] pub(crate) enum KernelProcessIdentity {
Linux { boot: KernelBootIdentity, pid: u32, start_ticks: u64, uid: u32 },
Windows { boot: KernelBootIdentity, pid: u32, creation_time: u64, sid: WindowsSid },
Mac { boot: KernelBootIdentity, pid: u32, pid_version: u32, audit: AuditToken },
}
#[derive(Debug)] pub(crate) enum ConfiguredServicePrincipal {
UnixUid(u32),
WindowsSid(WindowsSid),
}
#[derive(Debug)] pub(crate) enum ConfiguredOwnerPrincipal {
UnixUid(u32),
WindowsSid(WindowsSid),
}
#[derive(Debug)] pub(crate) struct NativeOccurrence { original_launch: ProducerLaunchEpoch, id: [u8; 32] }
impl NativeOccurrence {
pub(crate) fn original_launch(&self) -> &ProducerLaunchEpoch { &self.original_launch }
pub(crate) fn id(&self) -> &[u8; 32] { &self.id }
}
#[derive(Debug)] pub(crate) struct NativeResumeContinuity { prior_admission_occurrence: NativeOccurrence, actor: NativeActorId, root: NativeActorId, parent: Option<NativeActorId>, context_digest: Digest }
impl NativeResumeContinuity {
pub(crate) fn prior_admission_occurrence(&self) -> &NativeOccurrence { &self.prior_admission_occurrence }
pub(crate) fn actor(&self) -> &NativeActorId { &self.actor }
pub(crate) fn root(&self) -> &NativeActorId { &self.root }
pub(crate) fn parent(&self) -> Option<&NativeActorId> { self.parent.as_ref() }
pub(crate) fn context_digest(&self) -> &Digest { &self.context_digest }
}
#[derive(Debug)] pub(crate) struct MeasuredAssetIdentity { installation: InstallationIdentity, manifest_entry: u32, digest: Digest }
impl MeasuredAssetIdentity {
pub(crate) fn installation(&self) -> &InstallationIdentity { &self.installation }
pub(crate) fn manifest_entry(&self) -> u32 { self.manifest_entry }
pub(crate) fn digest(&self) -> &Digest { &self.digest }
}
#[derive(Debug)] pub(crate) struct ImmutableEnvironmentEntry { key: Vec<u8>, value: Vec<u8> }
impl ImmutableEnvironmentEntry {
pub(crate) fn key(&self) -> &[u8] { &self.key }
pub(crate) fn value(&self) -> &[u8] { &self.value }
}
#[derive(Debug)] pub(crate) struct MeasuredDirectoryIdentity { installation: InstallationIdentity, manifest_entry: u32, measurement: Digest }
impl MeasuredDirectoryIdentity {
pub(crate) fn installation(&self) -> &InstallationIdentity { &self.installation }
pub(crate) fn manifest_entry(&self) -> u32 { self.manifest_entry }
pub(crate) fn measurement(&self) -> &Digest { &self.measurement }
}
#[derive(Debug)] pub(crate) enum ConfinementProfile {
WindowsAppContainer { manifest_digest: Digest },
LinuxBubblewrap { manifest_digest: Digest },
MacSeatbelt { manifest_digest: Digest },
}
#[derive(Debug)] pub(crate) struct InheritedResourceSpec { resource: ChannelIdentity, direction: ChannelDirection, allowed_operations: InheritedOperations }
impl InheritedResourceSpec {
pub(crate) fn resource(&self) -> &ChannelIdentity { &self.resource }
pub(crate) fn direction(&self) -> &ChannelDirection { &self.direction }
pub(crate) fn allowed_operations(&self) -> &InheritedOperations { &self.allowed_operations }
}
#[derive(Debug)] pub(crate) enum RecordedDomainOutcome {
Success { canonical_result: Vec<u8> },
Failure { canonical_error: Vec<u8> },
}
#[derive(Debug)] pub(crate) struct BoundOperationIdentity { invocation: NodeInvocationKey, purpose: OperationPurpose, payload_digest: Digest }
impl BoundOperationIdentity {
pub(crate) fn invocation(&self) -> &NodeInvocationKey { &self.invocation }
pub(crate) fn purpose(&self) -> &OperationPurpose { &self.purpose }
pub(crate) fn payload_digest(&self) -> &Digest { &self.payload_digest }
}
#[derive(Debug)] pub(crate) enum CleanupResultKind {
Completed,
Failed(NativeAdapterError),
Unresolved(NativeAdapterError),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)] pub(crate) enum ChannelDirection {
ReadOnly,
WriteOnly,
Duplex,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)] pub(crate) enum InheritedOperations {
Read,
Write,
ReadWrite,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)] pub(crate) enum HeldPhase {
Held,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)] pub(crate) enum RunningPhase {
Running,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)] pub(crate) enum CleanupPhase {
BeforeConsume,
Terminal,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)] pub(crate) enum RequiredAction {
CloseChannel,
TerminateAndReapProcess,
ReleaseMeasurement,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)] pub(crate) enum ProcessDisposition {
Held,
Running,
Unknown,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)] pub(crate) enum UnknownEffectReason {
BindingMismatchBeforeEffect,
RecordingLostAfterPossibleEffect,
OwnerLost,
Interrupted,
}
pub(crate) struct NativeBirthView<'a> { actor: &'a NativeActorId, context: &'a [u8], root: &'a NativeActorId, direct_parent: Option<&'a NativeActorId>, event_kind: &'a NativeEventKind }
impl<'a> NativeBirthView<'a> {
pub(crate) fn actor(&self) -> &'a NativeActorId { self.actor }
pub(crate) fn context(&self) -> &'a [u8] { self.context }
pub(crate) fn root(&self) -> &'a NativeActorId { self.root }
pub(crate) fn direct_parent(&self) -> Option<&'a NativeActorId> { self.direct_parent }
pub(crate) fn event_kind(&self) -> &'a NativeEventKind { self.event_kind }
}
pub(crate) struct NativeResumeView<'a> { actor: &'a NativeActorId, context: &'a [u8], root: &'a NativeActorId, direct_parent: Option<&'a NativeActorId>, event_kind: &'a NativeEventKind, continuity: &'a NativeResumeContinuity }
impl<'a> NativeResumeView<'a> {
pub(crate) fn actor(&self) -> &'a NativeActorId { self.actor }
pub(crate) fn context(&self) -> &'a [u8] { self.context }
pub(crate) fn root(&self) -> &'a NativeActorId { self.root }
pub(crate) fn direct_parent(&self) -> Option<&'a NativeActorId> { self.direct_parent }
pub(crate) fn event_kind(&self) -> &'a NativeEventKind { self.event_kind }
pub(crate) fn continuity(&self) -> &'a NativeResumeContinuity { self.continuity }
}
pub(crate) struct NativeInvocationView<'a> { actor: &'a NativeActorId, context: &'a [u8], root: &'a NativeActorId, direct_parent: Option<&'a NativeActorId>, event_kind: &'a NativeEventKind, purpose: &'a OperationPurpose, original_payload: &'a [u8] }
impl<'a> NativeInvocationView<'a> {
pub(crate) fn actor(&self) -> &'a NativeActorId { self.actor }
pub(crate) fn context(&self) -> &'a [u8] { self.context }
pub(crate) fn root(&self) -> &'a NativeActorId { self.root }
pub(crate) fn direct_parent(&self) -> Option<&'a NativeActorId> { self.direct_parent }
pub(crate) fn event_kind(&self) -> &'a NativeEventKind { self.event_kind }
pub(crate) fn purpose(&self) -> &'a OperationPurpose { self.purpose }
pub(crate) fn original_payload(&self) -> &'a [u8] { self.original_payload }
}
pub(crate) struct NativeContextView<'a> { actor: &'a NativeActorId, root: &'a NativeActorId, parent: Option<&'a NativeActorId>, bytes: &'a [u8] }
impl<'a> NativeContextView<'a> {
pub(crate) fn actor(&self) -> &'a NativeActorId { self.actor }
pub(crate) fn root(&self) -> &'a NativeActorId { self.root }
pub(crate) fn parent(&self) -> Option<&'a NativeActorId> { self.parent }
pub(crate) fn bytes(&self) -> &'a [u8] { self.bytes }
}
pub(crate) enum NativeMessageView<'a> {
Birth(NativeBirthView<'a>),
Resume(NativeResumeView<'a>),
Invocation(NativeInvocationView<'a>),
}
pub(crate) trait NativeInstallationGraph: native_seal::InstallationGraph + Sized {
type Installation: InstallationProjection<Self>;
type Process: ProcessProjection;
type Channel: ChannelProjection<Self>;
type RawChannel: RawChannelProjection<Self>;
type MeasuredAsset: MeasuredAssetProjection;
type Holder: HolderProjection<Self>;
}
pub(crate) trait NativeGraph: NativeInstallationGraph + native_seal::Graph + Sized {
type Launch: LaunchProjection<Self>;
type LaunchPlan: ProducerLaunchPlanProjection<Self>;
type Producer: ProducerProjection<Self>;
type Message: ProducerMessageProjection<Self>;
type Bootstrap: BootstrapProjection<Self>;
type Binding: BindingProjection<Self>;
type Plan: PlanProjection<Self>;
type ChildCustody: ChildProjection<Self>;
type Caller: CallerProjection<Self>;
type CallerMessage: CallerMessageProjection<Self>;
type BeforeConsume: BeforeConsumeProjection<Self>;
type Revoked: RevokedProjection<Self>;
type Terminal: TerminalProjection<Self>;
type Cleanup: CleanupProjection<Self>;
type ChannelFailure: ChannelFailureProjection<Self>;
type SpawnFailure: SpawnFailureProjection<Self>;
type ReleaseFailure: ReleaseFailureProjection<Self>;
type CleanupFailure: CleanupFailureProjection<Self>;
}
pub(crate) trait EffectGraph<G: NativeGraph>: effect_seal::Graph + Sized {
type Owner: EffectOwnerProjection<G>;
type Permit: PermitProjection<G, Self>;
type Operation: BoundOperationProjection<G, Self>;
type Completion: CompletionProjection<G, Self>;
type Unknown: UnknownEffectProjection<G, Self>;
}
pub(crate) trait ProcessProjection: native_seal::Process {
fn identity(&self) -> &KernelProcessIdentity;
fn observation(&self) -> &ProcessObservation;
}
pub(crate) trait InstallationProjection<G: NativeInstallationGraph>: native_seal::Installation {
fn identity(&self) -> &InstallationIdentity;
fn manifest_digest(&self) -> &Digest;
fn assets(&self) -> &[G::MeasuredAsset];
fn service_principal(&self) -> &ConfiguredServicePrincipal;
fn owner_principal(&self) -> &ConfiguredOwnerPrincipal;
}
pub(crate) trait MeasuredAssetProjection: native_seal::MeasuredAsset {
fn identity(&self) -> &MeasuredAssetIdentity;
fn digest(&self) -> &Digest;
}
pub(crate) trait ChannelProjection<G: NativeInstallationGraph>: native_seal::Channel {
fn identity(&self) -> &ChannelIdentity;
fn process(&self) -> &G::Process;
fn holder(&self) -> &G::Holder;
fn reader_epoch(&self) -> &ReaderEpoch;
fn direction(&self) -> ChannelDirection;
}
pub(crate) trait RawChannelProjection<G: NativeInstallationGraph>: native_seal::RawChannel {
fn identity(&self) -> &ChannelIdentity;
fn observed_process(&self) -> Option<&G::Process>;
fn holder(&self) -> &G::Holder;
fn direction(&self) -> ChannelDirection;
}
pub(crate) trait HolderProjection<G: NativeInstallationGraph>: native_seal::Holder {
fn identity(&self) -> &HolderIdentity;
fn process(&self) -> &G::Process;
}
pub(crate) trait LaunchProjection<G: NativeGraph>: native_seal::Launch {
fn installation(&self) -> &G::Installation;
fn process(&self) -> &G::Process;
fn producer_channel(&self) -> &G::Channel;
fn launch_epoch(&self) -> &ProducerLaunchEpoch;
fn measured_plan(&self) -> &G::LaunchPlan;
}
pub(crate) trait ProducerProjection<G: NativeGraph>: native_seal::Producer {
fn installation(&self) -> &G::Installation;
fn launch(&self) -> &G::Launch;
fn process(&self) -> &G::Process;
fn channel(&self) -> &G::Channel;
fn admission(&self) -> &ProducerAdmissionKey;
}
pub(crate) trait BootstrapProjection<G: NativeGraph>: native_seal::Bootstrap {
fn installation(&self) -> &G::Installation;
fn assets(&self) -> &[G::MeasuredAsset];
}
pub(crate) trait ProducerMessageProjection<G: NativeGraph>: native_seal::ProducerMessage {
fn producer(&self) -> &G::Producer;
fn occurrence(&self) -> &NativeOccurrence;
fn view(&self) -> NativeMessageView<'_>;
}
pub(crate) trait BindingProjection<G: NativeGraph>: native_seal::Binding {
fn producer(&self) -> &G::Producer;
fn invocation_message(&self) -> &G::Message;
fn session_key(&self) -> &NodeSessionKey;
fn invocation_key(&self) -> &NodeInvocationKey;
fn context(&self) -> NativeContextView<'_>;
fn purpose(&self) -> &OperationPurpose;
fn payload_digest(&self) -> &Digest;
fn spawn_plan_digest(&self) -> &Digest;
}
pub(crate) trait PlanProjection<G: NativeGraph>: native_seal::Plan {
fn binding(&self) -> &G::Binding;
fn executable(&self) -> &G::MeasuredAsset;
fn assets(&self) -> &[G::MeasuredAsset];
fn arguments(&self) -> &[ImmutableArgument];
fn environment(&self) -> &[ImmutableEnvironmentEntry];
fn directory(&self) -> &MeasuredDirectoryIdentity;
fn confinement(&self) -> &ConfinementProfile;
fn inheritance(&self) -> &[InheritedResourceSpec];
}
pub(crate) trait ProducerLaunchPlanProjection<G: NativeGraph>: native_seal::LaunchPlan {
fn installation(&self) -> &G::Installation;
fn executable(&self) -> &G::MeasuredAsset;
fn assets(&self) -> &[G::MeasuredAsset];
fn arguments(&self) -> &[ImmutableArgument];
fn environment(&self) -> &[ImmutableEnvironmentEntry];
fn directory(&self) -> &MeasuredDirectoryIdentity;
fn confinement(&self) -> &ConfinementProfile;
fn inheritance(&self) -> &[InheritedResourceSpec];
}
pub(crate) trait ChildProjection<G: NativeGraph>: native_seal::Child {
fn producer(&self) -> &G::Producer;
fn bootstrap(&self) -> &G::Bootstrap;
fn binding(&self) -> &G::Binding;
fn plan(&self) -> &G::Plan;
fn process(&self) -> &G::Process;
fn holder(&self) -> &G::Holder;
fn resources(&self) -> &[RetainedResource<G>];
}
pub(crate) trait HeldProjection<G: NativeGraph>: ChildProjection<G> + native_seal::Held {
fn phase(&self) -> HeldPhase;
}
pub(crate) trait RunningProjection<G: NativeGraph>: ChildProjection<G> + native_seal::Running {
fn phase(&self) -> RunningPhase;
}
pub(crate) trait CallerProjection<G: NativeGraph>: native_seal::Caller {
fn running(&self) -> &RunningHandle<G>;
fn binding(&self) -> &G::Binding;
fn channel(&self) -> &G::Channel;
fn message(&self) -> &G::CallerMessage;
}
pub(crate) trait CallerMessageProjection<G: NativeGraph>: native_seal::CallerMessage {
fn channel(&self) -> &G::Channel;
fn running(&self) -> &RunningHandle<G>;
fn original_bytes(&self) -> &[u8];
fn view(&self) -> NativeInvocationView<'_>;
}
pub(crate) trait BeforeConsumeProjection<G: NativeGraph>: native_seal::BeforeConsume {
fn id(&self) -> &ObligationId;
fn binding(&self) -> &G::Binding;
fn process(&self) -> &G::Process;
fn holder(&self) -> &G::Holder;
fn phase(&self) -> CleanupPhase;
fn requirements(&self) -> &[ResourceRequirement<G>];
fn preserved(&self) -> &[RetainedResource<G>];
}
pub(crate) trait TerminalProjection<G: NativeGraph>: native_seal::Terminal {
fn id(&self) -> &ObligationId;
fn binding(&self) -> &G::Binding;
fn process(&self) -> &G::Process;
fn holder(&self) -> &G::Holder;
fn phase(&self) -> CleanupPhase;
fn requirements(&self) -> &[ResourceRequirement<G>];
}
pub(crate) trait RevokedProjection<G: NativeGraph>: native_seal::Revoked {
fn binding(&self) -> &G::Binding;
fn obligation_id(&self) -> &ObligationId;
fn revocation(&self) -> &NodeRevocationKey;
}
pub(crate) trait CleanupProjection<G: NativeGraph>: native_seal::Cleanup {
fn obligation_id(&self) -> &ObligationId;
fn binding(&self) -> &G::Binding;
fn process(&self) -> &G::Process;
fn holder(&self) -> &G::Holder;
fn phase(&self) -> CleanupPhase;
fn results(&self) -> &[ResourceCleanupResult<G>];
fn preserved(&self) -> &[RetainedResource<G>];
}
pub(crate) trait ChannelFailureProjection<G: NativeGraph>: native_seal::ChannelFailure {
fn classification(&self) -> NativeAdapterError;
fn custody(&self) -> &UnresolvedCustody<G>;
fn producer(&self) -> Option<&G::Producer>;
}
pub(crate) trait SpawnFailureProjection<G: NativeGraph>: native_seal::SpawnFailure {
fn classification(&self) -> NativeAdapterError;
fn custody(&self) -> &UnresolvedCustody<G>;
fn binding(&self) -> &G::Binding;
fn plan(&self) -> &G::Plan;
}
pub(crate) trait ReleaseFailureProjection<G: NativeGraph>: native_seal::ReleaseFailure {
fn classification(&self) -> NativeAdapterError;
fn custody(&self) -> &UnresolvedCustody<G>;
fn binding(&self) -> &G::Binding;
fn plan(&self) -> &G::Plan;
}
pub(crate) trait CleanupFailureProjection<G: NativeGraph>: native_seal::CleanupFailure {
fn classification(&self) -> NativeAdapterError;
fn custody(&self) -> &UnresolvedCustody<G>;
fn partial_results(&self) -> &[ResourceCleanupResult<G>];
}
pub(crate) trait EffectOwnerProjection<G: NativeGraph>: effect_seal::Identity {
fn process(&self) -> &G::Process;
fn identity(&self) -> &NodeEffectOwnerKey;
}
pub(crate) trait PermitProjection<G: NativeGraph, E: EffectGraph<G>>: effect_seal::Permit {
fn binding(&self) -> &G::Binding;
fn permit_id(&self) -> &EffectPermitId;
fn owner(&self) -> &E::Owner;
fn operation_identity(&self) -> &BoundOperationIdentity;
}
pub(crate) trait BoundOperationProjection<G: NativeGraph, E: EffectGraph<G>>: effect_seal::Operation {
fn binding(&self) -> &G::Binding;
fn owner(&self) -> &E::Owner;
fn purpose(&self) -> &OperationPurpose;
fn canonical_payload(&self) -> &[u8];
fn payload_digest(&self) -> &Digest;
}
pub(crate) trait CompletionProjection<G: NativeGraph, E: EffectGraph<G>>: effect_seal::Completion {
fn permit(&self) -> &E::Permit;
fn operation(&self) -> &E::Operation;
fn owner(&self) -> &E::Owner;
fn outcome(&self) -> &RecordedDomainOutcome;
fn record_id(&self) -> &CompletionRecordId;
}
pub(crate) trait UnknownEffectProjection<G: NativeGraph, E: EffectGraph<G>>: effect_seal::Unknown {
fn permit(&self) -> &E::Permit;
fn operation(&self) -> &E::Operation;
fn owner(&self) -> &E::Owner;
fn reason(&self) -> UnknownEffectReason;
fn retained_resources(&self) -> &UnresolvedCustody<G>;
}
enum RetainedResourceOwner<G: NativeGraph> {
Channel(Arc<G::Channel>),
ProcessCustody(Arc<ProcessCustody<G>>),
MeasuredAsset(Arc<G::MeasuredAsset>),
}
pub(crate) struct RetainedResource<G: NativeGraph> { owner: RetainedResourceOwner<G> }
pub(crate) enum RetainedResourceView<'a, G: NativeGraph> {
Channel(&'a G::Channel),
ProcessCustody(&'a ProcessCustody<G>),
MeasuredAsset(&'a G::MeasuredAsset),
}
impl<G: NativeGraph> RetainedResource<G> {
pub(crate) fn view(&self) -> RetainedResourceView<'_, G> { match &self.owner {
RetainedResourceOwner::Channel(owner) => RetainedResourceView::Channel(owner.as_ref()),
RetainedResourceOwner::ProcessCustody(owner) => RetainedResourceView::ProcessCustody(owner.as_ref()),
RetainedResourceOwner::MeasuredAsset(owner) => RetainedResourceView::MeasuredAsset(owner.as_ref()),
} }
}
pub(crate) struct ProcessCustody<G: NativeGraph> { process: Arc<G::Process>, holder: Arc<G::Holder>, binding: Arc<G::Binding>, plan: Arc<G::Plan>, phase: ProcessDisposition }
impl<G: NativeGraph> ProcessCustody<G> {
pub(crate) fn process(&self) -> &G::Process { self.process.as_ref() }
pub(crate) fn holder(&self) -> &G::Holder { self.holder.as_ref() }
pub(crate) fn binding(&self) -> &G::Binding { self.binding.as_ref() }
pub(crate) fn plan(&self) -> &G::Plan { self.plan.as_ref() }
pub(crate) fn phase(&self) -> ProcessDisposition { self.phase }
}
pub(crate) struct ResourceRequirement<G: NativeGraph> { resource: RetainedResource<G>, action: RequiredAction }
impl<G: NativeGraph> ResourceRequirement<G> {
pub(crate) fn resource(&self) -> &RetainedResource<G> { &self.resource }
pub(crate) fn action(&self) -> RequiredAction { self.action }
}
pub(crate) struct ResourceCleanupResult<G: NativeGraph> { resource: RetainedResource<G>, action: RequiredAction, outcome: CleanupResultKind }
impl<G: NativeGraph> ResourceCleanupResult<G> {
pub(crate) fn resource(&self) -> &RetainedResource<G> { &self.resource }
pub(crate) fn action(&self) -> RequiredAction { self.action }
pub(crate) fn outcome(&self) -> &CleanupResultKind { &self.outcome }
}
pub(crate) struct UnresolvedCustody<G: NativeGraph> { raw_channels: Vec<G::RawChannel>, channels: Vec<G::Channel>, processes: Vec<ProcessCustody<G>>, measurements: Vec<G::MeasuredAsset>, before_consume: Vec<G::BeforeConsume>, terminal: Vec<G::Terminal>, revoked: Vec<G::Revoked>, holders: Vec<G::Holder> }
impl<G: NativeGraph> UnresolvedCustody<G> {
pub(crate) fn raw_channels(&self) -> &[G::RawChannel] { &self.raw_channels }
pub(crate) fn channels(&self) -> &[G::Channel] { &self.channels }
pub(crate) fn processes(&self) -> &[ProcessCustody<G>] { &self.processes }
pub(crate) fn measurements(&self) -> &[G::MeasuredAsset] { &self.measurements }
pub(crate) fn before_consume(&self) -> &[G::BeforeConsume] { &self.before_consume }
pub(crate) fn terminal(&self) -> &[G::Terminal] { &self.terminal }
pub(crate) fn revoked(&self) -> &[G::Revoked] { &self.revoked }
pub(crate) fn holders(&self) -> &[G::Holder] { &self.holders }
}
pub(crate) struct HeldHandle<G: NativeGraph> { inner: G::ChildCustody }
impl<G: NativeGraph> native_seal::Child for HeldHandle<G> {}
impl<G: NativeGraph> native_seal::Held for HeldHandle<G> {}
impl<G: NativeGraph> ChildProjection<G> for HeldHandle<G> {
fn producer(&self) -> &G::Producer { self.inner.producer() }
fn bootstrap(&self) -> &G::Bootstrap { self.inner.bootstrap() }
fn binding(&self) -> &G::Binding { self.inner.binding() }
fn plan(&self) -> &G::Plan { self.inner.plan() }
fn process(&self) -> &G::Process { self.inner.process() }
fn holder(&self) -> &G::Holder { self.inner.holder() }
fn resources(&self) -> &[RetainedResource<G>] { self.inner.resources() }
}
impl<G: NativeGraph> HeldProjection<G> for HeldHandle<G> {
fn phase(&self) -> HeldPhase { HeldPhase::Held }
}
pub(crate) struct RunningHandle<G: NativeGraph> { inner: G::ChildCustody }
impl<G: NativeGraph> native_seal::Child for RunningHandle<G> {}
impl<G: NativeGraph> native_seal::Running for RunningHandle<G> {}
impl<G: NativeGraph> ChildProjection<G> for RunningHandle<G> {
fn producer(&self) -> &G::Producer { self.inner.producer() }
fn bootstrap(&self) -> &G::Bootstrap { self.inner.bootstrap() }
fn binding(&self) -> &G::Binding { self.inner.binding() }
fn plan(&self) -> &G::Plan { self.inner.plan() }
fn process(&self) -> &G::Process { self.inner.process() }
fn holder(&self) -> &G::Holder { self.inner.holder() }
fn resources(&self) -> &[RetainedResource<G>] { self.inner.resources() }
}
impl<G: NativeGraph> RunningProjection<G> for RunningHandle<G> {
fn phase(&self) -> RunningPhase { RunningPhase::Running }
}
pub(crate) trait NativeCallerPlatform<G: NativeGraph>: native_seal::Platform {
fn verify_installation(&mut self) -> Result<G::Installation, NativeAdapterError>;
fn verify_bootstrap(&mut self, installation: &G::Installation) -> Result<G::Bootstrap, NativeAdapterError>;
fn authenticate_producer(&mut self, channel: G::RawChannel, installation: &G::Installation, launch: &G::Launch) -> Result<G::Producer, G::ChannelFailure>;
fn receive_producer_message(&mut self, producer: &G::Producer) -> Result<G::Message, G::ChannelFailure>;
fn hold_spawn(&mut self, producer: &G::Producer, bootstrap: &G::Bootstrap, binding: &G::Binding, plan: &G::Plan) -> Result<HeldHandle<G>, G::SpawnFailure>;
fn observe_held(&mut self, held: &HeldHandle<G>) -> Result<ProcessObservation, NativeAdapterError>;
fn release(&mut self, held: HeldHandle<G>, binding: &G::Binding) -> Result<RunningHandle<G>, G::ReleaseFailure>;
fn inspect_caller(&mut self, channel: G::RawChannel, installation: &G::Installation, running: &RunningHandle<G>, binding: &G::Binding) -> Result<G::Caller, G::ChannelFailure>;
fn caller_observation(&mut self, caller: &G::Caller) -> Result<ProcessObservation, NativeAdapterError>;
fn is_live(&mut self, running: &RunningHandle<G>) -> Result<bool, NativeAdapterError>;
fn cleanup_before_consume(&mut self, running: &RunningHandle<G>, obligation: G::BeforeConsume) -> Result<G::Cleanup, G::CleanupFailure>;
fn cancel_hold(&mut self, held: HeldHandle<G>, revoked: G::Revoked, obligation: G::Terminal) -> Result<G::Cleanup, G::CleanupFailure>;
fn terminate(&mut self, running: RunningHandle<G>, revoked: G::Revoked, obligation: G::Terminal) -> Result<G::Cleanup, G::CleanupFailure>;
}
pub(crate) trait NodeEffectOwner<G: NativeGraph, E: EffectGraph<G>>: effect_seal::Owner {
fn identity(&self) -> &E::Owner;
fn execute(&mut self, permit: E::Permit, operation: E::Operation) -> Result<E::Completion, E::Unknown>;
}
pub(crate) const UNRESOLVED_NATIVE_PROFILES: &[(&str, &[&str])] = &[
("native-message", &["actor-bytes", "event-kind-bytes", "purpose-bytes", "context-bytes", "original-payload-bytes"]),
("launch-arguments", &["argument-bytes", "argument-count", "argument-aggregate-bytes"]),
("launch-environment", &["key-bytes", "value-bytes", "entry-count", "aggregate-bytes", "unique-keys"]),
("retained-resources", &["asset-count", "inheritance-count", "resource-count", "cleanup-results-count", "unresolved-custody-count"]),
("domain-recording", &["canonical-result-bytes", "canonical-error-bytes"]),
];
}
pub(crate) use native_projection::*;
