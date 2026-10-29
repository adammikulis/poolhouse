import Foundation

struct CodePin: Equatable {
    let cdhash: String
    let sha256: String
    let publisherKind: UInt8
    let publisherDigest: String
    let requirementDigest: String
    let entitlementsDigest: String
}

struct PublicManifest: Equatable {
    static let service = "app.poolhouse.authority"
    let owner: UInt32
    let authority: String
    let installation: String
    let generation: UInt64
    let receiptPublicKey: String
    let policyDigest: String
    let purposePolicy: String
    let rotationPolicy: String
    let helper: CodePin
    let gui: CodePin
    let node: CodePin

    static func loadFixed() throws -> PublicManifest {
        var bytes = [UInt8](repeating: 0, count: 16384), count = 0
        guard ph_manifest_read(&bytes, bytes.count, &count) else { throw ApprovalFailure.unavailable }
        return PublicManifest(fields: try ManifestFields.decode(Data(bytes.prefix(count))))
    }

    private init(fields: ManifestFields) {
        owner = fields.owner; authority = fields.authority; installation = fields.installation
        generation = fields.generation; receiptPublicKey = fields.receiptPublicKey
        policyDigest = fields.policyDigest; purposePolicy = fields.purposePolicy; rotationPolicy = fields.rotationPolicy
        helper = fields.helper; gui = fields.gui; node = fields.node
    }
}

struct ManifestFields: Equatable {
    let owner: UInt32
    let authority: String
    let installation: String
    let generation: UInt64
    let receiptPublicKey: String
    let policyDigest: String
    let purposePolicy: String
    let rotationPolicy: String
    let helper: CodePin
    let gui: CodePin
    let node: CodePin

    static func decode(_ bytes: Data) throws -> ManifestFields {
        var raw = ph_manifest_values()
        let decoded = bytes.withUnsafeBytes { input in
            ph_native_manifest_decode(input.bindMemory(to: UInt8.self).baseAddress, input.count, &raw)
        }
        guard decoded else { throw ApprovalFailure.invalid }
        func hex<T>(_ value: T) -> String {
            withUnsafeBytes(of: value) { $0.map { String(format: "%02x", $0) }.joined() }
        }
        func pin(_ value: ph_manifest_pin) -> CodePin {
            CodePin(cdhash: hex(value.cdhash), sha256: hex(value.sha256), publisherKind: value.publisher_kind,
                    publisherDigest: hex(value.publisher_digest), requirementDigest: hex(value.requirement_digest),
                    entitlementsDigest: hex(value.entitlements_digest))
        }
        return ManifestFields(owner: raw.owner_uid, authority: hex(raw.authority_id), installation: hex(raw.installation_id),
                              generation: raw.generation, receiptPublicKey: hex(raw.receipt_public_key),
                              policyDigest: hex(raw.policy_digest), purposePolicy: hex(raw.purpose_policy),
                              rotationPolicy: hex(raw.rotation_policy), helper: pin(raw.helper), gui: pin(raw.gui), node: pin(raw.node))
    }
}
