import CodexRelayCore
import SwiftUI

struct SidebarView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var preferences: AppPreferences
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.locale) private var locale

    var body: some View {
        VStack(spacing: 0) {
            header
            HazardStripe(color: colorScheme == .light ? RelayTheme.amber : RelayTheme.accent, height: 2)
                .opacity(colorScheme == .light ? 0.55 : 0.65)
                .padding(.horizontal, 14)
            nav
            Spacer(minLength: 0)
            accountFooter
        }
        .background {
            LinearGradient(
                colors: [RelayTheme.panel, RelayTheme.panelElevated.opacity(0.55)],
                startPoint: .top,
                endPoint: .bottom
            )
        }
        .overlay(alignment: .trailing) {
            Rectangle().fill(RelayTheme.line).frame(width: 1)
        }
    }

    private var header: some View {
        HStack(spacing: 11) {
            ZStack {
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [RelayTheme.accent, RelayTheme.accentDim],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: 36, height: 36)
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .stroke(RelayTheme.onAccent.opacity(0.35), lineWidth: 1)
                    .frame(width: 23, height: 23)
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: 13, weight: .black))
                    .foregroundStyle(RelayTheme.onAccent)
            }
            .overlay {
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .strokeBorder(RelayTheme.accent.opacity(0.30), lineWidth: 0.7)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("CODEX RELAY")
                    .font(.hudDisplay(17))
                    .tracking(1.1)
                    .foregroundStyle(RelayTheme.text)
                Text("// 账号与会话接力")
                    .font(.hudLabel(8))
                    .tracking(1.4)
                    .foregroundStyle(RelayTheme.accent)
            }
            Spacer()
        }
        .padding(.horizontal, 15)
        .padding(.top, 17)
        .padding(.bottom, 12)
    }

    private var nav: some View {
        VStack(spacing: 4) {
            ForEach(Array(SidebarSection.allCases.enumerated()), id: \.element) { index, item in
                SidebarNavButton(
                    index: index + 1,
                    item: item,
                    selected: model.section == item
                ) {
                    // Page changes are intentionally not animated: animating
                    // the whole detail tree made the split panes resize and
                    // briefly clip at windowed sizes.
                    model.section = item
                }
            }
        }
        .padding(.horizontal, 10)
        .padding(.top, 12)
    }

    private var accountFooter: some View {
        VStack(alignment: .leading, spacing: 9) {
            HudDivider()
            HStack {
                HudLabel(text: "当前账号", color: RelayTheme.accent)
                Spacer()
                Text(L10n.format("版本 %@", AppVersion.display))
                    .font(.hudLabel(8))
                    .foregroundStyle(.tertiary)
            }
            if let account = model.currentAccount {
                HStack(spacing: 8) {
                    AccountAvatar(account: account, size: 20, cornerRadius: 3)
                    Rectangle()
                        .fill(account.isHealthy
                              ? (account.isExhausted ? RelayTheme.amber : RelayTheme.green)
                              : RelayTheme.amber)
                        .frame(width: 6, height: 6)
                    Text(account.displayName(locale: locale))
                        .font(.hudMono(12, weight: .bold))
                        .foregroundStyle(RelayTheme.text)
                        .lineLimit(1)
                    Spacer()
                    Text(account.plan?.uppercased() ?? "-")
                        .font(.hudLabel(8))
                        .foregroundStyle(RelayTheme.textMuted)
                }
                Text(account.email ?? "-")
                    .font(.caption2)
                    .foregroundStyle(RelayTheme.textMuted)
                    .lineLimit(1)
                UsageBar(title: "5 小时", window: account.usage?.primary, compact: true, mode: preferences.quotaDisplayMode)
                UsageBar(title: "1 周", window: account.usage?.secondary, compact: true, mode: preferences.quotaDisplayMode)
                if let error = model.usageSyncError {
                    Label("同步失败", systemImage: "exclamationmark.triangle.fill")
                        .font(.hudLabel(8))
                        .foregroundStyle(RelayTheme.red)
                        .help(error)
                } else if let last = model.lastUsageSync {
                    Text(L10n.format("同步 // %@", last.localizedString(locale: Locale(identifier: preferences.language.rawValue))))
                        .font(.hudLabel(8))
                        .foregroundStyle(.tertiary)
                }
            } else if let auth = model.unmanagedAuth {
                Text("未纳入账号仓库")
                    .font(.hudLabel(8))
                    .foregroundStyle(RelayTheme.amber)
                Text(auth.email ?? "-")
                    .font(.caption2)
                    .foregroundStyle(RelayTheme.textMuted)
                Button("捕获当前账号") { model.captureCurrentAccount() }
                    .buttonStyle(HudButtonStyle(primary: true))
            } else {
                Text("未检测到活动账号")
                    .font(.hudLabel(9))
                    .foregroundStyle(RelayTheme.amber)
            }

            HStack(spacing: 6) {
                Button {
                    Task { await model.refreshUsage() }
                } label: {
                    Label("同步", systemImage: "arrow.triangle.2.circlepath")
                }
                .buttonStyle(HudButtonStyle(primary: false, compact: true))
                .disabled(model.isRefreshingUsage || model.isSwitching || model.isOpeningProcess)

                Menu {
                    Picker("外观", selection: $preferences.appearance) {
                        ForEach(RelayAppearance.allCases) { option in
                            Label(L10n.text(option.title), systemImage: option.icon).tag(option)
                        }
                    }
                } label: {
                    Image(systemName: preferences.appearance.icon)
                }
                .buttonStyle(HudButtonStyle(primary: false, compact: true))
                .help("切换外观")

                Button {
                    model.beginAddAccount()
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(HudButtonStyle(primary: false, compact: true))
                .help("添加账号")
            }
        }
        .padding(13)
    }
}

private struct SidebarNavButton: View {
    @Environment(\.locale) private var locale
    let index: Int
    let item: SidebarSection
    let selected: Bool
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                Text(String(format: "%02d", index))
                    .font(.hudLabel(8))
                    .foregroundStyle(selected ? RelayTheme.accent : RelayTheme.textMuted.opacity(0.7))
                Image(systemName: item.icon)
                    .font(.system(size: 11, weight: .semibold))
                    .frame(width: 15)
                Text(item.title.localized(locale: locale))
                    .font(.system(size: 12, weight: selected ? .semibold : .regular))
                Spacer()
                if selected {
                    Rectangle().fill(RelayTheme.accent).frame(width: 3, height: 14)
                }
            }
            .foregroundStyle(selected ? RelayTheme.text : RelayTheme.textMuted)
            .padding(.horizontal, 9)
            .padding(.vertical, 8)
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
                    .strokeBorder(selected ? RelayTheme.accent.opacity(0.25) : Color.clear, lineWidth: 0.7)
            }
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}
