import SwiftUI
import CodexRelayCore

@MainActor
final class GuardViewModel: ObservableObject {
    @Published var snapshot: GuardSnapshot?
    @Published var settings: GuardSettings?
    @Published var busy = false
    @Published var error: String?
    @Published var selectedHost = ""
    @Published var search = ""
    @Published var selectedKey: String?
    private let backend = RelayBackend()

    var threads: [GuardThread] {
        (snapshot?.threads ?? []).filter {
            (selectedHost.isEmpty || $0.hostId == selectedHost) &&
            (search.isEmpty || ($0.name + " " + ($0.cwd ?? "") + " " + $0.id).localizedCaseInsensitiveContains(search))
        }
    }
    func refresh() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        let backend = backend
        do {
            let value = try await Task.detached { try backend.guardSnapshot() }.value
            snapshot = value
            if settings == nil { settings = value.settings }
            error = nil
        } catch { self.error = error.relayLocalizedDescription }
    }
    func action(_ action: String, threadID: String, hostID: String) async {
        guard !busy else { return }
        busy = true
        let backend = backend
        do {
            if action == "background", let settings {
                try await Task.detached { try backend.saveGuardSettings(settings) }.value
            }
            try await Task.detached { try backend.guardAction(action, threadID: threadID, hostID: hostID) }.value
            error = nil
        } catch { self.error = error.relayLocalizedDescription }
        busy = false
        // Refresh after an action without clearing a failed-action message.
        let actionError = error
        await refresh()
        if let actionError { error = actionError }
    }
    func save() async {
        guard !busy, let settings else { return }
        busy = true
        defer { busy = false }
        let backend = backend
        do {
            try await Task.detached { try backend.saveGuardSettings(settings) }.value
            error = nil
        } catch { self.error = error.relayLocalizedDescription }
    }
}

