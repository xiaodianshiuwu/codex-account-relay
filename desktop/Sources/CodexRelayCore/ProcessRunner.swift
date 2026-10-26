import Foundation
import Darwin

public struct RelayProcessOutput {
    public let stdout: String
    public let stderr: String
    public let exitCode: Int32
}

public enum RelayBackendError: LocalizedError {
    case binaryNotFound
    case launchFailed(String)
    case timeout(String)
    case commandFailed(Int32, String)
    case decoding(String)

    public var errorDescription: String? {
        switch self {
        case .binaryNotFound:
            return "找不到 codex-relay。请确认已运行 install.sh，或设置 CODEX_RELAY_BIN。"
        case .launchFailed(let message):
            return "无法启动 codex-relay：\(message)"
        case .timeout(let command):
            return "命令超时：\(command)"
        case .commandFailed(_, let message):
            return message.isEmpty ? "codex-relay failed" : message
        case .decoding(let message):
            return "无法解析 codex-relay 输出：\(message)"
        }
    }
}

private final class LockedData {
    private let lock = NSLock()
    private var storage = Data()

    func append(_ data: Data) {
        lock.lock()
        storage.append(data)
        lock.unlock()
    }

    func value() -> Data {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}

private final class LineReader {
    private let handle: FileHandle
    private let onLine: (String) -> Void
    private let completion: () -> Void
    private var buffer = Data()
    private let lock = NSLock()
    private var stopped = false

    init(handle: FileHandle, onLine: @escaping (String) -> Void, completion: @escaping () -> Void) {
        self.handle = handle; self.onLine = onLine; self.completion = completion
    }
    func stop() { lock.lock(); stopped = true; lock.unlock() }
    private var shouldStop: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
    func start() {
        let fd = handle.fileDescriptor
        _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)
        DispatchQueue.global(qos: .userInitiated).async {
            var bytes = [UInt8](repeating: 0, count: 8192)
            while true {
                let count = Darwin.read(fd, &bytes, bytes.count)
                if count > 0 {
                    self.buffer.append(contentsOf: bytes.prefix(count))
                    while let index = self.buffer.firstIndex(of: 0x0A) {
                        let line = String(decoding: self.buffer[..<index], as: UTF8.self)
                        self.buffer.removeSubrange(self.buffer.startIndex...index)
                        self.onLine(line)
                    }
                } else if count == 0 { break }
                else if errno == EINTR { continue }
                else if errno == EAGAIN || errno == EWOULDBLOCK {
                    if self.shouldStop { break }
                    usleep(10_000)
                } else { break }
            }
            if !self.buffer.isEmpty { self.onLine(String(decoding: self.buffer, as: UTF8.self)) }
            self.completion()
        }
    }
}

public enum ProcessRunner {
    public static func defaultEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let extras = [
            "\(home)/.local/bin",
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
        let current = env["PATH"] ?? ""
        var seen = Set<String>()
        let path = (extras + current.split(separator: ":").map(String.init))
            .filter { !$0.isEmpty && seen.insert($0).inserted }
            .joined(separator: ":")
        env["PATH"] = path
        env["HOME"] = home
        return env
    }

    public static func run(
        executable: String,
        arguments: [String],
        timeout: TimeInterval = 120,
        environment: [String: String] = [:]
    ) throws -> RelayProcessOutput {
        try runStreaming(
            executable: executable,
            arguments: arguments,
            timeout: timeout,
            environment: environment,
            onStdoutLine: { _ in },
            onStderrLine: { _ in }
        )
    }

    public static func runStreaming(
        executable: String,
        arguments: [String],
        timeout: TimeInterval = 180,
        environment: [String: String] = [:],
        onStart: ((Process) -> Void)? = nil,
        onStdoutLine: @escaping (String) -> Void,
        onStderrLine: @escaping (String) -> Void
    ) throws -> RelayProcessOutput {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.environment = defaultEnvironment().merging(environment) { _, new in new }
        process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser

        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe
        process.standardInput = FileHandle.nullDevice

        do {
            try process.run()
        } catch {
            throw RelayBackendError.launchFailed(error.localizedDescription)
        }
        onStart?(process)

        let stdoutBox = LockedData()
        let stderrBox = LockedData()
        let group = DispatchGroup()
        group.enter()
        group.enter()

        let stdoutReader = LineReader(handle: stdoutPipe.fileHandleForReading, onLine: { line in
            stdoutBox.append((line + "\n").data(using: .utf8) ?? Data())
            onStdoutLine(line)
        }, completion: {
            group.leave()
        })

        let stderrReader = LineReader(handle: stderrPipe.fileHandleForReading, onLine: { line in
            stderrBox.append((line + "\n").data(using: .utf8) ?? Data())
            onStderrLine(line)
        }, completion: {
            group.leave()
        })

        stdoutReader.start()
        stderrReader.start()
        let deadline = timeout > 0 ? ProcessInfo.processInfo.systemUptime + timeout : .infinity
        while process.isRunning && ProcessInfo.processInfo.systemUptime < deadline {
            usleep(50_000)
        }
        if process.isRunning {
            process.terminate()
            let graceDeadline = Date().addingTimeInterval(10)
            while process.isRunning && Date() < graceDeadline {
                usleep(50_000)
            }
            if process.isRunning {
                kill(process.processIdentifier, SIGKILL)
            }
            process.waitUntilExit()
            stdoutReader.stop(); stderrReader.stop()
            _ = group.wait(timeout: .now() + 2)
            throw RelayBackendError.timeout(URL(fileURLWithPath: executable).lastPathComponent + " " + (arguments.first ?? ""))
        }

        process.waitUntilExit()
        stdoutReader.stop(); stderrReader.stop()
        _ = group.wait(timeout: .now() + 2)

        let stdout = String(data: stdoutBox.value(), encoding: .utf8) ?? ""
        let stderr = String(data: stderrBox.value(), encoding: .utf8) ?? ""
        return RelayProcessOutput(stdout: stdout, stderr: stderr, exitCode: process.terminationStatus)
    }
}
