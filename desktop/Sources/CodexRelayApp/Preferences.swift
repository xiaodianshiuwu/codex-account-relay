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

    private let defaults = UserDefaults.standard

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

    /// These values are mirrored to ~/.codex-relay/auto.json so the CLI
    /// `codex-relay auto` daemon and the desktop settings use one source of
    /// truth. They contain no credentials.
    @Published var autoContinueEnabled: Bool {
        didSet { defaults.set(autoContinueEnabled, forKey: Keys.autoEnabled); persistAutoSettings() }
    }
    @Published var autoContinuePrompt: String {
        didSet { defaults.set(autoContinuePrompt, forKey: Keys.autoPrompt); persistAutoSettings() }
    }
    @Published var autoRemainingThreshold: Double {
        didSet {
            let clamped = min(100, max(0, autoRemainingThreshold))
            if clamped != autoRemainingThreshold { autoRemainingThreshold = clamped; return }
            defaults.set(autoRemainingThreshold, forKey: Keys.autoRemaining)
            persistAutoSettings()
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
        let auto = AutoSettingsFile.load()
        autoContinueEnabled = defaults.object(forKey: Keys.autoEnabled) as? Bool ?? auto.enabled
        autoContinuePrompt = defaults.string(forKey: Keys.autoPrompt) ?? (auto.prompt.isEmpty ? L10n.text("继续") : auto.prompt)
        autoRemainingThreshold = defaults.object(forKey: Keys.autoRemaining) as? Double ?? auto.remainingThreshold
        L10n.language = language
        AppAppearance.apply(appearance)
    }

    var preferredColorScheme: ColorScheme? {
        appearance.colorScheme
    }

    private func persistAutoSettings() {
        guard autoContinuePrompt.count <= 4000 else { return }
        AutoSettingsFile(
            enabled: autoContinueEnabled,
            prompt: autoContinuePrompt,
            remainingThreshold: autoRemainingThreshold,
            interval: 60,
            strategy: "best"
        ).save()
    }
}

private struct AutoSettingsFile: Codable {
    var enabled: Bool
    var prompt: String
    var remainingThreshold: Double
    var interval: Double
    var strategy: String

    enum CodingKeys: String, CodingKey {
        case enabled, prompt, interval, strategy
        case remainingThreshold = "remaining_threshold"
    }

    static var url: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".codex-relay", isDirectory: true)
            .appendingPathComponent("auto.json")
    }

    static func load() -> AutoSettingsFile {
        guard let data = try? Data(contentsOf: url),
              let value = try? JSONDecoder().decode(AutoSettingsFile.self, from: data) else {
            return AutoSettingsFile(enabled: false, prompt: "", remainingThreshold: 10, interval: 60, strategy: "best")
        }
        return value
    }

    func save() {
        let directory = Self.url.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        guard let data = try? JSONEncoder().encode(self) else { return }
        try? data.write(to: Self.url, options: [.atomic])
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: Self.url.path)
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
