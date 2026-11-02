import Foundation
import Security

#if TESTING
@main
struct KeyObservationTests {
    static func main() throws {
        let base: [String: Any] = [
            kSecAttrKeyClass as String: kSecAttrKeyClassPrivate,
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits as String: NSNumber(value: 256),
            kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
            kSecAttrCanSign as String: kCFBooleanTrue as Any
        ]
        try ProviderAttributeShape.check(base, keyClass: kSecAttrKeyClassPrivate, requireProvider: true)
        let cases: [(String, Any?, ProviderObservationFailure)] = [
            (kSecAttrKeyClass as String, kSecAttrKeyClassPublic, .wrongKeyClass),
            (kSecAttrKeyClass as String, nil, .missingAttributes),
            (kSecAttrKeyType as String, kSecAttrKeyTypeRSA, .unsupportedKeyType),
            (kSecAttrKeyType as String, nil, .missingAttributes),
            (kSecAttrKeySizeInBits as String, NSNumber(value: 256.0), .unsupportedKeySize),
            (kSecAttrKeySizeInBits as String, NSNumber(value: true), .unsupportedKeySize),
            (kSecAttrKeySizeInBits as String, "256", .unsupportedKeySize),
            (kSecAttrKeySizeInBits as String, NSNumber(value: 384), .unsupportedKeySize),
            (kSecAttrTokenID as String, "unrecognized-provider", .unsupportedProvider),
            (kSecAttrTokenID as String, nil, .unsupportedProvider),
            (kSecAttrCanSign as String, NSNumber(value: 1), .cannotSign),
            (kSecAttrCanSign as String, kCFBooleanFalse, .cannotSign),
            (kSecAttrCanSign as String, nil, .cannotSign)
        ]
        for (name, value, expected) in cases {
            var attributes = base
            attributes[name] = value
            do {
                try ProviderAttributeShape.check(attributes, keyClass: kSecAttrKeyClassPrivate, requireProvider: true)
                preconditionFailure("malformed or unsupported attribute accepted")
            } catch let failure as ProviderObservationFailure { precondition(failure == expected) }
        }
        // Arbitrary claims stay attribute data and cannot create an observation
        // or a policy capability. No SecKey is created or queried by these tests.
        var claims = base
        claims["strong"] = true
        claims["os_per_signature_presence"] = true
        claims[kSecAttrApplicationLabel as String] = Data(repeating: 1, count: 32)
        try ProviderAttributeShape.check(claims, keyClass: kSecAttrKeyClassPrivate, requireProvider: true)
        print("native provider attribute refusals passed; no key or policy admission")
    }
}
#endif
