import AppKit
import CodexRelayCore
import SwiftUI

enum RelayAppearance: String, CaseIterable, Identifiable {
    case system
    case dark
    case light

    var id: String { rawValue }
    var title: String {
        switch self {
        case .system: return "跟随系统"
        case .dark: return "暗色终端"
        case .light: return "亮色档案"
        }
    }
    var shortTitle: String {
        switch self {
        case .system: return "系统"
        case .dark: return "暗色"
        case .light: return "亮色"
        }
    }
    var icon: String {
        switch self {
        case .system: return "circle.lefthalf.filled"
        case .dark: return "moon.fill"
        case .light: return "sun.max.fill"
        }
    }
    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .dark: return .dark
        case .light: return .light
        }
    }
}

private func relayDynamic(
    _ name: String,
    light: NSColor,
    dark: NSColor
) -> Color {
    Color(nsColor: NSColor(name: NSColor.Name(name), dynamicProvider: { appearance in
        let match = appearance.bestMatch(from: [.darkAqua, .aqua])
        return match == .darkAqua ? dark : light
    }))
}

enum RelayTheme {
    // Arknights-inspired: monochrome first, cyan action, yellow warning.
    // Dark = terminal / operations; Light = archive / paper dossier.
    static var background: Color {
        relayDynamic(
        "relay.background",
        light: NSColor(red: 0.953, green: 0.953, blue: 0.933, alpha: 1),
        dark: NSColor(red: 0.031, green: 0.039, blue: 0.043, alpha: 1)
        )
    }
    static var panel: Color {
        relayDynamic(
        "relay.panel",
        light: NSColor(red: 0.992, green: 0.992, blue: 0.980, alpha: 1),
        dark: NSColor(red: 0.067, green: 0.078, blue: 0.086, alpha: 1)
        )
    }
    static var panelElevated: Color {
        relayDynamic(
        "relay.panelElevated",
        light: NSColor(red: 0.925, green: 0.925, blue: 0.900, alpha: 1),
        dark: NSColor(red: 0.090, green: 0.102, blue: 0.110, alpha: 1)
        )
    }
    static var line: Color {
        relayDynamic(
        "relay.line",
        light: NSColor.black.withAlphaComponent(0.14),
        dark: NSColor.white.withAlphaComponent(0.10)
        )
    }
    static var text: Color {
        relayDynamic(
        "relay.text",
        light: NSColor(red: 0.086, green: 0.098, blue: 0.102, alpha: 1),
        dark: NSColor(red: 0.910, green: 0.902, blue: 0.875, alpha: 1)
        )
    }
    static var textMuted: Color {
        relayDynamic(
        "relay.textMuted",
        light: NSColor(red: 0.345, green: 0.380, blue: 0.395, alpha: 1),
        dark: NSColor(red: 0.610, green: 0.650, blue: 0.670, alpha: 1)
        )
    }
    static var accent: Color {
        relayDynamic(
        "relay.accent",
        light: NSColor(red: 0.0, green: 0.459, blue: 0.639, alpha: 1),
        dark: NSColor(red: 0.094, green: 0.820, blue: 1.0, alpha: 1)
        )
    }
    static var accentDim: Color {
        relayDynamic(
        "relay.accentDim",
        light: NSColor(red: 0.0, green: 0.373, blue: 0.525, alpha: 1),
        dark: NSColor(red: 0.0, green: 0.596, blue: 0.863, alpha: 1)
        )
    }
    static var purple: Color {
        relayDynamic(
        "relay.purple",
        light: NSColor(red: 0.325, green: 0.400, blue: 0.780, alpha: 1),
        dark: NSColor(red: 0.490, green: 0.560, blue: 1.0, alpha: 1)
        )
    }
    static var green: Color {
        relayDynamic(
        "relay.green",
        light: NSColor(red: 0.137, green: 0.541, blue: 0.357, alpha: 1),
        dark: NSColor(red: 0.478, green: 0.898, blue: 0.510, alpha: 1)
        )
    }
    static var amber: Color {
        relayDynamic(
        "relay.amber",
        light: NSColor(red: 0.698, green: 0.463, blue: 0.0, alpha: 1),
        dark: NSColor(red: 1.0, green: 0.831, blue: 0.161, alpha: 1)
        )
    }
    static var amberText: Color {
        relayDynamic(
        "relay.amberText",
        light: NSColor(red: 0.541, green: 0.365, blue: 0.0, alpha: 1),
        dark: NSColor(red: 1.0, green: 0.831, blue: 0.161, alpha: 1)
        )
    }
    static var amberFill: Color {
        relayDynamic(
        "relay.amberFill",
        light: NSColor(red: 0.949, green: 0.690, blue: 0.0, alpha: 1),
        dark: NSColor(red: 1.0, green: 0.831, blue: 0.161, alpha: 1)
        )
    }
    static var orange: Color {
        relayDynamic(
        "relay.orange",
        light: NSColor(red: 0.769, green: 0.353, blue: 0.071, alpha: 1),
        dark: NSColor(red: 1.0, green: 0.541, blue: 0.239, alpha: 1)
        )
    }
    static var red: Color {
        relayDynamic(
        "relay.red",
        light: NSColor(red: 0.702, green: 0.196, blue: 0.196, alpha: 1),
        dark: NSColor(red: 1.0, green: 0.353, blue: 0.373, alpha: 1)
        )
    }
    static var onAccent: Color {
        relayDynamic(
        "relay.onAccent",
        light: NSColor.white,
        dark: NSColor(red: 0.024, green: 0.063, blue: 0.078, alpha: 1)
        )
    }
    static var track: Color {
        relayDynamic(
        "relay.track",
        light: NSColor.black.withAlphaComponent(0.08),
        dark: NSColor.white.withAlphaComponent(0.07)
        )
    }
    static var hover: Color {
        relayDynamic(
        "relay.hover",
        light: NSColor.black.withAlphaComponent(0.045),
        dark: NSColor.white.withAlphaComponent(0.035)
        )
    }
    static var grid: Color {
        relayDynamic(
        "relay.grid",
        light: NSColor.black.withAlphaComponent(0.055),
        dark: NSColor.white.withAlphaComponent(0.025)
        )
    }
    static var shadow: Color {
        relayDynamic(
        "relay.shadow",
        light: NSColor.black.withAlphaComponent(0.12),
        dark: NSColor.black.withAlphaComponent(0.40)
        )
    }
    static let cardRadius: CGFloat = 2

