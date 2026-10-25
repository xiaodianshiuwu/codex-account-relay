import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var preferences: AppPreferences

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HudSectionTitle(
                    code: "05 / 设置",
                    title: "设置",
                    subtitle: "让额度、数字和界面更符合你的使用习惯"
                )

                RelayCard {
                    VStack(alignment: .leading, spacing: 14) {
                        HudLabel(text: "界面语言", color: RelayTheme.accent)
                        Text("切换后界面会立即更新；语言偏好会在下次启动时保持。")
                            .font(.caption)
                            .foregroundStyle(RelayTheme.textMuted)

                        LazyVGrid(
                            columns: [GridItem(.adaptive(minimum: 190), spacing: 8)],
                            alignment: .leading,
                            spacing: 8
                        ) {
                            ForEach(AppLanguage.allCases) { option in
                                PreferenceOption(
                                    title: option.nativeTitle,
                                    subtitle: languageDescription(option),
                                    icon: option == .zhCN ? "character.book.closed" : (option == .ja ? "character" : "globe"),
                                    selected: preferences.language == option
                                ) {
                                    preferences.language = option
                                }
                            }
                        }
                    }
                }

                RelayCard {
                    VStack(alignment: .leading, spacing: 14) {
                        HudLabel(text: "额度显示", color: RelayTheme.accent)
                        Text("选择所有额度卡和进度条统一使用的显示方式。原始数据始终按「已使用百分比」读取，切换显示不会改变额度含义。")
                            .font(.caption)
                            .foregroundStyle(RelayTheme.textMuted)

                        HStack(spacing: 8) {
                            ForEach(QuotaDisplayMode.allCases) { option in
                                PreferenceOption(
                                    title: option.title,
                                    subtitle: option.explanation,
                                    icon: option == .remaining ? "battery.75percent" : "chart.bar.fill",
                                    selected: preferences.quotaDisplayMode == option
                                ) {
                                    preferences.quotaDisplayMode = option
                                }
                            }
                        }
                    }
                }

                RelayCard {
                    VStack(alignment: .leading, spacing: 14) {
                        HudLabel(text: "数字格式", color: RelayTheme.accent)
                        Text("影响 Token、缓存和推理等大数字的显示方式；简写只改变展示，不改变统计数据。")
                            .font(.caption)
                            .foregroundStyle(RelayTheme.textMuted)

                        HStack(spacing: 8) {
                            ForEach(NumberFormatMode.allCases) { option in
                                PreferenceOption(
                                    title: option.title,
                                    subtitle: option.explanation,
                                    icon: option == .full ? "textformat.123" : "textformat.size.smaller",
                                    selected: preferences.numberFormatMode == option
                                ) {
                                    preferences.numberFormatMode = option
                                }
                            }
                        }
                    }
                }

                RelayCard {
                    VStack(alignment: .leading, spacing: 14) {
                        HudLabel(text: "外观主题", color: RelayTheme.accent)
                        Text("选择后会立即应用到主窗口、弹窗、状态颜色和设置页。")
                            .font(.caption)
                            .foregroundStyle(RelayTheme.textMuted)

                        HStack(spacing: 8) {
                            ForEach(RelayAppearance.allCases) { option in
                                PreferenceOption(
                                    title: option.title,
                                    subtitle: appearanceDescription(option),
                                    icon: option.icon,
                                    selected: preferences.appearance == option
                                ) {
                                    preferences.appearance = option
                                }
                            }
                        }
                    }
                }

                RelayCard {
                    VStack(alignment: .leading, spacing: 14) {
                        HudLabel(text: "自动续跑", color: RelayTheme.accent)
                        Text("达到剩余额度阈值时切换账号，并在原 thread 上发送续跑提示词。配置会写入 ~/.codex-relay/auto.json，供 `codex-relay auto` 守护读取。")
                            .font(.caption)
                            .foregroundStyle(RelayTheme.textMuted)

                        Toggle("启用自动续跑", isOn: $preferences.autoContinueEnabled)
                            .toggleStyle(.switch)

                        VStack(alignment: .leading, spacing: 6) {
                            Text("续跑提示词")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundStyle(RelayTheme.text)
                            TextField("例如：继续", text: $preferences.autoContinuePrompt)
                                .textFieldStyle(.roundedBorder)
                        }

                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text("剩余额度阈值")
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundStyle(RelayTheme.text)
                                Spacer()
                                Text("\(Int(preferences.autoRemainingThreshold))%")
                                    .font(.system(size: 12, design: .monospaced))
                                    .foregroundStyle(RelayTheme.accent)
                            }
                            Slider(value: $preferences.autoRemainingThreshold, in: 0...100, step: 1)
                            Text("剩余额度低于该百分比时准备切换；0% 表示只在额度耗尽时切换。")
                                .font(.caption2)
                                .foregroundStyle(RelayTheme.textMuted)
                        }

                        Text("自动切换会重启 ChatGPT，必须由用户在终端显式启动守护并加 --allow-chatgpt-restart --yes；不会在后台静默操作。")
                            .font(.caption2)
                            .foregroundStyle(RelayTheme.textMuted)
                    }
                }

                RelayCard {
                    VStack(alignment: .leading, spacing: 10) {
                        HudLabel(text: "数据与同步", color: RelayTheme.accent)
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text("额度同步")
                                    .font(.system(size: 12, weight: .medium))
                                    .foregroundStyle(RelayTheme.text)
                                Text(syncDescription)
                                    .font(.caption)
                                    .foregroundStyle(RelayTheme.textMuted)
                            }
                            Spacer()
                            Button {
                                Task { await model.refreshUsage() }
                            } label: {
                                if model.isRefreshingUsage {
                                    ProgressView().controlSize(.small)
                                } else {
                                    Label("立即同步", systemImage: "arrow.clockwise")
                                }
                            }
                            .buttonStyle(HudButtonStyle(primary: false))
                            .disabled(model.isRefreshingUsage || model.isSwitching || model.isOpeningProcess)
                        }
                        HudDivider()
                        HStack(spacing: 8) {
                            Button("打开凭据仓库") { model.openStore() }
                                .buttonStyle(HudButtonStyle(primary: false, compact: true))
                            Button("打开使用文档") { model.openDocs() }
                                .buttonStyle(HudButtonStyle(primary: false, compact: true))
                            Spacer()
                        }
                    }
                }

            }
            .padding(24)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(Color.clear)
    }

    private func languageDescription(_ language: AppLanguage) -> String {
        switch language {
        case .zhCN: return "简体中文界面"
        case .zhTW: return "繁體中文介面"
        case .en: return "English interface"
        case .ja: return "日本語インターフェース"
        }
    }

    private var syncDescription: String {
        if let last = model.lastUsageSync {
            return L10n.format("活动账号每 10 秒同步；上次同步 %@。", last.localizedString(locale: preferences.language.locale))
        }
        return L10n.text("活动账号每 10 秒同步；启动时及每 30 秒全量刷新。")
    }

    private func appearanceDescription(_ appearance: RelayAppearance) -> String {
        switch appearance {
        case .system: return "随 macOS 自动切换"
        case .dark: return "深色战术终端"
        case .light: return "浅色档案界面"
        }
    }
}

private struct PreferenceOption: View {
    @Environment(\.locale) private var locale
    let title: String
    let subtitle: String
    let icon: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 7) {
                    Image(systemName: icon)
                    Text(title.localized(locale: locale))
                        .font(.system(size: 12, weight: .semibold))
                    Spacer()
                    if selected {
                        Image(systemName: "checkmark")
                            .font(.system(size: 9, weight: .bold))
                    }
                }
                .foregroundStyle(selected ? RelayTheme.accent : RelayTheme.text)
                Text(subtitle.localized(locale: locale))
                    .font(.caption2)
                    .foregroundStyle(RelayTheme.textMuted)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(11)
            .frame(maxWidth: .infinity, minHeight: 78, alignment: .topLeading)
            .background(selected ? RelayTheme.accent.opacity(0.10) : RelayTheme.panelElevated.opacity(0.55))
            .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .strokeBorder(selected ? RelayTheme.accent.opacity(0.55) : RelayTheme.line.opacity(0.6), lineWidth: 0.7)
            }
        }
        .buttonStyle(.plain)
    }
}
