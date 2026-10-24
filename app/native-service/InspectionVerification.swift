import Foundation
import ServiceManagement

// Internal verification entry point only; never packaged as the product app.
#if SERVICE_INSPECTION_VERIFY
@main
struct InspectionVerification {
    static func main() {
        if #available(macOS 13.0, *) {
            precondition(PassiveServiceObserver.availability(for: .enabled) == .registeredEligible)
            precondition(PassiveServiceObserver.availability(for: .requiresApproval) == .requiresConsent)
            precondition(PassiveServiceObserver.availability(for: .notRegistered) == .notRegistered)
            precondition(PassiveServiceObserver.availability(for: .notFound) == .notFound)
        }
        let observation = PassiveServiceObserver().observe()
        precondition(observation.availability == .bindingUnavailable)
        precondition(observation.observedAt > 0)
        print("PASS: outside designated app; BindingUnavailable; no service registration query")
    }
}
#endif
