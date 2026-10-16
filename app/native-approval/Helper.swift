import Foundation

// No caller-admission constructor exists until the root helper's message authentication is implemented.
@MainActor
final class AuthenticatedHelper {
    private var disconnection: (() -> Void)?
    private init() {}
    static func connect() throws -> AuthenticatedHelper { throw ApprovalFailure.unavailable }
    func challenge(requestID: String) throws -> AdmittedChallenge { throw ApprovalFailure.unavailable }
    func validate(_ challenge: AdmittedChallenge) throws { throw ApprovalFailure.unavailable }
    func commit(result: LocalPresenceResult, challenge: AdmittedChallenge, snapshot: WindowSnapshot) throws {
        try result.consume(challenge: challenge, snapshot: snapshot)
        try validate(challenge)
        throw ApprovalFailure.unavailable
    }
    func onDisconnection(_ callback: @escaping () -> Void) { disconnection = callback }
    func disconnected() { let callback = disconnection; disconnection = nil; callback?() }
    func cancel() { disconnection = nil }
}
