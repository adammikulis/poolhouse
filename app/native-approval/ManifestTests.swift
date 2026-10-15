#if TESTING
import Foundation
import Darwin
import XPC

struct ManifestTests {
    static func run() throws {
        let cd = String(repeating: "1", count: 40), hash = String(repeating: "2", count: 64)
        let json = "{\"version\":1,\"owner_uid\":501,\"authority_id\":\"\(hash)\",\"helper\":{\"service\":\"app.poolhouse.authority\",\"cdhash\":\"\(cd)\",\"sha256\":\"\(hash)\"},\"gui\":{\"cdhash\":\"\(cd)\",\"sha256\":\"\(hash)\"}}"
        let manifest = try PublicManifest.decode(Data(json.utf8))
        precondition(manifest.owner == 501 && manifest.helper.cdhash == cd && manifest.authority == hash)
        func refused(_ body: () throws -> Void) { do { try body(); preconditionFailure("Accepted bad manifest") } catch {} }
        for changed in [json.replacingOccurrences(of: "\"version\":1", with: "\"version\":1,\"version\":1"),
                        json.replacingOccurrences(of: "\"owner_uid\":501", with: "\"owner_uid\":true"),
                        json.replacingOccurrences(of: "\"owner_uid\":501", with: "\"owner_uid\":0"),
                        json.replacingOccurrences(of: "app.poolhouse.authority", with: "app.poolhouse.user-service"),
                        json.replacingOccurrences(of: "\"version\":1", with: "\"version\":1.0"),
                        json.replacingOccurrences(of: "\"version\":1", with: "\"version\":01"),
                        json.replacingOccurrences(of: "\"version\":1", with: "\"version\":1,\"namespace\":\"user\""),
                        json.replacingOccurrences(of: cd, with: String(repeating: "A", count: 40)),
                        json + "{}", String(repeating: " ", count: 16385)] {
            refused { _ = try PublicManifest.decode(Data(changed.utf8)) }
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
        precondition(!ph_message_peer(message, cd))
        var asid: UInt32 = 0
        precondition(!ph_local_session(0, &asid))
        print("Manifest schema, descriptor boundaries, and unattested message refusal passed")
    }
}
#endif
