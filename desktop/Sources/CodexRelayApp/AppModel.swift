import CodexRelayCore
import AppKit
import Combine
import Foundation
import SwiftUI
import UniformTypeIdentifiers

enum SidebarSection: String, CaseIterable, Identifiable {
    case overview
    case accounts
    case sessions
    case guardMonitor
    case diagnostics
    case settings
    case about

    var id: String { rawValue }
    var title: String {
        switch self {
        case .overview: return "概览"
        case .accounts: return "账号"
        case .sessions: return "会话"
        case .guardMonitor: return "额度守护"
        case .diagnostics: return "诊断"
        case .settings: return "设置"
        case .about: return "关于"
        }
    }
    var icon: String {
        switch self {
        case .overview: return "rectangle.grid.2x2"
        case .accounts: return "person.2"
        case .sessions: return "bubble.left.and.bubble.right"
        case .guardMonitor: return "pause.circle"
        case .diagnostics: return "stethoscope"
        case .settings: return "gearshape"
        case .about: return "info.circle"
        }
    }
}

enum PendingProcessActionKind {
    case switchAccount
    case restart
    case openThread
    case openApp

    var isRestart: Bool { self == .restart }
    var isOpen: Bool { self == .openThread || self == .openApp }
}

struct PendingProcessAction: Identifiable {
    let id = UUID()
    let account: RelayAccount?
    let threadID: String
    let kind: PendingProcessActionKind
    var continuationPrompt: String? = nil

    func title(locale: Locale) -> String {
        switch kind {
        case .restart:
            return L10n.text("重启 ChatGPT 并恢复会话？")
        case .switchAccount:
            return L10n.format("切换到 %@ 并重启 ChatGPT？", account?.preferredName ?? L10n.text("目标账号"))
        case .openThread:
            return L10n.text("在 ChatGPT 中打开会话？")
        case .openApp:
            return L10n.text("打开 ChatGPT 最近会话？")
        }
    }

    func confirmationLabel(locale: Locale) -> String {
        switch kind {
        case .openThread, .openApp:
            return L10n.text("我确认现在打开 ChatGPT")
        case .switchAccount, .restart:
            return L10n.text("我确认现在退出并重启 ChatGPT")
        }
    }
}

@MainActor
final class AppModel: ObservableObject {
    let backend = RelayBackend()

    @Published var accounts: [RelayAccount] = []
    @Published var sessions: [RelaySession] = []
    @Published var status: RelayStatus?
    @Published var doctor: RelayDoctorReport?
    @Published var section: SidebarSection = .overview
    @Published var selectedAccountID: String?
    @Published var selectedSessionID: String?
    @Published var searchText = ""
    @Published var accountFilter: String?
    @Published var isRefreshing = false
    @Published var isRefreshingUsage = false
    private var pendingFullUsageRefresh = false
    @Published var isRefreshingDoctor = false
    @Published var usageSyncError: String?
    @Published var lastUsageSync: Date?
    @Published var isSwitching = false
    @Published var isOpeningProcess = false
    @Published var switchStage = "准备中…"
    @Published var switchLog: [String] = []
    @Published var isAdding = false
    @Published var showingAddSheet = false
    @Published var addLog: [String] = []
    @Published var showingError = false
    @Published var errorTitle = "操作失败"
    @Published var errorMessage = ""
    @Published var toast: String?
    @Published var inspectingSessionID: String?
    @Published var inspections: [String: RelayInspectReport] = [:]
    @Published var inspectionErrors: [String: String] = [:]
    @Published var inspectionFetchedAt: [String: Date] = [:]
    @Published var isRenamingAccount = false
    @Published var avatarPaths: [String: String] = [:]
    @Published var backendVersion = ""
    @Published var pendingProcessAction: PendingProcessAction?

    private var timer: Timer?
    private var usageTimer: Timer?
    private var fullUsageTimer: Timer?
    private var autoTimer: Timer?
    private weak var autoPreferences: AppPreferences?
    private var autoCheckRunning = false
    private var lastAutoCheck = Date.distantPast
    private var started = false
    private var dataGeneration = 0

