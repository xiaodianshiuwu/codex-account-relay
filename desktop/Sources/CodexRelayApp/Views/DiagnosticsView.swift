import CodexRelayCore
import SwiftUI

struct DiagnosticsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.locale) private var locale

    private var sortedChecks: [RelayDoctorCheck] {
        let checks = model.doctor?.checks ?? []
        return checks.sorted { rank($0.level) < rank($1.level) }
    }

    private func displayName(for check: String) -> String {
        if check.hasPrefix("profile:") {
            return L10n.format("账号：%@", String(check.dropFirst("profile:".count)))
        }
        let names: [String: String] = [
            "platform": "运行平台",
            "relay_home": "Relay 数据目录",
            "codex_cli": "Codex CLI",
            "desktop_app": "ChatGPT 桌面 App",
            "desktop_embedded_cli": "内置 Codex CLI",
            "auth_storage_mode": "凭据存储方式",
            "auth": "账号认证",
            "active_profile": "活动账号",
            "state_file": "状态文件",
            "profiles": "账号仓库",
            "state_db": "状态数据库",
            "thread_history_db": "会话数据库",
            "threads": "最近会话",
            "latest_thread": "最新会话",
            "desktop_processes": "桌面进程",
            "app_server_daemon": "共享 daemon",
            "network_auth": "认证网络",
            "network_backend": "后端网络",
            "backups": "备份",
            "last_event": "最近事件",
            "temp_dirs": "残留临时目录",
            "temp_cleanup": "清理残留临时目录",
        ]
        return names[check] ?? check
    }

    private func levelName(_ level: String) -> String {
        switch level {
        case "pass": return "通过"
        case "warn": return "警告"
        case "fail": return "失败"
        default: return level
        }
    }

    private func rank(_ level: String) -> Int {
        switch level {
        case "fail": return 0
        case "warn": return 1
        default: return 2
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                environmentCard
                if let doctor = model.doctor {
                    HStack(spacing: 12) {
                        summaryCard(
                            title: "失败项",
                            value: doctor.failures,
                            color: doctor.failures > 0 ? RelayTheme.red : RelayTheme.green
                        )
                        summaryCard(
                            title: "警告项",
                            value: doctor.warnings,
                            color: doctor.warnings > 0 ? RelayTheme.amber : RelayTheme.green
                        )
                        summaryCard(
                            title: "检查项",
                            value: doctor.checks.count,
                            color: RelayTheme.accent
                        )
                        Spacer()
                    }
                    RelayCard(accented: doctor.failures > 0) {
                        VStack(alignment: .leading, spacing: 8) {
                            HudLabel(text: "检查详情", color: RelayTheme.accent)
                            ForEach(sortedChecks) { check in
                                HStack(alignment: .top, spacing: 9) {
                                    Image(systemName: check.symbol)
                                        .foregroundStyle(RelayTheme.levelColor(check.level))
                                        .font(.system(size: 12, weight: .semibold))
                                        .frame(width: 18, height: 18)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(displayName(for: check.check).localized(locale: locale))
                                            .font(.system(size: 12, weight: .medium))
                                            .foregroundStyle(RelayTheme.text)
                                        Text(check.message.localized(locale: locale))
                                            .font(.caption)
                                            .foregroundStyle(RelayTheme.textMuted)
                                            .textSelection(.enabled)
                                    }
                                    Spacer()
                                    StatusBadge(
                                        text: levelName(check.level),
                                        color: RelayTheme.levelColor(check.level)
                                    )
                                }
                                .padding(.vertical, 4)
                                if check.id != sortedChecks.last?.id {
                                    HudDivider().opacity(0.6)
                                }
                            }
                        }
                    }
                } else {
                    RelayCard {
                        HStack {
                            Text(L10n.text("尚未运行诊断"))
                                .font(.callout)
                                .foregroundStyle(RelayTheme.textMuted)
                            Spacer()
                            Button {
                                Task { await model.refreshDoctor() }
                            } label: {
                                Label(L10n.text("运行诊断"), systemImage: "stethoscope")
                            }
                            .buttonStyle(HudButtonStyle(primary: true))
                        }
                    }
                }

                if let processes = model.status?.desktopProcesses, !processes.isEmpty {
                    RelayCard {
                        VStack(alignment: .leading, spacing: 8) {
                            HudLabel(text: "桌面进程", color: RelayTheme.accent)
                            ForEach(processes) { process in
                                HStack {
                                    Text("\(process.role ?? "-") · \(process.pid)")
                                        .font(.hudMono(10))
                                        .foregroundStyle(RelayTheme.text)
                                    Spacer()
                                    Text(process.started ?? "-")
                                        .font(.caption2)
                                        .foregroundStyle(RelayTheme.textMuted)
                                }
                                .padding(.vertical, 2)
                            }
                        }
                    }
                }
            }
            .padding(24)
            .frame(maxWidth: 980, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(Color.clear)
        .task {
            if model.doctor == nil {
                await model.refreshDoctor()
            }
        }
    }

    private var header: some View {
        HStack(alignment: .center) {
            HudSectionTitle(
                code: "04 / 诊断",
                title: "诊断",
                subtitle: "安装、认证、进程、数据库、网络与切换记录"
            )
            Spacer()
            if let doctor = model.doctor {
                StatusBadge(
                    text: doctor.ok ? "全部通过" : "需要处理",
                    color: doctor.ok ? RelayTheme.green : RelayTheme.red,
                    icon: doctor.ok ? "checkmark.circle" : "xmark.octagon"
                )
            }
            Button {
                Task { await model.refreshDoctor() }
            } label: {
                if model.isRefreshingDoctor {
                    ProgressView().controlSize(.small)
                } else {
                    Label(L10n.text("刷新"), systemImage: "arrow.clockwise")
                }
            }
            .buttonStyle(HudButtonStyle(primary: false))
            .disabled(model.isRefreshingDoctor)
        }
    }

    private var environmentCard: some View {
        RelayCard {
            VStack(alignment: .leading, spacing: 10) {
                HudLabel(text: "环境信息", color: RelayTheme.accent)
                KVRow(key: "codex-relay", value: model.backendVersion)
                KVRow(key: "CODEX_HOME", value: model.status?.codexHome ?? "~/.codex", monospaced: true)
                KVRow(key: "relay 仓库", value: model.status?.relayHome ?? "~/.codex-relay", monospaced: true)
                KVRow(key: "auth.json", value: model.status?.authPath ?? "-", monospaced: true)
                KVRow(
                    key: "桌面 App",
                    value: model.status?.desktopRunning == true ? L10n.text("运行中") : L10n.text("未运行")
                )
                KVRow(
                    key: "共享 daemon",
                    value: model.status?.daemonRunning == true ? L10n.text("运行中（可能缓存旧账号）") : L10n.text("未运行")
                )
                HStack(spacing: 8) {
                    Button(L10n.text("打开凭据仓库")) { model.openStore() }
                        .buttonStyle(HudButtonStyle(primary: false, compact: true))
                    Button(L10n.text("打开文档")) { model.openDocs() }
                        .buttonStyle(HudButtonStyle(primary: false, compact: true))
                    Button(L10n.text("打开最近会话")) { model.requestOpenApp() }
                        .buttonStyle(HudButtonStyle(primary: false, compact: true))
                    Spacer()
                }
                .padding(.top, 2)
            }
        }
    }

    private func summaryCard(title: String, value: Int, color: Color) -> some View {
        RelayCard(padding: 14) {
            VStack(alignment: .leading, spacing: 2) {
                HudLabel(text: title, color: RelayTheme.textMuted)
                Text("\(value)")
                    .font(.hudDisplay(25))
                    .foregroundStyle(color)
            }
        }
    }
}
