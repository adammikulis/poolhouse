//! Immutable launch DATA validation/accounting, not reservation or execution authority.
//! A future native constructor must retain the SAME immutable input allocation in
//! plan and reservation, plus actual resource custody (never a hash-only receipt).
//! Hold/spawn must revalidate the exact current OS, image and reservation generation.
//! Public reference kernel accounting is NOT proof of the running unpublished kernel.
use crate::contract_lifecycle::{BindingProjection, Digest, ImmutableArgument, ImmutableEnvironmentEntry,
    InstallationProjection, LaunchAccountingProfile, LaunchInputsProjection, LaunchLimitsData,
    LaunchReservationProjection, LaunchTargetKind, MeasuredAssetProjection, NativeGraph,
    PlanProjection, ProducerLaunchPlanProjection, ProducerProjection};
#[path = "../contract_launch_data.rs"]
mod rules;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum LaunchDataError { Binding, EmptyArgv, Null, Key, DuplicateKey, Encoding,
    WindowsNativeUnavailable, Budget, Work, Limit, Overflow, Changed }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct LaunchAccounting { pub(crate) arguments: u64, pub(crate) environment: u64,
    pub(crate) string_bytes: u64, pub(crate) kernel_bytes: u64, pub(crate) vector_bytes: u64, pub(crate) allocation_bytes: u64 }
#[derive(Clone, Copy)]
struct Limits { os_bytes: u64, reserved_bytes: u64, string_bytes: u64, arguments: u64,
    environment: u64, vector_bytes: u64, overhead: u64, kernel_pointer: u64, vector_pointer: u64, filename: u64, work: u64 }
impl Limits {
    fn read(value: &LaunchLimitsData) -> Self { Self { os_bytes: value.os_combined_bytes(),
        reserved_bytes: value.reservation_bytes(), string_bytes: value.max_string_bytes(),
        arguments: value.max_argument_count(), environment: value.max_environment_count(),
        vector_bytes: value.vector_reserved_bytes(), overhead: value.allocation_overhead_bytes(), kernel_pointer: value.kernel_pointer_bytes(),
        vector_pointer: value.vector_pointer_bytes(), filename: value.exec_filename_bytes(), work: value.work() } }
}
struct BindingData<'a> {
    expected_target: LaunchTargetKind, target: LaunchTargetKind, reserved_target: LaunchTargetKind,
    expected_digest: &'a [u8; 32], digest: &'a [u8; 32], reserved_digest: &'a [u8; 32],
    inputs: &'a [u8; 32], reserved_inputs: &'a [u8; 32], generation: &'a str,
    reserved_generation: &'a str, retained_generation: &'a str,
    installation: (&'a str, &'a str), expected_installation: (&'a str, &'a str),
}
impl BindingData<'_> {
    fn check(&self) -> Result<(), LaunchDataError> {
        if self.expected_target != self.target || self.target != self.reserved_target
            || self.expected_digest != self.digest || self.digest != self.reserved_digest
            || self.inputs != self.reserved_inputs || self.generation != self.reserved_generation
            || self.generation != self.retained_generation || self.installation != self.expected_installation {
            return Err(LaunchDataError::Binding);
        }
        Ok(())
    }
}
// Private data-view seam permits mechanical tests without constructing a NativeGraph
// implementation, a reservation handle, a process, or any purported OS proof.
trait View {
    fn binding(&self) -> BindingData<'_>;
    fn profile(&self) -> LaunchAccountingProfile;
    fn limits(&self) -> Limits;
    fn argument_count(&self) -> usize;
    fn environment_count(&self) -> usize;
    fn argument(&self, index: usize) -> Option<&[u8]>;
    fn environment(&self, index: usize) -> Option<(&[u8], &[u8])>;
}
struct Projected<'a, G: NativeGraph> { inputs: &'a G::LaunchInputs, reservation: &'a G::LaunchReservation,
    digest: &'a Digest, target: LaunchTargetKind, installation: &'a G::Installation }
