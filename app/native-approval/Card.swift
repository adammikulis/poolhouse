import Foundation
import CoreFoundation

enum ApprovalFailure: Error { case unavailable, invalid, expired, cancelled }

enum ApprovalAction: String {
    case foundPool = "found_pool", governanceAct = "governance_act"
    case enrollComputer = "enroll_trusted_computer", enrollPhone = "enroll_phone", revokePhone = "revoke_phone"
    case bootstrapPhone = "bootstrap_phone"
    var title: String {
        switch self {
        case .foundPool: return "Create a pool"
        case .governanceAct: return "Change pool governance"
        case .enrollComputer: return "Trust a computer’s approval service"
        case .enrollPhone: return "Add a phone approval key"
        case .revokePhone: return "Remove a phone approval key"
        case .bootstrapPhone: return "Add the first phone approval key"
        }
    }
}

struct PoolApprovalContext: Equatable {
    let pool: String, anchor: String, name: String, signer: String, text: String
    let targetKey: String?, targetDevice: String?
}

struct FirstPhoneContext: Equatable {
    let key: String, device: String, tls: String, offer: String
    let offerExpires: UInt64
    let scope: FirstPhoneScope
}

struct FirstPhonePool: Equatable {
    let id: String, anchor: String, name: String, head: String
}

enum FirstPhoneListing: String {
    case unlisted, listedActive = "listed_active", listedPending = "listed_pending"
}

enum FirstPhoneScope: Equatable {
    case unfounded
    case governed(FirstPhonePool, FirstPhoneListing)
}

enum ApprovalContext: Equatable {
    case pool(PoolApprovalContext)
    case firstPhone(FirstPhoneContext)
}

struct ApprovalCard: Equatable {
    let requestID: String
    let action: ApprovalAction
    let context: ApprovalContext
    let expires: UInt64
    let nonce: String
    let digest: String
    let head: String
    let generation: UInt64

    // This codec accepts only the authenticated helper's bounded CF dictionary.
    static func decode(_ value: Any, requestedID: String, now: UInt64) throws -> ApprovalCard {
        guard let fields = value as? NSDictionary, Set(fields.allKeys.compactMap { $0 as? String }).count == fields.count else { throw ApprovalFailure.invalid }
        func string(_ name: String, limit: Int) throws -> String {
            guard let value = fields[name], CFGetTypeID(value as CFTypeRef) == CFStringGetTypeID(),
                  let text = value as? String, !text.isEmpty, text.utf8.count <= limit,
                  !text.unicodeScalars.contains(where: { $0.value == 0 }) else { throw ApprovalFailure.invalid }
            return text
        }
        func integer(_ object: NSDictionary, _ name: String) throws -> UInt64 {
            guard let number = object[name] as? NSNumber,
                  CFGetTypeID(number) == CFNumberGetTypeID(),
                  !CFNumberIsFloatType(unsafeBitCast(number, to: CFNumber.self)),
                  number.int64Value >= 0 else { throw ApprovalFailure.invalid }
            return number.uint64Value
        }
        guard try integer(fields, "version") == 1 else { throw ApprovalFailure.invalid }
        let id = try string("request_id", limit: 64)
        func object(_ name: String, keys: Set<String>) throws -> NSDictionary {
            guard let object = fields[name] as? NSDictionary,
                  Set(object.allKeys.compactMap { $0 as? String }) == keys, object.count == keys.count else {
                throw ApprovalFailure.invalid
            }
            return object
        }
        let nonce = try string("nonce", limit: 64), digest = try string("digest", limit: 64)
        guard [id, nonce, digest].allSatisfy({ hex($0) }), id == requestedID,
              let action = ApprovalAction(rawValue: try string("action", limit: 32)) else { throw ApprovalFailure.invalid }
        let head = try string("head", limit: 64), generation = try integer(fields, "generation")
        guard hex(head), (action == .foundPool || action == .bootstrapPhone || head != String(repeating: "0", count: 64)) else { throw ApprovalFailure.invalid }
        let expires = try integer(fields, "expires_at")
        guard expires > now, expires - now <= 300 else { throw ApprovalFailure.expired }
        let common: Set<String> = ["version", "request_id", "action", "expires_at", "nonce", "digest", "head", "generation"]
        let context: ApprovalContext
        if action == .bootstrapPhone {
            guard Set(fields.allKeys.compactMap { $0 as? String }) == common.union(["target", "scope"]) else { throw ApprovalFailure.invalid }
            let target = try object("target", keys: ["key", "device", "tls", "offer", "offer_expires_at"])
            guard let key = target["key"] as? String, hex(key), let device = target["device"] as? String, hex(device),
                  let tls = target["tls"] as? String, hex(tls), let offer = target["offer"] as? String, hex(offer) else { throw ApprovalFailure.invalid }
            let offerExpires = try integer(target, "offer_expires_at")
            guard offerExpires >= expires else { throw ApprovalFailure.expired }
            guard let scopeFields = fields["scope"] as? NSDictionary, let kind = scopeFields["kind"] as? String else { throw ApprovalFailure.invalid }
            let scope: FirstPhoneScope
            if kind == "unfounded" {
                guard scopeFields.count == 1 else { throw ApprovalFailure.invalid }
                scope = .unfounded
            } else if kind == "governed" {
                guard Set(scopeFields.allKeys.compactMap { $0 as? String }) == ["kind", "pool", "listing"], scopeFields.count == 3,
                      let listingName = scopeFields["listing"] as? String, let listing = FirstPhoneListing(rawValue: listingName),
                      let pool = scopeFields["pool"] as? NSDictionary,
                      Set(pool.allKeys.compactMap { $0 as? String }) == ["id", "anchor", "name", "head"], pool.count == 4,
                      let poolID = pool["id"] as? String, hex(poolID, length: 32), poolID != String(repeating: "0", count: 32),
                      let anchor = pool["anchor"] as? String, hex(anchor), poolID == String(anchor.prefix(32)),
                      let name = pool["name"] as? String, !name.isEmpty, name.utf8.count <= 256, safeText(name, multiline: false),
                      let poolHead = pool["head"] as? String, hex(poolHead), poolHead != String(repeating: "0", count: 64) else { throw ApprovalFailure.invalid }
                scope = .governed(FirstPhonePool(id: poolID, anchor: anchor, name: name, head: poolHead), listing)
            } else { throw ApprovalFailure.invalid }
            context = .firstPhone(FirstPhoneContext(key: key, device: device, tls: tls, offer: offer, offerExpires: offerExpires, scope: scope))
        } else {
            let needsTarget = action == .enrollPhone || action == .revokePhone || action == .enrollComputer
            guard Set(fields.allKeys.compactMap { $0 as? String }) == common.union(["pool", "signer", "text"]).union(needsTarget ? ["target"] : []) else { throw ApprovalFailure.invalid }
            let poolFields = try object("pool", keys: ["id", "anchor", "name"])
            let signerFields = try object("signer", keys: ["fingerprint", "algorithm", "role", "assurance"])
            guard let pool = poolFields["id"] as? String, hex(pool, length: 32),
                  let anchor = poolFields["anchor"] as? String, hex(anchor), pool == String(anchor.prefix(32)),
                  let name = poolFields["name"] as? String, !name.isEmpty, name.utf8.count <= 256, safeText(name, multiline: false),
                  let signer = signerFields["fingerprint"] as? String, hex(signer), signerFields["algorithm"] as? String == "p256",
                  signerFields["role"] as? String == "admin", signerFields["assurance"] as? String == "trusted-native" else { throw ApprovalFailure.invalid }
            var targetKey: String?, targetDevice: String?
            if needsTarget {
                let target = try object("target", keys: ["key", "device"])
                guard let key = target["key"] as? String, hex(key), let device = target["device"] as? String, hex(device) else { throw ApprovalFailure.invalid }
                targetKey = key; targetDevice = device
            }
            let text = try string("text", limit: 65536)
            guard safeText(text, multiline: true) else { throw ApprovalFailure.invalid }
            context = .pool(PoolApprovalContext(pool: pool, anchor: anchor, name: name, signer: signer, text: text,
                                                targetKey: targetKey, targetDevice: targetDevice))
        }
        return ApprovalCard(requestID: id, action: action, context: context, expires: expires,
                            nonce: nonce, digest: digest, head: head, generation: generation)
    }

