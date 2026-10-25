import CodexRelayCore
import SwiftUI

struct AccountsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        GeometryReader { proxy in
            let listWidth = min(max(proxy.size.width * 0.28, 250), 340)
            HStack(spacing: 0) {
                listPane
                    .frame(width: listWidth)
                Rectangle()
                    .fill(RelayTheme.line)
                    .frame(width: 1)
                detailPane
                    .frame(minWidth: 0, maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onChange(of: model.selectedAccountID) { _, _ in
            Task { await model.refreshSelectedUsageIfNeeded() }
        }
        .onAppear {
            // Refresh every profile, not only the selected row; the list is
            // shared by the whole page and an inactive account may have reset.
            Task { await model.refreshUsage(silent: true) }
        }
    }

    private var listPane: some View {
        VStack(spacing: 0) {
            header
            HudDivider()
            if model.accounts.isEmpty {
                EmptyStateView(
                    icon: "person.2.slash",
                    title: "还没有账号",
                    message: "先捕获当前账号，或添加另一个 ChatGPT 账号。"
                )
            } else {
                ScrollView {
                    LazyVStack(spacing: 6) {
                        ForEach(model.accounts) { account in
                            AccountRow(
                                account: account,
                                selected: model.selectedAccountID == account.id
                            ) {
                                model.selectedAccountID = account.id
                            }
                        }
                    }
                    .padding(12)
                }
            }
        }
        .background(RelayTheme.panel)
    }

    @ViewBuilder
    private var detailPane: some View {
        if let account = model.selectedAccount {
            AccountDetailView(account: account)
                .id(account.id)
        } else {
            EmptyStateView(
                icon: "person.crop.circle",
                title: "选择一个账号",
                message: "查看额度、状态，并切换后恢复原会话。"
            )
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                HudLabel(text: "02 / 账号", color: RelayTheme.accent)
                Text("账号")
                    .font(.hudDisplay(24))
                    .foregroundStyle(RelayTheme.text)
                Text(L10n.format("%d 个已保存账号", model.accounts.count))
                    .font(.caption)
                    .foregroundStyle(RelayTheme.textMuted)
            }
            Spacer()
            Button {
                model.beginAddAccount()
            } label: {
                Image(systemName: "plus")
            }
            .buttonStyle(HudButtonStyle(primary: true, compact: true))
            .help("添加账号")
        }
        .padding(16)
    }
}

