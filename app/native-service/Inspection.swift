import Darwin
import Foundation
import ServiceManagement

struct PassiveServiceObserver {
    private static let appIdentifier = "app.poolhouse.app"
    private static let plistName = "app.poolhouse.node.plist"

    // No path, service name, installation handle or authority is supplied by a caller.
    func observe() -> PassiveServiceData {
        let bundle = Bundle.main
        guard bundle.bundleIdentifier == Self.appIdentifier,
              bundle.bundleURL.pathExtension == "app",
              let executable = bundle.executableURL,
              executable.deletingLastPathComponent() == bundle.bundleURL.appendingPathComponent("Contents/MacOS"),
              bundle.bundleURL.resolvingSymlinksInPath() == bundle.bundleURL.standardizedFileURL else {
            return Self.result(.bindingUnavailable, .containingAppUnavailable)
        }
        let directory = open(bundle.bundleURL.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard directory >= 0 else { return Self.result(.unverified, .containingAppInspectionUnavailable) }
        var held = [directory]
        defer { for descriptor in held.reversed() { close(descriptor) } }
        var parent = directory
        for component in ["Contents", "Library", "LaunchDaemons"] {
            let next = openat(parent, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            guard next >= 0 else {
                return errno == ENOENT
                    ? Self.result(.plistAbsent, .plistAbsent)
                    : Self.result(.unverified, .locationUnverified)
            }
            held.append(next)
            parent = next
        }
        let descriptor = openat(parent, Self.plistName, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else {
            return errno == ENOENT
                ? Self.result(.plistAbsent, .plistAbsent)
                : Self.result(.unverified, .plistUnverified)
        }
        held.append(descriptor)
        // Bound allocation/parsing of untrusted bundled metadata. Oversize stays unverified;
        // this is a safety bound, never a user configuration or setup requirement.
        var original = stat()
        guard fstat(descriptor, &original) == 0,
              original.st_mode & S_IFMT == S_IFREG, original.st_nlink == 1,
              original.st_size > 0, original.st_size <= 65536 else {
            return Self.result(.unverified, .plistUnverified)
        }
        var bytes = [UInt8](repeating: 0, count: Int(original.st_size))
        var offset = 0
        let length = bytes.count
        while offset < length {
            let count = bytes.withUnsafeMutableBytes { buffer in
                pread(descriptor, buffer.baseAddress!.advanced(by: offset), length - offset, off_t(offset))
            }
            guard count > 0 else { return Self.result(.unverified, .plistReadUnavailable) }
            offset += count
        }
        guard let plist = try? PropertyListSerialization.propertyList(from: Data(bytes), format: nil) as? [String: Any],
              plist["Label"] as? String == "app.poolhouse.node" else {
            return Self.result(.unverified, .plistBindingMismatch)
        }
        // Passive diagnostics deliberately do not scan or validate the whole app bundle.
        // This fixed-context/plist observation cannot issue an installed scope or code proof.
        var current = stat()
        guard fstatat(parent, Self.plistName, &current, AT_SYMLINK_NOFOLLOW) == 0,
              current.st_dev == original.st_dev, current.st_ino == original.st_ino,
              current.st_size == original.st_size,
              current.st_mtimespec.tv_sec == original.st_mtimespec.tv_sec,
              current.st_mtimespec.tv_nsec == original.st_mtimespec.tv_nsec else {
            return Self.result(.unverified, .plistChanged)
        }
        if #available(macOS 13.0, *) {
            return Self.result(Self.availability(for: SMAppService.daemon(plistName: Self.plistName).status),
                               .eligibilityOnly)
        }
        return Self.result(.unknown, .statusUnavailable)
    }

    @available(macOS 13.0, *)
    static func availability(for status: SMAppService.Status) -> PassiveServiceAvailabilityTag {
        switch status {
        case .notRegistered: return .notRegistered
        case .enabled: return .registeredEligible
        case .requiresApproval: return .requiresConsent
        case .notFound: return .notFound
        @unknown default: return .unknown
        }
    }

    private static func result(_ availability: PassiveServiceAvailabilityTag, _ reason: PassiveServiceReasonCode) -> PassiveServiceData {
        var clock = timespec()
        guard clock_gettime(CLOCK_REALTIME, &clock) == 0, clock.tv_sec >= 0,
              clock.tv_nsec >= 0, clock.tv_nsec < 1_000_000_000 else {
            return PassiveServiceData(observedAt: 0, availability: .unknown, reason: .clockUnavailable)
        }
        let (seconds, overflow) = UInt64(clock.tv_sec).multipliedReportingOverflow(by: 1_000_000)
        let (micros, additionOverflow) = seconds.addingReportingOverflow(UInt64(clock.tv_nsec / 1000))
        guard !overflow, !additionOverflow else {
            return PassiveServiceData(observedAt: 0, availability: .unknown, reason: .clockUnavailable)
        }
        return PassiveServiceData(observedAt: micros, availability: availability, reason: reason)
    }
}