    static func levelColor(_ level: String) -> Color {
        switch level {
        case "pass": return green
        case "warn": return amber
        default: return red
        }
    }

    static func usageColor(_ usedPercent: Double) -> Color {
        quotaTextColor(usedPercent: usedPercent, mode: .used)
    }

    static func quotaTextColor(usedPercent: Double?, mode: QuotaDisplayMode) -> Color {
        guard let used = usedPercent.map({ min(max($0, 0), 100) }) else {
            return textMuted
        }
        if mode == .remaining {
            let remaining = max(0, 100 - used)
            if remaining <= 5 { return red }
            if remaining <= 20 { return amberText }
            return green
        }
        if used >= 95 { return red }
        if used >= 75 { return amberText }
        return green
    }

    static func quotaBarColor(usedPercent: Double?, mode: QuotaDisplayMode) -> Color {
        guard let used = usedPercent.map({ min(max($0, 0), 100) }) else {
            return textMuted
        }
        if mode == .remaining {
            let remaining = max(0, 100 - used)
            if remaining <= 5 { return red }
            if remaining <= 20 { return amberFill }
            return green
        }
        if used >= 95 { return red }
        if used >= 75 { return amberFill }
        return green
    }

    /// Cache hit rate is the opposite of quota usage: higher is better.
    static func cacheColor(_ percent: Double) -> Color {
        if percent >= 90 { return green }
        if percent >= 60 { return accent }
        return amberText
    }
}

extension Font {
    static func hudDisplay(_ size: CGFloat, weight: Font.Weight = .bold) -> Font {
        .custom("DIN Alternate", size: size).weight(weight)
    }

    static func hudMono(_ size: CGFloat, weight: Font.Weight = .regular) -> Font {
        .system(size: size, weight: weight, design: .monospaced)
    }

    static func hudLabel(_ size: CGFloat = 9) -> Font {
        .system(size: size, weight: .semibold, design: .monospaced)
    }
}

