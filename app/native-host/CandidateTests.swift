#if TESTING
import Foundation

enum CandidateTests {
    static func require(_ value: Bool) { precondition(value) }
    static func reject(_ body: () throws -> Void) {
        do { try body(); preconditionFailure("Accepted invalid candidate") } catch { }
    }
    static func run() throws {
        require(ph_host_test_header(0xfeedfacf, 0x100000c, 0, 2))
        require(!ph_host_test_header(0xcafebabe, 0x100000c, 0, 2))
        require(!ph_host_test_header(0xfeedfacf, 0x1000007, 0, 2))
        require(!ph_host_test_header(0xfeedfacf, 0x100000c, 2, 2))
        require(!ph_host_test_header(0xfeedfacf, 0x100000c, 0, 6))
        require(ph_host_test_policy(0x10000, nil))
        require(!ph_host_test_policy(0, nil))
        for name in ["com.apple.security.cs.allow-dyld-environment-variables", "com.apple.security.cs.allow-jit",
                     "com.apple.security.cs.allow-unsigned-executable-memory", "com.apple.security.cs.disable-library-validation",
                     "com.apple.security.cs.disable-executable-page-protection", "com.apple.security.get-task-allow",
                     "get-task-allow", "com.apple.security.cs.debugger", "unknown.entitlement"] {
            require(!ph_host_test_policy(0x10000, name))
        }
        let paths = [CandidateInventory.helper, CandidateInventory.main, CandidateInventory.info, CandidateInventory.seal, CandidateInventory.service].sorted()
        func inventory(size: UInt64 = 0) -> Data {
            Data((CandidateInventory.header + paths.map {
                let role = $0 == CandidateInventory.helper ? "helper" : ($0 == CandidateInventory.service ? "service" : "gui")
                let mode = [$0 == CandidateInventory.helper, $0 == CandidateInventory.main].contains(true) ? "0755" : "0644"
                return "\(role)\t\($0)\t\(size)\t\(mode)\t\(String(repeating: "0", count: 64))\n"
            }.joined()).utf8)
        }
        let valid = inventory(), text = String(decoding: valid, as: UTF8.self)
        _ = try CandidateInventory(valid)
        for bad in [text + "\n", text.replacingOccurrences(of: "\t0\t", with: "\t00\t"),
                    text.replacingOccurrences(of: "\t0\t", with: "\t-1\t"),
                    text.replacingOccurrences(of: "Helper/PoolhouseAuthority", with: "Helper/../PoolhouseAuthority"),
                    text.replacingOccurrences(of: "0755", with: "0777"),
                    text.replacingOccurrences(of: "helper\t", with: "unknown\t"),
                    text.replacingOccurrences(of: "\n", with: "\r\n")] {
            reject { _ = try CandidateInventory(Data(bad.utf8)) }
        }
        reject { _ = try CandidateInventory(inventory(size: 64 * 1024 * 1024)) }
        reject { _ = try CandidateInventory(Data(repeating: 32, count: 16385)) }
        var boundary = text.split(separator: "\n").dropFirst().map(String.init)
        for index in 0..<59 {
            boundary.append("gui\t\(CandidateInventory.gui)Contents/Resources/file\(String(format: "%02d", index))\t0\t0644\t\(String(repeating: "0", count: 64))")
        }
        var remaining = 16384 - (CandidateInventory.header.utf8.count + boundary.reduce(0) { $0 + $1.utf8.count + 1 })
        for index in boundary.indices where boundary[index].contains("/Resources/") {
            let count = min(remaining, 150)
            boundary[index] = boundary[index].replacingOccurrences(of: "\t0\t", with: String(repeating: "x", count: count) + "\t0\t")
            remaining -= count
        }
        require(remaining == 0)
        let exact = CandidateInventory.header + boundary.sorted { $0.split(separator: "\t")[1] < $1.split(separator: "\t")[1] }.joined(separator: "\n") + "\n"
        require(exact.utf8.count == 16384)
        require(try CandidateInventory(Data(exact.utf8)).records.count == 64)
        let over = exact.replacingOccurrences(of: "Resources/file00", with: "Resources/file00z")
        require(over.utf8.count == 16385)
        reject { _ = try CandidateInventory(Data(over.utf8)) }
        print("Native host candidate protocol passed")
    }
}
#endif
