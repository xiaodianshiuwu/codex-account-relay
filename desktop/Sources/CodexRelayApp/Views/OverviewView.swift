import CodexRelayCore
import SwiftUI

struct OverviewView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var preferences: AppPreferences
    @Environment(\.locale) private var locale

    private let columns = [
        GridItem(.adaptive(minimum: 280, maximum: 420), spacing: 16, alignment: .top)
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                pageHeader
                safetyBanner
                if let error = model.usageSyncError {
                    HudNotice(color: RelayTheme.red) {
                        HStack(spacing: 9) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(RelayTheme.red)
                            Text(L10n.format("额度同步失败：%@", error))
                                .font(.caption)
                                .foregroundStyle(RelayTheme.textMuted)
                            Spacer()
                        }
                    }
                }
                if let auth = model.unmanagedAuth {
                    unmanagedBanner(auth)
                }
                if let account = model.currentAccount, account.status == "needs_relogin" {
                    reloginBanner(account)
                }

                LazyVGrid(columns: columns, alignment: .leading, spacing: 16) {
                    currentAccountCard
                    usageCard(
                        title: "5 小时额度",
                        window: model.currentAccount?.usage?.primary
                    )
                    usageCard(
                        title: "每周额度",
                        window: model.currentAccount?.usage?.secondary
                    )
                    systemCard
                }

                quickActions
                recentSessions
            }
            .padding(24)
            .frame(maxWidth: 1120, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(Color.clear)
    }

    private var safetyBanner: some View {
        HudNotice(color: RelayTheme.accent) {
            HStack(spacing: 10) {
                Image(systemName: "hand.raised.fill")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(RelayTheme.accent)
                Text(L10n.text("安全模式：工具不会在后台自动启停 ChatGPT。每次切换/重启/打开会话都必须先确认，CLI 需显式 --allow-chatgpt-restart。"))
                    .font(.caption)
                    .foregroundStyle(RelayTheme.textMuted)
                Spacer()
            }
        }
    }

    private var pageHeader: some View {
        HStack(alignment: .center) {
            HudSectionTitle(
                code: "01 / 概览",
                title: "概览",
                subtitle: "账号、额度与原会话接力状态"
            )
            Spacer()
            if model.isRefreshing || model.isRefreshingUsage {
                ProgressView().controlSize(.small)
            }
            if let last = model.lastUsageSync {
                Text(L10n.format("同步 // %@", last.localizedString(locale: Locale(identifier: preferences.language.rawValue))))
                    .font(.hudLabel(8))
                    .foregroundStyle(RelayTheme.textMuted)
            }
        }
    }

    @ViewBuilder
    private var currentAccountCard: some View {
        RelayCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    HudLabel(text: "当前账号", color: RelayTheme.accent)
                    Spacer()
                    if let account = model.currentAccount {
                        StatusBadge(
                            text: account.isActive ? "当前" : "仓库",
                            color: RelayTheme.accent,
                            icon: "checkmark"
                        )
                    }
                }
                if let account = model.currentAccount {
                    HStack(spacing: 12) {
                        avatar(account)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(account.displayName(locale: locale))
                                .font(.hudDisplay(19))
                                .lineLimit(1)
                            Text("\(account.email ?? "-") · \(account.plan?.uppercased() ?? "-")")
                                .font(.hudLabel(8))
                                .tracking(0.8)
                                .foregroundStyle(RelayTheme.textMuted)
                                .lineLimit(1)
                        }
                    }
                    HudDivider()
                    KVRow(key: "账号 ID", value: account.accountId ?? "-", monospaced: true)
                    KVRow(key: "refresh token", value: account.status == "ok" ? L10n.text("有效/可续期") : (account.status ?? "-"))
                    KVRow(key: "最近校验", value: RelayFormat.localTimestamp(account.lastVerifiedAt ?? account.usage?.checkedAt, locale: locale))
                } else if let auth = model.status?.auth {
                    Text(auth.email ?? "-").font(.hudDisplay(19))
                    Text("\(auth.plan ?? "-") · \(auth.accountId ?? "-")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(L10n.text("该账号尚未纳入账号仓库，切换前请先捕获。"))
                        .font(.caption)
                        .foregroundStyle(RelayTheme.orange)
                } else {
                    Text(L10n.text("未登录 Codex")).font(.hudDisplay(19))
                    Text(L10n.text("请先在 ChatGPT/Codex 中登录，或添加账号。"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func avatar(_ account: RelayAccount) -> some View {
        AccountAvatar(account: account, size: 44)
    }

    private func usageCard(title: String, window: RelayUsageWindow?) -> some View {
        RelayCard {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 4) {
                    HudLabel(text: title, color: RelayTheme.textMuted)
                    Text("// \(model.currentAccount?.preferredName ?? "-")")
                        .font(.hudLabel())
                        .foregroundStyle(RelayTheme.textMuted)
                    Spacer()
                    Image(systemName: "gauge.with.dots.needle.50percent")
                        .foregroundStyle(RelayTheme.accent.opacity(0.7))
                }
                if let window {
                    let used = window.clampedUsedPercent
                    let shown = window.percent(for: preferences.quotaDisplayMode)
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(shown.map { String(format: "%.0f", $0) } ?? "-")
                            .font(.hudDisplay(42))
                            .foregroundStyle(RelayTheme.quotaBarColor(usedPercent: used, mode: preferences.quotaDisplayMode))
                        Text("%").font(.hudMono(14, weight: .bold)).foregroundStyle(RelayTheme.textMuted)
                        Text(preferences.quotaDisplayMode.shortTitle.localized(locale: locale))
                            .font(.hudLabel(8))
                            .foregroundStyle(RelayTheme.textMuted)
                        Spacer()
                        Text(window.displayWindowLabel(locale: locale))
                            .font(.hudLabel(8))
                            .foregroundStyle(RelayTheme.textMuted)
                    }
                    UsageBar(title: preferences.quotaDisplayMode.title, window: window, mode: preferences.quotaDisplayMode, showsModeLabel: false)
                    if let verified = model.currentAccount?.lastVerifiedAt ?? model.currentAccount?.usage?.checkedAt {
                        Text(L10n.format("校验 // %@", RelayFormat.localTimestamp(verified, locale: locale)))
                            .font(.hudLabel(8))
                            .foregroundStyle(.tertiary)
                    }
                } else {
                    Text(L10n.text("暂无数据")).font(.hudLabel(10)).foregroundStyle(RelayTheme.amber)
                    Text(L10n.text("点击“刷新用量”通过官方接口读取。"))
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            }
        }
    }

    private var systemCard: some View {
        RelayCard {
            VStack(alignment: .leading, spacing: 10) {
                HudLabel(text: "系统状态", color: RelayTheme.accent)
                HStack(spacing: 8) {
                    Rectangle()
                        .fill(model.status?.desktopRunning == true ? RelayTheme.green : RelayTheme.amber)
                        .frame(width: 7, height: 7)
                    Text((model.status?.desktopRunning == true ? "ChatGPT 桌面 App 运行中" : "ChatGPT 桌面 App 未运行").localized(locale: locale))
                        .font(.callout)
                }
                KVRow(
                    key: "app-server",
                    value: {
                        let count = model.status?.desktopProcesses?.filter { $0.role == "desktop-app-server" }.count ?? 0
                        return count == 1 ? L10n.text("1 个进程") : L10n.format("%d 个进程", count)
                    }()
                )
                KVRow(
                    key: "共享 daemon",
                    value: model.status?.daemonRunning == true ? L10n.text("运行中") : L10n.text("未运行")
                )
                KVRow(key: "CODEX_HOME", value: model.status?.codexHome ?? "~/.codex", monospaced: true)
                if let last = model.status?.lastSwitch {
                    HudDivider()
                    KVRow(
                        key: "上次切换",
                        value: "\(model.preferredName(forAlias: last.from?.alias) ?? "?") → \(model.preferredName(forAlias: last.to?.alias) ?? "?") · \(last.mode ?? "-")"
                    )
                }
            }
        }
    }

    private var quickActions: some View {
        RelayCard {
            VStack(alignment: .leading, spacing: 12) {
                HudLabel(text: "快捷操作", color: RelayTheme.accent)
                HStack(spacing: 10) {
                    actionButton("刷新用量", icon: "gauge.with.dots.needle.50percent") {
                        Task { await model.refreshUsage() }
                    }
                    actionButton("重启并恢复", icon: "arrow.clockwise.circle") {
                        model.requestRestart()
                    }
                    actionButton("打开最近会话", icon: "arrow.up.forward.app") {
                        model.requestOpenApp()
                    }
                    actionButton("诊断", icon: "stethoscope") {
                        model.section = .diagnostics
                        Task { await model.refreshDoctor() }
                    }
                    Spacer()
                }
                .disabled(model.isSwitching || model.isOpeningProcess)
            }
        }
    }

    private func actionButton(_ title: String, icon: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title.localized(locale: locale), systemImage: icon)
        }
        .buttonStyle(HudButtonStyle(primary: false))
    }

    private var recentSessions: some View {
        RelayCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    HudLabel(text: "最近会话", color: RelayTheme.accent)
                    Spacer()
                    Button(L10n.text("查看全部")) {
                        model.section = .sessions
                    }
                    .buttonStyle(HudButtonStyle(primary: false))
                }
                if model.sessions.isEmpty {
                    Text(L10n.text("暂无会话")).font(.callout).foregroundStyle(.secondary)
                } else {
                    ForEach(model.sessions.prefix(6)) { session in
                        Button {
                            model.selectedSessionID = session.id
                            model.section = .sessions
                        } label: {
                            HStack(spacing: 10) {
                                Image(systemName: "bubble.left.and.text.bubble.right")
                                    .foregroundStyle(.secondary)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(session.displayTitle(locale: locale))
                                        .font(.callout.weight(.medium))
                                        .lineLimit(1)
                                    Text(L10n.format("%@ · %@", session.creatorAlias ?? L10n.text("未归属"), session.updatedLocal ?? "-"))
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Text(session.shortID)
                                    .font(.caption2.monospaced())
                                    .foregroundStyle(.tertiary)
                            }
                            .contentShape(Rectangle())
                            .padding(.vertical, 4)
                        }
                        .buttonStyle(.plain)
                        if session.id != model.sessions.prefix(6).last?.id {
                            HudDivider().opacity(0.7)
                        }
                    }
                }
            }
        }
    }

    private func unmanagedBanner(_ auth: RelayAuth) -> some View {
        banner(
            icon: "person.crop.circle.badge.questionmark",
            title: "当前账号尚未纳入仓库",
            message: L10n.format("%@。捕获后才能安全地切走并切回。", auth.email ?? "-"),
            color: RelayTheme.amber
        ) {
            Button(L10n.text("捕获当前账号")) { model.captureCurrentAccount() }
                .buttonStyle(HudButtonStyle(primary: true, compact: true))
        }
    }

    private func reloginBanner(_ account: RelayAccount) -> some View {
        banner(
            icon: "exclamationmark.triangle.fill",
            title: L10n.format("%@ 需要重新登录", account.preferredName),
            message: "该账号的 refresh token 已被撤销或失效。切换前请重新登录。",
            color: RelayTheme.red
        ) {
            Button(L10n.text("添加/重新登录")) { model.beginAddAccount() }
                .buttonStyle(HudButtonStyle(primary: true, compact: true))
        }
    }

    private func banner<Actions: View>(
        icon: String,
        title: String,
        message: String,
        color: Color,
        @ViewBuilder actions: () -> Actions
    ) -> some View {
        HudNotice(color: color) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(color)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title.localized(locale: locale)).font(.callout.weight(.semibold))
                    Text(message.localized(locale: locale)).font(.caption).foregroundStyle(RelayTheme.textMuted)
                }
                Spacer()
                actions()
            }
        }
    }
}