impl<G: NativeGraph> View for Projected<'_, G> {
    fn binding(&self) -> BindingData<'_> {
        let retained = self.reservation.inputs();
        let actual = self.inputs.executable().identity().installation();
        let expected = self.installation.identity();
        BindingData { expected_target: self.target, target: self.inputs.target(), reserved_target: retained.target(),
            expected_digest: self.digest.bytes(), digest: self.inputs.plan_digest().bytes(), reserved_digest: retained.plan_digest().bytes(),
            inputs: self.inputs.identity().bytes(), reserved_inputs: retained.identity().bytes(),
            generation: self.inputs.reservation_generation().as_str(), reserved_generation: self.reservation.generation().as_str(),
            retained_generation: retained.reservation_generation().as_str(),
            installation: (actual.id().as_str(), actual.generation().as_str()),
            expected_installation: (expected.id().as_str(), expected.generation().as_str()) }
    }
    fn profile(&self) -> LaunchAccountingProfile { self.inputs.profile() }
    fn limits(&self) -> Limits { Limits::read(self.inputs.limits()) }
    fn argument_count(&self) -> usize { self.inputs.arguments().len() }
    fn environment_count(&self) -> usize { self.inputs.environment().len() }
    fn argument(&self, index: usize) -> Option<&[u8]> { self.inputs.arguments().get(index).map(ImmutableArgument::bytes) }
    fn environment(&self, index: usize) -> Option<(&[u8], &[u8])> { self.inputs.environment().get(index).map(|entry: &ImmutableEnvironmentEntry| (entry.key(), entry.value())) }
}
pub(crate) fn validate_plan<G: NativeGraph>(plan: &G::Plan) -> Result<LaunchAccounting, LaunchDataError> {
    validate(&Projected::<G> { inputs: plan.inputs(), reservation: plan.reservation(),
        digest: plan.binding().spawn_plan_digest(), target: LaunchTargetKind::Worker,
        installation: plan.binding().producer().installation() })
}
pub(crate) fn validate_producer_plan<G: NativeGraph>(plan: &G::LaunchPlan) -> Result<LaunchAccounting, LaunchDataError> {
    validate(&Projected::<G> { inputs: plan.inputs(), reservation: plan.reservation(),
        digest: plan.plan_digest(), target: LaunchTargetKind::Producer, installation: plan.installation() })
}
struct Work(u64);
impl Work {
    fn take(&mut self, count: usize) -> Result<(), LaunchDataError> {
        self.0 = self.0.checked_sub(u64::try_from(count).map_err(|_| LaunchDataError::Overflow)?).ok_or(LaunchDataError::Work)?;
        Ok(())
    }
}
fn add(a: u64, b: u64) -> Result<u64, LaunchDataError> { a.checked_add(b).ok_or(LaunchDataError::Overflow) }
fn mul(a: u64, b: u64) -> Result<u64, LaunchDataError> { a.checked_mul(b).ok_or(LaunchDataError::Overflow) }
fn count(value: usize) -> Result<u64, LaunchDataError> { u64::try_from(value).map_err(|_| LaunchDataError::Overflow) }
fn width(value: u64) -> Result<(), LaunchDataError> { if matches!(value, 4 | 8) { Ok(()) } else { Err(LaunchDataError::Budget) } }
fn unix_string(bytes: &[u8], work: &mut Work) -> Result<u64, LaunchDataError> {
    work.take(bytes.len())?;
    if bytes.contains(&0) { return Err(LaunchDataError::Null); }
    add(count(bytes.len())?, 1)
}
fn key(bytes: &[u8], work: &mut Work) -> Result<(), LaunchDataError> {
    work.take(bytes.len())?;
    if bytes.is_empty() || bytes.contains(&b'=') { return Err(LaunchDataError::Key); }
    Ok(())
}
fn validate(view: &impl View) -> Result<LaunchAccounting, LaunchDataError> {
    // This exact binding guard precedes even reading limits/profile/launch values.
    view.binding().check()?;
    let profile = view.profile();
    let limits = view.limits();
    let mut work = Work(limits.work); work.take(1)?;
    if matches!(profile, LaunchAccountingProfile::WindowsNativeUnavailable) { return windows_data(view, &mut work); }
    width(limits.kernel_pointer)?; width(limits.vector_pointer)?;
    let arguments = count(view.argument_count())?;
    let environment = count(view.environment_count())?;
    if arguments == 0 { return Err(LaunchDataError::EmptyArgv); }
    // Counts are C/kernel int fields as well as reservation-limited allocations.
    i32::try_from(arguments).map_err(|_| LaunchDataError::Limit)?;
    i32::try_from(environment).map_err(|_| LaunchDataError::Limit)?;
    if arguments > limits.arguments || environment > limits.environment { return Err(LaunchDataError::Limit); }
    let mut strings = 0;
    for index in 0..view.argument_count() {
        work.take(1)?;
        let size = unix_string(view.argument(index).ok_or(LaunchDataError::Changed)?, &mut work)?;
        if size > limits.string_bytes { return Err(LaunchDataError::Limit); }
        strings = add(strings, size)?;
    }
    for index in 0..view.environment_count() {
        work.take(1)?;
        let (name, value) = view.environment(index).ok_or(LaunchDataError::Changed)?;
        key(name, &mut work)?;
        let size = add(unix_string(name, &mut work)?, unix_string(value, &mut work)?)?; // '=' + NUL.
        if size > limits.string_bytes { return Err(LaunchDataError::Limit); }
        for prior in 0..index {
            work.take(1)?;
            let other = view.environment(prior).ok_or(LaunchDataError::Changed)?.0;
            work.take(name.len().min(other.len()).checked_add(1).ok_or(LaunchDataError::Overflow)?)?;
            if name == other { return Err(LaunchDataError::DuplicateKey); }
        }
        strings = add(strings, size)?;
    }
    let entries = add(arguments, environment)?;
    let vector_bytes = mul(add(entries, rules::INPUT_VECTOR_NULL_SLOTS)?, limits.vector_pointer)?;
    if vector_bytes > limits.vector_bytes { return Err(LaunchDataError::Limit); }
    let kernel_bytes = match profile {
        LaunchAccountingProfile::MacNativeImage => mac_bytes(strings, entries, limits)?,
        LaunchAccountingProfile::LinuxNativeImage => linux_bytes(strings, entries, limits)?,
        LaunchAccountingProfile::WindowsNativeUnavailable => return Err(LaunchDataError::WindowsNativeUnavailable),
    };
    // Kernel argument-space charging and actual owned input allocation differ,
    // especially across ABIs. Protected overhead includes actual extra capacity,
    // alignment/bookkeeping and other owned buffers; it is never a caller estimate.
    let allocation_bytes = add(add(strings, vector_bytes)?, limits.overhead)?;
    if kernel_bytes > limits.os_bytes || allocation_bytes > limits.reserved_bytes { return Err(LaunchDataError::Limit); }
    Ok(LaunchAccounting { arguments, environment, string_bytes: strings, kernel_bytes, vector_bytes, allocation_bytes })
}
fn aligned(value: u64, width: u64) -> Result<u64, LaunchDataError> { add(value, (width - value % width) % width) }
fn mac_bytes(strings: u64, entries: u64, limits: Limits) -> Result<u64, LaunchDataError> {
    if !rules::MAC_ALIGN_STRING_TAIL || rules::MAC_CHARGE_EXEC_FILENAME || limits.filename != 0 { return Err(LaunchDataError::Budget); }
    add(aligned(strings, limits.kernel_pointer)?, mul(add(entries, rules::MAC_VECTOR_NULL_SLOTS)?, limits.kernel_pointer)?)
}
fn linux_bytes(strings: u64, entries: u64, limits: Limits) -> Result<u64, LaunchDataError> {
    if rules::LINUX_ALIGN_STRING_TAIL || !rules::LINUX_CHARGE_EXEC_FILENAME || limits.filename == 0 || limits.filename > limits.string_bytes { return Err(LaunchDataError::Budget); }
    add(add(strings, limits.filename)?, mul(add(entries, rules::LINUX_VECTOR_NULL_SLOTS)?, limits.kernel_pointer)?)
}
// Raw units are format DATA only. Native Windows key equivalence/sorting and
// exact target-specific command-line binding are unavailable: never substitute
// ASCII casefolding, Unicode normalization, an ANSI cap or a generic CRT parser.
fn windows_units(bytes: &[u8], work: &mut Work) -> Result<u64, LaunchDataError> {
    work.take(bytes.len())?;
    if bytes.len() % rules::WINDOWS_UNIT_BYTES != 0 { return Err(LaunchDataError::Encoding); }
    if bytes.chunks_exact(2).any(|unit| unit == [0, 0]) { return Err(LaunchDataError::Null); }
    count(bytes.len() / rules::WINDOWS_UNIT_BYTES)
}
fn windows_data(view: &impl View, work: &mut Work) -> Result<LaunchAccounting, LaunchDataError> {
    if view.argument_count() == 0 { return Err(LaunchDataError::EmptyArgv); }
    let mut units = 0;
    for index in 0..view.argument_count() {
        work.take(1)?; units = add(units, add(windows_units(view.argument(index).ok_or(LaunchDataError::Changed)?, work)?, 1)?)?;
    }
    let mut block = 1;
    for index in 0..view.environment_count() {
        work.take(1)?;
        let (name, value) = view.environment(index).ok_or(LaunchDataError::Changed)?;
        let key_units = windows_units(name, work)?;
        if name.starts_with(&[0, b'=']) { return Err(LaunchDataError::WindowsNativeUnavailable); } // Drive-state grammar is not implemented.
        if key_units == 0 || name.chunks_exact(2).any(|unit| unit == [0, b'=']) { return Err(LaunchDataError::Key); }
        block = add(block, add(add(key_units, windows_units(value, work)?)?, 2)?)?;
    }
    let _encoded_bytes = mul(add(units, block.max(2))?, count(rules::WINDOWS_UNIT_BYTES)?)?;
    Err(LaunchDataError::WindowsNativeUnavailable)
}
#[cfg(test)]
#[path = "launch_data_tests.rs"]
mod tests;
