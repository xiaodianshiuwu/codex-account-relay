import AppKit
import SwiftUI
import CodexRelayCore

/// Offscreen review with synthetic data only. Never starts the normal app lifecycle.
@MainActor
func renderReview(to directory: URL) throws {
    guard ProcessInfo.processInfo.environment["CODEX_RELAY_HOME"] != nil,
          ProcessInfo.processInfo.environment["CODEX_RELAY_PREFS_SUITE"] != nil else {
        throw NSError(domain: "ReviewPreview", code: 1, userInfo: [NSLocalizedDescriptionKey: "Preview requires isolated data and preferences"])
    }
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let preferences = AppPreferences()
    let model = AppModel()
    let account = RelayAccount(id: "fixture", alias: "fixture", displayName: "Example", email: "example@example.com", status: "ok")
    model.accounts = [account]
    let thread = "11111111-1111-4111-8111-111111111111"
    let fixture = """
    {"connected":true,"hosts":[{"id":"local","name":"Mac","connected":true,"quotaKnown":true,"windows":[{"name":"primary","remaining":4,"resetsAt":1800000000,"windowDurationMins":300}]}],"threads":[{"id":"11111111-1111-4111-8111-111111111111","hostId":"local","hostName":"Mac","name":"Example project","cwd":"/example/project"}],"monitors":[],"warnings":[],"settings":{"threshold":5,"resumeRemaining":10,"interval":30,"continuation":"Continue the remaining task."}}
    """
    let snapshot = try JSONDecoder().decode(GuardSnapshot.self, from: Data(fixture.utf8))
    func render<V: View>(_ root: V, name: String, size: NSSize) throws {
        let content = root.environmentObject(model).environmentObject(preferences)
            .environment(\.locale, preferences.language.locale).preferredColorScheme(preferences.preferredColorScheme)
        let hosting = NSHostingView(rootView: content)
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: .borderless,
                              backing: .buffered, defer: false)
        window.appearance = NSAppearance(named: preferences.appearance == .light ? .aqua : .darkAqua)
        window.contentView = hosting
        hosting.frame = NSRect(origin: .zero, size: size)
        hosting.layoutSubtreeIfNeeded()
        guard let bitmap = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else { throw POSIXError(.EIO) }
        hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:]) else { throw POSIXError(.EIO) }
        try png.write(to: directory.appendingPathComponent(name + ".png"))
        window.orderOut(nil)
    }
    let activityDecoder = JSONDecoder()
    activityDecoder.keyDecodingStrategy = .convertFromSnakeCase
    let activity = try activityDecoder.decode(RelayActivity.self, from: Data("""
    {"idle":false,"active":[{"thread_id":"11111111-1111-4111-8111-111111111111","turn_id":"fixture-turn","evidence":"desktop-runtime"}],"ignored_history":[{"thread_id":"22222222-2222-4222-8222-222222222222","reason":"unowned-before-desktop-start"}]}
    """.utf8))
    for language in AppLanguage.allCases {
        preferences.language = language
        preferences.appearance = .dark
        try render(ProcessConfirmationSheet(action: PendingProcessAction(account: account, threadID: thread, kind: .switchAccount), previewActivity: activity, previewConfirmedIdle: true),
                   name: "confirmation-" + language.rawValue, size: NSSize(width: 650, height: 720))
    }
    preferences.language = .en
    for appearance in [RelayAppearance.light, .dark] {
        preferences.appearance = appearance
        try render(GuardView(preview: snapshot), name: "guard-" + appearance.rawValue, size: NSSize(width: 1000, height: 1000))
    }
    if let suite = ProcessInfo.processInfo.environment["CODEX_RELAY_PREFS_SUITE"] {
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
    }
}
