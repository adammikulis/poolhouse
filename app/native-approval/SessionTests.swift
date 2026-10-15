#if TESTING
import Foundation

@main
struct SessionTests {
    static func main() throws {
        let id = String(repeating: "1", count: 64)
        let fields: NSDictionary = ["version": 1, "request_id": id, "action": "EnrollPhone",
            "pool": String(repeating: "2", count: 64), "key": String(repeating: "3", count: 64),
            "expires_at": 200, "text": "Add this exact phone key.\nNo other key changes.",
            "nonce": String(repeating: "4", count: 64), "digest": String(repeating: "5", count: 64)]
        let card = try ApprovalCard.decode(fields, requestedID: id, now: 100)
        precondition(card.display.contains(card.pool) && card.display.contains(card.key) && card.display.contains(card.text))
        func refused(_ work: () throws -> Void) { do { try work(); preconditionFailure("Accepted invalid input") } catch {} }
        for (key, value) in [("version", true as Any), ("expires_at", true), ("expires_at", 200.0),
                             ("expires_at", -1), ("action", "Unknown"), ("nonce", "A".padding(toLength: 64, withPad: "a", startingAt: 0)),
                             ("text", "safe\u{202e}spoof"), ("text", "x\0y"), ("text", String(repeating: "x", count: 65537))] {
            let changed = fields.mutableCopy() as! NSMutableDictionary; changed[key] = value
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
