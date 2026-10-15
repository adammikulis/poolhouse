import Foundation

struct WindowSnapshot: Equatable {
    let windowID: UUID
    let card: ApprovalCard
}

enum ApprovalPhase: Equatable { case displaying, authenticating, submitting, finished, cancelled }

struct ApprovalSession {
    let snapshot: WindowSnapshot
    private(set) var phase: ApprovalPhase = .displaying

    init(card: ApprovalCard, windowID: UUID) { snapshot = WindowSnapshot(windowID: windowID, card: card) }

    mutating func begin(visible: Bool, active: Bool, now: UInt64) throws {
        guard phase == .displaying, visible, active, now < snapshot.card.expires else {
            cancel(); throw ApprovalFailure.cancelled
        }
        phase = .authenticating
    }

    mutating func background() {
        if phase != .authenticating { cancel() }
    }

    mutating func authenticated(snapshot current: WindowSnapshot, visible: Bool, active: Bool, now: UInt64) throws {
        guard phase == .authenticating, current == snapshot, visible, active,
              now < snapshot.card.expires else { cancel(); throw ApprovalFailure.cancelled }
        phase = .submitting
    }

    mutating func completed() throws {
        guard phase == .submitting else { cancel(); throw ApprovalFailure.cancelled }
        phase = .finished
    }

    mutating func cancel() { if phase != .finished { phase = .cancelled } }
}

struct SingleExport {
    private var consumed = false
    mutating func claim() throws {
        guard !consumed else { throw ApprovalFailure.cancelled }
        consumed = true
    }
}