// MARK: - Background

struct HudBackground: View {
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        ZStack {
            RelayTheme.background
            Canvas { context, size in
                var grid = Path()
                let step: CGFloat = 32
                var x: CGFloat = 0
                while x <= size.width {
                    grid.move(to: CGPoint(x: x, y: 0))
                    grid.addLine(to: CGPoint(x: x, y: size.height))
                    x += step
                }
                var y: CGFloat = 0
                while y <= size.height {
                    grid.move(to: CGPoint(x: 0, y: y))
                    grid.addLine(to: CGPoint(x: size.width, y: y))
                    y += step
                }
                context.stroke(grid, with: .color(colorScheme == .dark ? .white.opacity(0.025) : .black.opacity(0.045)), lineWidth: 0.5)

                // Sparse halftone dots, a nod to the printed dossier texture.
                let dotColor = colorScheme == .dark ? Color.white.opacity(0.025) : Color.black.opacity(0.035)
                var dotY: CGFloat = 16
                while dotY < size.height {
                    var dotX: CGFloat = 16
                    while dotX < size.width {
                        context.fill(Path(ellipseIn: CGRect(x: dotX, y: dotY, width: 1.2, height: 1.2)), with: .color(dotColor))
                        dotX += 64
                    }
                    dotY += 64
                }
            }
            RadialGradient(
                colors: [RelayTheme.accent.opacity(colorScheme == .dark ? 0.07 : 0.05), .clear],
                center: .topLeading,
                startRadius: 20,
                endRadius: 720
            )
            if colorScheme == .dark {
                TimelineView(.animation(minimumInterval: 1.0 / 12.0)) { timeline in
                    GeometryReader { proxy in
                        let height = max(proxy.size.height, 1)
                        let y = (timeline.date.timeIntervalSinceReferenceDate * 26)
                            .truncatingRemainder(dividingBy: Double(height))
                        Rectangle()
                            .fill(
                                LinearGradient(
                                    colors: [.clear, RelayTheme.accent.opacity(0.10), .clear],
                                    startPoint: .leading,
                                    endPoint: .trailing
                                )
                            )
                            .frame(height: 1)
                            .offset(y: CGFloat(y))
                    }
                    .opacity(0.55)
                }
            }
            LinearGradient(
                colors: [.clear, RelayTheme.shadow.opacity(colorScheme == .dark ? 0.55 : 0.20)],
                startPoint: .center,
                endPoint: .bottom
            )
        }
        .ignoresSafeArea()
    }
}

// MARK: - Angular primitives

struct HudCorner: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let length: CGFloat = 14
        path.move(to: CGPoint(x: rect.minX, y: rect.minY + length))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.minX + length, y: rect.minY))
        path.move(to: CGPoint(x: rect.maxX - length, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - length))
        return path
    }
}

struct RelayCard<Content: View>: View {
    @Environment(\.colorScheme) private var colorScheme
    var padding: CGFloat = 18
    var accented: Bool = false
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                ZStack {
                    RelayTheme.panel
                    LinearGradient(
                        colors: [
                            RelayTheme.panelElevated.opacity(colorScheme == .dark ? 0.42 : 0.55),
                            RelayTheme.panel.opacity(0)
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))
            .overlay(alignment: .topLeading) {
                Rectangle()
                    .fill(RelayTheme.accent)
                    .frame(width: accented ? 52 : 28, height: 2)
            }
            .overlay {
                if accented {
                    HudCorner()
                        .stroke(RelayTheme.accent.opacity(colorScheme == .dark ? 0.65 : 0.80), lineWidth: 1)
                        .padding(5)
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .strokeBorder(RelayTheme.line.opacity(0.75), lineWidth: 0.7)
            }
            .shadow(
                color: RelayTheme.shadow.opacity(colorScheme == .dark ? 0.18 : 0.06),
                radius: 8,
                y: 3
            )
    }
}

struct HudSectionTitle: View {
    @Environment(\.locale) private var locale
    let code: String
    let title: String
    let subtitle: String?

