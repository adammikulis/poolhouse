import Foundation
import XPC

struct OwnerSession: Equatable {
    let owner: UInt32
    let asid: UInt32
}

struct CallerBinding: Equatable {
    let authority: String
    let guiCDHash: String
    let session: OwnerSession
}

final class AdmittedChallenge {
    let card: ApprovalCard
    fileprivate let message: xpc_object_t
    fileprivate let admissionID: UUID
    fileprivate let generation: UInt64
    let caller: CallerBinding
    fileprivate init(card: ApprovalCard, message: xpc_object_t, generation: UInt64, admissionID: UUID, caller: CallerBinding) {
        self.card = card; self.message = message; self.generation = generation; self.caller = caller; self.admissionID = admissionID
    }
}

struct ChallengeFields {
    static func decode(_ message: xpc_object_t, authorityID: String, requestID: String, now: UInt64) throws -> ApprovalCard {
        guard xpc_get_type(message) == XPC_TYPE_DICTIONARY, xpc_dictionary_get_count(message) == 3,
              let kind = xpc_dictionary_get_value(message, "kind"), xpc_get_type(kind) == XPC_TYPE_STRING,
              xpc_string_get_length(kind) == 9,
              let name = xpc_string_get_string_ptr(kind), String(cString: name) == "challenge",
              let authority = xpc_dictionary_get_value(message, "authority_id"), xpc_get_type(authority) == XPC_TYPE_STRING,
              xpc_string_get_length(authority) == 64, let authorityText = xpc_string_get_string_ptr(authority),
              String(cString: authorityText) == authorityID,
              let value = xpc_dictionary_get_value(message, "card"), xpc_get_type(value) == XPC_TYPE_DATA else { throw ApprovalFailure.invalid }
        let count = xpc_data_get_length(value)
        guard count > 0, count <= 98304, let bytes = xpc_data_get_bytes_ptr(value) else { throw ApprovalFailure.invalid }
        var parser = try StrictCardJSON(Data(bytes: bytes, count: count))
        let object = try parser.document()
        return try ApprovalCard.decode(object, requestedID: requestID, now: now)
    }
}

final class CallerAdmission {
    private let lock = NSRecursiveLock()
    private let manifest: PublicManifest
    private let requestID: String
    private let admissionID = UUID()
    private let initialASID: UInt32
    private var generation: UInt64 = 1
    private var cancelled = false
    private var admitted = false

    init(manifest: PublicManifest, requestID: String) throws {
        var asid: UInt32 = 0
        guard ApprovalCard.hex(requestID), ph_local_session(manifest.owner, &asid),
              ph_self_code(manifest.gui.cdhash) else { throw ApprovalFailure.unavailable }
        self.manifest = manifest; self.requestID = requestID; initialASID = asid
    }

    private var callerBinding: CallerBinding {
        CallerBinding(authority: manifest.authority, guiCDHash: manifest.gui.cdhash,
                      session: OwnerSession(owner: manifest.owner, asid: initialASID))
    }

    func cancel() { lock.lock(); defer { lock.unlock() }; cancelled = true; generation = 0 }

    func receive(_ message: xpc_object_t, now: UInt64) throws -> AdmittedChallenge {
        lock.lock(); defer { lock.unlock() }
        var asid: UInt32 = 0
        guard !cancelled, !admitted, ph_local_session(manifest.owner, &asid), asid == initialASID,
              ph_message_caller(message, manifest.helper.cdhash) else { cancel(); throw ApprovalFailure.unavailable }
        do {
            let card = try ChallengeFields.decode(message, authorityID: manifest.authority, requestID: requestID, now: now)
            admitted = true
            return AdmittedChallenge(card: card, message: message, generation: generation, admissionID: admissionID, caller: callerBinding)
        } catch { cancel(); throw error }
    }

    func validate(_ challenge: AdmittedChallenge, now: UInt64) throws {
        lock.lock(); defer { lock.unlock() }
        var asid: UInt32 = 0
        guard !cancelled, admitted, challenge.admissionID == admissionID, challenge.generation == generation, challenge.caller == callerBinding,
              challenge.card.requestID == requestID, now < challenge.card.expires,
              ph_local_session(manifest.owner, &asid), asid == initialASID,
              ph_message_caller(challenge.message, manifest.helper.cdhash), ph_self_code(manifest.gui.cdhash) else {
            cancel(); throw ApprovalFailure.cancelled
        }
    }
}

struct SystemXPCPlan {
    let service: String
    let flags: UInt64
    let requirement: String

    init(manifest: PublicManifest) {
        service = PublicManifest.service
        flags = UInt64(XPC_CONNECTION_MACH_SERVICE_PRIVILEGED)
        requirement = "cdhash H\"\(manifest.helper.cdhash)\""
    }
}