private struct AccountRow: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var preferences: AppPreferences
    @Environment(\.locale) private var locale
    let account: RelayAccount
    let selected: Bool
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                AccountAvatar(account: account, size: 32, cornerRadius: 4)

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 5) {
                        Text(account.displayName(locale: locale))
                            .font(.hudMono(12, weight: .semibold))
                            .foregroundStyle(RelayTheme.text)
                            .lineLimit(1)
                        if account.isActive {
                            StatusBadge(text: "当前", color: RelayTheme.green)
                        }
                        if !account.isHealthy {
                            StatusBadge(text: "需重登", color: RelayTheme.red)
                        } else if account.isExhausted {
                            StatusBadge(text: "额度耗尽", color: RelayTheme.red)
                        }
                    }
                    Text(account.email ?? "-")
                        .font(.caption2)
                        .foregroundStyle(RelayTheme.textMuted)
                        .lineLimit(1)
                    HStack(spacing: 10) {
                        Text(L10n.format(
                            "5 小时 · %@ %@",
                            L10n.text(preferences.quotaDisplayMode.shortTitle, language: preferences.language),
                            account.usage?.primary?.percent(for: preferences.quotaDisplayMode).map { String(format: "%.0f%%", $0) } ?? "-"
                        ))
                        Text(L10n.format(
                            "1 周 · %@ %@",
                            L10n.text(preferences.quotaDisplayMode.shortTitle, language: preferences.language),
                            account.usage?.secondary?.percent(for: preferences.quotaDisplayMode).map { String(format: "%.0f%%", $0) } ?? "-"
                        ))
                    }
                    .font(.hudLabel(8))
                    .foregroundStyle(.tertiary)
                }
                Spacer(minLength: 4)
                Text(account.plan?.uppercased() ?? "-")
                    .font(.hudLabel(8))
                    .foregroundStyle(RelayTheme.textMuted)
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 9)
            .background(
                selected
                    ? RelayTheme.accent.opacity(0.10)
                    : (hovering ? RelayTheme.hover : Color.clear)
            )
            .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            .overlay(alignment: .leading) {
                Rectangle()
                    .fill(selected ? RelayTheme.accent : Color.clear)
                    .frame(width: 2)
            }
            .overlay {
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .strokeBorder(selected ? RelayTheme.accent.opacity(0.30) : Color.clear, lineWidth: 0.7)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

private struct AccountDetailView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var preferences: AppPreferences
    @Environment(\.locale) private var locale
    let account: RelayAccount

    @State private var threadChoice = "last"
    @State private var initialized = false
    @State private var showingRename = false

    private var recentSessions: [RelaySession] {
        let owned = model.sessions.filter { $0.creatorAlias == account.alias }
        return owned.isEmpty ? Array(model.sessions.prefix(12)) : Array(owned.prefix(12))
    }

    private var selectedThreadLabel: String {
        if threadChoice == "last" { return "最近会话".localized(locale: locale) }
        return recentSessions.first(where: { $0.id == threadChoice })?.displayTitle.shortened ?? "最近会话".localized(locale: locale)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header

                if !account.isHealthy {
                    HudNotice(color: RelayTheme.red) {
                        HStack(spacing: 10) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(RelayTheme.red)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("该账号需要重新登录").font(.callout.weight(.semibold))
                                Text("refresh token 已失效或被撤销。请重新添加/登录后再切换。")
                                    .font(.caption)
                                    .foregroundStyle(RelayTheme.textMuted)
                            }
                            Spacer()
                        }
                    }
                }

                if account.isExhausted {
                    HudNotice(color: RelayTheme.red) {
                        HStack(spacing: 10) {
                            Image(systemName: "bolt.slash.fill")
                                .foregroundStyle(RelayTheme.red)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("当前额度已耗尽").font(.callout.weight(.semibold))
                                Text("CLI 默认会拒绝切换到该账号；等待重置或使用 --force。")
                                    .font(.caption)
                                    .foregroundStyle(RelayTheme.textMuted)
                            }
                            Spacer()
                        }
                    }
                }

                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 180), spacing: 12)],
                    alignment: .leading,
                    spacing: 12
                ) {
                    usageCard(title: "5 小时额度", window: account.usage?.primary)
                    usageCard(title: "每周额度", window: account.usage?.secondary)
                }

                RelayCard {
                    VStack(alignment: .leading, spacing: 10) {
                        HudLabel(text: "账号信息", color: RelayTheme.accent)
                        KVRow(key: "内部别名", value: account.alias)
                        KVRow(key: "邮箱", value: account.email ?? "-")
                        KVRow(key: "计划", value: account.plan ?? "-")
                        KVRow(key: "账号 ID", value: account.accountId ?? "-", monospaced: true)
                        KVRow(key: "状态", value: account.statusLabel.localized(locale: locale))
                        KVRow(key: "最近校验", value: RelayFormat.localTimestamp(account.lastVerifiedAt ?? account.usage?.checkedAt, locale: locale))
                        KVRow(key: "最近刷新", value: RelayFormat.localTimestamp(account.lastRefresh, locale: locale))
                    }
                }

                RelayCard(accented: true) {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            HudLabel(
                                text: account.isActive ? "恢复会话" : "会话接力",
                                color: RelayTheme.accent
                            )
                            Spacer()
                            if account.isActive {
                                StatusBadge(text: "在线", color: RelayTheme.green)
                            }
                        }
                        Text((account.isActive ? "恢复会话" : "切换并恢复").localized(locale: locale))
                            .font(.hudDisplay(18))
                            .foregroundStyle(RelayTheme.text)
                        Text("选择一条原会话；切换后会重启 ChatGPT，并用目标账号打开同一会话。")
                            .font(.caption)
                            .foregroundStyle(RelayTheme.textMuted)

                        Menu {
                            Button {
                                threadChoice = "last"
                            } label: {
                                HStack {
                                    Text("最近会话")
                                    if threadChoice == "last" {
                                        Image(systemName: "checkmark")
                                    }
                                }
                            }
                            if !recentSessions.isEmpty {
                                Divider()
                                ForEach(recentSessions) { session in
                                    Button {
                                        threadChoice = session.id
                                    } label: {
                                        Text("\(session.displayTitle.shortened) · \(session.updatedLocal ?? "")")
                                    }
                                }
                            }
                        } label: {
                            HStack(spacing: 8) {
                                Image(systemName: "text.bubble")
                                    .foregroundStyle(RelayTheme.accent)
                                Text(selectedThreadLabel)
                                    .font(.system(size: 11.5))
                                    .foregroundStyle(RelayTheme.text)
                                    .lineLimit(1)
                                Spacer()
                                Image(systemName: "chevron.up.chevron.down")
                                    .font(.system(size: 9, weight: .bold))
                                    .foregroundStyle(RelayTheme.textMuted)
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 9)
                            .background(RelayTheme.panelElevated)
                            .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
                            .overlay {
                                RoundedRectangle(cornerRadius: 4, style: .continuous)
                                    .strokeBorder(RelayTheme.line.opacity(0.65), lineWidth: 0.7)
                            }
                        }
                        .menuStyle(.borderlessButton)
                        .menuIndicator(.hidden)
                        .frame(maxWidth: 520, alignment: .leading)

                        HStack(spacing: 10) {
                            if account.isActive {
                                Button {
                                    model.requestRestart(threadID: threadChoice)
                                } label: {
                                    Label("重启并恢复此会话", systemImage: "arrow.clockwise.circle")
                                }
                                .buttonStyle(HudButtonStyle(primary: true))
                                .disabled(model.isSwitching || model.isOpeningProcess)
                            } else {
                                Button {
                                    model.requestSwitch(account, threadID: threadChoice)
                                } label: {
                                    Label("切换到此账号并恢复", systemImage: "arrow.triangle.2.circlepath")
                                }
                                .buttonStyle(HudButtonStyle(primary: true))
                                .disabled(!account.isHealthy || account.isExhausted || model.isSwitching || model.isOpeningProcess)
                            }
                            Button {
                                model.requestOpenApp()
                            } label: {
                                Label("打开最近会话", systemImage: "arrow.up.forward.app")
                            }
                            .buttonStyle(HudButtonStyle(primary: false))
                            Spacer()
                        }
                        if !account.isActive {
                            Text("切换通常需要 6–20 秒：校验账号 → 退出 App → 备份 → 写入凭据 → 重启 → 恢复会话。")
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(Color.clear)
        .task {
            await model.refreshSelectedUsageIfNeeded()
        }
        .sheet(isPresented: $showingRename) {
            RenameAccountSheet(account: account)
                .environmentObject(model)
                .environmentObject(preferences)
        }
        .onAppear {
            guard !initialized else { return }
            initialized = true
            if let selected = model.selectedSessionID, recentSessions.contains(where: { $0.id == selected }) {
                threadChoice = selected
            } else if let last = model.lastThreadID, recentSessions.contains(where: { $0.id == last }) {
                threadChoice = last
            }
        }
    }

    private var header: some View {
        HStack(spacing: 14) {
            Menu {
                Button("更换头像…") {
                    model.chooseAvatar(for: account)
                }
                if model.hasCustomAvatar(for: account) {
                    Button("恢复默认头像", role: .destructive) {
                        model.resetAvatar(for: account)
                    }
                }
            } label: {
                AccountAvatar(account: account, size: 54)
                    .overlay(alignment: .bottomTrailing) {
                        Image(systemName: "camera.fill")
                            .font(.system(size: 8, weight: .bold))
                            .foregroundStyle(RelayTheme.onAccent)
                            .padding(4)
                            .background(RelayTheme.accent)
                            .clipShape(Circle())
                            .offset(x: 3, y: 3)
                    }
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("点击更换或恢复头像")

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 7) {
                    Text(account.displayName(locale: locale))
                        .font(.hudDisplay(26))
                        .foregroundStyle(RelayTheme.text)
                        .lineLimit(1)
                        .minimumScaleFactor(0.55)
                        .layoutPriority(1)
                    if account.isActive {
                        StatusBadge(text: "当前账号", color: RelayTheme.green, icon: "checkmark")
                    }
                    StatusBadge(
                        text: account.statusLabel,
                        color: account.isHealthy && !account.isExhausted ? RelayTheme.accent : RelayTheme.red,
                        icon: account.isHealthy && !account.isExhausted ? "checkmark.shield" : "exclamationmark.triangle"
                    )
                }
                Text("\(account.email ?? "-") · \(account.plan ?? "-")")
                    .font(.caption)
                    .foregroundStyle(RelayTheme.textMuted)
            }
            Spacer()
            Button {
                showingRename = true
            } label: {
                Label("重命名", systemImage: "pencil")
            }
            .buttonStyle(HudButtonStyle(primary: false, compact: true))
            .disabled(model.isRenamingAccount)

            Button {
                Task { await model.refreshUsageForAccount(account, auto: false) }
            } label: {
                if model.isRefreshingUsage {
                    ProgressView().controlSize(.small)
                } else {
                    Label("刷新额度", systemImage: "arrow.clockwise")
                }
            }
            .buttonStyle(HudButtonStyle(primary: false, compact: true))
            .disabled(model.isRefreshingUsage || model.isSwitching || model.isOpeningProcess)
        }
    }

    private func usageCard(title: String, window: RelayUsageWindow?) -> some View {
        RelayCard {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 4) {
                    HudLabel(text: title, color: RelayTheme.textMuted)
                    Text("// \(account.preferredName)")
                        .font(.hudLabel())
                        .foregroundStyle(RelayTheme.textMuted)
                    Spacer()
                }
                if let window {
                    UsageBar(title: preferences.quotaDisplayMode.title, window: window, mode: preferences.quotaDisplayMode, showsModeLabel: false)
                    Text(L10n.format("校验 // %@", RelayFormat.localTimestamp(account.lastVerifiedAt ?? account.usage?.checkedAt, locale: locale)))
                        .font(.hudLabel(8))
                        .foregroundStyle(.tertiary)
                } else {
                    Text("暂无数据")
                        .font(.hudLabel(10))
                        .foregroundStyle(RelayTheme.amber)
                }
            }
        }
    }
}