    init(code: String, title: String, subtitle: String? = nil) {
        self.code = code
        self.title = title
        self.subtitle = subtitle
    }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Rectangle()
                .fill(RelayTheme.accent)
                .frame(width: 3, height: 38)
            VStack(alignment: .leading, spacing: 3) {
                Text(code.localized(locale: locale)).font(.hudLabel()).tracking(1.8).foregroundStyle(RelayTheme.accent)
                Text(title.localized(locale: locale)).font(.hudDisplay(28)).foregroundStyle(RelayTheme.text)
                if let subtitle {
                    Text(subtitle.localized(locale: locale)).font(.caption).foregroundStyle(RelayTheme.textMuted)
                }
            }
        }
    }
}

struct HudLabel: View {
    @Environment(\.locale) private var locale
    let text: String
    var color: Color = RelayTheme.textMuted

    var body: some View {
        Text(text.localized(locale: locale))
            .font(.hudLabel())
            .tracking(1.4)
            .foregroundStyle(color)
            .lineLimit(1)
            .minimumScaleFactor(0.7)
    }
}

struct HudDivider: View {
    var body: some View {
        Rectangle()
            .fill(RelayTheme.line)
            .frame(height: 1)
    }
}

struct HazardStripe: View {
    var color: Color = RelayTheme.amber
    var height: CGFloat = 4

    var body: some View {
        Canvas { context, size in
            let stripe: CGFloat = 8
            var x: CGFloat = -size.height
            while x < size.width + size.height {
                var path = Path()
                path.move(to: CGPoint(x: x, y: size.height))
                path.addLine(to: CGPoint(x: x + size.height, y: 0))
                path.addLine(to: CGPoint(x: x + size.height + stripe, y: 0))
                path.addLine(to: CGPoint(x: x + stripe, y: size.height))
                context.fill(path, with: .color(color.opacity(0.55)))
                x += stripe * 2.4
            }
        }
        .frame(height: height)
        .clipped()
    }
}

// MARK: - Badges / bars / rows

struct StatusBadge: View {
    @Environment(\.locale) private var locale
    let text: String
    let color: Color
    var icon: String? = nil

    var body: some View {
        HStack(spacing: 4) {
            if let icon {
                Image(systemName: icon).font(.system(size: 8.5, weight: .bold))
            } else {
                Rectangle().fill(color).frame(width: 4, height: 4)
            }
            Text(text.localized(locale: locale))
                .font(.hudLabel(8.5))
                .tracking(0.55)
                .lineLimit(1)
        }
        .foregroundStyle(color)
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(color.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 3, style: .continuous))
    }
}

struct UsageBar: View {
    @Environment(\.locale) private var locale
    let title: String
    let window: RelayUsageWindow?
    var compact: Bool = false
    var mode: QuotaDisplayMode = .remaining
    var showsModeLabel: Bool = true

    var body: some View {
        let used = window?.clampedUsedPercent
        let shown = window?.percent(for: mode)
        let textColor = RelayTheme.quotaTextColor(usedPercent: used, mode: mode)
        let barColor = RelayTheme.quotaBarColor(usedPercent: used, mode: mode)
        VStack(alignment: .leading, spacing: compact ? 4 : 7) {
            HStack(spacing: 5) {
                Text(title.localized(locale: locale))
                    .font(.hudLabel(compact ? 8 : 9))
                    .tracking(0.7)
                    .foregroundStyle(RelayTheme.textMuted)
                if showsModeLabel {
                    Text(mode.shortTitle.localized(locale: locale))
                        .font(.hudLabel(compact ? 7 : 8))
                        .foregroundStyle(textColor.opacity(0.9))
                }
                Spacer()
                Text(shown.map { String(format: "%.0f%%", $0) } ?? "-")
                    .font(.hudMono(compact ? 9 : 11, weight: .semibold))
                    .foregroundStyle(textColor)
            }
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                        .fill(RelayTheme.track)
                    RoundedRectangle(cornerRadius: 1.5, style: .continuous)
                        .fill(
                            LinearGradient(
                                colors: [barColor.opacity(0.72), barColor],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .frame(width: max(0, proxy.size.width * CGFloat((shown ?? 0) / 100)))
                    HStack(spacing: 0) {
                        ForEach(0..<10, id: \.self) { index in
                            Rectangle()
                                .fill(RelayTheme.background.opacity(0.55))
                                .frame(width: 1)
                            if index < 9 { Spacer(minLength: 0) }
                        }
                    }
                }
            }
            .frame(height: compact ? 4 : 6)
            if let reset = window?.resetDate, !compact {
                Text(L10n.format("重置 // %@", reset.localizedString(locale: locale)))
                    .font(.hudLabel(8))
                    .foregroundStyle(.tertiary)
            }
        }
    }
}

