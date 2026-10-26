import AppKit
import SwiftUI
import CodexRelayCore

final class RelayAppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
}

@main
struct CodexRelayApp: App {
    @NSApplicationDelegateAdaptor(RelayAppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()
    @StateObject private var preferences = AppPreferences()

    init() {
        if let index = CommandLine.arguments.firstIndex(of: "--render-review"), CommandLine.arguments.count > index + 1 {
            do { try renderReview(to: URL(fileURLWithPath: CommandLine.arguments[index + 1])); exit(0) }
            catch { fputs("review render failed: \(error)\n", stderr); exit(1) }
        }
        if CommandLine.arguments.contains("--i18n-self-test") {
            let keys = [
                "设置", "概览", "账号", "会话", "诊断", "关于",
                "刷新用量", "重启并恢复", "打开最近会话", "未运行",
                "5 小时额度", "每周额度", "额度已耗尽", "缓存 Token",
                "累计缓存命中率", "English interface", "创建者",
            ]
            var missing = 0
            for language in AppLanguage.allCases {
                for key in keys {
                    let value = L10n.localized(key, language: language)
                    print("\(language.rawValue)\t\(key)\t\(value)")
                    if language != .zhCN, value == key, key != "English interface" {
                        missing += 1
                    }
                }
            }
            exit(missing == 0 ? 0 : 1)
        }
        if CommandLine.arguments.contains("--self-test") {
            let backend = RelayBackend()
            print("backend=\(backend.binaryPath ?? "-")")
            do {
                let accounts = try backend.listAccounts(refresh: false)
                let status = try backend.status()
                let sessions = try backend.sessions(limit: 5)
                let preferences = AppPreferences()
                let active = accounts.first(where: \.isActive)
                let primary = active?.usage?.primary?.usedPercent.map { String(format: "%.0f%%", $0) } ?? "-"
                let secondary = active?.usage?.secondary?.usedPercent.map { String(format: "%.0f%%", $0) } ?? "-"
                print("accounts=\(accounts.count) active=\(active?.alias ?? "-") 5h=\(primary) 1w=\(secondary) verified=\(active?.lastVerifiedAt ?? "-") desktop=\(status.desktopRunning ?? false) sessions=\(sessions.count)")
                print("prefs quota=\(preferences.quotaDisplayMode.rawValue) number=\(preferences.numberFormatMode.rawValue) appearance=\(preferences.appearance.rawValue) language=\(preferences.language.rawValue)")
                let langSamples = AppLanguage.allCases.map { "\($0.rawValue)=\(L10n.localized("设置", language: $0))" }.joined(separator: ",")
                print("langSamples=\(langSamples) localizations=\(Bundle.main.localizations.sorted())")
                print("langFormat=\(L10n.format("已用时 %d 秒", 3, language: .en))")
                exit(0)
            } catch {
                fputs("self-test failed: \(error.localizedDescription)\n", stderr)
                exit(1)
            }
        }
    }

    var body: some Scene {
        Window("Codex Relay", id: "main") {
            ContentView()
                .environmentObject(model)
                .environmentObject(preferences)
                .environment(\.locale, preferences.language.locale)
                .frame(minWidth: 1000, minHeight: 680)
                .preferredColorScheme(preferences.preferredColorScheme)
        }
        .defaultSize(width: 1160, height: 760)
        .commands {
            CommandMenu("Codex Relay") {
                Picker(L10n.text("外观"), selection: $preferences.appearance) {
                    ForEach(RelayAppearance.allCases) { appearance in
                        Text(L10n.text(appearance.title)).tag(appearance)
                    }
                }
                Divider()
                Button(L10n.text("刷新账号与会话")) {
                    Task { await model.refreshAll() }
                }
                .keyboardShortcut("r", modifiers: .command)
                Button(L10n.text("刷新账号用量")) {
                    Task { await model.refreshUsage() }
                }
                .keyboardShortcut("u", modifiers: [.command, .shift])
                Divider()
                Button(L10n.text("打开最近会话")) { model.requestOpenApp() }
                    .keyboardShortcut("o", modifiers: [.command, .shift])
                Button(L10n.text("打开凭据仓库")) { model.openStore() }
            }
        }

        MenuBarExtra {
            MenuBarView()
                .environmentObject(model)
                .environmentObject(preferences)
                .environment(\.locale, preferences.language.locale)
        } label: {
            Label(model.menuBarTitle, systemImage: "arrow.triangle.2.circlepath")
        }
        .menuBarExtraStyle(.menu)
    }
}
