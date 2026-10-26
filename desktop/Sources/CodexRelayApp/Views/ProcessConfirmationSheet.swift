import CodexRelayCore
import SwiftUI

struct ProcessConfirmationSheet: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var preferences: AppPreferences
    @Environment(\.locale) private var locale
    let action: PendingProcessAction

    @State private var confirmed = false
    @State private var selectedThreadID: String
    @State private var continueAfterSwitch = false
    @State private var continuationText = ""

    init(action: PendingProcessAction) {
        self.action = action
        _selectedThreadID = State(initialValue: action.threadID == "last" ? "none" : action.threadID)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 5, style: .continuous)
                        .fill(RelayTheme.amber.opacity(0.14))
                        .frame(width: 44, height: 44)
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(RelayTheme.amber)
                }
                .overlay {
                    RoundedRectangle(cornerRadius: 5, style: .continuous)
                        .strokeBorder(RelayTheme.amber.opacity(0.30), lineWidth: 0.7)
                }
                VStack(alignment: .leading, spacing: 3) {
                    HudLabel(text: "进程控制 / 确认", color: RelayTheme.amber)
                    Text(action.title(locale: locale))
                        .font(.hudDisplay(22))
                        .foregroundStyle(RelayTheme.text)
                    Text((action.kind.isOpen
                         ? "此操作会向 ChatGPT 发送深链接；需要你明确确认。"
                         : "这是唯一会碰 ChatGPT 进程的操作，必须由你确认。").localized(locale: locale))
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                }
                Spacer()
            }

            HazardStripe(color: RelayTheme.amber, height: 2)
                .opacity(0.7)

            if !action.kind.isOpen, let account = action.account, account.isExhausted || !account.isHealthy {
                HudNotice(color: RelayTheme.red) {
                    HStack(alignment: .top, spacing: 9) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(RelayTheme.red)
                        VStack(alignment: .leading, spacing: 2) {
                            Text((account.isExhausted ? "目标账号额度已耗尽" : "目标账号需要重新登录").localized(locale: locale))
                                .font(.callout.weight(.semibold))
                            Text((account.isExhausted
                                 ? "CLI 会拒绝切换（除非使用 --force）；建议先换一个有额度的账号。"
                                 : "refresh token 已失效，切换会失败；请先重新登录。").localized(locale: locale))
                                .font(.caption)
                                .foregroundStyle(RelayTheme.textMuted)
                        }
                        Spacer()
                    }
                }
            }

            RelayCard {
                VStack(alignment: .leading, spacing: 7) {
                    HudLabel(text: "操作摘要", color: RelayTheme.accent)
                    if action.kind.isOpen {
                        KVRow(key: "操作", value: L10n.text(action.kind == .openApp ? "打开最近会话" : "打开指定会话"))
                        KVRow(
                            key: "会话 ID",
                            value: action.kind == .openApp ? L10n.text("最近会话") : action.threadID,
                            monospaced: action.kind == .openThread
                        )
                        KVRow(key: "预计耗时", value: L10n.text("< 5 秒"))
                        KVRow(key: "失败处理", value: L10n.text("不修改账号凭据"))
                    } else {
                        if let account = action.account {
                            KVRow(key: "目标账号", value: "\(account.preferredName) · \(account.email ?? "-")")
                            KVRow(key: "目标额度", value: usageText(account))
                        } else {
                            KVRow(key: "操作", value: L10n.text("仅重启 ChatGPT，不切换账号"))
                        }
                        Picker(L10n.text("恢复会话"), selection: $selectedThreadID) {
                            Text(L10n.text("仅切换账号")).tag("none")
                            if selectedThreadID != "none" && !model.sessions.contains(where: { $0.id == selectedThreadID }) {
                                Text(selectedThreadID).tag(selectedThreadID)
                            }
                            ForEach(model.sessions) { session in
                                Text(session.displayTitle(locale: locale)).tag(session.id)
                            }
                        }
                        if selectedThreadID != "none" {
                            Text(selectedThreadID).font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
                        }
                        KVRow(key: "预计耗时", value: L10n.text("6–20 秒"))
                        KVRow(key: "失败处理", value: L10n.text("自动恢复备份并重启 ChatGPT"))
                    }
                }
            }

            VStack(alignment: .leading, spacing: 5) {
                if action.kind.isOpen {
                    bullet("会通过 CLI 向 ChatGPT 发送深链接；CLI 必须收到 --allow-chatgpt-restart。")
                    bullet("如果 ChatGPT 未运行，macOS 会按需启动它。")
                    bullet("不会退出或重启 ChatGPT，也不会修改账号凭据。")
                    bullet("此处只执行你确认的操作；自动接力在设置中单独启用。")
                } else {
                    bullet("勾选续跑时会先确认中断所选轮次；其他活动聊天仍会阻止切号。")
                    bullet("会备份 auth.json 和 state，再写入目标凭据。")
                    bullet("切换期间不要操作 ChatGPT。")
                    bullet("此处只执行你确认的操作；自动接力在设置中单独启用。")
                }
            }

            if action.kind == .switchAccount {
                Toggle(L10n.text("切号后继续原聊天"), isOn: $continueAfterSwitch)
                    .disabled(selectedThreadID == "none")
                if continueAfterSwitch {
                    TextField(L10n.text("续跑提示词"), text: $continuationText).textFieldStyle(.roundedBorder)
                }
            }

            Toggle(isOn: $confirmed) {
                Text(action.confirmationLabel(locale: locale))
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(RelayTheme.text)
            }
            .toggleStyle(.switch)
            .tint(RelayTheme.amber)

            Spacer(minLength: 0)

            HStack(spacing: 8) {
                Button(L10n.text("取消")) {
                    model.cancelPendingProcessAction()
                }
                .buttonStyle(HudButtonStyle(primary: false))
                .keyboardShortcut(.cancelAction)
                Spacer()
                Button(L10n.text("确认并继续")) {
                    model.confirmPendingProcessAction(threadID: action.kind.isOpen ? action.threadID : selectedThreadID, continuationPrompt: continueAfterSwitch ? continuationText : nil)
                }
                .buttonStyle(HudButtonStyle(primary: true))
                .keyboardShortcut(.defaultAction)
                .disabled(!confirmed || (continueAfterSwitch && (selectedThreadID == "none" || continuationText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)) || (!action.kind.isOpen && (action.account?.isHealthy == false || action.account?.isExhausted == true)))
            }
        }
        .padding(22)
        .frame(width: 650, height: 640)
        .background(HudBackground())
        .interactiveDismissDisabled()
        .onAppear { continuationText = preferences.autoContinuePrompt.isEmpty ? L10n.text("继续") : preferences.autoContinuePrompt }
    }

    private func usageText(_ account: RelayAccount) -> String {
        guard let usage = account.usage else { return L10n.text("未刷新") }
        let fiveHour = usage.primary?.percent(for: preferences.quotaDisplayMode).map { String(format: "%.0f%%", $0) } ?? "-"
        let weekly = usage.secondary?.percent(for: preferences.quotaDisplayMode).map { String(format: "%.0f%%", $0) } ?? "-"
        return L10n.format(
            "5 小时%@ %@ · 1 周%@ %@",
            L10n.text(preferences.quotaDisplayMode.shortTitle, language: preferences.language),
            fiveHour,
            L10n.text(preferences.quotaDisplayMode.shortTitle, language: preferences.language),
            weekly
        )
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Rectangle()
                .fill(RelayTheme.amber.opacity(0.75))
                .frame(width: 6, height: 2)
                .padding(.top, 7)
            Text(text.localized(locale: locale))
                .font(.caption)
                .foregroundStyle(RelayTheme.textMuted)
        }
    }
}
