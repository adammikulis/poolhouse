import Foundation
import CoreFoundation

enum ApprovalFailure: Error { case unavailable, invalid, expired, cancelled }

enum ApprovalAction: String {
    case foundPool = "found_pool", governanceAct = "governance_act"
    case enrollComputer = "enroll_trusted_computer", enrollPhone = "enroll_phone", revokePhone = "revoke_phone"
    var title: String {
        switch self {
        case .foundPool: return "Create a pool"
        case .governanceAct: return "Change pool governance"
        case .enrollComputer: return "Trust this computer"
        case .enrollPhone: return "Add a phone approval key"
        case .revokePhone: return "Remove a phone approval key"
        }
    }
}

struct ApprovalCard: Equatable {
    let requestID: String
    let action: ApprovalAction
    let pool: String
    let anchor: String
    let poolName: String
    let signer: String
    let targetKey: String?
    let targetDevice: String?
    let expires: UInt64
    let text: String
    let nonce: String
    let digest: String

    // This codec accepts only the authenticated helper's bounded CF dictionary.
    static func decode(_ value: Any, requestedID: String, now: UInt64) throws -> ApprovalCard {
        guard let fields = value as? NSDictionary, (fields.count == 9 || fields.count == 10),
              Set(fields.allKeys.compactMap { $0 as? String }).subtracting(["target"]) == Set([
                "version", "request_id", "action", "pool", "signer", "expires_at", "text", "nonce", "digest"
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
        func object(_ name: String, keys: Set<String>) throws -> NSDictionary {
            guard let object = fields[name] as? NSDictionary,
                  Set(object.allKeys.compactMap { $0 as? String }) == keys, object.count == keys.count else {
                throw ApprovalFailure.invalid
            }
            return object
        }
        let poolFields = try object("pool", keys: ["id", "anchor", "name"])
        let signerFields = try object("signer", keys: ["fingerprint", "algorithm", "role", "assurance"])
        guard let pool = poolFields["id"] as? String, hex(pool, length: 32),
              let anchor = poolFields["anchor"] as? String, hex(anchor), pool == String(anchor.prefix(32)),
              let poolName = poolFields["name"] as? String, !poolName.isEmpty, poolName.utf8.count <= 256,
              safeText(poolName, multiline: false),
              let signer = signerFields["fingerprint"] as? String, hex(signer),
              signerFields["algorithm"] as? String == "p256", signerFields["role"] as? String == "admin",
              signerFields["assurance"] as? String == "trusted-native" else { throw ApprovalFailure.invalid }
        let nonce = try string("nonce", limit: 64), digest = try string("digest", limit: 64)
        guard [id, nonce, digest].allSatisfy({ hex($0) }), id == requestedID,
              let action = ApprovalAction(rawValue: try string("action", limit: 32)) else { throw ApprovalFailure.invalid }
        var targetKey: String?, targetDevice: String?
        if action == .enrollPhone || action == .revokePhone || action == .enrollComputer {
            let target = try object("target", keys: ["key", "device"])
            guard let key = target["key"] as? String, hex(key),
                  let device = target["device"] as? String, hex(device) else { throw ApprovalFailure.invalid }
            targetKey = key; targetDevice = device
        } else if fields["target"] != nil { throw ApprovalFailure.invalid }
        let expires = try integer("expires_at")
        guard expires > now, expires - now <= 300 else { throw ApprovalFailure.expired }
        let text = try string("text", limit: 65536)
        guard safeText(text, multiline: true) else { throw ApprovalFailure.invalid }
        return ApprovalCard(requestID: id, action: action, pool: pool, anchor: anchor, poolName: poolName,
                            signer: signer, targetKey: targetKey, targetDevice: targetDevice,
                            expires: expires, text: text, nonce: nonce, digest: digest)
    }

    private static func safeText(_ text: String, multiline: Bool) -> Bool {
        !text.unicodeScalars.contains { scalar in
            let n = scalar.value
            return (n < 32 && !(multiline && n == 10)) || (127...159).contains(n)
                || (0x202a...0x202e).contains(n) || (0x2066...0x2069).contains(n)
                || n == 0x2028 || n == 0x2029 || n == 0x061c || n == 0x200e || n == 0x200f
        }
    }

    static func hex(_ value: String, length: Int = 64) -> Bool {
        value.utf8.count == length && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }

    var display: String {
        let name = poolName.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        var out = "\(action.title)\n\nPool: \u{2068}\"\(name)\"\u{2069}\nPool ID: \(pool)\nFounding anchor: \(anchor)"
        let role = action == .foundPool ? "Proposed founding signer" : "Authorizing signer"
        out += "\n\(role): \(signer); p256; admin; trusted-native"
        if let targetKey, let targetDevice { out += "\nTarget key: \(targetKey)\nTarget device: \(targetDevice)" }
        return out + "\nExpires: \(expires)\n\n\(text)"
    }
}
