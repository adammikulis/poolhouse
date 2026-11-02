#if TESTING
import Foundation
import XPC

@main
struct SessionTests {
    @MainActor static func main() throws {
        try PresenceTests.run()
        try ManifestTests.run()
        let id = String(repeating: "1", count: 64)
        let fields: NSDictionary = ["version": 1, "request_id": id, "action": "enroll_phone",
            "pool": ["id": String(repeating: "2", count: 32), "anchor": String(repeating: "2", count: 64), "name": "Test pool"],
            "signer": ["fingerprint": String(repeating: "3", count: 64), "algorithm": "p256", "role": "admin", "assurance": "trusted-native"],
            "target": ["key": String(repeating: "6", count: 64), "device": String(repeating: "7", count: 64)],
            "expires_at": 200, "text": "Add this exact phone key.\nNo other key changes.",
            "nonce": String(repeating: "4", count: 64), "digest": String(repeating: "5", count: 64),
            "head": String(repeating: "8", count: 64), "generation": 7]
        let card = try ApprovalCard.decode(fields, requestedID: id, now: 100)
        let bootstrap: NSDictionary = ["version": 1, "request_id": id, "action": "bootstrap_phone",
            "target": ["key": String(repeating: "6", count: 64), "device": String(repeating: "7", count: 64),
                       "tls": String(repeating: "9", count: 64), "offer": String(repeating: "a", count: 64), "offer_expires_at": 210],
            "scope": ["kind": "unfounded"],
            "expires_at": 200, "nonce": String(repeating: "4", count: 64), "digest": String(repeating: "5", count: 64),
            "head": String(repeating: "0", count: 64), "generation": 0]
        let firstPhone = try ApprovalCard.decode(bootstrap, requestedID: id, now: 100)
        precondition(firstPhone.display.contains("first local approval key") && firstPhone.display.contains(String(repeating: "6", count: 64)))
        precondition(firstPhone.display.contains("No pool") && !firstPhone.display.contains("Authorizing signer"))
        precondition(firstPhone.display.contains(String(repeating: "7", count: 64)) && firstPhone.display.contains(String(repeating: "9", count: 64)))
        guard case let .pool(pool) = card.context else { preconditionFailure("expected pool context") }
        precondition(card.display.contains(pool.pool) && card.display.contains(pool.signer) && card.display.contains(pool.targetKey!) && card.display.contains(pool.targetDevice!) && card.display.contains(pool.text))
        func refused(_ work: () throws -> Void) { do { try work(); preconditionFailure("Accepted invalid input") } catch {} }
        for malformed in ["{\"version\":1,\"version\":1}", "{\"version\":1,\"v\\u0065rsion\":1}",
                          "{\"target\":{\"key\":\"x\",\"k\\u0065y\":\"y\"}}", "{\"version\":01}", "{\"version\":true}",
                          "{\"a\":{\"b\":{\"c\":{\"d\":\"deep\"}}}}"] {
            refused { var parser = try StrictCardJSON(Data(malformed.utf8)); _ = try parser.document() }
        }
        let poolScope: NSDictionary = ["kind": "governed", "listing": "unlisted",
            "pool": ["id": String(repeating: "2", count: 32), "anchor": String(repeating: "2", count: 64),
                     "name": "Test pool", "head": String(repeating: "3", count: 64)]]
        for (listing, expected) in [("unlisted", "not listed as a pool administrator"),
                                    ("listed_active", "already listed as an active pool administrator"),
                                    ("listed_pending", "not active yet")] {
            let changed = bootstrap.mutableCopy() as! NSMutableDictionary
            let scope = poolScope.mutableCopy() as! NSMutableDictionary
            scope["listing"] = listing
            changed["scope"] = scope
            let governed = try ApprovalCard.decode(changed, requestedID: id, now: 100)
            precondition(governed.display.contains(expected) && governed.display.contains(String(repeating: "2", count: 64)))
        }
        for value in ["unknown", "revoked", NSNull(), ["kind": "unfounded", "listing": "unlisted"],
                      ["kind": "governed", "pool": ["id": "bad"], "listing": "unlisted"]] as [Any] {
            let changed = bootstrap.mutableCopy() as! NSMutableDictionary
            changed["scope"] = value
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        for field in ["version", "request_id", "action", "target", "scope", "expires_at", "nonce", "digest", "head", "generation"] {
            let changed = bootstrap.mutableCopy() as! NSMutableDictionary
            changed.removeObject(forKey: field)
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        for field in ["key", "device", "tls", "offer", "offer_expires_at"] {
            let changed = bootstrap.mutableCopy() as! NSMutableDictionary
            let target = (changed["target"] as! NSDictionary).mutableCopy() as! NSMutableDictionary
            target.removeObject(forKey: field)
            changed["target"] = target
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        for field in ["pool", "signer", "text"] {
            let changed = bootstrap.mutableCopy() as! NSMutableDictionary
            changed[field] = "attacker supplied"
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        let shortOffer = bootstrap.mutableCopy() as! NSMutableDictionary
        let shortTarget = (shortOffer["target"] as! NSDictionary).mutableCopy() as! NSMutableDictionary
        shortTarget["offer_expires_at"] = 199
        shortOffer["target"] = shortTarget
        refused { _ = try ApprovalCard.decode(shortOffer, requestedID: id, now: 100) }
        let challenge = xpc_dictionary_create(nil, nil, 0)
        xpc_dictionary_set_string(challenge, "kind", "challenge")
        xpc_dictionary_set_string(challenge, "authority_id", id)
        let cardBytes = try JSONSerialization.data(withJSONObject: fields)
        cardBytes.withUnsafeBytes { xpc_dictionary_set_data(challenge, "card", $0.baseAddress, $0.count) }
        let decodedChallenge = try ChallengeFields.decode(challenge, authorityID: id, requestID: id, now: 100)
        precondition(decodedChallenge == card)
        refused { _ = try ChallengeFields.decode(challenge, authorityID: String(repeating: "8", count: 64), requestID: id, now: 100) }
        precondition(!ph_message_caller(challenge, String(repeating: "1", count: 40)))

        for (key, value) in [("version", true as Any), ("expires_at", true), ("expires_at", 200.0),
                             ("expires_at", -1), ("generation", true), ("generation", 1.0), ("generation", -1), ("generation", NSNumber(value: UInt64.max)),
                             ("head", String(repeating: "0", count: 64)), ("action", "Unknown"), ("nonce", "A".padding(toLength: 64, withPad: "a", startingAt: 0)),
                             ("text", "safe\u{202e}spoof"), ("text", "x\0y"), ("text", String(repeating: "x", count: 65537))] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed[key] = value
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        for mark in ["\u{061c}", "\u{200b}", "\u{200c}", "\u{200d}", "\u{200e}", "\u{200f}", "\u{2060}", "\u{feff}"] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed["text"] = "safe" + mark + "spoof"
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        for action in ["enroll_phone", "revoke_phone", "enroll_trusted_computer"] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed["action"] = action
            let typed = try ApprovalCard.decode(changed, requestedID: id, now: 100)
            precondition(typed.display.contains("Authorizing signer: " + pool.signer))
            if action == "enroll_trusted_computer" {
                precondition(typed.action.title == "Trust a computer’s approval service")
                precondition(!typed.display.contains("Trust this computer"))
            }
            precondition(typed.display.contains("Target key: " + pool.targetKey!))
            changed.removeObject(forKey: "target")
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        for action in ["found_pool", "governance_act"] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed["action"] = action
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
            changed.removeObject(forKey: "target")
            if action == "found_pool" {
                _ = try ApprovalCard.decode(changed, requestedID: id, now: 100)
                changed["head"] = String(repeating: "0", count: 64)
            }
            let typed = try ApprovalCard.decode(changed, requestedID: id, now: 100)
            if action == "found_pool" { precondition(typed.display.contains("Proposed founding signer:")) }
        }
        for (name, bad) in [("pool", ["id": "bad", "anchor": pool.anchor, "name": "Test pool"]),
                            ("signer", ["fingerprint": pool.signer, "algorithm": "p256", "role": "admin", "assurance": "declared"]),
                            ("target", ["key": pool.targetKey!, "device": "bad"])] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed[name] = bad
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        let extra = fields.mutableCopy() as! NSMutableDictionary; extra["summary"] = "Approve anything"
        refused { _ = try ApprovalCard.decode(extra, requestedID: id, now: 100) }
        refused { _ = try ApprovalCard.decode(fields, requestedID: String(repeating: "6", count: 64), now: 100) }
        refused { _ = try ApprovalCard.decode(fields, requestedID: id, now: 200) }
        let window = UUID()
        var approved = ApprovalSession(card: card, windowID: window)
        try approved.begin(visible: true, active: true, now: 100)
        approved.background()
        precondition(approved.phase == .authenticating)
        try approved.authenticated(snapshot: approved.snapshot, visible: true, active: true, now: 101)
        try approved.completed()
        precondition(approved.phase == .finished)
        refused { try approved.begin(visible: true, active: true, now: 102) }
        for event in 0..<6 {
            var session = ApprovalSession(card: card, windowID: window)
            if event == 0 { session.background() }
            else {
                try session.begin(visible: true, active: true, now: 100)
                if event == 1 { session.cancel() }
                let changed = WindowSnapshot(windowID: event == 2 ? UUID() : window, card: card)
                refused { try session.authenticated(snapshot: changed, visible: event != 3,
                    active: event != 4, now: event == 5 ? 200 : 101) }
            }
            precondition(session.phase == .cancelled)
            refused { try session.begin(visible: true, active: true, now: 102) }
        }
        print("Native approval pure protocol and lifecycle checks passed")
    }
}
#endif
