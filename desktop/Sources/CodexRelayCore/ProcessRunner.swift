import Foundation

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

    init(handle: FileHandle, onLine: @escaping (String) -> Void, completion: @escaping () -> Void) {
        self.handle = handle
        self.onLine = onLine
        self.completion = completion
    }

    func start() {
        DispatchQueue.global(qos: .userInitiated).async {
            while true {
                let chunk: Data?
                do {
                    chunk = try self.handle.read(upToCount: 4096)
                } catch {
                    break
                }
                guard let chunk, !chunk.isEmpty else { break }
                self.buffer.append(chunk)
                while let index = self.buffer.firstIndex(of: 0x0A) {
                    let lineData = self.buffer[..<index]
                    self.buffer.removeSubrange(self.buffer.startIndex...index)
                    if let line = String(data: lineData, encoding: .utf8) {
                        self.onLine(line)
                    }
                }
            }
            if !self.buffer.isEmpty, let line = String(data: self.buffer, encoding: .utf8), !line.isEmpty {
                self.onLine(line)
            }
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

        LineReader(handle: stdoutPipe.fileHandleForReading, onLine: { line in
            stdoutBox.append((line + "\n").data(using: .utf8) ?? Data())
            onStdoutLine(line)
        }, completion: {
            group.leave()
        }).start()

        LineReader(handle: stderrPipe.fileHandleForReading, onLine: { line in
            stderrBox.append((line + "\n").data(using: .utf8) ?? Data())
            onStderrLine(line)
        }, completion: {
            group.leave()
        }).start()

        let deadline = Date().addingTimeInterval(timeout)
        while process.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if process.isRunning {
            process.terminate()
            let graceDeadline = Date().addingTimeInterval(2)
            while process.isRunning && Date() < graceDeadline {
                usleep(50_000)
            }
            if process.isRunning {
                kill(process.processIdentifier, SIGKILL)
            }
            group.wait()
            throw RelayBackendError.timeout("\(executable) " + arguments.joined(separator: " "))
        }

        process.waitUntilExit()
        group.wait()

        let stdout = String(data: stdoutBox.value(), encoding: .utf8) ?? ""
        let stderr = String(data: stderrBox.value(), encoding: .utf8) ?? ""
        return RelayProcessOutput(stdout: stdout, stderr: stderr, exitCode: process.terminationStatus)
    }
}