private struct RenameAccountSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var preferences: AppPreferences
    @Environment(\.dismiss) private var dismiss

    let account: RelayAccount
    @State private var name: String

    init(account: RelayAccount) {
        self.account = account
        _name = State(initialValue: account.preferredName)
    }

    private var valid: Bool {
        !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 11) {
                ZStack {
                    RoundedRectangle(cornerRadius: 5, style: .continuous)
                        .fill(RelayTheme.accent.opacity(0.13))
                        .frame(width: 42, height: 42)
                    Image(systemName: "pencil.and.outline")
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundStyle(RelayTheme.accent)
                }
                VStack(alignment: .leading, spacing: 3) {
                    HudLabel(text: "账号管理", color: RelayTheme.accent)
                    Text("重命名账号")
                        .font(.hudDisplay(21))
                        .foregroundStyle(RelayTheme.text)
                    Text("自定义名称会保存在账号仓库中，刷新和切换后仍会保留。")
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                }
                Spacer()
            }

            HudLabel(text: "显示名称", color: RelayTheme.textMuted)
            TextField("例如：工作账号、个人账号", text: $name)
                .textFieldStyle(HudTextFieldStyle())
                .onSubmit { save() }

            HStack(spacing: 7) {
                Image(systemName: "at")
                    .foregroundStyle(RelayTheme.textMuted)
                Text(account.email ?? account.alias)
                    .font(.hudMono(9))
                    .foregroundStyle(RelayTheme.textMuted)
            }

            Spacer(minLength: 0)

            HStack(spacing: 8) {
                Button("取消") { dismiss() }
                    .buttonStyle(HudButtonStyle(primary: false))
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("保存名称") { save() }
                    .buttonStyle(HudButtonStyle(primary: true))
                    .keyboardShortcut(.defaultAction)
                    .disabled(!valid || model.isRenamingAccount)
            }
        }
        .padding(22)
        .frame(width: 500, height: 280)
        .background(HudBackground())
    }

    private func save() {
        guard valid else { return }
        dismiss()
        Task {
            await model.renameAccount(account, displayName: name)
        }
    }
}
