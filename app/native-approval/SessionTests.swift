#if TESTING
import Foundation
import XPC

@main
struct SessionTests {
    static func main() throws {
        try ManifestTests.run()
        let id = String(repeating: "1", count: 64)
        let fields: NSDictionary = ["version": 1, "request_id": id, "action": "enroll_phone",
            "pool": ["id": String(repeating: "2", count: 32), "anchor": String(repeating: "2", count: 64), "name": "Test pool"],
            "signer": ["fingerprint": String(repeating: "3", count: 64), "algorithm": "p256", "role": "admin", "assurance": "trusted-native"],
            "target": ["key": String(repeating: "6", count: 64), "device": String(repeating: "7", count: 64)],
            "expires_at": 200, "text": "Add this exact phone key.\nNo other key changes.",
            "nonce": String(repeating: "4", count: 64), "digest": String(repeating: "5", count: 64)]
        let card = try ApprovalCard.decode(fields, requestedID: id, now: 100)
        precondition(card.display.contains(card.pool) && card.display.contains(card.signer) && card.display.contains(card.targetKey!) && card.display.contains(card.targetDevice!) && card.display.contains(card.text))
        func refused(_ work: () throws -> Void) { do { try work(); preconditionFailure("Accepted invalid input") } catch {} }
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
                             ("expires_at", -1), ("action", "Unknown"), ("nonce", "A".padding(toLength: 64, withPad: "a", startingAt: 0)),
                             ("text", "safe\u{202e}spoof"), ("text", "x\0y"), ("text", String(repeating: "x", count: 65537))] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed[key] = value
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        for mark in ["\u{061c}", "\u{200e}", "\u{200f}"] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed["text"] = "safe" + mark + "spoof"
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        for action in ["enroll_phone", "revoke_phone", "enroll_trusted_computer"] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed["action"] = action
            let typed = try ApprovalCard.decode(changed, requestedID: id, now: 100)
            precondition(typed.display.contains("Authorizing signer: " + card.signer))
            if action == "enroll_trusted_computer" {
                precondition(typed.action.title == "Trust a computer’s approval service")
                precondition(!typed.display.contains("Trust this computer"))
            }
            precondition(typed.display.contains("Target key: " + card.targetKey!))
            changed.removeObject(forKey: "target")
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        for action in ["found_pool", "governance_act"] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed["action"] = action
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
            changed.removeObject(forKey: "target")
            let typed = try ApprovalCard.decode(changed, requestedID: id, now: 100)
            if action == "found_pool" { precondition(typed.display.contains("Proposed founding signer:")) }
        }
        for (name, bad) in [("pool", ["id": "bad", "anchor": card.anchor, "name": "Test pool"]),
                            ("signer", ["fingerprint": card.signer, "algorithm": "p256", "role": "admin", "assurance": "declared"]),
                            ("target", ["key": card.targetKey!, "device": "bad"])] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed[name] = bad
            refused { _ = try ApprovalCard.decode(changed, requestedID: id, now: 100) }
        }
        var export = SingleExport()
        try export.claim()
        refused { try export.claim() }
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