    private static func safeText(_ text: String, multiline: Bool) -> Bool {
        !text.unicodeScalars.contains { scalar in
            let n = scalar.value
            return (n < 32 && !(multiline && n == 10)) || (127...159).contains(n)
                || (0x202a...0x202e).contains(n) || (0x2066...0x2069).contains(n)
                || n == 0x2028 || n == 0x2029 || scalar.properties.generalCategory == .format
        }
    }

    static func hex(_ value: String, length: Int = 64) -> Bool {
        value.utf8.count == length && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }

    var display: String {
        if case let .firstPhone(phone) = context {
            let words = stride(from: 0, to: 24, by: 4).map { offset in
                let start = phone.key.index(phone.key.startIndex, offsetBy: offset)
                return String(phone.key[start..<phone.key.index(start, offsetBy: 4)])
            }.joined(separator: " ")
            let poolText: String
            switch phone.scope {
            case .unfounded: poolText = "No pool has been created for this device."
            case let .governed(pool, listing):
                let name = pool.name.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
                let role: String
                switch listing {
                case .unlisted: role = "This key is not listed as a pool administrator; adding it here does not make it one."
                case .listedActive: role = "This key is already listed as an active pool administrator."
                case .listedPending: role = "This key is listed as a pool administrator but is not active yet."
                }
                poolText = "Pool: \u{2068}\"\(name)\"\u{2069}\nPool ID: \(pool.id)\nFounding anchor: \(pool.anchor)\nGovernance head: \(pool.head)\n\(role)"
            }
            return "\(action.title)\n\n\(poolText) This phone becomes the first local approval key for this device. It can approve requests for actions and resources permitted by the device's authorization policy. Compare this key with the one on the phone.\n\nPhone key: \(words)\nFull phone key: \(phone.key)\nDevice: \(phone.device)\nDevice phone-listener TLS certificate: \(phone.tls)\nOffer: \(phone.offer)\nOffer expires: \(phone.offerExpires)\nApproval expires: \(expires)"
        }
        guard case let .pool(scope) = context else { return "" }
        let name = scope.name.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
        var out = "\(action.title)\n\nPool: \u{2068}\"\(name)\"\u{2069}\nPool ID: \(scope.pool)\nFounding anchor: \(scope.anchor)"
        let role = action == .foundPool ? "Proposed founding signer" : "Authorizing signer"
        out += "\n\(role): \(scope.signer); p256; admin; trusted-native"
        if let targetKey = scope.targetKey, let targetDevice = scope.targetDevice { out += "\nTarget key: \(targetKey)\nTarget device: \(targetDevice)" }
        return out + "\nExpires: \(expires)\n\n\(scope.text)"
    }
}
