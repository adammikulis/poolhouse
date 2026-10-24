import CryptoKit
import Foundation
import Security

// This leaf observes an existing native reference. It issues no owner, root,
// creation-origin, key-policy or admitted-provider capability.
enum ProviderObservationFailure: Error, Equatable {
    case missingAttributes
    case wrongKeyClass
    case unsupportedKeyType
    case unsupportedKeySize
    case unsupportedProvider
    case cannotSign
    case unsupportedSignatureAlgorithm
    case publicKeyUnavailable
    case invalidPublicKey
}

enum ProviderKeyPolicyObservation { case unknown }

// Format validation of SDK attribute values, not native provenance or policy.
enum ProviderAttributeShape {
    static func check(_ attributes: [String: Any], keyClass: CFString, requireProvider: Bool) throws {
        guard let actualClass = attributes[kSecAttrKeyClass as String] as? String else {
            throw ProviderObservationFailure.missingAttributes
        }
        guard actualClass == keyClass as String else { throw ProviderObservationFailure.wrongKeyClass }
        guard let kind = attributes[kSecAttrKeyType as String] as? String else {
            throw ProviderObservationFailure.missingAttributes
        }
        guard kind == kSecAttrKeyTypeECSECPrimeRandom as String else {
            throw ProviderObservationFailure.unsupportedKeyType
        }
        guard let size = attributes[kSecAttrKeySizeInBits as String] as? NSNumber,
              CFGetTypeID(size) == CFNumberGetTypeID(),
              !CFNumberIsFloatType(size), size.int64Value == 256 else {
            throw ProviderObservationFailure.unsupportedKeySize
        }
        if requireProvider {
            guard let token = attributes[kSecAttrTokenID as String] as? String,
                  token == kSecAttrTokenIDSecureEnclave as String else {
                throw ProviderObservationFailure.unsupportedProvider
            }
            guard let sign = attributes[kSecAttrCanSign as String] as? NSNumber,
                  CFGetTypeID(sign) == CFBooleanGetTypeID(), sign.boolValue else {
                throw ProviderObservationFailure.cannotSign
            }
        }
    }
}

// Both successful and refused observations retain this exact original reference.
// A SecKey reference is not evidence of its creator, owner session or ACL flags.
final class ProviderKeyCustody {
    fileprivate let key: SecKey
    fileprivate init(_ key: SecKey) { self.key = key }
}

final class NativeKeyObservation {
    let custody: ProviderKeyCustody
    let publicKeyX963: Data
    let policy: ProviderKeyPolicyObservation = .unknown

    fileprivate init(custody: ProviderKeyCustody, publicKeyX963: Data) {
        self.custody = custody
        self.publicKeyX963 = publicKeyX963
    }
}

enum ProviderObservationResult {
    case observed(NativeKeyObservation)
    case unavailable(ProviderKeyCustody, ProviderObservationFailure)
}

enum NativeKeyObserver {
    // Future caller: a measured, provisioned user-session holder with an
    // independently admitted owner/root scope. Never an app-supplied dictionary,
    // root-daemon keychain query, arbitrary tag lookup or policy-label adapter.
    static func observeExisting(_ key: SecKey) -> ProviderObservationResult {
        let custody = ProviderKeyCustody(key)
        do {
            guard let attributes = SecKeyCopyAttributes(custody.key) as? [String: Any] else {
                throw ProviderObservationFailure.missingAttributes
            }
            try ProviderAttributeShape.check(attributes, keyClass: kSecAttrKeyClassPrivate, requireProvider: true)
            guard SecKeyIsAlgorithmSupported(custody.key, .sign, .ecdsaSignatureMessageX962SHA256) else {
                throw ProviderObservationFailure.unsupportedSignatureAlgorithm
            }
            guard let publicKey = SecKeyCopyPublicKey(custody.key),
                  let publicAttributes = SecKeyCopyAttributes(publicKey) as? [String: Any] else {
                throw ProviderObservationFailure.publicKeyUnavailable
            }
            // The only external-representation call receives the checked PUBLIC
            // reference returned by SecKeyCopyPublicKey, never the private key.
            try ProviderAttributeShape.check(publicAttributes, keyClass: kSecAttrKeyClassPublic, requireProvider: false)
            var error: Unmanaged<CFError>?
            let representation = SecKeyCopyExternalRepresentation(publicKey, &error)
            let exportFailure = error?.takeRetainedValue()
            guard let encoded = representation, exportFailure == nil else {
                throw ProviderObservationFailure.publicKeyUnavailable
            }
            let bytes = encoded as Data
            guard bytes.count == 65, bytes.first == 4,
                  (try? P256.Signing.PublicKey(x963Representation: bytes)) != nil else {
                throw ProviderObservationFailure.invalidPublicKey
            }
            return .observed(NativeKeyObservation(custody: custody, publicKeyX963: bytes))
        } catch let failure as ProviderObservationFailure {
            return .unavailable(custody, failure)
        } catch {
            return .unavailable(custody, .missingAttributes)
        }
    }
}
