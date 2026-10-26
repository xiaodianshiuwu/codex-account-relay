import SwiftUI

struct AboutView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.locale) private var locale

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                HStack(alignment: .center, spacing: 16) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(
                                LinearGradient(
                                    colors: [RelayTheme.accent, RelayTheme.purple],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                            .frame(width: 66, height: 66)
                        Rectangle()
                            .stroke(RelayTheme.onAccent.opacity(0.35), lineWidth: 1)
                            .frame(width: 44, height: 44)
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .font(.system(size: 28, weight: .bold))
                            .foregroundStyle(RelayTheme.onAccent)
                    }
                    .overlay {
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .strokeBorder(RelayTheme.accent.opacity(0.35), lineWidth: 0.8)
                    }

                    VStack(alignment: .leading, spacing: 4) {
                        HudLabel(text: "06 / 系统信息", color: RelayTheme.accent)
                        Text("Codex Relay")
                            .font(.hudDisplay(30))
                            .foregroundStyle(RelayTheme.text)
                        Text(L10n.text("多账号 · 原会话接力 · 推理与缓存诊断"))
                            .font(.callout)
                            .foregroundStyle(RelayTheme.textMuted)
                        Text(L10n.format("桌面版 %@", AppVersion.display))
                            .font(.hudLabel(8))
                            .foregroundStyle(.tertiary)
                    }
                    Spacer()
                    HazardStripe(color: RelayTheme.amber, height: 3)
                        .frame(width: 100)
                        .opacity(0.7)
                }

                RelayCard {
                    VStack(alignment: .leading, spacing: 10) {
                        HudLabel(text: "运行信息", color: RelayTheme.accent)
                        KVRow(key: "CLI", value: model.backend.binaryPath ?? L10n.text("未找到"), monospaced: true)
                        KVRow(key: "CLI 版本", value: localizedBackendVersion(model.backendVersion))
                        KVRow(key: "账号仓库", value: model.status?.relayHome ?? "~/.codex-relay", monospaced: true)
                        KVRow(key: "Codex 数据", value: model.status?.codexHome ?? "~/.codex", monospaced: true)
                    }
                }

                RelayCard {
                    VStack(alignment: .leading, spacing: 10) {
                        HudLabel(text: "安全边界", color: RelayTheme.amber)
                        bullet("账号凭据按 0600 保存，切换前自动备份；工具不打印 token。")
                        bullet("桌面 App 通过官方支持的“替换 auth.json + 重启”切换，不做进程内凭据注入。")
                        bullet("不会后台自动启停 ChatGPT；每次切换/重启/打开会话都要在确认框勾选，CLI 必须显式 --allow-chatgpt-restart。")
                        bullet("同一 CODEX_HOME 只允许一个写者；检测到其他 Codex 进程时会拒绝切换。")
                        bullet("加密推理项会原样回放，但服务端是否实际复用无法从客户端证明。")
                        bullet("Prompt Cache 按组织隔离，跨账号首轮不会命中。")
                    }
                }

                HStack(spacing: 8) {
                    Button {
                        model.openDocs()
                    } label: {
                        Label(L10n.text("打开完整文档"), systemImage: "book")
                    }
                    .buttonStyle(HudButtonStyle(primary: true))
                    Button {
                        model.openStore()
                    } label: {
                        Label(L10n.text("打开凭据仓库"), systemImage: "folder")
                    }
                    .buttonStyle(HudButtonStyle(primary: false))
                    Button {
                        model.requestOpenApp()
                    } label: {
                        Label(L10n.text("打开最近会话"), systemImage: "arrow.up.forward.app")
                    }
                    .buttonStyle(HudButtonStyle(primary: false))
                    Spacer()
                }
            }
            .padding(24)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(Color.clear)
    }

    private func bullet(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Rectangle()
                .fill(RelayTheme.accent.opacity(0.75))
                .frame(width: 6, height: 2)
                .padding(.top, 7)
            Text(text.localized(locale: locale))
                .font(.caption)
                .foregroundStyle(RelayTheme.textMuted)
        }
    }

    /// The CLI `--version` output is a multi-line human-readable string and
    /// may be cached in the CLI process language. Localize the labels at the
    /// presentation boundary so the About page follows the app language even
    /// when an older installed CLI is used.
    private func localizedBackendVersion(_ raw: String) -> String {
        let labels = ["桌面 App", "内置 CLI"]
        return raw.split(separator: "\n", omittingEmptySubsequences: false).map { line in
            for label in labels {
                let prefix = label + ": "
                if line.hasPrefix(prefix) {
                    return label.localized(locale: locale) + ": " + line.dropFirst(prefix.count)
                }
            }
            return String(line)
        }.joined(separator: "\n")
    }

}
