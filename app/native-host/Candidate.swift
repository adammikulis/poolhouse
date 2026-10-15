import Foundation
import CryptoKit
import Darwin

enum CandidateFailure: Error { case invalid, unavailable }

struct InventoryRecord {
    let role: String
    let path: String
    let size: UInt64
    let mode: UInt32
    let digest: String
}

struct CandidateInventory {
    static let header = "poolhouse-native-candidate-v1\n"
    static let gui = "GUI/PoolhouseApproval.app/"
    static let helper = "Helper/PoolhouseAuthority"
    static let service = "Services/app.poolhouse.authority.plist"
    static let main = gui + "Contents/MacOS/PoolhouseApproval"
    static let info = gui + "Contents/Info.plist"
    static let seal = gui + "Contents/_CodeSignature/CodeResources"
    let records: [InventoryRecord]
    let bytes: Data
    var paths: String { records.map(\.path).joined(separator: "\n") + "\n" }

    init(_ bytes: Data) throws {
        guard !bytes.isEmpty, bytes.count <= 16384,
              bytes.allSatisfy({ $0 == 9 || $0 == 10 || (32...126).contains($0) }),
              let text = String(data: bytes, encoding: .ascii), text.hasPrefix(Self.header), text.hasSuffix("\n") else {
            throw CandidateFailure.invalid
        }
        let lines = text.dropFirst(Self.header.count).dropLast().split(separator: "\n", omittingEmptySubsequences: false)
        guard !lines.isEmpty, lines.count <= 64 else { throw CandidateFailure.invalid }
        var result: [InventoryRecord] = [], total: UInt64 = 0, previous = ""
        for line in lines {
            let fields = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
            guard fields.count == 5 else { throw CandidateFailure.invalid }
            let role = fields[0], path = fields[1]
            guard path > previous, Self.supported(role: role, path: path),
                  let size = UInt64(fields[2]), String(size) == fields[2], size <= 64 * 1024 * 1024,
                  fields[3] == "0644" || fields[3] == "0755", Self.hex(fields[4]) else { throw CandidateFailure.invalid }
            let addition = total.addingReportingOverflow(size)
            guard !addition.overflow, addition.partialValue <= 64 * 1024 * 1024 else { throw CandidateFailure.invalid }
            total = addition.partialValue
            let executable = path == Self.helper || path == Self.main
            guard fields[3] == (executable ? "0755" : "0644") else { throw CandidateFailure.invalid }
            result.append(InventoryRecord(role: role, path: path, size: size, mode: executable ? 0o755 : 0o644, digest: fields[4]))
            previous = path
        }
        let paths = Set(result.map(\.path))
        guard Set([Self.helper, Self.service, Self.main, Self.info, Self.seal]).isSubset(of: paths) else {
            throw CandidateFailure.invalid
        }
        records = result; self.bytes = bytes
    }

    private static func supported(role: String, path: String) -> Bool {
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        guard !parts.isEmpty, parts.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".."
            && $0.utf8.allSatisfy({ (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0)
                || [45, 46, 95].contains($0) }) }) else { return false }
        switch role {
        case "helper": return path == helper
        case "service": return path == service
        case "gui":
            if [main, info, seal].contains(path) { return true }
            let prefix = gui + "Contents/Resources/"
            return path.hasPrefix(prefix) && !path.dropFirst(prefix.count).contains("/") && path.count > prefix.count
        default: return false
        }
    }

    static func hex(_ value: String, count: Int = 64) -> Bool {
        value.utf8.count == count && value.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) })
    }
}

struct CapturedCandidate {
    let inventory: CandidateInventory
    private let files: [String: Data]
    let inventoryDigest: String

    init(directory: String, inventory: CandidateInventory) throws {
        let root = ph_host_open(directory, false)
        guard root >= 0 else { throw CandidateFailure.invalid }
        defer { close(root) }
        guard ph_host_tree(root, inventory.paths) else { throw CandidateFailure.invalid }
        var captured: [String: Data] = [:]
        for record in inventory.records {
            var bytes = [UInt8](repeating: 0, count: max(1, Int(record.size)))
            guard ph_host_read(root, record.path, record.size, record.mode, &bytes) else { throw CandidateFailure.invalid }
            let data = Data(bytes.prefix(Int(record.size)))
            guard Self.hash(data) == record.digest else { throw CandidateFailure.invalid }
            captured[record.path] = data
        }
        guard ph_host_tree(root, inventory.paths) else { throw CandidateFailure.invalid }
        self.inventory = inventory; files = captured; inventoryDigest = Self.hash(inventory.bytes)
    }

    static func hash(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }

    func publicReport() -> String {
        "Untrusted captured candidate; no installation or authority.\nInventory SHA256: \(inventoryDigest)\n"
            + inventory.records.map { "\($0.role)\t\($0.path)\t\($0.size)\t\($0.digest)" }.joined(separator: "\n") + "\n"
    }

    func checkProtectedCode(directory: String, helperCDHash: String, guiCDHash: String) throws {
        guard CandidateInventory.hex(helperCDHash, count: 40), CandidateInventory.hex(guiCDHash, count: 40),
              ph_host_protected_tree(directory, inventory.paths) else { throw CandidateFailure.unavailable }
        let reread = try CapturedCandidate(directory: directory, inventory: inventory)
        guard reread.files == files,
              ph_host_protected_code(directory, inventory.paths, helperCDHash, guiCDHash),
              ph_host_protected_tree(directory, inventory.paths) else { throw CandidateFailure.invalid }
        let after = try CapturedCandidate(directory: directory, inventory: inventory)
        guard after.files == files else { throw CandidateFailure.invalid }
    }
}
