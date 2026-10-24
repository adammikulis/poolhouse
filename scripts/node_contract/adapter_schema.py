"""Closed semantic grammar for the private native graph; never Rust source input."""
from __future__ import annotations


def getter(name, result, ownership="borrow", *, optional=False):
    return {"name": name, "result": result, "ownership": ownership, "optional": optional}


def projection(seal, graph, *getters, seal_scope="native"):
    return {"seal": seal, "graph": graph, "seal_scope": seal_scope, "getters": list(getters)}


G = getter
PROJECTIONS = {
    "ProcessProjection": projection("Process", "none", G("identity", "KernelProcessIdentity"), G("observation", "ProcessObservation")),
    "InstallationProjection": projection("Installation", "installation", G("identity", "InstallationIdentity"), G("manifest_digest", "Digest"), G("assets", "MeasuredAsset", "borrow-slice"), G("service_principal", "ConfiguredServicePrincipal"), G("owner_principal", "ConfiguredOwnerPrincipal")),
    "MeasuredAssetProjection": projection("MeasuredAsset", "none", G("identity", "MeasuredAssetIdentity"), G("digest", "Digest")),
    "ChannelProjection": projection("Channel", "installation", G("identity", "ChannelIdentity"), G("process", "Process"), G("holder", "Holder"), G("reader_epoch", "ReaderEpoch"), G("direction", "ChannelDirection", "value")),
    "RawChannelProjection": projection("RawChannel", "installation", G("identity", "ChannelIdentity"), G("observed_process", "Process", optional=True), G("holder", "Holder"), G("direction", "ChannelDirection", "value")),
    "HolderProjection": projection("Holder", "installation", G("identity", "HolderIdentity"), G("process", "Process")),
    "LaunchProjection": projection("Launch", "native", G("installation", "Installation"), G("process", "Process"), G("producer_channel", "Channel"), G("launch_epoch", "ProducerLaunchEpoch"), G("measured_plan", "LaunchPlan")),
    "ProducerProjection": projection("Producer", "native", G("installation", "Installation"), G("launch", "Launch"), G("process", "Process"), G("channel", "Channel"), G("admission", "ProducerAdmissionKey")),
    "BootstrapProjection": projection("Bootstrap", "native", G("installation", "Installation"), G("assets", "MeasuredAsset", "borrow-slice")),
    "ProducerMessageProjection": projection("ProducerMessage", "native", G("producer", "Producer"), G("occurrence", "NativeOccurrence"), G("view", "NativeMessageView", "value")),
    "BindingProjection": projection("Binding", "native", G("producer", "Producer"), G("invocation_message", "Message"), G("session_key", "NodeSessionKey"), G("invocation_key", "NodeInvocationKey"), G("context", "NativeContextView", "value"), G("purpose", "OperationPurpose"), G("payload_digest", "Digest"), G("spawn_plan_digest", "Digest")),
    "PlanProjection": projection("Plan", "native", G("binding", "Binding"), G("inputs", "LaunchInputs"), G("reservation", "LaunchReservation")),
    "ProducerLaunchPlanProjection": projection("LaunchPlan", "native", G("installation", "Installation"), G("plan_digest", "Digest"), G("inputs", "LaunchInputs"), G("reservation", "LaunchReservation")),
    "LaunchInputsProjection": projection("LaunchInputs", "native", G("identity", "LaunchInputsId"), G("target", "LaunchTargetKind", "value"), G("plan_digest", "Digest"), G("reservation_generation", "ResourceGeneration"), G("profile", "LaunchAccountingProfile", "value"), G("limits", "LaunchLimitsData"), G("executable", "MeasuredAsset"), G("assets", "MeasuredAsset", "borrow-slice"), G("arguments", "ImmutableArgument", "borrow-slice"), G("environment", "ImmutableEnvironmentEntry", "borrow-slice"), G("directory", "MeasuredDirectoryIdentity"), G("confinement", "ConfinementProfile"), G("inheritance", "InheritedResourceSpec", "borrow-slice")),
    "LaunchReservationProjection": projection("LaunchReservation", "native", G("identity", "LaunchReservationId"), G("inputs", "LaunchInputs"), G("generation", "ResourceGeneration"), G("holder", "Holder")),
    "ChildProjection": projection("Child", "native", G("producer", "Producer"), G("bootstrap", "Bootstrap"), G("binding", "Binding"), G("plan", "Plan"), G("process", "Process"), G("holder", "Holder"), G("resources", "RetainedResource", "borrow-slice")),
    "HeldProjection": projection("Held", "native", G("phase", "HeldPhase", "value")),
    "RunningProjection": projection("Running", "native", G("phase", "RunningPhase", "value")),
    "CallerProjection": projection("Caller", "native", G("running", "RunningHandle"), G("binding", "Binding"), G("channel", "Channel"), G("message", "CallerMessage")),
    "CallerMessageProjection": projection("CallerMessage", "native", G("channel", "Channel"), G("running", "RunningHandle"), G("original_bytes", "bytes", "borrow-slice"), G("view", "NativeInvocationView", "value")),
    "BeforeConsumeProjection": projection("BeforeConsume", "native", G("id", "ObligationId"), G("binding", "Binding"), G("process", "Process"), G("holder", "Holder"), G("phase", "CleanupPhase", "value"), G("requirements", "ResourceRequirement", "borrow-slice"), G("preserved", "RetainedResource", "borrow-slice")),
    "TerminalProjection": projection("Terminal", "native", G("id", "ObligationId"), G("binding", "Binding"), G("process", "Process"), G("holder", "Holder"), G("phase", "CleanupPhase", "value"), G("requirements", "ResourceRequirement", "borrow-slice")),
    "RevokedProjection": projection("Revoked", "native", G("binding", "Binding"), G("obligation_id", "ObligationId"), G("revocation", "NodeRevocationKey")),
    "CleanupProjection": projection("Cleanup", "native", G("obligation_id", "ObligationId"), G("binding", "Binding"), G("process", "Process"), G("holder", "Holder"), G("phase", "CleanupPhase", "value"), G("results", "ResourceCleanupResult", "borrow-slice"), G("preserved", "RetainedResource", "borrow-slice")),
    "ChannelFailureProjection": projection("ChannelFailure", "native", G("classification", "NativeAdapterError", "value"), G("custody", "UnresolvedCustody"), G("producer", "Producer", optional=True)),
    "SpawnFailureProjection": projection("SpawnFailure", "native", G("classification", "NativeAdapterError", "value"), G("custody", "UnresolvedCustody"), G("binding", "Binding"), G("plan", "Plan")),
    "ReleaseFailureProjection": projection("ReleaseFailure", "native", G("classification", "NativeAdapterError", "value"), G("custody", "UnresolvedCustody"), G("binding", "Binding"), G("plan", "Plan")),
    "CleanupFailureProjection": projection("CleanupFailure", "native", G("classification", "NativeAdapterError", "value"), G("custody", "UnresolvedCustody"), G("partial_results", "ResourceCleanupResult", "borrow-slice")),
    "EffectOwnerProjection": projection("Identity", "native", G("process", "Process"), G("identity", "NodeEffectOwnerKey"), seal_scope="effect"),
    "PermitProjection": projection("Permit", "effect", G("binding", "Binding"), G("permit_id", "EffectPermitId"), G("owner", "Owner"), G("operation_identity", "BoundOperationIdentity"), seal_scope="effect"),
    "BoundOperationProjection": projection("Operation", "effect", G("binding", "Binding"), G("owner", "Owner"), G("purpose", "OperationPurpose"), G("canonical_payload", "bytes", "borrow-slice"), G("payload_digest", "Digest"), seal_scope="effect"),
    "CompletionProjection": projection("Completion", "effect", G("permit", "Permit"), G("operation", "Operation"), G("owner", "Owner"), G("outcome", "RecordedDomainOutcome"), G("record_id", "CompletionRecordId"), seal_scope="effect"),
    "UnknownEffectProjection": projection("Unknown", "effect", G("permit", "Permit"), G("operation", "Operation"), G("owner", "Owner"), G("reason", "UnknownEffectReason", "value"), G("retained_resources", "UnresolvedCustody"), seal_scope="effect"),
}

