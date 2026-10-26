import Foundation

public struct GuardSettings: Codable, Equatable, Sendable {
    public var threshold: Double
    public var resumeRemaining: Double
    public var interval: Double
    public var continuation: String
}
public struct GuardWindow: Decodable, Sendable {
    public let name: String
    public let remaining: Double
    public let resetsAt: Double
    public let windowDurationMins: Double
}
public struct GuardForecastSlot: Decodable, Sendable {
    public let start: Double
    public let end: Double
    public let resetConfirmed: Bool
}
public struct GuardForecast: Decodable, Sendable {
    public let slots: [GuardForecastSlot]
    public let notice: String
}
public struct GuardHost: Decodable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let connected: Bool
    public let quotaKnown: Bool
    public let quotaReason: String?
    public let reason: String?
    public let windows: [GuardWindow]
    public let forecast: GuardForecast?
}
public struct GuardThread: Decodable, Identifiable, Hashable, Sendable {
    public let id: String
    public let hostId: String
    public let hostName: String
    public let name: String
    public let cwd: String?
    public var key: String { hostId + ":" + id }
}
public struct GuardMonitor: Decodable, Identifiable, Sendable {
    public let threadId: String
    public let hostId: String
    public let monitoring: Bool
    public let displayPhase: String
    public let reason: String?
    public let resumeCount: Int
    public var id: String { hostId + ":" + threadId }
}
public struct GuardSnapshot: Decodable, Sendable {
    public let connected: Bool
    public let hosts: [GuardHost]
    public let threads: [GuardThread]
    public let monitors: [GuardMonitor]
    public let warnings: [String]
    public let settings: GuardSettings
}
