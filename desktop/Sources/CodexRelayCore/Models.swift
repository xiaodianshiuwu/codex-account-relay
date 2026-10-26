import Foundation

public struct RelayAccountList: Decodable {
    public let profiles: [RelayAccount]
    public let activeAccountId: String?

    public init(profiles: [RelayAccount], activeAccountId: String?) {
        self.profiles = profiles
        self.activeAccountId = activeAccountId
    }
}

public struct RelayAccountLabelResult: Decodable {
    public let ok: Bool?
    public let profile: RelayAccount

    public init(ok: Bool?, profile: RelayAccount) {
        self.ok = ok
        self.profile = profile
    }
}

public struct RelayAccount: Decodable, Identifiable, Hashable {
    public let id: String
    public let alias: String
    public let displayName: String?
    public let email: String?
    public let name: String?
    public let plan: String?
    public let accountId: String?
    public let status: String?
    public let lastRefresh: String?
    public let lastUsedAt: String?
    public let lastVerifiedAt: String?
    public let usage: RelayUsage?
    public let active: Bool?
    public let usageText: String?
    public let lastError: String?

    public init(
        id: String,
        alias: String,
        displayName: String? = nil,
        email: String? = nil,
        name: String? = nil,
        plan: String? = nil,
        accountId: String? = nil,
        status: String? = nil,
        lastRefresh: String? = nil,
        lastUsedAt: String? = nil,
        lastVerifiedAt: String? = nil,
        usage: RelayUsage? = nil,
        active: Bool? = nil,
        usageText: String? = nil,
        lastError: String? = nil
    ) {
        self.id = id
        self.alias = alias
        self.displayName = displayName
        self.email = email
        self.name = name
        self.plan = plan
        self.accountId = accountId
        self.status = status
        self.lastRefresh = lastRefresh
        self.lastUsedAt = lastUsedAt
        self.lastVerifiedAt = lastVerifiedAt
        self.usage = usage
        self.active = active
        self.usageText = usageText
        self.lastError = lastError
    }

    public var isActive: Bool { active == true }
    public var isHealthy: Bool { (status ?? "ok") == "ok" }

    /// Prefer a user-set display name, then the server-provided account name,
    /// then the internal alias. This keeps account identity understandable
    /// without ever showing an empty label.
    public var preferredName: String {
        let candidates = [displayName, name, alias]
        for candidate in candidates {
            if let value = candidate?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty {
                return value
            }
        }
        return "未命名账号"
    }

    public func withActive(_ value: Bool?) -> RelayAccount {
        RelayAccount(
            id: id,
            alias: alias,
            displayName: displayName,
            email: email,
            name: name,
            plan: plan,
            accountId: accountId,
            status: status,
            lastRefresh: lastRefresh,
            lastUsedAt: lastUsedAt,
            lastVerifiedAt: lastVerifiedAt,
            usage: usage,
            active: value ?? active,
            usageText: usageText,
            lastError: lastError
        )
    }
    public var maxUsedPercent: Double {
        guard let usage else { return 0 }
        if usage.rateLimitReachedType != nil { return 100 }
        return max(usage.primary?.usedPercent ?? 0, usage.secondary?.usedPercent ?? 0)
    }
    public var isExhausted: Bool { maxUsedPercent >= 100 }
    public var statusLabel: String {
        if !isHealthy { return status == "needs_relogin" ? "需要重新登录" : (status ?? "异常") }
        if isExhausted { return "额度已耗尽" }
        return "可用"
    }
}

public struct RelayUsage: Decodable, Hashable {
    public let checkedAt: String?
    public let source: String?
    public let plan: String?
    public let accountId: String?
    public let primary: RelayUsageWindow?
    public let secondary: RelayUsageWindow?
    public let rateLimitReachedType: RelayLimitReached?

    public init(
        checkedAt: String? = nil,
        source: String? = nil,
        plan: String? = nil,
        accountId: String? = nil,
        primary: RelayUsageWindow? = nil,
        secondary: RelayUsageWindow? = nil,
        rateLimitReachedType: RelayLimitReached? = nil
    ) {
        self.checkedAt = checkedAt
        self.source = source
        self.plan = plan
        self.accountId = accountId
        self.primary = primary
        self.secondary = secondary
        self.rateLimitReachedType = rateLimitReachedType
    }
}

public enum RelayLimitReached: Decodable, Hashable {
    case string(String)
    case object([String: String])

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(String.self) {
            self = .string(value)
            return
        }
        if let value = try? container.decode([String: String].self) {
            self = .object(value)
            return
        }
        self = .string("rate_limit_reached")
    }
}

public struct RelayUsageWindow: Decodable, Hashable {
    public let usedPercent: Double?
    public let windowMinutes: Int?
    public let resetsAt: Int?

    public init(usedPercent: Double?, windowMinutes: Int?, resetsAt: Int?) {
        self.usedPercent = usedPercent
        self.windowMinutes = windowMinutes
        self.resetsAt = resetsAt
    }

    public var resetDate: Date? {
        guard let resetsAt else { return nil }
        return Date(timeIntervalSince1970: TimeInterval(resetsAt))
    }
}

public struct RelayAccountRef: Decodable, Hashable {
    public let alias: String?
    public let email: String?
    public let accountId: String?

    public init(alias: String?, email: String?, accountId: String?) {
        self.alias = alias
        self.email = email
        self.accountId = accountId
    }
}

public struct RelayProcess: Decodable, Hashable, Identifiable {
    public let pid: Int
    public let ppid: Int?
    public let role: String?
    public let started: String?
    public let command: String?

