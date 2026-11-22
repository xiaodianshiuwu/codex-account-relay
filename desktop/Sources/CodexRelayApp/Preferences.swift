import AppKit
import CodexRelayCore
import Foundation
import SwiftUI

enum QuotaDisplayMode: String, CaseIterable, Identifiable {
    case remaining
    case used

    var id: String { rawValue }

    var title: String {
        switch self {
        case .remaining: return "剩余额度"
        case .used: return "已使用"
        }
    }

    var shortTitle: String {
        switch self {
        case .remaining: return "剩余"
        case .used: return "已用"
        }
    }

    var explanation: String {
        switch self {
        case .remaining: return "显示还有多少可用额度，第一眼更直观。"
        case .used: return "显示 5 小时 / 每周窗口已经消耗了多少。"
        }
    }
}

enum NumberFormatMode: String, CaseIterable, Identifiable {
    case full
    case compact

    var id: String { rawValue }

    var title: String {
        switch self {
        case .full: return "完整数值"
        case .compact: return "简写"
        }
    }

    var explanation: String {
        switch self {
        case .full: return "显示完整数字，例如 1,234,567。"
        case .compact: return "使用 K / M / B 简写，例如 1.2M。"
        }
    }
}

@MainActor
enum AppAppearance {
    /// Apply the selected theme to AppKit as well as SwiftUI. The static
    /// `NSColor` dynamic providers used by the HUD resolve against the
    /// NSAppearance, so setting only `.preferredColorScheme` can leave some
    /// windows/components on the previous appearance until another redraw.
    static func apply(_ appearance: RelayAppearance) {
        let resolved: NSAppearance?
        switch appearance {
        case .dark:
            resolved = NSAppearance(named: .darkAqua)
        case .light:
            resolved = NSAppearance(named: .aqua)
        case .system:
            resolved = nil
        }
        NSApp?.appearance = resolved
        NSApp?.windows.forEach { window in
            window.appearance = resolved
        }
    }
}

@MainActor
final class AppPreferences: ObservableObject {
    private enum Keys {
        static let quotaDisplay = "relay.quotaDisplay"
        static let numberFormat = "relay.numberFormat"
        static let appearance = "relay.appearance"
        static let language = "relay.language"
        static let autoEnabled = "relay.autoEnabled"
        static let autoPrompt = "relay.autoPrompt"
        static let autoRemaining = "relay.autoRemaining"
    }

    private let defaults = ProcessInfo.processInfo.environment["CODEX_RELAY_PREFS_SUITE"]
        .flatMap(UserDefaults.init(suiteName:)) ?? .standard

    @Published var quotaDisplayMode: QuotaDisplayMode {
        didSet { defaults.set(quotaDisplayMode.rawValue, forKey: Keys.quotaDisplay) }
    }
    @Published var numberFormatMode: NumberFormatMode {
        didSet { defaults.set(numberFormatMode.rawValue, forKey: Keys.numberFormat) }
    }
    @Published var appearance: RelayAppearance {
        didSet {
            defaults.set(appearance.rawValue, forKey: Keys.appearance)
            AppAppearance.apply(appearance)
        }
    }
    @Published var language: AppLanguage {
        didSet {
            defaults.set(language.rawValue, forKey: Keys.language)
            L10n.language = language
        }
    }

    private enum AutoSettingValue: Sendable {
        case bool(Bool)
        case double(Double)
        case string(String)

        var anyValue: Any {
            switch self {
            case .bool(let v): return v
            case .double(let v): return v
            case .string(let v): return v
            }
        }
    }

    private let autoWriteQueue = DispatchQueue(label: "com.codexrelay.autosettings", qos: .userInitiated)
    private var isRollingBack = false
    private var lastPersisted: [String: AutoSettingValue] = [:]
    private var keyGeneration: [String: Int] = [:]
    private var debounceTasks: [String: Task<Void, Never>] = [:]
    private var pendingDebouncedValues: [String: AutoSettingValue] = [:]
    private var inflightSaves = 0
    private var pendingSaveWaiters: [CheckedContinuation<Void, Never>] = []

    var hasPendingAutoSettingsSave: Bool {
        inflightSaves > 0 || !debounceTasks.isEmpty
    }

    @Published var settingsError: String?
    @Published var autoContinueEnabled: Bool {
        didSet { schedulePersist("enabled", .bool(autoContinueEnabled), debounced: false) }
    }
    @Published var autoContinuePrompt: String {
        didSet { schedulePersist("prompt", .string(autoContinuePrompt), debounced: true) }
    }
    @Published var autoRemainingThreshold: Double {
        didSet { schedulePersist("remaining_threshold", .double(autoRemainingThreshold), debounced: true) }
    }
    @Published var autoInterval: Double {
        didSet { schedulePersist("interval", .double(autoInterval), debounced: true) }
    }
    @Published var autoStrategy: String {
        didSet { schedulePersist("strategy", .string(autoStrategy), debounced: false) }
    }
    @Published var autoAllowRestart: Bool {
        didSet { schedulePersist("allow_chatgpt_restart", .bool(autoAllowRestart), debounced: false) }
    }
    @Published var autoThreadID: String {
        didSet { schedulePersist("thread_id", .string(autoThreadID), debounced: false) }
    }

