import Foundation
import Security
import Darwin

// No peer-admission constructor exists until the root helper's message authentication is implemented.
final class AuthenticatedHelper {
    private init() {}
    static func connect() throws -> AuthenticatedHelper { throw ApprovalFailure.unavailable }
    func card(requestID: String) throws -> ApprovalCard { throw ApprovalFailure.unavailable }
    func authenticate(_ bearer: UnsafePointer<AuthorizationExternalForm>, snapshot: WindowSnapshot,
                      completion: @escaping @MainActor (Result<Void, Error>) -> Void) throws {
        throw ApprovalFailure.unavailable
    }
    func commit(snapshot: WindowSnapshot) throws { throw ApprovalFailure.unavailable }
    func cancel() {}
}

final class FreshAuthorization {
    private var reference: AuthorizationRef?
    private var export = SingleExport()
    private var external = AuthorizationExternalForm()

    init(helper: AuthenticatedHelper) throws {
        var created: AuthorizationRef?
        let status = AuthorizationCreate(nil, nil, [], &created)
        guard status == errAuthorizationSuccess, let created else { throw ApprovalFailure.unavailable }
        reference = created
        guard AuthorizationMakeExternalForm(created, &external) == errAuthorizationSuccess else {
            destroy(); throw ApprovalFailure.unavailable
        }
    }

    func authenticate(helper: AuthenticatedHelper, snapshot: WindowSnapshot,
                      completion: @escaping @MainActor (Result<Void, Error>) -> Void) throws {
        guard reference != nil else { throw ApprovalFailure.cancelled }
        try export.claim()
        try withUnsafePointer(to: &external) { try helper.authenticate($0, snapshot: snapshot, completion: completion) }
        wipe()
    }

    private func wipe() {
        withUnsafeMutableBytes(of: &external) { bytes in
            if let base = bytes.baseAddress { explicit_bzero(base, bytes.count) }
        }
    }

    func destroy() {
        if let reference { AuthorizationFree(reference, [.destroyRights]) }
        reference = nil
        wipe()
    }
    deinit { destroy() }
}