# Role names are closed schema references, not Rust aliases or independent phase types.
ASSOCIATIONS = [
    ("Installation", "InstallationProjection", "VerifiedInstallation"), ("Launch", "LaunchProjection", "VerifiedProducerLaunch"),
    ("LaunchInputs", "LaunchInputsProjection", "RetainedLaunchInputs"), ("LaunchReservation", "LaunchReservationProjection", "ProtectedLaunchReservation"),
    ("LaunchPlan", "ProducerLaunchPlanProjection", "ProducerLaunchPlan"), ("Process", "ProcessProjection", "Process"),
    ("Channel", "ChannelProjection", "Channel"), ("RawChannel", "RawChannelProjection", "RawChannel"),
    ("MeasuredAsset", "MeasuredAssetProjection", "MeasuredAsset"), ("Producer", "ProducerProjection", "AdmittedProducer"),
    ("Message", "ProducerMessageProjection", "AuthenticatedProducerMessage"), ("Bootstrap", "BootstrapProjection", "VerifiedBootstrap"),
    ("Binding", "BindingProjection", "Binding"), ("Plan", "PlanProjection", "SpawnPlan"),
    ("Holder", "HolderProjection", "Holder"), ("ChildCustody", "ChildProjection", "ChildCustody"),
    ("Caller", "CallerProjection", "AuthenticatedCaller"), ("CallerMessage", "CallerMessageProjection", "CallerMessage"),
    ("BeforeConsume", "BeforeConsumeProjection", "BeforeConsumeObligation"), ("Revoked", "RevokedProjection", "RevokedAuthorization"),
    ("Terminal", "TerminalProjection", "TerminalCleanupObligation"), ("Cleanup", "CleanupProjection", "HolderCleanup"),
    ("ChannelFailure", "ChannelFailureProjection", "OwnedChannelFailure"), ("SpawnFailure", "SpawnFailureProjection", "OwnedSpawnFailure"),
    ("ReleaseFailure", "ReleaseFailureProjection", "OwnedReleaseFailure"), ("CleanupFailure", "CleanupFailureProjection", "OwnedCleanupFailure"),
]
INSTALLATION_ROLES = frozenset({"Installation", "MeasuredAsset", "Process", "Holder", "RawChannel", "Channel"})

