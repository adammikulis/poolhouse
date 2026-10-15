import Foundation
import CoreFoundation

enum ApprovalFailure: Error { case unavailable, invalid, expired, cancelled }

enum ApprovalAction: String {
    case bootstrap = "Bootstrap", enrollPhone = "EnrollPhone", revokePhone = "RevokePhone"
    var title: String {
        switch self {
        case .bootstrap: return "Create protected pool authority"
        case .enrollPhone: return "Add a phone approval key"
        case .revokePhone: return "Remove a phone approval key"
        }
    }
}

struct ApprovalCard: Equatable {
    let requestID: String
    let action: ApprovalAction
    let pool: String
    let key: String
    let expires: UInt64
    let text: String
    let nonce: String
    let digest: String

    // This codec accepts only the authenticated helper's bounded CF dictionary.
    static func decode(_ value: Any, requestedID: String, now: UInt64) throws -> ApprovalCard {
        guard let fields = value as? NSDictionary, fields.count == 9,
              Set(fields.allKeys.compactMap { $0 as? String }) == Set([
                "version", "request_id", "action", "pool", "key", "expires_at", "text", "nonce", "digest"
              ]) else { throw ApprovalFailure.invalid }
        func string(_ name: String, limit: Int) throws -> String {
            guard let value = fields[name], CFGetTypeID(value as CFTypeRef) == CFStringGetTypeID(),
                  let text = value as? String, !text.isEmpty, text.utf8.count <= limit,
                  !text.unicodeScalars.contains(where: { $0.value == 0 }) else { throw ApprovalFailure.invalid }
            return text
        }
        func integer(_ name: String) throws -> UInt64 {
            guard let number = fields[name] as? NSNumber,
                  CFGetTypeID(number) == CFNumberGetTypeID(),
                  !CFNumberIsFloatType(unsafeBitCast(number, to: CFNumber.self)),
                  number.int64Value >= 0 else { throw ApprovalFailure.invalid }
            return number.uint64Value
        }
        guard try integer("version") == 1 else { throw ApprovalFailure.invalid }
        let id = try string("request_id", limit: 64)
        let pool = try string("pool", limit: 64), key = try string("key", limit: 64)
        let nonce = try string("nonce", limit: 64), digest = try string("digest", limit: 64)
        guard [id, pool, key, nonce, digest].allSatisfy({ hex($0) }), id == requestedID,
              let action = ApprovalAction(rawValue: try string("action", limit: 32)) else { throw ApprovalFailure.invalid }
        let expires = try integer("expires_at")
        guard expires > now, expires - now <= 300 else { throw ApprovalFailure.expired }
        let text = try string("text", limit: 65536)
        guard !text.unicodeScalars.contains(where: { scalar in
            let n = scalar.value
            return (n < 32 && n != 10) || (127...159).contains(n)
                || (0x202a...0x202e).contains(n) || (0x2066...0x2069).contains(n)
                || n == 0x2028 || n == 0x2029 || n == 0x061c || n == 0x200e || n == 0x200f
        }) else { throw ApprovalFailure.invalid }
        return ApprovalCard(requestID: id, action: action, pool: pool, key: key,
                            expires: expires, text: text, nonce: nonce, digest: digest)
    }

    static func hex(_ value: String) -> Bool {
        value.utf8.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }

    var display: String {
        "\(action.title)\n\nPool: \(pool)\nApproval key: \(key)\nExpires: \(expires)\n\n\(text)"
    }
}