    init() {
        if let argument = CommandLine.arguments.first(where: { $0.hasPrefix("--ui-smoke=") }),
           let section = SidebarSection(rawValue: String(argument.dropFirst("--ui-smoke=".count))) {
            self.section = section
        }
        loadAvatarIndex()
    }

    var currentAccount: RelayAccount? {
        // auth.json is the source of truth for the account ChatGPT is actually
        // using. Prefer it over a stale `active` flag left in an old cache.
        if let activeID = status?.auth?.accountId,
           let account = accounts.first(where: { $0.accountId == activeID }) {
            return account
        }
        return accounts.first(where: \.isActive) ?? status?.currentProfile
    }

    var selectedAccount: RelayAccount? {
        if let selectedAccountID, let account = accounts.first(where: { $0.id == selectedAccountID }) {
            return account
        }
        return currentAccount ?? accounts.first
    }

    func preferredName(forAlias alias: String?) -> String? {
        guard let alias, !alias.isEmpty else { return nil }
        return accounts.first(where: { $0.alias == alias })?.preferredName ?? alias
    }

    var selectedSession: RelaySession? {
        guard let selectedSessionID else { return nil }
        return sessions.first(where: { $0.id == selectedSessionID })
    }

    var unmanagedAuth: RelayAuth? {
        guard let auth = status?.auth else { return nil }
        if accounts.contains(where: { $0.accountId == auth.accountId }) { return nil }
        return auth
    }

    var menuBarTitle: String {
        if let name = currentAccount?.preferredName { return "CX \(name)" }
        return "CX"
    }

    var lastThreadID: String? {
        status?.lastThreadId
    }

    var bestThreadID: String {
        selectedSession?.id ?? "none"
    }

    var filteredSessions: [RelaySession] {
        var result = sessions
        if let accountFilter, !accountFilter.isEmpty {
            result = result.filter { $0.creatorAlias == accountFilter }
        }
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        if !query.isEmpty {
            result = result.filter {
                $0.displayTitle.localizedCaseInsensitiveContains(query)
                    || ($0.cwd ?? "").localizedCaseInsensitiveContains(query)
                    || $0.id.localizedCaseInsensitiveContains(query)
                    || ($0.creatorAlias ?? "").localizedCaseInsensitiveContains(query)
            }
        }
        return result
    }

