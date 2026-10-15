import Foundation

struct CodePin: Equatable {
    let cdhash: String
    let sha256: String
}

struct PublicManifest: Equatable {
    static let service = "app.poolhouse.authority"
    let owner: UInt32
    let authority: String
    let helper: CodePin
    let gui: CodePin

    static func loadFixed() throws -> PublicManifest {
        var bytes = [UInt8](repeating: 0, count: 16384), count = 0
        guard ph_manifest_read(&bytes, bytes.count, &count) else { throw ApprovalFailure.unavailable }
        return try decode(Data(bytes.prefix(count)))
    }

    static func decode(_ bytes: Data) throws -> PublicManifest {
        var parser = ManifestJSON(bytes: Array(bytes))
        guard case let .object(root) = try parser.document(), Set(root.keys) == ["version", "owner_uid", "authority_id", "helper", "gui"],
              root["version"] == .integer(1), case let .integer(owner)? = root["owner_uid"], owner > 0, owner <= UInt32.max,
              case let .string(authority)? = root["authority_id"], ApprovalCard.hex(authority), authority.contains(where: { $0 != "0" }),
              case let .object(helper)? = root["helper"], Set(helper.keys) == ["service", "cdhash", "sha256"],
              helper["service"] == .string(service), case let .object(gui)? = root["gui"], Set(gui.keys) == ["cdhash", "sha256"] else {
            throw ApprovalFailure.invalid
        }
        func pin(_ fields: [String: ManifestValue]) throws -> CodePin {
            guard case let .string(cdhash)? = fields["cdhash"], ApprovalCard.hex(cdhash, length: 40),
                  case let .string(sha256)? = fields["sha256"], ApprovalCard.hex(sha256),
                  cdhash.contains(where: { $0 != "0" }), sha256.contains(where: { $0 != "0" }) else { throw ApprovalFailure.invalid }
            return CodePin(cdhash: cdhash, sha256: sha256)
        }
        return try PublicManifest(owner: UInt32(owner), authority: authority, helper: pin(helper), gui: pin(gui))
    }
}

private indirect enum ManifestValue: Equatable {
    case object([String: ManifestValue]), string(String), integer(UInt64)
}

private struct ManifestJSON {
    let bytes: [UInt8]
    var index = 0
    mutating func whitespace() { while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 } }
    mutating func take(_ expected: UInt8) throws {
        whitespace()
        guard index < bytes.count, bytes[index] == expected else { throw ApprovalFailure.invalid }
        index += 1
    }
    mutating func string() throws -> String {
        try take(34)
        let start = index
        while index < bytes.count && bytes[index] != 34 {
            let byte = bytes[index]
            guard (32...126).contains(byte), byte != 92, index - start < 128 else { throw ApprovalFailure.invalid }
            index += 1
        }
        guard index < bytes.count else { throw ApprovalFailure.invalid }
        let result = String(decoding: bytes[start..<index], as: UTF8.self)
        index += 1
        return result
    }
    mutating func value(depth: Int) throws -> ManifestValue {
        whitespace()
        guard index < bytes.count, depth <= 2 else { throw ApprovalFailure.invalid }
        if bytes[index] == 34 { return .string(try string()) }
        if bytes[index] == 123 {
            try take(123)
            var fields: [String: ManifestValue] = [:]
            whitespace()
            if index < bytes.count && bytes[index] == 125 { index += 1; return .object(fields) }
            while true {
                let key = try string()
                guard fields[key] == nil, fields.count < 8 else { throw ApprovalFailure.invalid }
                try take(58)
                fields[key] = try value(depth: depth + 1)
                whitespace()
                guard index < bytes.count else { throw ApprovalFailure.invalid }
                if bytes[index] == 125 { index += 1; return .object(fields) }
                try take(44)
            }
        }
        let start = index
        while index < bytes.count && (48...57).contains(bytes[index]) { index += 1 }
        guard index > start, index - start <= 10, !(index - start > 1 && bytes[start] == 48),
              let number = UInt64(String(decoding: bytes[start..<index], as: UTF8.self)) else { throw ApprovalFailure.invalid }
        return .integer(number)
    }
    mutating func document() throws -> ManifestValue {
        guard !bytes.isEmpty, bytes.count <= 16384 else { throw ApprovalFailure.invalid }
        let result = try value(depth: 0)
        whitespace()
        guard index == bytes.count else { throw ApprovalFailure.invalid }
        return result
    }
}
