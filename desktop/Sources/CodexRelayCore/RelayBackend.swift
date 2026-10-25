import Foundation

public final class RelayBackend: @unchecked Sendable {
    public let binaryPath: String?

    private let lock = NSLock()
    private var activeAddProcess: Process?

    public init(binaryPath: String? = nil) {
        self.binaryPath = binaryPath ?? RelayBackend.discoverBinary()
    }

    public static func discoverBinary() -> String? {
        if let override = ProcessInfo.processInfo.environment["CODEX_RELAY_BIN"],
           FileManager.default.isExecutableFile(atPath: override) {
            return override
        }
        // The release app ships its Python backend inside the bundle so a
        // fresh download works without a separate pip/wheel install.
        if let bundled = Bundle.main.path(forResource: "codex-relay", ofType: nil),
           FileManager.default.isExecutableFile(atPath: bundled) {
            return bundled
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            "\(home)/.local/bin/codex-relay",
            "/opt/homebrew/bin/codex-relay",
            "/usr/local/bin/codex-relay",
        ]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        if let path = ProcessInfo.processInfo.environment["PATH"] {
            for component in path.split(separator: ":") {
                let candidate = "\(component)/codex-relay"
                if FileManager.default.isExecutableFile(atPath: candidate) {
                    return candidate
                }
            }
        }
        return nil
    }

    public var isAvailable: Bool { binaryPath != nil }

    private func requireBinary() throws -> String {
        guard let binaryPath else { throw RelayBackendError.binaryNotFound }
        return binaryPath
    }

    private func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    private var cliEnvironment: [String: String] {
        // AppPreferences stores the selected language in UserDefaults; the
        // core reads the same key so CLI diagnostics and human-readable output
        // always follow the app language.
        if let language = UserDefaults.standard.string(forKey: "relay.language"), !language.isEmpty {
            return ["CODEX_RELAY_LANG": language]
        }
        return [:]
    }

