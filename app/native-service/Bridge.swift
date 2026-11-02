import Foundation

// Unsafe output storage is supplied only by the trusted in-process app caller.
// The generated C declaration/version and imported struct define the entire ABI.
@_cdecl("ph_passive_service_observe_v1")
func passiveServiceObserve(_ output: UnsafeMutablePointer<PhPassiveServiceObservationV1>?) -> Int32 {
    guard let output else { return PassiveServiceResult.invalidOutput.rawValue }
    return autoreleasepool {
        let value = PassiveServiceObserver().observe()
        output.pointee = PhPassiveServiceObservationV1(observed_at: value.observedAt,
                                                     availability: value.availability.rawValue,
                                                     reason: value.reason.rawValue)
        return PassiveServiceResult.ok.rawValue
    }
}