    private func schedulePersist(_ key: String, _ value: AutoSettingValue, debounced: Bool) {
        guard !isRollingBack else { return }
        debounceTasks[key]?.cancel()
        debounceTasks.removeValue(forKey: key)
        pendingDebouncedValues.removeValue(forKey: key)
        let generation = (keyGeneration[key] ?? 0) + 1
        keyGeneration[key] = generation

        if debounced {
            pendingDebouncedValues[key] = value
            debounceTasks[key] = Task { @MainActor [weak self] in
                do {
                    try await Task.sleep(nanoseconds: 300_000_000)
                } catch {
                    return
                }
                guard let self else { return }
                self.debounceTasks.removeValue(forKey: key)
                self.pendingDebouncedValues.removeValue(forKey: key)
                await self.commitPersist(key, value, generation: generation)
            }
        } else {
            Task { @MainActor [weak self] in
                await self?.commitPersist(key, value, generation: generation)
            }
        }
    }

    private func commitPersist(_ key: String, _ value: AutoSettingValue, generation: Int) async {
        guard keyGeneration[key] == generation else {
            notifyPendingSaveWaitersIfIdle()
            return
        }
        inflightSaves += 1
        let writeQueue = self.autoWriteQueue
        let writeError: Error? = await withCheckedContinuation { continuation in
            writeQueue.async {
                do {
                    try AutoSettingsStore.update([key: value.anyValue])
                    continuation.resume(returning: nil)
                } catch {
                    continuation.resume(returning: error)
                }
            }
        }
        inflightSaves = max(0, inflightSaves - 1)
        if let error = writeError {
            settingsError = L10n.text("设置保存失败") + ": " + error.localizedDescription
            if keyGeneration[key] == generation {
                rollback(key)
            }
        } else {
            lastPersisted[key] = value
            if keyGeneration[key] == generation {
                settingsError = nil
            }
        }
        notifyPendingSaveWaitersIfIdle()
    }

    private static func strictBool(_ raw: Any?) -> Bool? {
        guard let num = raw as? NSNumber, CFGetTypeID(num) == CFBooleanGetTypeID() else { return nil }
        return num.boolValue
    }

    private static func strictDouble(_ raw: Any?) -> Double? {
        guard let num = raw as? NSNumber, CFGetTypeID(num) != CFBooleanGetTypeID(), num.doubleValue.isFinite else { return nil }
        return num.doubleValue
    }

    private func rollback(_ key: String) {
        let onDisk = AutoSettingsStore.load()
        isRollingBack = true
        defer { isRollingBack = false }
        switch key {
        case "enabled":
            let fallback: Bool
            if let diskValue = Self.strictBool(onDisk[key]) {
                fallback = diskValue
            } else if case .bool(let last) = lastPersisted[key] {
                fallback = last
            } else {
                fallback = false
            }
            autoContinueEnabled = fallback
            lastPersisted[key] = .bool(fallback)
        case "prompt":
            let fallback: String
            if let diskValue = onDisk[key] as? String {
                fallback = diskValue
            } else if case .string(let last) = lastPersisted[key] {
                fallback = last
            } else {
                fallback = ""
            }
            autoContinuePrompt = fallback
            lastPersisted[key] = .string(fallback)
        case "remaining_threshold":
            let fallback: Double
            if let diskValue = Self.strictDouble(onDisk[key]) {
                fallback = diskValue
            } else if case .double(let last) = lastPersisted[key] {
                fallback = last
            } else {
                fallback = 10
            }
            autoRemainingThreshold = fallback
            lastPersisted[key] = .double(fallback)
        case "interval":
            let fallback: Double
            if let diskValue = Self.strictDouble(onDisk[key]) {
                fallback = diskValue
            } else if case .double(let last) = lastPersisted[key] {
                fallback = last
            } else {
                fallback = 60
            }
            autoInterval = fallback
            lastPersisted[key] = .double(fallback)
        case "strategy":
            let fallback: String
            if let diskValue = onDisk[key] as? String {
                fallback = diskValue
            } else if case .string(let last) = lastPersisted[key] {
                fallback = last
            } else {
                fallback = "best"
            }
            autoStrategy = fallback
            lastPersisted[key] = .string(fallback)
        case "allow_chatgpt_restart":
            let fallback: Bool
            if let diskValue = Self.strictBool(onDisk[key]) {
                fallback = diskValue
            } else if case .bool(let last) = lastPersisted[key] {
                fallback = last
            } else {
                fallback = false
            }
            autoAllowRestart = fallback
            lastPersisted[key] = .bool(fallback)
        case "thread_id":
            let fallback: String
            if let diskValue = onDisk[key] as? String {
                fallback = diskValue
            } else if case .string(let last) = lastPersisted[key] {
                fallback = last
            } else {
                fallback = ""
            }
            autoThreadID = fallback
            lastPersisted[key] = .string(fallback)
        default:
            break
        }
    }