    public func run(_ arguments: [String], timeout: TimeInterval = 120) throws -> String {
        let binary = try requireBinary()
        let output = try ProcessRunner.run(
            executable: binary,
            arguments: arguments,
            timeout: timeout,
            environment: cliEnvironment
        )
        guard output.exitCode == 0 else {
            let message = output.stderr.isEmpty ? output.stdout : output.stderr
            throw RelayBackendError.commandFailed(output.exitCode, message.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return output.stdout
    }

    public func runJSON<T: Decodable>(_ type: T.Type, _ arguments: [String], timeout: TimeInterval = 120) throws -> T {
        let text = try run(arguments, timeout: timeout)
        guard let data = text.data(using: .utf8), !data.isEmpty else {
            throw RelayBackendError.decoding("输出为空")
        }
        do {
            return try decoder().decode(T.self, from: data)
        } catch {
            throw RelayBackendError.decoding("\(error.localizedDescription)\n\(text.prefix(1200))")
        }
    }

    public func listAccounts(refresh: Bool = false) throws -> [RelayAccount] {
        var args = ["list", "--json"]
        if refresh { args.append("--refresh") }
        let result = try runJSON(RelayAccountList.self, args, timeout: refresh ? 300 : 30)
        return result.profiles
    }

    public func status() throws -> RelayStatus {
        try runJSON(RelayStatus.self, ["status", "--json"], timeout: 30)
    }

    public func usage(alias: String? = nil) throws -> [RelayAccount] {
        var args: [String] = ["usage"]
        if let alias, !alias.isEmpty {
            args.append(alias)
        }
        args.append("--json")
        let result = try runJSON(RelayAccountList.self, args, timeout: 180)
        return result.profiles
    }

    public func sessions(limit: Int = 80, search: String? = nil) throws -> [RelaySession] {
        var args = ["sessions", "--json", "--limit", "\(max(1, min(limit, 300)))"]
        if let search, !search.isEmpty {
            args.append("--search")
            args.append(search)
        }
        let result = try runJSON(RelaySessionList.self, args, timeout: 60)
        return result.threads
    }

    public func doctor() throws -> RelayDoctorReport {
        try runJSON(RelayDoctorReport.self, ["doctor", "--json"], timeout: 180)
    }

    public func inspect(threadID: String) throws -> RelayInspectReport {
        try runJSON(RelayInspectReport.self, ["inspect", threadID, "--json"], timeout: 180)
    }

    public func setDisplayName(alias: String, displayName: String) throws -> RelayAccount {
        let result = try runJSON(
            RelayAccountLabelResult.self,
            ["label", alias, displayName, "--json"],
            timeout: 30
        )
        return result.profile
    }

    public func captureCurrent(alias: String) throws {
        let output = try run(["add", "--capture", alias, "--json"], timeout: 30)
        if let data = output.data(using: .utf8),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           object["ok"] as? Bool == true {
            return
        }
        // A successful CLI call without a parseable result is still acceptable;
        // the caller refreshes accounts from disk.
    }

    public func switchAccount(
        alias: String,
        threadID: String,
        allowRestart: Bool,
        progress: @escaping (RelayProgressEvent) -> Void
    ) throws -> RelaySwitchResult {
        let binary = try requireBinary()
        var args = [
            "switch", alias,
            "--desktop",
            "--thread", threadID,
            "--json",
            "--progress",
        ]
        if allowRestart {
            args.append("--allow-chatgpt-restart")
        }
        let output = try ProcessRunner.runStreaming(
            executable: binary,
            arguments: args,
            timeout: 180,
            onStdoutLine: { _ in },
            onStderrLine: { line in
                guard let data = line.data(using: .utf8),
                      let event = try? self.decoder().decode(RelayProgressEvent.self, from: data) else {
                    return
                }
                progress(event)
            }
        )
        guard output.exitCode == 0 else {
            throw RelayBackendError.commandFailed(
                output.exitCode,
                output.stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        }
        do {
            return try decoder().decode(RelaySwitchResult.self, from: Data(output.stdout.utf8))
        } catch {
            throw RelayBackendError.decoding("\(error.localizedDescription)\n\(output.stdout.prefix(1200))")
        }
    }

    public func restart(
        threadID: String,
        allowRestart: Bool,
        progress: @escaping (RelayProgressEvent) -> Void
    ) throws -> RelaySwitchResult {
        let binary = try requireBinary()
        var args = ["restart", "--thread", threadID, "--json", "--progress"]
        if allowRestart {
            args.append("--allow-chatgpt-restart")
        }
        let output = try ProcessRunner.runStreaming(
            executable: binary,
            arguments: args,
            timeout: 180,
            onStdoutLine: { _ in },
            onStderrLine: { line in
                guard let data = line.data(using: .utf8),
                      let event = try? self.decoder().decode(RelayProgressEvent.self, from: data) else {
                    return
                }
                progress(event)
            }
        )
        guard output.exitCode == 0 else {
            throw RelayBackendError.commandFailed(
                output.exitCode,
                output.stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        }
        do {
            return try decoder().decode(RelaySwitchResult.self, from: Data(output.stdout.utf8))
        } catch {
            throw RelayBackendError.decoding("\(error.localizedDescription)\n\(output.stdout.prefix(1200))")
        }
    }

    public func addAccount(
        alias: String,
        deviceAuth: Bool,
        onLine: @escaping (String) -> Void
    ) throws {
        let binary = try requireBinary()
        var args = ["add", alias]
        if deviceAuth { args.append("--device") }
        defer {
            lock.lock()
            activeAddProcess = nil
            lock.unlock()
        }
        let output = try ProcessRunner.runStreaming(
            executable: binary,
            arguments: args,
            timeout: 660,
            onStart: { process in
                self.lock.lock()
                self.activeAddProcess = process
                self.lock.unlock()
            },
            onStdoutLine: { line in
                DispatchQueue.main.async { onLine(line) }
            },
            onStderrLine: { line in
                DispatchQueue.main.async { onLine(line) }
            }
        )
        guard output.exitCode == 0 else {
            throw RelayBackendError.commandFailed(
                output.exitCode,
                output.stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        }
    }

    public func cancelAdd() {
        lock.lock()
        let process = activeAddProcess
        lock.unlock()
        process?.terminate()
    }

    /// Opens a thread through the CLI so the explicit ChatGPT permission flag
    /// and the process-control guard are applied before any deep link is sent.
    public func resumeThread(_ threadID: String, allowRestart: Bool) throws {
        var args = ["resume", threadID, "--desktop", "--json"]
        if allowRestart { args.append("--allow-chatgpt-restart") }
        _ = try run(args, timeout: 45)
    }

    /// Opens the most recent thread through the CLI. Used for the explicit
    /// "open ChatGPT" action; it never launches the app behind the user's back.
    public func resumeLast(allowRestart: Bool) throws {
        var args = ["resume", "--last", "--desktop", "--json"]
        if allowRestart { args.append("--allow-chatgpt-restart") }
        _ = try run(args, timeout: 45)
    }

    public func openPath(_ path: String) throws {
        let output = try ProcessRunner.run(
            executable: "/usr/bin/open",
            arguments: [path],
            timeout: 10
        )
        guard output.exitCode == 0 else {
            throw RelayBackendError.commandFailed(output.exitCode, output.stderr)
        }
    }

    public func versionLine() -> String {
        (try? run(["--version"], timeout: 15).trimmingCharacters(in: .whitespacesAndNewlines)) ?? "未知"
    }
}