    public var id: Int { pid }
}

public struct RelayAuth: Decodable, Hashable {
    public let authMode: String?
    public let accountId: String?
    public let userId: String?
    public let email: String?
    public let name: String?
    public let plan: String?
    public let lastRefresh: String?
    public let accessExpiresAt: String?
    public let accessExpired: Bool?
    public let hasRefreshToken: Bool?
    public let hasApiKey: Bool?
}

public struct RelaySwitchRecord: Decodable, Hashable {
    public let at: String?
    public let from: RelayAccountRef?
    public let to: RelayAccountRef?
    public let mode: String?
    public let threadId: String?
    public let backup: String?
    public let appServerPid: Int?
}

public struct RelayStatus: Decodable {
    public let checkedAt: String?
    public let codexHome: String?
    public let relayHome: String?
    public let authPath: String?
    public let authPresent: Bool?
    public let auth: RelayAuth?
    public let currentProfile: RelayAccount?
    public let stateActiveProfileId: String?
    public let desktopRunning: Bool?
    public let desktopProcesses: [RelayProcess]?
    public let cliProcesses: [RelayProcess]?
    public let daemonRunning: Bool?
    public let daemonVersion: String?
    public let lastSwitch: RelaySwitchRecord?
    public let lastThreadId: String?
}

public struct RelaySessionList: Decodable {
    public let threads: [RelaySession]
}

public struct RelaySession: Decodable, Identifiable, Hashable {
    public let id: String
    public let rolloutPath: String?
    public let createdAt: Int64?
    public let updatedAt: Int64?
    public let updatedLocal: String?
    public let cwd: String?
    public let title: String?
    public let source: String?
    public let model: String?
    public let reasoningEffort: String?
    public let tokensUsed: Int64?
    public let archived: Bool?
    public let gitBranch: String?
    public let creatorAccountId: String?
    public let creatorUserId: String?
    public let creatorAlias: String?

    public var displayTitle: String {
        if let title, !title.isEmpty { return title }
        return "(未命名会话)"
    }

    public var shortID: String { String(id.prefix(8)) }
}

public struct RelayDoctorReport: Decodable {
    public let checkedAt: String?
    public let ok: Bool
    public let failures: Int
    public let warnings: Int
    public let checks: [RelayDoctorCheck]
}

public struct RelayDoctorCheck: Decodable, Identifiable, Hashable {
    public let check: String
    public let level: String
    public let message: String

    public var id: String { "\(check)-\(message)" }
    public var symbol: String {
        switch level {
        case "pass": return "checkmark.circle.fill"
        case "warn": return "exclamationmark.triangle.fill"
        default: return "xmark.octagon.fill"
        }
    }
}

public struct RelayInspectReport: Decodable {
    public let thread: RelaySession?
    public let metrics: RelayMetrics?
}

public struct RelayMetrics: Decodable {
    public let sizeHuman: String?
    public let reasoningItems: Int?
    public let reasoningEncryptedItems: Int?
    public let reasoningEncryptedBytes: Int?
    public let compactions: [RelayCompaction]?
    public let turns: [RelayTurnUsage]?
    public let totals: RelayTokenTotals?
}

public struct RelayTokenTotals: Decodable, Hashable {
    public let turns: Int?
    public let requests: Int?
    public let inputTokens: Int?
    public let cachedInputTokens: Int?
    public let outputTokens: Int?
    public let reasoningOutputTokens: Int?
    public let totalTokens: Int?
    public let cacheRatio: Double?
}

public struct RelayCompaction: Decodable, Identifiable, Hashable {
    public let timestamp: String?
    public let windowNumber: Int?
    public let itemCount: Int?
    public let hasEncryptedReasoning: Bool?
    public let summaryChars: Int?

    public var id: String { "\(windowNumber ?? 0)-\(timestamp ?? "")" }
}

public struct RelayTurnUsage: Decodable, Identifiable, Hashable {
    public let turnId: String
    public let requests: Int?
    public let firstInputTokens: Int?
    public let firstCachedTokens: Int?
    public let firstCacheRatio: Double?
    public let lastInputTokens: Int?
    public let lastCachedTokens: Int?
    public let lastCacheRatio: Double?
    public let totalCacheRatio: Double?
    public let firstTimestamp: String?
    public let outputTokens: Int?
    public let reasoningOutputTokens: Int?

    public var id: String { turnId }
}

public struct RelaySwitchResult: Decodable {
    public let continued: Bool?
    public let continueError: String?
    public let ok: Bool?
    public let changed: Bool?
    public let reason: String?
    public let from: RelayAccountRef?
    public let to: RelayAccountRef?
    public let mode: String?
    public let threadId: String?
    public let appServerPid: Int?
    public let durationMs: Int?
    public let backup: String?
    public let notes: [String]?
}

public struct RelayProgressEvent: Decodable {
    public let event: String
    public let stage: String?
    public let message: String?
}

public struct RelayAutoResult: Decodable {
    public let action: String?
    public let reason: String?
    public let from: String?
    public let to: String?
    public let continued: Bool?
    public let continueError: String?
    public let threadId: String?
    public let usedPercent: Double?
    public let threshold: Double?
}

public struct RelayVersionInfo {
    public let relay: String
    public let codex: String
    public let desktop: String
}

public struct RelayActivity: Decodable, Sendable {
    public let idle: Bool
    public let active: [RelayActiveTurn]
    public let ignoredHistory: [RelayActiveTurn]
}

public struct RelayActiveTurn: Decodable, Sendable {
    public let threadId: String
    public let turnId: String?
    public let evidence: String?
    public let reason: String?
}
