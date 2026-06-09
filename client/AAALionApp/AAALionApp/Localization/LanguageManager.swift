import SwiftUI
import Foundation
import ObjectiveC

// MARK: - In-app language toggle (Chinese ⇄ English)
//
// Strategy: the app's source strings ARE the Chinese text (every `Text("中文")`
// uses that literal as its LocalizedStringKey). So Chinese mode needs NO strings
// table at all — the key renders verbatim. English mode points `Bundle.main` at
// `en.lproj/Localizable.strings` (key = the Chinese literal, value = English) via
// a runtime class override, so `Text("设置")` resolves to "Settings".
//
// String-typed / concatenated / interpolated values that SwiftUI does NOT treat
// as a LocalizedStringKey go through `L(_:)` below, which performs the same
// lookup explicitly.

@Observable
final class LanguageManager {
    static let shared = LanguageManager()
    static let defaultsKey = "lionpick.language"

    enum Lang: String, CaseIterable, Identifiable {
        case zh
        case en
        var id: String { rawValue }
        var display: String { self == .zh ? "中文" : "English" }
    }

    var lang: Lang {
        didSet {
            guard lang != oldValue else { return }
            UserDefaults.standard.set(lang.rawValue, forKey: Self.defaultsKey)
            Bundle.applyAppLanguage(lang)
        }
    }

    private init() {
        // CI / screenshot override: `-lionpickLang en` forces a language so the
        // English UI can be captured headlessly. Falls back to the saved choice.
        let argLang = UserDefaults.standard.string(forKey: "lionpickLang")
        let saved = UserDefaults.standard.string(forKey: Self.defaultsKey)
        let raw = argLang ?? saved ?? Lang.zh.rawValue
        lang = Lang(rawValue: raw) ?? .zh
        Bundle.applyAppLanguage(lang)
    }
}

/// Explicit localization lookup for String-typed values (toasts, computed
/// summaries, concatenations, arrays) that SwiftUI won't auto-localize.
/// Falls back to the Chinese key when no translation exists, so it is always
/// safe to wrap a string in `L(...)`.
func L(_ zhKey: String) -> String {
    Bundle.main.localizedString(forKey: zhKey, value: zhKey, table: nil)
}

/// Localized + formatted, for interpolated strings. The key is the Chinese
/// format string with printf placeholders (e.g. "已 %d/%d 人"); the en/zh-Hans
/// table maps it to the translated format, then args are filled in. Reliable
/// for String-typed values where SwiftUI's auto-LocalizedStringKey can't apply.
func Lf(_ zhFormatKey: String, _ args: CVarArg...) -> String {
    String(format: L(zhFormatKey), arguments: args)
}

// MARK: - Runtime bundle-language override

private var _langBundlePathKey: UInt8 = 0

/// A Bundle subclass that redirects every localized-string lookup to the
/// language `.lproj` chosen at runtime. Installed onto `Bundle.main` via
/// `object_setClass` (one-time) so SwiftUI's `Text(LocalizedStringKey)` and
/// `NSLocalizedString` both honor the in-app toggle without an app relaunch.
private final class _RuntimeLanguageBundle: Bundle, @unchecked Sendable {
    override func localizedString(forKey key: String, value: String?, table tableName: String?) -> String {
        if let path = objc_getAssociatedObject(self, &_langBundlePathKey) as? String,
           let override = Bundle(path: path) {
            return override.localizedString(forKey: key, value: value, table: tableName)
        }
        return super.localizedString(forKey: key, value: value, table: tableName)
    }
}

private let _installRuntimeBundleOnce: Void = {
    object_setClass(Bundle.main, _RuntimeLanguageBundle.self)
}()

extension Bundle {
    /// Point `Bundle.main` at the chosen language's strings table.
    /// Both languages use an override: `zh-Hans.lproj` strips the legacy
    /// bilingual "中文 / English" keys down to their Chinese half (monolingual
    /// Chinese keys simply fall back to themselves), and `en.lproj` carries the
    /// full English translation.
    static func applyAppLanguage(_ lang: LanguageManager.Lang) {
        _ = _installRuntimeBundleOnce
        let resource = (lang == .en) ? "en" : "zh-Hans"
        let path = Bundle.main.path(forResource: resource, ofType: "lproj")
        objc_setAssociatedObject(Bundle.main, &_langBundlePathKey, path, .OBJC_ASSOCIATION_RETAIN)
    }
}
