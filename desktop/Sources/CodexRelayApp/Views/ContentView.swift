import AppKit
import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var preferences: AppPreferences
    @Environment(\.locale) private var locale

    var body: some View {
        NavigationSplitView {
            SidebarView()
                .navigationSplitViewColumnWidth(min: 230, ideal: 260, max: 300)
        } detail: {
            ZStack {
                HudBackground()
                detail
            }
        }
        .navigationSplitViewStyle(.balanced)
        .environment(\.locale, preferences.language.locale)
        .preferredColorScheme(preferences.preferredColorScheme)
        .task {
            model.start(preferences: preferences)
        }
        .onAppear {
            AppAppearance.apply(preferences.appearance)
        }
        .onChange(of: preferences.appearance) { _, newValue in
            AppAppearance.apply(newValue)
        }
        .onChange(of: preferences.language) { _, _ in
            model.resetTransientLocalizedState()
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await model.refreshActiveUsage(auto: true) }
        }
        .onChange(of: model.section) { _, newValue in
            if newValue == .diagnostics, model.doctor == nil {
                Task { await model.refreshDoctor() }
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                statusIndicator
                Menu {
                    Picker("外观", selection: $preferences.appearance) {
                        ForEach(RelayAppearance.allCases) { option in
                            Label(L10n.text(option.title), systemImage: option.icon).tag(option)
                        }
                    }
                } label: {
                    Label("外观", systemImage: preferences.appearance.icon)
                }
                .help("切换暗色终端 / 亮色档案")
                if model.isOpeningProcess {
                    ProgressView()
                        .controlSize(.small)
                        .help("正在打开 ChatGPT")
                } else if model.isRefreshingUsage {
                    ProgressView()
                        .controlSize(.small)
                        .help("正在刷新账号用量")
                } else if model.usageSyncError != nil {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(RelayTheme.red)
                        .help(L10n.text("用量同步失败"))
                }
                Button {
                    Task { await model.refreshAll() }
                } label: {
                    Label("刷新", systemImage: "arrow.clockwise")
                }
                .help("刷新账号与会话")
                Button {
                    model.beginAddAccount()
                } label: {
                    Label("添加账号", systemImage: "person.badge.plus")
                }
                .help("添加另一个 ChatGPT 账号")
            }
        }
        .sheet(isPresented: $model.showingAddSheet) {
            AddAccountSheet()
                .environmentObject(model)
                .environmentObject(preferences)
        }
        .sheet(isPresented: $model.isSwitching) {
            SwitchProgressSheet()
                .environmentObject(model)
                .environmentObject(preferences)
                .interactiveDismissDisabled()
        }
        .sheet(item: $model.pendingProcessAction) { action in
            ProcessConfirmationSheet(action: action)
                .environmentObject(model)
                .environmentObject(preferences)
        }
        .alert(model.errorTitle, isPresented: $model.showingError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(L10n.localized(model.errorMessage, language: preferences.language))
        }
        .overlay(alignment: .top) {
            if let toast = model.toast {
                ToastView(text: toast)
                    .padding(.top, 12)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.2), value: model.toast)
    }

    @ViewBuilder
    private var detail: some View {
        switch model.section {
        case .overview:
            OverviewView()
        case .accounts:
            AccountsView()
        case .sessions:
            SessionsView()
        case .diagnostics:
            DiagnosticsView()
        case .settings:
            SettingsView()
        case .about:
            AboutView()
        }
    }

    private var statusIndicator: some View {
        HStack(spacing: 5) {
            Rectangle()
                .fill(model.status?.desktopRunning == true ? RelayTheme.green : RelayTheme.amber)
                .frame(width: 6, height: 6)
            Text((model.status?.desktopRunning == true ? "Codex 运行中" : "Codex 未运行").localized(locale: locale))
                .font(.hudLabel(8))
                .foregroundStyle(RelayTheme.textMuted)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(RelayTheme.panelElevated)
        .clipShape(RoundedRectangle(cornerRadius: 3, style: .continuous))
    }
}

struct ToastView: View {
    @Environment(\.locale) private var locale
    let text: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(RelayTheme.green)
            Text(text.localized(locale: locale)).font(.callout.weight(.medium))
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
        .background(RelayTheme.panelElevated)
        .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
        .overlay(alignment: .leading) {
            Rectangle().fill(RelayTheme.green).frame(width: 2)
        }
        .overlay {
            RoundedRectangle(cornerRadius: 4, style: .continuous)
                .strokeBorder(RelayTheme.line.opacity(0.75), lineWidth: 0.7)
        }
        .shadow(color: RelayTheme.shadow.opacity(0.35), radius: 8, y: 3)
        .frame(maxWidth: 560)
    }
}
