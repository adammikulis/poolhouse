import Darwin
import Foundation
import Security
import ServiceManagement

// Mirrors the separate passive contract; no value is installation or caller proof.
enum PassiveServiceAvailability: String {
    case bindingUnavailable = "BindingUnavailable"
    case plistAbsent = "PlistAbsent"
    case unverified = "Unverified"
    case notRegistered = "NotRegistered"
    case registeredEligible = "RegisteredEligible"
    case requiresConsent = "RequiresConsent"
    case notFound = "NotFound"
    case unknown = "Unknown"
}

struct PassiveServiceObservation {
    let availability: PassiveServiceAvailability
    let observedAt: UInt64
    let reason: String
}

struct PassiveServiceObserver {
    private static let appIdentifier = "app.poolhouse.app"
    private static let plistName = "app.poolhouse.node.plist"

    // No path, service name, installation handle or authority is supplied by a caller.
    func observe() -> PassiveServiceObservation {
        let bundle = Bundle.main
        guard bundle.bundleIdentifier == Self.appIdentifier,
              bundle.bundleURL.pathExtension == "app",
              let executable = bundle.executableURL,
              executable.deletingLastPathComponent() == bundle.bundleURL.appendingPathComponent("Contents/MacOS"),
              bundle.bundleURL.resolvingSymlinksInPath() == bundle.bundleURL.standardizedFileURL else {
            return Self.result(.bindingUnavailable, "The designated containing app is unavailable.")
        }
        let directory = open(bundle.bundleURL.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard directory >= 0 else { return Self.result(.unverified, "The containing app cannot be inspected.") }
        var held = [directory]
        defer { for descriptor in held.reversed() { close(descriptor) } }
        var parent = directory
        for component in ["Contents", "Library", "LaunchDaemons"] {
            let next = openat(parent, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard next >= 0 else {
                return errno == ENOENT
                    ? Self.result(.plistAbsent, "The planned bundled service plist is absent.")
                    : Self.result(.unverified, "The bundled service location is unverified.")
            }
            held.append(next)
            parent = next
        }
        let descriptor = openat(parent, Self.plistName, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else {
            return errno == ENOENT
                ? Self.result(.plistAbsent, "The planned bundled service plist is absent.")
                : Self.result(.unverified, "The bundled service plist is unverified.")
        }
        held.append(descriptor)
        // Bound allocation/parsing of untrusted bundled metadata. Oversize stays unverified;
        // this is a safety bound, never a user configuration or setup requirement.
        var original = stat()
        guard fstat(descriptor, &original) == 0,
              original.st_mode & S_IFMT == S_IFREG, original.st_nlink == 1,
              original.st_size > 0, original.st_size <= 65536 else {
            return Self.result(.unverified, "The bundled service plist is unverified.")
        }
        var bytes = [UInt8](repeating: 0, count: Int(original.st_size))
        var offset = 0
        let length = bytes.count
        while offset < length {
            let count = bytes.withUnsafeMutableBytes { buffer in
                pread(descriptor, buffer.baseAddress!.advanced(by: offset), length - offset, off_t(offset))
            }
            guard count > 0 else { return Self.result(.unverified, "The bundled service plist cannot be read.") }
            offset += count
        }
        guard let plist = try? PropertyListSerialization.propertyList(from: Data(bytes), format: nil) as? [String: Any],
              plist["Label"] as? String == "app.poolhouse.node" else {
            return Self.result(.unverified, "The bundled service plist does not match its planned binding.")
        }
        // A consistent seal is not an approved signer, installed service, scope or launch right.
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(bundle.bundleURL as CFURL, [], &code) == errSecSuccess, let code else {
            return Self.result(.unverified, "The containing app seal is unverified.")
        }
        let flags = SecCSFlags(rawValue: kSecCSStrictValidate | kSecCSCheckNestedCode).union(.noNetworkAccess)
        guard SecStaticCodeCheckValidity(code, flags, nil) == errSecSuccess else {
            return Self.result(.unverified, "The containing app seal is unverified.")
        }
        var information: CFDictionary?
        guard SecCodeCopySigningInformation(code, [], &information) == errSecSuccess,
              let values = information as? [String: Any],
              values[kSecCodeInfoIdentifier as String] as? String == Self.appIdentifier else {
            return Self.result(.unverified, "The containing app signing label is inconsistent.")
        }
        var current = stat()
        guard fstatat(parent, Self.plistName, &current, AT_SYMLINK_NOFOLLOW) == 0,
              current.st_dev == original.st_dev, current.st_ino == original.st_ino,
              current.st_size == original.st_size,
              current.st_mtimespec.tv_sec == original.st_mtimespec.tv_sec,
              current.st_mtimespec.tv_nsec == original.st_mtimespec.tv_nsec else {
            return Self.result(.unverified, "The bundled service plist changed during inspection.")
        }
        if #available(macOS 13.0, *) {
            return Self.result(Self.availability(for: SMAppService.daemon(plistName: Self.plistName).status),
                               "Registration eligibility only; installation and runtime readiness are unverified.")
        }
        return Self.result(.unknown, "Native service registration observation is unavailable.")
    }

    @available(macOS 13.0, *)
    static func availability(for status: SMAppService.Status) -> PassiveServiceAvailability {
        switch status {
        case .notRegistered: return .notRegistered
        case .enabled: return .registeredEligible
        case .requiresApproval: return .requiresConsent
        case .notFound: return .notFound
        @unknown default: return .unknown
        }
    }

    private static func result(_ availability: PassiveServiceAvailability, _ reason: String) -> PassiveServiceObservation {
        var clock = timespec()
        guard clock_gettime(CLOCK_REALTIME, &clock) == 0, clock.tv_sec >= 0,
              clock.tv_nsec >= 0, clock.tv_nsec < 1_000_000_000 else {
            return PassiveServiceObservation(availability: .unknown, observedAt: 0, reason: "The observation clock is unavailable.")
        }
        let (seconds, overflow) = UInt64(clock.tv_sec).multipliedReportingOverflow(by: 1_000_000)
        let (micros, additionOverflow) = seconds.addingReportingOverflow(UInt64(clock.tv_nsec / 1000))
        guard !overflow, !additionOverflow else {
            return PassiveServiceObservation(availability: .unknown, observedAt: 0, reason: "The observation clock is unavailable.")
        }
        return PassiveServiceObservation(availability: availability, observedAt: micros, reason: reason)
    }
}
