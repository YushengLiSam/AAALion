import SwiftUI
import Foundation

// MARK: - In-app language toggle (Chinese ⇄ English)
//
// SwiftUI's `Text("中文")` auto-localization resolves against the bundle's
// LAUNCH-TIME localization (the dev region), so a runtime in-app switch does
// NOT change it (and for the dev language it just shows the key verbatim —
// which is why bilingual "中文 / English" keys showed both halves). So instead
// of relying on SwiftUI auto-localization, every user-facing string goes
// through `L(...)`, which reads from the CHOSEN language's `.lproj` bundle
// directly. On switch, the root view's `.id(lang)` rebuild re-runs every
// `L(...)` against the newly-selected bundle — instant, no relaunch.

@Observable
final class LanguageManager {
    static let shared = LanguageManager()
    static let defaultsKey = "lionpick.language"

    enum Lang: String, CaseIterable, Identifiable {
        case zh
        case en
        var id: String { rawValue }
        var display: String { self == .zh ? "中文" : "English" }
        /// The `.lproj` folder name in the app bundle.
        var lproj: String { self == .en ? "en" : "zh-Hans" }
    }

    var lang: Lang {
        didSet {
            guard lang != oldValue else { return }
            UserDefaults.standard.set(lang.rawValue, forKey: Self.defaultsKey)
            bundle = Self.makeBundle(lang)
        }
    }

    /// The strings bundle for the current language; `L(_:)` reads from it.
    private(set) var bundle: Bundle

    private static func makeBundle(_ lang: Lang) -> Bundle {
        if let path = Bundle.main.path(forResource: lang.lproj, ofType: "lproj"),
           let b = Bundle(path: path) {
            return b
        }
        return .main
    }

    private init() {
        // CI / screenshot override: `-lionpickLang en` forces a language so the
        // English UI can be captured headlessly. Falls back to the saved choice.
        let argLang = UserDefaults.standard.string(forKey: "lionpickLang")
        let saved = UserDefaults.standard.string(forKey: Self.defaultsKey)
        let raw = argLang ?? saved ?? Lang.zh.rawValue
        let resolved = Lang(rawValue: raw) ?? .zh
        lang = resolved
        bundle = Self.makeBundle(resolved)
    }
}

/// Localize a UI string. The key is the original source literal (Chinese, or
/// the legacy bilingual "中文 / English"); the chosen `.lproj` maps it to the
/// right language (en → English; zh → the Chinese-only half / the key itself).
/// Falls back to the key when no translation exists, so it is always safe.
func L(_ key: String) -> String {
    LanguageManager.shared.bundle.localizedString(forKey: key, value: key, table: nil)
}

/// Localized + `String(format:)`, for interpolated strings. The key is the
/// Chinese format string with placeholders (e.g. "已 %@/%@ 人"); args fill in.
func Lf(_ formatKey: String, _ args: CVarArg...) -> String {
    String(format: L(formatKey), arguments: args)
}