struct KVRow: View {
    @Environment(\.locale) private var locale
    let key: String
    let value: String
    var monospaced: Bool = false

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(key.localized(locale: locale))
                .font(.hudLabel(8))
                .tracking(0.8)
                .foregroundStyle(RelayTheme.textMuted)
                .frame(minWidth: 78, maxWidth: 110, alignment: .leading)
            Spacer(minLength: 0)
            Text(value)
                .font(monospaced ? .hudMono(10) : .system(size: 11.5))
                .foregroundStyle(RelayTheme.text)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
        .padding(.vertical, 1)
    }
}

struct EmptyStateView: View {
    @Environment(\.locale) private var locale
    let icon: String
    let title: String
    let message: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 30, weight: .light))
                .foregroundStyle(RelayTheme.accent.opacity(0.75))
            Text(title.localized(locale: locale))
                .font(.hudLabel(11))
                .tracking(1.2)
                .foregroundStyle(RelayTheme.text)
            Text(message.localized(locale: locale))
                .font(.callout)
                .foregroundStyle(RelayTheme.textMuted)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 360)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(32)
    }
}

// MARK: - Buttons

struct HudButtonStyle: ButtonStyle {
    var primary: Bool = false
    var danger: Bool = false
    var compact: Bool = false

    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        let base = danger ? RelayTheme.red : RelayTheme.accent
        configuration.label
            .font(.hudLabel(compact ? 8 : 9))
            .tracking(0.65)
            .foregroundStyle(primary ? RelayTheme.onAccent : (danger ? RelayTheme.red : RelayTheme.text))
            .lineLimit(2)
            .multilineTextAlignment(.center)
            .padding(.horizontal, compact ? 8 : 12)
            .padding(.vertical, compact ? 4 : 7)
            .background {
                if primary {
                    RoundedRectangle(cornerRadius: 4, style: .continuous)
                        .fill(
                            LinearGradient(
                                colors: [base, base.opacity(0.80)],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                } else {
                    RoundedRectangle(cornerRadius: 4, style: .continuous)
                        .fill(RelayTheme.panelElevated)
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .strokeBorder(primary ? base.opacity(0.05) : RelayTheme.line.opacity(0.65), lineWidth: 0.7)
            }
            .shadow(color: primary ? base.opacity(0.20) : .clear, radius: 4, y: 1)
            .opacity(isEnabled ? (configuration.isPressed ? 0.70 : 1) : 0.42)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

struct HudTextFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .font(.hudMono(12))
            .foregroundStyle(RelayTheme.text)
            .padding(.horizontal, 11)
            .padding(.vertical, 9)
            .background(RelayTheme.panelElevated)
            .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .strokeBorder(RelayTheme.line.opacity(0.65), lineWidth: 0.7)
            }
    }
}

struct HudNotice<Content: View>: View {
    let color: Color
    @ViewBuilder var content: Content

    init(color: Color, @ViewBuilder content: () -> Content) {
        self.color = color
        self.content = content()
    }

