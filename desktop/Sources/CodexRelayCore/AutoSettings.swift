import Foundation
import Darwin
import CoreFoundation

/// Shares the same auto.lock and atomic snapshots as the Python backend.
public enum AutoSettingsStore {
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
    public static func update(_ changes: [String: Any], at url: URL = url) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                               attributes: [.posixPermissions: 0o700])
        let fd = Darwin.open(url.deletingPathExtension().appendingPathExtension("lock").path, O_CREAT | O_RDWR, 0o600)
        guard fd >= 0 else { throw POSIXError(.EACCES) }
        defer { Darwin.close(fd) }
        // Do not block the main actor behind another settings writer.
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { throw POSIXError(.EBUSY) }
        defer { flock(fd, LOCK_UN) }
        var values = load(at: url)
        values.merge(changes) { _, new in new }
        for key in ["interval", "remaining_threshold"] {
            if let value = values[key] {
                guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite else { throw POSIXError(.EINVAL) }
                if key == "interval" && number.doubleValue <= 0 { throw POSIXError(.EINVAL) }
                if key == "remaining_threshold" && !(0...100).contains(number.doubleValue) { throw POSIXError(.EINVAL) }
            }
        }
        if let thread = values["thread_id"] as? String, !thread.isEmpty, UUID(uuidString: thread) == nil { throw POSIXError(.EINVAL) }
        let data = try JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url, options: [.atomic])
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}