    func flushDebouncedAutoSettings() {
        let pending = pendingDebouncedValues
        for (key, task) in debounceTasks {
            task.cancel()
            debounceTasks.removeValue(forKey: key)
        }
        pendingDebouncedValues.removeAll()
        for (key, value) in pending {
            let generation = keyGeneration[key] ?? 0
            Task { @MainActor [weak self] in
                await self?.commitPersist(key, value, generation: generation)
            }
        }
    }

    func waitForPendingAutoSettings() async {
        flushDebouncedAutoSettings()
        guard hasPendingAutoSettingsSave else { return }
        await withCheckedContinuation { continuation in
            pendingSaveWaiters.append(continuation)
        }
    }

    private func notifyPendingSaveWaitersIfIdle() {
        guard !hasPendingAutoSettingsSave else { return }
        let waiters = pendingSaveWaiters
        pendingSaveWaiters.removeAll()
        for waiter in waiters {
            waiter.resume()
        }
    }

    init() {
        let quotaRaw = defaults.string(forKey: Keys.quotaDisplay)
        let numberRaw = defaults.string(forKey: Keys.numberFormat)
        let appearanceRaw = defaults.string(forKey: Keys.appearance)
        let languageRaw = defaults.string(forKey: Keys.language)
        quotaDisplayMode = QuotaDisplayMode(rawValue: quotaRaw ?? "") ?? .remaining
        numberFormatMode = NumberFormatMode(rawValue: numberRaw ?? "") ?? .full
        appearance = RelayAppearance(rawValue: appearanceRaw ?? "") ?? .dark
        language = AppLanguage(rawValue: languageRaw ?? "") ?? .systemDefault
        let auto = AutoSettingsStore.load()
        let enabled = Self.strictBool(auto["enabled"]) ?? false
        let prompt = auto["prompt"] as? String ?? ""
        let remaining = Self.strictDouble(auto["remaining_threshold"]) ?? 10
        let interval = Self.strictDouble(auto["interval"]) ?? 60
        let strategy = auto["strategy"] as? String ?? "best"
        let allowRestart = Self.strictBool(auto["allow_chatgpt_restart"]) ?? false
        let threadID = auto["thread_id"] as? String ?? ""
        autoContinueEnabled = enabled
        autoContinuePrompt = prompt
        autoRemainingThreshold = remaining
        autoInterval = interval
        autoStrategy = strategy
        autoAllowRestart = allowRestart
        autoThreadID = threadID
        lastPersisted = [
            "enabled": .bool(enabled),
            "prompt": .string(prompt),
            "remaining_threshold": .double(remaining),
            "interval": .double(interval),
            "strategy": .string(strategy),
            "allow_chatgpt_restart": .bool(allowRestart),
            "thread_id": .string(threadID),
        ]
        L10n.language = language
        AppAppearance.apply(appearance)
    }

    var preferredColorScheme: ColorScheme? {
        appearance.colorScheme
    }

}

enum NumberDisplay {
    static func integer(_ value: Int?, mode: NumberFormatMode, locale: Locale = .current) -> String {
        guard let value else { return "-" }
        return integer(Int64(value), mode: mode, locale: locale)
    }

    static func integer(_ value: Int64?, mode: NumberFormatMode, locale: Locale = .current) -> String {
        guard let value else { return "-" }
        if mode == .full {
            let formatter = NumberFormatter()
            formatter.locale = locale
            formatter.numberStyle = .decimal
            return formatter.string(from: NSNumber(value: value)) ?? String(value)
        }
        return compact(value)
    }

    private static func compact(_ value: Int64) -> String {
        let absolute = abs(value)
        let sign = value < 0 ? "-" : ""
        let units: [(Double, String)] = [(1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")]
        for (threshold, suffix) in units where Double(absolute) >= threshold {
            let scaled = Double(absolute) / threshold
            var text: String
            if scaled >= 100 {
                text = String(format: "%.0f", scaled)
            } else if scaled >= 10 {
                text = String(format: "%.1f", scaled)
            } else {
                text = String(format: "%.2f", scaled)
            }
            while text.contains("."), text.hasSuffix("0") {
                text.removeLast()
            }
            if text.hasSuffix(".") { text.removeLast() }
            return sign + text + suffix
        }
        return sign + NumberFormatter.localizedString(from: NSNumber(value: absolute), number: .decimal)
    }
}

extension RelayUsageWindow {
    var clampedUsedPercent: Double? {
        guard let usedPercent else { return nil }
        return min(max(usedPercent, 0), 100)
    }

    var remainingPercent: Double? {
        guard let used = clampedUsedPercent else { return nil }
        return max(0, 100 - used)
    }

    func percent(for mode: QuotaDisplayMode) -> Double? {
        switch mode {
        case .remaining: return remainingPercent
        case .used: return clampedUsedPercent
        }
    }
}
