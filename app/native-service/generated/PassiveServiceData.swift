// Generated passive data only; no installation or authority.
import Foundation
enum PassiveServiceResult: Int32 {
    case ok = 0
    case invalidOutput = 1
}
enum PassiveServiceAvailabilityTag: UInt32 {
    case unknown = 0
    case bindingUnavailable = 1
    case plistAbsent = 2
    case unverified = 3
    case notRegistered = 4
    case registeredEligible = 5
    case requiresConsent = 6
    case notFound = 7
}
enum PassiveServiceReasonCode: UInt32 {
    case unknown = 0
    case containingAppUnavailable = 1
    case containingAppInspectionUnavailable = 2
    case plistAbsent = 3
    case locationUnverified = 4
    case plistUnverified = 5
    case plistReadUnavailable = 6
    case plistBindingMismatch = 7
    case sealUnverified = 8
    case signingLabelMismatch = 9
    case plistChanged = 10
    case eligibilityOnly = 11
    case statusUnavailable = 12
    case clockUnavailable = 13
    var message: String {
        switch self {
        case .unknown: return "Service availability is unknown."
        case .containingAppUnavailable: return "The designated containing app is unavailable."
        case .containingAppInspectionUnavailable: return "The containing app cannot be inspected."
        case .plistAbsent: return "The planned bundled service plist is absent."
        case .locationUnverified: return "The bundled service location is unverified."
        case .plistUnverified: return "The bundled service plist is unverified."
        case .plistReadUnavailable: return "The bundled service plist cannot be read."
        case .plistBindingMismatch: return "The bundled service plist does not match its planned binding."
        case .sealUnverified: return "The containing app seal is unverified."
        case .signingLabelMismatch: return "The containing app signing label is inconsistent."
        case .plistChanged: return "The bundled service plist changed during inspection."
        case .eligibilityOnly: return "Registration eligibility only; installation and runtime readiness are unverified."
        case .statusUnavailable: return "Native service registration observation is unavailable."
        case .clockUnavailable: return "The observation clock is unavailable."
        }
    }
}
struct PassiveServiceData {
    let observedAt: UInt64
    let availability: PassiveServiceAvailabilityTag
    let reason: PassiveServiceReasonCode
    static func validLayout() -> Bool {
        MemoryLayout<PhPassiveServiceObservationV1>.size == 16
            && MemoryLayout<PhPassiveServiceObservationV1>.stride == 16
            && MemoryLayout<PhPassiveServiceObservationV1>.alignment == 8
            && MemoryLayout<PhPassiveServiceObservationV1>.offset(of: \.observed_at) == 0
            && MemoryLayout<PhPassiveServiceObservationV1>.offset(of: \.availability) == 8
            && MemoryLayout<PhPassiveServiceObservationV1>.offset(of: \.reason) == 12
    }
    static func decode(result: Int32, raw: PhPassiveServiceObservationV1) -> Self? {
        guard validLayout(), result == PassiveServiceResult.ok.rawValue,
              let availability = PassiveServiceAvailabilityTag(rawValue: raw.availability),
              let reason = PassiveServiceReasonCode(rawValue: raw.reason) else { return nil }
        if raw.observed_at == 0 || reason == .clockUnavailable {
            guard raw.observed_at == 0, availability == .unknown, reason == .clockUnavailable else { return nil }
        }
        return Self(observedAt: raw.observed_at, availability: availability, reason: reason)
    }
}
