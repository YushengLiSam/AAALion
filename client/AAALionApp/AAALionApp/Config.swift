import Foundation

enum Config {
    private static let userDefaultsKey = "lionpick.backendURL"
    /// R9.A — keyed UserDefault that records WHICH baked default URL
    /// the stored UserDefaults override was set against. If the app
    /// boots with a different `defaultBackendURL` (because we rebaked
    /// after the tunnel rotated) we auto-purge the stale override so
    /// the new default takes effect. Without this, every URL rotation
    /// would require the user to manually delete the app.
    private static let userDefaultsBaselineKey = "lionpick.backendURL.bakedAgainst"

    /// Backend URL the iOS app talks to.
    ///
    /// Resolution order:
    /// 1. Process env `PUBLIC_BACKEND_URL` (Xcode-scheme debug runs).
    /// 2. `UserDefaults.standard["lionpick.backendURL"]` (set via Settings sheet)
    ///    — auto-purged on launch if the baseline doesn't match the current
    ///    baked default (handles tunnel rotation).
    /// 3. `defaultBackendURL` baked at build time.
    ///
    /// Update `defaultBackendURL` only when you redeploy; users change the
    /// runtime URL via the in-app Settings sheet (no rebuild needed).
    // R8.D: public Cloudflare Tunnel URL pointing at the Mac's backend.
    // Works from any network (cellular, hotel Wi-Fi, anywhere) — no LAN
    // configuration needed. URL changes whenever cloudflared restarts;
    // refresh by running `tools/start-tunnel.sh` and re-baking this string.
    //
    // Phase 2 (before defense, ~2026-06-05): replace this with a cloud-VM
    // URL (Hetzner CX22 + `api.lionpick.<domain>`) so the Mac isn't
    // a dependency for judges. See docs/DEPLOY_GUIDE.md §Cloud VM.
    //
    // For Mac-side dev (simulator), `PUBLIC_BACKEND_URL=http://localhost:8000`
    // env var in the Xcode scheme overrides this to avoid round-tripping
    // through the public internet.
    static let defaultBackendURL = "https://actions-funeral-treating-trigger.trycloudflare.com"

    static var backendURL: URL {
        if let raw = ProcessInfo.processInfo.environment["PUBLIC_BACKEND_URL"],
           let url = URL(string: raw) {
            return url
        }
        // R9.A auto-purge: if the baked default URL changed (e.g. we
        // rotated the cloudflared quick tunnel), any UserDefault saved
        // against the old default is stale by definition. Wipe it on
        // first launch with the new default so we don't fall back to a
        // dead tunnel. If the user has TYPED a different URL via the
        // dev-mode Settings sheet, setBackendURL() re-stamps the
        // baseline so their override survives the next rebake.
        let defaults = UserDefaults.standard
        let lastBakedDefault = defaults.string(forKey: userDefaultsBaselineKey)
        if lastBakedDefault != defaultBackendURL {
            defaults.removeObject(forKey: userDefaultsKey)
            defaults.set(defaultBackendURL, forKey: userDefaultsBaselineKey)
        }
        if let raw = defaults.string(forKey: userDefaultsKey),
           !raw.isEmpty,
           let url = URL(string: raw) {
            // R8 defensive: a stale `localhost` UserDefault from an earlier
            // install on a real device will resolve to the iPhone itself
            // and silently fail every request. Fall through to defaultBackendURL
            // when the saved URL points at a loopback address on iOS.
            #if !targetEnvironment(simulator)
            let host = url.host?.lowercased() ?? ""
            if host == "localhost" || host == "127.0.0.1" || host == "::1" {
                return URL(string: defaultBackendURL)!
            }
            #endif
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
