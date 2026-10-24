import Foundation
import SwiftUI

enum AppLanguage: String, CaseIterable, Identifiable {
    case zhCN = "zh-Hans"
    case zhTW = "zh-Hant"
    case en
    case ja

    var id: String { rawValue }

    var nativeTitle: String {
        switch self {
        case .zhCN: return "简体中文"
        case .zhTW: return "繁體中文"
        case .en: return "English"
        case .ja: return "日本語"
        }
    }

    var locale: Locale {
        Locale(identifier: rawValue)
    }

    var resourceName: String { rawValue }

    static var systemDefault: AppLanguage {
        for identifier in Locale.preferredLanguages {
            let lower = identifier.lowercased()
            if lower.hasPrefix("zh-hant") || lower.hasPrefix("zh-tw") || lower.hasPrefix("zh-hk") || lower.hasPrefix("zh-mo") {
                return .zhTW
            }
            if lower.hasPrefix("zh") {
                return .zhCN
            }
            if lower.hasPrefix("ja") {
                return .ja
            }
            if lower.hasPrefix("en") {
                return .en
            }
        }
        return .zhCN
    }
}

enum L10n {
    static var language: AppLanguage = .systemDefault

    static func text(_ key: String, language: AppLanguage? = nil) -> String {
        localized(key, language: language ?? self.language)
    }

    static func format(_ key: String, _ arguments: CVarArg..., language: AppLanguage? = nil) -> String {
        String(format: text(key, language: language), arguments: arguments)
    }

    static func localized(_ key: String, language: AppLanguage) -> String {
        guard let path = Bundle.main.path(forResource: language.resourceName, ofType: "lproj"),
              let bundle = Bundle(path: path) else {
            return key
        }
        return bundle.localizedString(forKey: key, value: key, table: nil)
    }
}

extension Date {
    func localizedString(locale: Locale) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        return formatter.string(from: self)
    }
}

extension String {
    func localized(locale: Locale) -> String {
        let language = AppLanguage.allCases.first(where: { locale.identifier.hasPrefix($0.rawValue) })
            ?? .zhCN
        return L10n.localized(self, language: language)
    }
}

extension View {
    func localizedFrame() -> some View {
        self
    }
}
