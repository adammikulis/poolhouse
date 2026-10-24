// Generated app-private passive data. No authority or serialization traits.
#[repr(C)] #[derive(Clone, Copy)]
pub(crate) struct PassiveServiceObservationV1 {
    pub(crate) observed_at: u64,
    pub(crate) availability: u32,
    pub(crate) reason: u32,
}
const _: () = {
    assert!(std::mem::size_of::<PassiveServiceObservationV1>() == 16);
    assert!(std::mem::align_of::<PassiveServiceObservationV1>() == 8);
    assert!(std::mem::offset_of!(PassiveServiceObservationV1, observed_at) == 0);
    assert!(std::mem::offset_of!(PassiveServiceObservationV1, availability) == 8);
    assert!(std::mem::offset_of!(PassiveServiceObservationV1, reason) == 12);
};
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PassiveServiceResult {
    Ok,
    InvalidOutput,
}
impl PassiveServiceResult {
    pub(crate) fn from_raw(value: i32) -> Option<Self> {
        match value {
            0 => Some(Self::Ok),
            1 => Some(Self::InvalidOutput),
            _ => None,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PassiveServiceAvailabilityTag {
    Unknown,
    BindingUnavailable,
    PlistAbsent,
    Unverified,
    NotRegistered,
    RegisteredEligible,
    RequiresConsent,
    NotFound,
}
impl PassiveServiceAvailabilityTag {
    pub(crate) fn from_raw(value: u32) -> Option<Self> {
        match value {
            0 => Some(Self::Unknown),
            1 => Some(Self::BindingUnavailable),
            2 => Some(Self::PlistAbsent),
            3 => Some(Self::Unverified),
            4 => Some(Self::NotRegistered),
            5 => Some(Self::RegisteredEligible),
            6 => Some(Self::RequiresConsent),
            7 => Some(Self::NotFound),
            _ => None,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum PassiveServiceReasonCode {
    Unknown,
    ContainingAppUnavailable,
    ContainingAppInspectionUnavailable,
    PlistAbsent,
    LocationUnverified,
    PlistUnverified,
    PlistReadUnavailable,
    PlistBindingMismatch,
    SealUnverified,
    SigningLabelMismatch,
    PlistChanged,
    EligibilityOnly,
    StatusUnavailable,
    ClockUnavailable,
}
impl PassiveServiceReasonCode {
    pub(crate) fn from_raw(value: u32) -> Option<Self> {
        match value {
            0 => Some(Self::Unknown),
            1 => Some(Self::ContainingAppUnavailable),
            2 => Some(Self::ContainingAppInspectionUnavailable),
            3 => Some(Self::PlistAbsent),
            4 => Some(Self::LocationUnverified),
            5 => Some(Self::PlistUnverified),
            6 => Some(Self::PlistReadUnavailable),
            7 => Some(Self::PlistBindingMismatch),
            8 => Some(Self::SealUnverified),
            9 => Some(Self::SigningLabelMismatch),
            10 => Some(Self::PlistChanged),
            11 => Some(Self::EligibilityOnly),
            12 => Some(Self::StatusUnavailable),
            13 => Some(Self::ClockUnavailable),
            _ => None,
        }
    }
    pub(crate) fn message(self) -> &'static str {
        match self {
            Self::Unknown => "Service availability is unknown.",
            Self::ContainingAppUnavailable => "The designated containing app is unavailable.",
            Self::ContainingAppInspectionUnavailable => "The containing app cannot be inspected.",
            Self::PlistAbsent => "The planned bundled service plist is absent.",
            Self::LocationUnverified => "The bundled service location is unverified.",
            Self::PlistUnverified => "The bundled service plist is unverified.",
            Self::PlistReadUnavailable => "The bundled service plist cannot be read.",
            Self::PlistBindingMismatch => "The bundled service plist does not match its planned binding.",
            Self::SealUnverified => "The containing app seal is unverified.",
            Self::SigningLabelMismatch => "The containing app signing label is inconsistent.",
            Self::PlistChanged => "The bundled service plist changed during inspection.",
            Self::EligibilityOnly => "Registration eligibility only; installation and runtime readiness are unverified.",
            Self::StatusUnavailable => "Native service registration observation is unavailable.",
            Self::ClockUnavailable => "The observation clock is unavailable.",
        }
    }
}
pub(crate) struct PassiveServiceData {
    pub(crate) observed_at: u64,
    pub(crate) availability: PassiveServiceAvailabilityTag,
    pub(crate) reason: PassiveServiceReasonCode,
}
impl PassiveServiceData {
    pub(crate) fn decode(result: i32, raw: PassiveServiceObservationV1) -> Option<Self> {
        if PassiveServiceResult::from_raw(result)? != PassiveServiceResult::Ok { return None; }
        let availability = PassiveServiceAvailabilityTag::from_raw(raw.availability)?;
        let reason = PassiveServiceReasonCode::from_raw(raw.reason)?;
        if (raw.observed_at == 0 || reason == PassiveServiceReasonCode::ClockUnavailable)
            && !(raw.observed_at == 0 && availability == PassiveServiceAvailabilityTag::Unknown
                && reason == PassiveServiceReasonCode::ClockUnavailable) { return None; }
        Some(Self { observed_at: raw.observed_at, availability, reason })
    }
}
