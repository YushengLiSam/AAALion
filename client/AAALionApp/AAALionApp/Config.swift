import Foundation

enum Config {
    /// Backend URL the iOS app talks to.
    ///
    /// **Change this single constant before each LAN demo session.**
    /// On the Mac: `ipconfig getifaddr en0` → your current LAN IP.
    /// Then `aaalion ios-device` to rebuild + redeploy.
    ///
    /// Resolution order:
    /// 1. `PUBLIC_BACKEND_URL` env var (debug-launched from Xcode only).
    /// 2. `defaultBackendURL` below.
    ///
    /// Simulator builds work with localhost regardless because
    /// Simulator routes loopback to the host Mac.
    private static let defaultBackendURL = "http://10.76.138.67:8000"

    static let backendURL: URL = {
        if let raw = ProcessInfo.processInfo.environment["PUBLIC_BACKEND_URL"],
           let url = URL(string: raw) {
            return url
        }
        return URL(string: defaultBackendURL)!
    }()
}