    func start(preferences: AppPreferences? = nil) {
        guard !started else { return }
        started = true
        autoPreferences = preferences
        let backend = self.backend
        Task {
            backendVersion = await Task.detached { backend.versionLine() }.value
        }
        Task {
            await refreshAll()
            await refreshActiveUsage(auto: true)
            // A full pass also refreshes non-active accounts, so the account
            // list cannot show a stale quota after a reset.
            await refreshUsage(silent: true)
        }
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.refreshAll(silent: true)
            }
        }
        // Keep the active account's quota close to real time without spawning
        // a second app-server. Non-active accounts are refreshed by the full
        // pass below; the cadence is short enough that a quota reset is visible
        // without manually clicking each account.
        usageTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.refreshActiveUsage(auto: true)
            }
        }
        fullUsageTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.refreshUsage(silent: true)
            }
        }
        if let timer {
            RunLoop.main.add(timer, forMode: .common)
        }
        if let usageTimer {
            RunLoop.main.add(usageTimer, forMode: .common)
        }
        if let fullUsageTimer {
            RunLoop.main.add(fullUsageTimer, forMode: .common)
        }
        // The SwiftUI app is the supported frontend. Run the same `auto
        // --once` policy in-process instead of requiring a terminal daemon;
        // this is what makes the Settings toggle effective.
        autoTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                await self?.checkAutoContinue()
            }
        }
        if let autoTimer {
            RunLoop.main.add(autoTimer, forMode: .common)
        }
        if preferences?.autoContinueEnabled == true {
            Task { await checkAutoContinue() }
        }
    }

    func checkAutoContinue() async {
        guard let preferences = autoPreferences,
              preferences.autoContinueEnabled,
              preferences.autoAllowRestart,
              preferences.autoInterval.isFinite, preferences.autoInterval > 0,
              Date().timeIntervalSince(lastAutoCheck) >= preferences.autoInterval,
              !autoCheckRunning,
              !isSwitching,
              !isOpeningProcess,
              !isAdding,
              !isRefreshing,
              !isRefreshingUsage,
              pendingProcessAction == nil else {
            return
        }
        lastAutoCheck = Date()
        autoCheckRunning = true
        defer { autoCheckRunning = false }
        let backend = self.backend
        do {
            let result = try await Task.detached(priority: .utility) {
                try backend.autoContinueOnce()
            }.value
            if result.action == "switched", let target = result.to {
                // The account switch can succeed while the continuation command
                // fails; surface both facts instead of pretending the whole
                // operation failed or succeeded silently.
                if let continuationError = result.continueError, !continuationError.isEmpty {
                    toast = continuationError
                } else {
                    toast = L10n.format("已切换到 %@", target)
                }
                clearToastLater()
                await refreshAll(silent: true)
                await refreshActiveUsage(auto: true)
            } else if result.action == "blocked" {
                // Never show a modal from a background check; a toast keeps
                // the user informed without interrupting their work.
                switch result.reason {
                case "active-turn":
                    toast = L10n.text("检测到 Codex 正在执行一轮任务，拒绝重启。等待完成或使用 --force。")
                case "no-usable-candidate":
                    toast = L10n.text("没有仍有额度的其他账号")
                case "no-candidate":
                    toast = L10n.text("没有其他可用账号")
                case "process-control-disabled":
                    toast = L10n.text("自动接力会重启 ChatGPT，已默认禁用。")
                default:
                    toast = result.continueError ?? L10n.text("自动续跑检查被阻止")
                }
                clearToastLater()
            }
        } catch {
            toast = error.relayLocalizedDescription
            clearToastLater()
        }
    }

    deinit {
        timer?.invalidate()
        usageTimer?.invalidate()
        fullUsageTimer?.invalidate()
        autoTimer?.invalidate()
    }

    // MARK: - Account avatars

    private var avatarDirectory: URL {
        AutoSettingsStore.url.deletingLastPathComponent()
            .appendingPathComponent("avatars", isDirectory: true)
    }

    private var avatarIndexURL: URL {
        AutoSettingsStore.url.deletingLastPathComponent()
            .appendingPathComponent("avatars.json")
    }

    func avatarImage(for account: RelayAccount) -> NSImage? {
        guard let path = avatarPaths[account.id] else { return nil }
        return NSImage(contentsOfFile: path)
    }

    func hasCustomAvatar(for account: RelayAccount) -> Bool {
        guard let path = avatarPaths[account.id] else { return false }
        return FileManager.default.fileExists(atPath: path)
    }

    func chooseAvatar(for account: RelayAccount) {
        let panel = NSOpenPanel()
        panel.title = "选择账号头像"
        panel.message = L10n.format("选择一张图片作为 %@ 的头像", account.preferredName)
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        guard panel.runModal() == .OK, let source = panel.url else { return }
        do {
            let png = try normalizedAvatarPNG(from: source)
            try FileManager.default.createDirectory(at: avatarDirectory, withIntermediateDirectories: true)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: avatarDirectory.path)
            let filename = "\(account.id)-\(UUID().uuidString).png"
            let target = avatarDirectory.appendingPathComponent(filename)
            try png.write(to: target, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: target.path)
            if let oldPath = avatarPaths[account.id] {
                try? FileManager.default.removeItem(atPath: oldPath)
            }
            avatarPaths[account.id] = target.path
            saveAvatarIndex()
            toast = L10n.text("头像已更新")
            clearToastLater()
        } catch {
            presentError(L10n.text("头像更新失败"), error.relayLocalizedDescription)
        }
    }

    func resetAvatar(for account: RelayAccount) {
        if let path = avatarPaths.removeValue(forKey: account.id) {
            try? FileManager.default.removeItem(atPath: path)
        }
        saveAvatarIndex()
        toast = L10n.text("已恢复默认头像")
        clearToastLater()
    }

    private func normalizedAvatarPNG(from url: URL) throws -> Data {
        guard let image = NSImage(contentsOf: url) else {
            throw NSError(domain: "CodexRelay.Avatar", code: 1, userInfo: [NSLocalizedDescriptionKey: "无法读取图片文件。"])
        }
        let side: CGFloat = 512
        let target = NSSize(width: side, height: side)
        let resized = NSImage(size: target)
        resized.lockFocus()
        NSColor.clear.setFill()
        NSRect(origin: .zero, size: target).fill()
        let scale = max(side / max(image.size.width, 1), side / max(image.size.height, 1))
        let drawSize = NSSize(width: image.size.width * scale, height: image.size.height * scale)
        let origin = NSPoint(x: (side - drawSize.width) / 2, y: (side - drawSize.height) / 2)
        image.draw(in: NSRect(origin: origin, size: drawSize), from: .zero, operation: .copy, fraction: 1)
        resized.unlockFocus()
        guard let tiff = resized.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let data = bitmap.representation(using: .png, properties: [:]) else {
            throw NSError(domain: "CodexRelay.Avatar", code: 2, userInfo: [NSLocalizedDescriptionKey: "无法转换为 PNG 图片。"])
        }
        return data
    }

    private func loadAvatarIndex() {
        guard let data = try? Data(contentsOf: avatarIndexURL),
              let values = try? JSONDecoder().decode([String: String].self, from: data) else {
            return
        }
        avatarPaths = values
    }

    private func saveAvatarIndex() {
        do {
            try FileManager.default.createDirectory(
                at: avatarIndexURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            let data = try JSONEncoder().encode(avatarPaths)
            try data.write(to: avatarIndexURL, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: avatarIndexURL.path)
        } catch {
            presentError(L10n.text("保存头像配置失败"), error.relayLocalizedDescription)
        }
    }

    private func detached<T: Sendable>(_ work: @escaping @Sendable () throws -> T) async throws -> T {
        try await Task.detached(priority: .userInitiated) { try work() }.value
    }

    func refreshAll(silent: Bool = false) async {
        guard !isRefreshing, !isRefreshingUsage, !isSwitching, !isAdding else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        let backend = self.backend
        let generation = dataGeneration
        do {
            async let accountList = detached { try backend.listAccounts(refresh: false) }
            async let statusValue = detached { try backend.status() }
            async let sessionList = detached { try backend.sessions(limit: 0) }
            let (newAccounts, newStatus, newSessions) = try await (accountList, statusValue, sessionList)
            // Do not overwrite a usage refresh that finished while we were reading
            // the local state snapshot.
            if generation == dataGeneration {
                accounts = newAccounts
                dataGeneration += 1
            }
            status = newStatus
            sessions = newSessions
            if selectedAccountID == nil {
                selectedAccountID = accounts.first(where: \.isActive)?.id ?? accounts.first?.id
            }
            if let selectedSessionID, !newSessions.contains(where: { $0.id == selectedSessionID }) {
                self.selectedSessionID = nil
            }
        } catch {
            if !silent {
                presentError(L10n.text("刷新失败"), error.relayLocalizedDescription)
            }
        }
    }

    func refreshUsage(silent: Bool = false) async {
        guard !isSwitching, !isAdding, !isOpeningProcess else { return }
        if isRefreshingUsage {
            // Active-account polling can overlap the 30s full pass. Queue one
            // retry instead of dropping the pass and leaving other rows stale.
            pendingFullUsageRefresh = true
            return
        }
        isRefreshingUsage = true
        defer {
            isRefreshingUsage = false
            if pendingFullUsageRefresh {
                pendingFullUsageRefresh = false
                Task { @MainActor in await self.refreshUsage(silent: true) }
            }
        }
        let backend = self.backend
        do {
            let refreshed = try await detached { try backend.listAccounts(refresh: true) }
            accounts = refreshed
            dataGeneration += 1
            lastUsageSync = Date()
            let failed = refreshed.filter { $0.status != "ok" }
            usageSyncError = failed.isEmpty
                ? nil
                : L10n.format("%d 个账号同步失败：%@", failed.count, failed.map(\.alias).joined(separator: ", "))
            if !silent {
                toast = failed.isEmpty ? L10n.text("账号用量已刷新") : L10n.text("部分账号同步失败")
                clearToastLater()
            }
        } catch {
            usageSyncError = error.relayLocalizedDescription
            if !silent {
                presentError(L10n.text("刷新用量失败"), error.relayLocalizedDescription)
            }
        }
    }

    /// Refresh one account. Active accounts use the read-only HTTP endpoint;
    /// non-active accounts use the isolated app-server path. Callers pass
    /// `auto: true` for timers and `auto: false` for explicit user actions.
    func refreshUsageForAccount(_ account: RelayAccount, auto: Bool = true) async {
        guard !isRefreshingUsage, !isSwitching, !isAdding, !isOpeningProcess else { return }
        isRefreshingUsage = true
        defer { isRefreshingUsage = false }
        let backend = self.backend
        let alias = account.alias
        let expectedID = account.id
        do {
            let refreshed = try await detached { try backend.usage(alias: alias) }
            guard let updated = refreshed.first(where: { $0.id == expectedID }) else {
                usageSyncError = L10n.format("CLI 未返回 %@ 的用量结果", alias)
                if !auto { presentError(L10n.text("同步用量失败"), usageSyncError ?? L10n.text("未知错误")) }
                return
            }
            applyUsageUpdate(updated)
            let isCurrent = currentAccount?.id == updated.id
            if isCurrent {
                lastUsageSync = Date()
            }
            if updated.status != "ok" {
                let message = L10n.format("%@ 同步失败：%@", alias, updated.lastError ?? updated.status ?? "unknown")
                if isCurrent { usageSyncError = message }
                if !auto { presentError(L10n.text("同步用量失败"), message) }
            } else {
                if isCurrent { usageSyncError = nil }
                if !auto {
                    toast = L10n.format("%@ 用量已同步", alias)
                    clearToastLater()
                }
            }
        } catch {
            let isCurrent = currentAccount?.id == expectedID
            if isCurrent { usageSyncError = error.relayLocalizedDescription }
            if !auto { presentError(L10n.text("同步用量失败"), error.relayLocalizedDescription) }
        }
    }

    func refreshActiveUsage(auto: Bool = false) async {
        guard let current = currentAccount else { return }
        await refreshUsageForAccount(current, auto: auto)
    }

    /// Called when the user opens an account detail. Skip if the last verified
    /// timestamp is still fresh; otherwise refresh only that account.
    func refreshSelectedUsageIfNeeded() async {
        guard let account = selectedAccount else { return }
        if let raw = account.lastVerifiedAt,
           let checked = Self.parseTimestamp(raw),
           Date().timeIntervalSince(checked) < 30 {
            return
        }
        await refreshUsageForAccount(account, auto: true)
    }

    private func applyUsageUpdate(_ updated: RelayAccount) {
        if let index = accounts.firstIndex(where: { $0.id == updated.id }) {
            let wasActive = accounts[index].isActive
            accounts[index] = updated.withActive(wasActive ? true : updated.active)
        } else {
            accounts.append(updated.withActive(updated.active))
        }
        dataGeneration += 1
    }

    private static func parseTimestamp(_ raw: String) -> Date? {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: raw) { return date }
        iso.formatOptions = [.withInternetDateTime]
        return iso.date(from: raw)
    }

    func renameAccount(_ account: RelayAccount, displayName: String) async {
        let value = displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            presentError(L10n.text("名称不能为空"), L10n.text("请输入一个容易辨认的账号名称。"))
            return
        }
        if accounts.contains(where: {
            $0.id != account.id && $0.preferredName.caseInsensitiveCompare(value) == .orderedSame
        }) {
            presentError(L10n.text("名称重复"), L10n.format("已经有账号使用「%@」，请换一个名称以便区分。", value))
            return
        }
        guard !isRenamingAccount else { return }
        isRenamingAccount = true
        defer { isRenamingAccount = false }
        let backend = self.backend
        do {
            let updated = try await detached {
                try backend.setDisplayName(alias: account.alias, displayName: value)
            }
            if let index = accounts.firstIndex(where: { $0.id == updated.id }) {
                accounts[index] = updated.withActive(accounts[index].isActive)
                dataGeneration += 1
            }
            await refreshAll(silent: true)
            toast = L10n.text("账号名称已更新")
            clearToastLater()
        } catch {
            presentError(L10n.text("重命名失败"), error.relayLocalizedDescription)
        }
    }

    func refreshDoctor() async {
        guard !isRefreshingDoctor else { return }
        isRefreshingDoctor = true
        defer { isRefreshingDoctor = false }
        let backend = self.backend
        do {
            doctor = try await detached { try backend.doctor() }
        } catch {
            presentError(L10n.text("诊断失败"), error.relayLocalizedDescription)
        }
    }

    func requestSwitch(_ account: RelayAccount, threadID: String? = nil) {
        guard !isSwitching, !isOpeningProcess else { return }
        guard account.isHealthy else {
            presentError(L10n.text("账号不可用"), L10n.format("%@ 需要重新登录，暂时不能切换。", account.preferredName))
            return
        }
        guard !account.isExhausted else {
            presentError(L10n.text("额度已耗尽"), L10n.format("%@ 当前额度已耗尽；请等待重置或先用 CLI 明确 --force。", account.preferredName))
            return
        }
        pendingProcessAction = PendingProcessAction(
            account: account,
            threadID: threadID ?? bestThreadID,
            kind: .switchAccount
        )
    }

    func requestRestart(threadID: String? = nil) {
        guard !isSwitching, !isOpeningProcess else { return }
        pendingProcessAction = PendingProcessAction(
            account: nil,
            threadID: threadID ?? bestThreadID,
            kind: .restart
        )
    }

    func confirmPendingProcessAction(threadID: String? = nil, continuationPrompt: String? = nil) {
        guard let pending = pendingProcessAction else { return }
        let action = PendingProcessAction(account: pending.account, threadID: threadID ?? pending.threadID, kind: pending.kind, continuationPrompt: continuationPrompt)
        pendingProcessAction = nil
        // Let the confirmation sheet dismiss before presenting the next step.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) {
            switch action.kind {
            case .restart:
                self.performRestart(threadID: action.threadID)
            case .switchAccount:
                if let account = action.account {
                    self.performSwitch(account, threadID: action.threadID, continuationPrompt: action.continuationPrompt)
                }
            case .openThread, .openApp:
                self.performOpen(action)
            }
        }
    }

    func cancelPendingProcessAction() {
        pendingProcessAction = nil
    }

    private func performSwitch(_ account: RelayAccount, threadID: String, continuationPrompt: String? = nil) {
        guard !isSwitching, !autoCheckRunning else { return }
        isSwitching = true
        switchStage = "准备切换…"
        switchLog = [
            L10n.format("目标账号：%@", "\(account.preferredName)（\(account.email ?? "-")）"),
            L10n.format("目标会话：%@", threadID)
        ]
        let alias = account.alias
        let backend = self.backend
        Task {
            do {
                let result = try await Task.detached(priority: .userInitiated) {
                    try backend.switchAccount(
                        alias: alias,
                        threadID: threadID,
                        allowRestart: true,
                        continuationPrompt: continuationPrompt
                    ) { event in
                        Task { @MainActor in
                            self.applyProgress(event)
                        }
                    }
                }.value
                isSwitching = false
                if let error = result.continueError {
                    presentError(L10n.text("账号已切换，原聊天未续跑"), error)
                }
                let mode = result.mode ?? "desktop"
                toast = L10n.format("已切换到 %@（%@，%d ms）", result.to?.alias ?? alias, mode, result.durationMs ?? 0)
                clearToastLater()
                await refreshAll()
                await refreshActiveUsage(auto: true)
            } catch {
                isSwitching = false
                switchLog.append(error.relayLocalizedDescription)
                presentError(L10n.text("切换失败"), error.relayLocalizedDescription)
            }
        }
    }

    private func performRestart(threadID: String) {
        guard !isSwitching else { return }
        isSwitching = true
        switchStage = "准备重启…"
        switchLog = [L10n.format("目标会话：%@", threadID)]
        let backend = self.backend
        Task {
            do {
                let result = try await Task.detached(priority: .userInitiated) {
                    try backend.restart(
                        threadID: threadID,
                        allowRestart: true
                    ) { event in
                        Task { @MainActor in
                            self.applyProgress(event)
                        }
                    }
                }.value
                isSwitching = false
                toast = L10n.format("ChatGPT 已重启并恢复会话（%d ms）", result.durationMs ?? 0)
                clearToastLater()
                await refreshAll()
                await refreshActiveUsage(auto: true)
            } catch {
                isSwitching = false
                presentError(L10n.text("重启失败"), error.relayLocalizedDescription)
            }
        }
    }

    private func performOpen(_ action: PendingProcessAction) {
        guard !isOpeningProcess, !isSwitching else { return }
        isOpeningProcess = true
        let backend = self.backend
        Task {
            do {
                try await Task.detached(priority: .userInitiated) {
                    if action.kind == .openApp {
                        try backend.resumeLast(allowRestart: true)
                    } else {
                        try backend.resumeThread(action.threadID, allowRestart: true)
                    }
                }.value
                toast = action.kind == .openApp ? L10n.text("已打开 ChatGPT 最近会话") : L10n.text("已在 ChatGPT 中打开会话")
                clearToastLater()
            } catch {
                presentError(L10n.text("打开 ChatGPT 失败"), error.relayLocalizedDescription)
            }
            isOpeningProcess = false
        }
    }

    private func applyProgress(_ event: RelayProgressEvent) {
        if let message = event.message, !message.isEmpty {
            switchStage = message
            if switchLog.last != message {
                switchLog.append(message)
            }
        }
    }

    func beginAddAccount() {
        guard !isSwitching, pendingProcessAction == nil else { return }
        addLog = []
        showingAddSheet = true
    }

    func addAccount(alias: String, deviceAuth: Bool) {
        let trimmed = alias.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.range(of: #"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"#, options: .regularExpression) != nil else {
            presentError(L10n.text("别名无效"), L10n.text("别名只能包含字母、数字、点、下划线和短横线，长度 1-32，且必须以字母或数字开头。"))
            return
        }
        guard !isAdding else { return }
        isAdding = true
        addLog = ["正在启动官方登录流程…", "浏览器打开后，请选择要添加的那个 ChatGPT 账号。"]
        let backend = self.backend
        Task {
            do {
                try await Task.detached(priority: .userInitiated) {
                    try backend.addAccount(alias: trimmed, deviceAuth: deviceAuth) { line in
                        Task { @MainActor in
                            self.addLog.append(line)
                            if self.addLog.count > 80 { self.addLog.removeFirst(20) }
                        }
                    }
                }.value
                isAdding = false
                showingAddSheet = false
                toast = L10n.format("账号 %@ 已添加", trimmed)
                clearToastLater()
                await refreshAll()
            } catch {
                isAdding = false
                addLog.append(error.relayLocalizedDescription)
                presentError(L10n.text("添加账号失败"), error.relayLocalizedDescription)
            }
        }
    }

    func cancelAdd() {
        backend.cancelAdd()
        isAdding = false
        addLog.append("已取消登录")
    }

    func captureCurrentAccount() {
        guard !isSwitching else { return }
        let alias = "captured"
        let backend = self.backend
        Task {
            do {
                try await Task.detached { try backend.captureCurrent(alias: alias) }.value
                toast = L10n.format("当前账号已捕获为 %@", alias)
                clearToastLater()
                await refreshAll()
            } catch {
                presentError(L10n.text("捕获失败"), error.relayLocalizedDescription)
            }
        }
    }

    func requestOpenThread(_ threadID: String) {
        guard !isSwitching, !isOpeningProcess else { return }
        pendingProcessAction = PendingProcessAction(
            account: nil,
            threadID: threadID,
            kind: .openThread
        )
    }

    func openSession(_ session: RelaySession) {
        requestOpenThread(session.id)
    }

    func inspectSession(_ session: RelaySession, reportError: Bool = true) async {
        guard inspectingSessionID == nil else { return }
        inspectingSessionID = session.id
        inspectionErrors[session.id] = nil
        defer { inspectingSessionID = nil }
        let backend = self.backend
        do {
            let report = try await detached { try backend.inspect(threadID: session.id) }
            inspections[session.id] = report
            inspectionFetchedAt[session.id] = Date()
        } catch {
            let message = error.relayLocalizedDescription
            inspectionErrors[session.id] = message
            // A stale row in Codex's local threads DB can point at a rollout
            // that was deleted or compacted. Show that inline in the detail
            // pane; a modal alert is disruptive and blocks the whole app.
            if reportError, !message.localizedCaseInsensitiveContains("rollout") {
                presentError(L10n.text("分析失败"), message)
            }
        }
    }

    /// Automatically load analysis once when a thread is opened. Re-use a
    /// recent result for five minutes; the explicit refresh button can force
    /// a new request at any time.
    func autoInspectSession(_ session: RelaySession) async {
        if let running = inspectingSessionID, running != session.id {
            return
        }
        if inspections[session.id] != nil,
           let fetched = inspectionFetchedAt[session.id],
           Date().timeIntervalSince(fetched) < 300 {
            return
        }
        if inspectionErrors[session.id] != nil, inspections[session.id] != nil {
            return
        }
        await inspectSession(session, reportError: false)
    }

    func requestOpenApp() {
        guard !isSwitching, !isOpeningProcess else { return }
        pendingProcessAction = PendingProcessAction(
            account: nil,
            threadID: bestThreadID,
            kind: .openApp
        )
    }

    func openStore() {
        let path = AutoSettingsStore.url.deletingLastPathComponent().path
        let backend = self.backend
        Task {
            do {
                try await Task.detached { try backend.openPath(path) }.value
            } catch {
                presentError(L10n.text("打开目录失败"), error.relayLocalizedDescription)
            }
        }
    }

    func openDocs() {
        // Prefer the copy bundled with the app; fall back to the installed
        // share directory, then the public repository readme. Never assume a
        // developer checkout exists on the user's machine.
        let candidates = [
            Bundle.main.url(forResource: "README", withExtension: "md"),
            URL(fileURLWithPath: NSString(string: "~/.local/share/codex-relay/README.md").expandingTildeInPath),
        ].compactMap { $0 }
        if let local = candidates.first(where: { FileManager.default.fileExists(atPath: $0.path) }) {
            NSWorkspace.shared.open(local)
            return
        }
        if let remote = URL(string: "https://github.com/xiaodianshiuwu/codex-account-relay#readme") {
            NSWorkspace.shared.open(remote)
        } else {
            presentError(L10n.text("打开文档失败"), L10n.text("未找到文档"))
        }
    }

    func accountAlias(for accountID: String?) -> String? {
        guard let accountID else { return nil }
        return accounts.first(where: { $0.accountId == accountID })?.alias
    }

    func copy(_ value: String, label: String = L10n.text("已复制")) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
        toast = label
        clearToastLater()
    }

    /// Drop transient text that was rendered in the previous language.  The
    /// persistent model data is untouched; only short-lived banners/logs that
    /// cannot re-localize themselves are cleared when the user switches.
    func resetTransientLocalizedState() {
        toast = nil
        if showingError {
            showingError = false
            errorMessage = ""
        }
        if !isSwitching {
            switchStage = L10n.text("准备中…")
            switchLog = []
        }
        if !isAdding {
            addLog = []
        }
    }

    func presentError(_ title: String, _ message: String) {
        errorTitle = title
        errorMessage = message
        showingError = true
    }

    private func clearToastLater() {
        let current = toast
        Task {
            try? await Task.sleep(nanoseconds: 3_500_000_000)
            if toast == current {
                toast = nil
            }
        }
    }
}