EFFECT_ASSOCIATIONS = [("Owner", "EffectOwnerProjection"), ("Permit", "PermitProjection"), ("Operation", "BoundOperationProjection"), ("Completion", "CompletionProjection"), ("Unknown", "UnknownEffectProjection")]
PHASES = [{"name": "HeldHandle", "role": "HeldProcess", "projection": "HeldProjection", "phase": "HeldPhase", "variant": "Held"},
          {"name": "RunningHandle", "role": "RunningProcess", "projection": "RunningProjection", "phase": "RunningPhase", "variant": "Running"}]


def field(name, kind, optional=False):
    return {"name": name, "type": kind, "optional": optional}


def record(*fields):
    return {"kind": "record", "fields": list(fields)}


def enum(**variants):
    return {"kind": "enum", "variants": [{"name": name, "fields": fields} for name, fields in variants.items()]}


F = field
LEAVES = {name: {"kind": "bytes32"} for name in ("Digest", "ProducerLaunchEpoch", "RegistryBootEpoch", "KernelBootIdentity", "ReaderEpoch", "ObligationId", "CompletionRecordId", "HolderIdentity", "ChannelIdentity", "NodeEffectOwnerKey", "LaunchInputsId", "LaunchReservationId")}
LEAVES.update({name: record(F("boot", "RegistryBootEpoch"), F("generation", "u64")) for name in ("ProducerAdmissionKey", "NodeSessionKey", "NodeInvocationKey", "NodeRevocationKey", "EffectPermitId")})
LEAVES.update({name: {"kind": "bytes"} for name in ("NativeActorId", "NativeEventKind", "OperationPurpose", "ImmutableArgument")})
LEAVES.update({
    "LaunchTargetKind": {**enum(Worker=[], Producer=[]), "copy": True},
    "LaunchAccountingProfile": {**enum(MacNativeImage=[], LinuxNativeImage=[], WindowsNativeUnavailable=[]), "copy": True},
    "LaunchLimitsData": record(*(F(name, "u64") for name in ("os_combined_bytes", "reservation_bytes", "max_string_bytes", "max_argument_count", "max_environment_count", "vector_reserved_bytes", "allocation_overhead_bytes", "kernel_pointer_bytes", "vector_pointer_bytes", "exec_filename_bytes", "work"))),
})
LEAVES.update({
    "InstallationIdentity": record(F("id", "InstallationId"), F("generation", "ResourceGeneration")),
    "KernelProcessIdentity": enum(Linux=[F("boot", "KernelBootIdentity"), F("pid", "u32"), F("start_ticks", "u64"), F("uid", "u32")], Windows=[F("boot", "KernelBootIdentity"), F("pid", "u32"), F("creation_time", "u64"), F("sid", "WindowsSid")], Mac=[F("boot", "KernelBootIdentity"), F("pid", "u32"), F("pid_version", "u32"), F("audit", "AuditToken")]),
    "ConfiguredServicePrincipal": enum(UnixUid=[F("uid", "u32")], WindowsSid=[F("sid", "WindowsSid")]),
    "ConfiguredOwnerPrincipal": enum(UnixUid=[F("uid", "u32")], WindowsSid=[F("sid", "WindowsSid")]),
    "NativeOccurrence": record(F("original_launch", "ProducerLaunchEpoch"), F("id", "bytes32")),
    "NativeResumeContinuity": record(F("prior_admission_occurrence", "NativeOccurrence"), F("actor", "NativeActorId"), F("root", "NativeActorId"), F("parent", "NativeActorId", True), F("context_digest", "Digest")),
    "MeasuredAssetIdentity": record(F("installation", "InstallationIdentity"), F("manifest_entry", "u32"), F("digest", "Digest")),
    "ImmutableEnvironmentEntry": record(F("key", "bytes"), F("value", "bytes")),
    "MeasuredDirectoryIdentity": record(F("installation", "InstallationIdentity"), F("manifest_entry", "u32"), F("measurement", "Digest")),
    "ConfinementProfile": enum(WindowsAppContainer=[F("manifest_digest", "Digest")], LinuxBubblewrap=[F("manifest_digest", "Digest")], MacSeatbelt=[F("manifest_digest", "Digest")]),
    "InheritedResourceSpec": record(F("resource", "ChannelIdentity"), F("direction", "ChannelDirection"), F("allowed_operations", "InheritedOperations")),
    "RecordedDomainOutcome": enum(Success=[F("canonical_result", "bytes")], Failure=[F("canonical_error", "bytes")]),
    "BoundOperationIdentity": record(F("invocation", "NodeInvocationKey"), F("purpose", "OperationPurpose"), F("payload_digest", "Digest")),
    "CleanupResultKind": enum(Completed=[], Failed=[F("error", "NativeAdapterError")], Unresolved=[F("error", "NativeAdapterError")]),
})
VALUE_ENUMS = {"ChannelDirection": ("ReadOnly", "WriteOnly", "Duplex"), "InheritedOperations": ("Read", "Write", "ReadWrite"), "HeldPhase": ("Held",), "RunningPhase": ("Running",), "CleanupPhase": ("BeforeConsume", "Terminal"), "RequiredAction": ("CloseChannel", "TerminateAndReapProcess", "ReleaseMeasurement"), "ProcessDisposition": ("Held", "Running", "Unknown"), "UnknownEffectReason": ("BindingMismatchBeforeEffect", "RecordingLostAfterPossibleEffect", "OwnerLost", "Interrupted")}
LEAVES.update({name: {**enum(**{variant: [] for variant in variants}), "copy": True} for name, variants in VALUE_ENUMS.items()})
BASE_VIEW = [G("actor", "NativeActorId"), G("context", "bytes", "borrow-slice"), G("root", "NativeActorId"), G("direct_parent", "NativeActorId", optional=True), G("event_kind", "NativeEventKind")]
VIEWS = {"NativeBirthView": {"kind": "borrowed-record", "fields": BASE_VIEW},
         "NativeResumeView": {"kind": "borrowed-record", "fields": [*BASE_VIEW, G("continuity", "NativeResumeContinuity")]},
         "NativeInvocationView": {"kind": "borrowed-record", "fields": [*BASE_VIEW, G("original_payload", "bytes", "borrow-slice")]},
         "NativeContextView": {"kind": "borrowed-record", "fields": [G("actor", "NativeActorId"), G("root", "NativeActorId"), G("parent", "NativeActorId", optional=True), G("bytes", "bytes", "borrow-slice")]},
         "NativeMessageView": {"kind": "borrowed-enum", "variants": [{"name": "Birth", "type": "NativeBirthView"}, {"name": "Resume", "type": "NativeResumeView"}, {"name": "Invocation", "type": "NativeInvocationView"}]}}


