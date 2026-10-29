#if TESTING
import Foundation
import Darwin
import XPC

struct ManifestTests {
    static func run() throws {
        let cd = String(repeating: "1", count: 40), hash = String(repeating: "2", count: 64)
        let install = String(repeating: "3", count: 64), publicKey = String(repeating: "4", count: 64)
        let requirement = String(repeating: "5", count: 64), entitlements = String(repeating: "6", count: 64)
        let purpose = String(repeating: "7", count: 64), rotation = String(repeating: "8", count: 64)
        let publisher = String(repeating: "9", count: 64)
        let policy = "6d4d6a0199d218c49fca231d29665755af64bab10f463eb43f442d3c16462473"
        let pin = "\"cdhash\":\"\(cd)\",\"sha256\":\"\(hash)\",\"publisher_kind\":1,\"publisher_digest\":\"\(publisher)\",\"requirement_digest\":\"\(requirement)\",\"entitlements_digest\":\"\(entitlements)\""
        let json = "{\"version\":2,\"owner_uid\":501,\"authority_id\":\"\(hash)\",\"installation_id\":\"\(install)\",\"generation\":1,\"receipt_algorithm\":\"ed25519\",\"receipt_public_key\":\"\(publicKey)\",\"policy_digest\":\"\(policy)\",\"purpose_policy\":\"\(purpose)\",\"rotation_policy\":\"\(rotation)\",\"helper\":{\"service\":\"app.poolhouse.authority\",\(pin)},\"gui\":{\(pin)},\"node\":{\(pin)}}"

        let manifest = try ManifestFields.decode(Data(json.utf8))
        precondition(manifest.owner == 501 && manifest.helper.cdhash == cd && manifest.authority == hash
                     && manifest.installation == install && manifest.generation == 1 && manifest.receiptPublicKey == publicKey
                     && manifest.node.cdhash == cd && manifest.policyDigest == policy)
        let localPolicy = "cef90319d5a94f35a4c03eae9c306a39558dbd987ee984537c19dbfb9c0fc424"
        let local = json.replacingOccurrences(of: "\"publisher_kind\":1", with: "\"publisher_kind\":2")
                        .replacingOccurrences(of: policy, with: localPolicy)
        let localManifest = try ManifestFields.decode(Data(local.utf8))
        precondition(localManifest.helper.publisherKind == 2 && localManifest.policyDigest == localPolicy)
        let last = json.replacingOccurrences(of: "\"generation\":1", with: "\"generation\":18446744073709551615")
        let lastManifest = try ManifestFields.decode(Data(last.utf8))
        precondition(lastManifest.generation == UInt64.max)
        func refused(_ body: () throws -> Void) { do { try body(); preconditionFailure("Accepted bad manifest") } catch {} }
        for changed in [json.replacingOccurrences(of: "\"version\":2", with: "\"version\":2,\"version\":2"),
                        json.replacingOccurrences(of: "\"owner_uid\":501", with: "\"owner_uid\":true"),
                        json.replacingOccurrences(of: "\"owner_uid\":501", with: "\"owner_uid\":0"),
                        json.replacingOccurrences(of: "\"owner_uid\":501", with: "\"owner_uid\":4294967295"),
                        json.replacingOccurrences(of: "app.poolhouse.authority", with: "app.poolhouse.user-service"),
                        json.replacingOccurrences(of: "\"version\":2", with: "\"version\":2.0"),
                        json.replacingOccurrences(of: "\"version\":2", with: "\"version\":02"),
                        json.replacingOccurrences(of: "\"version\":2", with: "\"version\":2,\"namespace\":\"user\""),
                        json.replacingOccurrences(of: "\"generation\":1", with: "\"generation\":0"),
                        json.replacingOccurrences(of: "\"generation\":1", with: "\"generation\":18446744073709551616"),
                        json.replacingOccurrences(of: "\"generation\":1", with: "\"generation\":-1"),
                        json.replacingOccurrences(of: "\"generation\":1", with: "\"generation\":true"),
                        json.replacingOccurrences(of: "ed25519", with: "p256"),
                        json.replacingOccurrences(of: "\"publisher_kind\":1", with: "\"publisher_kind\":2"),
                        json.replacingOccurrences(of: "\"publisher_kind\":1", with: "\"publisher_kind\":0"),
                        json.replacingOccurrences(of: "\"publisher_kind\":1", with: "\"publisher_kind\":3"),
                        json.replacingOccurrences(of: publisher, with: String(repeating: "0", count: 64)),
                        json.replacingOccurrences(of: requirement, with: String(repeating: "9", count: 64)),
                        json.replacingOccurrences(of: purpose, with: String(repeating: "0", count: 64)),
                        json.replacingOccurrences(of: policy, with: String(repeating: "0", count: 64)),
                        json.replacingOccurrences(of: publicKey, with: String(repeating: "0", count: 64)),
                        json.replacingOccurrences(of: install, with: String(repeating: "0", count: 64)),
                        json.replacingOccurrences(of: cd, with: String(repeating: "A", count: 40)),
                        json + "{}", String(repeating: " ", count: 16385)] {
            refused { _ = try ManifestFields.decode(Data(changed.utf8)) }
        }
        let folder = URL(fileURLWithPath: ProcessInfo.processInfo.environment["TMPDIR"]!).appendingPathComponent("manifest-fixture")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false)
        precondition(chmod(folder.path, 0o755) == 0)
        let file = folder.appendingPathComponent("manifest.json")
        try Data(json.utf8).write(to: file)
        precondition(chmod(file.path, 0o644) == 0)
        let fd = open(folder.path, O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        precondition(fd >= 0)
        defer { close(fd) }
        func reads() -> Bool {
            var bytes = [UInt8](repeating: 0, count: 16384), length = 0
            let result = ph_manifest_fixture(fd, getuid(), &bytes, bytes.count, &length)
            if result { precondition(Data(bytes.prefix(length)) == Data(json.utf8)) }
            return result
        }
        precondition(reads())
        var wrongBytes = [UInt8](repeating: 0, count: 16384), wrongLength = 0
        precondition(!ph_manifest_fixture(fd, getuid() ^ 1, &wrongBytes, wrongBytes.count, &wrongLength))
        precondition(chmod(file.path, 0o664) == 0); precondition(!reads())
        precondition(chmod(file.path, 0o644) == 0)
        precondition(chmod(folder.path, 0o775) == 0); precondition(!reads())
        precondition(chmod(folder.path, 0o755) == 0)
        let alias = folder.appendingPathComponent("alias")
        precondition(link(file.path, alias.path) == 0); precondition(!reads())
        precondition(unlink(alias.path) == 0)
        precondition(rename(file.path, alias.path) == 0)
        precondition(symlink(alias.path, file.path) == 0); precondition(!reads())
        precondition(unlink(file.path) == 0)
        precondition(mkfifo(file.path, 0o644) == 0); precondition(!reads())
        precondition(unlink(file.path) == 0)
        precondition(rename(alias.path, file.path) == 0); precondition(reads())
        let fileFD = open(file.path, O_RDONLY | O_CLOEXEC)
        precondition(fileFD >= 0)
        defer { close(fileFD) }
        precondition(ph_manifest_fixture_acl(fileFD, false)); precondition(reads())
        precondition(ph_manifest_fixture_acl(fileFD, true)); precondition(!reads())
        let message = xpc_dictionary_create(nil, nil, 0)
        precondition(!ph_message_caller(message, cd))
        var asid: UInt32 = 0
        precondition(!ph_local_session(0, &asid))
        print("Manifest schema, descriptor boundaries, and unattested message refusal passed")
    }
}
#endif
