import CodexRelayCore
import SwiftUI

struct SessionsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.locale) private var locale

    var body: some View {
        GeometryReader { proxy in
            // HSplitView uses the detail's large ideal width when the window is
            // windowed, which can push the sidebar off-screen. A bounded HStack
            // keeps both panes inside the actual window at every size.
            let listWidth = min(max(proxy.size.width * 0.30, 260), 360)
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
    }

    private var listPane: some View {
        VStack(spacing: 0) {
            header
            HudDivider()
            if model.filteredSessions.isEmpty {
                EmptyStateView(
                    icon: "bubble.left.and.exclamationmark.bubble.right",
                    title: "没有匹配的会话",
                    message: "调整搜索或账号筛选，或先在 Codex 中开始一个会话。"
                )
            } else {
                ScrollView {
                    LazyVStack(spacing: 6) {
                        ForEach(model.filteredSessions) { session in
                            SessionRow(
                                session: session,
                                creatorName: model.preferredName(forAlias: session.creatorAlias),
                                selected: model.selectedSessionID == session.id,
                                isActiveAccount: session.creatorAlias == model.currentAccount?.alias
                            ) {
                                model.selectedSessionID = session.id
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
        if let session = model.selectedSession {
            SessionDetailView(session: session)
                .id(session.id)
        } else {
            EmptyStateView(
                icon: "bubble.left.and.bubble.right",
                title: "选择一个会话",
                message: "查看创建账号、工作目录，并切换账号后继续同一会话。"
            )
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center) {
                VStack(alignment: .leading, spacing: 3) {
                    HudLabel(text: "03 / 会话档案", color: RelayTheme.accent)
                    Text("会话")
                        .font(.hudDisplay(24))
                        .foregroundStyle(RelayTheme.text)
                    Text(L10n.format("%d 条 · 恢复原会话", model.filteredSessions.count))
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                }
                Spacer()
                if model.isRefreshing {
                    ProgressView().controlSize(.small)
                }
            }

            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(RelayTheme.textMuted)
                TextField("搜索标题、目录、ID", text: $model.searchText)
                    .textFieldStyle(.plain)
                    .font(.system(size: 11.5))
                if !model.searchText.isEmpty {
                    Button {
                        model.searchText = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(RelayTheme.textMuted)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(RelayTheme.panelElevated)
            .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .strokeBorder(RelayTheme.line.opacity(0.65), lineWidth: 0.7)
            }

            Menu {
                Button("全部账号") { model.accountFilter = nil }
                Divider()
                ForEach(model.accounts) { account in
                    Button {
                        model.accountFilter = account.alias
                    } label: {
                        if model.accountFilter == account.alias {
                            Label(account.displayName(locale: locale), systemImage: "checkmark")
                        } else {
                            Text(account.displayName(locale: locale))
                        }
                    }
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "line.3.horizontal.decrease.circle")
                    Text(model.accountFilter.flatMap { model.preferredName(forAlias: $0) } ?? L10n.text("全部账号"))
                    Image(systemName: "chevron.down")
                        .font(.system(size: 8, weight: .bold))
                }
                .font(.hudLabel(8.5))
                .foregroundStyle(RelayTheme.textMuted)
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
                .background(RelayTheme.panelElevated)
                .clipShape(RoundedRectangle(cornerRadius: 3, style: .continuous))
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .frame(maxWidth: 180, alignment: .leading)
        }
        .padding(16)
    }
}

private struct SessionRow: View {
    @Environment(\.locale) private var locale
    let session: RelaySession
    let creatorName: String?
    let selected: Bool
    let isActiveAccount: Bool
    let action: () -> Void

    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 6) {
                    Text(session.displayTitle(locale: locale))
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(RelayTheme.text)
                        .lineLimit(1)
                    if isActiveAccount {
                        StatusBadge(text: "当前账号", color: RelayTheme.green)
                    }
                    Spacer(minLength: 4)
                    Text(session.shortID)
                        .font(.hudMono(8))
                        .foregroundStyle(.tertiary)
                }
                HStack(spacing: 8) {
                    Label(creatorName ?? L10n.text("未归属"), systemImage: "person.crop.circle")
                    Text("·")
                    Text(session.updatedLocal ?? "-")
                    Spacer()
                }
                .font(.hudLabel(8))
                .foregroundStyle(RelayTheme.textMuted)
                HStack(spacing: 6) {
                    Image(systemName: "folder").font(.system(size: 9))
                    Text(session.cwd ?? "-")
                        .font(.caption2)
                        .lineLimit(1)
                        .foregroundStyle(.tertiary)
                }
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

private struct SessionDetailView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var preferences: AppPreferences
    @Environment(\.locale) private var locale
    let session: RelaySession

    private var creatorAccount: RelayAccount? {
        guard let accountID = session.creatorAccountId else { return nil }
        return model.accounts.first(where: { $0.accountId == accountID })
    }

    private var inspection: RelayInspectReport? {
        model.inspections[session.id]
    }

    private var inspectionError: String? {
        model.inspectionErrors[session.id]
    }

    private var isInspecting: Bool {
        model.inspectingSessionID == session.id
    }

    private var anotherInspectionRunning: Bool {
        guard let running = model.inspectingSessionID else { return false }
        return running != session.id
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                actionBar
                RelayCard {
                    VStack(alignment: .leading, spacing: 10) {
                        HudLabel(text: "会话信息", color: RelayTheme.accent)
                        KVRow(key: "会话 ID", value: session.id, monospaced: true)
                        KVRow(key: "工作目录", value: session.cwd ?? "-", monospaced: true)
                        KVRow(key: "创建者", value: session.creatorAlias ?? session.creatorAccountId ?? L10n.text("未归属"))
                        KVRow(key: "来源", value: session.source ?? "-")
                        KVRow(key: "模型", value: session.model ?? "-")
                        KVRow(key: "推理强度", value: session.reasoningEffort ?? "-")
                        KVRow(key: "累计 Token", value: NumberDisplay.integer(session.tokensUsed, mode: preferences.numberFormatMode, locale: locale))
                        KVRow(key: "更新时间", value: session.updatedLocal ?? "-")
                    }
                }

                if let metrics = inspection?.metrics {
                    analysisHeader
                    metricsView(metrics)
                } else if isInspecting {
                    analysisLoadingCard
                } else if anotherInspectionRunning {
                    analysisWaitingCard
                } else if let error = inspectionError {
                    analysisErrorCard(error)
                } else if inspection != nil {
                    analysisEmptyCard
                } else {
                    analysisLoadingCard
                }
            }
            .padding(24)
            .frame(maxWidth: 900, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(Color.clear)
        .task(id: session.id) {
            await model.autoInspectSession(session)
        }
        .onChange(of: model.inspectingSessionID) { _, running in
            if running == nil, inspection == nil, inspectionError == nil {
                Task { await model.autoInspectSession(session) }
            }
        }
    }

    private var analysisHeader: some View {
        HStack(spacing: 8) {
            HudLabel(text: "缓存与推理分析", color: RelayTheme.accent)
            if let fetched = model.inspectionFetchedAt[session.id] {
                Text(L10n.format("读取于 %@", fetched.localizedString(locale: locale)))
                    .font(.hudLabel(8))
                    .foregroundStyle(RelayTheme.textMuted)
            }
            Spacer()
            if isInspecting {
                ProgressView().controlSize(.small)
            } else if inspectionError != nil {
                Text("上次刷新失败")
                    .font(.hudLabel(8))
                    .foregroundStyle(RelayTheme.red)
            }
            Button {
                Task { await model.inspectSession(session) }
            } label: {
                Label("重新分析", systemImage: "arrow.clockwise")
            }
            .buttonStyle(HudButtonStyle(primary: false, compact: true))
            .disabled(isInspecting)
        }
    }

    private var analysisLoadingCard: some View {
        RelayCard {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 4, style: .continuous)
                        .fill(RelayTheme.accent.opacity(0.12))
                        .frame(width: 38, height: 38)
                    Image(systemName: "waveform.path.ecg")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(RelayTheme.accent)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text("正在读取缓存与推理数据…")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(RelayTheme.text)
                    Text("读取会话记录、加密推理项、压缩检查点和逐轮缓存命中率。")
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                }
                Spacer()
                ProgressView().controlSize(.small)
            }
        }
    }

    private var analysisWaitingCard: some View {
        RelayCard {
            HStack(spacing: 12) {
                Image(systemName: "hourglass")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(RelayTheme.amberText)
                VStack(alignment: .leading, spacing: 2) {
                    Text("另一条会话正在分析")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(RelayTheme.text)
                    Text("当前分析完成后会自动读取这条会话；也可以稍后手动重试。")
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                }
                Spacer()
                Button("立即分析") {
                    Task { await model.inspectSession(session) }
                }
                .buttonStyle(HudButtonStyle(primary: false, compact: true))
            }
        }
    }

    private var analysisEmptyCard: some View {
        RelayCard {
            HStack(spacing: 12) {
                Image(systemName: "doc.text.magnifyingglass")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(RelayTheme.amberText)
                VStack(alignment: .leading, spacing: 2) {
                    Text("没有找到可分析的缓存数据")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(RelayTheme.text)
                    Text("这条会话可能没有可读取的会话记录，或统计文件已被清理。")
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                }
                Spacer()
                Button("重新分析") {
                    Task { await model.inspectSession(session) }
                }
                .buttonStyle(HudButtonStyle(primary: false, compact: true))
                .disabled(isInspecting)
            }
        }
    }

    private func analysisErrorCard(_ error: String) -> some View {
        HudNotice(color: RelayTheme.red) {
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(RelayTheme.red)
                VStack(alignment: .leading, spacing: 2) {
                    Text("缓存与推理分析失败")
                        .font(.callout.weight(.semibold))
                    Text(error.localized(locale: locale))
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                        .lineLimit(3)
                }
                Spacer()
                Button("重试") {
                    Task { await model.inspectSession(session) }
                }
                .buttonStyle(HudButtonStyle(primary: true, compact: true))
                .disabled(isInspecting)
            }
        }
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 13) {
            ZStack {
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .fill(RelayTheme.accent.opacity(0.12))
                    .frame(width: 46, height: 46)
                Image(systemName: "bubble.left.and.text.bubble.right")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(RelayTheme.accent)
            }
            .overlay {
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .strokeBorder(RelayTheme.accent.opacity(0.25), lineWidth: 0.7)
            }

            VStack(alignment: .leading, spacing: 5) {
                Text(session.displayTitle(locale: locale))
                    .font(.hudDisplay(23))
                    .foregroundStyle(RelayTheme.text)
                    .lineLimit(2)
                HStack(spacing: 6) {
                    if let creator = model.preferredName(forAlias: session.creatorAlias) {
                        StatusBadge(text: creator, color: RelayTheme.purple, icon: "person.crop.circle")
                    }
                    StatusBadge(text: session.shortID, color: RelayTheme.textMuted, icon: "number")
                    if creatorAccount?.isActive == true {
                        StatusBadge(text: "当前账号创建", color: RelayTheme.green, icon: "checkmark")
                    }
                }
            }
            Spacer()
            Button {
                model.copy(session.id, label: "会话 ID 已复制")
            } label: {
                Label("复制 ID", systemImage: "doc.on.doc")
            }
            .buttonStyle(HudButtonStyle(primary: false))
        }
    }

    private var actionBar: some View {
        HStack(spacing: 10) {
            Button {
                model.openSession(session)
            } label: {
                Label("仅打开会话", systemImage: "arrow.up.forward.app")
            }
            .buttonStyle(HudButtonStyle(primary: false))
            .disabled(model.isSwitching || model.isOpeningProcess)

            if let creator = creatorAccount {
                if creator.isActive {
                    Button {
                        model.requestRestart(threadID: session.id)
                    } label: {
                        Label("重启并打开", systemImage: "arrow.clockwise.circle")
                    }
                    .buttonStyle(HudButtonStyle(primary: false))
                    .disabled(model.isSwitching || model.isOpeningProcess)
                } else {
                    Button {
                        model.requestSwitch(creator, threadID: session.id)
                    } label: {
                        Label(L10n.format("切换到 %@ 并打开", creator.preferredName), systemImage: "arrow.triangle.2.circlepath")
                    }
                    .buttonStyle(HudButtonStyle(primary: true))
                    .disabled(!creator.isHealthy || creator.isExhausted || model.isSwitching || model.isOpeningProcess)
                }
            }
            Spacer()
        }
    }

    @ViewBuilder
    private func metricsView(_ metrics: RelayMetrics) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 150, maximum: 260), spacing: 12)],
                alignment: .leading,
                spacing: 12
            ) {
                metricCard(
                    title: "加密推理项",
                    value: NumberDisplay.integer(metrics.reasoningEncryptedItems, mode: preferences.numberFormatMode, locale: locale),
                    subtitle: byteString(metrics.reasoningEncryptedBytes ?? 0),
                    color: RelayTheme.purple
                )
                metricCard(
                    title: "推理项总数",
                    value: NumberDisplay.integer(metrics.reasoningItems, mode: preferences.numberFormatMode, locale: locale),
                    subtitle: "会话记录内",
                    color: RelayTheme.accent
                )
                metricCard(
                    title: "压缩检查点",
                    value: NumberDisplay.integer(metrics.compactions?.count, mode: preferences.numberFormatMode, locale: locale),
                    subtitle: "压缩窗口",
                    color: RelayTheme.amberText
                )
                metricCard(
                    title: "记录大小",
                    value: metrics.sizeHuman ?? "-",
                    subtitle: "本地文件",
                    color: RelayTheme.textMuted
                )
            }

            if let totals = metrics.totals {
                tokenTotalsCard(totals)
            }

            if let compactions = metrics.compactions, !compactions.isEmpty {
                RelayCard {
                    VStack(alignment: .leading, spacing: 8) {
                        HudLabel(text: "压缩检查点", color: RelayTheme.accent)
                        ForEach(compactions) { compaction in
                            HStack {
                                Text(L10n.format("窗口 %d", compaction.windowNumber ?? 0))
                                    .font(.hudMono(10, weight: .medium))
                                    .foregroundStyle(RelayTheme.text)
                                Text(compaction.timestamp ?? "-")
                                    .font(.caption2)
                                    .foregroundStyle(RelayTheme.textMuted)
                                Spacer()
                                Text(L10n.format("%d 项", compaction.itemCount ?? 0))
                                    .font(.hudMono(9))
                                    .foregroundStyle(RelayTheme.textMuted)
                                StatusBadge(
                                    text: compaction.hasEncryptedReasoning == true ? "含推理" : "无推理",
                                    color: compaction.hasEncryptedReasoning == true ? RelayTheme.purple : RelayTheme.textMuted
                                )
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
            }

            if let turns = metrics.turns, !turns.isEmpty {
                let visible = Array(turns.suffix(8))
                let startNumber = max(1, turns.count - visible.count + 1)
                RelayCard {
                    VStack(alignment: .leading, spacing: 10) {
                        HudLabel(text: L10n.format("逐轮缓存 // 最近 %d 轮", visible.count), color: RelayTheme.accent)
                        ScrollView(.horizontal, showsIndicators: false) {
                            VStack(alignment: .leading, spacing: 10) {
                                turnHeader
                                HudDivider()
                                ForEach(Array(visible.enumerated()), id: \.element.id) { offset, turn in
                                    turnRow(index: startNumber + offset, turn: turn)
                                    if offset < visible.count - 1 {
                                        HudDivider().opacity(0.55)
                                    }
                                }
                            }
                            .frame(minWidth: 500, alignment: .leading)
                        }
                    }
                }
            }
        }
    }

    private func tokenTotalsCard(_ totals: RelayTokenTotals) -> some View {
        RelayCard(accented: true) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    HudLabel(text: "Token 统计", color: RelayTheme.accent)
                    Spacer()
                    Text(L10n.format("%d 轮 · %d 请求", totals.turns ?? 0, totals.requests ?? 0))
                        .font(.hudMono(9))
                        .foregroundStyle(RelayTheme.textMuted)
                }
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 105, maximum: 210), spacing: 9)],
                    alignment: .leading,
                    spacing: 9
                ) {
                    metricTile(
                        title: "输入",
                        value: tokens(totals.inputTokens),
                        subtitle: "输入",
                        color: RelayTheme.accent
                    )
                    metricTile(
                        title: "缓存 Token",
                        value: tokens(totals.cachedInputTokens),
                        subtitle: "\(String(format: "%.2f", totals.cacheRatio ?? 0))% " + L10n.text("命中"),
                        color: RelayTheme.cacheColor(totals.cacheRatio ?? 0)
                    )
                    metricTile(
                        title: "输出",
                        value: tokens(totals.outputTokens),
                        subtitle: "输出",
                        color: RelayTheme.purple
                    )
                    metricTile(
                        title: "推理",
                        value: tokens(totals.reasoningOutputTokens),
                        subtitle: "推理输出",
                        color: RelayTheme.amber
                    )
                }
                HudProgressLine(
                    value: min(max((totals.cacheRatio ?? 0) / 100, 0), 1),
                    color: RelayTheme.cacheColor(totals.cacheRatio ?? 0)
                )
                HStack {
                    Text("总 Token（输入 + 输出）")
                        .font(.caption)
                        .foregroundStyle(RelayTheme.textMuted)
                    Spacer()
                    Text(tokens(totals.totalTokens))
                        .font(.hudMono(12, weight: .semibold))
                        .foregroundStyle(RelayTheme.text)
                }
            }
        }
    }

    private var turnHeader: some View {
        HStack {
            Text("#").frame(width: 28, alignment: .leading)
            Text("请求").frame(width: 48, alignment: .trailing)
            Text("首轮输入 Token").frame(width: 100, alignment: .trailing)
            Text("首轮缓存命中率").frame(width: 88, alignment: .trailing)
            Text("末轮缓存命中率").frame(width: 88, alignment: .trailing)
            Text("累计缓存命中率").frame(width: 88, alignment: .trailing)
            Spacer()
        }
        .font(.hudLabel(8))
        .foregroundStyle(RelayTheme.textMuted)
    }

    private func turnRow(index: Int, turn: RelayTurnUsage) -> some View {
        HStack {
            Text("\(index)").frame(width: 28, alignment: .leading)
            Text("\(turn.requests ?? 0)").frame(width: 48, alignment: .trailing)
            Text(tokens(turn.firstInputTokens)).frame(width: 78, alignment: .trailing)
            Text(percent(turn.firstCacheRatio)).frame(width: 68, alignment: .trailing)
                .foregroundStyle(RelayTheme.cacheColor(turn.firstCacheRatio ?? 0))
            Text(percent(turn.lastCacheRatio)).frame(width: 68, alignment: .trailing)
                .foregroundStyle(RelayTheme.cacheColor(turn.lastCacheRatio ?? 0))
            Text(percent(turn.totalCacheRatio)).frame(width: 68, alignment: .trailing)
                .foregroundStyle(RelayTheme.cacheColor(turn.totalCacheRatio ?? 0))
            Spacer()
        }
        .font(.hudMono(9))
        .foregroundStyle(RelayTheme.text)
    }

    private func metricCard(title: String, value: String, subtitle: String, color: Color) -> some View {
        RelayCard(padding: 13) {
            VStack(alignment: .leading, spacing: 3) {
                HudLabel(text: title, color: RelayTheme.textMuted)
                Text(value)
                    .font(.hudDisplay(23))
                    .foregroundStyle(color)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                Text(subtitle.localized(locale: locale))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
    }

    private func metricTile(title: String, value: String, subtitle: String, color: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HudLabel(text: title, color: RelayTheme.textMuted)
            Text(value)
                .font(.hudDisplay(20))
                .foregroundStyle(color)
                .lineLimit(1)
                .minimumScaleFactor(0.62)
            Text(subtitle.localized(locale: locale))
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RelayTheme.panelElevated.opacity(0.72))
        .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 4, style: .continuous)
                .strokeBorder(RelayTheme.line.opacity(0.55), lineWidth: 0.6)
        }
    }

    private func tokens(_ value: Int?) -> String {
        NumberDisplay.integer(value, mode: preferences.numberFormatMode, locale: locale)
    }

    private func percent(_ value: Double?) -> String {
        guard let value else { return "-" }
        return String(format: "%.1f%%", value)
    }

    private func byteString(_ value: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .file)
    }
}
