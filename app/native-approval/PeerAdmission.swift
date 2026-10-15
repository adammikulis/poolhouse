import Foundation
import XPC

final class AdmittedChallenge {
    let card: ApprovalCard
    fileprivate let message: xpc_object_t
    fileprivate let admissionID: UUID
    fileprivate let generation: UInt64
    fileprivate let asid: UInt32
    fileprivate init(card: ApprovalCard, message: xpc_object_t, generation: UInt64, asid: UInt32, admissionID: UUID) {
        self.card = card; self.message = message; self.generation = generation; self.asid = asid; self.admissionID = admissionID
    }
}

final class PeerAdmission {
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

    func cancel() { lock.lock(); defer { lock.unlock() }; cancelled = true; generation = 0 }

    func receive(_ message: xpc_object_t, now: UInt64) throws -> AdmittedChallenge {
        lock.lock(); defer { lock.unlock() }
        var asid: UInt32 = 0
        guard !cancelled, !admitted, ph_local_session(manifest.owner, &asid), asid == initialASID,
              ph_message_peer(message, manifest.helper.cdhash),
              xpc_get_type(message) == XPC_TYPE_DICTIONARY, xpc_dictionary_get_count(message) == 2,
              let kind = xpc_dictionary_get_value(message, "kind"), xpc_get_type(kind) == XPC_TYPE_STRING,
              let name = xpc_string_get_string_ptr(kind), String(cString: name) == "challenge",
              let value = xpc_dictionary_get_value(message, "card"), xpc_get_type(value) == XPC_TYPE_DATA else {
            cancel(); throw ApprovalFailure.unavailable
        }
        let count = xpc_data_get_length(value)
        guard count > 0, count <= 98304, let bytes = xpc_data_get_bytes_ptr(value) else {
            cancel(); throw ApprovalFailure.invalid
        }
        do {
            let object = try JSONSerialization.jsonObject(with: Data(bytes: bytes, count: count))
            let card = try ApprovalCard.decode(object, requestedID: requestID, now: now)
            admitted = true
            return AdmittedChallenge(card: card, message: message, generation: generation, asid: asid, admissionID: admissionID)
        } catch { cancel(); throw error }
    }

    func validate(_ challenge: AdmittedChallenge, now: UInt64) throws {
        lock.lock(); defer { lock.unlock() }
        var asid: UInt32 = 0
        guard !cancelled, admitted, challenge.admissionID == admissionID, challenge.generation == generation, challenge.asid == initialASID,
              challenge.card.requestID == requestID, now < challenge.card.expires,
              ph_local_session(manifest.owner, &asid), asid == initialASID,
              ph_message_peer(challenge.message, manifest.helper.cdhash) else {
            cancel(); throw ApprovalFailure.cancelled
        }
    }
}

final class XPCAdmissionTransport {
    private let admission: PeerAdmission
    private let connection: xpc_connection_t

    init(manifest: PublicManifest, requestID: String) throws {
        admission = try PeerAdmission(manifest: manifest, requestID: requestID)
        connection = xpc_connection_create_mach_service(PublicManifest.service, nil, UInt64(XPC_CONNECTION_MACH_SERVICE_PRIVILEGED))
        guard xpc_connection_set_peer_code_signing_requirement(connection, "cdhash H\"\(manifest.helper.cdhash)\"") == 0 else {
            xpc_connection_cancel(connection); throw ApprovalFailure.unavailable
        }
        xpc_connection_set_event_handler(connection) { [weak self] event in
            guard let self else { return }
            guard xpc_get_type(event) == XPC_TYPE_DICTIONARY else { self.admission.cancel(); return }
            // No admitted challenge is exposed to the authorization path in this checkpoint.
            do { _ = try self.admission.receive(event, now: UInt64(max(0, Date().timeIntervalSince1970))) }
            catch { self.admission.cancel(); xpc_connection_cancel(self.connection) }
        }
    }

    func cancel() { admission.cancel(); xpc_connection_cancel(connection) }
    deinit { xpc_connection_cancel(connection) }
}
