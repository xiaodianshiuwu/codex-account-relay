import Foundation
import Darwin
import CoreFoundation

/// Shares the same auto.lock and atomic snapshots as the Python backend.
public enum AutoSettingsStore {
    public static let defaultLockRetries = 20
    public static let defaultLockRetryDelayMicros: useconds_t = 25_000

    public static var url: URL {
        let root = ProcessInfo.processInfo.environment["CODEX_RELAY_HOME"]
            .map { NSString(string: $0).expandingTildeInPath }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex-relay").path
        return URL(fileURLWithPath: root).appendingPathComponent("auto.json")
    }

    public static func load(at url: URL = url) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let values = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return values
    }

    public static func update(
        _ changes: [String: Any],
        at url: URL = url,
        maxLockRetries: Int = defaultLockRetries,
        retryDelayMicros: useconds_t = defaultLockRetryDelayMicros
    ) throws {
        let fm = FileManager.default
        let dir = url.deletingLastPathComponent()
        try fm.createDirectory(at: dir, withIntermediateDirectories: true,
                               attributes: [.posixPermissions: 0o700])
        let lockPath = url.deletingPathExtension().appendingPathExtension("lock").path
        let fd = Darwin.open(lockPath, O_CREAT | O_RDWR, 0o600)
        guard fd >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EACCES)
        }
        defer { Darwin.close(fd) }

        var locked = false
        let attempts = max(1, maxLockRetries + 1)
        for attempt in 0..<attempts {
            if flock(fd, LOCK_EX | LOCK_NB) == 0 {
                locked = true
                break
            }
            let code = errno
            if code != EWOULDBLOCK && code != EAGAIN && code != EBUSY && code != EINTR {
                throw POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO)
            }
            if attempt + 1 < attempts {
                usleep(retryDelayMicros)
            }
        }
        guard locked else { throw POSIXError(.EBUSY) }
        defer { flock(fd, LOCK_UN) }

        var values = load(at: url)
        values.merge(changes) { _, new in new }
        try validate(&values)

        var data = try JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys])
        data.append(0x0A)
        try atomicWritePrivate(data, to: url)
    }

    private static func validate(_ values: inout [String: Any]) throws {
        for key in ["enabled", "allow_chatgpt_restart"] {
            if let value = values[key] {
                guard let number = value as? NSNumber,
                      CFGetTypeID(number) == CFBooleanGetTypeID() else {
                    throw POSIXError(.EINVAL)
                }
            }
        }
        for key in ["interval", "remaining_threshold"] {
            if let value = values[key] {
                guard let number = value as? NSNumber,
                      CFGetTypeID(number) != CFBooleanGetTypeID(),
                      number.doubleValue.isFinite else {
                    throw POSIXError(.EINVAL)
                }
                if key == "interval" && number.doubleValue <= 0 { throw POSIXError(.EINVAL) }
                if key == "remaining_threshold" && !(0...100).contains(number.doubleValue) { throw POSIXError(.EINVAL) }
            }
        }
        if let strategy = values["strategy"] {
            guard let text = strategy as? String, text == "next" || text == "best" else {
                throw POSIXError(.EINVAL)
            }
        }
        for key in ["prompt", "thread_id"] {
            if let value = values[key] {
                guard let text = value as? String, !text.contains("\0") else {
                    throw POSIXError(.EINVAL)
                }
            }
        }
        if let thread = values["thread_id"] as? String, !thread.isEmpty {
            guard let parsed = UUID(uuidString: thread) else { throw POSIXError(.EINVAL) }
            values["thread_id"] = parsed.uuidString.lowercased()
        }
    }

    private static func atomicWritePrivate(_ data: Data, to url: URL) throws {
        let dir = url.deletingLastPathComponent()
        let tempName = ".\(url.lastPathComponent).\(UUID().uuidString).tmp"
        let tempURL = dir.appendingPathComponent(tempName)
        let tempFd = Darwin.open(tempURL.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
        guard tempFd >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        var closed = false
        var renamed = false
        defer {
            if !closed { Darwin.close(tempFd) }
            if !renamed { Darwin.unlink(tempURL.path) }
        }
        _ = fchmod(tempFd, 0o600)
        try data.withUnsafeBytes { rawBuffer in
            guard let base = rawBuffer.baseAddress else { return }
            var remaining = rawBuffer.count
            var offset = 0
            while remaining > 0 {
                let written = Darwin.write(tempFd, base.advanced(by: offset), remaining)
                if written < 0 {
                    let code = errno
                    if code == EINTR { continue }
                    throw POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO)
                }
                offset += written
                remaining -= written
            }
        }
        if fsync(tempFd) != 0 {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        if Darwin.close(tempFd) != 0 {
            closed = true
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        closed = true
        if Darwin.rename(tempURL.path, url.path) != 0 {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        renamed = true
    }
}
