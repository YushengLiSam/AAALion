import Foundation

enum Config {
    private static let userDefaultsKey = "lionpick.backendURL"

    /// Backend URL the iOS app talks to.
    ///
    /// Resolution order:
    /// 1. Process env `PUBLIC_BACKEND_URL` (Xcode-scheme debug runs).
    /// 2. `UserDefaults.standard["lionpick.backendURL"]` (set via Settings sheet).
    /// 3. `defaultBackendURL` baked at build time.
    ///
    /// Update `defaultBackendURL` only when you redeploy; users change the
    /// runtime URL via the in-app Settings sheet (no rebuild needed).
    static let defaultBackendURL = "http://10.76.138.67:8000"

    static var backendURL: URL {
        if let raw = ProcessInfo.processInfo.environment["PUBLIC_BACKEND_URL"],
           let url = URL(string: raw) {
            return url
        }
        if let raw = UserDefaults.standard.string(forKey: userDefaultsKey),
           !raw.isEmpty,
           let url = URL(string: raw) {
            return url
        }
        return URL(string: defaultBackendURL)!
    }

    /// Persist a new backend URL. Empty string clears the override
    /// and falls back to `defaultBackendURL`.
    static func setBackendURL(_ raw: String) {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            UserDefaults.standard.removeObject(forKey: userDefaultsKey)
        } else {
            UserDefaults.standard.set(trimmed, forKey: userDefaultsKey)
        }
    }
}
