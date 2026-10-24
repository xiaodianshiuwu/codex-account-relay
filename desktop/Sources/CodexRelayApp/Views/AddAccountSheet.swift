import SwiftUI

struct AddAccountSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var alias = ""
    @State private var deviceAuth = false

    private var effectiveAlias: String {
        let trimmed = alias.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "account\(model.accounts.count + 1)" : trimmed
    }

    private var aliasValid: Bool {
        effectiveAlias.range(of: #"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"#, options: .regularExpression) != nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 5, style: .continuous)
                        .fill(RelayTheme.accent.opacity(0.13))
                        .frame(width: 44, height: 44)
                    Image(systemName: "person.badge.plus")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(RelayTheme.accent)
                }
                .overlay {
                    RoundedRectangle(cornerRadius: 5, style: .continuous)
                        .strokeBorder(RelayTheme.accent.opacity(0.25), lineWidth: 0.7)
                }

                VStack(alignment: .leading, spacing: 3) {
                    HudLabel(text: "添加账号", color: RelayTheme.accent)
                    Text("添加 ChatGPT 账号")
                        .font(.hudDisplay(23))
                        .foregroundStyle(RelayTheme.text)
                    Text("使用隔离 CODEX_HOME 登录，不撤销当前账号")
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                }
                Spacer()
            }

            VStack(alignment: .leading, spacing: 8) {
                HudLabel(text: "账号别名", color: RelayTheme.textMuted)
                TextField("可留空，自动使用 accountN", text: $alias)
                    .textFieldStyle(HudTextFieldStyle())
                    .disabled(model.isAdding)
                Text("别名仅用于内部识别和历史 CLI 命令；列表优先显示账号真实名称。实际身份按 chatgpt_account_id 匹配。")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            Toggle("使用设备码登录（浏览器不方便打开时）", isOn: $deviceAuth)
                .toggleStyle(.switch)
                .tint(RelayTheme.accent)
                .disabled(model.isAdding)

            HudNotice(color: RelayTheme.accent) {
                HStack(alignment: .top, spacing: 9) {
                    Image(systemName: "info.circle")
                        .foregroundStyle(RelayTheme.accent)
                    Text("点击开始后会在浏览器打开官方登录页。请选择要添加的那个账号；建议使用无痕窗口，避免又登进当前账号。")
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                }
            }

            if model.isAdding || !model.addLog.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        if model.isAdding {
                            ProgressView().controlSize(.small)
                            Text("登录进行中…")
                                .font(.hudLabel(8))
                                .foregroundStyle(RelayTheme.accent)
                        } else {
                            HudLabel(text: "登录输出", color: RelayTheme.textMuted)
                        }
                        Spacer()
                    }
                    ScrollViewReader { proxy in
                        ScrollView {
                            VStack(alignment: .leading, spacing: 2) {
                                ForEach(Array(model.addLog.enumerated()), id: \.offset) { index, line in
                                    Text(line)
                                        .font(.hudMono(9.5))
                                        .foregroundStyle(RelayTheme.textMuted)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        .id(index)
                                }
                            }
                            .padding(9)
                        }
                        .frame(height: 140)
                        .background(RelayTheme.panel)
                        .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
                        .overlay {
                            RoundedRectangle(cornerRadius: 4, style: .continuous)
                                .strokeBorder(RelayTheme.line.opacity(0.65), lineWidth: 0.7)
                        }
                        .onChange(of: model.addLog.count) { _, count in
                            if count > 0 {
                                withAnimation {
                                    proxy.scrollTo(count - 1, anchor: .bottom)
                                }
                            }
                        }
                    }
                }
            }

            Spacer(minLength: 0)

            HStack(spacing: 8) {
                if model.isAdding {
                    Button("取消登录") {
                        model.cancelAdd()
                    }
                    .buttonStyle(HudButtonStyle(primary: false))
                }
                Spacer()
                Button("关闭") {
                    if model.isAdding { model.cancelAdd() }
                    dismiss()
                }
                .buttonStyle(HudButtonStyle(primary: false))
                .keyboardShortcut(.cancelAction)
                Button("开始登录") {
                    model.addAccount(alias: effectiveAlias, deviceAuth: deviceAuth)
                }
                .buttonStyle(HudButtonStyle(primary: true))
                .keyboardShortcut(.defaultAction)
                .disabled(!aliasValid || model.isAdding)
            }
        }
        .padding(22)
        .frame(width: 600, height: 500)
        .background(HudBackground())
        .onDisappear {
            if model.isAdding {
                model.cancelAdd()
            }
        }
    }
}

struct SwitchProgressSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.locale) private var locale
    @State private var startedAt = Date()

    var body: some View {
        VStack(spacing: 18) {
            ZStack {
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(RelayTheme.accent.opacity(0.11))
                    .frame(width: 70, height: 70)
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .strokeBorder(RelayTheme.accent.opacity(0.28), lineWidth: 0.8)
                    .frame(width: 70, height: 70)
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(RelayTheme.accent)
                    .rotationEffect(.degrees(model.isSwitching ? 360 : 0))
                    .animation(
                        model.isSwitching
                            ? .linear(duration: 2.0).repeatForever(autoreverses: false)
                            : .default,
                        value: model.isSwitching
                    )
            }
            VStack(spacing: 5) {
                Text(model.switchStage.localized(locale: locale))
                    .font(.hudDisplay(18))
                    .foregroundStyle(RelayTheme.text)
                    .multilineTextAlignment(.center)
                Text("切换过程中请勿操作 ChatGPT。通常需要 6–20 秒。")
                    .font(.caption)
                    .foregroundStyle(RelayTheme.textMuted)
            }
            SwitchProgressBar(active: model.isSwitching)
                .frame(maxWidth: 380)
            TimelineView(.periodic(from: .now, by: 1)) { timeline in
                HStack(spacing: 7) {
                    Circle()
                        .fill(RelayTheme.accent)
                        .frame(width: 5, height: 5)
                    Text(L10n.format("已用时 %d 秒", Int(max(0, timeline.date.timeIntervalSince(startedAt)))))
                        .font(.hudMono(9))
                        .foregroundStyle(RelayTheme.textMuted)
                    Text("· 正在处理，请保持 ChatGPT 运行")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                    Spacer()
                }
                .frame(maxWidth: 380)
            }

            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 5) {
                        ForEach(Array(model.switchLog.enumerated()), id: \.offset) { index, line in
                            HStack(alignment: .top, spacing: 7) {
                                Rectangle()
                                    .fill(index == model.switchLog.count - 1 ? RelayTheme.accent : RelayTheme.green)
                                    .frame(width: 4, height: 4)
                                    .padding(.top, 5)
                                Text(line.localized(locale: locale))
                                    .font(.hudMono(9.5))
                                    .foregroundStyle(RelayTheme.textMuted)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .id(index)
                        }
                    }
                    .padding(10)
                }
                .frame(height: 145)
                .background(RelayTheme.panel)
                .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: 4, style: .continuous)
                        .strokeBorder(RelayTheme.line.opacity(0.65), lineWidth: 0.7)
                }
                .onChange(of: model.switchLog.count) { _, count in
                    if count > 0 {
                        withAnimation {
                            proxy.scrollTo(count - 1, anchor: .bottom)
                        }
                    }
                }
            }

            Text("失败会自动恢复备份并重启 ChatGPT。")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(24)
        .frame(width: 560, height: 460)
        .background(HudBackground())
        .onAppear { startedAt = Date() }
    }
}