def retained(*fields, shared=False):
    return {"kind": "shared-record" if shared else "retained-record", "fields": list(fields)}


REPRESENTATIONS = {
    "RetainedResource": {"kind": "shared-resource", "variants": [{"name": "Channel", "type": "Channel"}, {"name": "ProcessCustody", "type": "ProcessCustody"}, {"name": "MeasuredAsset", "type": "MeasuredAsset"}]},
    "ProcessCustody": retained(G("process", "Process"), G("holder", "Holder"), G("binding", "Binding"), G("plan", "Plan"), G("phase", "ProcessDisposition", "value"), shared=True),
    "ResourceRequirement": retained(G("resource", "RetainedResource"), G("action", "RequiredAction", "value")),
    "ResourceCleanupResult": retained(G("resource", "RetainedResource"), G("action", "RequiredAction", "value"), G("outcome", "CleanupResultKind")),
    "UnresolvedCustody": retained(G("raw_channels", "RawChannel", "borrow-slice"), G("channels", "Channel", "borrow-slice"), G("processes", "ProcessCustody", "borrow-slice"), G("measurements", "MeasuredAsset", "borrow-slice"), G("before_consume", "BeforeConsume", "borrow-slice"), G("terminal", "Terminal", "borrow-slice"), G("revoked", "Revoked", "borrow-slice"), G("holders", "Holder", "borrow-slice")),
}
PROFILES = [
    {"name": "native-message", "definition": "native_boundaries/native_message.toml", "status": "unresolved-profile-evidence", "fields": ["actor-bytes", "event-kind-bytes", "context-bytes", "original-payload-bytes"]},
    {"name": "launch-arguments", "definition": "native_boundaries/launch_data.toml", "status": "unresolved-profile-evidence", "fields": ["argument-bytes", "argument-count", "argument-aggregate-bytes"]},
    {"name": "launch-environment", "definition": "native_boundaries/launch_data.toml", "status": "unresolved-profile-evidence", "fields": ["key-bytes", "value-bytes", "entry-count", "aggregate-bytes", "unique-keys"]},
    {"name": "retained-resources", "status": "unresolved-profile-evidence", "fields": ["asset-count", "inheritance-count", "resource-count", "cleanup-results-count", "unresolved-custody-count"]},
    {"name": "domain-recording", "status": "unresolved-profile-evidence", "fields": ["canonical-result-bytes", "canonical-error-bytes"]},
]
NEW_REQUIREMENTS = {"source-occurrence-namespace-independent-of-node-boot-and-admission", "durable-consumption-fence-before-effect", "durable-source-disposition-and-custody-fence-before-right", "missing-or-rolled-back-occurrence-ledger-refuses-prior-source", "native-source-view-has-no-node-issued-session-keys", "exact-associated-type-projection-graph", "fixed-nominal-held-and-running-phases", "same-owner-shared-retention-without-duplicating-custody", "unique-phase-obligation-and-full-resource-accounting", "separate-sealed-domain-effect-owner", "unknown-effect-retains-permit-operation-and-owner", "unresolved-profiles-block-runtime-validation"}

for _name in ("ConfiguredServicePrincipal", "ConfiguredOwnerPrincipal", "CleanupResultKind"):
    for _variant in LEAVES[_name]["variants"]:
        if _variant["fields"]:
            _variant["style"] = "tuple"