    var body: some View {
        content
            .padding(12)
            .background(color.opacity(0.075))
            .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
            .overlay(alignment: .leading) {
                Rectangle().fill(color.opacity(0.9)).frame(width: 2)
            }
            .overlay {
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .strokeBorder(color.opacity(0.22), lineWidth: 0.7)
            }
    }
}

struct SwitchProgressBar: View {
    let active: Bool

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { timeline in
            GeometryReader { proxy in
                let width = max(proxy.size.width, 1)
                let segment = min(max(width * 0.28, 72), 150)
                let cycle = 1.45
                let phase = CGFloat(
                    timeline.date.timeIntervalSinceReferenceDate
                        .truncatingRemainder(dividingBy: cycle) / cycle
                )
                let secondPhase = (phase + 0.52).truncatingRemainder(dividingBy: 1)
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(RelayTheme.track)
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(
                            LinearGradient(
                                colors: [.clear, RelayTheme.accent.opacity(0.9), .clear],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .frame(width: segment)
                        .offset(x: -segment + (width + segment) * phase)
                        .opacity(active ? 0.92 : 0)
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(RelayTheme.accent.opacity(active ? 0.38 : 0))
                        .frame(width: segment * 0.72)
                        .offset(x: -segment + (width + segment) * secondPhase)
                }
                .clipShape(RoundedRectangle(cornerRadius: 2, style: .continuous))
            }
        }
        .frame(height: 6)
    }
}

struct HudProgressLine: View {
    var value: Double? = nil
    var color: Color = RelayTheme.accent

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 1.5).fill(RelayTheme.track)
                if let value {
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(LinearGradient(colors: [color.opacity(0.7), color], startPoint: .leading, endPoint: .trailing))
                        .frame(width: proxy.size.width * CGFloat(min(max(value, 0), 1)))
                } else {
                    TimelineView(.animation(minimumInterval: 1.0 / 20.0)) { timeline in
                        let phase = timeline.date.timeIntervalSinceReferenceDate
                            .truncatingRemainder(dividingBy: 1.8) / 1.8
                        RoundedRectangle(cornerRadius: 1.5)
                            .fill(LinearGradient(colors: [.clear, color, .clear], startPoint: .leading, endPoint: .trailing))
                            .frame(width: proxy.size.width * 0.32)
                            .offset(x: (proxy.size.width + 20) * CGFloat(phase) - 20)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 1.5))
                }
            }
        }
        .frame(height: 4)
    }
}

enum AppVersion {
    static var display: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "0.3.0"
    }
}

extension Error {
    /// User-facing backend errors are localized in the app layer; the core
    /// target intentionally has no dependency on SwiftUI/L10n.
    var relayLocalizedDescription: String {
        guard let error = self as? RelayBackendError else {
            return localizedDescription
        }
        switch error {
        case .binaryNotFound:
            return L10n.text("找不到 codex-relay。请确认已运行 install.sh，或设置 CODEX_RELAY_BIN。")
        case .launchFailed(let message):
            return L10n.format("无法启动 codex-relay：%@", message)
        case .timeout(let command):
            return L10n.format("命令超时：%@", command)
        case .commandFailed(let code, let message):
            return L10n.format("codex-relay 退出码 %d：%@", code, message)
        case .decoding(let message):
            let detail = message == "输出为空" ? L10n.text("输出为空") : message
            return L10n.format("无法解析 codex-relay 输出：%@", detail)
        }
    }
}

extension RelayUsageWindow {
    /// Localized window duration (for example 5 hours / 1 week), independent
    /// of the raw `windowMinutes` value returned by Codex.
    func displayWindowLabel(locale: Locale) -> String {
        guard let windowMinutes else { return "" }
        if windowMinutes >= 24 * 60 {
            return L10n.format("%d 天", windowMinutes / (24 * 60))
        }
        if windowMinutes >= 60 {
            return L10n.format("%d 小时", windowMinutes / 60)
        }
        return L10n.format("%d 分钟", windowMinutes)
    }
}

extension RelayAccount {
    /// The core model uses a Chinese fallback marker for unnamed accounts;
    /// localize it at the presentation boundary without touching user data.
    func displayName(locale: Locale) -> String {
        let name = preferredName
        return name == "未命名账号" ? "未命名账号".localized(locale: locale) : name
    }
}

extension RelaySession {
    func displayTitle(locale: Locale) -> String {
        let title = displayTitle
        return title == "(未命名会话)" ? "(未命名会话)".localized(locale: locale) : title
    }
}

enum RelayFormat {
    static func localTimestamp(_ raw: String?, locale: Locale = .current) -> String {
        guard let raw, !raw.isEmpty else { return "-" }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = iso.date(from: raw)
        if date == nil {
            iso.formatOptions = [.withInternetDateTime]
            date = iso.date(from: raw)
        }
        guard let date else { return raw }
        return date.localizedString(locale: locale)
    }
}

extension String {
    var shortened: String {
        count > 42 ? String(prefix(41)) + "…" : self
    }
}
