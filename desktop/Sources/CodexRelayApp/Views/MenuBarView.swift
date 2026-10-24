import AppKit
import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.locale) private var locale
    @EnvironmentObject private var preferences: AppPreferences
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        if let account = model.currentAccount {
            Text(L10n.format("当前：%@ · %@", account.preferredName, account.plan ?? "-"))
            Text(account.email ?? "-")
        } else {
            Text("当前未登录 Codex")
        }
        Divider()

        if model.isSwitching {
            Text(model.switchStage.localized(locale: locale))
        } else {
            ForEach(model.accounts) { account in
                Button {
                    openWindow(id: "main")
                    NSApp.activate(ignoringOtherApps: true)
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                        model.requestSwitch(account)
                    }
                } label: {
                    HStack {
                        if account.isActive {
                            Image(systemName: "checkmark")
                        }
                        Text(account.displayName(locale: locale))
                        Spacer()
                        Text(account.usage?.primary?.percent(for: preferences.quotaDisplayMode).map {
                            L10n.format("5小时%@ %.0f%%", L10n.text(preferences.quotaDisplayMode.shortTitle, language: preferences.language), $0)
                        } ?? L10n.text("未刷新"))
                    }
                }
                .disabled(account.isActive || !account.isHealthy || account.isExhausted || model.isOpeningProcess)
            }
        }

        Divider()
        Button("打开 Codex Relay") {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
        }
        Button("刷新账号用量") {
            Task { await model.refreshUsage() }
        }
        .disabled(model.isRefreshingUsage || model.isSwitching)
        Menu("外观") {
            Picker("外观", selection: $preferences.appearance) {
                ForEach(RelayAppearance.allCases) { appearance in
                    Label(L10n.text(appearance.title), systemImage: appearance.icon).tag(appearance)
                }
            }
        }
        Button("打开最近会话") {
            openWindow(id: "main")
            NSApp.activate(ignoringOtherApps: true)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
                model.requestOpenApp()
            }
        }
        Divider()
        Button("退出 Codex Relay") {
            NSApp.terminate(nil)
        }
    }
}