@MainActor
struct GuardView: View {
    @StateObject private var guardModel: GuardViewModel
    private let isPreview: Bool
    init(preview: GuardSnapshot? = nil) {
        let model = GuardViewModel()
        model.snapshot = preview; model.settings = preview?.settings
        _guardModel = StateObject(wrappedValue: model)
        isPreview = preview != nil
    }
    @EnvironmentObject private var appModel: AppModel
    @EnvironmentObject private var preferences: AppPreferences

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HudSectionTitle(code: "04 / GUARD", title: "额度守护",
                                subtitle: "小时额度低时中断，恢复后在原聊天继续；无需重启 ChatGPT")
                HStack {
                    Picker(L10n.text("主机"), selection: $guardModel.selectedHost) {
                        Text(L10n.text("所有主机")).tag("")
                        ForEach(guardModel.snapshot?.hosts ?? []) { Text($0.name).tag($0.id) }
                    }.frame(maxWidth: 240)
                    TextField(L10n.text("搜索聊天"), text: $guardModel.search).textFieldStyle(.roundedBorder)
                    Button(L10n.text("刷新")) { Task { await guardModel.refresh() } }.disabled(guardModel.busy)
                    if guardModel.busy { ProgressView().controlSize(.small) }
                }
                if let error = guardModel.error {
                    Text(error).foregroundStyle(RelayTheme.red).textSelection(.enabled)
                }
                ForEach(guardModel.snapshot?.warnings ?? [], id: \.self) { Text($0).foregroundStyle(RelayTheme.amber) }
                ForEach(guardModel.snapshot?.hosts ?? []) { host in
                    RelayCard {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(host.name).font(.headline)
                            Text(host.quotaReason ?? host.reason ?? (host.connected ? L10n.text("已连接") : L10n.text("未连接")))
                                .font(.caption).foregroundStyle(RelayTheme.textMuted)
                            ForEach(host.windows, id: \.name) { window in
                                HStack {
                                    Text(window.name == "primary" ? L10n.text("5 小时额度") : L10n.text("每周额度"))
                                    ProgressView(value: window.remaining, total: 100).frame(maxWidth: 240)
                                    Text(window.remaining.formatted(.number.precision(.fractionLength(1))) + "%")
                                    Text(Date(timeIntervalSince1970: window.resetsAt), style: .relative)
                                        .foregroundStyle(RelayTheme.textMuted)
                                }.font(.caption)
                            }
                            if let forecast = host.forecast, !forecast.slots.isEmpty {
                                Text(L10n.text("后续窗口估算")).font(.caption.bold())
                                ForEach(Array(forecast.slots.enumerated()), id: \.offset) { _, slot in
                                    HStack {
                                        Text(slot.resetConfirmed ? L10n.text("接口重置时间") : L10n.text("预计下一轮"))
                                        Text(Date(timeIntervalSince1970: slot.start), style: .date)
                                        Text(Date(timeIntervalSince1970: slot.start), style: .time)
                                        Text("→")
                                        Text(Date(timeIntervalSince1970: slot.end), style: .time)
                                    }.font(.caption2).foregroundStyle(RelayTheme.textMuted)
                                }
                                Text(L10n.text("后续窗口按立即持续使用估算；实际可用时间以接口为准。"))
                                    .font(.caption2).foregroundStyle(RelayTheme.textMuted)
                            }
                        }
                    }
                }
                if guardModel.settings != nil { settingsCard }
                RelayCard {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(L10n.text("选择原聊天")).font(.headline)
                        Text(L10n.text("请先在 Codex 中打开目标聊天；开始后会检查原轮次、模型、目录和权限。"))
                            .font(.caption).foregroundStyle(RelayTheme.textMuted)
                        ForEach(guardModel.threads, id: \.key) { thread in
                            HStack {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(thread.name).lineLimit(1)
                                    Text(thread.hostName + " · " + (thread.cwd ?? thread.id))
                                        .font(.caption2).foregroundStyle(RelayTheme.textMuted).lineLimit(1)
                                }
                                Spacer()
                                Button(L10n.text("开始守护")) {
                                    Task { await guardModel.action("background", threadID: thread.id, hostID: thread.hostId) }
                                }.disabled(guardModel.busy || appModel.isSwitching || preferences.autoContinueEnabled && preferences.autoAllowRestart ||
                                    guardModel.snapshot?.monitors.contains(where: { $0.threadId == thread.id && $0.hostId == thread.hostId && $0.monitoring }) == true)
                            }.padding(.vertical, 4)
                        }
                        if guardModel.threads.isEmpty { Text(L10n.text("暂无可显示的聊天")).foregroundStyle(RelayTheme.textMuted) }
                    }
                }
                ForEach(guardModel.snapshot?.monitors ?? []) { monitor in
                    RelayCard {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(monitor.threadId).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                                Text(monitor.reason ?? monitor.displayPhase).font(.caption)
                                Text(L10n.format("已续跑 %d 次", monitor.resumeCount)).font(.caption2).foregroundStyle(RelayTheme.textMuted)
                            }
                            Spacer()
                            Button(L10n.text("停止守护")) {
                                Task { await guardModel.action("stop", threadID: monitor.threadId, hostID: monitor.hostId) }
                            }.disabled(guardModel.busy || !monitor.monitoring)
                        }
                    }
                }
                Text(L10n.text("开始后在后台运行，退出 Relay 也会继续；停止守护仅取消后续操作。周额度只显示；未知回执不会自动重发。"))
                    .font(.caption).foregroundStyle(RelayTheme.textMuted)
            }.padding(24)
        }
        .background(HudBackground())
        .task {
            guard !isPreview else { return }
            await guardModel.refresh()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                await guardModel.refresh()
            }
        }
    }

    private var settingsCard: some View {
        RelayCard {
            VStack(alignment: .leading, spacing: 10) {
                Text(L10n.text("守护设置")).font(.headline)
                HStack {
                    numberField("停止阈值（%）", \.threshold)
                    numberField("恢复阈值（%）", \.resumeRemaining)
                    numberField("检查间隔（秒）", \.interval)
                }
                Text(L10n.text("续跑提示词")).font(.caption)
                TextEditor(text: Binding(get: { guardModel.settings?.continuation ?? "" },
                                         set: { guardModel.settings?.continuation = $0 }))
                    .font(.body).frame(minHeight: 75)
                HStack {
                    Button(L10n.text("保存设置")) { Task { await guardModel.save() } }.disabled(guardModel.busy)
                    Text(L10n.text("修改只影响下次开始的监控")).font(.caption).foregroundStyle(RelayTheme.textMuted)
                }
            }
        }
    }
    private func numberField(_ title: String, _ path: WritableKeyPath<GuardSettings, Double>) -> some View {
        VStack(alignment: .leading) {
            Text(L10n.text(title)).font(.caption)
            TextField("", value: Binding(get: { guardModel.settings?[keyPath: path] ?? 0 },
                                         set: { guardModel.settings?[keyPath: path] = $0 }), format: .number)
                .textFieldStyle(.roundedBorder)
        }
    }
}
